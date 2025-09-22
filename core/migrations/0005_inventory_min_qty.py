from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0004_estimate_estimateitem"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventoryitem",
            name="min_qty",
            field=models.IntegerField(default=0),
        ),
        migrations.AlterField(
            model_name="notification",
            name="order",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                db_index=True,
                to="core.serviceorder",
            ),
        ),
    ]
