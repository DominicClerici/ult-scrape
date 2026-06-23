# XTZ Decoder (`decoder-rs`) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot Rust CLI (`decoder-rs`) that walks the Python scraper's output tree, decrypts each un-decoded `.xtz` into the real Guitar Pro `.gp`, and extracts `Content/score.gpif` alongside it.

**Architecture:** A small crate with four focused modules: `cipher` (pure XTZ→GP decryption, a bit-exact port of `PY/xtz_decrypt.py`), `discover` (filesystem scan for pending files), `output` (validate GP ZIP + extract score.gpif + atomic write), and `lib::run` orchestration driven by a `clap` CLI in `main`. Decryption runs in parallel across files via `rayon`. The decoder shares no code with the scraper — only its frozen output contract (`OUTPUT_DIR/<tab_id>/` with `*.xtz` + `metadata.json` commit marker).

**Tech Stack:** Rust (edition 2021), `clap` 4, `walkdir` 2, `zip` 2, `rayon` 1, `anyhow` 1. Cipher is hand-rolled (ChaCha8 Bernstein layout — *not* the IETF layout a stock crate gives).

## Global Constraints

- Crate lives at repo-root `decoder-rs/`, edition 2021.
- Toolchain is at `~/.cargo/bin` (not on `PATH`). Prefix cargo invocations: `PATH="$HOME/.cargo/bin:$PATH" cargo ...`.
- Pinned deps (exact): `clap = { version = "4.6", features = ["derive"] }`, `walkdir = "2.5"`, `zip = "2.4"`, `rayon = "1.12"`, `anyhow = "1.0"`. No `serde_json` — `metadata.json` is an existence gate only, never parsed.
- The cipher must be byte-for-byte faithful to `PY/xtz_decrypt.py`. Replicate its `z > 1 ? z : 1` seed guard verbatim (proven safe: `L ≥ 33 > 32`-bit width of `z`, so the LFSR never seeds to 0 — record this in a comment).
- Magic is `XTZ\0`; header is 20 bytes; payload is ChaCha8 stream-XOR; counter starts at 0; nonce/counter are 64-bit (Bernstein), little-endian words.
- Output written only after validation passes; writes are atomic (temp file in the same dir + rename). Per-file errors are isolated and never abort the batch. Process exits 0 on a completed run.
- Idempotency: a `<stem>.xtz` is done iff sibling `<stem>.gp` exists; `--force` overrides.
- Golden fixture (committed, byte-verified against the Python): `PY/captures/20260506-135324-eagles-hotel-california-official-1910943/002-tab-download-ssid-1910943-1e895791e7ac.xtz` and its sibling `.gp`. Reference it from tests via `concat!(env!("CARGO_MANIFEST_DIR"), "/../PY/captures/...")`.

**Golden cipher vectors** (generated from the Python reference; use verbatim in tests):
- Fixture header: `nonce = 3cc4e61300210c84`, `c = 3051246439`, `z = 3274506942` (→ `idx = 7`, `L = 40`).
- `derive_first_half()` = `b612c51bf9f10ffe95ba22d587e63ebd`
- `derive_second_half(3051246439, 3274506942)` = `7d40b4c30beb58c68a976f0a4b25b706`
- Raw Galois LFSR, taps index 0, init `0xDEADBEAF`, first 8 bytes = `f57db5628b986896`
- Decrypted payload == sibling `.gp`, both 89286 bytes, head `504b030414000000` (`PK\x03\x04`).
- `score.gpif`: 1845208 bytes, head `<?xml version="1.0" encoding="utf-8"?>\n<GPIF>`.

---

### Task 1: Crate scaffold

**Files:**
- Create: `decoder-rs/Cargo.toml`
- Create: `decoder-rs/src/main.rs`
- Create: `decoder-rs/.gitignore`

**Interfaces:**
- Produces: a buildable binary crate `decoder-rs` with all deps resolved. No public API yet.

- [ ] **Step 1: Create `decoder-rs/Cargo.toml`**

