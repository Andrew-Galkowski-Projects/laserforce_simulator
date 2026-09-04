# UX-01 — Team.manager (the Account a Team belongs to, ADR-0038).
#
# Single AddField, NO RunPython / NO backfill (ADR-0004 disposable-data
# posture; ADR-0038 rejects a superuser backfill as vacuous — a custom user
# model means an empty user table on every existing database). Existing Teams
# stay manager=NULL, i.e. **Unmanaged rows**, until `manage.py claim_unmanaged`.

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("teams", "0014_player_team_health_injury"),
    ]

    operations = [
        migrations.AddField(
            model_name="team",
            name="manager",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="teams",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
    ]
