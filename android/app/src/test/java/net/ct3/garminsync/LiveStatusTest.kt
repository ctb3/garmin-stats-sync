package net.ct3.garminsync

import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import java.io.File

/**
 * Parses a payload captured from a running server, so the app is checked
 * against what the service actually emits rather than a hand-written sample.
 * Skipped when the capture is absent (a normal CI build).
 */
class LiveStatusTest {

    @Test
    fun `parses a real server payload`() {
        val capture = File("build/live-status.json")
        assumeTrue("no live capture present", capture.exists())

        val status = StatusClient.parse(capture.readText())

        assertTrue("expected a login url", status.loginUrl.startsWith("http"))
        assertTrue("expected runs", status.runs.isNotEmpty())
        assertTrue("expected a pending weigh-in", status.pending.isNotEmpty())
        assertTrue(status.pending.all { it.weightKg > 0 })
        status.runs.forEach { require(it.trigger.isNotEmpty()) }
    }
}
