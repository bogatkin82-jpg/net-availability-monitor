#!/usr/bin/env bash
# Сборка NetBar.app — индикатора доступности в строке меню macOS.
# Требуется Xcode Command Line Tools (swiftc). Сборка локальная, без сети.
set -euo pipefail
cd "$(dirname "$0")"

APP="NetBar.app"
BIN="$APP/Contents/MacOS/NetBar"
CACHE_DIR="/tmp/netbar-swift-module-cache"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$CACHE_DIR"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key>
    <string>NetBar</string>
    <key>CFBundleDisplayName</key>
    <string>NetBar — мониторинг сети</string>
    <key>CFBundleIdentifier</key>
    <string>local.netmon.menubar</string>
    <key>CFBundleExecutable</key>
    <string>NetBar</string>
    <key>CFBundlePackageType</key>
    <string>APPL</string>
    <key>CFBundleShortVersionString</key>
    <string>1.5.5</string>
    <key>CFBundleVersion</key>
    <string>1</string>
    <key>LSMinimumSystemVersion</key>
    <string>12.0</string>
    <key>LSUIElement</key>
    <true/>
    <key>NSHighResolutionCapable</key>
    <true/>
</dict>
</plist>
PLIST

swiftc -swift-version 5 -O \
    -module-cache-path "$CACHE_DIR" \
    -framework Cocoa \
    -o "$BIN" \
    NetBar.swift

# Ad-hoc подпись: macOS охотнее запускает подписанный бандл, сертификат не нужен.
if command -v codesign >/dev/null 2>&1; then
    codesign --force --sign - "$APP" >/dev/null 2>&1 || true
fi

echo "Собрано: $(pwd)/$APP"
echo "Запуск:  open \"$(pwd)/$APP\""
echo "Проверка без GUI: \"$(pwd)/$BIN\" --dump"
