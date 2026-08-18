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

from telemetry.models import SensorReading

from .aqi import compute_air_quality_index
from .models import EnrollmentToken, Probe
from .views import PUBLIC_SUMMARY_SENSOR_TYPES


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
        ssh_pubkey_path = tmp_path / "id_ed25519.pub"
        ssh_pubkey_path.write_text("ssh-ed25519 AAAAtestkey ubuntu@test\n")

        cls._settings_override = override_settings(
            CA_KEY_PATH=str(ca_key_path),
            CA_CERT_PATH=str(ca_cert_path),
            WIREGUARD_SERVER_PUBLIC_KEY_PATH=str(wg_pubkey_path),
            SERVER_SSH_PUBLIC_KEY_PATH=str(ssh_pubkey_path),
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
        self.assertEqual(body["server_ssh_public_key"], "ssh-ed25519 AAAAtestkey ubuntu@test")

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

    @override_settings(SERVER_SSH_PUBLIC_KEY_PATH="/nonexistent/id_ed25519.pub")
    def test_missing_ssh_key_file_degrades_gracefully(self):
        # A missing/unreadable SSH public key must never fail enrollment
        # itself -- it's a best-effort convenience layered on top.
        raw_token, _ = self._create_token()
        response = self._enroll(raw_token)
        self.assertEqual(response.status_code, 201, response.content)
        self.assertIsNone(response.json()["server_ssh_public_key"])

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


class AirQualityIndexTests(TestCase):
    def test_clean_air_and_ideal_humidity_scores_near_100(self):
        self.assertEqual(compute_air_quality_index(100000, 100000, 40), 100)

    def test_missing_input_returns_none(self):
        self.assertIsNone(compute_air_quality_index(None, 100000, 40))
        self.assertIsNone(compute_air_quality_index(50000, 100000, None))
        self.assertIsNone(compute_air_quality_index(50000, 0, 40))
        self.assertIsNone(compute_air_quality_index(50000, None, 40))

    def test_gas_resistance_above_baseline_is_capped_not_amplified(self):
        # A reading above the rolling baseline (the baseline can lag a
        # sudden improvement in air quality) must not push the score
        # over 100.
        self.assertEqual(
            compute_air_quality_index(200000, 100000, 40),
            compute_air_quality_index(100000, 100000, 40),
        )


class PublicApiTestBase:
    """Shared fixture for the /api/v1/public/* views: same API key
    setting, same notion of an eligible probe.
    """

    def _make_probe(self, **kwargs):
        kwargs.setdefault("name", "garden-station")
        kwargs.setdefault("hardware_type", Probe.HardwareType.RASPBERRY_PI_3)
        kwargs.setdefault("location_latitude", "45.464200")
        kwargs.setdefault("location_longitude", "9.190400")
        return Probe.objects.create(**kwargs)


@override_settings(PUBLIC_SUMMARY_API_KEY="test-summary-key")
class PublicSummaryViewTests(PublicApiTestBase, TestCase):
    def _get(self, api_key="test-summary-key"):
        kwargs = {"headers": {"X-Api-Key": api_key}} if api_key is not None else {}
        return self.client.get(reverse("public-summary"), **kwargs)

    def test_missing_api_key_is_rejected(self):
        response = self._get(api_key=None)
        self.assertEqual(response.status_code, 401)

    def test_wrong_api_key_is_rejected(self):
        response = self._get(api_key="not-the-right-key")
        self.assertEqual(response.status_code, 401)

    @override_settings(PUBLIC_SUMMARY_API_KEY="")
    def test_unconfigured_key_always_rejects(self):
        # Even an empty header must not match an empty configured key --
        # an unset key means the endpoint is disabled, not "open".
        response = self._get(api_key="")
        self.assertEqual(response.status_code, 401)

    def test_probe_without_coordinates_is_excluded(self):
        self._make_probe(location_latitude=None, location_longitude=None)
        response = self._get()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["probes"], [])

    def test_inactive_probe_is_excluded(self):
        self._make_probe(is_active=False)
        response = self._get()
        self.assertEqual(response.json()["probes"], [])

    def test_coordinates_are_rounded_and_contact_fields_never_included(self):
        probe = self._make_probe(
            owner_email="owner@example.com",
            owner_phone="+39 000 000 0000",
            location_address="Via Roma 1, Milano",
        )
        response = self._get()
        body = response.json()["probes"][0]

        self.assertEqual(body["latitude"], 45.46)
        self.assertEqual(body["longitude"], 9.19)
        for leaked_field in ("owner_email", "owner_phone", "location_address"):
            self.assertNotIn(leaked_field, body)
        self.assertNotIn("owner@example.com", response.content.decode())

    @override_settings(PUBLIC_LOCATION_PRECISION_DECIMALS=1)
    def test_precision_is_configurable(self):
        self._make_probe()
        response = self._get()
        body = response.json()["probes"][0]
        self.assertEqual(body["latitude"], 45.5)
        self.assertEqual(body["longitude"], 9.2)

    def test_latest_readings_and_aqi_are_returned(self):
        probe = self._make_probe()
        now = timezone.now()

        # An older reading that must be superseded by the newer one below.
        SensorReading.objects.create(
            probe=probe, sensor_type="temperature_c", value=10.0, time=now - timedelta(minutes=10)
        )
        SensorReading.objects.create(
            probe=probe, sensor_type="temperature_c", value=22.5, time=now - timedelta(minutes=1)
        )
        SensorReading.objects.create(probe=probe, sensor_type="humidity_pct", value=40, time=now)
        SensorReading.objects.create(probe=probe, sensor_type="gas_resistance_ohm", value=90000, time=now)
        # 7-day baseline: the max over the window, not the latest value.
        SensorReading.objects.create(
            probe=probe, sensor_type="gas_resistance_ohm", value=100000, time=now - timedelta(days=2)
        )

        response = self._get()
        readings = response.json()["probes"][0]["readings"]

        self.assertEqual(readings["temperature_c"], 22.5)
        self.assertEqual(readings["humidity_pct"], 40)
        self.assertEqual(readings["gas_resistance_ohm"], 90000)
        self.assertEqual(readings["air_quality_index"], compute_air_quality_index(90000, 100000, 40))

    def test_readings_older_than_seven_days_do_not_count_toward_gas_baseline(self):
        probe = self._make_probe()
        now = timezone.now()
        SensorReading.objects.create(probe=probe, sensor_type="humidity_pct", value=40, time=now)
        SensorReading.objects.create(probe=probe, sensor_type="gas_resistance_ohm", value=50000, time=now)
        # Far outside the 7-day baseline window -- must not inflate it.
        SensorReading.objects.create(
            probe=probe, sensor_type="gas_resistance_ohm", value=500000, time=now - timedelta(days=30)
        )

        response = self._get()
        readings = response.json()["probes"][0]["readings"]
        self.assertEqual(readings["air_quality_index"], compute_air_quality_index(50000, 50000, 40))


