import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone


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
    # client certificate. It's generated here, by Django, the moment an
    # EnrollmentToken is redeemed (see views.EnrollView) -- never chosen
    # by the operator or the probe, hence editable=False.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
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


def _default_token_expiry():
    return timezone.now() + timedelta(minutes=settings.ENROLLMENT_TOKEN_TTL_MINUTES)


class EnrollmentToken(models.Model):
    """A short-lived, single-use credential that lets a brand-new probe
    bootstrap itself with no pre-existing mTLS certificate (a probe
    obviously can't use mTLS to fetch its own first certificate -- see
    PROJECT_SPEC.md Section 5.7). Redeeming one (views.EnrollView)
    creates the Probe row and issues its client certificate atomically.
    """

    # SHA-256 hex digest of the raw token -- never store the raw value
    # itself, same principle as password storage. The raw token is shown
    # to the operator exactly once, in admin.py's save_model(), and is
    # unrecoverable after that.
    token_hash = models.CharField(max_length=64, unique=True, editable=False)

    # What the resulting Probe should be called / registered as -- set
    # by the operator at token-creation time, copied onto the Probe row
    # when the token is redeemed.
    probe_name = models.CharField(max_length=200)
    hardware_type = models.CharField(max_length=32, choices=Probe.HardwareType.choices)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_token_expiry)
    # Null means still redeemable. Set atomically, inside the same
    # select_for_update() transaction that checks it, so a token can't
    # be redeemed twice even under a race (see views.EnrollView).
    used_at = models.DateTimeField(null=True, blank=True)
    resulting_probe = models.ForeignKey(
        Probe, null=True, blank=True, on_delete=models.SET_NULL, related_name="enrollment_token"
    )

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    def __str__(self):
        status = "used" if self.used_at else ("expired" if self.is_expired else "unused")
        return f"{self.probe_name} ({status})"
