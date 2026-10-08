$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
. (Join-Path $repoRoot 'tools\Install-Core.ps1')
$testRoot = Join-Path $repoRoot ('build\installer-test-' + [guid]::NewGuid().ToString('N'))
Assert-InstallChild $testRoot (Join-Path $repoRoot 'build')
New-Item -ItemType Directory -Path $testRoot | Out-Null

function New-Fixture([string]$Name, [bool]$Existing) {
    $root = Join-Path $testRoot $Name
    $source = Join-Path $root 'source'
    $acRoot = Join-Path $root 'ac'
    $plugins = Join-Path $root 'plugins'
    New-Item -ItemType Directory -Path (Join-Path $source 'build\Release'),(Join-Path $source 'ac_app'),$acRoot,$plugins -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $repoRoot 'ac_app\GridTalk') -Destination (Join-Path $source 'ac_app\GridTalk') -Recurse
    Set-Content -LiteralPath (Join-Path $source 'build\Release\assetto_corsa_notifier.dll') -Value 'new DLL'
    Set-Content -LiteralPath (Join-Path $acRoot 'acs.exe') -Value 'fake test game'
    if ($Existing) {
        $oldApp = Join-Path $acRoot 'apps\python\GridTalk'
        $oldLegacy = Join-Path $acRoot 'apps\python\TSVoice'
        New-Item -ItemType Directory -Path (Join-Path $oldApp 'assets'),$oldLegacy -Force | Out-Null
        Set-Content -LiteralPath (Join-Path $oldApp 'GridTalk.py') -Value 'old app'
        Set-Content -LiteralPath (Join-Path $oldApp 'assets\red.png') -Value 'old texture'
        Set-Content -LiteralPath (Join-Path $oldLegacy 'TSVoice.py') -Value 'legacy app'
        Set-Content -LiteralPath (Join-Path $plugins 'assetto_corsa_notifier.dll') -Value 'old DLL'
        Set-Content -LiteralPath (Join-Path $plugins 'ts_ac_udp.dll') -Value 'legacy DLL'
    }
    return @{SourceRoot=$source; AssettoCorsaPath=$acRoot; PluginDirectory=$plugins}
}

try {
    foreach ($step in @('staged','app','plugin','migration')) {
        $fixture = New-Fixture $step $true
        $injection = { param($current) if ($current -eq $step) { throw 'injected failure' } }.GetNewClosure()
        $failed = $false
        try { Invoke-GridTalkInstall @fixture -AfterStep $injection | Out-Null } catch { $failed = $true }
        if (!$failed) { throw "Failure injection did not trigger: $step" }
        $oldApp = Join-Path $fixture.AssettoCorsaPath 'apps\python\GridTalk'
        foreach ($pair in @(@((Join-Path $oldApp 'GridTalk.py'),'old app'),@((Join-Path $oldApp 'assets\red.png'),'old texture'),
                             @((Join-Path $fixture.PluginDirectory 'assetto_corsa_notifier.dll'),'old DLL'),
                             @((Join-Path $fixture.PluginDirectory 'ts_ac_udp.dll'),'legacy DLL'),
                             @((Join-Path $fixture.AssettoCorsaPath 'apps\python\TSVoice\TSVoice.py'),'legacy app'))) {
            if ((Get-Content -LiteralPath $pair[0] -Raw).Trim() -ne $pair[1]) { throw "Rollback mismatch after $step : $($pair[0])" }
        }
    }
    $fixture = New-Fixture 'clean-failure' $false
    try { Invoke-GridTalkInstall @fixture -AfterStep { param($step) if ($step -eq 'plugin') { throw 'injected failure' } } | Out-Null } catch {}
    if ((Test-Path -LiteralPath (Join-Path $fixture.AssettoCorsaPath 'apps\python\GridTalk')) -or
        (Test-Path -LiteralPath (Join-Path $fixture.PluginDirectory 'assetto_corsa_notifier.dll'))) { throw 'Partial clean install retained' }
    $fixture = New-Fixture 'missing-asset' $true
    Remove-Item -LiteralPath (Join-Path $fixture.SourceRoot 'ac_app\GridTalk\assets\red.png')
    $failed = $false
    try { Invoke-GridTalkInstall @fixture | Out-Null } catch { $failed = $true }
    if (!$failed -or (Test-Path -LiteralPath (Join-Path $fixture.SourceRoot 'backups'))) { throw 'Missing asset preflight changed installation' }
    $fixture = New-Fixture 'success' $true
    Invoke-GridTalkInstall @fixture | Out-Null
    Invoke-GridTalkInstall @fixture | Out-Null
    $backups = @(Get-ChildItem -LiteralPath (Join-Path $fixture.SourceRoot 'backups') -Directory)
    if ($backups.Count -ne 2) { throw 'Backup paths were not unique' }
    if (Test-Path -LiteralPath (Join-Path $fixture.AssettoCorsaPath 'apps\python\TSVoice')) { throw 'Legacy app remains' }
    if (Test-Path -LiteralPath (Join-Path $fixture.PluginDirectory 'ts_ac_udp.dll')) { throw 'Legacy DLL remains' }
    Write-Output 'PASS: staged install, full asset backups, unique paths, preflight, migration, and rollback at every mutation stage'
} finally {
    Assert-InstallChild $testRoot (Join-Path $repoRoot 'build')
    Remove-Item -LiteralPath $testRoot -Recurse -Force
}
