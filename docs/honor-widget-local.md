# Локальная сборка виджета с ресурсами HONOR

Это **личная локальная сборка** (APK лежит в `local-artifacts/`, каталог в `.gitignore`).
Проприетарные шрифты и иконки HONOR **не публикуются** в GitHub — в репозитории остаётся
только код с fallback-ресурсами.

## Что используется

- часы: `Honor-digit-light.otf` (из `/system/fonts` телефона);
- остальной текст: `HONORSansVFNormal.ttf`;
- иконки погоды: drawable `bg_remote_views_sun/cloud/cloud_dark/rain/snow/fog_haze/star`
  из `HnWeatherClock.apk` (`com.hihonor.android.totemweather`), извлечены локально;
- layout повторяет дизайн-код оригинала: прозрачная карточка 4×2, паддинги **16/12 dp**,
  часы **50 dp** тонким шрифтом, дата с отступом **2 dp**, город/погода справа.

## Как собрано

Ассеты лежат в `android/app/src/localHonor/res/` (в `.gitignore`). Gradle подключает
каталог к `main` source set, только если он существует:

```kotlin
val localHonorRes = file("src/localHonor/res")
if (localHonorRes.isDirectory) sourceSets["main"].res.srcDir(localHonorRes)
```

Код выбирает layout/иконки в рантайме через `resources.getIdentifier(...)`:
если локальных ресурсов нет (CI, GitHub-сборка), используется обычный layout и
векторные иконки из `main/res`.

Сборка и установка:

```bash
cd android
./gradlew assembleRelease
adb install -r app/build/outputs/apk/release/app-release.apk
# город для погоды (можно и в настройках приложения):
adb shell "am start -n ru.netmon.android/.MainActivity --es widget_city 'Лесные Поляны'"
```

## Погода

Данные — Open-Meteo (температура/код погоды), город задаётся вручную в настройках
приложения (кнопка «Сохранить город»), иначе определяется по IP. Системный погодный
провайдер HONOR приватный и не вызывается; иконки/типографика при этом — из HONOR.

## Ограничения

- Это не побайтовая копия: RemoteViews не позволяет повторить все эффекты оригинала,
  а шрифт/иконки используются локально.
- Не коммитьте `android/app/src/localHonor/` и `local-artifacts/` — они в `.gitignore`.

## Уточнённые параметры (по ресурсам HONOR)

| Элемент | Значение |
|---|---|
| Часы | `Honor-digit-light.otf`, **50 dp**, light |
| Дата | HONOR Sans, **14 dp**, weight 500 (medium) |
| Город | HONOR Sans, **12 dp**, weight 500 |
| Температура | HONOR Sans, **14 dp**, weight 500 |
| Состояние | HONOR Sans, **12 dp**, regular |
| Иконка погоды | **40 dp** (`bg_remote_views_*`) |
| Паддинги карточки | **16 dp / 12 dp** |
| minHeight виджета | **100 dp** (как у HONOR) |
| Клик по погоде | `com.hihonor.android.totemweather/.WeatherHome` |

Локальный APK: `local-artifacts/netmon-honor-local-1.4.0.apk`.
