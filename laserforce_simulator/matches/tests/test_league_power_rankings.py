"""LG-01z-b — Tests for the Power Rankings league screen.

Two layers:

* ``TestComputePowerRankings*`` / ``TestNoDjangoImportsLeaked`` — pure-unit
  coverage of ``matches/power_rankings_logic.py`` (no DB, no Django in the
  assertion path).
* ``TestPowerRankings*`` — Django ``TestCase`` coverage of the view
  ``matches.league_screens.power_rankings.power_rankings``. The view is NOT
  yet URL-wired (the orchestrator wires the ``league_power_rankings`` route
  centrally), so these tests call the view directly via ``RequestFactory``
  with a real session attached — mirroring the LG-01z game-log precedent.

Fixtures are hand-constructed ``Match`` + ``GameRound`` rows; LG-01z runs
NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.power_rankings import power_rankings
from matches.models import GameRound, League, Match, Season
from matches.power_rankings_logic import (
    SORT_KEYS,
    PowerRankingInput,
    coerce_dir,
    coerce_sort,
    compute_power_rankings,
    sort_power_rankings,
)
from matches.tests.conftest import make_team_with_slots

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
        # rating spans 0..100 → 1.0 / 0.5 / 0.0.
        self.assertAlmostEqual(by_id[1].norm_rating, 1.0)
        self.assertAlmostEqual(by_id[2].norm_rating, 0.5)
        self.assertAlmostEqual(by_id[3].norm_rating, 0.0)
        # win_pct identical across all → 0.0 contribution from that component.
        self.assertAlmostEqual(by_id[1].norm_win_pct, 0.0)
        self.assertAlmostEqual(by_id[2].norm_win_pct, 0.0)

    def test_tie_breaks_on_team_name_ascending(self) -> None:
        # Identical inputs ⇒ all components normalize to 0.0 ⇒ power 0.0 each;
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
        # -10 is the max (higher), -30 the min → 1.0 / 0.0.
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

    Mirrors HX-01 / RES-04 / LG-01 precedent: spawn a fresh subprocess,
    ``import matches.power_rankings_logic``, then walk ``sys.modules`` and
    assert no entry matches the ``django`` prefix.
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
        result = subprocess.run(
            [sys.executable, "-c", script],
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.power_rankings_logic.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )


# ===========================================================================
# View helpers
# ===========================================================================


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, query: str = ""):
    url = f"/leagues/{league_id}/power-rankings/{query}"
    request = RequestFactory().get(url)
    return _attach_session(request)


def _make_league(name: str = "PRLeague") -> League:
    return League.objects.create(name=name)


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


def _set_team_rating(team, value: int) -> None:
    """Bump every stat of every active player to ``value`` so the team's
    mean ``overall_rating`` becomes exactly ``value``."""
    stat_fields = [
        "player_awareness",
        "game_awareness",
        "resource_awareness",
        "decision_making",
        "positioning",
        "stamina",
        "speed",
        "flexibility",
        "adaptability",
        "communication",
        "teamwork",
        "Offensive_synergy",
        "defensive_synergy",
        "midfield_synergy",
        "resupply_synergy",
        "resupply_efficiency",
        "accuracy",
        "survival",
        "special_usage",
    ]
    for player in team.active_players:
        for field in stat_fields:
            setattr(player, field, value)
        player.save()


def _make_completed_match(season, team_red, team_blue, *, winner):
    """Two-round completed Match where ``winner`` takes both rounds."""
    red_wins = winner.id == team_red.id
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        red_round1_points=100 if red_wins else 0,
        blue_round1_points=0 if red_wins else 100,
        red_round2_points=100 if red_wins else 0,
        blue_round2_points=0 if red_wins else 100,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=100 if red_wins else 0,
        blue_points=0 if red_wins else 100,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_blue,
        team_blue=team_red,
        round_number=2,
        red_points=0 if red_wins else 100,
        blue_points=100 if red_wins else 0,
        is_completed=True,
    )
    return match


# ===========================================================================
# View: routing / method / 404 / session
# ===========================================================================


class TestPowerRankingsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = power_rankings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/power-rankings/")
        )
        response = power_rankings(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            power_rankings(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        power_rankings(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ===========================================================================
# View: empty state (no Season)
# ===========================================================================


class TestPowerRankingsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = power_rankings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("power-rankings-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = power_rankings(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ===========================================================================
# View: body — rows, DOM ids, sidebar_active, ranking correctness
# ===========================================================================


class TestPowerRankingsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        # Team A: high rating + a clean sweep over Team B.
        _set_team_rating(self.team_a, 90)
        _set_team_rating(self.team_b, 30)
        _make_completed_match(self.season, self.team_a, self.team_b, winner=self.team_a)

    def test_table_and_row_dom_ids_present(self) -> None:
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("power-rankings-table", content)
        self.assertIn(f"power-rankings-row-{self.team_a.id}", content)
        self.assertIn(f"power-rankings-row-{self.team_b.id}", content)

    def test_top_team_is_the_dominant_team(self) -> None:
        # Team A leads on rating, win%, and score diff — must rank #1, so its
        # row appears before Team B's in the rendered HTML.
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        idx_a = content.index(f"power-rankings-row-{self.team_a.id}")
        idx_b = content.index(f"power-rankings-row-{self.team_b.id}")
        self.assertLess(idx_a, idx_b)

    def test_ranked_rows_in_context_are_sorted(self) -> None:
        request = _get(self.league.id)
        power_rankings(request, self.league.id)
        # Re-run capturing context via a render check: assert the dominant
        # team's id leads the ranked list by reading the rendered rank cell.
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        # Team A's row must contain rank 1.
        row_a_start = content.index(f"power-rankings-row-{self.team_a.id}")
        row_a_chunk = content[row_a_start : row_a_start + 400]
        self.assertIn(">1<", row_a_chunk.replace(" ", "").replace("\n", ""))

    def test_team_names_rendered(self) -> None:
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn(self.team_a.name, content)
        self.assertIn(self.team_b.name, content)

    def test_sidebar_active_is_power_rankings(self) -> None:
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("sidebar-league-power_rankings", content)

    def test_only_enrolled_teams_appear(self) -> None:
        # A team not enrolled in the displayed Season must not be ranked.
        other, _ = make_team_with_slots("Outsider")
        response = power_rankings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn(f"power-rankings-row-{other.id}", content)


# ===========================================================================
# View: sortable columns
# ===========================================================================


class TestPowerRankingsSort(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=3)
        self.t_hi, self.t_mid, self.t_lo = self.teams
        # Distinct ratings so a mean-rating sort has an unambiguous order, and
        # a clean sweep so win% / score-diff differ too.
        _set_team_rating(self.t_hi, 90)
        _set_team_rating(self.t_mid, 60)
        _set_team_rating(self.t_lo, 30)
        _make_completed_match(self.season, self.t_hi, self.t_lo, winner=self.t_hi)

    def _order(self, content: str):
        """Rendered row order as a list of team ids."""
        import re

        return [int(m) for m in re.findall(r"power-rankings-row-(\d+)", content)]

    def test_sortable_headers_present(self) -> None:
        content = power_rankings(_get(self.league.id), self.league.id).content.decode()
        for key in (
            "rank",
            "team",
            "mean_rating",
            "win_pct",
            "avg_score_diff",
            "power_score",
        ):
            self.assertIn(f"power-rankings-th-{key}", content)

    def test_default_order_is_rank_ascending(self) -> None:
        content = power_rankings(_get(self.league.id), self.league.id).content.decode()
        # t_hi dominates all three components → rank 1 → first row.
        self.assertEqual(self._order(content)[0], self.t_hi.id)

    def test_sort_by_mean_rating_desc(self) -> None:
        content = power_rankings(
            _get(self.league.id, "?sort=mean_rating&dir=desc"), self.league.id
        ).content.decode()
        self.assertEqual(
            self._order(content), [self.t_hi.id, self.t_mid.id, self.t_lo.id]
        )

    def test_sort_by_mean_rating_asc(self) -> None:
        content = power_rankings(
            _get(self.league.id, "?sort=mean_rating&dir=asc"), self.league.id
        ).content.decode()
        self.assertEqual(
            self._order(content), [self.t_lo.id, self.t_mid.id, self.t_hi.id]
        )

    def test_sort_by_team_name_asc(self) -> None:
        content = power_rankings(
            _get(self.league.id, "?sort=team&dir=asc"), self.league.id
        ).content.decode()
        expected = [
            t.id
            for t in sorted((self.t_hi, self.t_mid, self.t_lo), key=lambda x: x.name)
        ]
        self.assertEqual(self._order(content), expected)

    def test_invalid_sort_falls_back_to_rank(self) -> None:
        # Forgiving fallback: bogus params must not crash; rank-asc applies.
        content = power_rankings(
            _get(self.league.id, "?sort=bogus&dir=sideways"), self.league.id
        ).content.decode()
        self.assertEqual(self._order(content)[0], self.t_hi.id)

    def test_active_sort_header_renders_flip_link_and_arrow(self) -> None:
        content = power_rankings(
            _get(self.league.id, "?sort=mean_rating&dir=asc"), self.league.id
        ).content.decode()
        # The active asc column links to flip to desc and shows the up arrow.
        # LG-06d: sort-header hrefs now also carry the season param, so the
        # querystring is "?season=<id>&sort=mean_rating&dir=desc" — assert the
        # season-agnostic substring rather than the old "?sort=" prefix.
        self.assertIn("sort=mean_rating&dir=desc", content)
        self.assertIn("&uarr;", content)


# ===========================================================================
# LG-07a — member-night Matches excluded from Power Rankings (ADR-0033)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-07-member-night-seam-contract.md`` §3
# (site #7): the power-rankings win% component appends
# ``.exclude(season_phase__phase_type="member_night")`` — member nights are
# social, not ranked, so they must not move power rankings or add drawn-team rows.


class TestLg07aMemberNightPowerExclusion(TestCase):
    """A member-night Match adds NO drawn-team row and does not move the ranking."""

    def setUp(self) -> None:
        from matches.models import SeasonPhase
        from teams.models import Team

        self.league = _make_league("MnPR")
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        _set_team_rating(self.team_a, 90)
        _set_team_rating(self.team_b, 30)
        _make_completed_match(self.season, self.team_a, self.team_b, winner=self.team_a)

        # Member-night completed Match between two drawn Teams (excluded).
        self.mn = SeasonPhase.objects.create(
            season=self.season, ordinal=2, phase_type="member_night"
        )
        self.da = Team.objects.create(name="MN PR Draw A", is_draw_team=True)
        self.db = Team.objects.create(name="MN PR Draw B", is_draw_team=True)
        mnm = Match.objects.create(
            team_red=self.da,
            team_blue=self.db,
            season=self.season,
            season_phase=self.mn,
            red_round1_points=100,
            blue_round1_points=0,
            red_round2_points=100,
            blue_round2_points=0,
            is_completed=True,
        )
        GameRound.objects.create(
            match=mnm,
            team_red=self.da,
            team_blue=self.db,
            round_number=1,
            red_points=100,
            blue_points=0,
            is_completed=True,
        )
        GameRound.objects.create(
            match=mnm,
            team_red=self.db,
            team_blue=self.da,
            round_number=2,
            red_points=0,
            blue_points=100,
            is_completed=True,
        )

    def _content(self) -> str:
        return power_rankings(_get(self.league.id), self.league.id).content.decode()

    def test_drawn_teams_not_ranked(self) -> None:
        content = self._content()
        self.assertNotIn(f"power-rankings-row-{self.da.id}", content)
        self.assertNotIn(f"power-rankings-row-{self.db.id}", content)

    def test_enrolled_teams_still_ranked(self) -> None:
        content = self._content()
        self.assertIn(f"power-rankings-row-{self.team_a.id}", content)
        self.assertIn(f"power-rankings-row-{self.team_b.id}", content)
