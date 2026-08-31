package net.ct3.garminsync

import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.time.Instant
import java.time.ZoneId
import java.time.format.DateTimeFormatter

/** One line in the run log. */
data class Run(
    val at: Instant,
    val trigger: String,
    val uploaded: Int,
    val skipped: Int,
    val failed: Int,
    val error: String?,
)

/** One weigh-in the server has accepted but not yet delivered to Garmin. */
data class Pending(
    val takenAt: Instant,
    val weightKg: Double,
)

/** The whole pipeline in one look. */
data class Status(
    val ok: Boolean,
    val tokenState: String,
    val loginUrl: String,
    val lastSuccess: Instant?,
    val consecutiveFailures: Int,
    val pending: List<Pending>,
    val runs: List<Run>,
) {
    val needsLogin: Boolean get() = tokenState != "valid"
}

object StatusClient {

    /** Fetches /status. Throws on anything other than a clean 200. */
    fun fetch(settings: Settings): Status {
        val connection =
            URL("${settings.serverUrl}/status").openConnection() as HttpURLConnection
        connection.apply {
            requestMethod = "GET"
            connectTimeout = 10_000
            readTimeout = 10_000
            setRequestProperty("X-Auth-Token", settings.token)
        }
        val body = try {
            if (connection.responseCode !in 200..299) {
                throw IllegalStateException("server returned ${connection.responseCode}")
            }
            connection.inputStream.bufferedReader().readText()
        } finally {
            connection.disconnect()
        }
        return parse(body)
    }

    fun parse(body: String): Status {
        val json = JSONObject(body)
        val runs = json.optJSONArray("runs")?.let { array ->
            (0 until array.length()).map { i ->
                val r = array.getJSONObject(i)
                Run(
                    at = Instant.parse(r.getString("at")),
                    trigger = r.optString("trigger", ""),
                    uploaded = r.optInt("uploaded"),
                    skipped = r.optInt("skipped"),
                    failed = r.optInt("failed"),
                    error = r.optString("error").takeIf {
                        it.isNotEmpty() && it != "null"
                    },
                )
            }
        } ?: emptyList()

        val pending = json.optJSONArray("pending")?.let { array ->
            (0 until array.length()).map { i ->
                val p = array.getJSONObject(i)
                Pending(
                    takenAt = Instant.parse(p.getString("taken_at")),
                    weightKg = p.getDouble("weight_kg"),
                )
            }
        } ?: emptyList()

        return Status(
            ok = json.optBoolean("ok"),
            tokenState = json.optString("token_state", "unknown"),
            loginUrl = json.optString("login_url", ""),
            lastSuccess = json.optString("last_success")
                .takeIf { it.isNotEmpty() && it != "null" }
                ?.let { Instant.parse(it) },
            consecutiveFailures = json.optInt("consecutive_failures"),
            pending = pending,
            runs = runs,
        )
    }
}

/**
 * Timestamps are rendered in the phone's own timezone - the only one that means
 * anything to the person holding it. The server sends UTC ISO throughout.
 */
object When {
    private val stamp: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm:ss")
    private val short: DateTimeFormatter =
        DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")

    fun full(instant: Instant, zone: ZoneId = ZoneId.systemDefault()): String =
        stamp.format(instant.atZone(zone))

    fun brief(instant: Instant, zone: ZoneId = ZoneId.systemDefault()): String =
        short.format(instant.atZone(zone))

    /** "3 minutes ago" style, for the one timestamp that is read at a glance. */
    fun ago(instant: Instant, now: Instant = Instant.now()): String {
        val seconds = java.time.Duration.between(instant, now).seconds
        return when {
            seconds < 0 -> "just now"
            seconds < 60 -> "just now"
            seconds < 3600 -> "${seconds / 60} min ago"
            seconds < 172_800 -> "${seconds / 3600} h ago"
            else -> "${seconds / 86_400} days ago"
        }
    }
}
