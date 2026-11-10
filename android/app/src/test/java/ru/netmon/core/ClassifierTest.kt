package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Классификация сервисов — паритет с `Monitor._classify()` в server.py.
 */
class ClassifierTest {

    private val config = TestFixtures.repoConfig()
    private val youtube = config.serviceById("youtube")!!
    private val internet = config.serviceById("internet")!!

    private fun buildChecks(
        service: ServiceConfig,
        okById: Map<String, Boolean>,
        latencyById: Map<String, Int> = emptyMap(),
        errors: Map<String, String> = emptyMap(),
    ): List<CheckResult> = service.checks.map { spec ->
        val ok = okById[spec.id] ?: true
        val error = if (ok) null else errors[spec.id] ?: ErrorCodes.CONNECT_REFUSED
        TestFixtures.checkResult(
            spec = spec,
            ok = ok,
            latencyMs = latencyById[spec.id] ?: 100,
            error = error,
            stage = if (ok) CheckStage.OK else TestFixtures.stageFor(error ?: ErrorCodes.UNKNOWN_ERROR),
        )
    }

    @Test
    fun `all core ok gives ok`() {
        val info = Classifier.classify(config, youtube, buildChecks(youtube, emptyMap()), Status.OK)
        assertEquals(Status.OK, info.status)
        assertEquals("Доступен", info.label)
        assertEquals("Все проверки проходят", info.reason)
        assertNull(info.hint)
        assertEquals(100, info.latencyMs)
        assertEquals(5, info.checksOk)
        assertEquals(5, info.checksTotal)
    }

    @Test
    fun `slow core above 4000ms gives degraded`() {
        val checks = buildChecks(
            youtube,
            emptyMap(),
            latencyById = mapOf("yt_https" to 4500, "yt_img" to 120),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.DEGRADED, info.status)
        assertEquals("Сервис отвечает медленно: 4500 мс", info.reason)
        assertEquals(youtube.hints["degraded"], info.hint)
    }

