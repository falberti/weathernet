from django.db import models

from probes.models import Probe


class SensorReading(models.Model):
    """One sensor reading, stored in a TimescaleDB hypertable.

    `time` is the hypertable partitioning column. TimescaleDB requires
    any unique/primary-key constraint on a hypertable to include that
    column, which Django's default single-column auto-incrementing `id`
    PK does not satisfy. The migration that calls create_hypertable()
    (0003_hypertables) replaces the PK Django creates here with a
    composite (id, time) one for exactly that reason.
    """

    id = models.BigAutoField(primary_key=True)
    time = models.DateTimeField()
    probe = models.ForeignKey(Probe, on_delete=models.PROTECT, related_name="sensor_readings")
    sensor_type = models.CharField(max_length=64)
    value = models.FloatField()
    unit = models.CharField(max_length=32, null=True, blank=True)


class ProbeHealth(models.Model):
    """One probe health snapshot, stored in a TimescaleDB hypertable.

    See SensorReading's docstring for why the primary key is composite.
    """

    id = models.BigAutoField(primary_key=True)
    time = models.DateTimeField()
    probe = models.ForeignKey(Probe, on_delete=models.PROTECT, related_name="health_reports")
    cpu_temp_c = models.FloatField(null=True, blank=True)
    cpu_percent = models.FloatField()
    mem_percent = models.FloatField()
    disk_percent = models.FloatField()
    uptime_seconds = models.BigIntegerField()
    # From `vcgencmd get_throttled` on the probe (weathernet_probe/health.py)
    # -- null on non-Pi hardware or if vcgencmd isn't available, same
    # convention as cpu_temp_c above. `_occurred` is sticky since the
    # probe's last reboot: a brief brownout (e.g. a sensor's fan inrush
    # current sagging the Pi's own 5V rail) between two reporting
    # cycles would very likely be missed by `_now` alone.
    undervoltage_now = models.BooleanField(null=True, blank=True)
    undervoltage_occurred = models.BooleanField(null=True, blank=True)
