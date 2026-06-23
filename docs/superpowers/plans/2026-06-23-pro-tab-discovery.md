# Pro Tab Discovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an endpoint-triggered, deterministic discovery capability to `scraper-py` that enumerates Ultimate Guitar Pro tabs at scale, reuses the scraper's Camoufox session (mutually exclusive with scraping), and persists each tab's raw UG explore metadata in SQLite — without enqueuing or touching the decoder/output contract.

**Architecture:** `POST /discover` records a request and signals the worker; the worker (sole owner of the browser) runs an adaptive crawler when idle with an empty queue. The crawler walks `/explore?type[]=Pro&…` slices (genre × decade), reading each slice's `total_results` to subdivide cap-hitting slices down a facet ladder (then sort-order windows), parsing the page-embedded `js-store` JSON, deduping by numeric tab id, and upserting metadata. A separate manual endpoint enqueues discovered tabs later.

**Tech Stack:** Python ≥ 3.13, FastAPI, aiosqlite (WAL), pydantic / pydantic-settings, Camoufox via Playwright async, pytest (asyncio auto mode).

## Global Constraints

- **Python ≥ 3.13**; deps managed via `scraper-py/pyproject.toml`. No new third-party dependency — parsing uses stdlib `re` + `html` only.
- **`repo.py` is the ONLY module that issues SQL** and owns all state transitions. New tables/queries go there.
- **Only `worker.py` drives the browser**, through the `BrowserSession` Protocol. The API stays browser-free and testable with a fake.
- **The scraper never decrypts; discovery never writes under `OUTPUT_DIR`.** The output contract, `metadata.json`, `app/output.py`, and all of `decoder-rs` are untouched.
- **Tests are deterministic and browser-free by default.** Use the in-memory `repo` fixture with the injectable clock (`repo.clock["t"]`) and a fake `BrowserSession`. Real-browser behavior goes behind the `integration` marker.
- **No comments that restate code** (user global rule). Comments only for non-obvious why.
- Run all commands from `scraper-py/` with the venv active. Test command base: `python -m pytest`.
- UG query param names (verified live): `type` (`type[]=Pro`), `genres` (numeric id), `decade` (year), `order` (sort url_name), `page`. The page state lives in `<div class="js-store" data-content="{…HTML-escaped JSON…}">` at `store.page.data` with keys `data` (50 tabs), `pagination{pages≤20, per_page:50, current}`, `totalResults`, `filters` (list of `{param_name,title,values:[{name,url_name,count}]}`), `order{all:[{name,url_name}]}`. Hard cap: 20 pages × 50 = **1000 reachable per filter+sort combo**.

---

### Task 1: Discovery config keys

**Files:**
- Modify: `scraper-py/app/config.py:24-33` (add keys in the `Settings` body)
- Modify: `scraper-py/.env.example`
- Test: `scraper-py/tests/test_config_discovery.py`

**Interfaces:**
- Produces: `Settings` gains fields `discovery_sort_orders: str`, `discovery_page_delay_min: float`, `discovery_page_delay_max: float`, `discovery_max_slices: int`, `discovery_target_cap: int`, `discovery_request_timeout_ms: int`, `discovery_facet_ladder: str`, `discovery_untagged_sweep: bool`. Consumed by Tasks 8 (runner) and 10 (routes).

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_config_discovery.py
from app.config import Settings


def test_discovery_defaults():
    s = Settings(_env_file=None)
    assert s.discovery_sort_orders == "date_desc,artistname_asc,artistname_desc,songname_asc"
    assert s.discovery_facet_ladder == "genres,decade,tonality"
    assert s.discovery_page_delay_min == 2.0
    assert s.discovery_page_delay_max == 6.0
    assert s.discovery_max_slices == 0
    assert s.discovery_target_cap == 0
    assert s.discovery_request_timeout_ms == 30_000
    assert s.discovery_untagged_sweep is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config_discovery.py -v`
Expected: FAIL with `AttributeError`/`ValidationError` (fields don't exist).

- [ ] **Step 3: Add the fields to `Settings`**

Insert after `poll_interval_seconds: float = 5.0` (config.py:29):

```python
    discovery_sort_orders: str = "date_desc,artistname_asc,artistname_desc,songname_asc"
    discovery_facet_ladder: str = "genres,decade,tonality"
    discovery_page_delay_min: float = 2.0
    discovery_page_delay_max: float = 6.0
    discovery_max_slices: int = 0
    discovery_target_cap: int = 0
    discovery_request_timeout_ms: int = 30_000
    discovery_untagged_sweep: bool = True
```

- [ ] **Step 4: Mirror the keys in `.env.example`**

Append:

```bash
# --- Discovery (Pro tab enumeration) ---
DISCOVERY_SORT_ORDERS=date_desc,artistname_asc,artistname_desc,songname_asc
DISCOVERY_FACET_LADDER=genres,decade,tonality
DISCOVERY_PAGE_DELAY_MIN=2.0
DISCOVERY_PAGE_DELAY_MAX=6.0
DISCOVERY_MAX_SLICES=0
DISCOVERY_TARGET_CAP=0
DISCOVERY_REQUEST_TIMEOUT_MS=30000
DISCOVERY_UNTAGGED_SWEEP=true
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_config_discovery.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/config.py .env.example tests/test_config_discovery.py
git commit -m "feat(discovery): add DISCOVERY_* config keys"
```

---

### Task 2: Schema, models, and ServiceState

**Files:**
- Modify: `scraper-py/app/db.py:5-30` (extend `SCHEMA`)
- Modify: `scraper-py/app/models.py` (add `DISCOVERING`, `DiscoveryRun`, request/response models)
- Test: `scraper-py/tests/test_discovery_schema.py`

**Interfaces:**
- Produces:
  - `ServiceState.DISCOVERING = "discovering"`.
  - `DiscoveryRun` (pydantic): `id: str`, `params: dict`, `state: str`, `created_at: float`, `started_at: float | None`, `finished_at: float | None`, `slices_total: int`, `slices_done: int`, `tabs_found: int`, `cancel_requested: bool`, `error: str | None`.
  - `DiscoveryStartRequest` (pydantic): all optional overrides — `sorts: list[str] | None = None`, `facet_ladder: list[str] | None = None`, `max_slices: int | None = None`, `target_cap: int | None = None`, `genres: list[int] | None = None`, `decades: list[int] | None = None`, `untagged_sweep: bool | None = None`.
  - Tables `tab_metadata` and `discovery_runs` (columns below). Consumed by Task 6 (repo).

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_discovery_schema.py
import pytest_asyncio

from app import db
from app.models import DiscoveryRun, DiscoveryStartRequest, ServiceState


@pytest_asyncio.fixture
async def conn():
    c = await db.connect(":memory:")
    await db.init_schema(c)
    yield c
    await c.close()


async def test_discovery_tables_exist(conn):
    cur = await conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )
    names = {r["name"] for r in await cur.fetchall()}
    assert {"tab_metadata", "discovery_runs"} <= names


async def test_discovering_state_and_models():
    assert ServiceState.DISCOVERING.value == "discovering"
    run = DiscoveryRun(
        id="r1", params={}, state="requested", created_at=1.0,
        started_at=None, finished_at=None, slices_total=0, slices_done=0,
        tabs_found=0, cancel_requested=False, error=None,
    )
    assert run.state == "requested"
    req = DiscoveryStartRequest(genres=[4, 8])
    assert req.genres == [4, 8]
    assert req.sorts is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_schema.py -v`
Expected: FAIL (tables/models missing).

- [ ] **Step 3: Extend the schema**

In `db.py`, append to the `SCHEMA` string (before the closing `"""`):

