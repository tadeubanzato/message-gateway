package app.messagegateway.sms.core

import android.content.Context
import android.content.SharedPreferences
import androidx.core.content.edit
import app.messagegateway.sms.cloud.CloudAuth
import app.messagegateway.sms.cloud.CloudAuthChooser
import app.messagegateway.sms.cloud.CloudSettings
import app.messagegateway.sms.cloud.CloudUrls

/** All user-changeable settings, kept in one place. */
class Prefs(context: Context) : ApiCredentials, SendSettings, CloudSettings {
    private val sp: SharedPreferences =
        context.applicationContext.getSharedPreferences("settings", Context.MODE_PRIVATE)

    val deviceId: String = sp.getString(KEY_DEVICE_ID, null) ?: Ids.random(21).also { sp.edit { putString(KEY_DEVICE_ID, it) } }

    override val username: String = sp.getString(KEY_USER, null) ?: "sms-${Ids.random(6)}".also { sp.edit { putString(KEY_USER, it) } }

    override var password: String = sp.getString(KEY_PASSWORD, null) ?: Ids.random(16).also { sp.edit { putString(KEY_PASSWORD, it) } }
        private set

    /** Key that signs webhook bodies; a receiver can verify it to know the phone sent them. */
    var webhookSigningKey: String = sp.getString(KEY_SIGNING, null) ?: Ids.random(32).also { sp.edit { putString(KEY_SIGNING, it) } }
        private set

    var port: Int
        get() = sp.getInt(KEY_PORT, 8080)
        set(value) = sp.edit { putInt(KEY_PORT, value) }

    var serverEnabled: Boolean
        get() = sp.getBoolean(KEY_SERVER, false)
        set(value) = sp.edit { putBoolean(KEY_SERVER, value) }

    var startOnBoot: Boolean
        get() = sp.getBoolean(KEY_BOOT, true)
        set(value) = sp.edit { putBoolean(KEY_BOOT, value) }

    /** Hold a partial wake lock while the server runs so the phone answers with the screen off. */
    var keepAwake: Boolean
        get() = sp.getBoolean(KEY_AWAKE, true)
        set(value) = sp.edit { putBoolean(KEY_AWAKE, value) }

    /** Also hold a high-performance Wi-Fi lock. Lower latency on Wi-Fi, noticeably more battery. */
    var wifiHighPerformance: Boolean
        get() = sp.getBoolean(KEY_WIFI, false)
        set(value) = sp.edit { putBoolean(KEY_WIFI, value) }

    override var sendIntervalMinSec: Int
        get() = sp.getInt(KEY_INTERVAL_MIN, 0)
        set(value) = sp.edit { putInt(KEY_INTERVAL_MIN, value) }

    override var sendIntervalMaxSec: Int
        get() = sp.getInt(KEY_INTERVAL_MAX, 0)
        set(value) = sp.edit { putInt(KEY_INTERVAL_MAX, value) }

    override var rateLimitCount: Int
        get() = sp.getInt(KEY_LIMIT_COUNT, 0)
        set(value) = sp.edit { putInt(KEY_LIMIT_COUNT, value) }

    override var rateLimitPeriodMin: Int
        get() = sp.getInt(KEY_LIMIT_PERIOD, 30)
        set(value) = sp.edit { putInt(KEY_LIMIT_PERIOD, value) }

    /** Ask the SMSGate server what the internet sees this phone as, to show a public address on Home. */
    var lookupPublicIp: Boolean
        get() = sp.getBoolean(KEY_PUBLIC_IP, true)
        set(value) = sp.edit { putBoolean(KEY_PUBLIC_IP, value) }

    var retentionDays: Int
        get() = sp.getInt(KEY_RETENTION, 30)
        set(value) = sp.edit { putInt(KEY_RETENTION, value) }

    // --- Cloud -------------------------------------------------------------------------------------

    override var cloudEnabled: Boolean
        get() = sp.getBoolean(KEY_CLOUD, false)
        set(value) = sp.edit { putBoolean(KEY_CLOUD, value) }

    /** [CloudUrls.SERVER_PUBLIC] (the SMSGate cloud) or [CloudUrls.SERVER_PRIVATE] (your own server). */
    var cloudServer: String
        get() = sp.getString(KEY_CLOUD_SERVER, CloudUrls.SERVER_PUBLIC) ?: CloudUrls.SERVER_PUBLIC
        set(value) = sp.edit { putString(KEY_CLOUD_SERVER, value) }

    var cloudPrivateUrl: String
        get() = sp.getString(KEY_CLOUD_URL, "") ?: ""
        set(value) = sp.edit { putString(KEY_CLOUD_URL, value) }

    /** The private server's registration key. */
    var cloudServerKey: String
        get() = sp.getString(KEY_CLOUD_KEY, "") ?: ""
        set(value) = sp.edit { putString(KEY_CLOUD_KEY, value) }

