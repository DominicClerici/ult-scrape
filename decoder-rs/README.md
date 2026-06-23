# decoder-rs

Decrypts Ultimate Guitar `.xtz` tab files (scraped by `scraper-py`) into real
Guitar Pro `.gp` files, and extracts `Content/score.gpif` alongside each.

It is a one-shot CLI: point it at the scraper's output root, and for every
committed `OUTPUT_DIR/<tab_id>/` directory (one containing `metadata.json`) it
decrypts each `*.xtz` whose `.gp` is not already present.

## Build

```bash
cargo build --release
```

## Usage

```bash
decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

- `OUTPUT_DIR` — scan root. Defaults to `$OUTPUT_DIR`, then `./output`.
- `--force` — re-decode even when a sibling `.gp` exists.
- `--jobs N` — parallel decode threads (default: CPU count).
- `--quiet` — suppress per-file lines; still print the summary.

For each `<stem>.xtz` it writes `<stem>.gp` (the Guitar Pro ZIP) and
`<stem>.gpif` (the extracted score XML) in the same directory. Files that fail to
decrypt or do not yield a valid GP ZIP are reported and skipped; the run still
exits 0.

## How it works

`.xtz` is a 20-byte header (`XTZ\0` magic, 8-byte nonce, two key-schedule
integers) followed by a ChaCha8 stream-XOR payload. The cipher is verified
byte-for-byte against UG's `xtzmain.wasm` via a committed golden fixture
(`tests/fixtures/sample.{xtz,gp}`).

## Test

```bash
cargo test
```
