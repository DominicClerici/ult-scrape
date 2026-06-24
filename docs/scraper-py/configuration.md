# scraper-py — Configuration & Output Writer

> Part of the [documentation map](../../OVERVIEW.md) ·
> [scraper overview](./overview.md). Sources: `app/config.py`, `.env.example`,
> `app/output.py`.

## Settings

All config is loaded by `app/config.py` (`pydantic-settings`) from environment
variables and a `.env` file (`extra="ignore"`). `get_settings()` returns a fresh
`Settings`. Copy `.env.example` to `.env` and fill in credentials.

| Key | Default | Purpose |
|---|---|---|
| `UG_EMAIL` | `""` | Ultimate Guitar login email. Required to scrape. |
| `UG_PASSWORD` | `""` | UG password. Required to scrape. |
| `UG_PROXY` | `""` | Optional proxy `server` URL (e.g. `http://gate.decodo.com:7000`). Blank → bare requests, no proxy. When set, Camoufox `geoip` is enabled. Use a **sticky-session** endpoint so the exit IP stays stable across login + scrape. |
| `UG_PROXY_USERNAME` | `""` | Proxy auth username (required for authenticated proxies like Decodo). Passed as a separate field — credentials in the `UG_PROXY` URL are ignored by Playwright/Camoufox. Only sent when `UG_PROXY` is set. |
| `UG_PROXY_PASSWORD` | `""` | Proxy auth password. Paired with `UG_PROXY_USERNAME`. |
| `OUTPUT_DIR` | `../output` | Root for committed per-job output dirs. Relative to the service's working dir (`scraper-py/`), so the default resolves to the **repo-root `output/`** directory. **Shared with the decoder.** |
| `DB_PATH` | `./scraper.db` | SQLite file path (queue + state). |
| `PROFILE_DIR` | `./camoufox-profile` | Persistent Camoufox profile dir (cookies, localStorage, cache — keeps the login session). |
| `FINGERPRINT_PATH` | `./camoufox-fingerprint.json` | Persisted browser fingerprint (device identity). Generated once, reloaded every launch so the device matches the saved session. |
| `HEADLESS` | `false` | Headful by default (local Mac). |
| `MAX_ATTEMPTS` | `3` | Retry limit per job before dead-letter. |
| `BACKOFF_BASE_SECONDS` | `30` | Base for exponential retry backoff (`base * 2^(attempts-1)`). |
| `INTER_JOB_DELAY_MIN` | `5` | Min seconds of human-like delay between jobs. |
| `INTER_JOB_DELAY_MAX` | `20` | Max seconds between jobs (set max to 0 to disable the delay). |
| `CLOUDFLARE_TIMEOUT_MS` | `120000` | Max wait for a Cloudflare challenge to clear. |
| `CAPTURE_WINDOW_MS` | `30000` | Max time to wait for the download after navigation; the scrape returns as soon as the file lands, so this is a ceiling for slow players, not a fixed wait. |
| `POLL_INTERVAL_SECONDS` | `5` | Idle worker re-check interval when the queue is empty. |
| `CIRCUIT_BREAKER_THRESHOLD` | `5` | Auto-pause the worker after this many **consecutive non-successful** jobs (safety against hammering UG when something is broken). See [queue & worker](./queue-and-worker.md#circuit-breaker). |
| `RATE_LIMIT_DELAY_SECONDS` | `300` | Cool-off applied after a `403`/`429` rate-limit before the next job (on top of the normal retry backoff). |
| `SESSION_EXPIRY_BACKOFF_SECONDS` | `60` | Delay before a session-expired job becomes eligible again. No retry is consumed; this just prevents a tight re-login loop. |
| `API_HOST` | `127.0.0.1` | Bind address (localhost-only). |
| `API_PORT` | `8000` | API port. |
| `API_KEY` | `""` | Optional `X-API-Key` auth; empty disables the check. |
| `DISCOVERY_SORT_ORDERS` | `date_desc,artistname_asc,artistname_desc,songname_asc` | Comma-separated list of sort-order names to use as sliding windows when a slice is over the 1000-result cap. |
| `DISCOVERY_FACET_LADDER` | `genres,decade,tonality` | Comma-separated facet names tried for subdivision (in order) before falling back to sort windows. |
| `DISCOVERY_PAGE_DELAY_MIN` | `2.0` | Min seconds of human-like delay between page fetches during discovery. |
| `DISCOVERY_PAGE_DELAY_MAX` | `6.0` | Max seconds between page fetches (`0` disables the delay). |
| `DISCOVERY_MAX_SLICES` | `0` | Stop after N slices; `0` = unlimited. |
| `DISCOVERY_TARGET_CAP` | `0` | Stop once N distinct tabs are found; `0` = unlimited. |
| `DISCOVERY_REQUEST_TIMEOUT_MS` | `30000` | Per-page fetch timeout in milliseconds passed to `fetch_explore_html`. |
| `DISCOVERY_UNTAGGED_SWEEP` | `true` | When `true`, add a final no-genre slice to catch tabs that UG hasn't tagged to any genre. |

Notes:

- `DISCOVERY_*` keys control the [official tab discovery](./discovery.md) component
  only. They have no effect on the scrape job queue. Every key can be overridden
  **per run** via the `DiscoveryStartRequest` body on `POST /discover`; the
  runner merges request-level overrides on top of these defaults.
- `OUTPUT_DIR` **must match** the directory the [decoder](../decoder-rs/overview.md)
  scans. Both default to the **repo-root `output/`** directory: the scraper via
  `../output` (it runs from `scraper-py/`), the decoder by walking up for the repo
  root from wherever it is launched. Setting `$OUTPUT_DIR` (which the decoder also
  reads) overrides both and keeps them aligned anywhere.
- Path settings are `pathlib.Path`. Secrets (`UG_PASSWORD`, `API_KEY`) should
  never be logged.

## Output writer (`app/output.py`)

`write_job_output(...)` implements the producer side of the
[output contract](../output-contract.md). Given the captured artifacts it:

1. Stages the directory in `OUTPUT_DIR/.tmp/<uuid>/`.
2. Writes each artifact's raw bytes (untouched), computing `sha256`, `byte_size`,
   and the `XTZ\0` magic check for `metadata.json`.
3. Writes `metadata.json` **last** (the commit marker), `sort_keys=True, indent=2`.
   The optional `song=` argument (captured by the browser layer) is added as the
   additive [`song` block](../output-contract.md#the-additive-song-block) only
   when truthy — omitted entirely otherwise.
4. Removes any existing `OUTPUT_DIR/<tab_id>/`, then `os.replace(staging, final)` —
   an **atomic directory rename** within the same filesystem.
5. Cleans up the staging dir in a `finally`.

Because the move is atomic and `metadata.json` is written last, a consumer never
sees a half-written tab directory. Tested in `tests/test_output.py`.

> **Filesystem requirement:** staging (`OUTPUT_DIR/.tmp/...`) and the final
> location must be on the **same filesystem** for the rename to be atomic. Keep
> `OUTPUT_DIR` on one volume.
