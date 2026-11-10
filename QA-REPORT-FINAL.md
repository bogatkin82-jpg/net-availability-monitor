# QA-отчёт: финальная приёмка net-availability-monitor

| | |
|---|---|
| **Дата** | 2026-11-10 |
| **Объект** | `net-availability-monitor` — мультиплатформенный монитор доступности YouTube/Telegram (macOS + Windows + Android + Python/Linux) |
| **Версия** | 1.0.0 (`server.py VERSION`, Android `versionName`, CHANGELOG) |
| **Коммит** | `13f29b5` (поверх `17920d3`), ветка `main` |
| **QA** | независимый QA-инженер (`qa-final`); чужие файлы не правились, изменены только `tests/` и этот отчёт |
| **Окружение** | macOS Darwin 27.2 arm64; Python 3.9.6; pytest 8.4.2; Swift 6.4.2; JDK 17.0.21 (`.toolchain/jdk17`); Android SDK 35; Gradle 8.9; PyYAML/jsonschema — во временном venv `/tmp/qa-venv` |
| **Сеть** | реальная: `youtube=blocked` (TLS handshake timeout 8 c), `telegram=blocked` (connect timeout), контрольный канал `internet=ok`; цикл 8,0 c |
| **Вердикт** | **PASS WITH ISSUES** — блокеров в продукте нет; 6 дефектов, найденных в первой итерации, закрыты в `13f29b5`; остались только cosmetic/minor в документации и сообщении NetBar. **Условие публикации:** закоммитить `tests/test_unit.py` (см. G-1) и этот отчёт — иначе CI-джоб `python` на `13f29b5` красный. |

---

## 1. Краткое резюме

Проверены все четыре платформы и общий конфиг: Python-сервер, Windows-слой, Android-приложение
(сборка + тесты + APK), macOS-менюбар (сборка Swift + установщик), а также git-гигиена, CI-workflow,
README/документация и отсутствие секретов.

Автоматика на `13f29b5`: **108 pytest passed · 10 live skipped (нужен QA_LIVE) · 10/10 live пройдено
отдельным прогоном · 36/36 Windows-тестов · 120 Android JUnit-проверок (60 тестов × debug/release) ·
APK 8 910 333 Б (sha256 `f621a06c…`), `assets/config.json` байт-в-байт равен корневому · Swift-индикатор
собирается**. `tools/check_config.py` — exit 0. `git ls-files` (97 файлов) — без артефактов, ключей и
секретов.

Паритет настроек проверен в четырёх местах и подтверждён: Python-дефолты = `config.json`;
Kotlin `AppConfig.DEFAULTS` = `config.json` (хосты/порты/таймауты/пороги/статусы/цвета/подсказки);
Windows-трей и `.exe` читают тот же файл; macOS-менюбар после фикса читает `server.port`,
`display.macos_menubar.*`, `status_labels`, `status_colors` (проверено вживую на config с `port=8123`
и кастомными подписями).

---

## 2. Результаты прогонов

| Набор | Команда | Проверок | Результат |
|---|---|---|---|
| Python unit (герметичный, CI) | `pytest -q tests/test_unit.py` | 108 | **OK** (0 failed; в venv с PyYAML — 108 passed; в CI python-джобе без PyYAML — 107 passed + 1 skip YAML-теста, который отдельно проверяется в job `config`) |
| Весь `tests/` как в README | `pytest -q tests` | 118 | **OK**: 108 passed, 10 skipped (live), **0 errors** |
| Живой сервер (QA_LIVE) | `QA_LIVE=1 QA_PORT=8801 pytest -q tests/test_live_acceptance.py` | 10 | **OK** (48,4 с) |
| Windows-слой | `python3 windows/tests/test_windows.py` | 36 | **OK** (1,7 с) |
| Android JUnit | `./gradlew --no-daemon test assembleDebug` | 60 × 2 варианта | **OK**, 0 failures; BUILD SUCCESSFUL |
| APK | `unzip -l` + `aapt2` + `apksigner` + diff assets | 8 проверок | **OK** |
| macOS Swift | `swiftc -swift-version 5 -O -framework Cocoa menubar/NetBar.swift` | 1 | **OK** (1 warning: non-optional compare, строка 348) |
| Конфиг | `python3 tools/check_config.py` + jsonschema | 3 | **OK** (exit 0; `config.json` и android-asset валидны по схеме) |
| Git-гигиена | ручная ревизия `git ls-files` + `git grep` | 97 файлов | **OK**, нарушений нет |

