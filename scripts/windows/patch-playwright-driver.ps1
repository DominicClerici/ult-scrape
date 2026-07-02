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
# restores the vendored, unpatched bundle). start-scraper.ps1 runs it for you.

$ErrorActionPreference = 'Stop'

$py = $env:PYTHON
if (-not $py) {
    $cmd = Get-Command python -ErrorAction SilentlyContinue
    if (-not $cmd) { $cmd = Get-Command python3 -ErrorAction SilentlyContinue }
    if ($cmd) { $py = $cmd.Source }
}
if (-not $py) {
    Write-Host "patch-playwright-driver: python not found; skipping."
    exit 0
}

$code = @'
import os
try:
    import playwright
except Exception:
    raise SystemExit(0)
p = os.path.join(os.path.dirname(playwright.__file__),
                 "driver", "package", "lib", "coreBundle.js")
print(p if os.path.exists(p) else "")
'@

$bundle = (& $py -c $code) -join "`n"
$bundle = $bundle.Trim()

if (-not $bundle -or -not (Test-Path -LiteralPath $bundle -PathType Leaf)) {
    Write-Host "patch-playwright-driver: coreBundle.js not found; skipping."
    exit 0
}

$content = Get-Content -Raw -LiteralPath $bundle

if ($content.Contains('pageError.location?.url ?? ""')) {
    exit 0  # already patched
}

if ($content -notmatch 'pageError\.location') {
    Write-Host "patch-playwright-driver: target line not found (Playwright changed?); skipping."
    exit 0
}

# Make the three reads safe AND schema-valid: optional-chain the lookup and fall
# back to the schema's required types (url is tString, line/column are tInt), so
# a missing `location` yields ""/0 instead of undefined (which the driver's own
# validator rejects). Matches the original or an already-optional-chained form,
# with or without an existing fallback, so it's idempotent.
$content = [regex]::Replace($content, 'pageError\.location\??\.url(?:\s*\?\?\s*"")?', 'pageError.location?.url ?? ""')
$content = [regex]::Replace($content, 'pageError\.location\??\.lineNumber(?:\s*\?\?\s*0)?', 'pageError.location?.lineNumber ?? 0')
$content = [regex]::Replace($content, 'pageError\.location\??\.columnNumber(?:\s*\?\?\s*0)?', 'pageError.location?.columnNumber ?? 0')

[System.IO.File]::WriteAllText($bundle, $content)

Write-Host "patch-playwright-driver: patched $bundle"
