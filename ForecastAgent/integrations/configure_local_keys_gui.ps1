param(
    [switch]$ReconfigureAll,
    [ValidateSet('OPENROUTER','OPENROUTER2','TAVILY_KEY1','TAVILY_KEY2','EXA_API','EXA_API2','METACULUS_TOKEN')]
    [string[]]$OnlySecrets
)
$ErrorActionPreference = 'Stop'
# Load the Windows PowerShell module explicitly when launched from PowerShell 7.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force
& (Join-Path $PSScriptRoot 'configure_local_keys.ps1') -PrepareOnly
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()
$repoRoot = Split-Path (Split-Path $PSScriptRoot -Parent) -Parent
$configDirectory = Join-Path $repoRoot '.local'
$configPath = Join-Path $configDirectory 'credentials.clixml'
$credentials = @{}
if (Test-Path -LiteralPath $configPath) {
    $credentials = Import-Clixml -LiteralPath $configPath
    if ($credentials -isnot [System.Collections.IDictionary]) { throw 'Invalid local credential file.' }
}
$entries = @(
    @{ Secret = 'OPENROUTER'; Variable = 'OPENROUTER_API_KEY' },
    @{ Secret = 'OPENROUTER2'; Variable = 'OPENROUTER2' },
    @{ Secret = 'TAVILY_KEY1'; Variable = 'TAVILY_API_KEY' },
    @{ Secret = 'TAVILY_KEY2'; Variable = 'TAVILY_KEY2' },
    @{ Secret = 'EXA_API'; Variable = 'EXA_API_KEY' },
    @{ Secret = 'EXA_API2'; Variable = 'EXA_API_KEY2' },
    @{ Secret = 'METACULUS_TOKEN'; Variable = 'METACULUS_TOKEN' }
)
if ($OnlySecrets) { $entries = @($entries | Where-Object { $_.Secret -in $OnlySecrets }) }
$statusPath = Join-Path $configDirectory 'configuration-status.json'
function Write-ConfigurationStatus([string]$state, [string]$currentSecret) {
    $configured = @($entries | Where-Object {
        $value = $credentials[$_.Variable]
        $value -is [System.Security.SecureString] -and $value.Length -gt 0
    } | ForEach-Object { $_.Secret })
    @{ state = $state; current_secret = $currentSecret; configured_names = $configured;
       configured_count = $configured.Count; requested_count = $entries.Count;
       updated_at_utc = [DateTime]::UtcNow.ToString('o'); process_id = $PID } |
        ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
$cancelled = $false
for ($index = 0; $index -lt $entries.Count; $index++) {
    $entry = $entries[$index]
    $existing = $credentials[$entry.Variable]
    if (!$ReconfigureAll -and $existing -is [System.Security.SecureString] -and $existing.Length -gt 0) { continue }
    $form = New-Object System.Windows.Forms.Form
    $form.Text = 'ForecastAgent - ' + $entry.Secret + ' (' + ($index + 1) + '/' + $entries.Count + ')'
    $form.ClientSize = New-Object System.Drawing.Size(560, 220)
    $form.StartPosition = 'CenterScreen'
    $form.FormBorderStyle = 'FixedDialog'
    $form.MaximizeBox = $false
    $form.MinimizeBox = $false
    $form.TopMost = $true
    $form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
    $label = New-Object System.Windows.Forms.Label
    $label.Location = New-Object System.Drawing.Point(20, 18)
    $label.Size = New-Object System.Drawing.Size(520, 48)
    $label.Text = 'Enter ' + $entry.Secret + "`r`nLocal variable: " + $entry.Variable
    $inputBox = New-Object System.Windows.Forms.TextBox
    $inputBox.Location = New-Object System.Drawing.Point(20, 76)
    $inputBox.Size = New-Object System.Drawing.Size(520, 30)
    $inputBox.UseSystemPasswordChar = $true
    $note = New-Object System.Windows.Forms.Label
    $note.Location = New-Object System.Drawing.Point(20, 118)
    $note.Size = New-Object System.Drawing.Size(520, 40)
    $note.Text = 'Encrypted on this computer. Never sent to chat or GitHub.'
    $saveButton = New-Object System.Windows.Forms.Button
    $saveButton.Location = New-Object System.Drawing.Point(300, 170)
    $saveButton.Size = New-Object System.Drawing.Size(120, 32)
    $saveButton.Text = 'Save and next'
    $cancelButton = New-Object System.Windows.Forms.Button
    $cancelButton.Location = New-Object System.Drawing.Point(430, 170)
    $cancelButton.Size = New-Object System.Drawing.Size(110, 32)
    $cancelButton.Text = 'Cancel'
    $cancelButton.DialogResult = 'Cancel'
    $form.AcceptButton = $saveButton
    $form.CancelButton = $cancelButton
    $saveButton.Add_Click({
        if ([string]::IsNullOrWhiteSpace($inputBox.Text)) {
            [void][System.Windows.Forms.MessageBox]::Show($form, 'Enter the key before saving.', 'Missing key')
            return
        }
        $temporary = Join-Path $configDirectory ('credentials-' + [guid]::NewGuid().ToString('N') + '.clixml')
        try {
            $credentials[$entry.Variable] = ConvertTo-SecureString $inputBox.Text.Trim() -AsPlainText -Force
            $credentials | Export-Clixml -LiteralPath $temporary -Encoding UTF8
            Move-Item -LiteralPath $temporary -Destination $configPath -Force
            $inputBox.Clear()
            Write-ConfigurationStatus 'saved' $entry.Secret
            $form.DialogResult = 'OK'
            $form.Close()
        } catch {
            if ($null -ne $existing) { $credentials[$entry.Variable] = $existing }
            else { $credentials.Remove($entry.Variable) }
            [void][System.Windows.Forms.MessageBox]::Show($form,
                'Saving failed. The key was not logged. Please retry or cancel.', 'Local configuration error')
        } finally {
            if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        }
    })
    $form.Controls.AddRange(@($label, $inputBox, $note, $saveButton, $cancelButton))
    $form.Add_Shown({
        $form.WindowState = 'Normal'
        $form.BringToFront()
        $form.Activate()
        [void]$inputBox.Focus()
        Write-ConfigurationStatus 'prompt_open' $entry.Secret
        Write-Output ('Input dialog shown: ' + $entry.Secret + '; visible=' + $form.Visible)
    })
    Write-Output ('Opening input dialog: ' + $entry.Secret)
    $result = $form.ShowDialog()
    $form.Dispose()
    if ($result -ne 'OK') { $cancelled = $true; Write-ConfigurationStatus 'cancelled' $entry.Secret; break }
}
if (!$cancelled) {
    Write-ConfigurationStatus 'completed' ''
    [void][System.Windows.Forms.MessageBox]::Show(
        'All requested keys are saved locally (encrypted). No providers were called.',
        'ForecastAgent configuration complete')
}