Порт: **8797 в этой сессии занят чужим сервером** (1 сервис, interval 7) — не перезапускался;
живые тесты гонялись на **8801**, серверы 8099/8789 не трогались.

---

## 3. Таблица проверок

### A. Конфиг и паритет настроек

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `config.json` ↔ `docs/config.schema.json` | валиден | jsonschema: OK (после добавления `classification`) | OK |
| `tools/check_config.py` | exit 0, 3 сервиса / 16 проверок | exit 0, «3 сервисов, 16 проверок, порт 8099, интервал 20 c», android assets OK | OK |
| Python-дефолты (без файла) = `config.json` | побайтово равны по canonical JSON | равны (unit-тест) | OK |
| Python читает все секции | port/interval/timeouts/пороги/history/labels/colors/display/notifications | все значения совпадают с файлом | OK |
| Python-сервисы = `config.json` | id/name/subtitle/accent/hints/checks(kind,host,port,path,expect,core,timeout) | полное совпадение (unit-тест) | OK |
| Приоритет конфига | `--config` > `NETMON_CONFIG` > файл рядом > дефолты | соответствует; missing-файл → ConfigError, exit 2 | OK |
| Валидация битого конфига | понятная ошибка + exit 2 | битый JSON, kind, дубли id сервиса/проверки, порт 0/70000, interval<5, цвета, expect, пустые services, uppercase id, неизвестный статус — все отловлены | OK |
| Android `assets/config.json` = корневой | байт-в-байт | tracked-файл идентичен; внутри APK идентичен | OK |
| Kotlin `AppConfig.DEFAULTS` = `config.json` | хосты/порты/таймауты/пороги/статусы/цвета/подсказки | совпадают (мой python-тест + 11 Kotlin `ConfigParityTest`) | OK |
| Android читает сервисы из assets | `context.assets.open(ConfigLoader.ASSET_NAME)` | так и есть; жёстко зашитых `http(s)://` в Kotlin нет | OK |
| Gradle копирует корневой конфиг | `syncConfigJson` из `../config.json`, preBuild/mergeAssets/Test | task присутствует, APK собирается с актуальным asset | OK |
| macOS-менюбар читает конфиг | `display.macos_menubar.*`, `server.port`, `status_labels`, `status_colors`, `NETMON_CONFIG` | читает (после `13f29b5`); проверено вживую: `--dump` с `NETMON_CONFIG` (port 8123) подключился к 8123 и показал кастомные подписи из конфига | OK |
| macOS-менюбар без чужих endpoint'ов | нет хостов сервисов | хостов нет | OK |
| Windows читает конфиг | `load_settings`, `--config`, port/interval/services/dot_pt/notifications | tray.py/entry.py используют settings; selftest показывает значения из конфига | OK |
| Windows-сборка упаковывает конфиг/статику | spec datas `static/` + `config.json` | присутствуют; `pystray._win32` hiddenimport | OK |
| Автозапуск Windows | HKCU `Run\NetMonitor` | install.ps1 создаёт, uninstall.ps1 удаляет | OK |

