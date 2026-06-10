"""LG-03 — Season.season_awards_json lazy-awards cache.

Single ``AddField`` adding the ``season_awards_json`` JSONField cache to
``Season`` (the ``highlights_json`` / ``starting_team_ids_json`` JSONField-cache
precedent). ``None`` = not yet computed (cache miss); a dict (the §3 shape) =
warmed cache.

No ``RunPython`` / data migration / backfill — existing completed Seasons take
``default=None`` and warm lazily on first awards render (ADR-0004
disposable-data posture).
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0047_seasonphase_tournament_subconfig"),
    ]

    operations = [
        migrations.AddField(
            model_name="season",
            name="season_awards_json",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