```toml
[package]
name = "decoder-rs"
version = "0.1.0"
edition = "2021"

[dependencies]
clap = { version = "4.6", features = ["derive"] }
walkdir = "2.5"
zip = "2.4"
rayon = "1.12"
anyhow = "1.0"

[[bin]]
name = "decoder-rs"
path = "src/main.rs"

[lib]
name = "decoder_rs"
path = "src/lib.rs"
```

- [ ] **Step 2: Create `decoder-rs/.gitignore`**

```
/target
```

- [ ] **Step 3: Create a placeholder `decoder-rs/src/lib.rs`**

```rust
//! XTZ → Guitar Pro decoder library.
```

- [ ] **Step 4: Create `decoder-rs/src/main.rs`**

```rust
fn main() {
    println!("decoder-rs");
}
```

- [ ] **Step 5: Build to resolve deps and verify it compiles**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo build`
Expected: compiles; `Cargo.lock` created; `zip` resolves to `2.4.x`.

- [ ] **Step 6: Commit**

```bash
cd decoder-rs && git add Cargo.toml Cargo.lock .gitignore src/lib.rs src/main.rs
git commit -m "feat(decoder-rs): crate scaffold"
```

---

### Task 2: Cipher — LFSR + key-half derivation

**Files:**
- Create: `decoder-rs/src/cipher.rs`
- Modify: `decoder-rs/src/lib.rs`

**Interfaces:**
- Produces (module-private, exercised by in-file tests this task):
  `fn taps_bitmap(taps: [u8; 4]) -> u64`,
  `fn lfsr_byte(state: &mut u64, taps: u64) -> u8`,
  `fn lfsr_bytes(state: u64, taps: u64, n: usize) -> Vec<u8>`,
  `fn derive_first_half() -> [u8; 16]`,
  `fn derive_second_half(c: u32, z: u32) -> [u8; 16]`;
  consts `LFSR_TAPS_BY_INDEX: [[u8; 4]; 32]`, `COPYRIGHT_PREFIX: &[u8]`.

- [ ] **Step 1: Declare the module in `lib.rs`**

Replace `decoder-rs/src/lib.rs` with:
```rust
//! XTZ → Guitar Pro decoder library.

pub mod cipher;
```

- [ ] **Step 2: Write the failing tests in `decoder-rs/src/cipher.rs`**

Create the file with the tap table, consts, and a test module (implementations come next):
```rust
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
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test cipher`
Expected: FAIL — `cannot find function taps_bitmap` (does not compile).

- [ ] **Step 4: Implement the functions in `cipher.rs`**

Insert above the `#[cfg(test)]` block:
```rust
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
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test cipher`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd decoder-rs && git add src/cipher.rs src/lib.rs
git commit -m "feat(decoder-rs): LFSR key-half derivation"
```

---

### Task 3: Cipher — ChaCha8 + `decrypt_xtz` (+ golden end-to-end)

**Files:**
- Modify: `decoder-rs/src/cipher.rs`

**Interfaces:**
- Consumes: `derive_first_half`, `derive_second_half` (Task 2).
- Produces: `pub fn decrypt_xtz(data: &[u8]) -> anyhow::Result<Vec<u8>>` — strips the 20-byte XTZ header and returns the decrypted GP bytes; errors on bad magic or input shorter than 21 bytes.

- [ ] **Step 1: Add the failing tests to the `tests` module in `cipher.rs`**

Append inside `mod tests`:
```rust
    fn fixture_xtz() -> Vec<u8> {
        std::fs::read(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../PY/captures/20260506-135324-eagles-hotel-california-official-1910943/",
            "002-tab-download-ssid-1910943-1e895791e7ac.xtz"
        ))
        .expect("fixture .xtz present")
    }

    fn fixture_gp() -> Vec<u8> {
        std::fs::read(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../PY/captures/20260506-135324-eagles-hotel-california-official-1910943/",
            "002-tab-download-ssid-1910943-1e895791e7ac.gp"
        ))
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test cipher`
Expected: FAIL — `cannot find function decrypt_xtz`.

- [ ] **Step 3: Implement ChaCha8 + `decrypt_xtz` in `cipher.rs`**

Add at the top of the file (after the doc comment):
```rust
use anyhow::{bail, Result};

const SIGMA: &[u8; 16] = b"expand 32-byte k";
```

Add above the `#[cfg(test)]` block:
```rust
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test cipher`
Expected: PASS (6 tests total) — including `decrypts_fixture_byte_for_byte`.

