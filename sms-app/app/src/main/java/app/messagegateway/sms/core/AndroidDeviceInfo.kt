package app.messagegateway.sms.core

import android.Manifest
import android.content.Context
import android.content.pm.PackageManager
import android.net.ConnectivityManager
import android.net.NetworkCapabilities
import android.os.Build
import android.telephony.PhoneNumberUtils
import android.telephony.SubscriptionManager
import android.telephony.TelephonyManager
import androidx.core.content.ContextCompat
import java.net.Inet4Address
import java.util.Locale

class AndroidDeviceInfo(private val context: Context, prefs: Prefs) : DeviceInfo {
    override val deviceId: String = prefs.deviceId
    override val deviceName: String = "${Build.MANUFACTURER}/${Build.PRODUCT}"
    override val installedAt: Long = try {
        context.packageManager.getPackageInfo(context.packageName, 0).firstInstallTime
    } catch (_: Exception) {
        System.currentTimeMillis()
    }

    fun granted(permission: String) =
        ContextCompat.checkSelfPermission(context, permission) == PackageManager.PERMISSION_GRANTED

    override fun sims(): List<SimView> {
        if (!granted(Manifest.permission.READ_PHONE_STATE)) return emptyList()
        return try {
            context.getSystemService(SubscriptionManager::class.java).activeSubscriptionInfoList.orEmpty().map {
                SimView(it.simSlotIndex, it.simSlotIndex + 1, null, it.carrierName?.toString())
            }
        } catch (_: SecurityException) {
            emptyList()
        }
    }

    override fun health(): Pair<String, Map<String, String>> {
        val problems = linkedMapOf<String, String>()
        if (!granted(Manifest.permission.SEND_SMS)) problems["sendSms"] = "SEND_SMS permission is not granted"
        if (problems.isNotEmpty()) return "fail" to problems
        if (!granted(Manifest.permission.RECEIVE_SMS)) problems["receiveSms"] = "RECEIVE_SMS permission is not granted: incoming texts are not delivered"
        return (if (problems.isEmpty()) "pass" else "warn") to problems
    }

    /** Formats a local number for the SIM's country; null when it can't be done. */
    fun toE164(number: String): String? {
        val region = context.getSystemService(TelephonyManager::class.java)?.simCountryIso?.uppercase()?.takeIf { it.length == 2 }
            ?: Locale.getDefault().country.takeIf { it.length == 2 }
            ?: return null
        return PhoneNumberUtils.formatNumberToE164(number, region)
    }

    /**
     * Addresses other machines on the same network can use to reach this phone: Wi-Fi first, then wired.
     * Taken from Android's own view of each network, so the Wi-Fi address is never mixed up with a
     * mobile-data one.
     */
    fun localAddresses(): List<LocalAddress> {
        val connectivity = context.getSystemService(ConnectivityManager::class.java) ?: return emptyList()
        val candidates = try {
            connectivity.allNetworks.mapNotNull { network ->
                val capabilities = connectivity.getNetworkCapabilities(network) ?: return@mapNotNull null
                val kind = when {
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_WIFI) -> NetworkAddresses.Kind.Wifi
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET) -> NetworkAddresses.Kind.Ethernet
                    capabilities.hasTransport(NetworkCapabilities.TRANSPORT_CELLULAR) -> NetworkAddresses.Kind.Cellular
                    else -> NetworkAddresses.Kind.Other
                }
                val ips = connectivity.getLinkProperties(network)?.linkAddresses.orEmpty()
                    .mapNotNull { (it.address as? Inet4Address)?.hostAddress }
                NetworkAddresses.Candidate(kind, ips)
            }
        } catch (_: SecurityException) {
            emptyList()
        }
        // Wi-Fi before wired, so the one people usually want is on top.
        return NetworkAddresses.pickLocal(candidates.sortedBy { it.kind.ordinal })
    }
}
