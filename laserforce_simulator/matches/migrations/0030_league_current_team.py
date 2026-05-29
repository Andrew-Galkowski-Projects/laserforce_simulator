# LG-01g — add League.current_team FK (manager's Team picker default).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("teams", "0010_player_profile_fields"),
        ("matches", "0029_league_season_match_fk"),
    ]

    operations = [
        migrations.AddField(
            model_name="league",
            name="current_team",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="managed_in_leagues",
                to="teams.team",
            ),
        ),
    ]
