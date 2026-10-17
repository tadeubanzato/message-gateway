package app.messagegateway.sms.data

import androidx.room.Dao
import androidx.room.Database
import androidx.room.Insert
import androidx.room.OnConflictStrategy
import androidx.room.Query
import androidx.room.RoomDatabase
import androidx.room.Transaction
import app.messagegateway.sms.domain.ProcessingState
import kotlinx.coroutines.flow.Flow

/** One outgoing text to one recipient: when its message was created and how it ended up. */
data class OutgoingPoint(val at: Long, val state: ProcessingState)

@Dao
interface MessageDao {
    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertMessage(message: MessageEntity)

    @Insert(onConflict = OnConflictStrategy.ABORT)
    suspend fun insertRecipients(recipients: List<RecipientEntity>)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertState(state: StateEntity)

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsertRecipient(recipient: RecipientEntity)

    @Query("UPDATE messages SET state = :state WHERE id = :id")
    suspend fun setMessageState(id: String, state: ProcessingState)

    @Transaction
    suspend fun insertNew(message: MessageEntity, recipients: List<RecipientEntity>, now: Long) {
        insertMessage(message)
        insertRecipients(recipients)
        upsertState(StateEntity(message.id, ProcessingState.Pending, now))
    }

    @Transaction
    @Query("SELECT * FROM messages WHERE id = :id")
    suspend fun get(id: String): MessageWithRecipients?

    @Query("SELECT * FROM recipients WHERE messageId = :id AND phoneNumber = :phone")
    suspend fun recipient(id: String, phone: String): RecipientEntity?

    @Query("SELECT * FROM recipients WHERE messageId = :id ORDER BY rowid")
    suspend fun recipients(id: String): List<RecipientEntity>

    /** The next message that is due, highest priority first, then oldest first. */
    @Transaction
    @Query(
        "SELECT * FROM messages WHERE state = 'Pending' AND (scheduleAt IS NULL OR scheduleAt <= :now) " +
            "ORDER BY priority DESC, createdAt ASC LIMIT 1"
    )
    suspend fun nextDue(now: Long): MessageWithRecipients?

    @Query("SELECT MIN(scheduleAt) FROM messages WHERE state = 'Pending' AND scheduleAt > :now")
    suspend fun nextScheduledAt(now: Long): Long?

    @Transaction
    @Query(
        "SELECT * FROM messages WHERE source = :source AND (:state IS NULL OR state = :state) AND createdAt BETWEEN :from AND :to " +
            "ORDER BY createdAt DESC LIMIT :limit OFFSET :offset"
    )
    suspend fun list(source: String, state: ProcessingState?, from: Long, to: Long, limit: Int, offset: Int): List<MessageWithRecipients>

    @Query("SELECT COUNT(*) FROM messages WHERE source = :source AND (:state IS NULL OR state = :state) AND createdAt BETWEEN :from AND :to")
    suspend fun count(source: String, state: ProcessingState?, from: Long, to: Long): Int

    @Transaction
    @Query("SELECT * FROM messages ORDER BY createdAt DESC LIMIT :limit")
    fun observeRecent(limit: Int): Flow<List<MessageWithRecipients>>

    @Query("SELECT COUNT(*) FROM messages WHERE state = 'Pending'")
    fun observePendingCount(): Flow<Int>

    /** Every recipient of every message created since [since], for the activity chart. */
    @Query("SELECT m.createdAt AS at, r.state AS state FROM messages m JOIN recipients r ON r.messageId = m.id WHERE m.createdAt >= :since")
    fun observeOutgoingSince(since: Long): Flow<List<OutgoingPoint>>

    /** How many recipients were handed to the radio since [since]: feeds the rate limit. */
    @Query("SELECT COUNT(*) FROM message_states WHERE state = 'Processed' AND updatedAt >= :since")
    suspend fun processedSince(since: Long): Int

