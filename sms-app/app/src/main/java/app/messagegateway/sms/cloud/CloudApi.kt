package app.messagegateway.sms.cloud

import app.messagegateway.sms.server.ApiJson
import io.ktor.client.HttpClient
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.HttpTimeoutConfig
import io.ktor.client.plugins.timeout
import io.ktor.client.plugins.UserAgent
import io.ktor.client.request.header
import io.ktor.client.request.prepareGet
import io.ktor.client.request.request
import io.ktor.client.request.setBody
import io.ktor.client.statement.bodyAsChannel
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import io.ktor.utils.io.readUTF8Line
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.encodeToString
import java.util.Base64

/** The calls the phone makes to the SMSGate server. */
interface CloudApi {
    suspend fun register(request: CloudRegisterRequest, auth: CloudAuth): CloudRegisterResponse
    suspend fun patchDevice(token: String, body: CloudDevicePatch)
    suspend fun getMessages(token: String): List<CloudMessage>
    suspend fun patchMessages(token: String, body: List<CloudMessagePatch>)
    suspend fun getWebhooks(token: String): List<CloudWebhook>

    /** Changes the password of the account this phone belongs to. */
    suspend fun changePassword(token: String, body: CloudPasswordChange)

    /**
     * Holds the event stream open until it ends or the caller is cancelled. [onOpen] runs once the server
     * has accepted the connection; [onEvent] runs for each event.
     */
    suspend fun streamEvents(token: String, onOpen: suspend () -> Unit, onEvent: suspend (name: String, data: String) -> Unit)
}

class HttpCloudApi(
    baseUrl: String,
    userAgent: String,
    private val client: HttpClient = defaultClient(userAgent),
) : CloudApi {
    private val base = baseUrl.trimEnd('/')

    override suspend fun register(request: CloudRegisterRequest, auth: CloudAuth): CloudRegisterResponse {
        val header = when (auth) {
            CloudAuth.None -> null
            is CloudAuth.ServerKey -> "Bearer ${auth.key}"
            is CloudAuth.Account -> "Basic " + Base64.getEncoder().encodeToString("${auth.login}:${auth.password}".toByteArray())
        }
        return ApiJson.decodeFromString(send(HttpMethod.Post, "/device", header, ApiJson.encodeToString(request)))
    }

    override suspend fun patchDevice(token: String, body: CloudDevicePatch) {
        send(HttpMethod.Patch, "/device", bearer(token), ApiJson.encodeToString(body))
    }

    override suspend fun getMessages(token: String): List<CloudMessage> =
        ApiJson.decodeFromString(ListSerializer(CloudMessage.serializer()), send(HttpMethod.Get, "/message?order=fifo", bearer(token), null))

    override suspend fun patchMessages(token: String, body: List<CloudMessagePatch>) {
        send(HttpMethod.Patch, "/message", bearer(token), ApiJson.encodeToString(ListSerializer(CloudMessagePatch.serializer()), body))
    }

    override suspend fun getWebhooks(token: String): List<CloudWebhook> =
        ApiJson.decodeFromString(ListSerializer(CloudWebhook.serializer()), send(HttpMethod.Get, "/webhooks", bearer(token), null))

    override suspend fun changePassword(token: String, body: CloudPasswordChange) {
        send(HttpMethod.Patch, "/user/password", bearer(token), ApiJson.encodeToString(body))
    }

    override suspend fun streamEvents(token: String, onOpen: suspend () -> Unit, onEvent: suspend (String, String) -> Unit) {
        client.prepareGet("$base/events") {
            header(HttpHeaders.Authorization, bearer(token))
            header(HttpHeaders.Accept, "text/event-stream")
            // The stream stays open for hours; the server sends a keepalive every 15s, so a minute of silence means it died.
            timeout {
                requestTimeoutMillis = HttpTimeoutConfig.INFINITE_TIMEOUT_MS
                socketTimeoutMillis = STREAM_SILENCE_MS
            }
        }.execute { response ->
            if (!response.status.isSuccess()) throw CloudHttpException(response.status.value, response.bodyAsText())
            onOpen()
            val channel = response.bodyAsChannel()
            val parser = SseParser()
            while (true) {
                val line = channel.readUTF8Line() ?: break
                parser.feed(line)?.let { onEvent(it.name, it.data) }
            }
        }
    }

    private fun bearer(token: String) = "Bearer $token"

    private suspend fun send(method: HttpMethod, path: String, authorization: String?, body: String?): String {
        val response = client.request("$base$path") {
            this.method = method
            authorization?.let { header(HttpHeaders.Authorization, it) }
            if (body != null) {
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        val text = response.bodyAsText()
        if (!response.status.isSuccess()) throw CloudHttpException(response.status.value, text)
        return text
    }

    companion object {
        const val STREAM_SILENCE_MS = 60_000L

        fun defaultClient(userAgent: String) = HttpClient(OkHttp) {
            install(UserAgent) { agent = userAgent }
            install(HttpTimeout) {
                connectTimeoutMillis = 15_000
                requestTimeoutMillis = 30_000
                socketTimeoutMillis = 30_000
            }
        }
    }
}
