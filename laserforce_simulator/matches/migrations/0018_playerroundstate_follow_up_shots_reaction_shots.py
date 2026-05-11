from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0017_playerroundstate_times_tagged_in_reset_window"),
    ]

    operations = [
        migrations.AddField(
            model_name="playerroundstate",
            name="follow_up_shots",
            field=models.IntegerField(default=0),
        ),
        migrations.AddField(
            model_name="playerroundstate",
            name="reaction_shots",
            field=models.IntegerField(default=0),
        ),
    ]
