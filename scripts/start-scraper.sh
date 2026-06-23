#!/usr/bin/env bash
# Start the scraper-py FastAPI service, loading scraper-py/.env for its settings.
#
# Activates scraper-py/.venv if present, then runs uvicorn bound to the
# API_HOST/API_PORT from .env. Extra args are passed straight to uvicorn
# (e.g. --reload, --log-level debug).
#
# With -l/--login, instead of starting the service, opens a visible browser at
# Ultimate Guitar for a one-time manual login. Log in by hand, then press Enter
# in the terminal to save the authenticated session + fingerprint (overwriting
# any previously saved one). Later runs without --login reuse that session.
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

LOGIN_MODE=0
UVICORN_ARGS=()
for arg in "$@"; do
  case "$arg" in
    -l|--login) LOGIN_MODE=1 ;;
    *) UVICORN_ARGS+=("$arg") ;;
  esac
done

if [ ! -f "$ENV_FILE" ]; then
  echo "error: $ENV_FILE not found." >&2
  echo "       cp $SCRAPER_DIR/.env.example $ENV_FILE  and fill in UG_EMAIL / UG_PASSWORD." >&2
  exit 1
fi

cd "$SCRAPER_DIR"

if [ -f ".venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source ".venv/bin/activate"
fi

PYTHON="$(command -v python || command -v python3 || true)"
if [ -z "$PYTHON" ]; then
  echo "error: python not found. Install deps first:" >&2
  echo "       cd $SCRAPER_DIR && pip install -e \".[dev]\"" >&2
  exit 1
fi

# Re-apply the Playwright driver crash workaround (no-op if already patched).
# A fresh `pip install` restores the vendored, unpatched bundle, so do this on
# every start. See scripts/patch-playwright-driver.sh for the why.
PYTHON="$PYTHON" "$_COMMON_DIR/patch-playwright-driver.sh" || true

if [ "$LOGIN_MODE" = 1 ]; then
  echo "Opening Ultimate Guitar for a manual login (this overwrites any saved session)…"
  exec "$PYTHON" -m app.manual_login
fi

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "error: uvicorn not found. Install deps first:" >&2
  echo "       cd $SCRAPER_DIR && pip install -e \".[dev]\"" >&2
  exit 1
fi

echo "Starting scraper on http://$API_HOST:$API_PORT (output: ${OUTPUT_DIR:-../output})"
exec uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" ${UVICORN_ARGS[@]+"${UVICORN_ARGS[@]}"}
