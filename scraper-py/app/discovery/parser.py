from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass

from app.errors import DiscoveryParseError

_STORE_RE = re.compile(r'class="js-store"[^>]*\sdata-content="([^"]*)"')

_CF_MARKERS = (
    "just a moment",
    "challenges.cloudflare.com",
    "/cdn-cgi/challenge-platform/",
    "cf-chl",
    "verify you are human",
    "attention required",
)


def _diagnose(page_html: str) -> str:
    low = page_html.lower()
    if any(m in low for m in _CF_MARKERS):
        return "response looks like a Cloudflare challenge"
    if "login" in low and "password" in low:
        return "response looks like a login page"
    return "no js-store in HTML (markup change or unexpected response)"


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
        raise DiscoveryParseError(
            f"js-store data-content not found — {_diagnose(page_html)} "
            f"({len(page_html)} bytes)"
        )
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
