package com.jamilcpu.personalautomations.ui.common

import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.time.format.DateTimeParseException
import java.util.Locale

private const val MINUTE = 60L
private const val HOUR = 3_600L
private const val DAY = 86_400L

/** "3d 4h", "5h 12m" or "7m". */
fun formatUptime(seconds: Long): String = when {
    seconds >= DAY -> "${seconds / DAY}d ${seconds % DAY / HOUR}h"
    seconds >= HOUR -> "${seconds / HOUR}h ${seconds % HOUR / MINUTE}m"
    else -> "${seconds / MINUTE}m"
}

private val TIME = DateTimeFormatter.ofPattern("HH:mm", Locale.ENGLISH)
private val DAY_TIME = DateTimeFormatter.ofPattern("d MMM HH:mm", Locale.ENGLISH)

private fun parseInstant(ts: String): Instant? =
    try {
        OffsetDateTime.parse(ts).toInstant()
    } catch (_: DateTimeParseException) {
        try {
            Instant.parse(ts)
        } catch (_: DateTimeParseException) {
            null
        }
    }

/** "just now", "2 min ago", "3 h ago", "yesterday 21:14", else "28 Oct 08:05". Unparseable input is returned as is. */
fun relativeTime(ts: String, now: Instant = Instant.now(), zone: ZoneId = ZoneId.systemDefault()): String {
    val then = parseInstant(ts) ?: return ts
    val age = Duration.between(then, now)
    val local = then.atZone(zone)
    val today = now.atZone(zone).toLocalDate()
    return when {
        age.toMinutes() < 1 -> "just now"
        age.toMinutes() < MINUTE -> "${age.toMinutes()} min ago"
        local.toLocalDate() == today -> "${age.toHours()} h ago"
        local.toLocalDate() == today.minusDays(1) -> "yesterday ${TIME.format(local)}"
        else -> DAY_TIME.format(local)
    }
}

/** Device id to display label; the tag chips and filter chips share it. */
val DEVICE_LABELS: Map<String, String> = linkedMapOf(
    "jw-pc" to "jw PC",
    "robot-pi" to "Robot Pi",
    "pi1" to "Pi1",
    "pi3" to "Pi3",
)

fun deviceLabel(id: String): String = DEVICE_LABELS[id] ?: id
