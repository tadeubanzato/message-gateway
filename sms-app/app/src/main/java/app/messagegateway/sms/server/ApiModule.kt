package app.messagegateway.sms.server

import app.messagegateway.sms.core.ApiCredentials
import app.messagegateway.sms.core.ConflictException
import app.messagegateway.sms.core.DeviceInfo
import app.messagegateway.sms.core.InboxReader
import app.messagegateway.sms.core.InvalidStateException
import app.messagegateway.sms.core.Iso
import app.messagegateway.sms.core.LogReader
import app.messagegateway.sms.core.MessageService
import app.messagegateway.sms.core.MessageView
import app.messagegateway.sms.core.NewMessage
import app.messagegateway.sms.core.WebhookRegistry
import app.messagegateway.sms.domain.ProcessingState
import io.ktor.http.HttpStatusCode
import io.ktor.serialization.kotlinx.json.json
import io.ktor.server.application.Application
import io.ktor.server.application.install
import io.ktor.server.auth.Authentication
import io.ktor.server.auth.UserIdPrincipal
import io.ktor.server.auth.authenticate
import io.ktor.server.auth.basic
import io.ktor.server.plugins.BadRequestException
import io.ktor.server.plugins.contentnegotiation.ContentNegotiation
import io.ktor.server.plugins.statuspages.StatusPages
import io.ktor.server.request.receive
import io.ktor.server.response.header
import io.ktor.server.response.respond
import io.ktor.server.routing.Route
import io.ktor.server.routing.delete
import io.ktor.server.routing.get
import io.ktor.server.routing.post
import io.ktor.server.routing.route
import io.ktor.server.routing.routing
import kotlinx.serialization.SerializationException
import java.security.MessageDigest
import app.messagegateway.sms.core.NotFoundException as MessageNotFound

/** Everything the HTTP API needs. The Android app wires real implementations; tests wire fakes. */
class ApiDeps(
    val messages: MessageService,
    val webhooks: WebhookRegistry,
    val inbox: InboxReader,
    val logs: LogReader,
    val device: DeviceInfo,
    val credentials: ApiCredentials,
    val appVersion: String,
    val appVersionCode: Int,
    val clock: () -> Long = System::currentTimeMillis,
)

fun Application.apiModule(deps: ApiDeps) {
    install(ContentNegotiation) { json(ApiJson) }

    install(StatusPages) {
        exception<Throwable> { call, cause ->
            val status = when (cause) {
                is IllegalArgumentException, is BadRequestException, is SerializationException, is InvalidStateException ->
                    HttpStatusCode.BadRequest
                is ConflictException -> HttpStatusCode.Conflict
                is MessageNotFound -> HttpStatusCode.NotFound
                else -> HttpStatusCode.InternalServerError
            }
            val text = (cause.cause?.message?.takeIf { cause is BadRequestException } ?: cause.message)
                ?: cause.javaClass.simpleName
            call.respond(status, ErrorDto(text))
        }
    }

    install(Authentication) {
        basic("auth-basic") {
            realm = "Access to SMS Gateway"
            validate { credentials ->
                val user = MessageDigest.isEqual(credentials.name.toByteArray(), deps.credentials.username.toByteArray())
                val pass = MessageDigest.isEqual(credentials.password.toByteArray(), deps.credentials.password.toByteArray())
                if (user && pass) UserIdPrincipal(credentials.name) else null
            }
        }
    }

    routing {
        get("/health") {
            val (status, failing) = deps.device.health()
            val checks = if (failing.isEmpty()) {
                mapOf("sms" to CheckDto("pass", "SMS permissions granted"))
            } else {
                failing.mapValues { CheckDto(status, it.value) }
            }
            val code = if (status == "fail") HttpStatusCode.InternalServerError else HttpStatusCode.OK
            call.respond(code, HealthDto(status, deps.appVersion, deps.appVersionCode, checks))
        }

        authenticate("auth-basic") {
            get("/") { call.respond(RootDto("ok", deps.device.deviceName)) }

            for (path in listOf("/device", "/devices")) {
                route(path) { deviceRoutes(deps) }
            }
            for (path in listOf("/message", "/messages")) {
                route(path) { messageRoutes(deps) }
            }
            for (path in listOf("/webhook", "/webhooks")) {
                route(path) { webhookRoutes(deps) }
            }
            route("/inbox") { inboxRoutes(deps) }
            route("/logs") { logRoutes(deps) }
        }
    }
}

private fun Route.deviceRoutes(deps: ApiDeps) {
    get {
        val now = Iso.format(deps.clock())
        val device = deps.device
        call.respond(
            listOf(
                DeviceDto(
                    id = device.deviceId,
                    name = device.deviceName,
                    createdAt = Iso.format(device.installedAt),
                    updatedAt = now,
                    lastSeen = now,
                    simCards = device.sims().map { SimCardDto(it.slotIndex, it.simNumber, it.phoneNumber, it.carrierName) },
                )
            )
        )
    }
}

