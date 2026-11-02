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
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.TextButton
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import androidx.compose.runtime.getValue
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.lifecycle.compose.LifecycleResumeEffect
import androidx.lifecycle.viewmodel.compose.viewModel
import com.jamilcpu.personalautomations.data.HubApi
import com.jamilcpu.personalautomations.data.buildPinnedHubClient
import com.jamilcpu.personalautomations.data.UiPrefsStore
import com.jamilcpu.personalautomations.ui.devices.DeviceScreen
import com.jamilcpu.personalautomations.ui.programs.ProgramDetailScreen
import com.jamilcpu.personalautomations.ui.programs.ProgramDetailViewModel
import com.jamilcpu.personalautomations.ui.programs.ProgramDetailViewModelFactory
import com.jamilcpu.personalautomations.ui.programs.ProgramsScreen
import com.jamilcpu.personalautomations.ui.programs.ProgramsViewModel
import com.jamilcpu.personalautomations.ui.programs.ProgramsViewModelFactory
import android.net.Uri
import androidx.compose.ui.platform.LocalContext
import androidx.navigation.NavType
import androidx.navigation.navArgument
import com.jamilcpu.personalautomations.ui.devices.DevicesScreen
import com.jamilcpu.personalautomations.ui.devices.DevicesViewModel
import com.jamilcpu.personalautomations.ui.devices.DevicesViewModelFactory
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
    private var pendingPair by mutableStateOf<HubSettings?>(null)

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // A launch from a pa://pair link is handled once; a recreation must not re-pair.
        if (savedInstanceState == null) handlePairIntent(intent)
        setContent {
            MaterialTheme {
                val state by remember { settingsStore.settings.map { SettingsState(true, it) } }
                    .collectAsState(initial = SettingsState(false, null))
                AppShell(state.settings, loaded = state.loaded)
                pendingPair?.let { candidate ->
                    AlertDialog(
                        onDismissRequest = { pendingPair = null },
                        title = { Text("Replace the current hub pairing?") },
                        confirmButton = {
                            TextButton(onClick = { pendingPair = null; savePairing(candidate) }) { Text("Replace") }
                        },
                        dismissButton = { TextButton(onClick = { pendingPair = null }) { Text("Cancel") } },
                    )
                }
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
            if (settingsStore.settings.first() == null) savePairing(parsed) else pendingPair = parsed
        }
    }

    private fun savePairing(s: HubSettings) {
        lifecycleScope.launch {
            settingsStore.save(s)
            Toast.makeText(this@MainActivity, "Paired with hub", Toast.LENGTH_SHORT).show()
        }
    }
}

