package app.messagegateway.sms.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilledTonalButton
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
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

        NeedsAttention(deliveries, onRetry = { vm.retryWebhooks() })

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
                if (group.fromServer) OriginTag(Origin.Server, "From server") else OriginTag(Origin.Local, "On this phone")
            }

            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    group.maskedUrl, Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall, fontFamily = FontFamily.Monospace,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                // The full address, secret included, is what gets copied.
                SmallIconButton(ClipboardIcon, "Copy webhook URL") { copyToClipboard(context, "Webhook URL", group.url) }
            }

            FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                group.events.forEach { e ->
                    // A server's copies can't be removed here, so only a phone-side duplicate is worth flagging.
                    if (e.count > 1 && !group.fromServer) Tag("${e.label} · registered ${e.count}×", palette.warn.container, palette.warn.onContainer)
                    else EventTag(e.event, e.label)
                }
            }
            if (group.hasDuplicates && !group.fromServer) {
                val twice = group.events.filter { it.count > 1 }.joinToString(" and ") { it.label }
                Text(
                    "$twice is registered more than once for this address, so each of those events is sent to it " +
                        (if (group.fromServer) "twice. The copies are set up on your server; remove the extra one there."
                        else "twice. Remove this webhook and add it again to get one."),
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

            var showCalls by remember { mutableStateOf(false) }
            if (calls.isNotEmpty()) {
                TextButton({ showCalls = !showCalls }) { Text(if (showCalls) "Hide recent calls" else "Show recent calls") }
            }
            if (showCalls) calls.sortedByDescending { it.createdAt }.take(5).forEach { CallRow(it, showHost = false) }

            if (group.fromServer) {
                Text("Managed on your server, so it can't be removed here.", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            } else {
                TextButton(onRemove) { Text("Remove") }
            }
        }
    }
}

/** How far back a failed call still counts as something to look at. */
private const val FAILED_WINDOW_MS = 24 * 60 * 60 * 1000L
private const val ATTENTION_SHOWN = 5

/** Calls still being retried, and ones that gave up today. Nothing shows while every call has gone through. */
@Composable
private fun NeedsAttention(deliveries: List<WebhookDeliveryEntity>, onRetry: () -> Unit) {
    val now = System.currentTimeMillis()
    val trouble = deliveries.filter {
        it.state == WebhookDeliveryEntity.PENDING ||
            (it.state == WebhookDeliveryEntity.FAILED && now - it.createdAt < FAILED_WINDOW_MS)
    }.sortedByDescending { it.createdAt }
    if (trouble.isEmpty()) return

    SectionCard("Needs attention") {
        Text(
            "Calls that are still being retried, and ones that failed in the last 24 hours.",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        trouble.take(ATTENTION_SHOWN).forEach { CallRow(it, showHost = true) }
        if (trouble.size > ATTENTION_SHOWN) {
            Text(
                "and ${trouble.size - ATTENTION_SHOWN} more",
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        if (trouble.any { it.state == WebhookDeliveryEntity.PENDING }) TextButton(onRetry) { Text("Retry now") }
    }
}

/** One call: what happened, how it ended, and when. Unfinished calls also show the attempt and the error. */
@Composable
private fun CallRow(d: WebhookDeliveryEntity, showHost: Boolean) {
    val ok = d.state == WebhookDeliveryEntity.DELIVERED
    val failed = d.state == WebhookDeliveryEntity.FAILED
    val status = LocalStatusPalette.current
    Column {
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            Text(WebhookGroups.label(d.event), style = MaterialTheme.typography.titleSmall)
            if (showHost) {
                Text(
                    "to " + WebhookGroups.hostOf(d.url),
                    Modifier.weight(1f),
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 1,
                    overflow = androidx.compose.ui.text.style.TextOverflow.Ellipsis,
                )
            } else {
                Spacer(Modifier.weight(1f))
            }
            val style = status.of(if (ok) Level.Good else if (failed) Level.Bad else Level.Warn)
            Tag(d.state, style.container, style.onContainer)
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

/** An event a webhook listens for, in the same colors the message cards use for it. */
@Composable
private fun EventTag(event: String, label: String) {
    val status = LocalStatusPalette.current
    when (event) {
        "sms:received" -> OriginTag(Origin.Received, label)
        "sms:delivered" -> Tag(label, status.good.container, status.good.onContainer)
        "sms:failed" -> Tag(label, status.bad.container, status.bad.onContainer)
        else -> OriginTag(Origin.Sent, label)
    }
}
