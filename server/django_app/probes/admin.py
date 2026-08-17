import hashlib
import secrets

from django.conf import settings
from django.contrib import admin, messages
from django.utils.html import format_html

from .ca import server_cert_fingerprint_sha256
from .models import EnrollmentToken, Probe


@admin.register(Probe)
class ProbeAdmin(admin.ModelAdmin):
    list_display = ("name", "hardware_type", "is_active", "last_seen_at", "wireguard_tunnel_ip")
    list_filter = ("hardware_type", "is_active")
    search_fields = ("name", "id", "location")
    # id is generated at enrollment time (see EnrollmentTokenAdmin below),
    # not chosen by hand -- shown for reference, not editable.
    readonly_fields = ("id", "created_at", "last_seen_at", "last_health_summary")


@admin.register(EnrollmentToken)
class EnrollmentTokenAdmin(admin.ModelAdmin):
    list_display = ("probe_name", "hardware_type", "created_at", "expires_at", "used_at", "resulting_probe")
    list_filter = ("hardware_type",)
    readonly_fields = ("created_at", "used_at", "resulting_probe")

    def save_model(self, request, obj, form, change):
        if change:
            super().save_model(request, obj, form, change)
            return

        # The only place the raw token ever exists outside the
        # operator's terminal -- store only its hash, surface the raw
        # value exactly once via message_user below.
        raw_token = secrets.token_urlsafe(32)
        obj.token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
        super().save_model(request, obj, form, change)
        self._announce_token(request, raw_token)

    def _announce_token(self, request, raw_token):
        server_ip = settings.SERVER_PUBLIC_IP or "<server-public-ip>"
        command_lines = [
            "./probe/scripts/setup.sh \\",
            f"    --server {server_ip} \\",
            f"    --token {raw_token}",
        ]
        try:
            fingerprint = server_cert_fingerprint_sha256()
            command_lines[-1] += " \\"
            command_lines.append(f"    --fingerprint {fingerprint}")
        except OSError:
            # Server cert not mounted/readable -- degrade gracefully
            # (proceed without pinning) rather than fail the token save.
            self.message_user(
                request,
                "Could not read the server certificate to include a --fingerprint "
                "(TLS pinning) in the command below. Enrollment will still work.",
                level=messages.WARNING,
            )
        command = "\n".join(command_lines)

        self.message_user(
            request,
            format_html(
                "Token (shown once): <code>{}</code><br>"
                "Run on the probe, after <code>git clone</code>-ing this repo:"
                "<pre>{}</pre>",
                raw_token,
                command,
            ),
            level=messages.SUCCESS,
        )
