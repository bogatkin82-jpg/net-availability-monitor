package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** История серий, uptime, срез series_points и персистентность состояния. */
class SeriesUptimeTest {

    private var now: Long = 1_700_000_000_000L

    private fun newState(config: AppConfig = TestFixtures.repoConfig()): MonitorState =
        MonitorState(config, clock = { now })

    private fun round(state: MonitorState, config: AppConfig, youtubeOk: Boolean = true, stepMs: Long = 60_000L) {
        state.applyRound(
            TestFixtures.round(config, okByService = mapOf("youtube" to youtubeOk)),
        )
        now += stepMs
    }

    @Test
    fun `uptime 1h and 24h count ok points`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        repeat(10) { round(state, config, youtubeOk = true) }
        var service = state.snapshot().services.first { it.id == "youtube" }
        assertEquals(100.0, service.uptime1h, 0.001)
        assertEquals(100.0, service.uptime24h, 0.001)

        repeat(10) { round(state, config, youtubeOk = false) }
        service = state.snapshot().services.first { it.id == "youtube" }
        assertEquals(50.0, service.uptime1h, 0.001)
    }

    @Test
    fun `series keeps only configured number of points`() {
        val base = TestFixtures.repoConfig()
        val config = base.copy(history = base.history.copy(seriesPoints = 5))
        val state = newState(config)
        repeat(12) { index ->
            round(state, config, youtubeOk = index % 2 == 0, stepMs = 1_000L)
        }
        val service = state.snapshot().services.first { it.id == "youtube" }
        assertEquals(5, service.series.size)
    }

    @Test
    fun `series drops points older than max age`() {
        val base = TestFixtures.repoConfig()
        val config = base.copy(history = base.history.copy(maxAgeHours = 1))
        val state = newState(config)
        round(state, config, stepMs = 0L)
        assertEquals(1, state.snapshot().services.first { it.id == "youtube" }.series.size)
        now += 2 * 3600 * 1000L
        round(state, config, stepMs = 0L)
        assertEquals(1, state.snapshot().services.first { it.id == "youtube" }.series.size)
    }

    @Test
    fun `state survives persistence round trip`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        repeat(2) {
            state.applyRound(TestFixtures.round(config, okByService = mapOf("youtube" to false)))
            now += 20_000L
        }
        val before = state.snapshot()
        val json = state.toJson()

        val restored = newState(config)
        restored.loadJson(json)
        val after = restored.snapshot()
        assertEquals(before.services.map { it.id to it.status }, after.services.map { it.id to it.status })
        assertEquals(before.summary.overall, after.summary.overall)
        assertEquals(
            before.services.first { it.id == "youtube" }.series.size,
            after.services.first { it.id == "youtube" }.series.size,
        )
        assertTrue(after.services.first { it.id == "youtube" }.activeIncident != null)
        assertEquals(2, after.version)
    }

    @Test
    fun `reset history clears series and incidents`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        repeat(2) {
            state.applyRound(TestFixtures.round(config, okByService = mapOf("youtube" to false)))
            now += 20_000L
        }
        assertTrue(state.snapshot().services.first { it.id == "youtube" }.activeIncident != null)
        state.resetHistory()
        val after = state.snapshot()
        assertEquals(Status.UNKNOWN, after.services.first { it.id == "youtube" }.status)
        assertEquals(0, after.services.first { it.id == "youtube" }.series.size)
        assertEquals(0, after.summary.activeIncidents)
        assertEquals(0, state.incidentsReport().total)
    }

    @Test
    fun `overall summary follows statuses`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        state.applyRound(TestFixtures.round(config))
        var snapshot = state.snapshot()
        assertEquals(Status.OK, snapshot.summary.overall)
        assertEquals("Всё работает", snapshot.summary.overallLabel)

        now += 20_000L
        state.applyRound(TestFixtures.round(config, okByService = mapOf("youtube" to false)))
        snapshot = state.snapshot()
        assertEquals(Status.DEGRADED, snapshot.summary.overall)
        assertEquals("Есть проблемы", snapshot.summary.overallLabel)

        now += 20_000L
        state.applyRound(
            TestFixtures.round(
                config,
                okByService = mapOf("youtube" to false, "internet" to false),
                errorCode = ErrorCodes.CONNECT_REFUSED,
            ),
        )
        snapshot = state.snapshot()
        assertEquals(Status.DOWN, snapshot.summary.overall)
        assertEquals("Нет доступа в интернет", snapshot.summary.overallLabel)
        assertEquals(Status.DOWN, snapshot.internet.status)
    }

    @Test
    fun `next check countdown is exposed in snapshot`() {
        val config = TestFixtures.repoConfig()
        val state = newState(config)
        state.applyRound(TestFixtures.round(config))
        state.setChecking(false)
        state.markNextRound(now + 5_000L)
        val snapshot = state.snapshot()
        assertEquals(5.0, snapshot.nextCheckIn, 0.001)
        assertEquals(20, snapshot.pollInterval)
        assertEquals(3, snapshot.summary.servicesTotal)
    }

    @Test
    fun `disabled services disappear from snapshot`() {
        val base = TestFixtures.repoConfig()
        val config = base.copy(services = base.services.filter { it.id != "telegram" })
        val state = newState(base)
        state.updateConfig(config)
        assertEquals(listOf("youtube", "internet"), state.snapshot().services.map { it.id })
        assertNull(state.activeIncident("telegram"))
    }
}
