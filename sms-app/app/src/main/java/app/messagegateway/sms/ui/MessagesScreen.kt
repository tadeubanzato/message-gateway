package app.messagegateway.sms.ui

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.FilterChip
import androidx.compose.material3.FloatingActionButton
import androidx.compose.material3.Icon
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
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.width
import androidx.compose.material3.Button
import androidx.compose.material3.SuggestionChip
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import app.messagegateway.sms.data.IncomingEntity
import app.messagegateway.sms.data.MessageWithRecipients
import app.messagegateway.sms.domain.ProcessingState

/** One row of the combined list: a text this phone sent, or one it received. */
private sealed interface Entry {
    val key: String
    val at: Long

    class Out(val item: MessageWithRecipients) : Entry {
        override val key get() = "out-" + item.message.id
        override val at get() = item.message.createdAt
    }

    class In(val sms: IncomingEntity) : Entry {
        override val key get() = "in-" + sms.id
        override val at get() = sms.receivedAt
    }
}

/** What the filter chips narrow the list to: a direction, or the state of the texts sent. */
private enum class Show(val label: String) {
    All("All"), Received("Received"), Outgoing("Outgoing"),
    Pending("Pending"), Sent("Sent"), Delivered("Delivered"), Failed("Failed");

    fun matches(e: Entry): Boolean = when (this) {
        All -> true
        Received -> e is Entry.In
        Outgoing -> e is Entry.Out
        else -> e is Entry.Out && e.item.message.state.name == name
    }
}

@Composable
fun MessagesScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val sent by vm.messages.collectAsStateWithLifecycle()
    val received by vm.incoming.collectAsStateWithLifecycle()
    var filter by remember { mutableStateOf(Show.All) }
    var selectedKey by remember { mutableStateOf<String?>(null) }
    var composing by remember { mutableStateOf(false) }

    val all = remember(sent, received) {
        (sent.map { Entry.Out(it) } + received.map { Entry.In(it) }).sortedByDescending { it.at }
    }
    val shown = remember(all, filter) { all.filter(filter::matches) }
    val selected = selectedKey?.let { k -> all.firstOrNull { it.key == k } }

    Box(modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                items(Show.entries.toList()) { show ->
                    FilterChip(filter == show, { filter = show }, { Text(show.label) })
                }
            }
            if (shown.isEmpty()) {
                EmptyState(
                    when {
                        all.isEmpty() -> "No messages yet. Tap + to send a test text, or text this phone once \"Receive SMS\" is allowed on the Home tab."
                        filter == Show.Received -> "No texts received yet. Allow \"Receive SMS\" on the Home tab, then text this phone."
                        else -> "Nothing here."
                    },
                )
            } else {
                LazyColumn(
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 96.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(shown, key = { it.key }) { e ->
                        when (e) {
                            is Entry.Out -> MessageCard(e.item) { selectedKey = e.key }
                            is Entry.In -> ReceivedCard(e.sms) { selectedKey = e.key }
                        }
                    }
                }
            }
        }
        FloatingActionButton(
            onClick = { composing = true },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Icon(Icons.Filled.Add, contentDescription = "New message") }
    }

    if (composing) ComposeDialog(vm) { composing = false }
    when (selected) {
        is Entry.Out -> MessageDialog(vm, selected.item) { selectedKey = null }
        is Entry.In -> ReceivedDialog(selected.sms) { selectedKey = null }
        null -> {}
    }
}

