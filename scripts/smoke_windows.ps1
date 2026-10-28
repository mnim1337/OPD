param(
    [string]$Dist = "dist"
)

$ErrorActionPreference = "Stop"
$root = (Resolve-Path (Join-Path $PSScriptRoot ".."))
$cli = Join-Path $root "$Dist\opd\opd.exe"
if (-not (Test-Path $cli)) {
    throw "Не найден CLI-пакет: $cli"
}

$control = Join-Path $root "requirements\PAK_export_reference_4files_v1.zip"
$profile = Join-Path $root "configs\examples\profile_demo.json"
$temp = Join-Path ([System.IO.Path]::GetTempPath()) ("opd-stage6-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $temp | Out-Null

try {
    Expand-Archive -Path $control -DestinationPath $temp
    $folder = Get-ChildItem -Path $temp -Directory | Select-Object -First 1
    & $cli check $folder.FullName
    if ($LASTEXITCODE -ne 0) { throw "CLI check завершился с кодом $LASTEXITCODE" }
    & $cli calculate $folder.FullName --profile $profile --output (Join-Path $temp "assessment.json")
    if ($LASTEXITCODE -ne 0) { throw "CLI calculate завершился с кодом $LASTEXITCODE" }
    if (-not (Test-Path (Join-Path $temp "assessment.json"))) {
        throw "CLI не создал assessment.json"
    }
    Write-Host "Smoke-проверка CLI пройдена"
}
finally {
    Remove-Item -Recurse -Force $temp -ErrorAction SilentlyContinue
}
