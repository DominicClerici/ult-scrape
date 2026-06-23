# Pro Tab Discovery — Design

> Status: approved design (brainstorm output). Scope: **discovery only**.
> Enrichment (external music IDs, `metadata.json` wiring, audio) is explicitly
> out of scope and deferred to a later session.

## Goal

Add an endpoint-triggered **discovery** capability to `scraper-py` that
enumerates Ultimate Guitar **Pro** (Guitar Pro / `.xtz`) tabs at large scale
(target 50k+), persisting rich UG-native metadata for every tab found. Discovery
reuses the single warm Camoufox session and is mutually exclusive with scraping.

The end purpose (not built here) is a dataset for training mp3→tab conversion;
this phase produces the **tab list + identity metadata** that later enrichment
will resolve to external IDs and pair with source audio.

## Decisions locked during brainstorming

- **Scale:** large/exhaustive (50k+).
- **Source song:** identity/IDs only, and **deferred** — discovery just captures
  UG's own metadata (incl. the `recording` field); no external resolution now.
- **Placement:** modules inside `scraper-py`, triggered by an HTTP endpoint,
  using the same Camoufox instance as the scraper.
- **Execution model:** recon-then-codify → a **deterministic crawler** (no LLM /
  agent in the run loop), consistent with the repo's existing philosophy.
- **Coverage:** genre × decade slicing + adaptive sort re-crawl on cap-hit +
  an untagged Pro sweep; dedup by numeric tab `id`.
- **Metadata:** persist each tab's **raw explore record** in SQLite, keyed by
  `tab_id`.
- **Enqueue:** discovery **persists only**; a separate manual step enqueues
  discovered tabs as scrape jobs.

## Recon findings (verified against live UG)

- `/explore?type[]=Pro&…` is Cloudflare-gated (raw `curl` → 403) but loads inside
  an authenticated browser context — the Camoufox session the scraper keeps warm.
- Each explore page **server-renders its full state** into
  `<div class="js-store" data-content="{…JSON…}">` → `store.page.data`:
  - `data[]` — **50 tabs/page**, each with: `id`, `song_id`, `artist_id`,
    `song_name`, `artist_name`, `type`, `part`, `version`, `votes`, `difficulty`,
    `rating`, `date`, `tonality_name`, `version_description`, `verified`,
    `recording`, `album_cover`, `tab_url`, `artist_url`, `source`, …
  - `pagination` (`pages`, `per_page: 50`, `current`), `totalResults`, and the
    full `filters` facet catalog.
- **Hard ceiling: 1000 reachable tabs per filter+sort combo** (`pagination.pages`
  maxes at 20 × 50). `totalResults` is a capped display value (≤10000) and is
  **not** how many are paginable.
- **Slicing dimensions:** `type=Pro` (fixed) + genre (24) + decade (8) + ~9 more
  facets, plus **8 sort orders** (`artistname_asc/desc`, `songname_asc/desc`,
  `date_desc`, `rating_desc`, `hitstotal_desc`, `hitsdailygroup_desc`).
- An **in-page `fetch()`** of an explore URL (from inside the CF-passed context)
  returns the full HTML with the embedded store → the crawler can fetch+parse
  per page without a full render each time.
