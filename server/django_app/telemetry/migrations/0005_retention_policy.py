from django.conf import settings
from django.db import migrations

TABLES = ["telemetry_sensorreading", "telemetry_probehealth"]


def enable_retention(apps, schema_editor):
    retention_days = int(settings.TELEMETRY_RETENTION_DAYS)
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(
                "SELECT add_retention_policy(%s, %s * INTERVAL '1 day');",
                [table, retention_days],
            )


def disable_retention(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute("SELECT remove_retention_policy(%s, if_exists => true);", [table])


class Migration(migrations.Migration):
    dependencies = [
        ("telemetry", "0004_compression_policy"),
    ]

    operations = [
        migrations.RunPython(enable_retention, reverse_code=disable_retention),
    ]
