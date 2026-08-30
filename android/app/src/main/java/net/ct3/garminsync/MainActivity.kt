package net.ct3.garminsync

import android.os.Build
import android.os.Bundle
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.launch
import java.time.Instant

/**
 * A configuration and status screen, not a product. Two fields, two buttons,
 * and enough state to tell whether the thing is working.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var settings: Settings
    private lateinit var urlField: EditText
    private lateinit var tokenField: EditText
    private lateinit var status: TextView

    private val requestPermissions =
        registerForActivityResult(
            PermissionController.createRequestPermissionResultContract()
        ) { granted ->
            if (granted.containsAll(SyncWorker.PERMISSIONS)) {
                SyncWorker.schedule(this)
                toast("Permissions granted, syncing scheduled")
            } else {
                toast("Weight permission is required")
            }
            refresh()
        }

    private val requestNotifications =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        setContentView(buildLayout())
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestNotifications.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        refresh()
    }

    private fun buildLayout(): ViewGroup {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        root.addView(TextView(this).apply {
            text = getString(R.string.server_label)
        })
        urlField = EditText(this).apply {
            hint = "https://garmin-sync.example.net"
            setText(settings.serverUrl)
        }
        root.addView(urlField)

        root.addView(TextView(this).apply { text = getString(R.string.token_label) })
        tokenField = EditText(this).apply {
            hint = "X-Auth-Token"
            setText(settings.token)
        }
        root.addView(tokenField)

        root.addView(Button(this).apply {
            text = getString(R.string.save_and_grant)
            setOnClickListener { saveAndGrant() }
        })
        root.addView(Button(this).apply {
            text = getString(R.string.sync_now)
            setOnClickListener { syncNow() }
        })

        status = TextView(this).apply { setPadding(0, pad, 0, 0) }
        root.addView(status)
        return root
    }

    private fun saveAndGrant() {
        settings.serverUrl = urlField.text.toString()
        settings.token = tokenField.text.toString()
        if (!settings.isConfigured) {
            toast("Enter both the server address and the token")
            return
        }
        if (HealthConnectClient.getSdkStatus(this) != HealthConnectClient.SDK_AVAILABLE) {
            toast("Health Connect is not available on this device")
            return
        }
        lifecycleScope.launch {
            val client = HealthConnectClient.getOrCreate(this@MainActivity)
            val granted = client.permissionController.getGrantedPermissions()
            if (granted.containsAll(SyncWorker.PERMISSIONS)) {
                SyncWorker.schedule(this@MainActivity)
                toast("Saved, syncing scheduled")
                refresh()
            } else {
                requestPermissions.launch(SyncWorker.PERMISSIONS)
            }
        }
    }

    private fun syncNow() {
        if (!settings.isConfigured) {
            toast("Save the server address and token first")
            return
        }
        val request = OneTimeWorkRequestBuilder<SyncWorker>().build()
        WorkManager.getInstance(this).enqueue(request)
        toast("Sync requested")
        status.postDelayed({ refresh() }, 3000)
    }

    private fun refresh() {
        val confirmed = settings.lastConfirmedMillis
        status.text = buildString {
            appendLine("Last result: ${settings.lastResult}")
            appendLine(
                "Confirmed through: " +
                    if (confirmed > 0) Instant.ofEpochMilli(confirmed).toString()
                    else "nothing yet"
            )
            append("Consecutive failures: ${settings.consecutiveFailures}")
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }
}
