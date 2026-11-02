package com.jamilcpu.personalautomations.ui.programs

import com.jamilcpu.personalautomations.data.HubResult
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
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class ProgramsViewModelTest {
    private val dispatcher = StandardTestDispatcher()
    private val all = listOf(
        prog("a", device = "robot-pi"),
        prog("sys", device = "robot-pi", system = true),
        prog("b", device = "pi1"),
    )

    @Before fun setUp() = Dispatchers.setMain(dispatcher)

    @After fun tearDown() = Dispatchers.resetMain()

    @Test
    fun hide_system_default_true_and_filters() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        assertTrue(vm.state.value.hideSystem)
        vm.start()
        runCurrent()
        assertEquals(listOf("a", "b"), vm.state.value.programs.map { it.id })
        assertEquals(null to false, hub.queries.last())

        vm.setHideSystem(false)
        runCurrent()
        assertFalse(vm.state.value.hideSystem)
        assertEquals(listOf("a", "sys", "b"), vm.state.value.programs.map { it.id })
        assertEquals(null to true, hub.queries.last())
        vm.stop()
    }

    @Test
    fun hide_system_preference_is_persisted_and_restored() = runTest(dispatcher) {
        val prefs = FakePrefs(initial = false)
        val vm = ProgramsViewModel(FakePrograms(all), prefs)
        runCurrent()
        assertFalse(vm.state.value.hideSystem)
        vm.setHideSystem(true)
        runCurrent()
        assertTrue(prefs.hideSystem.value)
    }

    @Test
    fun device_filter_applies() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        vm.setFilter("pi1")
        runCurrent()
        assertEquals("pi1", vm.state.value.deviceFilter)
        assertEquals(listOf("b"), vm.state.value.programs.map { it.id })
        assertEquals("pi1" to false, hub.queries.last())
        vm.setFilter(null)
        runCurrent()
        assertEquals(2, vm.state.value.programs.size)
        vm.stop()
    }

    @Test
    fun poll_failure_after_load_marks_stale_without_error_and_recovers() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        assertFalse(vm.state.value.stale)

        hub.failure = HubResult.Err("Can't reach the hub", 0)
        advanceTimeBy(5_001)
        assertTrue(vm.state.value.stale)
        assertNull(vm.state.value.error)
        assertEquals(2, vm.state.value.programs.size)

        hub.failure = null
        advanceTimeBy(5_000)
        assertFalse(vm.state.value.stale)
        vm.stop()
    }

    @Test
    fun failure_before_any_load_sets_error_and_retry_recovers() = runTest(dispatcher) {
        val hub = FakePrograms(all).apply { failure = HubResult.Err("Can't reach the hub", 0) }
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        assertEquals("Can't reach the hub", vm.state.value.error)
        assertFalse(vm.state.value.loading)
        assertFalse(vm.state.value.stale)

        hub.failure = null
        vm.refresh()
        runCurrent()
        assertNull(vm.state.value.error)
        assertEquals(2, vm.state.value.programs.size)
        vm.stop()
    }

    @Test
    fun hub_error_after_load_shows_message_not_unreachable() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        hub.failure = HubResult.Err("hub exploded", 500)
        advanceTimeBy(5_001)
        assertFalse(vm.state.value.stale)
        assertEquals("hub exploded", vm.state.value.banner)
        assertEquals(2, vm.state.value.programs.size)
        vm.stop()
    }

    @Test
    fun prefs_read_failure_falls_back_to_default() = runTest(dispatcher) {
        val prefs = object : com.jamilcpu.personalautomations.data.UiPrefs {
            override val hideSystem = kotlinx.coroutines.flow.flow<Boolean> { throw java.io.IOException("disk") }
            override suspend fun setHideSystem(value: Boolean) = Unit
        }
        val vm = ProgramsViewModel(FakePrograms(all), prefs)
        vm.start()
        runCurrent()
        assertTrue(vm.state.value.hideSystem)
        assertEquals(2, vm.state.value.programs.size)
        vm.stop()
    }

    @Test
    fun filter_switch_from_specific_device_clears_and_shows_refreshing_then_data() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        vm.setFilter("robot-pi")
        runCurrent()
        hub.delays["pi1"] = 3_000
        vm.setFilter("pi1")
        runCurrent()
        assertTrue(vm.state.value.refreshing)
        assertTrue(vm.state.value.programs.isEmpty())
        assertNull(vm.state.value.emptyMessage())
        advanceTimeBy(3_001)
        assertFalse(vm.state.value.refreshing)
        assertEquals(listOf("b"), vm.state.value.programs.map { it.id })
        vm.stop()
    }

    @Test
    fun filter_switch_from_all_keeps_filtered_old_list_while_refreshing() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        hub.delays["pi1"] = 3_000
        vm.setFilter("pi1")
        runCurrent()
        assertTrue(vm.state.value.refreshing)
        assertEquals(listOf("b"), vm.state.value.programs.map { it.id })
        advanceTimeBy(3_001)
        assertFalse(vm.state.value.refreshing)
        vm.stop()
    }

    @Test
    fun rapid_switches_end_with_last_filter_data_and_not_refreshing() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        hub.delays["robot-pi"] = 3_000
        hub.delays["pi1"] = 1_000
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        vm.setFilter("robot-pi")
        runCurrent()
        vm.setFilter("pi1")
        runCurrent()
        advanceTimeBy(3_500)
        assertEquals("pi1", vm.state.value.deviceFilter)
        assertEquals(listOf("b"), vm.state.value.programs.map { it.id })
        assertFalse(vm.state.value.refreshing)
        vm.stop()
    }

    @Test
    fun empty_result_shows_explicit_message() = runTest(dispatcher) {
        val hub = FakePrograms(all)
        val vm = ProgramsViewModel(hub, FakePrefs())
        vm.start()
        runCurrent()
        vm.setFilter("pi3")
        runCurrent()
        assertTrue(vm.state.value.programs.isEmpty())
        assertFalse(vm.state.value.refreshing)
        assertEquals("No programs on Pi3 (system programs hidden)", vm.state.value.emptyMessage())
        vm.setHideSystem(false)
        runCurrent()
        assertEquals("No programs on Pi3", vm.state.value.emptyMessage())
        vm.stop()
    }
}
