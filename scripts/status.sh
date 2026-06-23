#!/usr/bin/env bash
# Print the scraper's current status (GET /status).
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

api_request GET /status | pretty_json
