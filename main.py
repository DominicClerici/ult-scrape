import asyncio
import os
import random
import re
import time
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from playwright.async_api import async_playwright

load_dotenv()

UG_EMAIL = os.getenv("UG_EMAIL")
UG_PASSWORD = os.getenv("UG_PASSWORD")

TAB_URL = "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943"
OUTPUT_FILE = "hotel_california.gp"
DOWNLOAD_FILE_URL = re.compile(r"^https://tabs\.ultimate-guitar\.com/tab/download/file\?")
BROWSER_PROFILE_DIR = Path("playwright-profile")
BROWSER_CHANNEL = "chrome"
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
CLOUDFLARE_WAIT_TIMEOUT_MS = 120000
CLOUDFLARE_POLL_INTERVAL_MS = 1000
CLOUDFLARE_CHALLENGE_SELECTOR = (
    "iframe[src*='challenges.cloudflare.com'], "
    "iframe[src*='turnstile'], "
    "input[name='cf-turnstile-response'], "
    ".cf-turnstile, "
    "[id*='cf-chl'], "
    "[class*='cf-chl']"
)
CLOUDFLARE_CHALLENGE_TEXT = (
    "verify you are human",
    "checking if the site connection is secure",
    "cloudflare",
)


class CloudflareChallengeTimeout(Exception):
    pass


class BrowserConsoleLogger:
    def __init__(self, log_path):
        self.log_path = Path(log_path).resolve()
        self.log_file = self.log_path.open("a", encoding="utf-8", buffering=1)
        self.attached_pages = set()

    def attach_to_context(self, context):
        context.on("page", self.attach_to_page)

    def attach_to_page(self, page):
        page_id = id(page)
        if page_id in self.attached_pages:
            return

        self.attached_pages.add(page_id)
        page.on("console", lambda message: self.write_console_message(page, message))
        page.on("pageerror", lambda error: self.write_page_error(page, error))

    def write_console_message(self, page, message):
        location = message.location
        source = location.get("url") or page.url or "unknown"
        line = location.get("lineNumber", 0)
        column = location.get("columnNumber", 0)

        self.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[console:{message.type}] {source}:{line}:{column} {message.text}"
        )

    def write_page_error(self, page, error):
        self.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[pageerror] {page.url or 'unknown'} {error}"
        )

    def write(self, line):
        self.log_file.write(f"{line}\n")

    def close(self):
        self.log_file.flush()
        self.log_file.close()


def is_auth_log_url(url):
    return any(part in url for part in AUTH_LOG_URL_PARTS)


def selected_headers(headers, names):
    return {
        name: headers[name]
        for name in names
        if name in headers
    }


def sanitize_log_text(text):
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


def mask_email(value):
    if not value:
        return ""

    if "@" not in value:
        return f"{value[:2]}***"

    local, domain = value.split("@", 1)
    visible = local[:2] if len(local) > 1 else local[:1]
    return f"{visible}***@{domain}"


def attach_auth_diagnostics(page, logger):
    def log_request(request):
        if not is_auth_log_url(request.url):
            return

        post_data = request.post_data or ""
        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[auth:request] {request.method} {request.url} "
            f"headers={selected_headers(request.headers, REQUEST_HEADER_LOG_NAMES)} "
            f"post_data_length={len(post_data)}"
        )

    async def log_response(response):
        if not is_auth_log_url(response.url):
            return

        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[auth:response] {response.status} {response.url} "
            f"headers={selected_headers(response.headers, RESPONSE_HEADER_LOG_NAMES)}"
        )

        try:
            body = await response.text()
        except Exception as error:
            logger.write(
                f"[{datetime.now().isoformat(timespec='seconds')}] "
                f"[auth:response-body-error] {response.url} {error}"
            )
            return

        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[auth:response-body] {response.url} {sanitize_log_text(body)}"
        )

    def log_request_failed(request):
        if not is_auth_log_url(request.url):
            return

        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[auth:request-failed] {request.method} {request.url} "
            f"failure={request.failure}"
        )

    page.on("request", log_request)
    page.on("response", lambda response: asyncio.create_task(log_response(response)))
    page.on("requestfailed", log_request_failed)


