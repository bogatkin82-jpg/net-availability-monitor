#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Герметичные юнит-тесты net-availability-monitor для CI и любого раннера.

Проверяется без сети и без запуска HTTP-сервера:
  * загрузка config.json: встроенные дефолты, файл, приоритет --config /
    NETMON_CONFIG, битые конфиги и код выхода 2;
  * классификация сервисов (ok / degraded / blocked / down / unknown);
  * движок инцидентов: пороги, закрытие, duration, max_stored;
  * снимок /api/state (summary/services/series/uptime), журнал инцидентов
    (q/service/status/hours/limit/offset);
  * паритет настроек: config.json ↔ Python ↔ Android ↔ macOS ↔ Windows;
  * статика и контракт API docs/api-contract.md;
  * гигиена репозитория: .gitignore, лицензия, отсутствие секретов и ссылок
    на несуществующие документы.

Запуск:  python -m pytest -q tests/test_unit.py
Зависимости: только pytest (в CI ставится отдельно) + stdlib.

Тесты намеренно не трогают чужие файлы и не пишут в репозиторий: временные
конфиги и data-dir создаются в tmp_path.
"""

import copy
import glob
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server as S  # noqa: E402

CONFIG_PATH = os.path.join(ROOT, "config.json")
CHECK_CONFIG_PY = os.path.join(ROOT, "tools", "check_config.py")
ANDROID_DIR = os.path.join(ROOT, "android")
ANDROID_ASSET = os.path.join(ANDROID_DIR, "app", "src", "main", "assets", "config.json")
ANDROID_CONFIG_KT = os.path.join(
    ANDROID_DIR, "app", "src", "main", "java", "ru", "netmon", "core", "Config.kt")
ANDROID_CONFIG_REPO_KT = os.path.join(
    ANDROID_DIR, "app", "src", "main", "java", "ru", "netmon", "android",
    "ConfigRepository.kt")
ANDROID_BUILD_GRADLE = os.path.join(ANDROID_DIR, "app", "build.gradle.kts")
NETBAR_SWIFT = os.path.join(ROOT, "menubar", "NetBar.swift")
MENUBAR_BUILD = os.path.join(ROOT, "menubar", "build.sh")
INSTALL_SH = os.path.join(ROOT, "install.sh")
TRAY_PY = os.path.join(ROOT, "tray.py")
WINDOWS_DIR = os.path.join(ROOT, "windows")
ENTRY_PY = os.path.join(WINDOWS_DIR, "entry.py")
SPEC_FILE = os.path.join(WINDOWS_DIR, "netmon.spec")
INSTALL_PS1 = os.path.join(WINDOWS_DIR, "install.ps1")
UNINSTALL_PS1 = os.path.join(WINDOWS_DIR, "uninstall.ps1")
WINDOWS_TESTS = os.path.join(WINDOWS_DIR, "tests", "test_windows.py")
STATIC_DIR = os.path.join(ROOT, "static")
CONTRACT_MD = os.path.join(ROOT, "docs", "api-contract.md")
SCHEMA_JSON = os.path.join(ROOT, "docs", "config.schema.json")
GITIGNORE = os.path.join(ROOT, ".gitignore")
README_MD = os.path.join(ROOT, "README.md")
README_RU_MD = os.path.join(ROOT, "README.ru.md")

# Глобалы server.py, которые configure() переписывает из настроек.
GLOBAL_NAMES = (
    "SERVICES", "SERVICE_BY_ID", "HINTS", "STATUS_LABELS", "STATUS_COLORS",
    "OPEN_AFTER_FAILS", "CLOSE_AFTER_OKS", "SERIES_MAX_AGE", "SERIES_MAX_POINTS",
    "SERIES_POINTS", "POLL_INTERVAL", "CHECK_TIMEOUT", "DNS_TIMEOUT",
    "SERVER_HOST", "SERVER_PORT", "INCIDENTS_MAX_STORED", "SLOW_LATENCY_MS",
    "NOTIFICATIONS", "DISPLAY", "ACTIVE_SETTINGS",
)


# --------------------------------------------------------------------------
# Инфраструктура тестов
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_module_state(monkeypatch):
    """Каждый тест начинается со встроенных дефолтов и не портит глобалы."""
    saved = {name: copy.deepcopy(getattr(S, name)) for name in GLOBAL_NAMES}
    monkeypatch.delenv("NETMON_CONFIG", raising=False)
    S.configure(S._build_settings(S._default_config(), "builtin", "тест"))
    try:
        yield
    finally:
        for name, value in saved.items():
            setattr(S, name, value)


def read_text(path):
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def read_json(path):
    with open(path, "r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path, payload):
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    return str(path)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def root_config():
    return read_json(CONFIG_PATH)


def tmp_config(tmp_path, payload, name="config.json"):
    return write_json(str(tmp_path / name), payload)


def config_with(tmp_path, **changes):
    """Копия корневого конфига с заменой секций верхнего уровня."""
    payload = json.loads(json.dumps(root_config()))
    payload.update(changes)
    return tmp_config(tmp_path, payload)


def default_payload():
    return json.loads(json.dumps(root_config()))


def expect_config_error(path):
    with pytest.raises(S.ConfigError) as excinfo:
        S.load_settings(path)
    return excinfo.value


def check_config_module():
    spec = importlib.util.spec_from_file_location("qa_check_config", CHECK_CONFIG_PY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --------------------------------------------------------------------------
# Синтетические результаты проверок (сеть не используется)
# --------------------------------------------------------------------------

def build_checks(sid, default_ok=True, error=None, latency=100, stage=None,
                 overrides=None):
    """Собрать check-результаты для сервиса sid по его спекам из конфига."""
    service = S.SERVICE_BY_ID[sid]
    out = []
    for spec in service["checks"]:
        conf = (overrides or {}).get(spec["id"]) or {}
        ok = conf.get("ok", default_ok)
        item = {
            "id": spec["id"],
            "label": spec["label"],
            "target": conf.get("target") or spec.get("host") or "",
            "kind": spec["kind"],
            "core": bool(spec.get("core")),
            "ok": bool(ok),
            "latency_ms": conf.get("latency", latency if ok else None),
            "stage": conf.get("stage", "ok" if ok else (stage or "tcp")),
            "error": None if ok else conf.get("error", error or "connect_timeout"),
            "error_label": None if ok else "Тестовая ошибка",
            "error_text": None if ok else "synthetic test error",
            "http_status": None,
            "ip": "127.0.0.1",
            "timings": {"dns_ms": 1, "tcp_ms": None, "tls_ms": None, "http_ms": None},
            "measured_at": S.iso_now(),
        }
        out.append(item)
    return out


def all_ok(latency=100):
    return {sid: build_checks(sid, latency=latency)
            for sid in ("youtube", "telegram", "internet")}


def all_bad(error="connect_timeout", stage="tcp", latency=None):
    return {sid: build_checks(sid, default_ok=False, error=error, stage=stage,
                              latency=latency)
            for sid in ("youtube", "telegram", "internet")}


def per_service_checks(sid, mode, latency=100):
    if mode == "ok":
        return build_checks(sid, latency=latency)
    if mode == "unknown":
        return []
    if mode == "degraded":
        core_ids = [c["id"] for c in S.SERVICE_BY_ID[sid]["checks"] if c.get("core")]
        return build_checks(sid, latency=latency, overrides={
            core_ids[-1]: {"ok": False, "error": "connect_timeout", "stage": "tcp"},
        })
    if mode == "blocked":
        return build_checks(sid, default_ok=False, error="tls_handshake_timeout",
                            stage="tls")
    if mode == "dns":
        return build_checks(sid, default_ok=False, error="dns_error", stage="dns")
    if mode == "down":
        return build_checks(sid, default_ok=False, error="connect_refused", stage="tcp")
    raise AssertionError("неизвестный режим %r" % (mode,))


def apply_round(monitor, youtube="ok", telegram="ok", internet="ok"):
    monitor._apply_round({
        "youtube": per_service_checks("youtube", youtube),
        "telegram": per_service_checks("telegram", telegram),
        "internet": per_service_checks("internet", internet),
    })


def make_monitor(tmp_path, interval=15, name="data"):
    return S.Monitor(interval=interval, data_dir=str(tmp_path / name))


def make_settings(**sections):
    """Настройки на базе корневого конфига с заменой секций/полей."""
    raw = json.loads(json.dumps(S._default_config()))
    for key, value in sections.items():
        if isinstance(value, dict) and isinstance(raw.get(key), dict):
            raw[key].update(value)
        else:
            raw[key] = value
    return S._build_settings(raw, "unit-test", "тест")


def add_incident(monitor, service_id, status="active", started_epoch=None,
                 title=None, reason="Тестовая причина", **extra):
    now = time.time()
    started = now - 60 if started_epoch is None else started_epoch
    service = S.SERVICE_BY_ID[service_id]
    incident = {
        "id": uuid.uuid4().hex[:12],
        "service_id": service_id,
        "service_name": service["name"],
        "title": title or ("%s: тест" % service["name"]),
        "reason": reason,
        "status": status,
        "started_at": S.iso_from_epoch(started),
        "started_epoch": started,
        "ended_at": None if status == "active" else S.iso_from_epoch(started + 30),
        "ended_epoch": None if status == "active" else started + 30,
        "duration_sec": 0 if status == "active" else 30,
        "failed_checks": 2,
        "total_checks": 2,
        "service_status": "blocked",
    }
    incident.update(extra)
    monitor.incidents.append(incident)
    return incident


# ==========================================================================
# 1. Конфигурация: загрузка, приоритеты, валидация
# ==========================================================================

def test_builtin_defaults_when_no_config_file(monkeypatch):
    monkeypatch.setattr(S, "_candidate_config_paths", lambda: [])
    settings = S.load_settings()
    assert settings.source == "builtin"
    assert settings.source_kind == "встроенные значения"
    assert settings.port == 8099
    assert settings.host == "127.0.0.1"
    assert settings.interval == 20
    assert settings.check_timeout == 8.0
    assert settings.dns_timeout == 4.0
    assert settings.open_after_fails == 2
    assert settings.close_after_oks == 1
    assert settings.max_incidents == 500
    assert [svc["id"] for svc in settings.services] == ["youtube", "telegram", "internet"]
    assert len(settings.services[0]["checks"]) == 5
    assert len(settings.services[1]["checks"]) == 7
    assert len(settings.services[2]["checks"]) == 4


def test_builtin_defaults_equal_root_config_json():
    """Встроенный фолбэк обязан совпадать с config.json (единый источник)."""
    assert canonical(S._default_config()) == canonical(root_config())


def test_load_settings_from_explicit_file(tmp_path):
    path = config_with(tmp_path, server={"host": "127.0.0.1", "port": 9101})
    settings = S.load_settings(path)
    assert settings.source == path
    assert settings.source_kind == "аргумент --config"
    assert settings.port == 9101
    assert len(settings.services) == 3


def test_explicit_config_wins_over_env(tmp_path, monkeypatch):
    base = default_payload()
    base["server"]["port"] = 9102
    explicit = tmp_config(tmp_path, base, name="explicit.json")
    base2 = default_payload()
    base2["server"]["port"] = 9103
    env_path = tmp_config(tmp_path, base2, name="env.json")
    monkeypatch.setenv("NETMON_CONFIG", env_path)
    assert S.load_settings().port == 9103
    assert S.load_settings(explicit).port == 9102


def test_missing_explicit_config_is_reported(tmp_path):
    missing = str(tmp_path / "nope.json")
    error = expect_config_error(missing)
    assert "файл конфигурации не найден" in str(error)
    assert "--config" in str(error)


def test_missing_env_config_is_reported(tmp_path, monkeypatch):
    monkeypatch.setenv("NETMON_CONFIG", str(tmp_path / "nope.json"))
    error = expect_config_error(None)
    assert "NETMON_CONFIG" in str(error)


@pytest.mark.parametrize("argv, expected", [
    (["--check-config"], 0),
    (["--config", CONFIG_PATH, "--check-config"], 0),
])
def test_cli_check_config_exit_zero(argv, expected, capsys, monkeypatch):
    monkeypatch.setattr(S, "_candidate_config_paths", lambda: [])
    assert S.main(argv) == expected
    out = capsys.readouterr().out
    assert "Конфигурация корректна" in out


def test_cli_broken_config_exits_2(tmp_path, capsys):
    broken = str(tmp_path / "broken.json")
    with open(broken, "w", encoding="utf-8") as handle:
        handle.write("{ not json")
    assert S.main(["--config", broken, "--check-config"]) == 2
    err = capsys.readouterr().err
    assert "Ошибка конфигурации" in err
    assert "некорректный JSON" in err


@pytest.mark.parametrize("mutation, needle", [
    (lambda c: c["services"][0]["checks"][0].__setitem__("kind", "icmp"),
     "неизвестный тип проверки"),
    (lambda c: c["services"].append(copy.deepcopy(c["services"][0])),
     "уже используется"),
    (lambda c: c["services"][1]["checks"][0].__setitem__("id", "yt_https"),
     "уже используется в другом сервисе"),
    (lambda c: c["server"].__setitem__("port", 70000), "больше допустимого максимума"),
    (lambda c: c["server"].__setitem__("port", 0), "меньше допустимого минимума"),
    (lambda c: c["polling"].__setitem__("interval_sec", 2), "polling.interval_sec"),
    (lambda c: c["status_colors"].__setitem__("ok", "#zzzzzz"), "цвет вида #rrggbb"),
    (lambda c: c["services"][0]["checks"][0].__setitem__("expect", [700]),
     "HTTP-статус 100..599"),
    (lambda c: c.__setitem__("services", []),
     "обязательный непустой список сервисов"),
    (lambda c: c["services"][0].__setitem__("id", "YouTube"),
     "строчные латинские"),
    (lambda c: c["status_labels"].__setitem__("bogus", "x"), "неизвестный статус"),
    (lambda c: c["server"].__setitem__("host", "bad host"), "server.host"),
    (lambda c: c.__setitem__("polling", []), "ожидается JSON-объект"),
    (lambda c: c.__setitem__("version", 0), "version"),
    (lambda c: c["services"][0]["checks"][0].__setitem__("timeout", 0.1),
     "меньше допустимого минимума"),
    (lambda c: c["history"].__setitem__("max_age_hours", 0), "max_age_hours"),
    (lambda c: c["services"][0].__setitem__("hints", {"nope": "x"}),
     "неизвестный статус"),
])
def test_config_validation_errors(tmp_path, mutation, needle):
    payload = default_payload()
    mutation(payload)
    error = expect_config_error(tmp_config(tmp_path, payload))
    assert needle in str(error), "ожидали %r в %r" % (needle, str(error))


def test_config_root_must_be_object(tmp_path):
    path = str(tmp_path / "list.json")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("[1, 2, 3]")
    error = expect_config_error(path)
    assert "JSON-объектом" in str(error)


def test_config_error_formatting_mentions_source(tmp_path):
    payload = default_payload()
    payload["server"]["port"] = -1
    path = tmp_config(tmp_path, payload)
    error = expect_config_error(path)
    text = S.format_config_error(error)
    assert path in text
    assert "Ошибка конфигурации" in text
    assert text.count("- ") >= 1


def test_configure_applies_settings_to_globals():
    settings = make_settings(
        server={"host": "0.0.0.0", "port": 9123},
        polling={"interval_sec": 33, "check_timeout_sec": 5, "dns_timeout_sec": 2},
        incidents={"open_after_failures": 4, "close_after_successes": 3,
                   "max_stored": 42},
        history={"max_age_hours": 6, "max_points": 100, "series_points": 7},
        classification={"slow_latency_ms": 1500},
    )
    S.configure(settings)
    assert S.SERVER_HOST == "0.0.0.0"
    assert S.SERVER_PORT == 9123
    assert S.POLL_INTERVAL == 33
    assert S.CHECK_TIMEOUT == 5.0
    assert S.DNS_TIMEOUT == 2.0
    assert S.OPEN_AFTER_FAILS == 4
    assert S.CLOSE_AFTER_OKS == 3
    assert S.INCIDENTS_MAX_STORED == 42
    assert S.SERIES_MAX_AGE == 6 * 3600
    assert S.SERIES_MAX_POINTS == 100
    assert S.SERIES_POINTS == 7
    assert S.SLOW_LATENCY_MS == 1500


def test_monitor_clamps_interval_to_minimum(tmp_path):
    assert make_monitor(tmp_path).interval == 15
    assert S.Monitor(interval=2, data_dir=str(tmp_path / "d2")).interval == 5


# ==========================================================================
# 2. Классификация сервисов
# ==========================================================================

@pytest.fixture
def classify_monitor(tmp_path):
    """Monitor нужен только как носитель _classify (сеть не используется)."""
    return S.Monitor(interval=15, data_dir=str(tmp_path / "classify"))


def classify(monitor, sid, mode="ok", internet_status="ok"):
    return monitor._classify(S.SERVICE_BY_ID[sid],
                             per_service_checks(sid, mode), internet_status)


def test_classify_all_ok(classify_monitor):
    info = classify(classify_monitor, "youtube")
    assert info["status"] == "ok"
    assert info["status_label"] == S.STATUS_LABELS["ok"] == "Доступен"
    assert info["hint"] is None
    assert info["checks_ok"] == info["checks_total"] == 5
    assert isinstance(info["latency_ms"], int) and info["latency_ms"] > 0

    internet = classify(classify_monitor, "internet")
    assert internet["status"] == "ok"
    assert internet["status_label"] == "Интернет работает"


def test_classify_partial_is_degraded(classify_monitor):
    checks = build_checks("youtube", overrides={
        "yt_https": {"ok": False, "error": "connect_timeout", "stage": "tcp"},
    })
    info = classify_monitor._classify(S.SERVICE_BY_ID["youtube"], checks, "ok")
    assert info["status"] == "degraded"
    assert info["status_label"] == S.STATUS_LABELS["degraded"] == "Нестабильно"
    assert "Не проходят 1 из 2" in info["status_reason"]
    assert info["hint"] == S.HINTS["youtube"]["degraded"]


def test_classify_tls_timeout_is_blocked(classify_monitor):
    info = classify(classify_monitor, "youtube", "blocked")
    assert info["status"] == "blocked"
    assert info["status_label"] == S.STATUS_LABELS["blocked"]
    assert info["hint"] == S.HINTS["youtube"]["blocked"]
    assert info["latency_ms"] is None


def test_classify_dns_error_is_blocked(classify_monitor):
    info = classify(classify_monitor, "youtube", "dns")
    assert info["status"] == "blocked"
    assert "DNS не разрешает" in info["status_reason"]


def test_classify_connect_refused_is_down(classify_monitor):
    info = classify(classify_monitor, "youtube", "down")
    assert info["status"] == "down"
    assert info["status_label"] == S.STATUS_LABELS["down"] == "Недоступен"
    assert info["hint"] == S.HINTS["youtube"]["down"]


def test_classify_http_status_unexpected_is_down(classify_monitor):
    checks = build_checks("youtube", default_ok=False,
                          error="http_status_unexpected", stage="http")
    info = classify_monitor._classify(S.SERVICE_BY_ID["youtube"], checks, "ok")
    assert info["status"] == "down"


def test_classify_internet_down_makes_services_down(classify_monitor):
    checks = build_checks("youtube", default_ok=False,
                          error="tls_handshake_timeout", stage="tls")
    assert classify_monitor._classify(
        S.SERVICE_BY_ID["youtube"], checks, "ok")["status"] == "blocked"
    info = classify_monitor._classify(S.SERVICE_BY_ID["youtube"], checks, "down")
    assert info["status"] == "down"
    assert "интернет" in info["status_reason"].lower()


def test_classify_slow_is_degraded(classify_monitor):
    checks = build_checks("youtube", latency=S.SLOW_LATENCY_MS + 500)
    info = classify_monitor._classify(S.SERVICE_BY_ID["youtube"], checks, "ok")
    assert info["status"] == "degraded"
    assert "медленно" in info["status_reason"]
    baseline = classify_monitor._classify(
        S.SERVICE_BY_ID["internet"],
        build_checks("internet", latency=S.SLOW_LATENCY_MS + 1), "ok")
    assert baseline["status"] == "degraded"
    assert baseline["status_label"] == "Интернет нестабилен"


def test_classify_empty_checks_is_unknown(classify_monitor):
    info = classify(classify_monitor, "youtube", "unknown")
    assert info["status"] == "unknown"
    assert info["status_label"] == S.STATUS_LABELS["unknown"] == "Нет данных"
    assert "не выполнялись" in info["status_reason"]
    assert info["latency_ms"] is None


def test_classify_ignores_non_core_failures(classify_monitor):
    """Не-core проверки не влияют на статус сервиса (как в контракте)."""
    checks = build_checks("youtube", overrides={
        "yt_www": {"ok": False, "error": "http_status_unexpected", "stage": "http"},
        "yt_tcp": {"ok": False, "error": "connect_refused", "stage": "tcp"},
        "yt_dns": {"ok": False, "error": "dns_error", "stage": "dns"},
    })
    info = classify_monitor._classify(S.SERVICE_BY_ID["youtube"], checks, "ok")
    assert info["status"] == "ok"
    assert info["checks_ok"] == 2  # прошли только две core-проверки
    assert info["checks_total"] == 5
    assert "Все проверки проходят" in info["status_reason"]


def test_classify_baseline_partial_is_degraded(classify_monitor):
    checks = build_checks("internet", overrides={
        "inet_tcp": {"ok": False, "error": "connect_timeout", "stage": "tcp"},
    })
    info = classify_monitor._classify(S.SERVICE_BY_ID["internet"], checks, "ok")
    assert info["status"] == "degraded"
    assert info["status_label"] == "Интернет нестабилен"


# ==========================================================================
# 3. Инциденты
# ==========================================================================

def test_incident_opens_after_two_failures(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="blocked")
    assert monitor.incidents == []
    apply_round(monitor, youtube="blocked")
    assert len(monitor.incidents) == 1
    incident = monitor.incidents[0]
    assert incident["service_id"] == "youtube"
    assert incident["status"] == "active"
    assert incident["service_status"] == "blocked"
    assert incident["failed_checks"] == 2
    assert incident["total_checks"] == 2
    assert incident["ended_epoch"] is None
    assert incident["duration_sec"] >= 0


def test_incident_resolves_after_success(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="blocked")
    apply_round(monitor, youtube="blocked")
    apply_round(monitor, youtube="ok")
    assert len(monitor.incidents) == 1
    incident = monitor.incidents[0]
    assert incident["status"] == "resolved"
    assert incident["ended_epoch"] is not None
    assert incident["ended_at"] is not None
    assert isinstance(incident["duration_sec"], int)
    assert incident["duration_sec"] >= 0


def test_incident_not_duplicated_while_active(tmp_path):
    monitor = make_monitor(tmp_path)
    for _ in range(5):
        apply_round(monitor, youtube="down")
    assert len(monitor.incidents) == 1
    assert monitor.incidents[0]["status"] == "active"


def test_incident_duration_grows_while_active(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="down")
    apply_round(monitor, youtube="down")
    incident = monitor.incidents[0]
    incident["started_epoch"] -= 120
    incident["started_at"] = S.iso_from_epoch(incident["started_epoch"])
    apply_round(monitor, youtube="down")
    state = monitor.snapshot()
    active = [svc for svc in state["services"]
              if svc["id"] == "youtube"][0]["active_incident"]
    assert active is not None
    assert active["duration_sec"] >= 120


def test_incident_threshold_from_config(tmp_path):
    S.configure(make_settings(incidents={"open_after_failures": 3,
                                         "close_after_successes": 1,
                                         "max_stored": 500}))
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="down")
    apply_round(monitor, youtube="down")
    assert monitor.incidents == []
    apply_round(monitor, youtube="down")
    assert len(monitor.incidents) == 1


def test_incident_close_after_successes_from_config(tmp_path):
    S.configure(make_settings(incidents={"open_after_failures": 2,
                                         "close_after_successes": 2,
                                         "max_stored": 500}))
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="down")
    apply_round(monitor, youtube="down")
    assert monitor.incidents[0]["status"] == "active"
    apply_round(monitor, youtube="ok")
    assert monitor.incidents[0]["status"] == "active"
    apply_round(monitor, youtube="ok")
    assert monitor.incidents[0]["status"] == "resolved"


def test_incident_max_stored_from_config(tmp_path):
    S.configure(make_settings(incidents={"open_after_failures": 2,
                                         "close_after_successes": 1,
                                         "max_stored": 2}))
    monitor = make_monitor(tmp_path)
    # три сервиса одновременно уходят в down -> три инцидента, хранится 2
    apply_round(monitor, youtube="down", telegram="down", internet="down")
    apply_round(monitor, youtube="down", telegram="down", internet="down")
    assert len(monitor.incidents) <= 2
    # закрываем и открываем новую волну — лимит по-прежнему соблюдается
    apply_round(monitor, youtube="ok", telegram="ok", internet="ok")
    apply_round(monitor, youtube="down", telegram="down", internet="down")
    apply_round(monitor, youtube="down", telegram="down", internet="down")
    assert len(monitor.incidents) <= 2
    assert S.INCIDENTS_MAX_STORED == 2


def test_incident_unknown_status_is_ignored(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="unknown")
    apply_round(monitor, youtube="unknown")
    assert monitor.incidents == []
    assert monitor.streaks["youtube"]["fail"] == 0
    assert monitor.snapshot()["services"][0]["status"] == "unknown"


# ==========================================================================
# 4. Снимок состояния: summary/services/series/uptime
# ==========================================================================

def test_snapshot_before_first_round(tmp_path):
    monitor = make_monitor(tmp_path)
    state = monitor.snapshot()
    assert state["summary"]["overall"] == "unknown"
    assert state["summary"]["services_total"] == 3
    assert state["summary"]["checks_total"] == 0
    assert state["internet"]["status"] == "unknown"
    for service in state["services"]:
        assert service["status"] == "unknown"
        assert service["status_label"] == S.STATUS_LABELS["unknown"]
        assert service["series"] == []


def test_snapshot_after_all_ok_round(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor)
    state = monitor.snapshot()
    for key in ("generated_at", "epoch", "version", "running", "poll_interval",
                "next_check_in", "checking", "internet", "summary", "services",
                "recent_incidents"):
        assert key in state
    summary = state["summary"]
    assert summary["overall"] == "ok"
    assert summary["overall_label"] == "Всё работает"
    assert summary["services_total"] == 3
    assert summary["services_ok"] == 3
    assert summary["services_affected"] == 0
    assert summary["active_incidents"] == 0
    assert summary["checks_total"] == 16
    assert summary["checks_ok"] == 16
    assert isinstance(summary["avg_latency_ms"], int)
    assert state["internet"]["status"] == "ok"
    assert state["poll_interval"] == 15
    for service in state["services"]:
        for key in ("id", "name", "subtitle", "icon", "accent", "status",
                    "status_label", "status_reason", "hint", "latency_ms",
                    "uptime_1h", "uptime_24h", "checks_ok", "checks_total",
                    "checks", "series", "active_incident", "last_incident"):
            assert key in service
        assert service["uptime_1h"] == 100.0
        assert len(service["series"]) == 1
        assert service["series"][-1]["status"] == "ok"


def test_snapshot_arbitrary_statuses(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="ok", telegram="blocked", internet="ok")
    state = monitor.snapshot()
    by_id = {svc["id"]: svc["status"] for svc in state["services"]}
    assert by_id == {"youtube": "ok", "telegram": "blocked", "internet": "ok"}
    assert state["summary"]["overall"] == "degraded"
    assert state["summary"]["services_ok"] == 2
    assert state["summary"]["services_affected"] == 1
    assert state["summary"]["active_incidents"] == 0  # заблокирован один раз


def test_snapshot_internet_down(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="ok", telegram="ok", internet="down")
    state = monitor.snapshot()
    assert state["internet"]["status"] == "down"
    assert state["summary"]["overall"] == "down"


def test_snapshot_uptime_window_math(tmp_path):
    monitor = make_monitor(tmp_path)
    now = time.time()
    monitor.series["youtube"] = [
        {"t": int(now) - 30, "status": "ok", "latency_ms": 10},
        {"t": int(now) - 20, "status": "ok", "latency_ms": 11},
        {"t": int(now) - 10, "status": "down", "latency_ms": None},
        {"t": int(now) - 25 * 3600, "status": "ok", "latency_ms": 12},
    ]
    state = monitor.snapshot()
    youtube = [svc for svc in state["services"] if svc["id"] == "youtube"][0]
    assert youtube["uptime_1h"] == 66.7
    assert youtube["uptime_24h"] == 66.7  # точка 25 ч назад вне окна


def test_snapshot_series_points_capped(tmp_path):
    S.configure(make_settings(history={"max_age_hours": 24, "max_points": 8640,
                                       "series_points": 5}))
    monitor = make_monitor(tmp_path)
    now = time.time()
    monitor.series["youtube"] = [
        {"t": int(now) - i, "status": "ok", "latency_ms": 1} for i in range(10)
    ]
    youtube = [svc for svc in monitor.snapshot()["services"]
               if svc["id"] == "youtube"][0]
    assert len(youtube["series"]) == 5
    assert S.SERIES_POINTS == 5


def test_snapshot_recent_incidents_capped_at_five(tmp_path):
    monitor = make_monitor(tmp_path)
    now = time.time()
    for index in range(7):
        add_incident(monitor, "youtube", status="resolved",
                     started_epoch=now - 1000 + index, title="inc-%d" % index)
    state = monitor.snapshot()
    assert len(state["recent_incidents"]) == 5
    titles = [inc["title"] for inc in state["recent_incidents"]]
    assert titles == sorted(titles, key=lambda value: int(value.rsplit("-", 1)[1]),
                            reverse=True)


def test_snapshot_serializes_active_and_last_incident(tmp_path):
    monitor = make_monitor(tmp_path)
    apply_round(monitor, youtube="blocked")
    apply_round(monitor, youtube="blocked")
    apply_round(monitor, youtube="ok")
    youtube = [svc for svc in monitor.snapshot()["services"]
               if svc["id"] == "youtube"][0]
    assert youtube["active_incident"] is None
    last = youtube["last_incident"]
    assert last is not None
    for key in ("id", "service_id", "title", "status", "started_at",
                "started_epoch", "ended_at", "ended_epoch", "duration_sec"):
        assert key in last
    assert last["status"] == "resolved"
    assert last["ended_epoch"] >= last["started_epoch"]


# ==========================================================================
# 5. Журнал инцидентов: фильтры, поиск, пагинация
# ==========================================================================

def _report_monitor(tmp_path):
    monitor = make_monitor(tmp_path)
    now = time.time()
    add_incident(monitor, "youtube", status="active", started_epoch=now - 100,
                 title="YouTube недоступен", reason="TLS-таймаут")
    add_incident(monitor, "telegram", status="resolved", started_epoch=now - 7200,
                 title="Telegram: похоже на блокировку", reason="connect() timeout")
    add_incident(monitor, "internet", status="resolved", started_epoch=now - 90 * 3600,
                 title="Интернет недоступен", reason="Нет доступа в интернет")
    add_incident(monitor, "youtube", status="resolved", started_epoch=now - 300,
                 title="YouTube недоступен", reason="TLS-таймаут")
    return monitor


def test_incidents_report_defaults(tmp_path):
    monitor = _report_monitor(tmp_path)
    report = monitor.incidents_report()
    assert report["total"] == 4
    assert report["limit"] == 100
    assert report["offset"] == 0
    assert len(report["incidents"]) == 4
    starts = [inc["started_epoch"] for inc in report["incidents"]]
    assert starts == sorted(starts, reverse=True)


def test_incidents_report_service_filter(tmp_path):
    monitor = _report_monitor(tmp_path)
    report = monitor.incidents_report(service="youtube")
    assert report["total"] == 2
    assert {inc["service_id"] for inc in report["incidents"]} == {"youtube"}
    assert monitor.incidents_report(service="all")["total"] == 4
    assert monitor.incidents_report(service="bogus")["total"] == 0


def test_incidents_report_status_filter(tmp_path):
    monitor = _report_monitor(tmp_path)
    active = monitor.incidents_report(status="active")
    assert active["total"] == 1
    assert active["incidents"][0]["status"] == "active"
    resolved = monitor.incidents_report(status="resolved")
    assert resolved["total"] == 3
    assert monitor.incidents_report(status="all")["total"] == 4


def test_incidents_report_hours_filter(tmp_path):
    monitor = _report_monitor(tmp_path)
    assert monitor.incidents_report(hours=0)["total"] == 4
    assert monitor.incidents_report(hours=1)["total"] == 2   # два инцидента YouTube
    assert monitor.incidents_report(hours=48)["total"] == 3   # + Telegram (2 ч назад)
    assert monitor.incidents_report(hours=100)["total"] == 4
    # битое значение hours обрабатывается как 0 на уровне HTTP-обработчика
    assert monitor.incidents_report(hours=-5)["total"] == 4


def test_incidents_report_query_filter(tmp_path):
    monitor = _report_monitor(tmp_path)
    assert monitor.incidents_report(query="youtube")["total"] == 2
    assert monitor.incidents_report(query="YOUTUBE")["total"] == 2
    assert monitor.incidents_report(query="БЛОКИРОВКУ")["total"] == 1
    assert monitor.incidents_report(query="интернет")["total"] == 1
    assert monitor.incidents_report(query="щщщ")["total"] == 0
    assert monitor.incidents_report(query="  ")["total"] == 4


def test_incidents_report_pagination(tmp_path):
    monitor = _report_monitor(tmp_path)
    page1 = monitor.incidents_report(limit=2, offset=0)
    page2 = monitor.incidents_report(limit=2, offset=2)
    page3 = monitor.incidents_report(limit=2, offset=4)
    assert page1["total"] == page2["total"] == 4
    assert len(page1["incidents"]) == 2 and len(page2["incidents"]) == 2
    assert page3["incidents"] == []
    ids1 = {inc["id"] for inc in page1["incidents"]}
    ids2 = {inc["id"] for inc in page2["incidents"]}
    assert ids1.isdisjoint(ids2)
    # страницы сохраняют общий порядок «новые сверху»
    combined = [inc["started_epoch"] for inc in page1["incidents"] + page2["incidents"]]
    assert combined == sorted(combined, reverse=True)


def test_incidents_report_combined_filters(tmp_path):
    monitor = _report_monitor(tmp_path)
    report = monitor.incidents_report(service="youtube", status="resolved",
                                      hours=1, query="tls")
    assert report["total"] == 1
    assert report["incidents"][0]["service_id"] == "youtube"
    assert report["incidents"][0]["status"] == "resolved"


def test_incidents_report_active_duration_recomputed(tmp_path):
    monitor = _report_monitor(tmp_path)
    active = monitor.incidents_report(status="active")["incidents"][0]
    assert active["duration_sec"] >= 100
    assert active["ended_at"] is None


# ==========================================================================
# 6. Паритет настроек: Python ↔ config.json ↔ Android
# ==========================================================================

def test_python_settings_pick_up_root_config():
    settings = S.load_settings(CONFIG_PATH)
    payload = root_config()
    assert settings.port == payload["server"]["port"] == 8099
    assert settings.host == payload["server"]["host"]
    assert settings.interval == payload["polling"]["interval_sec"] == 20
    assert settings.check_timeout == payload["polling"]["check_timeout_sec"] == 8
    assert settings.dns_timeout == payload["polling"]["dns_timeout_sec"] == 4
    assert settings.open_after_fails == payload["incidents"]["open_after_failures"] == 2
    assert settings.close_after_oks == payload["incidents"]["close_after_successes"] == 1
    assert settings.max_incidents == payload["incidents"]["max_stored"] == 500
    assert settings.history_age_sec == payload["history"]["max_age_hours"] * 3600
    assert settings.history_max_points == payload["history"]["max_points"]
    assert settings.series_points == payload["history"]["series_points"]
    assert settings.status_labels == payload["status_labels"]
    assert settings.status_colors == payload["status_colors"]
    assert settings.display["macos_menubar"]["mode"] == "overlay"
    assert settings.display["windows_tray"]["dot_pt"] == 16
    assert settings.notifications["windows"]["enabled"] is True
    assert settings.notifications["windows"]["notify_on"] == ["blocked", "down"]


def test_python_services_match_config_json_exactly():
    settings = S.load_settings(CONFIG_PATH)
    payload = root_config()
    assert [svc["id"] for svc in settings.services] == \
        [svc["id"] for svc in payload["services"]]
    for got, expected in zip(settings.services, payload["services"]):
        assert got["name"] == expected["name"]
        assert got["subtitle"] == expected["subtitle"]
        assert got["accent"] == expected["accent"]
        assert got["incident_title"] == expected["incident_title"]
        assert got["hints"] == expected["hints"]
        assert [chk["id"] for chk in got["checks"]] == \
            [chk["id"] for chk in expected["checks"]]
        for got_check, expected_check in zip(got["checks"], expected["checks"]):
            for key in ("kind", "host", "port", "core", "timeout"):
                assert got_check[key] == expected_check[key], \
                    "%s/%s.%s" % (got["id"], got_check["id"], key)
            if expected_check["kind"] == "https":
                assert got_check["path"] == expected_check["path"]
                assert got_check["expect"] == expected_check["expect"]


def test_check_config_tool_returns_zero():
    result = subprocess.run([sys.executable, CHECK_CONFIG_PY],
                            cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    assert "config.json: OK" in result.stdout
    assert "3 сервисов" in result.stdout
    assert "16 проверок" in result.stdout


def test_check_config_validator_matches_root_config():
    module = check_config_module()
    summary = module.check_config(root_config())
    assert summary == {"services": 3, "checks": 16, "port": 8099, "interval": 20}


@pytest.mark.parametrize("mutation", [
    lambda c: c["services"].append(copy.deepcopy(c["services"][0])),
    lambda c: c["services"][0]["checks"][0].__setitem__("kind", "icmp"),
    lambda c: c["server"].__setitem__("port", 0),
    lambda c: c["status_colors"].__setitem__("ok", "red"),
    lambda c: c["display"]["macos_menubar"].__setitem__("mode", "bogus"),
])
def test_check_config_validator_rejects_broken_config(mutation):
    module = check_config_module()
    payload = default_payload()
    mutation(payload)
    with pytest.raises(module.ConfigError):
        module.check_config(payload)


def test_check_config_android_parity_state():
    module = check_config_module()
    payload = root_config()
    state = module.check_android_parity(payload)
    if os.path.isfile(ANDROID_ASSET):
        assert state == "ok"
        assert canonical(read_json(ANDROID_ASSET)) == canonical(payload)
    else:
        assert state == "skip"


def test_android_asset_matches_root_config():
    if not os.path.isfile(ANDROID_ASSET):
        pytest.skip("android assets/config.json ещё не сгенерирован (нет Gradle-сборки)")
    assert canonical(read_json(ANDROID_ASSET)) == canonical(root_config())


def test_android_gradle_syncs_root_config_into_assets():
    build_gradle = read_text(ANDROID_BUILD_GRADLE)
    assert "syncConfigJson" in build_gradle
    assert 'rootProject.file("../config.json")' in build_gradle
    assert "src/main/assets" in build_gradle
    assert "preBuild" in build_gradle
    assert "mergeDebugAssets" in build_gradle
    assert "tasks.withType<Test>()" in build_gradle
    assert "repo.config.path" in build_gradle
    assert "assets.config.path" in build_gradle


def test_android_reads_config_from_assets():
    repository = read_text(ANDROID_CONFIG_REPO_KT)
    assert "context.assets.open(ConfigLoader.ASSET_NAME)" in repository
    assert "ConfigParser.parse" in repository
    assert "AppConfig.DEFAULTS" in repository
    config_kt = read_text(ANDROID_CONFIG_KT)
    assert 'const val ASSET_NAME: String = "config.json"' in config_kt
    # display.* — только для desktop-индикаторов, Android его не читает.
    for json_key in ("interval_sec", "check_timeout_sec", "dns_timeout_sec",
                     "open_after_failures", "close_after_successes", "max_stored",
                     "status_labels", "status_colors", "services", "notifications"):
        assert json_key in config_kt, "Config.kt не разбирает ключ %r" % json_key


def test_android_kotlin_defaults_hosts_match_config():
    """В Kotlin нет «чужих» endpoint'ов: все хосты совпадают с config.json."""
    config_kt = read_text(ANDROID_CONFIG_KT)
    payload = root_config()
    expected = {payload["server"]["host"]}
    for service in payload["services"]:
        for check in service["checks"]:
            expected.add(check["host"])
    found = set(re.findall(r'host\s*=\s*"([^"]+)"', config_kt))
    assert found == expected, "лишние/недостающие хосты в Config.kt: %r" % (
        found.symmetric_difference(expected),)


