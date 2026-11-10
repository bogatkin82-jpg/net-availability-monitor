# Архитектура и паритет настроек

## Один конфиг — все платформы

`config.json` в корне репозитория — единственный источник истины. Его читают:

| Платформа | Как получает конфиг |
|---|---|
| Python-сервер (macOS/Linux/Windows) | `--config PATH`, env `NETMON_CONFIG`, `config.json` рядом со `server.py`/`.exe`, иначе встроенные дефолты |
| Windows-трей / `.exe` | тот же `config.json`; PyInstaller кладёт копию рядом с exe/внутрь бандла |
| macOS-менюбар | путь к рантайму (`NETMON_DIR`/`~/.netmon-dir`); читает `server.port`, `display.macos_menubar.*`, `status_labels`, `status_colors` из рантайм-копии `config.json`, статусы получает из `/api/state` |
| Android | Gradle-таск копирует корневой `config.json` в `app/src/main/assets/config.json` перед сборкой |

Что задаётся конфигом:

- `server.host/port` — где слушает Python-сервер;
- `polling.interval_sec`, `check_timeout_sec`, `dns_timeout_sec` — частота и таймауты;
- `incidents.open_after_failures/close_after_successes/max_stored` — правила журнала сбоев;
- `history.max_age_hours/max_points/series_points` — глубина истории и длина серий;
- `status_labels`, `status_colors` — подписи и цвета статусов;
- `services[]` — сервисы (YouTube, Telegram, контрольный канал) и их проверки:
  `kind` (dns/tcp/tls/https), `host`, `port`, `path`, `expect`, `core`, `timeout`;
- `hints` — подсказки пользователю для degraded/blocked/down;
- `display.*` — вид индикатора (macOS overlay/dot/text, размеры; Windows tray);
- `notifications.*` — что и когда уведомлять (Windows/Android).

Валидация: `python3 tools/check_config.py` — проверяет схему, уникальность id, типы,
диапазоны, цвета, коды HTTP и паритет Android-ассета с корневым конфигом.
Схема для редакторов: [`docs/config.schema.json`](config.schema.json). Python-сервер дополнительно
понимает необязательный `classification.slow_latency_ms` (порог «медленно», по умолчанию 4000 мс);
Windows запускает тот же `server.py` и параметр уважает; Android пока использует фиксированные 4000 мс.

## Общая логика проверок

Каждая проверка проходит стадии **DNS → TCP → TLS → HTTP** и фиксирует тайминги.
Статус сервиса вычисляется по core-проверкам:

| Ситуация | Статус |
|---|---|
| все core-проверки прошли | `ok` |
| часть прошла | `degraded` |
| все упали, интернет жив, ошибки — таймауты/сбросы TCP/TLS или DNS | `blocked` (похоже на фильтрацию) |
| все упали, соединение отклонено/HTTP-ошибка | `down` |
| интернет недоступен | `down` |
| медленно (max core ok > 4 c) | `degraded` |

Коды ошибок одинаковы на всех платформах: `dns_error`, `dns_timeout`, `connect_timeout`,
`connect_refused`, `connect_unreachable`, `tls_handshake_timeout`, `tls_reset`,
`tls_cert_error`, `tls_error`, `read_timeout`, `http_status_unexpected`, `http_error`,
`unknown_error`.

Инцидент открывается после `open_after_failures` подряд неудачных опросов и закрывается
после `close_after_successes` успешных. История — точки `{t, status, latency_ms}`,
uptime за 1 ч и 24 ч считается по ним.

## Платформы

- **macOS**: Python-сервер + дашборд в браузере + нативный индикатор в меню-баре
  (Swift/AppKit, `menubar/NetBar.swift`), автозапуск через LaunchAgents (`install.sh`).
- **Windows**: тот же Python-сервер и дашборд + трей-иконка (pystray), один `.exe`
  (PyInstaller) и установщик с автозапуском (`windows/`).
- **Android**: отдельное приложение (Kotlin/Jetpack Compose) со своим движком проверок,
  тем же конфигом, журналом сбоев и уведомлениями (`android/`).

Общий HTTP API описан в [`docs/api-contract.md`](api-contract.md); сервер отдаёт
дашборд из `static/`.
