package app.messagegateway.sms.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.messagegateway.sms.data.WebhookDeliveryEntity
import app.messagegateway.sms.webhooks.WebhookService

@Composable
fun WebhooksScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val context = LocalContext.current
    val hooks by vm.webhooks.collectAsStateWithLifecycle()
    val deliveries by vm.deliveries.collectAsStateWithLifecycle()
    var adding by remember { mutableStateOf(false) }
    var removing by remember { mutableStateOf<WebhookGroup?>(null) }
    var signingKey by remember { mutableStateOf(vm.prefs.webhookSigningKey) }
    val groups = remember(hooks) { WebhookGroups.group(hooks) }

    Column(
        modifier.verticalScroll(rememberScrollState()).padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
    ) {
        Text(
            "The phone calls these addresses when a text arrives or one you sent is sent, delivered or fails.",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        if (groups.isEmpty()) {
            SectionCard("No webhooks yet") {
                Text("Add an address, or turn on Incoming messages in your gateway and it appears here by itself.", style = MaterialTheme.typography.bodyMedium)
            }
        }
        groups.forEach { group ->
            val calls = deliveries.filter { it.url == group.url }
            WebhookGroupCard(group, calls, context, onRemove = { removing = group })
        }

        FilledTonalButton({ adding = true }) { Text("Add webhook") }

        SectionCard("Signing key") {
            Text(
                "Every call carries X-Timestamp and X-Signature (HMAC-SHA256 of body + timestamp, hex). Verify it to be sure the phone sent it.",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
            CopyRow("Signing key", signingKey, context)
            TextButton({ vm.prefs.regenerateSigningKey(); signingKey = vm.prefs.webhookSigningKey }) { Text("Generate a new key") }
        }

        RecentCalls(deliveries, onRetry = { vm.retryWebhooks() })
    }

    if (adding) AddWebhookDialog(vm) { adding = false }
    removing?.let { group ->
        AlertDialog(
            onDismissRequest = { removing = null },
            title = { Text("Remove this webhook?") },
            text = { Text("The phone stops calling ${group.host} for ${group.events.joinToString { it.label }}.") },
            confirmButton = { TextButton({ group.ids.forEach(vm::deleteWebhook); removing = null }) { Text("Remove") } },
            dismissButton = { TextButton({ removing = null }) { Text("Cancel") } },
        )
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun WebhookGroupCard(
    group: WebhookGroup,
    calls: List<WebhookDeliveryEntity>,
    context: android.content.Context,
    onRemove: () -> Unit,
) {
    val palette = LocalStatusPalette.current
    Card(
        Modifier.fillMaxWidth(),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(group.host, Modifier.weight(1f), style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                Pill(
                    if (group.fromServer) "From server" else "On this phone",
                    MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    group.maskedUrl, Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // The full address, secret included, is what gets copied.
                TextButton({ copyToClipboard(context, "Webhook URL", group.url) }) { Text("Copy") }
            }

            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                group.events.forEach { e ->
                    if (e.count > 1) Pill("${e.label} ×${e.count}", palette.warn.container, palette.warn.onContainer)
                    else Pill(e.label, MaterialTheme.colorScheme.primaryContainer, MaterialTheme.colorScheme.onPrimaryContainer)
                }
            }
            if (group.hasDuplicates) {
                Text(
                    "The same event is registered more than once for this address. Remove the extra one so each text is announced once.",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            val last = calls.maxByOrNull { it.createdAt }
            // Earlier failures besides the one already named in the "last call" line.
            val trouble = calls.count { it.state == WebhookDeliveryEntity.FAILED } -
                (if (last?.state == WebhookDeliveryEntity.FAILED) 1 else 0)
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                val level = when {
                    last == null -> null
                    last.state == WebhookDeliveryEntity.DELIVERED -> Level.Good
                    last.state == WebhookDeliveryEntity.FAILED -> Level.Bad
                    else -> Level.Warn
                }
                if (level != null) StatusDot(palette.of(level).accent)
                Text(
                    when {
                        last == null -> "No calls yet"
                        else -> "Last call ${formatTime(last.createdAt)} · ${last.state.lowercase()}" +
                            if (trouble > 0) " · $trouble earlier failed" else ""
                    },
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }

            if (group.fromServer) {
                Text("Managed on your server, so it can't be removed here.", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                TextButton(onRemove) { Text("Remove") }
            }
        }
    }
}

@Composable
private fun RecentCalls(deliveries: List<WebhookDeliveryEntity>, onRetry: () -> Unit) {
    SectionCard("Recent calls") {
        if (deliveries.isEmpty()) Text("Nothing sent yet.", style = MaterialTheme.typography.bodyMedium)
        deliveries.take(10).forEach { d ->
            val ok = d.state == WebhookDeliveryEntity.DELIVERED
            val failed = d.state == WebhookDeliveryEntity.FAILED
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(WebhookGroups.label(d.event), Modifier.weight(1f), style = MaterialTheme.typography.titleSmall)
                    Pill(
                        d.state,
                        if (ok) MaterialTheme.colorScheme.primaryContainer else if (failed) MaterialTheme.colorScheme.errorContainer else MaterialTheme.colorScheme.tertiaryContainer,
                        if (ok) MaterialTheme.colorScheme.onPrimaryContainer else if (failed) MaterialTheme.colorScheme.onErrorContainer else MaterialTheme.colorScheme.onTertiaryContainer,
                    )
                }
                Text(
                    formatTime(d.createdAt) +
                        (if (!ok) " · attempt ${d.attempts}/${WebhookService.MAX_ATTEMPTS}" else "") +
                        (d.lastError?.let { " · $it" } ?: ""),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        if (deliveries.any { it.state == WebhookDeliveryEntity.PENDING }) TextButton(onRetry) { Text("Retry now") }
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun AddWebhookDialog(vm: AppViewModel, onDismiss: () -> Unit) {
    var url by remember { mutableStateOf("https://") }
    var events by remember { mutableStateOf(setOf("sms:received")) }
    var error by remember { mutableStateOf<String?>(null) }
    val all = listOf("sms:received", "sms:sent", "sms:delivered", "sms:failed")
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Add webhook") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(url, { url = it }, label = { Text("URL") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                Text("Call it when a text is…", style = MaterialTheme.typography.labelMedium)
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    all.forEach { name ->
                        FilterChip(
                            selected = name in events,
                            onClick = { events = if (name in events) events - name else events + name },
                            label = { Text(WebhookGroups.label(name)) },
                        )
                    }
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            TextButton({
                if (events.isEmpty()) error = "Pick at least one event." else vm.addWebhooks(url, all.filter { it in events }) { error = it; if (it == null) onDismiss() }
            }) { Text("Add") }
        },
        dismissButton = { TextButton(onDismiss) { Text("Cancel") } },
    )
}
