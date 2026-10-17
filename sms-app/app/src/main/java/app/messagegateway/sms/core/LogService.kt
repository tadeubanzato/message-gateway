package app.messagegateway.sms.core

import android.util.Log
import app.messagegateway.sms.data.LogDao
import app.messagegateway.sms.data.LogEntity
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import java.util.concurrent.atomic.AtomicInteger

/** Keeps the last couple of thousand log lines so problems can be diagnosed from the app or the API. */
class LogService(private val dao: LogDao, private val scope: CoroutineScope) : AppLog, LogReader {
    private val written = AtomicInteger()

    override fun log(level: String, tag: String, message: String) {
        Log.println(
            when (level) { "ERROR" -> Log.ERROR; "WARN" -> Log.WARN; else -> Log.INFO },
            tag, message,
        )
        scope.launch(Dispatchers.IO) {
            dao.insert(LogEntity(createdAt = System.currentTimeMillis(), level = level, tag = tag, message = message))
            if (written.incrementAndGet() % TRIM_EVERY == 0) dao.trim(KEEP)
        }
    }

    override suspend fun list(from: Long, to: Long, limit: Int): List<LogView> =
        dao.list(from, to, limit).map { LogView(it.id, it.level, it.tag, it.message, it.createdAt) }

    private companion object {
        const val KEEP = 2000
        const val TRIM_EVERY = 200
    }
}