def test_android_kotlin_defaults_numbers_match_config():
    config_kt = read_text(ANDROID_CONFIG_KT)
    payload = root_config()
    server = re.search(
        r'ServerConfig\(\s*host\s*=\s*"([^"]+)",\s*port\s*=\s*(\d+)\s*\)', config_kt)
    assert server, "не найден ServerConfig(...) в Config.kt"
    assert server.group(1) == payload["server"]["host"]
    assert int(server.group(2)) == payload["server"]["port"]

    polling = re.search(
        r'PollingConfig\(\s*intervalSec\s*=\s*(\d+),\s*checkTimeoutSec\s*=\s*(\d+),'
        r'\s*dnsTimeoutSec\s*=\s*(\d+)\s*\)', config_kt)
    assert polling, "не найден PollingConfig(...) в Config.kt"
    assert int(polling.group(1)) == payload["polling"]["interval_sec"]
    assert int(polling.group(2)) == payload["polling"]["check_timeout_sec"]
    assert int(polling.group(3)) == payload["polling"]["dns_timeout_sec"]

    incidents = re.search(
        r'IncidentsConfig\(\s*openAfterFailures\s*=\s*(\d+),'
        r'\s*closeAfterSuccesses\s*=\s*(\d+),\s*maxStored\s*=\s*(\d+)\s*\)',
        config_kt)
    assert incidents, "не найден IncidentsConfig(...) в Config.kt"
    assert int(incidents.group(1)) == payload["incidents"]["open_after_failures"]
    assert int(incidents.group(2)) == payload["incidents"]["close_after_successes"]
    assert int(incidents.group(3)) == payload["incidents"]["max_stored"]


