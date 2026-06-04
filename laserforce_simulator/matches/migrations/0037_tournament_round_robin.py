# Generated for LG-02c — round-robin tournament format.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0036_bracketnode_double_elimination"),
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
                ],
                default="winners",
                max_length=12,
            ),
        ),
    ]
