# ult-scrape — Documentation Map

This is the **map** of the project's documentation. Start here, then follow the
links to detailed docs for each part. Every doc page links back here and to its
neighbors, so you can explore the system one hop at a time.

## What this project is

`ult-scrape` turns an Ultimate Guitar (UG) tab URL into a usable Guitar Pro file,
in **three decoupled stages** that share no code — only a filesystem directory:

```
  UG tab URL ──▶  scraper-py  ──▶  OUTPUT_DIR/<tab_id>/*.xtz  ──▶  decoder-rs  ──▶  *.gp + *.gpif
                  (downloads raw      (the frozen seam:            (decrypts;
                   encrypted bytes)    output contract)             no scraping)
                                             │
                                       enricher-py  ──▶  audio.<ext> + audio.json
                                       (downloads best-available source audio per tab)
```

- **[`scraper-py/`](./scraper-py/)** — a FastAPI service that logs into UG, works a
  SQLite queue of scrape jobs with one async browser worker, and writes **raw
  encrypted `.xtz`** files. It does **no** decryption.
- **[`decoder-rs/`](./decoder-rs/)** — a one-shot Rust CLI that walks the scraper's
  output and decrypts each `.xtz` into a Guitar Pro `.gp` (+ extracted `.gpif`). It
  does **no** scraping.
- **[`enricher-py/`](./enricher-py/)** — an async Python CLI that walks the shared
  `output/` tree and downloads the best-available full audio (YouTube, Topic-first
  via `yt-dlp`) into each tab directory.

All three communicate **only** through the filesystem — see the output contract below.

## The map

### System-level

| Doc | Read it to understand… |
|---|---|
| 📐 [Architecture](./docs/architecture.md) | The two-project split, end-to-end data flow, and why it's decoupled. **Start here.** |
| 🔌 [Output contract](./docs/output-contract.md) | The single interface between the two projects: directory layout, the `metadata.json` commit marker, `.xtz`/`.gp`/`.gpif` files, idempotency. |
| 🛠️ [Operator scripts](./docs/scripts.md) | The `scripts/` wrappers for running and driving the scraper from the CLI: `start-scraper.sh`, `enqueue.sh`, `status.sh`, `pause.sh`, `resume.sh`. |

### scraper-py (Python · FastAPI + Camoufox)

| Doc | Covers |
|---|---|
| 📦 [Overview](./docs/scraper-py/overview.md) | Project map, architecture, layout, setup/run/test. **Entry point for the scraper.** |
| 🌐 [HTTP API](./docs/scraper-py/api.md) | Endpoints: enqueue, status, pause/resume, retry, cancel. |
| ⚙️ [Queue & worker](./docs/scraper-py/queue-and-worker.md) | SQLite schema, job state machine, the worker loop, error taxonomy, retry/backoff. |
| 🕸️ [Browser automation](./docs/scraper-py/browser.md) | Camoufox session, login, scrape capture, human-like behavior, Cloudflare. |
| 🔧 [Configuration](./docs/scraper-py/configuration.md) | Every setting/env var, and the atomic output writer. |

### decoder-rs (Rust · CLI)

| Doc | Covers |
|---|---|
| 📦 [Overview](./docs/decoder-rs/overview.md) | Crate layout, build/run, dependencies, tests. **Entry point for the decoder.** |
| 🔐 [XTZ format & cipher](./docs/decoder-rs/xtz-format-and-cipher.md) | The `.xtz` binary format, dual-LFSR key schedule, and hand-rolled ChaCha8. |
| 🏭 [Pipeline](./docs/decoder-rs/pipeline.md) | discover → decode → validate → write, CLI flags, idempotency, parallelism, errors. |

### enricher-py (Python · async CLI)

| Doc | Covers |
|---|---|
| 📦 [Overview](./docs/enricher-py/overview.md) | Module map, queue/idempotency/recovery, commands, deferred work. **Entry point for the enricher.** |

### Design history (the "why")

The brainstorm specs and implementation plans that produced these projects live
under [`docs/superpowers/`](./docs/superpowers/). They capture locked decisions
and rationale. The pages above describe the system **as built** and are the docs
kept current.

## Quick start

```bash
# 1. Scrape (produces OUTPUT_DIR/<tab_id>/*.xtz)
cd scraper-py
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]" && python -m camoufox fetch
cp .env.example .env            # fill in UG_EMAIL / UG_PASSWORD / OUTPUT_DIR
uvicorn app.main:app            # then POST /jobs to enqueue tabs
# …or use the operator scripts (see docs/scripts.md):
#   ./scripts/start-scraper.sh  &&  ./scripts/enqueue.sh scripts/tabs.csv

# 2. Decode (consumes the same OUTPUT_DIR, produces *.gp + *.gpif)
cd ../decoder-rs
cargo build --release
OUTPUT_DIR=../scraper-py/output ./target/release/decoder-rs
```

## Keeping these docs current

These docs are part of the codebase. When you change behavior, update the matching
page in the same change — see the rules in [`CLAUDE.md`](./CLAUDE.md#keeping-documentation-up-to-date).
