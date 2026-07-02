# Pause the worker after the current job finishes (POST /pause).

. "$PSScriptRoot\_common.ps1"

Invoke-ApiRequest -Method POST -Path '/pause' | Write-PrettyJson
Write-Host "Worker paused — the in-flight job (if any) finishes first."
