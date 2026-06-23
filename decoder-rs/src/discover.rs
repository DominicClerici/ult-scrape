use std::collections::HashSet;
use std::path::{Path, PathBuf};

use walkdir::WalkDir;

#[derive(Debug)]
pub struct Discovery {
    pub pending: Vec<PathBuf>,
    pub already_decoded: usize,
}

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