    /** True to add this phone to an existing account instead of creating a new one. */
    var cloudUseExistingAccount: Boolean
        get() = sp.getBoolean(KEY_CLOUD_EXISTING, false)
        set(value) = sp.edit { putBoolean(KEY_CLOUD_EXISTING, value) }

    var cloudAccountLogin: String
        get() = sp.getString(KEY_CLOUD_LOGIN, "") ?: ""
        set(value) = sp.edit { putString(KEY_CLOUD_LOGIN, value) }

    var cloudAccountPassword: String
        get() = sp.getString(KEY_CLOUD_PASSWORD, "") ?: ""
        set(value) = sp.edit { putString(KEY_CLOUD_PASSWORD, value) }

    override var cloudDeviceToken: String?
        get() = sp.getString(KEY_CLOUD_TOKEN, null)
        set(value) = sp.edit { putString(KEY_CLOUD_TOKEN, value) }

    override var cloudDeviceId: String?
        get() = sp.getString(KEY_CLOUD_DEVICE, null)
        set(value) = sp.edit { putString(KEY_CLOUD_DEVICE, value) }

    /** The account this phone is registered under: what a system uses to send texts through the server. */
    var cloudUserLogin: String?
        get() = sp.getString(KEY_CLOUD_USER, null)
        set(value) = sp.edit { putString(KEY_CLOUD_USER, value) }

    /** Only known when the server created the account for this phone. */
    override var cloudUserPassword: String?
        get() = sp.getString(KEY_CLOUD_USER_PASSWORD, null)
        set(value) = sp.edit { putString(KEY_CLOUD_USER_PASSWORD, value) }

    override fun mobileBaseUrl(): String = CloudUrls.mobileBase(cloudServer, cloudPrivateUrl)

    override fun registrationAuth(): CloudAuth = CloudAuthChooser.choose(
        useExistingAccount = cloudUseExistingAccount,
        accountLogin = cloudAccountLogin,
        accountPassword = cloudAccountPassword,
        privateServer = cloudServer == CloudUrls.SERVER_PRIVATE,
        serverKey = cloudServerKey,
        savedLogin = cloudUserLogin,
        savedPassword = cloudUserPassword,
    )

    override fun saveRegistration(deviceId: String, token: String, login: String, password: String?) {
        cloudDeviceId = deviceId
        cloudDeviceToken = token
        cloudUserLogin = login
        // A server returns the password only when it creates the account; keep the one we already have otherwise.
        if (password != null) cloudUserPassword = password
        else if (cloudUseExistingAccount) cloudUserPassword = cloudAccountPassword
    }

    /** Forgets this phone's registration (not the settings), so the next connect registers again. */
    fun clearCloudRegistration() = sp.edit {
        remove(KEY_CLOUD_TOKEN); remove(KEY_CLOUD_DEVICE); remove(KEY_CLOUD_USER); remove(KEY_CLOUD_USER_PASSWORD)
    }

    fun regeneratePassword() {
        password = Ids.random(16)
        sp.edit { putString(KEY_PASSWORD, password) }
    }

    fun regenerateSigningKey() {
        webhookSigningKey = Ids.random(32)
        sp.edit { putString(KEY_SIGNING, webhookSigningKey) }
    }

    private companion object {
        const val KEY_DEVICE_ID = "device_id"
        const val KEY_USER = "username"
        const val KEY_PASSWORD = "password"
        const val KEY_SIGNING = "webhook_signing_key"
        const val KEY_PORT = "port"
        const val KEY_SERVER = "server_enabled"
        const val KEY_BOOT = "start_on_boot"
        const val KEY_AWAKE = "keep_awake"
        const val KEY_WIFI = "wifi_high_performance"
        const val KEY_INTERVAL_MIN = "send_interval_min"
        const val KEY_INTERVAL_MAX = "send_interval_max"
        const val KEY_LIMIT_COUNT = "rate_limit_count"
        const val KEY_LIMIT_PERIOD = "rate_limit_period_min"
        const val KEY_RETENTION = "retention_days"
        const val KEY_PUBLIC_IP = "lookup_public_ip"
        const val KEY_CLOUD = "cloud_enabled"
        const val KEY_CLOUD_SERVER = "cloud_server"
        const val KEY_CLOUD_URL = "cloud_private_url"
        const val KEY_CLOUD_KEY = "cloud_server_key"
        const val KEY_CLOUD_EXISTING = "cloud_existing_account"
        const val KEY_CLOUD_LOGIN = "cloud_account_login"
        const val KEY_CLOUD_PASSWORD = "cloud_account_password"
        const val KEY_CLOUD_TOKEN = "cloud_device_token"
        const val KEY_CLOUD_DEVICE = "cloud_device_id"
        const val KEY_CLOUD_USER = "cloud_user_login"
        const val KEY_CLOUD_USER_PASSWORD = "cloud_user_password"
    }
}
