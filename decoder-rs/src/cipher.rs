//! Bit-exact XTZ decryptor: dual-LFSR key schedule + ChaCha8, verified
//! byte-for-byte against UG's xtzmain.wasm via the committed golden fixture.

use anyhow::{bail, Result};

const SIGMA: &[u8; 16] = b"expand 32-byte k";

/// Tap polynomials for LFSRs of length 33..64, indexed by `c & 31`.
const LFSR_TAPS_BY_INDEX: [[u8; 4]; 32] = [
    [33, 32, 29, 27], [34, 31, 30, 26], [35, 34, 28, 27], [36, 35, 29, 28],
    [37, 36, 33, 31], [38, 37, 33, 32], [39, 38, 35, 32], [40, 37, 36, 35],
    [41, 40, 39, 38], [42, 40, 37, 35], [43, 42, 38, 37], [44, 42, 39, 38],
    [45, 44, 42, 41], [46, 40, 39, 38], [47, 46, 43, 42], [48, 44, 41, 39],
    [49, 45, 44, 43], [50, 48, 47, 46], [51, 50, 48, 45], [52, 51, 49, 46],
    [53, 52, 51, 47], [54, 51, 48, 46], [55, 54, 53, 49], [56, 54, 52, 49],
    [57, 55, 54, 52], [58, 57, 53, 52], [59, 57, 55, 52], [60, 58, 56, 55],
    [61, 60, 59, 56], [62, 59, 57, 56], [63, 62, 59, 58], [64, 63, 61, 60],
];

const COPYRIGHT_PREFIX: &[u8] = b"Copyright (c) 2020 WSM Group";

fn taps_bitmap(taps: [u8; 4]) -> u64 {
    let mut bm = 0u64;
    for t in taps {
        if t != 0 {
            bm |= 1u64 << (t - 1);
        }
    }
    bm
}

/// Advance a Galois LFSR 8 steps; emit one MSB-first byte (bit 7 = first step).
fn lfsr_byte(state: &mut u64, taps: u64) -> u8 {
    let mut byte = 0u8;
    for i in 0..8 {
        let out_bit = (*state & 1) as u8;
        *state = (*state >> 1) ^ if out_bit == 1 { taps } else { 0 };
        byte |= out_bit << (7 - i);
    }
    byte
}

fn lfsr_bytes(mut state: u64, taps: u64, n: usize) -> Vec<u8> {
    (0..n).map(|_| lfsr_byte(&mut state, taps)).collect()
}

/// first16 = COPYRIGHT_PREFIX[..16] XOR a 33-bit LFSR (init 0xDEADBEAF, taps index 0).
/// The C++ skips zero source bytes; the prefix's first 16 bytes contain no NULs, so
/// the skip never fires here — replicated for faithfulness.
fn derive_first_half() -> [u8; 16] {
    let mut state = 0xDEAD_BEAFu64;
    let taps = taps_bitmap(LFSR_TAPS_BY_INDEX[0]);
    let mut out = [0u8; 16];
    let mut src_idx = 0usize;
    for slot in out.iter_mut() {
        let kb = lfsr_byte(&mut state, taps);
        let ch = COPYRIGHT_PREFIX[src_idx];
        *slot = ch ^ kb;
        if ch != 0 {
            src_idx += 1;
        }
    }
    out
}

/// second16 = first 16 bytes of an L-bit LFSR, L = (c & 31) + 33, taps from the table.
/// Seed = `z` when `z > 1`, else 1. Proven safe (cannot seed to 0): L >= 33 exceeds the
/// 32-bit width of `z`, so the implicit `z & ((1<<L)-1)` mask never truncates `z`, and
/// `z & mask == 0` only when `z == 0`, which the guard maps to 1.
fn derive_second_half(c: u32, z: u32) -> [u8; 16] {
    let idx = (c & 0x1F) as usize;
    let taps = taps_bitmap(LFSR_TAPS_BY_INDEX[idx]);
    let state = if z > 1 { z as u64 } else { 1 };
    let v = lfsr_bytes(state, taps, 16);
    let mut out = [0u8; 16];
    out.copy_from_slice(&v);
    out
}

fn quarter(s: &mut [u32; 16], a: usize, b: usize, c: usize, d: usize) {
    s[a] = s[a].wrapping_add(s[b]); s[d] = (s[d] ^ s[a]).rotate_left(16);
    s[c] = s[c].wrapping_add(s[d]); s[b] = (s[b] ^ s[c]).rotate_left(12);
    s[a] = s[a].wrapping_add(s[b]); s[d] = (s[d] ^ s[a]).rotate_left(8);
    s[c] = s[c].wrapping_add(s[d]); s[b] = (s[b] ^ s[c]).rotate_left(7);
}

