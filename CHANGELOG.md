# Changelog

## [0.1.0] — 2026-11-10

Первый публичный релиз.

### Добавлено

- Python-бэкенд с постадийными проверками DNS → TCP → TLS → HTTP, классификацией
  `ok/degraded/blocked/down`, журналом инцидентов, историей и JSON API.
- Дашборд (`static/`) с карточками сервисов, sparkline, журналом сбоев с поиском
  и экспортом.
- macOS: индикатор в меню-баре (Swift/AppKit) в режимах overlay/dot/text,
  установщик с LaunchAgents и автозапуском.
- Windows: трей-индикатор, единый `.exe` (PyInstaller), установщик с HKCU Run.
- Android: нативное приложение Kotlin/Compose со своим движком проверок,
  настройками и foreground-уведомлениями.
- Единый `config.json` и `tools/check_config.py` для валидации и паритета настроек.
- CI: конфиг, Python-тесты, сборка macOS-индикатора, Windows `.exe`, Android APK.
