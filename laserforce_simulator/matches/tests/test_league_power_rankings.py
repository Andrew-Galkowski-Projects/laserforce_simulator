"""LG-01z-b -- Power Rankings logic.

* ``TestComputePowerRankings*`` / ``TestSortPowerRankings`` /
  ``TestNoDjangoImportsLeaked`` -- pure-unit coverage of
  ``matches/power_rankings_logic.py`` (no DB, no Django in the assertion path).
* ``TestLg07aMemberNightPowerExclusion`` -- the service
  ``league_screen_services.build_power_rankings`` leaves member-night Matches
  and drawn Teams out (ADR-0033).

The HTML screen's view tests went with the screen (ANG-08); the API serves the
same rows (``GET /api/leagues/<id>/power-rankings/``).
"""

from __future__ import annotations

import unittest
from datetime import date

from django.test import TestCase

from matches.league_screen_services import build_power_rankings
from matches.models import GameRound, League, Match, Season, SeasonPhase
from matches.power_rankings_logic import (
    SORT_KEYS,
    PowerRankingInput,
    coerce_dir,
    coerce_sort,
    compute_power_rankings,
    sort_power_rankings,
)
from matches.tests.conftest import make_team_with_slots
from laserforce_simulator.testing import run_project_python
from teams.models import Team

# ===========================================================================
# Pure-unit: compute_power_rankings
# ===========================================================================


def _inp(team_id, name, rating, win_pct, score_diff) -> PowerRankingInput:
    return PowerRankingInput(
        team_id=team_id,
        team_name=name,
        mean_rating=rating,
        win_pct=win_pct,
        avg_score_diff=score_diff,
    )


class TestComputePowerRankingsEmpty(unittest.TestCase):
    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(compute_power_rankings([]), [])


class TestComputePowerRankingsRanking(unittest.TestCase):
    def test_highest_sum_ranks_first(self) -> None:
        # Team A dominates every component; Team B is the floor on all three.
        rows = compute_power_rankings(
            [
                _inp(1, "Alpha", rating=90.0, win_pct=1.0, score_diff=50.0),
                _inp(2, "Bravo", rating=10.0, win_pct=0.0, score_diff=-50.0),
            ]
        )
        self.assertEqual([r.team_id for r in rows], [1, 2])
        self.assertEqual(rows[0].rank, 1)
        self.assertEqual(rows[1].rank, 2)
        # Top team normalizes to 1+1+1 = 3.0; floor team to 0+0+0 = 0.0.
        self.assertAlmostEqual(rows[0].power_score, 3.0)
        self.assertAlmostEqual(rows[1].power_score, 0.0)

    def test_normalization_is_per_component_min_max(self) -> None:
        rows = compute_power_rankings(
            [
                _inp(1, "Alpha", rating=100.0, win_pct=0.5, score_diff=0.0),
                _inp(2, "Bravo", rating=50.0, win_pct=0.5, score_diff=0.0),
                _inp(3, "Charlie", rating=0.0, win_pct=0.5, score_diff=0.0),
            ]
        )
        by_id = {r.team_id: r for r in rows}
        # rating spans 0..100 -> 1.0 / 0.5 / 0.0.
        self.assertAlmostEqual(by_id[1].norm_rating, 1.0)
        self.assertAlmostEqual(by_id[2].norm_rating, 0.5)
        self.assertAlmostEqual(by_id[3].norm_rating, 0.0)
        # win_pct identical across all -> 0.0 contribution from that component.
        self.assertAlmostEqual(by_id[1].norm_win_pct, 0.0)
        self.assertAlmostEqual(by_id[2].norm_win_pct, 0.0)

    def test_tie_breaks_on_team_name_ascending(self) -> None:
        # Identical inputs => all components normalize to 0.0 => power 0.0 each;
        # tie broken by name ascending.
        rows = compute_power_rankings(
            [
                _inp(1, "Zulu", rating=50.0, win_pct=0.5, score_diff=0.0),
                _inp(2, "Alpha", rating=50.0, win_pct=0.5, score_diff=0.0),
                _inp(3, "Mike", rating=50.0, win_pct=0.5, score_diff=0.0),
            ]
        )
        self.assertEqual([r.team_name for r in rows], ["Alpha", "Mike", "Zulu"])
        self.assertEqual([r.rank for r in rows], [1, 2, 3])

    def test_score_diff_can_be_negative_and_still_normalizes(self) -> None:
        rows = compute_power_rankings(
            [
                _inp(1, "Alpha", rating=50.0, win_pct=0.5, score_diff=-10.0),
                _inp(2, "Bravo", rating=50.0, win_pct=0.5, score_diff=-30.0),
            ]
        )
        by_id = {r.team_id: r for r in rows}
        # -10 is the max (higher), -30 the min -> 1.0 / 0.0.
        self.assertAlmostEqual(by_id[1].norm_score_diff, 1.0)
        self.assertAlmostEqual(by_id[2].norm_score_diff, 0.0)

    def test_single_team_normalizes_to_zero_no_div_by_zero(self) -> None:
        rows = compute_power_rankings(
            [_inp(1, "Solo", rating=77.0, win_pct=0.9, score_diff=12.0)]
        )
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].rank, 1)
        self.assertAlmostEqual(rows[0].power_score, 0.0)
        # Raw values are preserved for display even when normalized to 0.
        self.assertAlmostEqual(rows[0].mean_rating, 77.0)
        self.assertAlmostEqual(rows[0].win_pct, 0.9)
        self.assertAlmostEqual(rows[0].avg_score_diff, 12.0)


