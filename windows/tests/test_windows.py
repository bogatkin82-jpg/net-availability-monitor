#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Тесты Windows-слоя net-availability-monitor (stdlib, без GUI и внешней сети).

Покрывают то, что нельзя проверить на macOS глазами:
  * загрузку и валидацию config.json (приоритеты, сообщения, код выхода 2);
  * применение настроек сервером (порт/интервал/один сервис из конфига);
  * логику трея без GUI: --selftest, меню, tooltip, цвета, автозапуск;
  * --selftest сборки (windows/entry.py), статику spec-файла;
  * встроенный сервер EmbeddedServer (режим «сервер + трей»).

Запуск из корня репозитория:
    python3 windows/tests/test_windows.py
"""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.error
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WINDOWS_DIR = os.path.join(ROOT, "windows")
SERVER_PY = os.path.join(ROOT, "server.py")
TRAY_PY = os.path.join(ROOT, "tray.py")
ENTRY_PY = os.path.join(WINDOWS_DIR, "entry.py")
SPEC_FILE = os.path.join(WINDOWS_DIR, "netmon.spec")

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)
if WINDOWS_DIR not in sys.path:
    sys.path.insert(0, WINDOWS_DIR)

import server as S  # noqa: E402
import tray as T  # noqa: E402

PYTHON = sys.executable


# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------

def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def minimal_config(port=8099, interval=20):
    """Минимальный корректный конфиг: один сервис, одна TCP-проверка."""
    return {
        "version": 1,
        "server": {"host": "127.0.0.1", "port": port},
        "polling": {"interval_sec": interval, "check_timeout_sec": 2,
                    "dns_timeout_sec": 1},
        "services": [{
            "id": "solo",
            "name": "Solo",
            "subtitle": "тестовый сервис",
            "checks": [{
                "id": "solo_tcp",
                "label": "TCP 127.0.0.1:1",
                "kind": "tcp",
                "host": "127.0.0.1",
                "port": 1,
                "core": True,
                "timeout": 0.8,
            }],
        }],
    }


def normalize(config):
    """Разобрать конфиг в памяти (как load_settings, но без файла)."""
    settings = S.Settings()
    settings.raw = config
    S._normalize_config(config, settings, "test")
    return settings


def subprocess_env():
    env = dict(os.environ)
    env.pop("NETMON_CONFIG", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def http_json(url, timeout=5.0, method="GET"):
    request = urllib.request.Request(url, data=b"" if method == "POST" else None,
                                     method=method)
    with LOCAL_OPENER.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def wait_for_health(port, timeout=20.0):
    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            payload = http_json("http://127.0.0.1:%d/api/health" % port, timeout=2.0)
            if payload.get("ok"):
                return payload
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last_error = exc
        time.sleep(0.2)
    raise AssertionError("сервер не ответил на /api/health: %s" % last_error)


class TempDirTestCase(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="netmon-win-test-")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def write_config(self, config, name="config.json"):
        path = os.path.join(self.tmpdir, name)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(config, handle, ensure_ascii=False, indent=2)
        return path


# --------------------------------------------------------------------------
# Конфигурация: встроенные значения и приоритеты
# --------------------------------------------------------------------------

class DefaultsTests(unittest.TestCase):
    def test_builtin_defaults_match_v1(self):
        """Без config.json поведение и тексты совпадают с историческим v1."""
        self.assertEqual([svc["id"] for svc in S.SERVICES],
                         ["youtube", "telegram", "internet"])
        self.assertEqual(len(S.SERVICE_BY_ID["youtube"]["checks"]), 5)
        self.assertEqual(len(S.SERVICE_BY_ID["telegram"]["checks"]), 7)
        self.assertEqual(len(S.SERVICE_BY_ID["internet"]["checks"]), 4)
        self.assertEqual(S.OPEN_AFTER_FAILS, 2)
        self.assertEqual(S.CLOSE_AFTER_OKS, 1)
        self.assertEqual(S.SERIES_MAX_AGE, 24 * 3600)
        self.assertEqual(S.SERIES_MAX_POINTS, 8640)
        self.assertEqual(S.SERIES_POINTS, 180)
        self.assertEqual(S.SERVER_PORT, 8099)
        self.assertEqual(S.SERVER_HOST, "127.0.0.1")
        self.assertEqual(S.STATUS_LABELS["blocked"], "Похоже на блокировку")
        self.assertIn("blocked", S.HINTS["youtube"])
        self.assertTrue(S.NOTIFICATIONS["windows"]["enabled"])

    def test_default_check_timeout_and_dns(self):
        settings = S._build_settings(S._default_config(), "builtin", "test")
        self.assertEqual(settings.check_timeout, 8.0)
        self.assertEqual(settings.dns_timeout, 4.0)


class ConfigResolutionTests(TempDirTestCase):
    def test_explicit_path_overrides_env(self):
        explicit = self.write_config(minimal_config(port=8123, interval=7),
                                     "explicit.json")
        env = self.write_config(minimal_config(port=8999, interval=99), "env.json")
        old_env = os.environ.get("NETMON_CONFIG")
        try:
            os.environ["NETMON_CONFIG"] = env
            settings = S.load_settings(explicit)
            self.assertEqual(settings.source, explicit)
            self.assertEqual(settings.port, 8123)
            self.assertEqual(settings.interval, 7)
        finally:
            if old_env is None:
                os.environ.pop("NETMON_CONFIG", None)
            else:
                os.environ["NETMON_CONFIG"] = old_env

    def test_env_used_when_no_explicit(self):
        env = self.write_config(minimal_config(port=8999, interval=99), "env.json")
        old_env = os.environ.get("NETMON_CONFIG")
        try:
            os.environ["NETMON_CONFIG"] = env
            settings = S.load_settings(None)
            self.assertEqual(settings.source, env)
            self.assertEqual(settings.port, 8999)
        finally:
            if old_env is None:
                os.environ.pop("NETMON_CONFIG", None)
            else:
                os.environ["NETMON_CONFIG"] = old_env

    def test_missing_explicit_file_raises(self):
        missing = os.path.join(self.tmpdir, "nope.json")
        with self.assertRaises(S.ConfigError) as ctx:
            S.load_settings(missing)
        self.assertIn("не найден", str(ctx.exception))

    def test_bad_json_raises(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{not a json")
        with self.assertRaises(S.ConfigError) as ctx:
            S.load_settings(path)
        self.assertIn("некорректный JSON", str(ctx.exception))


class ConfigValidationTests(TempDirTestCase):
    def assert_invalid(self, config, fragment):
        with self.assertRaises(S.ConfigError) as ctx:
            normalize(config)
        text = str(ctx.exception)
        self.assertIn(fragment, text, "ожидали %r в сообщении: %s" % (fragment, text))

    def test_unknown_kind(self):
        config = minimal_config()
        config["services"][0]["checks"][0]["kind"] = "udp"
        self.assert_invalid(config, "неизвестный тип проверки")

    def test_duplicate_service_ids(self):
        config = minimal_config()
        config["services"].append(dict(config["services"][0]))
        config["services"][1]["checks"] = [dict(config["services"][0]["checks"][0],
                                                id="other_check")]
        self.assert_invalid(config, "уже используется")

    def test_duplicate_check_ids_across_services(self):
        config = minimal_config()
        second = dict(config["services"][0])
        second["id"] = "second"
        second["name"] = "Second"
        second["checks"] = [dict(config["services"][0]["checks"][0])]
        config["services"].append(second)
        self.assert_invalid(config, "уже используется в другом сервисе")

    def test_bad_port(self):
        config = minimal_config()
        config["services"][0]["checks"][0]["port"] = 99999
        self.assert_invalid(config, "больше допустимого максимума")

    def test_bad_expect_status(self):
        config = minimal_config()
        config["services"][0]["checks"][0] = {
            "id": "solo_https", "kind": "https", "host": "example.com",
            "path": "/", "expect": [42], "core": True,
        }
        self.assert_invalid(config, "HTTP-статус 100..599")

    def test_https_requires_path_only(self):
        config = minimal_config()
        config["services"][0]["checks"][0]["expect"] = [200]
        self.assert_invalid(config, "допустим только для kind=https")

    def test_empty_services(self):
        config = minimal_config()
        config["services"] = []
        self.assert_invalid(config, "обязательный непустой список сервисов")

    def test_missing_host(self):
        config = minimal_config()
        del config["services"][0]["checks"][0]["host"]
        self.assert_invalid(config, "host: обязательное")

    def test_bad_interval(self):
        config = minimal_config()
        config["polling"]["interval_sec"] = 0
        self.assert_invalid(config, "меньше допустимого минимума")

    def test_bad_status_color(self):
        config = minimal_config()
        config["status_colors"] = {"ok": "green"}
        self.assert_invalid(config, "цвет вида #rrggbb")

    def test_unknown_status_in_labels(self):
        config = minimal_config()
        config["status_labels"] = {"super": "Супер"}
        self.assert_invalid(config, "неизвестный статус")

    def test_valid_custom_config_normalized(self):
        config = minimal_config(port=8300, interval=12)
        config["status_labels"] = {"ok": "Работает"}
        config["status_colors"] = {"ok": "#00ff00"}
        config["display"] = {"windows_tray": {"mode": "tray", "dot_pt": 20}}
        settings = normalize(config)
        self.assertEqual(settings.port, 8300)
        self.assertEqual(settings.interval, 12)
        self.assertEqual(settings.status_labels["ok"], "Работает")
        self.assertEqual(settings.status_labels["blocked"], "Похоже на блокировку")
        self.assertEqual(settings.status_colors["ok"], "#00ff00")
        self.assertEqual(settings.display["windows_tray"]["dot_pt"], 20)
        self.assertEqual(settings.services[0]["checks"][0]["timeout"], 0.8)
        self.assertEqual(settings.services[0]["hints"], {})


# --------------------------------------------------------------------------
# CLI: коды выхода и применение конфига запущенным сервером
# --------------------------------------------------------------------------

class ServerCliTests(TempDirTestCase):
    def run_server_cli(self, config_path, extra=()):
        command = [PYTHON, "-B", SERVER_PY, "--config", config_path,
                   "--check-config"] + list(extra)
        return subprocess.run(command, cwd=ROOT, env=subprocess_env(),
                              capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)

    def test_bad_json_exit_code_2(self):
        path = os.path.join(self.tmpdir, "bad.json")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("{broken")
        result = self.run_server_cli(path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("Ошибка конфигурации", result.stderr)

    def test_unknown_kind_exit_code_2(self):
        config = minimal_config()
        config["services"][0]["checks"][0]["kind"] = "carrier-pigeon"
        path = self.write_config(config)
        result = self.run_server_cli(path)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn("неизвестный тип проверки", result.stderr)

    def test_check_config_ok(self):
        path = self.write_config(minimal_config(port=8321, interval=7))
        result = self.run_server_cli(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Конфигурация корректна", result.stdout)

    def test_server_applies_custom_config_and_cli_interval(self):
        """Один сервис и interval_sec=7 из конфига; --interval 9 переопределяет."""
        port = free_port()
        path = self.write_config(minimal_config(port=port, interval=7))
        data_dir = os.path.join(self.tmpdir, "data")
        process = subprocess.Popen(
            [PYTHON, "-B", SERVER_PY, "--config", path, "--interval", "9",
             "--data-dir", data_dir],
            cwd=ROOT, env=subprocess_env(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        try:
            wait_for_health(port)
            state = http_json("http://127.0.0.1:%d/api/state" % port, timeout=10.0)
            self.assertEqual(state["poll_interval"], 9)
            self.assertEqual(len(state["services"]), 1)
            self.assertEqual(state["services"][0]["id"], "solo")
            self.assertEqual(state["services"][0]["name"], "Solo")
            self.assertIn("internet", state)
            self.assertIn(state["internet"]["status"],
                          {"ok", "degraded", "down", "unknown"})
            self.assertTrue(os.path.isdir(data_dir))
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


# --------------------------------------------------------------------------
# Трей без GUI
# --------------------------------------------------------------------------

class TrayLogicTests(TempDirTestCase):
    def load(self, config=None):
        path = self.write_config(config or minimal_config(port=free_port()))
        return S.load_settings(path), path

    def test_format_duration(self):
        self.assertEqual(T.format_duration(0), "0 с")
        self.assertEqual(T.format_duration(45), "45 с")
        self.assertEqual(T.format_duration(60), "1 мин")
        self.assertEqual(T.format_duration(754), "12 мин")
        self.assertEqual(T.format_duration(3600 + 300), "1 ч 05 мин")
        self.assertEqual(T.format_duration(86400 + 7200), "1 д 2 ч")
        self.assertEqual(T.format_duration(None), "0 с")

    def test_status_color_and_offline_grey(self):
        settings, _ = self.load()
        self.assertEqual(T.status_color(settings, "ok", True), "#34c759")
        self.assertEqual(T.status_color(settings, "blocked", True), "#ff3b30")
        self.assertEqual(T.status_color(settings, "ok", False), "#8e8e93")
        self.assertEqual(T.status_color(settings, "unknown", True), "#8e8e93")
        self.assertEqual(T.parse_color("#34c759"), (52, 199, 89))

    def test_tooltip_online_and_offline(self):
        settings, _ = self.load()
        state = T.synthetic_state(settings)
        online = T.tooltip_text(settings, state, True)
        self.assertLessEqual(len(online), 120)
        self.assertIn("Мониторинг доступности ·", online)
        self.assertIn("Solo:", online)
        offline = T.tooltip_text(settings, None, False)
        self.assertIn("Сервер недоступен", offline)

    def test_menu_model_online(self):
        settings, _ = self.load()
        state = T.synthetic_state(settings)
        entries = T.menu_model(settings, state, True, checking=False,
                               autostart={"available": True, "enabled": True})
        texts = [entry.get("text", "") for entry in entries]
        self.assertTrue(any(text.startswith("Мониторинг доступности · ")
                            for text in texts), texts)
        self.assertTrue(any("Solo —" in text for text in texts))
        actions = [entry.get("action") for entry in entries]
        self.assertIn("check", actions)
        self.assertIn("dashboard", actions)
        self.assertIn("start_server", actions)
        self.assertIn("autostart", actions)
        self.assertIn("quit", actions)
        autostart = [entry for entry in entries if entry.get("action") == "autostart"]
        self.assertTrue(autostart[0].get("checked"))
        service_entry = [entry for entry in entries if "Solo —" in entry.get("text", "")][0]
        self.assertTrue(service_entry.get("children"))
        child_text = " ".join(child["text"] for child in service_entry["children"])
        self.assertIn("Проверки:", child_text)

    def test_menu_model_offline_disables_server_actions(self):
        settings, _ = self.load()
        entries = T.menu_model(settings, None, False, checking=False,
                               autostart={"available": False, "enabled": False})
        texts = [entry.get("text", "") for entry in entries]
        self.assertIn("Сервер недоступен", texts)
        check_entry = [entry for entry in entries if entry.get("action") == "check"][0]
        self.assertFalse(check_entry["enabled"])
        start_entry = [entry for entry in entries
                       if entry.get("action") == "start_server"][0]
        self.assertTrue(start_entry["enabled"])
        self.assertFalse(any(entry.get("action") == "autostart" for entry in entries))

    def test_autostart_behaviour_on_non_windows(self):
        status = T.autostart_status()
        if os.name == "nt":
            self.assertTrue(status["available"])
            command = T.autostart_command().replace("\\", "/")
            self.assertTrue(command)
            self.assertTrue("entry.py" in command or "NetMonitor" in command,
                            command)
        else:
            self.assertFalse(status["available"])
            with self.assertRaises(OSError):
                T.set_autostart(True)

    def test_synthetic_state_matches_config_services(self):
        settings, _ = self.load()
        state = T.synthetic_state(settings)
        self.assertEqual(len(state["services"]), 1)
        self.assertEqual(state["services"][0]["id"], "solo")
        self.assertIn("summary", state)
        self.assertIn(state["summary"]["overall"], S.SERVICE_STATUSES)


class TraySelftestCliTests(TempDirTestCase):
    def test_tray_selftest_exit_zero(self):
        port = free_port()
        path = self.write_config(minimal_config(port=port))
        result = subprocess.run(
            [PYTHON, "-B", TRAY_PY, "--selftest", "--config", path],
            cwd=ROOT, env=subprocess_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("РЕЗУЛЬТАТ: OK", result.stdout)
        self.assertIn("сервер недоступен", result.stdout.lower())
        self.assertIn("Проверить сейчас", result.stdout)
        self.assertIn("Solo —", result.stdout)

    def test_tray_selftest_invalid_config_exit_2(self):
        config = minimal_config()
        config["services"][0]["checks"][0]["kind"] = "udp"
        path = self.write_config(config)
        result = subprocess.run(
            [PYTHON, "-B", TRAY_PY, "--selftest", "--config", path],
            cwd=ROOT, env=subprocess_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("Ошибка конфигурации", result.stderr)

    def test_entry_selftest_exit_zero(self):
        port = free_port()
        path = self.write_config(minimal_config(port=port))
        result = subprocess.run(
            [PYTHON, "-B", ENTRY_PY, "--selftest", "--config", path],
            cwd=ROOT, env=subprocess_env(), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("самопроверка сборки", result.stdout)
        self.assertIn("static/", result.stdout)
        self.assertIn("РЕЗУЛЬТАТ: OK", result.stdout)


# --------------------------------------------------------------------------
# Сборка: spec и встроенный сервер entry.py
# --------------------------------------------------------------------------

class BuildArtifactTests(unittest.TestCase):
    def test_spec_contains_static_config_and_tray_backend(self):
        with open(SPEC_FILE, "r", encoding="utf-8") as handle:
            spec = handle.read()
        self.assertIn('"static"', spec)
        self.assertIn("config.json", spec)
        self.assertIn("pystray._win32", spec)
        self.assertIn("console=False", spec)

    def test_entry_parses_server_argv(self):
        import entry

        args, unknown = entry.parse_args([
            "--server-only", "--port", "8100", "--interval", "30",
            "--data-dir", "/tmp/x", "--config", "/tmp/c.json"])
        argv = entry._server_argv("/tmp/c.json", args, unknown)
        self.assertIn("--port", argv)
        self.assertIn("8100", argv)
        self.assertIn("--interval", argv)
        self.assertIn("30", argv)
        self.assertIn("/tmp/c.json", argv)

    def test_entry_conflicting_modes(self):
        import entry

        with self.assertRaises(SystemExit):
            entry.parse_args(["--server-only", "--tray-only"])


class EmbeddedServerTests(TempDirTestCase):
    def tearDown(self):
        S.configure(S._build_settings(S._default_config(), "builtin", "test"))
        super().tearDown()

    def test_embedded_server_start_stop(self):
        import entry

        port = free_port()
        path = self.write_config(minimal_config(port=port, interval=7))
        settings = S.load_settings(path)
        S.configure(settings)
        data_dir = os.path.join(self.tmpdir, "data")
        server = entry.EmbeddedServer(settings, data_dir=data_dir)
        try:
            self.assertTrue(server.start())
            health = wait_for_health(port, timeout=15)
            self.assertTrue(health["ok"])
            state = http_json("http://127.0.0.1:%d/api/state" % port)
            self.assertEqual(state["poll_interval"], 7)
            self.assertEqual([svc["id"] for svc in state["services"]], ["solo"])
        finally:
            server.stop()
        # после остановки порт свободен
        sock = socket.socket()
        try:
            sock.settimeout(2)
            self.assertNotEqual(sock.connect_ex(("127.0.0.1", port)), 0)
        finally:
            sock.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
