param(
    [Parameter(Mandatory=$true)][ValidatePattern('^ForecastAgent(?:\.[A-Za-z_][A-Za-z0-9_]*)*$')][string]$Module,
    [string[]]$PythonArguments = @(),
    [string]$Workspace,
    [ValidateSet('OPENROUTER', 'OPENROUTER2')][string]$OpenRouterSecret = 'OPENROUTER',
    [ValidateSet('TAVILY_KEY1', 'TAVILY_KEY2')][string]$TavilySecret = 'TAVILY_KEY1'
)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
if (!$Workspace) { $Workspace = $repoRoot }
$Workspace = (Resolve-Path -LiteralPath $Workspace).Path
if (!(Test-Path -LiteralPath (Join-Path $Workspace 'ForecastAgent\__init__.py'))) {
    throw 'Workspace must contain the ForecastAgent package.'
}
$pythonPath = Join-Path $repoRoot '.local\venv\Scripts\python.exe'
if (!(Test-Path -LiteralPath $pythonPath)) { throw 'Local Python environment is missing. See LOCAL_DEVELOPMENT.md.' }
$variableNames = @('OPENROUTER_API_KEY', 'TAVILY_API_KEY', 'EXA_API_KEY', 'EXA_API_KEY2', 'METACULUS_TOKEN', 'PYTHONUTF8', 'PYTHONPATH', 'PLAYWRIGHT_BROWSERS_PATH', 'FORECAST_MODEL', 'FORECAST_MODEL_FALLBACK_SUPER', 'FORECAST_EXA_FAILOVER_MODULE', 'FORECAST_EXA_TRANSPORT_ROOT')
$previous = @{}
foreach ($name in $variableNames) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process') }
$pushed = $false
$resultCode = 1
try {
    . (Join-Path $PSScriptRoot 'load_local_env.ps1') -OpenRouterSecret $OpenRouterSecret -TavilySecret $TavilySecret
    $env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repoRoot '.local\browsers'
    $env:PYTHONPATH = $Workspace
    $env:FORECAST_MODEL = 'nvidia/nemotron-3-super-120b-a12b:free'
    $env:FORECAST_MODEL_FALLBACK_SUPER = '0'
    Push-Location -LiteralPath $Workspace
    $pushed = $true
    & $pythonPath -m $Module @PythonArguments
    $resultCode = $LASTEXITCODE
} finally {
    if ($pushed) { Pop-Location }
    foreach ($name in $variableNames) { [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process') }
    $previous.Clear()
}
exit $resultCode