def test_android_has_unit_tests_for_core():
    tests = glob.glob(os.path.join(ANDROID_DIR, "app", "src", "test", "**", "*.kt"),
                      recursive=True)
    assert tests, "в android нет unit-тестов (src/test/**.kt)"
    names = " ".join(os.path.basename(path) for path in tests)
    assert "Test" in names


def test_android_no_hardcoded_http_endpoints():
    java_root = os.path.join(ANDROID_DIR, "app", "src", "main", "java")
    offenders = []
    for dirpath, _dirnames, filenames in os.walk(java_root):
        for name in filenames:
            if not name.endswith(".kt"):
                continue
            text = read_text(os.path.join(dirpath, name))
            if re.search(r'https?://', text):
                offenders.append(name)
    assert offenders == [], "жёстко зашитые URL в Kotlin: %r" % (offenders,)


# ==========================================================================
# 7. macOS: menubar и установщик
# ==========================================================================

def test_netbar_reads_display_settings_from_config():
    swift = read_text(NETBAR_SWIFT)
    for needle in ("macos_menubar", "overlay_window_pt", "overlay_dot_pt",
                   "status_item_pt", "mode", "NETMON_CONFIG", "NETMON_DIR"):
        assert needle in swift, "NetBar.swift не использует %r" % (needle,)
    # режимы, которые понимает конфиг
    for mode in ("overlay", "dot", "text"):
        assert '"%s"' % mode in swift
    # порт/подписи/цвета берутся из того же config.json (паритет с десктопом)
    for key in ("server", "status_labels", "status_colors"):
        assert 'root["%s"]' % key in swift, \
            "NetBar.swift не читает config.json → %s" % (key,)
    # индикатор опрашивает контрактный API (URL может собираться через
    # appendingPathComponent, поэтому проверяем путь без ведущего слэша)
    assert "api/state" in swift
    assert "api/check" in swift


