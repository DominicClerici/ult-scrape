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
