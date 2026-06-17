# FIN-04 — health budget + injury/availability fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0013_player_salary_team_finance"),
    ]

    operations = [
        migrations.AddField(
            model_name="player",
            name="games_unavailable",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="team",
            name="budget_health",
            field=models.PositiveSmallIntegerField(default=34),
        ),
        migrations.AddField(
            model_name="team",
            name="injury_policy",
            field=models.CharField(
                choices=[
                    ("auto_sub", "Auto-substitute"),
                    ("play_hurt", "Play hurt"),
                ],
                default="auto_sub",
                max_length=16,
            ),
        ),
    ]
