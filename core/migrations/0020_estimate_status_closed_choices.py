from django.db import migrations, models
from django.db.models import Count


def _remap_status(apps, schema_editor):
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
        if pending > 0:
            estimate.status = "PENDING"
        elif accepted == total:
            estimate.status = "CLOSED_ACCEPTED"
        elif rejected == total:
            estimate.status = "CLOSED_REJECTED"
        elif accepted > 0 and rejected > 0:
            estimate.status = "CLOSED_PARTIAL"
        else:
            estimate.status = "PENDING"
        estimate.save(update_fields=["status"])


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0019_estimate_status"),
    ]

    operations = [
        migrations.AlterField(
            model_name="estimate",
            name="status",
            field=models.CharField(
                choices=[
                    ("PENDING", "Pendiente"),
                    ("CLOSED_ACCEPTED", "Cerrada · aprobada"),
                    ("CLOSED_PARTIAL", "Cerrada · aceptacion parcial"),
                    ("CLOSED_REJECTED", "Cerrada · rechazada"),
                ],
                db_index=True,
                default="PENDING",
                max_length=20,
            ),
        ),
        migrations.RunPython(_remap_status, migrations.RunPython.noop),
    ]
