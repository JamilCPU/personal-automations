package com.jamilcpu.personalautomations.data

import java.security.MessageDigest
import java.security.SecureRandom
import java.security.cert.Certificate
import java.security.cert.CertificateException
import java.security.cert.X509Certificate
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSession
import javax.net.ssl.X509TrustManager
import okhttp3.OkHttpClient

/** Lowercase hex SHA-256 over the certificate's DER encoding. */
fun certFingerprint(cert: Certificate): String =
    MessageDigest.getInstance("SHA-256").digest(cert.encoded).joinToString("") { "%02x".format(it) }

private fun matches(cert: Certificate, expectedHex: String): Boolean =
    MessageDigest.isEqual(
        certFingerprint(cert).toByteArray(),
        expectedHex.lowercase().toByteArray(),
    )

/** Accepts a server chain only when its leaf certificate has the pinned fingerprint. */
class PinnedTrustManager(private val fingerprintHex: String) : X509TrustManager {
    override fun checkServerTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        val leaf = chain?.firstOrNull() ?: throw CertificateException("Empty certificate chain")
        if (!matches(leaf, fingerprintHex)) {
            throw CertificateException("Hub certificate does not match the paired fingerprint")
        }
    }

    override fun checkClientTrusted(chain: Array<out X509Certificate>?, authType: String?) {
        throw CertificateException("Client certificates are not supported")
    }

    override fun getAcceptedIssuers(): Array<X509Certificate> = emptyArray()
}

/** The hub cert is self-signed with IP SANs; trust is the pin, so accept the pinned peer cert. */
class PinnedHostnameVerifier(private val fingerprintHex: String) : HostnameVerifier {
    override fun verify(hostname: String?, session: SSLSession?): Boolean {
        val leaf = try {
            session?.peerCertificates?.firstOrNull()
        } catch (_: Exception) {
            null
        } ?: return false
        return matches(leaf, fingerprintHex)
    }
}

fun pinnedOkHttp(fingerprintHex: String): OkHttpClient {
    val trustManager = PinnedTrustManager(fingerprintHex)
    val context = SSLContext.getInstance("TLS").apply {
        init(null, arrayOf(trustManager), SecureRandom())
    }
    return OkHttpClient.Builder()
        .sslSocketFactory(context.socketFactory, trustManager)
        .hostnameVerifier(PinnedHostnameVerifier(fingerprintHex))
        .build()
}
