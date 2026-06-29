//! GP6 (Guitar Pro 6 `.gpx`) container support — pure, no I/O.
//!
//! A decrypted GP6 payload is a `BCFZ` blob: a bit-stream-compressed `BCFS`
//! sector filesystem. This module decompresses it and pulls `score.gpif` out,
//! the GP6 analogue of `Content/score.gpif` inside a GP7 ZIP (see `output.rs`).
//!
//! The format is a faithful port of the documented GPX layout, cross-checked
//! against the `Antti/rust-gpx-reader` reference and verified to extract a valid
//! `score.gpif` from every real GP6 tab in the corpus.

use anyhow::{bail, Result};

const SECTOR_SIZE: usize = 0x1000;
const GPIF_NAME: &str = "score.gpif";

/// MSB-first bit reader over a byte slice. Reads past the end yield zero bits —
/// a BCFZ stream's final byte is frequently only partially present, and the
/// reference decoder reads it zero-padded rather than erroring.
struct BitReader<'a> {
    data: &'a [u8],
    byte_pos: usize,
    bit_pos: u8, // 0..=7, counted from the MSB
}

impl<'a> BitReader<'a> {
    fn new(data: &'a [u8]) -> Self {
        BitReader { data, byte_pos: 0, bit_pos: 0 }
    }

    fn read_bit(&mut self) -> u8 {
        let bit = match self.data.get(self.byte_pos) {
            Some(&byte) => (byte >> (7 - self.bit_pos)) & 1,
            None => 0, // zero-pad past EOF
        };
        self.bit_pos += 1;
        if self.bit_pos == 8 {
            self.bit_pos = 0;
            self.byte_pos += 1;
        }
        bit
    }

    /// Read `count` bits MSB-first into an integer.
    fn read_bits(&mut self, count: u32) -> u64 {
        let mut word = 0u64;
        for _ in 0..count {
            word = (word << 1) | self.read_bit() as u64;
        }
        word
    }

    /// Read `count` bits, each placed at a successively higher bit (LSB-first).
    fn read_bits_reversed(&mut self, count: u32) -> u64 {
        let mut word = 0u64;
        for i in 0..count {
            word |= (self.read_bit() as u64) << i;
        }
        word
    }

    fn read_u8(&mut self) -> u8 {
        self.read_bits(8) as u8
    }
}

/// Decompress a BCFZ stream — `data` is the bytes **after** the 4-byte `BCFZ`
/// magic. Layout: a little-endian u32 uncompressed length, then a bit stream of
/// uncompressed runs (flag 0) and back-references (flag 1).
fn decompress_bcfz(data: &[u8]) -> Result<Vec<u8>> {
    if data.len() < 4 {
        bail!("BCFZ stream too short");
    }
    let expected = u32::from_le_bytes([data[0], data[1], data[2], data[3]]) as usize;
    let mut reader = BitReader::new(data);
    reader.byte_pos = 4; // the length is byte-aligned; the bit stream follows it

    let mut out: Vec<u8> = Vec::with_capacity(expected);
    while out.len() < expected {
        // Corruption guard: a valid stream produces `expected` bytes before
        // running this far past its input; without it, a truncated/garbage
        // stream of zero-padded bits would loop forever making no progress.
        if reader.byte_pos > data.len() + 16 {
            bail!("BCFZ stream truncated or corrupt");
        }
        if reader.read_bit() == 0 {
            // Uncompressed run: a 2-bit count, then that many raw bytes.
            let count = reader.read_bits_reversed(2);
            for _ in 0..count {
                out.push(reader.read_u8());
            }
        } else {
            // Back-reference: a 4-bit word size, then offset + length in it.
            let word_size = reader.read_bits(4) as u32;
            let offset = reader.read_bits_reversed(word_size) as usize;
            let length = reader.read_bits_reversed(word_size) as usize;
            if offset == 0 || offset > out.len() {
                bail!("BCFZ back-reference offset out of range");
            }
            let src = out.len() - offset;
            let to_read = length.min(offset);
            out.extend_from_within(src..src + to_read);
        }
    }
    Ok(out)
}

fn read_i32_le(data: &[u8], off: usize) -> Option<i32> {
    let b = data.get(off..off + 4)?;
    Some(i32::from_le_bytes([b[0], b[1], b[2], b[3]]))
}

fn read_entry_name(data: &[u8], off: usize) -> String {
    let end = (off + 127).min(data.len());
    let raw = data.get(off..end).unwrap_or(&[]);
    let nul = raw.iter().position(|&b| b == 0).unwrap_or(raw.len());
    String::from_utf8_lossy(&raw[..nul]).into_owned()
}