- [ ] **Step 5: Commit**

```bash
cd decoder-rs && git add src/cipher.rs
git commit -m "feat(decoder-rs): ChaCha8 + decrypt_xtz with golden e2e test"
```

---

### Task 4: Discovery — find pending `.xtz` files

**Files:**
- Create: `decoder-rs/src/discover.rs`
- Modify: `decoder-rs/src/lib.rs`

**Interfaces:**
- Produces: `pub struct Discovery { pub pending: Vec<PathBuf>, pub already_decoded: usize }`;
  `pub fn discover(root: &Path, force: bool) -> Discovery`. A directory is eligible iff it directly contains `metadata.json`. In an eligible dir, each `*.xtz` whose sibling `<stem>.gp` is absent (or when `force`) is pending; an `.xtz` whose `.gp` exists counts toward `already_decoded` (0 when `force`).

- [ ] **Step 1: Declare the module in `lib.rs`**

Append to `decoder-rs/src/lib.rs`:
```rust
pub mod discover;
```

- [ ] **Step 2: Write the failing tests in `decoder-rs/src/discover.rs`**

```rust
use std::collections::HashSet;
use std::path::{Path, PathBuf};

use walkdir::WalkDir;

#[derive(Debug)]
pub struct Discovery {
    pub pending: Vec<PathBuf>,
    pub already_decoded: usize,
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn touch(path: &Path, bytes: &[u8]) {
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(path, bytes).unwrap();
    }

    fn names(paths: &[PathBuf]) -> HashSet<String> {
        paths
            .iter()
            .map(|p| p.file_name().unwrap().to_string_lossy().into_owned())
            .collect()
    }

    #[test]
    fn finds_pending_in_eligible_dir() {
        let tmp = tempdir();
        touch(&tmp.join("artist/song-1/tab.xtz"), b"XTZ\x00...");
        touch(&tmp.join("artist/song-1/metadata.json"), b"{}");
        let d = discover(&tmp, false);
        assert_eq!(names(&d.pending), HashSet::from(["tab.xtz".to_string()]));
        assert_eq!(d.already_decoded, 0);
    }

    #[test]
    fn skips_dir_without_metadata() {
        let tmp = tempdir();
        touch(&tmp.join("artist/song-2/tab.xtz"), b"XTZ\x00...");
        let d = discover(&tmp, false);
        assert!(d.pending.is_empty());
    }

    #[test]
    fn skips_already_decoded_unless_force() {
        let tmp = tempdir();
        touch(&tmp.join("a/s/tab.xtz"), b"XTZ\x00...");
        touch(&tmp.join("a/s/tab.gp"), b"PK\x03\x04");
        touch(&tmp.join("a/s/metadata.json"), b"{}");

        let d = discover(&tmp, false);
        assert!(d.pending.is_empty());
        assert_eq!(d.already_decoded, 1);

        let f = discover(&tmp, true);
        assert_eq!(names(&f.pending), HashSet::from(["tab.xtz".to_string()]));
        assert_eq!(f.already_decoded, 0);
    }

    // Minimal temp-dir helper (no external dev-dep).
    fn tempdir() -> PathBuf {
        let base = std::env::temp_dir().join(format!(
            "decoder-rs-test-{}-{}",
            std::process::id(),
            COUNTER.fetch_add(1, std::sync::atomic::Ordering::SeqCst)
        ));
        std::fs::create_dir_all(&base).unwrap();
        base
    }

    static COUNTER: std::sync::atomic::AtomicU64 = std::sync::atomic::AtomicU64::new(0);
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test discover`
Expected: FAIL — `cannot find function discover`.

- [ ] **Step 4: Implement `discover` in `discover.rs`**

