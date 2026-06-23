#!/usr/bin/env bash
# Work around an upstream Playwright bug that crashes the Node driver when a page
# emits an uncaught error whose `location` is undefined.
#
# The driver's PageError dispatcher (driver/package/lib/coreBundle.js) reads
# `pageError.location.url` (and .lineNumber/.columnNumber) unconditionally. With
# Camoufox/Firefox, some uncaught page errors (e.g. from cross-origin third-party
# scripts on ultimate-guitar.com) arrive with no `location`, so that read throws
# *inside* the Node event emitter and takes the whole driver process down — the
# Python side then dies with "the handler is closed". We never even subscribe to
# pageError; Playwright dispatches it regardless, so this can't be avoided from
# Python. The fix makes those reads use optional chaining.
#
# Idempotent. Re-run after any `pip install` / Playwright reinstall (which
# restores the vendored, unpatched bundle). start-scraper.sh runs it for you.
set -euo pipefail

PY="${PYTHON:-python}"
command -v "$PY" >/dev/null 2>&1 || PY=python3

bundle="$("$PY" - <<'PYEOF'
import os
try:
    import playwright
except Exception:
    raise SystemExit(0)
p = os.path.join(os.path.dirname(playwright.__file__),
                 "driver", "package", "lib", "coreBundle.js")
print(p if os.path.exists(p) else "")
PYEOF
)"

if [ -z "$bundle" ] || [ ! -f "$bundle" ]; then
  echo "patch-playwright-driver: coreBundle.js not found; skipping." >&2
  exit 0
fi

if grep -qF 'pageError.location?.url ?? ""' "$bundle"; then
  exit 0  # already patched
fi

if ! grep -q 'pageError.location' "$bundle"; then
  echo "patch-playwright-driver: target line not found (Playwright changed?); skipping." >&2
  exit 0
fi

# Make the three reads safe AND schema-valid: optional-chain the lookup and fall
# back to the schema's required types (url is tString, line/column are tInt), so
# a missing `location` yields ""/0 instead of undefined (which the driver's own
# validator rejects). Matches the original or an already-optional-chained form,
# with or without an existing fallback, so it's idempotent. Perl keeps the
# in-place edit portable across macOS and Linux.
perl -pi -e '
  s/pageError\.location\??\.url(?:\s*\?\?\s*"")?/pageError.location?.url ?? ""/g;
  s/pageError\.location\??\.lineNumber(?:\s*\?\?\s*0)?/pageError.location?.lineNumber ?? 0/g;
  s/pageError\.location\??\.columnNumber(?:\s*\?\?\s*0)?/pageError.location?.columnNumber ?? 0/g;
' "$bundle"

echo "patch-playwright-driver: patched $bundle"
