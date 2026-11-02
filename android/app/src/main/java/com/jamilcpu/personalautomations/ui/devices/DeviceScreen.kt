package com.jamilcpu.personalautomations.ui.devices

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
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.ui.common.ConfirmDialog
import com.jamilcpu.personalautomations.ui.common.HubBanner
import com.jamilcpu.personalautomations.ui.common.StatusDot

private sealed interface Pending {
    data object UsbOff : Pending
    data object Reboot : Pending
    data class Group(val project: String) : Pending
}

@Composable
fun DeviceScreen(
    deviceId: String,
    state: DevicesUiState,
    viewModel: DevicesViewModel,
    onOpenPrograms: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    val device = state.devices.firstOrNull { it.id == deviceId }
    LaunchedEffect(deviceId) { viewModel.loadGroups(deviceId) }
    if (device == null) {
        Text("Device not found", modifier.padding(16.dp))
        return
    }
    DeviceContent(
        device = device,
        groups = state.groups[deviceId].orEmpty(),
        busy = deviceId in state.busy,
        stale = state.stale,
        banner = state.banner,
        onSetUsb = { viewModel.setUsb(deviceId, it) },
        onRestartGroup = { viewModel.restartGroup(deviceId, it) },
        onReboot = { viewModel.reboot(deviceId) },
        onOpenPrograms = { onOpenPrograms(deviceId) },
        modifier = modifier,
    )
}

@Composable
private fun DeviceContent(
    device: DeviceSummary,
    groups: List<String>,
    busy: Boolean,
    stale: Boolean,
    banner: String?,
    onSetUsb: (Boolean) -> Unit,
    onRestartGroup: (String) -> Unit,
    onReboot: () -> Unit,
    onOpenPrograms: () -> Unit,
    modifier: Modifier = Modifier,
) {
    var pending by remember { mutableStateOf<Pending?>(null) }
    val canAct = !busy && device.online && !stale
    Column(modifier.fillMaxSize()) {
        banner?.let { HubBanner(text = it) }
        Column(
            Modifier.verticalScroll(rememberScrollState()).padding(16.dp),
            verticalArrangement = Arrangement.spacedBy(16.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                StatusDot(device.online)
                Spacer(Modifier.width(12.dp))
                Column {
                    Text(device.name, style = MaterialTheme.typography.headlineSmall)
                    if (device.role.isNotBlank()) Text(device.role, style = MaterialTheme.typography.bodyMedium)
                }
            }
            val chips = healthChips(device)
            if (device.online && chips.isNotEmpty()) ChipRow(chips)

            if ("usb" in device.capabilities) {
                UsbRow(on = device.usb?.on == true, enabled = canAct) { wantOn ->
                    if (wantOn) onSetUsb(true) else pending = Pending.UsbOff
                }
            }
            groups.forEach { project ->
                Button(
                    onClick = { pending = Pending.Group(project) },
                    enabled = canAct,
                    modifier = Modifier.fillMaxWidth(),
                ) { Text(groupRestartLabel(project, device.capabilities)) }
            }
            TextButton(onClick = onOpenPrograms) { Text("Programs on ${device.name}") }
            if (canReboot(device)) {
                OutlinedButton(
                    onClick = { pending = Pending.Reboot },
                    enabled = canAct,
                    modifier = Modifier.fillMaxWidth(),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = MaterialTheme.colorScheme.error),
                ) { Text("Reboot ${device.name}…") }
            }
        }
    }

    when (val p = pending) {
        null -> Unit
        Pending.UsbOff -> ConfirmDialog(
            title = "Turn off USB power (all ports)?",
            confirmLabel = "Turn off",
            onConfirm = { pending = null; onSetUsb(false) },
            onDismiss = { pending = null },
        )
        Pending.Reboot -> ConfirmDialog(
            title = "Reboot ${device.name}?",
            text = "${device.name} will be offline for about a minute.",
            confirmLabel = "Reboot",
            danger = true,
            onConfirm = { pending = null; onReboot() },
            onDismiss = { pending = null },
        )
        is Pending.Group -> ConfirmDialog(
            title = groupRestartLabel(p.project, device.capabilities) + "?",
            confirmLabel = "Restart",
            onConfirm = { pending = null; onRestartGroup(p.project) },
            onDismiss = { pending = null },
        )
    }
}

@Composable
private fun UsbRow(on: Boolean, enabled: Boolean, onToggle: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text("USB power (all ports)", style = MaterialTheme.typography.titleMedium)
            Text("All USB ports · back on after reboot", style = MaterialTheme.typography.bodySmall)
        }
        Switch(checked = on, onCheckedChange = onToggle, enabled = enabled)
    }
}