/** The tags saying what a message is, then its time at the end of the line. */
@Composable
private fun CardFooter(time: Long, tags: @Composable () -> Unit) {
    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
        tags()
        Spacer(Modifier.weight(1f))
        Text(formatTime(time), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** The muted "Sent · Cloud · SIM 2 · 3:41 PM" line under a card's text. */
private fun metaLine(direction: String, source: String?, sim: Int?, time: Long): String =
    listOfNotNull(direction, source, sim?.let { "SIM $it" }, formatTime(time)).joinToString(" · ")

@Composable
private fun MessageCard(item: MessageWithRecipients, onClick: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "To " + item.recipients.map { it.phoneNumber }.sorted().joinToString(", "),
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                StateChip(item.message.state)
            }
            Text(item.message.text, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
            val fromServer = item.message.source == app.messagegateway.sms.data.MessageEntity.SOURCE_CLOUD
            CardFooter(item.message.createdAt) {
                // Replies the server sends through this phone (a /speed answer, say) are not typed here.
                if (fromServer) OriginTag(Origin.Server, "Server reply") else OriginTag(Origin.Local, "Sent here")
                item.message.simNumber?.let { OriginTag(Origin.Sim, "SIM $it") }
            }
        }
    }
}

@Composable
private fun ReceivedCard(sms: IncomingEntity, onClick: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    "From " + sms.sender,
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                OriginTag(Origin.Received)
            }
            Text(sms.body, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
            CardFooter(sms.receivedAt) {
                // The server's bot answers texts that start with a slash.
                if (sms.body.trimStart().startsWith("/")) OriginTag(Origin.Command)
                sms.simNumber?.let { OriginTag(Origin.Sim, "SIM $it") }
            }
        }
    }
}

@Composable
private fun ReceivedDialog(sms: IncomingEntity, onDismiss: () -> Unit) {
    val context = androidx.compose.ui.platform.LocalContext.current
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Received message") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(sms.body, style = MaterialTheme.typography.bodyLarge)
                CopyRow("From", sms.sender, context)
                Text(
                    metaLine("Received", null, sms.simNumber, sms.receivedAt),
                    style = MaterialTheme.typography.bodySmall,
                )
                Text("ID ${sms.id}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        },
        confirmButton = { TextButton(onDismiss) { Text("Close") } },
    )
}

@Composable
private fun MessageDialog(vm: AppViewModel, item: MessageWithRecipients, onDismiss: () -> Unit) {
    var error by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Sent message") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(item.message.text, style = MaterialTheme.typography.bodyLarge)
                Text("To", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                item.recipients.sortedBy { it.phoneNumber }.forEach { r ->
                    Column {
                        Row(verticalAlignment = Alignment.CenterVertically) {
                            Text(r.phoneNumber, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium)
                            StateChip(r.state)
                        }
                        r.error?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error) }
                    }
                }
                Text("History", style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                item.states.sortedBy { it.updatedAt }.forEach {
                    Text("${it.state.name} · ${formatTime(it.updatedAt)}", style = MaterialTheme.typography.bodySmall)
                }
                Text("ID ${item.message.id}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = { TextButton(onDismiss) { Text("Close") } },
        dismissButton = {
            if (item.message.state == ProcessingState.Pending) {
                TextButton({ vm.cancel(item.message.id) { error = it; if (it == null) onDismiss() } }) { Text("Cancel message") }
            }
        },
    )
}

@Composable
private fun ComposeDialog(vm: AppViewModel, onDismiss: () -> Unit) {
    var phone by remember { mutableStateOf("") }
    var text by remember { mutableStateOf("") }
    var sim by remember { mutableStateOf<Int?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    val sims = remember { vm.sims() }

    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("New message") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                OutlinedTextField(
                    phone, { phone = it }, label = { Text("Phone number") }, singleLine = true,
                    keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Phone),
                    modifier = Modifier.fillMaxWidth(),
                )
                OutlinedTextField(text, { text = it }, label = { Text("Message") }, minLines = 3, modifier = Modifier.fillMaxWidth())
                if (sims.size > 1) {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        FilterChip(sim == null, { sim = null }, { Text("Default SIM") })
                        sims.forEach { FilterChip(sim == it.simNumber, { sim = it.simNumber }, { Text("SIM ${it.simNumber}") }) }
                    }
                }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodySmall) }
            }
        },
        confirmButton = {
            TextButton({ vm.send(phone, text, sim) { error = it; if (it == null) onDismiss() } }) { Text("Send") }
        },
        dismissButton = { TextButton(onDismiss) { Text("Cancel") } },
    )
}