Insert above the `#[cfg(test)]` block:
```rust
pub fn discover(root: &Path, force: bool) -> Discovery {
    let mut eligible: HashSet<PathBuf> = HashSet::new();
    let mut xtz: Vec<PathBuf> = Vec::new();

    for entry in WalkDir::new(root).into_iter().filter_map(|e| e.ok()) {
        if !entry.file_type().is_file() {
            continue;
        }
        let path = entry.path();
        if path.file_name().and_then(|s| s.to_str()) == Some("metadata.json") {
            if let Some(parent) = path.parent() {
                eligible.insert(parent.to_path_buf());
            }
        }
        if path.extension().and_then(|s| s.to_str()) == Some("xtz") {
            xtz.push(path.to_path_buf());
        }
    }

    let mut pending = Vec::new();
    let mut already_decoded = 0usize;
    for path in xtz {
        let parent = match path.parent() {
            Some(p) => p,
            None => continue,
        };
        if !eligible.contains(parent) {
            continue;
        }
        if !force && path.with_extension("gp").exists() {
            already_decoded += 1;
        } else {
            pending.push(path);
        }
    }

    Discovery { pending, already_decoded }
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test discover`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd decoder-rs && git add src/discover.rs src/lib.rs
git commit -m "feat(decoder-rs): discover pending xtz files"
```

---

### Task 5: Output — validate GP ZIP, extract score.gpif, atomic write

**Files:**
- Create: `decoder-rs/src/output.rs`
- Modify: `decoder-rs/src/lib.rs`

**Interfaces:**
- Produces: `pub fn extract_score_gpif(gp: &[u8]) -> anyhow::Result<Vec<u8>>` (errors unless `gp` is a ZIP containing `Content/score.gpif`); `pub fn write_outputs(xtz_path: &Path, gp: &[u8], gpif: &[u8]) -> anyhow::Result<()>` (atomically writes `<stem>.gp` and `<stem>.gpif` beside the `.xtz`).

- [ ] **Step 1: Declare the module in `lib.rs`**

Append to `decoder-rs/src/lib.rs`:
```rust
pub mod output;
```

- [ ] **Step 2: Write the failing tests in `decoder-rs/src/output.rs`**

```rust
use std::io::Read;
use std::path::Path;

