package com.jamilcpu.personalautomations.ui.common

import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.graphics.vector.addPathNodes
import androidx.compose.ui.unit.dp

// Material "push_pin" (Apache 2.0); the extended icon set is too large to pull in for two icons.
private const val PIN_FILLED =
    "M16,9V4l1,0c0.55,0 1,-0.45 1,-1v0c0,-0.55 -0.45,-1 -1,-1H7C6.45,2 6,2.45 6,3v0" +
        "c0,0.55 0.45,1 1,1l1,0v5c0,1.66 -1.34,3 -3,3h0v2h5.97v7l1,1l1,-1v-7H19v-2h0" +
        "C17.34,12 16,10.66 16,9z"
private const val PIN_OUTLINED =
    "M14,4v5c0,1.12 0.37,2.16 1,3H9c0.65,-0.86 1,-1.9 1,-3V4H14M17,2H7C6.45,2 6,2.45 6,3" +
        "c0,0.55 0.45,1 1,1l1,0v5c0,1.66 -1.34,3 -3,3v2h5.97v7l1,1l1,-1v-7H19v-2" +
        "c-1.66,0 -3,-1.34 -3,-3V4l1,0c0.55,0 1,-0.45 1,-1C18,2.45 17.55,2 17,2L17,2z"

private fun pinVector(name: String, path: String): ImageVector =
    ImageVector.Builder(name, 24.dp, 24.dp, 24f, 24f)
        .addPath(pathData = addPathNodes(path), fill = SolidColor(Color.Black))
        .build()

private val PinFilled: ImageVector by lazy { pinVector("PinFilled", PIN_FILLED) }
private val PinOutlined: ImageVector by lazy { pinVector("PinOutlined", PIN_OUTLINED) }

/** Pin toggle: filled when pinned. */
@Composable
fun PinButton(pinned: Boolean, enabled: Boolean, onToggle: () -> Unit) {
    IconButton(onClick = onToggle, enabled = enabled) {
        Icon(
            if (pinned) PinFilled else PinOutlined,
            contentDescription = if (pinned) "Unpin" else "Pin",
        )
    }
}
