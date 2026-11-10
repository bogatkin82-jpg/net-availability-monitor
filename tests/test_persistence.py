#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Живой тест персистентности net-monitor: поднимает СВОЙ сервер на свободном
порту с временным --data-dir, накапливает историю и инциденты, останавливает
его SIGTERM и проверяет, что после повторного запуска series/инциденты на месте.

Никакая сеть, кроме локальных проверок самого монитора, не используется;
чужие серверы (8099/8788/8789/8790) не трогаются — только свой порт и /tmp.

Запуск:
    python3 tests/test_persistence.py [--keep]

Коды выхода: 0 — OK, 1 — FAIL.
"""

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVER = os.path.join(PROJECT_DIR, "server.py")
RESULTS = []
SKIPS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("OK  " if cond else "FAIL", name,
                       ("  [%s]" % detail) if (detail and not cond) else ""))


def skip(name, reason):
    SKIPS.append((name, reason))
    print("SKIP %s (%s)" % (name, reason))


def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def http_json(base, path, method="GET", timeout=120):
    req = urllib.request.Request(base + path, method=method)
    if method == "POST":
        req.data = b""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def start_server(port, data_dir):
    log_path = os.path.join(data_dir, "server-%d.log" % port)
    log = open(log_path, "wb")
    proc = subprocess.Popen(
        [sys.executable, "-B", SERVER, "--port", str(port), "--interval", "15",
         "--data-dir", data_dir],
        stdout=log, stderr=subprocess.STDOUT)
    return proc, log_path


def wait_ready(port, timeout=25):
    base = "http://127.0.0.1:%d" % port
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            health = http_json(base, "/api/health", timeout=3)
            return base, health
        except Exception:  # noqa: BLE001
            time.sleep(0.15)
    raise RuntimeError("сервер не поднялся на %d" % port)


def wait_round(base, prev_rounds, timeout=40):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if http_json(base, "/api/health")["rounds"] > prev_rounds:
            return True
        time.sleep(0.25)
    return False


def stop_server(proc):
    if proc.poll() is not None:
        return proc.returncode
    proc.send_signal(signal.SIGTERM)
    try:
        return proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=10)
        return -9


def snapshot_counts(base):
    state = http_json(base, "/api/state")
    health = http_json(base, "/api/health")
    incidents = http_json(base, "/api/incidents?limit=500")
    return {
        "series": {s["id"]: len(s["series"]) for s in state["services"]},
        "statuses": {s["id"]: s["status"] for s in state["services"]},
        "incidents_total": incidents["total"],
        "active_ids": sorted(i["id"] for i in incidents["incidents"]
                             if i["status"] == "active"),
        "active_durations": {i["id"]: i["duration_sec"]
                             for i in incidents["incidents"] if i["status"] == "active"},
        "version": state["version"],
        "rounds": health["rounds"],
        "state": state,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description="Живой тест персистентности net-monitor")
    parser.add_argument("--keep", action="store_true", help="не удалять временный каталог")
    args = parser.parse_args(argv)

    tmp = tempfile.mkdtemp(prefix="netmon-qa-persist-live-")
    port = free_port()
    print("=" * 72)
    print("net-monitor: живой тест персистентности (порт %d, данные %s)" % (port, tmp))
    print("=" * 72)
    proc = None
    proc2 = None
    try:
        # --- первый запуск -------------------------------------------------
        proc, log1 = start_server(port, tmp)
        base, health = wait_ready(port)
        check("сервер №1 поднялся", health["ok"] is True)
        # health отвечает ещё до первого цикла (rounds=0), поэтому ждём именно
        # завершения первого цикла, а не просто ответа API.
        check("дождались первого цикла", wait_round(base, 0, timeout=60),
              "rounds=%r" % http_json(base, "/api/health")["rounds"])

        # второй цикл: гарантированно открываем инциденты (реальная сеть:
        # YouTube/Telegram блокируются, интернет работает)
        resp = http_json(base, "/api/check", method="POST", timeout=120)
        check("POST /api/check: ok", resp.get("ok") is True, repr(resp.get("ok")))
        before = snapshot_counts(base)
        print("до остановки: series=%r, инцидентов=%d, активных=%d, version=%d, rounds=%d"
              % (before["series"], before["incidents_total"],
                 len(before["active_ids"]), before["version"], before["rounds"]))
        check("до остановки есть инциденты", before["incidents_total"] >= 1,
              before["incidents_total"])
        check("до остановки есть активные инциденты", len(before["active_ids"]) >= 1,
              before["active_ids"])

        state_file = os.path.join(tmp, "state.json")
        check("state.json создан на диске", os.path.isfile(state_file))
        # последний цикл пишет state.json; ждём следующий цикл, чтобы гарантировать запись
        # (POST уже сохранил состояние — _do_round вызывает _save())

        # --- SIGTERM и проверка файла -------------------------------------
        code = stop_server(proc)
        check("SIGTERM: процесс завершился", proc.poll() is not None, code)
        proc = None
        check("state.json валидный JSON после SIGTERM", _valid_json(state_file))
        on_disk = _read_json(state_file)
        check("state.json содержит series", isinstance(on_disk.get("series"), dict))
        check("state.json содержит incidents", isinstance(on_disk.get("incidents"), list))
        check("на диске столько же series, сколько отдавал API",
              {k: len(v) for k, v in on_disk.get("series", {}).items()}
              == {k: v for k, v in before["series"].items()},
              "%r vs %r" % ({k: len(v) for k, v in on_disk.get("series", {}).items()},
                            before["series"]))
        on_disk_incidents = on_disk.get("incidents") or []
        check("на диске столько же инцидентов",
              len(on_disk_incidents) == before["incidents_total"],
              "%d vs %d" % (len(on_disk_incidents), before["incidents_total"]))
        check("на диске активные инциденты сохранены как active",
              len([i for i in on_disk_incidents if i.get("status") == "active"])
              == len(before["active_ids"]),
              "%d vs %d" % (len([i for i in on_disk_incidents
                                 if i.get("status") == "active"]),
                            len(before["active_ids"])))

        # --- повторный запуск с тем же --data-dir --------------------------
        proc2, log2 = start_server(port, tmp)
        base2, health2 = wait_ready(port)
        check("сервер №2 поднялся с тем же --data-dir", health2["ok"] is True)
        print("после рестарта health: version=%d rounds=%d"
              % (health2["version"], health2["rounds"]))
        check("version сохранён (health.version >= до остановки)",
              health2["version"] >= before["version"],
              "%r vs %r" % (health2["version"], before["version"]))
        check("rounds сохранены (health.rounds >= до остановки)",
              health2["rounds"] >= before["rounds"],
              "%r vs %r" % (health2["rounds"], before["rounds"]))

        after = snapshot_counts(base2)
        check("series сохранились после рестарта",
              after["series"] == before["series"],
              "%r vs %r" % (after["series"], before["series"]))
        check("инциденты сохранились после рестарта",
              after["incidents_total"] == before["incidents_total"],
              "%d vs %d" % (after["incidents_total"], before["incidents_total"]))
        check("активные инциденты остались active после рестарта",
              after["active_ids"] == before["active_ids"],
              "%r vs %r" % (after["active_ids"], before["active_ids"]))
        if after["active_ids"]:
            grew = all(after["active_durations"].get(i, 0) >= before["active_durations"].get(i, 0)
                       for i in after["active_ids"])
            check("длительность активных не уменьшилась", grew,
                  "%r vs %r" % (after["active_durations"], before["active_durations"]))
        check("после рестарта статусы сервисов восстановлены (не unknown)",
              all(v != "unknown" for v in after["statuses"].values()),
              after["statuses"])

        # --- новый цикл дописывает series --------------------------------
        rounds_now = http_json(base2, "/api/health")["rounds"]
        check("после рестарта пошёл новый цикл", wait_round(base2, rounds_now, timeout=45))
        after2 = snapshot_counts(base2)
        check("новый цикл дописал series (+1 точка)",
              all(after2["series"][sid] == after["series"][sid] + 1
                  for sid in after2["series"]),
              "%r -> %r" % (after["series"], after2["series"]))
        check("активные инциденты не задвоились новым циклом",
              sorted(after2["active_ids"]) == sorted(after["active_ids"]),
              "%r vs %r" % (after2["active_ids"], after["active_ids"]))

        # --- лог ----------------------------------------------------------
        for name, path in (("№1", log1), ("№2", log2)):
            text = open(path, "r", encoding="utf-8", errors="replace").read()
            check("лог сервера %s без traceback" % name, "Traceback" not in text)
            check("лог сервера %s без [error]" % name, "[error]" not in text)
        print("\nлоги: %s , %s" % (log1, log2))
    except Exception:  # noqa: BLE001
        print("\n[CRASH]")
        traceback.print_exc()
        RESULTS.append(("исключение в тесте персистентности", False, ""))
    finally:
        for p in (proc, proc2):
            if p is not None:
                stop_server(p)
        if args.keep:
            print("временный каталог сохранён: %s" % tmp)
        else:
            shutil.rmtree(tmp, ignore_errors=True)

    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print("Проверок: %d, OK: %d, FAIL: %d, SKIP: %d"
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed), len(SKIPS)))
    if failed:
        print("Упали: %s" % ", ".join(failed))
        print("РЕЗУЛЬТАТ: FAIL")
        return 1
    print("РЕЗУЛЬТАТ: OK")
    return 0


def _valid_json(path):
    try:
        _read_json(path)
        return True
    except Exception:  # noqa: BLE001
        return False


def _read_json(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


if __name__ == "__main__":
    sys.exit(main())