### B. Python: классификация, инциденты, снимок, журнал (unit, без сети)

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| Классификация: all ok / partial / slow / empty | ok / degraded / degraded / unknown | соответствует, `checks_ok/total` считаются по всем проверкам, latency — по успешным core | OK |
| tls_handshake_timeout, dns_error → blocked; connect_refused, http_status_unexpected → down | коды из контракта | соответствует (blocked только при живом интернете) | OK |
| internet down → все сервисы down | down с причиной «нет интернета» | соответствует | OK |
| non-core проверки не влияют на статус | статус по core | подтверждено | OK |
| Инциденты: 2 неудачи → open, успех → resolved | пороги из конфига | соответствует; `duration_sec`, `ended_at`, `failed_checks/total_checks` корректны | OK |
| Инциденты: active не дублируется, пороги/`max_stored` из конфига, unknown игнорируется | по конфигу | соответствует (2/1, 3-я неудача по настройке, лимит хранения) | OK |
| `snapshot()`: summary/services/series/uptime/recent | согласованность и границы | summary сходится с services; uptime 66,7 % на 2 ok / 1 fail; series кап 5; recent ≤ 5; active duration растёт | OK |
| `incidents_report`: q/service/status/hours/limit/offset | фильтры и пагинация | все фильтры, регистронезависимый поиск (вкл. кириллицу), страницы не пересекаются, offset за пределами пуст | OK |
| Legacy/битый `state.json` | старый формат читается, битый не мешает | 1 точка серии и active-инцидент восстановились; битый JSON и отсутствие каталога не роняют запуск | OK |
| `next_check_in` | 0…interval | во время цикла 0, после цикла 11,2→1,2 с при interval 20 | OK |

### C. Живой HTTP API (порт 8801, свой data-dir)

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `/api/health` | `ok:true`, poll_interval из конфига | 20 c, rounds растут, version растёт, uptime_sec | OK |
| `/api/state` | схема контракта, консистентность summary | все поля; `services_total=3`, `checks_*`, `overall`, `internet.status` ∈ enum; series/uptime/стадии/ошибки соответствуют | OK |
| Применение `config.json` | сервисы/интервал/порт | ids/names/checks = config; `server.port` без `--port` → сервер слушает порт из конфига; `--port` переопределяет | OK |
| `/api/incidents` | total/limit/offset, q/service/status/hours, paging | соответствует; `limit=abc`→100, `offset=abc`→0, offset=10000 → пусто | OK |
| `POST /api/check` | 200, версия растёт / `already_running` | 200, цикл завершился, version+1 | OK |
| `/api/export` | attachment + `net-monitor-report-*.json` | заголовок и тело `{exported_at,state,incidents}` | OK |
| 404 | JSON `{"error":"not_found"}` | `/api/nope`, `/nope.css`, `/server.py`, `/data/state.json`, `/../server.py`, POST — все 404 | OK |
| Статика | content-type | `/` html, `/app.js` javascript, `/style.css` css, `/favicon.svg` svg — 200 | OK |
| Персистентность | SIGTERM → рестарт сохраняет version/series/incidents | сохранилось, series восстановлены, инциденты те же | OK |

### D. Android

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `./gradlew test assembleDebug` (независимо от разработчика) | BUILD SUCCESSFUL | BUILD SUCCESSFUL (70 tasks) | OK |
| JUnit | без падений | 60 тестов × debug/release = 120, 0 failures (Classifier/StagedChecker/Incidents/Config/ConfigParity/Settings/SeriesUptime/Json) | OK |
| APK | существует, содержит assets/config.json | 8 910 333 Б, sha256 `f621a06c…`, `assets/config.json` байт-в-байт = корневому | OK |
| AndroidManifest | пакет, SDK, разрешения, сервис | `ru.netmon.android`, minSdk 26 / target 35, INTERNET/ACCESS_NETWORK_STATE/POST_NOTIFICATIONS/FOREGROUND_SERVICE(+DATA_SYNC), `MonitorService` foregroundServiceType=1 exported=false | OK |
| Подпись APK | debug | apksigner: CN=Android Debug | OK |
| Паритет конфига в Kotlin | хосты/числа/статусы/цвета/уведомления из конфига | совпадает (Kotlin-тесты + мой python-тест) | OK |
| CI | workflow собирает APK, тесты, проверяет asset, релиз с версионным именем | android.yml валиден; artifact `net-availability-monitor-android-debug`, на тег переименование в `net-availability-monitor-android-<tag>.apk` (совпадает с README) | OK |
| docs/android.md | есть, описывает сборку/APK/ограничения | есть, соответствует фактам | OK |

