param(
    [string]$AssettoCorsaPath = 'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ts3client_win64,ts3client_win32,acs,acs_x86 -ErrorAction SilentlyContinue) {
    throw 'Close TeamSpeak and the AC driving session before installing. No files were changed.'
}
$pluginSource = Join-Path $PSScriptRoot 'build\Release\assetto_corsa_notifier.dll'
$appSource = Join-Path $PSScriptRoot 'ac_app\GridTalk\GridTalk.py'
$pluginDirectory = Join-Path $env:APPDATA 'TS3Client\plugins'
$appDirectory = Join-Path $AssettoCorsaPath 'apps\python\GridTalk'
if (!(Test-Path -LiteralPath $pluginSource) -or !(Test-Path -LiteralPath $appSource)) {
    throw 'Build the plugin before installing.'
}
if (!(Test-Path -LiteralPath (Join-Path $AssettoCorsaPath 'acs.exe'))) {
    throw 'Assetto Corsa was not found. Pass -AssettoCorsaPath with its installation folder.'
}
$backup = Join-Path $PSScriptRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$pluginTarget = Join-Path $pluginDirectory 'assetto_corsa_notifier.dll'
$appTarget = Join-Path $appDirectory 'GridTalk.py'
if (Test-Path -LiteralPath $pluginTarget) {
    Copy-Item -LiteralPath $pluginTarget -Destination (Join-Path $backup 'assetto_corsa_notifier.dll')
}
if (Test-Path -LiteralPath $appTarget) {
    Copy-Item -LiteralPath $appTarget -Destination (Join-Path $backup 'GridTalk.py')
}
New-Item -ItemType Directory -Path $pluginDirectory,$appDirectory -Force | Out-Null
Copy-Item -LiteralPath $pluginSource -Destination $pluginTarget -Force
Copy-Item -LiteralPath $appSource -Destination $appTarget -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'ac_app\GridTalk\assets') -Destination $appDirectory -Recurse -Force
Write-Output "Installed plugin: $pluginTarget"
Write-Output "Installed AC app: $appTarget"
Write-Output "Previous files backed up: $backup"

# Retire the old installation after copying the new one. Keep it recoverable,
# outside the plugin/app discovery paths, to avoid duplicate senders/receivers.
$legacyPlugin = Join-Path $pluginDirectory 'ts_ac_udp.dll'
if (Test-Path -LiteralPath $legacyPlugin) {
    Move-Item -LiteralPath $legacyPlugin -Destination (Join-Path $backup 'ts_ac_udp.dll')
}
$legacyApp = Join-Path $AssettoCorsaPath 'apps\python\TSVoice'
$resolvedLegacy = [System.IO.Path]::GetFullPath($legacyApp)
$expectedLegacy = [System.IO.Path]::GetFullPath((Join-Path $AssettoCorsaPath 'apps\python\TSVoice'))
if ($resolvedLegacy -ne $expectedLegacy -or !(($resolvedLegacy + '\').StartsWith([System.IO.Path]::GetFullPath($AssettoCorsaPath).TrimEnd('\') + '\'))) {
    throw 'Legacy app migration path is outside Assetto Corsa.'
}
if (Test-Path -LiteralPath $resolvedLegacy) {
    Move-Item -LiteralPath $resolvedLegacy -Destination (Join-Path $backup 'TSVoice')
}
Write-Output 'Enable Assetto Corsa Notifier in TeamSpeak and GridTalk in AC Python app settings.'
