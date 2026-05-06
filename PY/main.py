from __future__ import annotations

import os
from pathlib import Path
from threading import Event
from typing import Any

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
from playwright.sync_api import BrowserContext, Page

from common import (
    BrowserConsoleLogger,
    CloudflareChallengeTimeout,
    file_timestamp,
    timestamp,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)
from login import attach_auth_diagnostics, login
from scrape import scrape


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UG_PROXY = os.getenv("UG_PROXY")
CAMOUFOX_DISABLE_COOP = os.getenv("CAMOUFOX_DISABLE_COOP", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

DEFAULT_TAB_ROUTE = "eagles/hotel-california-official-1910943"
BROWSER_PROFILE_DIR = BASE_DIR / "camoufox-profile"
MANUAL_LOGIN = False


def wait_for_close(context: BrowserContext, page: Page) -> None:
    closed = Event()
    context.on("close", lambda _: closed.set())
    page.on("close", lambda _: closed.set())
    closed.wait()


def create_camoufox_options() -> dict[str, Any]:
    options: dict[str, Any] = {
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

    return options


def main() -> None:
    console_logger = BrowserConsoleLogger(BASE_DIR / f"{file_timestamp()}-logs.txt")

    try:
        options = create_camoufox_options()
        console_logger.write(
            f"[{timestamp()}] [camoufox:launch] "
            f"persistent_context=true os=windows locale=en-US "
            f"proxy_enabled={bool(UG_PROXY)} geoip_enabled={bool(UG_PROXY)} "
            f"disable_coop={CAMOUFOX_DISABLE_COOP}"
        )

        with Camoufox(**options) as context:
            console_logger.attach_to_context(context)

            page = context.pages[0] if context.pages else context.new_page()
            console_logger.attach_to_page(page)

            if MANUAL_LOGIN:
                page.goto("https://www.ultimate-guitar.com/", wait_until="domcontentloaded", timeout=60_000)
                wait_for_load_or_pause(page)
                wait_for_cloudflare_wall(page, console_logger)
                print(
                    "MANUAL_LOGIN is enabled. Log in manually, then close the browser window. "
                    f"Profile: {BROWSER_PROFILE_DIR.resolve()}"
                )
                wait_for_close(context, page)
                return

            attach_auth_diagnostics(page, console_logger)
            if not login(page, console_logger):
                print("Login failed. Exiting before scrape.")
                return

            scrape(page, DEFAULT_TAB_ROUTE, console_logger)
    except CloudflareChallengeTimeout as error:
        print(error)
    finally:
        console_logger.close()


if __name__ == "__main__":
    main()
