from __future__ import annotations

import logging

from camoufox.async_api import AsyncCamoufox

from app.browser.base import CapturedArtifact
from app.browser.discover import fetch_explore_html
from app.browser.fingerprint import load_or_create_fingerprint
from app.browser.login import is_logged_in, login
from app.browser.scrape import scrape_tab
from app.config import Settings

WINDOW_OS = "windows"
UG_HOME = "https://www.ultimate-guitar.com/"

log = logging.getLogger(__name__)


class CamoufoxBrowserSession:
    def __init__(self, settings: Settings):
        self.s = settings
        self._cm = None
        self._context = None
        self._page = None

    async def start(self) -> None:
        fingerprint = load_or_create_fingerprint(
            self.s.fingerprint_path, WINDOW_OS
        )
        opts = dict(
            headless=self.s.headless,
            humanize=True,
            persistent_context=True,
            user_data_dir=str(self.s.profile_dir),
            os=WINDOW_OS,
            locale="en-US",
            block_webrtc=True,
            fingerprint=fingerprint,
        )
        if self.s.ug_proxy:
            proxy = {"server": self.s.ug_proxy}
            if self.s.ug_proxy_username:
                proxy["username"] = self.s.ug_proxy_username
                proxy["password"] = self.s.ug_proxy_password
            opts["proxy"] = proxy
            opts["geoip"] = True
        self._cm = AsyncCamoufox(**opts)
        self._context = await self._cm.__aenter__()
        self._page = (
            self._context.pages[0]
            if self._context.pages
            else await self._context.new_page()
        )

    async def open_home(self) -> None:
        """Navigate the page to the UG homepage (used by the manual-login flow)."""
        await self._page.goto(
            UG_HOME, wait_until="domcontentloaded", timeout=60_000
        )

    async def ensure_logged_in(self) -> None:
        ok = await login(
            self._page, self.s.ug_email, self.s.ug_password,
            self.s.cloudflare_timeout_ms,
        )
        if not ok:
            raise RuntimeError("UG login failed")

    async def is_logged_in(self) -> bool:
        return await is_logged_in(self._page)

    async def scrape(
        self, tab_url: str
    ) -> tuple[list[CapturedArtifact], dict | None]:
        return await scrape_tab(
            self._page, tab_url, self.s.capture_window_ms,
            self.s.cloudflare_timeout_ms,
        )

    async def fetch_explore(self, query: str) -> str:
        return await fetch_explore_html(
            self._page, query, self.s.discovery_request_timeout_ms
        )

    async def close(self) -> None:
        if self._cm is not None:
            await self._cm.__aexit__(None, None, None)
            self._cm = None
