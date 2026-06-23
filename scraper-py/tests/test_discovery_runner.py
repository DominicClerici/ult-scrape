import html
import json

import pytest
import pytest_asyncio

from app import db
from app.config import Settings
from app.discovery import runner
from app.repo import JobRepo


def _html(tabs, *, pages=1, per_page=50, total=None, filters=None, sorts=("date_desc",)):
    data = {
        "store": {"page": {"data": {
            "pagination": {"pages": pages, "per_page": per_page, "current": 0},
            "totalResults": total if total is not None else len(tabs),
            "data": tabs,
            "order": {"all": [{"url_name": s} for s in sorts]},
            "filters": filters or [],
        }}}
    }
    esc = html.escape(json.dumps(data), quote=True)
    return f'<div class="js-store" data-content="{esc}"></div>'


def _tab(i):
    return {"id": i, "tab_url": f"https://tabs.ultimate-guitar.com/tab/band/song-{i}-official-{i}"}


class FakeBrowser:
    """Returns canned HTML per query substring; records fetched queries."""

    def __init__(self, responses, default_html):
        self._responses = responses  # list of (substr, html)
        self._default = default_html
        self.queries = []

    async def fetch_explore(self, query: str) -> str:
        self.queries.append(query)
        for substr, page_html in self._responses:
            if substr in query:
                return page_html
        return self._default


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    clock = {"t": 1000.0}
    r = JobRepo(conn, now_fn=lambda: clock["t"])
    r.clock = clock
    yield r
    await conn.close()


def _settings(**over):
    base = dict(
        discovery_sort_orders="date_desc,rating_desc",
        discovery_facet_ladder="genres,decade",
        discovery_untagged_sweep=False,
        discovery_max_slices=0,
        discovery_target_cap=0,
        discovery_page_delay_min=0.0,
        discovery_page_delay_max=0.0,
    )
    base.update(over)
    return Settings(_env_file=None, **base)


async def _noop_sleep(_):
    return None


async def test_runner_crawls_genre_slice_and_upserts(repo):
    catalog_filters = [
        {"param_name": "genres", "values": [{"name": "Rock", "url_name": 4, "count": 80}]},
        {"param_name": "decade", "values": [{"name": "2020s", "url_name": 2020, "count": 80}]},
    ]
    bootstrap = _html([], filters=catalog_filters, sorts=("date_desc", "rating_desc"))
    genre_page = _html([_tab(1), _tab(2)], pages=1, total=2,
                       filters=catalog_filters, sorts=("date_desc", "rating_desc"))
    browser = FakeBrowser(responses=[("genres", genre_page)], default_html=bootstrap)

    run = await repo.request_discovery({})
    run = await repo.claim_discovery()
    await runner.run(browser, repo, run, _settings(), sleep=_noop_sleep)

    routes = await repo.discovered_routes(exclude_succeeded=False)
    assert {r[0] for r in routes} == {"band/song-1-official-1", "band/song-2-official-2"}
    done = await repo.get_discovery_run(run.id)
    assert done.state == "done"
    assert done.tabs_found == 2


async def test_runner_subdivides_when_slice_hits_cap(repo):
    catalog_filters = [
        {"param_name": "genres", "values": [{"name": "Rock", "url_name": 4, "count": 99999}]},
        {"param_name": "decade", "values": [
            {"name": "2020s", "url_name": 2020, "count": 50},
            {"name": "2010s", "url_name": 2010, "count": 50},
        ]},
    ]
    bootstrap = _html([], filters=catalog_filters)
    # Genre-only slice reports > 1000 reachable (pages=20 cap, total huge) -> must subdivide.
    genre_capped = _html([_tab(900)], pages=20, total=99999, filters=catalog_filters)
    decade_2020 = _html([_tab(1)], pages=1, total=1, filters=catalog_filters)
    decade_2010 = _html([_tab(2)], pages=1, total=1, filters=catalog_filters)
    browser = FakeBrowser(
        responses=[
            ("decade%5B%5D=2020", decade_2020),
            ("decade%5B%5D=2010", decade_2010),
            ("genres%5B%5D=4", genre_capped),
        ],
        default_html=bootstrap,
    )

    run = await repo.request_discovery({})
    run = await repo.claim_discovery()
    await runner.run(browser, repo, run, _settings(), sleep=_noop_sleep)

    routes = {r[0] for r in await repo.discovered_routes(exclude_succeeded=False)}
    assert routes == {"band/song-1-official-1", "band/song-2-official-2"}


async def test_runner_honors_cancel(repo):
    catalog_filters = [
        {"param_name": "genres", "values": [
            {"name": "Rock", "url_name": 4, "count": 1},
            {"name": "Metal", "url_name": 8, "count": 1},
        ]},
    ]
    bootstrap = _html([], filters=catalog_filters)
    browser = FakeBrowser(responses=[], default_html=bootstrap)

    run = await repo.request_discovery({})
    run = await repo.claim_discovery()
    await repo.request_discovery_cancel(run.id)
    await runner.run(browser, repo, run, _settings(), sleep=_noop_sleep)

    done = await repo.get_discovery_run(run.id)
    assert done.state == "canceled"
