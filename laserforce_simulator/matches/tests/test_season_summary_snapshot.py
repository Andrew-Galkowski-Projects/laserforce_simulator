"""UXP-09 -- persisted whole-Season summary snapshot for COMPLETED Seasons.

A completed Season's ``PlayerRoundState`` corpus never changes (ADR-0029: the
scoreboard is never rewritten), so the whole-Season builders -- the award set
(Awards page, League History, League player page), the League Leaders boards
and the Season dashboard leader snippets -- are computed once and stored on
``Season.summary_snapshot_json``. These tests pin that:

- the cached output is identical to the freshly computed output;
- a warm read issues no whole-Season ``PlayerRoundState`` aggregation;
- an in-progress Season is never cached;
- Player / Team names are read live (a rename shows up), and a snapshot that
  names a deleted row, or has an unknown version, is recomputed.

Fixtures hand-build the corpus with ``random.seed(42)`` stats -- no simulation.
Assertions compare builder outputs with each other, never raw point totals.
"""

from __future__ import annotations

import random
from datetime import date
from itertools import combinations
from unittest import mock

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from matches import league_services
from matches.league_screen_services import build_leaderboards, resolve_season_scope
from matches.league_services import build_dashboard_context, compute_season_award_set
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.season_snapshot import SNAPSHOT_VERSION
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

_ROLE_OF = {
    "commander": "commander",
    "heavy": "heavy",
    "scout": "scout",
    "scout_2": "scout",
    "medic": "medic",
    "ammo": "ammo",
}

PRS_TABLE = PlayerRoundState._meta.db_table


def _build_season(*, n_teams: int = 4, completed: bool = True) -> tuple[Season, dict]:
    """A round-robin Season of ``n_teams`` Teams, 2 Rounds per Match, with
    seeded per-Round stats. Returns ``(season, {team_id: players})``."""
    random.seed(42)
    league = League.objects.create(name=f"Snap{League.objects.count()}")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n_teams):
        team, players = make_team_with_slots(f"{league.name}T{i}")
        teams.append((team, players))
        season.teams.add(team)
    season.start_season()
    rows: list[PlayerRoundState] = []
    for (team_a, players_a), (team_b, players_b) in combinations(teams, 2):
        match = Match.objects.create(
            team_red=team_a,
            team_blue=team_b,
            season=season,
            is_completed=True,
            winner=team_a,
        )
        for round_number in (1, 2):
            if round_number == 1:
                red, blue, p_red, p_blue = team_a, team_b, players_a, players_b
            else:
                red, blue, p_red, p_blue = team_b, team_a, players_b, players_a
            game_round = GameRound.objects.create(
                match=match,
                round_number=round_number,
                team_red=red,
                team_blue=blue,
                red_points=100,
                blue_points=80,
                is_completed=True,
            )
            for color, players in (("red", p_red), ("blue", p_blue)):
                for key, player in players.items():
                    rows.append(
                        PlayerRoundState(
                            game_round=game_round,
                            player=player,
                            team_color=color,
                            role=_ROLE_OF[key],
                            points_scored=random.randint(500, 9000),
                            tags_made=random.randint(5, 60),
                            times_tagged=random.randint(1, 30),
                            shots_missed=random.randint(10, 200),
                            resupplies_given=random.randint(0, 20),
                            specials_used=random.randint(0, 4),
                        )
                    )
    PlayerRoundState.objects.bulk_create(rows)
    if completed:
        Season.objects.filter(pk=season.pk).update(state="completed")
    season.refresh_from_db()
    return season, {team.id: players for team, players in teams}


def _fresh(season: Season) -> Season:
    return Season.objects.get(pk=season.pk)


def _leaderboards(season: Season) -> dict:
    scope = resolve_season_scope(season.league, season, str(season.id))
    return build_leaderboards(scope)


def _dashboard_leaders(season: Season) -> tuple:
    ctx = build_dashboard_context(season, season.state)
    return ctx["leaders_points"], ctx["leaders_tags"], ctx["leaders_ratio"]


def _prs_queries(ctx: CaptureQueriesContext) -> list[str]:
    return [q["sql"] for q in ctx.captured_queries if PRS_TABLE in q["sql"]]


