# Shared helpers for the ult-scrape Windows scripts. Dot-source this; don't run
# it directly.
#
# Resolves paths, loads scraper-py/.env, and exposes:
#   $ScriptsDir $RepoRoot $ScraperDir $EnvFile   - locations
#   $ApiHost $ApiPort $ApiKey                    - from .env (or the environment)
#   $BaseUrl                                     - where the API is reachable
#   Import-DotEnv <file>                         - load KEY=VALUE pairs (env wins)
#   Invoke-ApiRequest -Method -Path [-Body]      - call the API, return the body
#   Write-PrettyJson                             - pretty-print JSON from the pipeline
#
# Every value can be overridden from the environment, e.g.
#   $env:SCRAPER_URL = 'http://host:9000'; .\status.ps1

$ErrorActionPreference = 'Stop'

$Script:CommonDir = $PSScriptRoot
$Script:ScriptsDir = Split-Path -Parent $CommonDir
$Script:RepoRoot = Split-Path -Parent $ScriptsDir
$Script:ScraperDir = Join-Path $RepoRoot 'scraper-py'
$Script:EnvFile = Join-Path $ScraperDir '.env'

# Read KEY=VALUE lines from a .env file and set them as process env vars, but
# never clobber a value already set in the environment (so callers can
# override anything).
function Import-DotEnv {
    param([Parameter(Mandatory)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { return }
    foreach ($rawLine in Get-Content -LiteralPath $Path) {
        $line = $rawLine.TrimEnd("`r")
        $trimmed = $line.Trim()
        if ($trimmed -eq '' -or $trimmed.StartsWith('#')) { continue }
        if ($line -notmatch '=') { continue }

        $idx = $line.IndexOf('=')
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim()
        if ($val.Length -ge 2 -and $val.StartsWith('"') -and $val.EndsWith('"')) {
            $val = $val.Substring(1, $val.Length - 2)
        } elseif ($val.Length -ge 2 -and $val.StartsWith("'") -and $val.EndsWith("'")) {
            $val = $val.Substring(1, $val.Length - 2)
        }

        if (-not (Test-Path -LiteralPath "Env:$key")) {
            Set-Item -Path "Env:$key" -Value $val
        }
    }
}

Import-DotEnv -Path $EnvFile

$Script:ApiHost = if ($env:API_HOST) { $env:API_HOST } else { '127.0.0.1' }
$Script:ApiPort = if ($env:API_PORT) { $env:API_PORT } else { '8000' }
$Script:ApiKey = if ($env:API_KEY) { $env:API_KEY } else { '' }

# 0.0.0.0 is a bind address, not a connect address — talk to localhost instead.
$Script:_clientHost = $ApiHost
if ($_clientHost -eq '0.0.0.0') { $Script:_clientHost = '127.0.0.1' }
$Script:BaseUrl = if ($env:SCRAPER_URL) { $env:SCRAPER_URL } else { "http://${_clientHost}:$ApiPort" }

# Invoke-ApiRequest -Method <verb> -Path </path> [-Body <json>]
# Returns the response body as a string. Throws a terminating error (with a
# helpful message) on a connection failure or any HTTP status >= 400.
function Invoke-ApiRequest {
    param(
        [Parameter(Mandatory)][string]$Method,
        [Parameter(Mandatory)][string]$Path,
        [string]$Body
    )

    $headers = @{}
    if ($Script:ApiKey) { $headers['X-API-Key'] = $Script:ApiKey }
    $uri = "$Script:BaseUrl$Path"

    $params = @{
        Method         = $Method
        Uri            = $uri
        Headers        = $headers
        UseBasicParsing = $true
    }
    if ($PSBoundParameters.ContainsKey('Body')) {
        $params.Body = $Body
        $params.ContentType = 'application/json'
    }

    try {
        $resp = Invoke-WebRequest @params
        return $resp.Content
    } catch {
        $ex = $_.Exception
        if ($ex.Response) {
            $statusCode = [int]$ex.Response.StatusCode
            $body = ''
            try {
                $stream = $ex.Response.GetResponseStream()
                $reader = New-Object System.IO.StreamReader($stream)
                $body = $reader.ReadToEnd()
            } catch {}
            $msg = "error: $Method $Path returned HTTP $statusCode"
            if ($body) { $msg = "$msg`n$body" }
            throw $msg
        }
        throw "error: could not reach the scraper at $Script:BaseUrl — is it running? (scripts\windows\start-scraper.ps1)"
    }
}

# Pretty-print JSON text from the pipeline; passes non-JSON text through as-is.
function Write-PrettyJson {
    param([Parameter(ValueFromPipeline)]$InputObject)
    process {
        if ([string]::IsNullOrEmpty($InputObject)) { return }
        try {
            ($InputObject | ConvertFrom-Json) | ConvertTo-Json -Depth 20
        } catch {
            $InputObject
        }
    }
}
