#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
net-monitor — локальный дашборд проверки доступности сети (YouTube / Telegram).

Что делает:
  * каждые N секунд прогоняет набор сетевых проверок и раскладывает каждую
    на этапы DNS -> TCP -> TLS -> HTTP, замеряя время каждого этапа;
  * по точке отказа классифицирует состояние сервиса:
    ok / degraded / blocked (похоже на DPI-фильтрацию) / down / unknown;
  * ведёт историю опросов, открывает и закрывает инциденты недоступности;
  * отдаёт снимок состояния и журнал сбоев по HTTP API (см. docs/api-contract.md);
  * раздаёт статический дашборд из ./static.

Зависимостей нет — только стандартная библиотека Python 3.9+.

Настройки берутся из config.json (единый файл для macOS/Windows/Android):
приоритет — аргумент --config, переменная окружения NETMON_CONFIG, файл
config.json рядом со скриптом (или рядом с собранным exe), иначе встроенные
значения, совпадающие с историческим поведением v1. Ошибки конфигурации
печатаются в stderr, код выхода — 2.
"""

import argparse
import json
import os
import queue
import re
import signal
import socket
import ssl
import sys
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

VERSION = "1.0.0"


def _configure_utf8_stdio():
    """Принудительно UTF-8 для stdout/stderr.

    На Windows консоль/pipe по умолчанию cp1252: русский текст в print()
    падает с UnicodeEncodeError (а широкий except в selftest глушил вывод).
    """
    for name in ("stdout", "stderr"):
        stream = getattr(sys, name, None)
        if stream is None or not hasattr(stream, "reconfigure"):
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001 - в windowed-сборке поток может быть None
            pass


_configure_utf8_stdio()
# Каталог с кодом и ресурсами. В сборке PyInstaller onefile __file__ указывает
# внутрь временного каталога sys._MEIPASS, где лежат данные бандла (static/, config.json).
BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def bundle_dir():
    """Каталог с ресурсами бандла (у PyInstaller — sys._MEIPASS)."""
    return getattr(sys, "_MEIPASS", BASE_DIR)


def app_dir():
    """Каталог приложения: рядом с ним лежат изменяемые config.json и data/."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return BASE_DIR


def static_dir():
    """Каталог статики дашборда (в бандле — sys._MEIPASS/static)."""
    return os.path.join(bundle_dir(), "static")


def default_data_dir():
    """Каталог для state.json по умолчанию — рядом с приложением/скриптом."""
    return os.path.join(app_dir(), "data")


CONFIG_FILENAME = "config.json"

# Допустимые статусы сервиса/проверки и типы проверок.
SERVICE_STATUSES = ("ok", "degraded", "blocked", "down", "unknown")
CHECK_KINDS = ("dns", "tcp", "tls", "https")

# Сервис-«контрольный канал»: если он недоступен, остальные сервисы нельзя
# проверить и они классифицируются как down, а не blocked.
BASELINE_SERVICE_ID = "internet"

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")

ERROR_LABELS = {
    "dns_error": "DNS-запись не разрешается",
    "dns_timeout": "DNS-запрос не завершился",
    "connect_timeout": "TCP-соединение не устанавливается (таймаут)",
    "connect_refused": "TCP-соединение отклонено",
    "connect_unreachable": "Сеть недоступна",
    "tls_handshake_timeout": "TLS-рукопожатие не завершилось (таймаут)",
    "tls_reset": "TLS-соединение сброшено",
    "tls_cert_error": "Ошибка TLS-сертификата",
    "tls_error": "Ошибка TLS",
    "read_timeout": "Ответ не получен (таймаут)",
    "http_status_unexpected": "Неожиданный HTTP-статус",
    "http_error": "Ошибка HTTP-обмена",
    "unknown_error": "Неизвестная ошибка",
}

# Ошибки, которые при работающем интернете типичны для фильтрации трафика.
BLOCK_ERRORS = {
    "connect_timeout",
    "connect_unreachable",
    "tls_handshake_timeout",
    "tls_reset",
    "tls_error",
    "tls_cert_error",
    "read_timeout",
}

# Значения по умолчанию. Совпадают с историческим поведением v1 и с config.json
# из корня репозитория: если файла конфигурации нет, сервер работает ровно так же.
DEFAULT_STATUS_LABELS = {
    "ok": "Доступен",
    "degraded": "Нестабильно",
    "blocked": "Похоже на блокировку",
    "down": "Недоступен",
    "unknown": "Нет данных",
}

DEFAULT_STATUS_COLORS = {
    "ok": "#34c759",
    "degraded": "#ff9500",
    "blocked": "#ff3b30",
    "down": "#ff3b30",
    "unknown": "#8e8e93",
}

DEFAULT_NOTIFICATIONS = {
    "windows": {"enabled": True, "notify_on": ["blocked", "down"]},
    "android": {
        "enabled": True,
        "notify_on": ["blocked", "down"],
        "foreground_service": True,
        "sound": False,
        "vibrate": False,
    },
}


# --------------------------------------------------------------------------
# Описание проверок
# --------------------------------------------------------------------------

def _https(cid, label, host, path, expect, core=False, timeout=8, port=443):
    return {"id": cid, "label": label, "kind": "https", "host": host, "port": port,
            "path": path, "expect": list(expect), "core": core, "timeout": timeout}


def _tls(cid, label, host, core=False, timeout=8, port=443):
    return {"id": cid, "label": label, "kind": "tls", "host": host, "port": port,
            "core": core, "timeout": timeout}


def _tcp(cid, label, host, port=443, core=False, timeout=8):
    return {"id": cid, "label": label, "kind": "tcp", "host": host, "port": port,
            "core": core, "timeout": timeout}


def _dns(cid, label, host, core=False, timeout=8):
    return {"id": cid, "label": label, "kind": "dns", "host": host, "port": 443,
            "core": core, "timeout": timeout}


def _default_config():
    """Встроенная конфигурация — копия config.json v1 (на случай запуска без файла)."""
    return {
        "version": 1,
        "server": {"host": "127.0.0.1", "port": 8099},
        "polling": {"interval_sec": 20, "check_timeout_sec": 8, "dns_timeout_sec": 4},
        "incidents": {"open_after_failures": 2, "close_after_successes": 1, "max_stored": 500},
        "history": {"max_age_hours": 24, "max_points": 8640, "series_points": 180},
        "status_labels": dict(DEFAULT_STATUS_LABELS),
        "status_colors": dict(DEFAULT_STATUS_COLORS),
        "services": [
            {
                "id": "youtube",
                "name": "YouTube",
                "subtitle": "видеохостинг · youtube.com",
                "icon": "youtube",
                "accent": "#ff3040",
                "incident_title": "YouTube недоступен",
                "hints": {
                    "degraded": "Часть проверок не проходит — сравните с доступом через VPN",
                    "blocked": "Похоже на блокировку: проверьте доступ через VPN или мобильный интернет",
                    "down": "Сервис не отвечает — проверьте соединение и повторите позже",
                },
                "checks": [
                    _https("yt_https", "HTTPS /generate_204", "www.youtube.com",
                           "/generate_204", [204], True),
                    _https("yt_img", "HTTPS i.ytimg.com /generate_204", "i.ytimg.com",
                           "/generate_204", [204], True),
                    _https("yt_www", "HTTPS главная страница", "www.youtube.com", "/",
                           [200, 301, 302, 303]),
                    _tcp("yt_tcp", "TCP www.youtube.com:443", "www.youtube.com"),
                    _dns("yt_dns", "DNS www.youtube.com", "www.youtube.com"),
                ],
            },
            {
                "id": "telegram",
                "name": "Telegram",
                "subtitle": "мессенджер · telegram.org",
                "icon": "telegram",
                "accent": "#2ea6e0",
                "incident_title": "Telegram недоступен",
                "hints": {
                    "degraded": "Часть проверок не проходит — возможна фильтрация MTProto",
                    "blocked": "TCP/TLS к Telegram не проходит: вероятна блокировка — включите VPN",
                    "down": "Сервис не отвечает — проверьте соединение и повторите позже",
                },
                "checks": [
                    _https("tg_web", "HTTPS web.telegram.org", "web.telegram.org", "/",
                           [200, 301, 302], True),
                    _https("tg_api", "HTTPS api.telegram.org", "api.telegram.org", "/",
                           [200, 301, 302, 400, 404], True),
                    _tcp("tg_dc2", "TCP DC2 149.154.167.51:443", "149.154.167.51", 443, True),
                    _tcp("tg_dc1", "TCP DC1 149.154.175.50:443", "149.154.175.50", 443),
                    _tcp("tg_dc4", "TCP DC4 149.154.167.91:443", "149.154.167.91", 443),
                    _dns("tg_dns_api", "DNS api.telegram.org", "api.telegram.org"),
                    _dns("tg_dns_web", "DNS web.telegram.org", "web.telegram.org"),
                ],
            },
            {
                "id": "internet",
                "name": "Интернет",
                "subtitle": "контрольный канал · gstatic / cloudflare",
                "icon": "globe",
                "accent": "#38d39f",
                "incident_title": "Интернет недоступен",
                "hints": {
                    "degraded": "Канал нестабилен — проверьте Wi-Fi/роутер",
                    "down": "Проверьте Wi-Fi/кабель и роутер: базовые проверки не проходят",
                },
                "checks": [
                    _https("inet_gstatic", "HTTPS connectivitycheck.gstatic.com",
                           "connectivitycheck.gstatic.com", "/generate_204", [204], True),
                    _https("inet_cloudflare", "HTTPS cloudflare.com/cdn-cgi/trace",
                           "cloudflare.com", "/cdn-cgi/trace", [200], True),
                    _tcp("inet_tcp", "TCP 1.1.1.1:443", "1.1.1.1", 443, True),
                    _dns("inet_dns", "DNS cloudflare.com", "cloudflare.com"),
                ],
            },
        ],
        "display": {
            "macos_menubar": {
                "mode": "overlay",
                "overlay_window_pt": 24,
                "overlay_dot_pt": 9.6,
                "status_item_pt": 22,
            },
            "windows_tray": {"mode": "tray", "dot_pt": 16},
        },
        "notifications": DEFAULT_NOTIFICATIONS,
    }


