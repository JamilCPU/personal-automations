package com.jamilcpu.personalautomations.ui.devices

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.jamilcpu.personalautomations.data.ActionResult
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
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
    val error: String? = null,
    val busy: Set<String> = emptySet(),
    /** Robot-program group names (project) per device id, for "Restart all" buttons. */
    val groups: Map<String, List<String>> = emptyMap(),
)

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
            is HubResult.Ok -> _state.update { it.copy(loading = false, devices = result.value) }
            // Polling failures stay quiet once there is something on screen.
            is HubResult.Err -> _state.update {
                it.copy(loading = false, error = if (it.devices.isEmpty()) result.message else it.error)
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
