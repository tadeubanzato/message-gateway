package app.messagegateway.sms.ui

import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import android.os.PowerManager
import android.provider.Settings
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.material3.FilterChip
import androidx.compose.material3.LocalContentColor
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import app.messagegateway.sms.cloud.CloudState
import app.messagegateway.sms.cloud.CloudUrls
import app.messagegateway.sms.server.ServerState

private fun Context.has(permission: String) =
    ContextCompat.checkSelfPermission(this, permission) == PackageManager.PERMISSION_GRANTED

private fun Context.ignoresBatteryOptimization() =
    getSystemService(PowerManager::class.java).isIgnoringBatteryOptimizations(packageName)

@Composable
fun HomeScreen(vm: AppViewModel, onOpenCloud: () -> Unit, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val state by vm.serverState.collectAsStateWithLifecycle()
    val cloudState by vm.cloudState.collectAsStateWithLifecycle()
    val pendingMessages by vm.pendingMessages.collectAsStateWithLifecycle()
    val pendingDeliveries by vm.pendingDeliveries.collectAsStateWithLifecycle()
    val publicIp by vm.publicIp.collectAsStateWithLifecycle()

    // Permissions can change in system settings, so they are re-read every time the screen comes back.
    var refresh by remember { mutableIntStateOf(0) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { refresh++ }
    val canSend = remember(refresh) { context.has(Manifest.permission.SEND_SMS) }
    val canReceive = remember(refresh) { context.has(Manifest.permission.RECEIVE_SMS) }
    val canReadSims = remember(refresh) { context.has(Manifest.permission.READ_PHONE_STATE) }
    val canNotify = remember(refresh) {
        Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU || context.has(Manifest.permission.POST_NOTIFICATIONS)
    }
    val unrestricted = remember(refresh) { context.ignoresBatteryOptimization() }

    var message by remember { mutableStateOf<String?>(null) }
    val permissionLauncher = rememberLauncherForActivityResult(ActivityResultContracts.RequestMultiplePermissions()) {
        refresh++
        if (it[Manifest.permission.SEND_SMS] == true || context.has(Manifest.permission.SEND_SMS)) {
            message = null
        }
    }
    fun requestPermissions(vararg permissions: String) = permissionLauncher.launch(arrayOf(*permissions))

    val localOn = state is ServerState.Running || state is ServerState.Starting
    val cloudOn = cloudState !is CloudState.Disabled

    fun askForPermissionsThen(action: () -> Unit) {
        if (canSend) {
            message = null
            action()
        } else {
            message = "Allow sending SMS first."
            // SIM info is deliberately not asked for here: Android words that prompt as "make and manage phone
            // calls". It is requested from the checklist only if the user wants to choose a SIM.
            requestPermissions(
                Manifest.permission.SEND_SMS, Manifest.permission.RECEIVE_SMS,
                *if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) arrayOf(Manifest.permission.POST_NOTIFICATIONS) else emptyArray(),
            )
        }
    }

    Column(
        modifier.verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        // --- What is on ---
        val overall = GatewayStatus.overall(state, cloudState)
        val palette = LocalStatusPalette.current
        val look = palette.of(overall)
        Card(
            Modifier.fillMaxWidth(),
            colors = CardDefaults.cardColors(containerColor = look.container, contentColor = look.onContainer),
        ) {
            Column(Modifier.padding(20.dp), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    StatusDot(look.accent)
                    Text(GatewayStatus.headline(state, cloudState), style = MaterialTheme.typography.headlineSmall)
                }
                message?.let { Text(it, fontWeight = androidx.compose.ui.text.font.FontWeight.Medium, style = MaterialTheme.typography.bodyMedium) }

                ModeRow(
                    title = "Local API",
                    subtitle = when (val s = state) {
                        is ServerState.Running -> "Listening on port ${s.port}"
                        ServerState.Starting -> "Starting…"
                        ServerState.Stopped -> "Off. Your systems reach this phone directly."
                        is ServerState.Failed -> "Could not start: ${s.reason}"
                    },
                    level = GatewayStatus.local(state),
                    checked = localOn,
                ) { on ->
                    if (on) askForPermissionsThen { vm.setServerEnabled(context, true) } else vm.setServerEnabled(context, false)
                }

                ModeRow(
                    title = "Cloud",
                    subtitle = when (val c = cloudState) {
                        CloudState.Disabled ->
                            if (vm.prefs.cloudDeviceToken != null) "Off" else "Not set up. Reach this phone through a server."
                        CloudState.Connecting -> "Connecting…"
                        is CloudState.Connected -> "Connected to " + cloudServerName(vm)
                        is CloudState.Failed -> c.reason
                    },
                    level = GatewayStatus.cloud(cloudState),
                    checked = cloudOn,
                ) { on ->
                    when {
                        !on -> vm.setCloudEnabled(context, false)
                        // First time: let the user pick the server and account before anything is registered.
                        vm.prefs.cloudDeviceToken == null -> onOpenCloud()
                        else -> askForPermissionsThen { vm.connectCloud(context) }
                    }
                }
            }
        }

        // --- How to connect ---
        var tab by remember { mutableStateOf(if (cloudOn && !localOn) HomeTab.Cloud else HomeTab.Local) }
        androidx.compose.runtime.LaunchedEffect(tab, refresh) {
            if (tab == HomeTab.Local) vm.refreshPublicIp()
        }
        SectionCard("Connect your system") {
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(tab == HomeTab.Local, { tab = HomeTab.Local }, { Text("Local") })
                FilterChip(tab == HomeTab.Cloud, { tab = HomeTab.Cloud }, { Text("Cloud") })
            }
            if (tab == HomeTab.Local) {
                val addresses = remember(state, refresh) { vm.localAddresses() }
                val port = (state as? ServerState.Running)?.port ?: vm.prefs.port
                if (addresses.isEmpty()) {
                    Text(
                        "Not connected to Wi-Fi, so there's no local address to share. Connect to Wi-Fi to see it here.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                }
                addresses.forEach { CopyRow("${it.label} address", "http://${it.ip}:$port", context) }
                PublicIpRow(vm, publicIp, port, context)
                CopyRow("Username", vm.prefs.username, context)
                var localPassword by remember { mutableStateOf(vm.prefs.password) }
                PasswordRow(localPassword, context, onNew = {
                    vm.prefs.regeneratePassword()
                    localPassword = vm.prefs.password
                    Toast(context, "New password set. Update your clients; the server must be restarted to use it.")
                })
                Text(
                    "Anyone with these can send texts from this phone. Keep the address on a private network (or Tailscale), never open it to the internet.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            } else {
                val login = vm.prefs.cloudUserLogin
                if (vm.prefs.cloudDeviceToken == null || login == null) {
                    Text(
                        "Cloud isn't set up yet. Once it is, your systems send texts to a server and the server hands them to this phone, so it can sit behind any network.",
                        style = MaterialTheme.typography.bodyMedium,
                    )
                    FilledTonalButton(onOpenCloud) { Text("Set up cloud") }
                } else {
                    // A private server is reached through your own gateway, so only the public cloud has an address to share.
                    if (vm.prefs.cloudServer == CloudUrls.SERVER_PUBLIC) {
                        CopyRow("API address", CloudUrls.thirdPartyBase(CloudUrls.PUBLIC_MOBILE), context)
                    }
                    CopyRow("Username", login, context)
                    CloudPasswordRow(vm, context)
                    TextButton(onOpenCloud) { Text("Cloud settings") }
                }
            }
        }

        // --- Activity (with the queue as a quiet footer) ---
        TrendsCard(vm)

        // --- Permissions ---
        SectionCard("Setup checklist") {
            val manage = { openAppSettings(context) }
            CheckRow("Send SMS", canSend, "Required", { requestPermissions(Manifest.permission.SEND_SMS) }, manage)
            CheckRow("Receive SMS", canReceive, "Needed to get incoming texts and webhooks", { requestPermissions(Manifest.permission.RECEIVE_SMS) }, manage)
            CheckRow("Choose SIM", canReadSims, "Optional: only needed to pick a SIM on dual-SIM phones", { requestPermissions(Manifest.permission.READ_PHONE_STATE) }, manage)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                CheckRow("Notifications", canNotify, "Shows that the server is running", { requestPermissions(Manifest.permission.POST_NOTIFICATIONS) }, manage)
            }
            CheckRow(
                "Unrestricted battery", unrestricted, "Stops Android pausing the server when the screen is off",
                onFix = {
                    context.startActivity(
                        Intent(Settings.ACTION_REQUEST_IGNORE_BATTERY_OPTIMIZATIONS, Uri.parse("package:${context.packageName}"))
                    )
                },
                // The list of apps where the exemption can be switched back to "Optimized".
                onManage = { context.startActivity(Intent(Settings.ACTION_IGNORE_BATTERY_OPTIMIZATION_SETTINGS)) },
            )
        }
    }
}

