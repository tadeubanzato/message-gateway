package app.messagegateway.sms.cloud

import kotlinx.serialization.Serializable
import kotlinx.serialization.json.JsonElement

/** Wire format of the SMSGate server's mobile API (`/mobile/v1`). */

@Serializable
data class CloudSimCard(
    val slotIndex: Int,
    val simNumber: Int,
    val phoneNumber: String? = null,
    val carrierName: String? = null,
    val iccid: String? = null,
)

@Serializable
data class CloudRegisterRequest(
    val name: String,
    val pushToken: String? = null,
    val simCards: List<CloudSimCard> = emptyList(),
)

@Serializable
data class CloudRegisterResponse(
    val id: String,
    val token: String,
    val login: String,
    /** Only returned when the server created a new account for this device. */
    val password: String? = null,
)

@Serializable
data class CloudDevicePatch(
    val id: String,
    val pushToken: String? = null,
    val simCards: List<CloudSimCard>? = null,
)

@Serializable
data class CloudTextMessage(val text: String)

@Serializable
data class CloudMessage(
    val id: String,
    val message: String? = null,
    val textMessage: CloudTextMessage? = null,
    val dataMessage: JsonElement? = null,
    val mmsMessage: JsonElement? = null,
    val phoneNumbers: List<String> = emptyList(),
    val simNumber: Int? = null,
    val withDeliveryReport: Boolean? = null,
    val isEncrypted: Boolean? = null,
    val validUntil: String? = null,
    val scheduleAt: String? = null,
    val priority: Int? = null,
    val state: String? = null,
    val createdAt: String? = null,
)

@Serializable
data class CloudRecipientState(val phoneNumber: String, val state: String, val error: String? = null)

@Serializable
data class CloudMessagePatch(
    val id: String,
    val state: String,
    val recipients: List<CloudRecipientState>,
    val states: Map<String, String>,
)

@Serializable
data class CloudPasswordChange(val currentPassword: String, val newPassword: String)

@Serializable
data class CloudWebhook(val id: String, val url: String, val event: String)

/** How a registration request proves who is asking. */
sealed interface CloudAuth {
    /** Public server: anyone may register and gets a new account. */
    data object None : CloudAuth

    /** Private server: the server's registration key. */
    data class ServerKey(val key: String) : CloudAuth

    /** Add this phone to an existing account. */
    data class Account(val login: String, val password: String) : CloudAuth
}

class CloudHttpException(val code: Int, val body: String) : java.io.IOException(
    "HTTP $code" + body.take(200).let { if (it.isBlank()) "" else ": $it" },
)

object CloudAuthChooser {
    /**
     * Decides how to prove who is asking when registering. A phone that already has an account keeps it: if the
     * server forgets the phone, registering again with the server key would create a *different* account and
     * silently invalidate the username and password the user already gave their gateway.
     *
     * @throws IllegalStateException when something the user has to fill in is missing.
     */
    fun choose(
        useExistingAccount: Boolean,
        accountLogin: String,
        accountPassword: String,
        privateServer: Boolean,
        serverKey: String,
        savedLogin: String?,
        savedPassword: String?,
    ): CloudAuth = when {
        savedLogin != null && savedPassword != null -> CloudAuth.Account(savedLogin, savedPassword)
        useExistingAccount -> {
            check(accountLogin.isNotBlank() && accountPassword.isNotEmpty()) { "Enter the account's login and password" }
            CloudAuth.Account(accountLogin.trim(), accountPassword)
        }
        privateServer -> {
            check(serverKey.isNotBlank()) { "Enter the private token from your gateway" }
            CloudAuth.ServerKey(serverKey.trim())
        }
        else -> CloudAuth.None
    }
}
