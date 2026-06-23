from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from app.browser.base import CapturedArtifact
from app.browser.humanize import wait_for_cloudflare_wall, wait_for_load_or_pause
from app.browser.login import is_logged_in
from app.errors import (
    PermanentScrapeError,
    SessionExpiredError,
    TransientScrapeError,
)

CAPTURE_URL_PARTS = ("/download/public/", "/tab/download/file")
CAPTURE_HEADER_NAMES = (
    "content-disposition",
    "content-encoding",
    "content-length",
    "content-type",
    "location",
)
XTZ_MAGIC = b"XTZ\x00"


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


# Pulls the tab's song fields out of UG's page data. Returns the raw candidate
# fields (or null) — normalization happens in Python (_song_block).
#
# UG ships the page store as a JSON blob in a `.js-store` element's data-content
# attribute, then its JS bundle parses that into `window.UGAPP.store` and removes
# the element. By the time this runs (after load), the bundle has usually already
# stripped `.js-store` from the live DOM *and* `window.UGAPP` is frequently not
# yet/no longer readable — so reading the live page yields nothing. The reliable
# source is the server HTML itself: re-fetch it from the same authenticated
# session (as discover.py does for explore) and parse the still-present blob.
# The live window store is tried first as a no-extra-request fast path.
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


async def extract_song_meta(page) -> dict | None:
    """Read UG's page store (see `_SONG_META_JS`) and return the `song` block.

    Best-effort: any failure (no store, navigation error, unexpected shape) is
    swallowed so the primary job — capturing the `.xtz` — is never jeopardized.
    """
    try:
        raw = await page.evaluate(_SONG_META_JS)
    except Exception:
        return None
    return _song_block(raw)


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

        if not await is_logged_in(page):
            raise SessionExpiredError(f"not logged in on {tab_url}")
        if resp is not None and resp.status == 404:
            raise PermanentScrapeError(f"tab not found (404): {tab_url}")

        song = await extract_song_meta(page)

        await page.wait_for_timeout(capture_window_ms)

        artifacts: list[CapturedArtifact] = []
        for r in captured:
            if 300 <= r.status < 400:
                continue
            try:
                body = await r.body()
            except Exception:
                continue
            if not body.startswith(XTZ_MAGIC):
                continue
            artifacts.append(CapturedArtifact(
                filename=_filename(r.url, r.headers, body),
                data=body,
                source_url=r.url,
                http_status=r.status,
                content_headers=_selected_headers(r.headers),
            ))

        if not artifacts:
            raise TransientScrapeError(f"no XTZ download captured for {tab_url}")
        return artifacts, song
    finally:
        page.remove_listener("response", on_response)
