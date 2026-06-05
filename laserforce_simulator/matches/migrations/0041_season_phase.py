# Generated for LG-02-Part2a (SeasonPhase foundation).

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0040_tournament_random_draw"),
    ]

    operations = [
        migrations.CreateModel(
            name="SeasonPhase",
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
                ("ordinal", models.PositiveSmallIntegerField()),
                (
                    "phase_type",
                    models.CharField(
                        choices=[
                            ("round_robin", "Round-robin"),
                            ("tournament", "Tournament"),
                            ("member_night", "Member night"),
                        ],
                        default="round_robin",
                        max_length=16,
                    ),
                ),
                (
                    "season",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="phases",
                        to="matches.season",
                    ),
                ),
            ],
            options={
                "ordering": ["ordinal"],
            },
        ),
        migrations.AddConstraint(
            model_name="seasonphase",
            constraint=models.UniqueConstraint(
                fields=("season", "ordinal"),
                name="uniq_season_phase_ordinal",
            ),
        ),
    ]
