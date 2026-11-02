package com.jamilcpu.personalautomations

/** Bottom-bar tab labels, in display order. Route is the lowercase label. */
val NAV_TABS: List<String> = listOf("Devices", "Programs")

fun routeFor(tab: String): String = tab.lowercase()