# --------------------------------------------------------------------------
# Загрузка, валидация и применение конфигурации
# --------------------------------------------------------------------------

class ConfigError(Exception):
    """Ошибка конфигурации. CLI печатает её в stderr и завершается кодом 2."""

    def __init__(self, errors):
        if isinstance(errors, str):
            errors = [errors]
        self.errors = list(errors)
        self.source = None
        Exception.__init__(self, "; ".join(self.errors))


class Settings(object):
    """Нормализованные настройки монитора (результат разбора config.json)."""

    def __init__(self):
        self.source = "builtin"
        self.source_kind = "встроенные значения"
        self.raw = {}
        self.host = "127.0.0.1"
        self.port = 8099
        self.interval = 20
        self.check_timeout = 8.0
        self.dns_timeout = 4.0
        self.open_after_fails = 2
        self.close_after_oks = 1
        self.max_incidents = 500
        self.history_age_sec = 24 * 3600
        self.history_max_points = 8640
        self.series_points = 180
        self.slow_latency_ms = 4000
        self.status_labels = dict(DEFAULT_STATUS_LABELS)
        self.status_colors = dict(DEFAULT_STATUS_COLORS)
        self.services = []
        self.hints = {}
        self.notifications = {
            "windows": {"enabled": True, "notify_on": ["blocked", "down"]},
        }
        self.display = {
            "windows_tray": {"mode": "tray", "dot_pt": 16},
            "macos_menubar": {},
        }


def format_config_error(exc):
    """Человекочитаемый текст ошибки конфигурации для stderr."""
    header = "Ошибка конфигурации"
    if exc.source and exc.source != "builtin":
        header += " (%s)" % exc.source
    lines = [header + ":"]
    for item in exc.errors:
        lines.append("  - %s" % item)
    return "\n".join(lines)


def _kind_of(value):
    if isinstance(value, bool):
        return "true/false"
    if isinstance(value, dict):
        return "объект"
    if isinstance(value, list):
        return "список"
    if isinstance(value, str):
        return "строка"
    if isinstance(value, int):
        return "целое число"
    if isinstance(value, float):
        return "число"
    if value is None:
        return "null"
    return type(value).__name__


def _get_section(raw, key, errors):
    value = raw.get(key, {})
    if value is None:
        return {}
    if not isinstance(value, dict):
        errors.append("%s: ожидается JSON-объект, получено %s" % (key, _kind_of(value)))
        return {}
    return value


def _get_str(obj, key, ctx, errors, default=None, required=False):
    value = obj.get(key)
    if value is None:
        if required:
            errors.append("%s.%s: обязательное строковое поле" % (ctx, key))
        return default
    if not isinstance(value, str):
        errors.append("%s.%s: ожидается строка, получено %s" % (ctx, key, _kind_of(value)))
        return default
    if not value.strip():
        if required:
            errors.append("%s.%s: пустая строка" % (ctx, key))
        return default
    return value


def _get_bool(obj, key, ctx, errors, default):
    value = obj.get(key, default)
    if isinstance(value, bool):
        return value
    errors.append("%s.%s: ожидается true/false, получено %s" % (ctx, key, _kind_of(value)))
    return default


def _get_int(obj, key, ctx, errors, default, minimum=None, maximum=None):
    value = obj.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append("%s.%s: ожидается целое число, получено %s" % (
            ctx, key, _kind_of(value)))
        return default
    if minimum is not None and value < minimum:
        errors.append("%s.%s: значение %r меньше допустимого минимума %r" % (
            ctx, key, value, minimum))
        return default
    if maximum is not None and value > maximum:
        errors.append("%s.%s: значение %r больше допустимого максимума %r" % (
            ctx, key, value, maximum))
        return default
    return value


def _get_number(obj, key, ctx, errors, default, minimum=None, maximum=None):
    value = obj.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        errors.append("%s.%s: ожидается число, получено %s" % (ctx, key, _kind_of(value)))
        return default
    value = float(value)
    if minimum is not None and value < minimum:
        errors.append("%s.%s: значение %r меньше допустимого минимума %r" % (
            ctx, key, value, minimum))
        return default
    if maximum is not None and value > maximum:
        errors.append("%s.%s: значение %r больше допустимого максимума %r" % (
            ctx, key, value, maximum))
        return default
    return value


def _is_color(value):
    return isinstance(value, str) and re.match(r"^#[0-9a-fA-F]{6}$", value.strip()) is not None


def _get_color(obj, key, ctx, errors, default):
    value = obj.get(key)
    if value is None:
        return default
    if not _is_color(value):
        errors.append("%s.%s: ожидается цвет вида #rrggbb, получено %r" % (
            ctx, key, value))
        return default
    return value


def _get_status_list(obj, key, ctx, errors, default):
    value = obj.get(key, default)
    if value is None:
        return list(default)
    if not isinstance(value, list):
        errors.append("%s.%s: ожидается список статусов, получено %s" % (
            ctx, key, _kind_of(value)))
        return list(default)
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or item not in SERVICE_STATUSES:
            errors.append("%s.%s[%d]: ожидается один из статусов (%s), получено %r" % (
                ctx, key, index, ", ".join(SERVICE_STATUSES), item))
            continue
        result.append(item)
    return result or list(default)


def _normalize_check(raw, index, service_ctx, errors, check_timeout):
    """Проверить и нормализовать одну проверку сервиса."""
    ctx = "%s.checks[%d]" % (service_ctx, index)
    if not isinstance(raw, dict):
        errors.append("%s: ожидается JSON-объект, получено %s" % (ctx, _kind_of(raw)))
        return None
    check_id = _get_str(raw, "id", ctx, errors, required=True)
    kind = _get_str(raw, "kind", ctx, errors, required=True)
    host = _get_str(raw, "host", ctx, errors, required=True)
    label = _get_str(raw, "label", ctx, errors, default=None)
    port = _get_int(raw, "port", ctx, errors, 443, 1, 65535)
    core = _get_bool(raw, "core", ctx, errors, False)
    timeout = _get_number(raw, "timeout", ctx, errors, check_timeout, 0.5, 300.0)
    if kind is not None and kind not in CHECK_KINDS:
        errors.append("%s.kind: неизвестный тип проверки %r (допустимо: %s)" % (
            ctx, kind, ", ".join(CHECK_KINDS)))
        kind = None
    if host is not None and (" " in host or "/" in host):
        errors.append("%s.host: некорректный хост %r (ожидается домен или IP)" % (ctx, host))
    check = {
        "id": check_id,
        "label": label or ("%s %s" % ((kind or "CHECK").upper(), host or "?")),
        "kind": kind,
        "host": host,
        "port": port,
        "core": core,
        "timeout": timeout,
    }
    if kind == "https":
        path = _get_str(raw, "path", ctx, errors, default="/")
        if path is not None and not path.startswith("/"):
            errors.append("%s.path: должен начинаться с '/', получено %r" % (ctx, path))
            path = "/" + path.lstrip("/")
        check["path"] = path or "/"
        expect_raw = raw.get("expect", [200])
        expect = []
        if not isinstance(expect_raw, list) or not expect_raw:
            errors.append("%s.expect: ожидается непустой список HTTP-статусов, "
                          "например [200] или [204]" % ctx)
        else:
            for status_index, status in enumerate(expect_raw):
                if isinstance(status, bool) or not isinstance(status, int) \
                        or not 100 <= status <= 599:
                    errors.append("%s.expect[%d]: ожидается HTTP-статус 100..599, "
                                  "получено %r" % (ctx, status_index, status))
                    continue
                expect.append(status)
        check["expect"] = expect or [200]
    else:
        if "path" in raw:
            errors.append("%s.path: допустим только для kind=https" % ctx)
        if "expect" in raw:
            errors.append("%s.expect: допустим только для kind=https" % ctx)
    if check_id is None or kind is None or host is None:
        return None
    return check


