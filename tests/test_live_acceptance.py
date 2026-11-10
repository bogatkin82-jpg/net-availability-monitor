#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Живые приёмочные тесты net-availability-monitor (НЕ для CI).

Поднимают настоящий server.py на отдельном порту (по умолчанию 8801),
ходят в HTTP API по реальной сети и проверяют консистентность данных,
применение config.json и персистентность после рестарта.

По умолчанию тесты ПРОПУСКАЮТСЯ, чтобы `pytest -q tests` и CI оставались
зелёными без сети. Запуск:

    QA_LIVE=1 python -m pytest -q tests/test_live_acceptance.py
    QA_LIVE=1 QA_PORT=8797 python -m pytest -q tests/test_live_acceptance.py -v

Ограничения:
  * реальная сеть может отдавать blocked/down — это нормально, проверяется
    консистентность снимка, а не конкретные статусы;
  * порт по умолчанию 8801 (порт 8797 в этой сессии занят чужим сервером);
    чужие серверы (8099/8789/8797) не трогаем; если порт занят — тесты
    пропускаются с подсказкой задать QA_PORT;
  * временные данные — в tmp_path (никаких записей в data/ репозитория).
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CONFIG_PATH = os.path.join(ROOT, "config.json")
SERVER_PY = os.path.join(ROOT, "server.py")

LIVE = os.environ.get("QA_LIVE") == "1"
PORT = int(os.environ.get("QA_PORT") or 8801)

pytestmark = pytest.mark.skipif(
    not LIVE, reason="живые тесты: запустите с QA_LIVE=1 (нужна сеть и свободный порт)")

STATUSES = {"ok", "degraded", "blocked", "down", "unknown"}
STAGES = {"ok", "dns", "tcp", "tls", "http"}
CHECK_ERRORS = {
    None, "dns_error", "dns_timeout", "connect_timeout", "connect_refused",
    "connect_unreachable", "tls_handshake_timeout", "tls_reset", "tls_cert_error",
    "tls_error", "read_timeout", "http_status_unexpected", "http_error",
    "unknown_error",
}


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def port_available(port):
    """True, если порт свободен (нет чужого listener'а).

    SO_REUSEADDR повторяет поведение ThreadingHTTPServer: порт в TIME_WAIT
    после рестарта считается свободным, а реально слушающий процесс — занятым.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


class PortBusy(RuntimeError):
    pass


class Server(object):
    """Обёртка над запущенным server.py (свой порт и свой data-dir)."""

    def __init__(self, port, data_dir, config_path=CONFIG_PATH, extra_args=None):
        self.port = port
        self.data_dir = data_dir
        self.config_path = config_path
        self.extra_args = list(extra_args or [])
        self.proc = None
        self.output_path = os.path.join(data_dir, "server-%d.log" % port)

    @property
    def base(self):
        return "http://127.0.0.1:%d" % self.port

    def start(self):
        if not port_available(self.port):
            raise PortBusy("порт %d уже занят другим процессом" % self.port)
        os.makedirs(self.data_dir, exist_ok=True)
        argv = [sys.executable, "-B", SERVER_PY, "--port", str(self.port),
                "--data-dir", self.data_dir, "--config", self.config_path]
        argv.extend(self.extra_args)
        log = open(self.output_path, "w", encoding="utf-8")
        self.proc = subprocess.Popen(argv, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
        self.log = log
        self.wait_ready()
        return self

    def wait_ready(self, timeout=45.0):
        deadline = time.time() + timeout
        last_error = None
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise AssertionError("сервер завершился с кодом %s:\n%s" % (
                    self.proc.returncode, self.read_log()))
            try:
                self.get("/api/health", timeout=2.0)
                return True
            except (urllib.error.URLError, OSError, AssertionError) as exc:
                last_error = exc
                time.sleep(0.25)
        raise AssertionError("сервер не поднялся за %.0f c: %r\n%s" % (
            timeout, last_error, self.read_log()))

    def read_log(self):
        try:
            with open(self.output_path, "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()[-4000:]
        except OSError:
            return "<нет лога>"

    def get(self, path, timeout=30.0):
        return self.request("GET", path, timeout=timeout)

    def request(self, method, path, timeout=30.0, expect_status=None):
        url = self.base + path
        request = urllib.request.Request(url, method=method, data=b"" if method == "POST" else None)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                status = response.getcode()
                body = response.read()
                headers = dict(response.headers.items())
        except urllib.error.HTTPError as exc:
            status = exc.code
            body = exc.read()
            headers = dict(exc.headers.items()) if exc.headers else {}
        if expect_status is not None:
            assert status == expect_status, "%s %s -> %s, ждали %s" % (
                method, path, status, expect_status)
        return status, headers, body

    def json(self, path, timeout=30.0, method="GET", expect_status=200):
        status, headers, body = self.request(method, path, timeout=timeout,
                                             expect_status=expect_status)
        return json.loads(body.decode("utf-8")), status, headers, body

    def wait_round(self, timeout=60.0):
        """Дождаться завершения первого/следующего цикла проверок."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            state = self.json("/api/state")[0]
            if state["version"] >= 1 and not state["checking"]:
                return state
            time.sleep(0.5)
        raise AssertionError("опрос не завершился за %.0f c" % timeout)

    def stop(self, timeout=20.0):
        if self.proc is None:
            return
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=10)
        try:
            self.log.close()
        except OSError:
            pass


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    if not LIVE:
        pytest.skip("QA_LIVE!=1")
    data_dir = str(tmp_path_factory.mktemp("live-data"))
    instance = Server(PORT, data_dir)
    try:
        instance.start()
    except (PortBusy, OSError) as exc:
        pytest.skip("порт %d занят (%s) — задайте свободный QA_PORT" % (PORT, exc))
    # Защита от «подключения к чужому серверу»: конфиг должен примениться.
    config = json.load(open(CONFIG_PATH, encoding="utf-8"))
    state = instance.wait_round()
    expected_ids = [svc["id"] for svc in config["services"]]
    actual_ids = [svc["id"] for svc in state["services"]]
    if actual_ids != expected_ids:
        instance.stop()
        pytest.skip("на порту %d отвечает чужой сервер (сервисы %r) — задайте QA_PORT"
                    % (PORT, actual_ids))
    yield instance
    instance.stop()


