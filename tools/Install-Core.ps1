$ErrorActionPreference = 'Stop'

function Assert-InstallChild([string]$Path, [string]$Parent) {
    $resolvedTarget = [IO.Path]::GetFullPath($Path)
    $resolvedParent = [IO.Path]::GetFullPath($Parent).TrimEnd('\') + '\'
    if (!$resolvedTarget.StartsWith($resolvedParent, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Installation path is outside its expected root: $resolvedTarget"
    }
}

function Invoke-GridTalkInstall {
    param(
        [Parameter(Mandatory)][string]$SourceRoot,
        [Parameter(Mandatory)][string]$AssettoCorsaPath,
        [Parameter(Mandatory)][string]$PluginDirectory,
        [scriptblock]$AfterStep = {} # failure injection in tests using temporary roots
    )
    $SourceRoot = [IO.Path]::GetFullPath($SourceRoot)
    $AssettoCorsaPath = [IO.Path]::GetFullPath($AssettoCorsaPath)
    $PluginDirectory = [IO.Path]::GetFullPath($PluginDirectory)
    $pluginSource = Join-Path $SourceRoot 'build\Release\assetto_corsa_notifier.dll'
    $appSource = Join-Path $SourceRoot 'ac_app\GridTalk'
    $requiredFiles = @('GridTalk.py') + @('red','green','idle_left','idle_middle','idle_right','active_left','active_middle','active_right' | ForEach-Object { "assets\$_.png" })
    foreach ($required in @($pluginSource) + @($requiredFiles | ForEach-Object { Join-Path $appSource $_ })) {
        if (!(Test-Path -LiteralPath $required -PathType Leaf)) { throw "Required installation file missing: $required" }
    }
    if (!(Test-Path -LiteralPath (Join-Path $AssettoCorsaPath 'acs.exe') -PathType Leaf)) {
        throw 'Assetto Corsa was not found. Pass -AssettoCorsaPath with its installation folder.'
    }
    $appsRoot = Join-Path $AssettoCorsaPath 'apps\python'
    $appTarget = Join-Path $appsRoot 'GridTalk'
    $legacyApp = Join-Path $appsRoot 'TSVoice'
    $pluginTarget = Join-Path $PluginDirectory 'assetto_corsa_notifier.dll'
    $legacyPlugin = Join-Path $PluginDirectory 'ts_ac_udp.dll'
    $installId = (Get-Date -Format 'yyyyMMdd-HHmmss-fff') + '-' + [guid]::NewGuid().ToString('N')
    $backupRoot = Join-Path $SourceRoot 'backups'
    $backup = Join-Path $backupRoot $installId
    $stage = Join-Path $appsRoot ('GridTalk-stage-' + $installId)
    $previousApp = Join-Path $appsRoot ('GridTalk-previous-' + $installId)
    $dllStage = Join-Path $PluginDirectory ('GridTalk-stage-' + $installId + '.dll')
    foreach ($path in @($appTarget,$legacyApp,$stage,$previousApp)) { Assert-InstallChild $path $appsRoot }
    foreach ($path in @($pluginTarget,$legacyPlugin,$dllStage)) { Assert-InstallChild $path $PluginDirectory }
    Assert-InstallChild $backup $backupRoot
    $hadApp = Test-Path -LiteralPath $appTarget
    $hadPlugin = Test-Path -LiteralPath $pluginTarget
    foreach ($directory in @($appSource,$appTarget,$legacyApp,$PluginDirectory,$appsRoot)) {
        if ((Test-Path -LiteralPath $directory) -and
            ((Get-Item -LiteralPath $directory).Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw "Installation folder is a reparse point: $directory"
        }
    }
    foreach ($directory in @($appSource,$appTarget,$legacyApp)) {
        if (Test-Path -LiteralPath $directory) {
            $links = @(Get-ChildItem -LiteralPath $directory -Recurse -Force | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint })
            if ($links.Count) { throw "Installation tree contains reparse points: $directory" }
        }
    }
    $changedApp = $false
    $changedPlugin = $false
    $movedLegacyApp = $false
    $movedLegacyPlugin = $false
    $installSucceeded = $false
    New-Item -ItemType Directory -Path $appsRoot,$PluginDirectory,$backup -Force | Out-Null
    try {
        New-Item -ItemType Directory -Path (Join-Path $stage 'assets') -Force | Out-Null
        foreach ($relative in $requiredFiles) {
            Copy-Item -LiteralPath (Join-Path $appSource $relative) -Destination (Join-Path $stage $relative)
        }
        Copy-Item -LiteralPath $pluginSource -Destination $dllStage
        foreach ($relative in $requiredFiles) {
            if ((Get-FileHash -LiteralPath (Join-Path $stage $relative)).Hash -ne
                (Get-FileHash -LiteralPath (Join-Path $appSource $relative)).Hash) { throw "Staged file differs: $relative" }
        }
        if ((Get-FileHash -LiteralPath $dllStage).Hash -ne (Get-FileHash -LiteralPath $pluginSource).Hash) { throw 'Staged DLL differs' }
        if ($hadApp) { Copy-Item -LiteralPath $appTarget -Destination (Join-Path $backup 'GridTalk') -Recurse }
        if ($hadPlugin) { Copy-Item -LiteralPath $pluginTarget -Destination (Join-Path $backup 'assetto_corsa_notifier.dll') }
        & $AfterStep 'staged'
        if ($hadApp) { Move-Item -LiteralPath $appTarget -Destination $previousApp }
        $changedApp = $true
        Move-Item -LiteralPath $stage -Destination $appTarget
        & $AfterStep 'app'
        $changedPlugin = $true
        Move-Item -LiteralPath $dllStage -Destination $pluginTarget -Force
        & $AfterStep 'plugin'
        if (Test-Path -LiteralPath $legacyApp) {
            Move-Item -LiteralPath $legacyApp -Destination (Join-Path $backup 'TSVoice')
            $movedLegacyApp = $true
        }
        if (Test-Path -LiteralPath $legacyPlugin) {
            Move-Item -LiteralPath $legacyPlugin -Destination (Join-Path $backup 'ts_ac_udp.dll')
            $movedLegacyPlugin = $true
        }
        & $AfterStep 'migration'
        foreach ($relative in $requiredFiles) {
            if ((Get-FileHash -LiteralPath (Join-Path $appTarget $relative)).Hash -ne
                (Get-FileHash -LiteralPath (Join-Path $appSource $relative)).Hash) { throw "Installed file differs: $relative" }
        }
        if ((Get-FileHash -LiteralPath $pluginTarget).Hash -ne (Get-FileHash -LiteralPath $pluginSource).Hash) { throw 'Installed DLL differs' }
        $installSucceeded = $true
    } catch {
        $installFailure = $_
        try {
            if ($changedApp) {
                if (Test-Path -LiteralPath $appTarget) { Remove-Item -LiteralPath $appTarget -Recurse -Force }
                if ($hadApp) { Move-Item -LiteralPath $previousApp -Destination $appTarget }
            }
            if ($changedPlugin) {
                if ($hadPlugin) { Copy-Item -LiteralPath (Join-Path $backup 'assetto_corsa_notifier.dll') -Destination $pluginTarget -Force }
                elseif (Test-Path -LiteralPath $pluginTarget) { Remove-Item -LiteralPath $pluginTarget -Force }
            }
            if ($movedLegacyApp) { Move-Item -LiteralPath (Join-Path $backup 'TSVoice') -Destination $legacyApp }
            if ($movedLegacyPlugin) { Move-Item -LiteralPath (Join-Path $backup 'ts_ac_udp.dll') -Destination $legacyPlugin }
        } catch { throw "Install failed: $installFailure. Rollback also failed: $_. Backup: $backup" }
        throw "Install failed and previous files were restored: $installFailure. Backup: $backup"
    } finally {
        try {
            if (Test-Path -LiteralPath $stage) { Remove-Item -LiteralPath $stage -Recurse -Force }
            if (Test-Path -LiteralPath $dllStage) { Remove-Item -LiteralPath $dllStage -Force }
            if ($installSucceeded -and (Test-Path -LiteralPath $previousApp)) { Remove-Item -LiteralPath $previousApp -Recurse -Force }
        } catch { Write-Warning "Temporary install files could not be removed: $_" }
    }
    Write-Output "Installed plugin: $pluginTarget"
    Write-Output "Installed AC app: $appTarget"
    Write-Output "Previous installation backed up: $backup"
    Write-Output 'Enable Assetto Corsa Notifier in TeamSpeak and GridTalk in AC Python app settings.'
}