async def human_pause(min_ms=120, max_ms=420):
    await asyncio.sleep(random.uniform(min_ms, max_ms) / 1000)


async def wait_for_load_or_pause(page, min_ms=10000, max_ms=15000):
    timeout = random.randint(min_ms, max_ms)

    try:
        await page.wait_for_load_state("load", timeout=timeout)
    except PlaywrightTimeoutError:
        pass


async def is_cloudflare_wall(page):
    url = page.url.lower()

    if "challenges.cloudflare.com" in url or "/cdn-cgi/challenge-platform/" in url:
        return True

    try:
        if await page.locator(CLOUDFLARE_CHALLENGE_SELECTOR).count() > 0:
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
        body_text = (await page.locator("body").inner_text(timeout=1000)).lower()
    except Exception:
        body_text = ""

    return any(text in body_text for text in CLOUDFLARE_CHALLENGE_TEXT)


async def wait_for_cloudflare_wall(page, logger=None):
    if not await is_cloudflare_wall(page):
        return

    message = (
        "Cloudflare challenge detected. Waiting up to 2 minutes for it to clear..."
    )
    print(message)

    if logger:
        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[cloudflare:detected] {page.url}"
        )

    deadline = time.monotonic() + (CLOUDFLARE_WAIT_TIMEOUT_MS / 1000)

    while time.monotonic() < deadline:
        await asyncio.sleep(CLOUDFLARE_POLL_INTERVAL_MS / 1000)

        if not await is_cloudflare_wall(page):
            print("Cloudflare challenge cleared. Continuing.")

            if logger:
                logger.write(
                    f"[{datetime.now().isoformat(timespec='seconds')}] "
                    f"[cloudflare:cleared] {page.url}"
                )

            return

    if logger:
        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[cloudflare:timeout] {page.url}"
        )

    raise CloudflareChallengeTimeout(
        "Timed out waiting for the Cloudflare challenge to clear."
    )


async def human_click(page, locator, *, timeout=10000):
    await locator.wait_for(state="visible", timeout=timeout)
    box = await locator.bounding_box()

    if box:
        target_x = box["x"] + box["width"] * random.uniform(0.35, 0.65)
        target_y = box["y"] + box["height"] * random.uniform(0.35, 0.65)
        current_x = target_x + random.uniform(-180, 180)
        current_y = target_y + random.uniform(-90, 90)

        await page.mouse.move(current_x, current_y)
        await human_pause(80, 220)
        await page.mouse.move(target_x, target_y, steps=random.randint(8, 18))
        await human_pause(90, 260)
        await page.mouse.down()
        await human_pause(45, 130)
        await page.mouse.up()
        return

    await locator.click()


async def human_type(page, locator, text):
    await locator.wait_for(state="visible")
    await human_click(page, locator)
    await human_pause(120, 300)

    for char in text:
        await locator.type(char, delay=random.randint(45, 170))

        if random.random() < 0.08:
            await human_pause(180, 520)


async def login(page, logger=None):
    if not UG_EMAIL or not UG_PASSWORD:
        raise ValueError("UG_EMAIL and UG_PASSWORD must be set in .env before logging in.")

    await page.goto(
        "https://www.ultimate-guitar.com/",
        wait_until="domcontentloaded",
        timeout=60000,
    )
    await wait_for_load_or_pause(page)
    await wait_for_cloudflare_wall(page, logger)
    await human_pause(700, 1600)

    # Click the Log In button in the nav (disambiguate by child span text)
    await human_click(
        page,
        page.locator(
            "button[type='button'][tabindex='0'][data-react-aria-pressable='true']",
            has=page.locator("span", has_text=re.compile(r"^Log in$", re.I)),
        ),
        timeout=20000,
    )

    await human_pause(800, 1400)  # wait for the modal animation to complete

    # Wait for the modal to appear
    email_input = page.locator("input[name='username'][placeholder='Username or e-mail']")
    password_input = page.locator("input[name='password'][placeholder='Password']")
    await email_input.wait_for(state="visible")
    await human_pause(250, 650)

    # Fill email and password by placeholder to disambiguate the two inputs
    await human_type(page, email_input, UG_EMAIL)
    await human_pause(250, 700)
    await human_type(page, password_input, UG_PASSWORD)
    await human_pause(2050, 4275)

    email_value = await email_input.input_value()
    password_value = await password_input.input_value()

    if logger:
        logger.write(
            f"[{datetime.now().isoformat(timespec='seconds')}] "
            f"[auth:field-check] "
            f"email={mask_email(email_value)} "
            f"email_matches_env={email_value == UG_EMAIL} "
            f"password_length={len(password_value)} "
            f"password_matches_env={password_value == UG_PASSWORD}"
        )

    # Click the submit button (disambiguate by child text)
    await human_click(
        page,
        page.locator(
            "button[type='submit']",
            has=page.locator("span", has_text=re.compile(r"^Log in$", re.I)),
        ),
        timeout=20000,
    )

    await human_pause(4050, 8275)
    # Wait for login to complete; the modal removes its fields from the DOM.
    await page.wait_for_selector(
        "input[name='username'][placeholder='Username or e-mail']",
        state="detached",
    )

    print("Logged in.")



