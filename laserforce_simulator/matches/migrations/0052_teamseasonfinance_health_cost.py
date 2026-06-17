# FIN-04 — TeamSeasonFinance health-budget cost line.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("matches", "0051_teamseasonfinance_budget_levels"),
    ]

    operations = [
        migrations.AddField(
            model_name="teamseasonfinance",
            name="health_cost",
            field=models.FloatField(default=0.0),
        ),
    ]
