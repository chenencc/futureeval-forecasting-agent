param([switch]$StatusOnly, [switch]$PrepareOnly)

$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$configDirectory = Join-Path $repoRoot '.local'
$configPath = Join-Path $configDirectory 'credentials.clixml'
$entries = @(
    @{ Secret = 'OPENROUTER'; Variable = 'OPENROUTER_API_KEY' },
    @{ Secret = 'OPENROUTER2'; Variable = 'OPENROUTER2' },
    @{ Secret = 'TAVILY_KEY1'; Variable = 'TAVILY_API_KEY' },
    @{ Secret = 'TAVILY_KEY2'; Variable = 'TAVILY_KEY2' },
    @{ Secret = 'EXA_API'; Variable = 'EXA_API_KEY' },
    @{ Secret = 'EXA_API2'; Variable = 'EXA_API_KEY2' },
    @{ Secret = 'METACULUS_TOKEN'; Variable = 'METACULUS_TOKEN' }
)
$credentials = @{}
if (Test-Path -LiteralPath $configPath) {
    $credentials = Import-Clixml -LiteralPath $configPath
    if ($credentials -isnot [System.Collections.IDictionary]) { throw 'Invalid local credential file.' }
}
if ($StatusOnly) {
    foreach ($entry in $entries) {
        $value = $credentials[$entry.Variable]
        $configured = $value -is [System.Security.SecureString] -and $value.Length -gt 0
        Write-Output ($entry.Variable + ': ' + $(if ($configured) { 'configured' } else { 'missing' }))
    }
    exit 0
}

# Restrict local storage before prompting. Export-Clixml encrypts SecureString
# values with Windows DPAPI, bound to this Windows account and computer.
[void](New-Item -ItemType Directory -Path $configDirectory -Force)
$userSid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User
$systemSid = [System.Security.Principal.SecurityIdentifier]::new('S-1-5-18')
$acl = [System.Security.AccessControl.DirectorySecurity]::new()
$acl.SetAccessRuleProtection($true, $false)
$acl.SetOwner($userSid)
foreach ($sid in @($userSid, $systemSid)) {
    $rule = [System.Security.AccessControl.FileSystemAccessRule]::new(
        $sid, 'FullControl', 'ContainerInherit,ObjectInherit', 'None', 'Allow')
    [void]$acl.AddAccessRule($rule)
}
Set-Acl -LiteralPath $configDirectory -AclObject $acl
if ($PrepareOnly) {
    Write-Output 'Local credential directory permissions prepared.'
    exit 0
}
Write-Host 'Configure the Actions keys locally. Do not paste keys into chat.'
Write-Host 'Input is hidden. Each key is encrypted and saved immediately.'
Write-Host 'Existing configured keys are retained. Empty input skips an item.'
foreach ($entry in $entries) {
    $existing = $credentials[$entry.Variable]
    if ($existing -is [System.Security.SecureString] -and $existing.Length -gt 0) {
        Write-Host ($entry.Variable + ': already configured; retained.')
        continue
    }
    $value = Read-Host ($entry.Secret + ' -> ' + $entry.Variable) -AsSecureString
    if ($value.Length -eq 0) {
        Write-Host ($entry.Variable + ': skipped.')
        continue
    }
    $credentials[$entry.Variable] = $value
    $temporary = Join-Path $configDirectory ('credentials-' + [guid]::NewGuid().ToString('N') + '.clixml')
    try {
        $credentials | Export-Clixml -LiteralPath $temporary -Encoding UTF8
        Move-Item -LiteralPath $temporary -Destination $configPath -Force
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
    Write-Host ($entry.Variable + ': saved (encrypted).')
}
Write-Host 'Configuration finished. This file works only for this Windows account on this computer.'
Write-Host ('Saved to: ' + $configPath)
[void](Read-Host 'Press Enter to close')
