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


def _tab_list(raw) -> list[dict]:
    # UG nests the tab rows under data.data.tabs (alongside a parallel `hits`
    # list). Older/simplified payloads put the rows directly in data.data as a
    # bare list — accept both so a shape change degrades gracefully.
    if isinstance(raw, dict):
        raw = raw.get("tabs")
    if not isinstance(raw, list):
        return []
    return [row for row in raw if isinstance(row, dict)]


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
        tabs=_tab_list(data.get("data")),
        pages=int(pagination.get("pages", 0)),
        per_page=int(pagination.get("per_page", 0)),
        current_page=int(pagination.get("current", 0)),
        total_results=int(data.get("totalResults", 0)),
        filters=list(data.get("filters") or []),
        order=dict(data.get("order") or {}),
    )
