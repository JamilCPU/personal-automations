package com.jamilcpu.personalautomations.data

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** Per-user display preferences. Behind an interface so view models can take a fake. */
interface UiPrefs {
    /** Whether system programs are hidden on the Programs tab (default true). */
    val hideSystem: Flow<Boolean>

    suspend fun setHideSystem(value: Boolean)
}

private val Context.uiDataStore: DataStore<Preferences> by preferencesDataStore(name = "ui_prefs")

private val HIDE_SYSTEM_KEY = booleanPreferencesKey("hide_system")

class UiPrefsStore(context: Context) : UiPrefs {
    private val store = context.applicationContext.uiDataStore

    override val hideSystem: Flow<Boolean> = store.data.map { it[HIDE_SYSTEM_KEY] ?: true }

    override suspend fun setHideSystem(value: Boolean) {
        store.edit { it[HIDE_SYSTEM_KEY] = value }
    }
}
