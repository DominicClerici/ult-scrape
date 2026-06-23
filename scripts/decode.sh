#!/usr/bin/env bash
# Decrypt the scraper's saved .xtz files into Guitar Pro .gp files with decoder-rs.
#
# Builds the Rust decoder (release) if it isn't built yet, then runs it against
# the scraper's OUTPUT_DIR (read from scraper-py/.env and resolved the same way
# the scraper writes it). Extra args pass straight through to the decoder, e.g.
#   ./decode.sh --force            # re-decode even where a .gp already exists
#   ./decode.sh --jobs 4 --quiet   # 4 threads, summary only
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

DECODER_DIR="$REPO_ROOT/decoder-rs"
DECODER_BIN="$DECODER_DIR/target/release/decoder-rs"

# Resolve the output dir like the scraper does: a relative OUTPUT_DIR is relative
# to scraper-py/ (where the service runs), not to this script's CWD.
OUT="${OUTPUT_DIR:-../output}"
case "$OUT" in
  /*) ;;
  *) OUT="$SCRAPER_DIR/$OUT" ;;
esac

if [ ! -d "$OUT" ]; then
  echo "error: output dir not found: $OUT" >&2
  echo "       Run the scraper first (scripts/start-scraper.sh) so it writes .xtz files." >&2
  exit 1
fi

if [ ! -x "$DECODER_BIN" ]; then
  if ! command -v cargo >/dev/null 2>&1; then
    echo "error: cargo not found and the decoder isn't built ($DECODER_BIN)." >&2
    echo "       Install Rust (https://rustup.rs), then re-run." >&2
    exit 1
  fi
  echo "Building decoder-rs (release) ..."
  ( cd "$DECODER_DIR" && cargo build --release )
fi

echo "Decoding .xtz files in $OUT ..."
exec "$DECODER_BIN" "$OUT" "$@"
