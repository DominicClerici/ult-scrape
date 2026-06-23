# Scraper-PY Service — Design

**Date:** 2026-06-23
**Status:** Approved (brainstorm), pending implementation plan

## Context

The existing `PY/` project is a one-shot synchronous Playwright + Camoufox script.
It logs into Ultimate Guitar (UG) with stored credentials and a persistent browser
profile, clears Cloudflare challenges with human-like interaction, navigates to a
single hardcoded tab route, captures the encrypted XTZ download responses, **and
decrypts them inline** to Guitar Pro `.gp` files via `xtz_decrypt.py`.

We are splitting this into two decoupled projects:

1. **Scraper (this spec, Python, `scraper-py/`)** — only *gathers raw encrypted XTZ
   bytes* and writes them to the filesystem. No decryption.
2. **Decoder (separate, Rust, later)** — independently globs the scraper's output
   directory and decodes the raw XTZ into `.gp`.

The two projects communicate **only through the filesystem** (the output contract in
§4). The Rust decoder never reads the scraper's database.

This spec covers the Python scraper service only.

## Goals

- A long-running local service that boots, logs into UG once, and stays resident.
- A persistent **queue** of tab-scrape jobs, worked sequentially by a single
  background worker ("agent runtime").
- A **FastAPI** control surface: enqueue, dequeue, status, pause, resume, retry.
- Robust failure handling: retry-with-backoff then dead-letter; transient vs.
  permanent vs. session errors distinguished.
- Self-describing, atomically-committed output for the Rust decoder to consume.

## Non-Goals (YAGNI)

- No decryption (moves to Rust).
- No LLM/dynamic agent — the worker is a deterministic loop.
- No search-by-artist/title — jobs are exact tab routes/URLs.
- No multi-browser / concurrent workers — single sequential session.
- No remote/server deployment, no auth beyond an optional local API key.

## Decisions (locked during brainstorm)

| Topic | Decision |
|---|---|
| Worker model | Single deterministic background worker loop (no LLM) |
| State storage | SQLite (source of truth for queue + job state + history) |
| Job input | Exact tab URL or route/slug (no search) |
| Output layout | Self-describing per-job dir: raw `.xtz` + `metadata.json` |
| Failure handling | Retry with backoff, then dead-letter |
| Dedup | Skip if already succeeded; `force` flag overrides |
| Runtime env | Local Mac, headful browser, localhost-only API |
| Process architecture | Async-everything: port browser code to `camoufox.async_api`; worker is an asyncio task in the FastAPI event loop (no threads) |

## Architecture overview

A single process running one asyncio event loop:

- **FastAPI** serves the control API.
- On startup (lifespan), the process launches a **single Camoufox async browser**
  (persistent profile), confirms login, then starts the **worker** as an
  `asyncio.create_task` background coroutine.
- The worker owns the browser. The API never touches the browser directly — it
  communicates with the worker through SQLite (queue + state) and a few asyncio
  primitives (pause flag, wakeup event).
- The XTZ decrypt logic from `PY/` is **dropped**; the scraper captures raw bytes only.

### Project layout

```
scraper-py/
  pyproject.toml          # deps: fastapi, uvicorn, camoufox[geoip], aiosqlite,
                          #       pydantic-settings, python-dotenv
  .env.example
  app/
    main.py               # FastAPI app + lifespan: launch browser, ensure login, start worker
    config.py             # Settings from env/.env (pydantic-settings)
    db.py                 # aiosqlite connection, schema/migrations
    models.py             # Pydantic models: Job, EnqueueRequest, StatusResponse
    repo.py               # Job repository: CRUD + atomic state transitions (only module touching SQL)
    worker.py             # Agent runtime: async worker loop + session/pause control
    output.py             # Atomic per-job output dir + metadata.json writer
    browser/
      session.py          # Camoufox async launch, login bootstrap, is_logged_in health check
      login.py            # async port of the login flow
      scrape.py           # async port: navigate + capture XTZ responses -> in-memory artifacts
      humanize.py         # async port of common.py (human_*, cloudflare wait, timestamps)
    api/
      routes.py           # endpoints
  tests/
```

Each module has one purpose. The browser code is isolated under `browser/` and only
`worker.py` drives it. `repo.py` is the single owner of SQL and of the job state
machine. The worker depends on the browser through a thin `BrowserSession` interface
so it can be tested against a fake.

## 1. Data model (SQLite)

### `jobs` table

