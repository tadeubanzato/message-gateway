package app.messagegateway.sms.ui

import app.messagegateway.sms.data.WebhookEntity

/** One event a webhook listens for. [count] above 1 means the same event is registered twice for the same address. */
data class GroupedEvent(val event: String, val label: String, val count: Int)

/** Every webhook that calls the same address, shown as one card with its events as tags. */
data class WebhookGroup(
    val url: String,
    val source: String,
    val events: List<GroupedEvent>,
    val ids: List<String>,
) {
    val host: String = hostOf(url)
    val maskedUrl: String = WebhookGroups.mask(url)
    val fromServer: Boolean get() = source == "Cloud"
    val hasDuplicates: Boolean get() = events.any { it.count > 1 }

    private fun hostOf(url: String) = try { java.net.URI(url).host ?: url } catch (_: Exception) { url }
}

object WebhookGroups {
    private val known = listOf("sms:received", "sms:sent", "sms:delivered", "sms:failed")
    private val tokenLike = Regex("[A-Za-z0-9_-]{16,}")

    fun label(event: String): String = when (event) {
        "sms:received" -> "Received"
        "sms:sent" -> "Sent"
        "sms:delivered" -> "Delivered"
        "sms:failed" -> "Failed"
        else -> event.substringAfter(':').replace('-', ' ').replaceFirstChar { it.uppercase() }
    }

    /** Groups by address and origin, events in the order a message goes through them. */
    fun group(hooks: List<WebhookEntity>): List<WebhookGroup> =
        hooks.groupBy { it.url to it.source }
            .map { (key, items) ->
                val events = items.groupBy { it.event }
                    .map { (event, same) -> GroupedEvent(event, label(event), same.size) }
                    .sortedWith(compareBy({ known.indexOf(it.event).let { i -> if (i < 0) Int.MAX_VALUE else i } }, { it.event }))
                WebhookGroup(key.first, key.second, events, items.map { it.id })
            }
            .sortedWith(compareBy({ !it.fromServer }, { it.host }, { it.url }))

    /**
     * Keeps where a call goes and hides what protects it: any long token-like path segment becomes dots, and the
     * query string is dropped. The full address is still what gets copied.
     */
    fun mask(url: String): String = try {
        val uri = java.net.URI(url)
        val path = uri.path.orEmpty().split('/').joinToString("/") { if (tokenLike.matches(it)) "••••••" else it }
        val port = if (uri.port >= 0) ":${uri.port}" else ""
        val hidden = if (uri.rawQuery != null) "?…" else ""
        "${uri.scheme}://${uri.host}$port$path$hidden"
    } catch (_: Exception) {
        "(address hidden)"
    }
}
