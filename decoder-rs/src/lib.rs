//! XTZ → Guitar Pro decoder library.

pub mod cipher;
pub mod discover;
pub mod output;

use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};

use rayon::prelude::*;

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

#[cfg(test)]
mod run_tests {
    use super::*;

    fn fixture_xtz() -> PathBuf {
        PathBuf::from(concat!(env!("CARGO_MANIFEST_DIR"), "/tests/fixtures/sample.xtz"))
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