def test_netbar_has_no_hardcoded_service_endpoints():
    swift = read_text(NETBAR_SWIFT)
    payload = root_config()
    hosts = set()
    for service in payload["services"]:
        for check in service["checks"]:
            hosts.add(check["host"])
    found = sorted(host for host in hosts if host in swift)
    assert found == [], "NetBar.swift содержит хосты сервисов из конфига: %r" % (found,)
    assert "youtube.com" not in swift
    assert "telegram.org" not in swift


def test_install_sh_copies_config_and_exports_netmon_config():
    script = read_text(INSTALL_SH)
    assert 'cp -f "$PROJECT_DIR/config.json" "$RUNTIME_DIR/config.json"' in script
    assert "<key>NETMON_CONFIG</key>" in script
    assert "$RUNTIME_DIR/config.json" in script
    # режим меню берётся из config.json → display.macos_menubar.mode
    assert "macos_menubar" in script


def test_install_sh_syntax_is_valid():
    result = subprocess.run(["bash", "-n", INSTALL_SH],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_menubar_build_script_compiles_netbar():
    script = read_text(MENUBAR_BUILD)
    assert "NetBar.swift" in script
    assert "swiftc" in script


# ==========================================================================
# 8. Windows: трей, entry, spec, установщик
# ==========================================================================

def test_tray_reads_config_json():
    tray = read_text(TRAY_PY)
    assert "_server.load_settings(args.config)" in tray
    assert "settings.port" in tray
    assert "settings.host" in tray
    assert "settings.interval" in tray
    assert '"--config"' in tray
    assert "notifications" in tray
    assert "status_colors" in tray
    assert "display" in tray
    assert "--selftest" in tray


def test_windows_entry_reads_config_and_selftest():
    entry = read_text(ENTRY_PY)
    assert "ensure_config" in entry
    assert "load_settings" in entry
    assert "NETMON_CONFIG" in entry
    assert "--selftest" in entry
    assert "--config" in entry
    assert "--server-only" in entry
    assert "config.json" in entry


def test_pyinstaller_spec_bundles_static_and_config():
    spec = read_text(SPEC_FILE)
    assert 'os.path.join(ROOT, "static")' in spec
    assert 'os.path.join(ROOT, "config.json")' in spec
    assert 'datas.append((STATIC_DIR, "static"))' in spec
    assert 'datas.append((CONFIG_FILE, "."))' in spec
    assert 'hiddenimports = ["pystray._win32"]' in spec
    assert 'name="NetMonitor"' in spec


def test_windows_installer_registers_hkcu_run():
    script = read_text(INSTALL_PS1)
    assert 'HKCU:\\Software\\Microsoft\\Windows\\CurrentVersion\\Run' in script
    assert "Set-ItemProperty" in script
    assert '"NetMonitor"' in script
    assert "config.json.example" in script


def test_windows_uninstaller_removes_run_key():
    script = read_text(UNINSTALL_PS1)
    assert "Remove-ItemProperty" in script or "Remove-Item" in script
    assert "Run" in script


def test_windows_python_files_compile(tmp_path):
    env = dict(os.environ)
    env["PYTHONPYCACHEPREFIX"] = str(tmp_path / "pycache")
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", "server.py", "tray.py",
         os.path.join("windows", "entry.py")],
        cwd=ROOT, capture_output=True, text=True, timeout=120, env=env)
    assert result.returncode == 0, result.stderr


