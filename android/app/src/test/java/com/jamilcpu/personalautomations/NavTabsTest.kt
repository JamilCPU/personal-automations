package com.jamilcpu.personalautomations

import org.junit.Assert.assertEquals
import org.junit.Test

class NavTabsTest {
    @Test
    fun tabsAreDevicesThenPrograms() {
        assertEquals(listOf("Devices", "Programs"), NAV_TABS)
    }

    @Test
    fun routesAreLowercaseTabNames() {
        assertEquals(listOf("devices", "programs"), NAV_TABS.map(::routeFor))
    }
}
