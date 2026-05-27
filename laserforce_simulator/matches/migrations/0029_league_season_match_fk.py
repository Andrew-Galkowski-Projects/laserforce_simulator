# LG-01 — League / Season foundation. See ADR-0014.

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0028_gameround_is_simulated"),
        ("teams", "0010_player_profile_fields"),
    ]

    operations = [
        migrations.CreateModel(
            name="League",
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
                ("name", models.CharField(max_length=100)),
                (
                    "mode",
                    models.CharField(
                        choices=[
                            ("sandbox", "Sandbox"),
                            ("league", "League"),
                            ("multiplayer", "Multiplayer"),
                        ],
                        default="league",
                        max_length=16,
                    ),
                ),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("active", "Active"),
                            ("archived", "Archived"),
                        ],
                        default="active",
                        max_length=16,
                    ),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
        ),
        migrations.CreateModel(
            name="Season",
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
                ("name", models.CharField(max_length=100)),
                ("start_date", models.DateField()),
                (
                    "state",
                    models.CharField(
                        choices=[
                            ("draft", "Draft"),
                            ("active", "Active"),
                            ("completed", "Completed"),
                        ],
                        default="draft",
                        max_length=16,
                    ),
                ),
                (
                    "schedule_format",
                    models.CharField(
                        choices=[("single_round_robin", "Single round-robin")],
                        default="single_round_robin",
                        max_length=32,
                    ),
                ),
                (
                    "starting_team_ids_json",
                    models.JSONField(blank=True, default=None, null=True),
                ),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "champion_team",
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="seasons_won",
                        to="teams.team",
                    ),
                ),
                (
                    "league",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="seasons",
                        to="matches.league",
                    ),
                ),
                (
                    "teams",
                    models.ManyToManyField(
                        related_name="enrolled_seasons",
                        to="teams.team",
                    ),
                ),
            ],
        ),
        migrations.AddField(
            model_name="match",
            name="season",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="matches",
                to="matches.season",
            ),
        ),
    ]
