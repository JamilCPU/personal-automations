package com.jamilcpu.personalautomations.ui.programs

import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.jamilcpu.personalautomations.data.HistoryEntry
import com.jamilcpu.personalautomations.data.Program
import com.jamilcpu.personalautomations.ui.common.ConfirmDialog
import com.jamilcpu.personalautomations.ui.common.HubBanner
import com.jamilcpu.personalautomations.ui.common.LoadState
import com.jamilcpu.personalautomations.ui.common.StatusDot
import com.jamilcpu.personalautomations.ui.common.deviceLabel
import com.jamilcpu.personalautomations.ui.common.formatUptime
import com.jamilcpu.personalautomations.ui.common.relativeTime
import com.jamilcpu.personalautomations.ui.common.rememberRefreshing
import java.util.Locale

const val LOCKED_CAPTION = "Protected — can't be stopped from here"

private enum class Step { First, Second }

private data class PendingAction(val action: String, val step: Step)

/** "Stop" / "Restart" / "Start". */
fun actionLabel(action: String): String = action.replaceFirstChar { it.titlecase(Locale.ENGLISH) }

/** Actions that need a confirmation dialog. */
private fun needsConfirm(action: String) = action == "stop" || action == "restart"

@Composable
fun ProgramDetailScreen(
    state: ProgramDetailUiState,
    visibleActions: List<String>,
    locked: Boolean,
    needsSecondConfirm: Boolean,
    onAct: (String) -> Unit,
    onRefresh: () -> Unit,
    onRefreshLogs: () -> Unit,
    modifier: Modifier = Modifier,
) {
    val program = state.program
    if (program == null) {
        Column(modifier.fillMaxSize()) {
            if (state.stale) HubBanner()
            LoadState(error = if (state.loading) null else state.error ?: "Loading failed", onRetry = onRefresh)
        }
        return
    }
    DetailContent(state, program, visibleActions, locked, needsSecondConfirm, onAct, onRefresh, onRefreshLogs, modifier)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DetailContent(
    state: ProgramDetailUiState,
    program: Program,
    visibleActions: List<String>,
    locked: Boolean,
    needsSecondConfirm: Boolean,
    onAct: (String) -> Unit,
    onRefresh: () -> Unit,
    onRefreshLogs: () -> Unit,
    modifier: Modifier,
) {
    var pending by remember { mutableStateOf<PendingAction?>(null) }
    val canAct = !state.busy && !state.stale && !state.gone
    val (refreshing, startRefresh) = rememberRefreshing(onRefresh)
    Column(modifier.fillMaxSize()) {
        if (state.stale) HubBanner()
        PullToRefreshBox(isRefreshing = refreshing, onRefresh = startRefresh, modifier = Modifier.fillMaxSize()) {
            Column(
                Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),
                verticalArrangement = Arrangement.spacedBy(16.dp),
            ) {
                Header(program)
                state.error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
                InfoRows(program)
                if (locked) {
                    Text(LOCKED_CAPTION, style = MaterialTheme.typography.bodyMedium)
                } else {
                    visibleActions.forEach { action ->
                        ActionButton(action, canAct) {
                            if (needsConfirm(action)) pending = PendingAction(action, Step.First) else onAct(action)
                        }
                    }
                }
                LogBlock(state.logs, onRefreshLogs)
                HistoryList(state.history)
            }
        }
    }
    pending?.let { p ->
        val word = p.action
        when (p.step) {
            Step.First -> ConfirmDialog(
                title = "${actionLabel(word)} ${program.name}?",
                confirmLabel = actionLabel(word),
                danger = true,
                onConfirm = {
                    if (needsSecondConfirm) {
                        pending = PendingAction(word, Step.Second)
                    } else {
                        pending = null
                        onAct(word)
                    }
                },
                onDismiss = { pending = null },
            )
            Step.Second -> ConfirmDialog(
                title = "This is ${program.name}. Your network's DNS depends on it — really $word?",
                confirmLabel = actionLabel(word),
                danger = true,
                onConfirm = { pending = null; onAct(word) },
                onDismiss = { pending = null },
            )
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun Header(program: Program) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        StatusDot(program.status == "running")
        Spacer(Modifier.width(12.dp))
        Text(program.name, style = MaterialTheme.typography.headlineSmall, modifier = Modifier.weight(1f))
        AssistChip(onClick = {}, label = { Text(deviceLabel(program.device)) })
    }
}

@Composable
private fun InfoRows(program: Program) {
    val kind = listOfNotNull(program.kindLabel(), program.group).joinToString(" · ")
    Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
        InfoRow("Status", program.status)
        program.uptimeS?.let { InfoRow("Uptime", formatUptime(it)) }
        InfoRow("Kind", kind)
        if (program.kind == "k3s") {
            program.detail["node"]?.let { InfoRow("Node", it) }
            program.detail["restarts"]?.let { InfoRow("Restarts", it) }
        }
    }
}

private fun Program.kindLabel(): String = when (kind) {
    "docker" -> "Docker"
    "k3s" -> "k3s"
    "systemd" -> "systemd"
    "windows-service" -> "Windows service"
    "process" -> "Process"
    else -> kind
}

@Composable
private fun InfoRow(label: String, value: String) {
    Row(Modifier.fillMaxWidth()) {
        Text(label, Modifier.width(96.dp), style = MaterialTheme.typography.bodyMedium)
        Text(value, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun ActionButton(action: String, enabled: Boolean, onClick: () -> Unit) {
    if (action == "stop") {
        OutlinedButton(
            onClick = onClick,
            enabled = enabled,
            modifier = Modifier.fillMaxWidth(),
            colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
        ) { Text(actionLabel(action)) }
    } else {
        Button(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth()) {
            Text(actionLabel(action))
        }
    }
}

@Composable
private fun LogBlock(lines: List<String>, onRefresh: () -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Recent log", style = MaterialTheme.typography.titleMedium, modifier = Modifier.weight(1f))
            TextButton(onClick = onRefresh) { Text("Refresh") }
        }
        Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = MaterialTheme.shapes.small) {
            Text(
                if (lines.isEmpty()) "No log output" else lines.joinToString("\n"),
                Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(8.dp),
                fontFamily = FontFamily.Monospace,
                fontSize = 11.sp,
                softWrap = false,
            )
        }
    }
}

@Composable
private fun HistoryList(history: List<HistoryEntry>) {
    Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Text("History", style = MaterialTheme.typography.titleMedium)
        if (history.isEmpty()) Text("No actions yet", style = MaterialTheme.typography.bodySmall)
        history.forEach { entry ->
            Column {
                val result = if (entry.ok) "ok" else "failed"
                Text("${entry.client} · ${entry.action} · $result", style = MaterialTheme.typography.bodyMedium)
                val detail = listOfNotNull(entry.message.takeIf { it.isNotBlank() }, relativeTime(entry.ts))
                Text(detail.joinToString(" · "), style = MaterialTheme.typography.bodySmall)
            }
        }
    }
}
