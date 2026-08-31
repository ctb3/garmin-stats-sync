package net.ct3.garminsync

import android.content.Intent
import android.graphics.Color
import android.graphics.Typeface
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.Gravity
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
import androidx.appcompat.app.AlertDialog
import androidx.appcompat.app.AppCompatActivity
import androidx.core.view.ViewCompat
import androidx.core.view.WindowInsetsCompat
import androidx.health.connect.client.HealthConnectClient
import androidx.health.connect.client.PermissionController
import androidx.lifecycle.lifecycleScope
import androidx.work.OneTimeWorkRequestBuilder
import androidx.work.WorkManager
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * A dashboard for the whole pipeline: phone, server, and Garmin.
 *
 * Settings live behind a dialog rather than on the surface. They are set once
 * and then never touched, so leaving them as live text boxes only creates a way
 * to break a working install by mistyping into one.
 */
class MainActivity : AppCompatActivity() {

    private lateinit var settings: Settings
    private lateinit var headline: TextView
    private lateinit var detail: TextView
    private lateinit var loginButton: Button
    private lateinit var logs: TextView
    private var status: Status? = null

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

    private val pad by lazy { (16 * resources.displayMetrics.density).toInt() }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        settings = Settings(this)
        setContentView(buildLayout())
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            requestNotifications.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }
        if (!settings.isConfigured) showSettings()
    }

    override fun onResume() {
        super.onResume()
        refresh()
    }

    // --- layout -------------------------------------------------------------

    private fun buildLayout(): View {
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, pad)
        }

        val titleRow = LinearLayout(this).apply {
            orientation = LinearLayout.HORIZONTAL
            gravity = Gravity.CENTER_VERTICAL
        }
        titleRow.addView(
            TextView(this).apply {
                text = getString(R.string.app_name)
                textSize = 22f
                setTypeface(typeface, Typeface.BOLD)
            },
            LinearLayout.LayoutParams(0, WRAP_CONTENT, 1f),
        )
        titleRow.addView(Button(this).apply {
            text = getString(R.string.settings)
            setOnClickListener { showSettings() }
        })
        root.addView(titleRow)

        headline = TextView(this).apply {
            textSize = 18f
            setPadding(0, pad, 0, 0)
        }
        root.addView(headline)

        detail = TextView(this).apply { setPadding(0, pad / 2, 0, 0) }
        root.addView(detail)

        loginButton = Button(this).apply {
            text = getString(R.string.log_in_to_garmin)
            visibility = View.GONE
            setOnClickListener { openLogin() }
        }
        root.addView(loginButton)

        root.addView(Button(this).apply {
            text = getString(R.string.sync_now)
            setOnClickListener { syncNow() }
        })
        root.addView(Button(this).apply {
            text = getString(R.string.refresh)
            setOnClickListener { refresh() }
        })

        root.addView(TextView(this).apply {
            text = getString(R.string.recent_activity)
            textSize = 18f
            setTypeface(typeface, Typeface.BOLD)
            setPadding(0, pad + pad / 2, 0, pad / 2)
        })
        logs = TextView(this).apply {
            typeface = Typeface.MONOSPACE
            textSize = 12f
        }
        root.addView(logs)

        val scroll = ScrollView(this).apply {
            addView(root, LinearLayout.LayoutParams(MATCH_PARENT, WRAP_CONTENT))
        }
        // targetSdk 35+ draws edge to edge, so without this the first row sits
        // behind the status bar. The IME inset keeps content above the keyboard.
        ViewCompat.setOnApplyWindowInsetsListener(scroll) { view, insets ->
            val bars = insets.getInsets(
                WindowInsetsCompat.Type.systemBars() or WindowInsetsCompat.Type.ime()
            )
            view.setPadding(bars.left, bars.top, bars.right, bars.bottom)
            insets
        }
        return scroll
    }

    // --- settings dialog ----------------------------------------------------

    private fun showSettings() {
        val box = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(pad, pad, pad, 0)
        }
        val urlField = EditText(this).apply {
            hint = "https://garmin-sync.example.net"
            setText(settings.serverUrl)
            inputType = InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI
            setSingleLine()
        }
        val tokenField = EditText(this).apply {
            hint = "X-Auth-Token"
            setText(settings.token)
            inputType = InputType.TYPE_CLASS_TEXT or
                InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            setSingleLine()
        }
        box.addView(TextView(this).apply { text = getString(R.string.server_label) })
        box.addView(urlField)
        box.addView(TextView(this).apply {
            text = getString(R.string.token_label)
            setPadding(0, pad / 2, 0, 0)
        })
        box.addView(tokenField)

        AlertDialog.Builder(this)
            .setTitle(R.string.settings)
            .setView(box)
            .setPositiveButton(R.string.save) { _, _ ->
                settings.serverUrl = urlField.text.toString()
                settings.token = tokenField.text.toString()
                if (settings.isConfigured) {
                    grantAndSchedule()
                } else {
                    toast("Enter both the server address and the token")
                }
            }
            .setNegativeButton(R.string.cancel, null)
            .show()
    }

    private fun grantAndSchedule() {
        if (HealthConnectClient.getSdkStatus(this) != HealthConnectClient.SDK_AVAILABLE) {
            toast("Health Connect is not available on this device")
            return
        }
        lifecycleScope.launch {
            val client = HealthConnectClient.getOrCreate(this@MainActivity)
            if (client.permissionController.getGrantedPermissions()
                    .containsAll(SyncWorker.PERMISSIONS)
            ) {
                SyncWorker.schedule(this@MainActivity)
                toast("Saved, syncing scheduled")
                refresh()
            } else {
                requestPermissions.launch(SyncWorker.PERMISSIONS)
            }
        }
    }

    // --- actions ------------------------------------------------------------

    private fun openLogin() {
        val url = status?.loginUrl?.takeIf { it.startsWith("http") }
            ?: "${settings.serverUrl}/login"
        runCatching { startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url))) }
            .onFailure { toast("No browser available for $url") }
    }

    private fun syncNow() {
        if (!settings.isConfigured) {
            showSettings()
            return
        }
        WorkManager.getInstance(this)
            .enqueue(OneTimeWorkRequestBuilder<SyncWorker>().build())
        toast("Sync requested")
        headline.postDelayed({ refresh() }, 3000)
    }

    // --- rendering ----------------------------------------------------------

    private fun refresh() {
        if (!settings.isConfigured) {
            headline.text = getString(R.string.not_configured)
            headline.setTextColor(Color.parseColor(AMBER))
            detail.text = ""
            logs.text = ""
            loginButton.visibility = View.GONE
            return
        }
        headline.text = getString(R.string.checking)
        lifecycleScope.launch {
            val fetched = withContext(Dispatchers.IO) {
                runCatching { StatusClient.fetch(settings) }
            }
            fetched
                .onSuccess { status = it; render(it) }
                .onFailure { render(null, it.message.orEmpty()) }
        }
    }

    private fun render(state: Status?, error: String = "") {
        if (state == null) {
            headline.text = getString(R.string.server_unreachable)
            headline.setTextColor(Color.parseColor(RED))
            detail.text = buildString {
                appendLine(error.ifEmpty { "No response from the server." })
                appendLine()
                append("Last sync attempt from this phone: ${settings.lastResult}")
            }
            loginButton.visibility = View.GONE
            logs.text = ""
            return
        }

        val (text, colour) = when {
            state.needsLogin -> "Garmin login needed" to RED
            state.pending.isNotEmpty() ->
                "${state.pending.size} weigh-in(s) waiting" to AMBER
            state.ok -> "Everything is up to date" to GREEN
            else -> "Attention needed" to AMBER
        }
        headline.text = text
        headline.setTextColor(Color.parseColor(colour))

        detail.text = buildString {
            append("Garmin token: ${state.tokenState}")
            state.lastSuccess?.let {
                appendLine()
                append("Last success: ${When.full(it)} (${When.ago(it)})")
            }
            if (state.consecutiveFailures > 0) {
                appendLine()
                append("Consecutive failures: ${state.consecutiveFailures}")
            }
            if (state.pending.isNotEmpty()) {
                appendLine()
                appendLine()
                append("Waiting to upload:")
                state.pending.forEach {
                    appendLine()
                    append("  ${When.brief(it.takenAt)}   ${"%.1f".format(it.weightKg)} kg")
                }
            }
        }

        loginButton.visibility = if (state.needsLogin) View.VISIBLE else View.GONE

        logs.text = if (state.runs.isEmpty()) {
            getString(R.string.no_runs_yet)
        } else {
            state.runs.joinToString("\n") { run ->
                val counts = "up ${run.uploaded}  skip ${run.skipped}  fail ${run.failed}"
                val line = "${When.brief(run.at)}  ${run.trigger.padEnd(8)} $counts"
                if (run.error != null) "$line\n    ${run.error.take(90)}" else line
            }
        }
    }

    private fun toast(message: String) {
        Toast.makeText(this, message, Toast.LENGTH_SHORT).show()
    }

    private companion object {
        const val RED = "#c62828"
        const val AMBER = "#e65100"
        const val GREEN = "#2e7d32"
    }
}
