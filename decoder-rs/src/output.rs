use std::io::Read;
use std::path::Path;

use anyhow::{anyhow, bail, Result};

const GPIF_ENTRY: &str = "Content/score.gpif";

/// A decoded Guitar Pro container: the on-disk extension to store the container
/// bytes under (`gp` for GP7, `gpx` for GP6) and its extracted `score.gpif`.
pub struct Decoded {
    pub extension: &'static str,
    pub gpif: Vec<u8>,
}

/// Identify a decrypted Guitar Pro container by magic and extract its
/// `score.gpif`. GP7 payloads are ZIPs (`PK\x03\x04`); GP6 payloads are `BCFZ`
/// blobs handled by [`crate::gpx`].
pub fn decode_container(container: &[u8]) -> Result<Decoded> {
    if container.starts_with(b"PK\x03\x04") {
        Ok(Decoded { extension: "gp", gpif: extract_score_gpif(container)? })
    } else if container.starts_with(b"BCFZ") {
        Ok(Decoded { extension: "gpx", gpif: crate::gpx::extract_gpif(container)? })
    } else {
        bail!("decrypted output is not a recognized Guitar Pro file (no PK or BCFZ magic)");
    }
}

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

/// Write `<stem>.<extension>` (the container, `gp` or `gpx`) and `<stem>.gpif`
/// beside the given `.xtz`, atomically.
pub fn write_outputs(
    xtz_path: &Path,
    extension: &str,
    container: &[u8],
    gpif: &[u8],
) -> Result<()> {
    // Write the convenience .gpif BEFORE the container: the container file is
    // discovery's idempotency marker, so this ordering ensures that whenever the
    // marker exists the .gpif does too (a crash between writes just re-decodes
    // next run).
    atomic_write(&xtz_path.with_extension("gpif"), gpif)?;
    atomic_write(&xtz_path.with_extension(extension), container)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn fixture_gp() -> Vec<u8> {
        std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample.gp"))
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
    fn decode_container_classifies_gp7_zip() {
        let decoded = decode_container(&fixture_gp()).unwrap();
        assert_eq!(decoded.extension, "gp");
        assert!(decoded.gpif.starts_with(b"<?xml"));
    }

    #[test]
    fn decode_container_classifies_gp6_bcfz() {
        let xtz =
            std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample_gp6.xtz"))
                .unwrap();
        let container = crate::cipher::decrypt_xtz(&xtz).unwrap();
        let decoded = decode_container(&container).unwrap();
        assert_eq!(decoded.extension, "gpx");
        let expected =
            std::fs::read(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample_gp6.gpif"))
                .unwrap();
        assert_eq!(decoded.gpif, expected);
    }

    #[test]
    fn decode_container_rejects_unknown_magic() {
        assert!(decode_container(b"\x00\x01\x02\x03 neither pk nor bcfz").is_err());
    }

    #[test]
    fn write_outputs_creates_gp_and_gpif() {
        let dir = std::env::temp_dir().join(format!("decoder-out-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let xtz = dir.join("tab.xtz");
        std::fs::write(&xtz, b"XTZ\x00").unwrap();

        write_outputs(&xtz, "gp", b"PK\x03\x04gpbytes", b"<?xml gpif").unwrap();

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

    #[test]
    fn write_outputs_uses_gpx_extension_for_gp6() {
        let dir = std::env::temp_dir().join(format!("decoder-out-gpx-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let xtz = dir.join("tab.xtz");
        std::fs::write(&xtz, b"XTZ\x00").unwrap();

        write_outputs(&xtz, "gpx", b"BCFZcontainer", b"<?xml gpif").unwrap();

        assert_eq!(std::fs::read(dir.join("tab.gpx")).unwrap(), b"BCFZcontainer");
        assert_eq!(std::fs::read(dir.join("tab.gpif")).unwrap(), b"<?xml gpif");
        assert!(!dir.join("tab.gp").exists());
    }
}
