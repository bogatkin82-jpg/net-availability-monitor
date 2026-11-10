# Network Monitor — контракт API (v1)

Бэкенд: `python3 server.py --port 8099` (stdlib, без зависимостей).
Фронтенд: статика из `static/` (`index.html`, `app.js`, `style.css`), отдаётся тем же сервером
на `http://127.0.0.1:8099/`. Никаких сборщиков, CDN и внешних шрифтов.

Все времена — ISO 8601 с локальным смещением сервера (Europe/Moscow), поля `*_epoch` — unixtime (float).
Числовые метрики могут быть `null` (нет данных / сервис не отвечает).

## GET /api/state

Главный опрос. Обновляется каждые `poll_interval` секунд.

```json
{
  "generated_at": "2026-11-09T14:05:12+03:00",
  "epoch": 1762772712.4,
  "version": 42,
  "running": true,
  "poll_interval": 20,
  "next_check_in": 12.3,
  "checking": false,
  "internet": {
    "status": "ok",
    "label": "Интернет работает",
    "latency_ms": 210
  },
  "proxy": {
    "mode": "system",
    "active": true,
    "url": "http://127.0.0.1:12334"
  },
  "summary": {
    "overall": "degraded",
    "overall_label": "Есть проблемы",
    "services_total": 3,
    "services_ok": 1,
    "services_affected": 2,
    "active_incidents": 2,
    "avg_latency_ms": 187,
    "checks_total": 15,
    "checks_ok": 5
  },
  "services": [
    {
      "id": "youtube",
      "name": "YouTube",
      "subtitle": "видеохостинг · youtube.com",
      "icon": "youtube",
      "accent": "#ff3040",
      "status": "blocked",
      "status_label": "Похоже на блокировку",
      "status_reason": "TLS-рукопожатие не завершается (таймаут 8 с) — типичный признак DPI-фильтрации",
      "hint": "Проверьте доступ через VPN или другой канал",
      "latency_ms": null,
      "uptime_1h": 0.0,
      "uptime_24h": 0.0,
      "checks_ok": 2,
      "checks_total": 5,
      "checks": [
        {
          "id": "yt_https",
          "label": "HTTPS /generate_204",
          "target": "www.youtube.com",
          "kind": "https",
          "core": true,
          "ok": false,
          "latency_ms": 8621,
          "stage": "tls",
          "error": "tls_handshake_timeout",
          "error_label": "TLS-рукопожатие не завершилось",
          "error_text": "The handshake operation timed out",
          "http_status": null,
          "ip": "142.251.150.4",
          "timings": { "dns_ms": 3, "tcp_ms": 214, "tls_ms": null, "http_ms": null },
          "measured_at": "2026-11-09T14:05:00+03:00"
        }
      ],
      "series": [ { "t": 1762772600, "status": "ok", "latency_ms": 190 } ],
      "active_incident": null,
      "last_incident": null
    }
  ],
  "recent_incidents": []
}
```

### Енумы

`service.status` / `overall` / `series[].status`:
`ok` — доступен, `degraded` — нестабильно/частично, `blocked` — похоже на блокировку (таймауты/сбросы
TCP/TLS при работающем интернете), `down` — недоступен (нет интернета или полный отказ), `unknown` — нет данных.

`proxy` (с 1.2.0): режим проверок и активный прокси.

- `mode`: `direct` | `system` | `http://host:port`;
- `active`: используется ли прокси сейчас;
- `url`: адрес прокси или `null`.

`internet.status`: `ok` | `degraded` | `down` | `unknown`.
`internet.label`: «Интернет работает» / «Интернет нестабилен» / «Похоже, интернета нет» / «Проверка не выполнялась». `unknown` возможен только до завершения первого цикла опроса (первые ~8 с после старта) — UI должен трактовать его нейтрально, без ложного баннера «нет интернета».

`check.stage`: `ok` | `dns` | `tcp` | `tls` | `http` — этап, на котором проверка остановилась.
`check.error` (при `ok:false`): `dns_error`, `dns_timeout`, `connect_timeout`, `connect_refused`,
`connect_unreachable`, `tls_handshake_timeout`, `tls_reset`, `tls_cert_error`, `tls_error`,
`read_timeout`, `http_status_unexpected`, `http_error`, `unknown_error`.
При успехе `error: null`, `error_label: null`, `error_text: null`.

`series` — последние ~180 опросов (≈1 час при интервале 20 с), по возрастанию `t`.
`timings.*` — миллисекунды по этапам, `null` если этап не выполнялся/не завершился.

### incident (объект)

```json
{
  "id": "9f2c1b7a4e",
  "service_id": "telegram",
  "service_name": "Telegram",
  "title": "Telegram недоступен",
  "reason": "TCP-соединение с api.telegram.org не устанавливается (таймаут 8 с)",
  "status": "active",
  "started_at": "2026-11-09T13:40:00+03:00",
  "started_epoch": 1762771200.0,
  "ended_at": null,
  "ended_epoch": null,
  "duration_sec": 812,
  "failed_checks": 3,
  "total_checks": 4,
  "service_status": "down"
}
```

`status`: `active` | `resolved`. У активного сбоя `ended_at: null`, `duration_sec` растёт.
`failed_checks` / `total_checks` — состояние на момент открытия.

## GET /api/incidents

Серверный поиск по журналу сбоев.

Query: `q` (подстрока в `service_name`/`title`/`reason`, регистронезависимо, опционально),
`service` (`all` | `youtube` | `telegram` | `internet`), `status` (`all` | `active` | `resolved`),
`hours` (0 = без ограничения по времени; иначе только сбои, начавшиеся за последние N часов),
`limit` (1..500, по умолчанию 100), `offset` (по умолчанию 0).

```json
{ "total": 12, "limit": 100, "offset": 0, "incidents": [ { ...incident... } ] }
```

Сортировка — новые сверху. `total` — количество после фильтрации (до limit/offset).

## POST /api/check

Принудительный внеплановый опрос. Может выполняться до ~15 с.

```json
{ "ok": true, "state": { ...тот же объект, что GET /api/state... } }
```

Если опрос уже идёт — `200` с `"ok": false, "reason": "already_running"` и текущим `state`.

## GET /api/export?format=json

Скачивание отчёта: `Content-Disposition: attachment; filename="net-monitor-report-YYYYMMDD-HHMMSS.json"`.
Тело: `{"exported_at": "...", "state": {...}, "incidents": [ ...все... ]}`.

## GET /api/health

```json
{ "ok": true, "uptime_sec": 1234, "version": 42, "rounds": 61,
  "last_round_at": "2026-11-09T14:05:00+03:00", "poll_interval": 20 }
```

## Статика

`GET /` → `index.html`; `/app.js` → `static/app.js`; `/style.css`; `/favicon.svg` (опционально).
Остальное — 404 JSON `{"error":"not_found"}`.

## Примечания для фронтенда

- У каждой проверки в `services[].checks[]` есть `proxy_used` (`true`, если TCP/TLS/HTTP шли через прокси).
- Опрашивать `/api/state` каждые 3 с, `Cache-Control: no-store` сервер выставляет сам.
- При сетевой ошибке показать офлайн-баннер и продолжать попытки.
- Не блокировать UI на время `/api/check`: блокировать только кнопку «Проверить сейчас» (disabled + спиннер).
- `recent_incidents` в `/api/state` — последние 5; полный журнал/поиск — через `/api/incidents`.
- Всё, что `null`, отображать как «—».
