# Shared helpers for the ult-scrape scripts. Source this; don't run it directly.
#
# Resolves paths, loads scraper-py/.env, and exposes:
#   $REPO_ROOT $SCRAPER_DIR $ENV_FILE   - locations
#   $API_HOST $API_PORT $API_KEY        - from .env (or the environment)
#   $BASE_URL                           - where the API is reachable
#   load_dotenv <file>                  - export KEY=VALUE pairs (env wins)
#   api_request <METHOD> <PATH> [curl args...]  - call the API, print body
#
# Every value can be overridden from the environment, e.g.
#   SCRAPER_URL=http://host:9000 ./status.sh

_COMMON_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$_COMMON_DIR/.." && pwd)"
SCRAPER_DIR="$REPO_ROOT/scraper-py"
ENV_FILE="$SCRAPER_DIR/.env"

# Read KEY=VALUE lines from a .env file and export them, but never clobber a
# value already set in the environment (so callers can override anything).
load_dotenv() {
  local file="$1" line key val
  [ -f "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%$'\r'}"
    case "$line" in
      ''|'#'*) continue ;;
    esac
    [ "${line#*=}" = "$line" ] && continue   # no '=' on the line
    key="${line%%=*}"
    val="${line#*=}"
    key="$(printf '%s' "$key" | tr -d '[:space:]')"
    case "$val" in
      \"*\") val="${val#\"}"; val="${val%\"}" ;;
      \'*\') val="${val#\'}"; val="${val%\'}" ;;
    esac
    [ -z "${!key+x}" ] && export "$key=$val"
  done < "$file"
}

load_dotenv "$ENV_FILE"

API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8000}"
API_KEY="${API_KEY:-}"

# 0.0.0.0 is a bind address, not a connect address — talk to localhost instead.
_client_host="$API_HOST"
[ "$_client_host" = "0.0.0.0" ] && _client_host="127.0.0.1"
BASE_URL="${SCRAPER_URL:-http://$_client_host:$API_PORT}"

if command -v jq >/dev/null 2>&1; then HAVE_JQ=1; else HAVE_JQ=0; fi

# api_request METHOD PATH [extra curl args...]
# Prints the response body on stdout. Returns non-zero (and prints to stderr) on
# a connection failure or any HTTP status >= 400.
api_request() {
  local method="$1" path="$2"; shift 2
  local hdr=() resp status body
  [ -n "$API_KEY" ] && hdr=(-H "X-API-Key: $API_KEY")
  # ${hdr[@]+...} guard: tolerate an empty array under `set -u` (macOS bash 3.2).
  if ! resp="$(curl -sS -w $'\n%{http_code}' -X "$method" ${hdr[@]+"${hdr[@]}"} "$@" "$BASE_URL$path")"; then
    echo "error: could not reach the scraper at $BASE_URL — is it running? (scripts/start-scraper.sh)" >&2
    return 1
  fi
  status="${resp##*$'\n'}"
  body="${resp%$'\n'*}"
  if [ "$status" -ge 400 ]; then
    echo "error: $method $path returned HTTP $status" >&2
    [ -n "$body" ] && echo "$body" >&2
    return 1
  fi
  printf '%s\n' "$body"
}

# Pretty-print JSON from stdin when jq is available, otherwise pass it through.
pretty_json() {
  if [ "$HAVE_JQ" = 1 ]; then jq .; else cat; fi
}