def _normalize_services(raw, errors, check_timeout):
    """Проверить сервисы; вернуть (список сервисов, карта подсказок)."""
    services_raw = raw.get("services")
    if not isinstance(services_raw, list) or not services_raw:
        errors.append("services: обязательный непустой список сервисов")
        return [], {}
    services = []
    hints_map = {}
    seen_services = set()
    seen_checks = set()
    for service_index, service_raw in enumerate(services_raw):
        ctx = "services[%d]" % service_index
        if not isinstance(service_raw, dict):
            errors.append("%s: ожидается JSON-объект, получено %s" % (
                ctx, _kind_of(service_raw)))
            continue
        service_id = _get_str(service_raw, "id", ctx, errors, required=True)
        name = _get_str(service_raw, "name", ctx, errors, required=True)
        if service_id is not None:
            if not re.match(r"^[a-z0-9][a-z0-9_-]*$", service_id):
                errors.append("%s.id: допустимы строчные латинские буквы, цифры, "
                              "'_' и '-' (получено %r)" % (ctx, service_id))
            if service_id in seen_services:
                errors.append("%s.id: идентификатор %r уже используется" % (
                    ctx, service_id))
            seen_services.add(service_id)

        subtitle = _get_str(service_raw, "subtitle", ctx, errors, default="")
        icon = _get_str(service_raw, "icon", ctx, errors, default="globe")
        accent = _get_color(service_raw, "accent", ctx, errors, "#8e8e93")
        incident_title = _get_str(service_raw, "incident_title", ctx, errors, default=None)

        hints = {}
        hints_raw = service_raw.get("hints", {})
        if hints_raw is None:
            hints_raw = {}
        if not isinstance(hints_raw, dict):
            errors.append("%s.hints: ожидается JSON-объект, получено %s" % (
                ctx, _kind_of(hints_raw)))
        else:
            for status, text in hints_raw.items():
                if status not in SERVICE_STATUSES:
                    errors.append("%s.hints: неизвестный статус %r (допустимо: %s)" % (
                        ctx, status, ", ".join(SERVICE_STATUSES)))
                    continue
                if not isinstance(text, str) or not text.strip():
                    errors.append("%s.hints.%s: ожидается непустая строка" % (ctx, status))
                    continue
                hints[status] = text

        checks = []
        checks_raw = service_raw.get("checks")
        if not isinstance(checks_raw, list) or not checks_raw:
            errors.append("%s.checks: обязательный непустой список проверок" % ctx)
        else:
            for check_index, check_raw in enumerate(checks_raw):
                check = _normalize_check(check_raw, check_index, ctx, errors, check_timeout)
                if check is None:
                    continue
                if check["id"] in seen_checks:
                    errors.append("%s.checks[%d].id: идентификатор проверки %r "
                                  "уже используется в другом сервисе" % (
                                      ctx, check_index, check["id"]))
                seen_checks.add(check["id"])
                checks.append(check)

        if service_id and name and checks:
            services.append({
                "id": service_id,
                "name": name,
                "subtitle": subtitle or "",
                "icon": icon or "globe",
                "accent": accent,
                "incident_title": incident_title or ("%s недоступен" % name),
                "hints": hints,
                "checks": checks,
            })
            hints_map[service_id] = hints
    if not services and not errors:
        errors.append("services: ни одного корректного сервиса не найдено")
    return services, hints_map


def _normalize_config(raw, settings, source):
    """Проверить сырой JSON и заполнить settings; при ошибках — ConfigError."""
    if not isinstance(raw, dict):
        error = ConfigError("%s: корневой элемент должен быть JSON-объектом" % source)
        error.source = source
        raise error
    errors = []
    version = raw.get("version", 1)
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        errors.append("version: ожидается целое число >= 1, получено %r" % (version,))

    server = _get_section(raw, "server", errors)
    settings.host = _get_str(server, "host", "server", errors, default="127.0.0.1")
    if not settings.host:
        settings.host = "127.0.0.1"
    if " " in settings.host or "/" in settings.host:
        errors.append("server.host: некорректный адрес %r" % settings.host)
    settings.port = _get_int(server, "port", "server", errors, 8099, 1, 65535)

    polling = _get_section(raw, "polling", errors)
    settings.interval = _get_int(polling, "interval_sec", "polling", errors, 20, 5, 86400)
    settings.check_timeout = _get_number(polling, "check_timeout_sec", "polling",
                                         errors, 8.0, 0.5, 300.0)
    settings.dns_timeout = _get_number(polling, "dns_timeout_sec", "polling",
                                       errors, 4.0, 0.5, 60.0)

    incidents = _get_section(raw, "incidents", errors)
    settings.open_after_fails = _get_int(incidents, "open_after_failures", "incidents",
                                         errors, 2, 1, 1000)
    settings.close_after_oks = _get_int(incidents, "close_after_successes", "incidents",
                                        errors, 1, 1, 1000)
    settings.max_incidents = _get_int(incidents, "max_stored", "incidents",
                                      errors, 500, 1, 1000000)

    history = _get_section(raw, "history", errors)
    max_age_hours = _get_number(history, "max_age_hours", "history", errors,
                                24.0, 0.1, 24.0 * 365)
    settings.history_age_sec = int(round(max_age_hours * 3600))
    settings.history_max_points = _get_int(history, "max_points", "history",
                                           errors, 8640, 1, 10000000)
    settings.series_points = _get_int(history, "series_points", "history",
                                      errors, 180, 1, 100000)

    classification = _get_section(raw, "classification", errors)
    settings.slow_latency_ms = _get_int(classification, "slow_latency_ms", "classification",
                                        errors, 4000, 100, 600000)

    labels_raw = raw.get("status_labels", {})
    if labels_raw is None:
        labels_raw = {}
    if not isinstance(labels_raw, dict):
        errors.append("status_labels: ожидается JSON-объект, получено %s" % (
            _kind_of(labels_raw)))
    else:
        labels = dict(DEFAULT_STATUS_LABELS)
        for status, text in labels_raw.items():
            if status not in SERVICE_STATUSES:
                errors.append("status_labels: неизвестный статус %r (допустимо: %s)" % (
                    status, ", ".join(SERVICE_STATUSES)))
                continue
            if not isinstance(text, str) or not text.strip():
                errors.append("status_labels.%s: ожидается непустая строка" % status)
                continue
            labels[status] = text
        settings.status_labels = labels

    colors_raw = raw.get("status_colors", {})
    if colors_raw is None:
        colors_raw = {}
    if not isinstance(colors_raw, dict):
        errors.append("status_colors: ожидается JSON-объект, получено %s" % (
            _kind_of(colors_raw)))
    else:
        colors = dict(DEFAULT_STATUS_COLORS)
        for status, color in colors_raw.items():
            if status not in SERVICE_STATUSES:
                errors.append("status_colors: неизвестный статус %r (допустимо: %s)" % (
                    status, ", ".join(SERVICE_STATUSES)))
                continue
            if not _is_color(color):
                errors.append("status_colors.%s: ожидается цвет вида #rrggbb, "
                              "получено %r" % (status, color))
                continue
            colors[status] = color
        settings.status_colors = colors

    display = _get_section(raw, "display", errors)
    tray_raw = display.get("windows_tray", {})
    if tray_raw is None:
        tray_raw = {}
    if not isinstance(tray_raw, dict):
        errors.append("display.windows_tray: ожидается JSON-объект, получено %s" % (
            _kind_of(tray_raw)))
        tray_raw = {}
    tray_mode = _get_str(tray_raw, "mode", "display.windows_tray", errors, default="tray")
    dot_pt = _get_int(tray_raw, "dot_pt", "display.windows_tray", errors, 16, 8, 64)
    menubar_raw = display.get("macos_menubar")
    settings.display = {
        "windows_tray": {"mode": tray_mode or "tray", "dot_pt": dot_pt},
        "macos_menubar": menubar_raw if isinstance(menubar_raw, dict) else {},
    }

    notifications = _get_section(raw, "notifications", errors)
    windows_notify = notifications.get("windows", {})
    if windows_notify is None:
        windows_notify = {}
    if not isinstance(windows_notify, dict):
        errors.append("notifications.windows: ожидается JSON-объект, получено %s" % (
            _kind_of(windows_notify)))
        windows_notify = {}
    notify_enabled = _get_bool(windows_notify, "enabled", "notifications.windows",
                               errors, True)
    notify_on = _get_status_list(windows_notify, "notify_on", "notifications.windows",
                                 errors, ["blocked", "down"])
    settings.notifications = {
        "windows": {"enabled": notify_enabled, "notify_on": notify_on},
        "android": notifications.get("android") or {},
    }

    services, hints = _normalize_services(raw, errors, settings.check_timeout)
    settings.services = services
    settings.hints = hints

    if errors:
        error = ConfigError(errors)
        error.source = source
        raise error


