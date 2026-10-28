param(
    [string]$Python = "py -3.12",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$venv = Join-Path $root ".venv-stage6"
$pythonExe = Join-Path $venv "Scripts\python.exe"

if (-not (Test-Path $pythonExe)) {
    Invoke-Expression "$Python -m venv `"$venv`""
}

& $pythonExe -m pip install --upgrade pip
& $pythonExe -m pip install -e "${root}[package]"

if (-not $SkipTests) {
    $env:QT_QPA_PLATFORM = "offscreen"
    & $pythonExe -m pytest -q
    Remove-Item Env:QT_QPA_PLATFORM -ErrorAction SilentlyContinue
}

& $pythonExe -m PyInstaller --clean --noconfirm "$root\deploy\windows\opd-cli.spec"
& $pythonExe -m PyInstaller --clean --noconfirm "$root\deploy\windows\opd-gui.spec"

Write-Host "Сборка завершена. Артефакты: $root\dist\opd\opd.exe и $root\dist\opd-gui\opd-gui.exe"
