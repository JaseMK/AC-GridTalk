param(
    [string]$AssettoCorsaPath = 'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ts3client_win64,ts3client_win32,acs,acs_x86 -ErrorAction SilentlyContinue) {
    throw 'Close TeamSpeak and the AC driving session before installing. No files were changed.'
}
$pluginSource = Join-Path $PSScriptRoot 'build\Release\ts_ac_udp.dll'
$appSource = Join-Path $PSScriptRoot 'ac_app\TSVoice\TSVoice.py'
$pluginDirectory = Join-Path $env:APPDATA 'TS3Client\plugins'
$appDirectory = Join-Path $AssettoCorsaPath 'apps\python\TSVoice'
if (!(Test-Path -LiteralPath $pluginSource) -or !(Test-Path -LiteralPath $appSource)) {
    throw 'Build the plugin before installing.'
}
if (!(Test-Path -LiteralPath (Join-Path $AssettoCorsaPath 'acs.exe'))) {
    throw 'Assetto Corsa was not found. Pass -AssettoCorsaPath with its installation folder.'
}
$backup = Join-Path $PSScriptRoot ('backups\' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $backup -Force | Out-Null
$pluginTarget = Join-Path $pluginDirectory 'ts_ac_udp.dll'
$appTarget = Join-Path $appDirectory 'TSVoice.py'
if (Test-Path -LiteralPath $pluginTarget) {
    Copy-Item -LiteralPath $pluginTarget -Destination (Join-Path $backup 'ts_ac_udp.dll')
}
if (Test-Path -LiteralPath $appTarget) {
    Copy-Item -LiteralPath $appTarget -Destination (Join-Path $backup 'TSVoice.py')
}
New-Item -ItemType Directory -Path $pluginDirectory,$appDirectory -Force | Out-Null
Copy-Item -LiteralPath $pluginSource -Destination $pluginTarget -Force
Copy-Item -LiteralPath $appSource -Destination $appTarget -Force
Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'ac_app\TSVoice\assets') -Destination $appDirectory -Recurse -Force
Write-Output "Installed plugin: $pluginTarget"
Write-Output "Installed AC app: $appTarget"
Write-Output "Previous files backed up: $backup"
