package app.messagegateway.sms.domain

/** Lifecycle of a message and of each of its recipients. Names are part of the public API. */
enum class ProcessingState {
    Pending,
    Processed,
    Sent,
    Delivered,
    Failed,
    Cancelled,
}

/**
 * Which state changes are allowed. Failed is the only terminal state. Cancelled is not terminal:
 * what the radio actually did wins over a cancel that arrived too late.
 */
object StateTransitions {
    private val table: Map<ProcessingState, Set<ProcessingState>> = mapOf(
        ProcessingState.Pending to setOf(
            ProcessingState.Processed, ProcessingState.Sent, ProcessingState.Delivered,
            ProcessingState.Failed, ProcessingState.Cancelled,
        ),
        ProcessingState.Cancelled to setOf(
            ProcessingState.Sent, ProcessingState.Delivered, ProcessingState.Failed,
        ),
        ProcessingState.Processed to setOf(
            ProcessingState.Sent, ProcessingState.Delivered, ProcessingState.Failed,
        ),
        ProcessingState.Sent to setOf(ProcessingState.Delivered, ProcessingState.Failed),
        ProcessingState.Delivered to setOf(ProcessingState.Failed),
        ProcessingState.Failed to emptySet(),
    )

    /** A move to the same state is allowed, but is a no-op. */
    fun canTransition(from: ProcessingState, to: ProcessingState): Boolean =
        from == to || to in table.getValue(from)

    /** The state of a whole message, derived from the state of its recipients. */
    fun aggregate(recipients: List<ProcessingState>): ProcessingState {
        if (recipients.isEmpty()) return ProcessingState.Pending
        if (ProcessingState.Pending in recipients) return ProcessingState.Pending
        if (ProcessingState.Processed in recipients) return ProcessingState.Processed
        val live = recipients.filter { it != ProcessingState.Failed && it != ProcessingState.Cancelled }
        return when {
            live.isEmpty() ->
                if (recipients.all { it == ProcessingState.Cancelled }) ProcessingState.Cancelled
                else ProcessingState.Failed
            live.all { it == ProcessingState.Delivered } -> ProcessingState.Delivered
            else -> ProcessingState.Sent
        }
    }
}
