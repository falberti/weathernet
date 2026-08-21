from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from probes.models import Probe

from .models import ProbeHealth, SensorReading
from .serializers import IngestSerializer

CERT_CN_HEADER = "HTTP_X_CLIENT_CERT_CN"


class IngestView(APIView):
    """POST /api/v1/ingest -- the only endpoint probes call.

    SECURITY-RELEVANT TRUST BOUNDARY: this view trusts the
    X-Client-Cert-CN header verbatim as the caller's authenticated
    identity, without re-verifying anything itself. That is only safe
    because Django is not reachable from anywhere except nginx (it is
    not published outside the Docker network -- see
    server/docker-compose.yml), and nginx is configured to require and
    verify the client's mTLS certificate before proxying the request
    and to set this header from the verified certificate's CN itself
    (see server/nginx/nginx.conf.template). If Django is ever exposed
    directly, or nginx's mTLS verification is ever relaxed, this trust
    assumption breaks and this header must no longer be trusted as-is.
    """

    def post(self, request):
        cert_cn = request.META.get(CERT_CN_HEADER)
        if not cert_cn:
            return Response({"detail": "missing client certificate identity"}, status=403)

        serializer = IngestSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=400)
        payload = serializer.validated_data

        if str(payload["probe_id"]) != cert_cn:
            return Response({"detail": "probe_id does not match client certificate"}, status=403)

        try:
            probe = Probe.objects.get(pk=payload["probe_id"])
        except Probe.DoesNotExist:
            return Response({"detail": "unknown probe"}, status=404)

        if not probe.is_active:
            return Response({"detail": "probe is not active"}, status=403)

        timestamp = payload["timestamp"]
        health = payload["health"]

        with transaction.atomic():
            SensorReading.objects.bulk_create(
                SensorReading(
                    time=timestamp,
                    probe=probe,
                    sensor_type=reading["sensor_type"],
                    value=reading["value"],
                )
                for reading in payload["readings"]
            )
            ProbeHealth.objects.create(
                time=timestamp,
                probe=probe,
                cpu_temp_c=health["cpu_temp_c"],
                cpu_percent=health["cpu_percent"],
                mem_percent=health["mem_percent"],
                disk_percent=health["disk_percent"],
                uptime_seconds=health["uptime_seconds"],
                undervoltage_now=health["undervoltage_now"],
                undervoltage_occurred=health["undervoltage_occurred"],
            )
            probe.last_seen_at = timezone.now()
            probe.last_health_summary = {
                "cpu_temp_c": health["cpu_temp_c"],
                "cpu_percent": health["cpu_percent"],
                "mem_percent": health["mem_percent"],
                "disk_percent": health["disk_percent"],
                "uptime_seconds": health["uptime_seconds"],
                "undervoltage_now": health["undervoltage_now"],
                "undervoltage_occurred": health["undervoltage_occurred"],
            }
            probe.save(update_fields=["last_seen_at", "last_health_summary"])

        return Response(status=201)
