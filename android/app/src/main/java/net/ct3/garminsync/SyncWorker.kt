package net.ct3.garminsync

import android.content.Context
import android.util.Log
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.permission.HealthPermission
import androidx.health.connect.client.records.WeightRecord
import androidx.health.connect.client.request.ReadRecordsRequest
import androidx.health.connect.client.time.TimeRangeFilter
import androidx.work.BackoffPolicy
import androidx.work.Constraints
import androidx.work.CoroutineWorker
import androidx.work.ExistingPeriodicWorkPolicy
import androidx.work.NetworkType
import androidx.work.PeriodicWorkRequestBuilder
import androidx.work.WorkManager
import androidx.work.WorkerParameters
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.net.HttpURLConnection
import java.net.URL
import java.time.Duration
import java.time.Instant

/**
 * Reads new weigh-ins from Health Connect and posts them to the server.
 *
 * Power shape: the expensive thing would be waking the radio. Almost every run
 * has nothing new, and those runs return before touching the network - so a
 * 30-minute period costs a process wake and one Health Connect IPC read, not a
 * connection. Android 15+ background reads mean no foreground service is needed
 * at all, which is the real saving.
 */
class SyncWorker(context: Context, params: WorkerParameters) :
    CoroutineWorker(context, params) {

    override suspend fun doWork(): Result = withContext(Dispatchers.IO) {
        val settings = Settings(applicationContext)
        if (!settings.isConfigured) {
            return@withContext Result.success()
        }

        if (HealthConnectClient.getSdkStatus(applicationContext) !=
            HealthConnectClient.SDK_AVAILABLE
        ) {
            settings.lastResult = "Health Connect unavailable"
            return@withContext Result.success()
        }

        try {
            val client = HealthConnectClient.getOrCreate(applicationContext)

            // One read covers both jobs: deciding whether anything is new, and
            // supplying the batch to send.
            val window = toWeighIns(readWindow(client))
            val newest = window.maxOfOrNull { it.epochMillis } ?: 0L
            val forced = inputData.getBoolean(FORCE, false)
            if (!forced && newest <= settings.lastConfirmedMillis) {
                // The short-circuit that keeps polling cheap: no radio, no
                // connection, nothing to report. This is the path ~47 of 48
                // daily runs take - but never the path a Sync now takes, or
                // the button would appear to do nothing.
                settings.lastResult = "Nothing new at ${When.full(Instant.now())}"
                return@withContext Result.success()
            }
            if (window.isEmpty()) {
                settings.lastResult =
                    "No weigh-ins in Health Connect in the last 7 days"
                return@withContext Result.success()
            }

            // Send the whole window rather than only what is new. The server
            // dedupes by timestamp, so over-sending costs nothing and repairs
            // the pipeline if either side ever loses its place.
            val tokenState = post(settings, window)

            // The server accepted and durably spooled the reading, so the phone
            // is done with it either way - but if Garmin cannot be reached the
            // person needs to know it is sitting there.
            if (tokenState != null && tokenState != "valid") {
                Notifications.garminLoginNeeded(applicationContext)
            }

            settings.lastConfirmedMillis = newest
            settings.consecutiveFailures = 0
            settings.lastResult =
                "Sent ${window.size} weigh-in(s) at ${When.full(Instant.now())}"
            Notifications.clear(applicationContext)
            Result.success()
        } catch (e: Exception) {
            val failures = settings.consecutiveFailures + 1
            settings.consecutiveFailures = failures
            settings.lastResult =
                "Failed at ${When.full(Instant.now())}: ${e.message}"
            Log.w(TAG, "sync failed (attempt $failures)", e)
            if (failures >= FAILURES_BEFORE_NOTIFYING) {
                Notifications.syncFailing(applicationContext, e.message.orEmpty())
            }
            // WorkManager applies exponential backoff; the high-water mark has
            // not moved, so nothing is lost.
            Result.retry()
        }
    }

    private suspend fun readWindow(client: HealthConnectClient): List<WeightRecord> =
        read(client, TimeRangeFilter.after(Instant.now().minus(WINDOW)))

    private suspend fun read(
        client: HealthConnectClient,
        filter: TimeRangeFilter,
    ): List<WeightRecord> {
        val response = client.readRecords(
            ReadRecordsRequest(recordType = WeightRecord::class, timeRangeFilter = filter)
        )
        return response.records
    }

    /** Health Connect types stop here; everything downstream is plain data. */
    private fun toWeighIns(records: List<WeightRecord>): List<WeighIn> =
        Payload.excludeGarminOrigin(
            records.map {
                WeighIn(
                    id = it.metadata.id,
                    packageName = it.metadata.dataOrigin.packageName,
                    epochMillis = it.time.toEpochMilli(),
                    kilograms = it.weight.inKilograms,
                )
            }
        )

    /** Posts the batch. Returns the server's Garmin token state, if it sent one. */
    private fun post(settings: Settings, records: List<WeighIn>): String? {
        val body = Payload.build(records)

        val connection =
            URL("${settings.serverUrl}$INGEST_PATH").openConnection() as HttpURLConnection
        connection.apply {
            requestMethod = "POST"
            doOutput = true
            connectTimeout = 15_000
            readTimeout = 15_000
            setRequestProperty("Content-Type", "application/json")
            setRequestProperty("X-Auth-Token", settings.token)
        }
        try {
            connection.outputStream.use { it.write(body.toByteArray()) }
            val code = connection.responseCode
            if (code !in 200..299) {
                val detail = connection.errorStream?.bufferedReader()?.readText().orEmpty()
                throw IllegalStateException("server returned $code $detail".trim())
            }
            val reply = connection.inputStream.bufferedReader().readText()
            return runCatching {
                org.json.JSONObject(reply).optString("token_state").ifEmpty { null }
            }.getOrNull()
        } finally {
            connection.disconnect()
        }
    }

    companion object {
        private const val TAG = "SyncWorker"
        private const val WORK_NAME = "garmin-sync-periodic"
        private const val INGEST_PATH = "/weigh-ins"
        private const val FAILURES_BEFORE_NOTIFYING = 3

        /** Set on a manual sync, to bypass the "nothing new" short-circuit. */
        const val FORCE = "force"

        private val WINDOW: Duration = Duration.ofDays(7)
        private val PERIOD: Duration = Duration.ofMinutes(30)
        // Lets JobScheduler place the run inside a window it is already waking
        // for, rather than forcing a wake of its own.
        private val FLEX: Duration = Duration.ofMinutes(10)

        val PERMISSIONS = setOf(
            HealthPermission.getReadPermission(WeightRecord::class),
            HealthPermission.PERMISSION_READ_HEALTH_DATA_IN_BACKGROUND,
        )

        /** A one-off run that sends even when the phone thinks nothing changed. */
        fun runNow(context: Context) {
            WorkManager.getInstance(context).enqueue(
                androidx.work.OneTimeWorkRequestBuilder<SyncWorker>()
                    .setInputData(androidx.work.workDataOf(FORCE to true))
                    .build()
            )
        }

        fun schedule(context: Context) {
            val constraints = Constraints.Builder()
                // Unmetered also enforces the home-WiFi-only assumption.
                .setRequiredNetworkType(NetworkType.UNMETERED)
                .setRequiresBatteryNotLow(true)
                .build()

            val request = PeriodicWorkRequestBuilder<SyncWorker>(PERIOD, FLEX)
                .setConstraints(constraints)
                .setBackoffCriteria(BackoffPolicy.EXPONENTIAL, Duration.ofMinutes(5))
                .build()

            WorkManager.getInstance(context).enqueueUniquePeriodicWork(
                WORK_NAME,
                ExistingPeriodicWorkPolicy.UPDATE,
                request,
            )
        }
    }
}
