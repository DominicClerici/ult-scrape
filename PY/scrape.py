from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from urllib.parse import unquote, urlparse

from playwright.sync_api import Page
from playwright.sync_api import Request, Response

from common import (
    BrowserConsoleLogger,
    file_timestamp,
    timestamp,
    wait_for_cloudflare_wall,
    wait_for_load_or_pause,
)
from xtz_decrypt import decrypt_xtz


XTZ_MAGIC = b"XTZ\x00"
ZIP_MAGIC = b"PK\x03\x04"


TAB_BASE_URL = "https://tabs.ultimate-guitar.com/tab"
BASE_DIR = Path(__file__).resolve().parent
CAPTURE_BASE_DIR = BASE_DIR / "captures"
CAPTURE_WINDOW_MS = 10_000
CAPTURE_URL_PARTS = (
    "/download/public/",
    "/tab/download/file",
)
CAPTURE_HEADER_NAMES = (
    "cache-control",
    "cf-ray",
    "content-disposition",
    "content-encoding",
    "content-length",
    "content-type",
    "location",
    "server",
    "x-cache",
)


def should_capture_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.netloc != "tabs.ultimate-guitar.com":
        return False

    return any(part in parsed.path for part in CAPTURE_URL_PARTS)


def selected_headers(headers: dict[str, str]) -> dict[str, str]:
    lower_headers = {key.lower(): value for key, value in headers.items()}
    return {name: lower_headers[name] for name in CAPTURE_HEADER_NAMES if name in lower_headers}


def route_capture_dir(route: str) -> Path:
    route_slug = re.sub(r"[^a-zA-Z0-9._-]+", "-", route.strip("/")).strip("-")
    if not route_slug:
        route_slug = "tab"

    return CAPTURE_BASE_DIR / f"{file_timestamp()}-{route_slug}"


def safe_filename(value: str, fallback: str) -> str:
    sanitized = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip(".-")
    return sanitized[:120] or fallback


def body_extension(body: bytes) -> str:
    if body.startswith(b"\x1f\x8b"):
        return ".gz"

    if body.startswith(XTZ_MAGIC):
        return ".xtz"

    return ".bin"


def filename_from_response(response: Response, index: int, body: bytes) -> str:
    headers = {key.lower(): value for key, value in response.headers.items()}
    disposition = headers.get("content-disposition", "")
    disposition_match = re.search(r'filename\*?=(?:UTF-8\'\')?"?([^";]+)', disposition, re.IGNORECASE)

    if disposition_match:
        base_name = unquote(disposition_match.group(1))
    else:
        parsed = urlparse(response.url)
        base_name = Path(parsed.path).name or "response"
        if base_name == "file":
            ssid = urlparse(response.url).query.split("&", 1)[0].replace("=", "-")
            base_name = f"tab-download-{ssid}" if ssid else base_name

    digest = hashlib.sha256(body).hexdigest()[:12]
    safe_base_name = safe_filename(base_name, "response")
    extension = Path(safe_base_name).suffix or body_extension(body)
    stem = safe_filename(Path(safe_base_name).stem, "response")
    return f"{index:03d}-{stem}-{digest}{extension}"


def redirect_chain(request: Request) -> list[str]:
    urls = [request.url]
    current = request.redirected_from

    while current:
        urls.append(current.url)
        current = current.redirected_from

    urls.reverse()
    return urls


def write_jsonl(path: Path, payload: dict[str, object]) -> None:
    with path.open("a", encoding="utf-8") as file:
        file.write(f"{json.dumps(payload, ensure_ascii=True, sort_keys=True)}\n")


def decrypted_gp_path(encrypted_path: Path) -> Path:
    return encrypted_path.with_suffix(".gp")