@Composable
internal fun PasswordRow(password: String, context: Context, onNew: (() -> Unit)?) {
    var shown by remember { mutableStateOf(false) }
    CopyRow(
        "Password", if (shown) password else "•".repeat(12), context, copyValue = password,
        extra = {
            SmallIconButton(
                if (shown) EyeOpenIcon else EyeClosedIcon,
                if (shown) "Hide password" else "Show password",
            ) { shown = !shown }
        },
    )
    if (onNew != null) TextButton(onClick = onNew) { Text("New password") }
}

/** The cloud account's password, with a confirmed "New password" like the Local one has. */
@Composable
internal fun CloudPasswordRow(vm: AppViewModel, context: Context) {
    var password by remember { mutableStateOf(vm.prefs.cloudUserPassword) }
    var confirming by remember { mutableStateOf(false) }
    val current = password
    if (current == null) {
        Text("Password: the one for your existing account.", style = MaterialTheme.typography.bodyMedium)
        return
    }
    PasswordRow(current, context, onNew = { confirming = true })
    if (confirming) {
        androidx.compose.material3.AlertDialog(
            onDismissRequest = { confirming = false },
            title = { Text("Change the account password?") },
            text = {
                Text(
                    "Your gateway, and anything else that signs in to this account, will stop working until you " +
                        "enter the new password there."
                )
            },
            confirmButton = {
                TextButton({
                    confirming = false
                    vm.changeCloudPassword { result ->
                        result.onSuccess {
                            password = it
                            Toast(context, "New password set. Enter it in your gateway.")
                        }.onFailure {
                            Toast(context, "Couldn't change it: ${it.message ?: it.javaClass.simpleName}")
                        }
                    }
                }) { Text("Change it") }
            },
            dismissButton = { TextButton({ confirming = false }) { Text("Cancel") } },
        )
    }
}