def _uncached_outputs(season: Season) -> tuple:
    """The three builders' outputs computed straight from the corpus, by
    reading the Season as in-progress (which is never cached)."""
    as_active = _fresh(season)
    as_active.state = "active"
    return (
        compute_season_award_set(as_active),
        _leaderboards(as_active),
        _dashboard_leaders(as_active),
    )


class TestCachedOutputIdentical(TestCase):
    def setUp(self) -> None:
        self.season, _ = _build_season()

    def test_award_set_cached_equals_computed(self) -> None:
        expected = _uncached_outputs(self.season)[0]
        first = compute_season_award_set(_fresh(self.season))
        second = compute_season_award_set(_fresh(self.season))
        self.assertEqual(first, expected)
        self.assertEqual(second, expected)
        self.assertIsNotNone(expected.season_mvp)

    def test_award_set_kd_role_order_preserved(self) -> None:
        expected = _uncached_outputs(self.season)[0]
        compute_season_award_set(_fresh(self.season))
        cached = compute_season_award_set(_fresh(self.season))
        self.assertEqual(list(cached.kd_by_role), list(expected.kd_by_role))

    def test_leaderboards_cached_equals_computed(self) -> None:
        expected = _uncached_outputs(self.season)[1]
        _leaderboards(_fresh(self.season))
        cached = _leaderboards(_fresh(self.season))
        self.assertEqual(cached, expected)
        self.assertEqual(list(cached), list(expected))
        self.assertTrue(all(cached[board] for board in cached))

    def test_dashboard_leaders_cached_equals_computed(self) -> None:
        expected = _uncached_outputs(self.season)[2]
        _dashboard_leaders(_fresh(self.season))
        cached = _dashboard_leaders(_fresh(self.season))
        self.assertEqual(cached, expected)
        self.assertTrue(all(cached))

    def test_snapshot_is_persisted_with_version(self) -> None:
        compute_season_award_set(_fresh(self.season))
        snapshot = _fresh(self.season).summary_snapshot_json
        self.assertEqual(snapshot["v"], SNAPSHOT_VERSION)
        self.assertIn("awards", snapshot)


class TestWarmReadSkipsCorpus(TestCase):
    def setUp(self) -> None:
        self.season, _ = _build_season()

    def test_award_set_warm_read_issues_no_prs_query(self) -> None:
        compute_season_award_set(_fresh(self.season))
        season = _fresh(self.season)
        with CaptureQueriesContext(connection) as ctx:
            compute_season_award_set(season)
        self.assertEqual(_prs_queries(ctx), [])

    def test_leaderboards_warm_read_issues_no_prs_query(self) -> None:
        _leaderboards(_fresh(self.season))
        season = _fresh(self.season)
        with CaptureQueriesContext(connection) as ctx:
            _leaderboards(season)
        self.assertEqual(_prs_queries(ctx), [])

    def test_dashboard_warm_read_issues_no_prs_query(self) -> None:
        _dashboard_leaders(_fresh(self.season))
        season = _fresh(self.season)
        with CaptureQueriesContext(connection) as ctx:
            _dashboard_leaders(season)
        self.assertEqual(_prs_queries(ctx), [])


class TestInProgressNeverCached(TestCase):
    def test_active_season_recomputes_and_stores_nothing(self) -> None:
        season, _ = _build_season(completed=False)
        self.assertEqual(season.state, "active")
        with mock.patch.object(
            league_services,
            "_season_regular_round_dicts",
            wraps=league_services._season_regular_round_dicts,
        ) as spy:
            compute_season_award_set(_fresh(season))
            compute_season_award_set(_fresh(season))
        self.assertEqual(spy.call_count, 2)
        _leaderboards(_fresh(season))
        _dashboard_leaders(_fresh(season))
        self.assertIsNone(_fresh(season).summary_snapshot_json)

    def test_completed_season_computes_awards_once(self) -> None:
        season, _ = _build_season()
        with mock.patch.object(
            league_services,
            "_season_regular_round_dicts",
            wraps=league_services._season_regular_round_dicts,
        ) as spy:
            compute_season_award_set(_fresh(season))
            compute_season_award_set(_fresh(season))
            compute_season_award_set(_fresh(season))
        self.assertEqual(spy.call_count, 1)


