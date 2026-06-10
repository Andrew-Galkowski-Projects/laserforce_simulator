# Generated for LG-02-Part2c-3e — SeasonPhase per-format tournament sub-config.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0046_seasonphase_format_cut"),
    ]

    operations = [
        migrations.AddField(
            model_name="seasonphase",
            name="final_series_length",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")],
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="semifinal_series_length",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")],
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="quarterfinal_series_length",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")],
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="earlier_series_length",
            field=models.PositiveSmallIntegerField(
                choices=[(1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")],
                default=1,
            ),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="wb_advancers",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="lb_advancers",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="seasonphase",
            name="swiss_rounds",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