def test_tray_selftest_runs_offline():
    result = subprocess.run([sys.executable, "tray.py", "--selftest"],
                            cwd=ROOT, capture_output=True, text=True, timeout=180)
    assert result.returncode == 0, result.stderr
    assert "РЕЗУЛЬТАТ: OK" in result.stdout
    assert "конфигурация:" in result.stdout


def test_windows_layer_tests_exist():
    assert os.path.isfile(WINDOWS_TESTS)
    text = read_text(WINDOWS_TESTS)
    assert "config.json" in text
    assert "--selftest" in text
    assert "spec" in text.lower()


# ==========================================================================
# 9. Статика и контракт API
# ==========================================================================

def test_static_assets_present():
    for name in ("index.html", "app.js", "style.css", "favicon.svg"):
        path = os.path.join(STATIC_DIR, name)
        assert os.path.isfile(path), "нет static/%s" % name
        assert os.path.getsize(path) > 0
    html = read_text(os.path.join(STATIC_DIR, "index.html"))
    assert '/style.css' in html
    assert '/app.js' in html
    assert '/favicon.svg' in html
    js = read_text(os.path.join(STATIC_DIR, "app.js"))
    for endpoint in ("/api/state", "/api/incidents", "/api/check", "/api/export"):
        assert endpoint in js


