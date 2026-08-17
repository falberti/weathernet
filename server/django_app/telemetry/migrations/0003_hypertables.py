from django.db import migrations

# TimescaleDB requires any unique or primary-key constraint on a
# hypertable to include the partitioning column ("time"). The tables
# Django just created in 0002_initial have a single-column PK on "id"
# only, which create_hypertable() would reject. We replace that PK with
# a composite (id, time) one first -- id remains unique enough for
# Django's row-identity purposes, it just no longer enforces uniqueness
# on its own at the database level.
#
# This migration is a one-way trip: reversing a hypertable conversion
# cleanly requires recreating the table, so the reverse operation is a
# no-op rather than a broken approximation.
SQL = """
ALTER TABLE telemetry_sensorreading DROP CONSTRAINT telemetry_sensorreading_pkey;
ALTER TABLE telemetry_sensorreading ADD PRIMARY KEY (id, time);
CREATE INDEX telemetry_sensorreading_probe_time_idx
    ON telemetry_sensorreading (probe_id, time DESC);
SELECT create_hypertable('telemetry_sensorreading', 'time', migrate_data => true);

ALTER TABLE telemetry_probehealth DROP CONSTRAINT telemetry_probehealth_pkey;
ALTER TABLE telemetry_probehealth ADD PRIMARY KEY (id, time);
CREATE INDEX telemetry_probehealth_probe_time_idx
    ON telemetry_probehealth (probe_id, time DESC);
SELECT create_hypertable('telemetry_probehealth', 'time', migrate_data => true);
"""


class Migration(migrations.Migration):
    dependencies = [
        ("telemetry", "0002_initial"),
    ]

    operations = [
        migrations.RunSQL(sql=SQL, reverse_sql=migrations.RunSQL.noop),
    ]
