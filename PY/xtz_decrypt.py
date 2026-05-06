"""Decrypt UG's XTZ-v1 tab file format → real Guitar Pro 7/8 (.gp) ZIP.

Reverse-engineered from xtzmain.wasm v3.4.5-stable.25056088002 (UG's MuseScore-based
Pro-tab player). Verified bit-for-bit against the WASM's own decryption output.

Format (20-byte header + ChaCha8 stream-encrypted payload):
    bytes 0-3    "XTZ\\0" magic
    bytes 4-11   8-byte ChaCha8 nonce
    bytes 12-15  uint32 'c': low 5 bits select LFSR length for second-half key
    bytes 16-19  uint32 'z': LFSR initial state for second-half key
    bytes 20+    encrypted payload

ChaCha8 key (32 bytes) = first16 || second16:
    first16  = "Copyright (c) 20" XOR first 16 bytes of a 33-bit Galois LFSR
               (init=0xDEADBEAF, taps polynomial x^33+x^32+x^29+x^27 → bitmap 0x194000000)
    second16 = first 16 bytes of an L-bit Galois LFSR where L = (c & 31) + 33,
               taps from the table below (indexed by c & 31), init = z & ((1<<L)-1) or 1.

Each LFSR byte = MSB-first concatenation of 8 successive output bits (Galois shift-right
with output bit fed back via the taps polynomial). Counter is 64-bit, starts at 0
(Bernstein-style ChaCha layout, not IETF).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

# Tap polynomials for LFSRs of length 33..64. The static C++ table at WASM offset
# 0x15F870 lists them with degree-1 indices (e.g. (58,57,53,52) → bits 57,56,52,51).
LFSR_TAPS_BY_INDEX = {
    0:  (33, 32, 29, 27),  1:  (34, 31, 30, 26),  2:  (35, 34, 28, 27),
    3:  (36, 35, 29, 28),  4:  (37, 36, 33, 31),  5:  (38, 37, 33, 32),
    6:  (39, 38, 35, 32),  7:  (40, 37, 36, 35),  8:  (41, 40, 39, 38),
    9:  (42, 40, 37, 35), 10:  (43, 42, 38, 37), 11:  (44, 42, 39, 38),
    12: (45, 44, 42, 41), 13:  (46, 40, 39, 38), 14:  (47, 46, 43, 42),
    15: (48, 44, 41, 39), 16:  (49, 45, 44, 43), 17:  (50, 48, 47, 46),
    18: (51, 50, 48, 45), 19:  (52, 51, 49, 46), 20:  (53, 52, 51, 47),
    21: (54, 51, 48, 46), 22:  (55, 54, 53, 49), 23:  (56, 54, 52, 49),
    24: (57, 55, 54, 52), 25:  (58, 57, 53, 52), 26:  (59, 57, 55, 52),
    27: (60, 58, 56, 55), 28:  (61, 60, 59, 56), 29:  (62, 59, 57, 56),
    30: (63, 62, 59, 58), 31:  (64, 63, 61, 60),
}

SIGMA = b"expand 32-byte k"
COPYRIGHT_PREFIX = b"Copyright (c) 2020 WSM Group"


def _taps_bitmap(taps: tuple[int, ...]) -> int:
    bm = 0
    for t in taps:
        if t:
            bm |= 1 << (t - 1)
    return bm


def _lfsr_byte(state: int, taps: int) -> tuple[int, int]:
    """Advance Galois LFSR 8 steps; emit one MSB-first byte (bit 7 = first step)."""
    byte = 0
    for i in range(8):
        out_bit = state & 1
        state = (state >> 1) ^ (taps if out_bit else 0)
        byte |= out_bit << (7 - i)
    return state, byte


def _lfsr_bytes(state: int, taps: int, n: int) -> bytes:
    out = bytearray()
    for _ in range(n):
        state, b = _lfsr_byte(state, taps)
        out.append(b)
    return bytes(out)


def _derive_first_half(n: int = 16) -> bytes:
    """16-byte buffer = COPYRIGHT_PREFIX[:16] XOR 33-bit-LFSR(init=0xDEADBEAF) for n bytes.
    The C++ skips zero source bytes, so we never advance past the null terminator."""
    state = 0xDEADBEAF
    taps = _taps_bitmap(LFSR_TAPS_BY_INDEX[0])  # (33, 32, 29, 27)
    out = bytearray()
    src_idx = 0
    for _ in range(n):
        state, kb = _lfsr_byte(state, taps)
        ch = COPYRIGHT_PREFIX[src_idx]
        out.append(ch ^ kb)
        if ch != 0:
            src_idx += 1
    return bytes(out)


def _derive_second_half(c: int, z: int, n: int = 16) -> bytes:
    idx = c & 0x1F
    L = idx + 33
    taps = _taps_bitmap(LFSR_TAPS_BY_INDEX[idx])
    mask = (1 << L) - 1
    state = (z & mask) if z > 1 else 1
    return _lfsr_bytes(state, taps, n)


def _chacha8_xor(key32: bytes, counter: int, nonce8: bytes, payload: bytes) -> bytes:
    """ChaCha8 (8 rounds = 4 double-rounds), Bernstein layout (64-bit counter + 64-bit nonce)."""
    def rol(x: int, k: int) -> int:
        x &= 0xFFFFFFFF
        return ((x << k) | (x >> (32 - k))) & 0xFFFFFFFF

    def quarter(s: list[int], a: int, b: int, c: int, d: int) -> None:
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = rol(s[d] ^ s[a], 16)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = rol(s[b] ^ s[c], 12)
        s[a] = (s[a] + s[b]) & 0xFFFFFFFF; s[d] = rol(s[d] ^ s[a],  8)
        s[c] = (s[c] + s[d]) & 0xFFFFFFFF; s[b] = rol(s[b] ^ s[c],  7)

    nonce_lo, nonce_hi = struct.unpack('<II', nonce8)
    out = bytearray(payload)
    pos = 0
    n = len(out)
    while pos < n:
        state = [
            *struct.unpack('<4I', SIGMA),
            *struct.unpack('<8I', key32),
            counter & 0xFFFFFFFF, (counter >> 32) & 0xFFFFFFFF,
            nonce_lo, nonce_hi,
        ]
        w = list(state)
        for _ in range(4):  # 4 double-rounds = 8 ChaCha rounds
            quarter(w, 0, 4,  8, 12); quarter(w, 1, 5,  9, 13)
            quarter(w, 2, 6, 10, 14); quarter(w, 3, 7, 11, 15)
            quarter(w, 0, 5, 10, 15); quarter(w, 1, 6, 11, 12)
            quarter(w, 2, 7,  8, 13); quarter(w, 3, 4,  9, 14)
        keystream = b''.join(struct.pack('<I', (w[i] + state[i]) & 0xFFFFFFFF) for i in range(16))
        take = min(64, n - pos)
        for i in range(take):
            out[pos + i] ^= keystream[i]
        pos += take
        counter += 1
    return bytes(out)


def decrypt_xtz(data: bytes) -> bytes:
    """Decrypt an XTZ-v1 blob and return the underlying Guitar Pro 7/8 (.gp) bytes."""
    if data[:4] != b"XTZ\x00":
        raise ValueError("not an XTZ-v1 file (bad magic)")
    if len(data) < 21:
        raise ValueError("file too short for XTZ-v1 header")
    nonce = data[4:12]
    c = struct.unpack('<I', data[12:16])[0]
    z = struct.unpack('<I', data[16:20])[0]
    payload = data[20:]
    key = _derive_first_half() + _derive_second_half(c, z)
    return _chacha8_xor(key, 0, nonce, payload)


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: python xtz_decrypt.py <input.gp> [output.gp]", file=sys.stderr)
        return 2
    in_path = Path(argv[1])
    out_path = Path(argv[2]) if len(argv) > 2 else in_path.with_suffix(".real.gp")
    plaintext = decrypt_xtz(in_path.read_bytes())
    if plaintext[:4] != b"PK\x03\x04":
        print(f"warning: decrypted bytes don't look like a ZIP (head {plaintext[:4].hex()})", file=sys.stderr)
    out_path.write_bytes(plaintext)
    print(f"wrote {len(plaintext)} bytes to {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
