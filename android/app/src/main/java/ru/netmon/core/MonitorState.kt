package ru.netmon.core

import java.util.UUID
import kotlin.math.roundToInt

/** Точка истории опросов (серия). */
data class SeriesPoint(
    val t: Long,
    val status: String,
    val latencyMs: Int?,
)

enum class IncidentStatus(val id: String) {
    ACTIVE("active"),
    RESOLVED("resolved");

    companion object {
        fun fromId(id: String?): IncidentStatus? = entries.firstOrNull { it.id == id }
    }
}

/** Инцидент недоступности — поля как в server.py/API. */
data class Incident(
    val id: String,
    val serviceId: String,
    val serviceName: String,
    val title: String,
    val reason: String,
    val status: IncidentStatus,
    val startedAt: String,
    val startedEpoch: Long,
    val endedAt: String? = null,
    val endedEpoch: Long? = null,
    val durationSec: Long = 0,
    val failedChecks: Int = 0,
    val totalChecks: Int = 0,
    val serviceStatus: String = Status.UNKNOWN.id,
)

/** Последний результат сервиса (как last_results в server.py). */
data class ServiceResultData(
    val status: Status,
    val label: String,
    val reason: String,
    val hint: String?,
    val latencyMs: Int?,
    val checksOk: Int,
    val checksTotal: Int,
    val checks: List<CheckResult>,
)

data class ServiceSnapshot(
    val id: String,
    val name: String,
    val subtitle: String,
    val icon: String,
    val accent: String,
    val status: Status,
    val statusLabel: String,
    val statusReason: String,
    val hint: String?,
    val latencyMs: Int?,
    val uptime1h: Double,
    val uptime24h: Double,
    val checksOk: Int,
    val checksTotal: Int,
    val checks: List<CheckResult>,
    val series: List<SeriesPoint>,
    val activeIncident: Incident?,
    val lastIncident: Incident?,
)

data class InternetSummary(
    val status: Status,
    val label: String,
    val latencyMs: Int?,
)

data class SnapshotSummary(
    val overall: Status,
    val overallLabel: String,
    val servicesTotal: Int,
    val servicesOk: Int,
    val servicesAffected: Int,
    val activeIncidents: Int,
    val avgLatencyMs: Int?,
    val checksTotal: Int,
    val checksOk: Int,
)

data class Snapshot(
    val generatedAt: String,
    val epoch: Long,
    val version: Int,
    val running: Boolean,
    val pollInterval: Int,
    val nextCheckIn: Double,
    val checking: Boolean,
    val internet: InternetSummary,
    val summary: SnapshotSummary,
    val services: List<ServiceSnapshot>,
    val recentIncidents: List<Incident>,
)

data class IncidentReport(
    val total: Int,
    val incidents: List<Incident>,
)

/**
 * Состояние монитора: серии, инциденты, streaks и снимок.
 *
 * Чистый Kotlin (без Android API), часы и генератор id внедряются — поэтому
 * инциденты и uptime проверяются unit-тестами с управляемым временем.
 * Логика инцидентов — порт `Monitor._apply_round/_update_incidents` из server.py.
 */
