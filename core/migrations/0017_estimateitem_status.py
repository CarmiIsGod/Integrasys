from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0016_alter_notification_channel_kind"),
    ]

    operations = [
        migrations.AddField(
            model_name="estimateitem",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pendiente"),
                    ("ACCEPTED", "Aceptada"),
                    ("REJECTED", "Rechazada"),
                ],
                db_index=True,
                default="PENDING",
                max_length=12,
            ),
        ),
    ]
