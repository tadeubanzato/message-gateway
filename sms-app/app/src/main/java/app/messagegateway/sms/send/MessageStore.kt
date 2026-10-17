package app.messagegateway.sms.send

import androidx.room.withTransaction
import app.messagegateway.sms.core.ConflictException
import app.messagegateway.sms.core.InvalidStateException
import app.messagegateway.sms.core.MessageView
import app.messagegateway.sms.core.NotFoundException
import app.messagegateway.sms.core.RecipientView
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.data.MessageEntity
import app.messagegateway.sms.data.MessageWithRecipients
import app.messagegateway.sms.data.RecipientEntity
import app.messagegateway.sms.data.StateEntity
import app.messagegateway.sms.domain.ProcessingState
import app.messagegateway.sms.domain.StateTransitions
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock

/**
 * The one place that changes message state. Every change goes through [transitionRecipient], which checks
 * the transition table, records when each state was reached, and keeps the message's own state in step
 * with its recipients. Changes are serialized: results for the parts of one SMS can arrive at the same time.
 */
class MessageStore(
    private val db: AppDatabase,
    private val events: EventSink,
    private val deviceId: String,
    private val clock: () -> Long = System::currentTimeMillis,
) {
    private val dao = db.messages()
    private val lock = Mutex()

    /** Set by the cloud client; null while cloud mode is not in use. */
    @Volatile
    var reportSink: CloudReportSink? = null

    suspend fun insert(message: MessageEntity, recipients: List<RecipientEntity>) {
        try {
            dao.insertNew(message, recipients, clock())
        } catch (e: android.database.sqlite.SQLiteConstraintException) {
            throw ConflictException()
        }
    }

    suspend fun get(id: String): MessageWithRecipients? = dao.get(id)

    /**
     * Moves one recipient to [to] if the transition table allows it. [update] adjusts the other
     * fields of the recipient in the same write. Returns whether the state changed.
     */
    suspend fun transitionRecipient(
        id: String,
        phone: String,
        to: ProcessingState,
        error: String? = null,
        update: (RecipientEntity) -> RecipientEntity = { it },
    ): Boolean {
        var changedTo: RecipientEntity? = null
        val changed = lock.withLock {
            val current = dao.recipient(id, phone) ?: return false
            if (!StateTransitions.canTransition(current.state, to)) return false
            val now = clock()
            val next = update(current).copy(state = to, error = error ?: current.error)
            db.withTransaction {
                dao.upsertRecipient(next)
                if (current.state != to) dao.upsertState(StateEntity(id, to, now))
                refreshMessageState(id, now)
            }
            if (current.state != to) changedTo = next
            current.state != to
        }
        changedTo?.let { announce(id, it, to) }
        return changed
    }

    /** Re-derives the message's state from its recipients (used when a message has none left pending). */
    suspend fun refresh(id: String) = lock.withLock { refreshMessageState(id, clock()) }

    private suspend fun refreshMessageState(id: String, now: Long) {
        val message = dao.get(id)?.message ?: return
        val aggregate = StateTransitions.aggregate(dao.recipients(id).map { it.state })
        if (aggregate != message.state) {
            dao.setMessageState(id, aggregate)
            dao.upsertState(StateEntity(id, aggregate, now))
        }
    }

    /** After a recipient changed state: tell webhooks, and queue a report if the message came from the cloud. */
    private suspend fun announce(id: String, r: RecipientEntity, to: ProcessingState) {
        val message = dao.get(id)?.message ?: return
        val now = clock()
        val event = when (to) {
            ProcessingState.Sent -> GatewayEvent.SmsSent(id, r.phoneNumber, message.simNumber, r.partsTotal, now)
            ProcessingState.Delivered -> GatewayEvent.SmsDelivered(id, r.phoneNumber, message.simNumber, now)
            ProcessingState.Failed -> GatewayEvent.SmsFailed(id, r.phoneNumber, message.simNumber, r.error ?: "Unknown error", now)
            else -> null
        }
        event?.let { events.publish(it) }
        if (message.source == MessageEntity.SOURCE_CLOUD) reportSink?.stateChanged(id)
    }

    /** One part of an SMS was accepted by the network ([error] null) or rejected. */
    suspend fun onPartSent(id: String, phone: String, error: String?) {
        if (error != null) {
            transitionRecipient(id, phone, ProcessingState.Failed, error)
            return
        }
        val current = dao.recipient(id, phone) ?: return
        val sent = current.partsSent + 1
        if (current.partsTotal in 1..sent) {
            transitionRecipient(id, phone, ProcessingState.Sent) { it.copy(partsSent = sent) }
        } else {
            lock.withLock { dao.recipient(id, phone)?.let { dao.upsertRecipient(it.copy(partsSent = sent)) } }
        }
    }

    /** A delivery report for one part: [delivered] true, false (rejected), or null (still being tried). */
    suspend fun onPartDelivery(id: String, phone: String, delivered: Boolean?) {
        when (delivered) {
            null -> Unit
            false -> transitionRecipient(id, phone, ProcessingState.Failed, "Delivery failed")
            true -> {
                val current = dao.recipient(id, phone) ?: return
                val count = current.partsDelivered + 1
                if (current.partsTotal in 1..count) {
                    transitionRecipient(id, phone, ProcessingState.Delivered) { it.copy(partsDelivered = count) }
                } else {
                    lock.withLock { dao.recipient(id, phone)?.let { dao.upsertRecipient(it.copy(partsDelivered = count)) } }
                }
            }
        }
    }

    suspend fun cancel(id: String): MessageView {
        val message = dao.get(id) ?: throw NotFoundException()
        if (message.message.state != ProcessingState.Pending) {
            throw InvalidStateException("Message is ${message.message.state}, only Pending messages can be cancelled")
        }
        message.recipients.filter { it.state == ProcessingState.Pending }
            .forEach { transitionRecipient(id, it.phoneNumber, ProcessingState.Cancelled) }
        return view(requireNotNull(dao.get(id)))
    }

    fun view(m: MessageWithRecipients): MessageView = MessageView(
        id = m.message.id,
        deviceId = deviceId,
        state = m.message.state,
        createdAt = m.message.createdAt,
        text = m.message.text,
        recipients = m.recipients.sortedBy { it.phoneNumber }.map { RecipientView(it.phoneNumber, it.state, it.error) },
        states = m.states.associate { it.state to it.updatedAt },
        scheduleAt = m.message.scheduleAt,
    )
}
