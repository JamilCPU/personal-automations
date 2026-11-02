package com.jamilcpu.personalautomations.ui.common

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp

private val Green = Color(0xFF2E9E5B)
private val Grey = Color(0xFF9E9E9E)

/** Green when [online], grey otherwise. */
@Composable
fun StatusDot(online: Boolean, modifier: Modifier = Modifier, size: Dp = 10.dp) {
    Box(
        modifier
            .size(size)
            .background(if (online) Green else Grey, CircleShape)
            .semantics { contentDescription = if (online) "Online" else "Offline" },
    )
}
