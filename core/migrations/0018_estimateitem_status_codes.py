from django.db import migrations, models
from django.utils import timezone


def _map_statuses(apps, schema_editor):
    EstimateItem = apps.get_model("core", "EstimateItem")
    mapping = {
        "PENDING": "PEN",
        "ACC": "ACC",
        "ACCEPTED": "ACC",
        "REJ": "REJ",
        "REJECTED": "REJ",
    }
    updates = []
    now = timezone.now()
    for item in EstimateItem.objects.all():
        old = item.status or ""
        new_value = mapping.get(old, "PEN")
        if old != new_value:
            item.status = new_value
        # Only set decided_at if already decided and missing timestamp
        if new_value in ("ACC", "REJ") and not item.decided_at:
            item.decided_at = now
        updates.append(item)
    if updates:
        EstimateItem.objects.bulk_update(updates, ["status", "decided_at"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0017_estimateitem_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="estimateitem",
            name="decided_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(_map_statuses, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="estimateitem",
            name="status",
            field=models.CharField(
                choices=[("PEN", "Pendiente"), ("ACC", "Aceptada"), ("REJ", "Rechazada")],
                db_index=True,
                default="PEN",
                max_length=3,
            ),
        ),
    ]
