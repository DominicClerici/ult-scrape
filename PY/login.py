from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import Locator, Page, Request, Response, TimeoutError as PlaywrightTimeoutError

from common import (
    BrowserConsoleLogger,
    human_click,
    human_pause,
    human_type,
    timestamp,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

UG_EMAIL = os.getenv("UG_EMAIL")
UG_PASSWORD = os.getenv("UG_PASSWORD")
PROFILE_HREF = "https://www.ultimate-guitar.com/u/tnd29hh6r4"
PROFILE_HREF_SELECTOR = f'[href="{PROFILE_HREF}"]'
LOGIN_ENTRY_TIMEOUT_MS = 20_000

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


def login(page: Page, logger: BrowserConsoleLogger | None = None) -> bool:
    if not UG_EMAIL or not UG_PASSWORD:
        raise RuntimeError("UG_EMAIL and UG_PASSWORD must be set in PY/.env before logging in.")

    page.goto("https://www.ultimate-guitar.com/", wait_until="domcontentloaded", timeout=60_000)
    wait_for_load_or_pause(page)
    wait_for_cloudflare_wall(page, logger)
    human_pause(700, 1600)

    login_button = page.locator(
        "button[type='button'][tabindex='0'][data-react-aria-pressable='true']",
        has=page.locator("span", has_text=re.compile(r"^Log in$", re.IGNORECASE)),
    )

    if wait_for_existing_session(page, login_button):
        print("Already logged in.")
        return True

    try:
        human_click(page, login_button, timeout=20_000)

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
    except PlaywrightTimeoutError as error:
        logger and logger.write(f"[{timestamp()}] [auth:timeout] {error}")
        print("Login failed.")
        return False

    if is_logged_in(page):
        print("Logged in.")
        return True

    if is_login_form_closed(page):
        print("Logged in.")
        return True

    print("Login failed.")
    return False


def wait_for_existing_session(
    page: Page,
    login_button: Locator,
    timeout_ms: int = LOGIN_ENTRY_TIMEOUT_MS,
) -> bool:
    deadline = time.monotonic() + timeout_ms / 1000

    while time.monotonic() < deadline:
        if is_logged_in(page):
            human_pause(500, 1000)
            return True

        try:
            if login_button.is_visible(timeout=250):
                return False
        except Exception:
            pass

        time.sleep(0.1)

    return False


def is_logged_in(page: Page) -> bool:
    try:
        return page.locator(PROFILE_HREF_SELECTOR).count() > 0
    except Exception:
        return False


def is_login_form_closed(page: Page) -> bool:
    try:
        page.wait_for_selector(
            "input[name='username'][placeholder='Username or e-mail']",
            state="detached",
            timeout=30_000,
        )
        return True
    except Exception:
        return False
