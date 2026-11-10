package ru.netmon.core

import kotlin.math.roundToInt

/** Результат классификации сервиса (одна карточка на дашборде). */
data class ServiceInfo(
    val status: Status,
    val label: String,
    val reason: String,
    val hint: String?,
    val latencyMs: Int?,
    val checksOk: Int,
    val checksTotal: Int,
)

/**
 * Классификация состояния сервиса — построчный порт `Monitor._classify()` из server.py.
 *
 * Правила (паритет с десктопом):
 *  * все core-проверки ok -> ok; если максимальная задержка core >= 4000 мс -> degraded;
 *  * часть core ok -> degraded (кроме контрольного канала «интернет»: там тоже degraded);
 *  * все core fail, но интернет «down» -> down;
 *  * все core fail, ошибки только DNS -> blocked;
 *  * все core fail, ошибки из BLOCK_ERRORS -> blocked;
 *  * иначе -> down;
 *  * нет проверок -> unknown.
 *
 * Подписи/подсказки берутся из config.json (status_labels/hints), цвета — status_colors.
 * Для контрольного канала («internet») подписи те же, что на дашборде:
 * «Интернет работает» / «Интернет нестабилен» / «Нет интернета».
 */
object Classifier {

    const val SLOW_CORE_THRESHOLD_MS: Int = 4000

    private val BASELINE_LABELS: Map<Status, String> = mapOf(
        Status.OK to "Интернет работает",
        Status.DEGRADED to "Интернет нестабилен",
        Status.DOWN to "Нет интернета",
    )

    fun classify(
        config: AppConfig,
        service: ServiceConfig,
        checks: List<CheckResult>,
        internetStatus: Status,
    ): ServiceInfo {
        val baseline = service.id == AppConfig.BASELINE_SERVICE_ID
        val core = checks.filter { it.core }.ifEmpty { checks }
        val total = core.size
        val ok = core.filter { it.ok }
        val bad = core.filterNot { it.ok }

        // В карточку показываем все проверки, а не только core (как на дашборде).
        val allTotal = checks.size
        val allOk = checks.count { it.ok }

        // «Отклик» — только по успешным core-проверкам.
        val coreLatencies = ok.map { it.latencyMs }
        val latency = if (coreLatencies.isNotEmpty()) {
            (coreLatencies.sum().toDouble() / coreLatencies.size).roundToInt()
        } else {
            null
        }
        val maxLatency = coreLatencies.maxOrNull()

        fun label(status: Status): String =
            if (baseline) BASELINE_LABELS[status] ?: config.labelFor(status.id) else config.labelFor(status.id)

        fun result(
            status: Status,
            reason: String,
            hint: String?,
            latencyValue: Int? = latency,
            okCount: Int = allOk,
            totalCount: Int = allTotal,
        ): ServiceInfo = ServiceInfo(
            status = status,
            label = label(status),
            reason = reason,
            hint = hint,
            latencyMs = latencyValue,
            checksOk = okCount,
            checksTotal = totalCount,
        )

        if (total == 0) {
            return result(
                status = Status.UNKNOWN,
                reason = "Проверки ещё не выполнялись",
                hint = null,
                latencyValue = null,
                okCount = 0,
                totalCount = 0,
            )
        }

        if (ok.size == total) {
            if (maxLatency != null && maxLatency >= SLOW_CORE_THRESHOLD_MS) {
                return result(
                    status = Status.DEGRADED,
                    reason = "Сервис отвечает медленно: $maxLatency мс",
                    hint = service.hints["degraded"],
                )
            }
            return result(
                status = Status.OK,
                reason = if (baseline) "Базовые проверки проходят" else "Все проверки проходят",
                hint = null,
            )
        }

        val first = bad.first()
        if (baseline) {
            if (ok.isNotEmpty()) {
                return result(
                    status = Status.DEGRADED,
                    reason = "Не проходят ${bad.size} из $total проверок: ${first.reasonText()}",
                    hint = service.hints["degraded"],
                )
            }
            return result(
                status = Status.DOWN,
                reason = "Базовые проверки не проходят: ${first.reasonText()}",
                hint = service.hints["down"],
            )
        }

        if (ok.isNotEmpty()) {
            return result(
                status = Status.DEGRADED,
                reason = "Не проходят ${bad.size} из $total проверок: ${first.reasonText()}",
                hint = service.hints["degraded"],
            )
        }

        if (internetStatus == Status.DOWN) {
            return result(
                status = Status.DOWN,
                reason = "Нет доступа в интернет — проверка сервиса невозможна",
                hint = service.hints["down"],
            )
        }

        val errors = bad.mapNotNull { it.error }.toSet()
        if (errors.isNotEmpty() && errors.all { it in ErrorCodes.DNS_ERRORS }) {
            return result(
                status = Status.BLOCKED,
                reason = "DNS не разрешает адрес сервиса при работающем интернете: ${first.target}",
                hint = service.hints["blocked"],
            )
        }
        if (errors.any { it in ErrorCodes.BLOCK_ERRORS }) {
            return result(
                status = Status.BLOCKED,
                reason = "${first.reasonText()}. Похоже на фильтрацию трафика",
                hint = service.hints["blocked"],
            )
        }
        return result(
            status = Status.DOWN,
            reason = first.reasonText(),
            hint = service.hints["down"],
        )
    }
}
