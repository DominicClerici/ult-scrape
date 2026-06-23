# scraper-py — HTTP API

> Part of the [documentation map](../../OVERVIEW.md) ·
> [scraper overview](./overview.md). Source: `app/api/routes.py`,
> request/response models in `app/models.py`.

Localhost-only by default (`API_HOST=127.0.0.1`). An optional `API_KEY` enables a
`X-API-Key` header check on every endpoint except `/healthz` (off by default —
see [configuration](./configuration.md)).

## Endpoints

| Method & path | Purpose |
|---|---|
| `GET /healthz` | Liveness probe. Always `{"ok": true}`; no auth. |
| `GET /status` | Service state, current job, queue depth, counts by status, paused flag, live login health. |
| `GET /jobs?status=&limit=&offset=` | List/filter jobs (newest first). Defaults `limit=50`, `offset=0`. |
| `GET /jobs/{id}` | Job detail. 404 if unknown. |
| `POST /jobs` | Enqueue one job. |
| `POST /jobs/bulk` | Enqueue many jobs in one call. |
| `DELETE /jobs/{id}` | Cancel a **queued** job → `canceled`. 404 if unknown, 409 if not queued (e.g. running). |
| `POST /jobs/{id}/retry` | Re-queue a **failed** job. 404 if unknown, 409 if not failed. |
| `POST /pause` | Pause the worker after the current job finishes (persists `paused=true`). |
| `POST /resume` | Resume the worker. |
| `POST /discover` | Start a discovery run. **409** if any queued/running jobs exist, or a discovery run is already active. |
| `GET /discover` | List discovery runs (newest first). Optional `?limit=` (default 20). |
| `GET /discover/{run_id}` | Get a single discovery run. 404 if unknown. |
| `POST /discover/{run_id}/cancel` | Request cancellation of an active run. 404 if unknown, 409 if not cancelable (already finished). |
| `POST /discover/enqueue` | Enqueue all discovered tabs that have no `succeeded` job yet. Returns the list of created/existing jobs. |

## Enqueue

`POST /jobs` body ([`EnqueueRequest`](#models)):

```json
{ "url_or_route": "eagles/hotel-california-official-1910943", "priority": 0, "force": false }
```

- `url_or_route` accepts a full UG tab URL **or** a bare `artist/song-slug` route.
  It is [normalized](./queue-and-worker.md#normalization) to the canonical
  `tab_id`. Unparseable input → **422**.
- `priority` — lower runs sooner (default `0`).
- `force` — bypass the dedup short-circuit (re-scrape even if already succeeded).
- **Dedup:** if a `succeeded` job already exists for the same `tab_id` and
  `force` is false, the existing job is returned and **no new job is created**.
- On success the worker is woken immediately (`notify_enqueued()`), so a queued
  job is picked up without waiting for the poll interval.

`POST /jobs/bulk` takes `{ "items": [ <EnqueueRequest>, ... ] }` and returns the
list of created/existing jobs. Items that fail normalization are **silently
skipped** (no 422 for the batch).

## Status

`GET /status` returns [`StatusResponse`](#models):

```json
{
  "state": "idle",
  "current_job_id": null,
  "queue_depth": 0,
  "counts": { "succeeded": 12, "failed": 1, "queued": 0 },
  "paused": false,
  "logged_in": true
}
```

- `state` is the live `ServiceState`: `starting | logging_in | idle | running | paused | discovering | error`.
- `logged_in` calls the browser's `is_logged_in()` live (checks for the UG profile link).

## Models

Defined in `app/models.py`:

- `EnqueueRequest { url_or_route: str, priority: int = 0, force: bool = False }`
- `BulkEnqueueRequest { items: list[EnqueueRequest] }`
- `Job` — the full persisted job row (see [queue & worker](./queue-and-worker.md#jobs-table)).
- `StatusResponse { state, current_job_id, queue_depth, counts, paused, logged_in }`
- `DiscoveryRun { id, params, state, created_at, started_at, finished_at, slices_total, slices_done, tabs_found, cancel_requested, error }`
- `DiscoveryStartRequest { sorts?, facet_ladder?, max_slices?, target_cap?, genres?, decades?, untagged_sweep? }` — all fields optional; omitted fields inherit settings defaults.
- Enums `JobStatus` (`queued|running|succeeded|failed|canceled`) and `ServiceState` (`starting|logging_in|idle|running|paused|discovering|error`).

## Discovery

`POST /discover` accepts an optional `DiscoveryStartRequest` body with
per-run overrides. Any field omitted (or `null`) falls back to the corresponding
`DISCOVERY_*` setting:

```json
{
  "sorts": ["date_desc", "artistname_asc"],
  "facet_ladder": ["genres", "decade"],
  "max_slices": 50,
  "target_cap": 5000,
  "genres": [1, 6],
  "untagged_sweep": false
}
```

The endpoint records a `DiscoveryRun` row in state `requested` and signals the
worker. The worker claims the run only after the scrape queue drains — scraping
and discovery are mutually exclusive. See [discovery](./discovery.md) for the
full crawl model.

`POST /discover/enqueue` draws from `tab_metadata`: it fetches all tabs that
have no `succeeded` job and enqueues them using the same `repo.enqueue()` path
as `POST /jobs`. Tabs that already succeeded are skipped by default.

## Wiring note

Endpoints read `repo`, `worker`, and `settings` off `request.app.state`. These are
set either by the lifespan (production, `app/main.py`) or directly via
`create_app(repo=, worker=, settings=)` (tests). This is what lets the API be
tested with a fake worker/repo and no browser.
