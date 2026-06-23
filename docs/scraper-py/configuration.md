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
| `UG_PROXY` | `""` | Optional proxy `server` URL; when set, Camoufox `geoip` is enabled. |
| `OUTPUT_DIR` | `./output` | Root for committed per-job output dirs. **Shared with the decoder.** |
| `DB_PATH` | `./scraper.db` | SQLite file path (queue + state). |
| `PROFILE_DIR` | `./camoufox-profile` | Persistent Camoufox profile dir (cookies, localStorage, cache — keeps the login session). |
| `FINGERPRINT_PATH` | `./camoufox-fingerprint.json` | Persisted browser fingerprint (device identity). Generated once, reloaded every launch so the device matches the saved session. |
| `HEADLESS` | `false` | Headful by default (local Mac). |
| `MAX_ATTEMPTS` | `3` | Retry limit per job before dead-letter. |
| `BACKOFF_BASE_SECONDS` | `30` | Base for exponential retry backoff (`base * 2^(attempts-1)`). |
| `INTER_JOB_DELAY_MIN` | `5` | Min seconds of human-like delay between jobs. |
| `INTER_JOB_DELAY_MAX` | `20` | Max seconds between jobs (set max to 0 to disable the delay). |
| `CLOUDFLARE_TIMEOUT_MS` | `120000` | Max wait for a Cloudflare challenge to clear. |
| `CAPTURE_WINDOW_MS` | `10000` | Window to collect download responses after navigation. |
| `POLL_INTERVAL_SECONDS` | `5` | Idle worker re-check interval when the queue is empty. |
| `API_HOST` | `127.0.0.1` | Bind address (localhost-only). |
| `API_PORT` | `8000` | API port. |
| `API_KEY` | `""` | Optional `X-API-Key` auth; empty disables the check. |

Notes:

- `OUTPUT_DIR` **must match** the directory the [decoder](../decoder-rs/overview.md)
  scans. The decoder defaults to `$OUTPUT_DIR` then `./output`, so sharing the env
  var keeps them aligned.
- Path settings are `pathlib.Path`. Secrets (`UG_PASSWORD`, `API_KEY`) should
  never be logged.

## Output writer (`app/output.py`)

`write_job_output(...)` implements the producer side of the
[output contract](../output-contract.md). Given the captured artifacts it:

1. Stages the directory in `OUTPUT_DIR/.tmp/<uuid>/`.
2. Writes each artifact's raw bytes (untouched), computing `sha256`, `byte_size`,
   and the `XTZ\0` magic check for `metadata.json`.
3. Writes `metadata.json` **last** (the commit marker), `sort_keys=True, indent=2`.
4. Removes any existing `OUTPUT_DIR/<tab_id>/`, then `os.replace(staging, final)` —
   an **atomic directory rename** within the same filesystem.
5. Cleans up the staging dir in a `finally`.

Because the move is atomic and `metadata.json` is written last, a consumer never
sees a half-written tab directory. Tested in `tests/test_output.py`.

> **Filesystem requirement:** staging (`OUTPUT_DIR/.tmp/...`) and the final
> location must be on the **same filesystem** for the rename to be atomic. Keep
> `OUTPUT_DIR` on one volume.
