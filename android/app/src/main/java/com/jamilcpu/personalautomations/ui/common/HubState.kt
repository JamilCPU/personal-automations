package com.jamilcpu.personalautomations.ui.common

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay

const val HUB_UNREACHABLE = "Can't reach the hub"

/** Persistent banner shown while the hub is unreachable but older data is still on screen. */
@Composable
fun HubBanner(modifier: Modifier = Modifier) {
    Surface(modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.errorContainer) {
        Text(
            HUB_UNREACHABLE,
            Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            color = MaterialTheme.colorScheme.onErrorContainer,
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

/** Full-area state while nothing has loaded: a spinner, or the error message with a Retry button. */
@Composable
fun LoadState(error: String?, onRetry: () -> Unit, modifier: Modifier = Modifier) {
    Box(modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        if (error == null) {
            CircularProgressIndicator()
        } else {
            Column(
                Modifier.padding(24.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
                verticalArrangement = Arrangement.spacedBy(12.dp),
            ) {
                Text(error, style = MaterialTheme.typography.bodyLarge)
                Button(onClick = onRetry) { Text("Retry") }
            }
        }
    }
}

private const val REFRESH_INDICATOR_MS = 700L

/** Pull-to-refresh state: (isRefreshing, trigger). The indicator shows briefly after each pull. */
@Composable
fun rememberRefreshing(onRefresh: () -> Unit): Pair<Boolean, () -> Unit> {
    var refreshing by remember { mutableStateOf(false) }
    LaunchedEffect(refreshing) {
        if (refreshing) {
            delay(REFRESH_INDICATOR_MS)
            refreshing = false
        }
    }
    return refreshing to {
        refreshing = true
        onRefresh()
    }
}
