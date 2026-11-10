# iOS-приложение «Мониторинг сети» (NetMonitor)

Нативное iOS-приложение (SwiftUI, iOS 17+) сетевого мониторинга: проверяет
доступность YouTube / Telegram / интернета, классифицирует состояние
(`ok / degraded / blocked / down / unknown`), ведёт историю, открывает
инциденты и присылает локальные уведомления о сбоях.

Идейно это порт Python-сервера (`server.py`) и Android-клиента (`android/`):
те же проверки, те же пороги, те же подписи статусов и цветá. Единственный
источник истины для настроек — корневой `config.json` репозитория.

> Приложение пока **не собрано в CI** — первый запуск workflow `iOS build`
> и есть проверка сборки. Локально на машине без Xcode собрать нельзя
> (см. «Ограничения»).

---

## 1. Требования

| Что | Версия / примечание |
|---|---|
| Xcode | **16.x** (iOS 17+ SDK). На машине должен быть установлен полноценный Xcode, а не только Command Line Tools |
| XcodeGen | **≥ 2.38** (`brew install xcodegen`) — проект генерируется из `project.yml` |
| iOS | 17.0+ (симулятор или устройство) |
| SPM-зависимости | **нет** — только системные `Foundation`, `SwiftUI`, `Network`, `UserNotifications`, `XCTest` |
| Устройство | для установки на iPhone нужен Apple Developer Account и подпись |

Проверка тулчейна:

```bash
xcodebuild -version          # Xcode 16.x
xcodegen --version           # 2.38+
xcrun simctl list runtimes   # есть iOS 17+ runtime
```

---

## 2. Структура

```
ios/
├── project.yml                 # спецификация XcodeGen (app + tests + схема)
├── Info.plist                  # fallback; XcodeGen перегенерирует из project.yml
├── Resources/
│   ├── config.json             # копия корневого config.json (один-в-один)
│   └── Assets.xcassets/        # только AccentColor, без растровых ассетов
├── Sources/
│   ├── NetMonApp.swift         # @main, WindowGroup
│   ├── Core/
│   │   ├── Models.swift        # конфиг, CheckSpec/CheckResult, ErrorCodes, дефолты
│   │   ├── ConfigLoader.swift  # разбор config.json с дефолтами
│   │   ├── Classifier.swift    # классификация сервиса (порт _classify)
│   │   ├── MonitorState.swift  # серии, uptime, инциденты, события, персистентность
│   │   └── UserSettings.swift  # настройки поверх config.json + UserDefaults
│   ├── Net/
│   │   └── StagedChecker.swift # DNS → TCP → TLS → HTTPS, тайминги этапов
│   ├── Store/
│   │   ├── MonitorStore.swift  # ObservableObject: таймер, опросы, уведомления
│   │   └── NotificationManager.swift
│   └── UI/
│       ├── RootView.swift      # TabView: Статус / Журнал / Настройки
│       ├── StatusScreen.swift  # плашка, карточки, раскрываемые проверки
│       ├── JournalScreen.swift # инциденты + события, поиск, фильтр
│       ├── SettingsScreen.swift
│       ├── Components.swift    # бейджи, карточки, полоска серии, строки проверок
│       ├── Theme.swift         # цвета из config.json, SF Symbols
│       └── Format.swift        # русские форматы дат/длительностей
└── Tests/                      # XCTest: классификатор, конфиг, инциденты, настройки
```

Bundle id: `ru.netmon.ios`, display name: `NetMonitor`, iPhone-only, портрет.

---

## 3. Генерация и сборка

```bash
cd ios
xcodegen generate           # создаёт NetMon.xcodeproj из project.yml
open NetMon.xcodeproj       # Xcode 16 → выбрать симулятор iPhone (iOS 17+) → Run
```

Сборка из терминала (как в CI — без подписи):

```bash
cd ios
xcodebuild \
  -project NetMon.xcodeproj \
  -scheme NetMon \
  -configuration Debug \
  -sdk iphonesimulator \
  -destination "generic/platform=iOS Simulator" \
  -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO \
  build
```

Готовый бандл: `ios/build/Build/Products/Debug-iphonesimulator/NetMon.app`.

Тесты:

```bash
cd ios
UDID="$(xcrun simctl list devices available | sed -n 's/.*iPhone[^(]*(\([0-9A-Fa-f-]*\)).*/\1/p' | head -1)"
xcodebuild test \
  -project NetMon.xcodeproj \
  -scheme NetMon \
  -destination "platform=iOS Simulator,id=$UDID" \
  -derivedDataPath build \
  CODE_SIGNING_ALLOWED=NO
```

`NetMon.xcodeproj`, `ios/build/` и `ios/.build/` — генерируемые артефакты,
в git их держать не нужно (см. `ios/.gitignore`).

---

## 4. Конфигурация (`config.json`)

`ios/Resources/config.json` — **побайтовая копия** корневого `config.json`.
При сборке файл копируется в бандл приложения, приложение читает его через
`ConfigLoader.load()` (если файла нет — используются встроенные дефолты
`AppConfig.defaults`, которые обязаны совпадать с конфигом).

