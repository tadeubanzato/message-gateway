package app.messagegateway.sms.cloud

import app.messagegateway.sms.core.AppLog
import app.messagegateway.sms.core.DeviceInfo
import app.messagegateway.sms.core.NotFoundException
import app.messagegateway.sms.core.InvalidStateException
import app.messagegateway.sms.core.error
import app.messagegateway.sms.core.info
import app.messagegateway.sms.core.warn
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.data.WebhookEntity
import app.messagegateway.sms.send.CloudReportSink
import app.messagegateway.sms.send.MessageStore
import app.messagegateway.sms.send.SendEngine
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

sealed interface CloudState {
    data object Disabled : CloudState
    data object Connecting : CloudState
    data class Connected(val since: Long) : CloudState
    data class Failed(val reason: String) : CloudState
}

/** What the cloud connection needs to know and remember. */
interface CloudSettings {
    val cloudEnabled: Boolean
    /** Address of the server's mobile API; throws IllegalArgumentException when the settings are incomplete. */
    fun mobileBaseUrl(): String
    /** How to prove who is asking when registering; throws IllegalStateException when the settings are incomplete. */
    fun registrationAuth(): CloudAuth
    var cloudDeviceToken: String?
    var cloudDeviceId: String?
    /** The password of this phone's account, when known. */
    var cloudUserPassword: String?
    fun saveRegistration(deviceId: String, token: String, login: String, password: String?)
}

/**
 * Links the phone to an SMSGate server. The server tells the phone over a held-open event stream that a
 * message is waiting; the phone then fetches it, sends it through the same queue as the local API, and reports
 * how it went. The server's webhooks are copied down so the phone can call them.
 *
 * Received texts are deliberately not uploaded to the server: its inbox only accepts end-to-end encrypted content, which
 * this app doesn't produce. They reach your systems through webhooks instead.
 *
 * This build can't receive Firebase push (the public server only knows the official app's push identity), so the
 * event stream is the only wake-up channel; a periodic sync is the safety net for when the stream is down.
 */
