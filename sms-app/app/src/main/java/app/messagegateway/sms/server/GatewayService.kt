package app.messagegateway.sms.server

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.wifi.WifiManager
import android.os.Build
import android.os.IBinder
import android.os.PowerManager
import androidx.core.app.NotificationCompat
import androidx.core.app.ServiceCompat
import androidx.core.content.ContextCompat
import app.messagegateway.sms.App
import app.messagegateway.sms.AppContainer
import app.messagegateway.sms.MainActivity
import app.messagegateway.sms.R
import app.messagegateway.sms.core.error
import app.messagegateway.sms.core.info
import app.messagegateway.sms.send.CloudSyncWorker
import io.ktor.server.cio.CIO
import io.ktor.server.engine.EmbeddedServer
import io.ktor.server.engine.embeddedServer
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

sealed interface ServerState {
    data object Stopped : ServerState
    data object Starting : ServerState
    data class Running(val port: Int) : ServerState
    data class Failed(val reason: String) : ServerState
}

/**
 * Keeps the gateway alive in the background: the local HTTP API, the cloud connection, or both, depending on
 * what is switched on. A foreground service is what stops Android from killing the process while the screen is
 * off; the wake lock (optional) keeps the CPU up so requests and events are handled promptly.
 */
class GatewayService : Service() {
    private lateinit var container: AppContainer
    private var server: EmbeddedServer<*, *>? = null
    private var wakeLock: PowerManager.WakeLock? = null
    private var wifiLock: WifiManager.WifiLock? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        container = (application as App).container
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP) {
            container.prefs.serverEnabled = false
            container.prefs.cloudEnabled = false
            applyConfig()
            return START_NOT_STICKY
        }
        // Must happen promptly after startForegroundService, even if we are about to stop again.
        enterForeground()
        applyConfig()
        return if (wanted()) START_STICKY else START_NOT_STICKY
    }

    private fun wanted() = container.prefs.serverEnabled || container.prefs.cloudEnabled

    /** Makes what is running match what is switched on. */
    private fun applyConfig() {
        val prefs = container.prefs
        if (!wanted()) {
            stopSelf()
            return
        }
        if (prefs.serverEnabled) startServer() else stopServer()
        if (prefs.cloudEnabled) container.cloud.start() else container.cloud.stop()
        CloudSyncWorker.schedule(this, prefs.cloudEnabled)
        acquireLocks()
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION_ID, notification())
    }

    private fun enterForeground() {
        val type = if (Build.VERSION.SDK_INT >= 34) ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
        else ServiceInfo.FOREGROUND_SERVICE_TYPE_MANIFEST
        ServiceCompat.startForeground(this, NOTIFICATION_ID, notification(), type)
    }

    private fun notification(): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(CHANNEL_ID, getString(R.string.channel_server), NotificationManager.IMPORTANCE_LOW)
        )
        val prefs = container.prefs
        val what = listOfNotNull(
            if (prefs.serverEnabled) getString(R.string.notification_local, prefs.port) else null,
            if (prefs.cloudEnabled) getString(R.string.notification_cloud) else null,
        ).joinToString(" · ")
        val open = PendingIntent.getActivity(this, 0, Intent(this, MainActivity::class.java), PendingIntent.FLAG_IMMUTABLE)
        val stop = PendingIntent.getService(
            this, 1, Intent(this, GatewayService::class.java).setAction(ACTION_STOP), PendingIntent.FLAG_IMMUTABLE,
        )
        return NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(getString(R.string.notification_title))
            .setContentText(what)
            .setContentIntent(open)
            .addAction(0, getString(R.string.action_stop), stop)
            .setOngoing(true)
            .setSilent(true)
            .setCategory(NotificationCompat.CATEGORY_SERVICE)
            .build()
    }

    @Suppress("DEPRECATION")
    private fun acquireLocks() {
        val prefs = container.prefs
        if (prefs.keepAwake && wakeLock == null) {
            wakeLock = getSystemService(PowerManager::class.java)
                .newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "mgsms:gateway").apply { acquire() }
        }
        if (prefs.wifiHighPerformance && wifiLock == null) {
            wifiLock = applicationContext.getSystemService(WifiManager::class.java)
                .createWifiLock(WifiManager.WIFI_MODE_FULL_HIGH_PERF, "mgsms:gateway").apply { acquire() }
        }
    }

    private fun startServer() {
        if (server != null) return
        container.serverState.value = ServerState.Starting
        container.scope.launch(Dispatchers.IO) {
            try {
                val port = container.prefs.port
                val engine = embeddedServer(CIO, port = port, host = "0.0.0.0") { apiModule(container.api) }
                engine.start(wait = false)
                server = engine
                container.serverState.value = ServerState.Running(port)
                container.log.info(TAG, "API listening on port $port")
                container.engine.kick()
                container.webhooks.kick()
            } catch (e: Exception) {
                container.serverState.value = ServerState.Failed(e.message ?: e.javaClass.simpleName)
                container.log.error(TAG, "Could not start the API: ${e.message ?: e}")
                container.prefs.serverEnabled = false
                if (!wanted()) stopSelf()
            }
        }
    }

    private fun stopServer() {
        val running = server ?: return
        server = null
        container.serverState.value = ServerState.Stopped
        container.scope.launch(Dispatchers.IO) { running.stop(300, 2_000) }
    }

    override fun onDestroy() {
        stopServer()
        container.cloud.stop()
        CloudSyncWorker.schedule(this, false)
        if (container.serverState.value !is ServerState.Failed) container.serverState.value = ServerState.Stopped
        wakeLock?.takeIf { it.isHeld }?.release()
        wifiLock?.takeIf { it.isHeld }?.release()
        wakeLock = null
        wifiLock = null
        super.onDestroy()
    }

    companion object {
        private const val TAG = "GatewayService"
        private const val CHANNEL_ID = "server"
        private const val NOTIFICATION_ID = 1
        private const val ACTION_STOP = "app.messagegateway.sms.STOP"

        /** Starts the service if anything is switched on, stops it if nothing is; re-applies the settings if it runs. */
        fun refresh(context: Context) {
            val prefs = (context.applicationContext as App).container.prefs
            if (prefs.serverEnabled || prefs.cloudEnabled) {
                ContextCompat.startForegroundService(context, Intent(context, GatewayService::class.java))
            } else {
                context.stopService(Intent(context, GatewayService::class.java))
            }
        }
    }
}
