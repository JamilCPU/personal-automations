package com.jamilcpu.personalautomations

import android.content.Intent
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.lifecycle.lifecycleScope
import com.jamilcpu.personalautomations.data.HubSettings
import com.jamilcpu.personalautomations.data.SettingsStore
import com.jamilcpu.personalautomations.data.parsePairUri
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Settings
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController

const val UNPAIRED_TEXT =
    "Not paired yet. Run `pa-hub pair` on the PC and scan the QR code with the Pixel camera."

class MainActivity : ComponentActivity() {
    private val settingsStore by lazy { SettingsStore(applicationContext) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // A launch from a pa://pair link is handled once; a recreation must not re-pair.
        if (savedInstanceState == null) handlePairIntent(intent)
        setContent {
            MaterialTheme {
                val settings by remember { settingsStore.settings }.collectAsState(initial = null)
                AppShell(settings)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        setIntent(intent)
        handlePairIntent(intent)
    }

    private fun handlePairIntent(intent: Intent?) {
        val data = intent?.data ?: return
        if (data.scheme != "pa") return
        val parsed = parsePairUri(data.toString())
        if (parsed == null) {
            Toast.makeText(this, "Invalid pairing link", Toast.LENGTH_SHORT).show()
            return
        }
        lifecycleScope.launch {
            settingsStore.save(parsed)
            Toast.makeText(this@MainActivity, "Paired with hub", Toast.LENGTH_SHORT).show()
        }
    }
}

@Composable
fun AppShell(settings: HubSettings? = null) {
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route

    Scaffold(
        bottomBar = {
            NavigationBar {
                NAV_TABS.forEach { tab ->
                    val route = routeFor(tab)
                    NavigationBarItem(
                        selected = currentRoute == route,
                        onClick = {
                            navController.navigate(route) {
                                popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                                launchSingleTop = true
                                restoreState = true
                            }
                        },
                        icon = { Icon(tabIcon(tab), contentDescription = tab) },
                        label = { Text(tab) },
                    )
                }
            }
        },
    ) { padding ->
        NavHost(
            navController = navController,
            startDestination = routeFor(NAV_TABS.first()),
            modifier = Modifier.padding(padding),
        ) {
            composable(routeFor("Devices")) {
                Placeholder(if (settings == null) UNPAIRED_TEXT else "Devices")
            }
            composable(routeFor("Programs")) {
                Placeholder(if (settings == null) UNPAIRED_TEXT else "Programs")
            }
        }
    }
}

private fun tabIcon(tab: String): ImageVector = Icons.Filled.Settings

@Composable
private fun Placeholder(title: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(title)
    }
}