### E. Windows

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `windows/tests/test_windows.py` | 36 тестов OK | 36/36 OK | OK |
| `python3 windows/entry.py --selftest` | exit 0, «РЕЗУЛЬТАТ: OK» | exit 0 | OK |
| `python3 tray.py --selftest` | exit 0 | exit 0, «РЕЗУЛЬТАТ: OK» | OK |
| `py_compile` server/tray/entry | без ошибок | OK | OK |
| tray.py: конфиг, меню, уведомления | notifications.windows.enabled/notify_on, dot_pt, colors | использует настройки из конфига | OK |
| install.ps1 / uninstall.ps1 | HKCU Run, config.json.example, удаление | присутствует | OK |
| windows.yml | py_compile, tests, icon, PyInstaller, selftest, smoke /api/health+/api/state+дашборд, zip, release | соответствует docs/windows.md | OK |
| exe | собирается только в CI | локально не собирался (macOS) — ограничение | N/A |

### F. macOS

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `swiftc` сборка NetBar.swift | бинарник | собрался (198 840 Б), 1 warning без impact | OK |
| `install.sh` синтаксис | `bash -n` | OK | OK |
| install.sh копирует config в рантайм + NETMON_CONFIG | присутствует | `cp … config.json`, `<key>NETMON_CONFIG</key>` в plist | OK |
| install.sh берёт порт из config.json | порт из конфига, fallback 8099 | извлёк `SERVER_PORT=8123` из конфига; порт 70000 и битый JSON → 8099 | OK |
| NetBar читает порт/подписи/цвета | из config.json | `--dump` с port 8123 подключился к 8123 и напечатал кастомные подписи | OK |
| Менюбар режим/размеры | `display.macos_menubar.mode` + размеры | читает; режимы overlay/dot/text | OK |
| Установка в ~/Library | вне sandbox QA | переустанавливал Lead; QA не перезапускал — ограничение | N/A |

### G. Статика и API-контракт

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| `static/index.html, app.js, style.css, favicon.svg` | на месте, не пустые | есть; ссылки `/app.js`, `/style.css`, `/favicon.svg` | OK |
| Без внешних зависимостей | нет CDN | нет `http(s)://` в html/js/css | OK |
| API-контракт | все endpoint'ы и поля | `/api/state,/incidents,/check,/export,/health`, поля summary/services/series/uptime/incidents/paging описаны; сервер им соответствует (живые тесты) | OK |

### H. Гигиена, документация, CI

| Что проверялось | Ожидание | Факт | Статус |
|---|---|---|---|
| Git-артефакты | нет `.deploy/`, `data/`, `.toolchain/`, `.gradle/`, `.kotlin/`, `local.properties`, APK/exe/state.json | 97 tracked — чисто; запретные пути не отслеживаются | OK |
| Секреты | нет приватных ключей/токенов | `git grep` по PEM/ghp_/sk-/AKIA/xox — пусто; `.deploy/` (приватный deploy-ключ) в .gitignore | OK |
| Права файлов | gradlew/install.sh/build.sh/run.sh/check_config.py +x | 100755 в git | OK |
| `.gitignore` | покрывает данные/сборки/ключи | да (+ вложенный android/.gitignore) | OK |
| Лицензия | MIT | MIT License | OK |
| README.md/README.ru.md | 108-unit прогон, ссылки живые | `pytest -q tests` — 0 errors; все относительные ссылки существуют | OK |
| docs/windows.md, docs/android.md | не врут про артефакты/флаги | CLI-флаги, spec, zip/APK-имена, ограничения соответствуют | OK |
| CHANGELOG | 1.0.0 = VERSION = versionName | 1.0.0 | OK |
| Workflow-файлы | валидный YAML, покрывают 3 платформы | ci.yml + windows.yml + android.yml валидны; config job ставит pyyaml, Python-джоб гоняет test_unit.py, Swift-сборка, Windows exe + selftest + smoke, Android APK + asset-диff | OK |

