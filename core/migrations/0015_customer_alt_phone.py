from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0014_device_accessories_notes_device_password_notes_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="customer",
            name="alt_phone",
            field=models.CharField(blank=True, db_index=True, default="", max_length=30),
        ),
    ]
