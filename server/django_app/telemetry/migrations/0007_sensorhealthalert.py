import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("probes", "0004_probe_location_contact_fields"),
        ("telemetry", "0006_probehealth_undervoltage_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="SensorHealthAlert",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("sensor_type", models.CharField(max_length=64)),
                ("alerted_at", models.DateTimeField(auto_now_add=True)),
                ("probe", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to="probes.probe")),
            ],
            options={
                "unique_together": {("probe", "sensor_type")},
            },
        ),
    ]
