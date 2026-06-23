# XTZ Decoder (`decoder-rs`) — Design

**Date:** 2026-06-23
**Status:** Approved design, pre-implementation
**Worktree/branch:** `worktree-decoder-rs`

## Purpose

A one-shot Rust CLI that walks the Python scraper's output tree, finds committed
tab directories holding un-decoded `.xtz` files, decrypts each into the real
Guitar Pro 7/8 `.gp` (a ZIP), extracts `Content/score.gpif` alongside it, and
skips anything already decoded. It is a faithful port of the decryption in
`PY/xtz_decrypt.py`, which is documented as verified bit-for-bit against UG's
`xtzmain.wasm`.

The scraper and decoder share **no code** — only the frozen filesystem output
contract. The scraper never decrypts; the decoder never scrapes.

## Input contract (frozen, owned by the scraper)

For each successfully scraped tab the scraper atomically commits a directory:

```
OUTPUT_DIR/<tab_id>/                 # <tab_id> contains a slash, e.g. eagles/hotel-california-official-1910943
  <name>.xtz                         # one or more raw encrypted blobs, magic "XTZ\0"
  metadata.json                      # written LAST; its presence marks the dir complete
```

- The whole directory appears via a single atomic `rename`, so it is either
  fully present or absent — never half-written.
- On re-scrape the scraper does `rmtree(dir)` then re-commits, which also deletes
  any `.gp`/`.gpif` we wrote. This is the idempotency trigger (see below).
- `metadata.json` is the scraper's commit marker. We treat it **only** as an
  existence gate; we do not parse it.

## XTZ format (from `PY/xtz_decrypt.py`, abbreviated)

20-byte header + ChaCha8 stream-XOR payload:

| Bytes | Meaning |
|-------|---------|
| 0–3   | magic `XTZ\0` |
| 4–11  | 8-byte ChaCha8 nonce (Bernstein layout) |
| 12–15 | uint32 LE `c` — low 5 bits select the second-half LFSR length/taps |
| 16–19 | uint32 LE `z` — second-half LFSR seed |
| 20+   | encrypted payload (decrypts to the `.gp` ZIP) |

- ChaCha8 key (32 B) = `first16 || second16`.
  - `first16` = `"Copyright (c) 20"` XOR first 16 bytes of a 33-bit Galois LFSR
    (init `0xDEADBEAF`, taps index 0).
  - `second16` = first 16 bytes of an `L=(c&31)+33`-bit Galois LFSR, taps from
    the index table, seed `z & ((1<<L)-1)` (Python guard: `if z > 1 else 1`).
- ChaCha8 = 8 rounds (4 double-rounds), **Bernstein layout**: 64-bit counter
  (starts at 0) + 64-bit nonce. This is *not* the IETF 96-bit-nonce/32-bit-counter
  layout, so the stock `chacha20` crate would produce wrong keystream — we
  hand-roll the cipher to guarantee parity.

## Known caveats (carried from the Python reference)

1. **`second16` zero-seed edge case — investigated, proven a non-issue.** The
   Python guard tests raw `z > 1` while seeding from `z & mask`, which looked like
   a latent bug (seed 0 → all-zero key → garbage). On inspection it cannot happen:
   `L = (c & 31) + 33 ≥ 33`, strictly greater than the 32-bit width of `z`, so
   `mask = (1<<L)-1` covers every bit of `z` and `z & mask == z` for all uint32
   `z`. Thus the masked seed is 0 only when `z == 0`, which the `z > 1` guard maps
   to `1`. The LFSR never seeds to 0. We still **replicate the Python behavior
   exactly** for bit-parity, with a code comment recording this proof.
2. **Single format version.** Magic strictly `XTZ\0`, taps baked to one WASM
   build. A new container version decrypts to non-ZIP; we skip+warn, never crash
   or emit corrupt output.
3. **Validation is our responsibility.** The Python only warns on non-ZIP output.
   We validate (ZIP magic + `Content/score.gpif` present) **before** writing.
4. The `JSON_GP7.md` / `wasm_runner.py` GPIF-rebuild path is a superseded
   approach and is out of scope; direct decryption is lossless.

## Output scope

Per pending `<stem>.xtz`, on a validated decrypt, write into the same directory:

- `<stem>.gp` — the decrypted Guitar Pro ZIP, byte-for-byte the decrypted payload.
- `<stem>.gpif` — the `Content/score.gpif` XML extracted from that ZIP, for
  convenience.

Both are written atomically (temp file in the same dir + rename). Nothing is
written unless validation passes.

## Idempotency