/// Walk a BCFS image — `data` is the bytes **after** the 4-byte `BCFS` magic —
/// and return the contents of the file named `name`, if present.
///
/// The image is a list of `SECTOR_SIZE` sectors. A directory entry sits at a
/// sector boundary when its first i32 is `2`; the file's bytes live in the
/// sectors named by a zero-terminated index list at `+0x94`, sized by `+0x8C`.
fn extract_file(data: &[u8], name: &str) -> Option<Vec<u8>> {
    let mut offset = 0usize;
    loop {
        offset += SECTOR_SIZE;
        if offset + 3 >= data.len() {
            return None;
        }
        if read_i32_le(data, offset) != Some(2) {
            continue;
        }
        let name_off = offset + 4;
        let size_off = offset + 0x8C;
        let block_off = offset + 0x94;

        // Assemble the file from its sector chain (full sectors, then trim to
        // the declared size). Advancing `offset` to the last data sector skips
        // past the file's payload so it is never re-scanned as a directory.
        let mut file_data: Vec<u8> = Vec::new();
        let mut i = 0usize;
        while let Some(block) = read_i32_le(data, block_off + 4 * i) {
            if block == 0 {
                break;
            }
            let pos = (block as usize) * SECTOR_SIZE;
            if pos >= data.len() {
                break;
            }
            let end = (pos + SECTOR_SIZE).min(data.len());
            file_data.extend_from_slice(&data[pos..end]);
            offset = pos;
            i += 1;
        }

        let size = match read_i32_le(data, size_off) {
            Some(s) if s >= 0 => s as usize,
            _ => continue,
        };
        if size > file_data.len() {
            continue;
        }
        if read_entry_name(data, name_off) == name {
            return Some(file_data[..size].to_vec());
        }
    }
}

/// Extract `score.gpif` from a decrypted GP6 (`.gpx`/BCFZ) payload.
pub fn extract_gpif(gpx: &[u8]) -> Result<Vec<u8>> {
    if gpx.len() < 4 || &gpx[..4] != b"BCFZ" {
        bail!("not a BCFZ (GP6) container");
    }
    let bcfs = decompress_bcfz(&gpx[4..])?;
    if bcfs.len() < 4 || &bcfs[..4] != b"BCFS" {
        bail!("BCFZ did not decompress to a BCFS filesystem");
    }
    match extract_file(&bcfs[4..], GPIF_NAME) {
        Some(gpif) => Ok(gpif),
        None => bail!("BCFS filesystem has no {GPIF_NAME}"),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    // Bit-reader vectors (ported from Antti/rust-gpx-reader's bitbuffer tests).
    const SAMPLE: &[u8] = &[0b1100_1010, 0b1111_0000];

    #[test]
    fn reads_bits_msb() {
        assert_eq!(BitReader::new(SAMPLE).read_bits(8), 202);
    }

    #[test]
    fn reads_bits_msb_partial() {
        assert_eq!(BitReader::new(SAMPLE).read_bits(7), 101);
    }

    #[test]
    fn reads_bits_reversed_full_byte() {
        assert_eq!(BitReader::new(SAMPLE).read_bits_reversed(8), 83);
    }

    #[test]
    fn reads_bits_reversed_partial() {
        assert_eq!(BitReader::new(SAMPLE).read_bits_reversed(7), 83);
    }

    #[test]
    fn reads_individual_bits_in_order() {
        let mut r = BitReader::new(SAMPLE);
        let bits: Vec<u8> = (0..16).map(|_| r.read_bit()).collect();
        assert_eq!(bits, vec![1, 1, 0, 0, 1, 0, 1, 0, 1, 1, 1, 1, 0, 0, 0, 0]);
    }

    #[test]
    fn zero_pads_past_end_of_input() {
        let mut r = BitReader::new(&[0xFF]);
        assert_eq!(r.read_bits(8), 255);
        // Everything past the final byte reads as zero bits.
        assert_eq!(r.read_bits(8), 0);
        assert_eq!(r.read_bit(), 0);
    }

    fn gp6_xtz() -> Vec<u8> {
        std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample_gp6.xtz")).unwrap()
    }

    #[test]
    fn extract_gpif_matches_golden_fixture() {
        let gpx = crate::cipher::decrypt_xtz(&gp6_xtz()).unwrap();
        assert_eq!(&gpx[..4], b"BCFZ");
        let gpif = extract_gpif(&gpx).unwrap();
        let expected =
            std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample_gp6.gpif"))
                .unwrap();
        assert_eq!(gpif.len(), expected.len());
        assert_eq!(gpif, expected);
    }

    #[test]
    fn extracted_gpif_is_well_formed_gpif_xml() {
        let gpx = crate::cipher::decrypt_xtz(&gp6_xtz()).unwrap();
        let gpif = extract_gpif(&gpx).unwrap();
        assert!(gpif.starts_with(b"<?xml"));
        assert!(gpif.ends_with(b"</GPIF>\n"));
    }

    #[test]
    fn rejects_non_bcfz_payload() {
        assert!(extract_gpif(b"PK\x03\x04 definitely not bcfz").is_err());
    }

    #[test]
    fn rejects_too_short_payload() {
        assert!(extract_gpif(b"BCF").is_err());
    }
}
