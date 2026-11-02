"""Self-signed certificate helpers for LAN HTTPS with pinned fingerprints."""

import datetime
import ipaddress

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

DEFAULT_VALID_DAYS = 3650


def make_self_signed(
    common_name: str, ips: list[str], days: int = DEFAULT_VALID_DAYS
) -> tuple[bytes, bytes]:
    """Return (cert PEM, private key PEM). The SAN lists every IP plus the common name."""
    key = ec.generate_private_key(ec.SECP256R1())
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)])
    now = datetime.datetime.now(datetime.UTC)
    san = x509.SubjectAlternativeName(
        [
            x509.DNSName(common_name),
            *(x509.IPAddress(ipaddress.ip_address(ip)) for ip in ips),
        ]
    )
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))
        .not_valid_after(now + datetime.timedelta(days=days))
        .add_extension(san, critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .sign(key, hashes.SHA256())
    )
    cert_pem = cert.public_bytes(serialization.Encoding.PEM)
    key_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return cert_pem, key_pem


def fingerprint_sha256(cert_pem: bytes) -> str:
    """Lowercase hex SHA-256 over the certificate DER bytes, no colons."""
    cert = x509.load_pem_x509_certificate(cert_pem)
    return cert.fingerprint(hashes.SHA256()).hex()
