package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Паритет настроек: приложение обязано читать ровно тот config.json,
 * что лежит в корне репозитория. Тест ловит любой дрейф между
 * дефолтами в коде и реальным конфигом.
 */
class ConfigParityTest {

    private val config = TestFixtures.repoConfig()

    @Test
    fun `config parsing matches builtin defaults`() {
        assertEquals(AppConfig.DEFAULTS, config)
    }

    @Test
    fun `assets copy equals repository config`() {
        val assets = TestFixtures.assetsConfigFile()
        assertTrue("assets/config.json должен создаваться Gradle-таском syncConfigJson", assets != null)
        assertEquals(
            TestFixtures.repoConfigFile().readText().trim(),
            assets!!.readText().trim(),
        )
    }

    @Test
    fun `polling timeouts and incident thresholds come from config`() {
        assertEquals(20, config.polling.intervalSec)
        assertEquals(8, config.polling.checkTimeoutSec)
        assertEquals(4, config.polling.dnsTimeoutSec)
        assertEquals(2, config.incidents.openAfterFailures)
        assertEquals(1, config.incidents.closeAfterSuccesses)
        assertEquals(500, config.incidents.maxStored)
        assertEquals(24, config.history.maxAgeHours)
        assertEquals(8640, config.history.maxPoints)
        assertEquals(180, config.history.seriesPoints)
    }

    @Test
    fun `all three services and their checks are loaded`() {
        assertEquals(listOf("youtube", "telegram", "internet"), config.services.map { it.id })
        assertEquals(16, config.services.sumOf { it.checks.size })
        assertEquals(
            listOf("yt_https", "yt_img", "yt_www", "yt_tcp", "yt_dns"),
            config.serviceById("youtube")!!.checks.map { it.id },
        )
        assertEquals(
            listOf("tg_web", "tg_api", "tg_dc2", "tg_dc1", "tg_dc4", "tg_dns_api", "tg_dns_web"),
            config.serviceById("telegram")!!.checks.map { it.id },
        )
        assertEquals(
            listOf("inet_gstatic", "inet_cloudflare", "inet_tcp", "inet_dns"),
            config.serviceById("internet")!!.checks.map { it.id },
        )
    }

    @Test
    fun `endpoints kinds ports and timeouts exactly match config json`() {
        val expected = mapOf(
            "yt_https" to Triple("https", "www.youtube.com", 443),
            "yt_img" to Triple("https", "i.ytimg.com", 443),
            "yt_www" to Triple("https", "www.youtube.com", 443),
            "yt_tcp" to Triple("tcp", "www.youtube.com", 443),
            "yt_dns" to Triple("dns", "www.youtube.com", 443),
            "tg_web" to Triple("https", "web.telegram.org", 443),
            "tg_api" to Triple("https", "api.telegram.org", 443),
            "tg_dc2" to Triple("tcp", "149.154.167.51", 443),
            "tg_dc1" to Triple("tcp", "149.154.175.50", 443),
            "tg_dc4" to Triple("tcp", "149.154.167.91", 443),
            "tg_dns_api" to Triple("dns", "api.telegram.org", 443),
            "tg_dns_web" to Triple("dns", "web.telegram.org", 443),
            "inet_gstatic" to Triple("https", "connectivitycheck.gstatic.com", 443),
            "inet_cloudflare" to Triple("https", "cloudflare.com", 443),
            "inet_tcp" to Triple("tcp", "1.1.1.1", 443),
            "inet_dns" to Triple("dns", "cloudflare.com", 443),
        )
        val actual = config.services.flatMap { it.checks }.associate {
            it.id to Triple(it.kind.id, it.host, it.port)
        }
        assertEquals(expected, actual)
        config.services.flatMap { it.checks }.forEach { check ->
            assertEquals("timeout для ${check.id} должен быть 8 с", 8, check.timeoutSec)
        }
    }

