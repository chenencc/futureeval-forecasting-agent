param(
    [string]$ConfigRoot = 'D:\metaculus\.local',
    [switch]$LoadOnly
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Security\Microsoft.PowerShell.Security.psd1') -Force
$configPath = Join-Path $ConfigRoot 'congress-api-key.clixml'
$statusPath = Join-Path $ConfigRoot 'congress-configuration-status.json'
if ($LoadOnly) {
    $value = Import-Clixml -LiteralPath $configPath
    if ($value -isnot [System.Security.SecureString]) { throw 'Invalid Congress configuration.' }
    $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($value)
    try {
        $env:CONGRESS_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    } finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer) }
    return
}
if (!(Test-Path -LiteralPath $ConfigRoot)) { throw 'Prepare the existing local configuration directory first.' }
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class CongressConfigurationWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr handle, int command);
    [DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr handle);
}
'@
[System.Windows.Forms.Application]::EnableVisualStyles()
$form = New-Object System.Windows.Forms.Form
$form.Text = 'ForecastAgent - CONGRESS_API_KEY'
$form.ClientSize = New-Object System.Drawing.Size(620, 235)
$form.StartPosition = 'CenterScreen'
$form.FormBorderStyle = 'FixedDialog'
$form.MaximizeBox = $false
$form.TopMost = $true
$form.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$label = New-Object System.Windows.Forms.Label
$label.SetBounds(20, 15, 580, 70)
$label.Text = "Enter your Congress.gov API key.`r`nPaste only the key, without quotes or brackets.`r`nSaved locally; not uploaded to GitHub."
$inputBox = New-Object System.Windows.Forms.TextBox
$inputBox.SetBounds(20, 95, 580, 30)
$inputBox.UseSystemPasswordChar = $true
$note = New-Object System.Windows.Forms.Label
$note.SetBounds(20, 135, 580, 35)
$note.Text = 'Saved locally with Windows encryption. No value is sent to chat or GitHub.'
$save = New-Object System.Windows.Forms.Button
$save.Text = 'Save'
$save.SetBounds(365, 185, 110, 32)
$cancel = New-Object System.Windows.Forms.Button
$cancel.Text = 'Cancel'
$cancel.SetBounds(490, 185, 110, 32)
$cancel.DialogResult = 'Cancel'
$form.AcceptButton = $save
$form.CancelButton = $cancel
$save.Add_Click({
    $text = $inputBox.Text.Trim()
    if ([string]::IsNullOrWhiteSpace($text) -or $text.Length -gt 512 -or $text -match '\s') {
        [void][System.Windows.Forms.MessageBox]::Show($form, 'Enter only the API key, without whitespace.', 'Invalid configuration')
        return
    }
    $temporary = Join-Path $ConfigRoot ('congress-' + [guid]::NewGuid().ToString('N') + '.clixml')
    try {
        ConvertTo-SecureString $text -AsPlainText -Force | Export-Clixml -LiteralPath $temporary
        Move-Item -LiteralPath $temporary -Destination $configPath -Force
        @{state='saved'; configured_name='CONGRESS_API_KEY'; updated_at_utc=[DateTime]::UtcNow.ToString('o')} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
        $inputBox.Clear()
        $form.DialogResult = 'OK'
        $form.Close()
    } catch {
        [void][System.Windows.Forms.MessageBox]::Show($form, 'Local save failed. No value was logged.', 'Save failed')
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
    }
})
$form.Controls.AddRange(@($label, $inputBox, $note, $save, $cancel))
$form.Add_Shown({
    @{state='prompt_open'; configured_name='CONGRESS_API_KEY'; process_id=$PID} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
    $form.WindowState = 'Normal'
    [void][CongressConfigurationWindow]::ShowWindow($form.Handle, 5)
    $form.BringToFront()
    $form.Activate()
    [void][CongressConfigurationWindow]::SetForegroundWindow($form.Handle)
    [void]$inputBox.Focus()
})
$result = $form.ShowDialog()
if ($result -ne 'OK') {
    @{state='cancelled'; configured_name='CONGRESS_API_KEY'} | ConvertTo-Json | Set-Content -LiteralPath $statusPath -Encoding UTF8
}
$form.Dispose()

