from __future__ import annotations

import random
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from playwright.sync_api import (
    BrowserContext,
    ConsoleMessage,
    Locator,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)


CLOUDFLARE_WAIT_TIMEOUT_MS = 120_000
CLOUDFLARE_POLL_INTERVAL_MS = 1000
CLOUDFLARE_CHALLENGE_SELECTOR = ", ".join(
    (
        "iframe[src*='challenges.cloudflare.com']",
        "iframe[src*='turnstile']",
        "input[name='cf-turnstile-response']",
        ".cf-turnstile",
        "[id*='cf-chl']",
        "[class*='cf-chl']",
    )
)
CLOUDFLARE_CHALLENGE_TEXT = (
    "verify you are human",
    "checking if the site connection is secure",
    "cloudflare",
)


class CloudflareChallengeTimeout(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Timed out waiting for the Cloudflare challenge to clear.")


class BrowserConsoleLogger:
    def __init__(self, log_path: Path) -> None:
        self.log_path = log_path.resolve()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.log_path.open("a", encoding="utf-8")
        self._attached_page_ids: set[int] = set()

    def attach_to_context(self, context: BrowserContext) -> None:
        context.on("page", self.attach_to_page)

    def attach_to_page(self, page: Page) -> None:
        page_id = id(page)
        if page_id in self._attached_page_ids:
            return

        self._attached_page_ids.add(page_id)
        page.on("console", lambda message: self.write_console_message(page, message))
        page.on("pageerror", lambda error: self.write_page_error(page, error))

    def write_console_message(self, page: Page, message: ConsoleMessage) -> None:
        location = message.location
        source = location.get("url") or page.url or "unknown"
        line = location.get("lineNumber") or 0
        column = location.get("columnNumber") or 0
        self.write(
            f"[{timestamp()}] [console:{message.type}] "
            f"{source}:{line}:{column} {message.text}"
        )

    def write_page_error(self, page: Page, error: Any) -> None:
        self.write(f"[{timestamp()}] [pageerror] {page.url or 'unknown'} {error}")

    def write(self, line: str) -> None:
        self._file.write(f"{line}\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%dT%H:%M:%S")


def file_timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def human_pause(min_ms: int = 120, max_ms: int = 420) -> None:
    time.sleep(random_int(min_ms, max_ms) / 1000)


def wait_for_load_or_pause(page: Page, min_ms: int = 10_000, max_ms: int = 15_000) -> None:
    try:
        page.wait_for_load_state("load", timeout=random_int(min_ms, max_ms))
    except PlaywrightTimeoutError:
        pass


def is_cloudflare_wall(page: Page) -> bool:
    url = page.url.lower()
    if "challenges.cloudflare.com" in url or "/cdn-cgi/challenge-platform/" in url:
        return True

    try:
        if page.locator(CLOUDFLARE_CHALLENGE_SELECTOR).count() > 0:
            return True
    except Exception:
        pass

    try:
        title = page.title().lower()
    except Exception:
        title = ""

    if "just a moment" in title or "attention required" in title:
        return True

    try:
        body_text = page.locator("body").inner_text(timeout=1000).lower()
    except Exception:
        body_text = ""

    return any(text in body_text for text in CLOUDFLARE_CHALLENGE_TEXT)


def wait_for_cloudflare_wall(page: Page, logger: BrowserConsoleLogger | None = None) -> None:
    if not is_cloudflare_wall(page):
        return

    print("Cloudflare challenge detected. Waiting up to 2 minutes for it to clear...")
    logger and logger.write(f"[{timestamp()}] [cloudflare:detected] {page.url}")
    deadline = time.monotonic() + CLOUDFLARE_WAIT_TIMEOUT_MS / 1000

    while time.monotonic() < deadline:
        time.sleep(CLOUDFLARE_POLL_INTERVAL_MS / 1000)
        if not is_cloudflare_wall(page):
            print("Cloudflare challenge cleared. Continuing.")
            logger and logger.write(f"[{timestamp()}] [cloudflare:cleared] {page.url}")
            return

    logger and logger.write(f"[{timestamp()}] [cloudflare:timeout] {page.url}")
    raise CloudflareChallengeTimeout()


def human_click(page: Page, locator: Locator, timeout: int = 10_000) -> None:
    locator.wait_for(state="visible", timeout=timeout)
    box = locator.bounding_box()

    if box:
        target_x = box["x"] + box["width"] * random_float(0.35, 0.65)
        target_y = box["y"] + box["height"] * random_float(0.35, 0.65)
        current_x = target_x + random_float(-180, 180)
        current_y = target_y + random_float(-90, 90)

        page.mouse.move(current_x, current_y)
        human_pause(80, 220)
        page.mouse.move(target_x, target_y, steps=random_int(8, 18))
        human_pause(90, 260)
        page.mouse.down()
        human_pause(45, 130)
        page.mouse.up()
        return

    locator.click()


def human_type(page: Page, locator: Locator, text: str) -> None:
    locator.wait_for(state="visible")
    human_click(page, locator)
    human_pause(120, 300)

    for char in text:
        locator.type(char, delay=random_int(45, 170))
        if random.random() < 0.08:
            human_pause(180, 520)


def random_int(min_value: int, max_value: int) -> int:
    return int(random_float(min_value, max_value + 1))


def random_float(min_value: float, max_value: float) -> float:
    return random.random() * (max_value - min_value) + min_value
