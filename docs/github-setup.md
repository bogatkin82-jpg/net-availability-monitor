# Публикация на GitHub

Репозиторий уже подготовлен локально (git, ветка `main`), SSH-ключ для деплоя сгенерирован.
Нужно три шага в браузере — после них Lead запушит код и включит CI.

## 1. Создать пустой репозиторий

1. Откройте <https://github.com/new>.
2. **Repository name:** `net-availability-monitor`
3. **Visibility:** Public.
4. Не добавляйте README, .gitignore и лицензию (они уже есть в проекте).
5. Нажмите **Create repository**.

## 2. Добавить deploy-ключ с правом записи

1. В новом репозитории: **Settings → Deploy keys → Add deploy key**.
2. **Title:** `netmon-deploy-2026`
3. **Key:** вставьте публичный ключ:

```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIABoQf5CVmjucs4QS9zGiC3iIUBQbkurZTUMlsKX2bst netmon-deploy-2026
```

4. Обязательно поставьте галочку **Allow write access**.
5. **Add key**.

## 3. Сообщить Lead

Напишите в чат: «репозиторий создан: <URL>». После этого Lead:

- добавит remote и запушит `main`;
- проверит, что GitHub Actions прошли (CI, Windows exe, Android APK);
- снимет финальные артефакты и обновит README/Release.

## Что произойдёт дальше (автоматически)

- `.github/workflows/ci.yml` — валидация конфига, Python-тесты, сборка Swift-индикатора,
  проверка гигиены git;
- `.github/workflows/windows.yml` — сборка `NetMonitor-windows-x64.zip` на Windows-раннере;
- `.github/workflows/android.yml` — тесты и `app-debug.apk`.

## Если нужен HTTPS вместо SSH

Можно использовать Personal Access Token (classic, scope `repo`) и remote
`https://github.com/<owner>/net-availability-monitor.git`. В этом случае ключ не нужен,
но токен не должен попадать в чат/лог — лучше выполнить пуш вручную.
