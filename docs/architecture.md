# System Architecture

> Part of the [documentation map](../OVERVIEW.md).

This repository is **three decoupled projects** that together turn an Ultimate
Guitar (UG) tab URL into a usable Guitar Pro file with accompanying source audio:

| Project | Language | Role | Docs |
|---|---|---|---|
| [`scraper-py/`](./scraper-py/overview.md) | Python (FastAPI + Camoufox) | Logs into UG, works a queue of scrape jobs, downloads **raw encrypted `.xtz`** bytes to disk. **No decryption.** | [scraper docs](./scraper-py/overview.md) |
| [`decoder-rs/`](./decoder-rs/overview.md) | Rust (CLI) | Walks the scraper's output tree and decrypts each `.xtz` into a Guitar Pro `.gp` (+ extracted `.gpif`). **No scraping.** | [decoder docs](./decoder-rs/overview.md) |
| [`enricher-py/`](./enricher-py/overview.md) | Python (async CLI) | Walks the shared `output/` tree and downloads the best-available full audio (YouTube, Topic-first via `yt-dlp`) per tab. **No scraping, no decryption.** | [enricher docs](./enricher-py/overview.md) |

## The key design decision: one filesystem seam, no shared code

The three projects **never call each other and share no code**. Their only point
of contact is the filesystem: the scraper atomically commits per-tab output
directories, and the decoder and enricher independently discover and process them.
That interface is frozen and documented on its own page:

➡️ **[The output contract](./output-contract.md)** — read this to understand how
the three projects fit together.

This split was deliberate (see the original [design specs](#design-history)):

- **Separation of concerns** — scraping (slow, browser-driven, stateful, network
  flaky) is isolated from decryption (pure, CPU-bound, fast, deterministic).
- **Independent failure & restart** — the decoder can be re-run any time over
  whatever the scraper has committed so far; the scraper never blocks on
  decryption.
- **Language fit** — Python for browser automation (Camoufox/Playwright), Rust
  for a bit-exact, parallel cipher port.

## End-to-end data flow

```
                  ┌─────────────────────────── scraper-py (long-running service) ──────────────────────────┐
  POST /jobs  ──▶ │  FastAPI  ─▶  SQLite queue  ─▶  async Worker  ─▶  Camoufox browser  ─▶  capture .xtz    │
  {url_or_route}  │   (api/)        (repo.py)        (worker.py)        (browser/)          bytes in memory  │
                  │                                                          │                               │
                  │                                          write_job_output() atomically commits          │
                  └──────────────────────────────────────────────│─────────────────────────────────────────┘
                                                                  ▼
                                       OUTPUT_DIR/<tab_id>/  <name>.xtz  +  metadata.json   ◀── the seam
                                                                  │
                  ┌───────────────────────────────────── decoder-rs (one-shot CLI) ──────────│──────────────┐
   $ decoder-rs ─▶│  discover()  ─▶  decrypt_xtz()  ─▶  extract_score_gpif()  ─▶  write_outputs()            │
                  │  (discover.rs)    (cipher.rs)         (output.rs)              (output.rs)                │
                  └──────────────────────────────────────────────│─────────────────────────────────────────┘
                                                                  ▼
                                       OUTPUT_DIR/<tab_id>/  <name>.gp  +  <name>.gpif
                                                                  │
                  ┌──────────────────────────────────── enricher-py (async CLI) ──────────────│─────────────┐
  $ enricher run ▶│  scan output/  ─▶  build query  ─▶  YouTube search  ─▶  yt-dlp download  ─▶  commit      │
                  │  (discover.py)    (query.py)         (sources/youtube.py)                  (output.py)   │
                  └──────────────────────────────────────────────│─────────────────────────────────────────┘
                                                                  ▼
                                       OUTPUT_DIR/<tab_id>/  audio.<ext>  +  audio.json
```

1. A caller enqueues a tab (`POST /jobs` with a UG URL or `artist/song-slug`
   route). The scraper normalizes it to a canonical `tab_id`.
2. The single background worker claims the job, drives the browser to the tab
   page, clears Cloudflare, and captures the encrypted `.xtz` download response(s).
3. On success it atomically writes `OUTPUT_DIR/<tab_id>/` with the raw `.xtz`
   file(s) and a `metadata.json` commit marker.
4. Later (or concurrently), `decoder-rs` is run against the same `OUTPUT_DIR`. It
   finds every committed directory with un-decoded `.xtz` files and writes a
   `.gp` (the Guitar Pro ZIP) and `.gpif` (extracted score XML) beside each.
5. Independently, `enricher-py` walks the same `OUTPUT_DIR`. For each tab with a
   `metadata.json` but no `audio.<ext>` yet, it searches YouTube, selects the best
   candidate (preferring Topic Art Tracks), and downloads best-available audio,
   writing `audio.json` first then renaming the audio file in last as the commit
   marker.

## Why the decoder and enricher can run anytime

The decoder is **idempotent and stateless**: a `<stem>.xtz` is considered "done"
iff a sibling `<stem>.gp` exists. So re-runs skip finished work, and because a
scraper re-scrape wipes the whole tab directory (including any `.gp` the decoder
wrote), re-scraped tabs are naturally re-decoded. No coordination, no lockfiles,
no database shared between the projects. See
[idempotency](./decoder-rs/pipeline.md#idempotency).

The enricher is similarly idempotent: a tab is "done" when `audio.<ext>` exists.
A re-scrape's `rmtree` wipes those files too, so re-scraped tabs will be
re-enriched on the next `enricher run`. The enricher's own SQLite (`enricher.db`)
can be deleted and rebuilt from the tree by `enricher scan`.

## Design history

The detailed brainstorm specs and implementation plans that produced these
projects live under [`docs/superpowers/`](./superpowers/):

- `specs/2026-06-23-scraper-py-service-design.md`
- `specs/2026-06-23-xtz-decoder-rs-design.md`
- `plans/2026-06-23-scraper-py-service.md`
- `plans/2026-06-23-xtz-decoder-rs.md`

These capture the *why* behind locked decisions. The pages under `docs/` (this
map) describe the system *as built* and are the docs to keep current.
