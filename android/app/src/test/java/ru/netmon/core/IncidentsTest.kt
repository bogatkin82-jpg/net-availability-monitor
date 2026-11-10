package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Инциденты: пороги открытия/закрытия, streak, заголовки, лимит хранения.
 * Логика — порт `Monitor._update_incidents()` из server.py.
 */
class IncidentsTest {

    private var now: Long = 1_700_000_000_000L
    private var sequence: Int = 0

    private fun newState(config: AppConfig = TestFixtures.repoConfig()): MonitorState =
        MonitorState(
            config = config,
            clock = { now },
            idGenerator = { "inc-${sequence++}" },
        )

    private fun failRound(
        state: MonitorState,
        config: AppConfig,
        serviceId: String = "youtube",
        error: String = ErrorCodes.CONNECT_REFUSED,
        stepMs: Long = 20_000L,
    ) {
        state.applyRound(
            TestFixtures.round(
                config,
                okByService = mapOf(serviceId to false),
                errorCode = error,
            ),
        )
        now += stepMs
    }

    private fun okRound(
        state: MonitorState,
        config: AppConfig,
        serviceId: String = "youtube",
        stepMs: Long = 20_000L,
    ) {
        state.applyRound(TestFixtures.round(config, okByService = mapOf(serviceId to true)))
        now += stepMs
    }

    @Test
    fun `incident opens after configured failures`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        okRound(state, config)
        assertNull(state.activeIncident("youtube"))

        val firstFailEpoch = now / 1000
        failRound(state, config)
        assertNull("одного сбоя недостаточно", state.activeIncident("youtube"))

        failRound(state, config)
        val incident = state.activeIncident("youtube")
        assertNotNull(incident)
        assertEquals(IncidentStatus.ACTIVE, incident!!.status)
        assertEquals("youtube", incident.serviceId)
        assertEquals("YouTube", incident.serviceName)
        assertEquals("YouTube недоступен", incident.title)
        assertEquals(firstFailEpoch, incident.startedEpoch)
        assertEquals(2, incident.failedChecks)
        assertEquals(2, incident.totalChecks)
        assertEquals(Status.DOWN.id, incident.serviceStatus)
        assertTrue(incident.reason.isNotBlank())
    }

    @Test
    fun `one success closes incident with duration`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        failRound(state, config)
        failRound(state, config)
        val opened = state.activeIncident("youtube")!!
        now += 30_000L
        okRound(state, config, stepMs = 0L)

        assertNull(state.activeIncident("youtube"))
        val closed = state.lastIncident("youtube")!!
        assertEquals(IncidentStatus.RESOLVED, closed.status)
        assertEquals(opened.startedEpoch, closed.startedEpoch)
        assertNotNull(closed.endedEpoch)
        assertEquals(now / 1000, closed.endedEpoch)
        assertTrue(closed.durationSec >= 30)
    }

    @Test
    fun `repeated failures do not open duplicate incidents`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        repeat(5) { failRound(state, config) }
        val total = state.incidentsReport(limit = 100).total
        assertEquals(1, total)
    }

    @Test
    fun `open and close thresholds come from config`() {
        val base = TestFixtures.repoConfig()
        val config = base.copy(
            incidents = base.incidents.copy(openAfterFailures = 3, closeAfterSuccesses = 2),
        )
        val state = newState(config)

        failRound(state, config)
        failRound(state, config)
        assertNull("порог открытия 3 ещё не достигнут", state.activeIncident("youtube"))

        failRound(state, config)
        assertNotNull(state.activeIncident("youtube"))

        okRound(state, config)
        assertNotNull("одного успеха мало при пороге закрытия 2", state.activeIncident("youtube"))

        okRound(state, config)
        assertNull(state.activeIncident("youtube"))
    }

    @Test
    fun `blocked status produces blocked incident title`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        failRound(state, config, error = ErrorCodes.TLS_RESET)
        failRound(state, config, error = ErrorCodes.TLS_RESET)
        val incident = state.activeIncident("youtube")!!
        assertEquals(Status.BLOCKED.id, incident.serviceStatus)
        assertEquals("YouTube: похоже на блокировку", incident.title)
        assertEquals(config.serviceById("youtube")!!.hints["blocked"], state.snapshot().services
            .first { it.id == "youtube" }.hint)
    }

    @Test
    fun `degraded status produces degraded incident title`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        // Падает только одна core-проверка -> degraded.
        val failingYoutube = config.serviceById("youtube")!!.checks.map { spec ->
            val ok = spec.id != "yt_img"
            TestFixtures.checkResult(
                spec = spec,
                ok = ok,
                error = if (ok) null else ErrorCodes.TLS_RESET,
                stage = if (ok) CheckStage.OK else CheckStage.TLS,
            )
        }
        val healthy = TestFixtures.round(config)
        val results = config.services.associate { service ->
            service.id to if (service.id == "youtube") failingYoutube else healthy.getValue(service.id)
        }
        repeat(2) {
            state.applyRound(results)
            now += 20_000L
        }
        val incident = state.activeIncident("youtube")!!
        assertEquals(Status.DEGRADED.id, incident.serviceStatus)
        assertEquals("YouTube: нестабильная работа", incident.title)
    }

    @Test
    fun `max stored incidents are trimmed`() {
        val base = TestFixtures.repoConfig()
        val config = base.copy(incidents = base.incidents.copy(maxStored = 3))
        val state = newState(config)
        repeat(5) {
            failRound(state, config)
            failRound(state, config)
            okRound(state, config)
        }
        val report = state.incidentsReport(limit = 100)
        assertEquals(3, report.total)
        val titles = report.incidents.map { it.startedEpoch }
        assertEquals(titles.sortedDescending(), titles)
    }

    @Test
    fun `unknown status is skipped for incidents`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        val results = TestFixtures.round(config).toMutableMap()
        results["youtube"] = emptyList()
        repeat(3) {
            state.applyRound(results)
            now += 20_000L
        }
        assertEquals(Status.UNKNOWN, state.snapshot().services.first { it.id == "youtube" }.status)
        assertNull(state.activeIncident("youtube"))
    }

    @Test
    fun `active incident duration grows in snapshot`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        failRound(state, config, stepMs = 0L)
        failRound(state, config, stepMs = 0L)
        now += 60_000L
        val incident = state.snapshot().services.first { it.id == "youtube" }.activeIncident
        assertNotNull(incident)
        assertEquals(60L, incident!!.durationSec)
        assertNull(incident.endedAt)
    }
}
