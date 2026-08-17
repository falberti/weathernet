import uuid

from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Probe",
            fields=[
                (
                    "id",
                    models.UUIDField(
                        default=uuid.uuid4,
                        editable=True,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("name", models.CharField(max_length=200)),
                (
                    "hardware_type",
                    models.CharField(
                        choices=[
                            ("raspberry_pi_3", "Raspberry Pi 3"),
                            ("raspberry_pi_4", "Raspberry Pi 4"),
                            ("raspberry_pi_5", "Raspberry Pi 5"),
                            ("generic_linux", "Generic Linux"),
                        ],
                        max_length=32,
                    ),
                ),
                ("location", models.CharField(blank=True, max_length=200)),
                ("notes", models.TextField(blank=True)),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_seen_at", models.DateTimeField(blank=True, null=True)),
                ("last_health_summary", models.JSONField(blank=True, null=True)),
            ],
        ),
    ]
