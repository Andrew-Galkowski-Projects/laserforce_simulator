from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_basesightlineconfig_sightlineconfig"),
        ("matches", "0019_remove_singleround"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameround",
            name="arena_map",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="game_rounds",
                to="core.arenamap",
            ),
        ),
        migrations.AddField(
            model_name="gameround",
            name="zone_size",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.RenameField(
            model_name="playerroundstate",
            old_name="current_zone",
            new_name="zone_fallback",
        ),
        migrations.AddField(
            model_name="playerroundstate",
            name="cell_col",
            field=models.IntegerField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="playerroundstate",
            name="cell_row",
            field=models.IntegerField(blank=True, null=True),
        ),
    ]
