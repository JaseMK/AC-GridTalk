param(
    [string]$AssettoCorsaPath = 'C:\Program Files (x86)\Steam\steamapps\common\assettocorsa'
)
$ErrorActionPreference = 'Stop'
if (Get-Process -Name ts3client_win64,ts3client_win32,acs,acs_x86 -ErrorAction SilentlyContinue) {
    throw 'Close TeamSpeak and the AC driving session before installing. No files were changed.'
}
. (Join-Path $PSScriptRoot 'tools\Install-Core.ps1')
Invoke-GridTalkInstall -SourceRoot $PSScriptRoot -AssettoCorsaPath $AssettoCorsaPath -PluginDirectory (Join-Path $env:APPDATA 'TS3Client\plugins')
