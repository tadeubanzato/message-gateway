package app.messagegateway.sms.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle

@Composable
fun SettingsScreen(vm: AppViewModel, onOpenLogs: () -> Unit, onOpenCloud: () -> Unit, modifier: Modifier = Modifier) {
    val prefs = vm.prefs
    Column(
        modifier.verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        SectionCard("Server") {
            NumberField("Port", prefs.port, 1024..65535) { prefs.port = it }
            Text("A new port is used the next time the server starts.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            SwitchRow("Start after reboot", "Brings the server back when the phone restarts.", prefs.startOnBoot) { prefs.startOnBoot = it }
            SwitchRow(
                "Keep CPU awake", "Lets the phone answer requests with the screen off. Turn off to save battery if you only send occasionally.",
                prefs.keepAwake,
            ) { prefs.keepAwake = it }
            SwitchRow(
                "High-performance Wi-Fi", "Lower latency on Wi-Fi but uses noticeably more battery.",
                prefs.wifiHighPerformance,
            ) { prefs.wifiHighPerformance = it }
            SwitchRow(
                "Show public IP", "Looks up the address the internet sees on Home, by asking the SMSGate server. Turn off to make no such request.",
                prefs.lookupPublicIp,
            ) { prefs.lookupPublicIp = it }
            Text("Wake-lock changes apply the next time the server starts.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }

        SectionCard("Cloud") {
            Text(
                "Reach this phone through a server instead of directly.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            FilledTonalButton(onOpenCloud) { Text("Cloud settings") }
        }

        SectionCard("Sending speed") {
            Text(
                "Off by default: texts go out as fast as the phone allows. Turn on pauses or a limit if your carrier throttles bursts.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            NumberField("Pause between texts, minimum (seconds, 0 = none)", prefs.sendIntervalMinSec, 0..3600) { prefs.sendIntervalMinSec = it }
            NumberField("Pause between texts, maximum (seconds, 0 = none)", prefs.sendIntervalMaxSec, 0..3600) { prefs.sendIntervalMaxSec = it }
            NumberField("Limit: texts per period (0 = no limit)", prefs.rateLimitCount, 0..10_000) { prefs.rateLimitCount = it }
            NumberField("Limit period (minutes)", prefs.rateLimitPeriodMin, 1..1440) { prefs.rateLimitPeriodMin = it }
        }

        SectionCard("Data") {
            NumberField("Keep history for (days)", prefs.retentionDays, 1..3650) { prefs.retentionDays = it }
            FilledTonalButton(onOpenLogs) { Text("View logs") }
        }
    }
}

@Composable
private fun SwitchRow(title: String, hint: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    var value by remember { mutableStateOf(checked) }
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f).padding(end = 12.dp)) {
            Text(title, style = MaterialTheme.typography.bodyLarge)
            Text(hint, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Switch(value, { value = it; onChange(it) })
    }
}

/** A number box that only saves values inside [range]. */
@Composable
private fun NumberField(label: String, initial: Int, range: IntRange, onSave: (Int) -> Unit) {
    var text by remember { mutableStateOf(initial.toString()) }
    val parsed = text.toIntOrNull()
    val valid = parsed != null && parsed in range
    OutlinedTextField(
        value = text,
        onValueChange = { input ->
            text = input.filter { it.isDigit() }.take(6)
            text.toIntOrNull()?.takeIf { it in range }?.let(onSave)
        },
        label = { Text(label) },
        isError = !valid,
        supportingText = if (valid) null else ({ Text("Between ${range.first} and ${range.last}") }),
        singleLine = true,
        keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
        modifier = Modifier.fillMaxWidth(),
    )
}

@Composable
fun LogsScreen(vm: AppViewModel, onBack: () -> Unit, modifier: Modifier = Modifier) {
    val logs by vm.logs.collectAsStateWithLifecycle()
    Column(modifier) {
        Row(Modifier.padding(horizontal = 16.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onBack) { Text("‹ Back") }
            Text("Logs", Modifier.weight(1f), style = MaterialTheme.typography.titleMedium)
            TextButton({ vm.clearLogs() }) { Text("Clear") }
        }
        if (logs.isEmpty()) {
            EmptyState("No log entries yet.")
        } else {
            LazyColumn(contentPadding = PaddingValues(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(logs, key = { it.id }) { entry ->
                    Column {
                        Text(
                            "${formatTime(entry.createdAt)} · ${entry.level} · ${entry.tag}",
                            style = MaterialTheme.typography.labelSmall,
                            color = if (entry.level == "ERROR") MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                        Text(entry.message, style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace)
                    }
                }
            }
        }
    }
}
