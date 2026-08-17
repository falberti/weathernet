from django.core.management.base import BaseCommand

from probes.models import Probe
from probes.wireguard import render_peer


class Command(BaseCommand):
    """Print one WireGuard [Peer] stanza per probe registered for
    WireGuard access. Used by server/wireguard/sync-peers.sh to build
    /etc/wireguard/wg0.conf -- see PROJECT_SPEC.md Section 5.7/5.8.

    Includes inactive probes on purpose: is_active gates telemetry
    ingestion, not WireGuard reachability.
    """

    help = "Print a WireGuard [Peer] stanza for every probe with WireGuard access configured."

    def handle(self, *args, **options):
        probes = (
            Probe.objects.exclude(wireguard_public_key__isnull=True)
            .exclude(wireguard_public_key="")
            .order_by("name")
        )
        for probe in probes:
            if not probe.wireguard_tunnel_ip:
                self.stderr.write(
                    f"Skipping {probe.name} ({probe.id}): has a WireGuard public key "
                    f"but no tunnel IP assigned"
                )
                continue

            self.stdout.write(render_peer(probe))
