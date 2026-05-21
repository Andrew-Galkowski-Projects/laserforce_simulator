# Generated for RES-04 movement heatmap.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0025_alter_gameevent_event_type"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameround",
            name="cell_occupancy_json",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