    @Test
    fun `one failed core check gives degraded`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_img" to false),
            errors = mapOf("yt_img" to ErrorCodes.TLS_RESET),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.DEGRADED, info.status)
        assertEquals("Нестабильно", info.label)
        assertTrue(info.reason.startsWith("Не проходят 1 из 2 проверок"))
        assertEquals(youtube.hints["degraded"], info.hint)
    }

    @Test
    fun `all core failure with tls errors gives blocked`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_https" to false, "yt_img" to false),
            errors = mapOf(
                "yt_https" to ErrorCodes.TLS_HANDSHAKE_TIMEOUT,
                "yt_img" to ErrorCodes.TLS_RESET,
            ),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.BLOCKED, info.status)
        assertEquals("Похоже на блокировку", info.label)
        assertTrue(info.reason.contains("Похоже на фильтрацию трафика"))
        assertEquals(youtube.hints["blocked"], info.hint)
    }

    @Test
    fun `dns only failure gives blocked`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_https" to false, "yt_img" to false),
            errors = mapOf(
                "yt_https" to ErrorCodes.DNS_ERROR,
                "yt_img" to ErrorCodes.DNS_TIMEOUT,
            ),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.BLOCKED, info.status)
        assertTrue(info.reason.startsWith("DNS не разрешает адрес сервиса"))
        assertTrue(info.reason.contains("www.youtube.com"))
    }

    @Test
    fun `refused connection gives down`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_https" to false, "yt_img" to false),
            errors = mapOf(
                "yt_https" to ErrorCodes.CONNECT_REFUSED,
                "yt_img" to ErrorCodes.CONNECT_REFUSED,
            ),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.DOWN, info.status)
        assertEquals("Недоступен", info.label)
        assertEquals(youtube.hints["down"], info.hint)
    }

    @Test
    fun `unexpected http status gives down`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_https" to false, "yt_img" to false),
            errors = mapOf(
                "yt_https" to ErrorCodes.HTTP_STATUS_UNEXPECTED,
                "yt_img" to ErrorCodes.HTTP_ERROR,
            ),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.DOWN, info.status)
    }

    @Test
    fun `internet down forces service down`() {
        val checks = buildChecks(
            youtube,
            okById = mapOf("yt_https" to false, "yt_img" to false),
            errors = mapOf("yt_https" to ErrorCodes.CONNECT_REFUSED, "yt_img" to ErrorCodes.CONNECT_REFUSED),
        )
        val info = Classifier.classify(config, youtube, checks, Status.DOWN)
        assertEquals(Status.DOWN, info.status)
        assertEquals("Нет доступа в интернет — проверка сервиса невозможна", info.reason)
    }

    @Test
    fun `baseline service labels match dashboard`() {
        val okChecks = buildChecks(internet, emptyMap())
        val ok = Classifier.classify(config, internet, okChecks, Status.OK)
        assertEquals(Status.OK, ok.status)
        assertEquals("Интернет работает", ok.label)
        assertEquals("Базовые проверки проходят", ok.reason)

        val partialChecks = buildChecks(
            internet,
            okById = mapOf("inet_cloudflare" to false),
            errors = mapOf("inet_cloudflare" to ErrorCodes.TLS_RESET),
        )
        val degraded = Classifier.classify(config, internet, partialChecks, Status.OK)
        assertEquals(Status.DEGRADED, degraded.status)
        assertEquals("Интернет нестабилен", degraded.label)

        val deadChecks = buildChecks(
            internet,
            okById = mapOf(
                "inet_gstatic" to false,
                "inet_cloudflare" to false,
                "inet_tcp" to false,
            ),
            errors = mapOf(
                "inet_gstatic" to ErrorCodes.CONNECT_TIMEOUT,
                "inet_cloudflare" to ErrorCodes.CONNECT_TIMEOUT,
                "inet_tcp" to ErrorCodes.CONNECT_TIMEOUT,
            ),
        )
        val down = Classifier.classify(config, internet, deadChecks, Status.OK)
        assertEquals(Status.DOWN, down.status)
        assertEquals("Нет интернета", down.label)
    }

    @Test
    fun `no checks gives unknown with config label`() {
        val info = Classifier.classify(config, youtube, emptyList(), Status.OK)
        assertEquals(Status.UNKNOWN, info.status)
        assertEquals("Нет данных", info.label)
        assertEquals("Проверки ещё не выполнялись", info.reason)
        assertNull(info.latencyMs)
    }

    @Test
    fun `labels and hints are taken from config`() {
        val custom = config.copy(
            statusLabels = config.statusLabels + ("ok" to "Работает (custom)"),
            services = config.services.map { service ->
                if (service.id == "youtube") {
                    service.copy(hints = service.hints + ("degraded" to "подсказка из конфига"))
                } else {
                    service
                }
            },
        )
        val customYoutube = custom.serviceById("youtube")!!
        val ok = Classifier.classify(custom, customYoutube, buildChecks(customYoutube, emptyMap()), Status.OK)
        assertEquals("Работает (custom)", ok.label)

        val degradedChecks = buildChecks(
            customYoutube,
            okById = mapOf("yt_img" to false),
            errors = mapOf("yt_img" to ErrorCodes.TLS_RESET),
        )
        val degraded = Classifier.classify(custom, customYoutube, degradedChecks, Status.OK)
        assertEquals("подсказка из конфига", degraded.hint)
    }

    @Test
    fun `latency uses successful core checks only`() {
        val checks = buildChecks(
            youtube,
            emptyMap(),
            latencyById = mapOf(
                "yt_https" to 100,
                "yt_img" to 200,
                "yt_www" to 9000,
                "yt_tcp" to 9000,
                "yt_dns" to 9000,
            ),
        )
        val info = Classifier.classify(config, youtube, checks, Status.OK)
        assertEquals(Status.OK, info.status)
        assertEquals(150, info.latencyMs)
    }
}
