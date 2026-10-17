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
import app.messagegateway.sms.data.MessageWithRecipients
import app.messagegateway.sms.domain.ProcessingState

@Composable
fun MessagesScreen(vm: AppViewModel, modifier: Modifier = Modifier) {
    val all by vm.messages.collectAsStateWithLifecycle()
    var filter by remember { mutableStateOf<ProcessingState?>(null) }
    var selectedId by remember { mutableStateOf<String?>(null) }
    var composing by remember { mutableStateOf(false) }

    val shown = remember(all, filter) { all.filter { filter == null || it.message.state == filter } }
    val selected = selectedId?.let { id -> all.firstOrNull { it.message.id == id } }

    Box(modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            LazyRow(contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                item { FilterChip(filter == null, { filter = null }, { Text("All") }) }
                items(listOf(ProcessingState.Pending, ProcessingState.Sent, ProcessingState.Delivered, ProcessingState.Failed)) { state ->
                    FilterChip(filter == state, { filter = state }, { Text(state.name) })
                }
            }
            if (shown.isEmpty()) {
                EmptyState(if (all.isEmpty()) "No messages yet. Tap + to send a test text." else "Nothing in this state.")
            } else {
                LazyColumn(
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(start = 16.dp, end = 16.dp, top = 4.dp, bottom = 96.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(shown, key = { it.message.id }) { item -> MessageCard(item) { selectedId = item.message.id } }
                }
            }
        }
        FloatingActionButton(
            onClick = { composing = true },
            modifier = Modifier.align(Alignment.BottomEnd).padding(16.dp),
        ) { Icon(Icons.Filled.Add, contentDescription = "New message") }
    }

    if (composing) ComposeDialog(vm) { composing = false }
    selected?.let { MessageDialog(vm, it) { selectedId = null } }
}

@Composable
private fun MessageCard(item: MessageWithRecipients, onClick: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().clickable(onClick = onClick),
        colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceContainerLow),
    ) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text(
                    item.recipients.map { it.phoneNumber }.sorted().joinToString(", "),
                    style = MaterialTheme.typography.titleSmall,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    modifier = Modifier.weight(1f),
                )
                Spacer(Modifier.width(8.dp))
                if (item.message.source == app.messagegateway.sms.data.MessageEntity.SOURCE_CLOUD) {
                    Pill("Cloud", MaterialTheme.colorScheme.surfaceVariant, MaterialTheme.colorScheme.onSurfaceVariant)
                    Spacer(Modifier.width(6.dp))
                }
                StateChip(item.message.state)
            }
            Text(item.message.text, maxLines = 2, overflow = TextOverflow.Ellipsis, style = MaterialTheme.typography.bodyMedium)
            Text(
                formatTime(item.message.createdAt),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun MessageDialog(vm: AppViewModel, item: MessageWithRecipients, onDismiss: () -> Unit) {
    var error by remember { mutableStateOf<String?>(null) }
    AlertDialog(
        onDismissRequest = onDismiss,
        title = { Text("Message") },
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
