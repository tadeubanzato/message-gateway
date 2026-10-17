package app.messagegateway.sms.core

/** A way other machines can reach this phone on the network it is currently on. */
data class LocalAddress(val label: String, val ip: String)

object NetworkAddresses {
    enum class Kind { Wifi, Ethernet, Cellular, Other }

    data class Candidate(val kind: Kind, val ips: List<String>)

    /**
     * Keeps the addresses a computer on the same network could use: Wi-Fi and wired. Mobile-data addresses are
     * left out because the carrier does not let other devices reach them, and listing them next to the Wi-Fi
     * one makes it unclear which to copy.
     */
    fun pickLocal(candidates: List<Candidate>): List<LocalAddress> =
        candidates.flatMap { candidate ->
            val label = when (candidate.kind) {
                Kind.Wifi -> "Wi-Fi"
                Kind.Ethernet -> "Ethernet"
                else -> return@flatMap emptyList()
            }
            candidate.ips.filter(::isUsableIpv4).map { LocalAddress(label, it) }
        }.distinctBy { it.ip }

    /** Link-local (169.254.x.x) and unspecified addresses are not reachable from other devices. */
    fun isUsableIpv4(ip: String): Boolean {
        val parts = ip.split('.')
        if (parts.size != 4 || parts.any { it.toIntOrNull() !in 0..255 }) return false
        return !ip.startsWith("169.254.") && !ip.startsWith("127.") && ip != "0.0.0.0"
    }
}
