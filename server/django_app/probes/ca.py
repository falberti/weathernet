"""CSR signing helpers for zero-touch probe enrollment.

Loads the internal CA's key and certificate from the files bind-mounted
read-only into this container (see config/settings.py CA_KEY_PATH /
CA_CERT_PATH and docker-compose.yml) and uses them to sign a
probe-submitted CSR, forcing the certificate's CN to the newly-assigned
Probe UUID regardless of what the CSR itself requested -- the server is
authoritative for identity assignment here, not the probe.
"""
import datetime

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
from django.conf import settings

# ~2.25 years. There's no rotation/renewal flow in v1 (PROJECT_SPEC.md
# Section 12), so this is deliberately long-lived rather than short.
CERT_VALIDITY_DAYS = 825


class CSRSigningError(Exception):
    """Raised when a submitted CSR is malformed or fails a sanity check."""


def _load_ca():
    with open(settings.CA_KEY_PATH, "rb") as f:
        ca_key = serialization.load_pem_private_key(f.read(), password=None)
    with open(settings.CA_CERT_PATH, "rb") as f:
        ca_cert = x509.load_pem_x509_certificate(f.read())
    return ca_key, ca_cert


def sign_probe_csr(csr_pem: str, probe_id) -> str:
    """Sign a probe's CSR, forcing its subject CN to str(probe_id).

    Returns the signed certificate as a PEM string. Raises
    CSRSigningError if csr_pem doesn't parse or its self-signature is
    invalid -- this only catches a corrupted/malformed CSR, it is not
    itself a security boundary (anyone can produce a syntactically
    valid, validly-self-signed CSR for any key they hold). The actual
    identity guarantee comes entirely from forcing the CN to the
    server-generated probe_id below, never from anything the CSR claims
    about itself.
    """
    try:
        csr = x509.load_pem_x509_csr(csr_pem.encode())
    except ValueError as exc:
        raise CSRSigningError(f"could not parse CSR: {exc}") from exc

    if not csr.is_signature_valid:
        raise CSRSigningError("CSR signature is not valid")

    ca_key, ca_cert = _load_ca()

    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, str(probe_id))])
    now = datetime.datetime.now(datetime.timezone.utc)

    builder = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(ca_cert.subject)
        .public_key(csr.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=5))  # clock-skew slack
        .not_valid_after(now + datetime.timedelta(days=CERT_VALIDITY_DAYS))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                content_commitment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.CLIENT_AUTH]), critical=False)
    )

    certificate = builder.sign(private_key=ca_key, algorithm=hashes.SHA256())
    return certificate.public_bytes(serialization.Encoding.PEM).decode()


def ca_cert_pem() -> str:
    with open(settings.CA_CERT_PATH, "rb") as f:
        return f.read().decode()


def server_cert_fingerprint_sha256() -> str:
    """Colon-separated uppercase hex SHA-256 fingerprint of the server's
    TLS certificate -- same format `openssl x509 -fingerprint` prints.
    Used for the probe enrollment script's optional TLS pinning, and
    printed by admin.py when a token is created so the operator never
    has to compute or copy it by hand.
    """
    with open(settings.SERVER_CERT_PATH, "rb") as f:
        cert = x509.load_pem_x509_certificate(f.read())
    digest = cert.fingerprint(hashes.SHA256())
    return ":".join(f"{byte:02X}" for byte in digest)
