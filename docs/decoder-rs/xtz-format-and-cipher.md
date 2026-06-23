# decoder-rs — XTZ Format & Cipher

> Part of the [documentation map](../../OVERVIEW.md) ·
> [decoder overview](./overview.md). Source: `src/cipher.rs`. This is a bit-exact
> port of `PY/xtz_decrypt.py`, itself documented as verified against UG's
> `xtzmain.wasm`.

`cipher.rs` is **pure** (no I/O) and exhaustively unit-tested. Its public entry
point is:

```rust
pub fn decrypt_xtz(data: &[u8]) -> anyhow::Result<Vec<u8>>
```

It returns the decrypted Guitar Pro `.gp` (a ZIP) bytes, or errors on bad magic /
short input.

## The `.xtz` container (v1)

A 20-byte header followed by a ChaCha8 stream-XOR payload:

| Bytes | Meaning |
|---|---|
| 0–3   | magic `XTZ\0` |
| 4–11  | 8-byte ChaCha8 nonce (Bernstein layout) |
| 12–15 | uint32 LE `c` — low 5 bits select the second-half LFSR length/taps |
| 16–19 | uint32 LE `z` — second-half LFSR seed |
| 20+   | encrypted payload (decrypts to the `.gp` ZIP) |

`decrypt_xtz` rejects input shorter than 21 bytes or with the wrong magic.

## Key schedule (32-byte ChaCha8 key = `first16 || second16`)

The key is built from two Galois LFSRs. A Galois LFSR here is advanced 8 steps per
output byte, emitting bits **MSB-first** (`lfsr_byte`); tap positions come from a
baked 32-row table `LFSR_TAPS_BY_INDEX` (lengths 33..64), converted to a bitmask
by `taps_bitmap`.

### `first16` — `derive_first_half()`

`first16` = `COPYRIGHT_PREFIX[..16]` XOR the first 16 bytes of a 33-bit LFSR
(init `0xDEADBEAF`, taps row 0), where `COPYRIGHT_PREFIX = "Copyright (c) 2020 WSM Group"`.

> The C++/WASM reference skips NUL source bytes while XORing; the prefix's first
> 16 bytes contain no NULs so the skip never fires — but it is **replicated
> faithfully** for parity.

### `second16` — `derive_second_half(c, z)`

`second16` = first 16 bytes of an `L = (c & 31) + 33`-bit LFSR, taps from row
`c & 31`, seeded with `z` (when `z > 1`, else `1`).

> **The zero-seed edge case — proven a non-issue (preserved for parity).** The
> Python guard tests raw `z > 1` while the seed is implicitly masked to
> `z & ((1<<L)-1)`. Since `L >= 33` exceeds the 32-bit width of `z`, the mask
> never truncates `z`, so the masked seed is 0 only when `z == 0` — which the
> `z > 1` guard maps to `1`. The LFSR therefore never seeds to 0. The code keeps
> the exact Python behavior, with a comment recording this proof. Do not
> "simplify" the guard — it would break bit-parity if a future input ever hit the
> boundary.

## ChaCha8 — `chacha8_xor()`

8 rounds (4 double-rounds), **Bernstein layout**: a 64-bit counter (starts at 0) +
64-bit nonce — **not** the IETF 96-bit-nonce / 32-bit-counter layout. This is why
the cipher is hand-rolled rather than using the `chacha20` crate: the stock crate
would lay out the state words differently and produce the wrong keystream.

- State words: `"expand 32-byte k"` constants, the 32-byte key, then
  `counter_lo, counter_hi, nonce_lo, nonce_hi`.
- `quarter()` is the standard ChaCha quarter-round; the payload is processed in
  64-byte blocks, XORing each with the keystream and incrementing the counter.

## Why it's a faithful port (and must stay one)

The only correctness guarantee that matters is **byte-equality with the verified
Python decoder**. The `decrypts_fixture_byte_for_byte` test asserts exactly that
against the committed fixture (and that the result starts with the ZIP magic
`PK\x03\x04`). Reference key-schedule vectors are pinned in the unit tests:

- `lfsr_bytes(0xDEADBEAF, taps0, 8)` → `f57db5628b986896`
- `first16` → `b612c51bf9f10ffe95ba22d587e63ebd`
- `second16(c=3051246439, z=3274506942)` → `7d40b4c30beb58c68a976f0a4b25b706`

**Any change to this file must keep all cipher tests green.** Treat the algorithm
as frozen to the WASM build it mirrors. A different container version (different
magic, or different taps from a new WASM build) decrypts to non-ZIP and is
correctly rejected downstream (see [pipeline](./pipeline.md#error-handling)) —
never crashing or emitting corrupt output.
