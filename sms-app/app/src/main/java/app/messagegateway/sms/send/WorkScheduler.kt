package app.messagegateway.sms.send

import android.content.Context
import androidx.work.CoroutineWorker
import androidx.work.ExistingWorkPolicy
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import androidx.work.workDataOf
import app.messagegateway.sms.App
import java.util.concurrent.TimeUnit

/**
 * Books a wake-up with WorkManager. Only used for work that has to wait (a scheduled message, the rate
 * limit, a webhook retry): anything due now is handled directly in the running process.
 */
class WorkScheduler(private val context: Context, private val name: String) : DrainScheduler {
    private val sp = context.getSharedPreferences("scheduler", Context.MODE_PRIVATE)

    override fun scheduleAt(timeMillis: Long) {
        val now = System.currentTimeMillis()
        val booked = sp.getLong(name, 0)
        // An earlier wake-up that is still ahead already covers this one: it reschedules the rest itself.
        if (booked > now && booked <= timeMillis) return
        sp.edit().putLong(name, timeMillis).apply()
        val request = OneTimeWorkRequestBuilder<DrainWorker>()
            .setInitialDelay((timeMillis - now).coerceAtLeast(0), TimeUnit.MILLISECONDS)
            .setInputData(workDataOf(KEY_NAME to name))
            .build()
        WorkManager.getInstance(context).enqueueUniqueWork(name, ExistingWorkPolicy.REPLACE, request)
    }

    fun clear() = sp.edit().remove(name).apply()

    companion object {
        const val KEY_NAME = "name"
        const val MESSAGES = "drain-messages"
        const val WEBHOOKS = "drain-webhooks"
    }
}

class DrainWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as App).container
        val name = inputData.getString(WorkScheduler.KEY_NAME)
        return try {
            when (name) {
                WorkScheduler.WEBHOOKS -> {
                    container.webhookScheduler.clear()
                    container.webhooks.drain()
                }
                else -> {
                    container.messageScheduler.clear()
                    container.engine.recoverStale()
                    container.engine.drain()
                }
            }
            Result.success()
        } catch (e: Exception) {
            container.log.log("ERROR", "DrainWorker", "Failed: ${e.message ?: e}")
            Result.retry()
        }
    }
}

/** The safety net for cloud mode: if the event stream is down, this still picks up waiting messages. */
class CloudSyncWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val container = (applicationContext as App).container
        return try {
            container.cloud.syncOnce()
            Result.success()
        } catch (e: Exception) {
            container.log.log("WARN", "CloudSyncWorker", "Sync failed: ${e.message ?: e}")
            Result.retry()
        }
    }

    companion object {
        private const val NAME = "cloud-sync"

        fun schedule(context: Context, enabled: Boolean) {
            val manager = WorkManager.getInstance(context)
            if (!enabled) {
                manager.cancelUniqueWork(NAME)
                return
            }
            val request = androidx.work.PeriodicWorkRequestBuilder<CloudSyncWorker>(15, TimeUnit.MINUTES)
                .setConstraints(
                    androidx.work.Constraints.Builder().setRequiredNetworkType(androidx.work.NetworkType.CONNECTED).build()
                )
                .build()
            manager.enqueueUniquePeriodicWork(NAME, androidx.work.ExistingPeriodicWorkPolicy.KEEP, request)
        }
    }
}
