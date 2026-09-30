param(
    [string]$PythonPath,
    [string]$RepositoryPath,
    [string]$DataRoot = 'E:\metaculus_data',
    [string]$GhPath
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepositoryPath
New-Item -ItemType Directory -Force -Path (Join-Path $DataRoot 'logs') | Out-Null
$logPath = Join-Path $DataRoot 'logs\sync.log'
if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 10485760) {
    Move-Item -LiteralPath $logPath -Destination (Join-Path $DataRoot ('logs\sync-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log'))
}
$env:FORECAST_GH_PATH = $GhPath
$env:GH_PROMPT_DISABLED = '1'
$env:GH_NO_UPDATE_NOTIFIER = '1'
try {
    "$(Get-Date -Format o) Sync started" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    & $PythonPath -m ForecastAgent.local_sync sync --root $DataRoot 2>&1 | Out-File -LiteralPath $logPath -Append -Encoding utf8
    if ($LASTEXITCODE -ne 0) { throw 'Local synchronization failed; inspect the database sync events.' }
} catch {
    "$(Get-Date -Format o) $($_.Exception.Message)" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    exit 1
}
