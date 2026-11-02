package com.jamilcpu.personalautomations.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

@Serializable
data class ApiEnvelope<T>(
    val success: Boolean = false,
    val data: T? = null,
    val error: String? = null,
)

@Serializable
data class Program(
    val id: String,
    val name: String,
    val kind: String,
    val device: String,
    val status: String,
    @SerialName("uptime_s") val uptimeS: Long? = null,
    val description: String = "",
    val system: Boolean = false,
    val protection: String = "none",
    val actions: List<String> = emptyList(),
    val group: String? = null,
    val detail: Map<String, String> = emptyMap(),
    /** Stable across restarts; what the hub pins by. */
    @SerialName("pin_key") val pinKey: String = "",
    val pinned: Boolean = false,
)

@Serializable
data class Health(
    @SerialName("cpu_pct") val cpuPct: Double,
    @SerialName("mem_pct") val memPct: Double,
    @SerialName("disk_pct") val diskPct: Double,
    @SerialName("temp_c") val tempC: Double? = null,
    @SerialName("uptime_s") val uptimeS: Long,
    val gpu: Map<String, Double>? = null,
)

@Serializable
data class UsbState(val on: Boolean, val detail: String = "")

@Serializable
data class ActionResult(val ok: Boolean, val message: String = "")

@Serializable
data class DeviceSummary(
    val id: String,
    val name: String,
    val role: String = "",
    val ip: String = "",
    val online: Boolean = false,
    val health: Health? = null,
    @SerialName("programs_running") val programsRunning: Int = 0,
    @SerialName("programs_total") val programsTotal: Int = 0,
    val capabilities: List<String> = emptyList(),
    val usb: UsbState? = null,
)

/** `ts` is an ISO-8601 string as sent by the hub. */
@Serializable
data class HistoryEntry(
    val ts: String,
    val client: String,
    val device: String,
    val target: String,
    val action: String,
    val ok: Boolean,
    val message: String = "",
)

@Serializable
data class ProgramDetail(
    val program: Program,
    val history: List<HistoryEntry> = emptyList(),
)

@Serializable
internal data class ActionRequest(val device: String, val id: String, val action: String)

@Serializable
internal data class UsbRequest(val on: Boolean)

@Serializable
internal data class PinRequest(val device: String, val id: String, val pinned: Boolean)

sealed interface HubResult<out T> {
    data class Ok<out T>(val value: T) : HubResult<T>
    data class Err(val message: String, val code: Int) : HubResult<Nothing>
}
