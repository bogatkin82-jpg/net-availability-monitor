<#
.SYNOPSIS
    Удаление NetMonitor: автозапуск, ярлык, процессы и файлы программы.

.DESCRIPTION
    По умолчанию удаляет exe и служебные файлы, но сохраняет config.json,
    data\state.json и логи (историю мониторинга). С -PurgeData удаляет
    каталог установки целиком.

.EXAMPLE
    .\windows\uninstall.ps1

.EXAMPLE
    .\windows\uninstall.ps1 -PurgeData
#>
[CmdletBinding()]
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "net-availability-monitor"),
    [switch]$PurgeData
)

$ErrorActionPreference = "Stop"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$ShortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\NetMonitor.lnk"

Write-Host "Удаление NetMonitor:" -ForegroundColor Cyan

# 1. Остановить процессы.
$stopped = $false
Get-Process -Name "NetMonitor" -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-Process -Id $_.Id -Force -ErrorAction SilentlyContinue
    $stopped = $true
}
if ($stopped) { Write-Host "  процессы остановлены" }

# 2. Автозапуск.
if (Get-ItemProperty -Path $RunKey -Name "NetMonitor" -ErrorAction SilentlyContinue) {
    Remove-ItemProperty -Path $RunKey -Name "NetMonitor" -ErrorAction SilentlyContinue
    Write-Host "  автозапуск удалён (HKCU Run\NetMonitor)"
}

# 3. Ярлык.
if (Test-Path $ShortcutPath) {
    Remove-Item $ShortcutPath -Force
    Write-Host "  ярлык удалён: $ShortcutPath"
}

# 4. Файлы.
if (Test-Path $InstallDir) {
    if ($PurgeData) {
        Remove-Item $InstallDir -Recurse -Force
        Write-Host "  каталог удалён полностью (включая данные): $InstallDir"
    }
    else {
        foreach ($name in @("NetMonitor.exe", "config.json.example", "README.md")) {
            $path = Join-Path $InstallDir $name
            if (Test-Path $path) { Remove-Item $path -Force -ErrorAction SilentlyContinue }
        }
        Write-Host "  exe удалён; конфиг, данные и логи сохранены: $InstallDir"
        Write-Host "  (полное удаление: .\windows\uninstall.ps1 -PurgeData)"
    }
}
else {
    Write-Host "  каталог установки не найден: $InstallDir"
}

Write-Host ""
Write-Host "Готово." -ForegroundColor Green
