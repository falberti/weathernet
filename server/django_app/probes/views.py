import hashlib
import hmac
import logging
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from telemetry.models import SensorReading

from . import ca, ssh
from .aqi import compute_air_quality_index
from .models import EnrollmentToken, Probe
from .serializers import EnrollRequestSerializer
from .wireguard import SubnetExhaustedError, allocate_tunnel_ip, read_server_public_key, server_tunnel_ip

logger = logging.getLogger(__name__)

DEFAULT_REPORT_INTERVAL_SECONDS = 300
PUBLIC_SUMMARY_SENSOR_TYPES = ("temperature_c", "humidity_pct", "pressure_hpa", "gas_resistance_ohm")
PUBLIC_SUMMARY_GAS_BASELINE_WINDOW = timedelta(days=7)


class EnrollView(APIView):
    """POST /api/v1/enroll -- one-time, NOT authenticated by client cert.

    A brand-new probe has no mTLS certificate yet, so it can't
    authenticate the way /api/v1/ingest requires -- that's the whole
    problem this endpoint exists to solve. Trust comes entirely from
    the single-use token instead. nginx proxies this path through
    without requiring a client certificate (see PROJECT_SPEC.md
    Section 5.1); the token check below is this endpoint's only gate.
    """

    def post(self, request):
        serializer = EnrollRequestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        data = serializer.validated_data

        token_hash = hashlib.sha256(data["token"].encode()).hexdigest()

        with transaction.atomic():
            # select_for_update() makes the "is it unused and unexpired"
            # check and the "mark it used" write atomic, so a token
            # can't be redeemed twice even under a race (e.g. a retried
            # request whose first response got lost in transit).
            try:
                token = EnrollmentToken.objects.select_for_update().get(token_hash=token_hash)
            except EnrollmentToken.DoesNotExist:
                return Response({"detail": "unknown token"}, status=404)

            if token.used_at is not None or token.is_expired:
                return Response({"detail": "token expired or already used"}, status=410)

            detected = data.get("detected_hardware_type")
            if detected and detected != token.hardware_type:
                logger.warning(
                    "Enrollment hardware_type mismatch for token '%s...': "
                    "declared '%s', probe detected '%s'",
                    token.token_hash[:8],
                    token.hardware_type,
                    detected,
                )

            new_probe_id = uuid.uuid4()

            # Anything that can fail on the caller's input happens
            # before any write, so a 400/409 here leaves the token
            # untouched and safely retryable.
            try:
                client_cert_pem = ca.sign_probe_csr(data["csr_pem"], probe_id=new_probe_id)
            except ca.CSRSigningError as exc:
                return Response({"detail": f"invalid CSR: {exc}"}, status=400)

            try:
                tunnel_ip = allocate_tunnel_ip()
            except SubnetExhaustedError as exc:
                return Response({"detail": str(exc)}, status=409)

            # Everything the response body needs is gathered here too,
            # still before any write -- these are just file reads and
            # shouldn't normally fail, but if the CA cert or the
            # WireGuard public key file is ever unreadable (e.g. a
            # permissions slip -- see server/wireguard/generate-server-keys.sh),
            # this way that surfaces as a clean 500 with the token and
            # database left untouched, not as a burned token and an
            # orphaned Probe row with no certificate ever delivered.
            response_body = {
                "probe_id": str(new_probe_id),
                "client_cert_pem": client_cert_pem,
                "ca_cert_pem": ca.ca_cert_pem(),
                "server_url": f"https://{settings.SERVER_PUBLIC_IP}",
                "wireguard": {
                    "tunnel_ip": tunnel_ip,
                    "server_public_key": read_server_public_key(),
                    "server_endpoint": f"{settings.SERVER_PUBLIC_IP}:{settings.WIREGUARD_LISTEN_PORT}",
                    # The server's *tunnel* address (e.g. 10.10.0.1) --
                    # what this probe's own [Peer] block's AllowedIPs
                    # must be. Not the same thing as server_endpoint
                    # above (that's the server's public IP:port, used
                    # for Endpoint=).
                    "server_tunnel_ip": server_tunnel_ip(),
                },
                # None if SERVER_SSH_PUBLIC_KEY_PATH isn't configured or
                # readable -- best-effort convenience, never a reason to
                # fail enrollment itself (see ssh.py).
                "server_ssh_public_key": ssh.read_server_public_key(),
                "report_interval_seconds": DEFAULT_REPORT_INTERVAL_SECONDS,
            }

            probe = Probe.objects.create(
                id=new_probe_id,
                name=token.probe_name,
                hardware_type=token.hardware_type,
                wireguard_public_key=data["wireguard_public_key"],
                wireguard_tunnel_ip=tunnel_ip,
            )

            token.used_at = timezone.now()
            token.resulting_probe = probe
            token.save(update_fields=["used_at", "resulting_probe"])

        return Response(response_body, status=201)


