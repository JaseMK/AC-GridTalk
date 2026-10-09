<#
.SYNOPSIS
Builds the port-19999 test configuration and runs every GridTalk test suite.

.DESCRIPTION
Uses .venv\Scripts\python.exe when present (see README), otherwise python on PATH.
The AC Python 3.3 runtime test runs only when Assetto Corsa is found, from
-AssettoCorsaPath or Steam's registry keys. Never touches the live installation.
#>
param(
    [string]$AssettoCorsaPath,
    [string]$Python,
    [switch]$SkipAcRuntime,
    [switch]$SkipInstaller
)
$ErrorActionPreference = 'Stop'
$repoRoot = Split-Path $PSScriptRoot -Parent
$buildDir = 'build-tests'
$port = 19999
if (!$Python) {
    $venv = Join-Path $repoRoot '.venv\Scripts\python.exe'
    $Python = if (Test-Path -LiteralPath $venv) { $venv } else { 'python' }
}
$results = [System.Collections.Generic.List[string]]::new()

function Invoke-Step([string]$Name, [scriptblock]$Command) {
    Write-Host "==> $Name" -ForegroundColor Cyan
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "$Name failed with exit code $LASTEXITCODE" }
    $results.Add("PASS  $Name")
}

Push-Location $repoRoot
try {
    & $Python -c "import importlib.util, sys; sys.exit(importlib.util.find_spec('jsonschema') is None)"
    if ($LASTEXITCODE -ne 0) {
        throw "jsonschema is not importable by '$Python'. Run: python -m venv .venv; .venv\Scripts\python -m pip install -r requirements-dev.txt"
    }
    Invoke-Step 'configure build-tests' { cmake -S . -B $buildDir -A x64 -DGRIDTALK_BUILD_TESTS=ON "-DGRIDTALK_UDP_PORT=$port" }
    Invoke-Step 'build build-tests' { cmake --build $buildDir --config Release }
    Invoke-Step 'check_schema' { & $Python tests/check_schema.py }
    Invoke-Step 'check --schema' { & $Python tests/check.py --schema --port $port --build-dir $buildDir }
    Invoke-Step 'receiver_hardening' { & $Python tests/receiver_hardening.py }
    Invoke-Step 'native_hardening' { & $Python tests/native_hardening.py --port $port --build-dir $buildDir }
    if ($SkipInstaller) {
        $results.Add('SKIP  installer (-SkipInstaller)')
    } else {
        $shell = (Get-Process -Id $PID).Path
        Invoke-Step 'installer' { & $shell -NoProfile -ExecutionPolicy Bypass -File tests/installer.ps1 }
    }
    if ($SkipAcRuntime) {
        $results.Add('SKIP  ac_runtime (-SkipAcRuntime)')
    } else {
        if (!$AssettoCorsaPath) {
            $AssettoCorsaPath = & $Python tests/ac_runtime.py --locate
            if ($LASTEXITCODE -ne 0) { $AssettoCorsaPath = $null }
        }
        if ($AssettoCorsaPath) {
            Invoke-Step 'ac_runtime (AC Python 3.3)' { & $Python tests/ac_runtime.py --ac-path $AssettoCorsaPath }
        } else {
            $results.Add('SKIP  ac_runtime (Assetto Corsa not found; pass -AssettoCorsaPath)')
        }
    }
} finally {
    Pop-Location
    Write-Host ''
    $results | ForEach-Object { Write-Host $_ }
}
Write-Host 'All GridTalk test suites passed.' -ForegroundColor Green
