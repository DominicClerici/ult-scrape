#!/usr/bin/env bash
# Clear scrape work — two modes:
#
#   ./clear.sh                cancel every queued job (DELETE /jobs). The
#                             in-flight job finishes first, just like ./pause.sh.
#                             To stop new work too, pause first:
#                               ./pause.sh && ./clear.sh
#
#   ./clear.sh --hard-reset   FACTORY-WIPE all tracked data across the services:
#                             the shared output/ tree, the scraper queue DB, and
#                             the enricher DB (plus their SQLite sidecars). The
#                             browser login session (camoufox-profile/) is KEPT.
#                             Irreversible — prompts for confirmation. Refuses to
#                             run while the scraper is up (delete the DB out from
#                             under a live worker and the WAL can resurrect rows);
#                             stop the scraper first, and don't run it during an
#                             `enricher run` either.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

usage() { sed -n '2,17p' "$0" | sed 's/^# \{0,1\}//'; }

# Resolve $2 (relative paths are taken relative to base dir $1) to a path.
resolve_path() {
  case "$2" in
    /*) printf '%s\n' "$2" ;;
    *)  printf '%s\n' "$1/$2" ;;
  esac
}

# Print a directory's normalized absolute path when it exists, else as-is.
norm_dir() {
  if [ -d "$1" ]; then (cd "$1" && pwd); else printf '%s\n' "$1"; fi
}

# Human-readable size of a file, or "(absent)" when it does not exist.
file_size() {
  if [ -f "$1" ]; then du -h "$1" | cut -f1; else printf '%s' "(absent)"; fi
}

# Delete a SQLite database and its WAL/SHM/journal sidecars.
rm_sqlite() {
  rm -f "$1" "$1-wal" "$1-shm" "$1-journal"
}

hard_reset() {
  # Safety gate: a reachable scraper means a live worker may hold the DB open.
  if api_request GET /status >/dev/null 2>&1; then
    echo "error: the scraper is running at $BASE_URL." >&2
    echo "       Stop it (Ctrl-C scripts/start-scraper.sh) before a hard reset," >&2
    echo "       otherwise the live worker can resurrect the wiped database." >&2
    return 1
  fi

  local output_dir scraper_db enricher_db tab_count file_count
  output_dir="$(norm_dir "$(resolve_path "$SCRAPER_DIR" "${OUTPUT_DIR:-../output}")")"
  scraper_db="$(resolve_path "$SCRAPER_DIR" "${DB_PATH:-./scraper.db}")"
  enricher_db="$(resolve_path "$REPO_ROOT/enricher-py" "${ENRICHER_DB:-./enricher.db}")"

  tab_count=0
  file_count=0
  if [ -d "$output_dir" ]; then
    tab_count="$(find "$output_dir" -name metadata.json 2>/dev/null | wc -l | tr -d '[:space:]')"
    file_count="$(find "$output_dir" -type f 2>/dev/null | wc -l | tr -d '[:space:]')"
  fi

  echo "HARD RESET — this permanently deletes ALL tracked data:"
  echo
  echo "  output tree   $output_dir"
  echo "                  $tab_count committed tab(s), $file_count file(s)"
  echo "  scraper DB    $scraper_db  ($(file_size "$scraper_db"))"
  echo "  enricher DB   $enricher_db  ($(file_size "$enricher_db"))"
  echo
  echo "  The browser login session (camoufox-profile/) is kept."
  echo "  This cannot be undone."
  echo

  printf "Type 'yes' to proceed: "
  local reply=""
  read -r reply || true
  if [ "$reply" != "yes" ]; then
    echo "Aborted — nothing was deleted."
    return 1
  fi

  rm -rf "$output_dir"
  mkdir -p "$output_dir"
  rm_sqlite "$scraper_db"
  rm_sqlite "$enricher_db"

  echo "Done. Wiped the output tree and the scraper + enricher databases."
}

HARD_RESET=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --hard-reset) HARD_RESET=1; shift ;;
    -h|--help)    usage; exit 0 ;;
    *)
      echo "error: unknown argument: $1" >&2
      echo "       run ./clear.sh --help for usage." >&2
      exit 1
      ;;
  esac
done

if [ "$HARD_RESET" = 1 ]; then
  hard_reset
  exit $?
fi

resp="$(api_request DELETE /jobs)"
if [ "$HAVE_JQ" = 1 ]; then
  count="$(printf '%s' "$resp" | jq -r '.canceled')"
  echo "Canceled $count queued job(s). Any in-flight job finishes first."
else
  printf '%s\n' "$resp"
fi
