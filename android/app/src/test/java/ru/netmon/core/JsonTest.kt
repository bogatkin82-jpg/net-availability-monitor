package ru.netmon.core

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

/** Минимальный JSON-парсер/сериализатор: конфиг и состояние не должны зависеть от библиотек. */
class JsonTest {

    @Test
    fun `parses nested config like document`() {
        val json = """
            {"version":1,"polling":{"interval_sec":20,"check_timeout_sec":8},
             "services":[{"id":"youtube","checks":[{"id":"yt_https","port":443,"expect":[204],"core":true}]}],
             "enabled":true,"note":null,"ratio":1.5}
        """.trimIndent()
        val root = Json.parseObject(json)
        assertEquals(1, root["version"].jsonInt())
        assertEquals(20, root.objOrNull("polling")!!.intOr("interval_sec", 0))
        val services = root.listOrEmpty("services")
        assertEquals(1, services.size)
        val check = services.first().jsonObject()!!.listOrEmpty("checks").first().jsonObject()!!
        assertEquals(listOf(204), check.intList("expect"))
        assertTrue(check.boolOr("core", false))
        assertEquals(true, root["enabled"].jsonBool())
        assertNull(root["note"])
        assertEquals(1.5, root["ratio"].jsonDouble()!!, 0.0001)
    }

    @Test
    fun `handles escapes and unicode`() {
        val value = Json.parse("\"привет\\n\\\"мир\\\" \\u00e9\"")
        assertEquals("привет\n\"мир\" é", value)
    }

    @Test
    fun `write and parse round trip`() {
        val payload = linkedMapOf<String, Any?>(
            "text" to "значение \"кавычки\"",
            "number" to 42,
            "flag" to false,
            "list" to listOf(1, 2, 3),
            "nested" to linkedMapOf("a" to 1, "b" to null),
        )
        val text = Json.write(payload)
        val parsed = Json.parseObject(text)
        assertEquals("значение \"кавычки\"", parsed["text"])
        assertEquals(42, parsed["number"].jsonInt())
        assertEquals(false, parsed["flag"].jsonBool())
        assertEquals(listOf(1, 2, 3), parsed.listOrEmpty("list").map { it.jsonInt() })
    }

    @Test
    fun `malformed json fails explicitly`() {
        listOf("{", "{\"a\":}", "[1,2", "{\"a\" 1}", "tru").forEach { broken ->
            try {
                Json.parse(broken)
                fail("Ожидалась ошибка для $broken")
            } catch (_: JsonParseException) {
                // ожидаемо
            }
        }
    }

    @Test
    fun `string map and defaults helpers`() {
        val root = Json.parseObject("""{"labels":{"ok":"Доступен"},"count":7}""")
        assertEquals(mapOf("ok" to "Доступен"), root.stringMap("labels"))
        assertEquals(7, root.intOr("count", 0))
        assertEquals(9, root.intOr("missing", 9))
        assertEquals(true, root.boolOr("missing", true))
    }
}