class TestSnapshotFreshness(TestCase):
    def setUp(self) -> None:
        self.season, self.players_by_team = _build_season()
        # Warm every key.
        compute_season_award_set(_fresh(self.season))
        _leaderboards(_fresh(self.season))
        _dashboard_leaders(_fresh(self.season))

    def test_player_and_team_renames_show_in_cached_output(self) -> None:
        mvp = compute_season_award_set(_fresh(self.season)).season_mvp
        assert mvp is not None
        Player.objects.filter(pk=mvp.player_id).update(name="Renamed Player")
        Team.objects.filter(pk=mvp.team_id).update(name="Renamed Team")
        cached = compute_season_award_set(_fresh(self.season))
        assert cached.season_mvp is not None
        self.assertEqual(cached.season_mvp.player_name, "Renamed Player")
        self.assertEqual(cached.season_mvp.team_name, "Renamed Team")
        self.assertEqual(
            (
                cached,
                _leaderboards(_fresh(self.season)),
                _dashboard_leaders(_fresh(self.season)),
            ),
            _uncached_outputs(self.season),
        )

    def test_deleted_player_in_snapshot_triggers_recompute(self) -> None:
        mvp = compute_season_award_set(_fresh(self.season)).season_mvp
        assert mvp is not None
        Player.objects.filter(pk=mvp.player_id).delete()
        self.assertEqual(
            (
                compute_season_award_set(_fresh(self.season)),
                _leaderboards(_fresh(self.season)),
                _dashboard_leaders(_fresh(self.season)),
            ),
            _uncached_outputs(self.season),
        )

    def test_unknown_snapshot_version_is_recomputed(self) -> None:
        Season.objects.filter(pk=self.season.pk).update(
            summary_snapshot_json={"v": -1, "awards": {"bogus": True}}
        )
        cached = compute_season_award_set(_fresh(self.season))
        self.assertEqual(cached, _uncached_outputs(self.season)[0])
        self.assertEqual(
            _fresh(self.season).summary_snapshot_json["v"], SNAPSHOT_VERSION
        )

    def test_writing_one_key_keeps_the_others(self) -> None:
        snapshot = _fresh(self.season).summary_snapshot_json
        self.assertTrue(
            {"awards", "leaderboards", "dashboard_leaders"} <= set(snapshot)
        )


class TestViewsUseSnapshot(TestCase):
    """End-to-end: the second GET of each whole-Season page skips the corpus."""

    def setUp(self) -> None:
        self.season, players_by_team = _build_season()
        self.league = self.season.league
        self.player = next(iter(players_by_team.values()))["scout"]

    def _warm_prs_queries(self, url: str) -> tuple[list[str], list[str]]:
        with CaptureQueriesContext(connection) as cold:
            self.assertEqual(self.client.get(url).status_code, 200)
        with CaptureQueriesContext(connection) as warm:
            self.assertEqual(self.client.get(url).status_code, 200)
        return _prs_queries(cold), _prs_queries(warm)

    def test_season_awards_page(self) -> None:
        cold, warm = self._warm_prs_queries(
            reverse("season_awards", args=[self.season.id])
        )
        self.assertTrue(cold)
        # Only the cheap ``has_rounds`` EXISTS probe remains.
        self.assertLessEqual(len(warm), 1)

    def test_season_dashboard_page(self) -> None:
        cold, warm = self._warm_prs_queries(
            reverse("season_dashboard", args=[self.season.id])
        )
        self.assertTrue(cold)
        self.assertEqual(warm, [])

    def test_league_leaders_page(self) -> None:
        url = (
            reverse("stats_league_leaders", args=[self.league.id])
            + f"?season={self.season.id}"
        )
        cold, warm = self._warm_prs_queries(url)
        self.assertTrue(cold)
        self.assertEqual(warm, [])

    def test_league_player_page_skips_award_corpus(self) -> None:
        url = reverse("league_player_detail", args=[self.league.id, self.player.id])
        cold, warm = self._warm_prs_queries(url)
        # Every warm PRS query is scoped to this one Player.
        self.assertLess(len(warm), len(cold))
        for sql in warm:
            self.assertIn('."player_id" = ', sql)
