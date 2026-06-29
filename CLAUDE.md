# CLAUDE.md

Guidance for working in this repository. **Read [`OVERVIEW.md`](./OVERVIEW.md)
first** — it is the documentation map and links to a detailed doc page for every
part of the system. Don't re-derive how things work from the code when a doc page
already explains it; read the page, then read the code it points to.

## What this repo is

`ult-scrape` turns an Ultimate Guitar tab URL into a Guitar Pro file in **four
decoupled projects that share no code**, communicating only through a filesystem
directory:

- **`scraper-py/`** — FastAPI + Camoufox service. Logs into UG, works a SQLite
  queue with one async browser worker, writes **raw encrypted `.xtz`** files.
  **No decryption.**
- **`decoder-rs/`** — one-shot Rust CLI. Walks the scraper's output and decrypts
  each `.xtz` into a `.gp` (+ extracted `.gpif`). **No scraping.**
- **`enricher-py/`** — async Python CLI. Walks the shared `output/` tree and
  downloads best-available source audio (YouTube, Topic-first via `yt-dlp`) per
  tab. **No scraping, no decryption.**
- **`aligner-py/`** — Python CLI. Renders `.gp` to reference audio (MuseScore +
  FluidSynth), aligns reference ↔ real audio via chroma-CENS + DTW, writes
  `align.json` (warp function + confidence) per tab.

The boundary between them is the [output contract](./docs/output-contract.md) — a
frozen filesystem layout. **Never** make one project import, call, or read the
other's internals; if you change the contract, change all affected projects at once
and update `docs/output-contract.md`.

## Where to read about each part

| You're working on… | Read |
|---|---|
| The whole system / data flow | [docs/architecture.md](./docs/architecture.md) |
| The scraper↔decoder↔enricher interface | [docs/output-contract.md](./docs/output-contract.md) |
| Scraper service (any part) | [docs/scraper-py/overview.md](./docs/scraper-py/overview.md) |
| Scraper HTTP endpoints | [docs/scraper-py/api.md](./docs/scraper-py/api.md) |
| Queue / job state machine / worker | [docs/scraper-py/queue-and-worker.md](./docs/scraper-py/queue-and-worker.md) |
| Browser / login / Cloudflare / capture | [docs/scraper-py/browser.md](./docs/scraper-py/browser.md) |
| Scraper settings / output writer | [docs/scraper-py/configuration.md](./docs/scraper-py/configuration.md) |
| Decoder (any part) | [docs/decoder-rs/overview.md](./docs/decoder-rs/overview.md) |
| The XTZ format / cipher | [docs/decoder-rs/xtz-format-and-cipher.md](./docs/decoder-rs/xtz-format-and-cipher.md) |
| The GP6 `.gpx` / BCFZ format | [docs/decoder-rs/gpx-bcfz-format.md](./docs/decoder-rs/gpx-bcfz-format.md) |
| Decoder discovery/decode/write flow | [docs/decoder-rs/pipeline.md](./docs/decoder-rs/pipeline.md) |
| Enricher (any part) | [docs/enricher-py/overview.md](./docs/enricher-py/overview.md) |
| Aligner (any part) | [docs/aligner-py/overview.md](./docs/aligner-py/overview.md) |
| Running/driving the scraper from the CLI | [docs/scripts.md](./docs/scripts.md) |

## Common commands

**scraper-py** (Python ≥ 3.13):

```bash
cd scraper-py
pip install -e ".[dev]"          # deps + dev tools
python -m camoufox fetch         # one-time: download the browser
python -m pytest                 # unit tests (browser EXCLUDED by default)
python -m pytest -m integration  # live browser test; needs UG creds + network
uvicorn app.main:app             # run the service (localhost:8000)
```

**decoder-rs** (Rust, edition 2021):

