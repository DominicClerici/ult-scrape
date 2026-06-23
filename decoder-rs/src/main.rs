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
