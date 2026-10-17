package app.messagegateway.sms.send

/** Things that happened on the phone that a webhook can be told about. */
sealed interface GatewayEvent {
    val name: String

    data class SmsReceived(
        val messageId: String, val sender: String, val simNumber: Int?, val message: String, val receivedAt: Long,
    ) : GatewayEvent { override val name = "sms:received" }

    data class SmsSent(
        val messageId: String, val recipient: String, val simNumber: Int?, val partsCount: Int, val sentAt: Long,
    ) : GatewayEvent { override val name = "sms:sent" }

    data class SmsDelivered(
        val messageId: String, val recipient: String, val simNumber: Int?, val deliveredAt: Long,
    ) : GatewayEvent { override val name = "sms:delivered" }

    data class SmsFailed(
        val messageId: String, val recipient: String, val simNumber: Int?, val reason: String, val failedAt: Long,
    ) : GatewayEvent { override val name = "sms:failed" }
}

fun interface EventSink {
    suspend fun publish(event: GatewayEvent)
}

/** Sends one SMS through the radio. The Android implementation lives in [AndroidSmsTransport]. */
interface SmsTransport {
    /** How many SMS parts [text] will take. */
    fun partsCount(text: String, simNumber: Int?): Int

    /** Hands the message to the radio; sent/delivery results arrive later through [MessageStore]. */
    fun send(messageId: String, phone: String, text: String, simNumber: Int?, withDeliveryReport: Boolean)
}

/** Wakes the engine up again at a later time, even if the process has been stopped by then. */
fun interface DrainScheduler {
    fun scheduleAt(timeMillis: Long)
}

/** Told whenever a cloud-sourced message changes state, so the change can be reported to the server. */
fun interface CloudReportSink {
    suspend fun stateChanged(messageId: String)
}
