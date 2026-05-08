from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0018_playerroundstate_follow_up_shots_reaction_shots"),
        ("teams", "0008_team_slots_player_preferred_roles"),
    ]

    operations = [
        migrations.DeleteModel(
            name="SingleRound",
        ),
    ]
