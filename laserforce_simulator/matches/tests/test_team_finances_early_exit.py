"""PERF-01 T2 -- the finance writer's early exit (spec #313, ticket #315).

``_ensure_team_finances`` keeps its idempotent, no-backfill contract. Once every
required ``(team, season)`` TeamSeasonFinance row exists, a read loads the
existing pairs in one query and returns before any other read, so a warm owner
evaluation costs a constant number of queries however many Teams and Seasons
the League has. The row values are pinned by ``test_team_finances_writer.py``.
"""

from __future__ import annotations

from datetime import date
from itertools import count

from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from matches.league_services import _ensure_team_finances
from matches.models import League, Season, TeamSeasonFinance
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_team_finances_writer import _add_match
from teams.models import Team

_seq = count()


def _league_with(n_teams: int, n_seasons: int) -> tuple[League, list[Team], Season]:
    """A finance-on career League with ``n_teams`` Teams playing ``n_seasons``
    completed Seasons (each pair once per Season); returns the newest Season."""
    tag = next(_seq)
    teams = [make_team_with_slots(f"EE{tag}x{i}")[0] for i in range(n_teams)]
    league = League.objects.create(
        name=f"EE League {tag}",
        mode="league",
        state="active",
        current_team=teams[0],
        finance_enabled=True,
    )
    season = None
    for s in range(n_seasons):
        season = Season.objects.create(
            league=league,
            name=f"Season {s + 1}",
            start_date=date(2020 + s, 1, 1),
            schedule_format="single_round_robin",
            state="completed",
            starting_team_ids_json=sorted(t.id for t in teams),
        )
        for i, red in enumerate(teams):
            for blue in teams[i + 1 :]:
                _add_match(season, red, blue, red_pts=100, blue_pts=10)
    assert season is not None
    return league, teams, season


def _warm_sql(league: League, season: Season) -> list[str]:
    """The SQL of a read after the first (writing) read."""
    _ensure_team_finances(league, season)
    with CaptureQueriesContext(connection) as ctx:
        _ensure_team_finances(league, season)
    return [q["sql"] for q in ctx.captured_queries]


def _writes(sql: list[str]) -> list[str]:
    return [
        q for q in sql if q.lstrip().upper().startswith(("INSERT", "UPDATE", "DELETE"))
    ]


class TestWarmReadIsConstant(TestCase):
    def test_warm_read_query_count_does_not_grow_with_teams_or_seasons(self) -> None:
        small_league, _, small_season = _league_with(2, 1)
        large_league, _, large_season = _league_with(5, 3)
        small = _warm_sql(small_league, small_season)
        large = _warm_sql(large_league, large_season)
        self.assertEqual(len(small), len(large), large)

    def test_warm_read_writes_nothing(self) -> None:
        league, _, season = _league_with(3, 2)
        sql = _warm_sql(league, season)
        self.assertEqual(_writes(sql), [])


class TestMissingPairs(TestCase):
    def test_newly_listed_team_gets_its_row_on_the_next_read(self) -> None:
        league, _, season = _league_with(2, 2)
        _ensure_team_finances(league, season)
        newcomer = make_team_with_slots("EENew")[0]
        season.starting_team_ids_json = sorted(
            [*season.starting_team_ids_json, newcomer.id]
        )
        season.save(update_fields=["starting_team_ids_json"])

        _ensure_team_finances(league, season)

        self.assertTrue(
            TeamSeasonFinance.objects.filter(team=newcomer, season=season).exists()
        )

    def test_newly_listed_team_is_found_in_an_older_season_too(self) -> None:
        league, _, season = _league_with(2, 2)
        _ensure_team_finances(league, season)
        older = league.seasons.order_by("id").first()
        newcomer = make_team_with_slots("EEOld")[0]
        older.starting_team_ids_json = sorted(
            [*older.starting_team_ids_json, newcomer.id]
        )
        older.save(update_fields=["starting_team_ids_json"])

        _ensure_team_finances(league, season)

        self.assertTrue(
            TeamSeasonFinance.objects.filter(team=newcomer, season=older).exists()
        )

    def test_deleted_team_does_not_force_the_slow_path(self) -> None:
        league, teams, season = _league_with(3, 2)
        before = _warm_sql(league, season)
        # The row's FK is SET_NULL, so the deleted id's pair is now missing.
        teams[2].delete()

        after = _warm_sql(league, season)

        self.assertEqual(_writes(after), [])
        # No standings read (the slow path) on either warm read.
        self.assertFalse([q for q in after if "matches_match" in q], after)
        self.assertLessEqual(len(after), len(before) + 1, after)


def _next_season_sql(n_teams: int, n_seasons: int) -> list[str]:
    """SQL of the read that writes a new Season's rows once ``n_seasons``
    older Seasons already have theirs."""
    league, teams, season = _league_with(n_teams, n_seasons)
    _ensure_team_finances(league, season)
    new = Season.objects.create(
        league=league,
        name="Season new",
        start_date=date(2030, 1, 1),
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    _add_match(new, teams[0], teams[1], red_pts=100, blue_pts=10)
    with CaptureQueriesContext(connection) as ctx:
        _ensure_team_finances(league, new)
    return [q["sql"] for q in ctx.captured_queries]


def _standings_reads(sql: list[str]) -> int:
    return len([q for q in sql if 'FROM "matches_match"' in q])


def _finance_selects(sql: list[str]) -> int:
    return len(
        [
            q
            for q in sql
            if q.lstrip().upper().startswith("SELECT")
            and 'FROM "matches_teamseasonfinance"' in q
        ]
    )


class TestSlowPath(TestCase):
    def test_standings_are_read_once_per_season_not_per_team(self) -> None:
        two = _next_season_sql(2, 1)
        five = _next_season_sql(5, 1)
        self.assertEqual(_standings_reads(two), _standings_reads(five))

    def test_older_seasons_cost_no_standings_or_finance_reads(self) -> None:
        one = _next_season_sql(3, 1)
        three = _next_season_sql(3, 3)
        self.assertEqual(_standings_reads(one), _standings_reads(three))
        self.assertEqual(_finance_selects(one), _finance_selects(three))


class TestRepeatedId(TestCase):
    def test_a_repeated_id_credits_the_profit_once(self) -> None:
        league, teams, season = _league_with(2, 1)
        season.starting_team_ids_json = [teams[0].id, *season.starting_team_ids_json]
        season.save(update_fields=["starting_team_ids_json"])
        cash_before = teams[0].cash

        _ensure_team_finances(league, season)

        teams[0].refresh_from_db()
        row = TeamSeasonFinance.objects.get(team=teams[0], season=season)
        self.assertAlmostEqual(teams[0].cash, cash_before + row.profit, places=4)


class TestEmptySnapshotFallback(TestCase):
    def test_empty_snapshot_reads_the_enrolled_teams(self) -> None:
        league, teams, season = _league_with(2, 1)
        season.starting_team_ids_json = []
        season.save(update_fields=["starting_team_ids_json"])
        season.teams.add(*teams)

        _ensure_team_finances(league, season)

        self.assertEqual(
            set(
                TeamSeasonFinance.objects.filter(season=season).values_list(
                    "team_id", flat=True
                )
            ),
            {t.id for t in teams},
        )
        warm = _warm_sql(league, season)
        self.assertFalse([q for q in warm if "matches_match" in q], warm)
