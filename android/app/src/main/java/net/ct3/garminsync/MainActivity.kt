package net.ct3.garminsync

import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.View
import android.view.ViewGroup.LayoutParams.MATCH_PARENT
import android.view.ViewGroup.LayoutParams.WRAP_CONTENT
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.ScrollView
import android.widget.TextView
import android.widget.Toast
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
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

    private fun buildLayout(): View {
        val pad = (16 * resources.displayMetrics.density).toInt()
        val gap = pad / 2

        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        root.addView(TextView(this).apply {
            text = getString(R.string.app_name)
            textSize = 22f
            setTypeface(typeface, android.graphics.Typeface.BOLD)
            setPadding(0, 0, 0, pad)
        })

        root.addView(label(R.string.server_label))
        urlField = EditText(this).apply {
            hint = "https://garmin-sync.example.net"
            setText(settings.serverUrl)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        root.addView(urlField)

        root.addView(label(R.string.token_label))
        tokenField = EditText(this).apply {
            hint = "X-Auth-Token"
            setText(settings.token)
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSingleLine()
        }
        root.addView(tokenField)

        root.addView(Button(this).apply {
            text = getString(R.string.save_and_grant)
            setOnClickListener { saveAndGrant() }
            (layoutParams as? LinearLayout.LayoutParams)?.topMargin = pad
        })
        root.addView(Button(this).apply {
            text = getString(R.string.sync_now)
            setOnClickListener { syncNow() }
        })

        status = TextView(this).apply { setPadding(0, pad, 0, 0) }
        root.addView(status)

        // Scrolls so the keyboard cannot bury the fields it is being used to fill.
        val scroll = ScrollView(this).apply {
            addView(
                root,
                LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT),
            )
        }

        // targetSdk 35+ draws edge to edge, so the window's top is behind the
        // status bar. Without this the first field sits under the clock and
        // cannot be tapped. The IME inset keeps the content above the keyboard.
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime()
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        return scroll
    }

    private fun label(resId: Int) = TextView(this).apply {
        text = getString(resId)
        setPadding(0, gapPx, 0, 0)
    }

    private val gapPx: Int
        get() = (8 * resources.displayMetrics.density).toInt()

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
