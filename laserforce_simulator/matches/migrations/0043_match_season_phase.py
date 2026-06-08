# Generated for LG-02-Part2c-2: Match.season_phase FK.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0042_seasonphase_format_tournament"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="season_phase",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="matches",
                to="matches.seasonphase",
            ),
        ),
    ]
