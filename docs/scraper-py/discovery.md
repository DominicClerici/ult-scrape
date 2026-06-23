# scraper-py — Pro Tab Discovery

> Part of the [documentation map](../../OVERVIEW.md) ·
> [scraper overview](./overview.md) · [API](./api.md) ·
> [Queue & worker](./queue-and-worker.md) · [Configuration](./configuration.md).
> Sources: `app/discovery/`, `app/browser/discover.py`, `app/repo.py`,
> `app/api/routes.py`, `app/worker.py`.

Discovery enumerates Pro tabs from Ultimate Guitar's explore listing and persists
their metadata to SQLite. The goal is a complete catalogue of known Pro tab IDs
that can later be turned into scrape jobs — it does **not** enqueue or download
anything automatically.

## Purpose

The scraper normally receives exact tab URLs via `POST /jobs`. Discovery
provides a separate path: crawl the UG explore listing, collect every Pro tab
the site advertises, and store the raw UG metadata in `tab_metadata`. A human
operator then decides which discovered tabs to scrape by calling
`POST /discover/enqueue`.

Three explicit non-goals keep the boundary clean:

- **No auto-enqueue.** Discovery never creates jobs on its own. Enqueuing is a
  deliberate, separate step.
- **No decryption and no output files.** Discovery never writes under
  `OUTPUT_DIR`; the [output contract](../output-contract.md) is untouched.
- **No enrichment or audio.** Only the raw JSON that UG's explore page exposes
  is persisted — no further requests are made per tab.

## Trigger and ownership model

Discovery is endpoint-triggered and worker-owned:

1. `POST /discover` records a `DiscoveryRun` row in state `requested` and
   signals the worker's wakeup event (`notify_enqueued()`). The request is
   accepted only when the scrape queue is empty and no other discovery run is
   active; both conditions are checked atomically before inserting.
2. On every worker iteration, `repo.claim_discovery()` is checked **before**
   `repo.claim_next()`. If a `requested` run exists it is atomically promoted to
   `running` and the worker enters `DISCOVERING` state.
3. While a discovery run is active, the worker does nothing else. Scraping and
   discovery are mutually exclusive — both use the browser, and the worker owns
   the browser.

This means discovery only starts when the queue is drained, and while it runs
no scrape jobs are processed.

## Modules (`app/discovery/`)

### `parser.py`

`parse_explore_html(page_html: str) -> ExploreStore`

Extracts UG's embedded store from the explore page. The page embeds its data in
a `<div class="js-store" data-content="...">` attribute. The parser locates this
attribute with a regex, HTML-unescapes the value, and JSON-decodes the payload to
produce an `ExploreStore` (tabs, pagination, available filters, sort orders).

**Brittle point:** if UG changes the `js-store data-content` shape — the
attribute name, the nesting under `store.page.data`, or the `totalResults`/
`pagination` keys — this step raises `DiscoveryParseError`. When a discovery
run mysteriously fails with a parse error, start here.

### `facets.py`

Typed views over the `ExploreStore` data:

- `FacetCatalog` — the full set of available filter facets (genres, decade,
  tonality, …) and sort orders extracted from a store via `catalog_from_store()`.
- `SliceSpec` — an immutable filter+sort combination that describes one
  addressable slice of the listing.
- `build_query(spec, page)` — builds the `?key[]=value&order=…&page=N` query
  string that the explore endpoint accepts.

### `planner.py`

Three functions that drive the adaptive crawl strategy (see below):

- `initial_slices(catalog, *, untagged_sweep, genres, decades)` — produces the
  starting worklist: one `SliceSpec` per genre (optionally filtered to a
  caller-supplied list), plus an untagged slice if `untagged_sweep` is enabled.
- `subdivide(spec, catalog, ladder)` — given an over-full slice, attempts to
  narrow it by adding the next unused facet from the ladder. Returns `None` if
  the ladder is exhausted.
- `sort_windows(spec, sorts)` — as a last resort, produces one slice per sort
  order so different sort windows expose different tabs.

### `runner.py`

`runner.run(browser, repo, run, settings, *, sleep=asyncio.sleep)` is the
top-level coroutine called by the worker. It:

1. Resolves effective settings by merging `run.params` overrides over the
   `DISCOVERY_*` config defaults.
2. Fetches page 1 of the bare Pro listing (no genre filter) to bootstrap the
   `FacetCatalog`.
3. Builds the initial genre worklist via `planner.initial_slices()`.
4. Runs the adaptive crawl loop (see strategy below).
5. Reports progress to `repo.update_discovery_progress()` after each slice.
6. Calls `repo.finish_discovery(run_id, "done"|"canceled"|"failed")` on exit.

### `browser/discover.py`

The thin seam between the runner and the browser. `fetch_explore_html(page,
query, timeout_ms)` fetches one explore page:

- First attempt: in-page `fetch()` with `credentials: 'include'` (fast, uses
  the existing logged-in session without a navigation).
