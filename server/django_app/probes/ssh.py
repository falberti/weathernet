"""Reads the VM's own SSH public key to hand to a probe during
enrollment, so the server can SSH into it with no manual key exchange
(PROJECT_SPEC.md Section 5.7).

Deliberately best-effort: a missing or unreadable key must never fail
enrollment itself, it's a convenience layered on top, not a requirement
of it.
"""
import logging

from django.conf import settings

logger = logging.getLogger(__name__)


def read_server_public_key() -> str | None:
    try:
        with open(settings.SERVER_SSH_PUBLIC_KEY_PATH) as f:
            return f.read().strip()
    except OSError as exc:
        # Covers a missing file, a permissions problem, and the
        # IsADirectoryError Docker produces when a bind mount's host
        # source path doesn't exist (it creates an empty directory in
        # its place instead of failing loudly).
        logger.warning(
            "Could not read server SSH public key from %s (%s) -- "
            "enrollment will proceed without installing it on the probe",
            settings.SERVER_SSH_PUBLIC_KEY_PATH,
            exc,
        )
        return None