    @Query("SELECT MIN(updatedAt) FROM message_states WHERE state = 'Processed' AND updatedAt >= :since")
    suspend fun oldestProcessedSince(since: Long): Long?

    /** Recipients handed to the radio long ago with no result since: the app probably died mid-send. */
    @Query(
        "SELECT r.* FROM recipients r WHERE r.state = 'Processed' AND " +
            "(SELECT s.updatedAt FROM message_states s WHERE s.messageId = r.messageId AND s.state = 'Processed') < :before"
    )
    suspend fun staleProcessed(before: Long): List<RecipientEntity>

    @Query("DELETE FROM messages WHERE createdAt < :before AND state IN ('Delivered','Failed','Cancelled','Sent')")
    suspend fun deleteOlderThan(before: Long): Int
}

@Dao
interface IncomingDao {
    @Insert(onConflict = OnConflictStrategy.IGNORE)
    suspend fun insert(message: IncomingEntity): Long

    @Query("SELECT * FROM incoming WHERE receivedAt BETWEEN :from AND :to ORDER BY receivedAt DESC LIMIT :limit OFFSET :offset")
    suspend fun list(from: Long, to: Long, limit: Int, offset: Int): List<IncomingEntity>

    @Query("SELECT COUNT(*) FROM incoming WHERE receivedAt BETWEEN :from AND :to")
    suspend fun count(from: Long, to: Long): Int

    @Query("SELECT * FROM incoming ORDER BY receivedAt DESC LIMIT :limit")
    fun observeRecent(limit: Int): Flow<List<IncomingEntity>>

    @Query("SELECT receivedAt FROM incoming WHERE receivedAt >= :since")
    fun observeReceivedSince(since: Long): Flow<List<Long>>

    @Query("DELETE FROM incoming WHERE receivedAt < :before")
    suspend fun deleteOlderThan(before: Long): Int
}

@Dao
interface WebhookDao {
    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun upsert(webhook: WebhookEntity)

    @Query("SELECT * FROM webhooks WHERE source = 'Local' ORDER BY id")
    suspend fun local(): List<WebhookEntity>

    @Query("DELETE FROM webhooks WHERE source = 'Cloud'")
    suspend fun deleteCloud()

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun insertAll(webhooks: List<WebhookEntity>)

    /** Swaps the webhooks fetched from the cloud server for the latest list, leaving the local ones alone. */
    @Transaction
    suspend fun replaceCloud(webhooks: List<WebhookEntity>) {
        deleteCloud()
        insertAll(webhooks)
    }

    @Query("SELECT * FROM webhooks ORDER BY id")
    fun observeAll(): Flow<List<WebhookEntity>>

    @Query("SELECT * FROM webhooks WHERE event = :event")
    suspend fun forEvent(event: String): List<WebhookEntity>

    @Query("DELETE FROM webhooks WHERE id = :id AND source = 'Local'")
    suspend fun delete(id: String): Int

    @Insert
    suspend fun insertDelivery(delivery: WebhookDeliveryEntity): Long

    @Query("SELECT * FROM webhook_deliveries WHERE state = 'Pending' AND nextAttemptAt <= :now ORDER BY nextAttemptAt LIMIT :limit")
    suspend fun dueDeliveries(now: Long, limit: Int): List<WebhookDeliveryEntity>

    @Query("SELECT MIN(nextAttemptAt) FROM webhook_deliveries WHERE state = 'Pending'")
    suspend fun nextAttemptAt(): Long?

    @Query("UPDATE webhook_deliveries SET state = :state, attempts = :attempts, nextAttemptAt = :next, lastError = :error WHERE id = :id")
    suspend fun updateDelivery(id: Long, state: String, attempts: Int, next: Long, error: String?)

    @Query("SELECT * FROM webhook_deliveries ORDER BY createdAt DESC LIMIT :limit")
    fun observeDeliveries(limit: Int): Flow<List<WebhookDeliveryEntity>>

