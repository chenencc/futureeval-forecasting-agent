param(
    [Parameter(Mandatory=$true)][string]$PythonPath,
    [Parameter(Mandatory=$true)][string]$GhPath,
    [string]$DataRoot = 'E:\metaculus_data'
)
$ErrorActionPreference = 'Stop'
$repositoryPath = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$runnerPath = Join-Path $PSScriptRoot 'local_sync.ps1'
foreach ($requiredPath in @($PythonPath, $GhPath, $runnerPath)) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) { throw "Required executable/script missing: $requiredPath" }
}
New-Item -ItemType Directory -Force -Path $DataRoot | Out-Null
$argument = '-NoProfile -NonInteractive -WindowStyle Hidden -File "{0}" -PythonPath "{1}" -RepositoryPath "{2}" -DataRoot "{3}" -GhPath "{4}"' -f $runnerPath,$PythonPath,$repositoryPath,$DataRoot,$GhPath
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument $argument -WorkingDirectory $repositoryPath
$periodic = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(2) -RepetitionInterval (New-TimeSpan -Minutes 15)
$login = New-ScheduledTaskTrigger -AtLogOn -User ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name)
$principal = New-ScheduledTaskPrincipal -UserId ([System.Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName 'ForecastAgent Local Data Sync' -Action $action -Trigger @($periodic,$login) -Principal $principal -Settings $settings -Description 'Read Actions artifacts and maintain the local ForecastAgent data store; no acquisition or prediction submission.' -Force | Select-Object TaskName,State
