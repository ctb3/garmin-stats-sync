package net.ct3.garminsync

import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat

/**
 * Covers the failure direction the server's /health endpoint cannot: the server
 * or proxy being unreachable, which by definition no server-side check reports.
 */
object Notifications {

    private const val CHANNEL_ID = "sync-failures"
    private const val NOTIFICATION_ID = 1

    private fun ensureChannel(context: Context) {
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Sync problems",
            NotificationManager.IMPORTANCE_DEFAULT,
        ).apply { description = "Raised when weigh-ins cannot reach the server" }
        NotificationManagerCompat.from(context).createNotificationChannel(channel)
    }

    private fun allowed(context: Context): Boolean {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return true
        return ContextCompat.checkSelfPermission(
            context,
            android.Manifest.permission.POST_NOTIFICATIONS,
        ) == PackageManager.PERMISSION_GRANTED
    }

    fun syncFailing(context: Context, detail: String) {
        if (!allowed(context)) return
        ensureChannel(context)
        val notification = NotificationCompat.Builder(context, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_error)
            .setContentTitle("Weigh-ins are not reaching Garmin")
            .setContentText(detail.ifEmpty { "The sync server is unreachable." })
            .setStyle(NotificationCompat.BigTextStyle().bigText(detail))
            .setAutoCancel(true)
            .build()
        NotificationManagerCompat.from(context).notify(NOTIFICATION_ID, notification)
    }

    fun clear(context: Context) {
        NotificationManagerCompat.from(context).cancel(NOTIFICATION_ID)
    }
}
