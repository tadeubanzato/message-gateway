package app.messagegateway.sms.ui

import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.activity.compose.BackHandler

private enum class Tab(val label: String, val icon: ImageVector) {
    Home("Home", NavHomeIcon),
    Messages("Messages", NavMessagesIcon),
    Webhooks("Webhooks", NavWebhooksIcon),
    Settings("Settings", NavSettingsIcon),
}

@Composable
fun MainScreen(vm: AppViewModel) {
    var tab by rememberSaveable { mutableStateOf(Tab.Home) }
    var showLogs by rememberSaveable { mutableStateOf(false) }
    var showCloud by rememberSaveable { mutableStateOf(false) }
    BackHandler(enabled = showLogs || showCloud) { showLogs = false; showCloud = false }

    Scaffold(
        bottomBar = {
            NavigationBar {
                Tab.entries.forEach {
                    NavigationBarItem(
                        selected = tab == it && !showLogs && !showCloud,
                        onClick = { tab = it; showLogs = false; showCloud = false },
                        icon = { Icon(it.icon, contentDescription = null) },
                        label = { Text(it.label) },
                    )
                }
            }
        },
    ) { padding ->
        val modifier = Modifier.padding(padding)
        when {
            showLogs -> LogsScreen(vm, onBack = { showLogs = false }, modifier = modifier)
            showCloud -> CloudScreen(vm, onBack = { showCloud = false }, modifier = modifier)
            tab == Tab.Home -> HomeScreen(vm, onOpenCloud = { showCloud = true }, modifier = modifier)
            tab == Tab.Messages -> MessagesScreen(vm, modifier)
            tab == Tab.Webhooks -> WebhooksScreen(vm, modifier)
            else -> SettingsScreen(vm, onOpenLogs = { showLogs = true }, onOpenCloud = { showCloud = true }, modifier = modifier)
        }
    }
}
