package app.messagegateway.sms.send

import app.messagegateway.sms.core.AppLog
import app.messagegateway.sms.core.Ids
import app.messagegateway.sms.core.MessageService
import app.messagegateway.sms.core.MessageView
import app.messagegateway.sms.core.NewMessage
import app.messagegateway.sms.core.PRIORITY_EXPEDITED
import app.messagegateway.sms.core.SendSettings
import app.messagegateway.sms.core.error
import app.messagegateway.sms.core.info
import app.messagegateway.sms.data.AppDatabase
import app.messagegateway.sms.data.MessageEntity
import app.messagegateway.sms.data.RecipientEntity
import app.messagegateway.sms.domain.ProcessingState
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlin.random.Random

/**
 * Takes messages from the API (or the app) and hands them to the radio.
 *
 * A message is written to the database first, then sent straight away from the running process: there is
 * no job scheduler between the API call and the radio. Work that has to wait (a future `scheduleAt`, the
 * rate limit) is handed to [DrainScheduler], which wakes the process up again at the right time.
 */
class SendEngine(
    db: AppDatabase,
    private val store: MessageStore,
    private val transport: SmsTransport,
    private val settings: SendSettings,
    private val scheduler: DrainScheduler,
    private val scope: CoroutineScope,
    private val log: AppLog,
    private val normalizePhone: (phone: String, skipValidation: Boolean) -> String,
    private val clock: () -> Long = System::currentTimeMillis,
) : MessageService {
    private val dao = db.messages()
    private val drainLock = Mutex()

    override suspend fun enqueue(message: NewMessage): MessageView {
        require(message.text.isNotEmpty()) { "Text message is empty" }
        require(message.phoneNumbers.isNotEmpty()) { "Empty phone numbers list" }
        require(message.phoneNumbers.distinct().size == message.phoneNumbers.size) { "phone numbers must be unique" }
        require(message.simNumber == null || message.simNumber >= 1) { "SIM number cannot be less than 1" }
        val now = clock()
        require(message.scheduleAt == null || message.scheduleAt > now) { "scheduleAt must be in the future" }
        require(message.validUntil == null || message.validUntil > now) { "message already expired" }

        val numbers = message.phoneNumbers.map { normalizePhone(it, message.skipPhoneValidation) }
        require(numbers.distinct().size == numbers.size) { "phone numbers must be unique" }

        val id = message.id?.takeIf { it.isNotBlank() } ?: Ids.messageId()
        val entity = MessageEntity(
            id = id,
            text = message.text,
            createdAt = now,
            scheduleAt = message.scheduleAt,
            validUntil = message.validUntil,
            priority = message.priority,
            simNumber = message.simNumber,
            withDeliveryReport = message.withDeliveryReport,
            skipPhoneValidation = message.skipPhoneValidation,
            state = ProcessingState.Pending,
            source = message.source,
        )
        store.insert(entity, numbers.map { RecipientEntity(id, it, ProcessingState.Pending, null) })
        kick()
        return store.view(requireNotNull(store.get(id)))
    }

    // The API-facing reads below only show messages created through the phone's own API; messages
    // that came from the cloud server are the server's to manage.
    override suspend fun get(id: String): MessageView? =
        store.get(id)?.takeIf { it.message.source == MessageEntity.SOURCE_LOCAL }?.let(store::view)

    override suspend fun list(state: ProcessingState?, from: Long, to: Long, limit: Int, offset: Int): List<MessageView> =
        dao.list(MessageEntity.SOURCE_LOCAL, state, from, to, limit, offset).map(store::view)

    override suspend fun count(state: ProcessingState?, from: Long, to: Long): Int =
        dao.count(MessageEntity.SOURCE_LOCAL, state, from, to)

    override suspend fun cancel(id: String): MessageView {
        if (get(id) == null) throw app.messagegateway.sms.core.NotFoundException()
        return store.cancel(id)
    }

    /** Starts sending whatever is due, without making the caller wait. */
    fun kick() {
        scope.launch {
            try {
                drain()
            } catch (e: kotlinx.coroutines.CancellationException) {
                throw e
            } catch (e: Exception) {
                log.error(TAG, "Sending failed: ${e.message ?: e}")
            }
        }
    }

    /** Fails sends that were handed to the radio but never reported back, e.g. because the app was killed. */
    suspend fun recoverStale(olderThanMillis: Long = STALE_AFTER_MS) {
        dao.staleProcessed(clock() - olderThanMillis).forEach {
            store.transitionRecipient(it.messageId, it.phoneNumber, ProcessingState.Failed, "Interrupted before the phone reported a result")
        }
    }

    /** Sends everything that is due, one recipient at a time, then books a wake-up for what is still waiting. */
    suspend fun drain() = drainLock.withLock {
        var lastSendAt: Long? = null
        while (true) {
            val next = dao.nextDue(clock()) ?: break
            val message = next.message
            if (message.validUntil != null && message.validUntil < clock()) {
                next.recipients.filter { it.state == ProcessingState.Pending }.forEach {
                    store.transitionRecipient(message.id, it.phoneNumber, ProcessingState.Failed, "Message expired")
                }
                store.refresh(message.id)
                continue
            }

            val pending = next.recipients.filter { it.state == ProcessingState.Pending }
            if (pending.isEmpty()) {
                store.refresh(message.id)
                continue
            }
            for (recipient in pending) {
                if (!waitForRateLimit()) return@withLock
                paceBeforeSend(message.priority, lastSendAt)
                sendRecipient(message, recipient)
                lastSendAt = clock()
            }
        }
        dao.nextScheduledAt(clock())?.let(scheduler::scheduleAt)
    }

    private suspend fun sendRecipient(message: MessageEntity, recipient: RecipientEntity) {
        // Re-read: the message may have been cancelled while earlier recipients were being sent.
        val fresh = dao.recipient(message.id, recipient.phoneNumber)
        if (fresh == null || fresh.state != ProcessingState.Pending) return

        val parts = try {
            transport.partsCount(message.text, message.simNumber)
        } catch (e: Exception) {
            fail(message, recipient, e)
            return
        }
        store.transitionRecipient(message.id, recipient.phoneNumber, ProcessingState.Processed) { it.copy(partsTotal = parts) }
        try {
            transport.send(message.id, recipient.phoneNumber, message.text, message.simNumber, message.withDeliveryReport)
        } catch (e: Exception) {
            fail(message, recipient, e)
        }
    }

    private suspend fun fail(message: MessageEntity, recipient: RecipientEntity, e: Exception) {
        log.error(TAG, "Can't send ${message.id} to ${recipient.phoneNumber}: ${e.message ?: e}")
        store.transitionRecipient(message.id, recipient.phoneNumber, ProcessingState.Failed, e.message ?: e.javaClass.simpleName)
    }

    /** True when sending may go on now. False when the rest of the work was handed to the scheduler. */
    private suspend fun waitForRateLimit(): Boolean {
        val count = settings.rateLimitCount
        if (count <= 0) return true
        val window = settings.rateLimitPeriodMin * 60_000L
        val since = clock() - window
        if (dao.processedSince(since) < count) return true
        val oldest = dao.oldestProcessedSince(since) ?: return true
        val wait = oldest + window - clock() + 1_000
        if (wait <= SHORT_WAIT_MS) {
            delay(wait)
            return true
        }
        log.info(TAG, "Rate limit reached, resuming in ${wait / 1000}s")
        scheduler.scheduleAt(clock() + wait)
        return false
    }

    /** Optional pause between messages, so a burst doesn't look like spam to the carrier. */
    private suspend fun paceBeforeSend(priority: Int, lastSendAt: Long?) {
        val max = settings.sendIntervalMaxSec
        if (max <= 0 || lastSendAt == null || priority >= PRIORITY_EXPEDITED) return
        val min = settings.sendIntervalMinSec.coerceIn(0, max)
        val gap = (if (max > min) Random.nextInt(min, max + 1) else min) * 1_000L
        val remaining = lastSendAt + gap - clock()
        if (remaining > 0) delay(remaining)
    }

    private companion object {
        const val TAG = "SendEngine"
        const val SHORT_WAIT_MS = 30_000L
        const val STALE_AFTER_MS = 10 * 60_000L
    }
}
