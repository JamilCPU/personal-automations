package com.jamilcpu.personalautomations.data

import java.net.URI
import java.net.URLDecoder

data class HubSettings(val url: String, val token: String, val fingerprint: String)

private const val FINGERPRINT_HEX_LEN = 64
private val HEX_64 = Regex("^[0-9a-fA-F]{$FINGERPRINT_HEX_LEN}$")

/**
 * Parses `pa://pair?url=<url>&token=<token>&fp=<sha256>` (every value percent-encoded).
 * Returns null for any other scheme/host or when a field is missing or the fingerprint is not 64 hex.
 */
fun parsePairUri(uri: String): HubSettings? {
    val parsed = try {
        URI(uri)
    } catch (_: Exception) {
        return null
    }
    if (parsed.scheme != "pa" || parsed.host != "pair") return null
    val query = parsed.rawQuery ?: return null
    val params = mutableMapOf<String, String>()
    for (pair in query.split('&')) {
        val i = pair.indexOf('=')
        if (i <= 0) continue
        val key = decode(pair.substring(0, i)) ?: return null
        params[key] = decode(pair.substring(i + 1)) ?: return null
    }
    val url = params["url"]?.takeIf { it.startsWith("https://") } ?: return null
    val token = params["token"]?.takeIf { it.isNotBlank() } ?: return null
    val fp = params["fp"]?.takeIf { HEX_64.matches(it) } ?: return null
    return HubSettings(url, token, fp.lowercase())
}

private fun decode(s: String): String? =
    try {
        URLDecoder.decode(s, "UTF-8")
    } catch (_: IllegalArgumentException) {
        null
    }
