from __future__ import annotations

import asyncio
import random

from playwright.async_api import TimeoutError as PWTimeout

CF_POLL_MS = 1000
CF_SELECTOR = ", ".join((
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[src*='turnstile']",
    "input[name='cf-turnstile-response']",
    ".cf-turnstile",
    "[id*='cf-chl']",
    "[class*='cf-chl']",
))
CF_TEXT = (
    "verify you are human",
    "checking if the site connection is secure",
    "cloudflare",
)


class CloudflareTimeout(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Timed out waiting for the Cloudflare challenge to clear.")


def _rand_int(a: int, b: int) -> int:
    return int(random.random() * (b + 1 - a) + a)


def _rand_float(a: float, b: float) -> float:
    return random.random() * (b - a) + a


async def human_pause(min_ms: int = 120, max_ms: int = 420) -> None:
    await asyncio.sleep(_rand_int(min_ms, max_ms) / 1000)


async def wait_for_load_or_pause(page, min_ms: int = 10_000, max_ms: int = 15_000) -> None:
    try:
        await page.wait_for_load_state("load", timeout=_rand_int(min_ms, max_ms))
    except PWTimeout:
        pass


async def is_cloudflare_wall(page) -> bool:
    url = page.url.lower()
    if "challenges.cloudflare.com" in url or "/cdn-cgi/challenge-platform/" in url:
        return True
    try:
        if await page.locator(CF_SELECTOR).count() > 0:
            return True
    except Exception:
        pass
    try:
        title = (await page.title()).lower()
    except Exception:
        title = ""
    if "just a moment" in title or "attention required" in title:
        return True
    try:
        body = (await page.locator("body").inner_text(timeout=1000)).lower()
    except Exception:
        body = ""
    return any(t in body for t in CF_TEXT)


async def wait_for_cloudflare_wall(page, timeout_ms: int = 120_000) -> None:
    if not await is_cloudflare_wall(page):
        return
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_ms / 1000
    while loop.time() < deadline:
        await asyncio.sleep(CF_POLL_MS / 1000)
        if not await is_cloudflare_wall(page):
            return
    raise CloudflareTimeout()


async def human_click(page, locator, timeout: int = 10_000) -> None:
    await locator.wait_for(state="visible", timeout=timeout)
    box = await locator.bounding_box()
    if box:
        tx = box["x"] + box["width"] * _rand_float(0.35, 0.65)
        ty = box["y"] + box["height"] * _rand_float(0.35, 0.65)
        await page.mouse.move(tx + _rand_float(-180, 180), ty + _rand_float(-90, 90))
        await human_pause(80, 220)
        await page.mouse.move(tx, ty, steps=_rand_int(8, 18))
        await human_pause(90, 260)
        await page.mouse.down()
        await human_pause(45, 130)
        await page.mouse.up()
        return
    await locator.click()


async def human_type(page, locator, text: str) -> None:
    await locator.wait_for(state="visible")
    await human_click(page, locator)
    await human_pause(120, 300)
    for ch in text:
        await locator.type(ch, delay=_rand_int(45, 170))
        if random.random() < 0.08:
            await human_pause(180, 520)