@Composable
fun AppShell(settings: HubSettings? = null, loaded: Boolean = true) {
    val navController = rememberNavController()
    val backStack by navController.currentBackStackEntryAsState()
    val currentRoute = backStack?.destination?.route
    val snackbar = remember { SnackbarHostState() }
    var programsDevice by remember { mutableStateOf<String?>(null) }

    val api = remember(settings) { settings?.let { HubApi(buildPinnedHubClient(it), it) } }
    // One view model per pairing; the client is rebuilt only when the saved settings change.
    val devicesVm: DevicesViewModel? = settings?.let { s ->
        viewModel(key = "devices-${s.hashCode()}", factory = DevicesViewModelFactory(api!!))
    }
    val devicesState = devicesVm?.state?.collectAsState()?.value
    val context = LocalContext.current
    val programsVm: ProgramsViewModel? = api?.let { client ->
        viewModel(
            key = "programs-${settings.hashCode()}",
            factory = ProgramsViewModelFactory(client, UiPrefsStore(context)),
        )
    }
    val programsState = programsVm?.state?.collectAsState()?.value
    val onPrograms = currentRoute == routeFor("Programs")
    val onDevices = currentRoute == routeFor("devices") || currentRoute == DEVICE_ROUTE

    // Poll only while a devices screen is on screen and the app is resumed.
    if (devicesVm != null) {
        LifecycleResumeEffect(devicesVm, onDevices) {
            if (onDevices) devicesVm.start()
            onPauseOrDispose { devicesVm.stop() }
        }
        LaunchedEffect(devicesState?.error) {
            devicesState?.error?.let {
                snackbar.showSnackbar(it)
                devicesVm.clearError()
            }
        }
    }

    if (programsVm != null) {
        LifecycleResumeEffect(programsVm, onPrograms) {
            if (onPrograms) programsVm.start()
            onPauseOrDispose { programsVm.stop() }
        }
        LaunchedEffect(programsDevice) {
            // Consume the preset so tapping the same device link again re-applies it.
            programsDevice?.let { programsVm.setFilter(it); programsDevice = null }
        }
    }

    Scaffold(
        snackbarHost = { SnackbarHost(snackbar) },
        bottomBar = {
            NavigationBar {
                NAV_TABS.forEach { tab ->
                    val route = routeFor(tab)
                    NavigationBarItem(
                        selected = currentRoute == route ||
                            (tab == "Devices" && currentRoute == DEVICE_ROUTE) ||
                            (tab == "Programs" && currentRoute == PROGRAM_ROUTE),
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
                if (devicesVm == null || devicesState == null) {
                    Placeholder(placeholderText(settings, loaded, "Devices"))
                } else {
                    DevicesScreen(
                        devicesState,
                        onOpenDevice = { navController.navigate("device/$it") },
                        onRetry = devicesVm::retry,
                    )
                }
            }
            composable(DEVICE_ROUTE) { entry ->
                val id = entry.arguments?.getString("id")
                if (devicesVm != null && devicesState != null && id != null) {
                    DeviceScreen(
                        deviceId = id,
                        state = devicesState,
                        viewModel = devicesVm,
                        onOpenPrograms = {
                            programsDevice = it
                            navController.navigate(routeFor("Programs")) { launchSingleTop = true }
                        },
                    )
                }
            }
            composable(routeFor("Programs")) {
                if (programsVm == null || programsState == null) {
                    Placeholder(placeholderText(settings, loaded, "Programs"))
                } else {
                    ProgramsScreen(
                        state = programsState,
                        onFilter = programsVm::setFilter,
                        onHideSystem = programsVm::setHideSystem,
                        onRefresh = programsVm::refresh,
                        onOpenProgram = { device, id ->
                            navController.navigate("program/${Uri.encode(device)}/${Uri.encode(id)}")
                        },
                    )
                }
            }
            composable(
                PROGRAM_ROUTE,
                arguments = listOf(
                    navArgument("device") { type = NavType.StringType },
                    navArgument("id") { type = NavType.StringType },
                ),
            ) { entry ->
                // Nav decodes route arguments; ids contain ':' '/' '@' '#' so they were Uri-encoded above.
                val device = entry.arguments?.getString("device")
                val id = entry.arguments?.getString("id")
                if (api != null && device != null && id != null) {
                    ProgramDetailRoute(api, device, id)
                }
            }
        }
    }
}

@Composable
private fun ProgramDetailRoute(api: HubApi, device: String, id: String) {
    val vm: ProgramDetailViewModel = viewModel(
        key = "program-$device-$id",
        factory = ProgramDetailViewModelFactory(api, device, id),
    )
    val state by vm.state.collectAsState()
    LaunchedEffect(vm) { vm.load() }
    ProgramDetailScreen(
        state = state,
        visibleActions = vm.visibleActions(),
        locked = vm.isLocked,
        needsSecondConfirm = vm.needsSecondConfirm,
        onAct = vm::act,
        onRefresh = vm::load,
        onRefreshLogs = vm::refreshLogs,
    )
}

private const val DEVICE_ROUTE = "device/{id}"
private const val PROGRAM_ROUTE = "program/{device}/{id}"

private data class SettingsState(val loaded: Boolean, val settings: HubSettings?)

private fun placeholderText(settings: HubSettings?, loaded: Boolean, title: String): String =
    when {
        !loaded -> ""
        settings == null -> UNPAIRED_TEXT
        else -> title
    }

private fun tabIcon(tab: String): ImageVector = Icons.Filled.Settings

@Composable
private fun Placeholder(title: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Text(title)
    }
}
