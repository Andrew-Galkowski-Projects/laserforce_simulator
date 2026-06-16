"""FIN-01 — tests for the lazy writer ``matches.league_views._ensure_team_finances``
plus the salary-recompute sites (seam contract §5 / §8).

The writer ensures one ``TeamSeasonFinance`` row per (Team, Season) for every
completed Season of a finance-enabled League up to and including ``up_to_season``,
written oldest→newest in Season order so the hype loop carries correctly. It is
``get_or_create``-keyed on ``(team, season)`` (idempotent — a present row left
untouched, no backfill). First-season hype seed: ``prev_hype=0.0`` /
``winp_old=0.5``. Cash carries across seasons (``Team.cash += profit``). Salary is
recomputed from ``overall_rating`` by ``_write_baseline_ratings`` /
``_develop_league_for_new_season`` — gated on ``finance_enabled``.

Standings are hand-constructed from ``Match`` rows (the LG-01c / CAR-02
fixture-pattern) so ``winp`` is well-defined; assertions are schema-level —
row presence / Season order / hype carry / cash carry / salary recompute —
NEVER exact simulated point totals.

These FAIL until the Code agent lands ``TeamSeasonFinance`` + the writer + the
salary-recompute additions. Reuses the LG-01 ``current_team`` FK.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase

from matches import finance
from matches.league_views import _ensure_team_finances
from matches.models import (
    GameRound,
    League,
    Match,
    Season,
    TeamSeasonFinance,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str) -> Team:
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(
    name: str, *, current_team=None, finance_enabled: bool = True
) -> League:
    return League.objects.create(
        name=name,
        mode="league",
        state="active",
        current_team=current_team,
        finance_enabled=finance_enabled,
    )


def _make_completed_season(
    league: League,
    *,
    name: str,
    start_date: date,
    team_ids: list[int],
) -> Season:
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


# ===========================================================================
# §5 — TestEnsureWritesOneRowPerTeamSeason
# ===========================================================================


class TestEnsureWritesOneRowPerTeamSeason(TestCase):
    """One ``TeamSeasonFinance`` row per (enrolled Team, completed Season) up to
    and including ``up_to_season``, in ascending Season order."""

    def _two_team_two_season(self):
        team = _make_team("WT")
        opp = _make_team("WO")
        league = _make_league("WL", current_team=team)
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
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        _add_match(s2, team, opp, red_pts=100, blue_pts=10)
        return league, team, opp, s1, s2

    def test_one_row_per_team_per_season(self) -> None:
        league, _team, _opp, _s1, s2 = self._two_team_two_season()
        _ensure_team_finances(league, s2)
        # 2 teams x 2 seasons == 4 rows.
        self.assertEqual(TeamSeasonFinance.objects.count(), 4)

    def test_rows_written_in_ascending_season_order(self) -> None:
        league, team, _opp, s1, s2 = self._two_team_two_season()
        _ensure_team_finances(league, s2)
        season_ids = list(
            TeamSeasonFinance.objects.filter(team=team)
            .order_by("id")
            .values_list("season_id", flat=True)
        )
        self.assertEqual(season_ids, [s1.id, s2.id])

    def test_up_to_season_bounds_the_set(self) -> None:
        league, _team, _opp, s1, s2 = self._two_team_two_season()
        _ensure_team_finances(league, s1)
        season_ids = set(TeamSeasonFinance.objects.values_list("season_id", flat=True))
        self.assertEqual(season_ids, {s1.id})
        self.assertNotIn(s2.id, season_ids)


# ===========================================================================
# §5 — TestEnsureIdempotent
# ===========================================================================


class TestEnsureIdempotent(TestCase):
    """A second call writes no new rows and leaves existing rows untouched."""

    def _setup(self):
        team = _make_team("IdT")
        opp = _make_team("IdO")
        league = _make_league("IdL", current_team=team)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        return league, team, s1

    def test_second_call_writes_no_new_rows(self) -> None:
        league, _team, s1 = self._setup()
        _ensure_team_finances(league, s1)
        count_after_first = TeamSeasonFinance.objects.count()
        _ensure_team_finances(league, s1)
        self.assertEqual(TeamSeasonFinance.objects.count(), count_after_first)

    def test_existing_row_left_untouched(self) -> None:
        league, team, s1 = self._setup()
        _ensure_team_finances(league, s1)
        row = TeamSeasonFinance.objects.get(team=team, season=s1)
        before = (row.revenue, row.expenses, row.profit, row.hype)
        _ensure_team_finances(league, s1)
        row.refresh_from_db()
        after = (row.revenue, row.expenses, row.profit, row.hype)
        self.assertEqual(before, after)


# ===========================================================================
# §5 — TestEnsureToggleOff
# ===========================================================================


class TestEnsureToggleOff(TestCase):
    """``finance_enabled=False`` ⇒ the writer writes ZERO rows (the gate sits on
    top of the existing career-league gate)."""

    def test_disabled_league_writes_zero_rows(self) -> None:
        team = _make_team("OffT")
        opp = _make_team("OffO")
        league = _make_league("OffL", current_team=team, finance_enabled=False)
        s1 = _make_completed_season(
            league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            team_ids=[team.id, opp.id],
        )
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        _ensure_team_finances(league, s1)
        self.assertEqual(TeamSeasonFinance.objects.count(), 0)


# ===========================================================================
# §5 — TestEnsureHypeCarry
# ===========================================================================


class TestEnsureHypeCarry(TestCase):
    """Hype carries across seasons (the prior Season's ``hype`` feeds the next
    Season's ``prev_hype``); the first Season seeds ``prev_hype=0.0`` /
    ``winp_old=0.5``."""

    def _winning_streak_league(self, n_seasons: int):
        team = _make_team("HT")
        opp = _make_team("HO")
        league = _make_league("HL", current_team=team)
        seasons = []
        for i in range(n_seasons):
            s = _make_completed_season(
                league,
                name=f"Season {i + 1}",
                start_date=date(2020 + i, 1, 1),
                team_ids=[team.id, opp.id],
            )
            # Manager team dominates every Season ⇒ winp well above 0.55.
            _add_match(s, team, opp, red_pts=100, blue_pts=1)
            seasons.append(s)
        return league, team, seasons

    def test_first_season_seed_hype_starts_from_zero_baseline(self) -> None:
        # First Season: prev_hype=0.0, winp_old=0.5. A dominant first Season
        # should produce a small positive hype via the winp loop.
        league, team, seasons = self._winning_streak_league(1)
        _ensure_team_finances(league, seasons[0])
        row = TeamSeasonFinance.objects.get(team=team, season=seasons[0])
        # compute_hype(0.0, winp>0.55, 0.5) — bounded to [0,1], should be >= 0.
        expected_floor = finance.compute_hype(0.0, 1.0, 0.5)
        self.assertGreaterEqual(row.hype, 0.0)
        # And not exceed the dominant-season single-step value.
        self.assertLessEqual(row.hype, expected_floor + 1e-9)

    def test_hype_carries_across_two_seasons(self) -> None:
        league, team, seasons = self._winning_streak_league(2)
        _ensure_team_finances(league, seasons[-1])
        row1 = TeamSeasonFinance.objects.get(team=team, season=seasons[0])
        row2 = TeamSeasonFinance.objects.get(team=team, season=seasons[1])
        # Season 2's prev_hype is Season 1's hype; a continued dominant run keeps
        # hype non-decreasing across the two snapshots.
        self.assertGreaterEqual(row2.hype, row1.hype - 1e-9)

    def test_hype_bounded_to_unit_interval(self) -> None:
        league, team, seasons = self._winning_streak_league(8)
        _ensure_team_finances(league, seasons[-1])
        for row in TeamSeasonFinance.objects.filter(team=team):
            self.assertGreaterEqual(row.hype, 0.0)
            self.assertLessEqual(row.hype, 1.0 + 1e-9)


# ===========================================================================
# §5 — TestEnsureCashCarry
# ===========================================================================


class TestEnsureCashCarry(TestCase):
    """``Team.cash`` carries across seasons (``cash += profit`` per written
    snapshot)."""

    def test_cash_accumulates_profit(self) -> None:
        team = _make_team("CashT")
        opp = _make_team("CashO")
        league = _make_league("CashL", current_team=team)
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
        _add_match(s1, team, opp, red_pts=100, blue_pts=10)
        _add_match(s2, team, opp, red_pts=100, blue_pts=10)
        team.refresh_from_db()
        cash_before = team.cash
        _ensure_team_finances(league, s2)
        team.refresh_from_db()
        # cash carried: post-ensure cash == cash_before + sum of both seasons'
        # profit for this team.
        total_profit = sum(
            TeamSeasonFinance.objects.filter(team=team).values_list("profit", flat=True)
        )
        self.assertAlmostEqual(team.cash, cash_before + total_profit, places=4)


# ===========================================================================
# §5 — TestSalaryRecompute (baseline + develop write sites)
# ===========================================================================


class TestSalaryRecomputeBaseline(TestCase):
    """``_write_baseline_ratings`` sets ``Player.salary`` from
    ``salary_for_overall(overall_rating)`` ONLY when the Season's League is
    finance-enabled; otherwise salary stays ``None`` (byte-identical to today)."""

    def _enabled_season_with_players(self):
        team = _make_team("BSal")
        league = _make_league("BSalL", current_team=team)
        season = Season.objects.create(
            league=league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            state="draft",
        )
        season.teams.add(team)
        return league, season, team

    def test_baseline_sets_salary_when_finance_enabled(self) -> None:
        from matches.league_views import _write_baseline_ratings

        _league, season, team = self._enabled_season_with_players()
        players = list(team.active_players)
        _write_baseline_ratings(season, players)
        for player in players:
            player.refresh_from_db()
            self.assertIsNotNone(player.salary, f"{player.name} salary should be set")
            self.assertAlmostEqual(
                player.salary,
                finance.salary_for_overall(player.overall_rating),
                places=4,
            )

    def test_baseline_leaves_salary_none_when_finance_disabled(self) -> None:
        from matches.league_views import _write_baseline_ratings

        team = _make_team("BSalOff")
        league = _make_league("BSalOffL", current_team=team, finance_enabled=False)
        season = Season.objects.create(
            league=league,
            name="Season 1",
            start_date=date(2025, 1, 1),
            state="draft",
        )
        season.teams.add(team)
        players = list(team.active_players)
        _write_baseline_ratings(season, players)
        for player in players:
            player.refresh_from_db()
            self.assertIsNone(player.salary)
