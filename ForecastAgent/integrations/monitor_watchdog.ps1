param([string]$PythonPath,[string]$RepositoryPath,[string]$GhPath,[string]$DataRoot='E:\metaculus_data')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $RepositoryPath
$logRoot=Join-Path $DataRoot 'logs'
New-Item -ItemType Directory -Force -Path $logRoot | Out-Null
$logPath=Join-Path $logRoot 'monitor-watchdog.log'
if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 10485760) {
    Move-Item -LiteralPath $logPath -Destination (Join-Path $logRoot ('monitor-watchdog-'+(Get-Date -Format 'yyyyMMdd-HHmmss')+'.log'))
}
try {
    "$(Get-Date -Format o) Watchdog started" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    & $PythonPath -m ForecastAgent.monitor_watchdog --root (Join-Path $DataRoot 'monitor-watchdog') --gh-path $GhPath --apply 2>&1 | Out-File -LiteralPath $logPath -Append -Encoding utf8
    $taskExit=$LASTEXITCODE
    if ($taskExit -ne 0) { throw "Watchdog exited with code $taskExit" }
} catch {
    "$(Get-Date -Format o) $($_.Exception.Message)" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    exit 1
}
exit 0
