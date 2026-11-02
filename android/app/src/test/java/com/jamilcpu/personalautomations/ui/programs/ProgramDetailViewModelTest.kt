package com.jamilcpu.personalautomations.ui.programs

import com.jamilcpu.personalautomations.data.ActionResult
import com.jamilcpu.personalautomations.data.HistoryEntry
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.data.Program
import com.jamilcpu.personalautomations.data.ProgramDetail
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

private const val GONE = "This program no longer exists — pull to refresh"

@OptIn(ExperimentalCoroutinesApi::class)
class ProgramDetailViewModelTest {
    private val dispatcher = StandardTestDispatcher()

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    private fun vmFor(program: Program): Pair<ProgramDetailViewModel, FakePrograms> {
        val hub = FakePrograms()
        val entry = HistoryEntry("2026-11-01T10:00:00Z", "phone", program.device, program.id, "restart", true)
        hub.detail = HubResult.Ok(ProgramDetail(program, listOf(entry)))
        hub.logs = HubResult.Ok(listOf("line 1", "line 2"))
        return ProgramDetailViewModel(hub, program.device, program.id) to hub
    }

    @Test
    fun loads_program_logs_and_history() = runTest(dispatcher) {
        val (vm, _) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        val s = vm.state.value
        assertEquals("a", s.program?.id)
        assertEquals(listOf("line 1", "line 2"), s.logs)
        assertEquals(1, s.history.size)
        assertFalse(s.loading)
        assertEquals(listOf("stop", "restart"), vm.visibleActions())
    }

    @Test
    fun confirm_protection_requires_second_confirm() = runTest(dispatcher) {
        val (vm, _) = vmFor(prog("pihole-FTL.service", protection = "confirm"))
        vm.load()
        runCurrent()
        assertTrue(vm.needsSecondConfirm)
        val (plain, _) = vmFor(prog("b"))
        plain.load()
        runCurrent()
        assertFalse(plain.needsSecondConfirm)
    }

    @Test
    fun locked_program_has_no_buttons() = runTest(dispatcher) {
        val (vm, _) = vmFor(prog("ssh.service", protection = "locked", actions = listOf("stop", "restart")))
        vm.load()
        runCurrent()
        assertTrue(vm.visibleActions().isEmpty())
        assertTrue(vm.isLocked)
    }

    @Test
    fun act_calls_hub_then_refreshes() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        val before = hub.detailCalls
        vm.act("restart")
        runCurrent()
        assertEquals(listOf("restart"), hub.actions)
        assertEquals(before + 1, hub.detailCalls)
        assertNull(vm.state.value.error)
        assertFalse(vm.state.value.busy)
    }

    @Test
    fun action_error_surfaces_message() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.actResult = HubResult.Err("already running", 409)
        vm.act("start")
        runCurrent()
        assertEquals("already running", vm.state.value.error)
        assertFalse(vm.state.value.gone)
        vm.clearError()
        assertNull(vm.state.value.error)
    }

    @Test
    fun action_not_ok_surfaces_message() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.actResult = HubResult.Ok(ActionResult(false, "docker said no"))
        vm.act("restart")
        runCurrent()
        assertEquals("docker said no", vm.state.value.error)
    }

    @Test
    fun stale_program_404_shows_gone() = runTest(dispatcher) {
        val hub = FakePrograms().apply { detail = HubResult.Err("not found", 404) }
        val vm = ProgramDetailViewModel(hub, "robot-pi", "gone")
        vm.load()
        runCurrent()
        assertTrue(vm.state.value.gone)
        assertEquals(GONE, vm.state.value.error)
    }

    @Test
    fun act_404_shows_gone() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.actResult = HubResult.Err("not found", 404)
        vm.act("stop")
        runCurrent()
        assertTrue(vm.state.value.gone)
        assertEquals(GONE, vm.state.value.error)
    }

    @Test
    fun unreachable_after_load_is_stale_and_keeps_data() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.detail = HubResult.Err("Can't reach the hub", 0)
        vm.load()
        runCurrent()
        assertTrue(vm.state.value.stale)
        assertEquals("a", vm.state.value.program?.id)
        assertNull(vm.state.value.error)
    }

    @Test
    fun successful_load_clears_stale_error() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.actResult = HubResult.Err("boom", 409)
        vm.act("start")
        runCurrent()
        assertEquals("boom", vm.state.value.error)
        vm.load()
        runCurrent()
        assertNull(vm.state.value.error)
    }

    @Test
    fun hub_error_after_load_shows_message_not_stale() = runTest(dispatcher) {
        val (vm, hub) = vmFor(prog("a"))
        vm.load()
        runCurrent()
        hub.detail = HubResult.Err("hub exploded", 503)
        vm.load()
        runCurrent()
        assertFalse(vm.state.value.stale)
        assertEquals("hub exploded", vm.state.value.error)
    }
}
