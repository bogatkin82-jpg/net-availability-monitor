#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UI-приёмочные тесты дашборда net-monitor: рендер, обновление данных, поиск и
фильтры журнала, кнопка «Проверить сейчас», офлайн-баннер, адаптивность,
ошибки консоли.

Драйвер браузера — headless Chrome по Chrome DevTools Protocol. Внешних
зависимостей нет: WebSocket-клиент и CDP-обвязка реализованы на stdlib
(socket/struct/base64), чтобы тест запускался где угодно без pip.

Запуск (сервер уже поднят, статика раздаётся им же):
    python3 tests/test_ui.py
    python3 tests/test_ui.py --base-url http://127.0.0.1:8789
    python3 tests/test_ui.py --chrome "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

Коды выхода: 0 — все проверки прошли, 1 — есть FAIL.
Если Chrome не найден, браузерные проверки помечаются SKIP (серверные
проверки статики при этом всё равно выполняются).
"""

import argparse
import base64
import json
import os
import re
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.parse
import urllib.request

PROJECT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC_DIR = os.path.join(PROJECT_DIR, "static")
DEFAULT_CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

RESULTS = []
SKIPS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print("%s %s%s" % ("OK  " if cond else "FAIL", name,
                       ("  [%s]" % detail) if (detail and not cond) else ""))


def skip(name, reason):
    SKIPS.append((name, reason))
    print("SKIP %s (%s)" % (name, reason))


def section(title):
    print("\n== %s" % title)


# --------------------------------------------------------------------------
# Минимальный WebSocket-клиент (текстовые кадры) + CDP
# --------------------------------------------------------------------------

class WSClient(object):
    def __init__(self, url, timeout=20):
        match = re.match(r"^ws://([^/:]+):(\d+)(/.*)$", url)
        if not match:
            raise ValueError("неподдерживаемый ws url: %r" % url)
        host, port, path = match.group(1), int(match.group(2)), match.group(3)
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.sock.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        request = (
            "GET %s HTTP/1.1\r\nHost: %s:%d\r\nUpgrade: websocket\r\n"
            "Connection: Upgrade\r\nSec-WebSocket-Key: %s\r\n"
            "Sec-WebSocket-Version: 13\r\nOrigin: http://127.0.0.1\r\n\r\n"
            % (path, host, port, key))
        self.sock.sendall(request.encode("ascii"))
        self.reader = self.sock.makefile("rb")
        status = self.reader.readline()
        if b" 101 " not in status and not status.startswith(b"HTTP/1.1 101"):
            raise RuntimeError("WebSocket handshake failed: %r" % status)
        while True:
            line = self.reader.readline()
            if line in (b"\r\n", b"\n", b""):
                break

    def _send_frame(self, opcode, payload):
        header = bytearray([0x80 | opcode])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header += struct.pack(">H", length)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", length)
        mask = os.urandom(4)
        header += mask
        masked = bytes(byte ^ mask[ix % 4] for ix, byte in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def _read_frame(self):
        head = self.reader.read(2)
        if len(head) < 2:
            raise ConnectionError("WebSocket закрыт")
        finish = head[0] & 0x80
        opcode = head[0] & 0x0F
        masked = head[1] & 0x80
        length = head[1] & 0x7F
        if length == 126:
            length = struct.unpack(">H", self.reader.read(2))[0]
        elif length == 127:
            length = struct.unpack(">Q", self.reader.read(8))[0]
        mask = self.reader.read(4) if masked else None
        data = self.reader.read(length)
        if mask:
            data = bytes(byte ^ mask[ix % 4] for ix, byte in enumerate(data))
        return finish, opcode, data

    def recv_message(self):
        chunks = []
        while True:
            finish, opcode, data = self._read_frame()
            if opcode == 0x8:
                raise ConnectionError("WebSocket закрыт пиром")
            if opcode == 0x9:
                self._send_frame(0xA, data)
                continue
            if opcode == 0xA:
                continue
            chunks.append(data)
            if finish:
                break
        return b"".join(chunks).decode("utf-8")

    def close(self):
        try:
            self._send_frame(0x8, b"")
        except Exception:  # noqa: BLE001
            pass
        try:
            self.sock.close()
        except Exception:  # noqa: BLE001
            pass


class CDP(object):
    def __init__(self, ws_url, timeout=25):
        self.ws = WSClient(ws_url, timeout=timeout)
        self.next_id = 1
        self.events = []          # console/exception/log события
        self.exception_events = []

    def call(self, method, params=None, timeout=30):
        call_id = self.next_id
        self.next_id += 1
        self.ws.send(json.dumps({"id": call_id, "method": method,
                                 "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            message = json.loads(self.ws.recv_message())
            if message.get("id") == call_id:
                if "error" in message:
                    raise RuntimeError("%s -> %s" % (method, message["error"]))
                return message.get("result", {})
            self._record(message)
        raise TimeoutError("CDP timeout: %s" % method)

    def _record(self, message):
        method = message.get("method")
        params = message.get("params") or {}
        if method in ("Runtime.exceptionThrown", "Log.entryAdded",
                      "Runtime.consoleAPICalled"):
            self.events.append(message)
            if method == "Runtime.exceptionThrown":
                self.exception_events.append(params.get("exceptionDetails") or {})

    def drain(self, seconds=0.3):
        self.ws.sock.settimeout(seconds)
        deadline = time.time() + seconds
        try:
            while time.time() < deadline:
                self._record(json.loads(self.ws.recv_message()))
        except (socket.timeout, TimeoutError):
            pass
        finally:
            self.ws.sock.settimeout(25)

    def evaluate(self, expression):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True,
            "awaitPromise": True, "userGesture": True})
        if result.get("exceptionDetails"):
            raise RuntimeError("JS exception: %s" % json.dumps(
                result["exceptionDetails"], ensure_ascii=False)[:500])
        return (result.get("result") or {}).get("value")

    def wait_for(self, expression, timeout=20, interval=0.15):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.evaluate(expression):
                return True
            time.sleep(interval)
        return False

    def errors(self):
        out = []
        for message in self.events:
            method = message.get("method")
            params = message.get("params") or {}
            if method == "Runtime.exceptionThrown":
                details = params.get("exceptionDetails") or {}
                out.append("exception: %s" % (details.get("text")
                                              or details.get("exception", {}).get("description")))
            elif method == "Log.entryAdded":
                entry = params.get("entry") or {}
                if entry.get("level") == "error":
                    out.append("log: %s (%s)" % (entry.get("text"), entry.get("url")))
            elif method == "Runtime.consoleAPICalled":
                if params.get("type") in ("error", "assert"):
                    args = " ".join(str(a.get("value", a.get("description", "")))
                                    for a in (params.get("args") or []))
                    out.append("console.%s: %s" % (params.get("type"), args))
        return out


def free_port():
    sock = socket.socket()
    try:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]
    finally:
        sock.close()


def http_json(url, timeout=10):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


class Chrome(object):
    """Headless Chrome с включённым CDP; владеет процессом и профилем."""

    def __init__(self, executable, window_size=None, profile_dir=None):
        self.executable = executable
        self.profile_dir = profile_dir or tempfile.mkdtemp(prefix="netmon-qa-chrome-")
        self.port = free_port()
        args = [
            executable, "--headless", "--no-sandbox", "--disable-gpu",
            "--disable-breakpad", "--disable-crash-reporter",
            "--disable-background-networking", "--disable-extensions",
            "--no-first-run", "--no-default-browser-check", "--mute-audio",
            "--remote-debugging-port=%d" % self.port,
            "--remote-allow-origins=*", "--user-data-dir=" + self.profile_dir,
            "--hide-scrollbars",
        ]
        if window_size:
            args.append("--window-size=%s" % window_size)
        args.append("about:blank")
        self.stderr_path = os.path.join(self.profile_dir, "chrome.stderr.log")
        self._stderr = open(self.stderr_path, "wb")
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=self._stderr)

    def ws_url(self, timeout=25):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError("Chrome завершился с кодом %r" % self.proc.returncode)
            try:
                targets = http_json("http://127.0.0.1:%d/json/list" % self.port, timeout=3)
            except Exception:  # noqa: BLE001
                time.sleep(0.15)
                continue
            pages = [t for t in targets if t.get("type") == "page"]
            if pages and pages[0].get("webSocketDebuggerUrl"):
                return pages[0]["webSocketDebuggerUrl"]
            time.sleep(0.15)
        raise RuntimeError("не дождались CDP-таргета Chrome")

    def open_page(self, url):
        cdp = CDP(self.ws_url())
        cdp.call("Page.enable")
        cdp.call("Runtime.enable")
        cdp.call("Log.enable")
        cdp.call("Network.enable")
        cdp.call("Page.navigate", {"url": url})
        if not cdp.wait_for("document.readyState === 'complete'", timeout=25):
            raise RuntimeError("страница не догрузилась")
        return cdp

    def close(self):
        try:
            if self.proc.poll() is None:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
        finally:
            try:
                self._stderr.close()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------
# Вспомогательные проверки DOM через CDP
# --------------------------------------------------------------------------

def js_text(cdp, selector):
    return cdp.evaluate(
        "(function(){var e=document.querySelector(%s);return e?e.textContent.replace(/\\s+/g,' ').trim():null;})()"
        % json.dumps(selector))


def js_attr(cdp, selector, attr):
    return cdp.evaluate(
        "(function(){var e=document.querySelector(%s);return e?(e.getAttribute(%s)):null;})()"
        % (json.dumps(selector), json.dumps(attr)))


def js_hidden(cdp, selector):
    return cdp.evaluate(
        "(function(){var e=document.querySelector(%s);if(!e)return null;"
        "return !!e.hidden || getComputedStyle(e).display === 'none';})()" % json.dumps(selector))


def row_texts(cdp, selector="#inc-tbody tr[data-id]"):
    return cdp.evaluate(
        "(function(){return Array.prototype.map.call(document.querySelectorAll(%s),"
        "function(r){return Array.prototype.map.call(r.querySelectorAll('td'),"
        "function(c){return c.textContent.replace(/\\s+/g,' ').trim();}).join(' | ');});})()"
        % json.dumps(selector))


def api_incidents(base, **params):
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    return http_json("%s/api/incidents?%s" % (base, query))


def meta_found_count(cdp):
    text = js_text(cdp, "#inc-meta") or ""
    match = re.search(r"Найдено:\s*(\d+)", text)
    return int(match.group(1)) if match else None


def fill_search(cdp, text):
    return cdp.evaluate(
        "(function(){var el=document.getElementById('inc-search');"
        "el.value=%s;el.dispatchEvent(new Event('input',{bubbles:true}));return el.value;})()"
        % json.dumps(text, ensure_ascii=False))


# --------------------------------------------------------------------------
# Тесты
# --------------------------------------------------------------------------

def test_assets_real(base):
    for path, ctype, marker in (
            ("/", "text/html", "app.js"),
            ("/app.js", "javascript", "/api/state"),
            ("/style.css", "text/css", None),
            ("/favicon.svg", "image/svg+xml", "<svg")):
        try:
            with urllib.request.urlopen(base + path, timeout=10) as resp:
                status = resp.status
                headers = dict(resp.headers)
                body = resp.read().decode("utf-8", "replace")
        except urllib.error.HTTPError as exc:
            status, headers, body = exc.code, dict(exc.headers), ""
        check("%s -> 200" % path, status == 200, status)
        check("%s: content-type %s" % (path, ctype),
              ctype in headers.get("Content-Type", "").lower(),
              headers.get("Content-Type"))
        if marker:
            check("%s: содержит %r" % (path, marker), marker.lower() in body.lower())
    html = urllib.request.urlopen(base + "/", timeout=10).read().decode("utf-8")
    for needle in ("/app.js", "/style.css", "/favicon.svg", "Мониторинг доступности"):
        check("index.html содержит %r" % needle, needle in html)
    check("index.html без внешних CDN/шрифтов",
          "http://" not in html.replace("http://127.0.0.1", "") and
          "https://" not in html, "найдены внешние URL")


def test_desktop_render(cdp, base, state):
    section("Рендер десктопа: шапка, KPI, карточки, постадийная сетка")
    if not cdp.wait_for("document.querySelectorAll('#services .card').length === 3", timeout=25):
        check("отрисованы 3 карточки сервисов", False,
              cdp.evaluate("document.querySelectorAll('#services .card').length"))
        return

    check("title содержит «Мониторинг доступности»",
          "Мониторинг доступности" in (cdp.evaluate("document.title") or ""),
          cdp.evaluate("document.title"))
    check("h1 == «Мониторинг доступности»",
          js_text(cdp, "h1") == "Мониторинг доступности", js_text(cdp, "h1"))

    # KPI
    kpi = cdp.evaluate("""(function(){var o={};
      ['total','ok','affected','incidents','latency'].forEach(function(k){
        var el=document.getElementById('kpi-'+k);
        o[k]=el?el.textContent.replace(/\\s+/g,' ').trim():null;});
      return o;})()""")
    summary = state["summary"]
    check("KPI «Сервисов» == services_total", kpi["total"] == str(summary["services_total"]),
          "%r vs %r" % (kpi["total"], summary["services_total"]))
    check("KPI «Доступно» == services_ok", kpi["ok"] == str(summary["services_ok"]),
          "%r vs %r" % (kpi["ok"], summary["services_ok"]))
    check("KPI «С проблемами» == services_affected",
          kpi["affected"] == str(summary["services_affected"]),
          "%r vs %r" % (kpi["affected"], summary["services_affected"]))
    check("KPI «Активных сбоев» == active_incidents",
          kpi["incidents"] == str(summary["active_incidents"]),
          "%r vs %r" % (kpi["incidents"], summary["active_incidents"]))
    expected_lat = ("—" if summary["avg_latency_ms"] is None
                    else "%d мс" % summary["avg_latency_ms"])
    check("KPI «Средний отклик» == avg_latency_ms (%s)" % expected_lat,
          kpi["latency"] == expected_lat, "%r vs %r" % (kpi["latency"], expected_lat))
    body_text = cdp.evaluate("document.body.textContent.replace(/\\s+/g,' ')")
    for label in ("Сервисов", "Доступно", "С проблемами", "Активных сбоев", "Средний отклик"):
        check("KPI-подпись %r на месте" % label,
              label in (body_text or ""), label)

    # карточки: имена, статусы, причины
    cards = cdp.evaluate(
        "(function(){"
        "function txt(el){return el?el.textContent.replace(/\\s+/g,' ').trim():'';}"
        "return Array.prototype.map.call(document.querySelectorAll('#services .card'),"
        "function(c){return {"
        "id:c.getAttribute('data-id')||'',"
        "name:txt(c.querySelector('.card-name')),"
        "sub:txt(c.querySelector('.card-sub')),"
        "pill:txt(c.querySelector('.pill-text')),"
        "reason:txt(c.querySelector('.card-reason')),"
        "stages:Array.prototype.map.call(c.querySelectorAll('.stage .stage-label'),"
        "function(s){return s.textContent.trim();}),"
        "canvases:c.querySelectorAll('canvas').length,"
        "checkRows:c.querySelectorAll('.checks-list li.check').length"
        "};});})()")
    check("карточек ровно 3", len(cards) == 3, len(cards))
    api_by_id = {s["id"]: s for s in state["services"]}
    for card in cards:
        sid = card["id"] or ({"YouTube": "youtube", "Telegram": "telegram",
                               "Интернет": "internet"}.get(card["name"]))
        svc = api_by_id.get(sid)
        check("карточка %r найдена в API" % card["name"], svc is not None, sid)
        if not svc:
            continue
        check("%s: имя == %s" % (sid, svc["name"]), card["name"] == svc["name"],
              card["name"])
        check("%s: подзаголовок == subtitle" % sid, card["sub"] == svc["subtitle"],
              card["sub"])
        expected_pill = {"youtube": svc["status_label"], "telegram": svc["status_label"],
                         "internet": svc["status_label"]}[sid]
        check("%s: статус на карточке (%s)" % (sid, expected_pill),
              card["pill"] == expected_pill, card["pill"])
        check("%s: причина == status_reason" % sid, card["reason"] == svc["status_reason"],
              card["reason"])
        check("%s: постадийная сетка DNS/TCP/TLS/HTTP" % sid,
              card["stages"] == ["DNS", "TCP", "TLS", "HTTP"], card["stages"])
        check("%s: canvas-sparkline присутствует" % sid, card["canvases"] == 1,
              card["canvases"])
        check("%s: в «Детали проверок» %d строк" % (sid, len(svc["checks"])),
              card["checkRows"] == len(svc["checks"]),
              "%r vs %d" % (card["checkRows"], len(svc["checks"])))
    check("всего canvas == 3", cdp.evaluate("document.querySelectorAll('canvas').length") == 3,
          cdp.evaluate("document.querySelectorAll('canvas').length"))
    check("canvas имеет ненулевой размер",
          cdp.evaluate("Array.prototype.every.call(document.querySelectorAll('canvas'),"
                       "function(c){return c.width>0 && c.height>0;})"))
    check("«Детали проверок» встречается 3 раза",
          cdp.evaluate("(document.body.textContent.match(/Детали проверок/g)||[]).length") == 3,
          cdp.evaluate("(document.body.textContent.match(/Детали проверок/g)||[]).length"))

    # стадии содержат значения мс/«—»
    stage_values = cdp.evaluate(
        "Array.prototype.map.call(document.querySelectorAll('#services .card:first-child .stage-value'),"
        "function(v){return v.textContent.trim();})")
    check("значения этапов в формате «N мс»/«—»",
          stage_values and all(re.match(r"^(\d+ (мс|с)|—)$", v) for v in stage_values),
          stage_values)

    # журнал сбоев
    section("Журнал сбоев: таблица, поиск, фильтры")
    check("заголовок «Журнал сбоев»", js_text(cdp, "#inc-title") == "Журнал сбоев",
          js_text(cdp, "#inc-title"))
    check("поле поиска с плейсхолдером",
          "Поиск" in (js_attr(cdp, "#inc-search", "placeholder") or ""),
          js_attr(cdp, "#inc-search", "placeholder"))
    chips = cdp.evaluate(
        "Array.prototype.map.call(document.querySelectorAll('.chips .chip'),"
        "function(c){return c.getAttribute('data-service');})")
    check("фильтры-чипы: all/youtube/telegram/internet",
          chips == ["all", "youtube", "telegram", "internet"], chips)
    status_options = cdp.evaluate(
        "Array.prototype.map.call(document.querySelectorAll('#inc-status option'),"
        "function(o){return o.value;})")
    check("селект статуса: all/active/resolved",
          status_options == ["all", "active", "resolved"], status_options)
    hours_options = cdp.evaluate(
        "Array.prototype.map.call(document.querySelectorAll('#inc-hours option'),"
        "function(o){return Number(o.value);})")
    check("селект периода: 0/1/24/168",
          hours_options == [0, 1, 24, 168], hours_options)
    headers = cdp.evaluate(
        "Array.prototype.map.call(document.querySelectorAll('.inc-table thead th'),"
        "function(t){return t.textContent.trim();})")
    check("колонки таблицы сбоев", headers == ["Статус", "Сервис", "Начало",
                                              "Длительность", "Причина", "Проверки"],
          headers)
    total = api_incidents(base, limit=100)["total"]
    rows = row_texts(cdp)
    check("строк в таблице == total журнала (%d)" % total, len(rows) == total,
          "%d vs %d" % (len(rows), total))
    check("счётчик «Показано N из M» согласован",
          (js_text(cdp, "#inc-count") or "").strip() == "Показано %d из %d"
          % (len(rows), total), js_text(cdp, "#inc-count"))
    check("пустые состояния скрыты при наличии данных",
          js_hidden(cdp, "#services-empty") is True and
          js_hidden(cdp, "#inc-empty") is True and
          js_hidden(cdp, "#inc-more") is True)
    check("баннер не показан при работающем интернете", js_hidden(cdp, "#banner") is True)
    check("нет видимого «Нет данных о сервисах»",
          js_hidden(cdp, "#services-empty") is True)

    # кнопки/футер
    check("кнопка «Проверить сейчас» на месте",
          (js_text(cdp, "#btn-check") or "").find("Проверить сейчас") != -1,
          js_text(cdp, "#btn-check"))
    check("кнопка «Проверить сейчас» не disabled",
          js_attr(cdp, "#btn-check", "disabled") is None)
    check("ссылка «Экспорт» ведёт на /api/export",
          (js_attr(cdp, "#btn-export", "href") or "").startswith("/api/export"),
          js_attr(cdp, "#btn-export", "href"))
    check("у «Экспорт» есть download",
          js_attr(cdp, "#btn-export", "download") is not None)
    footer = js_text(cdp, "footer") or ""
    check("футер: net-monitor + Europe/Moscow",
          "net-monitor" in footer and "Europe/Moscow" in footer, footer)
    live = js_text(cdp, "#live-text") or ""
    check("живой индикатор «обновлено ЧЧ:ММ:СС»",
          re.match(r"^обновлено \d{2}:\d{2}:\d{2}$", live) is not None, live)
    check("aria-live регион обновляется",
          js_text(cdp, "#live-status") != "", js_text(cdp, "#live-status"))


def test_auto_update(cdp):
    section("Автообновление данных (опрос каждые 3 с)")
    first = js_text(cdp, "#live-text")
    time.sleep(4.0)
    second = js_text(cdp, "#live-text")
    check("«обновлено ЧЧ:ММ:СС» меняется через 4 с", first != second,
          "%r -> %r" % (first, second))
    check("интервал опроса ≈3 с (не замерло)", second is not None, second)
    next_text = js_text(cdp, "#next-check") or ""
    check("подпись о следующей проверке присутствует",
          "проверк" in next_text.lower() or "с" in next_text, next_text)


def test_search(cdp, base):
    section("Поиск по журналу: фильтрация, очистка, пустой результат")
    total = api_incidents(base, limit=100)["total"]
    expected = api_incidents(base, q="таймаут", limit=100)
    fill_search(cdp, "таймаут")
    ok = cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === %d"
                      % expected["total"], timeout=15)
    check("поиск запускает перерисовку журнала", ok)
    time.sleep(0.3)
    rows = row_texts(cdp)
    check("поиск «таймаут»: строк == total API (%d)" % expected["total"],
          len(rows) == expected["total"], "%d vs %d" % (len(rows), expected["total"]))
    check("поиск «таймаут»: каждая строка содержит «таймаут»",
          all("таймаут" in row.lower() for row in rows), rows[:2])
    check("поиск: счётчик совпадает с числом строк",
          (js_text(cdp, "#inc-count") or "") == "Показано %d из %d"
          % (len(rows), expected["total"]), js_text(cdp, "#inc-count"))
    check("кнопка очистки поиска стала видимой", js_hidden(cdp, "#inc-search-clear") is False)

    # регистронезависимость: латиница в верхнем регистре
    expected_y = api_incidents(base, q="YOUTUBE", limit=100)
    fill_search(cdp, "YOUTUBE")
    cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === %d"
                 % expected_y["total"], timeout=15)
    rows = row_texts(cdp)
    check("поиск «YOUTUBE» регистронезависим: строк == %d" % expected_y["total"],
          len(rows) == expected_y["total"], "%d vs %d" % (len(rows), expected_y["total"]))
    check("поиск «YOUTUBE»: строки содержат YouTube",
          all("youtube" in row.lower() for row in rows), rows[:2])

    # пустой результат
    fill_search(cdp, "щщщщщ")
    ok = cdp.wait_for("(function(){var e=document.getElementById('inc-empty');"
                      "return e && !e.hidden;})()", timeout=12)
    check("пустой поиск показывает empty-state", ok)
    check("empty-state: «Сбоев не найдено»",
          (js_text(cdp, "#inc-empty-title") or "") == "Сбоев не найдено",
          js_text(cdp, "#inc-empty-title"))
    check("пустой поиск: 0 строк", row_texts(cdp) == [], row_texts(cdp))
    check("пустой поиск: «Показано 0 из 0»",
          (js_text(cdp, "#inc-count") or "") == "Показано 0 из 0",
          js_text(cdp, "#inc-count"))
    check("пустой поиск: «Найдено: 0»", meta_found_count(cdp) == 0, meta_found_count(cdp))

    # очистка
    cdp.evaluate("document.getElementById('inc-search-clear').click()")
    ok = cdp.wait_for("document.getElementById('inc-search').value === '' && "
                      "document.querySelectorAll('#inc-tbody tr[data-id]').length === %d" % total,
                      timeout=12)
    check("кнопка «×» очищает поиск и возвращает строки", ok,
          "%d строк" % len(row_texts(cdp)))
    check("после очистки empty-state скрыт", js_hidden(cdp, "#inc-empty") is True)


def test_filters(cdp, base):
    section("Фильтры по сервису / статусу / периоду")
    # сервис
    cdp.evaluate("document.querySelector('.chips .chip[data-service=\"telegram\"]').click()")
    ok = cdp.wait_for("document.querySelector('.chips .chip[data-service=\"telegram\"]')"
                      ".getAttribute('aria-pressed') === 'true'", timeout=10)
    check("чип Telegram становится активным (aria-pressed)", ok)
    expected = api_incidents(base, service="telegram", limit=100)
    cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === %d"
                 % expected["total"], timeout=15)
    time.sleep(0.3)
    rows = row_texts(cdp)
    check("фильтр service=telegram: строк == %d" % expected["total"],
          len(rows) == expected["total"], "%d vs %d" % (len(rows), expected["total"]))
    check("все строки — Telegram",
          all("telegram" in row.lower() for row in rows), rows)

    # статус
    cdp.evaluate("document.querySelector('.chips .chip[data-service=\"all\"]').click()")
    cdp.evaluate("(function(){var s=document.getElementById('inc-status');"
                 "s.value='active';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
    ok = cdp.wait_for("document.getElementById('inc-status').value === 'active'", timeout=10)
    check("селект статуса переключён на «Активные»", ok)
    expected = api_incidents(base, status="active", limit=100)
    cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === %d"
                 % expected["total"], timeout=15)
    time.sleep(0.3)
    rows = row_texts(cdp)
    check("фильтр status=active: строк == %d" % expected["total"],
          len(rows) == expected["total"], "%d vs %d" % (len(rows), expected["total"]))
    check("все строки активные", all("Активен" in row for row in rows), rows)

    # период
    cdp.evaluate("(function(){var s=document.getElementById('inc-hours');"
                 "s.value='1';s.dispatchEvent(new Event('change',{bubbles:true}));})()")
    cdp.wait_for("document.getElementById('inc-hours').value === '1'", timeout=10)
    expected = api_incidents(base, status="active", hours=1, limit=100)
    cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === %d"
                 % expected["total"], timeout=15)
    time.sleep(0.3)
    rows = row_texts(cdp)
    check("фильтр hours=1: строк == %d" % expected["total"],
          len(rows) == expected["total"], "%d vs %d" % (len(rows), expected["total"]))
    # если строк нет — должен быть empty-state
    if not rows:
        check("пустой отфильтрованный журнал показывает empty-state",
              js_hidden(cdp, "#inc-empty") is False)

    # сброс
    cdp.evaluate("(function(){var s=document.getElementById('inc-hours');"
                 "s.value='0';s.dispatchEvent(new Event('change',{bubbles:true}));"
                 "var st=document.getElementById('inc-status');"
                 "st.value='all';st.dispatchEvent(new Event('change',{bubbles:true}));})()")
    ok = cdp.wait_for("document.querySelectorAll('#inc-tbody tr[data-id]').length === "
                      "%d" % api_incidents(base, limit=100)["total"], timeout=12)
    check("сброс фильтров возвращает полный журнал", ok,
          len(row_texts(cdp)))


def test_check_button(cdp, base):
    section("Кнопка «Проверить сейчас»")
    # дождаться свободного сервера, чтобы ручной цикл не столкнулся с плановым
    health = http_json(base + "/api/health")
    interval = health["poll_interval"]
    rounds0 = health["rounds"]
    deadline = time.time() + interval + 15
    while time.time() < deadline and http_json(base + "/api/health")["rounds"] <= rounds0:
        time.sleep(0.2)
    version_before = http_json(base + "/api/state")["version"]

    cdp.evaluate("document.getElementById('btn-check').click()")
    time.sleep(0.5)
    disabled = cdp.evaluate("document.getElementById('btn-check').disabled")
    spinner_visible = cdp.evaluate("!document.getElementById('check-spinner').hidden")
    check("во время цикла кнопка disabled", disabled is True, disabled)
    check("во время цикла виден спиннер", spinner_visible is True, spinner_visible)
    checking_text = js_text(cdp, "#check-label") or ""
    check("текст кнопки меняется на идущую проверку",
          checking_text != "Проверить сейчас", checking_text)

    enabled = cdp.wait_for("document.getElementById('btn-check').disabled === false",
                           timeout=45)
    check("после цикла кнопка снова активна", enabled)
    check("спиннер скрыт после завершения",
          cdp.evaluate("document.getElementById('check-spinner').hidden") is True)
    version_after = http_json(base + "/api/state")["version"]
    check("version снимка вырос (ручной цикл выполнен)",
          version_after > version_before, "%r -> %r" % (version_before, version_after))


def test_offline_banner(cdp, base, port):
    section("Офлайн-баннер при недоступном API")
    cdp.call("Network.setBlockedURLs",
             {"urls": ["*://127.0.0.1:%d/api/*" % port, "*://127.0.0.1:%d/*" % port]})
    ok = cdp.wait_for("(function(){var b=document.getElementById('banner');"
                      "return b && !b.hidden;})()", timeout=15)
    check("потеря связи показывает баннер", ok)
    if ok:
        title = js_text(cdp, "#banner-title") or ""
        check("баннер: «Нет связи с сервером мониторинга»",
              "Нет связи" in title, title)
        check("title страницы помечен ⚠",
              (cdp.evaluate("document.title") or "").startswith("⚠"),
              cdp.evaluate("document.title"))
        check("живой индикатор показывает «нет связи»",
              "нет связи" in (js_text(cdp, "#live-text") or ""),
              js_text(cdp, "#live-text"))
    # восстановление
    cdp.call("Network.setBlockedURLs", {"urls": []})
    ok = cdp.wait_for("(function(){var b=document.getElementById('banner');"
                      "return b && b.hidden;})()", timeout=20)
    check("после восстановления связи баннер скрывается", ok)
    check("статус после восстановления снова обновляется",
          re.match(r"^обновлено \d{2}:\d{2}:\d{2}$", js_text(cdp, "#live-text") or "") is not None,
          js_text(cdp, "#live-text"))


def test_console_errors(cdp):
    section("Консоль браузера")
    cdp.drain(0.5)
    errors = cdp.errors()
    check("нет JS-ошибок (Uncaught/TypeError/ReferenceError/SyntaxError/console.error)",
          not errors, "; ".join(errors[:5]))
    check("нет исключений Runtime.exceptionThrown", not cdp.exception_events,
          [e.get("text") for e in cdp.exception_events][:3])
    messages = len(cdp.events)
    print("   (всего перехвачено console/log/exception событий: %d)" % messages)


def test_responsive(chrome_path, base):
    section("Адаптивность: 390×844 (мобильный) и 1280×720 (десктоп)")
    chrome = Chrome(chrome_path)
    cdp = None
    try:
        cdp = chrome.open_page(base + "/")
        if not cdp.wait_for("document.querySelectorAll('#services .card').length === 3",
                            timeout=25):
            check("мобильный рендер: 3 карточки", False,
                  cdp.evaluate("document.querySelectorAll('#services .card').length"))
            return
        # window-size в headless Chrome упирается в минимальную ширину окна,
        # поэтому мобильный вьюпорт задаём эмуляцией метрик устройства.
        cdp.call("Emulation.setDeviceMetricsOverride", {
            "width": 390, "height": 844, "deviceScaleFactor": 2, "mobile": True})
        time.sleep(0.6)
        width = cdp.evaluate("window.innerWidth")
        check("ширина вьюпорта == 390 (факт %r)" % width, width == 390, width)
        for selector, label in (("header.topbar", "шапка"), (".kpi", "KPI-полоса"),
                                ("#services", "секция сервисов"),
                                ("section.incidents", "журнал сбоев"),
                                ("footer", "футер"),
                                ("#inc-search", "поиск"),
                                ("#btn-check", "кнопка проверки"),
                                ("#btn-export", "кнопка экспорта")):
            check("мобильный: %s на месте" % label,
                  cdp.evaluate("!!document.querySelector(%s)" % json.dumps(selector)))
        columns = cdp.evaluate("getComputedStyle(document.querySelector('#services'))"
                               ".gridTemplateColumns.split(' ').length")
        check("мобильный: карточки в 1 колонку", columns == 1, columns)
        overflow = cdp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
        check("мобильный: нет горизонтального скролла (overflow %r px)" % overflow,
              isinstance(overflow, int) and overflow <= 2, overflow)
        check("мобильный: консоль без ошибок", not cdp.errors(),
              "; ".join(cdp.errors()[:3]))
    finally:
        if cdp:
            cdp.ws.close()
        chrome.close()

    # десктоп: 3 колонки
    chrome = Chrome(chrome_path, window_size="1280,720")
    cdp = None
    try:
        cdp = chrome.open_page(base + "/")
        if cdp.wait_for("document.querySelectorAll('#services .card').length === 3",
                        timeout=25):
            columns = cdp.evaluate("getComputedStyle(document.querySelector('#services'))"
                                   ".gridTemplateColumns.split(' ').length")
            check("десктоп 1280: карточки в 3 колонки", columns == 3, columns)
            overflow = cdp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
            check("десктоп: нет горизонтального скролла", isinstance(overflow, int)
                  and overflow <= 2, overflow)
    finally:
        if cdp:
            cdp.ws.close()
        chrome.close()


def test_dumpdom_update(chrome_path, base):
    section("Обновление данных: два dump-dom с интервалом 5 с")
    def dump():
        profile = tempfile.mkdtemp(prefix="netmon-qa-dump-")
        try:
            out = subprocess.run(
                [chrome_path, "--headless", "--no-sandbox", "--disable-gpu",
                 "--disable-breakpad", "--disable-crash-reporter",
                 "--user-data-dir=" + profile, "--virtual-time-budget=8000",
                 "--dump-dom", base + "/"],
                capture_output=True, timeout=90)
            return out.stdout.decode("utf-8", "replace")
        finally:
            shutil.rmtree(profile, ignore_errors=True)

    def pick(html):
        live = re.search(r'id="live-text"[^>]*>([^<]*)<', html)
        nxt = re.search(r'id="next-check"[^>]*>([^<]*)<', html)
        return (live.group(1).strip() if live else None,
                nxt.group(1).strip() if nxt else None)

    first = pick(dump())
    time.sleep(5.0)
    second = pick(dump())
    check("первый dump-dom содержит «обновлено ЧЧ:ММ:СС»",
          first[0] and re.match(r"^обновлено \d{2}:\d{2}:\d{2}$", first[0]), first[0])
    check("второй dump-dom содержит «обновлено ЧЧ:ММ:СС»",
          second[0] and re.match(r"^обновлено \d{2}:\d{2}:\d{2}$", second[0]), second[0])
    check("данные между дампами обновились (live-text/next-check изменились)",
          first != second, "%r vs %r" % (first, second))


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

def main(argv=None):
    parser = argparse.ArgumentParser(description="UI-тесты дашборда net-monitor")
    parser.add_argument("--base-url", default="http://127.0.0.1:8789")
    parser.add_argument("--chrome", default=os.environ.get("CHROME_PATH", DEFAULT_CHROME))
    parser.add_argument("--skip-interactive", action="store_true",
                        help="только статика и dump-dom, без CDP-сценариев")
    args = parser.parse_args(argv)
    base = args.base_url.rstrip("/")
    parsed = urllib.parse.urlparse(base)
    port = parsed.port or 80
    chrome_path = args.chrome

    print("=" * 72)
    print("net-monitor: UI-тесты дашборда")
    print("сервер: %s | Chrome: %s" % (base, chrome_path))
    print("=" * 72)

    try:
        urllib.request.urlopen(base + "/api/health", timeout=5).read()
    except Exception as exc:  # noqa: BLE001
        print("Сервер недоступен: %s" % exc)
        return 1

    test_assets_real(base)
    state = http_json(base + "/api/state")

    if not os.path.isfile(chrome_path):
        skip("браузерные проверки", "Chrome не найден: %s" % chrome_path)
    elif args.skip_interactive:
        skip("CDP-сценарии", "--skip-interactive")
        test_dumpdom_update(chrome_path, base)
    else:
        chrome = Chrome(chrome_path, window_size="1280,720")
        cdp = None
        try:
            cdp = chrome.open_page(base + "/")
            test_desktop_render(cdp, base, state)
            test_auto_update(cdp)
            test_search(cdp, base)
            test_filters(cdp, base)
            test_offline_banner(cdp, base, port)
            test_check_button(cdp, base)
            test_console_errors(cdp)
        except Exception:  # noqa: BLE001
            print("\n[CRASH] CDP-сценарий упал:")
            traceback.print_exc()
            RESULTS.append(("исключение в CDP-сценарии", False, ""))
        finally:
            if cdp:
                cdp.ws.close()
            chrome.close()
            shutil.rmtree(chrome.profile_dir, ignore_errors=True)
        test_responsive(chrome_path, base)
        test_dumpdom_update(chrome_path, base)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print("\n" + "=" * 72)
    print("Проверок: %d, OK: %d, FAIL: %d, SKIP: %d"
          % (len(RESULTS), len(RESULTS) - len(failed), len(failed), len(SKIPS)))
    if failed:
        print("Упали:")
        for name in failed:
            print("  ✗ %s" % name)
    if SKIPS:
        for name, reason in SKIPS:
            print("  - SKIP: %s (%s)" % (name, reason))
    if failed:
        print("\nРЕЗУЛЬТАТ: FAIL")
        return 1
    print("\nРЕЗУЛЬТАТ: OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
