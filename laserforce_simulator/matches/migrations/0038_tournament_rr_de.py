from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0037_tournament_round_robin"),
    ]

    operations = [
        migrations.AlterField(
            model_name="tournament",
            name="format",
            field=models.CharField(
                choices=[
                    ("single_elimination", "Single elimination"),
                    ("double_elimination", "Double elimination"),
                    ("round_robin", "Round robin"),
                    ("round_robin_double_elim", "Round robin → Double elimination"),
                ],
                default="single_elimination",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="wb_advancers",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="tournament",
            name="lb_advancers",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
