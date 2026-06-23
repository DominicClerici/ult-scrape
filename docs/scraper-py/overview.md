# scraper-py — Overview

> Part of the [documentation map](../../OVERVIEW.md) · System context:
> [architecture](../architecture.md) · Output it produces:
> [output contract](../output-contract.md)

A long-running **FastAPI service** that logs into Ultimate Guitar once, works a
persistent **SQLite queue** of tab-scrape jobs with a single async worker, and
writes **raw encrypted `.xtz`** files to disk. **It performs no decryption** —
that is [`decoder-rs`](../decoder-rs/overview.md)'s job.

## Component docs

| Doc | Covers |
|---|---|
| [API](./api.md) | FastAPI endpoints: enqueue, status, pause/resume, retry, cancel. |
| [Queue & worker](./queue-and-worker.md) | SQLite schema, `repo.py` state machine, the worker loop, error taxonomy, retry/backoff/dead-letter. |
| [Browser automation](./browser.md) | Camoufox/Playwright session, login, scrape capture, human-like behavior, Cloudflare handling. |
| [Configuration](./configuration.md) | All settings/env vars and the output writer. |

## Architecture in one breath

A single process running one asyncio event loop:

- **FastAPI** serves the control API (`app/api/routes.py`).
- On startup (lifespan in `app/main.py`) the process opens SQLite, launches **one
  Camoufox async browser** with a persistent profile, confirms login, then starts
  the **worker** (`app/worker.py`) as an `asyncio.create_task` background coroutine.
- The **worker owns the browser**. The API never touches the browser directly — it
  talks to the worker through SQLite (`app/repo.py`) and a couple of asyncio
  primitives (a wakeup event on enqueue, a resume event for pause/resume).
- The worker claims one job at a time, scrapes it, and atomically commits output.

## Project layout

```
scraper-py/
  pyproject.toml          # deps + pytest config (Python >= 3.13)
  .env.example            # all settings with defaults
  app/
    main.py               # FastAPI app + lifespan: open DB, launch browser, login, start worker
    config.py             # Settings (pydantic-settings, from env/.env)
    db.py                 # aiosqlite connect + schema (WAL mode)
    models.py             # Pydantic + enums: Job, JobStatus, ServiceState, request/response models
    repo.py               # JobRepo: ALL SQL + atomic job state transitions + backoff()
    worker.py             # Worker: the async scrape loop + pause/wakeup control
    output.py             # write_job_output(): atomic per-job dir + metadata.json
    normalize.py          # normalize_tab(): URL/route -> (tab_id, url)
    errors.py             # ScrapeError taxonomy (Transient/Permanent/SessionExpired)
    manual_login.py       # `python -m app.manual_login`: interactive one-time login (see browser.md)
    browser/
      base.py             # BrowserSession Protocol + CapturedArtifact dataclass
      session.py          # CamoufoxBrowserSession: launch, ensure_logged_in, scrape, close
      login.py            # async UG login flow + is_logged_in() check
      scrape.py           # scrape_tab(): navigate, clear CF, capture .xtz responses
      humanize.py         # human_* helpers + Cloudflare wait
    api/routes.py         # all HTTP endpoints
  tests/                  # unit tests (browser excluded by default) + gated integration test
```

Each module has one purpose. `repo.py` is the **single owner of SQL** and of the
job state machine. Browser code is isolated under `browser/` and only `worker.py`
drives it, through the thin `BrowserSession` Protocol — so the worker is tested
against a fake browser.

## Setup

```bash
cd scraper-py
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m camoufox fetch          # download the Camoufox browser
cp .env.example .env              # then fill in UG_EMAIL / UG_PASSWORD
```

## Run

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

On startup the service launches Camoufox (headful by default), confirms login,
and idles until jobs are enqueued. See the [API](./api.md) for how to drive it.

## Testing

```bash
python -m pytest                  # unit tests; integration excluded by default
python -m pytest -m integration   # live browser test; needs UG creds + network
```

- `pyproject.toml` sets `asyncio_mode = "auto"` and `addopts = "-m 'not integration'"`,
  so the default run is the fast, deterministic, browser-free suite.
- The deterministic core is tested against an in-memory SQLite with an injectable
  clock (`tests/conftest.py` provides a `repo` fixture whose `now_fn` is
  controllable via `repo.clock["t"]`).
- The `browser/` modules are driven through the `BrowserSession` Protocol, so the
  worker is tested with a fake browser. Real-browser behavior is covered only by
  the `integration`-marked test.

## What this service intentionally does NOT do (YAGNI)

- **No decryption** (that is `decoder-rs`).
- **No LLM / dynamic agent** — the worker is a deterministic loop.
- **No search by artist/title** — jobs are exact tab URLs/routes.
- **No concurrency** — a single sequential browser session, one job at a time.
- **No remote deployment** — localhost-only API, optional local API key.
