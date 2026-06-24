from __future__ import annotations

import html
import json
import logging
import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.browser.base import CapturedArtifact
from app.browser.humanize import wait_for_cloudflare_wall, wait_for_load_or_pause
from app.browser.login import is_logged_in
from app.errors import (
    PermanentScrapeError,
    RateLimitScrapeError,
    SessionExpiredError,
    TransientScrapeError,
)

CAPTURE_URL_PARTS = ("/download/public/", "/tab/download/file")
RATE_LIMIT_STATUSES = (403, 429)
CAPTURE_HEADER_NAMES = (
    "content-disposition",
    "content-encoding",
    "content-length",
    "content-type",
    "location",
)
XTZ_MAGIC = b"XTZ\x00"

log = logging.getLogger(__name__)


def _raise_for_rate_limit(status: int | None, tab_url: str) -> None:
    if status in RATE_LIMIT_STATUSES:
        raise RateLimitScrapeError(f"rate limited (HTTP {status}) on {tab_url}")


def _captured_rate_limit_status(captured) -> int | None:
    """Return a 403/429 status among the captured download responses, if any.

    The tab page itself can return 200 (logged in, not blocked) while UG blocks
    the `/download/public/` endpoint with a 403/429 block page. The main-page
    `_raise_for_rate_limit` never sees that, so the download responses are checked
    separately. 3xx redirects are part of the normal flow and ignored.
    """
    for r in captured:
        if 300 <= r.status < 400:
            continue
        if r.status in RATE_LIMIT_STATUSES:
            return r.status
    return None


def _should_capture(url: str) -> bool:
    p = urlparse(url)
    if p.netloc != "tabs.ultimate-guitar.com":
        return False
    return any(part in p.path for part in CAPTURE_URL_PARTS)


def _selected_headers(headers: dict) -> dict:
    low = {k.lower(): v for k, v in headers.items()}
    return {n: low[n] for n in CAPTURE_HEADER_NAMES if n in low}


def _safe(value: str, fallback: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    return s[:120] or fallback


def _filename(response_url: str, headers: dict, body: bytes) -> str:
    low = {k.lower(): v for k, v in headers.items()}
    disp = low.get("content-disposition", "")
    m = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disp, re.IGNORECASE)
    if m:
        base = unquote(m.group(1))
    else:
        parsed = urlparse(response_url)
        base = Path(parsed.path).name or "response"
        if base == "file":
            ssid = parse_qs(parsed.query).get("ssid", [""])[0]
            base = f"tab-download-ssid-{ssid}" if ssid else base
    base = _safe(base, "response")
    if Path(base).suffix == "":
        base = f"{base}.xtz"
    return base


# Fallback song-meta reader, used only when the navigation document body did not
# carry the store (see `extract_song_meta`). Pulls the tab's song fields out of
# UG's page data and returns the raw candidate fields (or null) — normalization
# happens in Python (_song_block).
#
# UG ships the page store as a JSON blob in a `.js-store` element's data-content
# attribute, then its JS bundle parses that into `window.UGAPP.store` and removes
# the element. By the time this runs (after load), the bundle has usually already
# stripped `.js-store` from the live DOM *and* `window.UGAPP` is frequently not
# yet/no longer readable — so reading the live page yields nothing. As a last
# resort the server HTML is re-fetched from the same authenticated session (as
# discover.py does for explore) and the still-present blob is parsed. The live
# window store is tried first as a no-extra-request fast path.
_SONG_META_JS = """async () => {
  const pageData = (root) =>
    root && root.store && root.store.page && root.store.page.data;
  const extract = (d) => {
    if (!d) return null;
    const tab = d.tab || {};
    const meta = (d.tab_view && d.tab_view.meta) || {};
    const rec = tab.recording || {};
    return {
      artist_name: tab.artist_name ?? null,
      artist_id: tab.artist_id ?? null,
      song_name: tab.song_name ?? null,
      song_id: tab.song_id ?? null,
      album_id: rec.album_id ?? null,
      tonality: meta.tonality ?? null,
      tuning: meta.tuning ?? null,
    };
  };
  try {
    let d = window.UGAPP && pageData(window.UGAPP);
    if (!d) {
      const r = await fetch(location.href, { credentials: 'include' });
      const html = await r.text();
      const el = new DOMParser()
        .parseFromString(html, 'text/html')
        .querySelector('.js-store');
      if (el) d = pageData(JSON.parse(el.getAttribute('data-content')));
    }
    return extract(d);
  } catch (e) { return null; }
}"""

_SONG_SCALAR_FIELDS = (
    "artist_name", "artist_id", "song_name", "song_id", "album_id", "tonality",
)


def _song_block(raw) -> dict | None:
    """Normalize UG's raw song fields into the additive metadata `song` block.

    Drops null/blank fields, flattens the tuning object to its string value, and
    returns None unless both `artist_name` and `song_name` are present (anything
    less is useless to the enricher, which keys its fallback on those two).
    """
    if not isinstance(raw, dict):
        return None
    block: dict = {}
    for key in _SONG_SCALAR_FIELDS:
        val = raw.get(key)
        if isinstance(val, str):
            val = val.strip()
        if val in (None, ""):
            continue
        block[key] = val
    tuning = raw.get("tuning")
    if isinstance(tuning, dict):
        tuning = tuning.get("value")
    if isinstance(tuning, str) and tuning.strip():
        block["tuning"] = tuning.strip()
    if not block.get("artist_name") or not block.get("song_name"):
        return None
    return block


# Same `.js-store data-content` blob the explore parser reads (parser.py), but
# on a tab page rather than the listing.
_STORE_RE = re.compile(r'class="js-store"[^>]*\sdata-content="([^"]*)"')


