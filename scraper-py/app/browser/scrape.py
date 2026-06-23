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


async def scrape_tab(
    page, tab_url: str, capture_window_ms: int, cf_timeout_ms: int
) -> list[CapturedArtifact]:
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
        return artifacts
    finally:
        page.remove_listener("response", on_response)
