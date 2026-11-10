package ru.netmon.core

/**
 * Минимальный JSON-парсер/сериализатор без внешних зависимостей.
 *
 * Нужен, чтобы:
 *  * читать assets/config.json на Android и в JVM unit-тестах одинаково;
 *  * хранить историю/инциденты в SharedPreferences;
 *  * не тянуть в приложение лишние библиотеки.
 *
 * Поддерживаются все типы JSON: object -> Map<String, Any?>, array -> List<Any?>,
 * string -> String, number -> Long/Double, boolean -> Boolean, null -> null.
 */
class JsonParseException(message: String) : IllegalArgumentException(message)

object Json {

    fun parse(text: String): Any? {
        val parser = Parser(text)
        parser.skipWhitespace()
        val value = parser.parseValue()
        parser.skipWhitespace()
        if (!parser.isAtEnd()) {
            throw JsonParseException("Лишние данные в JSON на позиции ${parser.position}")
        }
        return value
    }

    @Suppress("UNCHECKED_CAST")
    fun parseObject(text: String): Map<String, Any?> {
        val value = parse(text) ?: throw JsonParseException("Ожидался JSON-объект, получен null")
        return value as? Map<String, Any?>
            ?: throw JsonParseException("Ожидался JSON-объект, получен ${value::class.simpleName}")
    }

    /** Сериализация в компактный JSON (для SharedPreferences и тестов). */
    fun write(value: Any?): String = StringBuilder().also { writeValue(it, value) }.toString()

    private fun writeValue(out: StringBuilder, value: Any?) {
        when (value) {
            null -> out.append("null")
            is String -> writeString(out, value)
            is Boolean -> out.append(if (value) "true" else "false")
            is Double -> out.append(if (value.isFinite()) value.toString() else "null")
            is Float -> out.append(if (value.isFinite()) value.toString() else "null")
            is Number -> out.append(value.toString())
            is Map<*, *> -> {
                out.append('{')
                var first = true
                for ((key, item) in value) {
                    if (!first) out.append(',')
                    first = false
                    writeString(out, key.toString())
                    out.append(':')
                    writeValue(out, item)
                }
                out.append('}')
            }
            is Iterable<*> -> {
                out.append('[')
                var first = true
                for (item in value) {
                    if (!first) out.append(',')
                    first = false
                    writeValue(out, item)
                }
                out.append(']')
            }
            is Array<*> -> writeValue(out, value.toList())
            is IntArray -> writeValue(out, value.toList())
            else -> writeString(out, value.toString())
        }
    }

    private fun writeString(out: StringBuilder, value: String) {
        out.append('"')
        for (ch in value) {
            when (ch) {
                '"' -> out.append("\\\"")
                '\\' -> out.append("\\\\")
                '\n' -> out.append("\\n")
                '\r' -> out.append("\\r")
                '\t' -> out.append("\\t")
                '\b' -> out.append("\\b")
                '\u000C' -> out.append("\\f")
                else -> if (ch < ' ') out.append("\\u%04x".format(ch.code)) else out.append(ch)
            }
        }
        out.append('"')
    }

    private class Parser(private val source: String) {
        var position: Int = 0
            private set

        fun isAtEnd(): Boolean = position >= source.length

        fun skipWhitespace() {
            while (position < source.length && source[position].isWhitespace()) position++
        }

        fun parseValue(): Any? {
            skipWhitespace()
            if (isAtEnd()) throw JsonParseException("Неожиданный конец JSON")
            return when (val ch = source[position]) {
                '{' -> parseObject()
                '[' -> parseArray()
                '"' -> parseString()
                't' -> {
                    expect("true"); true
                }
                'f' -> {
                    expect("false"); false
                }
                'n' -> {
                    expect("null"); null
                }
                else -> {
                    if (ch == '-' || ch.isDigit()) parseNumber()
                    else throw JsonParseException("Неожиданный символ '$ch' на позиции $position")
                }
            }
        }

        private fun expect(literal: String) {
            if (!source.startsWith(literal, position)) {
                throw JsonParseException("Ожидалось \"$literal\" на позиции $position")
            }
            position += literal.length
        }

        private fun parseObject(): Map<String, Any?> {
            val result = LinkedHashMap<String, Any?>()
            position++ // '{'
            skipWhitespace()
            if (!isAtEnd() && source[position] == '}') {
                position++
                return result
            }
            while (true) {
                skipWhitespace()
                if (isAtEnd() || source[position] != '"') {
                    throw JsonParseException("Ожидался ключ объекта на позиции $position")
                }
                val key = parseString()
                skipWhitespace()
                if (isAtEnd() || source[position] != ':') {
                    throw JsonParseException("Ожидалось ':' на позиции $position")
                }
                position++
                result[key] = parseValue()
                skipWhitespace()
                if (isAtEnd()) throw JsonParseException("Незакрытый объект JSON")
                when (source[position]) {
                    ',' -> {
                        position++
                    }
                    '}' -> {
                        position++
                        return result
                    }
                    else -> throw JsonParseException("Ожидалось ',' или '}' на позиции $position")
                }
            }
        }

