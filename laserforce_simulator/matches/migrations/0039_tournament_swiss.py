from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0038_tournament_rr_de"),
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
                    ("swiss", "Swiss"),
                ],
                default="single_elimination",
                max_length=32,
            ),
        ),
        migrations.AlterField(
            model_name="bracketnode",
            name="bracket_type",
            field=models.CharField(
                choices=[
                    ("winners", "Winners bracket"),
                    ("losers", "Losers bracket"),
                    ("grand_final", "Grand final"),
                    ("round_robin", "Round robin"),
                    ("swiss", "Swiss"),
                ],
                default="winners",
                max_length=12,
            ),
        ),
        migrations.AddField(
            model_name="tournament",
            name="swiss_rounds",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
