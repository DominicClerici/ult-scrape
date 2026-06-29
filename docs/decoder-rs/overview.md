# decoder-rs — Overview

> Part of the [documentation map](../../OVERVIEW.md) · System context:
> [architecture](../architecture.md) · Input it consumes:
> [output contract](../output-contract.md)

A one-shot **Rust CLI** that walks the scraper's `OUTPUT_DIR`, finds committed tab
directories holding un-decoded `.xtz` files, decrypts each into its native Guitar
Pro container, extracts `score.gpif` alongside it, and skips anything already
decoded. Two container families are handled, switched on the decrypted payload's
magic: **GP7/8** (`PK` ZIP → `.gp`) and **GP6** (`BCFZ` → `.gpx`); see
[GPX/BCFZ format](./gpx-bcfz-format.md). It **shares no code** with the scraper —
only the frozen [output contract](../output-contract.md). It never scrapes.

The decryption is verified **byte-for-byte** against a committed golden fixture
(and matches UG's `xtzmain.wasm`).

## Component docs

| Doc | Covers |
|---|---|
| [XTZ format & cipher](./xtz-format-and-cipher.md) | The `.xtz` binary format, the dual-LFSR key schedule, and the hand-rolled ChaCha8 (`src/cipher.rs`). |
| [GPX/BCFZ format](./gpx-bcfz-format.md) | The GP6 `.gpx` container: BCFZ bit-stream decompression + BCFS filesystem walk to extract `score.gpif` (`src/gpx.rs`). |
| [Pipeline](./pipeline.md) | discover → decode → classify → write, CLI flags, idempotency, parallelism, error handling (`src/discover.rs`, `src/output.rs`, `src/lib.rs`, `src/main.rs`). |

## Crate layout

```
decoder-rs/
  Cargo.toml              # edition 2021; deps: clap, walkdir, zip, rayon, anyhow
  src/
    cipher.rs             # LFSR key schedule + hand-rolled ChaCha8; decrypt_xtz(&[u8]) -> Result<Vec<u8>>. Pure, no I/O.
    gpx.rs                # GP6 path: BCFZ decompress + BCFS walk; extract_gpif(&[u8]) -> Result<Vec<u8>>. Pure, no I/O.
    discover.rs           # walkdir over OUTPUT_DIR; returns pending .xtz paths. Pure filesystem read.
    output.rs             # decode_container() (classify PK/BCFZ → extension + score.gpif) + atomic write_outputs().
    lib.rs                # run(Options) -> Summary: rayon parallel decode, per-file isolation, counts.
    main.rs               # clap CLI: parse args, resolve OUTPUT_DIR, call run(), print summary.
```

Each module has one purpose and a small interface: `cipher` and `gpx` are pure
transforms, `discover` is a pure filesystem read, `output` is classify+write,
`lib`/`main` is orchestration. There is both a `[lib]` (`decoder_rs`) and a
`[bin]` (`decoder-rs`) so the logic is testable as a library.

## Build & run

```bash
cargo build --release

decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

- `OUTPUT_DIR` — scan root. Defaults to `$OUTPUT_DIR`, then the repo-root
  `output/` directory (located by walking up from the current dir for the
  `scraper-py/` + `decoder-rs/` pair, so the default holds from any launch dir
  inside the repo), then `./output` outside the repo. Matches where the scraper
  writes.
- `--force` — re-decode even when a sibling `.gp`/`.gpx` exists.
- `--jobs N` — parallel decode threads (default: CPU count).
- `--quiet` — suppress per-file lines; still print the summary.

Per `<stem>.xtz` it writes the container (`<stem>.gp` for GP7, `<stem>.gpx` for
GP6) and `<stem>.gpif` in the same directory. Files that fail to decrypt or do not
yield a recognized Guitar Pro container are reported and skipped; the run still
**exits 0** and prints `decoded N | skipped N | failed N`. See
[pipeline](./pipeline.md) for the full flow and error handling.

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
  byte-equality with its reference-decoded `.gp` (the strongest correctness guarantee).
- **`gpx.rs` tests** — bit-reader vectors (MSB / reversed / zero-pad-past-EOF);
  the **golden GP6 test**: decrypt + extract the committed GP6 fixture and assert
  byte-equality with its reference-extracted `score.gpif`; well-formed-XML and
  bad-magic checks.
- **`discover.rs` tests** — eligible dir with pending xtz, already-decoded
  (sibling `.gp` **or** `.gpx`), ineligible dir (no `metadata.json`), `--force`
  re-inclusion.
- **`output.rs` tests** — `decode_container` classifies a GP7 ZIP (`.gp`) and a
  GP6 BCFZ (`.gpx`) and rejects unknown magic; valid GP ZIP yields `score.gpif`;
  `write_outputs` honors the container extension; no leftover temp files.
- **`lib.rs` run tests** — full `run()`: decode → idempotent skip → `--force`
  re-decode; corrupt `.xtz` counted as failed without writing output.

> **Golden-test fixtures** are vendored in `decoder-rs/tests/fixtures/`,
> referenced via `CARGO_MANIFEST_DIR`: the GP7 cipher pair
> (`sample.xtz` + reference-decoded `sample.gp`) and the GP6 pair
> (`sample_gp6.xtz` + reference-extracted `sample_gp6.gpif`). They are
> self-contained — `cargo test` needs no other directories. If you add a new
> format/version, add a matching fixture pair here.
