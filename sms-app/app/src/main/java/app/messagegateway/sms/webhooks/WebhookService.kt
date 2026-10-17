package app.messagegateway.sms.webhooks

import app.messagegateway.sms.core.AppLog
import app.messagegateway.sms.core.Ids
import app.messagegateway.sms.core.Iso
import app.messagegateway.sms.core.WebhookRegistry
import app.messagegateway.sms.core.WebhookView
import app.messagegateway.sms.core.error
import app.messagegateway.sms.core.info
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.data.WebhookDeliveryEntity
import app.messagegateway.sms.data.WebhookEntity
import app.messagegateway.sms.send.DrainScheduler
import app.messagegateway.sms.send.EventSink
import app.messagegateway.sms.send.GatewayEvent
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

data class WebhookResponse(val code: Int, val body: String)

/** Performs one HTTP POST. Blocking; called off the main thread. */
fun interface WebhookHttp {
    fun post(url: String, body: String, headers: Map<String, String>): WebhookResponse
}

object WebhookSigner {
    /** HMAC-SHA256 of the body followed by the timestamp, as lowercase hex. */
    fun sign(key: String, body: String, timestamp: String): String {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(key.toByteArray(), "HmacSHA256"))
        return mac.doFinal((body + timestamp).toByteArray()).joinToString("") { "%02x".format(it) }
    }
}

/**
 * Stores webhook registrations and delivers events to them. Each event is written to the delivery queue
 * first, so it survives the app being killed; delivery is retried with growing pauses.
 */