```sql
CREATE TABLE IF NOT EXISTS tab_metadata (
    tab_id TEXT PRIMARY KEY,
    numeric_id INTEGER,
    route TEXT NOT NULL,
    url TEXT NOT NULL,
    explore_json TEXT NOT NULL,
    first_seen_at REAL NOT NULL,
    last_seen_at REAL NOT NULL,
    discovery_run_id TEXT
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    id TEXT PRIMARY KEY,
    params_json TEXT NOT NULL,
    state TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    finished_at REAL,
    slices_total INTEGER NOT NULL DEFAULT 0,
    slices_done INTEGER NOT NULL DEFAULT 0,
    tabs_found INTEGER NOT NULL DEFAULT 0,
    cancel_requested INTEGER NOT NULL DEFAULT 0,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_discovery_state ON discovery_runs(state);
```

- [ ] **Step 4: Add the model code**

In `models.py`, add to `ServiceState` (after `ERROR = "error"`):

```python
    DISCOVERING = "discovering"
```

Append at end of `models.py`:

```python
class DiscoveryRun(BaseModel):
    id: str
    params: dict
    state: str
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    slices_total: int = 0
    slices_done: int = 0
    tabs_found: int = 0
    cancel_requested: bool = False
    error: str | None = None


class DiscoveryStartRequest(BaseModel):
    sorts: list[str] | None = None
    facet_ladder: list[str] | None = None
    max_slices: int | None = None
    target_cap: int | None = None
    genres: list[int] | None = None
    decades: list[int] | None = None
    untagged_sweep: bool | None = None
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_schema.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/db.py app/models.py tests/test_discovery_schema.py
git commit -m "feat(discovery): add tab_metadata/discovery_runs schema + models"
```

---

### Task 3: Explore HTML parser

**Files:**
- Create: `scraper-py/app/discovery/__init__.py` (empty)
- Create: `scraper-py/app/discovery/parser.py`
- Modify: `scraper-py/app/errors.py` (add `DiscoveryParseError`)
- Create: `scraper-py/tests/fixtures/explore_min.html`
- Test: `scraper-py/tests/test_discovery_parser.py`

**Interfaces:**
- Produces:
  - `class DiscoveryParseError(Exception)` in `app/errors.py`.
  - `ExploreStore` dataclass: `tabs: list[dict]`, `pages: int`, `per_page: int`, `current_page: int`, `total_results: int`, `filters: list[dict]`, `order: dict`.
  - `parse_explore_html(html: str) -> ExploreStore`. Consumed by Tasks 4 (facets reads `.filters`/`.order`) and 8 (runner).

- [ ] **Step 1: Create the committed fixture**

Write `scraper-py/tests/fixtures/explore_min.html` (note the `&quot;` entities — this mirrors how UG escapes the JSON into the attribute):

```html
<!doctype html><html><body>
<div class="js-store" data-content="{&quot;store&quot;:{&quot;page&quot;:{&quot;data&quot;:{&quot;pagination&quot;:{&quot;pages&quot;:2,&quot;per_page&quot;:50,&quot;current&quot;:0},&quot;totalResults&quot;:73,&quot;data&quot;:[{&quot;id&quot;:111,&quot;song_name&quot;:&quot;Song A&quot;,&quot;artist_name&quot;:&quot;Band&quot;,&quot;type&quot;:&quot;Pro&quot;,&quot;tab_url&quot;:&quot;https://tabs.ultimate-guitar.com/tab/band/song-a-official-111&quot;},{&quot;id&quot;:222,&quot;song_name&quot;:&quot;Song B&quot;,&quot;artist_name&quot;:&quot;Band&quot;,&quot;type&quot;:&quot;Pro&quot;,&quot;tab_url&quot;:&quot;https://tabs.ultimate-guitar.com/tab/band/song-b-official-222&quot;}],&quot;order&quot;:{&quot;all&quot;:[{&quot;name&quot;:&quot;Recently added&quot;,&quot;url_name&quot;:&quot;date_desc&quot;}]},&quot;filters&quot;:[{&quot;param_name&quot;:&quot;genres&quot;,&quot;title&quot;:&quot;genre&quot;,&quot;values&quot;:[{&quot;name&quot;:&quot;Rock&quot;,&quot;url_name&quot;:4,&quot;count&quot;:156741}]},{&quot;param_name&quot;:&quot;decade&quot;,&quot;title&quot;:&quot;Decade&quot;,&quot;values&quot;:[{&quot;name&quot;:&quot;2020s&quot;,&quot;url_name&quot;:2020,&quot;count&quot;:6078}]}]}}}}">
</div>
</body></html>
```

- [ ] **Step 2: Write the failing test**

```python
# scraper-py/tests/test_discovery_parser.py
from pathlib import Path

import pytest

from app.discovery.parser import ExploreStore, parse_explore_html
from app.errors import DiscoveryParseError

FIXTURE = Path(__file__).parent / "fixtures" / "explore_min.html"


def test_parse_extracts_tabs_and_pagination():
    store = parse_explore_html(FIXTURE.read_text())
    assert isinstance(store, ExploreStore)
    assert store.pages == 2
    assert store.per_page == 50
    assert store.current_page == 0
    assert store.total_results == 73
    assert [t["id"] for t in store.tabs] == [111, 222]
    assert store.tabs[0]["tab_url"].endswith("song-a-official-111")
    assert {f["param_name"] for f in store.filters} == {"genres", "decade"}
    assert store.order["all"][0]["url_name"] == "date_desc"


def test_parse_missing_store_raises():
    with pytest.raises(DiscoveryParseError):
        parse_explore_html("<html><body>no store here</body></html>")
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_parser.py -v`
Expected: FAIL with `ModuleNotFoundError: app.discovery`.

- [ ] **Step 4: Add the error type**

Append to `scraper-py/app/errors.py`:

```python
class DiscoveryParseError(Exception):
    """Raised when the explore page's embedded js-store cannot be located or parsed."""
```

- [ ] **Step 5: Implement the parser**

Create `scraper-py/app/discovery/__init__.py` (empty file).

Create `scraper-py/app/discovery/parser.py`:

```python
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from app.errors import DiscoveryParseError

_STORE_RE = re.compile(r'class="js-store"[^>]*\sdata-content="([^"]*)"')


@dataclass
class ExploreStore:
    tabs: list[dict]
    pages: int
    per_page: int
    current_page: int
    total_results: int
    filters: list[dict]
    order: dict


def parse_explore_html(page_html: str) -> ExploreStore:
    m = _STORE_RE.search(page_html)
    if not m:
        raise DiscoveryParseError("js-store data-content not found")
    try:
        payload = json.loads(html.unescape(m.group(1)))
        data = payload["store"]["page"]["data"]
    except (ValueError, KeyError, TypeError) as e:
        raise DiscoveryParseError(f"unparseable js-store payload: {e!r}") from e

    pagination = data.get("pagination") or {}
    return ExploreStore(
        tabs=list(data.get("data") or []),
        pages=int(pagination.get("pages", 0)),
        per_page=int(pagination.get("per_page", 0)),
        current_page=int(pagination.get("current", 0)),
        total_results=int(data.get("totalResults", 0)),
        filters=list(data.get("filters") or []),
        order=dict(data.get("order") or {}),
    )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_parser.py -v`
Expected: PASS (both tests).

- [ ] **Step 7: Commit**

```bash
git add app/discovery/__init__.py app/discovery/parser.py app/errors.py tests/fixtures/explore_min.html tests/test_discovery_parser.py
git commit -m "feat(discovery): parse explore js-store HTML into ExploreStore"
```

