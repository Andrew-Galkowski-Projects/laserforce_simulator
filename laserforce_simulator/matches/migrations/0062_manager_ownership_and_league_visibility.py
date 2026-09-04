# UX-01 — the four matches-app Ownership roots gain `manager`, and League
# gains the dormant `visibility` column (ADR-0038).
#
# `League` and `Tournament` are always roots; `Match` is a root only while
# `season_id IS NULL` (a sandbox Match) and `GameRound` only while
# `match_id IS NULL` (a standalone Round) — every other row derives its
# **Manager** by traversing its parent FK, so it needs no column here.
#
# AddFields only, NO RunPython / NO backfill (ADR-0004 disposable-data
# posture; ADR-0038 rejects a superuser backfill as vacuous — a custom user
# model means an empty user table on every existing database). Existing rows
# stay manager=NULL, i.e. **Unmanaged rows**, until `manage.py claim_unmanaged`.
#
# `visibility` ships DORMANT: a column plus one create-League control, with no
# read path anywhere this slice.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("matches", "0061_conference_map_rotation"),
    ]

    operations = [
        migrations.AddField(
            model_name="league",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="leagues",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tournaments",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="match",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="matches",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="gameround",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="game_rounds",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AddField(
            model_name="league",
            name="visibility",
            field=models.CharField(
                choices=[("closed", "Closed"), ("open", "Open")],
                default="closed",
                max_length=16,
            ),
        ),
    ]
