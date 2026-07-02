# Start the scraper-py FastAPI service, loading scraper-py/.env for its settings.
#
# Activates scraper-py/.venv if present, then runs uvicorn bound to the
# API_HOST/API_PORT from .env. Extra args are passed straight to uvicorn
# (e.g. -reload, --log-level debug).
#
# With -Login, instead of starting the service, opens a visible browser at
# Ultimate Guitar for a one-time manual login. Log in by hand, then press Enter
# in the terminal to save the authenticated session + fingerprint (overwriting
# any previously saved one). Later runs without -Login reuse that session.
param(
    [switch]$Login,
    [Parameter(ValueFromRemainingArguments = $true)][string[]]$UvicornArgs
)

. "$PSScriptRoot\_common.ps1"

if (-not (Test-Path -LiteralPath $EnvFile -PathType Leaf)) {
    Write-Error "error: $EnvFile not found."
    Write-Error "       Copy $ScraperDir\.env.example to $EnvFile and fill in UG_EMAIL / UG_PASSWORD."
    exit 1
}

Set-Location $ScraperDir

$venvActivate = Join-Path $ScraperDir '.venv\Scripts\Activate.ps1'
if (Test-Path -LiteralPath $venvActivate -PathType Leaf) {
    . $venvActivate
}

$pythonCmd = Get-Command python -ErrorAction SilentlyContinue
if (-not $pythonCmd) { $pythonCmd = Get-Command python3 -ErrorAction SilentlyContinue }
if (-not $pythonCmd) {
    Write-Error "error: python not found. Install deps first:"
    Write-Error "       cd $ScraperDir; pip install -e `".[dev]`""
    exit 1
}
$python = $pythonCmd.Source

# Re-apply the Playwright driver crash workaround (no-op if already patched).
# A fresh `pip install` restores the vendored, unpatched bundle, so do this on
# every start. See scripts\windows\patch-playwright-driver.ps1 for the why.
$env:PYTHON = $python
try {
    & "$PSScriptRoot\patch-playwright-driver.ps1"
} catch {}

if ($Login) {
    Write-Host "Opening Ultimate Guitar for a manual login (this overwrites any saved session)…"
    & $python -m app.manual_login
    exit $LASTEXITCODE
}

if (-not (Get-Command uvicorn -ErrorAction SilentlyContinue)) {
    Write-Error "error: uvicorn not found. Install deps first:"
    Write-Error "       cd $ScraperDir; pip install -e `".[dev]`""
    exit 1
}

$outDisplay = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { '../output' }
Write-Host "Starting scraper on http://${ApiHost}:${ApiPort} (output: $outDisplay)"
uvicorn app.main:app --host $ApiHost --port $ApiPort @UvicornArgs
exit $LASTEXITCODE
