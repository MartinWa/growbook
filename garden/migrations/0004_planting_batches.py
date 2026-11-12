import uuid

from django.db import migrations, models


def separate_existing_batches(apps, schema_editor):
    planting = apps.get_model("garden", "Planting")
    records = planting.objects.using(schema_editor.connection.alias)
    for pk in records.values_list("pk", flat=True).iterator():
        records.filter(pk=pk).update(batch_key=uuid.uuid4())


class Migration(migrations.Migration):
    dependencies = [("garden", "0003_cropcategory_cropcategorylink_and_more")]

    operations = [
        migrations.AlterField(
            model_name="planting",
            name="label",
            field=models.CharField(blank=True, max_length=301, verbose_name="Batch label"),
        ),
        migrations.AddField(
            model_name="planting",
            name="batch_key",
            field=models.UUIDField(db_index=True, editable=False, null=True),
        ),
        migrations.RunPython(separate_existing_batches, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="planting",
            name="batch_key",
            field=models.UUIDField(db_index=True, default=uuid.uuid4, editable=False),
        ),
        migrations.AddConstraint(
            model_name="planting",
            constraint=models.UniqueConstraint(
                fields=("batch_key", "variety"),
                condition=models.Q(variety__isnull=False),
                name="planting_batch_variety_unique",
            ),
        ),
        migrations.AddConstraint(
            model_name="planting",
            constraint=models.UniqueConstraint(
                fields=("batch_key",),
                condition=models.Q(variety__isnull=True),
                name="planting_batch_unknown_unique",
            ),
        ),
    ]
