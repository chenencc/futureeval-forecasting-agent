param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$RepositoryPath,
    [string]$DataRoot = 'E:\metaculus_data\prediction_markets\store',
    [Parameter(Mandatory=$true)][string]$GhPath
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $RepositoryPath
$env:PYTHONPATH = $RepositoryPath
$env:FORECAST_GH_PATH = $GhPath
$env:GH_PROMPT_DISABLED = '1'
$env:GH_NO_UPDATE_NOTIFIER = '1'
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$logPath = Join-Path $DataRoot 'sync.log'
if ((Test-Path -LiteralPath $logPath) -and (Get-Item -LiteralPath $logPath).Length -gt 5242880) {
    Move-Item -LiteralPath $logPath -Destination (Join-Path $DataRoot ('sync-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.log'))
}
try {
    "$(Get-Date -Format o) Market store sync started" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    # Windows PowerShell must drain stderr before checking the native exit code.
    $ErrorActionPreference = 'Continue'
    & $PythonPath -X utf8 -m ForecastAgent.market_store.cli sync --root $DataRoot 2>&1 | Out-File -LiteralPath $logPath -Append -Encoding utf8
    $syncExitCode = $LASTEXITCODE
    $ErrorActionPreference = 'Stop'
    if ($syncExitCode -ne 0) { throw 'Market store synchronization failed; inspect latest-sync.json.' }
} catch {
    "$(Get-Date -Format o) $($_.Exception.Message)" | Out-File -LiteralPath $logPath -Append -Encoding utf8
    exit 1
}
