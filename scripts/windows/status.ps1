# Print the scraper's current status (GET /status).

. "$PSScriptRoot\_common.ps1"

Invoke-ApiRequest -Method GET -Path '/status' | Write-PrettyJson
