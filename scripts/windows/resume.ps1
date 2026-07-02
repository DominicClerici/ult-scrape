# Resume the worker (POST /resume).

. "$PSScriptRoot\_common.ps1"

Invoke-ApiRequest -Method POST -Path '/resume' | Write-PrettyJson
Write-Host "Worker resumed."
