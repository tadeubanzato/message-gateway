package app.messagegateway.sms.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import app.messagegateway.sms.App
import app.messagegateway.sms.server.GatewayService
import kotlinx.coroutines.launch

/** After a reboot or an app update: bring the server back and finish any messages left in the queue. */
class BootReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Intent.ACTION_BOOT_COMPLETED && intent.action != Intent.ACTION_MY_PACKAGE_REPLACED) return
        val container = (context.applicationContext as App).container
        if (container.prefs.startOnBoot) GatewayService.refresh(context)
        val pending = goAsync()
        container.scope.launch {
            try {
                container.engine.recoverStale()
                container.engine.kick()
                container.webhooks.kick()
            } finally {
                pending.finish()
            }
        }
    }
}
