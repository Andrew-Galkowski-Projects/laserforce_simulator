# Generated for LG-02c — double-elimination tournaments.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0035_tournament_series_escalation"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[
                    ("single_elimination", "Single elimination"),
                    ("double_elimination", "Double elimination"),
                ],
                default="single_elimination",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="bracketnode",
            name="bracket_type",
            field=models.CharField(
                choices=[
                    ("winners", "Winners bracket"),
                    ("losers", "Losers bracket"),
                    ("grand_final", "Grand final"),
                ],
                default="winners",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="bracketnode",
            name="loser_advances_to",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="loser_feeders",
                to="matches.bracketnode",
            ),
        ),
        migrations.AddField(
            model_name="bracketnode",
            name="loser_advances_to_slot",
            field=models.CharField(
                blank=True,
                choices=[("a", "team_a"), ("b", "team_b")],
                max_length=1,
                null=True,
            ),
        ),
        migrations.RemoveConstraint(
            model_name="bracketnode",
            name="uniq_tournament_round_position",
        ),
        migrations.AddConstraint(
            model_name="bracketnode",
            constraint=models.UniqueConstraint(
                fields=["tournament", "bracket_type", "bracket_round", "position"],
                name="uniq_tournament_bracket_round_position",
            ),
        ),
    ]
