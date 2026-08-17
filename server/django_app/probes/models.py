import uuid

from django.db import models


class Probe(models.Model):
    """The probe registry.

    This is the authoritative source of truth for which probes may
    report data: the ingestion view rejects any request whose client
    certificate CN doesn't match an active row here (see
    telemetry/views.py). Deactivating a probe here actually stops its
    data from being accepted, not just from being displayed.
    """

    class HardwareType(models.TextChoices):
        RASPBERRY_PI_3 = "raspberry_pi_3", "Raspberry Pi 3"
        RASPBERRY_PI_4 = "raspberry_pi_4", "Raspberry Pi 4"
        RASPBERRY_PI_5 = "raspberry_pi_5", "Raspberry Pi 5"
        GENERIC_LINUX = "generic_linux", "Generic Linux"

    # This UUID is also the Common Name baked into the probe's mTLS
    # client certificate (see server/pki/generate-probe-cert.sh) -- cert
    # issuance and probe registration must agree on it.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=True)
    name = models.CharField(max_length=200)
    hardware_type = models.CharField(max_length=32, choices=HardwareType.choices)
    location = models.CharField(max_length=200, blank=True)
    notes = models.TextField(blank=True)

    # WireGuard remote access (PROJECT_SPEC.md Section 5.7) -- a second,
    # independent channel from mTLS/telemetry, for operator
    # troubleshooting. Deliberately not gated by is_active: a probe
    # deactivated because its sensors misbehave is exactly the one you
    # still want to be able to SSH into.
    wireguard_public_key = models.CharField(max_length=44, blank=True, null=True)
    # unique=True with null=True allows any number of NULLs on Postgres
    # (NULL != NULL) while still enforcing uniqueness once a probe is
    # actually assigned a tunnel IP.
    wireguard_tunnel_ip = models.GenericIPAddressField(blank=True, null=True, unique=True)

    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    last_seen_at = models.DateTimeField(null=True, blank=True)

    # Snapshot of the most recent health payload (cpu_temp_c, cpu_percent,
    # mem_percent, disk_percent, uptime_seconds), refreshed on every
    # successful ingest. Lets the Admin list page show probe health with
    # a single indexed lookup instead of querying the much larger
    # telemetry_probehealth hypertable for the latest row per probe.
    last_health_summary = models.JSONField(null=True, blank=True)

    def __str__(self):
        return f"{self.name} ({self.id})"
