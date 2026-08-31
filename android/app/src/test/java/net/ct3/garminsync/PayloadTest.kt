package net.ct3.garminsync

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File

/**
 * The wire contract with the server.
 *
 * These run on a plain JVM - no device, no emulator. The emitted payload is
 * also written to build/wire-contract.json so the Python side can be fed the
 * exact bytes this app produces, rather than a hand-written approximation.
 */
class PayloadTest {

    private fun sample() = listOf(
        WeighIn("hc-1", "com.etekcity.vesyncplatform", 1_756_150_200_000, 82.1),
        WeighIn("hc-2", "com.etekcity.vesyncplatform", 1_756_063_800_000, 81.9),
    )

    @Test
    fun `payload has the shape the server parses`() {
        val json = JSONObject(Payload.build(sample()))
        val records = json.getJSONArray("records")

        assertEquals(2, records.length())
        val first = records.getJSONObject(0)
        assertEquals(1_756_150_200_000L, first.getLong("time"))
        assertEquals(82.1, first.getJSONObject("weight").getDouble("kilograms"), 0.001)
        assertEquals("hc-1", first.getJSONObject("metadata").getString("id"))
        assertEquals(
            "com.etekcity.vesyncplatform",
            first.getJSONObject("metadata")
                .getJSONObject("dataOrigin").getString("packageName"),
        )
    }

    @Test
    fun `time is milliseconds, which the server divides down`() {
        val time = JSONObject(Payload.build(sample()))
            .getJSONArray("records").getJSONObject(0).getLong("time")
        // Epoch seconds would be ~1.7e9; the server relies on getting ms.
        assertTrue("expected milliseconds, got $time", time > 1_000_000_000_000L)
    }

    @Test
    fun `garmin's own records are excluded`() {
        val records = sample() + WeighIn(
            "garmin-1", Payload.GARMIN_PACKAGE, 1_756_150_300_000, 99.9
        )

        val kept = Payload.excludeGarminOrigin(records)

        assertEquals(2, kept.size)
        assertTrue(kept.none { it.packageName == Payload.GARMIN_PACKAGE })
    }

    @Test
    fun `empty input still produces a valid payload`() {
        val json = JSONObject(Payload.build(emptyList()))
        assertEquals(0, json.getJSONArray("records").length())
    }

    @Test
    fun `emit the payload for the server-side contract check`() {
        val out = File("build/wire-contract.json")
        out.parentFile.mkdirs()
        out.writeText(Payload.build(sample()))
        assertTrue(out.exists())
    }
}
