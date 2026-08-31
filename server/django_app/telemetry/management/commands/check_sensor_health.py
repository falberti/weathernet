import datetime

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db.models import Max
from django.utils import timezone

from probes.models import Probe
from subscriptions.telegram_api import send_message

from ...models import SensorHealthAlert, SensorReading

# How far back to look for "this probe/sensor_type pair has ever
# reported" -- a bound on how much of the hypertable the aggregate
# query below has to scan, not a correctness cutoff: a pair whose last
# reading falls outside this window is already stale well past any
# sane SENSOR_STALE_ALERT_MINUTES, and if it was already alerted on,
# that open SensorHealthAlert row just isn't re-evaluated until it
# reports again (harmless -- it only means a long-dead sensor's "still
# down" state stops being refreshed, not that the original alert is lost).
KNOWN_SENSOR_WINDOW = datetime.timedelta(days=30)


class Command(BaseCommand):
    """Run periodically (see server/scripts/check-sensor-health.sh + its
    systemd timer -- same host-level pattern as send_daily_digest).

    For every active probe, checks each sensor_type it has ever
    reported: if its most recent reading is older than
    SENSOR_STALE_ALERT_MINUTES, sends one Telegram alert to
    TELEGRAM_ALERT_CHAT_ID and remembers it (SensorHealthAlert) so the
    same failure doesn't re-alert every run. When a sensor with an open
    alert reports again, sends a "recovered" message and clears it.

    Deliberately doesn't distinguish "sensor stopped working" from
    "reading went out of range": the probe-side drivers already reject
    implausible readings before they're ever sent (e.g.
    weathernet_probe/sensors/sps30.py's _check_plausible), so from here
    both failure modes look identical -- no fresh reading for this
    sensor_type. A probe-wide outage (WiFi/server down) also surfaces
    here, as every one of that probe's sensor_types going stale at
    once -- expected, not a separate case to handle, though the probe's
    own offline spool (weathernet_probe/spool.py) means a real report
    resumes on its own once connectivity returns, without needing this
    alert to trigger any action.
    """

    help = "Alert via Telegram when a sensor hasn't reported fresh data in too long."

    def handle(self, *args, **options):
        if not settings.TELEGRAM_ALERT_CHAT_ID:
            self.stdout.write(self.style.WARNING(
                "TELEGRAM_ALERT_CHAT_ID is not set -- skipping (see .env.example)."
            ))
            return

        # A deactivated probe stops being checked immediately -- clear
        # any alert left open from before it was deactivated rather
        # than leaving it to sit unresolved forever.
        SensorHealthAlert.objects.filter(probe__is_active=False).delete()

        now = timezone.now()
        stale_cutoff = now - datetime.timedelta(minutes=settings.SENSOR_STALE_ALERT_MINUTES)
        known_since = now - KNOWN_SENSOR_WINDOW

        latest_by_sensor = (
            SensorReading.objects
            .filter(probe__is_active=True, time__gte=known_since)
            .values("probe_id", "sensor_type")
            .annotate(last_time=Max("time"))
        )
        probes_by_id = {probe.id: probe for probe in Probe.objects.filter(is_active=True)}

        checked, alerted, recovered = 0, 0, 0
        for row in latest_by_sensor:
            probe = probes_by_id.get(row["probe_id"])
            if probe is None:
                continue
            sensor_type = row["sensor_type"]
            last_time = row["last_time"]
            checked += 1

            existing = SensorHealthAlert.objects.filter(probe=probe, sensor_type=sensor_type).first()

            if last_time < stale_cutoff:
                if existing is None:
                    age = now - last_time
                    send_message(
                        settings.TELEGRAM_ALERT_CHAT_ID,
                        f"⚠️ {probe.name}: il sensore \"{sensor_type}\" non riporta dati "
                        f"da {_format_duration(age)} (ultima lettura: "
                        f"{timezone.localtime(last_time).strftime('%d/%m %H:%M')})."
                    )
                    SensorHealthAlert.objects.create(probe=probe, sensor_type=sensor_type)
                    alerted += 1
            elif existing is not None:
                send_message(
                    settings.TELEGRAM_ALERT_CHAT_ID,
                    f"✅ {probe.name}: il sensore \"{sensor_type}\" ha ripreso a riportare dati."
                )
                existing.delete()
                recovered += 1

        self.stdout.write(self.style.SUCCESS(
            f"Checked {checked} probe/sensor pair(s): {alerted} new alert(s), {recovered} recovered."
        ))


def _format_duration(delta: datetime.timedelta) -> str:
    total_minutes = int(delta.total_seconds() // 60)
    hours, minutes = divmod(total_minutes, 60)
    if hours:
        return f"{hours}h {minutes}min"
    return f"{minutes}min"