# --------------------------------------------------------------------------
# 1. Запуск, health, применение config.json
# --------------------------------------------------------------------------

def test_health_and_config_applied(server):
    health, _status, _headers, _body = server.json("/api/health")
    assert health["ok"] is True
    assert health["poll_interval"] == 20          # polling.interval_sec из config.json
    assert isinstance(health["version"], int)
    assert isinstance(health["uptime_sec"], int)
    assert health["rounds"] >= 1

    state = server.wait_round()
    config = json.load(open(CONFIG_PATH, encoding="utf-8"))
    assert state["poll_interval"] == config["polling"]["interval_sec"]
    assert [svc["id"] for svc in state["services"]] == \
        [svc["id"] for svc in config["services"]]
    assert [svc["name"] for svc in state["services"]] == \
        [svc["name"] for svc in config["services"]]
    for got, expected in zip(state["services"], config["services"]):
        assert [check["id"] for check in got["checks"]] == \
            [check["id"] for check in expected["checks"]]


def test_server_binds_port_from_config_file(tmp_path):
    """Без --port сервер слушает server.port из указанного config.json."""
    port = free_port()
    config = json.load(open(CONFIG_PATH, encoding="utf-8"))
    config["server"]["port"] = port
    config_path = os.path.join(str(tmp_path), "config-port.json")
    with open(config_path, "w", encoding="utf-8") as handle:
        json.dump(config, handle, ensure_ascii=False, indent=2)
    # Server.start() всегда добавляет --port, поэтому server.py запускаем
    # напрямую без --port: порт обязан прийти из config.json → server.port.
    log_path = os.path.join(str(tmp_path), "config-port.log")
    log = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-B", SERVER_PY, "--config", config_path,
         "--data-dir", os.path.join(str(tmp_path), "data")],
        cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    try:
        deadline = time.time() + 45
        ready = False
        while time.time() < deadline:
            if proc.poll() is not None:
                raise AssertionError("сервер упал: код %s" % proc.returncode)
            try:
                with urllib.request.urlopen("http://127.0.0.1:%d/api/health" % port,
                                            timeout=2) as response:
                    assert response.getcode() == 200
                    ready = True
                    break
            except (urllib.error.URLError, OSError):
                time.sleep(0.25)
        assert ready, "сервер не поднялся на server.port=%d из config.json" % port
        health = json.loads(urllib.request.urlopen(
            "http://127.0.0.1:%d/api/health" % port, timeout=5).read().decode("utf-8"))
        assert health["ok"] is True
        assert health["poll_interval"] == config["polling"]["interval_sec"]
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=20)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


# --------------------------------------------------------------------------
# 2. /api/state — схема и консистентность
# --------------------------------------------------------------------------