def test_static_has_no_external_dependencies():
    html = read_text(os.path.join(STATIC_DIR, "index.html"))
    js = read_text(os.path.join(STATIC_DIR, "app.js"))
    css = read_text(os.path.join(STATIC_DIR, "style.css"))
    for name, text in (("index.html", html), ("app.js", js), ("style.css", css)):
        assert "http://" not in text, "внешний http:// в %s" % name
        assert "https://" not in text, "внешний https:// в %s" % name


def test_api_contract_documents_all_endpoints():
    contract = read_text(CONTRACT_MD)
    for endpoint in ("GET /api/state", "GET /api/incidents", "POST /api/check",
                     "GET /api/export", "GET /api/health"):
        assert endpoint in contract, "контракт не описывает %s" % endpoint
    for field in ("summary", "services", "series", "uptime_1h", "uptime_24h",
                  "active_incident", "recent_incidents", "next_check_in",
                  "total", "limit", "offset", "duration_sec"):
        assert field in contract, "контракт не описывает поле %r" % (field,)


def test_config_schema_describes_config_sections():
    schema = read_json(SCHEMA_JSON)
    payload = root_config()
    assert schema.get("type") == "object"
    properties = schema.get("properties") or {}
    for key in payload:
        assert key in properties, "config.schema.json не описывает %r" % (key,)
    assert schema.get("required") == list(payload) or \
        set(schema.get("required") or []) <= set(payload)
    service = (schema.get("definitions") or {}).get("service") or {}
    if service:
        assert "checks" in (service.get("properties") or {})


