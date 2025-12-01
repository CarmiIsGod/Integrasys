from django.db import migrations, models
from django.db.models import Count


def _backfill_status(apps, schema_editor):
    Estimate = apps.get_model("core", "Estimate")
    EstimateItem = apps.get_model("core", "EstimateItem")
    for estimate in Estimate.objects.all():
        counts = {"PEN": 0, "ACC": 0, "REJ": 0}
        for row in estimate.items.values("status").annotate(total=Count("id")):
            status = row["status"] or ""
            if status in counts:
                counts[status] = row["total"]
        pending = counts["PEN"]
        accepted = counts["ACC"]
        rejected = counts["REJ"]
        total = pending + accepted + rejected
        if pending == total:
            estimate.status = "OPEN_PENDING"
        elif pending > 0 and (accepted > 0 or rejected > 0):
            estimate.status = "OPEN_PARTIAL"
        elif pending == 0 and accepted == total:
            estimate.status = "CLOSED_ACCEPTED"
        elif pending == 0 and rejected == total:
            estimate.status = "CLOSED_REJECTED"
        elif pending == 0 and accepted > 0 and rejected > 0:
            estimate.status = "CLOSED_PARTIAL"
        else:
            estimate.status = "OPEN_PENDING"
        estimate.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0018_estimateitem_status_codes"),
    ]

    operations = [
        migrations.AddField(
            model_name="estimate",
            name="status",
            field=models.CharField(
                choices=[
                    ("OPEN_PENDING", "Pendiente"),
                    ("OPEN_PARTIAL", "Pendiente (respuesta parcial)"),
                    ("CLOSED_ACCEPTED", "Cerrada · aprobada"),
                    ("CLOSED_PARTIAL", "Cerrada · aceptacion parcial"),
                    ("CLOSED_REJECTED", "Cerrada · rechazada"),
                ],
                db_index=True,
                default="OPEN_PENDING",
                max_length=20,
            ),
        ),
        migrations.RunPython(_backfill_status, migrations.RunPython.noop),
    ]
