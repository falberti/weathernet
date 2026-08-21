from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("telemetry", "0005_retention_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="probehealth",
            name="undervoltage_now",
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="probehealth",
            name="undervoltage_occurred",
            field=models.BooleanField(blank=True, null=True),
        ),
    ]