def test_state_contract_and_consistency(server):
    state = server.wait_round()
    for key in ("generated_at", "epoch", "version", "running", "poll_interval",
                "next_check_in", "checking", "internet", "summary", "services",
                "recent_incidents"):
        assert key in state
    assert state["running"] is True
    assert state["internet"]["status"] in STATUSES
    summary = state["summary"]
    assert summary["overall"] in STATUSES
    assert summary["services_total"] == len(state["services"]) == 3
    assert summary["services_ok"] == sum(1 for s in state["services"]
                                         if s["status"] == "ok")
    assert summary["services_affected"] == sum(
        1 for s in state["services"] if s["status"] in ("degraded", "blocked", "down"))
    assert summary["checks_total"] == sum(s["checks_total"] for s in state["services"])
    assert summary["checks_ok"] == sum(s["checks_ok"] for s in state["services"])
    if summary["checks_total"]:
        assert 0 <= summary["checks_ok"] <= summary["checks_total"]
    assert len(state["recent_incidents"]) <= 5
    assert summary["overall"] in ("ok", "degraded", "down", "unknown")
    for service in state["services"]:
        assert service["status"] in STATUSES
        assert isinstance(service["status_label"], str) and service["status_label"]
        assert isinstance(service["status_reason"], str) and service["status_reason"]
        assert service["uptime_1h"] is not None and 0 <= service["uptime_1h"] <= 100
        assert service["uptime_24h"] is not None and 0 <= service["uptime_24h"] <= 100
        assert isinstance(service["series"], list) and service["series"]
        for point in service["series"]:
            assert point["status"] in STATUSES
            assert isinstance(point["t"], int)
        for check in service["checks"]:
            assert check["stage"] in STAGES
            assert check["error"] in CHECK_ERRORS
            assert isinstance(check["core"], bool)
            assert check["ok"] is (check["error"] is None)
            # По контракту latency_ms — время выполнения проверки (в т.ч.
            # неуспешной: пример tls_handshake_timeout → 8621 в api-contract.md).
            assert isinstance(check["latency_ms"], int)
            if check["ok"]:
                assert check["latency_ms"] >= 0
                assert check["error_label"] is None
            else:
                assert check["error_label"]
                assert check["error_text"]
            for stage_key in ("dns_ms", "tcp_ms", "tls_ms", "http_ms"):
                assert stage_key in check["timings"]


def test_state_version_increases_across_rounds(server):
    before = server.json("/api/health")[0]["version"]
    deadline = time.time() + 60
    while time.time() < deadline:
        health = server.json("/api/health")[0]
        if health["version"] > before:
            return
        time.sleep(1)
    raise AssertionError("version не вырос за 60 c (интервал 20 c)")


# --------------------------------------------------------------------------
# 3. /api/incidents — фильтры и пагинация
# --------------------------------------------------------------------------

def test_incidents_report_shape_and_filters(server):
    report, _s, _h, _b = server.json("/api/incidents")
    assert set(report) >= {"total", "limit", "offset", "incidents"}
    assert report["limit"] == 100
    assert report["offset"] == 0
    assert report["total"] >= len(report["incidents"])

    page1 = server.json("/api/incidents?limit=1&offset=0")[0]
    page2 = server.json("/api/incidents?limit=1&offset=1")[0]
    assert page1["total"] == page2["total"] == report["total"]
    assert len(page1["incidents"]) <= 1 and len(page2["incidents"]) <= 1
    if report["total"] >= 2:
        assert page1["incidents"][0]["id"] != page2["incidents"][0]["id"]

    for status in ("active", "resolved"):
        filtered = server.json("/api/incidents?status=%s" % status)[0]
        assert filtered["total"] <= report["total"]
        assert all(inc["status"] == status for inc in filtered["incidents"])

    for service_id in ("youtube", "telegram", "internet"):
        filtered = server.json("/api/incidents?service=%s" % service_id)[0]
        assert all(inc["service_id"] == service_id for inc in filtered["incidents"])

    for hours in (0, 1, 24, 168):
        filtered = server.json("/api/incidents?hours=%d" % hours)[0]
        assert filtered["total"] <= report["total"]

    for query in ("x", "YOUTUBE", "таймаут", "блокировк"):
        filtered = server.json("/api/incidents?q=%s" % urllib.parse.quote(query))[0]
        assert filtered["total"] <= report["total"]
        if query == "x":
            assert filtered["total"] == 0

    bad = server.json("/api/incidents?limit=abc&offset=abc&hours=abc")[0]
    assert bad["limit"] == 100 and bad["offset"] == 0

    # offset за пределами выдачи
    beyond = server.json("/api/incidents?offset=10000")[0]
    assert beyond["incidents"] == []
    assert beyond["total"] == report["total"]


