from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("probes", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="probe",
            name="wireguard_public_key",
            field=models.CharField(blank=True, max_length=44, null=True),
        ),
        migrations.AddField(
            model_name="probe",
            name="wireguard_tunnel_ip",
            field=models.GenericIPAddressField(blank=True, null=True, unique=True),
        ),
    ]
