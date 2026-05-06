from __future__ import annotations

import json
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from threading import Event
from typing import Any

from camoufox.sync_api import Camoufox
from dotenv import load_dotenv
from playwright.sync_api import (
    BrowserContext,
    ConsoleMessage,
    Locator,
    Page,
    Request,
    Response,
    Route,
    TimeoutError as PlaywrightTimeoutError,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UG_EMAIL = os.getenv("UG_EMAIL")
UG_PASSWORD = os.getenv("UG_PASSWORD")
UG_PROXY = os.getenv("UG_PROXY")
CAMOUFOX_DISABLE_COOP = os.getenv("CAMOUFOX_DISABLE_COOP", "").lower() in {
    "1",
    "true",
    "yes",
    "on",
}

TAB_URL = "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943"
OUTPUT_FILE = BASE_DIR / "hotel_california.gp"
DOWNLOAD_FILE_URL = re.compile(r"^https://tabs\.ultimate-guitar\.com/tab/download/file\?")
BROWSER_PROFILE_DIR = BASE_DIR / "camoufox-profile"
MANUAL_LOGIN = False

AUTH_LOG_URL_PARTS = (
    "/user/auth/processSignIn",
    "/v1/user/register/view",
)
REQUEST_HEADER_LOG_NAMES = (
    "accept",
    "content-type",
    "origin",
    "referer",
    "sec-fetch-dest",
    "sec-fetch-mode",
    "sec-fetch-site",
    "user-agent",
    "x-requested-with",
)
RESPONSE_HEADER_LOG_NAMES = (
    "access-control-allow-origin",
    "cache-control",
    "cf-ray",
    "content-type",
    "server",
    "x-cache",
)
AUTH_BODY_LOG_LIMIT = 3000
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


class TimeoutError(RuntimeError):
    def __init__(self, message: str = "Timed out.") -> None:
        super().__init__(message)


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


def is_auth_log_url(url: str) -> bool:
    return any(part in url for part in AUTH_LOG_URL_PARTS)


def selected_headers(headers: dict[str, str], names: tuple[str, ...]) -> dict[str, str]:
    lower_headers = {key.lower(): value for key, value in headers.items()}
    return {name: lower_headers[name] for name in names if name in lower_headers}


def sanitize_log_text(text: str | None) -> str:
    if text is None:
        return ""

    sanitized = text
    for secret, replacement in (
        (UG_PASSWORD, "[REDACTED_PASSWORD]"),
        (UG_EMAIL, "[REDACTED_EMAIL]"),
    ):
        if secret:
            sanitized = sanitized.replace(secret, replacement)

    sanitized = sanitized.replace("\r", "\\r").replace("\n", "\\n")
    if len(sanitized) > AUTH_BODY_LOG_LIMIT:
        return f"{sanitized[:AUTH_BODY_LOG_LIMIT]}... [truncated]"

    return sanitized


def mask_email(value: str | None) -> str:
    if not value:
        return ""

    if "@" not in value:
        return f"{value[:2]}***"

    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 1 else local[:1]
    return f"{visible}***@{domain}"


def attach_auth_diagnostics(page: Page, logger: BrowserConsoleLogger) -> None:
    def on_request(request: Request) -> None:
        if not is_auth_log_url(request.url):
            return

        post_data = request.post_data or ""
        logger.write(
            f"[{timestamp()}] [auth:request] {request.method} {request.url} "
            f"headers={json.dumps(selected_headers(request.headers, REQUEST_HEADER_LOG_NAMES))} "
            f"post_data_length={len(post_data)}"
        )

    def on_response(response: Response) -> None:
        log_auth_response(response, logger)

    def on_request_failed(request: Request) -> None:
        if not is_auth_log_url(request.url):
            return

        logger.write(
            f"[{timestamp()}] [auth:request-failed] {request.method} {request.url} "
            f"failure={json.dumps(request.failure)}"
        )

    page.on("request", on_request)
    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)


def log_auth_response(response: Response, logger: BrowserConsoleLogger) -> None:
    if not is_auth_log_url(response.url):
        return

    logger.write(
        f"[{timestamp()}] [auth:response] {response.status} {response.url} "
        f"headers={json.dumps(selected_headers(response.headers, RESPONSE_HEADER_LOG_NAMES))}"
    )

    try:
        body = response.text()
        logger.write(f"[{timestamp()}] [auth:response-body] {response.url} {sanitize_log_text(body)}")
    except Exception as error:
        logger.write(f"[{timestamp()}] [auth:response-body-error] {response.url} {error}")


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


