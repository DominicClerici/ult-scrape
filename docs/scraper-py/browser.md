# scraper-py — Browser Automation

> Part of the [documentation map](../../OVERVIEW.md) ·
> [scraper overview](./overview.md). Sources: `app/browser/`.

All browser code lives under `app/browser/` and is the **only** part of the
service that touches the network or a real browser. The worker drives it through
the thin `BrowserSession` Protocol, so everything here is replaced by a fake in
unit tests. Real behavior is exercised only by the `integration`-marked test.

The browser is **[Camoufox](https://github.com/daijro/camoufox)** — a hardened,
anti-fingerprint Firefox driven through Playwright's async API.

## The `BrowserSession` interface

`app/browser/base.py` defines the contract the worker depends on:

```python
class BrowserSession(Protocol):
    async def ensure_logged_in(self) -> None: ...
    async def is_logged_in(self) -> bool: ...
    async def scrape(self, tab_url: str) -> list[CapturedArtifact]: ...
    async def close(self) -> None: ...
```

`CapturedArtifact` is the in-memory result of a captured download:
`filename, data: bytes, source_url, http_status, content_headers`.

## `session.py` — `CamoufoxBrowserSession`

The production implementation of the Protocol.

- `start()` launches `AsyncCamoufox` with a **persistent context**
  (`user_data_dir = PROFILE_DIR`), `humanize=True`, `os="windows"`,
  `locale="en-US"`, `block_webrtc=True`, and a **persisted `fingerprint`** (see
  `fingerprint.py`). If `UG_PROXY` is set it adds the proxy and enables `geoip`,
  attaching `UG_PROXY_USERNAME`/`UG_PROXY_PASSWORD` as separate auth fields when
  present (blank `UG_PROXY` → no proxy, bare requests). Reuses the first existing
  page or opens one.
- `ensure_logged_in()` delegates to `login.login(...)`; raises if login fails.
- `is_logged_in()` / `scrape()` delegate to `login.is_logged_in` / `scrape.scrape_tab`.
- `close()` exits the Camoufox context manager.

Two things persist across restarts, and together they make every run look like
the **same logged-in browser**:

1. The **persistent profile** (`user_data_dir = PROFILE_DIR`) holds cookies,
   `localStorage`, `IndexedDB`, and cache — so the login session survives and the
   full login flow only runs when actually logged out.
2. The **persisted fingerprint** (`FINGERPRINT_PATH`) holds the device identity.

## `fingerprint.py` — stable device identity

By default Camoufox generates a **new** fingerprint on every launch (user-agent,
screen, fonts, GPU/WebGL, codecs, `hardwareConcurrency`, …). Reusing persisted
cookies under a *different* device each run is a correlation signal anti-bot
systems flag, so this module pins it:

- `load_or_create_fingerprint(path, os_name)` — on first launch generates a
  Windows Firefox fingerprint via Camoufox's own generator and writes it to
  `FINGERPRINT_PATH` (atomic write); on every later launch it reloads that file
  and returns the same device. A corrupt/incompatible file is regenerated.
- `session.start()` passes the result as `fingerprint=` to `AsyncCamoufox`, which
  skips fingerprint regeneration when one is supplied.

> Camoufox logs a one-time `LeakWarning` ("passing your own fingerprint is not
> recommended") on each launch — benign here, since the fingerprint was produced
> by Camoufox's own generator. The small per-launch anti-fingerprinting *noise*
> (canvas/WebGL jitter, font-spacing seed) is intentionally left random.

> Delete `FINGERPRINT_PATH` to mint a fresh device (do it alongside clearing
> `PROFILE_DIR` if you also want a fresh session).

## `login.py` — UG login flow

- `is_logged_in(page)` — the canonical login check: counts a selector for the
  account's profile link (`PROFILE_HREF`). Used in status, scrape, and login.
- `login(page, email, password, cf_timeout_ms)` — navigates to UG, waits out any
  Cloudflare wall, and if not already logged in, performs a **human-like** login:
  click "Log in", type credentials with realistic delays, submit, then confirm by
  either the profile link appearing or the username field detaching. Returns a
  bool; raises if credentials are unset.

> ⚠️ `PROFILE_HREF` in `login.py` is hardcoded to a specific UG account's profile
> URL. The login check only works for that account. If you change the credentials,
> update `PROFILE_HREF`/`PROFILE_SELECTOR` too. See
> [maintenance notes](#gotchas--maintenance).

## `scrape.py` — `scrape_tab()`

The core capture routine:

1. Registers a `response` listener that buffers any response whose host is
   `tabs.ultimate-guitar.com` and whose path contains one of
   `CAPTURE_URL_PARTS` (`/download/public/`, `/tab/download/file`).
2. Navigates to the tab URL (`domcontentloaded`), waits for load, clears
   Cloudflare.
3. Classifies the page state and raises a typed error if needed:
   - not logged in → `SessionExpiredError`
   - HTTP 404 → `PermanentScrapeError`
4. Waits `CAPTURE_WINDOW_MS` for the download response(s) to arrive.
5. For each captured response: skips 3xx, reads the body, **keeps only bytes
   starting with the `XTZ\0` magic**, and builds a `CapturedArtifact` (with a safe
   filename derived from `Content-Disposition` or the URL's `ssid`).
6. If nothing matched → `TransientScrapeError` (retryable).

The raw bytes are returned untouched — **no decryption** — for
[`write_job_output`](../output-contract.md) to persist.

## `humanize.py` — human-like behavior & Cloudflare

Pure helpers (human-like behavior + Cloudflare handling), all randomized:

- `human_pause`, `human_click` (mouse move with jitter, steps, down/up), `human_type`
  (per-character delays with occasional longer pauses).
- `wait_for_load_or_pause` — best-effort load wait that swallows timeouts.
- `is_cloudflare_wall` / `wait_for_cloudflare_wall` — detect a Cloudflare
  challenge (by URL, known selectors, page title, or body text) and poll until it
  clears or `CLOUDFLARE_TIMEOUT_MS` elapses (`CloudflareTimeout`).

These are smoke-tested in `tests/test_humanize_smoke.py`,
`test_login_smoke.py`, `test_session_smoke.py`, `test_scrape_helpers.py` — the
pure helpers and URL/filename logic run without a real browser.

## Gotchas & maintenance

This layer is the most brittle part of the system because it depends on UG's live
markup and on Cloudflare. Things likely to need updating over time:

- **`PROFILE_HREF`** (`login.py`) — tied to a specific account.
- **Login selectors** (`login.py`) — UG's login button/inputs markup.
- **Capture matchers** — `CAPTURE_URL_PARTS` (`scrape.py`) if UG changes its
  download endpoints.
- **Cloudflare heuristics** — `CF_SELECTOR` / `CF_TEXT` (`humanize.py`).

If a scrape mysteriously yields "no XTZ download captured" or "not logged in",
start here. When you change any of these, update this page.
