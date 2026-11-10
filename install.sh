#!/usr/bin/env bash
# Установка net-monitor в автозапуск macOS:
#   1) дашборд-сервер как LaunchAgent (стартует при входе, перезапускается при падении);
#   2) NetBar.app (индикатор в строке меню) в ~/Applications + LaunchAgent.
#
# Почему рантайм копируется в ~/Library/Application Support/netmon:
#   launchd-агенты не имеют доступа к ~/Documents (TCC) и не могут запустить
#   server.py прямо из рабочей папки проекта. Application Support — вне защиты,
#   поэтому установленная копия работает из автозапуска, а исходники остаются в проекте.
#
# Повторный запуск безопасен: код и статика синхронизируются, история и инциденты
# в data/state.json сохраняются. Удаление: ./install.sh --uninstall
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
RUNTIME_DIR="$HOME/Library/Application Support/netmon"
APP_SRC="$PROJECT_DIR/menubar/NetBar.app"
APP_DST="$HOME/Applications/NetBar.app"
AGENTS_DIR="$HOME/Library/LaunchAgents"
LOGS_DIR="$HOME/Library/Logs"
SERVER_LABEL="local.netmon.server"
MENUBAR_LABEL="local.netmon.menubar"
UID_NUM="$(id -u)"
PYTHON_BIN="$(command -v python3 || true)"

info() { printf '  %s\n' "$*"; }

uninstall() {
    echo "Удаляю автозапуск net-monitor…"
    launchctl bootout "gui/$UID_NUM/$SERVER_LABEL" 2>/dev/null || true
    launchctl bootout "gui/$UID_NUM/$MENUBAR_LABEL" 2>/dev/null || true
    rm -f "$AGENTS_DIR/$SERVER_LABEL.plist" "$AGENTS_DIR/$MENUBAR_LABEL.plist"
    pkill -f "NetBar.app/Contents/MacOS/NetBar" 2>/dev/null || true
    PID="$(lsof -nP -iTCP:8099 -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
    if [ -n "$PID" ]; then kill "$PID" 2>/dev/null || true; fi
    echo "Готово: автозапуск и процессы остановлены."
    echo "Данные и установленная копия остались здесь: $RUNTIME_DIR"
    echo "Приложение: $APP_DST"
}

if [ "${1:-}" = "--uninstall" ]; then
    uninstall
    exit 0
fi

echo "Устанавливаю net-monitor:"
info "исходники:  $PROJECT_DIR"
info "рантайм:    $RUNTIME_DIR"
info "приложение: $APP_DST"
info "логи:       $LOGS_DIR/netmon-*.log"

[ -f "$PROJECT_DIR/server.py" ] || { echo "ОШИБКА: не найден $PROJECT_DIR/server.py"; exit 1; }
[ -n "$PYTHON_BIN" ] || { echo "ОШИБКА: python3 не найден"; exit 1; }

# 1. Собрать NetBar.app, если его ещё нет.
if [ ! -x "$APP_SRC/Contents/MacOS/NetBar" ]; then
    if command -v swiftc >/dev/null 2>&1; then
        info "сборка NetBar.app (swiftc)…"
        (cd "$PROJECT_DIR/menubar" && TMPDIR=/tmp ./build.sh)
    else
        echo "ОШИБКА: нет NetBar.app и нет swiftc (Xcode Command Line Tools)"
        exit 1
    fi
fi

MENUBAR_MODE="$(python3 - "$PROJECT_DIR/config.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        mode = json.load(handle).get("display", {}).get("macos_menubar", {}).get("mode", "overlay")
    print(mode if mode in ("overlay", "dot", "text") else "overlay")
except Exception:
    print("overlay")
PY
)"

mkdir -p "$HOME/Applications" "$AGENTS_DIR" "$LOGS_DIR" "$RUNTIME_DIR/data"

# 2. Остановить вручную запущенные экземпляры и старые агенты.
launchctl bootout "gui/$UID_NUM/$SERVER_LABEL" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$MENUBAR_LABEL" 2>/dev/null || true
pkill -f "NetBar.app/Contents/MacOS/NetBar" 2>/dev/null || true
OLD_PID="$(lsof -nP -iTCP:8099 -sTCP:LISTEN -t 2>/dev/null | head -1 || true)"
if [ -n "$OLD_PID" ]; then
    info "останавливаю ручной экземпляр сервера (pid $OLD_PID)"
    kill "$OLD_PID" 2>/dev/null || true
    sleep 1
fi

