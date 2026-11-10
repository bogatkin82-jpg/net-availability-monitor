#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Приёмочные чёрно-ящичные тесты HTTP API net-monitor (stdlib, без зависимостей).

Запуск (сервер уже поднят):
    python3 tests/test_api.py
    python3 tests/test_api.py --base-url http://127.0.0.1:8789
    python3 tests/test_api.py --skip-check        # не дёргать POST /api/check (~8 с)
    python3 tests/test_api.py --cold-start        # доп. проба состояния сразу после старта

Коды выхода: 0 — все проверки прошли, 1 — есть FAIL/ошибки.

Замечание: тест «ожидаемая картина сети» предполагает реальную сеть, где YouTube
блокируется по TLS, Telegram — по TCP, а контрольный канал работает. В другой
сетевой среде он честно упадёт — это не дефект продукта, см. QA-REPORT.md.
"""

import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SERVICE_STATUSES = {"ok", "degraded", "blocked", "down", "unknown"}
INTERNET_STATUSES = {"ok", "degraded", "down"}
OVERALL_STATUSES = SERVICE_STATUSES
CHECK_KINDS = {"https", "tcp", "dns", "tls"}
CHECK_STAGES = {"ok", "dns", "tcp", "tls", "http"}
CHECK_ERRORS = {
    "dns_error", "dns_timeout", "connect_timeout", "connect_refused",
    "connect_unreachable", "tls_handshake_timeout", "tls_reset",
    "tls_cert_error", "tls_error", "read_timeout", "http_status_unexpected",
    "http_error", "unknown_error",
}
TIMING_KEYS = {"dns_ms", "tcp_ms", "tls_ms", "http_ms"}

STATE_REQUIRED = {
    "generated_at", "epoch", "version", "poll_interval", "next_check_in",
    "checking", "internet", "summary", "services", "recent_incidents",
}
SUMMARY_REQUIRED = {
    "overall", "overall_label", "services_total", "services_ok",
    "services_affected", "active_incidents", "avg_latency_ms",
    "checks_total", "checks_ok",
}
SERVICE_REQUIRED = {
    "id", "name", "subtitle", "icon", "accent", "status", "status_label",
    "status_reason", "hint", "latency_ms", "uptime_1h", "uptime_24h",
    "checks_ok", "checks_total", "checks", "series", "active_incident",
    "last_incident",
}
CHECK_REQUIRED = {
    "id", "label", "target", "kind", "core", "ok", "latency_ms", "stage",
    "error", "error_label", "error_text", "http_status", "ip", "timings",
    "measured_at",
}
INCIDENT_REQUIRED = {
    "id", "service_id", "service_name", "title", "reason", "status",
    "started_at", "started_epoch", "ended_at", "ended_epoch", "duration_sec",
    "failed_checks", "total_checks", "service_status",
}

RESULTS = []   # (name, ok, detail)
SKIPS = []     # (name, reason)
KNOWN = []     # (defect_id, name, status, detail)  status: reproduced|unexpected-pass
_KNOWN_IDS = {}


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    if not cond:
        print("   ✗ %s%s" % (name, ("  [%s]" % detail) if detail else ""))


def check_known_defect(defect_id, name, cond, detail=""):
    """Проверка контракта, которая сейчас НАМЕРЕННО падает из-за известного дефекта.

    Если cond истинно — дефект, похоже, исправлен (unexpected-pass: тест надо
    обновить). Если ложно — дефект воспроизведён (reproduced), это ожидаемо и
    не роняет прогон, но печатается и попадает в отчёт.
    """
    if cond:
        print("   ! %s: ожидаемый дефект %s НЕ воспроизводится — похоже, исправлен"
              % (name, defect_id))
        _KNOWN_IDS[defect_id] = "unexpected-pass"
        KNOWN.append((defect_id, name, "unexpected-pass", detail))
    else:
        print("   ~ %s: известный дефект %s воспроизведён%s"
              % (name, defect_id, (" [%s]" % detail) if detail else ""))
        _KNOWN_IDS[defect_id] = "reproduced"
        KNOWN.append((defect_id, name, "reproduced", detail))


def skip(name, reason):
    SKIPS.append((name, reason))
    print("   - SKIP: %s (%s)" % (name, reason))


def section(title):
    print("\n%s" % title)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

def http(method, url, timeout=120):
    req = urllib.request.Request(url, method=method)
    if method == "POST":
        req.data = b""
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, dict(exc.headers), exc.read()


class Client(object):
    def __init__(self, base):
        self.base = base.rstrip("/")

    def raw(self, path, method="GET", timeout=120):
        return http(method, self.base + path, timeout=timeout)

    def json(self, path, method="GET", timeout=120):
        status, headers, body = self.raw(path, method, timeout)
        try:
            payload = json.loads(body.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            payload = None
        return status, headers, payload, body

    def get(self, path, timeout=120):
        status, headers, payload, body = self.json(path, timeout=timeout)
        if status != 200:
            raise AssertionError("GET %s -> HTTP %s: %s" % (path, status, body[:400]))
        return payload


# --------------------------------------------------------------------------
# Валидаторы
# --------------------------------------------------------------------------

def is_iso(value):
    if not isinstance(value, str):
        return False
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def is_num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_incident(prefix, inc, errors):
    if not isinstance(inc, dict):
        errors.append("%s: не объект" % prefix)
        return
    for key in sorted(INCIDENT_REQUIRED):
        if key not in inc:
            errors.append("%s: нет поля %s" % (prefix, key))
    for key in ("id", "service_id", "service_name", "title", "reason", "started_at"):
        if not isinstance(inc.get(key), str) or not inc.get(key):
            errors.append("%s.%s: пустая/не строковая обязательная строка" % (prefix, key))
    if inc.get("status") not in ("active", "resolved"):
        errors.append("%s.status: %r" % (prefix, inc.get("status")))
    if inc.get("service_status") not in SERVICE_STATUSES:
        errors.append("%s.service_status: %r" % (prefix, inc.get("service_status")))
    if not is_num(inc.get("started_epoch")):
        errors.append("%s.started_epoch: %r" % (prefix, inc.get("started_epoch")))
    if not isinstance(inc.get("duration_sec"), int) or isinstance(inc.get("duration_sec"), bool):
        errors.append("%s.duration_sec: %r" % (prefix, inc.get("duration_sec")))
    if inc.get("status") == "active":
        if inc.get("ended_at") is not None or inc.get("ended_epoch") is not None:
            errors.append("%s: у active ended_at/ended_epoch не null" % prefix)
    else:
        if not inc.get("ended_at"):
            errors.append("%s: у resolved нет ended_at" % prefix)
        elif not is_iso(inc.get("ended_at")):
            errors.append("%s.ended_at: не ISO %r" % (prefix, inc.get("ended_at")))
        if not is_num(inc.get("ended_epoch")):
            errors.append("%s.ended_epoch: %r" % (prefix, inc.get("ended_epoch")))


def validate_check(prefix, chk, errors):
    if not isinstance(chk, dict):
        errors.append("%s: не объект" % prefix)
        return
    for key in sorted(CHECK_REQUIRED):
        if key not in chk:
            errors.append("%s: нет поля %s" % (prefix, key))
    for key in ("id", "label", "target"):
        if not isinstance(chk.get(key), str) or not chk.get(key):
            errors.append("%s.%s: пустая обязательная строка" % (prefix, key))
    if chk.get("kind") not in CHECK_KINDS:
        errors.append("%s.kind: %r" % (prefix, chk.get("kind")))
    if not isinstance(chk.get("core"), bool):
        errors.append("%s.core: %r" % (prefix, chk.get("core")))
    if not isinstance(chk.get("ok"), bool):
        errors.append("%s.ok: %r" % (prefix, chk.get("ok")))
    if chk.get("latency_ms") is not None and not (is_num(chk.get("latency_ms"))
                                                  and chk["latency_ms"] >= 0):
        errors.append("%s.latency_ms: %r" % (prefix, chk.get("latency_ms")))
    if chk.get("stage") not in CHECK_STAGES:
        errors.append("%s.stage: %r" % (prefix, chk.get("stage")))
    if chk.get("ok"):
        for key in ("error", "error_label", "error_text"):
            if chk.get(key) is not None:
                errors.append("%s: у успешной проверки %s != null (%r)"
                              % (prefix, key, chk.get(key)))
    else:
        if chk.get("error") not in CHECK_ERRORS:
            errors.append("%s.error: %r" % (prefix, chk.get("error")))
        for key in ("error_label", "error_text"):
            if not isinstance(chk.get(key), str) or not chk.get(key):
                errors.append("%s.%s: пусто при ok=false" % (prefix, key))
        if chk.get("stage") == "ok":
            errors.append("%s: ok=false, но stage=ok" % prefix)
    if chk.get("http_status") is not None and not (isinstance(chk.get("http_status"), int)
                                                   and not isinstance(chk.get("http_status"), bool)):
        errors.append("%s.http_status: %r" % (prefix, chk.get("http_status")))
    if chk.get("ip") is not None and not isinstance(chk.get("ip"), str):
        errors.append("%s.ip: %r" % (prefix, chk.get("ip")))
    timings = chk.get("timings")
    if not isinstance(timings, dict):
        errors.append("%s.timings: не объект" % prefix)
    else:
        if set(timings) != TIMING_KEYS:
            errors.append("%s.timings: ключи %r" % (prefix, sorted(timings)))
        for key, value in timings.items():
            if value is not None and not (is_num(value) and value >= 0):
                errors.append("%s.timings.%s: %r" % (prefix, key, value))
    if not is_iso(chk.get("measured_at")):
        errors.append("%s.measured_at: не ISO %r" % (prefix, chk.get("measured_at")))


def validate_state(state):
    errors = []
    if not isinstance(state, dict):
        return ["/api/state: не объект"]
    for key in sorted(STATE_REQUIRED):
        if key not in state:
            errors.append("нет поля %s" % key)
    if not is_iso(state.get("generated_at")):
        errors.append("generated_at: не ISO %r" % state.get("generated_at"))
    for key in ("epoch", "next_check_in"):
        if not is_num(state.get(key)):
            errors.append("%s: %r" % (key, state.get(key)))
    for key in ("version", "poll_interval"):
        if not isinstance(state.get(key), int) or isinstance(state.get(key), bool):
            errors.append("%s: %r" % (key, state.get(key)))
    if not isinstance(state.get("checking"), bool):
        errors.append("checking: %r" % state.get("checking"))

    inet = state.get("internet")
    if not isinstance(inet, dict):
        errors.append("internet: не объект")
    else:
        if inet.get("status") not in INTERNET_STATUSES:
            errors.append("internet.status вне енума ok|degraded|down: %r" % inet.get("status"))
        if not isinstance(inet.get("label"), str) or not inet.get("label"):
            errors.append("internet.label: пусто")
        if inet.get("latency_ms") is not None and not is_num(inet.get("latency_ms")):
            errors.append("internet.latency_ms: %r" % inet.get("latency_ms"))

    summary = state.get("summary")
    if not isinstance(summary, dict):
        errors.append("summary: не объект")
    else:
        for key in sorted(SUMMARY_REQUIRED):
            if key not in summary:
                errors.append("summary: нет поля %s" % key)
        if summary.get("overall") not in OVERALL_STATUSES:
            errors.append("summary.overall вне енума: %r" % summary.get("overall"))
        if not isinstance(summary.get("overall_label"), str) or not summary.get("overall_label"):
            errors.append("summary.overall_label: пусто")
        for key in ("services_total", "services_ok", "services_affected",
                    "active_incidents", "checks_total", "checks_ok"):
            if not isinstance(summary.get(key), int) or isinstance(summary.get(key), bool):
                errors.append("summary.%s: %r" % (key, summary.get(key)))
        if summary.get("avg_latency_ms") is not None and not is_num(summary.get("avg_latency_ms")):
            errors.append("summary.avg_latency_ms: %r" % summary.get("avg_latency_ms"))

    services = state.get("services")
    if not isinstance(services, list) or len(services) != 3:
        errors.append("services: ожидалось 3, получено %r" % (len(services) if isinstance(services, list) else services))
        services = services if isinstance(services, list) else []
    seen_ids = []
    for idx, svc in enumerate(services):
        prefix = "services[%d]" % idx
        if not isinstance(svc, dict):
            errors.append("%s: не объект" % prefix)
            continue
        for key in sorted(SERVICE_REQUIRED):
            if key not in svc:
                errors.append("%s: нет поля %s" % (prefix, key))
        for key in ("id", "name", "subtitle", "icon", "accent", "status_label", "status_reason"):
            if not isinstance(svc.get(key), str) or not svc.get(key):
                errors.append("%s.%s: пустая обязательная строка" % (prefix, key))
        if svc.get("id") in seen_ids:
            errors.append("%s: дубликат id %r" % (prefix, svc.get("id")))
        seen_ids.append(svc.get("id"))
        if svc.get("status") not in SERVICE_STATUSES:
            errors.append("%s.status вне енума: %r" % (prefix, svc.get("status")))
        if svc.get("hint") is not None and not isinstance(svc.get("hint"), str):
            errors.append("%s.hint: %r" % (prefix, svc.get("hint")))
        if svc.get("latency_ms") is not None and not (is_num(svc.get("latency_ms"))
                                                      and svc["latency_ms"] >= 0):
            errors.append("%s.latency_ms: %r" % (prefix, svc.get("latency_ms")))
        for key in ("uptime_1h", "uptime_24h"):
            value = svc.get(key)
            if not is_num(value) or not (0.0 <= float(value) <= 100.0):
                errors.append("%s.%s: %r" % (prefix, key, value))
        for key in ("checks_ok", "checks_total"):
            if not isinstance(svc.get(key), int) or isinstance(svc.get(key), bool):
                errors.append("%s.%s: %r" % (prefix, key, svc.get(key)))
        if isinstance(svc.get("checks_ok"), int) and isinstance(svc.get("checks_total"), int):
            if not (0 <= svc["checks_ok"] <= svc["checks_total"]):
                errors.append("%s: checks_ok (%r) > checks_total (%r)"
                              % (prefix, svc["checks_ok"], svc["checks_total"]))
        checks = svc.get("checks")
        if not isinstance(checks, list) or not checks:
            errors.append("%s.checks: пусто/не список" % prefix)
            checks = []
        check_ids = set()
        for cidx, chk in enumerate(checks):
            cprefix = "%s.checks[%d]" % (prefix, cidx)
            validate_check(cprefix, chk, errors)
            if isinstance(chk, dict):
                if chk.get("id") in check_ids:
                    errors.append("%s: дубликат id %r" % (cprefix, chk.get("id")))
                check_ids.add(chk.get("id"))
        if checks:
            ok_count = sum(1 for c in checks if isinstance(c, dict) and c.get("ok"))
            if isinstance(svc.get("checks_ok"), int) and svc["checks_ok"] != ok_count:
                errors.append("%s.checks_ok (%r) не совпадает с числом ok-проверок (%d)"
                              % (prefix, svc["checks_ok"], ok_count))
            if isinstance(svc.get("checks_total"), int) and svc["checks_total"] != len(checks):
                errors.append("%s.checks_total (%r) != len(checks) (%d)"
                              % (prefix, svc["checks_total"], len(checks)))
        series = svc.get("series")
        if not isinstance(series, list):
            errors.append("%s.series: не список" % prefix)
        else:
            for pidx, point in enumerate(series):
                if not isinstance(point, dict) or "t" not in point:
                    errors.append("%s.series[%d]: нет t" % (prefix, pidx))
                    continue
                if not isinstance(point.get("t"), int) or isinstance(point.get("t"), bool):
                    errors.append("%s.series[%d].t: %r" % (prefix, pidx, point.get("t")))
                if point.get("status") not in SERVICE_STATUSES:
                    errors.append("%s.series[%d].status: %r" % (prefix, pidx, point.get("status")))
                if point.get("latency_ms") is not None and not is_num(point.get("latency_ms")):
                    errors.append("%s.series[%d].latency_ms: %r" % (prefix, pidx, point.get("latency_ms")))
            if series:
                ts = [p.get("t") for p in series if isinstance(p, dict)]
                if ts != sorted(ts):
                    errors.append("%s.series: не по возрастанию t" % prefix)
        for key in ("active_incident", "last_incident"):
            value = svc.get(key)
            if value is not None:
                validate_incident("%s.%s" % (prefix, key), value, errors)
        if svc.get("active_incident") is not None and svc.get("status") == "ok":
            errors.append("%s: активный инцидент при status=ok" % prefix)

    recent = state.get("recent_incidents")
    if not isinstance(recent, list):
        errors.append("recent_incidents: не список")
    else:
        if len(recent) > 5:
            errors.append("recent_incidents: больше 5 (%d)" % len(recent))
        for ridx, inc in enumerate(recent):
            validate_incident("recent_incidents[%d]" % ridx, inc, errors)
    return errors


# --------------------------------------------------------------------------
# Тесты
# --------------------------------------------------------------------------

def test_health(client):
    section("[1] GET /api/health")
    health = client.get("/api/health")
    check("ok == true", health.get("ok") is True, repr(health.get("ok")))
    for key in ("uptime_sec", "version", "rounds", "poll_interval"):
        check("поле %s — целое число" % key,
              isinstance(health.get(key), int) and not isinstance(health.get(key), bool),
              repr(health.get(key)))
    check("uptime_sec >= 0", (health.get("uptime_sec") or 0) >= 0, repr(health.get("uptime_sec")))
    check("rounds >= 1 (хотя бы один цикл прошёл)", (health.get("rounds") or 0) >= 1,
          repr(health.get("rounds")))
    check("poll_interval > 0", (health.get("poll_interval") or 0) > 0,
          repr(health.get("poll_interval")))
    check("last_round_at — ISO с таймзоной", is_iso(health.get("last_round_at")),
          repr(health.get("last_round_at")))

    interval = health["poll_interval"]
    rounds0 = health["rounds"]
    deadline = time.time() + interval + 15
    grown = False
    while time.time() < deadline:
        time.sleep(1.0)
        if client.get("/api/health")["rounds"] > rounds0:
            grown = True
            break
    check("rounds растёт в фоне (новый цикл за %d+15 с)" % interval, grown,
          "было %d" % rounds0)


def test_state(client):
    section("[2] GET /api/state: схема, типы, енумы")
    status, headers, state, raw = client.json("/api/state")
    check("HTTP 200", status == 200, str(status))
    check("Content-Type application/json",
          "application/json" in headers.get("Content-Type", ""), headers.get("Content-Type"))
    check("Cache-Control: no-store", headers.get("Cache-Control") == "no-store",
          repr(headers.get("Cache-Control")))
    check("Access-Control-Allow-Origin: *",
          headers.get("Access-Control-Allow-Origin") == "*",
          repr(headers.get("Access-Control-Allow-Origin")))
    body_text = raw.decode("utf-8", "replace")
    check("тело — валидный UTF-8 JSON без NaN/Infinity",
          "NaN" not in body_text and "Infinity" not in body_text)
    errors = validate_state(state)
    check("схема /api/state соответствует контракту", not errors,
          "; ".join(errors[:8]) + (" …" if len(errors) > 8 else ""))
    check("id сервисов ровно youtube/telegram/internet",
          [s.get("id") for s in state.get("services", [])] == ["youtube", "telegram", "internet"],
          repr([s.get("id") for s in state.get("services", [])]))
    check("version > 0 (циклы уже были)", state.get("version", 0) > 0, repr(state.get("version")))
    check("next_check_in в диапазоне 0..poll_interval",
          0 <= state.get("next_check_in", -1) <= state.get("poll_interval", 0) + 1,
          "%r / %r" % (state.get("next_check_in"), state.get("poll_interval")))
    return state


def test_real_picture(client, state):
    section("[3] Реальная картина сети после первых циклов")
    services = {s["id"]: s for s in state["services"]}
    yt, tg, inet = services["youtube"], services["telegram"], services["internet"]

    check("youtube == blocked", yt["status"] == "blocked",
          "%r (%s)" % (yt["status"], yt.get("status_label")))
    check("telegram == blocked", tg["status"] == "blocked",
          "%r (%s)" % (tg["status"], tg.get("status_label")))
    check("internet == ok", inet["status"] == "ok",
          "%r (%s)" % (inet["status"], inet.get("status_label")))
    check("internet.label == «Интернет работает»",
          state["internet"]["label"] == "Интернет работает", repr(state["internet"]["label"]))
    check("у заблокированных latency_ms == null",
          yt["latency_ms"] is None and tg["latency_ms"] is None,
          "yt=%r tg=%r" % (yt["latency_ms"], tg["latency_ms"]))
    check("u internet latency_ms — число", is_num(inet["latency_ms"]),
          repr(inet["latency_ms"]))

    # причины
    check("youtube: причина про TLS/таймаут",
          "tls" in yt["status_reason"].lower() and "таймаут" in yt["status_reason"].lower(),
          yt["status_reason"])
    check("telegram: причина про TCP/таймаут",
          "tcp" in tg["status_reason"].lower() and "таймаут" in tg["status_reason"].lower(),
          tg["status_reason"])
    check("у проблемных сервисов есть hint",
          bool(yt.get("hint")) and bool(tg.get("hint")),
          "%r / %r" % (yt.get("hint"), tg.get("hint")))

    # технические тексты таймаутов
    timeouts = [c for c in yt["checks"] + tg["checks"] if not c["ok"]]
    check("у всех упавших проверок есть технический error_text",
          timeouts and all(c.get("error_text") for c in timeouts),
          "%d упавших" % len(timeouts))
    tech = [c["error_text"].lower() for c in timeouts if c.get("error_text")]
    check("error_text таймаутов технический (timeout / timed out)",
          tech and all(("timeout" in t or "timed out" in t) for t in tech),
          "примеры: %r" % tech[:3])
    check("TLS-таймаут у youtube: SSL handshake",
          any("ssl handshake" in c["error_text"].lower()
              for c in timeouts if c.get("error") == "tls_handshake_timeout"),
          "errors=%r" % sorted({c.get("error") for c in timeouts}))
    check("TCP-таймаут у telegram: connect() timeout",
          any("connect() timeout" in c["error_text"].lower()
              for c in timeouts if c.get("error") == "connect_timeout"),
          "errors=%r" % sorted({c.get("error") for c in timeouts}))

    # успешные проверки: error* == null
    success = [c for s in state["services"] for c in s["checks"] if c["ok"]]
    check("у успешных проверок error/error_label/error_text = null",
          success and all(c["error"] is None and c["error_label"] is None
                          and c["error_text"] is None for c in success),
          "%d успешных" % len(success))
    check("у успешных HTTP-проверок есть http_status",
          all(c["http_status"] is not None for c in success if c["kind"] == "https"),
          [c["http_status"] for c in success if c["kind"] == "https"])
    check("у успешных проверок есть measured_at и ip",
          all(c["measured_at"] and c["ip"] for c in success))

    # DNS-этап жив при мёртвом TLS/TCP — видно на постадийной сетке
    dns_checks = [c for c in yt["checks"] + tg["checks"] if c["kind"] == "dns"]
    check("DNS-проверки youtube/telegram проходят (блокировка не в DNS)",
          dns_checks and all(c["ok"] for c in dns_checks),
          [(c["id"], c["ok"]) for c in dns_checks])
    check("интернет-проверки все успешны",
          all(c["ok"] for c in inet["checks"]),
          [(c["id"], c["ok"]) for c in inet["checks"]])

    # серия и uptime
    for svc in state["services"]:
        check("%s: series >= 2 точек" % svc["id"], len(svc["series"]) >= 2,
              len(svc["series"]))
        if svc["status"] == "ok":
            check("%s: uptime_1h/24h > 0 при ok" % svc["id"],
                  svc["uptime_1h"] > 0 and svc["uptime_24h"] > 0,
                  "%r/%r" % (svc["uptime_1h"], svc["uptime_24h"]))
        if svc["status"] == "blocked":
            check("%s: uptime_1h == 0 при blocked" % svc["id"],
                  svc["uptime_1h"] == 0.0, svc["uptime_1h"])

    # инциденты открылись после 2 неудачных циклов
    check("youtube: active_incident открыт", yt["active_incident"] is not None)
    check("telegram: active_incident открыт", tg["active_incident"] is not None)
    check("internet: active_incident отсутствует", inet["active_incident"] is None)
    if yt["active_incident"]:
        inc = yt["active_incident"]
        check("youtube: active_incident.status == active", inc["status"] == "active")
        check("youtube: active_incident.duration_sec > 0", inc["duration_sec"] > 0,
              inc["duration_sec"])
    check("summary.active_incidents >= 2", state["summary"]["active_incidents"] >= 2,
          state["summary"]["active_incidents"])
    check("recent_incidents непустой", len(state["recent_incidents"]) >= 2,
          len(state["recent_incidents"]))


def test_summary_consistency(client, state):
    section("[4] Согласованность summary / services")
    services = state["services"]
    summary = state["summary"]
    statuses = [s["status"] for s in services]
    expected_ok = sum(1 for s in statuses if s == "ok")
    expected_affected = sum(1 for s in statuses if s in ("degraded", "blocked", "down"))
    check("services_total == len(services)", summary["services_total"] == len(services),
          "%r vs %d" % (summary["services_total"], len(services)))
    check("services_ok совпадает с числом ok-сервисов", summary["services_ok"] == expected_ok,
          "%r vs %d" % (summary["services_ok"], expected_ok))
    check("services_affected совпадает с числом проблемных",
          summary["services_affected"] == expected_affected,
          "%r vs %d" % (summary["services_affected"], expected_affected))
    check("services_ok + services_affected <= services_total",
          summary["services_ok"] + summary["services_affected"] <= summary["services_total"])
    check("checks_total == сумма checks_total сервисов",
          summary["checks_total"] == sum(s["checks_total"] for s in services),
          "%r vs %d" % (summary["checks_total"], sum(s["checks_total"] for s in services)))
    check("checks_ok == сумма checks_ok сервисов",
          summary["checks_ok"] == sum(s["checks_ok"] for s in services),
          "%r vs %d" % (summary["checks_ok"], sum(s["checks_ok"] for s in services)))
    latencies = [s["latency_ms"] for s in services if s["latency_ms"] is not None]
    if latencies:
        expected_avg = int(round(sum(latencies) / len(latencies)))
        check("avg_latency_ms == среднее по сервисам (±1)",
              abs(summary["avg_latency_ms"] - expected_avg) <= 1,
              "%r vs %d" % (summary["avg_latency_ms"], expected_avg))
    else:
        check("avg_latency_ms == null при отсутствии латентностей",
              summary["avg_latency_ms"] is None, summary["avg_latency_ms"])
    overall = summary["overall"]
    if state["internet"]["status"] == "down":
        expected = "down"
    elif expected_affected:
        expected = "degraded"
    else:
        expected = "ok"
    check("summary.overall согласован со статусами (%s)" % expected, overall == expected,
          overall)
    check("overall_label непустой и человекочитаемый",
          isinstance(summary["overall_label"], str) and len(summary["overall_label"]) > 3,
          summary["overall_label"])
    # активные инциденты в summary == в журнале
    active = client.get("/api/incidents?status=active&limit=500")
    check("summary.active_incidents == total активных в /api/incidents",
          summary["active_incidents"] == active["total"],
          "%r vs %r" % (summary["active_incidents"], active["total"]))
    # recent_incidents == первые 5 журнала
    journal = client.get("/api/incidents?limit=5")
    check("recent_incidents == первые 5 журнала (ids)",
          [i["id"] for i in state["recent_incidents"]]
          == [i["id"] for i in journal["incidents"]],
          "%r vs %r" % ([i["id"] for i in state["recent_incidents"]],
                        [i["id"] for i in journal["incidents"]]))


def test_incident_filters(client):
    section("[5] GET /api/incidents: поиск, фильтры, енумы")
    status, headers, base, _ = client.json("/api/incidents")
    check("HTTP 200", status == 200, str(status))
    check("Cache-Control: no-store", headers.get("Cache-Control") == "no-store",
          repr(headers.get("Cache-Control")))
    for key in ("total", "limit", "offset", "incidents"):
        check("ответ содержит %s" % key, key in base)
    check("limit по умолчанию 100", base.get("limit") == 100, base.get("limit"))
    check("offset по умолчанию 0", base.get("offset") == 0, base.get("offset"))
    total = base["total"]
    check("total == len(incidents) без пагинации", total == len(base["incidents"]),
          "%r vs %d" % (total, len(base["incidents"])))
    errors = []
    for idx, inc in enumerate(base["incidents"]):
        validate_incident("incidents[%d]" % idx, inc, errors)
    check("объекты инцидентов соответствуют контракту", not errors,
          "; ".join(errors[:5]))
    starts = [i["started_epoch"] for i in base["incidents"]]
    check("сортировка: новые сверху", starts == sorted(starts, reverse=True))

    all_items = base["incidents"]

    def expected_total(predicate):
        return sum(1 for i in all_items if predicate(i))

    # --- поиск q: регистр, кириллица/латиница
    def by_q(query, items):
        needle = query.strip().lower()
        if not needle:
            return items
        return [i for i in items
                if needle in (" ".join([i["service_name"], i["title"], i["reason"]])).lower()]

    for query in ("youtube", "YOUTUBE", "YouTube", "таймаут", "ТАЙМАУТ", "Таймаут",
                  "telegram", "tls", "tcp", "dns"):
        got = client.get("/api/incidents?q=" + urllib.parse.quote(query))
        exp = len(by_q(query, all_items))
        check("q=%r: total %d == ожидаемое %d" % (query, got["total"], exp),
              got["total"] == exp, "total=%r" % got["total"])
        check("q=%r: все результаты содержат подстроку" % query,
              all(query.strip().lower() in " ".join(
                  [i["service_name"], i["title"], i["reason"]]).lower()
                  for i in got["incidents"]))
    got = client.get("/api/incidents?q=" + urllib.parse.quote("  таймаут  "))
    check("q с пробелами по краям обрабатывается",
          got["total"] == expected_total(
              lambda i: "таймаут" in " ".join([i["service_name"], i["title"], i["reason"]]).lower()),
          got["total"])
    got = client.get("/api/incidents?q=" + urllib.parse.quote("щщщщ"))
    check("заведомо пустой q -> total=0 и incidents=[]",
          got["total"] == 0 and got["incidents"] == [], "%r %r" % (got["total"], got["incidents"]))
    got = client.get("/api/incidents?q=")
    check("пустой q -> без фильтрации", got["total"] == total, "%r vs %r" % (got["total"], total))

    # --- service
    for svc in ("youtube", "telegram", "internet", "all"):
        got = client.get("/api/incidents?service=" + svc)
        exp = total if svc == "all" else sum(1 for i in all_items if i["service_id"] == svc)
        check("service=%s: total %d == %d" % (svc, got["total"], exp),
              got["total"] == exp, "total=%r" % got["total"])
        if svc != "all":
            check("service=%s: все результаты этого сервиса" % svc,
                  all(i["service_id"] == svc for i in got["incidents"]))
    got = client.get("/api/incidents?service=bogus")
    check("service=bogus -> 0", got["total"] == 0, got["total"])

    # --- status
    for st in ("active", "resolved", "all"):
        got = client.get("/api/incidents?status=" + st)
        exp = total if st == "all" else sum(1 for i in all_items if i["status"] == st)
        check("status=%s: total %d == %d" % (st, got["total"], exp),
              got["total"] == exp, "total=%r" % got["total"])
        if st != "all":
            check("status=%s: все результаты нужного статуса" % st,
                  all(i["status"] == st for i in got["incidents"]))
    got = client.get("/api/incidents?status=bogus")
    check("status=bogus -> 0", got["total"] == 0, got["total"])

    # --- hours
    now = time.time()
    for hours in ("0", "1", "24", "48"):
        got = client.get("/api/incidents?hours=" + hours)
        if hours == "0":
            exp = total
        else:
            exp = sum(1 for i in all_items
                      if (i["started_epoch"] or 0) >= now - float(hours) * 3600)
        check("hours=%s: total %d == %d" % (hours, got["total"], exp),
              got["total"] == exp, "total=%r" % got["total"])
    got_1 = client.get("/api/incidents?hours=1")
    got_24 = client.get("/api/incidents?hours=24")
    check("hours=1 ⊆ hours=24 ⊆ все",
          set(i["id"] for i in got_1["incidents"]) <= set(i["id"] for i in got_24["incidents"]))
    got = client.get("/api/incidents?hours=abc")
    check("hours=abc -> как 0 (без ограничения)", got["total"] == total,
          "%r vs %r" % (got["total"], total))

    # --- комбинации
    combos = ["service=youtube&status=active", "service=telegram&status=active",
              "status=resolved&hours=24", "service=internet&status=all",
              "q=%s&service=telegram" % urllib.parse.quote("таймаут")]
    for combo in combos:
        got = client.get("/api/incidents?" + combo)
        params = dict(p.split("=", 1) for p in combo.split("&"))
        for inc in got["incidents"]:
            ok = True
            if "service" in params and params["service"] != "all":
                ok = ok and inc["service_id"] == params["service"]
            if "status" in params and params["status"] != "all":
                ok = ok and inc["status"] == params["status"]
            if "q" in params:
                needle = urllib.parse.unquote(params["q"]).lower()
                ok = ok and needle in " ".join(
                    [inc["service_name"], inc["title"], inc["reason"]]).lower()
            if "hours" in params and params["hours"] != "0":
                ok = ok and (inc["started_epoch"] or 0) >= now - float(params["hours"]) * 3600
            check("combo %s: элемент соответствует фильтрам" % combo, ok,
                  inc["id"])
        check("combo %s: total == len(incidents)" % combo,
              got["total"] == len(got["incidents"]) if got["total"] <= got["limit"]
              else True)

    # --- пагинация
    if total >= 2:
        expected_ids = [i["id"] for i in all_items]
        seen = []
        totals = set()
        offset = 0
        while offset < total:
            page = client.get("/api/incidents?limit=1&offset=%d" % offset)
            totals.add(page["total"])
            check("paging offset=%d: total неизменен (%r)" % (offset, total),
                  page["total"] == total, page["total"])
            check("paging offset=%d: ровно 1 элемент" % offset,
                  len(page["incidents"]) == 1, len(page["incidents"]))
            seen.extend(i["id"] for i in page["incidents"])
            offset += 1
        check("paging: страницы не пересекаются и покрывают всё", seen == expected_ids,
              "%r vs %r" % (seen, expected_ids))
        page = client.get("/api/incidents?limit=100&offset=100000")
        check("paging: offset за пределами -> пусто, total тот же",
              page["incidents"] == [] and page["total"] == total,
              "%r/%r" % (len(page["incidents"]), page["total"]))
    else:
        skip("paging по limit/offset", "в журнале меньше 2 инцидентов (%d)" % total)

    got = client.get("/api/incidents?limit=999")
    check("limit=999 клампится до 500", got["limit"] == 500, got["limit"])
    got = client.get("/api/incidents?limit=0")
    check("limit=0 клампится до 1", got["limit"] == 1, got["limit"])
    got = client.get("/api/incidents?limit=-5")
    check("limit=-5 клампится до 1", got["limit"] == 1, got["limit"])
    got = client.get("/api/incidents?limit=abc")
    check("limit=abc -> 100 по умолчанию", got["limit"] == 100, got["limit"])
    got = client.get("/api/incidents?offset=-3")
    check("offset=-3 клампится до 0", got["offset"] == 0, got["offset"])
    got = client.get("/api/incidents?offset=abc")
    check("offset=abc -> 0 по умолчанию", got["offset"] == 0, got["offset"])

    # q + paging
    q = "таймаут"
    q_total = client.get("/api/incidents?q=" + urllib.parse.quote(q))["total"]
    if q_total >= 2:
        p1 = client.get("/api/incidents?q=%s&limit=1&offset=0" % urllib.parse.quote(q))
        p2 = client.get("/api/incidents?q=%s&limit=1&offset=1" % urllib.parse.quote(q))
        check("q+paging: total неизменен", p1["total"] == p2["total"] == q_total,
              "%r %r %r" % (p1["total"], p2["total"], q_total))
        check("q+paging: страницы не пересекаются",
              not (set(i["id"] for i in p1["incidents"]) & set(i["id"] for i in p2["incidents"])))
    else:
        skip("q+paging", "по q=%r меньше 2 записей" % q)


def test_next_check_in(client, state):
    section("[10] Точность поля next_check_in")
    interval = state["poll_interval"]
    if interval < 12:
        skip("точность next_check_in", "poll_interval=%d слишком мал" % interval)
        return
    # Ждём конца текущего цикла и следим, с каким next_check_in начнётся следующий.
    rounds0 = client.get("/api/health")["rounds"]
    deadline = time.time() + interval + 15
    while time.time() < deadline and client.get("/api/health")["rounds"] <= rounds0:
        time.sleep(0.2)
    last_value = None
    saw_start = False
    deadline = time.time() + interval + 10
    while time.time() < deadline:
        snap = client.get("/api/state")
        if snap["checking"]:
            saw_start = True
            break
        last_value = snap["next_check_in"]
        time.sleep(0.2)
    check("удалось поймать начало следующего цикла", saw_start)
    if not saw_start or last_value is None:
        return
    # Контракт: next_check_in — через сколько секунд начнётся следующая проверка.
    # Фактически цикл стартует, когда поле показывает ~длительность проверки.
    check_known_defect(
        "D-2", "next_check_in перед стартом цикла близок к 0 (<= 2 c)",
        last_value <= 2.0,
        "перед стартом цикла показывал %.1f c (poll_interval=%d)" % (last_value, interval))


def test_check_endpoint(client, state):
    section("[6] POST /api/check")
    interval = state["poll_interval"]
    if interval < 10:
        skip("POST /api/check", "poll_interval=%d слишком мал для безопасного теста" % interval)
        return
    # Дождаться окончания текущего цикла. POST заберёт round_lock сразу после
    # него, а плановый цикл (если начнётся) будет ждать освобождения блокировки.
    rounds0 = client.get("/api/health")["rounds"]
    deadline = time.time() + interval + 15
    while time.time() < deadline and client.get("/api/health")["rounds"] <= rounds0:
        time.sleep(0.2)
    st = client.get("/api/state")
    check("подготовка: текущий цикл завершён", st["checking"] is False, st["checking"])

    holder = {}

    def do_post():
        started = time.time()
        status, headers, payload, _ = client.json("/api/check", method="POST", timeout=120)
        holder["status"] = status
        holder["payload"] = payload
        holder["duration"] = time.time() - started

    prev_version = st["version"]
    thread = threading.Thread(target=do_post)
    thread.start()

    # ловим состояние checking=true, затем второй POST
    saw_checking = False
    deadline = time.time() + 8
    concurrent = None
    while time.time() < deadline:
        snap = client.get("/api/state")
        if snap["checking"]:
            saw_checking = True
            concurrent = client.json("/api/check", method="POST", timeout=30)
            break
        if not thread.is_alive():
            break
        time.sleep(0.15)

    thread.join(timeout=120)
    check("POST /api/check: поток завершился", not thread.is_alive())
    result = holder.get("payload") or {}
    check("POST /api/check: HTTP 200", holder.get("status") == 200, holder.get("status"))
    check("POST /api/check: ok == true", result.get("ok") is True, repr(result.get("ok")))
    check("POST /api/check: вернул state со схемой",
          isinstance(result.get("state"), dict)
          and not validate_state(result["state"]),
          "; ".join(validate_state(result.get("state") or {})[:5]))
    check("POST /api/check: version == предыдущему + 1",
          (result.get("state") or {}).get("version") == prev_version + 1,
          "%r vs %r+1" % ((result.get("state") or {}).get("version"), prev_version))
    duration = holder.get("duration") or 0
    check("POST /api/check: длительность 1..45 с", 1.0 <= duration <= 45.0,
          "%.1f c" % duration)
    check("POST /api/check: state.checking == false после завершения",
          (result.get("state") or {}).get("checking") is False,
          (result.get("state") or {}).get("checking"))

    check("во время цикла /api/state отдаёт checking=true", saw_checking)
    if concurrent:
        cstatus, cheaders, cpayload, _ = concurrent
        check("повторный POST во время цикла: HTTP 200", cstatus == 200, cstatus)
        check("повторный POST: ok == false", (cpayload or {}).get("ok") is False,
              repr((cpayload or {}).get("ok")))
        check("повторный POST: reason == already_running",
              (cpayload or {}).get("reason") == "already_running",
              repr((cpayload or {}).get("reason")))
        check("повторный POST: отдаёт текущий state",
              isinstance((cpayload or {}).get("state"), dict))
    else:
        skip("повторный POST during cycle", "не поймали checking=true (цикл слишком быстрый?)")

    health = client.get("/api/health")
    check("health.version совпадает с version после ручного опроса",
          health["version"] == (result.get("state") or {}).get("version"),
          "%r vs %r" % (health["version"], (result.get("state") or {}).get("version")))
    # Сразу после ручного цикла планировщик может запустить свой (он ждал lock),
    # поэтому ждём, пока checking снова станет false.
    became_idle = False
    deadline = time.time() + interval + 20
    while time.time() < deadline:
        if client.get("/api/state")["checking"] is False:
            became_idle = True
            break
        time.sleep(0.3)
    check("после POST и возможного планового цикла /api/state отдаёт checking=false",
          became_idle)


def test_export(client):
    section("[7] GET /api/export")
    status, headers, payload, body = client.json("/api/export", timeout=60)
    check("HTTP 200", status == 200, status)
    check("Content-Type application/json",
          "application/json" in headers.get("Content-Type", ""), headers.get("Content-Type"))
    disposition = headers.get("Content-Disposition") or headers.get("Content-disposition") or ""
    check("Content-Disposition: attachment", "attachment" in disposition.lower(), disposition)
    match = re.search(r'filename="?net-monitor-report-(\d{8}-\d{6})\.json"?', disposition)
    check("filename вида net-monitor-report-YYYYMMDD-HHMMSS.json", match is not None,
          disposition)
    if match:
        stamp = match.group(1)
        try:
            datetime.strptime(stamp, "%Y%m%d-%H%M%S")
            valid_stamp = True
        except ValueError:
            valid_stamp = False
        check("в штампе времени валидные дата/время", valid_stamp, stamp)
        check("штамп соответствует exported_at",
              _stamp_matches(stamp, payload.get("exported_at")),
              "%s vs %r" % (stamp, payload.get("exported_at")))
    check("тело: exported_at/state/incidents",
          set(payload) == {"exported_at", "state", "incidents"} or
          {"exported_at", "state", "incidents"} <= set(payload),
          sorted(payload))
    check("exported_at — ISO с таймзоной", is_iso(payload.get("exported_at")),
          payload.get("exported_at"))
    check("state в экспорте валиден", not validate_state(payload.get("state")),
          "; ".join(validate_state(payload.get("state") or {})[:5]))
    incidents = payload.get("incidents")
    check("incidents — список", isinstance(incidents, list), type(incidents).__name__)
    errors = []
    for idx, inc in enumerate(incidents if isinstance(incidents, list) else []):
        validate_incident("export.incidents[%d]" % idx, inc, errors)
    check("объекты инцидентов в экспорте валидны", not errors, "; ".join(errors[:5]))
    journal = client.get("/api/incidents?limit=500")
    check("экспорт содержит весь журнал (>= total журнала)",
          len(incidents) >= journal["total"],
          "%d vs %d" % (len(incidents), journal["total"]))
    check("экспорт подписан как attachment и не кэшируется",
          headers.get("Cache-Control") == "no-store", repr(headers.get("Cache-Control")))


def _stamp_matches(stamp, exported_at):
    if not isinstance(exported_at, str):
        return False
    try:
        parsed = datetime.fromisoformat(exported_at.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.strftime("%Y%m%d-%H%M%S") == stamp


def test_404(client):
    section("[8] 404 и защита статики")
    for path in ("/api/nope", "/api/state/extra", "/nope.css", "/nope",
                 "/server.py", "/data/state.json", "/../server.py"):
        status, headers, payload, body = client.json(path)
        check("%s -> 404" % path, status == 404, status)
        check("%s -> JSON {error: not_found}" % path,
              payload == {"error": "not_found"}, repr(payload)[:120])
        check("%s -> Content-Type json" % path,
              "application/json" in headers.get("Content-Type", ""),
              headers.get("Content-Type"))
    status, headers, payload, body = client.json("/api/nope", method="POST")
    check("POST /api/nope -> 404 JSON", status == 404 and payload == {"error": "not_found"},
          "%r %r" % (status, payload))


def test_static(client):
    section("[9] Статика")
    cases = [
        ("/", "text/html", "<html"),
        ("/app.js", "javascript", "/api/state"),
        ("/style.css", "text/css", None),
        ("/favicon.svg", "image/svg+xml", "<svg"),
    ]
    for path, ctype, marker in cases:
        status, headers, body = client.raw(path)
        check("%s -> 200" % path, status == 200, status)
        check("%s: Content-Type %s" % (path, ctype),
              ctype in headers.get("Content-Type", "").lower(), headers.get("Content-Type"))
        check("%s: Cache-Control no-cache" % path,
              headers.get("Cache-Control") == "no-cache", repr(headers.get("Cache-Control")))
        text = body.decode("utf-8", "replace")
        check("%s: непустое тело" % path, len(text) > 100, len(text))
        if marker:
            check("%s: содержит %r" % (path, marker), marker.lower() in text.lower())
    status, headers, payload, body = client.json("/")
    check("/: тело — валидный JSON? (ожидается HTML)", payload is None)
    html = client.raw("/")[2].decode("utf-8", "replace")
    for needle in ("app.js", "style.css", "Мониторинг доступности"):
        check("index.html ссылается/содержит %r" % needle, needle in html)
    # HEAD
    status, headers, body = client.raw("/", method="HEAD")
    check("HEAD / -> 200", status == 200, status)
    check("HEAD /: text/html", "text/html" in headers.get("Content-Type", ""),
          headers.get("Content-Type"))
    check("HEAD /: пустое тело", body == b"", len(body))


def test_cold_start():
    section("[11] Проба холодного старта (--cold-start)")
    tmpdir = tempfile.mkdtemp(prefix="netmon-qa-cold-")
    port = _free_port()
    server = os.path.join(PROJECT_DIR, "server.py")
    proc = subprocess.Popen(
        [sys.executable, "-B", server, "--port", str(port), "--interval", "60",
         "--data-dir", tmpdir],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    client = Client("http://127.0.0.1:%d" % port)
    state = None
    try:
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                state = client.get("/api/state", timeout=5)
                break
            except Exception:  # noqa: BLE001
                if proc.poll() is not None:
                    break
                time.sleep(0.1)
        check("холодный сервер ответил на /api/state", state is not None)
        if state is None:
            return
        inet_status = (state.get("internet") or {}).get("status")
        inet_label = (state.get("internet") or {}).get("label")
        # До первого цикла возможны два окна: (а) планировщик ещё спит 0.25 с —
        # checking=false, (б) цикл уже идёт — checking=true. Оба легальны.
        all_unknown = all(s.get("status") == "unknown" for s in state.get("services", []))
        check("до первого цикла сервисы в статусе unknown", all_unknown,
              [s.get("status") for s in state.get("services", [])])
        if not all_unknown:
            check("до первого цикла: checking == true", state.get("checking") is True,
                  repr(state.get("checking")))
        check_known_defect(
            "D-1", "internet.status до первого цикла в енуме ok|degraded|down (контракт)",
            inet_status in INTERNET_STATUSES,
            "получено %r (label %r)" % (inet_status, inet_label))
        check_known_defect(
            "D-1b", "internet.label до первого цикла из списка контракта",
            inet_label in ("Интернет работает", "Интернет нестабилен",
                           "Похоже, интернета нет"),
            repr(inet_label))
        errors = [e for e in validate_state(state)
                  if "internet.status" not in e and "checks: пусто" not in e]
        check("схема холодного снимка валидна (кроме енума internet.status "
              "и пустых checks до первого цикла)",
              not errors, "; ".join(errors[:5]))
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmpdir, ignore_errors=True)


def _free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="Приёмочные тесты HTTP API net-monitor")
    parser.add_argument("--base-url", default="http://127.0.0.1:8789")
    parser.add_argument("--skip-check", action="store_true",
                        help="не выполнять POST /api/check (долго)")
    parser.add_argument("--cold-start", action="store_true",
                        help="доп. проба состояния до первого цикла на отдельном порту")
    args = parser.parse_args(argv)




    client = Client(args.base_url)
    print("=" * 72)
    print("net-monitor: приёмочные тесты API")
    print("сервер: %s" % args.base_url)
    print("=" * 72)

    try:
        client.get("/api/health")
    except Exception as exc:  # noqa: BLE001
        print("Сервер недоступен: %s" % exc)
        return 1

    try:
        test_health(client)
        state = test_state(client)
        test_real_picture(client, state)
        test_summary_consistency(client, state)
        test_incident_filters(client)
        if args.skip_check:
            skip("POST /api/check", "--skip-check")
        else:
            test_check_endpoint(client, state)
        test_export(client)
        test_404(client)
        test_static(client)
        test_next_check_in(client, client.get("/api/state"))
        if args.cold_start:
            test_cold_start()
    except Exception:  # noqa: BLE001
        print("\n[CRASH] тест упал с исключением:")
        traceback.print_exc()
        RESULTS.append(("непойманное исключение в наборе тестов", False, ""))

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print("Проверок: %d, OK: %d, FAIL: %d, SKIP: %d" % (
        len(RESULTS), len(RESULTS) - len(failed), len(failed), len(SKIPS)))
    if failed:
        print("\nУпавшие проверки:")
        for name in failed:
            print("  ✗ %s" % name)
    if SKIPS:
        print("\nПропущено:")
        for name, reason in SKIPS:
            print("  - %s (%s)" % (name, reason))
    if KNOWN:
        print("\nИзвестные дефекты (ожидаемые xfail, не роняют прогон):")
        for defect_id, name, status, detail in KNOWN:
            print("  ~ %s [%s] %s%s" % (
                defect_id, status, name, (" — %s" % detail) if detail else ""))
        print("  см. net-monitor/QA-REPORT.md, раздел «Дефекты»")
    if failed:
        print("\nРЕЗУЛЬТАТ: FAIL")
        return 1
    print("\nРЕЗУЛЬТАТ: OK" + (" (с известными дефектами)" if KNOWN else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