Проверка и синхронизация:

```bash
python3 tools/check_config.py          # проверяет и паритет android/ios с корневым config.json
python3 tools/check_config.py --sync   # обновляет android/app/src/main/assets и ios/Resources/config.json
```

Что читается из конфига:

| Секция | Использование |
|---|---|
| `server` | справочно (приложение опрашивает сеть напрямую, а не HTTP API сервера) |
| `polling.interval_sec` | интервал автоопроса, минимум 5 с |
| `polling.check_timeout_sec` | общий таймаут проверки (переопределяет per-check `timeout`) |
| `polling.dns_timeout_sec` | таймаут DNS-этапа |
| `incidents.open_after_failures` / `close_after_successes` / `max_stored` | пороги инцидентов и размер журнала |
| `history.max_age_hours` / `max_points` / `series_points` | хранение серий и полоска истории |
| `network.proxy` | **только справочно**: на iOS не применяется (см. «Ограничения») |
| `status_labels`, `status_colors` | подписи и цвета статусов в UI |
| `services[].checks[]` | сами проверки: `dns / tcp / tls / https` |
| `services[].hints` | подсказки в карточке сервиса |
| `notifications` | `enabled`, `notify_on`; сначала ищется блок `notifications.ios`, если его нет — `notifications.android` (общий мобильный блок) |

Правки в экране «Настройки» накладываются поверх конфига и сохраняются в
`UserDefaults` (ключ `netmon.settings.v1`); кнопка «Сбросить настройки к
config.json» возвращает значения из файла. История/серии/инциденты хранятся
в `UserDefaults` под ключом `netmon.state.v1`.

---

## 5. Движок проверок

Аналог `run_check()` из `server.py`: стадии **DNS → TCP → TLS → HTTP** с
замерами в миллисекундах.

| Этап | Реализация |
|---|---|
| DNS | `getaddrinfo` (на global-очереди, с таймером на `dns_timeout_sec`); берётся первый адрес |
| TCP | `NWConnection` (Network.framework), успех — состояние `.ready` |
| TLS | `NWConnection` с `NWProtocolTLS.Options()` (SNI берётся из host), успех — `.ready`; проверка сертификата — системная |
| HTTPS | `tls`-проверки как выше; `https`-проверки — `URLSession` (ephemeral, без кеша/куки, `timeoutInterval` = остаток бюджета) с проверкой `expect` |

Особенности:

* `timeout` каждой проверки — из `services[].checks[].timeout` (или общий
  `check_timeout_sec`, если пользователь его менял); стадии получают остаток
  бюджета, поздние стадии — минимум 0.5 с.
* `https`-проверка дополнительно делает TCP-прогрев через `NWConnection`,
  чтобы отличить «TCP не проходит» от «TLS/HTTP не проходит» (как в Python).
* Все проверки раунда идут параллельно в `TaskGroup` (16 задач — как
  `ThreadPoolExecutor` в server.py).
* Редиректы `URLSession` не отключаются (делегата нет): `expect` проверяется
  по итоговому ответу. Для всех эндпоинтов из config.json итоговые коды входят
  в `expect`.
* Ошибки маппятся в те же коды, что в Python: `dns_error`, `dns_timeout`,
  `connect_timeout`, `connect_refused`, `connect_unreachable`,
  `tls_handshake_timeout`, `tls_reset`, `tls_cert_error`, `tls_error`,
  `read_timeout`, `http_status_unexpected`, `http_error`, `unknown_error`.

---

## 6. Классификация и инциденты

Правила — построчный порт `Monitor._classify()`:

* все **core**-проверки `ok` → `ok`; если максимальная задержка core ≥ 4000 мс → `degraded`;
* часть core-проверок упала → `degraded`;
* все core упали, ошибки только DNS → `blocked`;
* все core упали, ошибки из «блокирующего» набора (`connect_timeout`,
  `connect_unreachable`, `tls_handshake_timeout`, `tls_reset`, `tls_error`,
  `tls_cert_error`, `read_timeout`) → `blocked`;
* интернет-канал (`internet`) `down` → сервисы классифицируются как `down`;
* иначе → `down`; до первого опроса → `unknown`.

Контрольный канал — сервис `internet` (если его нет в конфиге, сервисы
классифицируются без привязки к интернету). Инцидент открывается после
`open_after_failures` сбоев подряд и закрывается после
`close_after_successes` успехов подряд; журнал ограничен `max_stored`.
Каждая смена статуса сервиса дополнительно пишется событием в «Журнал».

---

## 7. Уведомления

Используется `UNUserNotificationCenter`:

* разрешение запрашивается при первом запуске (если уведомления включены) и
  по кнопке «Запросить разрешение»;
* уведомление отправляется **только при переходе** статуса сервиса в один из
  `notify_on` (по умолчанию `blocked` и `down`), а не каждый раунд;
* текст — название сервиса, статус, причина и подсказка из конфига.

---

## 8. Ограничения iOS

1. **Нет фонового опроса как на Android.** iOS не даёт держать foreground
   service: таймер работает, пока приложение активно; при уходе в фон он
   останавливается, и при возврате в foreground опрос запускается заново
   (если данные устарели — сразу). Background fetch/BGTask не используются,
   чтобы не требовать дополнительных разрешений и не ломать предсказуемость.
