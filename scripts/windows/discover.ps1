# Start a Pro-tab discovery run (POST /discover).
#
# Discovery crawls UG's explore listing and records discovered tabs in
# tab_metadata. It does NOT enqueue or scrape anything — run .\discover.ps1
# -Enqueue afterwards to turn discovered tabs into scrape jobs.
#
# Usage:
#   .\discover.ps1                 start a run with the server defaults
#   .\discover.ps1 -Max 50         stop the run once 50 distinct tabs are found
#   .\discover.ps1 -ListRuns       list recent discovery runs and their progress
#   .\discover.ps1 -Enqueue        enqueue all discovered-but-unscraped tabs
#
# Notes:
#   - A run is accepted only when the scrape queue is empty and no other run is
#     active (the server returns 409 otherwise).
#   - The run is worker-owned and asynchronous: this script kicks it off and
#     prints the run id; watch progress with .\discover.ps1 -ListRuns.
param(
    [int]$Max = 0,
    [switch]$ListRuns,
    [switch]$Enqueue
)

. "$PSScriptRoot\_common.ps1"

if ($ListRuns) {
    Invoke-ApiRequest -Method GET -Path '/discover' | Write-PrettyJson
    exit 0
}

if ($Enqueue) {
    $resp = Invoke-ApiRequest -Method POST -Path '/discover/enqueue'
    $parsed = $resp | ConvertFrom-Json
    $created = ($parsed | Measure-Object).Count
    Write-Host "Enqueued $created discovered tab(s) as scrape job(s)."
    foreach ($job in $parsed) {
        Write-Host "  $($job.status)`t$($job.tab_id)`t$($job.id)"
    }
    exit 0
}

if ($Max -lt 0) {
    Write-Error "error: -Max must be a positive integer (got: $Max)"
    exit 1
}

$payload = '{}'
if ($Max -gt 0) {
    $payload = (@{ target_cap = $Max } | ConvertTo-Json)
}

$resp = Invoke-ApiRequest -Method POST -Path '/discover' -Body $payload
$parsed = $resp | ConvertFrom-Json
$runId = $parsed.id

if ($Max -gt 0) {
    Write-Host "Started discovery run $runId (stopping after $Max distinct tab(s))."
} else {
    Write-Host "Started discovery run $runId."
}
Write-Host "Watch progress:  .\discover.ps1 -ListRuns"
Write-Host "Enqueue results: .\discover.ps1 -Enqueue"
