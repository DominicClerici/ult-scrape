from __future__ import annotations

from playwright.sync_api import Page

from common import BrowserConsoleLogger, wait_for_cloudflare_wall, wait_for_load_or_pause


TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"


def scrape(
    page: Page,
    route: str,
    logger: BrowserConsoleLogger | None = None,
) -> None:
    tab_url = f"{TAB_BASE_URL}/{route.strip('/')}"

    page.goto(tab_url, wait_until="domcontentloaded", timeout=60_000)
    wait_for_load_or_pause(page)
    wait_for_cloudflare_wall(page, logger)

    print(f"Scrape skeleton loaded {tab_url}")
