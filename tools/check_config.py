#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Проверка config.json и паритета настроек между платформами.

Использование:
    python3 tools/check_config.py            # проверить корневой config.json
    python3 tools/check_config.py --sync     # синхронизировать android assets
    python3 tools/check_config.py --quiet    # только код выхода

Код выхода 0 — конфиг валиден и паритет соблюдён, 1 — есть ошибки.
Скрипт использует только stdlib и не требует установленных пакетов.
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config.json")
ANDROID_ASSET = os.path.join(ROOT, "android", "app", "src", "main", "assets", "config.json")
IOS_ASSET = os.path.join(ROOT, "ios", "Resources", "config.json")

STATUSES = ("ok", "degraded", "blocked", "down", "unknown")
KINDS = ("dns", "tcp", "tls", "https")


class ConfigError(Exception):
    pass


def fail(message):
    raise ConfigError(message)


def require(condition, message):
    if not condition:
        fail(message)


def is_hex_color(value):
    if not isinstance(value, str) or len(value) != 7 or not value.startswith("#"):
        return False
    return all(ch in "0123456789abcdefABCDEF" for ch in value[1:])


def check_config(config):
    """Семантическая валидация конфига. Бросает ConfigError."""
    require(isinstance(config, dict), "config.json должен быть объектом")
    require(config.get("version") == 1, "version должен быть 1")

    server = config.get("server") or {}
    require(isinstance(server, dict), "server должен быть объектом")
    require(isinstance(server.get("host"), str) and server["host"], "server.host обязателен")
    port = server.get("port")
    require(isinstance(port, int) and 1 <= port <= 65535, "server.port должен быть 1..65535")

    polling = config.get("polling") or {}
    require(isinstance(polling.get("interval_sec"), int) and polling["interval_sec"] >= 5,
            "polling.interval_sec должен быть >= 5")
    require(isinstance(polling.get("check_timeout_sec"), int) and polling["check_timeout_sec"] >= 1,
            "polling.check_timeout_sec должен быть >= 1")
    require(isinstance(polling.get("dns_timeout_sec"), (int, float)) and polling["dns_timeout_sec"] > 0,
            "polling.dns_timeout_sec должен быть > 0")

    incidents = config.get("incidents") or {}
    for key in ("open_after_failures", "close_after_successes", "max_stored"):
        require(isinstance(incidents.get(key), int) and incidents[key] >= 1,
                "incidents.%s должен быть целым >= 1" % key)

    history = config.get("history") or {}
    for key in ("max_age_hours", "max_points", "series_points"):
        require(isinstance(history.get(key), int) and history[key] >= 1,
                "history.%s должен быть целым >= 1" % key)

    labels = config.get("status_labels") or {}
    colors = config.get("status_colors") or {}
    for status in STATUSES:
        require(isinstance(labels.get(status), str) and labels[status].strip(),
                "status_labels.%s обязателен" % status)
        require(is_hex_color(colors.get(status)), "status_colors.%s должен быть #RRGGBB" % status)

    network = config.get("network", {})
    require(isinstance(network, dict), "network должен быть объектом")
    proxy = network.get("proxy", "direct")
    require(isinstance(proxy, str), "network.proxy должен быть строкой")
    if proxy not in ("direct", "system"):
        from urllib.parse import urlsplit
        parsed = urlsplit(proxy)
        require(parsed.scheme == "http" and parsed.hostname and parsed.port is not None,
                "network.proxy: ожидается 'direct', 'system' или http://host:port")

    services = config.get("services")
    require(isinstance(services, list) and services, "services должен быть непустым массивом")
    service_ids = set()
    for service in services:
        require(isinstance(service, dict), "каждый сервис должен быть объектом")
        sid = service.get("id")
        require(isinstance(sid, str) and sid and sid == sid.lower(),
                "у сервиса должен быть id в нижнем регистре")
        require(sid not in service_ids, "дублирующийся id сервиса: %s" % sid)
        service_ids.add(sid)
        for key in ("name", "subtitle", "icon", "incident_title"):
            require(isinstance(service.get(key), str) and service[key].strip(),
                    "%s.%s обязателен" % (sid, key))
        require(is_hex_color(service.get("accent")), "%s.accent должен быть #RRGGBB" % sid)
        hints = service.get("hints") or {}
        require(isinstance(hints, dict), "%s.hints должен быть объектом" % sid)
        for status in hints:
            require(status in STATUSES, "%s.hints: неизвестный статус %s" % (sid, status))

        checks = service.get("checks")
        require(isinstance(checks, list) and checks, "%s.checks должен быть непустым" % sid)
        check_ids = set()
        for check in checks:
            require(isinstance(check, dict), "%s.checks: проверка должна быть объектом" % sid)
            cid = check.get("id")
            require(isinstance(cid, str) and cid and cid == cid.lower(),
                    "%s.checks: у проверки должен быть id в нижнем регистре" % sid)
            require(cid not in check_ids, "%s.checks: дублирующийся id %s" % (sid, cid))
            check_ids.add(cid)
            require(isinstance(check.get("label"), str) and check["label"].strip(),
                    "%s.%s: label обязателен" % (sid, cid))
            require(check.get("kind") in KINDS,
                    "%s.%s: kind должен быть из %s" % (sid, cid, ", ".join(KINDS)))
            require(isinstance(check.get("host"), str) and check["host"].strip(),
                    "%s.%s: host обязателен" % (sid, cid))
            require(isinstance(check.get("port"), int) and 1 <= check["port"] <= 65535,
                    "%s.%s: port должен быть 1..65535" % (sid, cid))
            require(isinstance(check.get("core"), bool), "%s.%s: core должен быть bool" % (sid, cid))
            timeout = check.get("timeout")
            require(isinstance(timeout, (int, float)) and timeout > 0,
                    "%s.%s: timeout должен быть > 0" % (sid, cid))
            if check["kind"] == "https":
                require(isinstance(check.get("path"), str) and check["path"].startswith("/"),
                        "%s.%s: path должен начинаться с /" % (sid, cid))
                expect = check.get("expect")
                require(isinstance(expect, list) and expect and
                        all(isinstance(code, int) and 100 <= code <= 599 for code in expect),
                        "%s.%s: expect должен быть непустым списком HTTP-кодов" % (sid, cid))

    display = config.get("display") or {}
    macos = display.get("macos_menubar") or {}
    if macos:
        require(macos.get("mode") in ("overlay", "dot", "text"),
                "display.macos_menubar.mode должен быть overlay|dot|text")
        for key in ("overlay_window_pt", "overlay_dot_pt", "status_item_pt"):
            if key in macos:
                require(isinstance(macos[key], (int, float)) and macos[key] > 0,
                        "display.macos_menubar.%s должен быть > 0" % key)
    windows = (display.get("windows_tray") or {})
    if windows:
        require(windows.get("mode") == "tray", "display.windows_tray.mode должен быть tray")
        if "dot_pt" in windows:
            require(isinstance(windows["dot_pt"], int) and 8 <= windows["dot_pt"] <= 64,
                    "display.windows_tray.dot_pt должен быть 8..64")

    classification = config.get("classification") or {}
    if classification:
        require(isinstance(classification, dict), "classification должен быть объектом")
        slow = classification.get("slow_latency_ms")
        if slow is not None:
            require(isinstance(slow, (int, float)) and 100 <= slow <= 600000,
                    "classification.slow_latency_ms должен быть числом 100..600000")

    notifications = config.get("notifications") or {}
    require(isinstance(notifications, dict) and notifications, "notifications обязателен")
    for platform in ("windows", "android"):
        section = notifications.get(platform)
        if section is None:
            continue
        require(isinstance(section, dict), "notifications.%s должен быть объектом" % platform)
        if "enabled" in section:
            require(isinstance(section["enabled"], bool), "notifications.%s.enabled должен быть bool" % platform)
        for status in section.get("notify_on", []):
            require(status in STATUSES, "notifications.%s.notify_on: неизвестный статус %s" % (platform, status))
    return {
        "services": len(services),
        "checks": sum(len(service["checks"]) for service in services),
        "port": port,
        "interval": polling["interval_sec"],
    }


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def check_android_parity(config):
    if not os.path.isfile(ANDROID_ASSET):
        return "skip"  # Android ещё не собран/не добавлен
    try:
        with open(ANDROID_ASSET, "r", encoding="utf-8") as handle:
            asset = json.load(handle)
    except (OSError, ValueError) as exc:
        fail("не могу прочитать android assets/config.json: %s" % exc)
    if canonical(asset) != canonical(config):
        fail("android assets/config.json отличается от корневого config.json "
             "(запустите: python3 tools/check_config.py --sync)")
    return "ok"