    @Test
    fun `http paths and expected statuses are preserved`() {
        val byId = config.services.flatMap { it.checks }.associateBy { it.id }
        assertEquals("/generate_204", byId.getValue("yt_https").path)
        assertEquals(listOf(204), byId.getValue("yt_https").expect)
        assertEquals("/", byId.getValue("yt_www").path)
        assertEquals(listOf(200, 301, 302, 303), byId.getValue("yt_www").expect)
        assertEquals(listOf(200, 301, 302), byId.getValue("tg_web").expect)
        assertEquals(listOf(200, 301, 302, 400, 404), byId.getValue("tg_api").expect)
        assertEquals("/generate_204", byId.getValue("inet_gstatic").path)
        assertEquals("/cdn-cgi/trace", byId.getValue("inet_cloudflare").path)
        assertEquals(listOf(200), byId.getValue("inet_cloudflare").expect)
        assertTrue(byId.getValue("yt_https").core)
        assertTrue(byId.getValue("yt_img").core)
        assertFalse(byId.getValue("yt_www").core)
        assertTrue(byId.getValue("tg_dc2").core)
        assertTrue(byId.getValue("inet_tcp").core)
    }

    @Test
    fun `status labels and colors come from config`() {
        assertEquals("Доступен", config.statusLabels["ok"])
        assertEquals("Нестабильно", config.statusLabels["degraded"])
        assertEquals("Похоже на блокировку", config.statusLabels["blocked"])
        assertEquals("Недоступен", config.statusLabels["down"])
        assertEquals("Нет данных", config.statusLabels["unknown"])
        assertEquals("#34c759", config.statusColors["ok"])
        assertEquals("#ff9500", config.statusColors["degraded"])
        assertEquals("#ff3b30", config.statusColors["blocked"])
        assertEquals("#ff3b30", config.statusColors["down"])
        assertEquals("#8e8e93", config.statusColors["unknown"])
        assertEquals("#34c759", config.colorFor("ok"))
        assertEquals("Доступен", config.labelFor("ok"))
    }

    @Test
    fun `service metadata accents and hints come from config`() {
        val youtube = config.serviceById("youtube")!!
        assertEquals("YouTube", youtube.name)
        assertEquals("видеохостинг · youtube.com", youtube.subtitle)
        assertEquals("#ff3040", youtube.accent)
        assertEquals("YouTube недоступен", youtube.incidentTitle)
        assertEquals(
            "Похоже на блокировку: проверьте доступ через VPN или мобильный интернет",
            youtube.hints["blocked"],
        )
        val telegram = config.serviceById("telegram")!!
        assertEquals("#2ea6e0", telegram.accent)
        assertEquals("TCP/TLS к Telegram не проходит: вероятна блокировка — включите VPN", telegram.hints["blocked"])
        val internet = config.serviceById("internet")!!
        assertEquals("Интернет", internet.name)
        assertEquals("#38d39f", internet.accent)
        assertEquals("Канал нестабилен — проверьте Wi-Fi/роутер", internet.hints["degraded"])
    }

    @Test
    fun `android notifications settings come from config`() {
        val android = config.notifications.android
        assertTrue(android.enabled)
        assertEquals(listOf("blocked", "down"), android.notifyOn)
        assertTrue(android.foregroundService)
        assertFalse(android.sound)
        assertFalse(android.vibrate)
    }

    @Test
    fun `minimal config falls back to builtin defaults`() {
        val minimal = """
            {"services":[{"id":"youtube","name":"YouTube","checks":[
              {"id":"yt_https","label":"HTTPS","kind":"https","host":"www.youtube.com","port":443,
               "path":"/generate_204","expect":[204],"core":true,"timeout":8}]}]}
        """.trimIndent()
        val parsed = ConfigParser.parse(minimal)
        assertEquals(AppConfig.DEFAULTS.polling, parsed.polling)
        assertEquals(AppConfig.DEFAULTS.incidents, parsed.incidents)
        assertEquals(AppConfig.DEFAULTS.history, parsed.history)
        assertEquals(AppConfig.DEFAULTS.statusLabels, parsed.statusLabels)
        assertEquals(AppConfig.DEFAULTS.statusColors, parsed.statusColors)
        assertEquals(AppConfig.DEFAULTS.notifications, parsed.notifications)
        assertEquals(listOf("youtube"), parsed.services.map { it.id })
    }

    @Test
    fun `server block is parsed but unused on android`() {
        assertEquals("127.0.0.1", config.server.host)
        assertEquals(8099, config.server.port)
        assertEquals(1, config.version)
    }
}
