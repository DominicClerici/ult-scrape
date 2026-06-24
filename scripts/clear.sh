#!/usr/bin/env bash
# Clear the scrape queue: cancel every queued job (DELETE /jobs).
#
# Usage: ./clear.sh
#
# The currently running job (if any) is NOT canceled — it finishes first, just
# like ./pause.sh. To stop new work from starting too, pause first:
#   ./pause.sh && ./clear.sh
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

resp="$(api_request DELETE /jobs)"
if [ "$HAVE_JQ" = 1 ]; then
  count="$(printf '%s' "$resp" | jq -r '.canceled')"
  echo "Canceled $count queued job(s). Any in-flight job finishes first."
else
  printf '%s\n' "$resp"
fi
