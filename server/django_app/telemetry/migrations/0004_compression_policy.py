from django.conf import settings
from django.db import migrations

TABLES = ["telemetry_sensorreading", "telemetry_probehealth"]


def enable_compression(apps, schema_editor):
    compress_after_days = int(settings.TELEMETRY_COMPRESS_AFTER_DAYS)
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute(
                f"ALTER TABLE {table} SET ("
                f"timescaledb.compress, timescaledb.compress_segmentby = 'probe_id');"
            )
            cursor.execute(
                "SELECT add_compression_policy(%s, %s * INTERVAL '1 day');",
                [table, compress_after_days],
            )


def disable_compression(apps, schema_editor):
    with schema_editor.connection.cursor() as cursor:
        for table in TABLES:
            cursor.execute("SELECT remove_compression_policy(%s, if_exists => true);", [table])


class Migration(migrations.Migration):
    dependencies = [
        ("telemetry", "0003_hypertables"),
    ]

    operations = [
        migrations.RunPython(enable_compression, reverse_code=disable_compression),
    ]