def _build_settings(raw, source, source_kind):
    settings = Settings()
    settings.source = source
    settings.source_kind = source_kind
    settings.raw = raw
    _normalize_config(raw, settings, source)
    return settings


def _candidate_config_paths():
    """config.json рядом с приложением (exe/скрипт), затем внутри бандла PyInstaller."""
    paths = []
    for base in (app_dir(), bundle_dir()):
        path = os.path.join(base, CONFIG_FILENAME)
        if path not in paths:
            paths.append(path)
    return paths


def resolve_config_path(explicit=None):
    """Найти config.json по приоритету: --config, NETMON_CONFIG, рядом с приложением.

    Возвращает (путь или None, описание источника). Если явно указанный файл
    не существует — ConfigError (код выхода 2).
    """
    if explicit:
        path = os.path.abspath(os.path.expanduser(str(explicit)))
        if not os.path.isfile(path):
            error = ConfigError("файл конфигурации не найден: %s (аргумент --config)" % path)
            error.source = path
            raise error
        return path, "аргумент --config"
    env_path = os.environ.get("NETMON_CONFIG")
    if env_path:
        path = os.path.abspath(os.path.expanduser(env_path))
        if not os.path.isfile(path):
            error = ConfigError("файл конфигурации не найден: %s "
                                "(переменная NETMON_CONFIG)" % path)
            error.source = path
            raise error
        return path, "переменная NETMON_CONFIG"
    for path in _candidate_config_paths():
        if os.path.isfile(path):
            return path, "файл рядом с приложением"
    return None, "встроенные значения"


def load_settings(explicit_path=None):
    """Загрузить и провалидировать настройки; см. resolve_config_path."""
    path, source_kind = resolve_config_path(explicit_path)
    if path is None:
        raw = _default_config()
        source = "builtin"
    else:
        source = path
        try:
            with open(path, "r", encoding="utf-8-sig") as handle:
                raw = json.load(handle)
        except OSError as exc:
            error = ConfigError("не удалось прочитать файл: %s" % exc)
            error.source = path
            raise error
        except ValueError as exc:
            error = ConfigError("некорректный JSON: %s" % exc)
            error.source = path
            raise error
    return _build_settings(raw, source, source_kind)


def configure(settings):
    """Применить настройки к глобальным константам модуля."""
    global SERVICES, SERVICE_BY_ID, HINTS, STATUS_LABELS, STATUS_COLORS
    global OPEN_AFTER_FAILS, CLOSE_AFTER_OKS, SERIES_MAX_AGE, SERIES_MAX_POINTS
    global SERIES_POINTS, POLL_INTERVAL, CHECK_TIMEOUT, DNS_TIMEOUT
    global SERVER_HOST, SERVER_PORT, INCIDENTS_MAX_STORED, SLOW_LATENCY_MS
    global NOTIFICATIONS, DISPLAY, ACTIVE_SETTINGS

    SERVICES = settings.services
    SERVICE_BY_ID = {svc["id"]: svc for svc in SERVICES}
    HINTS = dict(settings.hints)
    STATUS_LABELS = dict(settings.status_labels)
    STATUS_COLORS = dict(settings.status_colors)
    OPEN_AFTER_FAILS = settings.open_after_fails
    CLOSE_AFTER_OKS = settings.close_after_oks
    SERIES_MAX_AGE = settings.history_age_sec
    SERIES_MAX_POINTS = settings.history_max_points
    SERIES_POINTS = settings.series_points
    POLL_INTERVAL = settings.interval
    CHECK_TIMEOUT = settings.check_timeout
    DNS_TIMEOUT = settings.dns_timeout
    SERVER_HOST = settings.host
    SERVER_PORT = settings.port
    INCIDENTS_MAX_STORED = settings.max_incidents
    SLOW_LATENCY_MS = settings.slow_latency_ms
    NOTIFICATIONS = settings.notifications
    DISPLAY = settings.display
    ACTIVE_SETTINGS = settings


# При импорте модуля действуют встроенные значения (историческое поведение v1).
# CLI (main) заменяет их настройками из config.json; entry.py делает то же явно.
configure(_build_settings(_default_config(), "builtin", "встроенные значения"))


# --------------------------------------------------------------------------
# Вспомогательные функции времени / DNS / проверок
# --------------------------------------------------------------------------

def iso_now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def iso_from_epoch(epoch):
    try:
        return datetime.fromtimestamp(float(epoch)).astimezone().isoformat(timespec="seconds")
    except (ValueError, OSError, OverflowError):
        return iso_now()


def _is_ip_literal(host):
    try:
        socket.inet_pton(socket.AF_INET, host)
        return True
    except OSError:
        pass
    try:
        socket.inet_pton(socket.AF_INET6, host)
        return True
    except OSError:
        return False


def resolve_host(host, timeout):
    """Возвращает список IP. DNS-вызов ограничен по времени (daemon-поток)."""
    if _is_ip_literal(host):
        return [host]
    box = queue.Queue(maxsize=1)

    def runner():
        try:
            box.put((True, socket.getaddrinfo(host, 0, type=socket.SOCK_STREAM)))
        except BaseException as exc:  # noqa: BLE001 - пробрасываем как есть
            box.put((False, exc))

    thread = threading.Thread(target=runner, name="dns-helper", daemon=True)
    thread.start()
    try:
        ok, value = box.get(timeout=timeout)
    except queue.Empty:
        raise TimeoutError("DNS timeout")
    if not ok:
        raise value
    seen = []
    for info in value:
        ip = info[4][0]
        if ip not in seen:
            seen.append(ip)
    ipv4 = [ip for ip in seen if ":" not in ip]
    return ipv4 or seen


def check_target(spec):
    host = spec["host"]
    port = spec.get("port", 443)
    kind = spec["kind"]
    if kind == "dns":
        return host
    if kind == "tcp":
        return "%s:%s" % (host, port)
    hostport = host if port == 443 else "%s:%s" % (host, port)
    return "%s%s" % (hostport, spec.get("path", "/"))


TLS_CONTEXT = ssl.create_default_context()
TLS_CONTEXT.check_hostname = True
TLS_CONTEXT.verify_mode = ssl.CERT_REQUIRED


class CheckError(Exception):
    def __init__(self, code, stage, text):
        super().__init__(text)
        self.code = code
        self.stage = stage
        self.text = text


def _mk_result(spec, ok, latency_ms, stage, error=None, error_text=None,
               http_status=None, ip=None, timings=None):
    return {
        "id": spec["id"],
        "label": spec["label"],
        "target": check_target(spec),
        "kind": spec["kind"],
        "core": bool(spec.get("core")),
        "ok": bool(ok),
        "latency_ms": latency_ms,
        "stage": stage,
        "error": error,
        "error_label": ERROR_LABELS.get(error) if error else None,
        "error_text": error_text,
        "http_status": http_status,
        "ip": ip,
        "timings": timings or {"dns_ms": None, "tcp_ms": None, "tls_ms": None, "http_ms": None},
        "measured_at": iso_now(),
    }


