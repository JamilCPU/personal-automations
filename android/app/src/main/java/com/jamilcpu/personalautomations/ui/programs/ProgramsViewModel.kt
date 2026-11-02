package com.jamilcpu.personalautomations.ui.programs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.data.Program
import com.jamilcpu.personalautomations.data.UiPrefs
import com.jamilcpu.personalautomations.ui.common.HUB_UNREACHABLE
import com.jamilcpu.personalautomations.ui.common.deviceLabel
import com.jamilcpu.personalautomations.ui.devices.POLL_INTERVAL_MS
import java.io.IOException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class ProgramsUiState(
    val loading: Boolean = true,
    val programs: List<Program> = emptyList(),
    val deviceFilter: String? = null,
    val hideSystem: Boolean = true,
    /** Load failure: the empty/error state when nothing has loaded, else a banner over the old data. */
    val error: String? = null,
    /** A poll failed after data had loaded: keep showing it, grey out actions, show the banner. */
    val stale: Boolean = false,
    val loaded: Boolean = false,
    /** A fetch triggered by a filter/toggle/refresh is in flight (poll ticks do not set this). */
    val refreshing: Boolean = false,
)

/** Explicit empty-state text once a fetch has completed with nothing to show; null otherwise. */
fun ProgramsUiState.emptyMessage(): String? {
    if (!loaded || refreshing || stale || error != null || programs.isNotEmpty()) return null
    val where = deviceFilter?.let { " on ${deviceLabel(it)}" }.orEmpty()
    val hidden = if (hideSystem) " (system programs hidden)" else ""
    return "No programs$where$hidden"
}

val ProgramsUiState.banner: String?
    get() = if (stale) HUB_UNREACHABLE else if (loaded) error else null

class ProgramsViewModel(private val api: HubClient, private val prefs: UiPrefs) : ViewModel() {
    private val _state = MutableStateFlow(ProgramsUiState())
    val state: StateFlow<ProgramsUiState> = _state.asStateFlow()
    private val prefsLoaded = CompletableDeferred<Unit>()
    private var pollJob: Job? = null

    init {
        viewModelScope.launch {
            // A DataStore read failure falls back to the default (hide system programs).
            val stored = try {
                prefs.hideSystem.first()
            } catch (_: IOException) {
                true
            }
            _state.update { it.copy(hideSystem = stored) }
            prefsLoaded.complete(Unit)
        }
    }

    /** Begin polling; call when the tab becomes visible. Idempotent. */
    fun start() {
        if (pollJob?.isActive == true) return
        pollJob = viewModelScope.launch {
            while (true) {
                fetch()
                delay(POLL_INTERVAL_MS)
            }
        }
    }

    fun stop() {
        pollJob?.cancel()
        pollJob = null
    }

    fun setFilter(deviceId: String?) {
        if (_state.value.deviceFilter == deviceId) return
        _state.update {
            // Only the unfiltered list is a superset of the new device's programs; otherwise
            // clear, so the screen shows a spinner instead of a misleading blank/stale list.
            val keep = if (it.deviceFilter == null) it.programs.filterBy(deviceId) else emptyList()
            it.copy(deviceFilter = deviceId, programs = keep, refreshing = true)
        }
        refresh()
    }

    fun setHideSystem(value: Boolean) {
        _state.update { it.copy(hideSystem = value, refreshing = true) }
        viewModelScope.launch { prefs.setHideSystem(value) }
        refresh()
    }

    /** Fetch now (pull to refresh, Retry, filter change). */
    fun refresh() {
        _state.update { it.copy(refreshing = true) }
        viewModelScope.launch { fetch() }
    }

    private suspend fun fetch() {
        prefsLoaded.await()
        val device = _state.value.deviceFilter
        val hide = _state.value.hideSystem
        val result = api.programs(device = device, includeSystem = !hide)
        // A newer filter/toggle choice supersedes this response.
        if (device != _state.value.deviceFilter || hide != _state.value.hideSystem) return
        when (result) {
            is HubResult.Ok -> _state.update {
                it.copy(
                    loading = false, loaded = true, stale = false, error = null, refreshing = false,
                    programs = result.value.filterBy(device),
                )
            }
            is HubResult.Err -> _state.update {
                when {
                    // Only a network failure (code 0) means the hub is unreachable.
                    result.code == 0 && it.loaded -> it.copy(stale = true, refreshing = false)
                    else -> it.copy(loading = false, stale = false, error = result.message, refreshing = false)
                }
            }
        }
    }

    private fun List<Program>.filterBy(device: String?): List<Program> =
        if (device == null) this else filter { it.device == device }
}

class ProgramsViewModelFactory(private val api: HubClient, private val prefs: UiPrefs) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = ProgramsViewModel(api, prefs) as T
}
