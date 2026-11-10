# NetMonitor для Windows

Windows-версия мультиплатформенного монитора доступности сети: локальный
Python-сервер с дашбордом и значок в системном трее. Настройки — общий
`config.json` из корня репозитория, поэтому поведение совпадает с macOS- и
Android-версиями. API описан в [api-contract.md](api-contract.md).

```
NetMonitor.exe
 ├─ сервер (Python, в фоновом потоке): проверки DNS→TCP→TLS→HTTP,
 │  инциденты, data/state.json, HTTP API и дашборд на http://127.0.0.1:8099/
 └─ трей (pystray + Pillow): цветная точка статуса, меню, уведомления
```

## Содержание

1. [Быстрый старт](#быстрый-старт)
2. [Установка в систему](#установка-в-систему)
3. [Автозапуск](#автозапуск)
4. [Конфигурация](#конфигурация)
5. [Порт, хост и firewall](#порт-хост-и-firewall)
6. [Меню трея](#меню-трея)
7. [Командная строка exe](#командная-строка-exe)
8. [Данные, логи, диагностика](#данные-логи-диагностика)
9. [Сборка из исходников](#сборка-из-исходников)
10. [CI и релизы](#ci-и-релизы)
11. [Удаление](#удаление)
12. [Ограничения](#ограничения)

## Быстрый старт

Требования: Windows 10/11 x64, ничего доустанавливать не нужно — Python
внутри exe (PyInstaller onefile).

1. Скачайте `NetMonitor-windows-x64.zip` из GitHub Actions (артефакт сборки) или
   из раздела Releases (сборки на тег `v*`) и распакуйте в любую папку.
2. Запустите `NetMonitor.exe`. При первом запуске рядом с exe появится
   `config.json` (копия встроенного шаблона) и каталоги `data/`, затем — значок
   в трее. В `config.json.example` лежит тот же шаблон для справки.
3. SmartScreen: exe не подписан сертификатом, поэтому Windows может показать
   «Windows защитила ваш компьютер». Нажмите **«Подробнее» → «Выполнить в любом
   случае»**. Это ожидаемо для неподписанных сборок; исходники открыты.
4. Двойной клик по значку открывает дашборд, либо меню трея → «Открыть дашборд»:
   <http://127.0.0.1:8099/>.

Первый цикл проверок занимает до ~8 секунд — до его завершения статусы
показываются как «Нет данных», это нормально (см. примечание о `unknown` в
[api-contract.md](api-contract.md)).

## Установка в систему

`install.ps1` копирует exe в `%LOCALAPPDATA%\net-availability-monitor`, создаёт
ярлык в меню «Пуск» и прописывает автозапуск при входе в систему.

```powershell
# из корня репозитория после сборки (или укажите -Source путь к exe)
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1

# сразу запустить после установки
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1 -Start

# без автозапуска / без ярлыка
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1 -NoAutostart -NoShortcut
```

Что делает установщик:

- останавливает запущенный `NetMonitor.exe`, чтобы заменить файл;
- копирует exe в `%LOCALAPPDATA%\net-availability-monitor\NetMonitor.exe`;
  существующий `config.json` **не перезаписывается**;
- создаёт ярлык `%APPDATA%\Microsoft\Windows\Start Menu\Programs\NetMonitor.lnk`;
- включает автозапуск в
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run` (значение `NetMonitor`,
  запуск exe без аргументов — сервер + трей).

Если `-ExecutionPolicy` запрещает скрипты, запускайте так, как показано выше
(`-ExecutionPolicy Bypass` действует только на этот запуск).

## Автозапуск

Автозапуск можно включать/выключать из меню трея: **«Автозапуск»** (галочка).
Пункт доступен только в Windows и пишет/удаляет то же значение в
`HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, что и `install.ps1`.

- Трей читает автозапуск заново при каждом обновлении меню, поэтому состояние
  всегда актуально.
- Запуск второго экземпляра не создаёт вторую иконку: приложение использует
  именованный mutex `Local\NetMonitorTray`, а повторный запуск просто открывает
  дашборд в браузере.
- Если при входе в систему сервер уже работает (например, запущен вручную),
  второй сервер не поднимается: exe проверяет `/api/health` и работает как трей.

## Конфигурация

Единый источник настроек — `config.json` (тот же файл, что у macOS/Android).
Порядок поиска:

1. аргумент `--config PATH` (у `NetMonitor.exe` и у `python server.py`);
2. переменная окружения `NETMON_CONFIG` (путь к файлу);
3. `config.json` рядом с exe (или рядом с `server.py` при запуске из исходников);
4. `config.json` внутри бандла PyInstaller (встроенный шаблон);
5. встроенные значения, полностью совпадающие с поведением v1.

При первом запуске exe копирует встроенный шаблон рядом с собой. Если каталог
exe недоступен для записи (например, `C:\Program Files`), копия создаётся в
`%LOCALAPPDATA%\net-availability-monitor\config.json` и используется
автоматически. После правки `config.json` перезапустите приложение (или сервер,
если он запущен отдельно).

Формальная схема настроек — `docs/config.schema.json` (её же проверяет CI через
`tools/check_config.py`); сервер дополнительно валидирует файл при загрузке.

Проверить файл без запуска сервера:

```powershell
.\NetMonitor.exe --server-only --config C:\path\config.json --check-config
# или из исходников:
python server.py --check-config --config config.json
```

Ошибки валидации печатаются в stderr (в windowed-сборке — в лог), процесс
завершается с кодом **2**. Сообщения указывают точное поле, например:

```
Ошибка конфигурации (C:\netmon\config.json):
  - services[1].checks[0].kind: неизвестный тип проверки 'udp' (допустимо: dns, tcp, tls, https)
  - services[2].id: идентификатор 'youtube' уже используется
```

### Поля конфигурации

| Поле | Назначение | По умолчанию |
|---|---|---|
| `version` | версия схемы (целое ≥ 1) | `1` |
| `server.host` | адрес прослушивания HTTP-сервера | `127.0.0.1` |
| `server.port` | порт HTTP-сервера (1..65535) | `8099` |
| `polling.interval_sec` | интервал опроса сети (≥ 5 с) | `20` |
| `polling.check_timeout_sec` | таймаут проверки по умолчанию, если у проверки нет `timeout` | `8` |
| `polling.dns_timeout_sec` | предел ожидания DNS | `4` |
| `incidents.open_after_failures` | сколько подряд неудач открывают инцидент | `2` |
| `incidents.close_after_successes` | сколько подряд успехов закрывают инцидент | `1` |
| `incidents.max_stored` | сколько инцидентов хранить | `500` |
| `history.max_age_hours` | глубина истории для графика | `24` |
| `history.max_points` | предел точек истории на сервис | `8640` |
| `history.series_points` | сколько точек отдавать в `/api/state` | `180` |
| `classification.slow_latency_ms` | порог «медленно → degraded», мс (необязательное) | `4000` |
| `status_labels` | подписи статусов `ok/degraded/blocked/down/unknown` | см. файл |
| `status_colors` | цвета точки трея `#rrggbb` для тех же статусов | см. файл |
| `display.windows_tray.dot_pt` | размер точки трея, px (8..64) | `16` |
| `notifications.windows.enabled` | всплывающие уведомления о сбоях | `true` |
| `notifications.windows.notify_on` | при каких агрегированных статусах уведомлять | `["blocked","down"]` |
| `services[]` | список сервисов и их проверок | YouTube, Telegram, Интернет |

Сервис:

| Поле | Назначение |
|---|---|
| `id` | уникальный идентификатор (`[a-z0-9_-]`, строчные) |
| `name` | отображаемое имя |
| `subtitle`, `icon`, `accent` | оформление карточки на дашборде |
| `incident_title` | заголовок инцидента (по умолчанию «<name> недоступен») |
| `hints.degraded/blocked/down` | подсказки пользователю при проблемах |
| `checks[]` | проверки сервиса (непустой список) |

Проверка:

| Поле | Назначение |
|---|---|
| `id` | уникальный идентификатор **среди всех сервисов** |
| `kind` | `dns` \| `tcp` \| `tls` \| `https` |
| `host` | домен или IP |
| `port` | порт (1..65535, по умолчанию 443) |
| `path` | путь для `https` (по умолчанию `/`; только для `https`) |
| `expect` | допустимые HTTP-статусы, например `[204]` (только для `https`) |
| `core` | участвует ли проверка в классификации (по умолчанию `false`) |
| `label` | подпись проверки |
| `timeout` | таймаут проверки, секунды (0.5..300) |

Правила валидации: обязательны `services`, у сервиса — `id`, `name`, `checks`,
у проверки — `id`, `kind`, `host`; `id` сервисов и проверок не повторяются;
`kind` — только из списка; порты и HTTP-статусы в допустимых диапазонах;
интервалы и таймауты положительные; цвета в формате `#rrggbb`. Неизвестные
ключи игнорируются (совместимость с будущими версиями схемы), неизвестные
статусы в `status_labels`/`status_colors`/`hints` — ошибка.

Сервис с `id: "internet"` — контрольный канал: если он недоступен, остальные
сервисы классифицируются как `down` (не `blocked`). Поле `internet` в API
строится из этого сервиса; если сервиса с таким `id` нет, оно собирается из
общего статуса.

### Пример: добавить свой сервис

```json
{
  "id": "github",
  "name": "GitHub",
  "subtitle": "разработка · github.com",
  "icon": "globe",
  "accent": "#6e5494",
  "incident_title": "GitHub недоступен",
  "hints": {
    "blocked": "Похоже на блокировку — проверьте доступ через VPN",
    "down": "Сервис не отвечает — повторите позже"
  },
  "checks": [
    { "id": "gh_https", "label": "HTTPS github.com", "kind": "https",
      "host": "github.com", "port": 443, "path": "/",
      "expect": [200, 301, 302], "core": true, "timeout": 8 },
    { "id": "gh_dns", "label": "DNS github.com", "kind": "dns",
      "host": "github.com", "core": false }
  ]
}
```

Добавьте объект в массив `services` и перезапустите приложение.

## Порт, хост и firewall

- По умолчанию сервер слушает только `127.0.0.1` — снаружи компьютера он
  недоступен. Меняйте `server.host` на `0.0.0.0` только осознанно: API не имеет
  авторизации.
- Если порт 8099 занят, сервер не стартует, а в логе появляется сообщение
  «Не удалось занять 127.0.0.1:8099». Варианты: поменять `server.port` в
  `config.json`; или запустить одноразово с флагом
  `NetMonitor.exe --server-only --port 8100`.
- Windows Firewall: для прослушивания на `127.0.0.1` правило не требуется. При
  `0.0.0.0` разрешите приложение в диалоге брандмауэра.
- Проверить, кто занял порт: `Get-NetTCPConnection -LocalPort 8099 | Select-Object OwningProcess`.
- Трей определяет адрес и порт из `config.json`; если сервер запускается с
  другим `--port`, передайте тот же флаг и трею: `NetMonitor.exe --tray-only --port 8100`.

## Меню трея

Значок — точка 16 px по агрегированному статусу (`status_colors` из конфига);
если сервер не отвечает — серая точка (`unknown`). Tooltip содержит время
обновления и компактные статусы сервисов. Опрос `/api/state` — каждые 10 секунд.

Пункты меню:

| Пункт | Действие |
|---|---|
| `Мониторинг доступности · HH:MM:SS` | заголовок с временем последнего обновления |
| `<Сервис> — <статус> · сбой N мин` | подменю: причина, подсказка, «Проверки: X из Y», начало и длительность активного сбоя |
| «Проверить сейчас» | `POST /api/check`; пункт заблокирован, пока идёт опрос (до ~15 с) |
| «Открыть дашборд» | открывает `http://<host>:<port>/` в браузере |
| «Запустить сервер» | активен, только когда `/api/health` не отвечает: поднимает сервер и ждёт до 15 с |
| «Автозапуск» | включает/выключает запуск при входе (HKCU Run); только Windows |
| «Выход» | останавливает сервер (если он встроенный), сохраняет `state.json` и закрывает трей |

Уведомления: при смене агрегированного статуса на `blocked`/`down`
(настраивается в `notifications.windows`) показывается всплывающее уведомление
с перечнем проблемных сервисов.

## Командная строка exe

```
NetMonitor.exe                       сервер в потоке + трей (обычный режим)
NetMonitor.exe --tray-only           только трей (сервер запущен отдельно)
NetMonitor.exe --server-only         только сервер (аналог python server.py)
NetMonitor.exe --selftest            самопроверка без GUI и сети; 0 — успех
NetMonitor.exe --check-config        проверить config.json и выйти (2 — ошибка)
    --config PATH                    путь к config.json
    --host 127.0.0.1                 переопределить server.host
    --port 8100                      переопределить server.port
    --interval 30                    переопределить polling.interval_sec
    --data-dir PATH                  каталог для state.json
```

`--server-only` понимает те же флаги, что и `python server.py`; флаги
`--port`/`--interval` переопределяют значения из конфига и в обычном режиме
(они применяются и к встроенному серверу, и к трею).

## Данные, логи, диагностика

| Что | Где |
|---|---|
| exe | `%LOCALAPPDATA%\net-availability-monitor\NetMonitor.exe` (после install.ps1) |
| конфиг | рядом с exe: `config.json` (создаётся при первом запуске) |
| состояние | рядом с exe: `data\state.json` (история, инциденты, серии) |
| лог | `%LOCALAPPDATA%\net-availability-monitor\logs\netmon.log` (ротация: 5 МБ → `.1`) |

В GUI-сборке нет консоли, поэтому весь вывод (banner, сообщения цикла, ошибки)
пишется в лог. Запуск из исходников (`python server.py`) выводит всё в консоль.

Типичные проблемы:

- **Значок серый, «Сервер недоступен»** — сервер не стартовал или слушает другой
  порт. Проверьте лог и `http://127.0.0.1:<port>/api/health`; меню →
  «Запустить сервер».
- **`--selftest` вернул не 0** — в логе указано, какая проверка не прошла
  (обычно отсутствует `static/` в бандле или ошибка конфигурации).
- **Автозапуск не срабатывает** — проверьте значение
  `HKCU\Software\Microsoft\Windows\CurrentVersion\Run\NetMonitor`;
  путь в значении должен указывать на существующий exe (после переустановки
  перезапустите `install.ps1` или включите «Автозапуск» в меню).
- **Антивирус блокирует exe** — неподписанный PyInstaller-бинарник может
  попадать под эвристику; добавьте в исключения или соберите exe самостоятельно.

## Сборка из исходников

Нужны Windows x64, Python 3.12 (подойдёт и 3.9+, CI использует 3.12) и
PowerShell 5.1+.

```powershell
.\windows\build.ps1            # pip install + icon + py_compile + PyInstaller + selftest + zip
.\windows\build.ps1 -SkipDeps  # если зависимости уже стоят
```

Результат:

```
dist\NetMonitor.exe
dist\NetMonitor-windows-x64.zip
   ├─ NetMonitor.exe
   ├─ config.json.example   (шаблон; рабочий config.json exe создаёт сам)
   └─ README.md             (копия docs\windows.md)
```

Структура `windows/`:

| Файл | Назначение |
|---|---|
| `entry.py` | точка входа exe: сервер в потоке + трей, режимы, первый запуск конфига, лог, mutex |
| `netmon.spec` | PyInstaller onefile: `static/` + `config.json`, `hiddenimports=["pystray._win32"]`, `console=False` |
| `make_icon.py` | генератор `icon.ico`/`icon.png` (Pillow); готовый `icon.ico` закоммичен |
| `build.ps1` | сборка, selftest и упаковка zip |
| `install.ps1` / `uninstall.ps1` | установка в `%LOCALAPPDATA%`, ярлык, HKCU Run; удаление |
| `tests/test_windows.py` | локальные тесты Windows-слоя (stdlib, без сети) |

Проверки на любой ОС (в т.ч. macOS/Linux) — из корня репозитория:

```bash
python3 -m py_compile server.py tray.py windows/entry.py
python3 server.py --check-config
python3 tray.py --selftest
python3 windows/entry.py --selftest
python3 windows/tests/test_windows.py
```

## CI и релизы

Workflow `.github/workflows/windows.yml` запускается на `windows-latest`:

1. Python 3.12, `pip install pyinstaller pystray pillow`;
2. `py_compile` всех точек входа;
3. `python windows/make_icon.py`;
4. `python -m PyInstaller --noconfirm --clean windows/netmon.spec`;
5. `dist\NetMonitor.exe --selftest` — должен вернуть 0 (GUI не создаётся, вывод
   дублируется в `%LOCALAPPDATA%\net-availability-monitor\logs\netmon.log`);
6. smoke-тест: `--server-only` на порту 18099, проверка `/api/health`,
   `/api/state` (в т.ч. что `--interval` применился) и отдачи дашборда из `static/`;
7. артефакт **`NetMonitor-windows-x64.zip`**: `NetMonitor.exe` +
   `config.json.example` + `README.md` (`docs/windows.md`).

Триггеры: push в `main`/`master` (при изменениях `server.py`, `tray.py`,
`windows/**`, `config.json`, `static/**`), pull request, ручной запуск
(`workflow_dispatch`) и теги `v*`. На тег `v*` тот же zip дополнительно
публикуется в GitHub Release (`softprops/action-gh-release`), с автоматическими
release notes.

## Удаление

```powershell
# остановить процессы, убрать автозапуск и ярлык, удалить exe;
# config.json, data\ и логи сохраняются
powershell -ExecutionPolicy Bypass -File .\windows\uninstall.ps1

# удалить и данные/конфиг/логи
powershell -ExecutionPolicy Bypass -File .\windows\uninstall.ps1 -PurgeData
```

## Ограничения

- exe не подписан сертификатом: SmartScreen может предупреждать, а некоторые
  антивирусы — ложно срабатывать на PyInstaller-бинарники.
- GUI-трей (иконка, меню, уведомления, HKCU Run, mutex) проверяется только в
  Windows/CI: на macOS/Linux доступны `--selftest`, сервер и логика меню без GUI.
- `install.ps1` ставит программу для текущего пользователя (HKCU), без прав
  администратора; установка в `Program Files` не поддерживается.
- Уведомления трея зависят от системных настроек Windows (фокус-ассистент может
  их скрывать).
- История и инциденты хранятся в `data/state.json` рядом с exe; при
  `-PurgeData` или переустановке в другой каталог они не переносятся.

---

## Установщик (Inno Setup)

Релизы содержат `NetMonitor-Setup-x64.exe` — per-user установщик без прав администратора:

- ставит приложение в `%LOCALAPPDATA%\net-availability-monitor`;
- создаёт ярлыки в меню «Пуск» (опционально — на рабочем столе);
- по желанию включает автозапуск (HKCU `Run`);
- регистрирует деинсталлятор в «Установка и удаление программ»;
- рядом кладёт `config.json.example`, `README.md` и публичный сертификат подписи.

Портативный вариант — `NetMonitor-windows-x64.zip` (запуск без установки). Оба варианта
собираются в CI (`windows/installer.iss`, `ISCC.exe`).

## Подпись и SmartScreen

`.exe` и установщик подписываются Authenticode-сертификатом в CI. Сертификат
**самоподписанный** (`windows/netmon-code-signing.cer`, прикладывается к релизу), поэтому
SmartScreen предупреждает до первого доверия. Проверка подписи:

```powershell
Get-AuthenticodeSignature .\NetMonitor.exe | Format-List Status, SignerCertificate
Get-AuthenticodeSignature .\NetMonitor-Setup-x64.exe | Format-List Status, SignerCertificate
```

Если хотите убрать предупреждение для этого издателя:

```powershell
Import-Certificate -FilePath .\netmon-code-signing.cer -CertStoreLocation Cert:\CurrentUser\Root
```

Секреты CI: `WINDOWS_PFX_BASE64` (base64 от `.pfx`) и `WINDOWS_PFX_PASSWORD`. Без них
push/PR-сборки собираются неподписанными; релизные теги требуют валидную подпись.

## Nightly

Каждую ночь workflow `Windows build` (cron `0 2 * * *`) публикует prerelease с тегом
`nightly`; вручную — Actions → Windows build → Run workflow → *nightly*.