```bash
cd decoder-rs
cargo build --release
cargo test                       # see fixture caveat below
./target/release/decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

**enricher-py** (Python ≥ 3.13; requires `ffmpeg` + `yt-dlp` on PATH):

```bash
cd enricher-py
pip install -e ".[dev]"          # installs the enricher CLI
enricher scan                    # enqueue tabs that need audio
enricher run [--jobs N] [--limit N] [--retry-failed] [--output-dir DIR] [--db PATH] [--quiet]
enricher status                  # counts by state
python3 -m pytest                # unit tests (network-free by default)
python3 -m pytest -m integration # live yt-dlp + ffprobe tests
```

**aligner-py** (Python ≥ 3.13; requires `mscore` + `fluidsynth` + `ffmpeg` on PATH):

```bash
cd aligner-py
pip install -e ".[dev]"          # installs the align CLI
align run <tab_id> [<tab_id> …]  # render → align → write align.json
align inspect <tab_id>           # build align_overlay.wav + align_plot.png
align status [<tab_id> …]        # counts by align.json status
python3 -m pytest                # unit tests (tool-free by default)
python3 -m pytest -m integration # end-to-end; needs mscore + fluidsynth + a fixture
```

## Conventions & invariants (don't break these)

- **The cipher is bit-exact and must stay one.** `decoder-rs/src/cipher.rs` is
  verified byte-for-byte against UG's `xtzmain.wasm` via the committed golden
  fixture (`decoder-rs/tests/fixtures/sample.{xtz,gp}`). Do not "simplify" the
  LFSR zero-seed guard or the Bernstein-layout ChaCha8 — they preserve parity.
  All cipher tests must stay green. See
  [the cipher doc](./docs/decoder-rs/xtz-format-and-cipher.md).
- **`repo.py` is the only place that issues SQL** and owns the job state machine.
  Add new queries/transitions there, not inline elsewhere.
- **Only `worker.py` drives the browser**, through the `BrowserSession` Protocol.
  Keep the API browser-free so it stays testable with a fake.
- **The scraper never decrypts; the decoder never scrapes.** Keep that split.
- **Output is committed atomically** with `metadata.json` written last as the
  commit marker. Consumers must gate on `metadata.json` existing.
- **Tests are deterministic and browser-free by default.** Real-browser behavior
  is gated behind the `integration` marker. Use the injectable clock
  (`repo.clock["t"]`) and a fake `BrowserSession` for new tests.
- Follow the user's global rules: don't add comments that restate the code; never
  push to a remote or delete files you didn't create without asking.

## Gotchas

- The decoder's golden tests depend on the vendored fixtures in
  `decoder-rs/tests/fixtures/` (`sample.xtz` + `sample.gp`). They are committed and
  self-contained — don't delete them, or the cipher's byte-for-byte test breaks.
- The scraper's browser layer depends on UG's live markup and Cloudflare and is
  the most brittle part — start at [browser.md](./docs/scraper-py/browser.md)
  (especially the hardcoded `PROFILE_HREF` and the capture/CF heuristics) when a
  scrape mysteriously fails.

## Keeping documentation up to date

**The docs under `docs/`, plus `OVERVIEW.md`, are part of the codebase. Keep them
current in the same change that alters behavior — do not defer doc updates.**

When you make a change, check whether it touches anything a doc page describes and
update that page. Use this map of code → doc:

| If you change… | Update… |
|---|---|
| The filesystem layout, `metadata.json`, or `.xtz`/`.gp`/`.gpif` files (either scraper/decoder project) | `docs/output-contract.md` **and** both projects' pages |
| The `audio.<ext>` / `audio.json` contract (enricher) | `docs/output-contract.md` **and** `docs/enricher-py/overview.md` |
| The `align.json` contract (aligner) | `docs/output-contract.md` **and** `docs/aligner-py/overview.md` |
| The four-project relationship / data flow | `docs/architecture.md` |
| `scraper-py/app/api/routes.py` or `models.py` (endpoints/shapes) | `docs/scraper-py/api.md` |
| `scraper-py/app/repo.py`, `worker.py`, `db.py`, `errors.py`, `normalize.py` | `docs/scraper-py/queue-and-worker.md` |
| `scraper-py/app/browser/*` | `docs/scraper-py/browser.md` |
| `scraper-py/app/browser/discover.py`, `app/discovery/*` | `docs/scraper-py/discovery.md` |
| `scraper-py/app/config.py`, `.env.example`, `app/output.py` | `docs/scraper-py/configuration.md` |
| `decoder-rs/src/cipher.rs` or the XTZ format | `docs/decoder-rs/xtz-format-and-cipher.md` |
| `decoder-rs/src/gpx.rs` or the GP6 `.gpx`/BCFZ format | `docs/decoder-rs/gpx-bcfz-format.md` |
| `decoder-rs/src/discover.rs`, `output.rs`, `lib.rs`, `main.rs` (flow/CLI) | `docs/decoder-rs/pipeline.md` |
| `enricher-py/app/` (any module), CLI flags, config keys | `docs/enricher-py/overview.md` (+ this file's commands) |
| `aligner-py/app/` (any module), CLI flags, config keys | `docs/aligner-py/overview.md` (+ this file's commands) |
| Crate/module layout, deps, build/test commands (any project) | the relevant `overview.md` (+ this file's commands) |
| `scripts/*.sh` (operator wrappers) | `docs/scripts.md` |

Also:

- **New component or doc page?** Add it to the tables in `OVERVIEW.md` (the map)
  and, if relevant, to the table above — every doc must be reachable from the map.
- **Adding/removing a config key?** Update `.env.example`, `config.py`, and
  `docs/scraper-py/configuration.md` together.
- **Adding/removing a CLI flag?** Update `main.rs`, the decoder `overview.md`, and
  `pipeline.md`.
- Keep doc cross-links valid. If you move or rename a page, fix the links in
  `OVERVIEW.md`, this file, and any sibling pages that point to it.
- Treat a code change whose docs weren't updated as **incomplete**.