class WebhookService(
    db: AppDatabase,
    private val deviceId: String,
    private val signingKey: () -> String,
    private val http: WebhookHttp,
    private val scheduler: DrainScheduler,
    private val scope: CoroutineScope,
    private val log: AppLog,
    private val clock: () -> Long = System::currentTimeMillis,
) : EventSink, WebhookRegistry {
    private val dao = db.webhooks()
    private val drainLock = Mutex()

    override suspend fun list(): List<WebhookView> = dao.local().map { WebhookView(it.id, it.url, it.event) }

    override suspend fun upsert(id: String?, url: String, event: String): WebhookView {
        require(event in SUPPORTED_EVENTS) { "Unsupported event: $event" }
        val parsed = try { java.net.URI(url) } catch (_: Exception) { null }
        require(parsed != null && parsed.scheme in setOf("http", "https") && !parsed.host.isNullOrEmpty()) {
            "url must be an absolute http(s) address"
        }
        val entity = WebhookEntity(id?.takeIf { it.isNotBlank() } ?: Ids.random(21), url, event)
        dao.upsert(entity)
        return WebhookView(entity.id, entity.url, entity.event)
    }

    override suspend fun delete(id: String): Boolean = dao.delete(id) > 0

    override suspend fun publish(event: GatewayEvent) {
        val hooks = dao.forEvent(event.name)
        if (hooks.isEmpty()) return
        val now = clock()
        hooks.forEach { hook ->
            dao.insertDelivery(
                WebhookDeliveryEntity(
                    webhookId = hook.id, url = hook.url, event = event.name,
                    body = envelope(hook.id, event).toString(),
                    attempts = 0, nextAttemptAt = now, state = WebhookDeliveryEntity.PENDING,
                    lastError = null, createdAt = now,
                )
            )
        }
        kick()
    }

    fun kick() {
        scope.launch {
            try {
                drain()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                log.error(TAG, "Delivery failed: ${e.message ?: e}")
            }
        }
    }

    /** Delivers everything that is due, then books a wake-up for the next retry. */
    suspend fun drain() = drainLock.withLock {
        while (true) {
            val due = dao.dueDeliveries(clock(), BATCH)
            if (due.isEmpty()) break
            for (delivery in due) deliver(delivery)
        }
        dao.nextAttemptAt()?.let(scheduler::scheduleAt)
    }

    private suspend fun deliver(delivery: WebhookDeliveryEntity) {
        val timestamp = (clock() / 1000).toString()
        val headers = mapOf(
            "Content-Type" to "application/json",
            "User-Agent" to "message-gateway-sms/1",
            "X-Timestamp" to timestamp,
            "X-Signature" to WebhookSigner.sign(signingKey(), delivery.body, timestamp),
        )
        val outcome = try {
            val response = withContext(Dispatchers.IO) { http.post(delivery.url, delivery.body, headers) }
            if (response.code in 200..299) {
                // What the receiver said back is how you tell "accepted" from "accepted and ignored".
                log.info(TAG, "${delivery.event} delivered to ${redact(delivery.url)} (HTTP ${response.code}): ${response.body.take(120)}")
                null
            } else {
                "HTTP ${response.code}"
            }
        } catch (e: Exception) {
            e.message ?: e.javaClass.simpleName
        }

        val attempts = delivery.attempts + 1
        when {
            outcome == null -> dao.updateDelivery(delivery.id, WebhookDeliveryEntity.DELIVERED, attempts, delivery.nextAttemptAt, null)
            attempts >= MAX_ATTEMPTS -> {
                log.error(TAG, "Giving up on ${delivery.event} to ${redact(delivery.url)}: $outcome")
                dao.updateDelivery(delivery.id, WebhookDeliveryEntity.FAILED, attempts, delivery.nextAttemptAt, outcome)
            }
            else -> {
                log.info(TAG, "${delivery.event} to ${redact(delivery.url)} failed ($outcome), retry $attempts")
                dao.updateDelivery(delivery.id, WebhookDeliveryEntity.PENDING, attempts, clock() + backoff(attempts), outcome)
            }
        }
    }

    private fun envelope(webhookId: String, event: GatewayEvent): JsonObject = buildJsonObject {
        put("id", Ids.random(21))
        put("webhookId", webhookId)
        put("event", event.name)
        put("deviceId", deviceId)
        put("payload", payload(event))
    }

    private fun payload(event: GatewayEvent): JsonElement = buildJsonObject {
        when (event) {
            is GatewayEvent.SmsReceived -> {
                put("messageId", event.messageId)
                put("sender", event.sender)
                event.simNumber?.let { put("simNumber", it) }
                put("phoneNumber", event.sender)
                put("message", event.message)
                put("receivedAt", Iso.format(event.receivedAt))
            }
            is GatewayEvent.SmsSent -> {
                put("messageId", event.messageId)
                put("recipient", event.recipient)
                event.simNumber?.let { put("simNumber", it) }
                put("phoneNumber", event.recipient)
                put("partsCount", event.partsCount)
                put("sentAt", Iso.format(event.sentAt))
            }
            is GatewayEvent.SmsDelivered -> {
                put("messageId", event.messageId)
                put("recipient", event.recipient)
                event.simNumber?.let { put("simNumber", it) }
                put("phoneNumber", event.recipient)
                put("deliveredAt", Iso.format(event.deliveredAt))
            }
            is GatewayEvent.SmsFailed -> {
                put("messageId", event.messageId)
                put("recipient", event.recipient)
                event.simNumber?.let { put("simNumber", it) }
                put("phoneNumber", event.recipient)
                put("failedAt", Iso.format(event.failedAt))
                put("reason", event.reason)
            }
        }
    }

    companion object {
        private const val TAG = "Webhooks"
        private const val BATCH = 20
        const val MAX_ATTEMPTS = 12
        val SUPPORTED_EVENTS = setOf("sms:received", "sms:sent", "sms:delivered", "sms:failed")

        /**
         * Webhook addresses often end in a secret that protects them, so logs show only where the call went.
         * Never put a full webhook URL in a log line.
         */
        fun redact(url: String): String = try {
            java.net.URI(url).let { "${it.scheme}://${it.host}/…" }
        } catch (_: Exception) {
            "(address hidden)"
        }

        /** 10s, 20s, 40s ... capped at one hour. */
        fun backoff(attempts: Int): Long =
            minOf(10_000L shl (attempts - 1).coerceIn(0, 20), 3_600_000L)
    }
}
