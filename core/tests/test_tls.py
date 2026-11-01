import ipaddress

from cryptography import x509
from pa_core.tls import fingerprint_sha256, make_self_signed


def test_tls_san_and_fingerprint():
    cert_pem, key_pem = make_self_signed("pi1", ["192.0.2.101", "127.0.0.1"])
    cert = x509.load_pem_x509_certificate(cert_pem)
    san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert ipaddress.ip_address("192.0.2.101") in san.get_values_for_type(x509.IPAddress)
    assert ipaddress.ip_address("127.0.0.1") in san.get_values_for_type(x509.IPAddress)
    assert "pi1" in san.get_values_for_type(x509.DNSName)
    assert b"PRIVATE KEY" in key_pem
    fp = fingerprint_sha256(cert_pem)
    assert len(fp) == 64
    assert fp == fp.lower()
    assert all(c in "0123456789abcdef" for c in fp)


def test_fingerprint_differs_per_cert():
    a, _ = make_self_signed("a", ["10.0.0.1"])
    b, _ = make_self_signed("a", ["10.0.0.1"])
    assert fingerprint_sha256(a) != fingerprint_sha256(b)