fn le32(b: &[u8]) -> u32 {
    u32::from_le_bytes([b[0], b[1], b[2], b[3]])
}

/// ChaCha8 (8 rounds = 4 double-rounds), Bernstein layout: 64-bit counter + 64-bit nonce.
fn chacha8_xor(key: &[u8; 32], mut counter: u64, nonce: &[u8; 8], payload: &mut [u8]) {
    let nonce_lo = le32(&nonce[0..4]);
    let nonce_hi = le32(&nonce[4..8]);
    for chunk in payload.chunks_mut(64) {
        let state: [u32; 16] = [
            le32(&SIGMA[0..4]), le32(&SIGMA[4..8]), le32(&SIGMA[8..12]), le32(&SIGMA[12..16]),
            le32(&key[0..4]), le32(&key[4..8]), le32(&key[8..12]), le32(&key[12..16]),
            le32(&key[16..20]), le32(&key[20..24]), le32(&key[24..28]), le32(&key[28..32]),
            counter as u32, (counter >> 32) as u32, nonce_lo, nonce_hi,
        ];
        let mut w = state;
        for _ in 0..4 {
            quarter(&mut w, 0, 4, 8, 12); quarter(&mut w, 1, 5, 9, 13);
            quarter(&mut w, 2, 6, 10, 14); quarter(&mut w, 3, 7, 11, 15);
            quarter(&mut w, 0, 5, 10, 15); quarter(&mut w, 1, 6, 11, 12);
            quarter(&mut w, 2, 7, 8, 13); quarter(&mut w, 3, 4, 9, 14);
        }
        let mut keystream = [0u8; 64];
        for i in 0..16 {
            keystream[i * 4..i * 4 + 4]
                .copy_from_slice(&w[i].wrapping_add(state[i]).to_le_bytes());
        }
        for (b, k) in chunk.iter_mut().zip(keystream.iter()) {
            *b ^= *k;
        }
        counter = counter.wrapping_add(1);
    }
}

/// Decrypt an XTZ-v1 blob; returns the underlying Guitar Pro (.gp ZIP) bytes.
pub fn decrypt_xtz(data: &[u8]) -> Result<Vec<u8>> {
    if data.len() < 21 {
        bail!("file too short for XTZ-v1 header ({} bytes)", data.len());
    }
    if &data[0..4] != b"XTZ\x00" {
        bail!("not an XTZ-v1 file (bad magic)");
    }
    let nonce: [u8; 8] = data[4..12].try_into().unwrap();
    let c = le32(&data[12..16]);
    let z = le32(&data[16..20]);

    let mut key = [0u8; 32];
    key[..16].copy_from_slice(&derive_first_half());
    key[16..].copy_from_slice(&derive_second_half(c, z));

    let mut payload = data[20..].to_vec();
    chacha8_xor(&key, 0, &nonce, &mut payload);
    Ok(payload)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn hex(bytes: &[u8]) -> String {
        bytes.iter().map(|b| format!("{b:02x}")).collect()
    }

    #[test]
    fn lfsr_index0_matches_reference() {
        let taps = taps_bitmap(LFSR_TAPS_BY_INDEX[0]);
        assert_eq!(hex(&lfsr_bytes(0xDEAD_BEAF, taps, 8)), "f57db5628b986896");
    }

    #[test]
    fn first_half_matches_reference() {
        assert_eq!(hex(&derive_first_half()), "b612c51bf9f10ffe95ba22d587e63ebd");
    }

    #[test]
    fn second_half_matches_reference() {
        // Fixture header: c = 3051246439, z = 3274506942.
        assert_eq!(
            hex(&derive_second_half(3_051_246_439, 3_274_506_942)),
            "7d40b4c30beb58c68a976f0a4b25b706"
        );
    }

    fn fixture_xtz() -> Vec<u8> {
        std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample.xtz"))
            .expect("fixture .xtz present")
    }

    fn fixture_gp() -> Vec<u8> {
        std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample.gp"))
            .expect("fixture .gp present")
    }

    #[test]
    fn decrypts_fixture_byte_for_byte() {
        let out = decrypt_xtz(&fixture_xtz()).unwrap();
        assert_eq!(out, fixture_gp(), "must match the Python-decoded .gp exactly");
        assert_eq!(&out[..4], b"PK\x03\x04");
    }

    #[test]
    fn rejects_bad_magic() {
        let mut data = fixture_xtz();
        data[0] = b'Z';
        assert!(decrypt_xtz(&data).is_err());
    }

    #[test]
    fn rejects_short_input() {
        assert!(decrypt_xtz(b"XTZ\x00short").is_err());
    }
}
