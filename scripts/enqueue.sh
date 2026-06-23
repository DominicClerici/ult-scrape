#!/usr/bin/env bash
# Enqueue tabs from a CSV file via POST /jobs/bulk.
#
# Usage: ./enqueue.sh [CSV_FILE]   (default: scripts/tabs.csv)
#
# CSV columns (comma-separated):  url_or_route[,priority[,force]]
#   url_or_route  full UG tab URL or a bare "artist/song-slug" route (required)
#   priority      integer, lower runs sooner (default 0)
#   force         true|false — re-scrape even if already succeeded (default false)
#
# Blank lines and lines starting with '#' are skipped. A header row whose first
# cell is "url_or_route" or "url" is ignored. See scripts/tabs.csv.example.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

CSV="${1:-$_COMMON_DIR/tabs.csv}"

if [ "$HAVE_JQ" != 1 ]; then
  echo "error: jq is required to build the request safely. Install it (brew install jq)." >&2
  exit 1
fi
if [ ! -f "$CSV" ]; then
  echo "error: CSV file not found: $CSV" >&2
  echo "       Copy scripts/tabs.csv.example to scripts/tabs.csv and edit it, or pass a path." >&2
  exit 1
fi

trim() { printf '%s' "$1" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//'; }

normalize_bool() {
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]' | tr -d '[:space:]')" in
    true|1|yes|y|force) echo true ;;
    *) echo false ;;
  esac
}

items=()
submitted=0
while IFS= read -r line || [ -n "$line" ]; do
  line="${line%$'\r'}"
  case "$(trim "$line")" in
    ''|'#'*) continue ;;
  esac

  IFS=',' read -r f_url f_prio f_force _rest <<< "$line"
  url="$(trim "$f_url")"
  prio="$(trim "${f_prio:-}")"
  force="$(trim "${f_force:-}")"

  # Skip a header row.
  case "$(printf '%s' "$url" | tr '[:upper:]' '[:lower:]')" in
    url_or_route|url) continue ;;
  esac
  [ -z "$url" ] && continue

  [[ "$prio" =~ ^-?[0-9]+$ ]] || prio=0
  force="$(normalize_bool "$force")"

  items+=("$(jq -n --arg u "$url" --argjson p "$prio" --argjson f "$force" \
    '{url_or_route:$u, priority:$p, force:$f}')")
  submitted=$((submitted + 1))
done < "$CSV"

if [ "$submitted" -eq 0 ]; then
  echo "No tabs found in $CSV — nothing to enqueue." >&2
  exit 1
fi

payload="$(printf '%s\n' "${items[@]}" | jq -s '{items: .}')"

echo "Enqueueing $submitted tab(s) from $CSV ..."
resp="$(api_request POST /jobs/bulk -H 'Content-Type: application/json' -d "$payload")"

created="$(printf '%s' "$resp" | jq 'length')"
echo "Server accepted $created job(s) (some may be deduped or skipped for unparseable routes)."
printf '%s\n' "$resp" | jq -r '.[] | "  \(.status)\t\(.tab_id)\t\(.id)"'