2. **Уведомления приходят только при активном приложении** (или сразу после
   возврата в foreground) — следствие п. 1.
3. **Установка на устройство требует Apple Developer Account и подписи.**
   CI собирает только симуляторную сборку без подписи (`CODE_SIGNING_ALLOWED=NO`).
4. **`network.proxy` игнорируется**: на iOS соединения идут через системный
   VPN/прокси, отдельный прокси-туннель из конфига не реализуется.
5. HTTPS-проверки выполняются `URLSession`, а не «сырым» HTTP по сокету —
   тайминги TLS-стадии для `https`-проверок не измеряются отдельно (только
   `dns_ms` и `http_ms`), для `tls`-проверок измеряются `tcp_ms`/`tls_ms`.
6. Приложение не обращается к HTTP API Python-сервера: проверки выполняются
   самим приложением (как в Android-версии), поэтому `server` из конфига —
   справочное поле.

---

## 9. Подпись и TestFlight

Сборка для симулятора подписи не требует. Для устройства:

1. В Xcode открыть `NetMon.xcodeproj` → target **NetMon** → вкладка
   **Signing & Capabilities**.
2. Включить **Automatically manage signing**, выбрать свой **Team**
   (Apple ID с ролью в команде).
3. При необходимости поменять `PRODUCT_BUNDLE_IDENTIFIER` (`ru.netmon.ios`),
   если он уже занят в вашем аккаунте.
4. Собрать на подключённый iPhone: `xcodebuild -project NetMon.xcodeproj
   -scheme NetMon -destination "generic/platform=iOS" DEVELOPMENT_TEAM=<TEAM_ID>
   -allowProvisioningUpdates build` (или просто Run в Xcode).

Team ID можно прописать в `ios/project.yml`:

```yaml
settings:
  base:
    DEVELOPMENT_TEAM: ABCDE12345
```

или не хранить в репозитории и передавать через `DEVELOPMENT_TEAM=...` в
`xcodebuild`. В CI подписи нет — она не нужна для симуляторной сборки.

**TestFlight / App Store** — только при наличии Apple Developer Program
(платный аккаунт): нужен Archive (`Product → Archive`), App Store Connect
record и загрузка билда (Xcode Organizer или `xcodebuild -exportArchive`).
В текущем CI этого шага нет.

---

## 10. CI

Workflow `.github/workflows/ios.yml` (macOS-раннер):

1. `brew install xcodegen`;
2. `xcodegen generate` в `ios/`;
3. `xcodebuild build` для iOS Simulator без подписи;
4. `xcodebuild test` на ближайшем доступном iPhone-симуляторе;
5. упаковка `NetMon.app` в `dist/NetMon-ios-simulator.zip` и загрузка
   артефактом `NetMon-ios-simulator`;
6. на тегах `v*`/nightly — попытка GitHub Release.

Если шаг релиза не находит файл в `dist/`, проверьте, что имя zip в шаге
публикации совпадает с именем, которое создаёт шаг упаковки.

---

## 11. Тесты

`ios/Tests` — только unit-тесты, **без сетевых вызовов** (проверки подменены
фикстурами):

| Файл | Что проверяет |
|---|---|
| `ConfigTests` | паритет встроенных дефолтов и `Resources/config.json`, структура 3 сервисов / 16 проверок, подписи и цвета, разбор кастомного JSON, игнор неизвестных `kind` |
| `ClassifierTests` | правила `ok/degraded/blocked/down/unknown`, медленный ответ ≥ 4000 мс, DNS-only → blocked, блокирующие ошибки, контрольный канал |
| `IncidentsTests` | открытие/закрытие инцидентов по порогам, `max_stored`, события смены статуса, uptime, лимиты серий, сводный статус, persistence, reset |
| `SettingsTests` | дефолты из конфига, ограничения значений, эффективный конфиг, `check_timeout` override, UserDefaults |

Тесты хостятся в приложении (`TEST_HOST`), поэтому `Bundle.main` в тестах —
бандл `NetMon.app`, и `config.json` доступен.

---

## 12. Частые проблемы

| Симптом | Причина / решение |
|---|---|
| `xcodegen: command not found` | `brew install xcodegen` |
| `xcodebuild: error: SDK "iphonesimulator" cannot be located` | установлен только Command Line Tools — нужен полноценный Xcode 16 (`xcode-select -s /Applications/Xcode.app`) |
| В тестах `config.json` не найден, статусы «Нет данных» | `Resources/config.json` не попал в бандл: проверьте `ios/Resources/config.json` и `resources: [Resources]` в `project.yml`, затем `xcodegen generate` заново |
| «Untrusted Developer» на устройстве | доверить разработчика в `Настройки → Основные → VPN и управление устройством` |
| Уведомления не приходят | проверьте разрешение в системных настройках; на iOS они приходят только при активном приложении |
| Статусы «Похоже на блокировку» при рабочем интернете | так и задумано для фильтрации TCP/TLS; сравните с VPN, как и на других платформах |
