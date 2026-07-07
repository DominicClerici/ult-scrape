# scraper-py — Queue & Worker

> Part of the [documentation map](../../OVERVIEW.md) ·
> [scraper overview](./overview.md). Sources: `app/repo.py`, `app/worker.py`,
> `app/db.py`, `app/models.py`, `app/errors.py`, `app/normalize.py`.

This is the deterministic, browser-free core of the scraper: a SQLite-backed job
queue, an atomic state machine in `repo.py`, and a single async worker loop in
`worker.py`. It is fully unit-tested without a browser.

## SQLite as source of truth

`app/db.py` opens the DB with `aiosqlite` in **WAL mode** and `Row` factory, and
creates the schema. `repo.py` is the **only** module that issues SQL.

### `jobs` table

| Column | Notes |
|---|---|
| `id` | UUID4 string, primary key |
| `tab_id` | Canonical route — the **dedup key** |
| `url` | Full UG tab URL the worker navigates to |
| `status` | `queued` \| `running` \| `succeeded` \| `failed` \| `canceled` |
| `priority` | Integer; **lower = sooner** (default 0) |
| `attempts` | Attempts made so far |
| `max_attempts` | Snapshot of the limit for this job (default from config) |
| `next_attempt_at` | Epoch seconds; job eligible when `<= now` |
| `force` | If true, bypass the dedup short-circuit |
| `created_at` / `updated_at` / `started_at` / `finished_at` | Timestamps (epoch seconds) |
| `error` | Last error message (nullable) |
| `output_dir` | Path to the committed output dir once succeeded (nullable) |

Indexes: `idx_jobs_claim(status, next_attempt_at, priority, created_at)` for the
claim query, and `idx_jobs_tab(tab_id, status)` for dedup lookups.

### `app_state` table

A tiny key/value store for state that must survive restart. Currently just
`paused` (`"1"`/`"0"`).

### `tab_metadata` table

Persists raw UG explore metadata for every discovered official tab (upsert on
`tab_id`). Written only by discovery — never by the scrape path.

| Column | Notes |
|---|---|
| `tab_id` | Canonical route — primary key (same key space as `jobs.tab_id`) |
| `numeric_id` | UG's integer tab id from the explore record |
| `route` | Same as `tab_id` |
| `url` | Full `https://tabs.ultimate-guitar.com/tab/…` URL |
| `explore_json` | Raw UG explore record (all fields UG returns) |
| `first_seen_at` | Epoch seconds when the tab was first seen |
| `last_seen_at` | Epoch seconds of the most recent discovery upsert |
| `discovery_run_id` | The run that last upserted this row |

### `discovery_runs` table

Tracks every discovery run from `requested` to terminal state.

| Column | Notes |
|---|---|
| `id` | UUID4 string — primary key |
| `params_json` | JSON of per-run override params from `DiscoveryStartRequest` |
| `state` | `requested` → `running` → `done` \| `canceled` \| `failed`; a run canceled while still `requested` goes straight to `canceled` |
| `created_at` | Epoch seconds |
| `started_at` | Epoch seconds when the worker claimed the run (nullable) |
| `finished_at` | Epoch seconds when the run ended (nullable) |
| `slices_total` | Running estimate of total slices |
| `slices_done` | Slices completed so far |
| `tabs_found` | Distinct tabs found (deduped) |
| `cancel_requested` | `1` once a cancel has been requested; polled each slice |
| `error` | Error message if `state='failed'` (nullable) |

Index `idx_discovery_state` on `state` speeds up `claim_discovery()` and
`has_active_discovery()`.

Discovery repo methods in `repo.py`: `request_discovery()`,
`claim_discovery()`, `get_discovery_run()`, `list_discovery_runs()`,
`update_discovery_progress()`, `finish_discovery()`,
`request_discovery_cancel()`, `is_discovery_cancel_requested()`,
`fail_interrupted_discovery()`, `upsert_tab_metadata()`, `discovered_routes()`,
`has_active_discovery()`.

