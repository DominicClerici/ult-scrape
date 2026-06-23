# enricher-py — overview

> Part of the [documentation map](../../OVERVIEW.md). The **third** decoupled
> project. It reads scraped tabs from the shared `output/` tree and downloads the
> best-available full audio (YouTube, Topic-first via `yt-dlp`) into each tab
> directory. It shares no code with `scraper-py`/`decoder-rs`.

## What it does

For each `output/<tab_id>/` that has a `metadata.json` but no audio yet, the
enricher builds a search query from the tab slug, searches YouTube, selects the
best candidate (preferring `<Artist> - Topic` Art Tracks = studio masters),
downloads best-available audio, and writes `audio.<ext>` + `audio.json`. See the
[output contract](../output-contract.md) for the artifacts.

## Module map

| Module | Responsibility |
|---|---|
| `app/config.py` | Settings (`.env`). |
| `app/db.py` | SQLite schema + connection (`enricher.db`). |
| `app/repo.py` | **Only** SQL owner: queue, transitions, backoff, recovery. |
| `app/query.py` | Tab slug → search query (pure). |
| `app/select.py` | Topic-first candidate selection (pure). |
| `app/discover.py` | Walk `output/`; per-tab filesystem state. |
| `app/output.py` | Atomic commit of `audio.<ext>` + `audio.json`. |
| `app/sources/base.py` | `Candidate`/`DownloadResult`/`AudioProbe` + Protocols. |
| `app/sources/youtube.py` | `yt-dlp`-backed search + download. |
| `app/sources/probe.py` | `ffprobe`-backed audio probe. |
| `app/worker.py` | `enrich_tab` pipeline + `run_pool` (concurrency, pause). |
| `app/cli.py` | `scan` / `run` / `status`. |

## Queue, idempotency, pause & recovery

- A tab is **done** when an `audio.<ext>` file exists; a `no_match` `audio.json`
  marks a permanent miss (skip unless `--retry-failed`). The DB tracks lifecycle
  (attempts/backoff/`failed`), but the filesystem is the source of truth for
  completion — a deleted `enricher.db` is rebuilt from the tree by `scan`.
- **Pause:** `Ctrl-C` sets a stop event; workers finish in-flight jobs, claim no
  more, and exit. Rerun to resume.
- **Crash recovery:** on `run`/`scan` startup, `reset_working_to_pending()`
  reclaims interrupted jobs. Downloads land in a temp dir and are renamed in only
  on success, so a partial never satisfies the done gate.

## Known limitations

- **`--limit` semantics:** `run --limit N` caps the number of jobs *claimed* in
  that run, not the number of downloads completed. A job that hits a transient
  failure consumes a budget unit and is re-queued (with backoff) for a later run;
  a single `--limit N` run may therefore complete fewer than N tabs.
- **Search metadata is best-effort:** the YouTube search uses `yt-dlp
  --flat-playlist`, which can return sparse per-result metadata (duration,
  view_count, and channel may be missing). Selection degrades gracefully — a
  candidate with unknown duration is not rejected by the `MIN_DURATION_S` guard,
  and Topic-channel detection requires the channel field to be present. This is a
  known area for future hardening (see the design spec's future-work section).

## Commands

```bash
cd enricher-py
pip install -e ".[dev]"     # needs ffmpeg (ffprobe) on PATH
enricher scan                           # enqueue tabs needing audio
enricher run --jobs 2                   # download (Ctrl-C = graceful pause)
enricher run --db PATH --output-dir DIR # override DB / output-dir paths
enricher status                         # counts by state
python3 -m pytest                       # unit tests (browser/network-free)
python3 -m pytest -m integration        # live yt-dlp + ffprobe
```

## Deferred / future

- `CAPTURE_NOTE.md` — optional `scraper-py` change to capture clean song
  metadata into `metadata.json`.
- Verification (correct-recording confirmation) and time-alignment are separate
  future steps (see the design spec, §15).
