from __future__ import annotations

import logging
import re

from playwright.async_api import TimeoutError as PWTimeout

from app.browser.humanize import (
    human_click,
    human_pause,
    human_type,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)

log = logging.getLogger(__name__)

PROFILE_HREF = "https://www.ultimate-guitar.com/u/tnd29hh6r4"
PROFILE_SELECTOR = f'[href="{PROFILE_HREF}"]'


async def is_logged_in(page) -> bool:
    try:
        return await page.locator(PROFILE_SELECTOR).count() > 0
    except Exception:
        return False


async def login(page, email: str, password: str, cf_timeout_ms: int) -> bool:
    if not email or not password:
        raise RuntimeError("UG_EMAIL and UG_PASSWORD must be set before logging in.")

    await page.goto(
        "https://www.ultimate-guitar.com/",
        wait_until="domcontentloaded", timeout=60_000,
    )
    await wait_for_load_or_pause(page)
    await wait_for_cloudflare_wall(page, cf_timeout_ms)
    await human_pause(700, 1600)

    if await is_logged_in(page):
        log.info("already logged in")
        return True

    login_button = page.locator(
        "button[type='button'][tabindex='0'][data-react-aria-pressable='true']",
        has=page.locator("span", has_text=re.compile(r"^Log in$", re.IGNORECASE)),
    )

    try:
        await human_click(page, login_button, timeout=20_000)
        await human_pause(800, 1400)

        email_input = page.locator(
            "input[name='username'][placeholder='Username or e-mail']"
        )
        password_input = page.locator(
            "input[name='password'][placeholder='Password']"
        )
        await email_input.wait_for(state="visible")
        await human_pause(250, 650)

        await human_type(page, email_input, email)
        await human_pause(250, 700)
        await human_type(page, password_input, password)
        await human_pause(2050, 4275)

        await human_click(
            page,
            page.locator(
                "button[type='submit']",
                has=page.locator(
                    "span", has_text=re.compile(r"^Log in$", re.IGNORECASE)
                ),
            ),
            timeout=20_000,
        )
        await human_pause(4050, 8275)
    except PWTimeout as e:
        log.warning("login timed out: %s", e)
        return False

    if await is_logged_in(page):
        log.info("logged in")
        return True

    try:
        await page.wait_for_selector(
            "input[name='username'][placeholder='Username or e-mail']",
            state="detached", timeout=30_000,
        )
        return True
    except Exception:
        return False
