package net.ct3.garminsync

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.time.Instant
import java.time.ZoneId

/** Parsing the server's /status, and rendering times the way a person reads them. */
class StatusTest {

    private val sample = """
        {"ok": true, "token_state": "valid",
         "login_url": "https://garmin-sync.example.net/login",
         "timezone": "America/New_York",
         "last_success": "2026-08-31T12:03:31.873005+00:00",
         "consecutive_failures": 0,
         "pending": [{"taken_at": "2026-08-31T11:30:00+00:00",
                      "weight_kg": 82.1,
                      "received_at": "2026-08-31T11:31:00+00:00"}],
         "runs": [{"at": "2026-08-31T12:03:31.873005+00:00", "trigger": "interval",
                   "uploaded": 0, "skipped": 0, "failed": 0, "fetched": 0,
                   "error": null}]}
    """.trimIndent()

    @Test
    fun `parses the pipeline state`() {
        val status = StatusClient.parse(sample)

        assertTrue(status.ok)
        assertEquals("valid", status.tokenState)
        assertEquals("https://garmin-sync.example.net/login", status.loginUrl)
        assertEquals(0, status.consecutiveFailures)
        assertFalse(status.needsLogin)
    }

    @Test
    fun `parses pending weigh-ins`() {
        val pending = StatusClient.parse(sample).pending

        assertEquals(1, pending.size)
        assertEquals(82.1, pending[0].weightKg, 0.001)
    }

    @Test
    fun `a null error stays null rather than becoming the string null`() {
        val run = StatusClient.parse(sample).runs.single()
        assertNull(run.error)
    }

    @Test
    fun `an expired token means a login is needed`() {
        val status = StatusClient.parse("""{"ok": false, "token_state": "expired"}""")
        assertTrue(status.needsLogin)
    }

    @Test
    fun `missing fields do not throw`() {
        val status = StatusClient.parse("{}")

        assertTrue(status.runs.isEmpty())
        assertTrue(status.pending.isEmpty())
        assertNull(status.lastSuccess)
    }

    @Test
    fun `timestamps render in the phone's timezone`() {
        val utc = Instant.parse("2026-08-31T12:03:31Z")

        // America/New_York is UTC-4 in August.
        assertEquals(
            "2026-08-31 08:03:31",
            When.full(utc, ZoneId.of("America/New_York")),
        )
        assertEquals("2026-08-31 08:03", When.brief(utc, ZoneId.of("America/New_York")))
    }

    @Test
    fun `relative times read naturally`() {
        val now = Instant.parse("2026-08-31T12:00:00Z")

        assertEquals("just now", When.ago(Instant.parse("2026-08-31T11:59:30Z"), now))
        assertEquals("5 min ago", When.ago(Instant.parse("2026-08-31T11:55:00Z"), now))
        assertEquals("3 h ago", When.ago(Instant.parse("2026-08-31T09:00:00Z"), now))
        assertEquals("3 days ago", When.ago(Instant.parse("2026-08-28T12:00:00Z"), now))
    }
}
