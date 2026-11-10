package ru.netmon.android

import android.content.Context
import android.util.Log
import ru.netmon.core.AppConfig
import ru.netmon.core.ConfigLoader
import ru.netmon.core.ConfigParser

/**
 * Загрузка настроек из `assets/config.json`.
 *
 * Файл в assets не пишется руками: Gradle-таск `syncConfigJson`
 * копирует туда корневой `config.json` репозитория перед каждой сборкой.
 * Поэтому сервисы/эндпоинты/таймауты/пороги/статусы/цвета/подсказки
 * на Android ровно те же, что у серверной и macOS-версии.
 */
class ConfigRepository(private val context: Context) {

    fun load(): AppConfig {
        return try {
            context.assets.open(ConfigLoader.ASSET_NAME).use { stream ->
                ConfigParser.parse(stream.readBytes().toString(Charsets.UTF_8))
            }
        } catch (error: Exception) {
            Log.w(TAG, "Не удалось прочитать assets/${ConfigLoader.ASSET_NAME}, беру дефолты", error)
            AppConfig.DEFAULTS
        }
    }

    private companion object {
        const val TAG = "NetMonConfig"
    }
}
