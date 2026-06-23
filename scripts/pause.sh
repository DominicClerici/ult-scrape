#!/usr/bin/env bash
# Pause the worker after the current job finishes (POST /pause).
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

api_request POST /pause | pretty_json
echo "Worker paused — the in-flight job (if any) finishes first."