---

### Task 4: Facet catalog, slice specs, and query builder

**Files:**
- Create: `scraper-py/app/discovery/facets.py`
- Test: `scraper-py/tests/test_discovery_facets.py`

**Interfaces:**
- Consumes: `ExploreStore.filters` and `ExploreStore.order` (Task 3) — lists of `{param_name,title,values:[{name,url_name,count}]}` and `{all:[{url_name}]}`.
- Produces:
  - `FacetValue` dataclass: `url_name: str`, `name: str`, `count: int`.
  - `FacetCatalog` dataclass: `facets: dict[str, list[FacetValue]]` (keyed by param_name, e.g. `"genres"`, `"decade"`, `"tonality"`), `sorts: list[str]`. Method `values(param_name) -> list[FacetValue]` (returns `[]` if absent).
  - `catalog_from_store(store: ExploreStore) -> FacetCatalog`.
  - `SliceSpec` dataclass: `filters: dict[str, int | str]` (param_name → value, always includes `"type": "Pro"`), `order: str | None = None`, `depth: int = 0`. Method `label() -> str`.
  - `build_query(spec: SliceSpec, page: int) -> str` — URL-encoded query string. Consumed by Tasks 5 (planner), 7 (browser), 8 (runner).

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_discovery_facets.py
from urllib.parse import parse_qs

from app.discovery.facets import (
    FacetCatalog,
    FacetValue,
    SliceSpec,
    build_query,
    catalog_from_store,
)
from app.discovery.parser import ExploreStore


def _store():
    return ExploreStore(
        tabs=[], pages=1, per_page=50, current_page=0, total_results=0,
        filters=[
            {"param_name": "genres", "values": [
                {"name": "Rock", "url_name": 4, "count": 156741},
                {"name": "Metal", "url_name": 8, "count": 111476},
            ]},
            {"param_name": "decade", "values": [
                {"name": "2020s", "url_name": 2020, "count": 6078},
            ]},
        ],
        order={"all": [{"url_name": "date_desc"}, {"url_name": "rating_desc"}]},
    )


def test_catalog_from_store():
    cat = catalog_from_store(_store())
    assert isinstance(cat, FacetCatalog)
    genres = cat.values("genres")
    assert genres[0] == FacetValue(url_name="4", name="Rock", count=156741)
    assert [g.url_name for g in genres] == ["4", "8"]
    assert cat.values("decade")[0].url_name == "2020"
    assert cat.values("missing") == []
    assert cat.sorts == ["date_desc", "rating_desc"]


def test_build_query_encodes_type_filters_sort_page():
    spec = SliceSpec(filters={"type": "Pro", "genres": 4, "decade": 2020}, order="date_desc")
    q = build_query(spec, page=3)
    parsed = parse_qs(q)
    assert parsed["type[]"] == ["Pro"]
    assert parsed["genres[]"] == ["4"]
    assert parsed["decade[]"] == ["2020"]
    assert parsed["order"] == ["date_desc"]
    assert parsed["page"] == ["3"]


