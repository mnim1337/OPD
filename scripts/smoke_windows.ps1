param(
    [string]$Dist = "dist"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$cli = Join-Path $root "$Dist\opd.exe"
$gui = Join-Path $root "$Dist\opd-gui.exe"
if (-not (Test-Path $cli)) {
    throw "Не найден CLI-пакет: $cli"
}
if (-not (Test-Path $gui)) {
    throw "Не найден GUI-пакет: $gui"
}

$control = Join-Path $root "requirements\PAK_export_reference_4files_v1.zip"
$profile = Join-Path $root "configs\examples\profile_demo.json"
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("opd-stage6-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null

try {
    Expand-Archive -Path $control -DestinationPath $temp
    & $cli check $temp
    if ($LASTEXITCODE -ne 0) { throw "CLI check завершился с кодом $LASTEXITCODE" }
    & $cli calculate $temp --profile $profile --output (Join-Path $temp "assessment.json")
    if ($LASTEXITCODE -ne 0) { throw "CLI calculate завершился с кодом $LASTEXITCODE" }
    if (-not (Test-Path (Join-Path $temp "assessment.json"))) {
        throw "CLI не создал assessment.json"
    }
    Write-Host "Smoke-проверка CLI пройдена"

    $previousQtPlatform = $env:QT_QPA_PLATFORM
    $guiProcess = $null
    try {
        $env:QT_QPA_PLATFORM = "offscreen"
        $guiProcess = Start-Process -FilePath $gui -WorkingDirectory $root -PassThru
        Start-Sleep -Seconds 5
        $guiProcess.Refresh()
        if ($guiProcess.HasExited) {
            throw "GUI-пакет завершился сразу после запуска с кодом $($guiProcess.ExitCode)"
        }
        Write-Host "Smoke-проверка запуска GUI пройдена"
    } finally {
        if ($null -ne $guiProcess -and -not $guiProcess.HasExited) {
            Stop-Process -Id $guiProcess.Id -Force
        }
        $env:QT_QPA_PLATFORM = $previousQtPlatform
    }
}
finally {
    Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}
