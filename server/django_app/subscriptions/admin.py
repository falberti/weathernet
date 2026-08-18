from django.contrib import admin

from .models import WeatherSubscription


@admin.register(WeatherSubscription)
class WeatherSubscriptionAdmin(admin.ModelAdmin):
    """Read-mostly in Django Admin -- subscriptions are created and
    removed by the user through the bot (subscriptions/bot.py), this
    is here for the operator to see what's subscribed and debug a
    surprising geocode result (query_text vs place_label), not to
    manage them day to day.
    """

    list_display = ("place_label", "chat_id", "chat_username", "probe_ever_found", "created_at")
    list_filter = ("probe_ever_found",)
    search_fields = ("place_label", "query_text", "chat_username", "chat_id")
    readonly_fields = (
        "chat_id",
        "chat_username",
        "query_text",
        "place_label",
        "latitude",
        "longitude",
        "created_at",
        "probe_ever_found",
    )

    def has_add_permission(self, request):
        return False