def _raw_song_from_store(data) -> dict | None:
    """Mirror `_SONG_META_JS`'s `extract`: pull the raw candidate fields out of a
    decoded `store.page.data` dict. Returns None if the shape isn't a dict."""
    if not isinstance(data, dict):
        return None
    tab = data.get("tab") or {}
    meta = (data.get("tab_view") or {}).get("meta") or {}
    rec = tab.get("recording") or {}
    return {
        "artist_name": tab.get("artist_name"),
        "artist_id": tab.get("artist_id"),
        "song_name": tab.get("song_name"),
        "song_id": tab.get("song_id"),
        "album_id": rec.get("album_id"),
        "tonality": meta.get("tonality"),
        "tuning": meta.get("tuning"),
    }


def _song_from_html(page_html: str) -> dict | None:
    """Parse the `song` block out of a tab page's server HTML. Returns None if the
    `.js-store` blob is absent (e.g. the body was a Cloudflare challenge) or its
    shape is unexpected — callers then fall back to the in-page reader."""
    m = _STORE_RE.search(page_html or "")
    if not m:
        return None
    try:
        data = json.loads(html.unescape(m.group(1)))["store"]["page"]["data"]
    except (ValueError, KeyError, TypeError):
        return None
    return _song_block(_raw_song_from_store(data))


async def extract_song_meta(page, nav_html: str | None = None) -> dict | None:
    """Return the `song` block for the current tab. Best-effort: any failure (no
    store, navigation error, unexpected shape) is swallowed so the primary job —
    capturing the `.xtz` — is never jeopardized.

    Primary source is `nav_html`, the navigation document body the scrape already
    downloaded — the server HTML reliably embeds the `.js-store` blob, so parsing
    it costs no extra request. Only when that body lacks the store (a Cloudflare
    challenge intercepted the navigation) do we fall back to the in-page reader
    (`_SONG_META_JS`: live window store, then a re-fetch).
    """
    if nav_html:
        block = _song_from_html(nav_html)
        if block is not None:
            return block
    try:
        raw = await page.evaluate(_SONG_META_JS)
    except Exception:
        return None
    return _song_block(raw)


_CAPTURE_POLL_MS = 250


async def _wait_for_download(page, captured: list, capture_window_ms: int) -> int:
    """Wait until the actual download (a non-3xx capture) lands, up to the window.

    Returns the moment a usable response appears so fast tabs don't pay the full
    window; caps at `capture_window_ms` for slow players that issue the signed
    `/tab/download/file` request late. The 302 `/download/public/` redirect is a
    3xx, so it never satisfies the wait. Returns the elapsed wait in ms.
    """
    waited = 0
    while waited < capture_window_ms:
        if any(not (300 <= r.status < 400) for r in captured):
            break
        await page.wait_for_timeout(_CAPTURE_POLL_MS)
        waited += _CAPTURE_POLL_MS
    return waited


async def scrape_tab(
    page, tab_url: str, capture_window_ms: int, cf_timeout_ms: int
) -> tuple[list[CapturedArtifact], dict | None]:
    captured = []

    def on_response(response):
        if _should_capture(response.url):
            captured.append(response)

    page.on("response", on_response)
    try:
        resp = await page.goto(
            tab_url, wait_until="domcontentloaded", timeout=60_000
        )
        await wait_for_load_or_pause(page)
        await wait_for_cloudflare_wall(page, cf_timeout_ms)

        _raise_for_rate_limit(resp.status if resp is not None else None, tab_url)
        if not await is_logged_in(page):
            raise SessionExpiredError(f"not logged in on {tab_url}")
        if resp is not None and resp.status == 404:
            raise PermanentScrapeError(f"tab not found (404): {tab_url}")

        nav_html = None
        if resp is not None:
            try:
                nav_html = await resp.text()
            except Exception:
                nav_html = None
        song = await extract_song_meta(page, nav_html)

        waited_ms = await _wait_for_download(page, captured, capture_window_ms)

        log.info(
            "[CAPTURE] %s: %d matching response(s) after %dms (window %dms)",
            tab_url, len(captured), waited_ms, capture_window_ms,
        )
        artifacts: list[CapturedArtifact] = []
        for r in captured:
            headers = r.headers
            ctype = headers.get("content-type", "")
            if 300 <= r.status < 400:
                log.info(
                    "[CAPTURE] skip 3xx (HTTP %d) %s -> location=%s",
                    r.status, r.url, headers.get("location", ""),
                )
                continue
            try:
                body = await r.body()
            except Exception as e:
                log.warning(
                    "[CAPTURE] body read failed (HTTP %d, ct=%s) %s: %r",
                    r.status, ctype, r.url, e,
                )
                continue
            magic_ok = body.startswith(XTZ_MAGIC)
            log.info(
                "[CAPTURE] response HTTP %d ct=%s bytes=%d magic_ok=%s %s",
                r.status, ctype, len(body), magic_ok, r.url,
            )
            if not magic_ok:
                continue
            artifacts.append(CapturedArtifact(
                filename=_filename(r.url, r.headers, body),
                data=body,
                source_url=r.url,
                http_status=r.status,
                content_headers=_selected_headers(r.headers),
            ))

        if not artifacts:
            blocked = _captured_rate_limit_status(captured)
            if blocked is not None:
                raise RateLimitScrapeError(
                    f"rate limited (HTTP {blocked}) on download for {tab_url}"
                )
            raise TransientScrapeError(f"no XTZ download captured for {tab_url}")
        return artifacts, song
    finally:
        page.remove_listener("response", on_response)
