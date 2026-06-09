# Generated for LG-02-Part2c-3b: SeasonPhase.tournament_mode field (dormant).
#
# Single AddField, NO RunPython / NO backfill (ADR-0004 disposable-data
# posture). Existing standings-playoff phases inherit default="standings".

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0044_match_leg"),
    ]

    operations = [
        migrations.AddField(
            model_name="seasonphase",
            name="tournament_mode",
            field=models.CharField(
                choices=[
                    ("standings", "Season-ending: from Standings"),
                    ("strength", "Mid-season: by team strength"),
                    ("unseeded", "Mid-season: random seed"),
                    ("random_draw", "Mid-season: drawn pool -> RR->DE"),
                ],
                default="standings",
                max_length=16,
            ),
        ),
    ]