@override_settings(PUBLIC_SUMMARY_API_KEY="test-summary-key")
class PublicHistoryViewTests(PublicApiTestBase, TestCase):
    def _get(self, api_key="test-summary-key", **query):
        kwargs = {"headers": {"X-Api-Key": api_key}} if api_key is not None else {}
        url = reverse("public-history")
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        return self.client.get(url, **kwargs)

    def test_missing_api_key_is_rejected(self):
        response = self._get(api_key=None)
        self.assertEqual(response.status_code, 401)

    def test_probe_without_coordinates_is_excluded(self):
        self._make_probe(location_latitude=None, location_longitude=None)
        response = self._get()
        self.assertEqual(response.json()["probes"], [])

    def test_returns_series_within_window_ordered_by_time(self):
        probe = self._make_probe()
        now = timezone.now()
        SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=20.0, time=now - timedelta(hours=2))
        SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=21.0, time=now - timedelta(hours=1))
        # Outside the default 24h window.
        SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=99.0, time=now - timedelta(hours=48))

        response = self._get()
        body = response.json()["probes"][0]
        temps = [point["value"] for point in body["series"]["temperature_c"]]
        self.assertEqual(temps, [20.0, 21.0])

    def test_every_configured_sensor_type_present_even_when_empty(self):
        self._make_probe()
        response = self._get()
        series = response.json()["probes"][0]["series"]
        self.assertEqual(set(series.keys()), set(PUBLIC_SUMMARY_SENSOR_TYPES))
        self.assertEqual(series["gas_resistance_ohm"], [])

    def test_hours_param_narrows_the_window(self):
        probe = self._make_probe()
        now = timezone.now()
        SensorReading.objects.create(probe=probe, sensor_type="temperature_c", value=20.0, time=now - timedelta(hours=5))

        response = self._get(hours=1)
        self.assertEqual(response.json()["probes"][0]["series"]["temperature_c"], [])
        self.assertEqual(response.json()["window_hours"], 1)

    def test_hours_param_is_capped(self):
        response = self._get(hours=99999)
        self.assertEqual(response.json()["window_hours"], 168)

    def test_invalid_hours_param_falls_back_to_default(self):
        response = self._get(hours="not-a-number")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["window_hours"], 24)
