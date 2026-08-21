import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Avg, Max, Min
from django.utils import timezone

from probes.aqi import compute_air_quality_index
from probes.sensor_fallback import (
    HUMIDITY_SENSOR_TYPES,
    PRESSURE_SENSOR_TYPES,
    TEMPERATURE_SENSOR_TYPES,
)
from telemetry.models import SensorReading

from ...matching import nearest_active_probe
from ...models import WeatherSubscription
from ...telegram_api import send_message

GAS_BASELINE_WINDOW = datetime.timedelta(days=7)


class Command(BaseCommand):
    """Run once a day (see server/scripts/send-daily-digest.sh + its
    systemd timer). For every subscription, finds the nearest active
    probe; if it's within SUBSCRIPTION_MAX_DISTANCE_KM, sends a
    summary of yesterday's readings. Subscriptions with no probe close
    enough are silently skipped -- not an error, just not ready yet
    (the user was already told this when they subscribed, see
    subscriptions/bot.py).

    The first time a subscription *does* get a nearby probe (tracked
    via probe_ever_found), the message includes an extra "a probe is
    now in range" line rather than just silently starting -- the user
    asked for exactly this when this feature was speced.
    """

    help = "Send the daily weather digest to every subscription with a probe close enough."

    def handle(self, *args, **options):
        yesterday_start, yesterday_end, label = _yesterday_range()
        sent, skipped = 0, 0

        for subscription in WeatherSubscription.objects.all():
            match = nearest_active_probe(float(subscription.latitude), float(subscription.longitude))
            if not match or match[1] > settings.SUBSCRIPTION_MAX_DISTANCE_KM:
                skipped += 1
                continue

            probe, distance_km = match
            is_first_time = not subscription.probe_ever_found
            if is_first_time:
                subscription.probe_ever_found = True
                subscription.save(update_fields=["probe_ever_found"])

            text = _build_digest_message(
                subscription, probe, distance_km, yesterday_start, yesterday_end, label, is_first_time
            )
            send_message(subscription.chat_id, text)
            sent += 1

        self.stdout.write(self.style.SUCCESS(f"Digest sent to {sent} subscription(s), {skipped} skipped (no probe close enough)"))


def _yesterday_range():
    """Yesterday's calendar day in the server's local timezone
    (settings.TIME_ZONE = Europe/Rome), as a [start, end) UTC-aware
    range -- readers get "yesterday" in their own intuitive sense, not
    a UTC day boundary that can be off by a couple of hours.
    """
    today_local = timezone.localtime(timezone.now()).date()
    yesterday_local = today_local - datetime.timedelta(days=1)
    start = timezone.make_aware(datetime.datetime.combine(yesterday_local, datetime.time.min))
    end = timezone.make_aware(datetime.datetime.combine(today_local, datetime.time.min))
    return start, end, yesterday_local.strftime("%d/%m/%Y")


def _fmt(value, suffix, decimals=1):
    return "n/d" if value is None else f"{value:.{decimals}f}{suffix}"


def _aggregate_with_fallback(readings, sensor_types, **aggregations):
    """Same coalescing policy as the public API (see
    probes/sensor_fallback.py): try each sensor_type in priority
    order, use the first one that actually has readings in this
    window. Lets a probe with only the newer chip-specific sensors
    (no BME680) still get a real digest instead of every field
    silently rendering "n/d".
    """
    empty = {key: None for key in aggregations}
    for sensor_type in sensor_types:
        result = readings.filter(sensor_type=sensor_type).aggregate(**aggregations)
        if any(value is not None for value in result.values()):
            return result
    return empty


def _build_digest_message(subscription, probe, distance_km, start, end, label, is_first_time):
    readings = SensorReading.objects.filter(probe=probe, time__gte=start, time__lt=end)
    temp = _aggregate_with_fallback(readings, TEMPERATURE_SENSOR_TYPES, min=Min("value"), max=Max("value"), avg=Avg("value"))
    humidity = _aggregate_with_fallback(readings, HUMIDITY_SENSOR_TYPES, avg=Avg("value"))
    pressure = _aggregate_with_fallback(readings, PRESSURE_SENSOR_TYPES, min=Min("value"), max=Max("value"), avg=Avg("value"))
    # No BME680 gas sensor equivalent exists on the fallback chips --
    # unlike the three above, this one intentionally has no fallback.
    gas = readings.filter(sensor_type="gas_resistance_ohm").aggregate(avg=Avg("value"))

    gas_baseline = (
        SensorReading.objects.filter(
            probe=probe, sensor_type="gas_resistance_ohm", time__gte=timezone.now() - GAS_BASELINE_WINDOW
        ).aggregate(baseline=Max("value"))["baseline"]
    )
    aqi = compute_air_quality_index(gas["avg"], gas_baseline, humidity["avg"])
    aqi_label = "n/d" if aqi is None else f"{_aqi_word(aqi)} ({aqi}/100)"

    lines = []
    if is_first_time:
        lines.append(
            f"Buone notizie: ora c'è una sonda abbastanza vicina a \"{subscription.place_label}\" "
            f"({distance_km:.1f} km) -- da oggi ricevi il riepilogo ogni mattina.\n"
        )

    lines.append(f"Riepilogo meteo di ieri ({label}) -- {subscription.place_label} (sonda a {distance_km:.1f} km)\n")
    lines.append(f"Temperatura: min {_fmt(temp['min'], '°C')}, max {_fmt(temp['max'], '°C')}, media {_fmt(temp['avg'], '°C')}")
    lines.append(f"Umidità media: {_fmt(humidity['avg'], '%', 0)}")
    lines.append(f"Pressione: {_fmt(pressure['min'], ' hPa', 0)} - {_fmt(pressure['max'], ' hPa', 0)} (media {_fmt(pressure['avg'], ' hPa', 0)})")
    lines.append(f"Qualità aria: {aqi_label}")
    return "\n".join(lines)


def _aqi_word(score):
    if score >= 70:
        return "Buona"
    if score >= 40:
        return "Moderata"
    return "Scarsa"
