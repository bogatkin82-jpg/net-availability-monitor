#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Системный трей монитора доступности сети (Windows, pystray + Pillow).

Что делает:
  * показывает в трее цветную точку по агрегированному статусу
    (цвета — status_colors из config.json; серая точка, если сервер недоступен);
  * опрашивает GET /api/state каждые 10 секунд и обновляет tooltip/меню;
  * меню: заголовок с временем, статусы сервисов (причина и длительность
    активного сбоя — в подменю), «Проверить сейчас» (POST /api/check, блокирует
    пункт на время), «Открыть дашборд», «Запустить сервер», «Автозапуск»
    (HKCU\\...\\Run, только Windows) и «Выход»;
  * опциональные уведомления о переходе в blocked/down
    (notifications.windows из config.json).

Зависимости нужны ТОЛЬКО для GUI: pystray и Pillow (импортируются лениво).
Логика меню/статусов и `--selftest` работают на любой ОС и без этих пакетов.

Запуск:
    python tray.py --selftest                 # проверка логики без GUI
    python tray.py                            # трей (Windows; на macOS pystray работает
                                              # через Cocoa, но целевая ОС — Windows)
    python tray.py --config /path/config.json
"""

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from datetime import datetime

import server as _server

VERSION = "1.0.0"

# Трей обновляет состояние раз в 10 с; сам сервер опрашивает сеть по своему интервалу.
TRAY_POLL_SECONDS = 10
API_TIMEOUT = 5.0
CHECK_TIMEOUT = 30.0
DEFAULT_COLOR = "#8e8e93"

# Автозапуск Windows: HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run
AUTOSTART_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
AUTOSTART_VALUE = "NetMonitor"

# Метка пункта меню-заголовка.
MENU_TITLE = "Мониторинг доступности"
CREATE_NO_WINDOW = 0x08000000  # subprocess.CREATE_NO_WINDOW (только Windows)

# Короткие подписи для tooltip (полные status_labels не помещаются в 128 символов).
SHORT_STATUS_LABELS = {
    "ok": "ok",
    "degraded": "нестабильно",
    "blocked": "блокировка",
    "down": "недоступен",
    "unknown": "нет данных",
}


# --------------------------------------------------------------------------
# HTTP-клиент API сервера
# --------------------------------------------------------------------------

class ApiError(Exception):
    """Сервер недоступен или ответил некорректно."""


class ApiClient(object):
    """Минимальный клиент к локальному API монитора (stdlib, urllib)."""

    def __init__(self, host, port, timeout=API_TIMEOUT):
        client_host = host if host not in ("", "0.0.0.0", "::") else "127.0.0.1"
        if ":" in client_host and not client_host.startswith("["):
            client_host = "[%s]" % client_host  # IPv6
        self.base_url = "http://%s:%d" % (client_host, port)
        self.dashboard_url = self.base_url + "/"
        self.timeout = timeout
        # Локальный API не должен уходить в системный прокси (Happ и т.п.).
        self._opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def _request(self, path, method="GET", timeout=None):
        url = self.base_url + path
        data = b"" if method == "POST" else None
        request = urllib.request.Request(url, data=data, method=method)
        request.add_header("Accept", "application/json")
        try:
            with self._opener.open(request, timeout=timeout or self.timeout) as response:
                body = response.read()
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise ApiError("%s" % exc)
        try:
            return json.loads(body.decode("utf-8", "replace"))
        except ValueError as exc:
            raise ApiError("некорректный JSON: %s" % exc)

    def get_state(self, timeout=None):
        payload = self._request("/api/state", timeout=timeout)
        if not isinstance(payload, dict):
            raise ApiError("неожиданный ответ /api/state")
        return payload

    def health(self, timeout=2.0):
        try:
            payload = self._request("/api/health", timeout=timeout)
        except ApiError:
            return None
        return payload if isinstance(payload, dict) and payload.get("ok") else None

    def trigger_check(self, timeout=CHECK_TIMEOUT):
        payload = self._request("/api/check", method="POST", timeout=timeout)
        if not isinstance(payload, dict):
            raise ApiError("неожиданный ответ /api/check")
        state = payload.get("state")
        if not isinstance(state, dict):
            raise ApiError("в ответе /api/check нет state")
        return state


# --------------------------------------------------------------------------
# Форматирование статусов и модель меню (используется и в GUI, и в --selftest)
# --------------------------------------------------------------------------

def aggregate_status(state):
    """Агрегированный статус из снимка /api/state (ok/degraded/down/unknown)."""
    if not state:
        return "unknown"
    summary = state.get("summary") or {}
    status = summary.get("overall")
    if status not in _server.SERVICE_STATUSES:
        return "unknown"
    return status


def aggregate_label(settings, state):
    """Подпись агрегированного статуса (из API либо status_labels конфига)."""
    if not state:
        return settings.status_labels.get("unknown", "Нет данных")
    summary = state.get("summary") or {}
    return summary.get("overall_label") or settings.status_labels.get(
        aggregate_status(state), "Нет данных")


def status_color(settings, status, online=True):
    """Цвет точки для статуса; при недоступном сервере — серый."""
    colors = settings.status_colors or {}
    if not online or status not in colors:
        status = "unknown"
    return colors.get(status) or colors.get("unknown") or DEFAULT_COLOR


def parse_color(value, fallback=(142, 142, 147)):
    """'#rrggbb' -> (r, g, b); при ошибке — fallback."""
    try:
        text = str(value or "").strip()
        if text.startswith("#") and len(text) == 7:
            return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
    except ValueError:
        pass
    return fallback


def format_duration(seconds):
    """Длительность по-русски: 12 с / 5 мин / 1 ч 05 мин / 2 д 3 ч."""
    try:
        seconds = int(seconds or 0)
    except (TypeError, ValueError):
        seconds = 0
    seconds = max(0, seconds)
    if seconds < 60:
        return "%d с" % seconds
    minutes, sec = divmod(seconds, 60)
    if minutes < 60:
        return "%d мин" % minutes
    hours, minutes = divmod(minutes, 60)
    if hours < 24:
        return "%d ч %02d мин" % (hours, minutes)
    days, hours = divmod(hours, 24)
    return "%d д %d ч" % (days, hours)


def format_clock(epoch=None):
    """HH:MM:SS по локальному времени (для заголовка меню и tooltip)."""
    if epoch is None:
        moment = datetime.now()
    else:
        try:
            moment = datetime.fromtimestamp(float(epoch))
        except (ValueError, OSError, OverflowError, TypeError):
            moment = datetime.now()
    return moment.strftime("%H:%M:%S")


def tooltip_text(settings, state, online, now=None):
    """Текст tooltip: время + компактные статусы (Windows ограничивает ~128 символов)."""
    clock = format_clock(now.timestamp() if now else None)
    header = "%s · %s" % (MENU_TITLE, clock)
    if not (online and state):
        return header + "\nСервер недоступен"
    parts = []
    for service in state.get("services") or []:
        name = service.get("name") or service.get("id") or "?"
        status = service.get("status") or "unknown"
        parts.append("%s: %s" % (name, SHORT_STATUS_LABELS.get(status, status)))
    line = ", ".join(parts)
    text = header + "\n" + line
    if len(text) > 120:
        budget = max(0, 120 - len(header) - 2)  # "\n" и "…"
        text = header + "\n" + line[:budget] + "…"
    return text


def menu_model(settings, state, online, checking=False, autostart=None, now=None):
    """Построить модель меню (список словарей) — общая для GUI и --selftest.

    Поля записи: text, enabled, action (check/dashboard/start_server/autostart/quit),
    checked (для автозапуска), children (подменю), kind ("separator").
    """
    autostart = autostart or {"available": False, "enabled": False}
    clock = format_clock(now.timestamp() if now else None)
    entries = [{"text": "%s · %s" % (MENU_TITLE, clock), "enabled": False}]

    if online and state:
        services = state.get("services") or []
        if not services:
            entries.append({"text": "Сервисы не настроены", "enabled": False})
        for service in services:
            name = service.get("name") or service.get("id") or "?"
            label = service.get("status_label") or settings.status_labels.get(
                service.get("status") or "unknown", "Нет данных")
            text = "%s — %s" % (name, label)
            incident = service.get("active_incident") or {}
            if incident:
                text += " · сбой %s" % format_duration(incident.get("duration_sec"))
            children = []
            reason = service.get("status_reason") or "Причина не сообщается"
            children.append({"text": reason, "enabled": False})
            hint = service.get("hint")
            if hint and service.get("status") in ("degraded", "blocked", "down"):
                children.append({"text": hint, "enabled": False})
            checks_total = service.get("checks_total")
            if checks_total:
                children.append({
                    "text": "Проверки: %s из %s" % (
                        service.get("checks_ok"), checks_total),
                    "enabled": False,
                })
            if incident:
                children.append({
                    "text": "Сбой с %s · %s" % (
                        format_clock(incident.get("started_epoch")),
                        format_duration(incident.get("duration_sec"))),
                    "enabled": False,
                })
            entries.append({"text": text, "enabled": True, "children": children})
    else:
        entries.append({"text": "Сервер недоступен", "enabled": False})

    entries.append({"kind": "separator"})
    entries.append({
        "text": "Проверяю…" if checking else "Проверить сейчас",
        "action": "check",
        "enabled": bool(online) and not checking,
    })
    entries.append({"text": "Открыть дашборд", "action": "dashboard", "enabled": True})
    entries.append({"text": "Запустить сервер", "action": "start_server",
                    "enabled": not online})
    if autostart.get("available"):
        entries.append({
            "text": "Автозапуск",
            "action": "autostart",
            "checked": bool(autostart.get("enabled")),
            "enabled": True,
        })
    entries.append({"kind": "separator"})
    entries.append({"text": "Выход", "action": "quit", "enabled": True})
    return entries


def flatten_menu(entries, depth=0):
    """Превратить модель меню в список строк для --selftest/логов."""
    lines = []
    prefix = "  " * depth
    for entry in entries:
        if entry.get("kind") == "separator":
            lines.append(prefix + "---")
            continue
        marks = []
        if entry.get("checked") is not None:
            marks.append("[x]" if entry.get("checked") else "[ ]")
        if not entry.get("enabled", True):
            marks.append("(disabled)")
        text = "%s%s %s" % (prefix, " ".join(marks), entry.get("text", ""))
        lines.append(text.rstrip())
        for child in entry.get("children") or []:
            lines.append("%s  · %s" % (prefix, child.get("text", "")))
    return lines


# --------------------------------------------------------------------------
# Иконка (Pillow импортируется только здесь)
# --------------------------------------------------------------------------

def make_dot_image(color_hex, size=16):
    """Нарисовать круглую точку заданного цвета и размера (RGBA PNG-совместимо)."""
    from PIL import Image, ImageDraw  # ленивый импорт: нужен только GUI

    size = max(8, int(size))
    scale = 8 if size <= 32 else 4  # рисуем крупно и уменьшаем — сглаживание краёв
    canvas_size = size * scale
    image = Image.new("RGBA", (canvas_size, canvas_size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    red, green, blue = parse_color(color_hex)
    margin = max(1, int(canvas_size * 0.06))
    diameter = canvas_size - margin * 2
    # Тёмный контур помогает точке читаться на светлой и тёмной панели задач.
    draw.ellipse([margin, margin, margin + diameter - 1, margin + diameter - 1],
                 fill=(red, green, blue, 255), outline=(20, 20, 22, 160),
                 width=max(1, int(canvas_size * 0.05)))
    return image.resize((size, size), Image.LANCZOS)


def icon_image(settings, state, online):
    """PIL-изображение точки по агрегированному статусу сервера."""
    status = aggregate_status(state) if online else "unknown"
    color = status_color(settings, status, online)
    dot_pt = int((settings.display.get("windows_tray") or {}).get("dot_pt") or 16)
    return make_dot_image(color, dot_pt)


# --------------------------------------------------------------------------
# Автозапуск Windows (HKCU Run)
# --------------------------------------------------------------------------

def autostart_command():
    """Команда для HKCU\\...\\Run: собранный exe либо python + entry.py/tray.py."""
    if getattr(sys, "frozen", False):
        return '"%s"' % sys.executable
    here = os.path.dirname(os.path.abspath(__file__))
    entry_path = os.path.join(here, "windows", "entry.py")
    if os.path.isfile(entry_path):
        return '"%s" "%s"' % (sys.executable, entry_path)
    return '"%s" "%s"' % (sys.executable, os.path.abspath(__file__))


def autostart_status():
    """Состояние автозапуска: {'available', 'enabled', 'command'}."""
    if os.name != "nt":
        return {"available": False, "enabled": False, "command": None,
                "reason": "автозапуск доступен только в Windows"}
    try:
        import winreg
    except ImportError:  # pragma: no cover - защита от экзотических сборок
        return {"available": False, "enabled": False, "command": None,
                "reason": "модуль winreg недоступен"}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY) as key:
            command, _ = winreg.QueryValueEx(key, AUTOSTART_VALUE)
        return {"available": True, "enabled": bool(command), "command": command}
    except OSError:
        return {"available": True, "enabled": False, "command": None}


def set_autostart(enabled):
    """Включить/выключить автозапуск в HKCU\\...\\Run; ошибки — OSError."""
    if os.name != "nt":
        raise OSError("автозапуск поддерживается только в Windows")
    import winreg
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, AUTOSTART_KEY, 0,
                            winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, AUTOSTART_VALUE, 0, winreg.REG_SZ,
                              autostart_command())
            return autostart_command()
        try:
            winreg.DeleteValue(key, AUTOSTART_VALUE)
        except FileNotFoundError:
            pass
        return None


# --------------------------------------------------------------------------
# Приложение трея
# --------------------------------------------------------------------------

class TrayApp(object):
    """Трей-приложение: опрос API, иконка, меню и действия."""

    def __init__(self, settings, config_source=None, start_server=None, on_quit=None):
        self.settings = settings
        self.config_source = config_source
        self.start_server = start_server
        self.on_quit = on_quit
        self.client = ApiClient(settings.host, settings.port)
        self.poll_seconds = max(3, int(TRAY_POLL_SECONDS))

        self.state = None
        self.online = False
        self.checking = False
        self.started = time.time()
        self.stop_event = threading.Event()
        self.icon = None
        self._pystray = None
        self._previous_overall = None

    # --- состояние --------------------------------------------------------
    def refresh(self):
        """Один опрос /api/state; ошибки сети просто означают «офлайн»."""
        state = None
        try:
            state = self.client.get_state(timeout=API_TIMEOUT)
        except ApiError:
            state = None
        self._apply_state(state)

    def _apply_state(self, state):
        self.state = state
        self.online = state is not None
        if self.online:
            self._maybe_notify_transition(state)
        self._update_icon()

    def _poll_loop(self):
        while not self.stop_event.is_set():
            self.refresh()
            self.stop_event.wait(self.poll_seconds)

    # --- иконка/tooltip ---------------------------------------------------
    def _icon_image(self):
        try:
            return icon_image(self.settings, self.state, self.online)
        except ImportError:
            return None

    def _tooltip(self):
        return tooltip_text(self.settings, self.state, self.online)

    def _update_icon(self):
        icon = self.icon
        if icon is None:
            return
        try:
            image = self._icon_image()
            if image is not None:
                icon.icon = image
            icon.title = self._tooltip()
            if self._pystray is not None:
                icon.menu = self._build_menu()
                icon.update_menu()
        except Exception:  # noqa: BLE001 - трей не должен падать из-за обновления
            self._log_traceback("не удалось обновить иконку трея")

    def _build_menu(self):
        pystray = self._pystray
        model = menu_model(self.settings, self.state, self.online,
                           checking=self.checking,
                           autostart=autostart_status())
        items = []
        for entry in model:
            items.append(self._menu_item(pystray, entry))
        return pystray.Menu(*items)

    def _menu_item(self, pystray, entry):
        if entry.get("kind") == "separator":
            return pystray.Menu.SEPARATOR
        children = entry.get("children")
        if children:
            submenu = pystray.Menu(*[self._menu_item(pystray, child)
                                    for child in children])
            return pystray.MenuItem(entry.get("text", ""), submenu,
                                    enabled=entry.get("enabled", True))
        action_name = entry.get("action")
        action = self._action_for(action_name) if action_name else None
        checked = entry.get("checked")
        if checked is not None:
            return pystray.MenuItem(
                entry.get("text", ""), action,
                checked=lambda item, value=bool(checked): value,
                enabled=entry.get("enabled", True))
        return pystray.MenuItem(entry.get("text", ""), action,
                                enabled=entry.get("enabled", True))

    def _action_for(self, name):
        return {
            "check": self.action_check,
            "dashboard": self.action_dashboard,
            "start_server": self.action_start_server,
            "autostart": self.action_autostart,
            "quit": self.action_quit,
        }.get(name)

    # --- уведомления ------------------------------------------------------
    def _notify(self, message, title=MENU_TITLE):
        icon = self.icon
        if icon is None:
            return
        try:
            icon.notify(message, title)
        except Exception:  # noqa: BLE001 - не все платформы поддерживают notify
            pass

    def _maybe_notify_transition(self, state):
        config = self.settings.notifications.get("windows") or {}
        if not config.get("enabled", True):
            return
        notify_on = config.get("notify_on") or []
        overall = aggregate_status(state)
        previous = self._previous_overall
        self._previous_overall = overall
        if previous is None or previous == overall or overall not in notify_on:
            return
        affected = []
        for service in state.get("services") or []:
            if service.get("status") in notify_on:
                affected.append("%s: %s" % (
                    service.get("name") or service.get("id"),
                    service.get("status_label") or service.get("status")))
        message = aggregate_label(self.settings, state)
        if affected:
            message += "\n" + ", ".join(affected)
        self._notify(message)

    # --- действия меню ----------------------------------------------------
    def action_check(self, icon=None, item=None):
        """Принудительная проверка: POST /api/check, пункт заблокирован до ответа."""
        if self.checking:
            return
        self.checking = True
        self._update_icon()
        try:
            state = self.client.trigger_check(timeout=CHECK_TIMEOUT)
            self._apply_state(state)
        except ApiError as exc:
            self._notify("Не удалось выполнить проверку: %s" % exc)
        finally:
            self.checking = False
            self.refresh()

    def action_dashboard(self, icon=None, item=None):
        webbrowser.open(self.client.dashboard_url)

    def action_start_server(self, icon=None, item=None):
        """Поднять сервер, если /api/health не отвечает (встроенно или процессом)."""
        if self.online:
            return
        self._notify("Запускаю сервер…")
        try:
            if self.start_server is not None:
                self.start_server()
            else:
                self._spawn_server_process()
        except Exception as exc:  # noqa: BLE001
            self._notify("Не удалось запустить сервер: %s" % exc)
            return
        deadline = time.time() + 15
        while time.time() < deadline and not self.stop_event.is_set():
            if self.client.health(timeout=2.0) is not None:
                break
            time.sleep(0.5)
        self.refresh()

    def _spawn_server_process(self):
        """Запустить сервер отдельным процессом (exe --server-only или python server.py)."""
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--server-only"]
        else:
            server_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "server.py")
            command = [sys.executable, server_path]
        if self.config_source and self.config_source != "builtin":
            command += ["--config", self.config_source]
        kwargs = {}
        if os.name == "nt":
            kwargs["creationflags"] = CREATE_NO_WINDOW
        subprocess.Popen(command, close_fds=True, **kwargs)

    def action_autostart(self, icon=None, item=None):
        status = autostart_status()
        if not status.get("available"):
            self._notify(status.get("reason") or "Автозапуск недоступен")
            return
        try:
            set_autostart(not status.get("enabled"))
        except OSError as exc:
            self._notify("Не удалось изменить автозапуск: %s" % exc)
            return
        self._update_icon()

    def action_quit(self, icon=None, item=None):
        self.shutdown()

    def shutdown(self):
        """Остановить опрос и убрать иконку (идемпотентно)."""
        self.stop_event.set()
        try:
            if self.on_quit is not None:
                self.on_quit()
        except Exception:  # noqa: BLE001
            self._log_traceback("ошибка при остановке сервера")
        icon = self.icon
        if icon is not None:
            try:
                icon.stop()
            except Exception:  # noqa: BLE001
                pass

    # --- запуск -----------------------------------------------------------
    def run(self):
        """Создать иконку и войти в цикл сообщений Windows (блокирующий вызов)."""
        import pystray  # ленивый импорт: --selftest работает без GUI-зависимостей
        from PIL import Image  # noqa: F401 - проверяем наличие Pillow
        self._pystray = pystray
        self.refresh()
        icon = pystray.Icon(
            "netmon",
            self._icon_image(),
            self._tooltip(),
            self._build_menu(),
        )
        self.icon = icon
        poll_thread = threading.Thread(target=self._poll_loop,
                                       name="netmon-tray-poll", daemon=True)

        def setup(icon_obj):  # noqa: ARG001
            poll_thread.start()

        try:
            icon.run(setup=setup)
        finally:
            self.stop_event.set()

    # --- служебное --------------------------------------------------------
    @staticmethod
    def _log_traceback(message):
        sys.stderr.write("[tray] %s\n%s\n" % (message, traceback.format_exc()))
        try:
            sys.stderr.flush()
        except (AttributeError, OSError):
            pass


def run_tray(settings, config_source=None, start_server=None, on_quit=None):
    """Запустить GUI трея. Возвращает код выхода (3 — нет pystray/Pillow)."""
    try:
        import pystray  # noqa: F401
        from PIL import Image  # noqa: F401
    except ImportError as exc:
        sys.stderr.write(
            "Трей требует pystray и Pillow: pip install pystray pillow (%s)\n" % exc)
        try:
            sys.stderr.flush()
        except (AttributeError, OSError):
            pass
        return 3
    app = TrayApp(settings, config_source=config_source,
                  start_server=start_server, on_quit=on_quit)
    try:
        app.run()
    finally:
        app.stop_event.set()
    return 0


# --------------------------------------------------------------------------
# --selftest: проверка логики без GUI (работает на любой ОС и без pystray)
# --------------------------------------------------------------------------

def synthetic_state(settings):
    """Синтетический снимок /api/state для selftest, если сервер недоступен."""
    now = time.time()
    services = []
    overall = "ok"
    for index, spec in enumerate(settings.services):
        status = "ok"
        if index == 1:
            status = "degraded"
            overall = "degraded"
        elif index == 2 and len(settings.services) > 2:
            status = "ok"
        total = len(spec.get("checks") or [])
        incident = None
        reason = "Все проверки проходят"
        if status != "ok":
            reason = "TLS-рукопожатие не завершилось (таймаут) — пример для selftest"
            incident = {
                "id": "selftest",
                "service_id": spec["id"],
                "status": "active",
                "started_at": _server.iso_from_epoch(now - 754),
                "started_epoch": now - 754,
                "duration_sec": 754,
            }
        services.append({
            "id": spec["id"],
            "name": spec["name"],
            "status": status,
            "status_label": settings.status_labels.get(status, status),
            "status_reason": reason,
            "hint": (spec.get("hints") or {}).get(status),
            "checks_ok": total if status == "ok" else max(0, total - 2),
            "checks_total": total,
            "active_incident": incident,
            "latency_ms": 123 if status == "ok" else None,
        })
    # «Интернет» в синтетическом снимке считаем рабочим.
    return {
        "generated_at": _server.iso_now(),
        "epoch": now,
        "summary": {
            "overall": overall,
            "overall_label": {"ok": "Всё работает", "degraded": "Есть проблемы",
                              "down": "Нет доступа в интернет",
                              "unknown": "Нет данных"}[overall],
        },
        "internet": {"status": "ok", "label": "Интернет работает"},
        "services": services,
    }


def run_selftest(settings, config_source=None, extra_lines=None):
    """Проверить загрузку конфига, модель меню, tooltip и цвет иконки без GUI."""
    lines = list(extra_lines or [])
    lines.append("NetMonitor — самопроверка трея (без GUI)")
    lines.append("версия: %s, python %s, платформа %s" % (
        VERSION, sys.version.split()[0], sys.platform))
    if getattr(sys, "frozen", False):
        lines.append("сборка: PyInstaller onefile, каталог: %s" % _server.app_dir())
    source = config_source
    if not source and settings.source != "builtin":
        source = settings.source
    lines.append("конфигурация: %s (%s)" % (
        source or "встроенные значения", settings.source_kind))
    lines.append("сервер: %s (хост %s, порт %s)" % (
        ApiClient(settings.host, settings.port).base_url,
        settings.host, settings.port))
    lines.append("сервисов в конфиге: %d, интервал опроса сервера: %d c" % (
        len(settings.services), max(5, settings.interval)))

    client = ApiClient(settings.host, settings.port)
    state = None
    try:
        state = client.get_state(timeout=3.0)
        online = True
        lines.append("состояние: получено от работающего сервера")
    except ApiError as exc:
        online = False
        state = synthetic_state(settings)
        lines.append("состояние: сервер недоступен (%s) — синтетические данные" % exc)

    now = datetime.now()
    # Для selftest всегда демонстрируем форматирование статусов: при офлайне
    # state синтетический (выше это явно указано), но меню/tooltip должны
    # показать, как выглядят сервисы и причины сбоев.
    display_online = True
    lines.append("время: %s" % format_clock(now.timestamp()))
    lines.append("агрегированный статус: %s — %s" % (
        aggregate_status(state), aggregate_label(settings, state)))
    lines.append("tooltip:")
    for line in tooltip_text(settings, state, display_online, now).splitlines():
        lines.append("  | " + line)

    # Иконка: проверяем цвет для каждого статуса и (если есть Pillow) рендер.
    colors = settings.status_colors or {}
    lines.append("цвета иконки из конфига: %s" % ", ".join(
        "%s=%s" % (status, colors.get(status, "?"))
        for status in _server.SERVICE_STATUSES))
    dot_pt = int((settings.display.get("windows_tray") or {}).get("dot_pt") or 16)
    lines.append("размер точки трея: %d px (display.windows_tray.dot_pt)" % dot_pt)
    lines.append("цвет при недоступном сервере: %s (серый)" % status_color(
        settings, "unknown", False))
    pillow_version = None
    try:
        import PIL  # noqa: F401
        pillow_version = getattr(PIL, "__version__", "?")
    except ImportError:
        pillow_version = None
    if pillow_version:
        try:
            dot = make_dot_image(status_color(settings, aggregate_status(state), online),
                                 dot_pt)
            lines.append("иконка: %dx%d px отрисована (Pillow %s)" % (
                dot.size[0], dot.size[1], pillow_version))
        except Exception as exc:  # noqa: BLE001 - selftest не должен падать
            lines.append("иконка: ошибка отрисовки — %s" % exc)
    else:
        lines.append("иконка: Pillow не установлен — отрисовка не проверялась "
                     "(для GUI нужен pip install pystray pillow)")

    autostart = autostart_status()
    entries = menu_model(settings, state, display_online, checking=False,
                         autostart=autostart)
    lines.append("меню (%d пунктов):" % len(entries))
    for line in flatten_menu(entries):
        lines.append("  " + line)

    if autostart.get("available"):
        lines.append("автозапуск: %s" % ("включён" if autostart.get("enabled")
                                        else "выключен"))
    else:
        lines.append("автозапуск: недоступен (%s)" %
                     autostart.get("reason", "не Windows"))

    lines.append("РЕЗУЛЬТАТ: OK")
    output = "\n".join(lines)
    try:
        print(output, flush=True)
    except Exception:  # noqa: BLE001 - в windowed-сборке stdout может быть None
        pass
    return 0


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Системный трей монитора доступности сети (Windows)")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--selftest", action="store_true",
                        help="проверить логику меню/статусов без GUI и выйти")
    args = parser.parse_args(argv)

    try:
        settings = _server.load_settings(args.config)
    except _server.ConfigError as exc:
        sys.stderr.write(_server.format_config_error(exc) + "\n")
        try:
            sys.stderr.flush()
        except (AttributeError, OSError):
            pass
        return 2

    if args.selftest:
        return run_selftest(settings, args.config)
    config_source = settings.source if settings.source != "builtin" else None
    return run_tray(settings, config_source=config_source)


if __name__ == "__main__":
    sys.exit(main())