---

## 4. Дефекты

### 4.1. Закрыты в `13f29b5` (найдены в `17920d3`)

| ID | Severity | Дефект | Как закрыт / проверка |
|---|---|---|---|
| **M-1** | **major** | CI-джоб `config` импортировал PyYAML без установки → красный CI на первом push | В ci.yml добавлен `python -m pip install --quiet pyyaml`; проверено в HEAD |
| **m-2** | minor | README обещал `pytest -q tests`, но было 20 errors (legacy `test_api.py`/`test_ui.py` требуют фикстуры) | `pytest.ini` + `conftest.py` с `collect_ignore`; `pytest -q tests` = 108 passed, 0 errors |
| **m-3** | minor | macOS игнорировал `server.port`: install.sh — `--port 8099`, NetBar — URL/spawn на 8099 | install.sh извлекает порт из конфига (fallback 8099); NetBar читает `server.port` и строит URL/spawn от него. Проверено вживую (port 8123) |
| **m-4** | minor | macOS-менюбар не читал `status_labels`/`status_colors` | NetBar читает и применяет; проверено: кастомные подписи видны в `--dump` |
| **m-5** | cosmetic | CHANGELOG `[0.1.0]` vs VERSION/versionName 1.0.0 | CHANGELOG → 1.0.0 |
| **m-6** | cosmetic | `classification.slow_latency_ms` поддержан только Python и не описан | добавлен в schema + architecture.md как опциональный (см. o-2 — формулировка неточна) |

### 4.2. Открытые (не блокируют публикацию)

| ID | Severity | Дефект | Где |
|---|---|---|---|
| **G-1** | **major (release gate)** | В закоммиченной версии `tests/test_unit.py` (HEAD `13f29b5`) остались assert'ы `"/api/state" in swift` и `"/api/check" in swift`, а после рефакторинга NetBar URL собираются через `appendingPathComponent("api/state")` → **CI-джоб `python` упадёт на 1 тесте**. В рабочем дереве QA тест уже исправлен (проверяет `api/state`, `api/check` и чтение `server`/`status_labels`/`status_colors`); 108 passed. | `tests/test_unit.py::test_netbar_reads_display_settings_from_config` |
| **o-1** | cosmetic | `docs/architecture.md`: в таблице «Платформа → как получает конфиг» две строки `macOS-менюбар` (старая и новая) — дубль после правки | `docs/architecture.md`, таблица строк 7–14 |
| **o-2** | minor | Неверная формулировка: в `docs/config.schema.json` (описание `classification.slow_latency_ms`) и `docs/architecture.md` сказано «Windows и Android используют 4000 мс». Windows запускает тот же `server.py` и **уважает** `classification.slow_latency_ms` (трей лишь отображает статусы сервера); жёстко 4000 мс только в Android (`Classifier.SLOW_CORE_THRESHOLD_MS`). | `docs/config.schema.json`, `docs/architecture.md` |
| **o-3** | cosmetic | `NetBar.swift:213`: сообщение `MONITOR UNAVAILABLE: http://127.0.0.1:8099/api/state недоступен` жёстко содержит 8099, хотя реально опрашивается порт из конфига (воспроизведено с port 8124). Функциональность не страдает, но текст вводит в заблуждение. | `menubar/NetBar.swift:213` |
| **o-4** | cosmetic | Границы `classification.slow_latency_ms`: schema `minimum: 1000`, server.py принимает 100…600000; `tools/check_config.py` (его гоняет CI) `classification` не валидирует вовсе. Сейчас значения совпадают (4000), но при ручном конфиге возможен рассинхрон «схема vs сервер». | `docs/config.schema.json`, `tools/check_config.py`, `server.py:617` |
| **o-5** | cosmetic | README badge ссылается на placeholder-org `net-availability-monitor/net-availability-monitor` — после создания репозитория slug нужно обновить (учтено в `docs/github-setup.md`). | `README.md:11` |
| **o-6** | cosmetic | `collect_ignore` продублирован в `pytest.ini` и `conftest.py` — избыточно, но безвредно. | `pytest.ini`, `conftest.py` |

