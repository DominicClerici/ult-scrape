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
        if spec.order is not None and first_store.total_results > PAGES_CAP * (first_store.per_page or 50):
            log.warning(
                "slice %s is saturated: total_results=%d exceeds ~%d reachable; some tabs will be missed",
                spec.label(),
                first_store.total_results,
                PAGES_CAP * (first_store.per_page or 50),
            )
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
