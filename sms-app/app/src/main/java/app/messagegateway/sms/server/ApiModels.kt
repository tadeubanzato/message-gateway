package app.messagegateway.sms.server

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement

/** Same wire format as the SMS Gateway for Android API, so existing clients keep working. */
val ApiJson = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}

@Serializable
data class TextMessageDto(val text: String)

@Serializable
data class PostMessageRequest(
    val id: String? = null,
    val message: String? = null,
    val textMessage: TextMessageDto? = null,
    val dataMessage: JsonElement? = null,
    val mmsMessage: JsonElement? = null,
    val phoneNumbers: List<String> = emptyList(),
    val simNumber: Int? = null,
    val withDeliveryReport: Boolean? = null,
    val isEncrypted: Boolean? = null,
    val priority: Int = 0,
    val deviceId: String? = null,
    val ttl: Long? = null,
    val validUntil: String? = null,
    val scheduleAt: String? = null,
)

@Serializable
data class RecipientDto(val phoneNumber: String, val state: String, val error: String? = null)

@Serializable
data class MessageDto(
    val id: String,
    val deviceId: String,
    val state: String,
    val createdAt: String,
    val isHashed: Boolean = false,
    val isEncrypted: Boolean = false,
    val textMessage: TextMessageDto? = null,
    val recipients: List<RecipientDto>,
    val states: Map<String, String>,
    val scheduleAt: String? = null,
)

@Serializable
data class SimCardDto(val slotIndex: Int, val simNumber: Int, val phoneNumber: String? = null, val carrierName: String? = null)

@Serializable
data class DeviceDto(
    val id: String,
    val name: String,
    val createdAt: String,
    val updatedAt: String,
    val lastSeen: String,
    val simCards: List<SimCardDto>,
)

@Serializable
data class WebhookDto(val id: String? = null, val deviceId: String? = null, val url: String, val event: String, val source: String = "Local")

@Serializable
data class InboxDto(
    val id: String,
    val type: String = "SMS",
    val sender: String,
    val simNumber: Int? = null,
    val content: String,
    val createdAt: String,
)

@Serializable
data class LogDto(val id: Long, val priority: String, val module: String, val message: String, val createdAt: String)

@Serializable
data class CheckDto(val status: String, val description: String)

@Serializable
data class HealthDto(val status: String, val version: String, val releaseId: Int, val checks: Map<String, CheckDto>)

@Serializable
data class ErrorDto(val message: String)

@Serializable
data class RootDto(val status: String, val model: String)