**Шаги воспроизведения G-1:**

```bash
cd net-availability-monitor
python3 -m pytest -q tests/test_unit.py::test_netbar_reads_display_settings_from_config
# на 13f29b5: FAILED (assert '/api/check' in swift)
# после обновления tests/test_unit.py из рабочего дерева QA: PASSED
```

**Шаги воспроизведения o-2/o-3:**

```bash
grep -n "Windows и Android" docs/architecture.md docs/config.schema.json   # o-2
git show HEAD:menubar/NetBar.swift | sed -n 211,215p                       # o-3
# o-3 вживую: config с server.port=8124 и недоступным сервером:
NETMON_CONFIG=/tmp/config-8124.json /tmp/qa-swift-final/NetBar --dump
# → "MONITOR UNAVAILABLE: http://127.0.0.1:8099/api/state недоступен"
```

---

## 5. Покрытие и ограничения (честно о непокрытом)

**Проверено интеграционно:** Python-сервер (живой API, рестарт, персистентность, legacy state),
Windows-слой (36 тестов + selftest), Android (реальная Gradle-сборка, JUnit, APK, манифест, подпись,
asset-паритет), macOS (Swift-сборка, install.sh-логика извлечения порта, NetBar `--dump` с конфигом),
конфиг-паритет Python↔Kotlin↔Swift↔Windows, git-гигиена, CI-workflow (статическая проверка YAML).

**НЕ проверено (и почему):**
- **Windows GUI и `.exe`:** нет Windows-машины; PyInstaller-сборка/трей-иконка/меню/уведомления/
  HKCU-Run проверяются только в `windows.yml` на `windows-latest`. Локально проверены только
  Python-логика, spec, selftest и workflow.
- **Android UI на устройстве/эмуляторе:** эмулятора нет; проверены сборка, JUnit-движок, манифест,
  APK. Compose-UI, реальные сетевые проверки с телефона, foreground-уведомления и ограничения
  `dataSync` вживую не наблюдались.
- **macOS-установка глазами пользователя:** `install.sh` пишет в `~/Applications`, `~/Library`,
  `launchctl` — вне sandbox QA; переустановку делал Lead (сервер health OK, история сохранена).
  QA проверил логику порта/режима и сборку Swift, но не визуальный overlay-точkу и поведение
  LaunchAgents после логина.
- **Реальный CI-прогон:** workflow-файлы проверены статически (YAML + состав шагов), запуска на
  GitHub Actions в этой сессии не было.
- **UI дашборда в браузере:** фронтенд не менялся после принятого `QA-REPORT` (docs/qa-report-macos.md);
  QA проверил отдачу статики/контракт, но не повторял Chrome/CDP-сценарии.
- **Длительный soak/нагрузка:** не гонялись (память, CPU, поведение за сутки, 8640 точек истории).
- **Release-подпись Android и code-signing macOS/Windows:** не проверялись (debug-подпись и
  unsigned-сборки заявлены в доках как ограничение).
- **Живая сеть:** статусы `youtube/telegram = blocked` на машине QA — проверялась консистентность
  данных, а не конкретные сетевые значения.

---

## 6. Как воспроизвести