## Normalization

`app/normalize.py` → `normalize_tab(url_or_route) -> (tab_id, url)`:

- Accepts a full URL (extracts everything after `/tab/`) or a bare route.
- Strips slashes; **requires** the route to contain a `/` (i.e. `artist/song`),
  else raises `ValueError` (surfaced as HTTP 422).
- Returns the canonical `tab_id` and the full URL
  `https://tabs.ultimate-guitar.com/tab/<tab_id>`.

This canonicalization is what makes dedup reliable: the same tab referenced as a
URL or a route maps to the same `tab_id`.

## Job state machine

```
            enqueue
               │
               ▼
           ┌────────┐   claim (next_attempt_at<=now,    ┌─────────┐
           │ queued │ ──order by priority,created)──────▶│ running │
           └────────┘                                    └─────────┘
             ▲   │                                          │  │  │
   re-queue  │   │ DELETE (only while queued)               │  │  │ success
   (backoff) │   ▼                                          │  │  ▼
           ┌────────┐                            transient  │  │ ┌───────────┐
           │canceled│◀───────────────────────────fail+retry │  │ │ succeeded │ (terminal)
           └────────┘                            remaining ─┘  │ └───────────┘
                                                               │
                                          retries exhausted /  │
                                          permanent error      ▼
                                                           ┌────────┐
                                               POST /retry │ failed │ (dead-letter, terminal,
                                               ───────────▶└────────┘  re-queueable)
```

Each transition is a single committed SQL statement in `repo.py`:

| Method | Transition |
|---|---|
| `enqueue()` | Inserts a `queued` job. With `force` false it is idempotent against live work: returns the existing `succeeded` job if one exists, else the existing `queued`/`running` job if one exists. Only `failed`/`canceled` history (or none) inserts a new row — re-enqueue after failure is deliberate retry semantics. |
| `claim_next()` | Atomic `UPDATE ... WHERE id = (SELECT ... WHERE status='queued' AND next_attempt_at<=now ORDER BY priority, created_at LIMIT 1) RETURNING *`. Sets `running`. Can't double-claim. |
| `succeeded_output_for()` | Dedup-on-claim lookup: is there already a succeeded job for this `tab_id`? |
| `mark_succeeded()` | `running → succeeded`, records `output_dir`, clears `error`. Terminal. |
| `record_transient_failure()` | `attempts++`; if `>= max_attempts` → `failed` (dead-letter), else `queued` with `next_attempt_at = now + backoff(attempts)`. |
| `mark_permanent_failure()` | `attempts++` then straight to `failed`. No retries. |
| `requeue_unchanged(delay=0.0)` | `running → queued`, **no attempt consumed**; sets `next_attempt_at = now + delay` (used for session expiry, with `SESSION_EXPIRY_BACKOFF_SECONDS`). |
| `cancel()` | `queued → canceled`, only while queued (returns false otherwise → API 409). |
| `cancel_all_queued()` | Bulk clear: every `queued → canceled` in one statement; returns the count. Leaves `running` untouched (backs `DELETE /jobs`). |
| `retry()` | `failed → queued`, resets `attempts=0`, clears `error`/timestamps. |
| `reset_running_to_queued()` | Startup recovery: any `running` job left over from a crash → `queued`. Called in the lifespan. |
| `fail_interrupted_discovery()` | Startup recovery: any `running` discovery run left over from a crash → `failed` with error `"interrupted by restart"`. Called in the lifespan alongside `reset_running_to_queued()`. Runs still in `requested` are left intact — they survive restart and are serviced once the worker is up. |

### Backoff

`repo.backoff(attempts, base)` = `base * 2^(attempts-1)` — exponential, where
`base` is `BACKOFF_BASE_SECONDS` (default 30s). So attempt 1→base, 2→2×base, etc.

## Error taxonomy

Raised by the browser layer (`app/errors.py`), handled by the worker:

