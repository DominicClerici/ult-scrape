#!/usr/bin/env bash
# Resume the worker (POST /resume).
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"

api_request POST /resume | pretty_json
echo "Worker resumed."
