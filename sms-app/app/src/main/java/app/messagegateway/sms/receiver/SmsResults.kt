package app.messagegateway.sms.receiver

/** Turns the numbers Android reports into words, and a delivery report into delivered / failed / still trying. */
object SmsResults {
    private val sendErrors = mapOf(
        1 to "Generic failure",
        2 to "Radio is off (airplane mode?)",
        3 to "No PDU provided",
        4 to "No service",
        5 to "Sending queue limit reached",
        6 to "Blocked by fixed dialing numbers (FDN)",
        7 to "Short code not allowed",
        8 to "Short codes never allowed",
        9 to "Radio not available",
        10 to "Rejected by the network",
        11 to "Invalid arguments",
        12 to "Invalid state",
        13 to "Out of memory",
        14 to "Invalid SMS format",
        15 to "System error",
        16 to "Modem error",
        17 to "Network error",
        18 to "Encoding error",
        19 to "Invalid SMSC address",
        20 to "Operation not allowed",
        21 to "Internal error",
        22 to "No resources",
        23 to "Cancelled",
        24 to "Request not supported",
    )

    fun describeSendError(resultCode: Int): String =
        sendErrors[resultCode]?.let { "$it (code $resultCode)" } ?: "Send failed (code $resultCode)"

    /**
     * [status] is the TP-Status of the delivery report (null when the report has none).
     * True when delivered, false on a permanent failure, null while the network is still trying.
     */
    fun deliveryOutcome(status: Int?, resultOk: Boolean): Boolean? = when {
        status == null -> resultOk
        status < 0x20 -> true
        status >= 0x40 -> false
        else -> null
    }
}
