package com.jamilcpu.personalautomations.data

import io.ktor.client.HttpClient
import io.ktor.client.engine.mock.MockEngine
import io.ktor.client.engine.mock.MockRequestHandleScope
import io.ktor.client.engine.mock.respond
import io.ktor.client.engine.mock.toByteArray
import io.ktor.client.request.HttpRequestData
import io.ktor.client.request.HttpResponseData
import io.ktor.http.HttpHeaders
import io.ktor.http.HttpMethod
import io.ktor.http.HttpStatusCode
import io.ktor.http.headersOf
import java.io.IOException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class HubApiTest {
    private val settings = HubSettings("https://192.0.2.99:7700", "secret-token", "ab".repeat(32))
    private val json = headersOf(HttpHeaders.ContentType, "application/json")

    private fun api(handler: suspend MockRequestHandleScope.(HttpRequestData) -> HttpResponseData) =
        HubApi(buildHubClient(MockEngine(handler)), settings)

    private val devicesBody = """{"success":true,"data":[{"id":"pi1","name":"Pi 1","role":"server",
      "ip":"192.0.2.101","online":true,"health":{"cpu_pct":12.5,"mem_pct":40.0,"disk_pct":55.0,
      "temp_c":48.0,"uptime_s":3600},"programs_running":3,"programs_total":5,
      "capabilities":["usb"],"usb":{"on":true,"detail":"ok"},"future_field":1}],"error":null}"""

    @Test
    fun devicesAreDecoded() = runTest {
        val r = api { respond(devicesBody, HttpStatusCode.OK, json) }.devices()
        val d = (r as HubResult.Ok).value.single()
        assertEquals("pi1", d.id)
        assertEquals(3, d.programsRunning)
        assertEquals(12.5, d.health!!.cpuPct, 0.001)
        assertEquals(3600, d.health!!.uptimeS)
        assertTrue(d.usb!!.on)
    }

    @Test
    fun envelopeErrorBecomesErr() = runTest {
        val r = api {
            respond("""{"success":false,"data":null,"error":"Robot Pi is offline"}""", HttpStatusCode.ServiceUnavailable, json)
        }.devices()
        assertEquals(HubResult.Err("Robot Pi is offline", 503), r)
    }

    @Test
    fun bearerHeaderAndUrlAreSent() = runTest {
        var seen: HttpRequestData? = null
        api { req -> seen = req; respond(devicesBody, HttpStatusCode.OK, json) }.devices()
        assertEquals("Bearer secret-token", seen!!.headers[HttpHeaders.Authorization])
        assertEquals("https://192.0.2.99:7700/v1/devices", seen!!.url.toString())
    }

    @Test
    fun connectionFailureIsCantReach() = runTest {
        val r = api { throw IOException("boom") }.devices()
        assertEquals(HubResult.Err("Can't reach the hub", 0), r)
    }

    @Test
    fun actPostsJsonBody() = runTest {
        var seen: HttpRequestData? = null
        var body = ""
        val r = api { req ->
            seen = req
            body = String(req.body.toByteArray())
            respond("""{"success":true,"data":{"ok":true,"message":"done"},"error":null}""", HttpStatusCode.OK, json)
        }.act("pi1", "docker:web", "restart")
        assertEquals(HttpMethod.Post, seen!!.method)
        assertTrue(body.contains("\"action\":\"restart\"") && body.contains("\"device\":\"pi1\""))
        assertEquals(ActionResult(true, "done"), (r as HubResult.Ok).value)
    }

    @Test
    fun programsQueryAndPathEncoding() = runTest {
        val urls = mutableListOf<String>()
        val h = api { req ->
            urls += req.url.toString()
            respond("""{"success":true,"data":[],"error":null}""", HttpStatusCode.OK, json)
        }
        h.programs("pi1", true)
        h.programs(null, false)
        assertEquals("https://192.0.2.99:7700/v1/programs?device=pi1&include_system=true", urls[0])
        assertEquals("https://192.0.2.99:7700/v1/programs?include_system=false", urls[1])
    }

    @Test
    fun usbAndRebootRoutes() = runTest {
        val seen = mutableListOf<Pair<HttpMethod, String>>()
        val h = api { req ->
            seen += req.method to req.url.encodedPath
            respond("""{"success":true,"data":{"ok":true,"message":"m","on":true,"detail":"d"},"error":null}""", HttpStatusCode.OK, json)
        }
        h.usb("pi1"); h.setUsb("pi1", false); h.reboot("pi1"); h.restartGroup("pi1", "my proj")
        assertEquals(HttpMethod.Get to "/v1/devices/pi1/usb", seen[0])
        assertEquals(HttpMethod.Put to "/v1/devices/pi1/usb", seen[1])
        assertEquals(HttpMethod.Post to "/v1/devices/pi1/reboot", seen[2])
        assertEquals(HttpMethod.Post to "/v1/devices/pi1/groups/my%20proj/restart", seen[3])
    }
}
