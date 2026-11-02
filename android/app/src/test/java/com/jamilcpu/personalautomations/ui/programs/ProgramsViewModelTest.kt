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
}
