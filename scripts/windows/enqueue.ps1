# Enqueue tabs from a CSV file via POST /jobs/bulk.
#
# Usage: .\enqueue.ps1 [CsvFile]   (default: scripts\tabs.csv)
#
# CSV columns (comma-separated):  url_or_route[,priority[,force]]
#   url_or_route  full UG tab URL or a bare "artist/song-slug" route (required)
#   priority      integer, lower runs sooner (default 0)
#   force         true|false — re-scrape even if already succeeded (default false)
#
# Blank lines and lines starting with '#' are skipped. A header row whose first
# cell is "url_or_route" or "url" is ignored. See scripts\tabs.csv.example.
param(
    [string]$CsvFile
)

. "$PSScriptRoot\_common.ps1"

if (-not $CsvFile) { $CsvFile = Join-Path $ScriptsDir 'tabs.csv' }

if (-not (Test-Path -LiteralPath $CsvFile -PathType Leaf)) {
    Write-Error "error: CSV file not found: $CsvFile"
    Write-Error "       Copy scripts\tabs.csv.example to scripts\tabs.csv and edit it, or pass a path."
    exit 1
}

function ConvertTo-NormalizedBool {
    param([string]$Value)
    switch (($Value -replace '\s', '').ToLowerInvariant()) {
        'true'  { return $true }
        '1'     { return $true }
        'yes'   { return $true }
        'y'     { return $true }
        'force' { return $true }
        default { return $false }
    }
}

$items = @()
$submitted = 0

foreach ($rawLine in Get-Content -LiteralPath $CsvFile) {
    $line = $rawLine.TrimEnd("`r")
    $trimmed = $line.Trim()
    if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }

    $parts = $line -split ',', 4
    $url = $parts[0].Trim()
    $prio = if ($parts.Count -gt 1) { $parts[1].Trim() } else { '' }
    $force = if ($parts.Count -gt 2) { $parts[2].Trim() } else { '' }

    # Skip a header row.
    if ($url.ToLowerInvariant() -eq 'url_or_route' -or $url.ToLowerInvariant() -eq 'url') { continue }
    if ($url -eq '') { continue }

    $prioVal = 0
    if ($prio -match '^-?\d+$') { $prioVal = [int]$prio }

    $forceVal = ConvertTo-NormalizedBool $force

    $items += [PSCustomObject]@{
        url_or_route = $url
        priority     = $prioVal
        force        = $forceVal
    }
    $submitted++
}

if ($submitted -eq 0) {
    Write-Error "No tabs found in $CsvFile — nothing to enqueue."
    exit 1
}

$payload = @{ items = $items } | ConvertTo-Json -Depth 10

Write-Host "Enqueueing $submitted tab(s) from $CsvFile ..."
$resp = Invoke-ApiRequest -Method POST -Path '/jobs/bulk' -Body $payload

$parsed = $resp | ConvertFrom-Json
$created = ($parsed | Measure-Object).Count
Write-Host "Server accepted $created job(s) (some may be deduped or skipped for unparseable routes)."
foreach ($job in $parsed) {
    Write-Host "  $($job.status)`t$($job.tab_id)`t$($job.id)"
}
