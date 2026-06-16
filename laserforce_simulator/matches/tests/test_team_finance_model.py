"""FIN-01 — model/constraint tests for the persisted finance schema
(seam contract §1 / §2 / §3 / §8).

Pins:

* ``matches.models.TeamSeasonFinance`` — the immutable per-(Team, Season)
  revenue/expense/profit/hype snapshot: field defaults, ``Meta.ordering``, the
  ``uniq_team_season_finance`` UniqueConstraint, and the on_delete behaviour
  (``team`` SET_NULL keeps the finance history; ``season`` CASCADE drops it).
* ``teams.Player.salary`` — nullable ``FloatField`` defaulting to ``None``.
* The five ``teams.Team`` finance fields — ``budget_scouting`` /
  ``budget_coaching`` / ``budget_facilities`` (default ``34``), ``ticket_price``
  / ``cash`` (default ``0.0``).
* ``matches.League.finance_enabled`` — ``BooleanField`` defaulting to ``False``.

These hand-build ``League`` / ``Season`` / ``Team`` / ``Player`` rows directly —
NO simulator, NO simulated point totals. They FAIL until the Code agent lands the
``TeamSeasonFinance`` model + the field additions + the two FIN-01 migrations.
"""

from __future__ import annotations

from datetime import date

from django.db import IntegrityError, transaction
from django.test import TestCase

from matches.finance import DEFAULT_LEVEL
from matches.models import League, Season, TeamSeasonFinance
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "FinL", *, finance_enabled: bool = False) -> League:
    return League.objects.create(
        name=name, mode="league", state="active", finance_enabled=finance_enabled
    )


def _make_season(league: League, *, name: str = "Season 1") -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, 1),
        state="completed",
    )


def _make_team(prefix: str) -> Team:
    team, _ = make_team_with_slots(prefix)
    return team


def _make_finance(team, season, **overrides) -> TeamSeasonFinance:
    defaults = dict(team=team, season=season)
    defaults.update(overrides)
    return TeamSeasonFinance.objects.create(**defaults)


# ===========================================================================
# §1 — TestPlayerSalaryField
# ===========================================================================


class TestPlayerSalaryField(TestCase):
    """``Player.salary`` is a nullable FloatField defaulting to ``None``."""

    def test_salary_defaults_to_none(self) -> None:
        team = _make_team("Sal")
        # A fresh player created outside any finance flow has no salary.
        player = Player.objects.create(team=team, name="Salaryless")
        player.refresh_from_db()
        self.assertIsNone(player.salary)

    def test_salary_accepts_a_float(self) -> None:
        team = _make_team("Sal2")
        player = Player.objects.create(team=team, name="Paid")
        player.salary = 42000.5
        player.save(update_fields=["salary"])
        player.refresh_from_db()
        self.assertAlmostEqual(player.salary, 42000.5, places=6)

    def test_salary_nullable_round_trip(self) -> None:
        team = _make_team("Sal3")
        player = Player.objects.create(team=team, name="Nuller", salary=10.0)
        player.salary = None
        player.save(update_fields=["salary"])
        player.refresh_from_db()
        self.assertIsNone(player.salary)


# ===========================================================================
# §2 — TestTeamFinanceFields
# ===========================================================================


