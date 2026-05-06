"""One-shot helper: open a Pro tab page in the authenticated Camoufox profile
and dump every JavaScript response (and inline scripts) to PY/js_dump/.

Used to grab the Pro-tab viewer bundles that aren't reachable via unauth fetch,
so we can reverse-engineer the XTZ -> .gp decoder.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from urllib.parse import urlparse

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
from playwright.sync_api import Response

from common import (
    BrowserConsoleLogger,
    CloudflareChallengeTimeout,
    file_timestamp,
    timestamp,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)
from login import attach_auth_diagnostics, login

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UG_PROXY = os.getenv("UG_PROXY")
CAMOUFOX_DISABLE_COOP = os.getenv("CAMOUFOX_DISABLE_COOP", "").lower() in {
    "1", "true", "yes", "on",
}

TAB_URL = "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943"
BROWSER_PROFILE_DIR = BASE_DIR / "camoufox-profile"
DUMP_DIR = BASE_DIR / "js_dump" / file_timestamp()


def safe_name(url: str) -> str:
    p = urlparse(url)
    raw = (p.netloc + p.path).strip("/") or "root"
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", raw)[:160]


def main() -> None:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    index_path = DUMP_DIR / "index.jsonl"
    console_logger = BrowserConsoleLogger(BASE_DIR / f"{file_timestamp()}-dumpjs-logs.txt")

    options = {
        "headless": False,
        "humanize": True,
        "persistent_context": True,
        "user_data_dir": str(BROWSER_PROFILE_DIR),
        "os": "windows",
        "locale": "en-US",
        "block_webrtc": True,
    }
    if UG_PROXY:
        options["proxy"] = {"server": UG_PROXY}
        options["geoip"] = True
    if CAMOUFOX_DISABLE_COOP:
        options["disable_coop"] = True
        options["i_know_what_im_doing"] = True

    saved: set[str] = set()

    def on_response(response: Response) -> None:
        url = response.url
        ctype = (response.headers.get("content-type") or "").lower()
        is_js = "javascript" in ctype or url.split("?", 1)[0].endswith(".js")
        if not is_js:
            return
        if url in saved:
            return
        try:
            body = response.body()
        except Exception as e:
            console_logger.write(f"[{timestamp()}] [dump:body-err] {url} {e}")
            return
        digest = hashlib.sha256(body).hexdigest()[:12]
        out = DUMP_DIR / f"{safe_name(url)}.{digest}.js"
        out.write_bytes(body)
        saved.add(url)
        with index_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "url": url, "status": response.status, "bytes": len(body),
                "sha256_12": digest, "path": str(out),
            }) + "\n")

    try:
        with Camoufox(**options) as context:
            console_logger.attach_to_context(context)
            page = context.pages[0] if context.pages else context.new_page()
            console_logger.attach_to_page(page)
            page.on("response", on_response)

            attach_auth_diagnostics(page, console_logger)
            if not login(page, console_logger):
                print("Login failed.")
                return

            print(f"Navigating to {TAB_URL}")
            page.goto(TAB_URL, wait_until="domcontentloaded", timeout=60_000)
            wait_for_load_or_pause(page)
            wait_for_cloudflare_wall(page, console_logger)
            page.wait_for_timeout(30_000)

            inline_html = page.content()
            (DUMP_DIR / "page.html").write_text(inline_html, encoding="utf-8")

            print(f"Saved {len(saved)} JS responses to {DUMP_DIR}")
    except CloudflareChallengeTimeout as e:
        print(e)
    finally:
        console_logger.close()


if __name__ == "__main__":
    main()