# 3. Синхронизировать код/статику в рантайм, сохранив данные.
cp -f "$PROJECT_DIR/server.py" "$RUNTIME_DIR/server.py"
cp -f "$PROJECT_DIR/config.json" "$RUNTIME_DIR/config.json"
rm -rf "$RUNTIME_DIR/static"
ditto "$PROJECT_DIR/static" "$RUNTIME_DIR/static"
if [ ! -f "$RUNTIME_DIR/data/state.json" ] && [ -f "$PROJECT_DIR/data/state.json" ]; then
    cp -f "$PROJECT_DIR/data/state.json" "$RUNTIME_DIR/data/state.json"
fi

# 4. Установить приложение и запомнить путь к рантайму (для кнопки «Запустить сервер»).
rm -rf "$APP_DST"
ditto "$APP_SRC" "$APP_DST"
codesign --force --sign - "$APP_DST" >/dev/null 2>&1 || true
printf '%s\n' "$RUNTIME_DIR" > "$HOME/.netmon-dir"

# 5. LaunchAgent сервера дашборда.
cat > "$AGENTS_DIR/$SERVER_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$SERVER_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$PYTHON_BIN</string>
        <string>-B</string>
        <string>server.py</string>
        <string>--port</string>
        <string>8099</string>
    </array>
    <key>WorkingDirectory</key><string>$RUNTIME_DIR</string>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key><true/>
    <key>ThrottleInterval</key><integer>10</integer>
    <key>EnvironmentVariables</key>
    <dict>
        <key>PYTHONDONTWRITEBYTECODE</key><string>1</string>
    </dict>
    <key>StandardOutPath</key><string>$LOGS_DIR/netmon-server.log</string>
    <key>StandardErrorPath</key><string>$LOGS_DIR/netmon-server.log</string>
</dict>
</plist>
PLIST

# 6. LaunchAgent индикатора в строке меню (перезапускается после падения,
#    «Выход» из меню — штатное завершение, его не реанимируем).
cat > "$AGENTS_DIR/$MENUBAR_LABEL.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>$MENUBAR_LABEL</string>
    <key>ProgramArguments</key>
    <array>
        <string>$APP_DST/Contents/MacOS/NetBar</string>
    </array>
    <key>RunAtLoad</key><true/>
    <key>KeepAlive</key>
    <dict><key>SuccessfulExit</key><false/></dict>
    <key>LimitLoadToSessionType</key><string>Aqua</string>
    <key>ProcessType</key><string>Interactive</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>NETMON_DIR</key><string>$RUNTIME_DIR</string>
        <!-- Режим берётся из config.json → display.macos_menubar.mode:
             overlay (по умолчанию), dot (в строке меню) или text (YT✓ TG✕). -->
        <key>NETBAR_MODE</key><string>$MENUBAR_MODE</string>
        <key>NETMON_CONFIG</key><string>$RUNTIME_DIR/config.json</string>
    </dict>
    <key>StandardOutPath</key><string>$LOGS_DIR/netmon-menubar.out.log</string>
    <key>StandardErrorPath</key><string>$LOGS_DIR/netmon-menubar.err.log</string>
</dict>
</plist>
PLIST

plutil -lint "$AGENTS_DIR/$SERVER_LABEL.plist" >/dev/null
plutil -lint "$AGENTS_DIR/$MENUBAR_LABEL.plist" >/dev/null

# 7. Загрузить агенты.
launchctl bootstrap "gui/$UID_NUM" "$AGENTS_DIR/$SERVER_LABEL.plist"
launchctl enable "gui/$UID_NUM/$SERVER_LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID_NUM" "$AGENTS_DIR/$MENUBAR_LABEL.plist"
launchctl enable "gui/$UID_NUM/$MENUBAR_LABEL" 2>/dev/null || true

# 8. Дождаться ответа сервера и показать сводку.
echo -n "  жду первый цикл проверок"
STATUS=""
for _ in $(seq 1 30); do
    sleep 1
    STATUS="$(curl -s --max-time 2 http://127.0.0.1:8099/api/health 2>/dev/null || true)"
    if [ -n "$STATUS" ]; then break; fi
    echo -n "."
done
echo

if [ -z "$STATUS" ]; then
    echo "ОШИБКА: сервер не поднялся. Логи: $LOGS_DIR/netmon-server.log"
    exit 1
fi

echo "Готово."
info "API:        http://127.0.0.1:8099/api/health → $STATUS"
info "агенты:     $(launchctl list | grep -E 'local\.netmon\.' | tr '\n' ' ')"
info "индикатор:  $(pgrep -fl 'NetBar.app/Contents/MacOS/NetBar' | head -1 || echo 'не запущен')"
info "Логи:       $LOGS_DIR/netmon-server.log, $LOGS_DIR/netmon-menubar.err.log, /tmp/netmon-menubar.log"
echo
echo "Управление:"
echo "  открыть дашборд:    open http://127.0.0.1:8099/"
echo "  удалить автозапуск: $PROJECT_DIR/install.sh --uninstall"
echo "  обновить код после правок: $PROJECT_DIR/install.sh"
