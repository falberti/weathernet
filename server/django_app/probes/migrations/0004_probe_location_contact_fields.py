from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("probes", "0003_enrollment_token"),
    ]

    operations = [
        migrations.RenameField(
            model_name="probe",
            old_name="location",
            new_name="location_address",
        ),
        migrations.AddField(
            model_name="probe",
            name="location_latitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="probe",
            name="location_longitude",
            field=models.DecimalField(blank=True, decimal_places=6, max_digits=9, null=True),
        ),
        migrations.AddField(
            model_name="probe",
            name="owner_email",
            field=models.EmailField(blank=True, max_length=254),
        ),
        migrations.AddField(
            model_name="probe",
            name="owner_phone",
            field=models.CharField(blank=True, max_length=32),
        ),
    ]
