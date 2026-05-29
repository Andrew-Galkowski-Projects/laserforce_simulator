"""LG-01j — Season map_mode + map_pool + starting_map_pool_ids_json.

Pure schema migration adding three fields to ``Season``:
    * ``map_mode``: CharField, 3 choices, default ``"none"``.
    * ``map_pool``: M2M to ``core.ArenaMap``, blank=True.
    * ``starting_map_pool_ids_json``: JSONField snapshot, null/default None.

No ``RunPython`` / data migration — pre-LG-01j Seasons take the
``map_mode="none"`` default + empty M2M + ``None`` snapshot, which
yields the LG-01d 3-zone fallback at simulation time.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0030_league_current_team"),
        ("core", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="map_mode",
            field=models.CharField(
                choices=[
                    ("none", "3-zone fallback"),
                    ("single", "Single map"),
                    ("random_per_round", "Random per Round"),
                ],
                default="none",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="season",
            name="map_pool",
            field=models.ManyToManyField(
                blank=True,
                related_name="seasons_using_pool",
                to="core.arenamap",
            ),
        ),
        migrations.AddField(
            model_name="season",
            name="starting_map_pool_ids_json",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