def run_check(spec):
    """Выполняет одну проверку: DNS -> TCP -> TLS -> HTTP, замеряя этапы."""
    timings = {"dns_ms": None, "tcp_ms": None, "tls_ms": None, "http_ms": None}
    host = spec["host"]
    port = int(spec.get("port", 443))
    kind = spec["kind"]
    timeout = float(spec.get("timeout", CHECK_TIMEOUT))
    t_start = time.monotonic()
    deadline = t_start + timeout

    ip = None
    http_status = None
    sock = None
    ssock = None

    def elapsed_ms():
        return int(round((time.monotonic() - t_start) * 1000))

    def remaining(floor=0.5):
        return max(floor, deadline - time.monotonic())

    try:
        # --- DNS ---------------------------------------------------------
        dns_t0 = time.monotonic()
        try:
            addresses = resolve_host(host, min(DNS_TIMEOUT, remaining()))
        except TimeoutError:
            raise CheckError("dns_timeout", "dns", "getaddrinfo timeout")
        except socket.gaierror as exc:
            raise CheckError("dns_error", "dns", str(exc))
        timings["dns_ms"] = int(round((time.monotonic() - dns_t0) * 1000))
        ip = addresses[0]
        if kind == "dns":
            return _mk_result(spec, True, elapsed_ms(), "ok", ip=ip, timings=dict(timings))

        # --- TCP ---------------------------------------------------------
        tcp_t0 = time.monotonic()
        try:
            sock = socket.create_connection((ip, port), timeout=remaining())
        except (socket.timeout, TimeoutError):
            raise CheckError("connect_timeout", "tcp",
                             "connect() timeout %.0fs" % timeout)
        except ConnectionRefusedError:
            raise CheckError("connect_refused", "tcp", "ECONNREFUSED")
        except OSError as exc:
            raise CheckError("connect_unreachable", "tcp", "%s: %s" % (type(exc).__name__, exc))
        timings["tcp_ms"] = int(round((time.monotonic() - tcp_t0) * 1000))
        if kind == "tcp":
            return _mk_result(spec, True, elapsed_ms(), "ok", ip=ip, timings=dict(timings))

        # --- TLS ---------------------------------------------------------
        tls_t0 = time.monotonic()
        sock.settimeout(remaining())
        try:
            ssock = TLS_CONTEXT.wrap_socket(sock, server_hostname=host)
        except ssl.SSLCertVerificationError as exc:
            raise CheckError("tls_cert_error", "tls", str(exc))
        except ssl.SSLError as exc:
            message = ("%s %s" % (getattr(exc, "reason", "") or "", exc)).lower()
            if "timed out" in message or "timeout" in message:
                raise CheckError("tls_handshake_timeout", "tls",
                                 "SSL handshake timeout %.0fs" % timeout)
            if "reset" in message or "eof" in message or "closed" in message:
                raise CheckError("tls_reset", "tls", str(exc))
            raise CheckError("tls_error", "tls", str(exc))
        except (socket.timeout, TimeoutError):
            raise CheckError("tls_handshake_timeout", "tls",
                             "SSL handshake timeout %.0fs" % timeout)
        except OSError as exc:
            message = str(exc).lower()
            if "reset" in message or "eof" in message or "closed" in message:
                raise CheckError("tls_reset", "tls", str(exc))
            raise CheckError("tls_error", "tls", str(exc))
        timings["tls_ms"] = int(round((time.monotonic() - tls_t0) * 1000))
        if kind == "tls":
            return _mk_result(spec, True, elapsed_ms(), "ok", ip=ip, timings=dict(timings))

        # --- HTTP --------------------------------------------------------
        ssock.settimeout(remaining())
        path = spec.get("path", "/")
        request = (
            "GET %s HTTP/1.1\r\n"
            "Host: %s\r\n"
            "User-Agent: %s\r\n"
            "Accept: */*\r\n"
            "Accept-Language: ru-RU,ru;q=0.9,en;q=0.8\r\n"
            "Connection: close\r\n\r\n" % (path, host, UA)
        ).encode("ascii", "replace")
        http_t0 = time.monotonic()
        try:
            ssock.sendall(request)
            data = ssock.recv(1024)
        except (socket.timeout, TimeoutError):
            raise CheckError("read_timeout", "http", "recv() timeout %.0fs" % timeout)
        except ssl.SSLError as exc:
            raise CheckError("http_error", "http", str(exc))
        except OSError as exc:
            raise CheckError("http_error", "http", "%s: %s" % (type(exc).__name__, exc))
        timings["http_ms"] = int(round((time.monotonic() - http_t0) * 1000))
        if not data:
            raise CheckError("http_error", "http", "Пустой ответ сервера")
        try:
            status_line = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
            http_status = int(status_line.split()[1])
        except (IndexError, ValueError):
            raise CheckError("http_error", "http", "Не удалось разобрать HTTP-статус")
        expect = spec.get("expect") or [200]
        if http_status not in expect:
            raise CheckError("http_status_unexpected", "http",
                             "HTTP %s (expected %s)" % (http_status, expect))
        return _mk_result(spec, True, elapsed_ms(), "ok", http_status=http_status,
                          ip=ip, timings=dict(timings))

    except CheckError as exc:
        return _mk_result(spec, False, elapsed_ms(), exc.stage, exc.code, exc.text,
                          http_status, ip, dict(timings))
    except Exception as exc:  # noqa: BLE001 - любая неожиданность не должна ронять цикл
        return _mk_result(spec, False, elapsed_ms(), "unknown", "unknown_error",
                          "%s: %s" % (type(exc).__name__, exc), http_status, ip, dict(timings))
    finally:
        for handle in (ssock, sock):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass


def _reason_text(check):
    label = check.get("error_label") or "Ошибка проверки"
    return "%s — %s" % (label, check.get("target"))


# --------------------------------------------------------------------------
# Монитор: цикл опросов, классификация, инциденты, персистентность
# --------------------------------------------------------------------------

