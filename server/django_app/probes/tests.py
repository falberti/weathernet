import hashlib
import tempfile
import threading
from datetime import timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from django.test import TestCase, TransactionTestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import EnrollmentToken, Probe


def _generate_csr_pem(common_name="ignored-by-server"):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    csr = (
        x509.CertificateSigningRequestBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, common_name)]))
        .sign(key, hashes.SHA256())
    )
    return csr.public_bytes(serialization.Encoding.PEM).decode()


def _build_test_ca(directory: Path):
    """Write a throwaway CA (key + cert) to `directory`, matching the
    files probes/ca.py expects to be bind-mounted in production."""
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test CA")])
    now = timezone.now()
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )

    key_path = directory / "ca.key.pem"
    cert_path = directory / "ca.cert.pem"
    key_path.write_bytes(
        ca_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    cert_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    return key_path, cert_path


class EnrollmentTestBase:
    """Shared fixture: a throwaway CA and WireGuard server public key on
    disk, with settings overridden to point at them -- the same files
    probes/ca.py and probes/wireguard.py expect to find bind-mounted in
    production (see docker-compose.yml).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls._tmpdir = tempfile.TemporaryDirectory()
        tmp_path = Path(cls._tmpdir.name)
        ca_key_path, ca_cert_path = _build_test_ca(tmp_path)
        wg_pubkey_path = tmp_path / "server_public.key"
        wg_pubkey_path.write_text("test-server-wg-pubkey=\n")

        cls._settings_override = override_settings(
            CA_KEY_PATH=str(ca_key_path),
            CA_CERT_PATH=str(ca_cert_path),
            WIREGUARD_SERVER_PUBLIC_KEY_PATH=str(wg_pubkey_path),
            WIREGUARD_SUBNET="10.10.0.0/24",
            SERVER_PUBLIC_IP="203.0.113.10",
            WIREGUARD_LISTEN_PORT=51820,
        )
        cls._settings_override.enable()

    @classmethod
    def tearDownClass(cls):
        cls._settings_override.disable()
        cls._tmpdir.cleanup()
        super().tearDownClass()

    def _create_token(self, raw_token="test-token-value", **kwargs):
        token = EnrollmentToken.objects.create(
            token_hash=hashlib.sha256(raw_token.encode()).hexdigest(),
            probe_name=kwargs.pop("probe_name", "test-probe"),
            hardware_type=kwargs.pop("hardware_type", Probe.HardwareType.RASPBERRY_PI_3),
            **kwargs,
        )
        return raw_token, token

    def _enroll(self, raw_token, **overrides):
        payload = {
            "token": raw_token,
            "csr_pem": _generate_csr_pem(),
            "wireguard_public_key": "probe-wg-pubkey-AAAAAAAAAAAAAAAAAAAAAAAA=",
            "detected_hardware_type": "raspberry_pi_3",
        }
        payload.update(overrides)
        return self.client.post(reverse("enroll"), data=payload, content_type="application/json")


class EnrollViewTests(EnrollmentTestBase, TestCase):
    def test_valid_token_enrolls_a_probe(self):
        raw_token, token = self._create_token(probe_name="garden-station", hardware_type=Probe.HardwareType.RASPBERRY_PI_4)

        response = self._enroll(raw_token)

        self.assertEqual(response.status_code, 201, response.content)
        body = response.json()
        self.assertIn("client_cert_pem", body)
        self.assertIn("BEGIN CERTIFICATE", body["client_cert_pem"])
        self.assertIn("ca_cert_pem", body)
        self.assertEqual(body["wireguard"]["tunnel_ip"], "10.10.0.2")
        self.assertEqual(body["wireguard"]["server_tunnel_ip"], "10.10.0.1")
        self.assertEqual(body["wireguard"]["server_public_key"], "test-server-wg-pubkey=")

        probe = Probe.objects.get(id=body["probe_id"])
        self.assertEqual(probe.name, "garden-station")
        self.assertEqual(probe.hardware_type, Probe.HardwareType.RASPBERRY_PI_4)
        self.assertEqual(probe.wireguard_tunnel_ip, "10.10.0.2")

        token.refresh_from_db()
        self.assertIsNotNone(token.used_at)
        self.assertEqual(token.resulting_probe, probe)

        # The certificate's CN must be the assigned probe UUID, not
        # whatever the CSR itself requested.
        cert = x509.load_pem_x509_certificate(body["client_cert_pem"].encode())
        cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)[0].value
        self.assertEqual(cn, str(probe.id))

    def test_unknown_token_is_rejected(self):
        response = self._enroll("not-a-real-token")
        self.assertEqual(response.status_code, 404)

    def test_expired_token_is_rejected(self):
        raw_token, _ = self._create_token(expires_at=timezone.now() - timedelta(minutes=1))
        response = self._enroll(raw_token)
        self.assertEqual(response.status_code, 410)

    def test_already_used_token_is_rejected(self):
        raw_token, token = self._create_token()
        token.used_at = timezone.now()
        token.save(update_fields=["used_at"])

        response = self._enroll(raw_token)
        self.assertEqual(response.status_code, 410)

    def test_malformed_csr_is_rejected_and_token_stays_usable(self):
        raw_token, token = self._create_token()

        response = self._enroll(raw_token, csr_pem="not a real CSR")
        self.assertEqual(response.status_code, 400)

        token.refresh_from_db()
        self.assertIsNone(token.used_at)
        self.assertEqual(Probe.objects.count(), 0)

    @override_settings(WIREGUARD_SUBNET="10.10.0.0/30")
    def test_exhausted_wireguard_subnet_is_rejected(self):
        # A /30 has exactly two host addresses; the first is reserved
        # for the server, leaving exactly one for a probe.
        raw_token_1, _ = self._create_token(raw_token="token-1")
        response_1 = self._enroll(raw_token_1)
        self.assertEqual(response_1.status_code, 201, response_1.content)

        raw_token_2, token_2 = self._create_token(raw_token="token-2")
        response_2 = self._enroll(raw_token_2)
        self.assertEqual(response_2.status_code, 409)

        token_2.refresh_from_db()
        self.assertIsNone(token_2.used_at)


class EnrollViewRaceConditionTests(EnrollmentTestBase, TransactionTestCase):
    """Proves select_for_update() actually prevents double-redemption
    under a real race, not just in the easy sequential case above.
    """

    def test_concurrent_redemption_of_the_same_token_only_succeeds_once(self):
        raw_token, _ = self._create_token()

        results = []

        def redeem():
            from django.db import connection
            from django.test import Client

            try:
                response = Client().post(
                    reverse("enroll"),
                    data={
                        "token": raw_token,
                        "csr_pem": _generate_csr_pem(),
                        "wireguard_public_key": "probe-wg-pubkey-AAAAAAAAAAAAAAAAAAAAAAAA=",
                    },
                    content_type="application/json",
                )
                results.append(response.status_code)
            finally:
                # Each thread gets its own DB connection (Django
                # connections are thread-local); it must be closed
                # explicitly or it leaks past the test and blocks
                # tearDownClass's DROP DATABASE.
                connection.close()

        threads = [threading.Thread(target=redeem) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(results.count(201), 1, results)
        self.assertEqual(results.count(410), 4, results)
        self.assertEqual(Probe.objects.count(), 1)
