param(
    [ValidateSet('OPENROUTER', 'OPENROUTER2')][string]$OpenRouterSecret = 'OPENROUTER',
    [ValidateSet('TAVILY_KEY1', 'TAVILY_KEY2')][string]$TavilySecret = 'TAVILY_KEY1'
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$configPath = Join-Path $repoRoot '.local\credentials.clixml'
if (!(Test-Path -LiteralPath $configPath)) { throw 'Run configure_local_keys.ps1 first.' }
$credentials = Import-Clixml -LiteralPath $configPath
if ($credentials -isnot [System.Collections.IDictionary]) { throw 'Invalid local credential file.' }
$mapping = @{
    OPENROUTER_API_KEY = $(if ($OpenRouterSecret -eq 'OPENROUTER2') { 'OPENROUTER2' } else { 'OPENROUTER_API_KEY' });
    TAVILY_API_KEY = $(if ($TavilySecret -eq 'TAVILY_KEY2') { 'TAVILY_KEY2' } else { 'TAVILY_API_KEY' });
    EXA_API_KEY = 'EXA_API_KEY'; METACULUS_TOKEN = 'METACULUS_TOKEN'
}
if ($credentials['EXA_API_KEY2'] -is [System.Security.SecureString] -and $credentials['EXA_API_KEY2'].Length -gt 0) {
    $mapping['EXA_API_KEY2'] = 'EXA_API_KEY2'
} else {
    [Environment]::SetEnvironmentVariable('EXA_API_KEY2', $null, 'Process')
}
foreach ($name in $mapping.Keys) {
    $value = $credentials[$mapping[$name]]
    if ($value -isnot [System.Security.SecureString] -or $value.Length -eq 0) {
        throw ('Missing selected local credential: ' + $mapping[$name])
    }
}
foreach ($name in $mapping.Keys) {
    $value = $credentials[$mapping[$name]]
    $pointer = [System.Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try {
        [System.Environment]::SetEnvironmentVariable(
            $name, [System.Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer), 'Process')
    } finally {
        [System.Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
    }
}
[System.Environment]::SetEnvironmentVariable('PYTHONUTF8', '1', 'Process')
[System.Environment]::SetEnvironmentVariable('PYTHONPATH', $repoRoot, 'Process')
$transportModule = Join-Path $repoRoot 'ForecastAgent\infrastructure\exa_failover.py'
$localPython = Join-Path $repoRoot '.local\venv\Scripts\python.exe'
if (Test-Path -LiteralPath $localPython) {
    if (!(Test-Path -LiteralPath $transportModule)) { throw 'Exa failover infrastructure is missing.' }
    & $localPython $transportModule --setup
    if ($LASTEXITCODE -ne 0) { throw 'Exa failover bootstrap setup failed.' }
    $env:FORECAST_EXA_FAILOVER_MODULE = $transportModule
    $env:FORECAST_EXA_TRANSPORT_ROOT = Join-Path $repoRoot '.local\exa-transport'
}
$exaNames = if ($mapping.ContainsKey('EXA_API_KEY2')) { 'EXA_API + EXA_API2 (credit failover)' } else { 'EXA_API' }
Write-Host ('Local credentials loaded into this process: ' + $OpenRouterSecret + ', ' + $TavilySecret + ', ' + $exaNames + ', METACULUS_TOKEN. Values are not displayed.')