# --------------------------------------------------------------------------
# 4. POST /api/check, /api/export, статика, 404
# --------------------------------------------------------------------------

def test_manual_check_endpoint(server):
    before = server.json("/api/state")[0]["version"]
    status, _headers, body = server.request("POST", "/api/check", timeout=60)
    assert status == 200
    payload = json.loads(body.decode("utf-8"))
    assert "ok" in payload and "state" in payload
    if payload["ok"]:
        assert payload["state"]["version"] >= before
        assert payload["state"]["checking"] is True or \
            payload["state"]["version"] > before
    else:
        assert payload["reason"] == "already_running"
    deadline = time.time() + 60
    while time.time() < deadline:
        state = server.json("/api/state")[0]
        if state["version"] > before and not state["checking"]:
            return
        time.sleep(0.5)
    raise AssertionError("ручная проверка не завершилась за 60 c")


def test_export_endpoint(server):
    status, headers, body = server.request("GET", "/api/export", timeout=30,
                                           expect_status=200)
    disposition = headers.get("Content-Disposition", "")
    assert "attachment" in disposition
    assert "filename=" in disposition
    filename = disposition.split("filename=", 1)[1].strip().strip('"')
    assert filename.startswith("net-monitor-report-")
    assert filename.endswith(".json")
    payload = json.loads(body.decode("utf-8"))
    assert set(payload) == {"exported_at", "state", "incidents"}
    assert payload["state"]["services"]
    assert isinstance(payload["incidents"], list)


def test_static_and_404(server):
    status, headers, body = server.request("GET", "/", expect_status=200)
    assert "text/html" in headers.get("Content-Type", "")
    assert b"<html" in body.lower()

    status, headers, _body = server.request("GET", "/app.js", expect_status=200)
    assert "javascript" in headers.get("Content-Type", "")
    status, headers, _body = server.request("GET", "/style.css", expect_status=200)
    assert "text/css" in headers.get("Content-Type", "")
    status, headers, _body = server.request("GET", "/favicon.svg", expect_status=200)
    assert "svg" in headers.get("Content-Type", "")

    for path in ("/api/nope", "/nope.css", "/server.py", "/data/state.json",
                 "/../server.py", "/static/index.html"):
        status, headers, body = server.request("GET", path)
        assert status == 404, "%s -> %s" % (path, status)
        assert json.loads(body.decode("utf-8")) == {"error": "not_found"}
    status, _h, body = server.request("POST", "/api/nope")
    assert status == 404
    assert json.loads(body.decode("utf-8")) == {"error": "not_found"}


# --------------------------------------------------------------------------
# 5. Персистентность после рестарта
# --------------------------------------------------------------------------

def test_persistence_after_restart(tmp_path):
    port = free_port()
    data_dir = str(tmp_path / "persist-data")
    first = Server(port, data_dir)
    try:
        first.start()
        state_before = first.wait_round()
        # ждём хотя бы один инцидент или просто сохраняем снимок
        first.stop()
        state_path = os.path.join(data_dir, "state.json")
        assert os.path.isfile(state_path), "state.json не создан"
        saved = json.load(open(state_path, encoding="utf-8"))
        assert "series" in saved and "incidents" in saved

        versions_before = state_before["version"]
        incidents_before = len(state_before["recent_incidents"])

        second = Server(port, data_dir)
        try:
            second.start()
            state_after = second.wait_round()
            assert state_after["version"] >= versions_before
            assert len(state_after["recent_incidents"]) >= incidents_before
            for service in state_after["services"]:
                assert service["series"], "история %s не восстановлена" % service["id"]
            for service_before in state_before["services"]:
                after = [svc for svc in state_after["services"]
                         if svc["id"] == service_before["id"]][0]
                assert after["uptime_24h"] >= 0
        finally:
            second.stop()
    finally:
        first.stop()


def test_incidents_persist_and_resolve_consistently(server):
    """Журнал инцидентов согласован: active-записи не имеют ended_at."""
    incidents = server.json("/api/incidents")[0]["incidents"]
    for incident in incidents:
        assert incident["status"] in ("active", "resolved")
        assert incident["service_id"] in ("youtube", "telegram", "internet")
        assert incident["started_epoch"] > 0
        assert incident["duration_sec"] >= 0
        if incident["status"] == "active":
            assert incident["ended_at"] is None
            assert incident["ended_epoch"] is None
        else:
            assert incident["ended_at"] is not None
            assert incident["ended_epoch"] >= incident["started_epoch"]
