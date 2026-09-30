param([Parameter(Mandatory=$true)][string]$PythonPath,[Parameter(Mandatory=$true)][string]$GhPath,[string]$DataRoot='E:\metaculus_data')
$ErrorActionPreference='Stop'
$repositoryPath=Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runner=Join-Path $PSScriptRoot 'monitor_watchdog.ps1'
foreach ($required in @($PythonPath,$GhPath,$runner)) {
    if (-not (Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required executable/script missing: $required" }
}
$PythonPath=(Resolve-Path -LiteralPath $PythonPath).Path
$GhPath=(Resolve-Path -LiteralPath $GhPath).Path
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$DataRoot=(Resolve-Path -LiteralPath $DataRoot).Path
$argument='-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -PythonPath "{1}" -RepositoryPath "{2}" -GhPath "{3}" -DataRoot "{4}"' -f $runner,$PythonPath,$repositoryPath,$GhPath,$DataRoot
$shellPath=Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action=New-ScheduledTaskAction -Execute $shellPath -Argument $argument -WorkingDirectory $repositoryPath
$periodic=New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5)
$login=New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal=New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 4) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ForecastAgent Monitor Watchdog' -Action $action -Trigger @($periodic,$login) -Principal $principal -Settings $settings -Description 'Dispatch overdue GitHub snapshot polls; no local collection, forecasts or budget resets.' -Force | Select-Object TaskName,State
