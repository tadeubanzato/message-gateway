package app.messagegateway.sms.cloud

import io.ktor.http.ContentType
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpStatusCode
import io.ktor.server.application.ApplicationCall
import io.ktor.server.cio.CIO
import io.ktor.server.engine.EmbeddedServer
import io.ktor.server.engine.embeddedServer
import io.ktor.server.request.header
import io.ktor.server.request.httpMethod
import io.ktor.server.request.receiveText
import io.ktor.server.response.respondText
import io.ktor.server.response.respondTextWriter
import io.ktor.server.routing.get
import io.ktor.server.routing.patch
import io.ktor.server.routing.post
import io.ktor.server.routing.routing
import kotlinx.coroutines.runBlocking
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.util.Base64
import java.util.concurrent.CopyOnWriteArrayList

/** Exercises [HttpCloudApi] against a stand-in for the SMSGate server on a real local socket. */
class HttpCloudApiTest {
    private data class Seen(val method: String, val path: String, val query: String, val auth: String?, val body: String)

    private val seen = CopyOnWriteArrayList<Seen>()
    private lateinit var server: EmbeddedServer<*, *>
    private lateinit var api: HttpCloudApi

    private suspend fun ApplicationCall.record(): Seen {
        val entry = Seen(
            request.httpMethod.value, request.local.uri.substringBefore('?'), request.local.uri.substringAfter('?', ""),
            request.header(HttpHeaders.Authorization), receiveText(),
        )
        seen += entry
        return entry
    }

    private suspend fun ApplicationCall.json(body: String, status: HttpStatusCode = HttpStatusCode.OK) =
        respondText(body, ContentType.Application.Json, status)

    @Before fun start() {
        server = embeddedServer(CIO, port = 0, host = "127.0.0.1") {
            routing {
                post("/api/mobile/v1/device") {
                    call.record()
                    call.json("""{"id":"d1","token":"t1","login":"U1","password":"p1","extra":"ignored"}""", HttpStatusCode.Created)
                }
                patch("/api/mobile/v1/device") { call.record(); call.respondText("", status = HttpStatusCode.NoContent) }
                get("/api/mobile/v1/message") {
                    val e = call.record()
                    if (e.auth == "Bearer bad") return@get call.json("""{"message":"nope"}""", HttpStatusCode.Unauthorized)
                    call.json(
                        """[{"id":"m1","textMessage":{"text":"hi"},"phoneNumbers":["+14155552671"],"state":"Pending","priority":0,"unknown":1}]"""
                    )
                }
                patch("/api/mobile/v1/message") { call.record(); call.respondText("", status = HttpStatusCode.NoContent) }
                get("/api/mobile/v1/webhooks") { call.record(); call.json("""[{"id":"w1","url":"https://x.example/h","event":"sms:received"}]""") }
                patch("/api/mobile/v1/user/password") {
                    val e = call.record()
                    if (e.body.contains("\"currentPassword\":\"wrong\"")) call.json("""{"message":"bad password"}""", HttpStatusCode.Unauthorized)
                    else call.respondText("", status = HttpStatusCode.NoContent)
                }
                get("/api/mobile/v1/boom") { call.record(); call.json("""{"message":"database is down"}""", HttpStatusCode.InternalServerError) }
                get("/api/mobile/v1/events") {
                    call.record()
                    call.respondTextWriter(ContentType.Text.EventStream) {
                        write(":keepalive\n\n"); flush()
                        write("event: MessageEnqueued\ndata: {}\n\n"); flush()
                        write(":keepalive\n\n"); flush()
                        write("event: WebhooksUpdated\ndata: {\"x\":1}\n\n"); flush()
                    }
                }
            }
        }.also { it.start(wait = false) }
        val port = runBlocking { server.engine.resolvedConnectors().first().port }
        api = HttpCloudApi("http://127.0.0.1:$port/api/mobile/v1/", "test-agent")
    }

    @After fun stop() {
        server.stop(100, 500)
    }

    @Test fun `registering on a public server sends no credentials`() = runBlocking {
        val result = api.register(CloudRegisterRequest("Pixel", null, listOf(CloudSimCard(0, 1, null, "T-Mobile"))), CloudAuth.None)
        assertEquals(CloudRegisterResponse("d1", "t1", "U1", "p1"), result)
        val request = seen.single()
        assertNull(request.auth)
        assertTrue(request.body.contains("\"name\":\"Pixel\""))
        assertTrue(request.body.contains("\"carrierName\":\"T-Mobile\""))
    }

    @Test fun `registering on a private server sends the key as a bearer token`() = runBlocking {
        api.register(CloudRegisterRequest("Pixel"), CloudAuth.ServerKey("secret-key"))
        assertEquals("Bearer secret-key", seen.single().auth)
    }

    @Test fun `joining an existing account sends basic credentials`() = runBlocking {
        api.register(CloudRegisterRequest("Pixel"), CloudAuth.Account("me", "pw"))
        assertEquals("Basic " + Base64.getEncoder().encodeToString("me:pw".toByteArray()), seen.single().auth)
    }

    @Test fun `pending messages are fetched oldest first with the device token`() = runBlocking {
        val messages = api.getMessages("t1")
        assertEquals("m1", messages.single().id)
        assertEquals("hi", messages.single().textMessage?.text)
        assertEquals("Bearer t1", seen.single().auth)
        assertEquals("order=fifo", seen.single().query)
    }

    @Test fun `a rejected token is reported with its status`() {
        val error = assertThrows(CloudHttpException::class.java) { runBlocking { api.getMessages("bad") } }
        assertEquals(401, error.code)
        assertTrue(error.message!!.contains("nope"))
    }

    @Test fun `reports are sent as a json list`() = runBlocking {
        api.patchMessages("t1", listOf(CloudMessagePatch("m1", "Sent", listOf(CloudRecipientState("+1", "Sent")), mapOf("Sent" to "2026-10-16T21:04:05.123+00:00"))))
        val request = seen.single()
        assertEquals("PATCH", request.method)
        assertTrue(request.body.startsWith("[{\"id\":\"m1\",\"state\":\"Sent\""))
        assertTrue(request.body.contains("\"states\":{\"Sent\":\"2026-10-16T21:04:05.123+00:00\"}"))
    }

    @Test fun `webhooks are fetched`() = runBlocking {
        assertEquals(listOf(CloudWebhook("w1", "https://x.example/h", "sms:received")), api.getWebhooks("t1"))
    }

    @Test fun `changing the password sends both passwords`() = runBlocking {
        api.changePassword("t1", CloudPasswordChange("old", "new"))
        val request = seen.single()
        assertEquals("PATCH", request.method)
        assertEquals("/api/mobile/v1/user/password", request.path)
        assertEquals("""{"currentPassword":"old","newPassword":"new"}""", request.body)
    }

    @Test fun `a wrong current password is refused`() {
        val error = assertThrows(CloudHttpException::class.java) {
            runBlocking { api.changePassword("t1", CloudPasswordChange("wrong", "new")) }
        }
        assertEquals(401, error.code)
    }

    @Test fun `the event stream delivers events in order and ends when the server closes it`() = runBlocking {
        val events = mutableListOf<Pair<String, String>>()
        var opened = false
        api.streamEvents("t1", onOpen = { opened = true }, onEvent = { name, data -> events += name to data })
        assertTrue(opened)
        assertEquals(listOf("MessageEnqueued" to "{}", "WebhooksUpdated" to "{\"x\":1}"), events)
        assertEquals("Bearer t1", seen.single().auth)
    }
}
