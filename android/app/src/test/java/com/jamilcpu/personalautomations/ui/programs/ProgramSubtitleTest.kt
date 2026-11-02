package com.jamilcpu.personalautomations.ui.programs

import org.junit.Assert.assertEquals
import org.junit.Test

class ProgramSubtitleTest {
    @Test fun description_wins() =
        assertEquals("DNS", programSubtitle(prog("a").copy(description = "DNS")))

    @Test fun running_shows_kind_and_uptime() =
        assertEquals("docker · up 2d 4h", programSubtitle(prog("a").copy(uptimeS = 2 * 86_400L + 4 * 3_600L)))

    @Test fun running_without_uptime_shows_kind() = assertEquals("docker", programSubtitle(prog("a")))

    @Test fun stopped_shows_status() = assertEquals("stopped", programSubtitle(prog("a", status = "stopped")))

    @Test fun restarting_shows_status() = assertEquals("restarting", programSubtitle(prog("a", status = "restarting")))
}
