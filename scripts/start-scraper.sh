#!/usr/bin/env bash
# Start the scraper-py FastAPI service, loading scraper-py/.env for its settings.
#
# Activates scraper-py/.venv if present, then runs uvicorn bound to the
# API_HOST/API_PORT from .env. Extra args are passed straight to uvicorn
# (e.g. --reload, --log-level debug).
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

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

if ! command -v uvicorn >/dev/null 2>&1; then
  echo "error: uvicorn not found. Install deps first:" >&2
  echo "       cd $SCRAPER_DIR && pip install -e \".[dev]\"" >&2
  exit 1
fi

echo "Starting scraper on http://$API_HOST:$API_PORT (output: ${OUTPUT_DIR:-./output})"
exec uvicorn app.main:app --host "$API_HOST" --port "$API_PORT" "$@"
