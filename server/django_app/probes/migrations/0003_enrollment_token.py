import uuid

import django.db.models.deletion
import probes.models
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("probes", "0002_wireguard_fields"),
    ]

    operations = [
        migrations.AlterField(
            model_name="probe",
            name="id",
            field=models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False),
        ),
        migrations.CreateModel(
            name="EnrollmentToken",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("token_hash", models.CharField(editable=False, max_length=64, unique=True)),
                ("probe_name", models.CharField(max_length=200)),
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
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("expires_at", models.DateTimeField(default=probes.models._default_token_expiry)),
                ("used_at", models.DateTimeField(blank=True, null=True)),
                (
                    "resulting_probe",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="enrollment_token",
                        to="probes.probe",
                    ),
                ),
            ],
        ),
    ]