        private fun parseArray(): List<Any?> {
            val result = ArrayList<Any?>()
            position++ // '['
            skipWhitespace()
            if (!isAtEnd() && source[position] == ']') {
                position++
                return result
            }
            while (true) {
                result.add(parseValue())
                skipWhitespace()
                if (isAtEnd()) throw JsonParseException("Незакрытый массив JSON")
                when (source[position]) {
                    ',' -> {
                        position++
                    }
                    ']' -> {
                        position++
                        return result
                    }
                    else -> throw JsonParseException("Ожидалось ',' или ']' на позиции $position")
                }
            }
        }

        private fun parseString(): String {
            val out = StringBuilder()
            position++ // '"'
            while (true) {
                if (isAtEnd()) throw JsonParseException("Незакрытая строка JSON")
                val ch = source[position++]
                when (ch) {
                    '"' -> return out.toString()
                    '\\' -> {
                        if (isAtEnd()) throw JsonParseException("Незавершённая escape-последовательность")
                        when (val esc = source[position++]) {
                            '"' -> out.append('"')
                            '\\' -> out.append('\\')
                            '/' -> out.append('/')
                            'b' -> out.append('\b')
                            'f' -> out.append('\u000C')
                            'n' -> out.append('\n')
                            'r' -> out.append('\r')
                            't' -> out.append('\t')
                            'u' -> {
                                if (position + 4 > source.length) {
                                    throw JsonParseException("Незавершённый \\u-escape")
                                }
                                val hex = source.substring(position, position + 4)
                                position += 4
                                val code = hex.toIntOrNull(16)
                                    ?: throw JsonParseException("Некорректный \\u-escape: $hex")
                                out.append(code.toChar())
                            }
                            else -> throw JsonParseException("Неизвестный escape '\\$esc'")
                        }
                    }
                    else -> out.append(ch)
                }
            }
        }

        private fun parseNumber(): Any {
            val start = position
            if (!isAtEnd() && source[position] == '-') position++
            while (!isAtEnd() && source[position].isDigit()) position++
            var isFloating = false
            if (!isAtEnd() && source[position] == '.') {
                isFloating = true
                position++
                while (!isAtEnd() && source[position].isDigit()) position++
            }
            if (!isAtEnd() && (source[position] == 'e' || source[position] == 'E')) {
                isFloating = true
                position++
                if (!isAtEnd() && (source[position] == '+' || source[position] == '-')) position++
                while (!isAtEnd() && source[position].isDigit()) position++
            }
            val raw = source.substring(start, position)
            if (raw.isEmpty() || raw == "-") throw JsonParseException("Некорректное число на позиции $start")
            return if (isFloating) {
                raw.toDoubleOrNull() ?: throw JsonParseException("Некорректное число: $raw")
            } else {
                raw.toLongOrNull() ?: raw.toDoubleOrNull() ?: throw JsonParseException("Некорректное число: $raw")
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Удобные типизированные аксессоры поверх разобранного JSON.
// ---------------------------------------------------------------------------

fun Any?.jsonObject(): Map<String, Any?>? = this as? Map<String, Any?>

fun Any?.jsonArray(): List<Any?>? = this as? List<Any?>

fun Any?.jsonString(): String? = this as? String

fun Any?.jsonInt(): Int? = when (this) {
    is Int -> this
    is Long -> if (this in Int.MIN_VALUE.toLong()..Int.MAX_VALUE.toLong()) toInt() else null
    is Double -> if (this.isFinite() && this % 1.0 == 0.0) toInt() else null
    is String -> toIntOrNull()
    else -> null
}

fun Any?.jsonDouble(): Double? = when (this) {
    is Double -> this
    is Float -> toDouble()
    is Number -> toDouble()
    is String -> toDoubleOrNull()
    else -> null
}

fun Any?.jsonBool(): Boolean? = when (this) {
    is Boolean -> this
    is String -> when (lowercase()) {
        "true" -> true
        "false" -> false
        else -> null
    }
    else -> null
}

fun Map<String, Any?>.objOrNull(key: String): Map<String, Any?>? = this[key].jsonObject()

fun Map<String, Any?>.listOrEmpty(key: String): List<Any?> = this[key].jsonArray() ?: emptyList()

fun Map<String, Any?>.stringOrNull(key: String): String? = this[key].jsonString()

fun Map<String, Any?>.intOr(key: String, fallback: Int): Int = this[key].jsonInt() ?: fallback

fun Map<String, Any?>.boolOr(key: String, fallback: Boolean): Boolean = this[key].jsonBool() ?: fallback

fun Map<String, Any?>.stringMap(key: String): Map<String, String> {
    val raw = objOrNull(key) ?: return emptyMap()
    val out = LinkedHashMap<String, String>()
    for ((k, v) in raw) {
        val text = v.jsonString()
        if (text != null) out[k] = text
    }
    return out
}

fun Map<String, Any?>.intList(key: String): List<Int> =
    listOrEmpty(key).mapNotNull { it.jsonInt() }
