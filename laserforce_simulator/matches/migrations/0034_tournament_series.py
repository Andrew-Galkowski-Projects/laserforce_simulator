# Generated for LG-02b — best-of-N series bracket nodes. Operations pinned in
# order: AddField(Tournament.series_length) -> CreateModel(SeriesMatch) ->
# RemoveField(BracketNode.match). No RunPython, no backfill (ADR-0004).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0033_tournament"),
        ("teams", "0010_player_profile_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="tournament",
            name="series_length",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")],
                default=1,
            ),
        ),
        migrations.CreateModel(
            name="SeriesMatch",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("game_number", models.PositiveIntegerField()),
                (
                    "match",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="series_match",
                        to="matches.match",
                    ),
                ),
                (
                    "node",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="series_matches",
                        to="matches.bracketnode",
                    ),
                ),
                (
                    "winner",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="+",
                        to="teams.team",
                    ),
                ),
            ],
            options={
                "ordering": ["game_number"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("node", "game_number"),
                        name="uniq_seriesmatch_node_game",
                    )
                ],
            },
        ),
        migrations.RemoveField(
            model_name="bracketnode",
            name="match",
        ),
    ]