class PublicSummaryView(APIView):
    """GET /api/v1/public/summary -- for an external, public-facing page
    (e.g. a PHP page on a different domain) to render current readings.
    Not protected by mTLS (nginx proxies this path through like
    /api/v1/enroll); gated instead by a shared API key, since unlike
    /api/v1/enroll this has no per-request token of its own and would
    otherwise be wide open to being scraped/hammered directly by
    anyone who finds the URL.

    Coordinates are deliberately rounded to a coarser precision than
    what's stored (PUBLIC_LOCATION_PRECISION_DECIMALS) -- enough to
    place a probe in its general area (and, with multiple probes,
    eventually plot a geographic heatmap) without revealing which
    building it's actually in. location_address and owner contact
    details are never included here at all.
    """

    def get(self, request):
        configured_key = settings.PUBLIC_SUMMARY_API_KEY
        provided_key = request.headers.get("X-Api-Key", "")
        # An unset configured_key means the endpoint is disabled, not
        # open -- compare_digest("", "") is True, so the emptiness is
        # checked separately rather than relying on the digest compare.
        if not configured_key or not hmac.compare_digest(provided_key, configured_key):
            return Response({"detail": "invalid or missing API key"}, status=401)

        probes = list(
            Probe.objects.filter(
                is_active=True,
                location_latitude__isnull=False,
                location_longitude__isnull=False,
            )
        )

        latest_readings = {}
        readings_qs = (
            SensorReading.objects.filter(probe__in=probes, sensor_type__in=PUBLIC_SUMMARY_SENSOR_TYPES)
            .order_by("probe_id", "sensor_type", "-time")
            .distinct("probe_id", "sensor_type")
        )
        for reading in readings_qs:
            latest_readings.setdefault(reading.probe_id, {})[reading.sensor_type] = reading.value

        gas_baselines = dict(
            SensorReading.objects.filter(
                probe__in=probes,
                sensor_type="gas_resistance_ohm",
                time__gte=timezone.now() - PUBLIC_SUMMARY_GAS_BASELINE_WINDOW,
            )
            .values("probe_id")
            .annotate(baseline=Max("value"))
            .values_list("probe_id", "baseline")
        )

        precision = settings.PUBLIC_LOCATION_PRECISION_DECIMALS
        payload = []
        for probe in probes:
            readings = latest_readings.get(probe.id, {})
            payload.append(
                {
                    "name": probe.name,
                    "hardware_type": probe.hardware_type,
                    "latitude": round(float(probe.location_latitude), precision),
                    "longitude": round(float(probe.location_longitude), precision),
                    "last_seen_at": probe.last_seen_at,
                    "readings": {
                        "temperature_c": readings.get("temperature_c"),
                        "humidity_pct": readings.get("humidity_pct"),
                        "pressure_hpa": readings.get("pressure_hpa"),
                        "gas_resistance_ohm": readings.get("gas_resistance_ohm"),
                        "air_quality_index": compute_air_quality_index(
                            readings.get("gas_resistance_ohm"),
                            gas_baselines.get(probe.id),
                            readings.get("humidity_pct"),
                        ),
                    },
                }
            )

        return Response({"generated_at": timezone.now(), "probes": payload})