def login(page: Page, logger: BrowserConsoleLogger | None = None) -> None:
    if not UG_EMAIL or not UG_PASSWORD:
        raise RuntimeError("UG_EMAIL and UG_PASSWORD must be set in PY/.env before logging in.")

    page.goto("https://www.ultimate-guitar.com/", wait_until="domcontentloaded", timeout=60_000)
    wait_for_load_or_pause(page)
    wait_for_cloudflare_wall(page, logger)
    human_pause(700, 1600)

    human_click(
        page,
        page.locator(
            "button[type='button'][tabindex='0'][data-react-aria-pressable='true']",
            has=page.locator("span", has_text=re.compile(r"^Log in$", re.IGNORECASE)),
        ),
        timeout=20_000,
    )

    human_pause(800, 1400)

    email_input = page.locator("input[name='username'][placeholder='Username or e-mail']")
    password_input = page.locator("input[name='password'][placeholder='Password']")
    email_input.wait_for(state="visible")
    human_pause(250, 650)

    human_type(page, email_input, UG_EMAIL)
    human_pause(250, 700)
    human_type(page, password_input, UG_PASSWORD)
    human_pause(2050, 4275)

    email_value = email_input.input_value()
    password_value = password_input.input_value()
    if logger:
        logger.write(
            f"[{timestamp()}] [auth:field-check] "
            f"email={mask_email(email_value)} "
            f"email_matches_env={email_value == UG_EMAIL} "
            f"password_length={len(password_value)} "
            f"password_matches_env={password_value == UG_PASSWORD}"
        )

    human_click(
        page,
        page.locator(
            "button[type='submit']",
            has=page.locator("span", has_text=re.compile(r"^Log in$", re.IGNORECASE)),
        ),
        timeout=20_000,
    )

    human_pause(4050, 8275)
    page.wait_for_selector(
        "input[name='username'][placeholder='Username or e-mail']",
        state="detached",
        timeout=30_000,
    )

    print("Logged in.")


def download_tab(page: Page, logger: BrowserConsoleLogger | None = None) -> None:
    downloaded = False
    download_error: Exception | None = None
    downloaded_event = Event()

    def capture_download(route: Route) -> None:
        nonlocal downloaded, download_error

        response = route.fetch()
        body = response.body()

        if DOWNLOAD_FILE_URL.search(route.request.url):
            content_type = response.headers.get("content-type", "")
            if "text/html" in content_type.lower() or body[:1] == b"<":
                download_error = RuntimeError("Ultimate Guitar returned HTML instead of the tab data.")
            else:
                OUTPUT_FILE.write_bytes(body)
                downloaded = True
                downloaded_event.set()

        route.fulfill(response=response, body=body)

    page.route("**/tab/download/file**", capture_download)

    try:
        page.goto(TAB_URL, wait_until="domcontentloaded", timeout=60_000)
        wait_for_load_or_pause(page)
        wait_for_cloudflare_wall(page, logger)

        try:
            page.wait_for_load_state("networkidle", timeout=20_000)
        except PlaywrightTimeoutError:
            pass

        if not downloaded:
            download_controls = (
                page.get_by_role("link", name=re.compile("download", re.IGNORECASE)),
                page.get_by_role("button", name=re.compile("download", re.IGNORECASE)),
                page.locator("a[href*='/download/public/']").first,
            )

            for control in download_controls:
                try:
                    human_click(page, control, timeout=4000)
                    if downloaded_event.wait(15):
                        break
                except (PlaywrightTimeoutError, TimeoutError):
                    pass

        if download_error:
            raise download_error

        if not downloaded:
            raise TimeoutError("Timed out waiting for the tab download request.")

        print(f"Downloaded to {OUTPUT_FILE}")
    finally:
        page.unroute("**/tab/download/file**", capture_download)


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
            login(page, console_logger)
            download_tab(page, console_logger)
    except CloudflareChallengeTimeout as error:
        print(error)
    finally:
        console_logger.close()


def random_int(min_value: int, max_value: int) -> int:
    return int(random_float(min_value, max_value + 1))


def random_float(min_value: float, max_value: float) -> float:
    return random.random() * (max_value - min_value) + min_value


if __name__ == "__main__":
    main()
