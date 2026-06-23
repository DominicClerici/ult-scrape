//! Bit-exact port of PY/xtz_decrypt.py: dual-LFSR key schedule + ChaCha8.

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
}
