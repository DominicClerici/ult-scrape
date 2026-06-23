# ult-scraper

FastAPI service that logs into Ultimate Guitar, works a SQLite queue of tab-scrape
jobs with a single async worker, and writes **raw encrypted XTZ** files to disk for a
separate Rust decoder. This service performs no decryption.

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

On startup the service launches Camoufox (headful), confirms login, and idles until
jobs are enqueued.

## API

| Method & path | Purpose |
|---|---|
| `GET /status` | Service state, queue depth, counts, login health |
| `GET /jobs?status=&limit=&offset=` | List/filter jobs |
| `GET /jobs/{id}` | Job detail |
| `POST /jobs` | Enqueue `{ "url_or_route": "...", "priority": 0, "force": false }` |
| `POST /jobs/bulk` | Enqueue `{ "items": [ ... ] }` |
| `DELETE /jobs/{id}` | Cancel a queued job (409 if running) |
| `POST /jobs/{id}/retry` | Re-queue a failed job |
| `POST /pause` / `POST /resume` | Pause/resume the worker |

## Output contract (for the Rust decoder)

Each successful job writes `OUTPUT_DIR/<tab_id>/`:
- `<filename>.xtz` — raw encrypted bytes, exactly as downloaded
- `metadata.json` — written last; its presence marks the directory as complete

## Tests

```bash
python -m pytest                  # unit tests (browser integration excluded by default)
python -m pytest -m integration   # live test; needs UG creds + network
```
