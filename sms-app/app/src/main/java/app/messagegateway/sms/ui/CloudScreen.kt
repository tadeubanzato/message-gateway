package app.messagegateway.sms.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.messagegateway.sms.cloud.CloudState
import app.messagegateway.sms.cloud.CloudUrls

@Composable
fun CloudScreen(vm: AppViewModel, onBack: () -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val prefs = vm.prefs
    val state by vm.cloudState.collectAsStateWithLifecycle()
    val lastSync by vm.cloudLastSync.collectAsStateWithLifecycle()
    val on = state !is CloudState.Disabled
    val registered = prefs.cloudDeviceToken != null

    // The form edits local copies; nothing is saved until "Connect".
    var privateServer by remember { mutableStateOf(prefs.cloudServer == CloudUrls.SERVER_PRIVATE) }
    var url by remember { mutableStateOf(prefs.cloudPrivateUrl) }
    var key by remember { mutableStateOf(prefs.cloudServerKey) }
    var existing by remember { mutableStateOf(prefs.cloudUseExistingAccount) }
    var login by remember { mutableStateOf(prefs.cloudAccountLogin) }
    var password by remember { mutableStateOf(prefs.cloudAccountPassword) }
    var error by remember { mutableStateOf<String?>(null) }

    // A connected phone that can't send would fail every message, so SMS permission comes first.
    val permissions = remember {
        arrayOf(Manifest.permission.SEND_SMS, Manifest.permission.RECEIVE_SMS) +
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) arrayOf(Manifest.permission.POST_NOTIFICATIONS) else emptyArray()
    }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) { granted ->
        if (granted[Manifest.permission.SEND_SMS] == true) vm.connectCloud(context)
        else error = "Allow sending SMS to connect: this phone can't send texts without it."
    }

    fun save(): Boolean {
        prefs.cloudServer = if (privateServer) CloudUrls.SERVER_PRIVATE else CloudUrls.SERVER_PUBLIC
        prefs.cloudPrivateUrl = url.trim()
        prefs.cloudServerKey = key.trim()
        prefs.cloudUseExistingAccount = existing
        prefs.cloudAccountLogin = login.trim()
        prefs.cloudAccountPassword = password
        error = vm.cloudProblem()
        return error == null
    }

    Column(modifier.verticalScroll(rememberScrollState()).padding(horizontal = 16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            TextButton(onBack) { Text("‹ Back") }
            Text("Cloud", Modifier.weight(1f), style = MaterialTheme.typography.titleLarge)
        }

        Column(verticalArrangement = Arrangement.spacedBy(16.dp), modifier = Modifier.padding(vertical = 8.dp)) {
            SectionCard("Status") {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatusDot(LocalStatusPalette.current.of(GatewayStatus.cloud(state)).accent)
                    Text(
                        when (val s = state) {
                            CloudState.Disabled -> if (registered) "Off" else "Not connected"
                            CloudState.Connecting -> "Connecting…"
                            is CloudState.Connected -> "Connected"
                            is CloudState.Failed -> "Can't connect"
                        },
                        style = MaterialTheme.typography.titleMedium,
                    )
                }
                (state as? CloudState.Failed)?.let {
                    Text(it.reason, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium)
                }
                lastSync?.takeIf { on }?.let {
                    Text("Last sync ${formatTime(it)}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                prefs.cloudDeviceId?.let { CopyRow("Device ID", it, context) }
                if (on) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilledTonalButton({ vm.syncCloudNow() }) { Text("Sync now") }
                        OutlinedButton({ vm.setCloudEnabled(context, false) }) { Text("Turn off") }
                    }
                }
            }

            val accountLogin = prefs.cloudUserLogin
            if (registered && accountLogin != null) {
                SectionCard("Your account") {
                    Text(
                        "Enter these where your system sends texts from, for example in your gateway's SMS settings.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    if (prefs.cloudServer == CloudUrls.SERVER_PUBLIC) {
                        CopyRow("API address", CloudUrls.thirdPartyBase(CloudUrls.PUBLIC_MOBILE), context)
                    }
                    CopyRow("Username", accountLogin, context)
                    CloudPasswordRow(vm, context)
                }
            }

            SectionCard("Server") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(!privateServer, { privateServer = false }, { Text("SMSGate Cloud") })
                    FilterChip(privateServer, { privateServer = true }, { Text("My own server") })
                }
                if (!privateServer) {
                    Text(
                        "The public server at api.sms-gate.app, run by the SMSGate project. Free, no sign-up. Texts pass through it.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    // The three things to enter come from one page of the user's own gateway; say where.
                    Text(
                        "1. Open your Message Gateway and sign in.\n" +
                            "2. Go to Channels → SMS → Android phone (SMSGate).\n" +
                            "3. Copy the API URL and the Private token into the boxes below.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedTextField(
                        url, { url = it }, label = { Text("API URL") },
                        placeholder = { Text("https://gateway.example.com/api/mobile/v1") },
                        supportingText = { Text("As your gateway shows it, or just your gateway's address. Must be https.") },
                        singleLine = true,
                        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
                        modifier = Modifier.fillMaxWidth(),
                    )
                    if (!existing) {
                        OutlinedTextField(
                            key, { key = it }, label = { Text("Private token") },
                            supportingText = { Text("Only needed the first time, to create the phone's account.") },
                            singleLine = true, visualTransformation = PasswordVisualTransformation(),
                            modifier = Modifier.fillMaxWidth(),
                        )
                    }
                }
            }

            SectionCard("Account") {
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(!existing, { existing = false }, { Text("New account") })
                    FilterChip(existing, { existing = true }, { Text("Existing account") })
                }
                if (!existing) {
                    Text(
                        "The server creates a username and password for this phone and this screen shows them once you're connected. " +
                            "Enter them back in your gateway (its SMS settings) so it can send through this phone.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    Text(
                        "Adds this phone to a phone account you already have (the username and password from a previous setup), next to your other phones. No token needed.",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    OutlinedTextField(login, { login = it }, label = { Text("Username") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    OutlinedTextField(
                        password, { password = it }, label = { Text("Password") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation(), modifier = Modifier.fillMaxWidth(),
                    )
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
                if (!on) {
                    Button(
                        onClick = {
                            // Compare with what is saved *before* saving: a changed server or account means this
                            // phone has to register again.
                            val changed = prefs.cloudDeviceToken != null &&
                                changedSinceRegistration(vm, privateServer, url, existing, login)
                            if (save()) {
                                if (changed) prefs.clearCloudRegistration()
                                val canSend = ContextCompat.checkSelfPermission(context, Manifest.permission.SEND_SMS) ==
                                    PackageManager.PERMISSION_GRANTED
                                if (canSend) vm.connectCloud(context) else permissionLauncher.launch(permissions)
                            }
                        },
                        modifier = Modifier.fillMaxWidth(),
                    ) { Text(if (registered) "Turn on" else "Connect") }
                }
                if (registered) {
                    TextButton({ vm.forgetCloud(context) }) { Text("Disconnect and forget this phone") }
                }
            }

            SectionCard("How it works") {
                Text(
                    "Your system sends a text to the server. The server tells this phone over a connection the phone keeps open, " +
                        "and the phone sends the text and reports back. Received texts and your webhooks sync the same way.",
                    style = MaterialTheme.typography.bodySmall,
                )
                Text(
                    "This app can't use Google push, so it keeps that connection open. That uses more battery than local mode; " +
                        "turn off \"Keep CPU awake\" in Settings if you only send occasionally.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

/** True when the server or account in the form differs from the one this phone registered with. */
private fun changedSinceRegistration(vm: AppViewModel, privateServer: Boolean, url: String, existing: Boolean, login: String): Boolean {
    val prefs = vm.prefs
    val wasPrivate = prefs.cloudServer == CloudUrls.SERVER_PRIVATE
    return wasPrivate != privateServer ||
        (privateServer && url.trim() != prefs.cloudPrivateUrl.trim()) ||
        (existing && prefs.cloudUserLogin != login.trim())
}
