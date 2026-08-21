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
from .sensor_fallback import ALL_SENSOR_TYPES as PUBLIC_SUMMARY_SENSOR_TYPES
from .sensor_fallback import HUMIDITY_SENSOR_TYPES as PUBLIC_HUMIDITY_SENSOR_TYPES
from .sensor_fallback import PRESSURE_SENSOR_TYPES as PUBLIC_PRESSURE_SENSOR_TYPES
from .sensor_fallback import TEMPERATURE_SENSOR_TYPES as PUBLIC_TEMPERATURE_SENSOR_TYPES
from .serializers import EnrollRequestSerializer
from .wireguard import (
    SubnetExhaustedError,
    allocate_tunnel_ip,
    read_server_public_key,
    server_tunnel_ip,
)

logger = logging.getLogger(__name__)

DEFAULT_REPORT_INTERVAL_SECONDS = 300
# See sensor_fallback.py for what these mean and why BME680 is
# preferred over the newer chip-specific sensors when both exist.
PUBLIC_SUMMARY_GAS_BASELINE_WINDOW = timedelta(days=7)
PUBLIC_HISTORY_DEFAULT_HOURS = 24
PUBLIC_HISTORY_MAX_HOURS = 168  # 7 days -- a public, keyed-but-not-per-request-limited
# endpoint shouldn't let a caller ask for an unbounded history query.


def _check_public_api_key(request):
    """Shared gate for every /api/v1/public/* view. Returns an error
    Response if the request should be rejected, None if it may proceed.

    An unset PUBLIC_SUMMARY_API_KEY means the whole public API surface
    is disabled, not open -- compare_digest("", "") is True, so the
    emptiness is checked separately rather than relying on the digest
    compare alone.
    """
    configured_key = settings.PUBLIC_SUMMARY_API_KEY
    provided_key = request.headers.get("X-Api-Key", "")
    if not configured_key or not hmac.compare_digest(provided_key, configured_key):
        return Response({"detail": "invalid or missing API key"}, status=401)
    return None


def _first_available(readings: dict, sensor_types: tuple):
    """The first non-None latest-reading value among sensor_types, in
    priority order -- e.g. BME680's generic temperature_c if present,
    else BMP280's chip-prefixed one. None if none of them have ever
    reported for this probe.
    """
    for sensor_type in sensor_types:
        value = readings.get(sensor_type)
        if value is not None:
            return value
    return None


def _first_non_empty_series(probe_series: dict, sensor_types: tuple):
    """Same coalescing idea as _first_available, but for a whole time
    series: the first non-empty list among sensor_types, in priority
    order. [] if none of them have any points in the requested window.
    """
    for sensor_type in sensor_types:
        series = probe_series.get(sensor_type)
        if series:
            return series
    return []


def _public_probes_queryset():
    """Probes eligible to appear anywhere in the public API: active,
    and with coordinates set (an operator hasn't necessarily filled
    those in for every probe -- see Probe.location_latitude/longitude).
    """
    return Probe.objects.filter(
        is_active=True,
        location_latitude__isnull=False,
        location_longitude__isnull=False,
    )


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
        error = _check_public_api_key(request)
        if error:
            return error

        probes = list(_public_probes_queryset())

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
                        "temperature_c": _first_available(readings, PUBLIC_TEMPERATURE_SENSOR_TYPES),
                        "humidity_pct": _first_available(readings, PUBLIC_HUMIDITY_SENSOR_TYPES),
                        "pressure_hpa": _first_available(readings, PUBLIC_PRESSURE_SENSOR_TYPES),
                        "gas_resistance_ohm": readings.get("gas_resistance_ohm"),
                        "air_quality_index": compute_air_quality_index(
                            readings.get("gas_resistance_ohm"),
                            gas_baselines.get(probe.id),
                            _first_available(readings, PUBLIC_HUMIDITY_SENSOR_TYPES),
                        ),
                    },
                }
            )

        return Response({"generated_at": timezone.now(), "probes": payload})


class PublicHistoryView(APIView):
    """GET /api/v1/public/history?hours=24 -- recent readings per probe,
    for the external public page to chart (Section 3's "fourth path",
    same trust model and gate as PublicSummaryView above).

    Deliberately a separate endpoint from /api/v1/public/summary rather
    than folding history into it: most requests only need the current
    values (rendered on every page load), while history is heavier and
    only needed for charts -- keeping them separate means a page that
    only wants the cards doesn't pay for a history query it isn't
    using, and vice versa.

    No AQI in the response: the heuristic score is relative to a
    trailing baseline computed as of "now" (see aqi.py) -- computing a
    historically-accurate baseline for every past point would need a
    rolling-max-as-of-each-timestamp query, real added complexity for
    a chart that's mainly about the raw sensor trends anyway.
    """

    def get(self, request):
        error = _check_public_api_key(request)
        if error:
            return error

        try:
            hours = int(request.query_params.get("hours", PUBLIC_HISTORY_DEFAULT_HOURS))
        except ValueError:
            hours = PUBLIC_HISTORY_DEFAULT_HOURS
        hours = max(1, min(hours, PUBLIC_HISTORY_MAX_HOURS))

        probes = list(_public_probes_queryset())

        series_by_probe = {}
        readings = (
            SensorReading.objects.filter(
                probe__in=probes,
                sensor_type__in=PUBLIC_SUMMARY_SENSOR_TYPES,
                time__gte=timezone.now() - timedelta(hours=hours),
            )
            .order_by("time")
            .values("probe_id", "sensor_type", "time", "value")
        )
        for reading in readings:
            probe_series = series_by_probe.setdefault(reading["probe_id"], {t: [] for t in PUBLIC_SUMMARY_SENSOR_TYPES})
            probe_series[reading["sensor_type"]].append({"time": reading["time"], "value": reading["value"]})

        payload = []
        for probe in probes:
            probe_series = series_by_probe.get(probe.id, {})
            payload.append(
                {
                    "name": probe.name,
                    "series": {
                        "temperature_c": _first_non_empty_series(probe_series, PUBLIC_TEMPERATURE_SENSOR_TYPES),
                        "humidity_pct": _first_non_empty_series(probe_series, PUBLIC_HUMIDITY_SENSOR_TYPES),
                        "pressure_hpa": _first_non_empty_series(probe_series, PUBLIC_PRESSURE_SENSOR_TYPES),
                        "gas_resistance_ohm": probe_series.get("gas_resistance_ohm", []),
                    },
                }
            )

        return Response({"generated_at": timezone.now(), "window_hours": hours, "probes": payload})
