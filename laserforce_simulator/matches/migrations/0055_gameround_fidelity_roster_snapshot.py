from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0054_season_map_rotation"),
    ]

    operations = [
        migrations.AddField(
            model_name="gameround",
            name="fidelity",
            field=models.CharField(
                choices=[
                    ("scores", "Scores"),
                    ("combat", "Combat"),
                    ("full", "Full"),
                ],
                default="full",
                max_length=6,
            ),
        ),
        migrations.AddField(
            model_name="gameround",
            name="roster_snapshot_json",
            field=models.JSONField(blank=True, default=None, null=True),
        ),
    ]