class TestSortPowerRankings(unittest.TestCase):
    """Pure-unit coverage of ``sort_power_rankings`` + the coerce helpers."""

    def _ranked(self):
        # Alpha #1 (power 3.0), Bravo #2, Charlie #3 (power 0.0).
        return compute_power_rankings(
            [
                _inp(1, "Alpha", rating=90.0, win_pct=1.0, score_diff=50.0),
                _inp(2, "Bravo", rating=50.0, win_pct=0.5, score_diff=0.0),
                _inp(3, "Charlie", rating=10.0, win_pct=0.0, score_diff=-50.0),
            ]
        )

    def test_coerce_sort_accepts_every_key(self) -> None:
        for key in SORT_KEYS:
            self.assertEqual(coerce_sort(key), key)

    def test_coerce_sort_falls_back_to_rank(self) -> None:
        for bad in (None, "", "bogus", "RANK"):
            self.assertEqual(coerce_sort(bad), "rank")

    def test_coerce_dir_fallback(self) -> None:
        self.assertEqual(coerce_dir("asc"), "asc")
        self.assertEqual(coerce_dir("desc"), "desc")
        for bad in (None, "", "DESC", "up"):
            self.assertEqual(coerce_dir(bad), "asc")

    def test_default_sort_is_rank_ascending(self) -> None:
        rows = sort_power_rankings(self._ranked(), "rank", "asc")
        self.assertEqual([r.rank for r in rows], [1, 2, 3])
        self.assertEqual([r.team_name for r in rows], ["Alpha", "Bravo", "Charlie"])

    def test_rank_descending_reverses(self) -> None:
        rows = sort_power_rankings(self._ranked(), "rank", "desc")
        self.assertEqual([r.rank for r in rows], [3, 2, 1])

    def test_sort_by_team_name(self) -> None:
        rows = sort_power_rankings(self._ranked(), "team", "asc")
        self.assertEqual([r.team_name for r in rows], ["Alpha", "Bravo", "Charlie"])
        rows = sort_power_rankings(self._ranked(), "team", "desc")
        self.assertEqual([r.team_name for r in rows], ["Charlie", "Bravo", "Alpha"])

    def test_sort_by_each_metric_asc_and_desc(self) -> None:
        for key in ("mean_rating", "win_pct", "avg_score_diff", "power_score"):
            asc = sort_power_rankings(self._ranked(), key, "asc")
            desc = sort_power_rankings(self._ranked(), key, "desc")
            attr = SORT_KEYS[key]
            asc_vals = [getattr(r, attr) for r in asc]
            self.assertEqual(asc_vals, sorted(asc_vals), f"{key} asc not ascending")
            self.assertEqual(
                [r.team_id for r in desc],
                [r.team_id for r in reversed(asc)],
                f"{key} desc is not the reverse of asc",
            )

    def test_rank_preserved_regardless_of_sort_column(self) -> None:
        # Sorting by a non-rank column reorders rows but each keeps its true
        # power rank (Alpha is always rank 1).
        rows = sort_power_rankings(self._ranked(), "team", "desc")
        by_name = {r.team_name: r.rank for r in rows}
        self.assertEqual(by_name["Alpha"], 1)
        self.assertEqual(by_name["Charlie"], 3)


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.power_rankings_logic`` must not transitively import Django.

    Spawn a fresh subprocess, ``import matches.power_rankings_logic``, then
    walk ``sys.modules`` and assert no entry matches the ``django`` prefix.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.power_rankings_logic  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = run_project_python(script, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.power_rankings_logic.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


# ===========================================================================
# LG-07a -- member-night Matches excluded from Power Rankings (ADR-0033)
# ===========================================================================


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _two_round_match(season, red, blue, *, phase=None, red_wins: bool = True) -> None:
    """A completed two-round Match where one side takes both rounds."""
    match = Match.objects.create(
        team_red=red,
        team_blue=blue,
        season=season,
        season_phase=phase,
        red_round1_points=100 if red_wins else 0,
        blue_round1_points=0 if red_wins else 100,
        red_round2_points=100 if red_wins else 0,
        blue_round2_points=0 if red_wins else 100,
        is_completed=True,
    )
    for number, (r, b) in enumerate(((red, blue), (blue, red)), start=1):
        winner_is_red_side = (r == red) == red_wins
        GameRound.objects.create(
            match=match,
            team_red=r,
            team_blue=b,
            round_number=number,
            red_points=100 if winner_is_red_side else 0,
            blue_points=0 if winner_is_red_side else 100,
            is_completed=True,
        )


class TestLg07aMemberNightPowerExclusion(TestCase):
    """A member-night Match adds NO drawn-team row (ADR-0033: member nights
    are social, not ranked)."""

    def setUp(self) -> None:
        self.league = League.objects.create(name="MnPR")
        self.season, (self.team_a, self.team_b) = _make_active_season(self.league)
        _two_round_match(self.season, self.team_a, self.team_b)
        mn = SeasonPhase.objects.create(
            season=self.season, ordinal=2, phase_type="member_night"
        )
        self.da = Team.objects.create(name="MN PR Draw A", is_draw_team=True)
        self.db = Team.objects.create(name="MN PR Draw B", is_draw_team=True)
        _two_round_match(self.season, self.da, self.db, phase=mn)

    def _ranked_team_ids(self) -> set[int]:
        rows = build_power_rankings(self.season, {"match__season": self.season})
        return {row.team_id for row, _team in rows}

    def test_drawn_teams_not_ranked(self) -> None:
        ranked = self._ranked_team_ids()
        self.assertNotIn(self.da.id, ranked)
        self.assertNotIn(self.db.id, ranked)

    def test_enrolled_teams_still_ranked(self) -> None:
        self.assertEqual(self._ranked_team_ids(), {self.team_a.id, self.team_b.id})
