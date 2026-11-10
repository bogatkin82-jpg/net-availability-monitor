#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Точка входа Windows-сборки NetMonitor (PyInstaller onefile).

Режимы:
    NetMonitor.exe                     сервер в фоновом потоке + значок в трее
    NetMonitor.exe --tray-only         только трей (сервер уже запущен/внешний)
    NetMonitor.exe --server-only       только сервер (как python server.py)
    NetMonitor.exe --selftest          самопроверка без GUI/сети (код 0 — успех)

Особенности сборки:
  * static/ и config.json включены в бандл (sys._MEIPASS);
  * при первом запуске config.json копируется рядом с exe (если каталог
    недоступен для записи — в %LOCALAPPDATA%\\net-availability-monitor);
  * data/state.json хранится рядом с exe (в %LOCALAPPDATA% после install.ps1);
  * в windowed-сборке нет stdout — вывод дублируется в
    %LOCALAPPDATA%\\net-availability-monitor\\logs\\netmon.log.
"""

import argparse
import os
import shutil
import sys
import threading
import time

FROZEN = bool(getattr(sys, "frozen", False))
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)


def app_dir():
    """Каталог приложения: рядом с ним config.json, data/ и логи."""
    if FROZEN:
        return os.path.dirname(os.path.abspath(sys.executable))
    return ROOT


def bundle_dir():
    """Каталог ресурсов бандла (static/, config.json) — sys._MEIPASS."""
    return getattr(sys, "_MEIPASS", ROOT)


def _ensure_import_path():
    """Дать доступ к server.py/tray.py при запуске из исходников."""
    for path in (ROOT, HERE, bundle_dir()):
        if path and path not in sys.path:
            sys.path.insert(0, path)


_ensure_import_path()

import server as _server  # noqa: E402
import tray as _tray  # noqa: E402

LOG_NAME = "netmon.log"
MUTEX_NAME = "Local\\NetMonitorTray"
ERROR_ALREADY_EXISTS = 183


# --------------------------------------------------------------------------
# Вывод и логи (в windowed-сборке stdout/stderr отсутствуют)
# --------------------------------------------------------------------------

def _say(message=""):
    """print, который не падает, если stdout отсутствует (GUI-сборка)."""
    try:
        print(message, flush=True)
    except Exception:  # noqa: BLE001 - вывод не должен ронять приложение
        pass


def log_dir():
    base = os.environ.get("LOCALAPPDATA") or app_dir()
    return os.path.join(base, "net-availability-monitor", "logs")


def log_path():
    return os.path.join(log_dir(), LOG_NAME)


def _rotate_log(path, max_bytes=5 * 1024 * 1024):
    try:
        if os.path.isfile(path) and os.path.getsize(path) > max_bytes:
            backup = path + ".1"
            try:
                os.remove(backup)
            except OSError:
                pass
            os.replace(path, backup)
    except OSError:
        pass


def setup_streams():
    """В GUI-сборке направить stdout/stderr в лог-файл; вернуть путь к логу."""
    if not FROZEN:
        return None
    if sys.stdout is not None and sys.stderr is not None:
        return None
    path = log_path()
    try:
        os.makedirs(log_dir(), exist_ok=True)
        _rotate_log(path)
        stream = open(path, "a", encoding="utf-8", buffering=1)
    except OSError:
        return None
    sys.stdout = stream
    sys.stderr = stream
    return path


# --------------------------------------------------------------------------
# Конфигурация: копия рядом с exe при первом запуске
# --------------------------------------------------------------------------

def ensure_config(explicit_path=None):
    """Вернуть путь к config.json, создав копию из бандла при первом запуске.

    Явный --config не трогаем. В сборке без доступного на запись каталога exe
    конфиг кладётся в %LOCALAPPDATA%\\net-availability-monitor.
    """
    if explicit_path:
        return os.path.abspath(os.path.expanduser(explicit_path))
    if not FROZEN:
        return None  # server.py/tray.py сами найдут config.json рядом с исходниками
    target = os.path.join(app_dir(), _server.CONFIG_FILENAME)
    if os.path.isfile(target):
        return target
    source = os.path.join(bundle_dir(), _server.CONFIG_FILENAME)
    if not os.path.isfile(source):
        _say("[entry] в бандле нет config.json — используются встроенные значения")
        return None
    try:
        os.makedirs(app_dir(), exist_ok=True)
        shutil.copyfile(source, target)
        _say("[entry] создан конфиг при первом запуске: %s" % target)
        return target
    except OSError as exc:
        fallback_dir = os.path.join(os.environ.get("LOCALAPPDATA") or app_dir(),
                                    "net-availability-monitor")
        fallback = os.path.join(fallback_dir, _server.CONFIG_FILENAME)
        try:
            os.makedirs(fallback_dir, exist_ok=True)
            shutil.copyfile(source, fallback)
            os.environ["NETMON_CONFIG"] = fallback
            _say("[entry] каталог exe недоступен (%s); конфиг создан: %s" % (exc, fallback))
            return fallback
        except OSError:
            _say("[entry] не удалось создать config.json: %s" % exc)
            return None


# --------------------------------------------------------------------------
# Встроенный сервер (поток) для режима «сервер + трей»
# --------------------------------------------------------------------------

def _health_ok(host, port, timeout=1.5):
    return _tray.ApiClient(host, port, timeout=timeout).health(timeout=timeout) is not None


class EmbeddedServer(object):
    """Monitor + ThreadingHTTPServer в фоновом потоке текущего процесса."""

    def __init__(self, settings, data_dir=None):
        self.settings = settings
        self.host = settings.host
        self.port = settings.port
        self.data_dir = data_dir or os.path.join(app_dir(), "data")
        self.monitor = None
        self.httpd = None
        self.thread = None
        self._lock = threading.Lock()

    def start(self):
        """Поднять сервер. Возвращает True, если он запущен этим вызовом."""
        with self._lock:
            if self.httpd is not None:
                return True
            if _health_ok(self.host, self.port):
                _say("[entry] сервер уже слушает %s:%s — встроенный не нужен" % (
                    self.host, self.port))
                return False
            monitor = _server.Monitor(interval=self.settings.interval,
                                      data_dir=self.data_dir)
            try:
                httpd = _server.make_http_server(self.host, self.port, monitor)
            except OSError as exc:
                _say("[entry] не удалось занять %s:%s — %s" % (self.host, self.port, exc))
                raise
            monitor.start()
            thread = threading.Thread(
                target=httpd.serve_forever,
                kwargs={"poll_interval": 0.5},
                name="netmon-http",
                daemon=True,
            )
            thread.start()
            self.monitor = monitor
            self.httpd = httpd
            self.thread = thread
            _say("[entry] сервер запущен: http://%s:%d/" % (self.host, self.port))
            return True

    def stop(self):
        """Остановить сервер и сохранить состояние (идемпотентно)."""
        with self._lock:
            monitor, httpd, thread = self.monitor, self.httpd, self.thread
            self.monitor = self.httpd = self.thread = None
        if monitor is not None:
            try:
                monitor.stop()
            except Exception:  # noqa: BLE001
                pass
        if httpd is not None:
            try:
                httpd.shutdown()
            except Exception:  # noqa: BLE001
                pass
            try:
                httpd.server_close()
            except Exception:  # noqa: BLE001
                pass
        if thread is not None:
            thread.join(timeout=3)
        if monitor is not None:
            _say("[entry] сервер остановлен")


# --------------------------------------------------------------------------
# Однократный запуск трея (Windows named mutex)
# --------------------------------------------------------------------------

def _acquire_single_instance():
    """Вернуть (handle, already_running); на не-Windows — (None, False)."""
    if os.name != "nt" or not FROZEN:
        return None, False
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, ctypes.c_bool,
                                          ctypes.c_wchar_p]
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
        if not handle:
            return None, False
        return handle, ctypes.get_last_error() == ERROR_ALREADY_EXISTS
    except Exception:  # noqa: BLE001 - mutex не критичен
        return None, False


def _release_single_instance(handle):
    if not handle:
        return
    try:
        import ctypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CloseHandle(handle)
    except Exception:  # noqa: BLE001
        pass


# --------------------------------------------------------------------------
# Режимы запуска
# --------------------------------------------------------------------------

def _server_argv(config_path, args, unknown):
    """Собрать argv для server.main из аргументов entry.py."""
    out = []
    if config_path:
        out += ["--config", config_path]
    if args.host is not None:
        out += ["--host", args.host]
    if args.port is not None:
        out += ["--port", str(args.port)]
    if args.interval is not None:
        out += ["--interval", str(args.interval)]
    if args.data_dir is not None:
        out += ["--data-dir", args.data_dir]
    out += list(unknown or [])
    return out


def run_server_only(config_path, args, unknown):
    """Только сервер: вызываем server.main с проброшенными аргументами."""
    _say("[entry] режим: только сервер")
    return _server.main(_server_argv(config_path, args, unknown))


def run_tray_only(settings, config_path):
    _say("[entry] режим: только трей")
    return _tray.run_tray(settings, config_source=config_path)


def run_default(settings, config_path, args):
    """Основной режим: сервер в потоке + трей."""
    handle, already_running = _acquire_single_instance()
    if already_running:
        url = _tray.ApiClient(settings.host, settings.port).dashboard_url
        _say("[entry] NetMonitor уже запущен — открываю %s" % url)
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:  # noqa: BLE001
            pass
        _release_single_instance(handle)
        return 0

    data_dir = args.data_dir or os.path.join(app_dir(), "data")
    embedded = EmbeddedServer(settings, data_dir=data_dir)
    try:
        embedded.start()
    except OSError:
        # Порт занят чужим процессом — трей всё равно покажем (сервер выключен).
        pass
    _say("[entry] режим: сервер + трей; данные: %s" % data_dir)

    def start_server():
        embedded.start()

    def on_quit():
        embedded.stop()

    try:
        return _tray.run_tray(settings, config_source=config_path,
                              start_server=start_server, on_quit=on_quit)
    finally:
        embedded.stop()
        _release_single_instance(handle)


def run_selftest(settings, config_path, args, unknown, current_log):
    """Самопроверка сборки без GUI и без обращения к сети.

    Проверяет: загрузку конфига, наличие static/, модель меню/tooltip/иконки.
    Возвращает 0 при успехе; 1 — если в бандле нет static/.
    """
    static_path = os.path.join(bundle_dir(), "static")
    static_ok = os.path.isdir(static_path)
    extra = [
        "NetMonitor %s — самопроверка сборки" % _server.VERSION,
        "сборка: %s" % ("PyInstaller onefile" if FROZEN else "запуск из исходников"),
        "python: %s" % sys.version.split()[0],
        "каталог приложения: %s" % app_dir(),
        "каталог бандла: %s" % bundle_dir(),
        "static/: %s" % (static_path if static_ok else "НЕ НАЙДЕН (%s)" % static_path),
        "config.json: %s" % (config_path or settings.source),
        "лог: %s" % (current_log or "stdout"),
    ]
    code = _tray.run_selftest(settings, config_source=config_path, extra_lines=extra)
    if not static_ok:
        _say("ОШИБКА: каталог static/ не попал в сборку — проверьте windows/netmon.spec")
        return 1
    return code


# --------------------------------------------------------------------------
# Точка входа
# --------------------------------------------------------------------------

def parse_args(argv):
    parser = argparse.ArgumentParser(
        description="NetMonitor для Windows: локальный сервер мониторинга + трей",
        epilog="Без флагов запускаются оба компонента (сервер в потоке и трей).")
    parser.add_argument("--selftest", action="store_true",
                        help="самопроверка без GUI и сети, затем выход")
    parser.add_argument("--server-only", action="store_true",
                        help="запустить только сервер (без трея)")
    parser.add_argument("--tray-only", action="store_true",
                        help="запустить только трей (сервер должен работать отдельно)")
    parser.add_argument("--config", default=None, help="путь к config.json")
    parser.add_argument("--host", default=None,
                        help="адрес сервера (по умолчанию из config.json)")
    parser.add_argument("--port", type=int, default=None,
                        help="порт сервера (по умолчанию из config.json)")
    parser.add_argument("--interval", type=int, default=None,
                        help="интервал опроса, секунды (по умолчанию из config.json)")
    parser.add_argument("--data-dir", default=None,
                        help="каталог для state.json (по умолчанию ./data рядом с exe)")
    args, unknown = parser.parse_known_args(argv)
    if args.server_only and args.tray_only:
        parser.error("--server-only и --tray-only взаимоисключающие")
    return args, unknown


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    args, unknown = parse_args(argv)
    current_log = setup_streams()
    config_path = ensure_config(args.config)

    try:
        settings = _server.load_settings(config_path)
    except _server.ConfigError as exc:
        _say(_server.format_config_error(exc))
        return 2

    # CLI-флаги entry.py переопределяют конфиг так же, как у server.py.
    if args.host is not None:
        settings.host = args.host
    if args.port is not None:
        settings.port = args.port
    if args.interval is not None:
        settings.interval = args.interval
    _server.configure(settings)

    if args.selftest:
        return run_selftest(settings, config_path, args, unknown, current_log)
    if args.server_only:
        return run_server_only(config_path, args, unknown)
    if args.tray_only:
        return run_tray_only(settings, config_path)
    return run_default(settings, config_path, args)


if __name__ == "__main__":
    sys.exit(main())