class TestTeamFinanceFields(TestCase):
    """The five ``Team`` finance fields and their neutral defaults."""

    def test_budget_defaults_are_default_level(self) -> None:
        team = Team.objects.create(name="BudgetTeam")
        team.refresh_from_db()
        self.assertEqual(team.budget_scouting, DEFAULT_LEVEL)
        self.assertEqual(team.budget_coaching, DEFAULT_LEVEL)
        self.assertEqual(team.budget_facilities, DEFAULT_LEVEL)

    def test_default_level_value_is_34(self) -> None:
        # The contract pins DEFAULT_LEVEL == 34 as the neutral budget level.
        team = Team.objects.create(name="DLTeam")
        self.assertEqual(team.budget_scouting, 34)

    def test_ticket_price_and_cash_default_to_zero(self) -> None:
        team = Team.objects.create(name="MoneyTeam")
        team.refresh_from_db()
        self.assertEqual(team.ticket_price, 0.0)
        self.assertEqual(team.cash, 0.0)

    def test_finance_fields_round_trip(self) -> None:
        team = Team.objects.create(name="RTTeam")
        team.budget_scouting = 80
        team.budget_coaching = 12
        team.budget_facilities = 100
        team.ticket_price = 25.5
        team.cash = 123456.0
        team.save(
            update_fields=[
                "budget_scouting",
                "budget_coaching",
                "budget_facilities",
                "ticket_price",
                "cash",
            ]
        )
        team.refresh_from_db()
        self.assertEqual(team.budget_scouting, 80)
        self.assertEqual(team.budget_coaching, 12)
        self.assertEqual(team.budget_facilities, 100)
        self.assertAlmostEqual(team.ticket_price, 25.5, places=6)
        self.assertAlmostEqual(team.cash, 123456.0, places=6)


# ===========================================================================
# §3 — TestLeagueFinanceEnabled
# ===========================================================================


class TestLeagueFinanceEnabled(TestCase):
    """``League.finance_enabled`` is a BooleanField defaulting to ``False``."""

    def test_defaults_to_false(self) -> None:
        league = League.objects.create(name="ToggleL", mode="league")
        league.refresh_from_db()
        self.assertFalse(league.finance_enabled)

    def test_can_be_set_true(self) -> None:
        league = League.objects.create(
            name="ToggleOnL", mode="league", finance_enabled=True
        )
        league.refresh_from_db()
        self.assertTrue(league.finance_enabled)


# ===========================================================================
# §3 — TestTeamSeasonFinanceFields
# ===========================================================================


class TestTeamSeasonFinanceFields(TestCase):
    """Field defaults + persisted round-trip for the snapshot row."""

    def test_revenue_expense_lines_default_to_zero(self) -> None:
        league = _make_league("TsfDefL")
        season = _make_season(league)
        team = _make_team("TsfDef")
        tsf = _make_finance(team, season)
        tsf.refresh_from_db()
        for name in (
            "ticket",
            "national_tv",
            "local_tv",
            "sponsor",
            "merch",
            "payroll",
            "scouting_cost",
            "coaching_cost",
            "facilities_cost",
            "luxury_tax",
            "min_payroll_penalty",
            "revenue",
            "expenses",
            "profit",
            "hype",
        ):
            self.assertEqual(getattr(tsf, name), 0.0, name)

    def test_created_at_auto_now_add(self) -> None:
        league = _make_league("TsfCaL")
        season = _make_season(league)
        tsf = _make_finance(_make_team("TsfCa"), season)
        self.assertIsNotNone(tsf.created_at)

    def test_full_round_trip(self) -> None:
        league = _make_league("TsfRtL")
        season = _make_season(league)
        team = _make_team("TsfRt")
        tsf = _make_finance(
            team,
            season,
            ticket=100.0,
            national_tv=200.0,
            local_tv=50.0,
            sponsor=30.0,
            merch=20.0,
            payroll=180.0,
            scouting_cost=10.0,
            coaching_cost=10.0,
            facilities_cost=10.0,
            luxury_tax=5.0,
            min_payroll_penalty=0.0,
            revenue=400.0,
            expenses=215.0,
            profit=185.0,
            hype=0.42,
        )
        tsf.refresh_from_db()
        self.assertEqual(tsf.team_id, team.id)
        self.assertEqual(tsf.season_id, season.id)
        self.assertAlmostEqual(tsf.revenue, 400.0, places=6)
        self.assertAlmostEqual(tsf.expenses, 215.0, places=6)
        self.assertAlmostEqual(tsf.profit, 185.0, places=6)
        self.assertAlmostEqual(tsf.hype, 0.42, places=6)


# ===========================================================================
# §3 — TestTeamSeasonFinanceMetaOrdering
# ===========================================================================


