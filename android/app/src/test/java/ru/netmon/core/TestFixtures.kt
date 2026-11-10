package ru.netmon.core

import java.io.File

/** Общие фикстуры unit-тестов: конфиг из репозитория и фабрики результатов. */
object TestFixtures {

    /**
     * Корневой config.json (Gradle передаёт путь через system property).
     * Резервно — относительный путь от модуля app/.
     */
    fun repoConfigFile(): File {
        val fromProperty = System.getProperty("repo.config.path")
        val candidates = buildList {
            if (!fromProperty.isNullOrBlank()) add(File(fromProperty))
            add(File("../config.json"))
            add(File("config.json"))
        }
        return candidates.firstOrNull { it.isFile }
            ?: error("Не найден config.json; проверены: ${candidates.joinToString(", ")}")
    }

    fun assetsConfigFile(): File? {
        val fromProperty = System.getProperty("assets.config.path")
        val candidates = buildList {
            if (!fromProperty.isNullOrBlank()) add(File(fromProperty))
            add(File("src/main/assets/config.json"))
        }
        return candidates.firstOrNull { it.isFile }
    }

    fun repoConfig(): AppConfig = ConfigParser.parse(repoConfigFile().readText())

    fun checkResult(
        spec: CheckSpec,
        ok: Boolean,
        latencyMs: Int = 50,
        error: String? = null,
        stage: String = if (ok) CheckStage.OK else CheckStage.TLS,
    ): CheckResult = CheckResult(
        id = spec.id,
        label = spec.label,
        target = spec.target(),
        kind = spec.kind,
        core = spec.core,
        ok = ok,
        latencyMs = latencyMs,
        stage = stage,
        error = if (ok) null else error,
        errorText = if (ok) null else "тестовая ошибка",
        timings = StageTimings(
            dnsMs = 5,
            tcpMs = if (spec.kind == CheckKind.DNS) null else 10,
            tlsMs = if (spec.kind == CheckKind.HTTPS || spec.kind == CheckKind.TLS) 20 else null,
            httpMs = if (spec.kind == CheckKind.HTTPS) 25 else null,
        ),
        measuredAt = "2026-01-01T00:00:00+03:00",
    )

    /**
     * Раунд проверок: сервис ok, если передан true; иначе падают все core-проверки
     * с заданным кодом ошибки (не-core остаются успешными, как чинит реальный движок).
     */
    fun round(
        config: AppConfig,
        okByService: Map<String, Boolean> = emptyMap(),
        errorCode: String = ErrorCodes.CONNECT_REFUSED,
        latencyByService: Map<String, Int> = emptyMap(),
    ): Map<String, List<CheckResult>> = config.services.associate { service ->
        val serviceOk = okByService[service.id] ?: true
        val latency = latencyByService[service.id] ?: 50
        service.id to service.checks.map { spec ->
            val checkOk = serviceOk || !spec.core
            checkResult(
                spec = spec,
                ok = checkOk,
                latencyMs = latency,
                error = if (checkOk) null else errorCode,
                stage = if (checkOk) CheckStage.OK else stageFor(errorCode),
            )
        }
    }

    fun stageFor(errorCode: String): String = when (errorCode) {
        ErrorCodes.DNS_ERROR, ErrorCodes.DNS_TIMEOUT -> CheckStage.DNS
        ErrorCodes.CONNECT_TIMEOUT, ErrorCodes.CONNECT_REFUSED, ErrorCodes.CONNECT_UNREACHABLE -> CheckStage.TCP
        ErrorCodes.TLS_HANDSHAKE_TIMEOUT, ErrorCodes.TLS_RESET, ErrorCodes.TLS_CERT_ERROR, ErrorCodes.TLS_ERROR -> CheckStage.TLS
        ErrorCodes.READ_TIMEOUT, ErrorCodes.HTTP_ERROR, ErrorCodes.HTTP_STATUS_UNEXPECTED -> CheckStage.HTTP
        else -> CheckStage.UNKNOWN
    }
}
