package app.messagegateway.sms

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.lifecycle.viewmodel.initializer
import androidx.lifecycle.viewmodel.viewModelFactory
import app.messagegateway.sms.ui.AppTheme
import app.messagegateway.sms.ui.AppViewModel
import app.messagegateway.sms.ui.MainScreen

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        val container = (application as App).container
        setContent {
            AppTheme {
                val vm = viewModel<AppViewModel>(factory = viewModelFactory { initializer { AppViewModel(container) } })
                MainScreen(vm)
            }
        }
    }
}
