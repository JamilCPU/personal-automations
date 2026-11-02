package com.jamilcpu.personalautomations.data

import java.io.InputStream
import java.security.MessageDigest
import java.security.cert.CertificateException
import java.security.cert.CertificateFactory
import java.security.cert.X509Certificate
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Test

class PairingTest {
    private val fp = "ab".repeat(32)

    @Test
    fun validUriParses() {
        val s = parsePairUri("pa://pair?url=https%3A%2F%2F192.168.0.99%3A7700&token=tok&fp=$fp")
        assertEquals(HubSettings("https://192.168.0.99:7700", "tok", fp), s)
    }

    @Test
    fun wrongSchemeOrHostIsNull() {
        assertNull(parsePairUri("https://pair?url=x&token=t&fp=$fp"))
        assertNull(parsePairUri("pa://other?url=https%3A%2F%2Fh&token=t&fp=$fp"))
    }

    @Test
    fun shortOrNonHexFingerprintIsNull() {
        assertNull(parsePairUri("pa://pair?url=https%3A%2F%2Fh&token=t&fp=abcd"))
        assertNull(parsePairUri("pa://pair?url=https%3A%2F%2Fh&token=t&fp=${"zz".repeat(32)}"))
    }

    @Test
    fun nonHttpsUrlIsNull() {
        assertNull(parsePairUri("pa://pair?url=http%3A%2F%2Fh%3A7700&token=t&fp=$fp"))
        assertNull(parsePairUri("pa://pair?url=192.168.0.99&token=t&fp=$fp"))
    }

    @Test
    fun missingFieldIsNull() {
        assertNull(parsePairUri("pa://pair?url=https%3A%2F%2Fh&fp=$fp"))
        assertNull(parsePairUri("pa://pair?token=t&fp=$fp"))
        assertNull(parsePairUri("not a uri"))
    }

    @Test
    fun urlEncodedTokenIsDecoded() {
        val s = parsePairUri("pa://pair?url=https%3A%2F%2Fh%3A7700&token=a%2Bb%2Fc%3D%20d&fp=${fp.uppercase()}")
        assertEquals("a+b/c= d", s?.token)
    }

    // --- pinned trust manager ---

    private fun cert(name: String): X509Certificate {
        val stream: InputStream = javaClass.classLoader!!.getResourceAsStream(name)!!
        return stream.use { CertificateFactory.getInstance("X.509").generateCertificate(it) as X509Certificate }
    }

    private fun sha256Hex(c: X509Certificate): String =
        MessageDigest.getInstance("SHA-256").digest(c.encoded).joinToString("") { "%02x".format(it) }

    @Test
    fun trustManagerAcceptsMatchingFingerprintCaseInsensitive() {
        val c = cert("test-cert.crt")
        PinnedTrustManager(sha256Hex(c)).checkServerTrusted(arrayOf(c), "RSA")
        PinnedTrustManager(sha256Hex(c).uppercase()).checkServerTrusted(arrayOf(c), "RSA")
        assertEquals(sha256Hex(c), certFingerprint(c))
    }

    @Test
    fun trustManagerRejectsOtherCertAndEmptyChain() {
        val pinned = PinnedTrustManager(sha256Hex(cert("test-cert.crt")))
        assertThrows(CertificateException::class.java) {
            pinned.checkServerTrusted(arrayOf(cert("other-cert.crt")), "RSA")
        }
        assertThrows(CertificateException::class.java) { pinned.checkServerTrusted(emptyArray(), "RSA") }
        assertThrows(CertificateException::class.java) { pinned.checkServerTrusted(null, "RSA") }
    }

    @Test
    fun pinnedClientBuilds() {
        assertNotNull(pinnedOkHttp(fp))
    }
}
