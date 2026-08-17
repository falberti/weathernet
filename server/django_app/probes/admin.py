from django.contrib import admin

from .models import Probe


@admin.register(Probe)
class ProbeAdmin(admin.ModelAdmin):
    list_display = ("name", "hardware_type", "is_active", "last_seen_at", "wireguard_tunnel_ip")
    list_filter = ("hardware_type", "is_active")
    search_fields = ("name", "id", "location")
    readonly_fields = ("created_at", "last_seen_at", "last_health_summary")
