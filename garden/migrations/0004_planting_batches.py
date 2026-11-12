import uuid
from itertools import islice

from django.db import migrations, models


def separate_existing_batches(apps, schema_editor):
    planting = apps.get_model("garden", "Planting")
    records = planting.objects.using(schema_editor.connection.alias)
    iterator = records.only("pk").iterator(chunk_size=500)
    while batch := list(islice(iterator, 500)):
        for record in batch:
            record.batch_key = uuid.uuid4()
        records.bulk_update(batch, ["batch_key"], batch_size=500)


class Migration(migrations.Migration):
    dependencies = [("garden", "0003_cropcategory_cropcategorylink_and_more")]

    operations = [
        migrations.AlterField(
            model_name="planting",
            name="label",
            field=models.CharField(blank=True, max_length=251, verbose_name="Planting label"),
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
