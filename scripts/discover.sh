#!/usr/bin/env bash
# Start a Pro-tab discovery run (POST /discover).
#
# Discovery crawls UG's explore listing and records discovered tabs in
# tab_metadata. It does NOT enqueue or scrape anything — run ./discover.sh
# --enqueue afterwards to turn discovered tabs into scrape jobs.
#
# Usage:
#   ./discover.sh                 start a run with the server defaults
#   ./discover.sh --max 50        stop the run once 50 distinct tabs are found
#   ./discover.sh --list          list recent discovery runs and their progress
#   ./discover.sh --enqueue       enqueue all discovered-but-unscraped tabs
#
# Notes:
#   - A run is accepted only when the scrape queue is empty and no other run is
#     active (the server returns 409 otherwise).
#   - The run is worker-owned and asynchronous: this script kicks it off and
#     prints the run id; watch progress with ./discover.sh --list.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

if [ "$HAVE_JQ" != 1 ]; then
  echo "error: jq is required. Install it (brew install jq)." >&2
  exit 1
fi

MAX=""
LIST=0
ENQUEUE=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --max)
      MAX="${2:-}"; shift 2 || true
      ;;
    --max=*)
      MAX="${1#--max=}"; shift
      ;;
    --list)
      LIST=1; shift
      ;;
    --enqueue)
      ENQUEUE=1; shift
      ;;
    -h|--help)
      sed -n '2,18p' "$0" | sed 's/^# \{0,1\}//'
      exit 0
      ;;
    *)
      echo "error: unknown argument: $1" >&2
      echo "       run ./discover.sh --help for usage." >&2
      exit 1
      ;;
  esac
done

if [ "$LIST" = 1 ]; then
  api_request GET /discover | pretty_json
  exit 0
fi

if [ "$ENQUEUE" = 1 ]; then
  resp="$(api_request POST /discover/enqueue -H 'Content-Type: application/json')"
  created="$(printf '%s' "$resp" | jq 'length')"
  echo "Enqueued $created discovered tab(s) as scrape job(s)."
  printf '%s\n' "$resp" | jq -r '.[] | "  \(.status)\t\(.tab_id)\t\(.id)"'
  exit 0
fi

payload='{}'
if [ -n "$MAX" ]; then
  if ! [[ "$MAX" =~ ^[0-9]+$ ]] || [ "$MAX" -le 0 ]; then
    echo "error: --max must be a positive integer (got: $MAX)" >&2
    exit 1
  fi
  payload="$(jq -n --argjson n "$MAX" '{target_cap: $n}')"
fi

resp="$(api_request POST /discover -H 'Content-Type: application/json' -d "$payload")"

run_id="$(printf '%s' "$resp" | jq -r '.id')"
if [ -n "$MAX" ]; then
  echo "Started discovery run $run_id (stopping after $MAX distinct tab(s))."
else
  echo "Started discovery run $run_id."
fi
echo "Watch progress:  ./discover.sh --list"
echo "Enqueue results: ./discover.sh --enqueue"