def scrape(
    page: Page,
    route: str,
    logger: BrowserConsoleLogger | None = None,
) -> None:
    tab_url = f"{TAB_BASE_URL}/{route.strip('/')}"
    capture_dir = route_capture_dir(route)
    capture_dir.mkdir(parents=True, exist_ok=True)
    metadata_path = capture_dir / "capture.jsonl"
    captured_responses: list[Response] = []

    def log_capture(event: str, payload: dict[str, object]) -> None:
        line_payload = {"event": event, "time": timestamp(), **payload}
        write_jsonl(metadata_path, line_payload)
        logger and logger.write(f"[{timestamp()}] [capture:{event}] {json.dumps(payload, ensure_ascii=True)}")

    def on_response(response: Response) -> None:
        if not should_capture_url(response.url):
            return

        captured_responses.append(response)
        log_capture(
            "response",
            {
                "status": response.status,
                "url": response.url,
                "headers": selected_headers(response.headers),
                "redirect_chain": redirect_chain(response.request),
            },
        )

    def on_request_failed(request: Request) -> None:
        if not should_capture_url(request.url):
            return

        log_capture(
            "request-failed",
            {
                "method": request.method,
                "url": request.url,
                "failure": request.failure,
                "redirect_chain": redirect_chain(request),
            },
        )

    def on_download(download) -> None:  # noqa: ANN001 - Playwright's sync Download type is runtime-only here.
        suggested_filename = safe_filename(download.suggested_filename, "download.bin")
        output_path = capture_dir / f"download-{file_timestamp()}-{suggested_filename}"

        try:
            download.save_as(output_path)
            log_capture(
                "download",
                {
                    "url": download.url,
                    "suggested_filename": download.suggested_filename,
                    "path": str(output_path),
                },
            )
        except Exception as error:
            log_capture(
                "download-error",
                {
                    "url": download.url,
                    "suggested_filename": download.suggested_filename,
                    "error": str(error),
                },
            )

    page.on("response", on_response)
    page.on("requestfailed", on_request_failed)
    page.on("download", on_download)

    try:
        page.goto(tab_url, wait_until="domcontentloaded", timeout=60_000)
        wait_for_load_or_pause(page)
        wait_for_cloudflare_wall(page, logger)
        page.wait_for_timeout(CAPTURE_WINDOW_MS)

        saved_count = 0
        for index, response in enumerate(captured_responses, start=1):
            if 300 <= response.status < 400:
                log_capture(
                    "response-body-skipped",
                    {
                        "status": response.status,
                        "url": response.url,
                        "reason": "redirect response bodies are unavailable in Playwright",
                    },
                )
                continue

            try:
                body = response.body()
                output_path = capture_dir / filename_from_response(response, index, body)
                output_path.write_bytes(body)
                saved_count += 1
                log_capture(
                    "response-body",
                    {
                        "status": response.status,
                        "url": response.url,
                        "path": str(output_path),
                        "bytes": len(body),
                        "sha256": hashlib.sha256(body).hexdigest(),
                        "body_prefix_hex": body[:16].hex(),
                        "body_gzip_magic": body.startswith(b"\x1f\x8b"),
                        "headers": selected_headers(response.headers),
                    },
                )
            except Exception as error:
                log_capture(
                    "response-body-error",
                    {
                        "status": response.status,
                        "url": response.url,
                        "error": str(error),
                    },
                )
                continue

            if not body.startswith(XTZ_MAGIC):
                continue

            try:
                plaintext = decrypt_xtz(body)
            except Exception as error:
                log_capture(
                    "decrypt-error",
                    {"source": str(output_path), "error": str(error)},
                )
                continue

            gp_path = decrypted_gp_path(output_path)
            gp_path.write_bytes(plaintext)
            log_capture(
                "decrypt-success",
                {
                    "source": str(output_path),
                    "path": str(gp_path),
                    "bytes": len(plaintext),
                    "sha256": hashlib.sha256(plaintext).hexdigest(),
                    "is_zip": plaintext[:4] == ZIP_MAGIC,
                },
            )

        print(
            f"Captured {len(captured_responses)} matching response(s), "
            f"saved {saved_count} body file(s) in {capture_dir.resolve()}"
        )
    finally:
        page.remove_listener("response", on_response)
        page.remove_listener("requestfailed", on_request_failed)
        page.remove_listener("download", on_download)
