package com.jamilcpu.personalautomations.ui.programs

import com.jamilcpu.personalautomations.data.ActionResult
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.data.Program
import com.jamilcpu.personalautomations.data.ProgramDetail
import com.jamilcpu.personalautomations.data.UiPrefs
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow

/** Fake hub honouring the device / include_system arguments like the real one. */
internal class FakePrograms(var all: List<Program> = emptyList()) : HubClient {
    var failure: HubResult.Err? = null
    val queries = mutableListOf<Pair<String?, Boolean>>()

    /** Simulated hub latency per device filter (key null = unfiltered). */
    val delays = mutableMapOf<String?, Long>()

    var detail: HubResult<ProgramDetail> = HubResult.Err("unset", 0)
    var logs: HubResult<List<String>> = HubResult.Ok(emptyList())
    var actResult: HubResult<ActionResult> = HubResult.Ok(ActionResult(true))
    val actions = mutableListOf<String>()
    var detailCalls = 0

    /** Result of setPinned; on success the fake applies the pin to [all] like the hub does. */
    var pinResult: HubResult<ActionResult> = HubResult.Ok(ActionResult(true))
    var pinDelay = 0L
    val pinCalls = mutableListOf<Triple<String, String, Boolean>>()

    override suspend fun devices(): HubResult<List<DeviceSummary>> = HubResult.Ok(emptyList())

    override suspend fun programs(device: String?, includeSystem: Boolean): HubResult<List<Program>> {
        queries += device to includeSystem
        delays[device]?.let { kotlinx.coroutines.delay(it) }
        failure?.let { return it }
        return HubResult.Ok(all.filter { (device == null || it.device == device) && (includeSystem || !it.system) })
    }

    override suspend fun program(device: String, id: String): HubResult<ProgramDetail> {
        detailCalls++
        return detail
    }

    override suspend fun logs(device: String, id: String, lines: Int): HubResult<List<String>> = logs

    override suspend fun act(device: String, id: String, action: String): HubResult<ActionResult> {
        actions += action
        return actResult
    }

    override suspend fun setPinned(device: String, id: String, pinned: Boolean): HubResult<ActionResult> {
        pinCalls += Triple(device, id, pinned)
        if (pinDelay > 0) kotlinx.coroutines.delay(pinDelay)
        val result = pinResult
        if (result is HubResult.Ok && result.value.ok) {
            all = all.map { if (it.device == device && it.id == id) it.copy(pinned = pinned) else it }
        }
        return result
    }

    override suspend fun setUsb(device: String, on: Boolean): HubResult<ActionResult> = HubResult.Ok(ActionResult(true))

    override suspend fun reboot(device: String): HubResult<ActionResult> = HubResult.Ok(ActionResult(true))

    override suspend fun restartGroup(device: String, project: String): HubResult<ActionResult> =
        HubResult.Ok(ActionResult(true))
}

internal class FakePrefs(initial: Boolean = true) : UiPrefs {
    private val flow = MutableStateFlow(initial)
    override val hideSystem: StateFlow<Boolean> = flow

    override suspend fun setHideSystem(value: Boolean) {
        flow.value = value
    }
}

internal fun prog(
    id: String,
    device: String = "robot-pi",
    system: Boolean = false,
    protection: String = "none",
    status: String = "running",
    actions: List<String> = listOf("stop", "restart"),
    pinned: Boolean = false,
) = Program(
    id = id, name = id, kind = "docker", device = device, status = status,
    system = system, protection = protection, actions = actions, pinKey = id, pinned = pinned,
)
