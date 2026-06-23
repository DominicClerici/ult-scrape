use std::path::{Path, PathBuf};

use clap::Parser;
use decoder_rs::{run, Options};

/// Decrypt scraped UG `.xtz` tabs into Guitar Pro `.gp` files.
#[derive(Parser)]
#[command(name = "decoder-rs", version, about)]
struct Cli {
    /// Output root to scan. Defaults to $OUTPUT_DIR, then the repo-root output/
    /// directory (found by walking up for the scraper-py/ + decoder-rs/ pair),
    /// then ./output. Works regardless of the launch directory.
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
        .unwrap_or_else(default_output_dir);
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

/// The scraper commits to the repo-root `output/` directory. Locate it by walking
/// up from the current dir to the repo root, so the decoder finds the same dir no
/// matter where it is launched from. Falls back to `./output` (current dir) when
/// the binary runs outside the repo tree.
fn default_output_dir() -> PathBuf {
    std::env::current_dir()
        .ok()
        .and_then(|cwd| find_repo_root(&cwd))
        .map(|root| root.join("output"))
        .unwrap_or_else(|| PathBuf::from("./output"))
}

/// Walk `start` and its ancestors, returning the first that looks like the repo
/// root: a directory containing both the `scraper-py/` and `decoder-rs/` projects.
fn find_repo_root(start: &Path) -> Option<PathBuf> {
    let mut dir = Some(start);
    while let Some(d) = dir {
        if d.join("scraper-py").is_dir() && d.join("decoder-rs").is_dir() {
            return Some(d.to_path_buf());
        }
        dir = d.parent();
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn finds_repo_root_from_a_subdir() {
        let base = std::env::temp_dir().join(format!("decoder-root-{}", std::process::id()));
        let root = base.join("ult-scrape");
        let nested = root.join("decoder-rs").join("src");
        std::fs::create_dir_all(&nested).unwrap();
        std::fs::create_dir_all(root.join("scraper-py")).unwrap();

        assert_eq!(find_repo_root(&nested).as_deref(), Some(root.as_path()));
        assert_eq!(find_repo_root(&root).as_deref(), Some(root.as_path()));

        std::fs::remove_dir_all(&base).ok();
    }

    #[test]
    fn returns_none_outside_the_repo() {
        let dir = std::env::temp_dir().join(format!("decoder-noroot-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();

        assert_eq!(find_repo_root(&dir), None);

        std::fs::remove_dir_all(&dir).ok();
    }
}
