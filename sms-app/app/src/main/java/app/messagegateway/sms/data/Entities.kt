package app.messagegateway.sms.data

import androidx.room.ColumnInfo
import androidx.room.Embedded
import androidx.room.Entity
import androidx.room.ForeignKey
import androidx.room.Index
import androidx.room.PrimaryKey
import androidx.room.Relation
import app.messagegateway.sms.domain.ProcessingState

@Entity(
    tableName = "messages",
    indices = [Index("state", "scheduleAt"), Index("createdAt")],
)
data class MessageEntity(
    @PrimaryKey val id: String,
    val text: String,
    val createdAt: Long,
    val scheduleAt: Long?,
    val validUntil: Long?,
    val priority: Int,
    val simNumber: Int?,
    val withDeliveryReport: Boolean,
    val skipPhoneValidation: Boolean,
    val state: ProcessingState,
    /** Where the message came from: the phone's own API ("Local") or the cloud server ("Cloud"). */
    @ColumnInfo(defaultValue = "'Local'") val source: String = SOURCE_LOCAL,
) {
    companion object {
        const val SOURCE_LOCAL = "Local"
        const val SOURCE_CLOUD = "Cloud"
    }
}

@Entity(
    tableName = "recipients",
    primaryKeys = ["messageId", "phoneNumber"],
    foreignKeys = [ForeignKey(
        entity = MessageEntity::class,
        parentColumns = ["id"],
        childColumns = ["messageId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index("messageId")],
)
data class RecipientEntity(
    val messageId: String,
    val phoneNumber: String,
    val state: ProcessingState,
    val error: String?,
    val partsTotal: Int = 0,
    val partsSent: Int = 0,
    val partsDelivered: Int = 0,
)

/** One row per state a message has reached, with the time it got there. */
@Entity(
    tableName = "message_states",
    primaryKeys = ["messageId", "state"],
    foreignKeys = [ForeignKey(
        entity = MessageEntity::class,
        parentColumns = ["id"],
        childColumns = ["messageId"],
        onDelete = ForeignKey.CASCADE,
    )],
    indices = [Index("state", "updatedAt")],
)
data class StateEntity(
    val messageId: String,
    val state: ProcessingState,
    val updatedAt: Long,
)

data class MessageWithRecipients(
    @Embedded val message: MessageEntity,
    @Relation(parentColumn = "id", entityColumn = "messageId")
    val recipients: List<RecipientEntity>,
    @Relation(parentColumn = "id", entityColumn = "messageId")
    val states: List<StateEntity>,
)

@Entity(tableName = "incoming", indices = [Index("receivedAt")])
data class IncomingEntity(
    @PrimaryKey val id: String,
    val sender: String,
    val body: String,
    val receivedAt: Long,
    val simNumber: Int?,
    /** Unused: kept because the column is part of the database schema. */
    @ColumnInfo(defaultValue = "1") val uploaded: Boolean = true,
)

@Entity(tableName = "webhooks")
data class WebhookEntity(
    @PrimaryKey val id: String,
    val url: String,
    val event: String,
    /** "Local" webhooks are set through the phone's API; "Cloud" ones are fetched from the cloud server. */
    @ColumnInfo(defaultValue = "'Local'") val source: String = MessageEntity.SOURCE_LOCAL,
)

@Entity(tableName = "webhook_deliveries", indices = [Index("state", "nextAttemptAt")])
data class WebhookDeliveryEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val webhookId: String,
    val url: String,
    val event: String,
    val body: String,
    val attempts: Int,
    val nextAttemptAt: Long,
    val state: String,
    val lastError: String?,
    val createdAt: Long,
) {
    companion object {
        const val PENDING = "Pending"
        const val DELIVERED = "Delivered"
        const val FAILED = "Failed"
    }
}

@Entity(tableName = "logs", indices = [Index("createdAt")])
data class LogEntity(
    @PrimaryKey(autoGenerate = true) val id: Long = 0,
    val createdAt: Long,
    val level: String,
    val tag: String,
    val message: String,
)

/**
 * A cloud message whose latest state still has to be reported to the server. One row per message: the report
 * is built from the message's current state when it is sent, so several quick changes become one request.
 * [version] goes up on every change, so a report is only cleared if nothing changed while it was in flight.
 */
@Entity(tableName = "cloud_reports")
data class CloudReportEntity(
    @PrimaryKey val messageId: String,
    val version: Long,
    val attempts: Int,
    val nextAttemptAt: Long,
)
