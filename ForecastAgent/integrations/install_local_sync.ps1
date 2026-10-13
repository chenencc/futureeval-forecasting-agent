param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$GhPath,
    [string]$DataRoot = 'E:\metaculus_data'
)
$ErrorActionPreference = 'Stop'
$repositoryPath = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runnerPath = Join-Path $PSScriptRoot 'local_sync.ps1'
$hiddenPath = Join-Path $PSScriptRoot 'run_hidden.vbs'
foreach ($requiredPath in @($PythonPath, $GhPath, $runnerPath, $hiddenPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required executable/script missing: $requiredPath" }
}
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$powershellPath = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$wscriptPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$argument = '//B //NoLogo "{0}" "{1}" -NoProfile -NonInteractive -WindowStyle Hidden -File "{2}" -PythonPath "{3}" -RepositoryPath "{4}" -DataRoot "{5}" -GhPath "{6}"' -f $hiddenPath,$powershellPath,$runnerPath,$PythonPath,$repositoryPath,$DataRoot,$GhPath
$action = New-ScheduledTaskAction -Execute $wscriptPath -Argument $argument -WorkingDirectory $repositoryPath
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15)
$login = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ForecastAgent Local Data Sync' -Action $action -Trigger @($periodic,$login) -Principal $principal -Settings $settings -Description 'Incremental artifact files and lightweight catalogs with persistent traffic caps; no acquisition or prediction submission.' -Force | Select-Object TaskName,State
