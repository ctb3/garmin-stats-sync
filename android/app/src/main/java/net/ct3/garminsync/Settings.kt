package net.ct3.garminsync

import android.content.Context
import android.content.SharedPreferences
import androidx.security.crypto.EncryptedSharedPreferences
import androidx.security.crypto.MasterKey

/**
 * Server address, token, and the high-water mark.
 *
 * The token is a shared secret, so it lives in EncryptedSharedPreferences. The
 * rest could live anywhere, but keeping one store is simpler than two.
 */
class Settings(context: Context) {

    private val prefs: SharedPreferences = EncryptedSharedPreferences.create(
        context,
        "garmin-sync",
        MasterKey.Builder(context).setKeyScheme(MasterKey.KeyScheme.AES256_GCM).build(),
        EncryptedSharedPreferences.PrefKeyEncryptionScheme.AES256_SIV,
        EncryptedSharedPreferences.PrefValueEncryptionScheme.AES256_GCM,
    )

    var serverUrl: String
        get() = prefs.getString(KEY_URL, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_URL, value.trim().trimEnd('/')).apply()

    var token: String
        get() = prefs.getString(KEY_TOKEN, "").orEmpty()
        set(value) = prefs.edit().putString(KEY_TOKEN, value.trim()).apply()

    /**
     * The newest record instant the server has *confirmed*.
     *
     * Advanced only on a 2xx. That is the whole retry mechanism: if a POST
     * fails this does not move, so the next run re-reads the same records from
     * Health Connect. Health Connect is the durable queue - keeping a second
     * one in the app could only diverge from it.
     */
    var lastConfirmedMillis: Long
        get() = prefs.getLong(KEY_CONFIRMED, 0L)
        set(value) = prefs.edit().putLong(KEY_CONFIRMED, value).apply()

    var consecutiveFailures: Int
        get() = prefs.getInt(KEY_FAILURES, 0)
        set(value) = prefs.edit().putInt(KEY_FAILURES, value).apply()

    var lastResult: String
        get() = prefs.getString(KEY_RESULT, "Never run").orEmpty()
        set(value) = prefs.edit().putString(KEY_RESULT, value).apply()

    val isConfigured: Boolean
        get() = serverUrl.isNotEmpty() && token.isNotEmpty()

    private companion object {
        const val KEY_URL = "server_url"
        const val KEY_TOKEN = "token"
        const val KEY_CONFIRMED = "last_confirmed_millis"
        const val KEY_FAILURES = "consecutive_failures"
        const val KEY_RESULT = "last_result"
    }
}
