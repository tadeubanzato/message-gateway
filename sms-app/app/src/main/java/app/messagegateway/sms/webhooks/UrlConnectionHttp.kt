package app.messagegateway.sms.webhooks

import java.net.HttpURLConnection
import java.net.URL

/** Webhook delivery over the platform's own HTTP client: no extra library on the phone. */
class UrlConnectionHttp : WebhookHttp {
    private companion object {
        const val MAX_REPLY = 2_000
    }

    override fun post(url: String, body: String, headers: Map<String, String>): WebhookResponse {
        val connection = URL(url).openConnection() as HttpURLConnection
        try {
            connection.requestMethod = "POST"
            connection.connectTimeout = 10_000
            connection.readTimeout = 15_000
            connection.doOutput = true
            headers.forEach { (name, value) -> connection.setRequestProperty(name, value) }
            connection.outputStream.use { it.write(body.toByteArray()) }
            val code = connection.responseCode
            // Read the response so the connection can be reused, and so the caller can see what the receiver said.
            val reply = (if (code >= 400) connection.errorStream else connection.inputStream)
                ?.use { it.readBytes().take(MAX_REPLY).toByteArray().decodeToString() }.orEmpty()
            return WebhookResponse(code, reply)
        } finally {
            connection.disconnect()
        }
    }
}
