package app.messagegateway.sms.cloud

/**
 * Reads a Server-Sent Events stream line by line. A blank line ends an event. Lines starting with ":" are
 * comments (the server's keepalive). An event with no name is a "MessageEnqueued", as the SMSGate app treats it.
 */
class SseParser {
    data class Event(val name: String, val data: String)

    private var name: String? = null
    private val data = StringBuilder()
    private var hasData = false

    /** Feeds one line (without its line ending); returns an event when this line completes one. */
    fun feed(line: String): Event? {
        if (line.isEmpty()) {
            val event = if (hasData) Event(name ?: DEFAULT_EVENT, data.toString()) else null
            name = null
            data.setLength(0)
            hasData = false
            return event
        }
        if (line.startsWith(":")) return null
        val separator = line.indexOf(':')
        val field = if (separator < 0) line else line.substring(0, separator)
        val value = if (separator < 0) "" else line.substring(separator + 1).removePrefix(" ")
        when (field) {
            "event" -> name = value
            "data" -> {
                if (hasData) data.append('\n')
                data.append(value)
                hasData = true
            }
        }
        return null
    }

    companion object {
        const val DEFAULT_EVENT = "MessageEnqueued"
    }
}
