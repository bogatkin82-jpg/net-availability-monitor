<#
.SYNOPSIS
    Установка NetMonitor.exe в %LOCALAPPDATA%\net-availability-monitor.

.DESCRIPTION
    * останавливает запущенный NetMonitor;
    * копирует exe в каталог установки (config.json.example — как шаблон,
      сам config.json создаётся при первом запуске, существующий не трогается);
    * создаёт ярлык в меню «Пуск» (NetMonitor.lnk);
    * прописывает автозапуск в HKCU\Software\Microsoft\Windows\CurrentVersion\Run
      (значение NetMonitor, запуск без аргументов: сервер + трей), если не указан -NoAutostart.

.EXAMPLE
    .\windows\build.ps1
    .\windows\install.ps1 -Start

.EXAMPLE
    .\windows\install.ps1 -Source C:\path\NetMonitor.exe -NoAutostart
#>
[CmdletBinding()]
param(
    [string]$Source,
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "net-availability-monitor"),
    [switch]$NoAutostart,
    [switch]$NoShortcut,
    [switch]$Start
)

$ErrorActionPreference = "Stop"
$WindowsDir = $PSScriptRoot
$Root = Split-Path -Parent $WindowsDir

if (-not $Source) {
    $Source = Join-Path $Root "dist\NetMonitor.exe"
}
$Source = (Resolve-Path $Source -ErrorAction Stop).Path
$Target = Join-Path $InstallDir "NetMonitor.exe"
$RunKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run"
$ShortcutPath = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs\NetMonitor.lnk"

Write-Host "Установка NetMonitor:" -ForegroundColor Cyan
Write-Host "  исходник: $Source"
Write-Host "  каталог:  $InstallDir"

New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null

# 1. Остановить уже запущенный экземпляр, чтобы заменить exe.
Get-Process -Name "NetMonitor" -ErrorAction SilentlyContinue |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 700

# 2. Скопировать exe и показать шаблон конфига (рабочий config.json не перезаписываем).
Copy-Item -Force $Source $Target
$exampleSource = Join-Path $Root "config.json"
$exampleTarget = Join-Path $InstallDir "config.json.example"
if ((Test-Path $exampleSource) -and -not (Test-Path $exampleTarget)) {
    Copy-Item $exampleSource $exampleTarget
}

# 3. Ярлык в меню «Пуск».
if (-not $NoShortcut) {
    $startMenu = Split-Path -Parent $ShortcutPath
    New-Item -ItemType Directory -Force -Path $startMenu | Out-Null
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($ShortcutPath)
    $shortcut.TargetPath = $Target
    $shortcut.WorkingDirectory = $InstallDir
    $shortcut.IconLocation = "$Target,0"
    $shortcut.Description = "Мониторинг доступности сети"
    $shortcut.Save()
    Write-Host "  ярлык:    $ShortcutPath"
}

# 4. Автозапуск при входе в систему (HKCU Run).
if (-not $NoAutostart) {
    New-Item -Path $RunKey -Force | Out-Null
    Set-ItemProperty -Path $RunKey -Name "NetMonitor" -Value ('"{0}"' -f $Target)
    Write-Host "  автозапуск: включён (HKCU Run\NetMonitor)"
}

# 5. Первый запуск (по желанию): exe сам создаст config.json рядом с собой.
if ($Start) {
    Start-Process -FilePath $Target
    Write-Host "  запуск:   выполнен"
}

Write-Host ""
Write-Host "Готово." -ForegroundColor Green
Write-Host "  exe:        $Target"
Write-Host "  конфиг:     $InstallDir\config.json (создастся при первом запуске)"
Write-Host "  данные:     $InstallDir\data\state.json"
Write-Host "  лог:        $env:LOCALAPPDATA\net-availability-monitor\logs\netmon.log"
Write-Host "  удалить:    .\windows\uninstall.ps1"
Write-Host ""
Write-Host "Если SmartScreen показывает «Windows защитила ваш компьютер»:"
Write-Host "  «Подробнее» -> «Выполнить в любом случае» (exe не подписан сертификатом)."
