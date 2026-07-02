# Clear scrape work — two modes:
#
#   .\clear.ps1                cancel every queued job (DELETE /jobs). The
#                               in-flight job finishes first, just like .\pause.ps1.
#                               To stop new work too, pause first:
#                                 .\pause.ps1; .\clear.ps1
#
#   .\clear.ps1 -HardReset      FACTORY-WIPE all tracked data across the services:
#                               the shared output/ tree, the scraper queue DB, and
#                               the enricher DB (plus their SQLite sidecars). The
#                               browser login session (camoufox-profile\) is KEPT.
#                               Irreversible — prompts for confirmation. Refuses to
#                               run while the scraper is up (delete the DB out from
#                               under a live worker and the WAL can resurrect rows);
#                               stop the scraper first, and don't run it during an
#                               `enricher run` either.
param(
    [switch]$HardReset
)

. "$PSScriptRoot\_common.ps1"

# Resolve a possibly-relative path against a base directory.
function Resolve-RelPath {
    param([string]$Base, [string]$Path)
    if ([System.IO.Path]::IsPathRooted($Path)) { return $Path }
    return Join-Path $Base $Path
}

# Print a directory's normalized absolute path when it exists, else as-is.
function Get-NormDir {
    param([string]$Path)
    if (Test-Path -LiteralPath $Path -PathType Container) {
        return (Resolve-Path -LiteralPath $Path).Path
    }
    return $Path
}

# Human-readable size of a file, or "(absent)" when it does not exist.
function Get-FileSizeDisplay {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return '(absent)' }
    $bytes = (Get-Item -LiteralPath $Path).Length
    if ($bytes -ge 1GB) { return "{0:N1}G" -f ($bytes / 1GB) }
    if ($bytes -ge 1MB) { return "{0:N1}M" -f ($bytes / 1MB) }
    if ($bytes -ge 1KB) { return "{0:N1}K" -f ($bytes / 1KB) }
    return "${bytes}B"
}

# Delete a SQLite database and its WAL/SHM/journal sidecars.
function Remove-SqliteDb {
    param([string]$Path)
    foreach ($suffix in '', '-wal', '-shm', '-journal') {
        $p = "$Path$suffix"
        if (Test-Path -LiteralPath $p) { Remove-Item -LiteralPath $p -Force }
    }
}

function Invoke-HardReset {
    # Safety gate: a reachable scraper means a live worker may hold the DB open.
    $reachable = $true
    try {
        Invoke-ApiRequest -Method GET -Path '/status' | Out-Null
    } catch {
        $reachable = $false
    }
    if ($reachable) {
        Write-Error "error: the scraper is running at $BaseUrl."
        Write-Error "       Stop it (Ctrl-C scripts\windows\start-scraper.ps1) before a hard reset,"
        Write-Error "       otherwise the live worker can resurrect the wiped database."
        return 1
    }

    $outputDirSetting = if ($env:OUTPUT_DIR) { $env:OUTPUT_DIR } else { '../output' }
    $outputDir = Get-NormDir (Resolve-RelPath $ScraperDir $outputDirSetting)
    $dbPathSetting = if ($env:DB_PATH) { $env:DB_PATH } else { './scraper.db' }
    $scraperDb = Resolve-RelPath $ScraperDir $dbPathSetting
    $enricherDir = Join-Path $RepoRoot 'enricher-py'
    $enricherDbSetting = if ($env:ENRICHER_DB) { $env:ENRICHER_DB } else { './enricher.db' }
    $enricherDb = Resolve-RelPath $enricherDir $enricherDbSetting

    $tabCount = 0
    $fileCount = 0
    if (Test-Path -LiteralPath $outputDir -PathType Container) {
        $tabCount = (Get-ChildItem -LiteralPath $outputDir -Recurse -Filter 'metadata.json' -File -ErrorAction SilentlyContinue | Measure-Object).Count
        $fileCount = (Get-ChildItem -LiteralPath $outputDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
    }

    Write-Host "HARD RESET — this permanently deletes ALL tracked data:"
    Write-Host ""
    Write-Host "  output tree   $outputDir"
    Write-Host "                  $tabCount committed tab(s), $fileCount file(s)"
    Write-Host "  scraper DB    $scraperDb  ($(Get-FileSizeDisplay $scraperDb))"
    Write-Host "  enricher DB   $enricherDb  ($(Get-FileSizeDisplay $enricherDb))"
    Write-Host ""
    Write-Host "  The browser login session (camoufox-profile\) is kept."
    Write-Host "  This cannot be undone."
    Write-Host ""

    $reply = Read-Host "Type 'yes' to proceed"
    if ($reply -ne 'yes') {
        Write-Host "Aborted — nothing was deleted."
        return 1
    }

    if (Test-Path -LiteralPath $outputDir) { Remove-Item -LiteralPath $outputDir -Recurse -Force }
    New-Item -Path $outputDir -ItemType Directory -Force | Out-Null
    Remove-SqliteDb -Path $scraperDb
    Remove-SqliteDb -Path $enricherDb

    Write-Host "Done. Wiped the output tree and the scraper + enricher databases."
    return 0
}

if ($HardReset) {
    exit (Invoke-HardReset)
}

$resp = Invoke-ApiRequest -Method DELETE -Path '/jobs'
$parsed = $resp | ConvertFrom-Json
Write-Host "Canceled $($parsed.canceled) queued job(s). Any in-flight job finishes first."
