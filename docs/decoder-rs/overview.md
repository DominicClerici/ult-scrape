# decoder-rs — Overview

> Part of the [documentation map](../../OVERVIEW.md) · System context:
> [architecture](../architecture.md) · Input it consumes:
> [output contract](../output-contract.md)

A one-shot **Rust CLI** that walks the scraper's `OUTPUT_DIR`, finds committed tab
directories holding un-decoded `.xtz` files, decrypts each into a real Guitar
Pro 7/8 `.gp` (a ZIP), extracts `Content/score.gpif` alongside it, and skips
anything already decoded. It **shares no code** with the scraper — only the frozen
[output contract](../output-contract.md). It never scrapes.

The decryption is a **bit-exact port** of `PY/xtz_decrypt.py`, verified
byte-for-byte against a known fixture.

## Component docs

| Doc | Covers |
|---|---|
| [XTZ format & cipher](./xtz-format-and-cipher.md) | The `.xtz` binary format, the dual-LFSR key schedule, and the hand-rolled ChaCha8 (`src/cipher.rs`). |
| [Pipeline](./pipeline.md) | discover → decode → validate → write, CLI flags, idempotency, parallelism, error handling (`src/discover.rs`, `src/output.rs`, `src/lib.rs`, `src/main.rs`). |

## Crate layout

```
decoder-rs/
  Cargo.toml              # edition 2021; deps: clap, walkdir, zip, rayon, anyhow
  src/
    cipher.rs             # LFSR key schedule + hand-rolled ChaCha8; decrypt_xtz(&[u8]) -> Result<Vec<u8>>. Pure, no I/O.
    discover.rs           # walkdir over OUTPUT_DIR; returns pending .xtz paths. Pure filesystem read.
    output.rs             # extract_score_gpif() (validate ZIP + pull score.gpif) + atomic write_outputs().
    lib.rs                # run(Options) -> Summary: rayon parallel decode, per-file isolation, counts.
    main.rs               # clap CLI: parse args, resolve OUTPUT_DIR, call run(), print summary.
```

Each module has one purpose and a small interface: `cipher` is a pure transform,
`discover` is a pure filesystem read, `output` is validate+write, `lib`/`main` is
orchestration. There is both a `[lib]` (`decoder_rs`) and a `[bin]`
(`decoder-rs`) so the logic is testable as a library.

## Build & run

```bash
cargo build --release

decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

- `OUTPUT_DIR` — scan root. Defaults to `$OUTPUT_DIR`, then `./output` (matches
  the scraper's default).
- `--force` — re-decode even when a sibling `.gp` exists.
- `--jobs N` — parallel decode threads (default: CPU count).
- `--quiet` — suppress per-file lines; still print the summary.

Per `<stem>.xtz` it writes `<stem>.gp` and `<stem>.gpif` in the same directory.
Files that fail to decrypt or do not yield a valid GP ZIP are reported and
skipped; the run still **exits 0** and prints `decoded N | skipped N | failed N`.
See [pipeline](./pipeline.md) for the full flow and error handling.

## Dependencies

`clap` (CLI), `walkdir` (traversal), `zip` (validate + extract `score.gpif`),
`rayon` (parallel decode), `anyhow` (error plumbing). **No `serde_json`** — the
decoder never parses `metadata.json` (existence gate only). The cipher is
hand-rolled (Bernstein-layout ChaCha8 parity), **not** a crypto crate — the stock
`chacha20` crate uses the IETF nonce/counter layout and would produce the wrong
keystream.

## Testing

```bash
cargo test
```

- **`cipher.rs` unit tests** — key-schedule vectors (LFSR bytes, `first16`,
  `second16` for the fixture's `c`/`z`), header-parsing errors, and the
  **golden end-to-end test**: decrypt the committed fixture and assert
  byte-equality with its Python-decoded `.gp` (the strongest correctness guarantee).
- **`discover.rs` tests** — eligible dir with pending xtz, already-decoded
  (sibling `.gp`), ineligible dir (no `metadata.json`), `--force` re-inclusion.
- **`output.rs` tests** — valid GP ZIP yields `score.gpif`; non-ZIP and
  ZIP-without-`score.gpif` are rejected without writing; no leftover temp files.
- **`lib.rs` run tests** — full `run()`: decode → idempotent skip → `--force`
  re-decode; corrupt `.xtz` counted as failed without writing output.

> ⚠️ **Golden-test fixture dependency.** Several tests read the committed fixture at
> `decoder-rs/../PY/captures/20260506-135324-eagles-hotel-california-official-1910943/002-tab-download-ssid-1910943-1e895791e7ac.{xtz,gp}`.
> The path is baked in via `CARGO_MANIFEST_DIR`. If the `PY/` directory is absent
> from the working tree (it is git-tracked but may be deleted locally), these
> tests fail with "fixture .xtz present". Restore `PY/` (`git checkout PY/`) before
> running `cargo test`, or these golden checks won't run.
