package com.jamilcpu.personalautomations.ui.programs

import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.viewModelScope
import com.jamilcpu.personalautomations.data.HistoryEntry
import com.jamilcpu.personalautomations.data.HubClient
import com.jamilcpu.personalautomations.data.HubResult
import com.jamilcpu.personalautomations.data.Program
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

const val GONE_MESSAGE = "This program no longer exists — pull to refresh"
const val LOG_LINES = 50
private const val NOT_FOUND = 404

data class ProgramDetailUiState(
    val loading: Boolean = true,
    val program: Program? = null,
    val history: List<HistoryEntry> = emptyList(),
    val logs: List<String> = emptyList(),
    val busy: Boolean = false,
    /** Action failure or the "gone" message. */
    val error: String? = null,
    val gone: Boolean = false,
    /** Hub unreachable after data had loaded: banner, actions greyed out. */
    val stale: Boolean = false,
)

class ProgramDetailViewModel(
    private val api: HubClient,
    val device: String,
    val id: String,
) : ViewModel() {
    private val _state = MutableStateFlow(ProgramDetailUiState())
    val state: StateFlow<ProgramDetailUiState> = _state.asStateFlow()

    val needsSecondConfirm: Boolean get() = _state.value.program?.protection == "confirm"

    val isLocked: Boolean get() = _state.value.program?.protection == "locked"

    /** The action buttons to show: none for a locked program. */
    fun visibleActions(): List<String> =
        if (isLocked) emptyList() else _state.value.program?.actions.orEmpty()

    fun clearError() = _state.update { it.copy(error = null) }

    /** Reload program, history and log. Used on open, pull to refresh and after actions. */
    fun load() {
        viewModelScope.launch { reload() }
    }

    fun refreshLogs() {
        viewModelScope.launch {
            val logs = api.logs(device, id, LOG_LINES)
            if (logs is HubResult.Ok) _state.update { it.copy(logs = logs.value) }
        }
    }

    fun act(action: String) {
        viewModelScope.launch {
            _state.update { it.copy(busy = true, error = null) }
            val failure = when (val result = api.act(device, id, action)) {
                is HubResult.Ok -> result.value.takeIf { !it.ok }?.message?.ifBlank { "Action failed" }
                is HubResult.Err -> if (result.code == NOT_FOUND) GONE_MESSAGE else result.message
            }
            reload()
            _state.update {
                it.copy(
                    busy = false,
                    error = failure ?: it.error,
                    gone = it.gone || failure == GONE_MESSAGE,
                )
            }
        }
    }

    private suspend fun reload() {
        when (val detail = api.program(device, id)) {
            is HubResult.Ok -> {
                val logs = (api.logs(device, id, LOG_LINES) as? HubResult.Ok)?.value
                _state.update {
                    it.copy(
                        loading = false, program = detail.value.program, history = detail.value.history,
                        logs = logs ?: it.logs, stale = false, gone = false,
                    )
                }
            }
            is HubResult.Err -> _state.update {
                when {
                    detail.code == NOT_FOUND -> it.copy(loading = false, gone = true, error = GONE_MESSAGE)
                    it.program != null && detail.code == 0 -> it.copy(loading = false, stale = true)
                    else -> it.copy(loading = false, error = detail.message)
                }
            }
        }
    }
}

class ProgramDetailViewModelFactory(
    private val api: HubClient,
    private val device: String,
    private val id: String,
) : ViewModelProvider.Factory {
    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T = ProgramDetailViewModel(api, device, id) as T
}
