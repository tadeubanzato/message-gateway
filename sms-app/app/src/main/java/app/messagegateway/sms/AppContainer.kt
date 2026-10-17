package app.messagegateway.sms

import android.content.Context
import androidx.room.Room
import app.messagegateway.sms.cloud.CloudService
import app.messagegateway.sms.cloud.HttpCloudApi
import app.messagegateway.sms.core.AndroidDeviceInfo
import app.messagegateway.sms.core.AppLog
import app.messagegateway.sms.core.LogService
import app.messagegateway.sms.core.PhoneNumbers
import app.messagegateway.sms.core.PublicIp
import app.messagegateway.sms.core.Prefs
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.send.AndroidSmsTransport
import app.messagegateway.sms.send.InboxService
import app.messagegateway.sms.send.MessageStore
import app.messagegateway.sms.send.SendEngine
import app.messagegateway.sms.send.WorkScheduler
import app.messagegateway.sms.server.ApiDeps
import app.messagegateway.sms.server.ServerState
import app.messagegateway.sms.webhooks.UrlConnectionHttp
import app.messagegateway.sms.webhooks.WebhookService
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow

/** Builds and holds the app's long-lived objects. Plain constructors: nothing here is hidden by a framework. */
class AppContainer(context: Context) {
    private val appContext = context.applicationContext

    val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    val prefs = Prefs(appContext)
    val db: AppDatabase = Room.databaseBuilder(appContext, AppDatabase::class.java, "gateway.db")
        .addMigrations(AppDatabase.MIGRATION_1_2)
        .build()

    private val logService = LogService(db.logs(), scope)
    val log: AppLog = logService

    val messageScheduler = WorkScheduler(appContext, WorkScheduler.MESSAGES)
    val webhookScheduler = WorkScheduler(appContext, WorkScheduler.WEBHOOKS)

    val deviceInfo = AndroidDeviceInfo(appContext, prefs)

    val webhooks = WebhookService(
        db = db,
        deviceId = prefs.deviceId,
        signingKey = { prefs.webhookSigningKey },
        http = UrlConnectionHttp(),
        scheduler = webhookScheduler,
        scope = scope,
        log = log,
    )
    val store = MessageStore(db, webhooks, prefs.deviceId)
    val inbox = InboxService(db, webhooks, log)
    val engine = SendEngine(
        db = db,
        store = store,
        transport = AndroidSmsTransport(appContext),
        settings = prefs,
        scheduler = messageScheduler,
        scope = scope,
        log = log,
        normalizePhone = { phone, skip -> PhoneNumbers.normalize(phone, skip) { deviceInfo.toE164(it) } },
    )

    val cloud = CloudService(
        settings = prefs,
        apiFactory = { baseUrl -> HttpCloudApi(baseUrl, "message-gateway-sms/${BuildConfig.VERSION_NAME}") },
        engine = engine,
        store = store,
        db = db,
        deviceInfo = deviceInfo,
        scope = scope,
        log = log,
    )

    init {
        // Cloud mode learns about state changes of cloud messages through this hook.
        store.reportSink = cloud
    }

    val publicIp = PublicIp()

    val serverState = MutableStateFlow<ServerState>(ServerState.Stopped)

    val api: ApiDeps by lazy {
        ApiDeps(
            messages = engine,
            webhooks = webhooks,
            inbox = inbox,
            logs = logService,
            device = deviceInfo,
            credentials = prefs,
            appVersion = BuildConfig.VERSION_NAME,
            appVersionCode = BuildConfig.VERSION_CODE,
        )
    }
}
