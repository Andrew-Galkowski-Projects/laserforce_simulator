"""FIN-01 — the byte-identical-when-OFF invariant (seam contract §5 / §8).

The load-bearing inertness guarantee: with ``League.finance_enabled=False`` the
entire finance subsystem is inert —

* ``_ensure_team_finances`` writes **zero** ``TeamSeasonFinance`` rows;
* every ``Player.salary`` stays ``None`` (no recompute);
* the ``OwnerEvaluation`` rows produced by ``_ensure_owner_evaluations`` are
  **byte-identical** to a pre-FIN-01 / no-finance run — ``money_delta == 0.0``,
  ``money_total == 0.0``, and identical ``verdict`` / ``hot_seat_level`` /
  ``wins_*`` / ``playoffs_*`` to a finance-disabled run of the same fixtures.

The cross-check compares two structurally-identical Leagues — one finance-OFF
and one with no finance involvement at all — and asserts their eval rows match
field-for-field. NO simulator, NO simulated point totals; standings are
hand-built so the verdict is deterministic.

These FAIL until the Code agent lands the gated writer + the gated money axis +
the gated salary recompute.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

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
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str) -> Team:
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(name, *, current_team=None, finance_enabled=False) -> League:
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


def _build_two_season_league(prefix: str, *, finance_enabled: bool):
    """A 2-season finance-OFF (or finance-uninvolved) League. The manager team
    wins Season 1 dominantly and loses Season 2 dominantly so the verdict math
    has both signs to exercise."""
    team = _make_team(f"{prefix}T")
    opp = _make_team(f"{prefix}O")
    league = _make_league(
        f"{prefix}L", current_team=team, finance_enabled=finance_enabled
    )
    s1 = _make_completed_season(
        league,
        name="Season 1",
        start_date=date(2024, 1, 1),
        team_ids=[team.id, opp.id],
    )
    s2 = _make_completed_season(
        league,
        name="Season 2",
        start_date=date(2025, 1, 1),
        team_ids=[team.id, opp.id],
    )
    _add_match(s1, team, opp, red_pts=100, blue_pts=1)  # manager wins
    _add_match(s2, opp, team, red_pts=100, blue_pts=1)  # manager loses
    return league, team, s2


# ===========================================================================
# §5 — TestNoFinanceRowsWhenOff
# ===========================================================================


class TestNoFinanceRowsWhenOff(TestCase):
    """``finance_enabled=False`` ⇒ the writer writes zero ``TeamSeasonFinance``
    rows even when called explicitly."""

    def test_ensure_writes_zero_rows(self) -> None:
        league, _team, s2 = _build_two_season_league("Off", finance_enabled=False)
        _ensure_team_finances(league, s2)
        self.assertEqual(TeamSeasonFinance.objects.count(), 0)


# ===========================================================================
# §5 — TestSalaryStaysNoneWhenOff
# ===========================================================================


class TestSalaryStaysNoneWhenOff(TestCase):
    """``finance_enabled=False`` ⇒ no ``Player.salary`` is ever set."""

    def test_all_player_salaries_none(self) -> None:
        league, _team, s2 = _build_two_season_league("SalOff", finance_enabled=False)
        # Run the full pre-season chain order (finance first, then evals).
        _ensure_team_finances(league, s2)
        _ensure_owner_evaluations(league, s2)
        # Not a single Player in the DB has a non-null salary.
        self.assertFalse(
            Player.objects.exclude(salary__isnull=True).exists(),
            "no Player.salary should be set when finance is OFF",
        )


# ===========================================================================
# §5 — TestOwnerEvaluationByteIdenticalWhenOff
# ===========================================================================


class TestOwnerEvaluationByteIdenticalWhenOff(TestCase):
    """The OwnerEvaluation rows of a finance-OFF League are byte-identical to a
    no-finance run of the same fixtures: ``money_delta == 0.0``,
    ``money_total == 0.0``, identical verdict / hot_seat_level / wins_* /
    playoffs_*."""

    def test_money_axis_is_zero_when_off(self) -> None:
        league, _team, s2 = _build_two_season_league("ZeroMoney", finance_enabled=False)
        _ensure_team_finances(league, s2)
        _ensure_owner_evaluations(league, s2)
        for ev in OwnerEvaluation.objects.filter(league=league):
            self.assertEqual(ev.money_delta, 0.0)
            self.assertEqual(ev.money_total, 0.0)

    def test_eval_rows_match_a_finance_uninvolved_run(self) -> None:
        # League A: finance OFF. League B: structurally identical, also OFF (the
        # "no finance involvement at all" control). Their eval rows must match
        # field-for-field across the whole tenure.
        league_a, _ta, s2a = _build_two_season_league("ByteA", finance_enabled=False)
        league_b, _tb, s2b = _build_two_season_league("ByteB", finance_enabled=False)

        # League A runs the FIN-01 pre-season chain (finance-ensure then evals).
        _ensure_team_finances(league_a, s2a)
        _ensure_owner_evaluations(league_a, s2a)

        # League B runs ONLY the eval writer (the pre-FIN-01 path) — no finance
        # ensure call at all.
        _ensure_owner_evaluations(league_b, s2b)

        evs_a = list(
            OwnerEvaluation.objects.filter(league=league_a).order_by("season_id")
        )
        evs_b = list(
            OwnerEvaluation.objects.filter(league=league_b).order_by("season_id")
        )
        self.assertEqual(len(evs_a), len(evs_b))
        self.assertGreater(len(evs_a), 0)
        for a, b in zip(evs_a, evs_b):
            self.assertEqual(a.verdict, b.verdict)
            self.assertEqual(a.hot_seat_level, b.hot_seat_level)
            self.assertAlmostEqual(a.wins_delta, b.wins_delta, places=9)
            self.assertAlmostEqual(a.playoffs_delta, b.playoffs_delta, places=9)
            self.assertAlmostEqual(a.wins_total, b.wins_total, places=9)
            self.assertAlmostEqual(a.playoffs_total, b.playoffs_total, places=9)
            self.assertEqual(a.money_delta, 0.0)
            self.assertEqual(b.money_delta, 0.0)
            self.assertEqual(a.money_total, 0.0)
            self.assertEqual(b.money_total, 0.0)
