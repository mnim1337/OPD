param(
    [string]$Python = "py -3.12",
    [switch]$SkipTests
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$venv = Join-Path $root ".venv-stage6"
$pythonExe = Join-Path $venv "Scripts\python.exe"

function Assert-ExitCode([string]$Step) {
    if ($LASTEXITCODE -ne 0) {
        throw "$Step завершился с кодом $LASTEXITCODE"
    }
}

if (-not (Test-Path $pythonExe)) {
    if ($Python -eq "py -3.12" -and -not (Get-Command py -ErrorAction SilentlyContinue)) {
        if (Get-Command python -ErrorAction SilentlyContinue) {
            $Python = "python"
            Write-Host "Команда py не найдена; используется python."
        } else {
            throw "Не найден Python. Установите официальный CPython 3.12 x64 или передайте -Python с командой запуска."
        }
    }
    if ($Python -eq "py -3.12") {
        & py -3.12 -m venv $venv
    } else {
        & $Python -m venv $venv
    }
    Assert-ExitCode "Создание виртуального окружения"
}

& $pythonExe -c "import struct, sys; assert sys.version_info[:2] == (3, 12) and struct.calcsize('P') == 8, 'Нужен CPython 3.12 x64'"
Assert-ExitCode "Проверка Python 3.12 x64"
& $pythonExe -m pip install --upgrade pip
Assert-ExitCode "Обновление pip"
& $pythonExe -m pip install -e "${root}[package]"
Assert-ExitCode "Установка приложения и зависимостей"

if (-not $SkipTests) {
    $previousQtPlatform = $env:QT_QPA_PLATFORM
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        & $pythonExe -m pytest -q
        Assert-ExitCode "Тесты"
    } finally {
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
}

& $pythonExe -m PyInstaller --clean --noconfirm "$root\deploy\windows\opd-cli.spec"
Assert-ExitCode "Сборка CLI"
& $pythonExe -m PyInstaller --clean --noconfirm "$root\deploy\windows\opd-gui.spec"
Assert-ExitCode "Сборка GUI"
& $pythonExe "$root\scripts\verify_windows_bundle.py" "$root\dist\opd-gui.exe"
Assert-ExitCode "Проверка Qt-плагинов в GUI-пакете"

foreach ($name in @("opd.exe", "opd-gui.exe")) {
    if (-not (Test-Path (Join-Path $root "dist\$name"))) {
        throw "После сборки отсутствует $name"
    }
}

Write-Host "Сборка завершена. Артефакты: $root\dist\opd.exe и $root\dist\opd-gui.exe"
