package com.jamilcpu.personalautomations.ui.programs

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.Checkbox
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jamilcpu.personalautomations.data.Program
import com.jamilcpu.personalautomations.ui.common.DEVICE_LABELS
import com.jamilcpu.personalautomations.ui.common.HubBanner
import com.jamilcpu.personalautomations.ui.common.LoadState
import com.jamilcpu.personalautomations.ui.common.StatusDot
import com.jamilcpu.personalautomations.ui.common.deviceLabel
import com.jamilcpu.personalautomations.ui.common.formatUptime
import com.jamilcpu.personalautomations.ui.common.rememberRefreshing

/** Card subtitle: the description, else kind plus "up 2d 4h" while running, else the status text. */
fun programSubtitle(program: Program): String = when {
    program.description.isNotBlank() -> program.description
    program.status == "running" ->
        listOfNotNull(program.kind, program.uptimeS?.let { "up ${formatUptime(it)}" }).joinToString(" · ")
    else -> program.status
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ProgramsScreen(
    state: ProgramsUiState,
    onFilter: (String?) -> Unit,
    onHideSystem: (Boolean) -> Unit,
    onRefresh: () -> Unit,
    onOpenProgram: (device: String, id: String) -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(modifier.fillMaxSize()) {
        var menuOpen by remember { mutableStateOf(false) }
        TopAppBar(
            title = { Text("Programs") },
            actions = {
                IconButton(onClick = { menuOpen = true }) { Icon(Icons.Filled.MoreVert, contentDescription = "Menu") }
                DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                    DropdownMenuItem(
                        text = { Text("Hide system programs") },
                        leadingIcon = { Checkbox(checked = state.hideSystem, onCheckedChange = null) },
                        onClick = { onHideSystem(!state.hideSystem) },
                    )
                }
            },
        )
        if (state.stale) HubBanner()
        FilterRow(state.deviceFilter, onFilter)
        if (!state.loaded) {
            LoadState(error = state.error, onRetry = onRefresh)
            return@Column
        }
        val (refreshing, startRefresh) = rememberRefreshing(onRefresh)
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = startRefresh, modifier = Modifier.fillMaxSize()) {
            if (state.programs.isEmpty()) {
                // Keeps the pull gesture available on an empty list.
                LazyColumn(Modifier.fillMaxSize()) {
                    item {
                        Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                            Text("No programs")
                        }
                    }
                }
            } else {
                LazyColumn(
                    Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                ) {
                    items(state.programs, key = { it.device + "/" + it.id }) { program ->
                        ProgramCard(program) { onOpenProgram(program.device, program.id) }
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun FilterRow(selected: String?, onFilter: (String?) -> Unit) {
    LazyRow(
        contentPadding = PaddingValues(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        item { FilterChip(selected = selected == null, onClick = { onFilter(null) }, label = { Text("All") }) }
        items(DEVICE_LABELS.entries.toList()) { (id, label) ->
            FilterChip(selected = selected == id, onClick = { onFilter(id) }, label = { Text(label) })
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ProgramCard(program: Program, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Row(
            Modifier.padding(16.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            StatusDot(program.status == "running")
            Column(Modifier.weight(1f)) {
                Text(program.name, style = MaterialTheme.typography.titleMedium)
                Text(programSubtitle(program), style = MaterialTheme.typography.bodySmall)
            }
            AssistChip(onClick = onClick, label = { Text(deviceLabel(program.device)) })
        }
    }
}
