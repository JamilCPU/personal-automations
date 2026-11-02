package com.jamilcpu.personalautomations.data

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map

/** Encrypts the hub token at rest. Behind an interface so JVM tests can fake the Keystore. */
interface TokenCipher {
    fun encrypt(plain: String): String

    /** Returns null when the value cannot be decrypted (for example the key was lost). */
    fun decrypt(encoded: String): String?
}

/** AES/GCM with a non-exportable key held in the Android Keystore. Output is Base64(iv + ciphertext). */
class KeystoreTokenCipher(private val alias: String = "pa_hub_token_key") : TokenCipher {
    private fun key(): SecretKey {
        val ks = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        (ks.getKey(alias, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)
        generator.init(
            KeyGenParameterSpec.Builder(
                alias,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(KEY_BITS)
                .build(),
        )
        return generator.generateKey()
    }

    override fun encrypt(plain: String): String {
        val cipher = Cipher.getInstance(TRANSFORMATION).apply { init(Cipher.ENCRYPT_MODE, key()) }
        val out = cipher.iv + cipher.doFinal(plain.toByteArray(Charsets.UTF_8))
        return Base64.encodeToString(out, Base64.NO_WRAP)
    }

    override fun decrypt(encoded: String): String? =
        try {
            val bytes = Base64.decode(encoded, Base64.NO_WRAP)
            val cipher = Cipher.getInstance(TRANSFORMATION).apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(TAG_BITS, bytes, 0, IV_BYTES))
            }
            String(cipher.doFinal(bytes, IV_BYTES, bytes.size - IV_BYTES), Charsets.UTF_8)
        } catch (_: Exception) {
            null
        }

    private companion object {
        const val ANDROID_KEYSTORE = "AndroidKeyStore"
        const val TRANSFORMATION = "AES/GCM/NoPadding"
        const val KEY_BITS = 256
        const val TAG_BITS = 128
        const val IV_BYTES = 12
    }
}

private val Context.hubDataStore: DataStore<Preferences> by preferencesDataStore(name = "hub_settings")

private val URL_KEY = stringPreferencesKey("url")
private val TOKEN_KEY = stringPreferencesKey("token_enc")
private val FP_KEY = stringPreferencesKey("fingerprint")

class SettingsStore(
    context: Context,
    private val cipher: TokenCipher = KeystoreTokenCipher(),
) {
    private val store = context.applicationContext.hubDataStore

    val settings: Flow<HubSettings?> = store.data.map { prefs ->
        val url = prefs[URL_KEY]
        val fp = prefs[FP_KEY]
        val token = prefs[TOKEN_KEY]?.let(cipher::decrypt)
        if (url == null || fp == null || token == null) null else HubSettings(url, token, fp)
    }

    suspend fun save(s: HubSettings) {
        val encrypted = cipher.encrypt(s.token)
        store.edit {
            it[URL_KEY] = s.url
            it[TOKEN_KEY] = encrypted
            it[FP_KEY] = s.fingerprint
        }
    }
}
