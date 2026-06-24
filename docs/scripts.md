# Operator scripts (`scripts/`)

> Part of the [documentation map](../OVERVIEW.md). These are thin convenience
> wrappers around the scraper's [HTTP API](./scraper-py/api.md) — they add no
> behavior the API doesn't already expose. Source of truth for shapes and status
> codes is still [`api.md`](./scraper-py/api.md).

A small set of Bash scripts for running and driving the scraper from the command
line. They live at the **repo root** (not inside either project) because they
operate the scraper as a black box over HTTP — they never import its internals,
keeping the [two-project split](./architecture.md) intact.

## The scripts

| Script | What it does |
|---|---|
| `start-scraper.sh` | Activates `scraper-py/.venv` (if present) and runs `uvicorn app.main:app`, bound to the `API_HOST`/`API_PORT` from `scraper-py/.env`. Extra args pass through to uvicorn (e.g. `--reload`). |
| `enqueue.sh [CSV]` | Reads a CSV of tabs and enqueues them in one `POST /jobs/bulk` call. Defaults to `scripts/tabs.csv`. |
| `discover.sh [--max N] [--list] [--enqueue]` | Starts an official-tab [discovery run](./scraper-py/discovery.md) (`POST /discover`). `--max N` caps the run at `N` distinct tabs (sets `target_cap`); `--list` shows recent runs and progress (`GET /discover`); `--enqueue` turns discovered-but-unscraped tabs into scrape jobs (`POST /discover/enqueue`). |
| `status.sh` | Pretty-prints `GET /status` (state, current job, queue depth, counts, paused, login health). |
| `clear.sh` | `DELETE /jobs` — cancels every **queued** job and prints the count. The in-flight job finishes first; pair with `pause.sh` to also stop new work. With `--hard-reset` it instead factory-wipes all tracked data (see below). |
| `pause.sh` | `POST /pause` — stop the worker after the current job finishes. |
| `resume.sh` | `POST /resume`. |
| `_common.sh` | Shared helper, **sourced** by the others (not run directly). Loads `.env`, derives `BASE_URL`, and provides an auth-aware curl wrapper. |

## How they find the service

`_common.sh` loads `scraper-py/.env` (without clobbering anything already set in
your environment) and reads:

- `API_HOST` (default `127.0.0.1`), `API_PORT` (default `8000`) → `BASE_URL`.
  A `0.0.0.0` bind host is rewritten to `127.0.0.1` for the client.
- `API_KEY` — when non-empty, every request sends the `X-API-Key` header.

Any value can be overridden from the environment, and `BASE_URL` can be set
directly:

```bash
SCRAPER_URL=http://otherhost:9000 ./status.sh
API_KEY=… ./enqueue.sh my-tabs.csv
```

## Logging

The worker logs its progress to the `start-scraper.sh` terminal (stderr). The
service configures the `app` logger at `INFO` on startup, so these lines surface
without any extra flags. The high-signal lines, all under fixed prefixes:

- `[JOB] Scraping <tab_id>` — a scrape job started.
- `[ERROR] Failed to scrape <tab_id>: <reason>` — a job failed (any failure path:
  permanent, transient/timeout, no artifacts captured, or output-write failure).
  A session-expiry re-queue is **not** a failure and logs no `[ERROR]`.
- `[COMPLETE] Finished scraping N tab(s)` — emitted once the queue drains, where
  `N` is the tabs successfully scraped since it last drained.
- `[JOB] started discovering up to X tracks` / `[COMPLETE] Finished discovering Y
  tabs` — a [discovery run](./scraper-py/discovery.md) starting and finishing.

On a connection failure or any HTTP status ≥ 400 the scripts print a one-line
error (plus the response body) to stderr and exit non-zero.

## Hard reset (`clear.sh --hard-reset`)

Unlike every other script, `--hard-reset` does **not** go over HTTP — it is an
operator-level wipe that no single endpoint can cover (the scraper has no
business touching the enricher's DB). It permanently deletes **all tracked data
across the services**:

- the shared `output/` tree (resolved from `OUTPUT_DIR`, default repo-root
  `output/`) — then recreates it empty;
- the scraper queue DB (`DB_PATH`, default `scraper-py/scraper.db`);
- the enricher DB (`ENRICHER_DB`, default `enricher-py/enricher.db`);
- and each DB's SQLite `-wal`/`-shm`/`-journal` sidecars.

The browser login session (`scraper-py/camoufox-profile/`) is **kept**, so you
don't have to re-login through Cloudflare.

Safeguards: the command **refuses to run while the scraper is reachable** (probes
`GET /status`) — deleting the DB out from under a live worker lets the WAL
resurrect rows, so stop `start-scraper.sh` first (and don't run it during an
`enricher run`, which holds the enricher DB open). It then prints what will be
deleted and **prompts for confirmation**: you must type `yes` to proceed. The
wipe is irreversible.

## The enqueue CSV

One tab per line, comma-separated:

```
url_or_route[,priority[,force]]
```

- `url_or_route` — a full UG tab URL **or** a bare `artist/song-slug` route
  (required). Server-side [normalization](./scraper-py/queue-and-worker.md#normalization)
  turns either into the canonical `tab_id`.
- `priority` — integer, lower runs sooner (default `0`).
- `force` — `true`/`false` (also `1`/`yes`); re-scrape even if already succeeded
  (default `false`).

Blank lines and `#` comments are skipped, as is a header row whose first cell is
`url_or_route` or `url`. See [`scripts/tabs.csv.example`](../scripts/tabs.csv.example).

Because the call goes through `POST /jobs/bulk`, two server-side behaviors apply
(see [api.md](./scraper-py/api.md#enqueue)): rows that fail normalization are
**silently skipped**, and a tab that already `succeeded` is **deduped** (the
existing job is returned, no new job created) unless `force` is set. So the
accepted-job count the script prints can be lower than the number of rows
submitted — that's expected.

> Requires `jq` (used to build the request body safely).

## Typical loop

```bash
./start-scraper.sh                 # in one terminal (foreground)
./enqueue.sh scripts/tabs.csv      # in another
./status.sh                        # watch progress
./pause.sh   # / ./resume.sh       # control the worker
```

Then run the [decoder](./decoder-rs/overview.md) over the same `OUTPUT_DIR` to
turn the captured `.xtz` files into `.gp`.

## Discovery loop

Instead of supplying tabs by hand, you can let the scraper crawl UG's explore
listing for official tabs (see [discovery](./scraper-py/discovery.md)). Discovery
only starts when the scrape queue is empty:

```bash
./discover.sh --max 50    # crawl until 50 distinct tabs are found
./discover.sh --list      # watch run state / progress
./discover.sh --enqueue   # turn the discovered tabs into scrape jobs
./status.sh               # watch the worker scrape them
```
