# decoder-rs — GP6 `.gpx` / BCFZ format

> Part of the [documentation map](../../OVERVIEW.md) ·
> [decoder overview](./overview.md). Source: `src/gpx.rs`. The extractor is
> verified against a committed golden fixture
> (`tests/fixtures/sample_gp6.{xtz,gpif}`) and was cross-checked against the
> `Antti/rust-gpx-reader` reference and every real GP6 tab in the corpus.

`decrypt_xtz` returns the raw decrypted Guitar Pro container. Two container
families occur in the wild, distinguished by their first 4 bytes:

| Magic | Format | Container file | gpif source |
|---|---|---|---|
| `PK\x03\x04` | **Guitar Pro 7/8** | `.gp` (a ZIP) | `Content/score.gpif` inside the ZIP (`src/output.rs`) |
| `BCFZ` | **Guitar Pro 6** | `.gpx` (a BCFZ blob) | `score.gpif` inside the BCFS filesystem (`src/gpx.rs`) |

`output::decode_container` switches on this magic. This page documents the GP6
path; older GP3/4/5 (`FICHIER GUITAR PRO`) are **not** handled and are rejected
with a clear error.

`gpx.rs` is **pure** (no I/O). Its public entry point is:

```rust
pub fn extract_gpif(gpx: &[u8]) -> anyhow::Result<Vec<u8>>
```

It decompresses the `BCFZ` blob, walks the inner `BCFS` filesystem, and returns
the `score.gpif` bytes — or errors on bad magic, a truncated stream, or a missing
`score.gpif`.

## The container: `BCFZ` (compressed) wrapping `BCFS`

A decoded GP6 `.gpx` file is a bit-stream-compressed image of a sector
filesystem:

```
BCFZ  ── decompress ──▶  BCFS  ── walk directory ──▶  score.gpif (+ misc.xml, BinaryStylesheet, …)
```

Both layers carry a 4-byte ASCII magic (`BCFZ` / `BCFS`). The `.gpx` bytes we
write to disk are the **outer BCFZ blob** byte-for-byte (the decrypted payload) —
directly openable in Guitar Pro, exactly as `.gp` is for GP7.

## BCFZ decompression (`decompress_bcfz`)

Input is the bytes **after** the 4-byte `BCFZ` magic:

| Bytes | Meaning |
|---|---|
| 0–3 | uint32 LE — uncompressed length (loop target) |
| 4+  | bit stream (read **MSB-first**) |

The bit stream is a sequence of chunks, each introduced by a 1-bit flag:

- **flag 0 — uncompressed run:** a 2-bit reversed count `n`, then `n` raw bytes
  copied to the output.
- **flag 1 — back-reference:** a 4-bit word size `w`, then an `offset` and a
  `length`, each `w` bits read **reversed** (LSB-first). Copy
  `min(length, offset)` bytes from `output[len - offset ..]` onto the end (an
  LZ77-style self-reference; the read range never overlaps the bytes being
  appended).

The loop runs until the output reaches the declared length.

> **Zero-padding past EOF is load-bearing.** The final byte of a stream is often
> only partially present; the reference reads the missing bits as zero rather
> than erroring (in the corpus, 1 file in 3 needs this). `BitReader` returns `0`
> for any read past the end. A corruption guard bails if reads run more than 16
> bytes past the input, so a truncated/garbage stream can't loop forever.

## BCFS filesystem walk (`extract_file`)

Input is the decompressed image **after** the 4-byte `BCFS` magic. The image is a
list of `0x1000`-byte sectors. A **directory entry** sits at a sector boundary
when its first `int32 == 2`; the offsets below are relative to that boundary:

| Offset | Field |
|---|---|
| `+0x00` | entry type (`2` = file) |
| `+0x04` | file name (NUL-terminated, ≤127 bytes) |
| `+0x8C` | file size (uint32) |
| `+0x94` | block index list: consecutive `int32` sector numbers, **0-terminated** |

File bytes are assembled by concatenating each listed sector in full, then
**truncating to the declared size**. The walk advances past a file's data sectors
so they are never misread as directory entries (mirroring the reference). All
reads are bounds-checked — a malformed image yields an error, never a panic.

## Why a faithful port (and how it's verified)

There is no second WASM oracle here as there is for the cipher, so correctness is
pinned three ways:

1. **Golden fixture** — `sample_gp6.xtz` decrypts + extracts to a byte-for-byte
   match of the committed `sample_gp6.gpif` (`extract_gpif_matches_golden_fixture`).
2. **Bit-reader vectors** — ported from the reference's own unit tests
   (MSB / reversed / zero-pad-past-EOF).
3. **Structural validity** — the extracted `score.gpif` is well-formed GPIF XML
   (`<?xml …</GPIF>`), confirmed across all real GP6 tabs.

Keep all `gpx.rs` tests green; treat the format as frozen to the layout the
fixture encodes.
