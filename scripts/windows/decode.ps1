# Decrypt the scraper's saved .xtz files into Guitar Pro .gp files with decoder-rs.
#
# Builds the Rust decoder (release) if it isn't built yet, then runs it against
# the scraper's OUTPUT_DIR (read from scraper-py/.env and resolved the same way
# the scraper writes it). Extra args pass straight through to the decoder, e.g.
#   .\decode.ps1 --force            # re-decode even where a .gp already exists
#   .\decode.ps1 --jobs 4 --quiet   # 4 threads, summary only

. "$PSScriptRoot\_common.ps1"

$DecoderDir = Join-Path $RepoRoot 'decoder-rs'
$DecoderBin = Join-Path $DecoderDir 'target\release\decoder-rs.exe'

# Resolve the output dir like the scraper does: a relative OUTPUT_DIR is relative
# to scraper-py\ (where the service runs), not to this script's CWD.
$outSetting = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { '../output' }
if ([System.IO.Path]::IsPathRooted($outSetting)) {
    $Out = $outSetting
} else {
    $Out = Join-Path $ScraperDir $outSetting
}

if (-not (Test-Path -LiteralPath $Out -PathType Container)) {
    Write-Error "error: output dir not found: $Out"
    Write-Error "       Run the scraper first (scripts\windows\start-scraper.ps1) so it writes .xtz files."
    exit 1
}

if (-not (Test-Path -LiteralPath $DecoderBin -PathType Leaf)) {
    if (-not (Get-Command cargo -ErrorAction SilentlyContinue)) {
        Write-Error "error: cargo not found and the decoder isn't built ($DecoderBin)."
        Write-Error "       Install Rust (https://rustup.rs), then re-run."
        exit 1
    }
    Write-Host "Building decoder-rs (release) ..."
    Push-Location $DecoderDir
    try {
        cargo build --release
        if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
    } finally {
        Pop-Location
    }
}

Write-Host "Decoding .xtz files in $Out ..."
& $DecoderBin $Out @args
exit $LASTEXITCODE
