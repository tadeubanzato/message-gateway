package app.messagegateway.sms

import android.app.Application
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch

class App : Application() {
    lateinit var container: AppContainer
        private set

    override fun onCreate() {
        super.onCreate()
        container = AppContainer(this)
        container.scope.launch(Dispatchers.IO) {
            container.engine.recoverStale()
            cleanUpOldData()
        }
    }

    private suspend fun cleanUpOldData() {
        val before = System.currentTimeMillis() - container.prefs.retentionDays * DAY_MS
        container.db.messages().deleteOlderThan(before)
        container.db.incoming().deleteOlderThan(before)
        container.db.webhooks().deleteFinishedBefore(before)
    }

    private companion object {
        const val DAY_MS = 24 * 60 * 60 * 1000L
    }
}