class TestTeamSeasonFinanceMetaOrdering(TestCase):
    """``Meta.ordering == ["season_id", "team_id"]``."""

    def test_meta_ordering_declared(self) -> None:
        self.assertEqual(
            list(TeamSeasonFinance._meta.ordering), ["season_id", "team_id"]
        )

    def test_default_queryset_ordered_by_season_then_team(self) -> None:
        league = _make_league("OrderL")
        s1 = _make_season(league, name="S1")
        s2 = _make_season(league, name="S2")
        ta = _make_team("OrdA")
        tb = _make_team("OrdB")
        # Insert scrambled.
        _make_finance(tb, s2)
        _make_finance(ta, s1)
        _make_finance(tb, s1)
        ordered = list(TeamSeasonFinance.objects.values_list("season_id", "team_id"))
        self.assertEqual(ordered, sorted(ordered))


# ===========================================================================
# §3 — TestUniqTeamSeasonFinance
# ===========================================================================


class TestUniqTeamSeasonFinance(TestCase):
    """The ``uniq_team_season_finance`` UniqueConstraint rejects a duplicate
    ``(team, season)`` row; the same team across different seasons is fine."""

    def test_constraint_name_declared(self) -> None:
        names = {c.name for c in TeamSeasonFinance._meta.constraints}
        self.assertIn("uniq_team_season_finance", names)

    def test_duplicate_team_season_rejected(self) -> None:
        league = _make_league("UniqL")
        season = _make_season(league)
        team = _make_team("Uniq")
        _make_finance(team, season)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                _make_finance(team, season)

    def test_same_team_different_season_allowed(self) -> None:
        league = _make_league("UniqOkL")
        s1 = _make_season(league, name="S1")
        s2 = _make_season(league, name="S2")
        team = _make_team("UniqOk")
        _make_finance(team, s1)
        _make_finance(team, s2)
        self.assertEqual(TeamSeasonFinance.objects.filter(team=team).count(), 2)

    def test_different_teams_same_season_allowed(self) -> None:
        league = _make_league("UniqOk2L")
        season = _make_season(league)
        ta = _make_team("UniqOk2A")
        tb = _make_team("UniqOk2B")
        _make_finance(ta, season)
        _make_finance(tb, season)
        self.assertEqual(TeamSeasonFinance.objects.filter(season=season).count(), 2)


# ===========================================================================
# §3 — TestTeamSeasonFinanceOnDelete
# ===========================================================================


class TestTeamSeasonFinanceOnDelete(TestCase):
    """``team`` SET_NULL (history kept); ``season`` CASCADE (history dropped)."""

    def test_team_delete_set_null(self) -> None:
        league = _make_league("SetNullL")
        season = _make_season(league)
        team = _make_team("SetNull")
        tsf = _make_finance(team, season)
        team.delete()
        tsf.refresh_from_db()
        self.assertIsNone(tsf.team_id)
        self.assertEqual(TeamSeasonFinance.objects.count(), 1)

    def test_season_delete_cascades(self) -> None:
        league = _make_league("CascadeL")
        season = _make_season(league)
        team = _make_team("Cascade")
        _make_finance(team, season)
        self.assertEqual(TeamSeasonFinance.objects.count(), 1)
        season.delete()
        self.assertEqual(TeamSeasonFinance.objects.count(), 0)

    def test_team_nullable_at_orm_level(self) -> None:
        league = _make_league("NullTeamL")
        season = _make_season(league)
        tsf = TeamSeasonFinance.objects.create(team=None, season=season)
        tsf.refresh_from_db()
        self.assertIsNone(tsf.team_id)


# ===========================================================================
# §3 — TestTeamSeasonFinanceRelatedNames
# ===========================================================================


class TestTeamSeasonFinanceRelatedNames(TestCase):
    """``team.season_finances`` / ``season.team_finances`` reverse accessors."""

    def test_team_reverse_accessor(self) -> None:
        league = _make_league("RevTeamL")
        season = _make_season(league)
        team = _make_team("RevTeam")
        tsf = _make_finance(team, season)
        self.assertEqual(list(team.season_finances.all()), [tsf])

    def test_season_reverse_accessor(self) -> None:
        league = _make_league("RevSeasonL")
        season = _make_season(league)
        team = _make_team("RevSeason")
        tsf = _make_finance(team, season)
        self.assertEqual(list(season.team_finances.all()), [tsf])