async def download_tab(page, logger=None):
    output_path = Path(OUTPUT_FILE).resolve()
    downloaded = asyncio.Event()
    download_error = None

    async def capture_download(route):
        nonlocal download_error

        response = await route.fetch()
        body = await response.body()

        if DOWNLOAD_FILE_URL.match(route.request.url):
            content_type = response.headers.get("content-type", "")

            if "text/html" in content_type.lower() or body.lstrip().startswith(b"<"):
                download_error = RuntimeError(
                    "Ultimate Guitar returned HTML instead of the tab data."
                )
            else:
                output_path.write_bytes(body)
                downloaded.set()

        await route.fulfill(response=response, body=body)

    await page.route("**/tab/download/file**", capture_download)

    try:
        await page.goto(TAB_URL, wait_until="domcontentloaded")
        await wait_for_load_or_pause(page)
        await wait_for_cloudflare_wall(page, logger)

        # The app often wires the download request after the tab viewer hydrates.
        try:
            await page.wait_for_load_state("networkidle", timeout=20000)
        except PlaywrightTimeoutError:
            pass

        if not downloaded.is_set():
            download_controls = [
                page.get_by_role("link", name=re.compile("download", re.I)),
                page.get_by_role("button", name=re.compile("download", re.I)),
                page.locator("a[href*='/download/public/']").first,
            ]

            for control in download_controls:
                try:
                    await human_click(page, control, timeout=4000)
                    await asyncio.wait_for(downloaded.wait(), timeout=15000)
                    break
                except (PlaywrightTimeoutError, asyncio.TimeoutError):
                    continue

        if download_error:
            raise download_error

        if not downloaded.is_set():
            raise TimeoutError("Timed out waiting for the tab download request.")

        print(f"Downloaded to {output_path}")
    finally:
        await page.unroute("**/tab/download/file**", capture_download)


async def main():
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    console_logger = BrowserConsoleLogger(f"{timestamp}-logs.txt")

    async with async_playwright() as p:
        context = None

        try:
            context = await p.chromium.launch_persistent_context(
                str(BROWSER_PROFILE_DIR.resolve()),
                channel=BROWSER_CHANNEL,
                headless=False,
            )
            console_logger.attach_to_context(context)
            page = context.pages[0] if context.pages else await context.new_page()
            console_logger.attach_to_page(page)

            if MANUAL_LOGIN:
                await page.goto(
                    "https://www.ultimate-guitar.com/",
                    wait_until="domcontentloaded",
                    timeout=60000,
                )
                await wait_for_load_or_pause(page)
                await wait_for_cloudflare_wall(page, console_logger)
                print(
                    "MANUAL_LOGIN is enabled. Log in manually, then close the "
                    f"browser window. Profile: {BROWSER_PROFILE_DIR.resolve()}"
                )

                closed = asyncio.Event()
                context.on("close", lambda *args: closed.set())
                page.on("close", lambda *args: closed.set())
                await closed.wait()
                return

            attach_auth_diagnostics(page, console_logger)

            await login(page, console_logger)
            await download_tab(page, console_logger)
        except CloudflareChallengeTimeout as error:
            print(error)
        finally:
            if context:
                await context.close()
            console_logger.close()


if __name__ == "__main__":
    asyncio.run(main())