class CloudService(
    private val settings: CloudSettings,
    private val apiFactory: (baseUrl: String) -> CloudApi,
    private val engine: SendEngine,
    private val store: MessageStore,
    private val db: AppDatabase,
    private val deviceInfo: DeviceInfo,
    private val scope: CoroutineScope,
    private val log: AppLog,
    private val clock: () -> Long = System::currentTimeMillis,
) : CloudReportSink {

    val state = MutableStateFlow<CloudState>(CloudState.Disabled)
    val lastSyncAt = MutableStateFlow<Long?>(null)

    private var job: Job? = null
    private val syncLock = Mutex()
    private var cachedApi: Pair<String, CloudApi>? = null

    // --- Lifecycle -----------------------------------------------------------------------------------

    fun start() {
        if (job?.isActive == true) return
        job = scope.launch(Dispatchers.IO) { run() }
    }

    fun stop() {
        job?.cancel()
        job = null
        state.value = CloudState.Disabled
    }

    val running: Boolean get() = job?.isActive == true

    private suspend fun run() {
        var backoff = MIN_BACKOFF_MS
        while (scope.isActive) {
            try {
                state.value = CloudState.Connecting
                // Registers on first use and keeps the server's copy of this phone's SIM info current.
                ensureRegistered()
                // A problem with one sync step must not stop the phone from listening for new messages, or it would
                // only notice them when it next retries.
                try {
                    syncAll()
                } catch (e: CancellationException) {
                    throw e
                } catch (e: CloudHttpException) {
                    if (e.code == 401 || e.code == 403) throw e
                    log.warn(TAG, "Sync step failed, listening anyway: ${describe(e)}")
                }
                state.value = CloudState.Connected(clock())
                backoff = MIN_BACKOFF_MS
                listen()
                log.warn(TAG, "Event stream closed by the server, reconnecting")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                state.value = CloudState.Failed(describe(e))
                log.error(TAG, "Cloud connection failed: ${describe(e)}")
            }
            delay(backoff)
            backoff = (backoff * 2).coerceAtMost(MAX_BACKOFF_MS)
        }
    }

    private suspend fun listen() = coroutineScope {
        val events = Channel<Pair<String, String>>(Channel.UNLIMITED)
        val consumer = launch {
            for ((name, data) in events) {
                try {
                    handleEvent(name, data)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    log.error(TAG, "Could not handle $name: ${describe(e)}")
                }
            }
        }
        try {
            authed { api, token ->
                api.streamEvents(
                    token,
                    onOpen = { state.value = CloudState.Connected(clock()) },
                    onEvent = { name, data -> events.trySend(name to data) },
                )
            }
        } finally {
            events.close()
            consumer.cancel()
        }
    }

    private suspend fun handleEvent(name: String, data: String) {
        when (name) {
            "MessageEnqueued", "MessageCancelled" -> syncLock.withLock { pullMessages(); flushReports() }
            "WebhooksUpdated" -> syncLock.withLock { syncWebhooks() }
            else -> log.info(TAG, "Ignoring event $name")
        }
    }

    // --- Account ---------------------------------------------------------------------------------------

    /**
     * Changes the account's password on the server and remembers the new one. Everything else that signs in to
     * this account (a gateway, a script) must be given the new password too.
     */
    suspend fun changePassword(newPassword: String) {
        val current = settings.cloudUserPassword ?: error("This phone doesn't know the account's current password")
        authed { api, token -> api.changePassword(token, CloudPasswordChange(current, newPassword)) }
        settings.cloudUserPassword = newPassword
        log.info(TAG, "Account password changed")
    }

    // --- Registration --------------------------------------------------------------------------------

    private fun api(): CloudApi {
        val base = settings.mobileBaseUrl()
        cachedApi?.takeIf { it.first == base }?.let { return it.second }
        return apiFactory(base).also { cachedApi = base to it }
    }

    private suspend fun ensureRegistered() {
        val token = settings.cloudDeviceToken
        if (token == null) {
            register()
            return
        }
        // Already registered: refresh what the server knows about this phone. A rejected token means the
        // device was removed on the server, so register again.
        try {
            api().patchDevice(token, CloudDevicePatch(settings.cloudDeviceId.orEmpty(), null, sims()))
        } catch (e: CloudHttpException) {
            if (e.code != 401 && e.code != 403) throw e
            register()
        }
    }

    private suspend fun register(): String {
        val auth = settings.registrationAuth()
        val response = api().register(CloudRegisterRequest(deviceInfo.deviceName, null, sims()), auth)
        settings.saveRegistration(response.id, response.token, response.login, response.password)
        log.info(TAG, "Registered with the server as device ${response.id}")
        return response.token
    }

    private fun sims() = deviceInfo.sims().map { CloudSimCard(it.slotIndex, it.simNumber, null, it.carrierName) }

    /** Runs [block] with the device token, registering again once if the server no longer accepts it. */
    private suspend fun <T> authed(block: suspend (CloudApi, String) -> T): T {
        val token = settings.cloudDeviceToken ?: register()
        try {
            return block(api(), token)
        } catch (e: CloudHttpException) {
            if (e.code != 401) throw e
        }
        return block(api(), register())
    }

    // --- Sync ----------------------------------------------------------------------------------------

    /** One full pass: fetch waiting messages, copy webhooks, send pending reports and received texts. */
    suspend fun syncAll() = syncLock.withLock {
        pullMessages()
        syncWebhooks()
        flushReports()
        lastSyncAt.value = clock()
    }

    /** For the periodic background job: does nothing unless the phone is registered. */
    suspend fun syncOnce() {
        if (!settings.cloudEnabled || settings.cloudDeviceToken == null) return
        syncAll()
    }

    private suspend fun pullMessages() {
        val waiting = authed { api, token -> api.getMessages(token) }
        for (m in waiting) {
            try {
                when (m.state) {
                    null, "Pending" -> accept(m)
                    "Cancelling" -> cancel(m)
                    else -> Unit
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log.error(TAG, "Could not process message ${m.id}: ${describe(e)}")
            }
        }
    }

    private suspend fun accept(m: CloudMessage) {
        // Pulled twice (e.g. the report didn't get through yet): don't queue it again, just report where it is.
        if (store.get(m.id) != null) {
            db.cloud().markDirty(m.id, clock())
            return
        }
        when (val mapped = CloudMapping.toNewMessage(m, clock())) {
            is CloudMapping.Mapped.Accept -> try {
                engine.enqueue(mapped.message)
                log.info(TAG, "Queued cloud message ${m.id} for ${m.phoneNumbers.size} recipient(s)")
            } catch (e: IllegalArgumentException) {
                reject(m, e.message ?: "Invalid message")
            }
            is CloudMapping.Mapped.Reject -> reject(m, mapped.reason)
        }
    }

    private suspend fun reject(m: CloudMessage, reason: String) {
        log.warn(TAG, "Rejecting cloud message ${m.id}: $reason")
        authed { api, token -> api.patchMessages(token, listOf(CloudMapping.failedPatch(m, reason, clock()))) }
    }

    private suspend fun cancel(m: CloudMessage) {
        try {
            store.cancel(m.id)
        } catch (_: NotFoundException) {
            // Never queued here, so there is nothing to stop: confirm the cancel to the server.
            authed { api, token -> api.patchMessages(token, listOf(CloudMapping.cancelledPatch(m, clock()))) }
        } catch (_: InvalidStateException) {
            // Already on its way out; its normal reports will tell the server.
        }
    }

    private suspend fun syncWebhooks() {
        val hooks = authed { api, token -> api.getWebhooks(token) }
        db.webhooks().replaceCloud(hooks.map { WebhookEntity(it.id, it.url, it.event, source = "Cloud") })
    }

    // --- Reports and uploads -------------------------------------------------------------------------

    override suspend fun stateChanged(messageId: String) {
        db.cloud().markDirty(messageId, clock())
        kickFlush()
    }

    private fun kickFlush() {
        if (!settings.cloudEnabled) return
        scope.launch(Dispatchers.IO) {
            try {
                syncLock.withLock { flushReports() }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Stays queued; the next connection or periodic sync sends it.
                log.warn(TAG, "Could not report to the server yet: ${describe(e)}")
            }
        }
    }

    private suspend fun flushReports() {
        if (settings.cloudDeviceToken == null) return
        while (true) {
            val due = db.cloud().due(clock(), REPORT_BATCH)
            if (due.isEmpty()) return
            val patches = due.mapNotNull { report -> store.get(report.messageId)?.let(CloudMapping::toPatch) }
            try {
                if (patches.isNotEmpty()) authed { api, token -> api.patchMessages(token, patches) }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                due.forEach {
                    val attempts = it.attempts + 1
                    db.cloud().retryLater(it.messageId, it.version, attempts, clock() + retryDelay(attempts))
                }
                throw e
            }
            // Only clears a report if the message did not change again while this one was being sent.
            due.forEach { db.cloud().deleteIfUnchanged(it.messageId, it.version) }
        }
    }

    private fun describe(e: Exception): String = when (e) {
        is CloudHttpException -> when (e.code) {
            401, 403 -> "The server rejected the credentials (HTTP ${e.code})"
            else -> e.message ?: "HTTP ${e.code}"
        }
        is java.net.UnknownHostException -> "Can't find the server (no internet, or a wrong address)"
        is java.net.ConnectException -> "Can't connect to the server"
        else -> e.message ?: e.javaClass.simpleName
    }

    companion object {
        private const val TAG = "Cloud"
        private const val MIN_BACKOFF_MS = 2_000L
        private const val MAX_BACKOFF_MS = 5 * 60_000L
        private const val REPORT_BATCH = 50

        fun retryDelay(attempts: Int): Long = minOf(15_000L shl (attempts - 1).coerceIn(0, 10), 15 * 60_000L)
    }
}
