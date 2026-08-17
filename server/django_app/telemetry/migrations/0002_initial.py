import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("probes", "0001_initial"),
        ("telemetry", "0001_enable_timescaledb"),
    ]

    operations = [
        migrations.CreateModel(
            name="SensorReading",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("time", models.DateTimeField()),
                ("sensor_type", models.CharField(max_length=64)),
                ("value", models.FloatField()),
                ("unit", models.CharField(blank=True, max_length=32, null=True)),
                (
                    "probe",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="sensor_readings",
                        to="probes.probe",
                    ),
                ),
            ],
        ),
        migrations.CreateModel(
            name="ProbeHealth",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("time", models.DateTimeField()),
                ("cpu_temp_c", models.FloatField(blank=True, null=True)),
                ("cpu_percent", models.FloatField()),
                ("mem_percent", models.FloatField()),
                ("disk_percent", models.FloatField()),
                ("uptime_seconds", models.BigIntegerField()),
                (
                    "probe",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="health_reports",
                        to="probes.probe",
                    ),
                ),
            ],
        ),
    ]