def check_ios_parity(config):
    if not os.path.isfile(IOS_ASSET):
        return "skip"  # iOS ещё не добавлен
    try:
        with open(IOS_ASSET, "r", encoding="utf-8") as handle:
            asset = json.load(handle)
    except (OSError, ValueError) as exc:
        fail("не могу прочитать ios/Resources/config.json: %s" % exc)
    if canonical(asset) != canonical(config):
        fail("ios/Resources/config.json отличается от корневого config.json "
             "(запустите: python3 tools/check_config.py --sync)")
    return "ok"


def sync_ios_asset(config):
    if not os.path.isdir(os.path.dirname(IOS_ASSET)):
        return False
    os.makedirs(os.path.dirname(IOS_ASSET), exist_ok=True)
    with open(IOS_ASSET, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return True


def sync_android_asset(config):
    if not os.path.isdir(os.path.dirname(ANDROID_ASSET)):
        return False
    os.makedirs(os.path.dirname(ANDROID_ASSET), exist_ok=True)
    with open(ANDROID_ASSET, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return True


def main(argv):
    quiet = "--quiet" in argv
    sync = "--sync" in argv
    if not os.path.isfile(CONFIG_PATH):
        print("ОШИБКА: не найден %s" % CONFIG_PATH, file=sys.stderr)
        return 1
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as handle:
            config = json.load(handle)
        summary = check_config(config)
        if sync:
            if sync_android_asset(config):
                if not quiet:
                    print("android assets/config.json обновлён из корневого конфига")
            else:
                if not quiet:
                    print("android-проект ещё не создан — синхронизировать нечего")
            if sync_ios_asset(config):
                if not quiet:
                    print("ios/Resources/config.json обновлён из корневого конфига")
            else:
                if not quiet:
                    print("ios-проект ещё не создан — синхронизировать нечего")
        parity = check_android_parity(config)
        ios_parity = check_ios_parity(config)
    except ConfigError as exc:
        print("ОШИБКА КОНФИГА: %s" % exc, file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print("ОШИБКА: не могу прочитать config.json: %s" % exc, file=sys.stderr)
        return 1

    if not quiet:
        print("config.json: OK — %d сервисов, %d проверок, порт %d, интервал %d c" % (
            summary["services"], summary["checks"], summary["port"], summary["interval"]))
        print("android assets: %s" % ("OK" if parity == "ok" else "пропущено (нет android проекта)"))
        print("ios assets: %s" % ("OK" if ios_parity == "ok" else "пропущено (нет ios проекта)"))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