- **Not verified:** per-artist tab listings as a long-tail backbone (initial URL
  guess 404'd). Treated as an optional future phase, not relied upon here.

## Architecture

### Browser ownership (preserves the invariant)

The repo invariant *"only `worker.py` drives the browser"* is preserved.
`POST /discover` never touches the browser; instead:

1. The endpoint validates preconditions, records a **discovery request**
   (`discovery_runs` row, state `requested`), and signals the worker.
2. The **worker loop** checks for a pending discovery request **before**
   `claim_next()`. When idle with an empty queue, it claims the run
   (`requested → running`) and executes discovery itself, under a new
   `ServiceState.DISCOVERING`.
3. "No queue while discovering" is therefore structural: the worker only enters
   discovery when there is nothing to claim, and the endpoint additionally
   returns **409** if any `queued`/`running` job exists at request time, or if a
   run is already `requested`/`running`.

### New package: `app/discovery/`

| Module | Purity | Responsibility |
|---|---|---|
| `parser.py` | pure | `parse_explore_html(html) -> ExploreStore` — locate the `js-store` `data-content`, HTML-entity-decode, `json.loads`, return `tabs: list[dict]`, `pagination`, `total_results`, `filters`. |
| `facets.py` | pure-ish | Build the genre/decade/sort catalog from a live `filters` payload (first fetch), with a static fallback. Helpers to build explore query strings from a slice spec. |
| `planner.py` | pure | Yield ordered **slice specs** (genre × decade, plus an untagged Pro sweep). Adaptive rule: if a slice hits the cap (reached page 20 / `total_results` > 1000), re-emit it under additional sort orders to capture different 1000-windows. Iterable/stateless → unit-testable. |
| `runner.py` | impure | Orchestrate: walk slices, call the browser primitive per page, parse, dedup by numeric `id`, upsert each tab's explore record, update run progress, apply human delays + Cloudflare re-checks, handle session-expiry (re-login like the worker), honor cancel + cap budgets. |

**Adaptive subdivision detail.** A slice is crawled page 1..N (N ≤ 20). The
planner observes the parsed `pagination`/`total_results` for the slice's first
page: if it indicates the slice exceeds 1000, the planner schedules the same
genre×decade slice again under each configured extra sort order. The runner
unions results and dedups by `id`, so re-crawls only add the windows a single
sort couldn't reach. Coverage is **near-exhaustive, not provably complete** — a
genre×decade slice that exceeds 1000 in *every* sort window, and tabs lacking
both genre and decade beyond the untagged sweep's reach, remain possible gaps.
The per-artist backbone (future) is the mitigation if gaps prove material.

### Browser seam (`app/browser/`)

Extend the `BrowserSession` Protocol (`base.py`):

```python
async def fetch_explore(self, query: str) -> str: ...   # raw explore HTML
```

- `session.py` delegates to a new `browser/discover.py`
  (`fetch_explore_html(page, query)`): ensure we're on a UG page (CF-passed),
  then an **in-page `fetch()`** of `/explore?<query>` returning response text.
  `page.goto` is the fallback if in-page fetch fails.
- The fake `BrowserSession` in tests returns canned HTML keyed by query, keeping
  the runner fully testable without a real browser.

### Persistence (`repo.py` — still the only SQL owner; `db.py` schema)

New tables:

```
tab_metadata(
  tab_id TEXT PRIMARY KEY,        -- canonical route (dedup key, matches jobs.tab_id)
  numeric_id INTEGER,             -- UG numeric tab id
  route TEXT,
  explore_json TEXT,              -- raw per-tab explore record (JSON)
  first_seen_at INTEGER,
  last_seen_at INTEGER,
  discovery_run_id TEXT
)

discovery_runs(
  id TEXT PRIMARY KEY,            -- UUID4
  params_json TEXT,
  state TEXT,                     -- requested | running | done | failed | canceled
  created_at INTEGER, started_at INTEGER, finished_at INTEGER,
  slices_total INTEGER, slices_done INTEGER, tabs_found INTEGER,
  error TEXT
)
```

New `repo.py` methods (each a single committed transition where applicable):

- `request_discovery(params) -> DiscoveryRun` — insert `requested` (rejects if one
  is already `requested`/`running`).
- `claim_discovery() -> DiscoveryRun | None` — atomic `requested → running`.
- `update_discovery_progress(run_id, slices_done, tabs_found)`.
- `finish_discovery(run_id, state, error=None)` — `running → done|failed|canceled`.
- `upsert_tab_metadata(run_id, record)` — insert or update by `tab_id`, refreshing
  `last_seen_at` + `explore_json`.
- `count_active_jobs() -> int` — `queued`+`running`, for the 409 precondition.
- `enqueue_discovered(filter…) -> list[Job]` — the manual enqueue step: bulk
  `enqueue()` of discovered tabs not already `succeeded` (reuses existing dedup).
- `reset_running_discovery_to_requested()` — startup recovery for a run left
  `running` by a crash (mirrors `reset_running_to_queued()`).

### Worker (`app/worker.py`)

In the loop, **before** `claim_next()`:

```
if (run := await repo.claim_discovery()):
    state = DISCOVERING
    await discovery.runner.run(browser, repo, run, settings, cancel_check)
    continue
```

Handles `SessionExpiredError` (re-login, then continue), unexpected errors
(`finish_discovery(..., "failed", error)`), and respects pause/stop + a cancel
flag checked between slices.

### API (`app/api/routes.py`, models in `app/models.py`)

| Method & path | Purpose |
|---|---|
| `POST /discover` | Start a run. Body: sort set, `max_slices`, delays, optional genre/decade subset (for testing), target cap. **409** if active jobs or a run already pending/running. Returns the run record. |
| `GET /discover` | List recent runs. |
| `GET /discover/{id}` | Run detail + progress (`slices_done/total`, `tabs_found`). |
| `POST /discover/{id}/cancel` | Set cancel flag; runner stops between slices. |
| `POST /discover/enqueue` | Manual enqueue step: bulk-enqueue discovered tabs not already succeeded (optional filter). Returns created/existing jobs. |

`GET /status` gains the `DISCOVERING` state and current discovery run id.

### Config (`app/config.py` + `.env.example`)

New keys (with defaults): `DISCOVERY_SORT_ORDERS`, `DISCOVERY_PAGE_DELAY_MIN`,
`DISCOVERY_PAGE_DELAY_MAX`, `DISCOVERY_MAX_SLICES` (0 = all),
`DISCOVERY_REQUEST_TIMEOUT_MS`, `DISCOVERY_TARGET_CAP` (0 = unlimited),
`DISCOVERY_ADAPTIVE_SORTS` (bool), `DISCOVERY_UNTAGGED_SWEEP` (bool).

## Explicitly NOT touched

- **Output contract**, `metadata.json`, `app/output.py`, and **all of
  `decoder-rs`** are untouched. Discovery only adds DB tables + an endpoint;
  it writes nothing under `OUTPUT_DIR`.
- Wiring `tab_metadata` into `metadata.json` and any external-ID resolution are
  **deferred enrichment** work.

## Testing

- `parser.py` — against a **committed explore-HTML fixture** captured during
  implementation (sanitized).
- `planner.py` — slice generation + adaptive subdivision ordering.
- `runner.py` — fake `BrowserSession` returning canned HTML, in-memory SQLite,
  injectable clock (`repo.clock["t"]`); asserts dedup, metadata upserts, progress.
- `worker.py` — the discovery-claim branch runs before job claim.
- `routes.py` — `/discover` 409 precondition + happy path with fake worker/repo.
- Browser-free by default; a real `fetch_explore` covered only by an
  `integration`-marked test.

## Docs to update (same change as code)

- **New** `docs/scraper-py/discovery.md` (the discovery component page).
- `docs/scraper-py/overview.md` — add the `discovery/` package to the layout +
  component table; retire/qualify the "No search by artist/title" YAGNI line.
- `docs/scraper-py/api.md` — the new endpoints + `DISCOVERING` status.
- `docs/scraper-py/queue-and-worker.md` — new tables, `DISCOVERING` state, the
  worker discovery branch, startup recovery.
- `docs/scraper-py/configuration.md` — the new `DISCOVERY_*` keys.
- `OVERVIEW.md` — add the discovery doc to the map.

## Open risks / notes

- **Coverage is near-exhaustive, not guaranteed.** Documented above; per-artist
  enumeration is the future mitigation and needs its own recon.
- **UG markup drift.** `parser.py` depends on the `js-store data-content` shape;
  it is the brittle point and should fail loudly (typed error) if the marker or
  schema changes, like the existing browser layer's capture matchers.
- **Cloudflare exposure** scales with page-fetch count; human delays + the
  existing CF wait helpers apply, and `DISCOVERY_MAX_SLICES`/`TARGET_CAP` bound a
  run.
