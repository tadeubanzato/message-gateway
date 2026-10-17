package app.messagegateway.sms.send

import app.messagegateway.sms.core.AppLog
import app.messagegateway.sms.core.InboxReader
import app.messagegateway.sms.core.InboxView
import app.messagegateway.sms.core.info
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.data.IncomingEntity
import java.security.MessageDigest

/** Stores texts the phone receives and tells webhooks about them. */
class InboxService(
    db: AppDatabase,
    private val events: EventSink,
    private val log: AppLog,
) : InboxReader {
    private val dao = db.incoming()

    suspend fun receive(sender: String, body: String, receivedAt: Long, simNumber: Int?) {
        // The id comes from the content, so a broadcast delivered twice is stored (and announced) once.
        val id = MessageDigest.getInstance("SHA-1").digest("$sender|$receivedAt|$body".toByteArray())
            .joinToString("") { "%02x".format(it) }.take(24)
        if (dao.insert(IncomingEntity(id, sender, body, receivedAt, simNumber)) == -1L) return
        log.info("Inbox", "Received a text from $sender")
        events.publish(GatewayEvent.SmsReceived(id, sender, simNumber, body, receivedAt))
    }

    override suspend fun list(from: Long, to: Long, limit: Int, offset: Int): List<InboxView> =
        dao.list(from, to, limit, offset).map { InboxView(it.id, it.sender, it.body, it.receivedAt, it.simNumber) }

    override suspend fun count(from: Long, to: Long): Int = dao.count(from, to)
}