class Monitor:
    def __init__(self, interval=None, data_dir=None):
        if interval is None:
            interval = POLL_INTERVAL
        # Нижняя граница 5 с — как в историческом поведении v1.
        self.interval = max(5, int(interval))
        self.data_dir = data_dir or default_data_dir()
        os.makedirs(self.data_dir, exist_ok=True)
        self.state_file = os.path.join(self.data_dir, "state.json")

        self.lock = threading.RLock()
        self.round_lock = threading.Lock()
        self.stop_event = threading.Event()

        self.series = {svc["id"]: [] for svc in SERVICES}
        self.last_results = {}
        self.incidents = []
        self.streaks = {svc["id"]: {"fail": 0, "ok": 0, "first_fail": None} for svc in SERVICES}
        self.version = 0
        self.rounds = 0
        self.checking = False
        self.started_epoch = time.time()
        self.last_round_epoch = None
        self.next_round_epoch = None
        self._load()

    # --- персистентность --------------------------------------------------
    def _load(self):
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, ValueError):
            return
        try:
            for sid in self.series:
                points = payload.get("series", {}).get(sid) or []
                if isinstance(points, list):
                    self.series[sid] = [p for p in points if isinstance(p, dict) and "t" in p]
            incidents = payload.get("incidents") or []
            if isinstance(incidents, list):
                self.incidents = [inc for inc in incidents if isinstance(inc, dict) and "id" in inc]
            streaks = payload.get("streaks") or {}
            for sid in self.streaks:
                value = streaks.get(sid)
                if isinstance(value, dict):
                    self.streaks[sid] = {
                        "fail": int(value.get("fail") or 0),
                        "ok": int(value.get("ok") or 0),
                        "first_fail": value.get("first_fail"),
                    }
            results = payload.get("last_results") or {}
            if isinstance(results, dict):
                self.last_results = {k: v for k, v in results.items() if isinstance(v, dict)}
            self.version = int(payload.get("version_counter") or 0)
            self.rounds = int(payload.get("rounds") or 0)
            self.last_round_epoch = payload.get("last_round_epoch")
            # незакрытые инциденты после рестарта остаются активными,
            # но "first_fail" не должен ссылаться на прошлые сутки
            for inc in self.incidents:
                if inc.get("status") == "active" and not inc.get("ended_epoch"):
                    inc["status"] = "active"
        except (TypeError, ValueError):
            # повреждённый файл не должен мешать запуску
            pass

    def _save(self):
        payload = {
            "saved_at": iso_now(),
            "state_version": 1,
            "interval": self.interval,
            "version_counter": self.version,
            "rounds": self.rounds,
            "last_round_epoch": self.last_round_epoch,
            "series": self.series,
            "incidents": self.incidents,
            "streaks": self.streaks,
            "last_results": self.last_results,
        }
        tmp_path = self.state_file + ".tmp"
        try:
            with open(tmp_path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp_path, self.state_file)
        except OSError as exc:
            print("[warn] не удалось сохранить состояние: %s" % exc, flush=True)

    # --- цикл -------------------------------------------------------------
    def start(self):
        thread = threading.Thread(target=self._loop, name="monitor", daemon=True)
        thread.start()

    def _loop(self):
        time.sleep(0.25)
        while not self.stop_event.is_set():
            cycle_start = time.monotonic()
            try:
                self.run_round(blocking=True)
            except Exception as exc:  # noqa: BLE001 - цикл не должен умирать
                print("[error] опрос не удался: %s: %s" % (type(exc).__name__, exc), flush=True)
            spent = time.monotonic() - cycle_start
            wait_seconds = max(1.0, self.interval - spent)
            with self.lock:
                self.next_round_epoch = time.time() + wait_seconds
            self.stop_event.wait(wait_seconds)

    def stop(self):
        self.stop_event.set()
        with self.lock:
            self._save()

    def run_round(self, blocking=True):
        """Один цикл проверок. Возвращает False, если цикл уже идёт и blocking=False."""
        if not self.round_lock.acquire(blocking=blocking):
            return False
        try:
            self._do_round()
            return True
        finally:
            self.round_lock.release()

    def _do_round(self):
        round_started = time.time()
        with self.lock:
            self.checking = True
        specs = [(svc["id"], check) for svc in SERVICES for check in svc["checks"]]
        results_by_id = {}
        try:
            with ThreadPoolExecutor(max_workers=min(16, max(4, len(specs)))) as pool:
                futures = {pool.submit(run_check, check): check for _, check in specs}
                for future in as_completed(futures):
                    check = futures[future]
                    try:
                        results_by_id[check["id"]] = future.result()
                    except Exception as exc:  # noqa: BLE001
                        results_by_id[check["id"]] = _mk_result(
                            check, False, 0, "unknown", "unknown_error", str(exc))
        finally:
            with self.lock:
                self.checking = False

        by_service = {
            svc["id"]: [results_by_id[chk["id"]] for chk in svc["checks"]]
            for svc in SERVICES
        }
        self._apply_round(by_service)
        duration = time.time() - round_started
        with self.lock:
            self.last_round_epoch = time.time()
            self.rounds += 1
            self.version += 1
            statuses = {sid: (self.last_results.get(sid) or {}).get("status", "?")
                        for sid in self.last_results}
            self._save()
        summary = " ".join("%s=%s" % (sid, st) for sid, st in statuses.items())
        print("[%s] опрос #%d: %s (%.1f c)" % (
            datetime.now().strftime("%H:%M:%S"), self.rounds, summary, duration), flush=True)

    # --- классификация ----------------------------------------------------
    def _classify(self, service, checks, internet_status):
        sid = service["id"]
        baseline = sid == BASELINE_SERVICE_ID
        core = [c for c in checks if c.get("core")] or checks
        total = len(core)
        ok = [c for c in core if c["ok"]]
        bad = [c for c in core if not c["ok"]]
        # В карточку сервиса показываем все проверки, а не только core:
        # так видно, что, например, DNS/TCP живы при мёртвом TLS.
        all_total = len(checks)
        all_ok = sum(1 for c in checks if c["ok"])
        # «Отклик» сервиса — только по успешным core-проверкам: если они все
        # мертвы (TLS/TCP-таймаут), честнее показать «—», а не 20 мс от DNS.
        core_ok_latencies = [c["latency_ms"] for c in ok if c["latency_ms"] is not None]
        latency = (int(round(sum(core_ok_latencies) / len(core_ok_latencies)))
                   if core_ok_latencies else None)
        max_latency = max(core_ok_latencies) if core_ok_latencies else None
        hints = HINTS.get(sid, {})

        def result(status, label, reason, hint, lat=latency, ok_count=None, total_count=None):
            return {
                "status": status,
                "status_label": label,
                "status_reason": reason,
                "hint": hint,
                "latency_ms": lat,
                "checks_ok": all_ok if ok_count is None else ok_count,
                "checks_total": all_total if total_count is None else total_count,
            }

        if total == 0:
            return result("unknown", "Нет данных", "Проверки ещё не выполнялись", None, None, 0)

        if len(ok) == total:
            if max_latency is not None and max_latency >= SLOW_LATENCY_MS:
                label = "Интернет нестабилен" if baseline else "Нестабильно"
                return result("degraded", label,
                              "Сервис отвечает медленно: %s мс" % max_latency,
                              hints.get("degraded"))
            if baseline:
                return result("ok", "Интернет работает", "Базовые проверки проходят", None)
            return result("ok", "Доступен", "Все проверки проходят", None)

        first = bad[0]
        if baseline:
            if ok:
                return result("degraded", "Интернет нестабилен",
                              "Не проходят %d из %d проверок: %s" % (len(bad), total, _reason_text(first)),
                              hints.get("degraded"))
            return result("down", "Нет интернета",
                          "Базовые проверки не проходят: %s" % _reason_text(first),
                          hints.get("down"))

        if ok:
            return result("degraded", "Нестабильно",
                          "Не проходят %d из %d проверок: %s" % (len(bad), total, _reason_text(first)),
                          hints.get("degraded"))

        if internet_status == "down":
            return result("down", "Недоступен",
                          "Нет доступа в интернет — проверка сервиса невозможна",
                          hints.get("down"))

        errors = {c["error"] for c in bad if c.get("error")}
        if errors and errors <= {"dns_error", "dns_timeout"}:
            return result("blocked", "Похоже на блокировку",
                          "DNS не разрешает адрес сервиса при работающем интернете: %s" % first["target"],
                          hints.get("blocked"))
        if errors & BLOCK_ERRORS:
            return result("blocked", "Похоже на блокировку",
                          "%s. Похоже на фильтрацию трафика" % _reason_text(first),
                          hints.get("blocked"))
        return result("down", "Недоступен", _reason_text(first), hints.get("down"))

    # --- применение результатов ------------------------------------------
    def _apply_round(self, by_service):
        now = time.time()
        baseline = SERVICE_BY_ID.get(BASELINE_SERVICE_ID)
        if baseline is not None:
            internet_info = self._classify(
                baseline, by_service[BASELINE_SERVICE_ID], "ok")
            internet_status = internet_info["status"]
            infos = {BASELINE_SERVICE_ID: internet_info}
        else:
            # Контрольного канала нет в конфиге: сервисы классифицируются без
            # привязки к «интернету» (down определяется по их собственным ошибкам).
            internet_status = None
            infos = {}
        for service in SERVICES:
            if service["id"] == BASELINE_SERVICE_ID:
                continue
            infos[service["id"]] = self._classify(service, by_service[service["id"]],
                                                  internet_status)

        with self.lock:
            for service in SERVICES:
                sid = service["id"]
                info = infos[sid]
                self.last_results[sid] = {
                    "status": info["status"],
                    "status_label": info["status_label"],
                    "status_reason": info["status_reason"],
                    "hint": info["hint"],
                    "latency_ms": info["latency_ms"],
                    "checks_ok": info["checks_ok"],
                    "checks_total": info["checks_total"],
                    "checks": by_service[sid],
                }
                points = self.series.setdefault(sid, [])
                points.append({
                    "t": int(now),
                    "status": info["status"],
                    "latency_ms": info["latency_ms"],
                })
                cutoff = now - SERIES_MAX_AGE
                points[:] = [p for p in points if p.get("t", 0) >= cutoff][-SERIES_MAX_POINTS:]

            self._update_incidents(infos, by_service, now)

    def _update_incidents(self, infos, by_service, now):
        for service in SERVICES:
            sid = service["id"]
            status = infos[sid]["status"]
            streak = self.streaks.setdefault(sid, {"fail": 0, "ok": 0, "first_fail": None})
            active = self._active_incident_locked(sid)
            if status == "unknown":
                continue
            if status == "ok":
                streak["fail"] = 0
                streak["ok"] = int(streak.get("ok") or 0) + 1
                if active is not None and streak["ok"] >= CLOSE_AFTER_OKS:
                    active["status"] = "resolved"
                    active["ended_epoch"] = now
                    active["ended_at"] = iso_from_epoch(now)
                    active["duration_sec"] = max(0, int(now - active["started_epoch"]))
                    print("[%s] инцидент закрыт: %s (%.0f c)" % (
                        datetime.now().strftime("%H:%M:%S"), sid,
                        active["duration_sec"]), flush=True)
            else:
                streak["fail"] = int(streak.get("fail") or 0) + 1
                streak["ok"] = 0
                if streak.get("first_fail") is None:
                    streak["first_fail"] = now
                if active is None and streak["fail"] >= OPEN_AFTER_FAILS:
                    started = float(streak.get("first_fail") or now)
                    checks = by_service[sid]
                    core = [c for c in checks if c.get("core")] or checks
                    incident = {
                        "id": uuid.uuid4().hex[:12],
                        "service_id": sid,
                        "service_name": service["name"],
                        "title": self._incident_title(service, status),
                        "reason": infos[sid]["status_reason"],
                        "status": "active",
                        "started_at": iso_from_epoch(started),
                        "started_epoch": started,
                        "ended_at": None,
                        "ended_epoch": None,
                        "duration_sec": max(0, int(now - started)),
                        "failed_checks": sum(1 for c in core if not c["ok"]),
                        "total_checks": len(core),
                        "service_status": status,
                    }
                    self.incidents.append(incident)
                    print("[%s] инцидент открыт: %s (%s) — %s" % (
                        datetime.now().strftime("%H:%M:%S"), sid, status,
                        incident["reason"]), flush=True)
            if status == "ok":
                streak["first_fail"] = None
        if len(self.incidents) > INCIDENTS_MAX_STORED:
            self.incidents.sort(key=lambda inc: inc.get("started_epoch") or 0)
            del self.incidents[:-INCIDENTS_MAX_STORED]

    @staticmethod
    def _incident_title(service, status):
        if status == "degraded":
            return "%s: нестабильная работа" % service["name"]
        if status == "blocked":
            return "%s: похоже на блокировку" % service["name"]
        return service["incident_title"]

    def _active_incident_locked(self, sid):
        for incident in reversed(self.incidents):
            if incident.get("service_id") == sid and incident.get("status") == "active":
                return incident
        return None

    def _last_incident_locked(self, sid):
        candidates = [inc for inc in self.incidents if inc.get("service_id") == sid]
        if not candidates:
            return None
        return max(candidates, key=lambda inc: inc.get("started_epoch") or 0)

    # --- сериализация -----------------------------------------------------
    @staticmethod
    def _uptime(points, since_epoch):
        window = [p for p in points if p.get("t", 0) >= since_epoch]
        if not window:
            return 0.0
        ok_count = sum(1 for p in window if p.get("status") == "ok")
        return round(100.0 * ok_count / len(window), 1)

    def _serialize_incident(self, incident, now):
        out = {
            "id": incident.get("id"),
            "service_id": incident.get("service_id"),
            "service_name": incident.get("service_name"),
            "title": incident.get("title"),
            "reason": incident.get("reason"),
            "status": incident.get("status"),
            "started_at": incident.get("started_at"),
            "started_epoch": incident.get("started_epoch"),
            "ended_at": incident.get("ended_at"),
            "ended_epoch": incident.get("ended_epoch"),
            "duration_sec": incident.get("duration_sec") or 0,
            "failed_checks": incident.get("failed_checks"),
            "total_checks": incident.get("total_checks"),
            "service_status": incident.get("service_status"),
        }
        if out["status"] == "active":
            out["ended_at"] = None
            out["ended_epoch"] = None
            out["duration_sec"] = max(0, int(now - (out["started_epoch"] or now)))
        return out

    def snapshot(self):
        with self.lock:
            now = time.time()
            services_out = []
            latencies = []
            for service in SERVICES:
                sid = service["id"]
                info = self.last_results.get(sid) or {}
                points = self.series.get(sid, [])
                status = info.get("status") or "unknown"
                latency = info.get("latency_ms")
                if latency is not None:
                    latencies.append(latency)
                active = self._active_incident_locked(sid)
                last = self._last_incident_locked(sid)
                services_out.append({
                    "id": sid,
                    "name": service["name"],
                    "subtitle": service["subtitle"],
                    "icon": service["icon"],
                    "accent": service["accent"],
                    "status": status,
                    "status_label": info.get("status_label") or STATUS_LABELS.get(status, "Нет данных"),
                    "status_reason": info.get("status_reason") or "Проверки ещё не выполнялись",
                    "hint": info.get("hint"),
                    "latency_ms": latency,
                    "uptime_1h": self._uptime(points, now - 3600),
                    "uptime_24h": self._uptime(points, now - SERIES_MAX_AGE),
                    "checks_ok": int(info.get("checks_ok") or 0),
                    "checks_total": int(info.get("checks_total") or 0),
                    "checks": info.get("checks") or [],
                    "series": points[-SERIES_POINTS:],
                    "active_incident": self._serialize_incident(active, now) if active else None,
                    "last_incident": self._serialize_incident(last, now) if last else None,
                })

            internet_labels = {
                "ok": "Интернет работает",
                "degraded": "Интернет нестабилен",
                "down": "Похоже, интернета нет",
                "unknown": "Проверка не выполнялась",
            }
            baseline_service = None
            for service_out in services_out:
                if service_out["id"] == BASELINE_SERVICE_ID:
                    baseline_service = service_out
                    break
            if baseline_service is not None:
                internet_map = {"ok": "ok", "degraded": "degraded", "unknown": "unknown"}
                internet_status = internet_map.get(baseline_service["status"], "down")
                internet = {
                    "status": internet_status,
                    "label": internet_labels[internet_status],
                    "latency_ms": baseline_service["latency_ms"],
                }
            else:
                # Контрольного канала нет — собираем internet-сводку из общего статуса.
                internet_status = None
                internet = None

            statuses = [svc["status"] for svc in services_out]
            affected = sum(1 for status in statuses if status in ("degraded", "blocked", "down"))
            services_ok = sum(1 for status in statuses if status == "ok")
            active_incidents = sum(1 for inc in self.incidents if inc.get("status") == "active")
            if all(status == "unknown" for status in statuses):
                overall = "unknown"
            elif internet_status == "down":
                overall = "down"
            elif affected:
                overall = "degraded"
            else:
                overall = "ok"
            if internet is None:
                mirror = overall if overall in internet_labels else "unknown"
                internet = {
                    "status": mirror,
                    "label": internet_labels[mirror],
                    "latency_ms": None,
                }
            overall_labels = {
                "ok": "Всё работает",
                "degraded": "Есть проблемы",
                "down": "Нет доступа в интернет",
                "unknown": "Нет данных",
            }

            recent = sorted(self.incidents, key=lambda inc: inc.get("started_epoch") or 0,
                            reverse=True)[:5]
            # Точный момент следующего старта знает только цикл опроса;
            # if/else — чтобы не показывать отрицательные значения при ручном опросе.
            next_check_in = 0.0
            if not self.checking and self.next_round_epoch is not None:
                next_check_in = max(0.0, self.next_round_epoch - now)

            checks_total = sum(int(svc["checks_total"]) for svc in services_out)
            checks_ok = sum(int(svc["checks_ok"]) for svc in services_out)

            return {
                "generated_at": iso_now(),
                "epoch": now,
                "version": self.version,
                "running": True,
                "poll_interval": self.interval,
                "next_check_in": round(next_check_in, 1),
                "checking": self.checking,
                "internet": internet,
                "summary": {
                    "overall": overall,
                    "overall_label": overall_labels[overall],
                    "services_total": len(services_out),
                    "services_ok": services_ok,
                    "services_affected": affected,
                    "active_incidents": active_incidents,
                    "avg_latency_ms": int(round(sum(latencies) / len(latencies))) if latencies else None,
                    "checks_total": checks_total,
                    "checks_ok": checks_ok,
                },
                "services": services_out,
                "recent_incidents": [self._serialize_incident(inc, now) for inc in recent],
            }

    def incidents_report(self, query="", service="all", status="all", hours=0,
                         limit=100, offset=0):
        with self.lock:
            now = time.time()
            needle = (query or "").strip().lower()
            items = []
            for incident in self.incidents:
                if service and service != "all" and incident.get("service_id") != service:
                    continue
                if status and status != "all" and incident.get("status") != status:
                    continue
                if hours and hours > 0:
                    started = incident.get("started_epoch") or 0
                    if started < now - hours * 3600:
                        continue
                if needle:
                    haystack = " ".join([
                        str(incident.get("service_name") or ""),
                        str(incident.get("title") or ""),
                        str(incident.get("reason") or ""),
                    ]).lower()
                    if needle not in haystack:
                        continue
                items.append(incident)
            items.sort(key=lambda inc: inc.get("started_epoch") or 0, reverse=True)
            total = len(items)
            page = items[offset:offset + limit]
            return {
                "total": total,
                "limit": limit,
                "offset": offset,
                "incidents": [self._serialize_incident(inc, now) for inc in page],
            }

    def all_incidents(self):
        with self.lock:
            now = time.time()
            items = sorted(self.incidents, key=lambda inc: inc.get("started_epoch") or 0,
                           reverse=True)
            return [self._serialize_incident(inc, now) for inc in items]

    def health(self):
        with self.lock:
            return {
                "ok": True,
                "uptime_sec": int(time.time() - self.started_epoch),
                "version": self.version,
                "rounds": self.rounds,
                "last_round_at": iso_from_epoch(self.last_round_epoch) if self.last_round_epoch else None,
                "poll_interval": self.interval,
            }