    @Query("SELECT COUNT(*) FROM webhook_deliveries WHERE state = 'Pending'")
    fun observePendingCount(): Flow<Int>

    @Query("DELETE FROM webhook_deliveries WHERE state != 'Pending' AND createdAt < :before")
    suspend fun deleteFinishedBefore(before: Long): Int
}

@Dao
interface LogDao {
    @Insert
    suspend fun insert(entry: LogEntity)

    @Query("SELECT * FROM logs WHERE createdAt BETWEEN :from AND :to ORDER BY id DESC LIMIT :limit")
    suspend fun list(from: Long, to: Long, limit: Int): List<LogEntity>

    @Query("SELECT * FROM logs ORDER BY id DESC LIMIT :limit")
    fun observeRecent(limit: Int): Flow<List<LogEntity>>

    @Query("DELETE FROM logs WHERE id <= (SELECT MAX(id) FROM logs) - :keep")
    suspend fun trim(keep: Int)

    @Query("DELETE FROM logs")
    suspend fun clear()
}

@Dao
interface CloudDao {
    @Query("SELECT * FROM cloud_reports WHERE messageId = :id")
    suspend fun get(id: String): CloudReportEntity?

    @Insert(onConflict = OnConflictStrategy.REPLACE)
    suspend fun put(report: CloudReportEntity)

    /** Notes that [id] changed and has to be reported again. */
    @Transaction
    suspend fun markDirty(id: String, now: Long) {
        put(CloudReportEntity(id, (get(id)?.version ?: 0) + 1, 0, now))
    }

    @Query("SELECT * FROM cloud_reports WHERE nextAttemptAt <= :now ORDER BY nextAttemptAt LIMIT :limit")
    suspend fun due(now: Long, limit: Int): List<CloudReportEntity>

    @Query("SELECT MIN(nextAttemptAt) FROM cloud_reports")
    suspend fun nextAttemptAt(): Long?

    @Query("DELETE FROM cloud_reports WHERE messageId = :id AND version = :version")
    suspend fun deleteIfUnchanged(id: String, version: Long)

    @Query("UPDATE cloud_reports SET attempts = :attempts, nextAttemptAt = :next WHERE messageId = :id AND version = :version")
    suspend fun retryLater(id: String, version: Long, attempts: Int, next: Long)

    @Query("DELETE FROM cloud_reports")
    suspend fun clear()
}

@Database(
    entities = [
        MessageEntity::class, RecipientEntity::class, StateEntity::class,
        IncomingEntity::class, WebhookEntity::class, WebhookDeliveryEntity::class, LogEntity::class,
        CloudReportEntity::class,
    ],
    version = 2,
    exportSchema = true,
)
abstract class AppDatabase : RoomDatabase() {
    abstract fun messages(): MessageDao
    abstract fun incoming(): IncomingDao
    abstract fun webhooks(): WebhookDao
    abstract fun logs(): LogDao
    abstract fun cloud(): CloudDao

    companion object {
        /** Adds cloud support: where each message and webhook came from, and what still has to be reported. */
        val MIGRATION_1_2 = object : androidx.room.migration.Migration(1, 2) {
            override fun migrate(db: androidx.sqlite.db.SupportSQLiteDatabase) {
                db.execSQL("ALTER TABLE messages ADD COLUMN source TEXT NOT NULL DEFAULT 'Local'")
                db.execSQL("ALTER TABLE webhooks ADD COLUMN source TEXT NOT NULL DEFAULT 'Local'")
                db.execSQL("ALTER TABLE incoming ADD COLUMN uploaded INTEGER NOT NULL DEFAULT 1")
                db.execSQL(
                    "CREATE TABLE IF NOT EXISTS cloud_reports (messageId TEXT NOT NULL, version INTEGER NOT NULL, " +
                        "attempts INTEGER NOT NULL, nextAttemptAt INTEGER NOT NULL, PRIMARY KEY(messageId))"
                )
            }
        }
    }
}