class MonitorState(
    config: AppConfig,
    private val clock: () -> Long = { System.currentTimeMillis() },
    private val idGenerator: () -> String = { UUID.randomUUID().toString().replace("-", "").take(12) },
) {
    private class Streak(
        var fail: Int = 0,
        var ok: Int = 0,
        var firstFail: Long? = null,
    )

    private var config: AppConfig = config
    private val series = LinkedHashMap<String, MutableList<SeriesPoint>>()
    private val streaks = LinkedHashMap<String, Streak>()
    private val incidents = mutableListOf<Incident>()
    private val lastResults = LinkedHashMap<String, ServiceResultData>()

    var version: Int = 0
        private set
    var rounds: Int = 0
        private set
    var lastRoundEpoch: Long? = null
        private set
    var nextRoundEpoch: Long? = null
        private set
    var checking: Boolean = false
        private set

    val startedEpoch: Long = clock()

    init {
        config.services.forEach { service ->
            series[service.id] = mutableListOf()
            streaks[service.id] = Streak()
        }
    }

    /** Применить пользовательские настройки (включение/выключение сервисов и т.п.). */
    fun updateConfig(next: AppConfig) {
        synchronized(this) {
            config = next
            next.services.forEach { service ->
                series.getOrPut(service.id) { mutableListOf() }
                streaks.getOrPut(service.id) { Streak() }
            }
            version += 1
        }
    }

    // --- управление состоянием прогона -----------------------------------
    fun setChecking(value: Boolean) {
        checking = value
    }

    fun markNextRound(epochMs: Long) {
        nextRoundEpoch = epochMs
    }

    /** Прогон всех сервисов: классификация, серии, инциденты. */
    fun applyRound(byService: Map<String, List<CheckResult>>): Snapshot {
        val now = clock()
        val checksByService = config.services.associate { service ->
            service.id to (byService[service.id] ?: emptyList())
        }

        val baselineService = config.serviceById(AppConfig.BASELINE_SERVICE_ID)
        val internetInfo = if (baselineService != null) {
            Classifier.classify(
                config = config,
                service = baselineService,
                checks = checksByService[baselineService.id] ?: emptyList(),
                internetStatus = Status.OK,
            )
        } else {
            ServiceInfo(Status.UNKNOWN, config.labelFor(Status.UNKNOWN.id), "Нет данных", null, null, 0, 0)
        }
        val internetStatus = internetInfo.status

        val infos = LinkedHashMap<String, ServiceInfo>()
        infos[AppConfig.BASELINE_SERVICE_ID] = internetInfo
        config.services.forEach { service ->
            if (service.id == AppConfig.BASELINE_SERVICE_ID) return@forEach
            infos[service.id] = Classifier.classify(
                config = config,
                service = service,
                checks = checksByService[service.id] ?: emptyList(),
                internetStatus = internetStatus,
            )
        }

        synchronized(this) {
            config.services.forEach { service ->
                val sid = service.id
                val info = infos[sid] ?: internetInfo
                val checks = checksByService[sid] ?: emptyList()
                lastResults[sid] = ServiceResultData(
                    status = info.status,
                    label = info.label,
                    reason = info.reason,
                    hint = info.hint,
                    latencyMs = info.latencyMs,
                    checksOk = info.checksOk,
                    checksTotal = info.checksTotal,
                    checks = checks,
                )
                val points = series.getOrPut(sid) { mutableListOf() }
                points += SeriesPoint(t = now / 1000, status = info.status.id, latencyMs = info.latencyMs)
                trimSeries(points, now)
            }
            updateIncidents(infos, checksByService, now)
            lastRoundEpoch = now
            rounds += 1
            version += 1
        }
        return snapshot()
    }

    private fun trimSeries(points: MutableList<SeriesPoint>, now: Long) {
        val maxAge = config.history.maxAgeHours.coerceAtLeast(1) * 3600L
        val cutoffSec = now / 1000 - maxAge
        points.removeAll { it.t < cutoffSec }
        val maxPoints = config.history.maxPoints.coerceAtLeast(1)
        if (points.size > maxPoints) {
            val from = points.size - maxPoints
            val kept = points.subList(from, points.size).toList()
            points.clear()
            points.addAll(kept)
        }
    }

    // --- инциденты -------------------------------------------------------
    private fun updateIncidents(
        infos: Map<String, ServiceInfo>,
        checksByService: Map<String, List<CheckResult>>,
        now: Long,
    ) {
        val incidentsConfig = config.incidents
        config.services.forEach { service ->
            val sid = service.id
            val info = infos[sid] ?: return@forEach
            val status = info.status
            if (status == Status.UNKNOWN) return@forEach
            val streak = streaks.getOrPut(sid) { Streak() }
            val active = activeIncident(sid)

            if (status == Status.OK) {
                streak.fail = 0
                streak.ok += 1
                if (active != null && streak.ok >= incidentsConfig.closeAfterSuccesses) {
                    val ended = now / 1000
                    resolveIncident(active, ended)
                    streak.firstFail = null
                }
            } else {
                streak.fail += 1
                streak.ok = 0
                if (streak.firstFail == null) streak.firstFail = now
                if (active == null && streak.fail >= incidentsConfig.openAfterFailures) {
                    val started = streak.firstFail ?: now
                    val checks = checksByService[sid] ?: emptyList()
                    val core = checks.filter { it.core }.ifEmpty { checks }
                    incidents += Incident(
                        id = idGenerator(),
                        serviceId = sid,
                        serviceName = service.name,
                        title = incidentTitle(service, status),
                        reason = info.reason,
                        status = IncidentStatus.ACTIVE,
                        startedAt = IsoTime.ofEpochMillis(started),
                        startedEpoch = started / 1000,
                        endedAt = null,
                        endedEpoch = null,
                        durationSec = maxOf(0L, now / 1000 - started / 1000),
                        failedChecks = core.count { !it.ok },
                        totalChecks = core.size,
                        serviceStatus = status.id,
                    )
                }
            }
            if (status == Status.OK) {
                streak.firstFail = null
            }
        }

        val maxStored = incidentsConfig.maxStored.coerceAtLeast(1)
        if (incidents.size > maxStored) {
            val sorted = incidents.sortedBy { it.startedEpoch }
            val kept = sorted.takeLast(maxStored)
            incidents.clear()
            incidents.addAll(kept)
        }
    }

    private fun resolveIncident(incident: Incident, endedEpoch: Long) {
        val index = incidents.indexOfFirst { it.id == incident.id }
        if (index < 0) return
        val updated = incident.copy(
            status = IncidentStatus.RESOLVED,
            endedAt = IsoTime.ofEpochMillis(endedEpoch * 1000),
            endedEpoch = endedEpoch,
            durationSec = maxOf(0L, endedEpoch - incident.startedEpoch),
        )
        incidents[index] = updated
    }

    private fun incidentTitle(service: ServiceConfig, status: Status): String = when (status) {
        Status.DEGRADED -> "${service.name}: нестабильная работа"
        Status.BLOCKED -> "${service.name}: похоже на блокировку"
        else -> service.incidentTitle
    }

    fun activeIncident(serviceId: String): Incident? =
        incidents.lastOrNull { it.serviceId == serviceId && it.status == IncidentStatus.ACTIVE }

    fun lastIncident(serviceId: String): Incident? =
        incidents.filter { it.serviceId == serviceId }.maxByOrNull { it.startedEpoch }

    /** Снимок с актуальными duration_sec для активных инцидентов. */
    fun incidentView(incident: Incident, now: Long = clock()): Incident =
        if (incident.status == IncidentStatus.ACTIVE) {
            incident.copy(
                endedAt = null,
                endedEpoch = null,
                durationSec = maxOf(0L, now / 1000 - incident.startedEpoch),
            )
        } else {
            incident
        }

    // --- uptime и снимок --------------------------------------------------
    fun uptime(serviceId: String, sinceEpochSec: Long): Double {
        val points = series[serviceId] ?: return 0.0
        val window = points.filter { it.t >= sinceEpochSec }
        if (window.isEmpty()) return 0.0
        val okCount = window.count { it.status == Status.OK.id }
        return ((1000.0 * okCount / window.size).roundToInt() / 10.0)
    }

    fun snapshot(): Snapshot {
        synchronized(this) {
            val now = clock()
            val nowSec = now / 1000
            val servicesOut = ArrayList<ServiceSnapshot>(config.services.size)
            val latencies = ArrayList<Int>()

            config.services.forEach { service ->
                val sid = service.id
                val info = lastResults[sid]
                val status = info?.status ?: Status.UNKNOWN
                val latency = info?.latencyMs
                if (latency != null) latencies += latency
                val points = series[sid] ?: mutableListOf()
                val active = activeIncident(sid)?.let { incidentView(it, now) }
                val last = lastIncident(sid)?.let { incidentView(it, now) }
                servicesOut += ServiceSnapshot(
                    id = sid,
                    name = service.name,
                    subtitle = service.subtitle,
                    icon = service.icon,
                    accent = service.accent,
                    status = status,
                    statusLabel = info?.label ?: config.labelFor(status.id),
                    statusReason = info?.reason ?: "Проверки ещё не выполнялись",
                    hint = info?.hint,
                    latencyMs = latency,
                    uptime1h = uptime(sid, nowSec - 3600),
                    uptime24h = uptime(sid, nowSec - 86400),
                    checksOk = info?.checksOk ?: 0,
                    checksTotal = info?.checksTotal ?: 0,
                    checks = info?.checks ?: emptyList(),
                    series = points.takeLast(config.history.seriesPoints.coerceAtLeast(1)),
                    activeIncident = active,
                    lastIncident = last,
                )
            }

            val internetService = servicesOut.firstOrNull { it.id == AppConfig.BASELINE_SERVICE_ID }
            val internetStatus = when (internetService?.status) {
                Status.OK -> Status.OK
                Status.DEGRADED -> Status.DEGRADED
                Status.UNKNOWN -> Status.UNKNOWN
                else -> Status.DOWN
            }
            val internetLabels = mapOf(
                Status.OK to "Интернет работает",
                Status.DEGRADED to "Интернет нестабилен",
                Status.DOWN to "Похоже, интернета нет",
                Status.UNKNOWN to "Проверка не выполнялась",
            )
            val internet = InternetSummary(
                status = internetStatus,
                label = internetLabels[internetStatus] ?: "Проверка не выполнялась",
                latencyMs = internetService?.latencyMs,
            )

            val statuses = servicesOut.map { it.status }
            val affected = statuses.count { it == Status.DEGRADED || it == Status.BLOCKED || it == Status.DOWN }
            val servicesOk = statuses.count { it == Status.OK }
            val activeIncidents = incidents.count { it.status == IncidentStatus.ACTIVE }
            val overall = when {
                statuses.isNotEmpty() && statuses.all { it == Status.UNKNOWN } -> Status.UNKNOWN
                internetStatus == Status.DOWN -> Status.DOWN
                affected > 0 -> Status.DEGRADED
                else -> Status.OK
            }
            val overallLabels = mapOf(
                Status.OK to "Всё работает",
                Status.DEGRADED to "Есть проблемы",
                Status.DOWN to "Нет доступа в интернет",
                Status.UNKNOWN to "Нет данных",
            )
            val recent = incidents.sortedByDescending { it.startedEpoch }.take(5)
                .map { incidentView(it, now) }

            val nextCheckIn = when {
                checking -> 0.0
                nextRoundEpoch != null -> maxOf(0.0, (nextRoundEpoch!! - now) / 1000.0)
                else -> 0.0
            }

            return Snapshot(
                generatedAt = IsoTime.ofEpochMillis(now),
                epoch = now,
                version = version,
                running = true,
                pollInterval = config.polling.intervalSec,
                nextCheckIn = nextCheckIn,
                checking = checking,
                internet = internet,
                summary = SnapshotSummary(
                    overall = overall,
                    overallLabel = overallLabels[overall] ?: "Нет данных",
                    servicesTotal = servicesOut.size,
                    servicesOk = servicesOk,
                    servicesAffected = affected,
                    activeIncidents = activeIncidents,
                    avgLatencyMs = if (latencies.isEmpty()) null else latencies.average().roundToInt(),
                    checksTotal = servicesOut.sumOf { it.checksTotal },
                    checksOk = servicesOut.sumOf { it.checksOk },
                ),
                services = servicesOut,
                recentIncidents = recent,
            )
        }
    }

    // --- журнал сбоев (фильтры как у /api/incidents) ----------------------
    fun incidentsReport(
        query: String = "",
        serviceId: String = "all",
        status: String = "all",
        hours: Int = 0,
        limit: Int = 200,
    ): IncidentReport {
        synchronized(this) {
            val now = clock()
            val needle = query.trim().lowercase()
            val filtered = incidents.filter { incident ->
                if (serviceId.isNotEmpty() && serviceId != "all" && incident.serviceId != serviceId) {
                    return@filter false
                }
                if (status.isNotEmpty() && status != "all" && incident.status.id != status) {
                    return@filter false
                }
                if (hours > 0 && incident.startedEpoch < now / 1000 - hours * 3600L) {
                    return@filter false
                }
                if (needle.isNotEmpty()) {
                    val haystack = listOf(incident.serviceName, incident.title, incident.reason)
                        .joinToString(" ").lowercase()
                    if (!haystack.contains(needle)) return@filter false
                }
                true
            }.sortedByDescending { it.startedEpoch }

            val page = if (limit > 0) filtered.take(limit) else filtered
            return IncidentReport(
                total = filtered.size,
                incidents = page.map { incidentView(it, now) },
            )
        }
    }

    fun resetHistory() {
        synchronized(this) {
            series.values.forEach { it.clear() }
            streaks.keys.forEach { streaks[it] = Streak() }
            incidents.clear()
            lastResults.clear()
            version += 1
            rounds = 0
            lastRoundEpoch = null
            nextRoundEpoch = null
        }
    }

    // --- персистентность (SharedPreferences) ------------------------------
    fun toJson(): String {
        synchronized(this) {
            val root = LinkedHashMap<String, Any?>()
            root["state_version"] = 1
            root["version_counter"] = version
            root["rounds"] = rounds
            root["last_round_epoch"] = lastRoundEpoch
            root["series"] = series.mapValues { (_, points) ->
                points.map { point ->
                    linkedMapOf<String, Any?>(
                        "t" to point.t,
                        "status" to point.status,
                        "latency_ms" to point.latencyMs,
                    )
                }
            }
            root["incidents"] = incidents.map { incidentToMap(it) }
            root["streaks"] = streaks.mapValues { (_, streak) ->
                linkedMapOf<String, Any?>(
                    "fail" to streak.fail,
                    "ok" to streak.ok,
                    "first_fail" to streak.firstFail,
                )
            }
            root["last_results"] = lastResults.mapValues { (_, data) -> serviceResultToMap(data) }
            return Json.write(root)
        }
    }

    fun loadJson(text: String?) {
        if (text.isNullOrBlank()) return
        val root = try {
            Json.parseObject(text)
        } catch (_: JsonParseException) {
            return
        }
        synchronized(this) {
            try {
                root.objOrNull("series")?.forEach { (sid, raw) ->
                    val points = raw.jsonArray() ?: return@forEach
                    val target = series.getOrPut(sid) { mutableListOf() }
                    target.clear()
                    points.forEach { item ->
                        val map = item.jsonObject() ?: return@forEach
                        val t = map["t"].jsonInt()?.toLong() ?: return@forEach
                        target += SeriesPoint(
                            t = t,
                            status = map["status"].jsonString() ?: Status.UNKNOWN.id,
                            latencyMs = map["latency_ms"].jsonInt(),
                        )
                    }
                }
                root.listOrEmpty("incidents").forEach { item ->
                    val map = item.jsonObject() ?: return@forEach
                    incidentFromMap(map)?.let { incidents += it }
                }
                root.objOrNull("streaks")?.forEach { (sid, raw) ->
                    val map = raw.jsonObject() ?: return@forEach
                    streaks[sid] = Streak(
                        fail = map.intOr("fail", 0),
                        ok = map.intOr("ok", 0),
                        firstFail = map["first_fail"].jsonInt()?.toLong(),
                    )
                }
                root.objOrNull("last_results")?.forEach { (sid, raw) ->
                    val map = raw.jsonObject() ?: return@forEach
                    serviceResultFromMap(map)?.let { lastResults[sid] = it }
                }
                version = root.intOr("version_counter", version)
                rounds = root.intOr("rounds", rounds)
                lastRoundEpoch = root["last_round_epoch"].jsonInt()?.toLong()
            } catch (_: JsonParseException) {
                // повреждённое состояние не должно мешать запуску
            }
        }
    }

    // --- (де)сериализация вложенных структур ------------------------------
    private fun incidentToMap(incident: Incident): Map<String, Any?> = linkedMapOf(
        "id" to incident.id,
        "service_id" to incident.serviceId,
        "service_name" to incident.serviceName,
        "title" to incident.title,
        "reason" to incident.reason,
        "status" to incident.status.id,
        "started_at" to incident.startedAt,
        "started_epoch" to incident.startedEpoch,
        "ended_at" to incident.endedAt,
        "ended_epoch" to incident.endedEpoch,
        "duration_sec" to incident.durationSec,
        "failed_checks" to incident.failedChecks,
        "total_checks" to incident.totalChecks,
        "service_status" to incident.serviceStatus,
    )

    private fun incidentFromMap(map: Map<String, Any?>): Incident? {
        val id = map.stringOrNull("id") ?: return null
        return Incident(
            id = id,
            serviceId = map.stringOrNull("service_id") ?: "",
            serviceName = map.stringOrNull("service_name") ?: "",
            title = map.stringOrNull("title") ?: "",
            reason = map.stringOrNull("reason") ?: "",
            status = IncidentStatus.fromId(map.stringOrNull("status")) ?: IncidentStatus.ACTIVE,
            startedAt = map.stringOrNull("started_at") ?: "",
            startedEpoch = map["started_epoch"].jsonInt()?.toLong() ?: 0L,
            endedAt = map.stringOrNull("ended_at"),
            endedEpoch = map["ended_epoch"].jsonInt()?.toLong(),
            durationSec = map["duration_sec"].jsonInt()?.toLong() ?: 0L,
            failedChecks = map.intOr("failed_checks", 0),
            totalChecks = map.intOr("total_checks", 0),
            serviceStatus = map.stringOrNull("service_status") ?: Status.UNKNOWN.id,
        )
    }

    private fun serviceResultToMap(data: ServiceResultData): Map<String, Any?> = linkedMapOf(
        "status" to data.status.id,
        "label" to data.label,
        "reason" to data.reason,
        "hint" to data.hint,
        "latency_ms" to data.latencyMs,
        "checks_ok" to data.checksOk,
        "checks_total" to data.checksTotal,
        "checks" to data.checks.map { checkToMap(it) },
    )

    private fun serviceResultFromMap(map: Map<String, Any?>): ServiceResultData? {
        val status = map.stringOrNull("status") ?: return null
        return ServiceResultData(
            status = Status.fromId(status),
            label = map.stringOrNull("label") ?: "",
            reason = map.stringOrNull("reason") ?: "",
            hint = map.stringOrNull("hint"),
            latencyMs = map["latency_ms"].jsonInt(),
            checksOk = map.intOr("checks_ok", 0),
            checksTotal = map.intOr("checks_total", 0),
            checks = map.listOrEmpty("checks").mapNotNull { item ->
                item.jsonObject()?.let { checkFromMap(it) }
            },
        )
    }

    private fun checkToMap(check: CheckResult): Map<String, Any?> = linkedMapOf(
        "id" to check.id,
        "label" to check.label,
        "target" to check.target,
        "kind" to check.kind.id,
        "core" to check.core,
        "ok" to check.ok,
        "latency_ms" to check.latencyMs,
        "stage" to check.stage,
        "error" to check.error,
        "error_text" to check.errorText,
        "http_status" to check.httpStatus,
        "ip" to check.ip,
        "timings" to linkedMapOf(
            "dns_ms" to check.timings.dnsMs,
            "tcp_ms" to check.timings.tcpMs,
            "tls_ms" to check.timings.tlsMs,
            "http_ms" to check.timings.httpMs,
        ),
        "measured_at" to check.measuredAt,
    )

    private fun checkFromMap(map: Map<String, Any?>): CheckResult? {
        val id = map.stringOrNull("id") ?: return null
        val kind = CheckKind.fromId(map.stringOrNull("kind")) ?: return null
        val timings = map.objOrNull("timings")
        return CheckResult(
            id = id,
            label = map.stringOrNull("label") ?: id,
            target = map.stringOrNull("target") ?: "",
            kind = kind,
            core = map.boolOr("core", false),
            ok = map.boolOr("ok", false),
            latencyMs = map.intOr("latency_ms", 0),
            stage = map.stringOrNull("stage") ?: CheckStage.UNKNOWN,
            error = map.stringOrNull("error"),
            errorText = map.stringOrNull("error_text"),
            httpStatus = map["http_status"].jsonInt(),
            ip = map.stringOrNull("ip"),
            timings = StageTimings(
                dnsMs = timings?.get("dns_ms")?.jsonInt(),
                tcpMs = timings?.get("tcp_ms")?.jsonInt(),
                tlsMs = timings?.get("tls_ms")?.jsonInt(),
                httpMs = timings?.get("http_ms")?.jsonInt(),
            ),
            measuredAt = map.stringOrNull("measured_at") ?: "",
        )
    }
}