# --------------------------------------------------------------------------
# HTTP-сервер
# --------------------------------------------------------------------------

STATIC_TYPES = {
    "index.html": "text/html; charset=utf-8",
    "app.js": "application/javascript; charset=utf-8",
    "style.css": "text/css; charset=utf-8",
    "favicon.svg": "image/svg+xml",
}


class Handler(BaseHTTPRequestHandler):
    server_version = "NetMonitor/" + VERSION
    protocol_version = "HTTP/1.1"

    @property
    def monitor(self):
        return self.server.monitor

    def log_message(self, fmt, *args):  # noqa: A003 - гасим шум access-логов
        if os.environ.get("NETMON_DEBUG"):
            sys.stderr.write("[http] " + (fmt % args) + "\n")

    # --- отправка ---------------------------------------------------------
    def _send(self, body, content_type, status=200, extra_headers=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def _json(self, payload, status=200, extra_headers=None):
        data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = {"Cache-Control": "no-store", "Access-Control-Allow-Origin": "*"}
        headers.update(extra_headers or {})
        self._send(data, "application/json; charset=utf-8", status, headers)

    def _not_found(self):
        self._json({"error": "not_found"}, 404)

    # --- маршруты ---------------------------------------------------------
    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        if path == "/api/state":
            self._json(self.monitor.snapshot())
            return
        if path == "/api/incidents":
            params = {key: values[0] for key, values in query.items() if values}
            try:
                hours = max(0, int(params.get("hours") or 0))
            except ValueError:
                hours = 0
            try:
                limit = min(500, max(1, int(params.get("limit") or 100)))
            except ValueError:
                limit = 100
            try:
                offset = max(0, int(params.get("offset") or 0))
            except ValueError:
                offset = 0
            self._json(self.monitor.incidents_report(
                query=params.get("q") or "",
                service=params.get("service") or "all",
                status=params.get("status") or "all",
                hours=hours,
                limit=limit,
                offset=offset,
            ))
            return
        if path == "/api/health":
            self._json(self.monitor.health())
            return
        if path == "/api/export":
            stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            payload = {
                "exported_at": iso_now(),
                "state": self.monitor.snapshot(),
                "incidents": self.monitor.all_incidents(),
            }
            data = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
            self._send(data, "application/json; charset=utf-8", 200, {
                "Content-Disposition": 'attachment; filename="net-monitor-report-%s.json"' % stamp,
                "Cache-Control": "no-store",
            })
            return
        if path.startswith("/api/"):
            self._not_found()
            return

        rel = path.lstrip("/") or "index.html"
        if rel == "" or rel == "index.html":
            rel = "index.html"
        self._serve_static(rel)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            try:
                self.rfile.read(length)
            except OSError:
                pass
        if parsed.path == "/api/check":
            started = self.monitor.run_round(blocking=False)
            state = self.monitor.snapshot()
            if started:
                self._json({"ok": True, "state": state})
            else:
                self._json({"ok": False, "reason": "already_running", "state": state})
            return
        self._not_found()

    def do_HEAD(self):  # noqa: N802
        self.do_GET()

    def _serve_static(self, rel):
        if rel not in STATIC_TYPES:
            self._not_found()
            return
        file_path = os.path.join(static_dir(), rel)
        if not os.path.isfile(file_path):
            self._not_found()
            return
        try:
            with open(file_path, "rb") as handle:
                data = handle.read()
        except OSError:
            self._not_found()
            return
        self._send(data, STATIC_TYPES[rel], 200, {"Cache-Control": "no-cache"})


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def make_http_server(host, port, monitor):
    """Создать HTTP-сервер монитора (используется CLI и entry.py для Windows)."""
    httpd = ThreadingHTTPServer((host, port), Handler)
    httpd.monitor = monitor
    httpd.daemon_threads = True
    return httpd


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Локальный монитор доступности YouTube/Telegram",
        epilog="Настройки берутся из config.json (--config, NETMON_CONFIG или файл "
               "рядом со скриптом/приложением); CLI-флаги их переопределяют.")
    parser.add_argument("--config", default=None,
                        help="путь к config.json (приоритетнее переменной NETMON_CONFIG)")
    parser.add_argument("--host", default=None,
                        help="адрес прослушивания (по умолчанию из config.json: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=None,
                        help="порт (по умолчанию из config.json: 8099)")
    parser.add_argument("--interval", type=int, default=None,
                        help="интервал опроса, секунды (по умолчанию из config.json: 20, "
                             "минимум 5)")
    parser.add_argument("--data-dir", default=None,
                        help="каталог для state.json (по умолчанию ./data рядом с приложением)")
    parser.add_argument("--check-config", action="store_true",
                        help="только проверить конфигурацию и выйти (0 — корректна, 2 — ошибка)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.config)
    except ConfigError as exc:
        print(format_config_error(exc), file=sys.stderr, flush=True)
        return 2
    if args.check_config:
        source_label = (settings.source if settings.source != "builtin"
                        else "встроенные значения")
        print("Конфигурация корректна: %s (%s)" % (source_label, settings.source_kind),
              flush=True)
        print("  сервисов: %d, порт: %d, интервал: %d c" % (
            len(settings.services), settings.port, max(5, settings.interval)), flush=True)
        return 0

    # Применяем настройки к глобальным константам модуля (SERVICES, HINTS, пороги…).
    configure(settings)
    host = args.host if args.host is not None else settings.host
    port = args.port if args.port is not None else settings.port
    interval = args.interval if args.interval is not None else settings.interval

    monitor = Monitor(interval=interval, data_dir=args.data_dir)
    monitor.start()

    try:
        httpd = make_http_server(host, port, monitor)
    except OSError as exc:
        print("Не удалось занять %s:%s — %s" % (host, port, exc), flush=True)
        print("Попробуйте другой порт: --port 8100", flush=True)
        return 1

    checks_summary = ", ".join("%s (%d)" % (svc["name"], len(svc["checks"]))
                               for svc in SERVICES)
    source_label = settings.source if settings.source != "builtin" else "встроенные значения"
    banner = (
        "\n  net-monitor %s\n"
        "  дашборд:      http://%s:%d/\n"
        "  API:          http://%s:%d/api/state\n"
        "  интервал:     %d c\n"
        "  проверки:     %s\n"
        "  конфигурация: %s\n"
        "  остановка:    Ctrl+C\n" % (
            VERSION, host, port, host, port, monitor.interval, checks_summary, source_label)
    )
    print(banner, flush=True)

    def handle_signal(signum, frame):  # noqa: ARG001
        print("\nполучен сигнал, останавливаюсь…", flush=True)
        monitor.stop()
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for signal_name in ("SIGTERM", "SIGBREAK"):
        signal_value = getattr(signal, signal_name, None)
        if signal_value is None:
            continue
        try:
            signal.signal(signal_value, handle_signal)
        except (OSError, ValueError):  # не главный поток/платформа без сигнала
            pass

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nостанавливаюсь по Ctrl+C…", flush=True)
    finally:
        monitor.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
