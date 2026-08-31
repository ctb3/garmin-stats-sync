package net.ct3.garminsync

import org.json.JSONArray
import org.json.JSONObject

/**
 * One weigh-in, reduced to the fields that cross the wire.
 *
 * Deliberately not a Health Connect type: keeping the wire format free of
 * Android classes is what lets the contract with the server be tested on a
 * plain JVM, with no device and no emulator.
 */
data class WeighIn(
    val id: String,
    val packageName: String,
    val epochMillis: Long,
    val kilograms: Double,
)

/**
 * Builds the request body the server's /weigh-ins endpoint parses.
 *
 * The shape here must match `health_connect.parse_record` on the server. That
 * pairing is covered by a test on each side, both fed from the same fixture.
 */
object Payload {

    const val GARMIN_PACKAGE = "com.garmin.android.apps.connectmobile"

    /**
     * Garmin Connect writes weight *into* Health Connect, one way. Without this
     * the app would read Garmin's own records back and upload them to Garmin.
     */
    fun excludeGarminOrigin(records: List<WeighIn>): List<WeighIn> =
        records.filter { it.packageName != GARMIN_PACKAGE }

    fun build(records: List<WeighIn>): String {
        val array = JSONArray()
        records.forEach { record ->
            array.put(
                JSONObject()
                    .put(
                        "metadata",
                        JSONObject()
                            .put("id", record.id)
                            .put(
                                "dataOrigin",
                                JSONObject().put("packageName", record.packageName),
                            ),
                    )
                    .put("time", record.epochMillis)
                    .put("weight", JSONObject().put("kilograms", record.kilograms))
            )
        }
        return JSONObject().put("records", array).toString()
    }
}