| Column | Notes |
|---|---|
| `id` | UUID, primary key |
| `tab_id` | Normalized canonical route — the **dedup key** |
| `url` | Full UG tab URL the worker navigates to |
| `status` | `queued` \| `running` \| `succeeded` \| `failed` \| `canceled` |
| `priority` | Integer; lower = sooner (default 0) |
| `attempts` | Count of attempts made so far |
| `max_attempts` | Snapshot of the limit for this job (default from config) |
| `next_attempt_at` | Epoch seconds; job is eligible when `<= now` |
| `force` | Bool; if true, bypass the dedup short-circuit |
| `created_at` / `updated_at` / `started_at` / `finished_at` | Timestamps |
| `error` | Last error message (nullable) |
| `output_dir` | Path to the per-job output dir once succeeded (nullable) |

### `app_state` table

Tiny key/value store for state that must survive restart, e.g. `paused=true|false`.

SQLite runs in **WAL mode**. Access is via `aiosqlite`. `repo.py` is the only module
that issues SQL.

## 2. Job lifecycle / state machine

```
            enqueue
               │
               ▼
           ┌────────┐  claim (next_attempt_at<=now,   ┌─────────┐
           │ queued │ ───────order by priority,created──▶│ running │
           └────────┘                                   └─────────┘
             ▲   │                                        │   │   │
   re-queue  │   │ DELETE (only while queued)             │   │   │ success
   (backoff) │   ▼                                        │   │   ▼
           ┌────────┐                          transient  │   │ ┌───────────┐
           │canceled│◀─────────────────────────fail+retry │   │ │ succeeded │ (terminal)
           └────────┘                          remaining ─┘   │ └───────────┘
                                                              │
                                          retries exhausted / │
                                          permanent error     ▼
                                                          ┌────────┐
                                              POST /retry │ failed │ (dead-letter, terminal,
                                              ───────────▶└────────┘  re-queueable)
```

- **Claim** is atomic: a single SQL `UPDATE ... WHERE status='queued' AND
  next_attempt_at<=now ORDER BY priority,created_at LIMIT 1 RETURNING ...` (or
  equivalent) so the same job can't be claimed twice. At most one job is `running`.
- **Dedup short-circuit:** when a job is claimed, if a `succeeded` job already exists
  for the same `tab_id` and this job's `force` is false, transition straight to
  `succeeded` (referencing the existing `output_dir`) without scraping.
- **Retry:** a transient failure sets `attempts++`, `status=queued`,
  `next_attempt_at = now + backoff(attempts)`. Exhausting `max_attempts`, or any
  permanent error, sets `status=failed`.
- **Session expiry is NOT a job failure.** If the worker detects a logged-out state
  mid-job, it re-queues the current job **unchanged** (no attempt consumed), drives
  re-login, and resumes.

### Error taxonomy

| Class | Examples | Worker action |
|---|---|---|
| Transient | Cloudflare wait timeout, navigation timeout, no XTZ captured in window | Retry with backoff |
| Permanent | Tab 404 / route invalid / unparseable | Dead-letter immediately (no wasted retries) |
| Session | Logged-out state detected | Re-login + resume; no attempt consumed |

## 3. Worker (agent runtime)

A single `asyncio` task started in the FastAPI lifespan **after** the browser is up
and login is confirmed. Loop:

1. If `paused`, await the resume event.
2. Atomically claim the next eligible job → `running`. If none, await a wakeup event
   (signaled on enqueue) or short-sleep, then re-check.
3. Dedup check (see §2). If short-circuited, mark `succeeded` and continue.
4. Run the scrape against the browser:
   - Navigate to the tab URL; wait for load; clear Cloudflare (ported logic).
   - Within a capture window, collect responses whose URL matches the XTZ download
     endpoints; buffer the raw bytes in memory.
   - Classify the outcome (success / transient / permanent / session) and raise a
     typed error on failure.
5. On success → write the output dir (§4), mark `succeeded`. On failure → apply the
   error taxonomy (§2).
6. Human-like inter-job delay (configurable random range) before the next claim.

**Service-level state** exposed via `GET /status`:
`starting | logging_in | idle | running | paused | error`.

### Session management

- On startup, the persistent Camoufox profile usually carries a valid session;
  `session.py` checks `is_logged_in` and only runs the full login flow if needed.
- The worker re-checks login health around navigation; a logged-out detection
  triggers the re-login-and-resume path above.

## 4. Output contract (interface to the Rust decoder)

Each successful job writes `OUTPUT_DIR/<tab_id>/` containing:

- `<original_filename>.xtz` — the raw encrypted bytes exactly as downloaded
  (no decryption, no transformation). A tab may yield more than one artifact.
- `metadata.json`:

```json
{
  "tab_id": "eagles/hotel-california-official-1910943",
  "url": "https://tabs.ultimate-guitar.com/tab/eagles/hotel-california-official-1910943",
  "route": "eagles/hotel-california-official-1910943",
  "scraped_at": "2026-06-23T12:00:00",
  "scraper_version": "0.1.0",
  "http_status": 200,
  "files": [
    {
      "filename": "tab-download-ssid-1910943.xtz",
      "sha256": "…",
      "byte_size": 89306,
      "source_url": "https://tabs.ultimate-guitar.com/tab/download/file?…",
      "content_headers": { "content-type": "…", "content-disposition": "…" },
      "xtz_magic_ok": true
    }
  ]
}
```

**Atomicity / commit marker:** artifacts are staged in a temp dir, then the directory
is moved into place; `metadata.json` is written **last** and is the commit marker. The
Rust decoder treats a per-job directory as ready **only once `metadata.json` exists**.
This prevents the decoder from reading partial output.

## 5. FastAPI surface

Localhost-only (`API_HOST=127.0.0.1`). Optional `API_KEY` header check, off by default.

| Method & path | Purpose |
|---|---|
| `GET /status` | Service state, current job, queue depth, counts by status, login/browser health |
| `GET /jobs?status=&limit=&offset=` | List/filter jobs |
| `GET /jobs/{id}` | Job detail |
| `POST /jobs` | Enqueue one (`{ url_or_route, priority?, force? }`). Returns the new job, or the existing succeeded job if dedup short-circuits |
| `POST /jobs/bulk` | Enqueue many in one call |
| `DELETE /jobs/{id}` | Dequeue a `queued` job → `canceled`. Running jobs can't be cancelled mid-scrape → 409 |
| `POST /jobs/{id}/retry` | Re-queue a `failed` job (resets `next_attempt_at`, keeps history) |
| `POST /pause` | Pause the worker after the current job finishes; persists `paused=true` |
| `POST /resume` | Resume |
| `GET /healthz` | Liveness |

Enqueue **normalizes** the input (full URL or bare route) into the canonical `tab_id`
so dedup is reliable. Invalid/unparseable input → 422.

## 6. Config (env / `.env`)

Loaded via `pydantic-settings`. Secrets are never logged (reuse the existing redaction
approach from `PY/login.py`).

| Key | Default | Purpose |
|---|---|---|
| `UG_EMAIL`, `UG_PASSWORD` | — | UG credentials |
| `UG_PROXY` | unset | Optional proxy; enables geoip when set |
| `OUTPUT_DIR` | — | Root for per-job output dirs |
| `DB_PATH` | — | SQLite file path |
| `PROFILE_DIR` | — | Persistent Camoufox profile dir |
| `HEADLESS` | `false` | Headful on local Mac |
| `MAX_ATTEMPTS` | `3` | Retry limit per job |
| `BACKOFF_BASE_SECONDS` | — | Base for exponential backoff |
| `INTER_JOB_DELAY_MIN/MAX` | — | Human-like pacing between jobs |
| `CLOUDFLARE_TIMEOUT_MS` | `120000` | Max wait for a Cloudflare challenge |
| `CAPTURE_WINDOW_MS` | `10000` | Window to collect download responses |
| `API_HOST` | `127.0.0.1` | Bind address |
| `API_PORT` | — | API port |
| `API_KEY` | unset | Optional API auth |

## 7. Testing strategy (TDD)

The deterministic core is tested without a browser, against an in-memory SQLite:

- `repo.py` state-machine transitions: claim, succeed, fail→backoff, exhaust→
  dead-letter, cancel, retry, dedup short-circuit, concurrent-claim safety.
- `output.py`: atomic writes, `metadata.json` schema, commit-marker ordering
  (artifacts before `metadata.json`).
- Backoff calculation; `tab_id` normalization (URL ↔ route).
- API routes via FastAPI `TestClient` against a fake worker/repo.

The `browser/` modules (login, scrape, humanize) are driven through a thin
`BrowserSession` interface, so the worker is tested against a fake browser. Real-browser
behavior is covered by an **integration test gated behind a marker** (requires creds +
network) and is not part of the default unit run.

## Migration / reuse from `PY/`

- Port to async: `login.py` → `browser/login.py`, `scrape.py` → `browser/scrape.py`,
  `common.py` (human_*, Cloudflare wait, logger, timestamps) → `browser/humanize.py`.
- Drop entirely: `xtz_decrypt.py`, `wasm_runner.py`, `dump_js.py` and the inline
  decrypt path in `scrape.py` (decryption is the Rust decoder's job).
- The original `PY/` project is left in place; `scraper-py/` is a fresh project.

## Open questions

None blocking. Defaults chosen above are configurable.