def test_build_query_omits_order_when_none():
    spec = SliceSpec(filters={"type": "Pro"})
    q = build_query(spec, page=1)
    assert "order=" not in q
    assert parse_qs(q)["page"] == ["1"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_facets.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement facets**

Create `scraper-py/app/discovery/facets.py`:

```python
from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlencode

from app.discovery.parser import ExploreStore


@dataclass(frozen=True)
class FacetValue:
    url_name: str
    name: str
    count: int


@dataclass
class FacetCatalog:
    facets: dict[str, list[FacetValue]] = field(default_factory=dict)
    sorts: list[str] = field(default_factory=list)

    def values(self, param_name: str) -> list[FacetValue]:
        return self.facets.get(param_name, [])


@dataclass
class SliceSpec:
    filters: dict[str, int | str]
    order: str | None = None
    depth: int = 0

    def label(self) -> str:
        parts = [f"{k}={v}" for k, v in sorted(self.filters.items()) if k != "type"]
        if self.order:
            parts.append(f"order={self.order}")
        return "&".join(parts) or "type=Pro"


def catalog_from_store(store: ExploreStore) -> FacetCatalog:
    facets: dict[str, list[FacetValue]] = {}
    for f in store.filters:
        param = f.get("param_name")
        if not param:
            continue
        facets[param] = [
            FacetValue(
                url_name=str(v.get("url_name")),
                name=str(v.get("name", "")),
                count=int(v.get("count", 0)),
            )
            for v in (f.get("values") or [])
        ]
    sorts = [str(o.get("url_name")) for o in (store.order.get("all") or []) if o.get("url_name")]
    return FacetCatalog(facets=facets, sorts=sorts)


def build_query(spec: SliceSpec, page: int) -> str:
    pairs: list[tuple[str, str]] = []
    for key, value in spec.filters.items():
        pairs.append((f"{key}[]", str(value)))
    if spec.order:
        pairs.append(("order", spec.order))
    pairs.append(("page", str(page)))
    return urlencode(pairs)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_facets.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add app/discovery/facets.py tests/test_discovery_facets.py
git commit -m "feat(discovery): facet catalog, slice specs, query builder"
```

---

### Task 5: Slice planner (initial slices, subdivision, sort windows)

**Files:**
- Create: `scraper-py/app/discovery/planner.py`
- Test: `scraper-py/tests/test_discovery_planner.py`

**Interfaces:**
- Consumes: `FacetCatalog`, `SliceSpec` (Task 4).
- Produces:
  - `initial_slices(catalog: FacetCatalog, *, untagged_sweep: bool, genres: list[int] | None = None, decades: list[int] | None = None) -> list[SliceSpec]` — one slice per genre (depth 0); plus, if `untagged_sweep`, a single `{type:Pro}` slice (depth 0). `genres`/`decades` restrict the facet values used (for testing/subsetting).
  - `subdivide(spec: SliceSpec, catalog: FacetCatalog, ladder: list[str]) -> list[SliceSpec] | None` — returns child slices that add the next unused ladder facet, or `None` if no finer facet is available. Children carry `depth = spec.depth + 1`.
  - `sort_windows(spec: SliceSpec, sorts: list[str]) -> list[SliceSpec]` — one copy of `spec` per sort order. Consumed by Task 8 (runner).

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_discovery_planner.py
from app.discovery.facets import FacetCatalog, FacetValue, SliceSpec
from app.discovery.planner import initial_slices, sort_windows, subdivide


def _catalog():
    return FacetCatalog(
        facets={
            "genres": [FacetValue("4", "Rock", 156741), FacetValue("8", "Metal", 111476)],
            "decade": [FacetValue("2020", "2020s", 6078), FacetValue("2010", "2010s", 9000)],
            "tonality": [FacetValue("1", "C", 50), FacetValue("2", "G", 40)],
        },
        sorts=["date_desc", "rating_desc"],
    )


def test_initial_slices_one_per_genre_plus_untagged():
    slices = initial_slices(_catalog(), untagged_sweep=True)
    genre_vals = sorted(s.filters.get("genres") for s in slices if "genres" in s.filters)
    assert genre_vals == ["4", "8"]
    untagged = [s for s in slices if s.filters == {"type": "Pro"}]
    assert len(untagged) == 1
    assert all(s.depth == 0 for s in slices)


def test_initial_slices_respects_genre_subset_and_no_sweep():
    slices = initial_slices(_catalog(), untagged_sweep=False, genres=[8])
    assert [s.filters["genres"] for s in slices] == ["8"]


def test_subdivide_adds_next_ladder_facet():
    parent = SliceSpec(filters={"type": "Pro", "genres": "4"}, depth=0)
    children = subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"])
    assert children is not None
    assert all(c.filters["genres"] == "4" for c in children)
    assert sorted(c.filters["decade"] for c in children) == ["2010", "2020"]
    assert all(c.depth == 1 for c in children)


def test_subdivide_descends_to_third_facet():
    parent = SliceSpec(filters={"type": "Pro", "genres": "4", "decade": "2020"}, depth=1)
    children = subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"])
    assert sorted(c.filters["tonality"] for c in children) == ["1", "2"]


def test_subdivide_returns_none_when_ladder_exhausted():
    parent = SliceSpec(
        filters={"type": "Pro", "genres": "4", "decade": "2020", "tonality": "1"}, depth=2
    )
    assert subdivide(parent, _catalog(), ladder=["genres", "decade", "tonality"]) is None


def test_sort_windows_one_per_sort():
    spec = SliceSpec(filters={"type": "Pro", "genres": "4"})
    windows = sort_windows(spec, ["date_desc", "rating_desc"])
    assert [w.order for w in windows] == ["date_desc", "rating_desc"]
    assert all(w.filters == spec.filters for w in windows)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_planner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the planner**

Create `scraper-py/app/discovery/planner.py`:

```python
from __future__ import annotations

from app.discovery.facets import FacetCatalog, SliceSpec


def initial_slices(
    catalog: FacetCatalog,
    *,
    untagged_sweep: bool,
    genres: list[int] | None = None,
    decades: list[int] | None = None,
) -> list[SliceSpec]:
    allowed = {str(g) for g in genres} if genres is not None else None
    slices: list[SliceSpec] = []
    for fv in catalog.values("genres"):
        if allowed is not None and fv.url_name not in allowed:
            continue
        slices.append(SliceSpec(filters={"type": "Pro", "genres": fv.url_name}, depth=0))
    if untagged_sweep:
        slices.append(SliceSpec(filters={"type": "Pro"}, depth=0))
    return slices


def subdivide(
    spec: SliceSpec, catalog: FacetCatalog, ladder: list[str]
) -> list[SliceSpec] | None:
    for facet in ladder:
        if facet in spec.filters:
            continue
        values = catalog.values(facet)
        if not values:
            continue
        return [
            SliceSpec(
                filters={**spec.filters, facet: fv.url_name},
                order=spec.order,
                depth=spec.depth + 1,
            )
            for fv in values
        ]
    return None


def sort_windows(spec: SliceSpec, sorts: list[str]) -> list[SliceSpec]:
    return [
        SliceSpec(filters=dict(spec.filters), order=s, depth=spec.depth) for s in sorts
    ]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_planner.py -v`
Expected: PASS (all six tests).

- [ ] **Step 5: Commit**

```bash
git add app/discovery/planner.py tests/test_discovery_planner.py
git commit -m "feat(discovery): adaptive slice planner"
```

---

### Task 6: Repo discovery methods

**Files:**
- Modify: `scraper-py/app/repo.py` (add methods + `DiscoveryRun` row mapping; import update)
- Test: `scraper-py/tests/test_repo_discovery.py`

**Interfaces:**
- Consumes: `DiscoveryRun` model (Task 2); `normalize_tab` (existing, `app/normalize.py`).
- Produces, on `JobRepo`:
  - `count_active_jobs() -> int` — count of `queued` + `running` jobs.
  - `has_active_discovery() -> bool` — any run in (`requested`,`running`).
  - `request_discovery(params: dict) -> DiscoveryRun | None` — insert `requested`; returns `None` if `has_active_discovery()`.
  - `claim_discovery() -> DiscoveryRun | None` — atomic oldest `requested → running`, sets `started_at`.
  - `get_discovery_run(run_id: str) -> DiscoveryRun | None`.
  - `list_discovery_runs(limit: int = 20) -> list[DiscoveryRun]` (newest first).
  - `update_discovery_progress(run_id, slices_total, slices_done, tabs_found) -> None`.
  - `finish_discovery(run_id, state: str, error: str | None = None) -> None` (`state` in done/failed/canceled; sets `finished_at`).
  - `request_discovery_cancel(run_id) -> bool` — set `cancel_requested=1` while run is requested/running.
  - `is_discovery_cancel_requested(run_id) -> bool`.
  - `fail_interrupted_discovery() -> int` — startup recovery: any `running` run → `failed` with error `"interrupted by restart"`.
  - `upsert_tab_metadata(run_id: str, record: dict) -> None` — derives `(tab_id, url)` via `normalize_tab(record["tab_url"])`, `numeric_id=record.get("id")`, stores `explore_json=json.dumps(record)`.
  - `discovered_routes(exclude_succeeded: bool = True) -> list[tuple[str, str]]` — `(tab_id, url)` pairs from `tab_metadata`, optionally excluding tabs with a succeeded job. Consumed by Tasks 8, 9, 10.

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_repo_discovery.py
import pytest_asyncio

from app import db
from app.repo import JobRepo


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    clock = {"t": 1000.0}
    r = JobRepo(conn, now_fn=lambda: clock["t"])
    r.clock = clock
    yield r
    await conn.close()


async def test_request_and_claim_discovery(repo):
    run = await repo.request_discovery({"max_slices": 5})
    assert run is not None and run.state == "requested"
    assert await repo.has_active_discovery() is True
    # second request rejected while one is active
    assert await repo.request_discovery({}) is None

    claimed = await repo.claim_discovery()
    assert claimed.id == run.id and claimed.state == "running"
    assert claimed.started_at == 1000.0
    assert await repo.claim_discovery() is None  # nothing left to claim


async def test_progress_finish_and_cancel(repo):
    run = await repo.request_discovery({})
    await repo.claim_discovery()
    await repo.update_discovery_progress(run.id, slices_total=10, slices_done=3, tabs_found=120)
    got = await repo.get_discovery_run(run.id)
    assert (got.slices_total, got.slices_done, got.tabs_found) == (10, 3, 120)

    assert await repo.request_discovery_cancel(run.id) is True
    assert await repo.is_discovery_cancel_requested(run.id) is True

    await repo.finish_discovery(run.id, "canceled")
    done = await repo.get_discovery_run(run.id)
    assert done.state == "canceled" and done.finished_at == 1000.0
    assert await repo.has_active_discovery() is False


async def test_upsert_tab_metadata_and_discovered_routes(repo):
    rec = {"id": 111, "tab_url": "https://tabs.ultimate-guitar.com/tab/band/song-a-official-111"}
    await repo.upsert_tab_metadata("run1", rec)
    first = await repo.get_discovery_run  # placeholder to keep import used
    routes = await repo.discovered_routes(exclude_succeeded=True)
    assert routes == [("band/song-a-official-111",
                       "https://tabs.ultimate-guitar.com/tab/band/song-a-official-111")]

    # re-upsert updates last_seen_at, no duplicate
    repo.clock["t"] = 2000.0
    await repo.upsert_tab_metadata("run2", rec)
    routes2 = await repo.discovered_routes()
    assert len(routes2) == 1

    # excluded once a succeeded job exists for that tab
    await repo.enqueue(tab_id="band/song-a-official-111",
                       url="https://tabs.ultimate-guitar.com/tab/band/song-a-official-111",
                       max_attempts=3)
    job = (await repo.list(status="queued"))[0]
    await repo.mark_succeeded(job.id, "/out/band/song-a-official-111")
    assert await repo.discovered_routes(exclude_succeeded=True) == []


async def test_fail_interrupted_discovery(repo):
    run = await repo.request_discovery({})
    await repo.claim_discovery()
    n = await repo.fail_interrupted_discovery()
    assert n == 1
    got = await repo.get_discovery_run(run.id)
    assert got.state == "failed" and got.error == "interrupted by restart"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_repo_discovery.py -v`
Expected: FAIL (methods missing).

- [ ] **Step 3: Implement the repo methods**

In `repo.py`, update the imports at the top:

```python
import json
import time
from uuid import uuid4

import aiosqlite

from app.models import DiscoveryRun, Job, JobStatus
from app.normalize import normalize_tab
```

Add a row mapper and the methods inside `JobRepo` (after `reset_running_to_queued`):

```python
    @staticmethod
    def _row_to_discovery(row) -> DiscoveryRun:
        return DiscoveryRun(
            id=row["id"],
            params=json.loads(row["params_json"]),
            state=row["state"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            slices_total=row["slices_total"],
            slices_done=row["slices_done"],
            tabs_found=row["tabs_found"],
            cancel_requested=bool(row["cancel_requested"]),
            error=row["error"],
        )

    async def count_active_jobs(self) -> int:
        cur = await self.conn.execute(
            "SELECT COUNT(*) c FROM jobs WHERE status IN ('queued','running')"
        )
        return (await cur.fetchone())["c"]

    async def has_active_discovery(self) -> bool:
        cur = await self.conn.execute(
            "SELECT COUNT(*) c FROM discovery_runs WHERE state IN ('requested','running')"
        )
        return (await cur.fetchone())["c"] > 0

    async def request_discovery(self, params: dict) -> DiscoveryRun | None:
        if await self.has_active_discovery():
            return None
        run_id = str(uuid4())
        now = self._now()
        await self.conn.execute(
            "INSERT INTO discovery_runs (id, params_json, state, created_at) "
            "VALUES (?,?, 'requested', ?)",
            (run_id, json.dumps(params), now),
        )
        await self.conn.commit()
        return await self.get_discovery_run(run_id)

    async def claim_discovery(self) -> DiscoveryRun | None:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET state='running', started_at=? "
            "WHERE id = (SELECT id FROM discovery_runs WHERE state='requested' "
            "ORDER BY created_at ASC LIMIT 1) RETURNING *",
            (now,),
        )
        row = await cur.fetchone()
        await self.conn.commit()
        return self._row_to_discovery(row) if row else None

    async def get_discovery_run(self, run_id: str) -> DiscoveryRun | None:
        cur = await self.conn.execute(
            "SELECT * FROM discovery_runs WHERE id=?", (run_id,)
        )
        row = await cur.fetchone()
        return self._row_to_discovery(row) if row else None

    async def list_discovery_runs(self, limit: int = 20) -> list[DiscoveryRun]:
        cur = await self.conn.execute(
            "SELECT * FROM discovery_runs ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        return [self._row_to_discovery(r) for r in await cur.fetchall()]

    async def update_discovery_progress(
        self, run_id: str, slices_total: int, slices_done: int, tabs_found: int
    ) -> None:
        await self.conn.execute(
            "UPDATE discovery_runs SET slices_total=?, slices_done=?, tabs_found=? WHERE id=?",
            (slices_total, slices_done, tabs_found, run_id),
        )
        await self.conn.commit()

    async def finish_discovery(
        self, run_id: str, state: str, error: str | None = None
    ) -> None:
        now = self._now()
        await self.conn.execute(
            "UPDATE discovery_runs SET state=?, error=?, finished_at=? WHERE id=?",
            (state, error, now, run_id),
        )
        await self.conn.commit()

    async def request_discovery_cancel(self, run_id: str) -> bool:
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET cancel_requested=1 "
            "WHERE id=? AND state IN ('requested','running')",
            (run_id,),
        )
        await self.conn.commit()
        return cur.rowcount > 0

    async def is_discovery_cancel_requested(self, run_id: str) -> bool:
        cur = await self.conn.execute(
            "SELECT cancel_requested FROM discovery_runs WHERE id=?", (run_id,)
        )
        row = await cur.fetchone()
        return bool(row and row["cancel_requested"])

    async def fail_interrupted_discovery(self) -> int:
        now = self._now()
        cur = await self.conn.execute(
            "UPDATE discovery_runs SET state='failed', error='interrupted by restart', "
            "finished_at=? WHERE state='running'",
            (now,),
        )
        await self.conn.commit()
        return cur.rowcount

    async def upsert_tab_metadata(self, run_id: str, record: dict) -> None:
        tab_id, url = normalize_tab(record["tab_url"])
        now = self._now()
        await self.conn.execute(
            "INSERT INTO tab_metadata "
            "(tab_id, numeric_id, route, url, explore_json, first_seen_at, last_seen_at, discovery_run_id) "
            "VALUES (?,?,?,?,?,?,?,?) "
            "ON CONFLICT(tab_id) DO UPDATE SET "
            "numeric_id=excluded.numeric_id, url=excluded.url, "
            "explore_json=excluded.explore_json, last_seen_at=excluded.last_seen_at, "
            "discovery_run_id=excluded.discovery_run_id",
            (tab_id, record.get("id"), tab_id, url, json.dumps(record), now, now, run_id),
        )
        await self.conn.commit()

    async def discovered_routes(
        self, exclude_succeeded: bool = True
    ) -> list[tuple[str, str]]:
        if exclude_succeeded:
            cur = await self.conn.execute(
                "SELECT m.tab_id, m.url FROM tab_metadata m "
                "WHERE NOT EXISTS (SELECT 1 FROM jobs j "
                "WHERE j.tab_id=m.tab_id AND j.status='succeeded') "
                "ORDER BY m.first_seen_at ASC"
            )
        else:
            cur = await self.conn.execute(
                "SELECT tab_id, url FROM tab_metadata ORDER BY first_seen_at ASC"
            )
        return [(r["tab_id"], r["url"]) for r in await cur.fetchall()]
```

In the test, fix the stray placeholder line: replace `first = await repo.get_discovery_run  # placeholder...` with nothing — delete that line before running. (It was only there to illustrate; remove it.)

- [ ] **Step 4: Remove the placeholder line from the test**

Delete the line `first = await repo.get_discovery_run  # placeholder to keep import used` from `test_upsert_tab_metadata_and_discovered_routes`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_repo_discovery.py -v`
Expected: PASS (all four tests).

- [ ] **Step 6: Run the full repo suite (no regressions)**

Run: `python -m pytest tests/test_repo_basic.py tests/test_repo_transitions.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/repo.py tests/test_repo_discovery.py
git commit -m "feat(discovery): repo methods for runs + tab_metadata"
```

---

### Task 7: Browser seam (`fetch_explore`)

**Files:**
- Modify: `scraper-py/app/browser/base.py` (extend Protocol)
- Create: `scraper-py/app/browser/discover.py`
- Modify: `scraper-py/app/browser/session.py` (implement `fetch_explore`)
- Test: `scraper-py/tests/test_discovery_browser.py`

**Interfaces:**
- Consumes: nothing from prior tasks (uses `build_query` indirectly via callers; this task only needs a raw `query` string).
- Produces:
  - `BrowserSession.fetch_explore(self, query: str) -> str` (Protocol method).
  - `app/browser/discover.py`: `explore_url(query: str) -> str` (pure) returning `https://www.ultimate-guitar.com/explore?<query>`; `async def fetch_explore_html(page, query: str, timeout_ms: int) -> str` (in-page `fetch()` with `page.goto` fallback).
  - `CamoufoxBrowserSession.fetch_explore(self, query) -> str`. Consumed by Task 8 (runner) and the fake browser in tests.

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_discovery_browser.py
from app.browser.discover import explore_url


def test_explore_url_builds_absolute_explore_path():
    assert explore_url("type%5B%5D=Pro&page=2") == (
        "https://www.ultimate-guitar.com/explore?type%5B%5D=Pro&page=2"
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_browser.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Extend the Protocol**

In `app/browser/base.py`, add to `BrowserSession`:

```python
    async def fetch_explore(self, query: str) -> str: ...
```

- [ ] **Step 4: Implement `discover.py`**

Create `scraper-py/app/browser/discover.py`:

```python
from __future__ import annotations

EXPLORE_BASE = "https://www.ultimate-guitar.com/explore"


def explore_url(query: str) -> str:
    return f"{EXPLORE_BASE}?{query}"


async def fetch_explore_html(page, query: str, timeout_ms: int) -> str:
    url = explore_url(query)
    try:
        return await page.evaluate(
            """async (u) => {
                const r = await fetch(u, { credentials: 'include' });
                return await r.text();
            }""",
            url,
        )
    except Exception:
        await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
        return await page.content()
```

- [ ] **Step 5: Implement on the session**

In `app/browser/session.py`, add the import near the others:

```python
from app.browser.discover import fetch_explore_html
```

Add the method to `CamoufoxBrowserSession` (after `scrape`):

```python
    async def fetch_explore(self, query: str) -> str:
        return await fetch_explore_html(
            self._page, query, self.s.discovery_request_timeout_ms
        )
```

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_browser.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/browser/base.py app/browser/discover.py app/browser/session.py tests/test_discovery_browser.py
git commit -m "feat(discovery): browser fetch_explore seam"
```

---

### Task 8: Discovery runner (adaptive online crawl)

**Files:**
- Create: `scraper-py/app/discovery/runner.py`
- Test: `scraper-py/tests/test_discovery_runner.py`

**Interfaces:**
- Consumes: `BrowserSession.fetch_explore` (Task 7), `parse_explore_html` (Task 3), `catalog_from_store`/`build_query`/`SliceSpec` (Task 4), `initial_slices`/`subdivide`/`sort_windows` (Task 5), `Settings` (Task 1), repo methods (Task 6), `DiscoveryRun` (Task 2).
- Produces: `async def run(browser, repo, run: DiscoveryRun, settings, *, sleep=asyncio.sleep) -> None`. Crawls, upserts metadata, updates progress, finishes the run. `sleep` is injectable so tests pass a no-op. Logic:
  1. Resolve params (run.params overriding settings: `sorts`, `facet_ladder`, `max_slices`, `target_cap`, `genres`, `decades`, `untagged_sweep`).
  2. Fetch page 1 of `{type:Pro}` once to build the `FacetCatalog`.
  3. Worklist = `initial_slices(...)`. Online loop: for each slice, fetch page 1; if `total_results > per_page*pages_cap` (cap-hit, where `pages_cap=20`) → `subdivide`; if subdivision returns `None` → expand into `sort_windows` and crawl each fully; else crawl pages `1..pages` of the slice. Dedup tab ids via an in-memory `set`; upsert each new tab. Update progress after each slice. Honor `target_cap` (stop when unique tabs ≥ cap, if >0), `max_slices` (cap processed slices, if >0), and `is_discovery_cancel_requested` (checked between slices → finish `canceled`).
  4. On unhandled exception → `finish_discovery(run.id, "failed", repr(e))` and re-raise so the worker logs it. On normal completion → `finish_discovery(run.id, "done")`.

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_discovery_runner.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_discovery_runner.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the runner**

Create `scraper-py/app/discovery/runner.py`:

```python
from __future__ import annotations

import asyncio
import logging
import random

from app.discovery.facets import SliceSpec, build_query, catalog_from_store
from app.discovery.parser import parse_explore_html
from app.discovery.planner import initial_slices, sort_windows, subdivide

log = logging.getLogger(__name__)

PAGES_CAP = 20  # UG hard limit: 20 pages * 50 = 1000 reachable per filter+sort combo


def _resolve(params: dict, settings):
    sorts = params.get("sorts") or [
        s for s in settings.discovery_sort_orders.split(",") if s
    ]
    ladder = params.get("facet_ladder") or [
        s for s in settings.discovery_facet_ladder.split(",") if s
    ]
    sweep = params.get("untagged_sweep")
    if sweep is None:
        sweep = settings.discovery_untagged_sweep
    return {
        "sorts": sorts,
        "ladder": ladder,
        "max_slices": params.get("max_slices") or settings.discovery_max_slices,
        "target_cap": params.get("target_cap") or settings.discovery_target_cap,
        "genres": params.get("genres"),
        "decades": params.get("decades"),
        "untagged_sweep": sweep,
    }


async def run(browser, repo, run, settings, *, sleep=asyncio.sleep) -> None:
    cfg = _resolve(run.params, settings)
    seen: set[str] = set()
    slices_done = 0

    async def delay():
        hi = settings.discovery_page_delay_max
        if hi > 0:
            await sleep(random.uniform(settings.discovery_page_delay_min, hi))

    async def fetch(spec: SliceSpec, page: int):
        html_text = await browser.fetch_explore(build_query(spec, page))
        return parse_explore_html(html_text)

    async def absorb(store) -> None:
        for record in store.tabs:
            tab_url = record.get("tab_url")
            if not tab_url:
                continue
            try:
                from app.normalize import normalize_tab

                tab_id, _ = normalize_tab(tab_url)
            except ValueError:
                continue
            if tab_id in seen:
                continue
            seen.add(tab_id)
            await repo.upsert_tab_metadata(run.id, record)

    async def crawl_full(spec: SliceSpec, first_store) -> None:
        await absorb(first_store)
        pages = min(first_store.pages, PAGES_CAP)
        for page in range(2, pages + 1):
            await delay()
            await absorb(await fetch(spec, page))

    try:
        bootstrap = await fetch(SliceSpec(filters={"type": "Pro"}), 1)
        catalog = catalog_from_store(bootstrap)
        catalog.sorts = cfg["sorts"] or catalog.sorts

        worklist = initial_slices(
            catalog,
            untagged_sweep=cfg["untagged_sweep"],
            genres=cfg["genres"],
            decades=cfg["decades"],
        )

        while worklist:
            if await repo.is_discovery_cancel_requested(run.id):
                await repo.finish_discovery(run.id, "canceled")
                return
            if cfg["max_slices"] and slices_done >= cfg["max_slices"]:
                break
            if cfg["target_cap"] and len(seen) >= cfg["target_cap"]:
                break

            spec = worklist.pop(0)
            store = await fetch(spec, 1)
            reachable_capped = store.pages >= PAGES_CAP and store.total_results > PAGES_CAP * (store.per_page or 50)

            if reachable_capped and spec.order is None:
                children = subdivide(spec, catalog, cfg["ladder"])
                if children is not None:
                    worklist[:0] = children
                else:
                    worklist[:0] = sort_windows(spec, catalog.sorts)
            else:
                await crawl_full(spec, store)

            slices_done += 1
            await repo.update_discovery_progress(
                run.id, slices_total=slices_done + len(worklist),
                slices_done=slices_done, tabs_found=len(seen),
            )
            await delay()

        await repo.finish_discovery(run.id, "done")
    except Exception as e:
        log.exception("discovery run %s failed", run.id)
        await repo.finish_discovery(run.id, "failed", repr(e))
        raise
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_discovery_runner.py -v`
Expected: PASS (all three tests).

- [ ] **Step 5: Commit**

```bash
git add app/discovery/runner.py tests/test_discovery_runner.py
git commit -m "feat(discovery): adaptive online crawl runner"
```

---

### Task 9: Worker discovery branch + startup recovery

**Files:**
- Modify: `scraper-py/app/worker.py` (discovery branch before `claim_next`; `DISCOVERING` state)
- Modify: `scraper-py/app/main.py:35` (call `fail_interrupted_discovery` at startup)
- Test: `scraper-py/tests/test_worker_discovery.py`

**Interfaces:**
- Consumes: `repo.claim_discovery`, `repo.fail_interrupted_discovery`, `repo.finish_discovery` (Task 6); `discovery.runner.run` (Task 8); `ServiceState.DISCOVERING` (Task 2).
- Produces: worker runs a claimed discovery run once per loop iteration before claiming jobs. No new public worker methods (reuses `notify_enqueued()` as the wakeup signal).

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_worker_discovery.py
import asyncio

import pytest
import pytest_asyncio

from app import db
from app.config import Settings
from app.models import ServiceState
from app.repo import JobRepo
from app.worker import Worker


class FakeBrowser:
    async def ensure_logged_in(self): ...
    async def is_logged_in(self): return True
    async def scrape(self, url): return []
    async def fetch_explore(self, query): return ""
    async def close(self): ...


@pytest_asyncio.fixture
async def repo():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    r = JobRepo(conn)
    yield r
    await conn.close()


async def test_worker_runs_pending_discovery(repo, monkeypatch):
    ran = {}

    async def fake_run(browser, repo_, run, settings, **kw):
        ran["id"] = run.id
        await repo_.finish_discovery(run.id, "done")

    monkeypatch.setattr("app.discovery.runner.run", fake_run)

    await repo.request_discovery({})
    settings = Settings(_env_file=None, poll_interval_seconds=0.01)
    worker = Worker(repo, FakeBrowser(), settings)

    task = asyncio.create_task(worker.run())
    for _ in range(200):
        await asyncio.sleep(0.005)
        if "id" in ran:
            break
    worker.stop()
    await asyncio.wait_for(task, timeout=1.0)

    assert "id" in ran
    assert await repo.has_active_discovery() is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_worker_discovery.py -v`
Expected: FAIL (worker never claims discovery).

- [ ] **Step 3: Add the worker branch**

In `app/worker.py`, add the import near the top (with the other `app` imports):

```python
from app.discovery import runner as discovery_runner
```

In `Worker.run()`, insert the discovery check **after** the pause block and **before** `job = await self.repo.claim_next()`:

```python
            run = await self.repo.claim_discovery()
            if run is not None:
                self.state = ServiceState.DISCOVERING
                try:
                    await discovery_runner.run(run=run, repo=self.repo, browser=self.browser, settings=self.settings)
                except Exception:
                    log.exception("discovery run %s crashed", run.id)
                continue
```

(`discovery_runner.run` already marks the run `failed` on error before re-raising; the `except` here only prevents the worker loop from dying.)

- [ ] **Step 4: Add startup recovery in the lifespan**

In `app/main.py`, after `await repo_.reset_running_to_queued()` (line 35), add:

```python
        await repo_.fail_interrupted_discovery()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_worker_discovery.py -v`
Expected: PASS.

- [ ] **Step 6: Run the existing worker suite (no regressions)**

Run: `python -m pytest tests/test_worker.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add app/worker.py app/main.py tests/test_worker_discovery.py
git commit -m "feat(discovery): worker runs pending discovery + startup recovery"
```

---

### Task 10: API endpoints

**Files:**
- Modify: `scraper-py/app/api/routes.py` (new endpoints; `DISCOVERING` already covered by `worker.state`)
- Test: `scraper-py/tests/test_api_discovery.py`

**Interfaces:**
- Consumes: `DiscoveryStartRequest`, `DiscoveryRun`, `Job` (Task 2); repo discovery methods (Task 6); `worker.notify_enqueued()` (existing).
- Produces endpoints:
  - `POST /discover` → `DiscoveryRun`. 409 if `count_active_jobs() > 0` or `has_active_discovery()`. Builds `params` from the request (only non-`None` fields), `request_discovery(params)`, `worker.notify_enqueued()`.
  - `GET /discover` → `list[DiscoveryRun]`.
  - `GET /discover/{run_id}` → `DiscoveryRun` (404 if unknown).
  - `POST /discover/{run_id}/cancel` → `{"canceled": run_id}` (404 if unknown; 409 if not cancelable).
  - `POST /discover/enqueue` → `list[Job]`. Enqueues `discovered_routes(exclude_succeeded=True)` via existing `repo.enqueue`, then `worker.notify_enqueued()`.

- [ ] **Step 1: Write the failing test**

```python
# scraper-py/tests/test_api_discovery.py
import pytest
import pytest_asyncio
from fastapi.testclient import TestClient

from app import db
from app.config import Settings
from app.main import create_app
from app.repo import JobRepo


class FakeWorker:
    def __init__(self):
        self.notified = False
        from app.models import ServiceState
        self.state = ServiceState.IDLE
        self.current_job_id = None

    def notify_enqueued(self):
        self.notified = True

    class _B:
        async def is_logged_in(self):
            return True

    browser = _B()


@pytest_asyncio.fixture
async def client():
    conn = await db.connect(":memory:")
    await db.init_schema(conn)
    repo = JobRepo(conn)
    app = create_app(repo=repo, worker=FakeWorker(), settings=Settings(_env_file=None))
    yield TestClient(app), repo
    await conn.close()


async def test_discover_start_and_get(client):
    c, repo = client
    r = c.post("/discover", json={"max_slices": 3, "genres": [4]})
    assert r.status_code == 200
    run_id = r.json()["id"]
    assert r.json()["state"] == "requested"
    assert r.json()["params"]["genres"] == [4]

    # 409 while one is active
    assert c.post("/discover", json={}).status_code == 409

    got = c.get(f"/discover/{run_id}")
    assert got.status_code == 200 and got.json()["id"] == run_id
    assert c.get("/discover").json()[0]["id"] == run_id


async def test_discover_rejected_when_jobs_active(client):
    c, repo = client
    await repo.enqueue(tab_id="a/b-official-1", url="https://tabs.ultimate-guitar.com/tab/a/b-official-1", max_attempts=3)
    assert c.post("/discover", json={}).status_code == 409


async def test_discover_cancel(client):
    c, repo = client
    run_id = c.post("/discover", json={}).json()["id"]
    assert c.post(f"/discover/{run_id}/cancel").status_code == 200
    assert c.post("/discover/does-not-exist/cancel").status_code == 404


async def test_discover_enqueue(client):
    c, repo = client
    await repo.upsert_tab_metadata("run1", {
        "id": 5, "tab_url": "https://tabs.ultimate-guitar.com/tab/a/b-official-5"})
    r = c.post("/discover/enqueue")
    assert r.status_code == 200
    assert [j["tab_id"] for j in r.json()] == ["a/b-official-5"]
    assert (await repo.queue_depth()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_api_discovery.py -v`
Expected: FAIL (routes return 404 — endpoints don't exist).

- [ ] **Step 3: Implement the endpoints**

In `app/api/routes.py`, extend the model import:

```python
from app.models import (
    BulkEnqueueRequest,
    DiscoveryRun,
    DiscoveryStartRequest,
    EnqueueRequest,
    Job,
    StatusResponse,
)
```

Append the endpoints at the end of the file:

```python
@router.post("/discover", response_model=DiscoveryRun)
async def discover_start(
    req: DiscoveryStartRequest, request: Request, _=Depends(require_api_key)
):
    repo, worker = _repo(request), _worker(request)
    if await repo.count_active_jobs() > 0:
        raise HTTPException(status_code=409, detail="queue not empty")
    params = req.model_dump(exclude_none=True)
    run = await repo.request_discovery(params)
    if run is None:
        raise HTTPException(status_code=409, detail="discovery already active")
    worker.notify_enqueued()
    return run


@router.get("/discover", response_model=list[DiscoveryRun])
async def discover_list(request: Request, limit: int = 20, _=Depends(require_api_key)):
    return await _repo(request).list_discovery_runs(limit=limit)


@router.get("/discover/{run_id}", response_model=DiscoveryRun)
async def discover_get(run_id: str, request: Request, _=Depends(require_api_key)):
    run = await _repo(request).get_discovery_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="discovery run not found")
    return run


@router.post("/discover/{run_id}/cancel")
async def discover_cancel(run_id: str, request: Request, _=Depends(require_api_key)):
    repo = _repo(request)
    if await repo.get_discovery_run(run_id) is None:
        raise HTTPException(status_code=404, detail="discovery run not found")
    if not await repo.request_discovery_cancel(run_id):
        raise HTTPException(status_code=409, detail="discovery run not cancelable")
    return {"canceled": run_id}


@router.post("/discover/enqueue", response_model=list[Job])
async def discover_enqueue(request: Request, _=Depends(require_api_key)):
    repo, worker, settings = _repo(request), _worker(request), _settings(request)
    routes = await repo.discovered_routes(exclude_succeeded=True)
    out = []
    for tab_id, url in routes:
        out.append(await repo.enqueue(
            tab_id=tab_id, url=url, max_attempts=settings.max_attempts,
        ))
    if out:
        worker.notify_enqueued()
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_api_discovery.py -v`
Expected: PASS (all four tests).

- [ ] **Step 5: Run the full suite (no regressions)**

Run: `python -m pytest`
Expected: PASS (integration excluded by default).

- [ ] **Step 6: Commit**

```bash
git add app/api/routes.py tests/test_api_discovery.py
git commit -m "feat(discovery): /discover endpoints"
```

---

### Task 11: Documentation

**Files:**
- Create: `docs/scraper-py/discovery.md`
- Modify: `docs/scraper-py/overview.md` (layout + component table; retire "No search by artist/title" line)
- Modify: `docs/scraper-py/api.md` (new endpoints + `DISCOVERING`)
- Modify: `docs/scraper-py/queue-and-worker.md` (new tables, `DISCOVERING` state, worker discovery branch, startup recovery)
- Modify: `docs/scraper-py/configuration.md` (new `DISCOVERY_*` keys)
- Modify: `OVERVIEW.md` (add discovery doc to the map)

**Interfaces:** none (docs only). This task documents everything built in Tasks 1–10.

- [ ] **Step 1: Write `docs/scraper-py/discovery.md`**

Cover, in prose matching the existing doc style: purpose (enumerate Pro tabs, persist UG metadata, no enqueue/decrypt); the endpoint-triggered + worker-owns-browser model and the empty-queue precondition; the `app/discovery/` modules (`parser`, `facets`, `planner`, `runner`) and the `browser/discover.py` seam; the 1000-cap and the adaptive genre→ladder→sort-windows strategy with dedup by numeric id; the `tab_metadata` / `discovery_runs` tables; the `DISCOVERY_*` config keys; and the explicit non-goals (no enrichment, no audio, output contract untouched). Link back to `overview.md`, `api.md`, `queue-and-worker.md`, `configuration.md`. Note the brittle point: the `js-store data-content` shape (raises `DiscoveryParseError`).

- [ ] **Step 2: Update `overview.md`**

Add `discovery/` (with `parser.py`, `facets.py`, `planner.py`, `runner.py`) and `browser/discover.py` to the Project layout block; add a "Discovery" row to the Component docs table pointing to `discovery.md`; change the YAGNI line **"No search by artist/title — jobs are exact tab URLs/routes."** to note that discovery now enumerates Pro tabs via the explore listing (enqueue remains a separate step).

- [ ] **Step 3: Update `api.md`**

Add the five `/discover*` endpoints to the endpoints table and a short "Discovery" section (start/list/get/cancel/enqueue, the 409 preconditions, request body = `DiscoveryStartRequest` overrides). Note `state` can now be `discovering`.

- [ ] **Step 4: Update `queue-and-worker.md`**

Add `tab_metadata` and `discovery_runs` table descriptions; add `DISCOVERING` to the `ServiceState` list; document that the worker checks `claim_discovery()` before `claim_next()`; add `fail_interrupted_discovery()` to the startup-recovery note alongside `reset_running_to_queued()`.

- [ ] **Step 5: Update `configuration.md` and `OVERVIEW.md`**

In `configuration.md`, document each `DISCOVERY_*` key with its default (values from Task 1). In `OVERVIEW.md`, add `docs/scraper-py/discovery.md` to the documentation map table(s).

- [ ] **Step 6: Verify links and commit**

Run: `python -m pytest` (confirm still green after the whole feature)
Expected: PASS.

```bash
git add docs/scraper-py/discovery.md docs/scraper-py/overview.md docs/scraper-py/api.md docs/scraper-py/queue-and-worker.md docs/scraper-py/configuration.md OVERVIEW.md
git commit -m "docs(discovery): document discovery component, endpoints, config"
```

---

## Self-Review

**Spec coverage:**
- Browser-ownership via worker + empty-queue precondition → Tasks 9, 10. ✓
- `parser.py` / `facets.py` / `planner.py` / `runner.py` → Tasks 3, 4, 5, 8. ✓
- Adaptive subdivision + sort windows + dedup by numeric id + untagged sweep → Tasks 5, 8. ✓
- `BrowserSession.fetch_explore` in-page fetch + `goto` fallback → Task 7. ✓
- `tab_metadata` + `discovery_runs` tables, all repo methods, `count_active_jobs`, `enqueue_discovered` path (`discovered_routes` + `/discover/enqueue`) → Tasks 2, 6, 10. ✓
- `DISCOVERING` state + startup recovery → Tasks 2, 9. ✓
- `/discover`, `/discover/{id}`, `/discover/{id}/cancel`, `/discover/enqueue`, list → Task 10. ✓
- `DISCOVERY_*` config → Task 1. ✓
- Persist-only (no auto-enqueue); output contract/decoder untouched → no task writes under `OUTPUT_DIR` or touches `output.py`/`decoder-rs`. ✓
- Tests browser-free by default; fake `BrowserSession`; injectable clock → Tasks 3–10. ✓
- Docs (new `discovery.md` + updates) → Task 11. ✓

**Placeholder scan:** Task 6's test intentionally contains a stray illustrative line that Step 4 explicitly removes before running — called out, not a silent placeholder. No `TBD`/`TODO`/"handle edge cases" left.

**Type consistency:** `parse_explore_html → ExploreStore`; `catalog_from_store(store)` consumes `ExploreStore`; `SliceSpec`/`build_query`/`initial_slices`/`subdivide`/`sort_windows` signatures match across Tasks 4, 5, 8; `runner.run(browser, repo, run, settings, *, sleep=...)` matches the worker call in Task 9 (keyword args) and tests; repo method names match their callers (`claim_discovery`, `upsert_tab_metadata`, `discovered_routes`, `count_active_jobs`, `has_active_discovery`, `fail_interrupted_discovery`, `is_discovery_cancel_requested`). `DiscoveryStartRequest` fields match `_resolve` keys in the runner.

One known coverage caveat carried from the spec: enumeration is **near-exhaustive, not provably complete** (a slice exceeding 1000 in every sort window after ladder exhaustion still truncates; the runner falls back to sort windows but does not guarantee total coverage). The per-artist backbone remains future work.
