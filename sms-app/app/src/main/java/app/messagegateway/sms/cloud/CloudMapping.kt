package app.messagegateway.sms.cloud

import app.messagegateway.sms.core.Iso
import app.messagegateway.sms.core.NewMessage
import app.messagegateway.sms.data.MessageEntity
import app.messagegateway.sms.data.MessageWithRecipients

/** Turns messages from the server into messages for the send queue, and local state back into reports. */
object CloudMapping {
    sealed interface Mapped {
        data class Accept(val message: NewMessage) : Mapped
        /** Something this app can't send; the server should be told it failed, with this reason. */
        data class Reject(val reason: String) : Mapped
    }

    fun toNewMessage(m: CloudMessage, now: Long): Mapped {
        if (m.isEncrypted == true) return Mapped.Reject("Encrypted messages are not supported by this app")
        if (m.dataMessage != null) return Mapped.Reject("Data messages are not supported by this app")
        if (m.mmsMessage != null) return Mapped.Reject("MMS messages are not supported by this app")
        val text = m.textMessage?.text ?: m.message
        if (text.isNullOrEmpty()) return Mapped.Reject("Message has no text")
        if (m.phoneNumbers.isEmpty()) return Mapped.Reject("Message has no recipients")

        val validUntil = m.validUntil?.let(Iso::parse)
        if (validUntil != null && validUntil <= now) return Mapped.Reject("Message expired")
        // A schedule time that has already passed by the time the phone hears about it means "send now".
        val scheduleAt = m.scheduleAt?.let(Iso::parse)?.takeIf { it > now }

        return Mapped.Accept(
            NewMessage(
                id = m.id,
                text = text,
                phoneNumbers = m.phoneNumbers.distinct(),
                simNumber = m.simNumber,
                withDeliveryReport = m.withDeliveryReport ?: true,
                priority = m.priority ?: 0,
                scheduleAt = scheduleAt,
                validUntil = validUntil,
                // The server has already checked these numbers.
                skipPhoneValidation = true,
                source = MessageEntity.SOURCE_CLOUD,
            )
        )
    }

    fun toPatch(m: MessageWithRecipients): CloudMessagePatch = CloudMessagePatch(
        id = m.message.id,
        state = m.message.state.name,
        recipients = m.recipients.sortedBy { it.phoneNumber }.map { CloudRecipientState(it.phoneNumber, it.state.name, it.error) },
        states = m.states.associate { it.state.name to Iso.format(it.updatedAt) },
    )

    /** Report for a message that was never queued here (it could not be sent at all). */
    fun failedPatch(m: CloudMessage, reason: String, now: Long): CloudMessagePatch = CloudMessagePatch(
        id = m.id,
        state = "Failed",
        recipients = m.phoneNumbers.map { CloudRecipientState(it, "Failed", reason) },
        states = mapOf("Failed" to Iso.format(now)),
    )

    fun cancelledPatch(m: CloudMessage, now: Long): CloudMessagePatch = CloudMessagePatch(
        id = m.id,
        state = "Cancelled",
        recipients = m.phoneNumbers.map { CloudRecipientState(it, "Cancelled") },
        states = mapOf("Cancelled" to Iso.format(now)),
    )
}
