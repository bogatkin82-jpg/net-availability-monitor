# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec: NetMonitor.exe для Windows (onefile, GUI/windowed).

Собирает windows/entry.py вместе с server.py, tray.py, статикой дашборда
и config.json (шаблон для первого запуска).

Сборка:  python -m PyInstaller --noconfirm --clean windows/netmon.spec
Результат: dist/NetMonitor.exe
"""

import os

SPEC_DIR = os.path.abspath(SPECPATH)
ROOT = os.path.dirname(SPEC_DIR)

STATIC_DIR = os.path.join(ROOT, "static")
CONFIG_FILE = os.path.join(ROOT, "config.json")
ICON_FILE = os.path.join(SPEC_DIR, "icon.ico")

datas = []
if os.path.isdir(STATIC_DIR):
    datas.append((STATIC_DIR, "static"))
if os.path.isfile(CONFIG_FILE):
    datas.append((CONFIG_FILE, "."))

# pystray выбирает бэкенд во время выполнения — статический анализ его не видит.
hiddenimports = ["pystray._win32"]

a = Analysis(
    [os.path.join(SPEC_DIR, "entry.py")],
    pathex=[ROOT, SPEC_DIR],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc", "doctest"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="NetMonitor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=ICON_FILE if os.path.isfile(ICON_FILE) else None,
)
