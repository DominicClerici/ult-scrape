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
| `enqueue()` | Inserts a `queued` job. Returns the existing succeeded job instead if dedup applies and `force` is false. |
| `claim_next()` | Atomic `UPDATE ... WHERE id = (SELECT ... WHERE status='queued' AND next_attempt_at<=now ORDER BY priority, created_at LIMIT 1) RETURNING *`. Sets `running`. Can't double-claim. |
| `succeeded_output_for()` | Dedup-on-claim lookup: is there already a succeeded job for this `tab_id`? |
| `mark_succeeded()` | `running → succeeded`, records `output_dir`, clears `error`. Terminal. |
| `record_transient_failure()` | `attempts++`; if `>= max_attempts` → `failed` (dead-letter), else `queued` with `next_attempt_at = now + backoff(attempts)`. |
| `mark_permanent_failure()` | `attempts++` then straight to `failed`. No retries. |
| `requeue_unchanged()` | `running → queued`, **no attempt consumed** (used for session expiry). |
| `cancel()` | `queued → canceled`, only while queued (returns false otherwise → API 409). |
| `retry()` | `failed → queued`, resets `attempts=0`, clears `error`/timestamps. |
| `reset_running_to_queued()` | Startup recovery: any `running` job left over from a crash → `queued`. Called in the lifespan. |

### Backoff

`repo.backoff(attempts, base)` = `base * 2^(attempts-1)` — exponential, where
`base` is `BACKOFF_BASE_SECONDS` (default 30s). So attempt 1→base, 2→2×base, etc.

## Error taxonomy

Raised by the browser layer (`app/errors.py`), handled by the worker:

| Class | Examples | Worker action |
|---|---|---|
| `TransientScrapeError` (and any unexpected `Exception`) | Cloudflare/navigation timeout, no `.xtz` captured | `record_transient_failure` → retry with backoff, then dead-letter |
| `PermanentScrapeError` | Tab 404 / invalid route | `mark_permanent_failure` → dead-letter immediately |
| `SessionExpiredError` | Logged-out state detected mid-job | `requeue_unchanged` (no attempt consumed) + re-login, then resume |

**Session expiry is not a job failure.** It re-queues the job unchanged and drives
re-login, so a dropped session never burns a job's retry budget.

## The worker loop

`app/worker.py` → `Worker.run()`, started by the lifespan **after** the browser is
up and login confirmed. Each iteration:

1. If `repo.is_paused()` → set state `PAUSED`, await the resume event, loop.
2. `repo.claim_next()`. If `None` → state `IDLE`, await the wakeup event (signaled
   on enqueue/retry) with a `POLL_INTERVAL_SECONDS` timeout, loop.
3. State `RUNNING`. `_process(job)`:
   - **Dedup short-circuit:** if not `force` and a succeeded output already exists
     for `tab_id`, `mark_succeeded` pointing at the existing dir; done.
   - `browser.scrape(job.url)` → list of `CapturedArtifact`.
   - Map exceptions via the taxonomy above; empty artifacts → transient failure.
   - On success → `write_job_output(...)` ([output contract](../output-contract.md))
     then `mark_succeeded`. A write failure is treated as transient.
4. Human-like inter-job delay (`random.uniform(INTER_JOB_DELAY_MIN, MAX)`).

`ServiceState` (`starting | logging_in | idle | running | paused | error`) is
surfaced by `GET /status`. Control primitives: `notify_enqueued()` (wakeup),
`request_resume()` (resume), `stop()` (shutdown; set in the lifespan `finally`).

## Testing this layer

Covered by `tests/test_repo_basic.py`, `test_repo_transitions.py`, `test_worker.py`,
`test_normalize.py`, `test_db.py`, `test_models.py`. The `repo` fixture
(`tests/conftest.py`) uses in-memory SQLite and an injectable clock
(`repo.clock["t"]`) so backoff timing is deterministic. The worker is tested
against a fake `BrowserSession`.
