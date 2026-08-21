import uuid

from django.test import TestCase
from django.urls import reverse

from probes.models import Probe

VALID_PAYLOAD = {
    "probe_id": None,
    "timestamp": "2026-08-17T14:32:00Z",
    "readings": [
        {"sensor_type": "temperature_c", "value": 21.4},
        {"sensor_type": "humidity_pct", "value": 55.2},
    ],
    "health": {
        "cpu_temp_c": 48.1,
        "cpu_percent": 12.5,
        "mem_percent": 34.0,
        "disk_percent": 21.0,
        "uptime_seconds": 903421,
    },
}


class IngestViewTests(TestCase):
    def setUp(self):
        self.probe = Probe.objects.create(
            name="garden-station-01",
            hardware_type=Probe.HardwareType.RASPBERRY_PI_3,
        )
        self.url = reverse("ingest")

    def _payload(self, probe_id):
        payload = {**VALID_PAYLOAD, "probe_id": str(probe_id)}
        payload["readings"] = list(VALID_PAYLOAD["readings"])
        payload["health"] = dict(VALID_PAYLOAD["health"])
        return payload

    def post(self, payload, cn):
        return self.client.post(
            self.url,
            data=payload,
            content_type="application/json",
            HTTP_X_CLIENT_CERT_CN=cn,
        )

    def test_valid_payload_is_accepted(self):
        response = self.post(self._payload(self.probe.id), cn=str(self.probe.id))

        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.probe.sensor_readings.count(), 2)
        self.assertEqual(self.probe.health_reports.count(), 1)

        self.probe.refresh_from_db()
        self.assertIsNotNone(self.probe.last_seen_at)
        self.assertEqual(self.probe.last_health_summary["cpu_percent"], 12.5)

    def test_payload_without_undervoltage_fields_is_still_accepted(self):
        # VALID_PAYLOAD above has no undervoltage_now/undervoltage_occurred
        # keys at all -- this is what an old, not-yet-updated probe (or
        # any non-Pi probe without vcgencmd) actually sends. Must not
        # 400 just because a field was added to the contract later.
        response = self.post(self._payload(self.probe.id), cn=str(self.probe.id))
        self.assertEqual(response.status_code, 201)

        health = self.probe.health_reports.get()
        self.assertIsNone(health.undervoltage_now)
        self.assertIsNone(health.undervoltage_occurred)

    def test_undervoltage_fields_are_stored_when_present(self):
        payload = self._payload(self.probe.id)
        payload["health"]["undervoltage_now"] = True
        payload["health"]["undervoltage_occurred"] = True

        response = self.post(payload, cn=str(self.probe.id))
        self.assertEqual(response.status_code, 201)

        health = self.probe.health_reports.get()
        self.assertTrue(health.undervoltage_now)
        self.assertTrue(health.undervoltage_occurred)

        self.probe.refresh_from_db()
        self.assertTrue(self.probe.last_health_summary["undervoltage_now"])
        self.assertTrue(self.probe.last_health_summary["undervoltage_occurred"])

    def test_missing_cert_header_is_rejected(self):
        response = self.client.post(
            self.url,
            data=self._payload(self.probe.id),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_unknown_probe_is_rejected(self):
        unknown_id = uuid.uuid4()
        response = self.post(self._payload(unknown_id), cn=str(unknown_id))
        self.assertEqual(response.status_code, 404)

    def test_inactive_probe_is_rejected(self):
        self.probe.is_active = False
        self.probe.save(update_fields=["is_active"])

        response = self.post(self._payload(self.probe.id), cn=str(self.probe.id))
        self.assertEqual(response.status_code, 403)

    def test_cn_body_mismatch_is_rejected(self):
        other = Probe.objects.create(
            name="other-station",
            hardware_type=Probe.HardwareType.GENERIC_LINUX,
        )
        response = self.post(self._payload(self.probe.id), cn=str(other.id))
        self.assertEqual(response.status_code, 403)

    def test_malformed_payload_is_rejected(self):
        payload = self._payload(self.probe.id)
        del payload["health"]
        response = self.post(payload, cn=str(self.probe.id))
        self.assertEqual(response.status_code, 400)
