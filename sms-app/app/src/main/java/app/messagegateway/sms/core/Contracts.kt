package app.messagegateway.sms.core

import app.messagegateway.sms.domain.ProcessingState

const val PRIORITY_EXPEDITED = 100

/** A message as accepted from the API or the app's own composer. */
data class NewMessage(
    val id: String?,
    val text: String,
    val phoneNumbers: List<String>,
    val simNumber: Int? = null,
    val withDeliveryReport: Boolean = true,
    val priority: Int = 0,
    val scheduleAt: Long? = null,
    val validUntil: Long? = null,
    val skipPhoneValidation: Boolean = false,
    /** "Local" for the phone's own API and app, "Cloud" for messages pulled from the cloud server. */
    val source: String = "Local",
)

data class RecipientView(val phoneNumber: String, val state: ProcessingState, val error: String?)

data class MessageView(
    val id: String,
    val deviceId: String,
    val state: ProcessingState,
    val createdAt: Long,
    val text: String,
    val recipients: List<RecipientView>,
    val states: Map<ProcessingState, Long>,
    val scheduleAt: Long?,
)

class ConflictException(message: String = "Message with this ID already exists") : RuntimeException(message)
class NotFoundException(message: String = "Not found") : RuntimeException(message)
class InvalidStateException(message: String) : RuntimeException(message)

/** What the HTTP API needs from the sending side. */
interface MessageService {
    suspend fun enqueue(message: NewMessage): MessageView
    suspend fun get(id: String): MessageView?
    suspend fun list(state: ProcessingState?, from: Long, to: Long, limit: Int, offset: Int): List<MessageView>
    suspend fun count(state: ProcessingState?, from: Long, to: Long): Int
    suspend fun cancel(id: String): MessageView
}

data class WebhookView(val id: String, val url: String, val event: String)

interface WebhookRegistry {
    suspend fun list(): List<WebhookView>
    suspend fun upsert(id: String?, url: String, event: String): WebhookView
    suspend fun delete(id: String): Boolean
}

data class InboxView(val id: String, val sender: String, val body: String, val receivedAt: Long, val simNumber: Int?)

interface InboxReader {
    suspend fun list(from: Long, to: Long, limit: Int, offset: Int): List<InboxView>
    suspend fun count(from: Long, to: Long): Int
}

data class SimView(val slotIndex: Int, val simNumber: Int, val phoneNumber: String?, val carrierName: String?)

data class LogView(val id: Long, val level: String, val tag: String, val message: String, val createdAt: Long)

interface LogReader {
    suspend fun list(from: Long, to: Long, limit: Int): List<LogView>
}

/** Facts about this phone that only Android can answer. */
interface DeviceInfo {
    val deviceId: String
    val deviceName: String
    val installedAt: Long
    fun sims(): List<SimView>
    /** "pass", "warn" or "fail" with a reason per failing check. */
    fun health(): Pair<String, Map<String, String>>
}

/** Credentials for the HTTP API. */
interface ApiCredentials {
    val username: String
    val password: String
}

/** Where the engine reads its pacing rules; changing them takes effect on the next message. */
interface SendSettings {
    val sendIntervalMinSec: Int
    val sendIntervalMaxSec: Int
    val rateLimitCount: Int
    val rateLimitPeriodMin: Int
}

interface AppLog {
    fun log(level: String, tag: String, message: String)
}

fun AppLog.info(tag: String, message: String) = log("INFO", tag, message)
fun AppLog.warn(tag: String, message: String) = log("WARN", tag, message)
fun AppLog.error(tag: String, message: String) = log("ERROR", tag, message)

object NoLog : AppLog {
    override fun log(level: String, tag: String, message: String) = Unit
}