private fun openAppSettings(context: Context) {
    context.startActivity(Intent(Settings.ACTION_APPLICATION_DETAILS_SETTINGS, Uri.parse("package:${context.packageName}")))
}

private fun Toast(context: Context, text: String) =
    android.widget.Toast.makeText(context, text, android.widget.Toast.LENGTH_LONG).show()

@Composable
private fun Stat(value: String, label: String) {
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Text(value, style = MaterialTheme.typography.headlineMedium)
        Text(label, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

@Composable
private fun CheckRow(title: String, ok: Boolean, hint: String, onFix: () -> Unit, onManage: () -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        val palette = LocalStatusPalette.current
        StatusDot(if (ok) palette.good.accent else palette.bad.accent)
        RowSpacer()
        Column(Modifier.weight(1f)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        // An app can't take back a permission or a battery exemption itself, so once granted the way to change it
        // is Android's own screen: this button opens it.
        if (!ok) TextButton(onFix) { Text("Allow") } else TextButton(onManage) { Text("Manage") }
    }
}

private enum class HomeTab { Local, Cloud }

private fun cloudServerName(vm: AppViewModel): String =
    if (vm.prefs.cloudServer == CloudUrls.SERVER_PUBLIC) "SMSGate Cloud"
    else runCatching { java.net.URI(vm.prefs.mobileBaseUrl()).host }.getOrNull() ?: "your server"

@Composable
private fun ModeRow(title: String, subtitle: String, level: Level, checked: Boolean, onChange: (Boolean) -> Unit) {
    val look = LocalStatusPalette.current.of(level)
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, style = MaterialTheme.typography.titleMedium)
            Text(subtitle, style = MaterialTheme.typography.bodySmall, color = LocalContentColor.current.copy(alpha = 0.85f))
        }
        // Green when it works, amber while it connects, red when it is off or broken: the track says it at a glance.
        Switch(
            checked, onChange,
            colors = SwitchDefaults.colors(
                checkedThumbColor = look.onAccent, checkedTrackColor = look.accent, checkedBorderColor = look.accent,
                uncheckedThumbColor = look.onAccent, uncheckedTrackColor = look.accent, uncheckedBorderColor = look.accent,
            ),
        )
    }
}

@Composable
private fun PublicIpRow(vm: AppViewModel, state: AppViewModel.PublicIpState, port: Int, context: Context) {
    when (state) {
        AppViewModel.PublicIpState.Off, AppViewModel.PublicIpState.Idle -> Unit
        AppViewModel.PublicIpState.Loading -> Text(
            "Public IP: looking up…",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        is AppViewModel.PublicIpState.Found -> {
            CopyRow("Public IP", state.ip, context)
            Text(
                "This is what the internet sees. Other networks can reach this phone on port $port only if your router forwards it, " +
                    "which is usually not what you want. Looked up through ${vm.publicIpHost}.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton({ vm.refreshPublicIp(force = true) }) { Text("Refresh public IP") }
        }
        is AppViewModel.PublicIpState.Failed -> {
            Text(
                "Public IP: couldn't look it up (${state.reason}).",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            TextButton({ vm.refreshPublicIp(force = true) }) { Text("Try again") }
        }
    }
}