A `<stem>.xtz` is **done** iff its sibling `<stem>.gp` exists. Re-runs skip done
files. `--force` re-decodes regardless. Because the scraper's re-scrape wipes the
whole dir (including our `.gp`), re-scraped tabs are naturally re-decoded — the
scheme is self-healing with no extra state files.

## Crate layout (`decoder-rs/`, edition 2021)

- `src/cipher.rs` — Galois LFSR byte generator, both key-half derivations,
  hand-rolled ChaCha8 (Bernstein layout), `decrypt_xtz(&[u8]) -> Result<Vec<u8>>`.
  Pure, no I/O, exhaustively unit-tested. Errors on bad magic / short input.
- `src/discover.rs` — `walkdir` over the output root. A directory is *eligible*
  iff it directly contains `metadata.json`; within an eligible dir, each `*.xtz`
  whose sibling `<stem>.gp` is absent (or `--force`) is *pending*. Returns the
  flat list of pending `.xtz` paths.
- `src/output.rs` — `validate_and_extract(blob) -> Result<gpif_bytes>` (ZIP magic
  + `Content/score.gpif` via the `zip` crate); `write_outputs(stem_dir, stem,
  gp_bytes, gpif_bytes)` writing both files atomically.
- `src/main.rs` — `clap` CLI; `rayon` parallel decode across the pending list
  (CPU-bound, embarrassingly parallel); per-file error isolation; final summary.

Each module has one purpose and a small interface: `cipher` is pure transform,
`discover` is pure filesystem read, `output` is validate+write, `main` is
orchestration.

## Data flow

```
discover(root) -> [pending .xtz paths]
  for each (parallel):
    bytes   = read(path)            # ENOENT (dir vanished mid-run) -> skip silently
    gp      = decrypt_xtz(bytes)    # bad magic/short -> warn, count failed
    gpif    = validate_and_extract(gp)  # non-ZIP / no score.gpif -> warn, count failed
    write_outputs(dir, stem, gp, gpif)  # atomic
  print summary: decoded / skipped / failed
```

## CLI

```
decoder-rs [OUTPUT_DIR] [--force] [--jobs N] [--quiet]
```

- `OUTPUT_DIR` positional; defaults to `$OUTPUT_DIR`, then `./output` (matches the
  scraper's default).
- `--force` — re-decode even when `<stem>.gp` exists.
- `--jobs N` — rayon thread cap (default: rayon's default = num CPUs).
- `--quiet` — suppress per-file lines; still print the summary.
- **Exit code:** always `0` on a completed run (even with per-file failures); the
  printed summary reports counts. (Chosen default; a `--strict` non-zero-on-failure
  flag can be added later if CI gating is wanted.)

## Error handling

Per-file `Result`, fully isolated — one bad file never aborts the batch.

| Condition | Behavior |
|-----------|----------|
| Bad magic / input < 21 bytes | warn, count failed, continue |
| Decrypts to non-ZIP / no `Content/score.gpif` | warn, count failed, continue (covers wrong version / edge-case key) |
| Directory/file vanished mid-run (`ENOENT`) | skip silently (scraper re-scrape race) |
| Write error (disk full, perms) | warn, count failed, continue |

## Dependencies

`clap` (CLI), `walkdir` (traversal), `zip` (validate + extract `score.gpif`),
`rayon` (parallel decode), `anyhow` (error plumbing). No `serde_json` — we never
parse `metadata.json`. The cipher is hand-rolled (Bernstein-layout parity), not a
crypto crate.

## Testing

1. **`cipher.rs` unit tests** — key-schedule vectors (first16, second16 for known
   `c`/`z`) generated from the Python reference; LFSR byte sequences; header
   parsing errors.
2. **Golden end-to-end test** — decrypt the committed fixture
   `PY/captures/20260506-135324-…/…1e895791e7ac.xtz` and assert byte-equality with
   its sibling `…1e895791e7ac.gp` produced by the verified Python. This is the
   strongest correctness guarantee.
3. **`discover.rs` tests** — temp tree covering: eligible dir with pending xtz,
   eligible dir already decoded (sibling `.gp`), ineligible dir (no
   `metadata.json`), `--force` re-inclusion.
4. **`output.rs` tests** — valid GP ZIP extracts `score.gpif`; non-ZIP and
   ZIP-without-score.gpif are rejected without writing.

## Out of scope

- Decoding `.gp`/GPIF into other formats (MIDI/JSON/ASCII tab).
- Watching/daemon mode (one-shot only; re-invoke or cron).
- Parsing/validating `metadata.json` contents or sha256 (existence gate only).
- Any change to the scraper or its output contract.
