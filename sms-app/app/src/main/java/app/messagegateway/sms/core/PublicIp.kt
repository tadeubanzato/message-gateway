package app.messagegateway.sms.core

import app.messagegateway.sms.cloud.CloudUrls
import app.messagegateway.sms.cloud.HttpCloudApi
import io.ktor.client.HttpClient
import io.ktor.client.request.get
import io.ktor.client.statement.bodyAsText
import io.ktor.http.isSuccess
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Finds the address the internet sees this phone as. Like the official SMSGate app, it asks the SMSGate server,
 * whose device endpoint echoes the caller's IP (no account needed). That is a call to a third party, so it can be
 * switched off in Settings.
 */
class PublicIp(
    private val url: String = CloudUrls.PUBLIC_MOBILE + "/device",
    private val client: HttpClient = HttpCloudApi.defaultClient("message-gateway-sms"),
) {
    val host: String get() = java.net.URI(url).host

    /** Throws when the lookup fails; the message is meant for the user. */
    suspend fun lookup(): String {
        val response = client.get(url)
        val text = response.bodyAsText()
        if (!response.status.isSuccess()) error("The server answered HTTP ${response.status.value}")
        return parse(text) ?: error("The server didn't say what your address is")
    }

    companion object {
        private val ipv4 = Regex("""^\d{1,3}(\.\d{1,3}){3}$""")
        private val ipv6 = Regex("""^[0-9a-fA-F:]+$""")

        /** Reads `externalIp` from the server's reply; null when it is missing or not an IP address. */
        fun parse(body: String): String? {
            val ip = try {
                Json.parseToJsonElement(body).jsonObject["externalIp"]?.jsonPrimitive?.contentOrNull
            } catch (_: Exception) {
                null
            }?.trim()
            return ip?.takeIf { it.isNotEmpty() && (ipv4.matches(it) || (it.contains(':') && ipv6.matches(it))) }
        }
    }
}
