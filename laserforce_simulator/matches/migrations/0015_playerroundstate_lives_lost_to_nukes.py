from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0014_playerroundstate_medic_lives_removed_from_nuke"),
    ]

    operations = [
        migrations.AddField(
            model_name="playerroundstate",
            name="lives_lost_to_nukes",
            field=models.IntegerField(default=0),
        ),
    ]
