# Enrich scraped tabs with source audio via enricher-py + yt-dlp.
#
# Activates enricher-py\.venv if present, then runs `enricher <args>` from
# enricher-py (so its relative OUTPUT_DIR/.env resolve the same way the
# enricher does when run by hand). Defaults to `run`, which scans for
# newly-ready tabs before downloading. Extra args pass straight through, e.g.
#   .\enrich.ps1                  # scan + download using .env's MAX_CONCURRENCY
#   .\enrich.ps1 run --jobs 4     # override worker count
#   .\enrich.ps1 run --retry-failed
#   .\enrich.ps1 status           # counts by job state
#   .\enrich.ps1 scan             # enqueue only, no downloads

. "$PSScriptRoot\_common.ps1"

$EnricherDir = Join-Path $RepoRoot 'enricher-py'
$EnricherEnvFile = Join-Path $EnricherDir '.env'

if (-not (Test-Path -LiteralPath $EnricherEnvFile -PathType Leaf)) {
    Write-Error "error: $EnricherEnvFile not found."
    Write-Error "       Copy enricher-py\.env.example to $EnricherEnvFile (defaults are fine to start)."
    exit 1
}

Set-Location $EnricherDir

$venvActivate = Join-Path $EnricherDir '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $venvActivate -PathType Leaf) {
    . $venvActivate
}

if (-not (Get-Command enricher -ErrorAction SilentlyContinue)) {
    Write-Error "error: enricher not found. Install deps first:"
    Write-Error "       cd $EnricherDir; python -m venv .venv; .venv\Scripts\Activate.ps1; pip install -e `".[dev]`""
    exit 1
}

if (-not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    Write-Error "error: ffprobe not found on PATH. Install ffmpeg (provides ffprobe) and re-run."
    exit 1
}

$cmdArgs = @('run')
if ($args.Count -gt 0) { $cmdArgs = $args }
Write-Host "Running: enricher $($cmdArgs -join ' ')"
enricher @cmdArgs
exit $LASTEXITCODE
