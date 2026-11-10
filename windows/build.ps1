<#
.SYNOPSIS
    Сборка NetMonitor.exe (PyInstaller onefile) и упаковка Windows-артефакта.

.DESCRIPTION
    1) ставит зависимости (pyinstaller, pystray, pillow), если не указан -SkipDeps;
    2) генерирует windows/icon.ico, если его нет (Pillow);
    3) проверяет синтаксис server.py, tray.py, windows/entry.py;
    4) собирает dist/NetMonitor.exe по windows/netmon.spec;
    5) запускает exe --selftest (должен вернуть 0);
    6) собирает dist/NetMonitor-windows-x64.zip:
         NetMonitor.exe
         config.json.example  (шаблон; exe сам создаст config.json при первом запуске)
         README.md            (копия docs/windows.md)

.EXAMPLE
    .\windows\build.ps1
    .\windows\build.ps1 -SkipDeps
#>
[CmdletBinding()]
param(
    [switch]$SkipDeps,
    [switch]$NoIcon
)

$ErrorActionPreference = "Stop"
$WindowsDir = $PSScriptRoot
$Root = Split-Path -Parent $WindowsDir
$Dist = Join-Path $Root "dist"
$Spec = Join-Path $WindowsDir "netmon.spec"
$Exe = Join-Path $Dist "NetMonitor.exe"
$Stage = Join-Path $Dist "NetMonitor-windows-x64"
$Zip = Join-Path $Dist "NetMonitor-windows-x64.zip"

function Write-Step([string]$Text) {
    Write-Host ""
    Write-Host "==> $Text" -ForegroundColor Cyan
}

Push-Location $Root
try {
    if (-not $SkipDeps) {
        Write-Step "Установка зависимостей сборки"
        python -m pip install --upgrade pyinstaller pystray pillow
        if ($LASTEXITCODE -ne 0) { throw "pip install завершился с кодом $LASTEXITCODE" }
    }

    if (-not $NoIcon -and -not (Test-Path (Join-Path $WindowsDir "icon.ico"))) {
        Write-Step "Генерация icon.ico"
        python (Join-Path $WindowsDir "make_icon.py")
        if ($LASTEXITCODE -ne 0) { throw "make_icon.py завершился с кодом $LASTEXITCODE" }
    }

    Write-Step "Проверка синтаксиса"
    python -m py_compile server.py tray.py windows/entry.py
    if ($LASTEXITCODE -ne 0) { throw "py_compile завершился с кодом $LASTEXITCODE" }

    Write-Step "Сборка NetMonitor.exe"
    python -m PyInstaller --noconfirm --clean --log-level=WARN $Spec
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller завершился с кодом $LASTEXITCODE" }
    if (-not (Test-Path $Exe)) { throw "PyInstaller не создал $Exe" }

    Write-Step "Самопроверка exe (--selftest)"
    $process = Start-Process -FilePath $Exe -ArgumentList "--selftest" -Wait -PassThru
    $logPath = Join-Path $env:LOCALAPPDATA "net-availability-monitor\logs\netmon.log"
    if (Test-Path $logPath) { Get-Content $logPath -Tail 60 }
    if ($process.ExitCode -ne 0) {
        throw "NetMonitor.exe --selftest вернул код $($process.ExitCode)"
    }

    Write-Step "Упаковка артефакта"
    if (Test-Path $Stage) { Remove-Item $Stage -Recurse -Force }
    New-Item -ItemType Directory -Path $Stage | Out-Null
    Copy-Item $Exe $Stage
    Copy-Item (Join-Path $Root "config.json") (Join-Path $Stage "config.json.example")
    Copy-Item (Join-Path $Root "docs\windows.md") (Join-Path $Stage "README.md")
    if (Test-Path $Zip) { Remove-Item $Zip -Force }
    Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $Zip

    $sizeMb = [math]::Round((Get-Item $Zip).Length / 1MB, 1)
    Write-Host ""
    Write-Host "Готово: $Zip ($sizeMb МБ)" -ForegroundColor Green
    Write-Host "Запуск:  $Exe"
    Write-Host "Установка в автозапуск: .\windows\install.ps1"
}
finally {
    Pop-Location
}
