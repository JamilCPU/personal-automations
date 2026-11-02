package com.jamilcpu.personalautomations.data

import io.ktor.client.HttpClient
import io.ktor.client.engine.HttpClientEngine
import io.ktor.client.engine.okhttp.OkHttp
import io.ktor.client.plugins.HttpTimeout
import io.ktor.client.plugins.timeout
import io.ktor.client.request.HttpRequestBuilder
import io.ktor.client.request.bearerAuth
import io.ktor.client.request.get
import io.ktor.client.request.parameter
import io.ktor.client.request.post
import io.ktor.client.request.put
import io.ktor.client.request.setBody
import io.ktor.client.statement.HttpResponse
import io.ktor.client.statement.bodyAsText
import io.ktor.http.ContentType
import io.ktor.http.contentType
import io.ktor.http.isSuccess
import java.net.URLEncoder
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.serializer

private const val READ_TIMEOUT_MS = 8_000L
private const val ACTION_TIMEOUT_MS = 35_000L
private const val UNREACHABLE = "Can't reach the hub"

internal val hubJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
}

/** A Ktor client over [engine]; JSON is handled by HubApi itself, so no content negotiation. */
fun buildHubClient(engine: HttpClientEngine): HttpClient = HttpClient(engine) {
    install(HttpTimeout)
    expectSuccess = false
}

/** The real client: OkHttp engine that trusts only the paired hub certificate. */
fun buildPinnedHubClient(settings: HubSettings): HttpClient =
    HttpClient(OkHttp) {
        install(HttpTimeout)
        expectSuccess = false
        engine {
            preconfigured = pinnedOkHttp(settings.fingerprint)
        }
    }

class HubApi(private val client: HttpClient, settings: HubSettings) {
    private val base = settings.url.trimEnd('/') + "/v1"
    private val token = settings.token

    suspend fun devices(): HubResult<List<DeviceSummary>> = read("/devices")

    suspend fun device(id: String): HubResult<DeviceSummary> = read("/devices/${seg(id)}")

    suspend fun programs(device: String?, includeSystem: Boolean): HubResult<List<Program>> =
        read("/programs") {
            if (device != null) parameter("device", device)
            parameter("include_system", includeSystem)
        }

    suspend fun program(device: String, id: String): HubResult<ProgramDetail> =
        read("/program") {
            parameter("device", device)
            parameter("id", id)
        }

    suspend fun logs(device: String, id: String, lines: Int): HubResult<List<String>> =
        read("/logs") {
            parameter("device", device)
            parameter("id", id)
            parameter("lines", lines)
        }

    suspend fun act(device: String, id: String, action: String): HubResult<ActionResult> =
        mutate(
            post = true,
            path = "/actions",
            body = hubJson.encodeToString(ActionRequest.serializer(), ActionRequest(device, id, action)),
        )

    suspend fun restartGroup(device: String, project: String): HubResult<ActionResult> =
        mutate(post = true, path = "/devices/${seg(device)}/groups/${seg(project)}/restart")

    suspend fun usb(device: String): HubResult<UsbState> = read("/devices/${seg(device)}/usb")

    suspend fun setUsb(device: String, on: Boolean): HubResult<ActionResult> =
        mutate(
            post = false,
            path = "/devices/${seg(device)}/usb",
            body = hubJson.encodeToString(UsbRequest.serializer(), UsbRequest(on)),
        )

    suspend fun reboot(device: String): HubResult<ActionResult> =
        mutate(post = true, path = "/devices/${seg(device)}/reboot")

    suspend fun history(limit: Int): HubResult<List<HistoryEntry>> =
        read("/history") { parameter("limit", limit) }

    private suspend inline fun <reified T> read(
        path: String,
        crossinline extra: HttpRequestBuilder.() -> Unit = {},
    ): HubResult<T> = call(serializer<T>()) {
        client.get(base + path) {
            bearerAuth(token)
            timeout { requestTimeoutMillis = READ_TIMEOUT_MS }
            extra()
        }
    }

    private suspend inline fun <reified T> mutate(
        post: Boolean,
        path: String,
        body: String? = null,
    ): HubResult<T> = call(serializer<T>()) {
        val configure: HttpRequestBuilder.() -> Unit = {
            bearerAuth(token)
            timeout { requestTimeoutMillis = ACTION_TIMEOUT_MS }
            if (body != null) {
                contentType(ContentType.Application.Json)
                setBody(body)
            }
        }
        if (post) client.post(base + path, configure) else client.put(base + path, configure)
    }

    @PublishedApi
    internal suspend fun <T> call(
        serializer: KSerializer<T>,
        request: suspend () -> HttpResponse,
    ): HubResult<T> =
        try {
            val response = request()
            val text = response.bodyAsText()
            val code = response.status.value
            if (response.status.isSuccess()) {
                val env = hubJson.decodeFromString(ApiEnvelope.serializer(serializer), text)
                val data = env.data
                if (env.success && data != null) {
                    HubResult.Ok(data)
                } else {
                    HubResult.Err(env.error ?: "Unexpected response", code)
                }
            } else {
                HubResult.Err(errorText(text) ?: "Hub error $code", code)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            HubResult.Err(UNREACHABLE, 0)
        }

    private fun errorText(body: String): String? =
        try {
            hubJson.decodeFromString(ApiEnvelope.serializer(JsonElement.serializer()), body).error
        } catch (_: Exception) {
            null
        }

    private fun seg(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")
}
