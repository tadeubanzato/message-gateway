package app.messagegateway.sms.receiver

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.provider.Telephony
import app.messagegateway.sms.App
import kotlinx.coroutines.launch

/** Declared in the manifest so texts are caught even when the app's process was not running. */
class IncomingSmsReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action != Telephony.Sms.Intents.SMS_RECEIVED_ACTION) return
        val parts = Telephony.Sms.Intents.getMessagesFromIntent(intent) ?: return
        if (parts.isEmpty()) return

        val sender = parts.first().displayOriginatingAddress ?: return
        val body = parts.joinToString("") { it.displayMessageBody.orEmpty() }
        val receivedAt = parts.first().timestampMillis
        val slot = intent.getIntExtra("android.telephony.extra.SLOT_INDEX", intent.getIntExtra("slot", -1))
        val simNumber = if (slot >= 0) slot + 1 else null

        val container = (context.applicationContext as App).container
        val pending = goAsync()
        container.scope.launch {
            try {
                container.inbox.receive(sender, body, receivedAt, simNumber)
            } finally {
                pending.finish()
            }
        }
    }
}
