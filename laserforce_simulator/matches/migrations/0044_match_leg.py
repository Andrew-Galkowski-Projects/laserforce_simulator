# Generated for LG-02-Part2c-3a: Match.leg field (double_round_robin).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0043_match_season_phase"),
    ]

    operations = [
        migrations.AddField(
            model_name="match",
            name="leg",
            field=models.PositiveSmallIntegerField(default=1),
        ),
    ]
