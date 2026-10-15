param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$GhPath,
    [string]$DataRoot = 'E:\metaculus_data\prediction_markets\store'
)
$ErrorActionPreference = 'Stop'
$repositoryPath = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runnerPath = Join-Path $PSScriptRoot 'market_store_sync.ps1'
$hiddenPath = Join-Path $PSScriptRoot 'run_hidden.vbs'
foreach ($requiredPath in @($PythonPath, $GhPath, $runnerPath, $hiddenPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required executable/script missing: $requiredPath" }
}
$powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$wscriptPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$argument = '//B //NoLogo "{0}" "{1}" -NoProfile -NonInteractive -WindowStyle Hidden -File "{2}" -PythonPath "{3}" -RepositoryPath "{4}" -DataRoot "{5}" -GhPath "{6}"' -f $hiddenPath,$powershellPath,$runnerPath,$PythonPath,$repositoryPath,$DataRoot,$GhPath
$action = New-ScheduledTaskAction -Execute $wscriptPath -Argument $argument -WorkingDirectory $repositoryPath
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15)
$login = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ForecastAgent Market Data Sync' -Action $action -Trigger @($periodic,$login) -Principal $principal -Settings $settings -Description 'Read-only normalized market database and public historical-price mirror; no models, searches, trades or submissions.' -Force | Select-Object TaskName,State
