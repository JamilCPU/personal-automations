package com.jamilcpu.personalautomations.ui.devices

import com.jamilcpu.personalautomations.data.ActionResult
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.data.Program
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.advanceTimeBy
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private class FakeHub : HubClient {
    var devices: HubResult<List<DeviceSummary>> = HubResult.Ok(emptyList())
    var usbResult: HubResult<ActionResult> = HubResult.Ok(ActionResult(true))
    var rebootGate: CompletableDeferred<Unit>? = null
    var programs: HubResult<List<Program>> = HubResult.Ok(emptyList())
    var deviceCalls = 0
    val rebooted = mutableListOf<String>()
    val usbCalls = mutableListOf<Pair<String, Boolean>>()
    val groupCalls = mutableListOf<Pair<String, String>>()

    override suspend fun devices(): HubResult<List<DeviceSummary>> {
        deviceCalls++
        return devices
    }

    override suspend fun programs(device: String?, includeSystem: Boolean) = programs

    override suspend fun setUsb(device: String, on: Boolean): HubResult<ActionResult> {
        usbCalls += device to on
        return usbResult
    }

    override suspend fun reboot(device: String): HubResult<ActionResult> {
        rebootGate?.await()
        rebooted += device
        return HubResult.Ok(ActionResult(true))
    }

    override suspend fun restartGroup(device: String, project: String): HubResult<ActionResult> {
        groupCalls += device to project
        return HubResult.Ok(ActionResult(true))
    }
}

@OptIn(ExperimentalCoroutinesApi::class)
class DevicesViewModelTest {
    private val dispatcher = StandardTestDispatcher()
    private val pi = DeviceSummary(id = "robot-pi", name = "Robot Pi", online = true)

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    @Test
    fun polls_and_exposes_devices() = runTest(dispatcher) {
        val hub = FakeHub().apply { devices = HubResult.Ok(listOf(pi)) }
        val vm = DevicesViewModel(hub)
        assertTrue(vm.state.value.loading)

        vm.start()
        runCurrent()
        assertEquals(listOf(pi), vm.state.value.devices)
        assertFalse(vm.state.value.loading)
        assertEquals(1, hub.deviceCalls)

        advanceTimeBy(5_001)
        assertEquals(2, hub.deviceCalls)
        advanceTimeBy(5_000)
        assertEquals(3, hub.deviceCalls)

        vm.stop()
        advanceTimeBy(20_000)
        assertEquals(3, hub.deviceCalls)
    }

    @Test
    fun usb_failure_shows_hub_message() = runTest(dispatcher) {
        val hub = FakeHub().apply {
            usbResult = HubResult.Err("lidar did not appear after power on", 502)
        }
        val vm = DevicesViewModel(hub)
        vm.setUsb("robot-pi", true)
        runCurrent()
        assertEquals("lidar did not appear after power on", vm.state.value.error)
        assertEquals(listOf("robot-pi" to true), hub.usbCalls)
        assertTrue(vm.state.value.busy.isEmpty())
        vm.clearError()
        assertEquals(null, vm.state.value.error)
    }

    @Test
    fun reboot_marks_busy_then_clears() = runTest(dispatcher) {
        val gate = CompletableDeferred<Unit>()
        val hub = FakeHub().apply { rebootGate = gate; devices = HubResult.Ok(listOf(pi)) }
        val vm = DevicesViewModel(hub)
        vm.reboot("robot-pi")
        runCurrent()
        assertEquals(setOf("robot-pi"), vm.state.value.busy)

        gate.complete(Unit)
        runCurrent()
        assertTrue(vm.state.value.busy.isEmpty())
        assertEquals(listOf("robot-pi"), hub.rebooted)
        assertEquals(listOf(pi), vm.state.value.devices)
    }

    @Test
    fun groups_come_from_device_programs() = runTest(dispatcher) {
        fun p(id: String, group: String?) =
            Program(id = id, name = id, kind = "docker", device = "robot-pi", status = "running", group = group)
        val hub = FakeHub().apply {
            programs = HubResult.Ok(listOf(p("a", "sentinel-ros"), p("b", "sentinel-ros"), p("c", null)))
        }
        val vm = DevicesViewModel(hub)
        vm.loadGroups("robot-pi")
        runCurrent()
        assertEquals(listOf("sentinel-ros"), vm.state.value.groups["robot-pi"])
        vm.restartGroup("robot-pi", "sentinel-ros")
        runCurrent()
        assertEquals(listOf("robot-pi" to "sentinel-ros"), hub.groupCalls)
    }
}
