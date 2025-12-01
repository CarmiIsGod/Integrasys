from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0020_estimate_status_closed_choices"),
    ]

    operations = [
        migrations.AddField(
            model_name="serviceorder",
            name="warranty_parent",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.SET_NULL,
                related_name="warranty_children",
                to="core.serviceorder",
            ),
        ),
        migrations.AlterField(
            model_name="serviceorder",
            name="status",
            field=models.CharField(
                choices=[
                    ("NEW", "Recibido"),
                    ("REV", "En revision"),
                    ("WAI", "En espera de repuestos"),
                    ("AUTH", "Requiere autorizacion de repuestos"),
                    ("READY", "Listo para recoger"),
                    ("DONE", "Entregado"),
                    ("CANC", "Cancelado"),
                ],
                db_index=True,
                default="NEW",
                max_length=10,
            ),
        ),
    ]
