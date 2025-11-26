from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0015_customer_alt_phone"),
    ]

    operations = [
        migrations.AlterField(
            model_name="notification",
            name="kind",
            field=models.CharField(db_index=True, max_length=40),
        ),
        migrations.AlterField(
            model_name="notification",
            name="channel",
            field=models.CharField(db_index=True, max_length=40),
        ),
    ]