private fun Route.messageRoutes(deps: ApiDeps) {
    get {
        val q = call.request.queryParameters
        val state = q["state"]?.takeIf { it.isNotEmpty() }?.let { value ->
            ProcessingState.entries.firstOrNull { it.name == value }
                ?: throw IllegalArgumentException("state must be one of: ${ProcessingState.entries.joinToString()}")
        }
        val limit = (q["limit"]?.toIntOrNull() ?: 50).coerceIn(1, 100)
        val offset = (q["offset"]?.toIntOrNull() ?: 0).coerceAtLeast(0)
        val from = q["from"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("from must be an ISO-8601 date-time") } ?: 0L
        val to = q["to"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("to must be an ISO-8601 date-time") } ?: deps.clock()
        require(from <= to) { "Start date cannot be after end date" }
        q["deviceId"]?.let { require(it == deps.device.deviceId) { "Invalid device ID" } }
        val includeContent = when (val raw = q["includeContent"]) {
            null -> false
            "true" -> true
            "false" -> false
            else -> throw IllegalArgumentException("includeContent must be true or false (got $raw)")
        }

        val total = deps.messages.count(state, from, to)
        val items = deps.messages.list(state, from, to, limit, offset)
        call.response.header("X-Total-Count", total.toString())
        call.respond(items.map { it.toDto(includeContent) })
    }

    post {
        val request = call.receive<PostMessageRequest>()
        require(request.deviceId == null || request.deviceId == deps.device.deviceId) { "Invalid device ID" }
        require(request.dataMessage == null && request.mmsMessage == null) {
            "dataMessage and mmsMessage are not supported yet, use textMessage"
        }
        require(request.isEncrypted != true) { "Encrypted messages are not supported yet" }
        val texts = listOfNotNull(request.textMessage?.text, request.message)
        require(texts.isNotEmpty()) { "Must specify exactly one of: textMessage or message" }
        require(texts.size == 1) { "Cannot specify multiple message types simultaneously" }
        require(request.ttl == null || request.validUntil == null) { "fields conflict: ttl and validUntil" }

        val now = deps.clock()
        val validUntil = request.validUntil?.let { Iso.parse(it) ?: throw IllegalArgumentException("validUntil must be an ISO-8601 date-time") }
            ?: request.ttl?.let { now + it * 1000L }
        val scheduleAt = request.scheduleAt?.let { Iso.parse(it) ?: throw IllegalArgumentException("scheduleAt must be an ISO-8601 date-time") }

        val message = deps.messages.enqueue(
            NewMessage(
                id = request.id,
                text = texts.single(),
                phoneNumbers = request.phoneNumbers,
                simNumber = request.simNumber,
                withDeliveryReport = request.withDeliveryReport ?: true,
                priority = request.priority,
                scheduleAt = scheduleAt,
                validUntil = validUntil,
                skipPhoneValidation = call.request.queryParameters["skipPhoneValidation"]?.toBooleanStrictOrNull() ?: false,
            )
        )
        call.respond(HttpStatusCode.Accepted, message.toDto(includeContent = true))
    }

    get("{id}") {
        val id = call.parameters["id"] ?: throw IllegalArgumentException("id is required")
        val message = deps.messages.get(id) ?: throw MessageNotFound()
        call.respond(message.toDto(includeContent = true))
    }

    delete("{id}") {
        val id = call.parameters["id"] ?: throw IllegalArgumentException("id is required")
        call.respond(HttpStatusCode.OK, deps.messages.cancel(id).toDto(includeContent = true))
    }
}

private fun Route.webhookRoutes(deps: ApiDeps) {
    get {
        call.respond(deps.webhooks.list().map { WebhookDto(it.id, deps.device.deviceId, it.url, it.event) })
    }
    post {
        val body = call.receive<WebhookDto>()
        require(body.deviceId == null || body.deviceId == deps.device.deviceId) { "Device ID mismatch" }
        val saved = deps.webhooks.upsert(body.id, body.url, body.event)
        call.respond(HttpStatusCode.Created, WebhookDto(saved.id, deps.device.deviceId, saved.url, saved.event))
    }
    delete("{id}") {
        val id = call.parameters["id"] ?: throw IllegalArgumentException("id is required")
        deps.webhooks.delete(id)
        call.respond(HttpStatusCode.NoContent)
    }
}

private fun Route.inboxRoutes(deps: ApiDeps) {
    get {
        val q = call.request.queryParameters
        val limit = (q["limit"]?.toIntOrNull() ?: 50).coerceIn(1, 100)
        val offset = (q["offset"]?.toIntOrNull() ?: 0).coerceAtLeast(0)
        val from = q["from"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("from must be an ISO-8601 date-time") } ?: 0L
        val to = q["to"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("to must be an ISO-8601 date-time") } ?: deps.clock()
        call.response.header("X-Total-Count", deps.inbox.count(from, to).toString())
        call.respond(deps.inbox.list(from, to, limit, offset).map {
            InboxDto(it.id, "SMS", it.sender, it.simNumber, it.body, Iso.format(it.receivedAt))
        })
    }
}

private fun Route.logRoutes(deps: ApiDeps) {
    get {
        val q = call.request.queryParameters
        val from = q["from"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("from must be an ISO-8601 date-time") } ?: 0L
        val to = q["to"]?.let { Iso.parse(it) ?: throw IllegalArgumentException("to must be an ISO-8601 date-time") } ?: deps.clock()
        call.respond(deps.logs.list(from, to, 500).map { LogDto(it.id, it.level, it.tag, it.message, Iso.format(it.createdAt)) })
    }
}

internal fun MessageView.toDto(includeContent: Boolean) = MessageDto(
    id = id,
    deviceId = deviceId,
    state = state.name,
    createdAt = Iso.format(createdAt),
    textMessage = if (includeContent) TextMessageDto(text) else null,
    recipients = recipients.map { RecipientDto(it.phoneNumber, it.state.name, it.error) },
    states = states.entries.associate { it.key.name to Iso.format(it.value) },
    scheduleAt = scheduleAt?.let { Iso.format(it) },
)