```bash
cd net-availability-monitor

# 0) временное окружение (pytest/PyYAML/jsonschema)
python3 -m venv /tmp/qa-venv && /tmp/qa-venv/bin/pip install -q pytest pyyaml jsonschema

# 1) герметичные unit-тесты (как в CI)
/tmp/qa-venv/bin/python -m pytest -q tests/test_unit.py          # 108 passed
/tmp/qa-venv/bin/python -m pytest -q tests                       # 108 passed, 10 skipped, 0 errors

# 2) живые API-тесты (своя сеть, свой data-dir, порт 8801)
QA_LIVE=1 QA_PORT=8801 /tmp/qa-venv/bin/python -m pytest -q tests/test_live_acceptance.py

# 3) Windows-слой
python3 windows/tests/test_windows.py                            # 36 OK
python3 tray.py --selftest && python3 windows/entry.py --selftest
python3 -m py_compile server.py tray.py windows/entry.py

# 4) Android (тулчейн из .toolchain, как в docs/android.md)
export JAVA_HOME="$PWD/.toolchain/jdk17" ANDROID_HOME="$PWD/.toolchain/android-sdk"
export ANDROID_SDK_ROOT="$ANDROID_HOME" GRADLE_USER_HOME="$PWD/.toolchain/gradle-home"
cd android && ./gradlew --no-daemon test assembleDebug && cd ..
unzip -p android/app/build/outputs/apk/debug/app-debug.apk assets/config.json | diff - config.json

# 5) macOS Swift
swiftc -swift-version 5 -O -framework Cocoa -o /tmp/NetBar menubar/NetBar.swift

# 6) конфиг и гигиена
python3 tools/check_config.py
bash -n install.sh
git ls-files | grep -Ei '\.(apk|aab|exe|pem|keystore|jks)$|^\.deploy/|^data/|^\.toolchain/' && echo FAIL || echo "git hygiene OK"
```

---

## 7. Артефакты и логи

| Артефакт | Путь |
|---|---|
| Герметичные тесты | [`tests/test_unit.py`](tests/test_unit.py) — 108 проверок |
| Живые приёмочные тесты | [`tests/test_live_acceptance.py`](tests/test_live_acceptance.py) — 10 проверок, `QA_LIVE=1` |
| Android APK | `android/app/build/outputs/apk/debug/app-debug.apk` (8 910 333 Б, sha256 `f621a06cad4fa604f77ce6980e464bcca3a9fba5e65c12315fbaf6a63087991d`) |
| Android test-results (XML) | `android/app/build/test-results/{testDebugUnitTest,testReleaseUnitTest}/` (120 записей, 0 failures) |
| Swift-бинарник QA-сборки | `/tmp/qa-swift-final/NetBar` |
| Лог Gradle-сборки QA | `/tmp/qa-android-build.log` |
| Логи живого сервера QA | `/tmp/qa-8797/server.log` (сеть blocked/ok), `/tmp/qa-8123/server.log` (port 8123) |
| Временное окружение | `/tmp/qa-venv` (pytest 8.4.2 + pyyaml + jsonschema) |
| Первичный macOS-отчёт | `docs/qa-report-macos.md` (API/инциденты/UI; фронтенд с тех пор не менялся) |

---

## 8. Чек-лист перед пушем

1. **Закоммитить `tests/test_unit.py`** (исправленные assert'ы NetBar) — иначе CI `python` красный (**G-1**).
2. Закоммитить этот `QA-REPORT-FINAL.md`.
3. По желанию (не блокирует): исправить дубль строки в `docs/architecture.md` (**o-1**) и формулировку
   «Windows … 4000 мс» (**o-2**), порт в сообщении `NetBar.swift:213` (**o-3**), границы
   `classification.slow_latency_ms`/валидацию в `tools/check_config.py` (**o-4**).
4. После создания GitHub-репозитория обновить slug в README-badge (**o-5**) — уже предусмотрено в
   `docs/github-setup.md`.
5. Прогнать CI (ci.yml / windows.yml / android.yml) на push — это единственное непокрытое локально
   звено (Windows exe/APK-артефакты/релизы).

---

## 9. Итоговый вердикт

**PASS WITH ISSUES.** Функциональных блокеров нет: все платформы собираются, конфиг-паритет
подтверждён в четырёх местах, API соответствует контракту, инциденты/история/персистентность
работают, git чист от секретов и артефактов. Все 6 дефектов первой итерации закрыты в `13f29b5`
и перепроверены. Остались cosmetic/minor замечания к документации и одно сообщение NetBar; плюс
release-gate **G-1** — закоммитить исправленный `tests/test_unit.py`, без этого CI на `13f29b5`
не зелёный.
