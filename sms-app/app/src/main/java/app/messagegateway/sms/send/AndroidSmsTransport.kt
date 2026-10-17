package app.messagegateway.sms.send

import android.Manifest
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.telephony.SmsManager
import android.telephony.SubscriptionManager
import androidx.core.content.ContextCompat
import app.messagegateway.sms.receiver.SmsResultReceiver

/** Sends through Android's [SmsManager]; results come back as broadcasts to [SmsResultReceiver]. */
class AndroidSmsTransport(private val context: Context) : SmsTransport {

    override fun partsCount(text: String, simNumber: Int?): Int = smsManager(simNumber).divideMessage(text).size

    override fun send(messageId: String, phone: String, text: String, simNumber: Int?, withDeliveryReport: Boolean) {
        check(ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) == PackageManager.PERMISSION_GRANTED) {
            "SEND_SMS permission is not granted"
        }
        val manager = smsManager(simNumber)
        val parts = manager.divideMessage(text)
        val sent = ArrayList<PendingIntent>(parts.size)
        val delivered = ArrayList<PendingIntent>(parts.size)
        for (i in parts.indices) {
            sent += resultIntent(SmsResultReceiver.ACTION_SENT, messageId, phone, i)
            if (withDeliveryReport) delivered += resultIntent(SmsResultReceiver.ACTION_DELIVERED, messageId, phone, i)
        }
        if (parts.size > 1) {
            manager.sendMultipartTextMessage(phone, null, parts, sent, delivered.takeIf { it.isNotEmpty() })
        } else {
            manager.sendTextMessage(phone, null, text, sent[0], delivered.firstOrNull())
        }
    }

    private fun resultIntent(action: String, messageId: String, phone: String, part: Int): PendingIntent {
        // Intents that differ only in extras are the same PendingIntent, so the data URI makes each one unique.
        val uri = Uri.Builder().scheme("mgsms").authority(action).appendPath(messageId).appendPath(phone)
            .appendPath(part.toString()).build()
        val intent = Intent(action, uri, context, SmsResultReceiver::class.java)
            .putExtra(SmsResultReceiver.EXTRA_MESSAGE_ID, messageId)
            .putExtra(SmsResultReceiver.EXTRA_PHONE, phone)
        // Mutable: the system adds result data (pdu, errorCode) to the intent before delivering it.
        return PendingIntent.getBroadcast(
            context, uri.hashCode(), intent, PendingIntent.FLAG_MUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
    }

    @Suppress("DEPRECATION")
    private fun smsManager(simNumber: Int?): SmsManager {
        if (simNumber == null) {
            return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                context.getSystemService(SmsManager::class.java)
            } else {
                SmsManager.getDefault()
            }
        }
        check(ContextCompat.checkSelfPermission(context, Manifest.permission.READ_PHONE_STATE) == PackageManager.PERMISSION_GRANTED) {
            "READ_PHONE_STATE permission is required to choose a SIM"
        }
        val subscription = context.getSystemService(SubscriptionManager::class.java)
            .getActiveSubscriptionInfoForSimSlotIndex(simNumber - 1)
            ?: throw IllegalStateException("SIM $simNumber not found")
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            context.getSystemService(SmsManager::class.java).createForSubscriptionId(subscription.subscriptionId)
        } else {
            SmsManager.getSmsManagerForSubscriptionId(subscription.subscriptionId)
        }
    }
}
