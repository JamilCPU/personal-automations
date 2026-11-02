package com.jamilcpu.personalautomations.ui.devices

import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Card
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.jamilcpu.personalautomations.data.DeviceSummary
import com.jamilcpu.personalautomations.ui.common.StatusDot
import kotlin.math.roundToInt

private const val MINUTE = 60L
private const val HOUR = 3_600L
private const val DAY = 86_400L

/** "3d 4h", "5h 12m" or "7m". */
fun formatUptime(seconds: Long): String = when {
    seconds >= DAY -> "${seconds / DAY}d ${seconds % DAY / HOUR}h"
    seconds >= HOUR -> "${seconds / HOUR}h ${seconds % HOUR / MINUTE}m"
    else -> "${seconds / MINUTE}m"
}

/** Chip labels for a device card, in display order. Empty when the device has no health. */
fun healthChips(device: DeviceSummary): List<String> {
    val health = device.health ?: return emptyList()
    val chips = mutableListOf<String>()
    health.tempC?.let { chips += "${it.roundToInt()}°C" }
    chips += "CPU ${health.cpuPct.roundToInt()}%"
    chips += "${device.programsRunning}/${device.programsTotal} programs"
    health.gpu?.get("temp_c")?.let { chips += "GPU ${it.roundToInt()}°C" }
    if (device.id == "jw-pc") chips += "up ${formatUptime(health.uptimeS)}"
    return chips
}

fun usbChip(device: DeviceSummary): String? =
    device.usb?.takeIf { "usb" in device.capabilities }?.let { if (it.on) "USB on" else "USB off" }

@Composable
fun DevicesScreen(
    state: DevicesUiState,
    onOpenDevice: (String) -> Unit,
    modifier: Modifier = Modifier,
) {
    if (state.loading && state.devices.isEmpty()) {
        Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator() }
        return
    }
    LazyColumn(
        modifier.fillMaxSize(),
        contentPadding = PaddingValues(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(state.devices, key = { it.id }) { device ->
            DeviceCard(device) { onOpenDevice(device.id) }
        }
    }
}

@Composable
private fun DeviceCard(device: DeviceSummary, onClick: () -> Unit) {
    Card(Modifier.fillMaxWidth().clickable(onClick = onClick)) {
        Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                StatusDot(device.online)
                Column {
                    Text(device.name, style = MaterialTheme.typography.titleMedium)
                    if (device.role.isNotBlank()) {
                        Text(device.role, style = MaterialTheme.typography.bodySmall)
                    }
                }
            }
            val chips = healthChips(device) + listOfNotNull(usbChip(device))
            if (device.online && chips.isNotEmpty()) ChipRow(chips)
        }
    }
}

@OptIn(ExperimentalLayoutApi::class, ExperimentalMaterial3Api::class)
@Composable
internal fun ChipRow(labels: List<String>) {
    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        labels.forEach { AssistChip(onClick = {}, label = { Text(it) }) }
    }
}
