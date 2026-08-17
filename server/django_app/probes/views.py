import hashlib
import logging
import uuid

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.views import APIView

from . import ca
from .models import EnrollmentToken, Probe
from .serializers import EnrollRequestSerializer
from .wireguard import SubnetExhaustedError, allocate_tunnel_ip, read_server_public_key, server_tunnel_ip

logger = logging.getLogger(__name__)

DEFAULT_REPORT_INTERVAL_SECONDS = 300


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