use anyhow::{anyhow, bail, Result};

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_gp() -> Vec<u8> {
        std::fs::read(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../PY/captures/20260506-135324-eagles-hotel-california-official-1910943/",
            "002-tab-download-ssid-1910943-1e895791e7ac.gp"
        ))
        .unwrap()
    }

    #[test]
    fn extracts_score_gpif_from_real_gp() {
        let gpif = extract_score_gpif(&fixture_gp()).unwrap();
        assert_eq!(gpif.len(), 1_845_208);
        assert!(gpif.starts_with(b"<?xml"));
    }

    #[test]
    fn rejects_non_zip() {
        assert!(extract_score_gpif(b"not a zip at all").is_err());
    }

    #[test]
    fn write_outputs_creates_gp_and_gpif() {
        let dir = std::env::temp_dir().join(format!("decoder-out-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let xtz = dir.join("tab.xtz");
        std::fs::write(&xtz, b"XTZ\x00").unwrap();

        write_outputs(&xtz, b"PK\x03\x04gpbytes", b"<?xml gpif").unwrap();

        assert_eq!(std::fs::read(dir.join("tab.gp")).unwrap(), b"PK\x03\x04gpbytes");
        assert_eq!(std::fs::read(dir.join("tab.gpif")).unwrap(), b"<?xml gpif");
        // No leftover temp files.
        let leftovers: Vec<_> = std::fs::read_dir(&dir)
            .unwrap()
            .filter_map(|e| e.ok())
            .filter(|e| e.file_name().to_string_lossy().ends_with(".tmp"))
            .collect();
        assert!(leftovers.is_empty());
    }
}
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test output`
Expected: FAIL — `cannot find function extract_score_gpif`.

- [ ] **Step 4: Implement `output.rs`**

Insert above the `#[cfg(test)]` block:
```rust
const GPIF_ENTRY: &str = "Content/score.gpif";

/// Validate `gp` is a real Guitar Pro ZIP and return its `Content/score.gpif` bytes.
pub fn extract_score_gpif(gp: &[u8]) -> Result<Vec<u8>> {
    if gp.len() < 4 || &gp[0..4] != b"PK\x03\x04" {
        bail!("decrypted output is not a ZIP (bad PK magic)");
    }
    let mut archive = zip::ZipArchive::new(std::io::Cursor::new(gp))
        .map_err(|e| anyhow!("not a valid ZIP: {e}"))?;
    let mut file = archive
        .by_name(GPIF_ENTRY)
        .map_err(|_| anyhow!("ZIP missing {GPIF_ENTRY}"))?;
    let mut out = Vec::new();
    file.read_to_end(&mut out)?;
    Ok(out)
}

fn atomic_write(path: &Path, bytes: &[u8]) -> Result<()> {
    let dir = path.parent().ok_or_else(|| anyhow!("no parent dir for {path:?}"))?;
    let file_name = path
        .file_name()
        .ok_or_else(|| anyhow!("no file name for {path:?}"))?
        .to_string_lossy();
    // Temp name is unique per target file; parallel jobs write distinct stems.
    let tmp = dir.join(format!(".{file_name}.tmp"));
    std::fs::write(&tmp, bytes)?;
    std::fs::rename(&tmp, path)?;
    Ok(())
}

/// Write `<stem>.gp` and `<stem>.gpif` beside the given `.xtz`, atomically.
pub fn write_outputs(xtz_path: &Path, gp: &[u8], gpif: &[u8]) -> Result<()> {
    atomic_write(&xtz_path.with_extension("gp"), gp)?;
    atomic_write(&xtz_path.with_extension("gpif"), gpif)?;
    Ok(())
}
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test output`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
cd decoder-rs && git add src/output.rs src/lib.rs
git commit -m "feat(decoder-rs): validate GP zip, extract score.gpif, atomic write"
```

---

### Task 6: Orchestration (`run`) + CLI

**Files:**
- Modify: `decoder-rs/src/lib.rs`
- Modify: `decoder-rs/src/main.rs`

**Interfaces:**
- Consumes: `discover` (Task 4), `cipher::decrypt_xtz` (Task 3), `output::{extract_score_gpif, write_outputs}` (Task 5).
- Produces: `pub struct Options { pub root: PathBuf, pub force: bool, pub jobs: usize, pub quiet: bool }`; `pub struct Summary { pub decoded: usize, pub skipped: usize, pub failed: usize }`; `pub fn run(opts: &Options) -> Summary`. `main` parses a `clap` CLI into `Options` (root defaults to `$OUTPUT_DIR` then `./output`) and prints the summary.

- [ ] **Step 1: Add the failing orchestration test to `lib.rs`**

Append to `decoder-rs/src/lib.rs`:
```rust
use std::path::PathBuf;

pub struct Options {
    pub root: PathBuf,
    pub force: bool,
    pub jobs: usize,
    pub quiet: bool,
}

pub struct Summary {
    pub decoded: usize,
    pub skipped: usize,
    pub failed: usize,
}

#[cfg(test)]
mod run_tests {
    use super::*;

    fn fixture_xtz() -> PathBuf {
        PathBuf::from(concat!(
            env!("CARGO_MANIFEST_DIR"),
            "/../PY/captures/20260506-135324-eagles-hotel-california-official-1910943/",
            "002-tab-download-ssid-1910943-1e895791e7ac.xtz"
        ))
    }

    fn staged_tree() -> PathBuf {
        let root = std::env::temp_dir().join(format!(
            "decoder-run-{}-{}",
            std::process::id(),
            std::time::SystemTime::now()
                .duration_since(std::time::UNIX_EPOCH)
                .unwrap()
                .as_nanos()
        ));
        let dir = root.join("artist/song-1");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::copy(fixture_xtz(), dir.join("tab.xtz")).unwrap();
        std::fs::write(dir.join("metadata.json"), b"{}").unwrap();
        root
    }

    fn opts(root: PathBuf, force: bool) -> Options {
        Options { root, force, jobs: 1, quiet: true }
    }

    #[test]
    fn run_decodes_then_is_idempotent() {
        let root = staged_tree();
        let song = root.join("artist/song-1");

        let s1 = run(&opts(root.clone(), false));
        assert_eq!((s1.decoded, s1.failed), (1, 0));
        assert!(song.join("tab.gp").exists());
        assert!(song.join("tab.gpif").exists());

        // Second run: already decoded -> skipped, nothing re-decoded.
        let s2 = run(&opts(root.clone(), false));
        assert_eq!((s2.decoded, s2.skipped, s2.failed), (0, 1, 0));

        // Force: re-decode.
        let s3 = run(&opts(root, true));
        assert_eq!((s3.decoded, s3.failed), (1, 0));
    }

    #[test]
    fn run_counts_corrupt_xtz_as_failed() {
        let root = std::env::temp_dir().join(format!("decoder-bad-{}", std::process::id()));
        let dir = root.join("a/s");
        std::fs::create_dir_all(&dir).unwrap();
        std::fs::write(dir.join("bad.xtz"), b"XTZ\x00 not really encrypted").unwrap();
        std::fs::write(dir.join("metadata.json"), b"{}").unwrap();

        let s = run(&opts(root, false));
        assert_eq!((s.decoded, s.failed), (0, 1));
        assert!(!dir.join("bad.gp").exists());
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test run_`
Expected: FAIL — `cannot find function run`.

- [ ] **Step 3: Implement `run` in `lib.rs`**

Add near the top of `decoder-rs/src/lib.rs` (after the existing `pub mod` lines):
```rust
use std::sync::atomic::{AtomicUsize, Ordering};

use rayon::prelude::*;
```

Add the function (below the `Summary` struct):
```rust
/// Decode one pending `.xtz`. Returns Ok(()) on success; Err carries a log message.
fn decode_one(xtz_path: &std::path::Path) -> anyhow::Result<()> {
    let data = std::fs::read(xtz_path)?;
    let gp = cipher::decrypt_xtz(&data)?;
    let gpif = output::extract_score_gpif(&gp)?;
    output::write_outputs(xtz_path, &gp, &gpif)?;
    Ok(())
}

pub fn run(opts: &Options) -> Summary {
    let found = discover::discover(&opts.root, opts.force);
    let decoded = AtomicUsize::new(0);
    let failed = AtomicUsize::new(0);

    let pool = rayon::ThreadPoolBuilder::new()
        .num_threads(opts.jobs)
        .build()
        .expect("failed to build thread pool");

    pool.install(|| {
        found.pending.par_iter().for_each(|xtz| match decode_one(xtz) {
            Ok(()) => {
                decoded.fetch_add(1, Ordering::Relaxed);
                if !opts.quiet {
                    eprintln!("decoded {}", xtz.display());
                }
            }
            Err(e) => {
                // A vanished dir (scraper re-scrape race) is not a real failure.
                if is_missing(&e) {
                    if !opts.quiet {
                        eprintln!("skip (vanished) {}", xtz.display());
                    }
                } else {
                    failed.fetch_add(1, Ordering::Relaxed);
                    eprintln!("FAILED {}: {e:#}", xtz.display());
                }
            }
        });
    });

    Summary {
        decoded: decoded.load(Ordering::Relaxed),
        skipped: found.already_decoded,
        failed: failed.load(Ordering::Relaxed),
    }
}

fn is_missing(e: &anyhow::Error) -> bool {
    e.downcast_ref::<std::io::Error>()
        .map(|io| io.kind() == std::io::ErrorKind::NotFound)
        .unwrap_or(false)
}
```

Note: `opts.jobs` must be `>= 1`. The CLI (Step 5) guarantees this; tests pass `jobs: 1`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo test run_`
Expected: PASS (2 tests).

- [ ] **Step 5: Implement the CLI in `main.rs`**

Replace `decoder-rs/src/main.rs` with:
```rust
use std::path::PathBuf;

use clap::Parser;
use decoder_rs::{run, Options};

/// Decrypt scraped UG `.xtz` tabs into Guitar Pro `.gp` files.
#[derive(Parser)]
#[command(name = "decoder-rs", version, about)]
struct Cli {
    /// Output root to scan (default: $OUTPUT_DIR, then ./output).
    output_dir: Option<PathBuf>,

    /// Re-decode even when a sibling .gp already exists.
    #[arg(long)]
    force: bool,

    /// Number of parallel decode threads (default: number of CPUs).
    #[arg(long)]
    jobs: Option<usize>,

    /// Suppress per-file lines; still print the summary.
    #[arg(long)]
    quiet: bool,
}

fn main() {
    let cli = Cli::parse();
    let root = cli
        .output_dir
        .or_else(|| std::env::var_os("OUTPUT_DIR").map(PathBuf::from))
        .unwrap_or_else(|| PathBuf::from("./output"));
    let jobs = cli.jobs.unwrap_or_else(num_cpus).max(1);

    let summary = run(&Options { root, force: cli.force, jobs, quiet: cli.quiet });
    println!(
        "decoded {} | skipped {} | failed {}",
        summary.decoded, summary.skipped, summary.failed
    );
}

fn num_cpus() -> usize {
    std::thread::available_parallelism().map(|n| n.get()).unwrap_or(1)
}
```

- [ ] **Step 6: Build the binary and verify the full test suite**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo build && PATH="$HOME/.cargo/bin:$PATH" cargo test`
Expected: builds; all tests PASS (cipher 6, discover 3, output 3, run 2).

- [ ] **Step 7: Commit**

```bash
cd decoder-rs && git add src/lib.rs src/main.rs
git commit -m "feat(decoder-rs): run orchestration + clap CLI"
```

---

### Task 7: README + final verification

**Files:**
- Create: `decoder-rs/README.md`

- [ ] **Step 1: Write `decoder-rs/README.md`**

```markdown
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
integers) followed by a ChaCha8 stream-XOR payload. The cipher is a bit-exact
port of `PY/xtz_decrypt.py`, verified byte-for-byte against a known fixture.

## Test

```bash
cargo test
```
```

- [ ] **Step 2: Run clippy and the full suite**

Run: `cd decoder-rs && PATH="$HOME/.cargo/bin:$PATH" cargo clippy --all-targets && PATH="$HOME/.cargo/bin:$PATH" cargo test`
Expected: clippy clean (address any warnings; the index-based ChaCha8 loops in `cipher.rs` may trip `clippy::needless_range_loop` — add `#[allow(clippy::needless_range_loop)]` on `chacha8_xor` rather than rewriting the verified layout). All tests PASS.

- [ ] **Step 3: Manual smoke against the real captures tree**

Run:
```bash
cd decoder-rs && cp -r ../PY/captures /tmp/decoder-smoke
# Make one dir eligible (the scraper would have written metadata.json):
echo '{}' > /tmp/decoder-smoke/20260506-135324-eagles-hotel-california-official-1910943/metadata.json
PATH="$HOME/.cargo/bin:$PATH" cargo run -- /tmp/decoder-smoke
```
Expected: prints `decoded 1 | skipped 0 | failed 0`; the eligible dir now holds a
fresh `*.gp` (byte-identical to the committed one) and a `*.gpif`.

- [ ] **Step 4: Commit**

```bash
cd decoder-rs && git add README.md
git commit -m "docs(decoder-rs): README"
```

---

## Self-Review Notes

- **Spec coverage:** input contract + `metadata.json` gate (Task 4); XTZ format + caveats #1/#2 replicated faithfully (Tasks 2–3, comments); validation before write / caveat #3 (Task 5); superseded GPIF path out of scope (no task — intentional); output scope `.gp` + `.gpif` (Task 5); idempotency via sibling `.gp` + `--force` (Tasks 4, 6); crate layout 4 modules (Tasks 2–6); data flow (Task 6 `decode_one`); CLI flags + defaults + exit 0 (Task 6); per-file error isolation + ENOENT skip (Task 6); deps incl. no serde_json (Task 1); all four test groups incl. golden e2e (Tasks 2–6).
- **Type consistency:** `decrypt_xtz(&[u8]) -> Result<Vec<u8>>`, `discover(&Path, bool) -> Discovery{pending, already_decoded}`, `extract_score_gpif(&[u8]) -> Result<Vec<u8>>`, `write_outputs(&Path, &[u8], &[u8])`, `run(&Options) -> Summary{decoded, skipped, failed}` are used identically across tasks. Fixture path string is identical in every test that reads it.
- **No placeholders:** every code step is complete and runnable; golden vectors are concrete values generated from the Python reference.
- **Note on `num_cpus`:** implemented via std `available_parallelism` (no extra crate); `rayon`'s pool is sized from `opts.jobs` which the CLI floors at 1.
```