def test_workflow_files_are_valid_yaml_and_cover_platforms():
    yaml = pytest.importorskip("yaml")
    workflows = sorted(glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")))
    assert workflows, "нет .github/workflows/*.yml"
    loaded = {}
    for path in workflows:
        with open(path, "r", encoding="utf-8") as handle:
            loaded[os.path.basename(path)] = yaml.safe_load(handle)
    all_text = "\n".join(read_text(path) for path in workflows)
    # CI: конфиг, Python-тесты, Swift-сборка
    assert "tools/check_config.py" in all_text
    assert "tests/test_unit.py" in all_text
    assert "swiftc" in all_text
    # Windows: PyInstaller + selftest + артефакт
    assert "PyInstaller" in all_text
    assert "--selftest" in all_text
    assert "actions/upload-artifact" in all_text
    # Android: хотя бы один workflow собирает APK
    assert "gradlew" in all_text, "ни один workflow не собирает Android APK"


# ==========================================================================
# 10. Гигиена репозитория и документация
# ==========================================================================

def test_gitignore_covers_local_artifacts_and_secrets():
    ignore = read_text(GITIGNORE)
    for pattern in ("data/", ".deploy/", ".toolchain/", ".gradle-cache/",
                    "__pycache__/", "menubar/NetBar.app/", "*.apk", "*.aab",
                    "*.keystore", "*.jks", "android/app/build/"):
        assert pattern in ignore, ".gitignore не игнорирует %r" % (pattern,)


def test_git_ignores_private_artifacts():
    if not os.path.isdir(os.path.join(ROOT, ".git")):
        pytest.skip("не git-репозиторий")
    private = [
        ".deploy/id_ed25519_netmon",
        "data/state.json",
        ".toolchain/jdk17/bin/java",
        "android/app/build/outputs/apk/debug/app-debug.apk",
        "windows/dist/NetMonitor.exe",
        "tests/artifacts/live-state.json",
    ]
    for relative in private:
        result = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", relative],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, "%r не игнорируется git" % (relative,)
    for relative in ("server.py", "config.json", "static/app.js"):
        result = subprocess.run(["git", "-C", ROOT, "check-ignore", "-q", relative],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 1, "исходник %r попал в .gitignore" % (relative,)


def test_no_secrets_or_private_keys_in_sources():
    patterns = [
        re.compile(r"BEGIN (RSA |EC |OPENSSH |DSA )?PRIVATE KEY"),
        re.compile(r"ghp_[A-Za-z0-9]{20,}"),
        re.compile(r"sk-[A-Za-z0-9]{20,}"),
        re.compile(r"AKIA[0-9A-Z]{16}"),
        re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ]
    # Локальные секреты и тулчейны лежат в gitignored-каталогах и не коммитятся;
    # тест проверяет именно исходники репозитория.
    skip_dirs = {".git", ".toolchain", ".deploy", ".secrets", ".tools", ".gh-config",
                 ".venv", ".venv39", ".cache", ".kotlin", ".gradle-cache", "build",
                 "data", "__pycache__", "artifacts", "NetBar.app", ".build"}
    skip_ext = {".png", ".ico", ".jar", ".apk", ".aab", ".zip", ".log", ".pyc",
                ".swiftmodule", ".o"}
    offenders = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [name for name in dirnames if name not in skip_dirs]
        for name in filenames:
            if os.path.splitext(name)[1].lower() in skip_ext:
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > 2 * 1024 * 1024:
                    continue
                text = read_text(path)
            except (OSError, UnicodeDecodeError):
                continue
            for pattern in patterns:
                if pattern.search(text):
                    offenders.append(os.path.relpath(path, ROOT))
                    break
    assert offenders == [], "найдены похожие на секреты строки: %r" % (offenders,)


def test_license_is_mit():
    license_text = read_text(os.path.join(ROOT, "LICENSE"))
    assert "MIT License" in license_text
    assert "Permission is hereby granted" in license_text


def test_readme_files_and_linked_docs_exist():
    assert os.path.isfile(README_MD)
    assert os.path.isfile(README_RU_MD)
    for readme in (README_MD, README_RU_MD):
        text = read_text(readme)
        assert "config.json" in text
        for link in re.findall(r"\]\(([^)#]+)\)", text):
            if link.startswith(("http://", "https://", "mailto:", "../", "#")):
                continue
            target = os.path.normpath(os.path.join(ROOT, link))
            assert os.path.exists(target), "%s ссылается на отсутствующий %r" % (
                os.path.basename(readme), link)


def test_readme_commands_point_to_existing_files():
    text = read_text(README_MD)
    for relative in ("server.py", "tools/check_config.py", "install.sh",
                     "static", "menubar", "windows", "android", "tests"):
        assert os.path.exists(os.path.join(ROOT, relative)), \
            "README упоминает несуществующий %r" % (relative,)
    assert "python3 server.py" in text
    assert "tools/check_config.py" in text


def test_windows_docs_match_artifacts():
    docs = read_text(os.path.join(ROOT, "docs", "windows.md"))
    assert "NetMonitor.exe" in docs
    assert "netmon.spec" in docs
    assert "install.ps1" in docs
    assert "config.json" in docs
    # описанные флаги действительно существуют в entry.py или прокидываются
    # в server.py через parse_known_args (--check-config определяет server.py)
    entry = read_text(ENTRY_PY)
    server_text = read_text(os.path.join(ROOT, "server.py"))
    assert "parse_known_args" in entry
    for flag in ("--selftest", "--server-only", "--tray-only", "--check-config"):
        assert flag in docs
        assert flag in entry or flag in server_text, \
            "docs/windows.md обещает %r, которого нет в entry.py/server.py" % (flag,)


def test_docs_android_exists_and_mentions_build():
    path = os.path.join(ROOT, "docs", "android.md")
    assert os.path.isfile(path), "нет docs/android.md (README на него ссылается)"
    docs = read_text(path)
    assert "gradlew" in docs
    assert "config.json" in docs
