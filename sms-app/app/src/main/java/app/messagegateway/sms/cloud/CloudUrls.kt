package app.messagegateway.sms.cloud

object CloudUrls {
    const val SERVER_PUBLIC = "public"
    const val SERVER_PRIVATE = "private"
    const val PUBLIC_MOBILE = "https://api.sms-gate.app/mobile/v1"

    /** The mobile API address for the chosen server. Throws IllegalArgumentException for a bad private address. */
    fun mobileBase(server: String, privateUrl: String): String =
        if (server == SERVER_PUBLIC) PUBLIC_MOBILE else normalizePrivate(privateUrl)

    /**
     * Accepts a bare host ("https://sms.example.com"), a "/api" address or the full "/api/mobile/v1" address.
     */
    fun normalizePrivate(input: String): String {
        val trimmed = input.trim().trimEnd('/')
        require(trimmed.startsWith("https://") || trimmed.startsWith("http://")) {
            "Server address must start with https://"
        }
        require(trimmed.removePrefix("https://").removePrefix("http://").isNotBlank()) { "Server address is incomplete" }
        return when {
            trimmed.endsWith("/mobile/v1") -> trimmed
            trimmed.endsWith("/api") -> "$trimmed/mobile/v1"
            else -> "$trimmed/api/mobile/v1"
        }
    }

    /** The address a system uses to send texts through this account. */
    fun thirdPartyBase(mobileBase: String): String = mobileBase.replace("/mobile/v1", "/3rdparty/v1")
}
