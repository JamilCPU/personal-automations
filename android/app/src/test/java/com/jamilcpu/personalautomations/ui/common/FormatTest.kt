package com.jamilcpu.personalautomations.ui.common

import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Test

class FormatTest {
    private val zone = ZoneId.of("UTC")
    private val now = ZonedDateTime.of(2026, 11, 1, 12, 0, 0, 0, zone).toInstant()

    @Test fun uptime_formats() {
        assertEquals("2d 4h", formatUptime(2 * 86_400L + 4 * 3_600L))
        assertEquals("5h 12m", formatUptime(5 * 3_600L + 12 * 60L))
        assertEquals("7m", formatUptime(7 * 60L))
    }

    @Test fun just_now() = assertEquals("just now", relativeTime("2026-11-01T11:59:40Z", now, zone))

    @Test fun minutes() = assertEquals("2 min ago", relativeTime("2026-11-01T11:58:00Z", now, zone))

    @Test fun hours_same_day() = assertEquals("3 h ago", relativeTime("2026-11-01T09:00:00Z", now, zone))

    @Test fun yesterday() = assertEquals("yesterday 21:14", relativeTime("2026-10-31T21:14:00Z", now, zone))

    @Test fun older() = assertEquals("28 Oct 08:05", relativeTime("2026-10-28T08:05:00Z", now, zone))

    @Test fun offset_timestamps_parse() =
        assertEquals("2 min ago", relativeTime("2026-11-01T13:58:00+02:00", now, zone))

    @Test fun garbage_is_returned_unchanged() = assertEquals("whenever", relativeTime("whenever", now, zone))

    @Test fun future_clamps_to_just_now() = assertEquals("just now", relativeTime("2026-11-01T12:05:00Z", now, zone))
}