- Fallback: `page.goto()` to `domcontentloaded` if the fetch call throws (e.g.
  CORS or network error).

The runner calls this through `browser.fetch_explore(query)` — the
`CamoufoxBrowserSession` method that wraps `fetch_explore_html`.

## The 1000-cap and adaptive crawl strategy

UG's explore endpoint is **capped at 20 pages × 50 results = 1000 reachable
tabs** per filter+sort combination. Any slice with more than 1000 tabs will
silently truncate.

The runner detects a capped slice when `store.pages >= 20` and
`total_results > 20 × per_page`. When it does:

1. **Facet-ladder subdivision** — `subdivide()` tries to narrow the slice by
   adding the next unused facet from `DISCOVERY_FACET_LADDER` (by default:
   `genres → decade → tonality`). Each subdivision produces N new slices (one
   per available facet value) and pushes them to the front of the worklist.
2. **Sort-order windows** — if the ladder is exhausted (all facets are already
   set), `sort_windows()` fans out to one slice per sort order. Different sort
   orders expose different 1000-tab windows from the same underlying set.

A slice that is not capped (or that already carries a sort order) is crawled in
full up to the page cap.

Deduplication is by **numeric tab ID** (`seen` set in `runner.run`). The same
tab appearing in multiple slices or sort windows is persisted only once.
The `seen` set is in-memory and scoped to a single run, so each discovery run
re-crawls from scratch; `tabs_found` reflects tabs seen in that run, not a
running total across all runs.

The optional **untagged sweep** (`DISCOVERY_UNTAGGED_SWEEP=true`) adds a final
slice with no genre filter to catch tabs that UG hasn't tagged to any genre.

> **Coverage caveat:** near-exhaustive, not provably complete. A slice that
> still saturates in every sort window after the full ladder is exhausted will
> truncate silently. The per-artist backbone remains future work.

## Database tables

### `tab_metadata`

Persists one row per discovered tab (upsert on `tab_id`).

| Column | Notes |
|---|---|
| `tab_id` | Canonical route (the same dedup key as the `jobs` table) — primary key |
| `numeric_id` | UG's integer tab id from the explore record |
| `route` | Same as `tab_id` |
| `url` | Full `https://tabs.ultimate-guitar.com/tab/…` URL |
| `explore_json` | Raw UG explore record as JSON (all fields UG returns) |
| `first_seen_at` | Epoch seconds when the tab was first discovered |
| `last_seen_at` | Epoch seconds of the most recent discovery that saw it (updated on upsert) |
| `discovery_run_id` | The run that last upserted this row |

### `discovery_runs`

Tracks one row per discovery run.

| Column | Notes |
|---|---|
| `id` | UUID4 string — primary key |
| `params_json` | JSON of the `DiscoveryStartRequest` overrides (may be `{}`) |
| `state` | `requested` → `running` → `done` \| `canceled` \| `failed` |
| `created_at` | Epoch seconds |
| `started_at` | Epoch seconds when the worker claimed the run (nullable) |
| `finished_at` | Epoch seconds when the run ended (nullable) |
| `slices_total` | Running estimate of total slices (updated after each slice) |
| `slices_done` | Slices completed so far |
| `tabs_found` | Distinct tabs accumulated (deduped count) |
| `cancel_requested` | `1` once `POST /discover/{id}/cancel` has been called |
| `error` | Error message if `state='failed'` (nullable) |

Index `idx_discovery_state` on `state` speeds up the `claim_discovery()` and
`has_active_discovery()` queries.

## Configuration

All `DISCOVERY_*` keys are optional; defaults are production-ready.
See [Configuration](./configuration.md) for the full table.

| Key | Default | Effect |
|---|---|---|
| `DISCOVERY_SORT_ORDERS` | `date_desc,artistname_asc,artistname_desc,songname_asc` | Ordered list of sort windows for over-full slices |
| `DISCOVERY_FACET_LADDER` | `genres,decade,tonality` | Ordered facets tried for subdivision before falling back to sort windows |
| `DISCOVERY_PAGE_DELAY_MIN` | `2.0` | Min seconds of human-like delay between page fetches |
| `DISCOVERY_PAGE_DELAY_MAX` | `6.0` | Max seconds between page fetches |
| `DISCOVERY_MAX_SLICES` | `0` (unlimited) | Stop after N slices (0 = no limit) |
| `DISCOVERY_TARGET_CAP` | `0` (unlimited) | Stop once N distinct tabs are found (0 = no limit) |
| `DISCOVERY_REQUEST_TIMEOUT_MS` | `30000` | Per-page fetch timeout passed to `fetch_explore_html` |
| `DISCOVERY_UNTAGGED_SWEEP` | `true` | Add a final no-genre slice to catch untagged tabs |

Each `DISCOVERY_*` key can also be overridden **per run** via the
`DiscoveryStartRequest` body on `POST /discover` — the runner's `_resolve()`
merges request-level overrides on top of settings defaults.
