"""WireGuard tunnel IP allocation and [Peer] stanza rendering.

Used by the enrollment view (allocate a fresh tunnel IP for a
newly-enrolled probe) and by the generate_wireguard_peers management
command (render the full peer list for wireguard/sync-peers.sh) --
PROJECT_SPEC.md Sections 5.7/5.8.
"""
import ipaddress

from django.conf import settings

from .models import Probe


class SubnetExhaustedError(Exception):
    """Raised when every address in WIREGUARD_SUBNET is already assigned."""


def server_tunnel_ip() -> str:
    """The server's own address inside WIREGUARD_SUBNET -- always the
    first host address (see wireguard/wg0.conf.header, which renders
    this same value into the server's own [Interface] block). Handed
    back to a probe during enrollment so it knows what to put in its
    own [Peer] block's AllowedIPs -- not to be confused with the
    server's public IP / WireGuard *endpoint*, a different address
    entirely.
    """
    network = ipaddress.ip_network(settings.WIREGUARD_SUBNET)
    return str(next(network.hosts()))


def allocate_tunnel_ip() -> str:
    """Return the lowest address in WIREGUARD_SUBNET not already
    assigned to another probe.

    The subnet's first host address is reserved for the server itself
    (server_tunnel_ip() above), so allocation starts from the second one.
    """
    network = ipaddress.ip_network(settings.WIREGUARD_SUBNET)
    taken = set(
        Probe.objects.exclude(wireguard_tunnel_ip__isnull=True).values_list(
            "wireguard_tunnel_ip", flat=True
        )
    )
    hosts = list(network.hosts())
    for host in hosts[1:]:
        candidate = str(host)
        if candidate not in taken:
            return candidate
    raise SubnetExhaustedError(f"no free address left in {settings.WIREGUARD_SUBNET}")


def read_server_public_key() -> str:
    """The server's own WireGuard public key, generated once by
    wireguard/generate-server-keys.sh and bind-mounted read-only into
    this container -- handed back to a probe during enrollment so it
    can render its own wg0.conf without the operator copying it by
    hand.
    """
    with open(settings.WIREGUARD_SERVER_PUBLIC_KEY_PATH) as f:
        return f.read().strip()


def render_peer(probe: Probe) -> str:
    """Render one [Peer] stanza for wg0.conf.

    Caller (the generate_wireguard_peers management command) is
    responsible for only calling this on probes that actually have both
    WireGuard fields set.
    """
    return (
        "[Peer]\n"
        f"# {probe.name} ({probe.id})\n"
        f"PublicKey = {probe.wireguard_public_key}\n"
        f"AllowedIPs = {probe.wireguard_tunnel_ip}/32\n"
    )
