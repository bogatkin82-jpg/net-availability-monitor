#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Юнит-тесты движка инцидентов, классификации и сериализации net-monitor.

Сеть НЕ используется: все результаты проверок синтезируются, Monitor
инстанцируется с временным --data-dir. Так тесты детерминированы и быстры.

Запуск:
    python3 tests/test_incidents.py            # из каталога net-monitor/
    python3 net-monitor/tests/test_incidents.py  # из любого места

Коды выхода: 0 — все тесты прошли, 1 — есть упавшие тесты.
Внешних зависимостей нет (только stdlib + импорт ../server.py).
"""

import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(HERE)
sys.path.insert(0, PROJECT_DIR)

import server as S  # noqa: E402

ISO_RE_SECONDS = "%Y-%m-%dT%H:%M:%S"

_results = []  # (name, ok, detail)


def check(name, cond, detail=""):
    _results.append((name, bool(cond), detail))
    if not cond and detail:
        print("   ↳ %s" % detail)


# --------------------------------------------------------------------------
# Синтетические результаты проверок
# --------------------------------------------------------------------------

def build_checks(sid, default_ok=True, error=None, latency=100, stage=None,
                 overrides=None):
    """Собрать список check-результатов для сервиса sid по его спекам."""
    service = S.SERVICE_BY_ID[sid]
    out = []
    overrides = overrides or {}
    for spec in service["checks"]:
        conf = overrides.get(spec["id"]) or {}
        ok = conf.get("ok", default_ok)
        item = {
            "id": spec["id"],
            "label": spec["label"],
            "target": conf.get("target") or spec.get("host") or "",
            "kind": spec["kind"],
            "core": spec.get("core", False),
            "ok": ok,
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


def by_service(youtube, telegram, internet):
    return {"youtube": youtube, "telegram": telegram, "internet": internet}


def all_ok(latency=100):
    return by_service(build_checks("youtube", latency=latency),
                      build_checks("telegram", latency=latency),
                      build_checks("internet", latency=latency))


def all_bad(error="connect_timeout", stage="tcp", latency=None):
    return by_service(build_checks("youtube", default_ok=False, error=error, stage=stage),
                      build_checks("telegram", default_ok=False, error=error, stage=stage),
                      build_checks("internet", default_ok=False, error=error, stage=stage))


def new_monitor():
    tmp = tempfile.mkdtemp(prefix="netmon-qa-unit-")
    mon = S.Monitor(interval=15, data_dir=tmp)
    return mon, tmp


# --------------------------------------------------------------------------
# 1. Классификация (_classify)
# --------------------------------------------------------------------------

def test_classify_basic():
    print("\n[1] _classify: базовые сценарии")
    mon, tmp = new_monitor()
    try:
        yt = S.SERVICE_BY_ID["youtube"]
        tg = S.SERVICE_BY_ID["telegram"]
        inet = S.SERVICE_BY_ID["internet"]

        # все проверки ок
        info = mon._classify(yt, build_checks("youtube"), "ok")
        check("all_ok -> ok", info["status"] == "ok", "got %r" % info["status"])
        check("all_ok -> latency есть", isinstance(info["latency_ms"], int),
              "latency=%r" % info["latency_ms"])
        check("all_ok -> checks_ok == checks_total",
              info["checks_ok"] == info["checks_total"] == 5,
              "%r/%r" % (info["checks_ok"], info["checks_total"]))

        # частично: 1 core ок, 1 core мёртв
        partial = build_checks("youtube", overrides={
            "yt_img": {"ok": False, "error": "tls_handshake_timeout", "stage": "tls"},
        })
        info = mon._classify(yt, partial, "ok")
        check("partial -> degraded", info["status"] == "degraded",
              "got %r (%s)" % (info["status"], info["status_label"]))

        # TLS-таймаут при живом интернете -> blocked
        tls = build_checks("youtube", default_ok=False,
                           error="tls_handshake_timeout", stage="tls")
        info = mon._classify(yt, tls, "ok")
        check("tls_handshake_timeout -> blocked", info["status"] == "blocked",
              "got %r" % info["status"])
        check("blocked: латентности нет", info["latency_ms"] is None,
              "latency=%r" % info["latency_ms"])
        check("blocked: status_label «Похоже на блокировку»",
              info["status_label"] == "Похоже на блокировку", info["status_label"])
        check("blocked: есть hint", bool(info["hint"]), repr(info["hint"]))

        # DNS-ошибка -> blocked
        dns = build_checks("telegram", default_ok=False, error="dns_error", stage="dns")
        info = mon._classify(tg, dns, "ok")
        check("dns_error -> blocked", info["status"] == "blocked", "got %r" % info["status"])

        # connect_refused -> down
        refused = build_checks("telegram", default_ok=False,
                               error="connect_refused", stage="tcp")
        info = mon._classify(tg, refused, "ok")
        check("connect_refused -> down", info["status"] == "down",
              "got %r" % info["status"])

        # неожиданный HTTP-статус -> down
        http_bad = build_checks("youtube", default_ok=False,
                                error="http_status_unexpected", stage="http")
        info = mon._classify(yt, http_bad, "ok")
        check("http_status_unexpected -> down", info["status"] == "down",
              "got %r" % info["status"])

        # read_timeout / tls_reset — тоже blocked
        for err, stage in (("read_timeout", "http"), ("tls_reset", "tls"),
                           ("connect_unreachable", "tcp")):
            info = mon._classify(yt, build_checks("youtube", default_ok=False,
                                                 error=err, stage=stage), "ok")
            check("%s -> blocked" % err, info["status"] == "blocked",
                  "got %r" % info["status"])

        # медленный, но успешный отклик -> degraded
        slow = build_checks("youtube", latency=7000)
        info = mon._classify(yt, slow, "ok")
        check("ok 7000ms -> degraded", info["status"] == "degraded",
              "got %r" % info["status"])
        check("ok 7000ms -> label «Нестабильно»", info["status_label"] == "Нестабильно",
              info["status_label"])

        # пустой набор проверок -> unknown
        info = mon._classify(yt, [], "ok")
        check("empty checks -> unknown", info["status"] == "unknown",
              "got %r" % info["status"])
        check("unknown: checks_total == 0", info["checks_total"] == 0,
              "%r" % info["checks_total"])

        # контрольный канал: свои ярлыки
        info = mon._classify(inet, build_checks("internet"), "ok")
        check("internet ok -> label «Интернет работает»",
              info["status_label"] == "Интернет работает", info["status_label"])
        info = mon._classify(inet, build_checks("internet", latency=7000), "ok")
        check("internet slow -> degraded + «Интернет нестабилен»",
              info["status"] == "degraded" and info["status_label"] == "Интернет нестабилен",
              "%r / %r" % (info["status"], info["status_label"]))
        partial_inet = build_checks("internet", overrides={
            "inet_cloudflare": {"ok": False, "error": "read_timeout", "stage": "http"},
        })
        info = mon._classify(inet, partial_inet, "ok")
        check("internet partial -> degraded", info["status"] == "degraded",
              "got %r" % info["status"])
        bad_inet = build_checks("internet", default_ok=False, error="connect_timeout")
        info = mon._classify(inet, bad_inet, "ok")
        check("internet all bad -> down", info["status"] == "down",
              "got %r" % info["status"])
        check("internet down -> label «Нет интернета»",
              info["status_label"] == "Нет интернета", info["status_label"])

        # нет интернета -> у сервисов down (а не blocked)
        yt_bad = build_checks("youtube", default_ok=False,
                              error="tls_handshake_timeout", stage="tls")
        info = mon._classify(yt, yt_bad, "down")
        check("internet down -> youtube down", info["status"] == "down",
              "got %r" % info["status"])
        check("internet down -> причина про интернет",
              "интернет" in info["status_reason"].lower(), info["status_reason"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 2. Жизненный цикл инцидента
# --------------------------------------------------------------------------

def test_incident_lifecycle():
    print("\n[2] Инциденты: открытие/закрытие/длительность")
    mon, tmp = new_monitor()
    try:
        yt_bad = build_checks("youtube", default_ok=False,
                              error="tls_handshake_timeout", stage="tls")
        tg_bad = build_checks("telegram", default_ok=False,
                              error="connect_timeout", stage="tcp")

        # 1 неудачный цикл -> инцидента нет
        mon._apply_round(by_service(yt_bad, tg_bad, build_checks("internet")))
        check("после 1 неудачного цикла инцидентов нет",
              len(mon.incidents) == 0, "incidents=%d" % len(mon.incidents))

        # 2-й подряд -> инцидент открыт
        time.sleep(1.05)
        mon._apply_round(by_service(yt_bad, tg_bad, build_checks("internet")))
        active_yt = [i for i in mon.incidents
                     if i["service_id"] == "youtube" and i["status"] == "active"]
        active_tg = [i for i in mon.incidents
                     if i["service_id"] == "telegram" and i["status"] == "active"]
        check("после 2 неудачных циклов youtube-инцидент открыт",
              len(active_yt) == 1, "n=%d" % len(active_yt))
        check("после 2 неудачных циклов telegram-инцидент открыт",
              len(active_tg) == 1, "n=%d" % len(active_tg))
        if active_yt:
            inc = active_yt[0]
            check("инцидент: status active", inc["status"] == "active")
            check("инцидент: ended_at is null", inc["ended_at"] is None)
            check("инцидент: started_epoch число", isinstance(inc["started_epoch"], float),
                  type(inc["started_epoch"]).__name__)
            check("инцидент: started_at ISO", _iso_ok(inc["started_at"]), inc["started_at"])
            check("инцидент: title содержит YouTube",
                  "YouTube" in inc["title"], inc["title"])
            check("инцидент: reason непустой", bool(inc["reason"]))
            check("инцидент: failed/total честные (2/2 core)",
                  (inc["failed_checks"], inc["total_checks"]) == (2, 2),
                  "%r/%r" % (inc["failed_checks"], inc["total_checks"]))
            check("инцидент: service_status=blocked",
                  inc["service_status"] == "blocked", inc["service_status"])
            check("инцидент: id 12 hex", len(inc["id"]) == 12, inc["id"])

            # длительность растёт
            snap1 = _active_incident(mon, "youtube")
            d1 = snap1["duration_sec"]
            time.sleep(1.15)
            snap2 = _active_incident(mon, "youtube")
            check("duration_sec растёт у активного",
                  snap2["duration_sec"] > d1, "%r -> %r" % (d1, snap2["duration_sec"]))
            ser = mon._serialize_incident(inc, time.time() + 60)
            check("_serialize_incident: duration считается от now",
                  ser["duration_sec"] >= inc["duration_sec"] + 60,
                  "%r vs %r" % (ser["duration_sec"], inc["duration_sec"]))

        # успешный цикл -> resolved
        mon._apply_round(all_ok())
        yt_inc = [i for i in mon.incidents if i["service_id"] == "youtube"][0]
        check("после успешного цикла инцидент resolved",
              yt_inc["status"] == "resolved", yt_inc["status"])
        check("resolved: ended_at заполнен", yt_inc["ended_at"] is not None)
        check("resolved: ended_at ISO", _iso_ok(yt_inc["ended_at"]), yt_inc["ended_at"])
        check("resolved: ended_epoch число", isinstance(yt_inc["ended_epoch"], float))
        check("resolved: duration_sec >= 1", yt_inc["duration_sec"] >= 1,
              "%r" % yt_inc["duration_sec"])
        check("resolved: duration + started == ended",
              abs((yt_inc["started_epoch"] + yt_inc["duration_sec"]) - yt_inc["ended_epoch"]) <= 1.0)
        check("resolved: активных инцидентов не осталось",
              not [i for i in mon.incidents if i["status"] == "active"])

        # ещё один успешный цикл не открывает новый инцидент
        n_before = len(mon.incidents)
        mon._apply_round(all_ok())
        check("успех не открывает новые инциденты", len(mon.incidents) == n_before,
              "%d -> %d" % (n_before, len(mon.incidents)))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_incident_streak_reset():
    print("\n[3] Инциденты: сброс серии и degraded")
    mon, tmp = new_monitor()
    try:
        yt_bad = build_checks("youtube", default_ok=False,
                              error="tls_handshake_timeout", stage="tls")
        good_inet = build_checks("internet")
        ok_yt = build_checks("youtube")
        tg = build_checks("telegram")

        # fail, ok, fail -> серия сброшена, инцидента нет
        mon._apply_round(by_service(yt_bad, tg, good_inet))
        mon._apply_round(by_service(ok_yt, tg, good_inet))
        mon._apply_round(by_service(yt_bad, tg, good_inet))
        check("серия сброшена успехом: после fail/ok/fail инцидента нет",
              not mon.incidents, "incidents=%d" % len(mon.incidents))
        # второй fail подряд -> открыт
        mon._apply_round(by_service(yt_bad, tg, good_inet))
        check("после fail/ok/fail/fail инцидент открыт",
              len([i for i in mon.incidents if i["status"] == "active"]) == 1,
              "n=%d" % len(mon.incidents))

        # degraded тоже открывает инцидент после 2 циклов
        mon2, tmp2 = new_monitor()
        try:
            partial = build_checks("youtube", overrides={
                "yt_img": {"ok": False, "error": "tls_handshake_timeout", "stage": "tls"},
            })
            mon2._apply_round(by_service(partial, build_checks("telegram"),
                                         build_checks("internet")))
            mon2._apply_round(by_service(partial, build_checks("telegram"),
                                         build_checks("internet")))
            inc = [i for i in mon2.incidents if i["service_id"] == "youtube"]
            check("degraded открывает инцидент", len(inc) == 1, "n=%d" % len(inc))
            check("degraded: title «нестабильная работа»",
                  inc and "нестабильная работа" in inc[0]["title"], inc and inc[0]["title"])
            check("degraded: service_status=degraded",
                  inc and inc[0]["service_status"] == "degraded")
        finally:
            shutil.rmtree(tmp2, ignore_errors=True)

        # unknown не открывает и не закрывает инцидент
        mon3, tmp3 = new_monitor()
        try:
            mon3._apply_round(by_service(yt_bad, tg, good_inet))
            empty = []
            mon3._apply_round(by_service(empty, [], []))
            check("unknown (пустые проверки) не создаёт инцидент",
                  not mon3.incidents, "incidents=%d" % len(mon3.incidents))
            mon3._apply_round(by_service(yt_bad, tg, good_inet))
            mon3._apply_round(by_service(empty, [], []))
            active = [i for i in mon3.incidents if i["status"] == "active"]
            check("unknown не закрывает активный инцидент", len(active) == 1,
                  "active=%d" % len(active))
        finally:
            shutil.rmtree(tmp3, ignore_errors=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 4. incidents_report: фильтры, поиск, пагинация
# --------------------------------------------------------------------------

def _synthetic_incidents(now):
    def inc(i, sid, name, status, started_ago_h, title=None, reason=None):
        started = now - started_ago_h * 3600
        resolved = status == "resolved"
        return {
            "id": "inc%02d" % i,
            "service_id": sid,
            "service_name": name,
            "title": title or "%s: похоже на блокировку" % name,
            "reason": reason or "TLS-рукопожатие не завершилось (таймаут) — www.example.com",
            "status": status,
            "started_at": S.iso_from_epoch(started),
            "started_epoch": started,
            "ended_at": S.iso_from_epoch(started + 600) if resolved else None,
            "ended_epoch": started + 600 if resolved else None,
            "duration_sec": 600 if resolved else 0,
            "failed_checks": 2,
            "total_checks": 2,
            "service_status": "blocked",
        }

    return [
        inc(0, "youtube", "YouTube", "resolved", 10),
        inc(1, "youtube", "YouTube", "active", 2),
        inc(2, "telegram", "Telegram", "resolved", 8,
            reason="TCP-соединение не устанавливается (таймаут 8 с)"),
        inc(3, "telegram", "Telegram", "active", 0.5,
            reason="TCP-соединение не устанавливается (таймаут 8 с)"),
        inc(4, "internet", "Интернет", "resolved", 5,
            reason="Базовые проверки не проходят: DNS не разрешает адрес"),
        inc(5, "youtube", "YouTube", "resolved", 30,
            title="YouTube: нестабильная работа", reason="Сервис отвечает медленно: 7000 мс"),
    ]


def test_incidents_report():
    print("\n[4] incidents_report: поиск, фильтры, пагинация")
    mon, tmp = new_monitor()
    try:
        now = time.time()
        mon.incidents = _synthetic_incidents(now)

        rep = mon.incidents_report()
        check("без фильтров: total=6", rep["total"] == 6, "total=%r" % rep["total"])
        check("по умолчанию limit=100 offset=0",
              (rep["limit"], rep["offset"]) == (100, 0))
        check("сортировка новые сверху",
              [i["started_epoch"] for i in rep["incidents"]]
              == sorted([i["started_epoch"] for i in rep["incidents"]], reverse=True))

        # q: регистронезависимость, кириллица/латиница
        check("q=YOUTUBE (upper latin) находит youtube",
              mon.incidents_report(query="YOUTUBE")["total"] == 3,
              "total=%r" % mon.incidents_report(query="YOUTUBE")["total"])
        check("q=youtube (lower latin) == верхний регистр",
              mon.incidents_report(query="youtube")["total"] == 3)
        check("q=таймаут (lower cyrillic) находит таймауты",
              mon.incidents_report(query="таймаут")["total"] == 4,
              "total=%r" % mon.incidents_report(query="таймаут")["total"])
        check("q=ТАЙМАУТ (upper cyrillic) == нижний регистр",
              mon.incidents_report(query="ТАЙМАУТ")["total"] == 4,
              "total=%r" % mon.incidents_report(query="ТАЙМАУТ")["total"])
        check("q с пробелами по краям обрезается",
              mon.incidents_report(query="  таймаут  ")["total"] == 4)
        check("q=нестабильная находит по title",
              mon.incidents_report(query="нестабильная")["total"] == 1)
        check("q по reason DNS",
              mon.incidents_report(query="DNS")["total"] == 1)
        check("q=щщщ -> total=0, incidents=[]",
              mon.incidents_report(query="щщщ")["total"] == 0
              and mon.incidents_report(query="щщщ")["incidents"] == [])
        check("пустой q == без q", mon.incidents_report(query="")["total"] == 6)

        # service / status
        check("service=youtube -> 3",
              mon.incidents_report(service="youtube")["total"] == 3)
        check("service=telegram -> 2",
              mon.incidents_report(service="telegram")["total"] == 2)
        check("service=internet -> 1",
              mon.incidents_report(service="internet")["total"] == 1)
        check("service=all == все", mon.incidents_report(service="all")["total"] == 6)
        check("status=active -> 2",
              mon.incidents_report(status="active")["total"] == 2)
        check("status=resolved -> 4",
              mon.incidents_report(status="resolved")["total"] == 4)
        check("status=all == все", mon.incidents_report(status="all")["total"] == 6)

        # hours
        check("hours=1 -> 1 (сбой 0.5ч)", mon.incidents_report(hours=1)["total"] == 1,
              "total=%r" % mon.incidents_report(hours=1)["total"])
        check("hours=3 -> 2 (0.5ч, 2ч)", mon.incidents_report(hours=3)["total"] == 2,
              "total=%r" % mon.incidents_report(hours=3)["total"])
        check("hours=9 -> 4 (0.5ч, 2ч, 5ч, 8ч)", mon.incidents_report(hours=9)["total"] == 4,
              "total=%r" % mon.incidents_report(hours=9)["total"])
        check("hours=0 -> без ограничения", mon.incidents_report(hours=0)["total"] == 6)
        check("hours=24 -> 5 (сбой 30ч назад исключён)",
              mon.incidents_report(hours=24)["total"] == 5,
              "total=%r" % mon.incidents_report(hours=24)["total"])
        check("hours=48 == без ограничения (6)",
              mon.incidents_report(hours=48)["total"] == 6,
              "total=%r" % mon.incidents_report(hours=48)["total"])

        # комбинации
        combo = mon.incidents_report(service="telegram", status="active", hours=1)
        check("combo telegram+active+1h -> 1", combo["total"] == 1, "total=%r" % combo["total"])
        check("combo: результат соответствует всем фильтрам",
              all(i["service_id"] == "telegram" and i["status"] == "active"
                  for i in combo["incidents"]))
        combo = mon.incidents_report(query="таймаут", service="telegram", status="active")
        check("combo q+service+status -> 1", combo["total"] == 1, "total=%r" % combo["total"])

        # пагинация
        full = mon.incidents_report()
        all_ids = [i["id"] for i in full["incidents"]]
        seen = []
        totals = set()
        for offset in range(0, 6, 2):
            page = mon.incidents_report(limit=2, offset=offset)
            totals.add(page["total"])
            seen.extend(i["id"] for i in page["incidents"])
        check("paging: total не меняется между страницами", totals == {6}, sorted(totals))
        check("paging: страницы не пересекаются", seen == all_ids,
              "%r vs %r" % (seen, all_ids))
        check("paging: offset за пределами -> пусто, total тот же",
              mon.incidents_report(limit=2, offset=99)["incidents"] == []
              and mon.incidents_report(limit=2, offset=99)["total"] == 6)
        check("paging: limit больше total возвращает все",
              len(mon.incidents_report(limit=500)["incidents"]) == 6)
        # пагинация вместе с q
        pages = mon.incidents_report(query="таймаут", limit=2, offset=0)
        pages2 = mon.incidents_report(query="таймаут", limit=2, offset=2)
        check("paging+q: total=4 на обеих страницах",
              (pages["total"], pages2["total"]) == (4, 4),
              "%r %r" % (pages["total"], pages2["total"]))
        ids1 = [i["id"] for i in pages["incidents"]]
        ids2 = [i["id"] for i in pages2["incidents"]]
        check("paging+q: страницы не пересекаются", not set(ids1) & set(ids2))

        # сериализация в отчёте
        act = mon.incidents_report(status="active")["incidents"]
        check("активные в отчёте: ended_at null",
              all(i["ended_at"] is None and i["ended_epoch"] is None for i in act))
        check("активные в отчёте: duration_sec считается",
              all(isinstance(i["duration_sec"], int) and i["duration_sec"] > 0 for i in act),
              [i["duration_sec"] for i in act])
        res = mon.incidents_report(status="resolved")["incidents"]
        check("resolved в отчёте: ended_at заполнен",
              all(i["ended_at"] for i in res))
        required = {"id", "service_id", "service_name", "title", "reason", "status",
                    "started_at", "started_epoch", "ended_at", "ended_epoch",
                    "duration_sec", "failed_checks", "total_checks", "service_status"}
        check("объект инцидента содержит все поля контракта",
              all(required <= set(i) for i in full["incidents"]))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 5. Персистентность
# --------------------------------------------------------------------------

def test_persistence():
    print("\n[5] Персистентность state.json")
    tmp = tempfile.mkdtemp(prefix="netmon-qa-persist-")
    try:
        mon = S.Monitor(interval=15, data_dir=tmp)
        yt_bad = build_checks("youtube", default_ok=False,
                              error="tls_handshake_timeout", stage="tls")
        mon._apply_round(by_service(yt_bad, build_checks("telegram"),
                                    build_checks("internet")))
        mon._apply_round(by_service(yt_bad, build_checks("telegram"),
                                    build_checks("internet")))
        mon._apply_round(all_ok())
        mon._apply_round(all_ok())
        mon.version = 7
        mon.rounds = 7
        mon._save()

        state_path = os.path.join(tmp, "state.json")
        check("state.json создан", os.path.isfile(state_path))
        series_before = {sid: len(pts) for sid, pts in mon.series.items()}

        mon2 = S.Monitor(interval=15, data_dir=tmp)
        check("после рестарта series восстановлены",
              {sid: len(pts) for sid, pts in mon2.series.items()} == series_before,
              "%r vs %r" % ({sid: len(p) for sid, p in mon2.series.items()}, series_before))
        check("после рестарта инциденты восстановлены",
              len(mon2.incidents) == len(mon.incidents),
              "%d vs %d" % (len(mon2.incidents), len(mon.incidents)))
        check("после рестарта version восстановлен", mon2.version == 7, mon2.version)
        check("после рестарта rounds восстановлены", mon2.rounds == 7, mon2.rounds)
        check("после рестарта last_results восстановлены",
              set(mon2.last_results) == {"youtube", "telegram", "internet"},
              sorted(mon2.last_results))
        # статусы не «оживают» сами
        snap = mon2.snapshot()
        check("snapshot после рестарта отдаёт статусы (не unknown)",
              all(s["status"] != "unknown" for s in snap["services"]),
              [s["status"] for s in snap["services"]])
        check("snapshot после рестарта сохраняет resolved-инцидент",
              any(i["status"] == "resolved" for i in mon2.incidents))

        # активный инцидент переживает рестарт как active
        mon3 = S.Monitor(interval=15, data_dir=tmp)
        mon3._apply_round(by_service(yt_bad, build_checks("telegram"),
                                     build_checks("internet")))
        mon3._apply_round(by_service(yt_bad, build_checks("telegram"),
                                     build_checks("internet")))
        mon3._save()
        active_before = [i for i in mon3.incidents if i["status"] == "active"]
        check("подготовлен активный инцидент", len(active_before) >= 1)
        mon4 = S.Monitor(interval=15, data_dir=tmp)
        active_after = [i for i in mon4.incidents if i["status"] == "active"]
        check("активный инцидент остался active после рестарта",
              len(active_after) == len(active_before),
              "%d vs %d" % (len(active_after), len(active_before)))
        check("после рестарта у активного ended_at null",
              all(i["ended_at"] is None for i in active_after))

        # битый state.json не мешает запуску
        with open(state_path, "w", encoding="utf-8") as fh:
            fh.write("{not a json")
        try:
            mon5 = S.Monitor(interval=15, data_dir=tmp)
            ok = mon5.snapshot()["summary"]["services_total"] == 3
        except Exception as exc:  # noqa: BLE001
            ok = False
            print("   ↳ исключение: %s" % exc)
        check("битый state.json не мешает старту", ok)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# 6. uptime / сериализация снимка
# --------------------------------------------------------------------------

def test_uptime_and_snapshot():
    print("\n[6] uptime и снимок состояния (unit)")
    mon, tmp = new_monitor()
    try:
        now = time.time()
        points = [
            {"t": int(now - 3000), "status": "ok", "latency_ms": 100},
            {"t": int(now - 2000), "status": "blocked", "latency_ms": None},
            {"t": int(now - 1000), "status": "ok", "latency_ms": 120},
            {"t": int(now - 100), "status": "ok", "latency_ms": 110},
        ]
        check("uptime: 3/4 ok == 75.0", mon._uptime(points, now - 3600) == 75.0,
              mon._uptime(points, now - 3600))
        check("uptime: пустое окно == 0.0", mon._uptime(points, now) == 0.0,
              mon._uptime(points, now))
        check("uptime: нет точек == 0.0", mon._uptime([], now - 3600) == 0.0)

        # до первого цикла — unknown, обязательные строки непустые
        snap = mon.snapshot()
        check("snapshot до опроса: overall=unknown",
              snap["summary"]["overall"] == "unknown",
              snap["summary"]["overall"])
        check("snapshot до опроса: version=0", snap["version"] == 0, snap["version"])
        check("snapshot до опроса: next_check_in=0", snap["next_check_in"] == 0.0,
              snap["next_check_in"])
        check("snapshot: services_total=3", snap["summary"]["services_total"] == 3)
        check("snapshot: статусы сервисов в енуме",
              all(s["status"] in S.STATUS_LABELS for s in snap["services"]),
              [s["status"] for s in snap["services"]])
        check("snapshot: обязательные строки непустые",
              all(s["name"] and s["subtitle"] and s["status_label"] and s["status_reason"]
                  for s in snap["services"]))
        check("snapshot: recent_incidents == []", snap["recent_incidents"] == [])

        # после цикла — интернет-маппинг и summary
        mon._apply_round(all_ok())
        snap = mon.snapshot()
        check("snapshot после ok: overall=ok", snap["summary"]["overall"] == "ok",
              snap["summary"]["overall"])
        check("snapshot после ok: internet.status=ok",
              snap["internet"]["status"] == "ok", snap["internet"]["status"])
        check("snapshot: services_ok + affected + unknown == total",
              snap["summary"]["services_ok"] + snap["summary"]["services_affected"] == 3)
        check("snapshot: avg_latency_ms число", isinstance(snap["summary"]["avg_latency_ms"], int))
        check("snapshot: checks_total == 16 (5+7+4)",
              snap["summary"]["checks_total"] == 16, snap["summary"]["checks_total"])
        check("snapshot: series растёт", len(snap["services"][0]["series"]) == 1)

        # internet down -> overall down
        mon._apply_round(by_service(
            build_checks("youtube", default_ok=False, error="connect_timeout", stage="tcp"),
            build_checks("telegram", default_ok=False, error="connect_timeout", stage="tcp"),
            build_checks("internet", default_ok=False, error="connect_timeout", stage="tcp")))
        snap = mon.snapshot()
        check("internet down -> overall=down", snap["summary"]["overall"] == "down",
              snap["summary"]["overall"])
        check("internet down -> internet.label «Похоже, интернета нет»",
              snap["internet"]["label"] == "Похоже, интернета нет",
              snap["internet"]["label"])
        # internet-сервис не бывает blocked (всегда down/degraded)
        check("internet service status никогда не blocked",
              snap["services"][2]["status"] in ("down", "degraded", "unknown"),
              snap["services"][2]["status"])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _iso_ok(value):
    if not isinstance(value, str):
        return False
    try:
        time.strptime(value[:19], ISO_RE_SECONDS)
    except ValueError:
        return False
    return value.endswith("+03:00") or value.endswith("Z") or "+" in value[19:] or "-" in value[19:]


def _active_incident(mon, sid):
    for inc in mon.incidents:
        if inc["service_id"] == sid and inc["status"] == "active":
            return mon._serialize_incident(inc, time.time())
    return {}


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main():
    print("=" * 72)
    print("net-monitor: юнит-тесты инцидентов/классификации (stdlib, без сети)")
    print("=" * 72)
    tests = [
        test_classify_basic,
        test_incident_lifecycle,
        test_incident_streak_reset,
        test_incidents_report,
        test_persistence,
        test_uptime_and_snapshot,
    ]
    crashed = []
    for test in tests:
        try:
            test()
        except Exception:  # noqa: BLE001
            crashed.append(test.__name__)
            print("\n[CRASH] %s" % test.__name__)
            traceback.print_exc()

    failed = [name for name, ok, _ in _results if not ok]
    print("\n" + "=" * 72)
    print("Проверок: %d, OK: %d, FAIL: %d, упавших тестов: %d" % (
        len(_results), len(_results) - len(failed), len(failed), len(crashed)))
    if failed:
        print("\nУпавшие проверки:")
        for name in failed:
            print("  ✗ %s" % name)
    if crashed:
        print("\nТесты с исключениями: %s" % ", ".join(crashed))
    if failed or crashed:
        print("\nРЕЗУЛЬТАТ: FAIL")
        return 1
    print("\nРЕЗУЛЬТАТ: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
