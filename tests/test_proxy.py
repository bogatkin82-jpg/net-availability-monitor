# -*- coding: utf-8 -*-
"""Тесты режима network.proxy (direct / system / явный HTTP-прокси)."""

import json
import os
import socket
import sys
import tempfile
import threading
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import server as S  # noqa: E402


class FakeConnectProxy(object):
    """Минимальный HTTP CONNECT-прокси: отвечает 200 и держит соединение."""

    def __init__(self, status=200):
        self.status = status
        self.connections = 0
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(8)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve, daemon=True)
        self.thread.start()

    def _serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            self.connections += 1
            try:
                conn.recv(4096)
                reason = "Connection established" if self.status == 200 else "Denied"
                conn.sendall(("HTTP/1.1 %d %s\r\n\r\n" % (
                    self.status, reason)).encode("ascii"))
                # держим туннель открытым, пока клиент не закроет
                while conn.recv(4096):
                    pass
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


def tcp_spec(port=443):
    return {
        "id": "t", "label": "TCP", "kind": "tcp", "host": "127.0.0.1",
        "port": port, "core": True, "timeout": 2,
    }


class ProxyModeTests(unittest.TestCase):
    def setUp(self):
        self._old_mode = S.NETWORK_PROXY
        self._old_cache = dict(S._PROXY_CACHE)

    def tearDown(self):
        S.NETWORK_PROXY = self._old_mode
        S._PROXY_CACHE.update(self._old_cache)

    def test_custom_proxy_used_for_tcp(self):
        proxy = FakeConnectProxy()
        try:
            S.NETWORK_PROXY = "http://127.0.0.1:%d" % proxy.port
            result = S.run_check(tcp_spec())
            self.assertTrue(result["ok"], result)
            self.assertTrue(result["proxy_used"])
            self.assertGreaterEqual(proxy.connections, 1)
        finally:
            proxy.close()

    def test_proxy_denied_is_reported(self):
        proxy = FakeConnectProxy(status=403)
        try:
            S.NETWORK_PROXY = "http://127.0.0.1:%d" % proxy.port
            result = S.run_check(tcp_spec())
            self.assertFalse(result["ok"])
            self.assertEqual(result["error"], "proxy_tunnel_failed")
            self.assertEqual(result["stage"], "tcp")
        finally:
            proxy.close()

    def test_direct_mode_ignores_proxy(self):
        S.NETWORK_PROXY = "direct"
        result = S.run_check(tcp_spec(port=1))
        self.assertFalse(result["ok"])
        self.assertFalse(result["proxy_used"])
        self.assertIn(result["error"], ("connect_refused", "connect_unreachable",
                                        "connect_timeout"))

    def test_system_mode_without_proxy_falls_back_to_direct(self):
        proxy = FakeConnectProxy()
        proxy.close()  # прокси «есть», но недоступен
        S.NETWORK_PROXY = "system"
        old_detect = S.detect_system_proxy
        S.detect_system_proxy = lambda: "http://127.0.0.1:%d" % proxy.port
        S._PROXY_CACHE["until"] = 0.0
        try:
            result = S.run_check(tcp_spec(port=1))
            self.assertFalse(result["ok"])
            self.assertFalse(result["proxy_used"])
            self.assertIn(result["error"], ("connect_refused", "connect_unreachable",
                                            "connect_timeout"))
        finally:
            S.detect_system_proxy = old_detect

    def test_config_accepts_proxy_modes(self):
        base = json.loads(json.dumps(S._default_config()))
        for value in ("direct", "system", "http://127.0.0.1:8080"):
            base["network"] = {"proxy": value}
            settings = S._build_settings(base, "test", "тест")
            self.assertEqual(settings.network_proxy, value)

    def test_config_rejects_unknown_proxy(self):
        base = json.loads(json.dumps(S._default_config()))
        base["network"] = {"proxy": "socks5://127.0.0.1:1080"}
        with self.assertRaises(S.ConfigError):
            S._build_settings(base, "test", "тест")

    def test_config_validation_cli_accepts_network_section(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "config.json")
            config = json.loads(json.dumps(S._default_config()))
            config["network"] = {"proxy": "system"}
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(config, handle)
            settings = S.load_settings(path)
            self.assertEqual(settings.network_proxy, "system")


if __name__ == "__main__":
    unittest.main()
