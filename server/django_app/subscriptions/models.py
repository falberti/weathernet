from django.db import models


class WeatherSubscription(models.Model):
    """One (Telegram chat, place) pair -- a chat can subscribe to more
    than one place (e.g. home and the holiday house), each tracked
    independently. Created entirely through conversation with the
    Telegram bot (see subscriptions/bot.py); there's no web form for
    this, on purpose (see PROJECT_SPEC.md Section 3).

    Deliberately no `probe` foreign key: which probe is "nearest" can
    change over time (a closer one could be enrolled later, or the
    current one deactivated), so it's recomputed on every digest run
    rather than pinned at subscribe time -- see
    management/commands/send_daily_digest.py.
    """

    # Telegram's chat id for a private chat -- large enough that a
    # plain IntegerField isn't safe.
    chat_id = models.BigIntegerField()
    # @username if the user has one set, for the operator's own
    # reference in Django Admin -- never used to send anything (all
    # sending is by chat_id).
    chat_username = models.CharField(max_length=64, blank=True)

    # What the user typed, and Nominatim's resolved display name for
    # it (see geocoding.py) -- kept separate so a confusing geocode
    # result is visible/debuggable later, not just silently trusted.
    query_text = models.CharField(max_length=255)
    place_label = models.CharField(max_length=255)
    latitude = models.DecimalField(max_digits=9, decimal_places=6)
    longitude = models.DecimalField(max_digits=9, decimal_places=6)

    created_at = models.DateTimeField(auto_now_add=True)

    # Set the first time send_daily_digest.py finds an active probe
    # within SUBSCRIPTION_MAX_DISTANCE_KM for this subscription -- lets
    # it send a one-time "a probe is now in range" notice instead of
    # just silently starting the regular digest (see that command's
    # docstring).
    probe_ever_found = models.BooleanField(default=False)

    class Meta:
        indexes = [models.Index(fields=["chat_id"])]

    def __str__(self):
        return f"{self.place_label} -> chat {self.chat_id}"
