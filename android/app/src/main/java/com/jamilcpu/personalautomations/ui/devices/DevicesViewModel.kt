package com.jamilcpu.personalautomations.ui.devices

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.jamilcpu.personalautomations.data.ActionResult
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.ui.common.HUB_UNREACHABLE
import kotlinx.coroutines.Job
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

const val POLL_INTERVAL_MS = 5_000L

data class DevicesUiState(
    val loading: Boolean = true,
    val devices: List<DeviceSummary> = emptyList(),
    /** Action failure, shown once as a snackbar. */
    val error: String? = null,
    /** Hub-reported (non-network) load failure; empty/error state with Retry when nothing has loaded. */
    val loadError: String? = null,
    /** A poll failed after data had loaded: banner, actions greyed out. */
    val stale: Boolean = false,
    val loaded: Boolean = false,
    val busy: Set<String> = emptySet(),
    /** Robot-program group names (project) per device id, for "Restart all" buttons. */
    val groups: Map<String, List<String>> = emptyMap(),
)

/** Banner text: unreachable, or the hub's own error while older data is shown. */
val DevicesUiState.banner: String?
    get() = if (stale) HUB_UNREACHABLE else if (loaded) loadError else null

class DevicesViewModel(private val api: HubClient) : ViewModel() {
    private val _state = MutableStateFlow(DevicesUiState())
    val state: StateFlow<DevicesUiState> = _state.asStateFlow()
    private var pollJob: Job? = null

    /** Begin polling; call when the screen becomes visible. Idempotent. */
    fun start() {
        if (pollJob?.isActive == true) return
        pollJob = viewModelScope.launch {
            while (true) {
                refresh()
                delay(POLL_INTERVAL_MS)
            }
        }
    }

    /** Stop polling; call when the screen is hidden. */
    fun stop() {
        pollJob?.cancel()
        pollJob = null
    }

    fun retry() {
        viewModelScope.launch { refresh() }
    }

    fun clearError() = _state.update { it.copy(error = null) }

    fun setUsb(deviceId: String, on: Boolean) = act(deviceId) { api.setUsb(deviceId, on) }

    fun reboot(deviceId: String) = act(deviceId) { api.reboot(deviceId) }

    fun restartGroup(deviceId: String, project: String) = act(deviceId) { api.restartGroup(deviceId, project) }

    fun loadGroups(deviceId: String) {
        viewModelScope.launch {
            val result = api.programs(device = deviceId, includeSystem = true)
            if (result is HubResult.Ok) {
                val groups = result.value.mapNotNull { it.group }.distinct()
                _state.update { it.copy(groups = it.groups + (deviceId to groups)) }
            }
        }
    }

    private suspend fun refresh() {
        when (val result = api.devices()) {
            is HubResult.Ok -> _state.update {
                it.copy(loading = false, loaded = true, stale = false, loadError = null, devices = result.value)
            }
            // Keep the last data on a failed poll and flag it; no repeated snackbars.
            is HubResult.Err -> _state.update {
                when {
                    // Only a network failure (code 0) means the hub is unreachable.
                    result.code == 0 && it.loaded -> it.copy(stale = true)
                    else -> it.copy(loading = false, stale = false, loadError = result.message)
                }
            }
        }
    }

    private fun act(deviceId: String, call: suspend () -> HubResult<ActionResult>) {
        viewModelScope.launch {
            _state.update { it.copy(busy = it.busy + deviceId) }
            val message = when (val result = call()) {
                is HubResult.Ok -> result.value.takeIf { !it.ok }?.message?.ifBlank { "Action failed" }
                is HubResult.Err -> result.message
            }
            if (message == null) refresh()
            _state.update { it.copy(busy = it.busy - deviceId, error = message ?: it.error) }
        }
    }
}

class DevicesViewModelFactory(private val api: HubClient) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = DevicesViewModel(api) as T
}
