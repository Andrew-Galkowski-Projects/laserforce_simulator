# FIN-02 — TeamSeasonFinance budget-level + games-played snapshot fields.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0050_league_finance_teamseasonfinance"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamseasonfinance",
            name="budget_scouting",
            field=models.PositiveSmallIntegerField(default=34),
        ),
        migrations.AddField(
            model_name="teamseasonfinance",
            name="budget_coaching",
            field=models.PositiveSmallIntegerField(default=34),
        ),
        migrations.AddField(
            model_name="teamseasonfinance",
            name="budget_facilities",
            field=models.PositiveSmallIntegerField(default=34),
        ),
        migrations.AddField(
            model_name="teamseasonfinance",
            name="games_played",
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