| Class | Examples | Worker action |
|---|---|---|
| `TransientScrapeError` (and any unexpected `Exception`) | Cloudflare/navigation timeout, no `.xtz` captured | `record_transient_failure` → retry with backoff, then dead-letter |
| `RateLimitScrapeError` (subclass of `TransientScrapeError`) | HTTP `403`/`429` on the tab navigation (UG/Cloudflare block) | logs `[RATE LIMIT]`, `record_transient_failure` (retryable), then an **escalating** cool-off before the next job (see [escalating rate-limit pacing](#escalating-rate-limit-pacing)) |
| `PermanentScrapeError` | Tab 404 / invalid route | `mark_permanent_failure` → dead-letter immediately |
| `SessionExpiredError` | Logged-out state detected mid-job | `requeue_unchanged(delay=SESSION_EXPIRY_BACKOFF_SECONDS)` (no attempt consumed) + re-login, then resume |

`RateLimitScrapeError` is raised in `scrape.py` **before** the logged-in check, so a
block page (which has no profile link) is reported as a rate-limit rather than
mis-classified as session expiry.

**Session expiry is not a job failure.** It re-queues the job unchanged and drives
re-login, so a dropped session never burns a job's retry budget. The requeue now
sets `next_attempt_at = now + SESSION_EXPIRY_BACKOFF_SECONDS` so a persistently
logged-out / unrecoverable state can't spin into a tight re-login loop.

### Circuit breaker

The worker tracks **consecutive non-successful jobs** (`_consecutive_failures`).
Any `SUCCESS` or dedup short-circuit resets the counter; every other outcome
(transient/permanent failure, rate-limit, session-expiry, unexpected error)
increments it. When it reaches `CIRCUIT_BREAKER_THRESHOLD` the worker logs
`[CIRCUIT BREAKER]`, calls `set_paused(True)`, and sets state `PAUSED`. The pause
is **persisted in `app_state`**, so it survives a restart — an operator must
investigate and `POST /resume` (which clears the pause and signals the resume
event). This is the safety net that stops the service from hammering UG when login
is broken, the account is flagged, or Cloudflare is walling every request.

Each job's processing returns a `worker.Outcome` (`SUCCESS` / `DEDUP` / `FAILURE`
/ `RATE_LIMITED` / `SESSION_EXPIRED`) which `run()` feeds to the circuit breaker
(`_note_outcome`), the rate-limit pacing (`_record_pacing_outcome`), and the
inter-job delay (`_delay_between_jobs`).

### Escalating rate-limit pacing

A single `403`/`429` cool-off is a blunt instrument: a flat delay re-hits the same
block at the same cadence. Instead the worker tracks an in-memory **escalation
level** (`_record_pacing_outcome`) that ratchets up as blocks repeat and relaxes
once the site is clearly happy again:

- Every `RATE_LIMITED` outcome **increments** the level (capped at
  `RATE_LIMIT_MAX_LEVEL`) and breaks the recovery streak.
- A run of `RATE_LIMIT_RECOVERY_SUCCESSES` consecutive `SUCCESS`/`DEDUP` outcomes
  **resets** the level to 0 (baseline).
- Any other failure (`FAILURE`/`SESSION_EXPIRED`) **holds** the level but breaks
  the streak, so stray non-rate-limit progress can't prematurely clear it.

The level then drives two delays in `_delay_between_jobs`, both via
`RATE_LIMIT_ESCALATION_FACTOR`:

- **Global cool-off** after a rate-limited job:
  `RATE_LIMIT_DELAY_SECONDS × factor^(level-1)`, capped at
  `RATE_LIMIT_MAX_DELAY_SECONDS`. So level 1 = the base cool-off, then ×factor per
  consecutive strike.
- **Widened inter-job gap** for ordinary jobs while the level is non-zero:
  the normal `random.uniform(INTER_JOB_DELAY_MIN, MAX)` is multiplied by
  `factor^level`, so even successful scrapes are spaced further apart until the
  escalation clears. At level 0 the gap is unchanged.

The level is **in-memory only** (like the circuit breaker's failure counter) and
resets on restart; the persisted `paused` flag remains the durable safety net.
The circuit breaker still counts each rate-limited job, so a sustained block trips
the pause (`CIRCUIT_BREAKER_THRESHOLD`) regardless of escalation.

## The worker loop

`app/worker.py` → `Worker.run()`, started by the lifespan **after** the browser is
up and login confirmed. Each iteration:

1. If `repo.is_paused()` → set state `PAUSED`, await the resume event, loop.
2. `repo.claim_discovery()`. If a `requested` run exists, atomically promote it
   to `running`, set state `DISCOVERING`, run `discovery_runner.run(...)`, then
   loop. Because this check comes **before** `claim_next()`, a pending discovery
   run takes priority over queued scrape jobs: the current job finishes, the
   discovery run is serviced to completion, then scraping resumes. This
   worker-level ordering is what keeps discovery and scraping mutually
   exclusive — both use the browser. `claim_discovery()` also finishes any
   `requested` run whose cancel was requested while it waited (→ `canceled`)
   without ever touching the browser.
3. `repo.claim_next()`. If `None` → state `IDLE`, await the wakeup event (signaled
   on enqueue/retry/discover) with a `POLL_INTERVAL_SECONDS` timeout, loop.
4. State `RUNNING`. `_process(job)`:
   - **Dedup short-circuit:** if not `force` and a succeeded output already exists
     for `tab_id`, `mark_succeeded` pointing at the existing dir; done.
   - `browser.scrape(job.url)` → list of `CapturedArtifact`.
   - Map exceptions via the taxonomy above; empty artifacts → transient failure.
   - On success → `write_job_output(...)` ([output contract](../output-contract.md))
     then `mark_succeeded`. A write failure is treated as transient.
5. Register the job's `Outcome` with the circuit breaker (`_note_outcome`) and the
   rate-limit pacing (`_record_pacing_outcome`), then apply the inter-job delay
   (`_delay_between_jobs`): an escalating cool-off if the job was rate-limited,
   otherwise the human-like `random.uniform(INTER_JOB_DELAY_MIN, MAX)` — itself
   widened while a rate-limit escalation is active. See
   [escalating rate-limit pacing](#escalating-rate-limit-pacing).

`ServiceState` (`starting | logging_in | idle | running | paused | discovering | error`) is
surfaced by `GET /status`. Control primitives: `notify_enqueued()` (wakeup),
`request_resume()` (resume), `stop()` (shutdown; set in the lifespan `finally`).

### Operator logging

The worker emits prefixed `INFO`/`ERROR` lines to the service's stderr (visible in
the `start-scraper.sh` terminal; the `app` logger is configured at `INFO` in the
lifespan via `main._configure_logging()`):

- `[JOB] Scraping <tab_id>` when a real scrape starts (the dedup short-circuit
  reuses an existing output and logs nothing).
- `[ERROR] Failed to scrape <tab_id>: <reason>` on every failure path
  (permanent, transient, empty artifacts, output-write failure). Session-expiry
  re-queues are not failures and emit no `[ERROR]`.
- `[COMPLETE] Finished scraping N tab(s)` when `claim_next()` returns `None` and
  the queue drains. `N` is `Worker._scraped_count` — successful scrapes since the
  last drain — which is then reset.

Discovery has its own matching `[JOB]`/`[COMPLETE]` lines (see
[discovery](./discovery.md)).

## Testing this layer

Covered by `tests/test_repo_basic.py`, `test_repo_transitions.py`, `test_worker.py`,
`test_normalize.py`, `test_db.py`, `test_models.py`. The `repo` fixture
(`tests/conftest.py`) uses in-memory SQLite and an injectable clock
(`repo.clock["t"]`) so backoff timing is deterministic. The worker is tested
against a fake `BrowserSession`.
