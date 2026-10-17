package app.messagegateway.sms.ui

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import app.messagegateway.sms.AppContainer
import app.messagegateway.sms.cloud.CloudState
import app.messagegateway.sms.core.LocalAddress
import app.messagegateway.sms.core.NewMessage
import app.messagegateway.sms.data.IncomingEntity
import app.messagegateway.sms.data.LogEntity
import app.messagegateway.sms.data.MessageWithRecipients
import app.messagegateway.sms.data.WebhookDeliveryEntity
import app.messagegateway.sms.data.WebhookEntity
import app.messagegateway.sms.server.GatewayService
import app.messagegateway.sms.server.ServerState
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.combine
import kotlinx.coroutines.flow.flatMapLatest
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import kotlinx.coroutines.launch

class AppViewModel(private val c: AppContainer) : ViewModel() {
    val prefs = c.prefs
    val serverState: StateFlow<ServerState> = c.serverState

    val messages: StateFlow<List<MessageWithRecipients>> = c.db.messages().observeRecent(300).state(emptyList())
    val pendingMessages: StateFlow<Int> = c.db.messages().observePendingCount().state(0)
    val incoming: StateFlow<List<IncomingEntity>> = c.db.incoming().observeRecent(300).state(emptyList())
    val webhooks: StateFlow<List<WebhookEntity>> = c.db.webhooks().observeAll().state(emptyList())
    val deliveries: StateFlow<List<WebhookDeliveryEntity>> = c.db.webhooks().observeDeliveries(100).state(emptyList())
    val pendingDeliveries: StateFlow<Int> = c.db.webhooks().observePendingCount().state(0)
    val logs: StateFlow<List<LogEntity>> = c.db.logs().observeRecent(300).state(emptyList())

    private fun <T> kotlinx.coroutines.flow.Flow<T>.state(initial: T) =
        stateIn(viewModelScope, SharingStarted.WhileSubscribed(5_000), initial)

    // --- Activity chart ---

    private val _trendDays = kotlinx.coroutines.flow.MutableStateFlow(7)
    val trendDays: StateFlow<Int> = _trendDays

    fun setTrendDays(days: Int) {
        _trendDays.value = days
    }

    /** Per-day counts for the chosen range. Stays on the previous bars while a new range loads. */
    @OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)
    val trendBuckets: StateFlow<List<DayBucket>> = _trendDays.flatMapLatest { days ->
        val zone = java.time.ZoneId.systemDefault()
        val today = java.time.LocalDate.now(zone)
        val since = Trends.startMillis(days, today, zone)
        combine(c.db.messages().observeOutgoingSince(since), c.db.incoming().observeReceivedSince(since)) { outgoing, received ->
            Trends.buckets(days, today, zone, outgoing, received)
        }
    }.state(emptyList())

    fun localAddresses(): List<LocalAddress> = c.deviceInfo.localAddresses()

    sealed interface PublicIpState {
        data object Off : PublicIpState
        data object Idle : PublicIpState
        data object Loading : PublicIpState
        data class Found(val ip: String) : PublicIpState
        data class Failed(val reason: String) : PublicIpState
    }

    private val _publicIp = kotlinx.coroutines.flow.MutableStateFlow<PublicIpState>(PublicIpState.Idle)
    val publicIp: StateFlow<PublicIpState> = _publicIp
    val publicIpHost: String get() = c.publicIp.host

    /** Looks the public address up once per app run, or again when [force] is set (the Refresh button). */
    fun refreshPublicIp(force: Boolean = false) {
        if (!prefs.lookupPublicIp) {
            _publicIp.value = PublicIpState.Off
            return
        }
        val current = _publicIp.value
        if (current is PublicIpState.Loading || (!force && current is PublicIpState.Found)) return
        _publicIp.value = PublicIpState.Loading
        viewModelScope.launch {
            _publicIp.value = try {
                PublicIpState.Found(c.publicIp.lookup())
            } catch (e: Exception) {
                PublicIpState.Failed(
                    when (e) {
                        is java.net.UnknownHostException, is java.net.ConnectException -> "No internet connection"
                        else -> e.message ?: e.javaClass.simpleName
                    }
                )
            }
        }
    }

    fun setServerEnabled(context: Context, enabled: Boolean) {
        prefs.serverEnabled = enabled
        GatewayService.refresh(context)
    }

    // --- Cloud ---

    val cloudState: StateFlow<CloudState> = c.cloud.state
    val cloudLastSync: StateFlow<Long?> = c.cloud.lastSyncAt

    /** Null when the saved cloud settings are complete, otherwise what is missing. */
    fun cloudProblem(): String? = try {
        prefs.mobileBaseUrl()
        prefs.registrationAuth()
        null
    } catch (e: Exception) {
        e.message ?: "Incomplete settings"
    }

    /** Switches cloud on with the settings saved in [prefs]; registers this phone if it isn't yet. */
    fun connectCloud(context: Context) {
        prefs.cloudEnabled = true
        c.cloud.stop()
        GatewayService.refresh(context)
    }

    fun setCloudEnabled(context: Context, enabled: Boolean) {
        prefs.cloudEnabled = enabled
        if (!enabled) c.cloud.stop()
        GatewayService.refresh(context)
    }

    /** Switches cloud off and forgets this phone's registration, so connecting again registers anew. */
    fun forgetCloud(context: Context) {
        setCloudEnabled(context, false)
        prefs.clearCloudRegistration()
        viewModelScope.launch { c.db.cloud().clear() }
    }

    /** Sets a new random password on the cloud account; [done] gets the new password or the reason it failed. */
    fun changeCloudPassword(done: (Result<String>) -> Unit) {
        viewModelScope.launch {
            val password = app.messagegateway.sms.core.Ids.random(16)
            done(
                try {
                    c.cloud.changePassword(password)
                    Result.success(password)
                } catch (e: Exception) {
                    Result.failure(e)
                }
            )
        }
    }

    fun syncCloudNow() {
        viewModelScope.launch { try { c.cloud.syncAll() } catch (_: Exception) { } }
    }

    fun send(phone: String, text: String, sim: Int?, done: (error: String?) -> Unit) {
        viewModelScope.launch {
            try {
                c.engine.enqueue(NewMessage(id = null, text = text, phoneNumbers = listOf(phone.trim()), simNumber = sim))
                done(null)
            } catch (e: Exception) {
                done(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    fun cancel(id: String, done: (error: String?) -> Unit) {
        viewModelScope.launch {
            try {
                c.engine.cancel(id)
                done(null)
            } catch (e: Exception) {
                done(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    fun addWebhook(url: String, event: String, done: (error: String?) -> Unit) {
        viewModelScope.launch {
            try {
                c.webhooks.upsert(null, url.trim(), event)
                done(null)
            } catch (e: Exception) {
                done(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    /** Registers [url] for every event in [events]; [done] gets null on success or the first problem. */
    fun addWebhooks(url: String, events: List<String>, done: (error: String?) -> Unit) {
        viewModelScope.launch {
            try {
                events.forEach { c.webhooks.upsert(null, url.trim(), it) }
                done(null)
            } catch (e: Exception) {
                done(e.message ?: e.javaClass.simpleName)
            }
        }
    }

    fun deleteWebhook(id: String) {
        viewModelScope.launch { c.webhooks.delete(id) }
    }

    fun retryWebhooks() = c.webhooks.kick()

    fun clearLogs() {
        viewModelScope.launch { c.db.logs().clear() }
    }

    fun sims() = c.deviceInfo.sims()
}
