package app.messagegateway.sms.receiver

import android.app.Activity
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.telephony.SmsMessage
import app.messagegateway.sms.App
import kotlinx.coroutines.launch

/** Receives the "sent" and "delivered" results for each SMS part. */
class SmsResultReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val id = intent.getStringExtra(EXTRA_MESSAGE_ID) ?: return
        val phone = intent.getStringExtra(EXTRA_PHONE) ?: return
        val container = (context.applicationContext as App).container
        val result = resultCode
        val pending = goAsync()
        container.scope.launch {
            try {
                when (intent.action) {
                    ACTION_SENT -> container.store.onPartSent(
                        id, phone, if (result == Activity.RESULT_OK) null else SmsResults.describeSendError(result),
                    )
                    ACTION_DELIVERED -> container.store.onPartDelivery(id, phone, deliveryOutcome(intent, result))
                }
            } finally {
                pending.finish()
            }
        }
    }

    private fun deliveryOutcome(intent: Intent, result: Int): Boolean? {
        val pdu = intent.getByteArrayExtra("pdu")
        val status = pdu?.let {
            try {
                SmsMessage.createFromPdu(it, intent.getStringExtra("format")).status
            } catch (_: Exception) {
                null
            }
        }
        return SmsResults.deliveryOutcome(status, result == Activity.RESULT_OK)
    }

    companion object {
        const val ACTION_SENT = "app.messagegateway.sms.SMS_SENT"
        const val ACTION_DELIVERED = "app.messagegateway.sms.SMS_DELIVERED"
        const val EXTRA_MESSAGE_ID = "message_id"
        const val EXTRA_PHONE = "phone"
    }
}
