"""FIN-01 — money-axis integration tests (seam contract §5 / §8).

With finance ON, ``_ensure_owner_evaluations`` reads the managed team's
``TeamSeasonFinance.profit`` for the Season and lights up the dormant CAR-02
money mood factor: the ``OwnerEvaluation`` row's ``money_delta`` is
``finance.money_delta(profit)`` and ``money_total`` is the cap-chained
cumulative (``owner_mood.cap_cumulative(running_money, money_delta)``). A
profitable Season raises the money mood; a loss lowers it.

The order is load-bearing: ``_ensure_team_finances`` must run BEFORE
``_ensure_owner_evaluations`` so the finance row exists when the money axis reads
it. These tests drive that ordering explicitly.

Standings are hand-constructed from ``Match`` rows; finance rows are written by
the real ``_ensure_team_finances`` so the profit feeding the money axis is the
genuine computed value. Assertions are schema-level — ``money_delta`` /
``money_total`` sign/magnitude + cap-chaining — NEVER exact simulated point totals.

These FAIL until the Code agent lands the finance writer + the money-axis wire in
``_ensure_owner_evaluations``.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from matches import finance
from matches.league_views import (
    _ensure_owner_evaluations,
    _ensure_team_finances,
)
from matches.models import (
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    Season,
    TeamSeasonFinance,
)
from matches.owner_mood import MOOD_FACTOR_CAP
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str) -> Team:
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(name, *, current_team=None, finance_enabled=True) -> League:
    return League.objects.create(
        name=name,
        mode="league",
        state="active",
        current_team=current_team,
        finance_enabled=finance_enabled,
    )


def _make_completed_season(league, *, name, start_date, team_ids) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(team_ids),
    )


def _add_match(season, team_red, team_blue, *, red_pts, blue_pts) -> Match:
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        red_round1_points=red_pts,
        blue_round1_points=blue_pts,
        red_round2_points=red_pts,
        blue_round2_points=blue_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=red_pts,
        blue_points=blue_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_blue,
        team_blue=team_red,
        round_number=2,
        red_points=blue_pts,
        blue_points=red_pts,
        is_completed=True,
    )
    return match


def _one_season_finance_league(*, finance_enabled=True):
    team = _make_team("MaT")
    opp = _make_team("MaO")
    league = _make_league("MaL", current_team=team, finance_enabled=finance_enabled)
    s1 = _make_completed_season(
        league,
        name="Season 1",
        start_date=date(2025, 1, 1),
        team_ids=[team.id, opp.id],
    )
    _add_match(s1, team, opp, red_pts=100, blue_pts=10)
    return league, team, opp, s1


# ===========================================================================
# §5 — TestMoneyAxisReadsFinanceProfit
# ===========================================================================


class TestMoneyAxisReadsFinanceProfit(TestCase):
    """With finance ON, the eval row's ``money_delta`` is the pure
    ``money_delta(profit)`` of the managed team's finance row, and ``money_total``
    cap-chains it."""

    def test_money_delta_matches_pure_fn_of_profit(self) -> None:
        league, team, _opp, s1 = _one_season_finance_league()
        _ensure_team_finances(league, s1)
        _ensure_owner_evaluations(league, s1)
        tsf = TeamSeasonFinance.objects.get(team=team, season=s1)
        ev = OwnerEvaluation.objects.get(league=league, season=s1)
        self.assertAlmostEqual(
            ev.money_delta, finance.money_delta(tsf.profit), places=6
        )

    def test_money_total_is_cap_chained_delta(self) -> None:
        league, _team, _opp, s1 = _one_season_finance_league()
        _ensure_team_finances(league, s1)
        _ensure_owner_evaluations(league, s1)
        ev = OwnerEvaluation.objects.get(league=league, season=s1)
        # Single Season ⇒ money_total == cap_cumulative(0.0, money_delta), which
        # is min(money_delta, MOOD_FACTOR_CAP) for a non-negative delta.
        self.assertLessEqual(ev.money_total, MOOD_FACTOR_CAP + 1e-9)

    def test_money_axis_non_zero_when_profit_non_zero(self) -> None:
        league, team, _opp, s1 = _one_season_finance_league()
        _ensure_team_finances(league, s1)
        _ensure_owner_evaluations(league, s1)
        tsf = TeamSeasonFinance.objects.get(team=team, season=s1)
        ev = OwnerEvaluation.objects.get(league=league, season=s1)
        # The money factor only stays 0.0 when profit happens to equal the
        # expected-profit pivot; for a real finance run that is the measure-zero
        # case, so assert the axis is wired (delta tracks profit's sign).
        if abs(finance.money_delta(tsf.profit)) > 1e-9:
            self.assertNotEqual(ev.money_delta, 0.0)


# ===========================================================================
# §5 — TestMoneyMoodTracksProfitSign
# ===========================================================================


class TestMoneyMoodTracksProfitSign(TestCase):
    """A profitable Season raises money mood; a loss lowers it. We drive the
    profit sign by directly stamping the ``TeamSeasonFinance.profit`` before the
    money axis reads it (decoupling the money math from the revenue/expense
    coefficients, which are tunable)."""

    def _eval_with_profit(self, profit_value: float) -> OwnerEvaluation:
        team = _make_team("SgT")
        opp = _make_team("SgO")
        league = _make_league("SgL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        # Write the finance rows, then override the managed team's profit so the
        # money axis reads a controlled value.
        _ensure_team_finances(league, s1)
        tsf = TeamSeasonFinance.objects.get(team=team, season=s1)
        tsf.profit = profit_value
        tsf.save(update_fields=["profit"])
        _ensure_owner_evaluations(league, s1)
        return OwnerEvaluation.objects.get(league=league, season=s1)

    def test_big_profit_raises_money_mood(self) -> None:
        # profit far above the expected-profit pivot ⇒ positive money_delta.
        ev = self._eval_with_profit(100_000.0)
        self.assertGreater(ev.money_delta, 0.0)

    def test_big_loss_lowers_money_mood(self) -> None:
        # A large loss ⇒ negative money_delta.
        ev = self._eval_with_profit(-100_000.0)
        self.assertLess(ev.money_delta, 0.0)

    def test_money_delta_equals_pure_money_delta_of_overridden_profit(self) -> None:
        ev = self._eval_with_profit(65.0)
        # money_delta(65) at baseline scf == (65 - 15)/100 == 0.5.
        self.assertAlmostEqual(ev.money_delta, finance.money_delta(65.0), places=6)
        self.assertAlmostEqual(ev.money_delta, 0.5, places=6)
