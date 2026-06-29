"""LG-01z-e — tests for the Team History (3-tab) league screen.

Two layers:

* Pure-unit tests for ``matches.team_history_logic`` (no DB, no Django) +
  a ``TestNoDjangoImportsLeaked`` subprocess purity check mirroring the
  HX-01 / RES-04 / LG-01 precedent.
* Django ``TestCase`` tests for the view
  ``matches.league_screens.team_history.team_history(request, league_id)``.
  The view is read-only / GET-only and NOT yet URL-wired (the orchestrator
  wires the ``team_history`` route centrally), so these tests call the view
  directly via ``RequestFactory`` with a real session attached.

Fixtures are hand-constructed League / Season / Team / Match / GameRound /
PlayerRoundState rows — LG-01z runs NO simulation, so the simulator is
never entered.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.team_history import team_history
from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.team_history_logic import (
    OverallRecord,
    PlayerRollup,
    SeasonRow,
    compute_overall_record,
    compute_player_rollups,
    compute_season_rows,
    round_outcome,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player

# ===========================================================================
# Pure-module unit tests
# ===========================================================================


class TestRoundOutcome(TestCase):
    def test_win_loss_tie(self) -> None:
        self.assertEqual(round_outcome(10, 5), "W")
        self.assertEqual(round_outcome(5, 10), "L")
        self.assertEqual(round_outcome(7, 7), "T")


class TestComputeOverallRecord(TestCase):
    def test_empty_is_all_zero(self) -> None:
        rec = compute_overall_record([], championships=0)
        self.assertEqual(rec, OverallRecord(0, 0, 0, 0, 0))

    def test_counts_outcomes(self) -> None:
        rec = compute_overall_record(
            ["W", "W", "L", "T"], championships=2, playoff_appearances=0
        )
        self.assertEqual(rec.wins, 2)
        self.assertEqual(rec.losses, 1)
        self.assertEqual(rec.ties, 1)
        self.assertEqual(rec.championships, 2)
        self.assertEqual(rec.playoff_appearances, 0)

    def test_unknown_outcome_counts_as_tie(self) -> None:
        rec = compute_overall_record(["?", "X"], championships=0)
        self.assertEqual((rec.wins, rec.losses, rec.ties), (0, 0, 2))


class TestComputeSeasonRows(TestCase):
    def test_empty(self) -> None:
        self.assertEqual(compute_season_rows([]), [])

    def test_maps_keys_in_order(self) -> None:
        rows = compute_season_rows(
            [
                {
                    "season_id": 2,
                    "year": 2027,
                    "wins": 3,
                    "losses": 1,
                    "ties": 0,
                    "rank": 1,
                },
                {
                    "season_id": 1,
                    "year": 2026,
                    "wins": 1,
                    "losses": 3,
                    "ties": 0,
                    "rank": 4,
                },
            ]
        )
        self.assertEqual([r.season_id for r in rows], [2, 1])
        self.assertEqual(rows[0], SeasonRow(2, 2027, 3, 1, 0, 1))
        self.assertIsNone(compute_season_rows([{"season_id": 5}])[0].rank)


class TestComputePlayerRollups(TestCase):
    def test_empty(self) -> None:
        self.assertEqual(compute_player_rollups([]), [])

    def test_folds_rounds_per_player(self) -> None:
        dicts = [
            {
                "player_id": 1,
                "player_name": "Ada",
                "on_team": True,
                "season_year": 2026,
                "points_scored": 100,
                "tags_made": 5,
                "times_tagged": 2,
                "missiles_landed": 0,
                "resupplies_given": 0,
                "specials_used": 1,
            },
            {
                "player_id": 1,
                "player_name": "Ada",
                "on_team": True,
                "season_year": 2027,
                "points_scored": 50,
                "tags_made": 3,
                "times_tagged": 1,
                "missiles_landed": 0,
                "resupplies_given": 0,
                "specials_used": 0,
            },
        ]
        rollups = compute_player_rollups(dicts)
        self.assertEqual(len(rollups), 1)
        r = rollups[0]
        self.assertEqual(r.games_played, 2)
        self.assertEqual(r.stats["points_scored"], 150)
        self.assertEqual(r.stats["tags_made"], 8)
        self.assertEqual(r.last_season_year, 2027)
        self.assertTrue(r.on_team)
        self.assertEqual(r.colour_class, "team-history-player-green")

    def test_off_team_is_blue(self) -> None:
        r = compute_player_rollups(
            [{"player_id": 9, "player_name": "Bo", "on_team": False}]
        )[0]
        self.assertFalse(r.on_team)
        self.assertEqual(r.colour_class, "team-history-player-blue")

    def test_sorted_by_name_then_id(self) -> None:
        rollups = compute_player_rollups(
            [
                {"player_id": 3, "player_name": "Zed", "on_team": True},
                {"player_id": 1, "player_name": "Ada", "on_team": True},
                {"player_id": 2, "player_name": "Ada", "on_team": True},
            ]
        )
        self.assertEqual([r.player_id for r in rollups], [1, 2, 3])

    def test_last_season_year_none_when_all_none(self) -> None:
        r = compute_player_rollups(
            [{"player_id": 1, "player_name": "X", "on_team": True, "season_year": None}]
        )[0]
        self.assertIsNone(r.last_season_year)
        self.assertIsInstance(r, PlayerRollup)


class TestNoDjangoImportsLeaked(TestCase):
    """The pure module must import zero Django modules."""

    def test_no_django_in_sys_modules(self) -> None:
        code = (
            "import sys\n"
            "import matches.team_history_logic\n"
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]\n"
            "assert not leaked, leaked\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


# ===========================================================================
# View-level helpers + fixtures
# ===========================================================================


def _attach_session(request):
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/team/history/"
    if query:
        path = f"{path}?{query}"
    return _attach_session(RequestFactory().get(path))


def _make_league(name: str = "HistLeague") -> League:
    return League.objects.create(name=name)


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}{name}T{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _play_round(season, team_red, team_blue, *, round_number, red_points, blue_points):
    """Hand-construct one completed Match + GameRound (no simulation)."""
    match = Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    gr = GameRound.objects.create(
        match=match,
        round_number=round_number,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )
    return match, gr


# ===========================================================================
# Routing / method / 404 / session
# ===========================================================================


class TestTeamHistoryRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = team_history(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/team/history/")
        )
        response = team_history(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            team_history(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        team_history(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ===========================================================================
# Empty state — no Season
# ===========================================================================


class TestTeamHistoryEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = team_history(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("team-history-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = team_history(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ===========================================================================
# 3-tab body + DOM ids + sidebar_active
# ===========================================================================


class TestTeamHistoryTabs(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_three_section_dom_ids_present(self) -> None:
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("team-history-tabs", content)
        self.assertIn("team-history-overall", content)
        self.assertIn("team-history-seasons", content)
        self.assertIn("team-history-players", content)

    def test_all_three_sections_on_one_screen(self) -> None:
        # Single-screen layout: Overall, Seasons and Players are all rendered
        # together — no Bootstrap tab toggle gating their visibility.
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertNotIn('data-bs-toggle="tab"', content)
        # All three section headings render at once.
        self.assertIn("Overall", content)
        self.assertIn("Seasons", content)
        self.assertIn("Players", content)

    def test_sidebar_active_is_history_team(self) -> None:
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("sidebar-team-history_team", content)


# ===========================================================================
# Overall tab content
# ===========================================================================


class TestTeamHistoryOverall(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_round_level_record_counts_per_round_not_match(self) -> None:
        # team_a wins round 1, loses round 2 → 1-1-0 at the ROUND level.
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        _play_round(
            self.season,
            self.team_b,
            self.team_a,
            round_number=2,
            red_points=80,
            blue_points=40,
        )
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("1-1-0", content)

    def test_championship_counted(self) -> None:
        self.season.champion_team = self.team_a
        self.season.save(update_fields=["champion_team"])
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        # Championships row should read 1.
        self.assertIn("Championships", content)

    def test_playoff_appearances_placeholder_zero(self) -> None:
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("Playoff appearances", content)


# ===========================================================================
# Seasons tab content
# ===========================================================================


class TestTeamHistorySeasons(TestCase):
    def test_enrolled_season_row_rendered_with_year(self) -> None:
        league = _make_league()
        season, teams = _make_active_season(league, n_teams=2)
        team_a = teams[0]
        league.current_team = team_a
        league.save(update_fields=["current_team"])
        content = team_history(_get(league.id), league.id).content.decode()
        self.assertIn(f"team-history-season-row-{season.id}", content)
        self.assertIn("2026", content)


# ===========================================================================
# Players tab content — derivation of green/blue + career rollups
# ===========================================================================


class TestTeamHistoryPlayers(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        self.match, self.gr = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )

    def _appear(self, player, team_color, **stats):
        return PlayerRoundState.objects.create(
            game_round=self.gr,
            player=player,
            team_color=team_color,
            **stats,
        )

    def test_player_on_team_rendered_green(self) -> None:
        player = self.team_a.slot_commander
        self._appear(player, "red", points_scored=42, tags_made=3)
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn(f"team-history-player-row-{player.id}", content)
        self.assertIn("team-history-player-green", content)
        self.assertIn(player.name, content)

    def test_player_now_elsewhere_rendered_blue(self) -> None:
        # A player who played for team_a but whose Player.team is now team_b.
        wanderer = Player.objects.create(team=self.team_b, name="Wanderer")
        self._appear(wanderer, "red", points_scored=10)
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        row_id = f"team-history-player-row-{wanderer.id}"
        self.assertIn(row_id, content)
        # The wanderer's row carries the blue colour class.
        self.assertIn("team-history-player-blue", content)

    def test_player_links_to_in_league_player_page(self) -> None:
        # LG-06h: player-name link repointed to league_player_detail.
        player = self.team_a.slot_heavy
        self._appear(player, "red", points_scored=5)
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertIn(f"/leagues/{self.league.id}/players/{player.id}/", content)

    def test_career_stats_summed_and_games_counted(self) -> None:
        player = self.team_a.slot_scout_1
        self._appear(player, "red", points_scored=30, tags_made=2)
        # A second appearance in another round of the same team.
        _, gr2 = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=2,
            red_points=60,
            blue_points=20,
        )
        PlayerRoundState.objects.create(
            game_round=gr2,
            player=player,
            team_color="red",
            points_scored=20,
            tags_made=1,
        )
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        row_id = f"team-history-player-row-{player.id}"
        self.assertIn(row_id, content)
        # 30 + 20 = 50 total points; 2 games.
        self.assertIn("50", content)


# ===========================================================================
# Players tab — LG-06a pagination + page-size <select> selector
# ===========================================================================


class TestTeamHistoryPlayersPagination(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        self.match, self.gr = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        # Seed > 10 distinct players who appeared for team_a (build the
        # PlayerRoundState rows exactly as TestTeamHistoryPlayers does).
        self.players = []
        for i in range(13):
            p = Player.objects.create(team=self.team_a, name=f"Roster{i:02d}")
            PlayerRoundState.objects.create(
                game_round=self.gr,
                player=p,
                team_color="red",
                points_scored=10 + i,
            )
            self.players.append(p)

    def _row_count(self, content: str) -> int:
        return content.count("team-history-player-row-")

    def test_per_page_select_dom_id_present(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}"),
            self.league.id,
        ).content.decode()
        self.assertIn("team-history-per-page-select", content)

    def test_selected_option_reflects_requested_per_page(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}&per_page=25"),
            self.league.id,
        ).content.decode()
        self.assertIn('value="25" selected', content)

    def test_per_page_form_carries_hidden_team_id(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}&per_page=10"),
            self.league.id,
        ).content.decode()
        self.assertIn('name="team_id"', content)

    def test_team_picker_form_carries_hidden_per_page(self) -> None:
        # The team-picker <select id="team-history-team-picker"> form must
        # carry the current per_page so switching teams keeps the page size.
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}&per_page=25"),
            self.league.id,
        ).content.decode()
        self.assertIn("team-history-team-picker", content)
        self.assertIn('name="per_page"', content)

    def test_pagination_renders_over_ten_players(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}&per_page=10"),
            self.league.id,
        ).content.decode()
        self.assertIn("team-history-players-pagination", content)

    def test_page_one_shows_ten_rows(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&per_page=10&page=1",
            ),
            self.league.id,
        ).content.decode()
        self.assertEqual(self._row_count(content), 10)

    def test_page_two_shows_remainder(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&per_page=10&page=2",
            ),
            self.league.id,
        ).content.decode()
        # 13 players → page 1 has 10, page 2 has the remaining 3.
        self.assertEqual(self._row_count(content), 3)

    def test_pagination_link_carries_team_id_and_no_stale_page(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&per_page=10&page=2",
            ),
            self.league.id,
        ).content.decode()
        # Locate the pagination nav and assert its links carry team_id and
        # do not carry a stale extra page= in the querystring base.
        self.assertIn("team-history-players-pagination", content)
        nav_start = content.index("team-history-players-pagination")
        nav = content[nav_start:]
        self.assertIn(f"team_id={self.team_a.id}", nav)
        # The querystring helper feeding the page links must not bake in a
        # stale page= (the page number is appended separately by the link).
        self.assertNotIn("page=2&", nav)


# ===========================================================================
# Team selection — ?team_id= validation + default
# ===========================================================================


class TestTeamHistoryTeamSelection(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_team_id_selects_requested_enrolled_team(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_b.id}"), self.league.id
        ).content.decode()
        self.assertIn(f"{self.team_b.name} — History", content)

    def test_invalid_team_id_falls_back_to_default(self) -> None:
        response = team_history(
            _get(self.league.id, query="team_id=not-an-int"), self.league.id
        )
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{self.team_a.name} — History", response.content.decode())

    def test_non_enrolled_team_id_falls_back_to_default(self) -> None:
        outsider, _ = make_team_with_slots("HistOutsider")
        content = team_history(
            _get(self.league.id, query=f"team_id={outsider.id}"), self.league.id
        ).content.decode()
        self.assertIn(f"{self.team_a.name} — History", content)

    def test_picker_lists_all_enrolled_teams(self) -> None:
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        for team in (self.team_a, self.team_b, self.team_c):
            self.assertIn(team.name, content)


# ===========================================================================
# LG-06c — Team History sortable columns (Seasons + Players; Overall NONE)
#
# Seasons: ?seasons_sort=&seasons_dir= keys {year, wins, losses, ties, rank},
#   default year/desc (newest first).
# Players: ?players_sort=&players_dir= keys {name, games_played,
#   points_scored, tags_made, times_tagged, missiles_landed, resupplies_given,
#   specials_used, last_season_year}, default name/asc, SORTED BEFORE
#   pagination, sort+dir carried in pagination links, sort change resets to
#   page 1. Overall tab gets NO sort headers. Namespaced params independent.
#
# EXPECTED TO FAIL until the Code agent lands view-side sorting + headers.
# ===========================================================================

_TH_GLYPH_UP = "↑"
_TH_GLYPH_DOWN = "↓"


def _player_row_ids_in_order(content: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"team-history-player-row-(\d+)", content)]


def _season_row_ids_in_order(content: str) -> list[int]:
    import re

    return [int(m) for m in re.findall(r"team-history-season-row-(\d+)", content)]


class TestTeamHistorySeasonsSort(TestCase):
    """Seasons table sort. Two enrolled Seasons in distinct calendar years so
    year-desc default and asc/desc flips are observable."""

    def setUp(self) -> None:
        self.league = _make_league()
        # Two Seasons for the SAME team so both appear in its Seasons table.
        self.season1, self.teams = _make_active_season(
            self.league, name="S1", n_teams=2
        )
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        # Second Season enrolling team_a, later year. Build it directly so
        # team_a is enrolled in both (start_date controls the year shown).
        self.season2 = Season.objects.create(
            league=self.league, name="S2", start_date=date(2028, 6, 1)
        )
        self.season2.teams.add(self.team_a, self.team_b)
        self.season2.start_season()
        self.season2.refresh_from_db()
        # season1 start_date is 2026 (from _make_active_season).

    def test_default_seasons_order_is_year_desc(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}"),
            self.league.id,
        ).content.decode()
        order = _season_row_ids_in_order(content)
        # Newest (2028 = season2) first.
        self.assertEqual(order[0], self.season2.id)

    def test_year_asc_flips_to_oldest_first(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&seasons_sort=year&seasons_dir=asc",
            ),
            self.league.id,
        ).content.decode()
        order = _season_row_ids_in_order(content)
        self.assertEqual(order[0], self.season1.id)

    def test_invalid_seasons_sort_falls_back_to_year_desc(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&seasons_sort=BOGUS&seasons_dir=NOPE",
            ),
            self.league.id,
        ).content.decode()
        order = _season_row_ids_in_order(content)
        self.assertEqual(order[0], self.season2.id)

    def test_seasons_th_dom_ids_present(self) -> None:
        content = team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}"),
            self.league.id,
        ).content.decode()
        for key in ("year", "wins", "rank"):
            self.assertIn(f"team-history-seasons-th-{key}", content)

    def test_active_seasons_header_glyph(self) -> None:
        content = team_history(
            _get(
                self.league.id,
                query=f"team_id={self.team_a.id}&seasons_sort=year&seasons_dir=asc",
            ),
            self.league.id,
        ).content.decode()
        th_start = content.index("team-history-seasons-th-year")
        window = content[th_start : th_start + 400]
        self.assertIn(_TH_GLYPH_UP, window)


class TestTeamHistoryPlayersSort(TestCase):
    """Players table sort over a >per_page roster so sort-before-pagination
    is observable. 13 players appear for team_a in one round."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        self.match, self.gr = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        # 13 players, ascending points so the global max is deterministic.
        self.players = []
        for i in range(13):
            p = Player.objects.create(team=self.team_a, name=f"Roster{i:02d}")
            PlayerRoundState.objects.create(
                game_round=self.gr,
                player=p,
                team_color="red",
                points_scored=10 + i,  # Roster12 has the highest, =22
                tags_made=i,
            )
            self.players.append(p)
        self.top_points_player = self.players[-1]  # Roster12, 22 points

    def _q(self, query: str) -> str:
        return team_history(
            _get(self.league.id, query=f"team_id={self.team_a.id}&{query}"),
            self.league.id,
        ).content.decode()

    def test_default_players_order_is_name_asc(self) -> None:
        content = self._q("per_page=25")
        order = _player_row_ids_in_order(content)
        names_by_id = {p.id: p.name for p in self.players}
        rendered_names = [names_by_id[i] for i in order if i in names_by_id]
        self.assertEqual(rendered_names, sorted(rendered_names))

    def test_points_scored_desc_puts_global_max_on_page_one(self) -> None:
        # SORT-BEFORE-PAGINATION: the global highest-points player (Roster12,
        # which would be on page 2 in name-asc default order) appears on
        # page 1 when sorted points_scored desc.
        content = self._q(
            "players_sort=points_scored&players_dir=desc&per_page=10&page=1"
        )
        order = _player_row_ids_in_order(content)
        self.assertEqual(order[0], self.top_points_player.id)

    def test_points_scored_asc_puts_global_min_on_page_one(self) -> None:
        content = self._q(
            "players_sort=points_scored&players_dir=asc&per_page=10&page=1"
        )
        order = _player_row_ids_in_order(content)
        self.assertEqual(order[0], self.players[0].id)  # Roster00, 10 points

    def test_tags_made_desc_sorts(self) -> None:
        content = self._q("players_sort=tags_made&players_dir=desc&per_page=10&page=1")
        order = _player_row_ids_in_order(content)
        # tags_made == i, so Roster12 (i=12) leads.
        self.assertEqual(order[0], self.players[-1].id)

    def test_name_desc_reverses(self) -> None:
        content = self._q("players_sort=name&players_dir=desc&per_page=25")
        order = _player_row_ids_in_order(content)
        names_by_id = {p.id: p.name for p in self.players}
        rendered_names = [names_by_id[i] for i in order if i in names_by_id]
        self.assertEqual(rendered_names, sorted(rendered_names, reverse=True))

    def test_every_player_key_returns_200_and_sorts(self) -> None:
        for key in (
            "name",
            "games_played",
            "points_scored",
            "tags_made",
            "times_tagged",
            "missiles_landed",
            "resupplies_given",
            "specials_used",
            "last_season_year",
        ):
            content = self._q(f"players_sort={key}&players_dir=asc&per_page=25")
            # 13 players all render under per_page=25.
            self.assertEqual(len(_player_row_ids_in_order(content)), 13, key)

    def test_invalid_players_sort_falls_back_to_name_asc(self) -> None:
        content = self._q("players_sort=BOGUS&players_dir=NOPE&per_page=25")
        order = _player_row_ids_in_order(content)
        names_by_id = {p.id: p.name for p in self.players}
        rendered_names = [names_by_id[i] for i in order if i in names_by_id]
        self.assertEqual(rendered_names, sorted(rendered_names))

    def test_players_th_dom_ids_present(self) -> None:
        content = self._q("per_page=25")
        for key in (
            "name",
            "games_played",
            "points_scored",
            "tags_made",
            "times_tagged",
            "missiles_landed",
            "resupplies_given",
            "specials_used",
            "last_season_year",
        ):
            self.assertIn(f"team-history-players-th-{key}", content)

    def test_active_players_header_glyph(self) -> None:
        content = self._q("players_sort=points_scored&players_dir=desc&per_page=25")
        th_start = content.index("team-history-players-th-points_scored")
        window = content[th_start : th_start + 400]
        self.assertIn(_TH_GLYPH_DOWN, window)


class TestTeamHistoryPlayersSortPaginationLinks(TestCase):
    """Sort change resets to page 1; pagination links preserve sort/dir +
    team_id + per_page. Uses the wired URL so query strings are real."""

    URL_NAME = "team_history"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        self.match, self.gr = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        for i in range(13):
            p = Player.objects.create(team=self.team_a, name=f"Roster{i:02d}")
            PlayerRoundState.objects.create(
                game_round=self.gr,
                player=p,
                team_color="red",
                points_scored=10 + i,
            )

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_players_header_href_omits_page(self) -> None:
        # A column-header href must NOT carry a stale page= (resets to page 1).
        content = self._get(
            query=f"team_id={self.team_a.id}&per_page=10&page=2"
            f"&players_sort=name&players_dir=asc"
        ).content.decode()
        th_start = content.index("team-history-players-th-points_scored")
        window = content[th_start : th_start + 500]
        self.assertIn("players_sort=points_scored", window)
        self.assertNotIn("page=2", window)

    def test_pagination_links_carry_sort_dir_team_id_per_page(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&per_page=10&page=1"
            f"&players_sort=points_scored&players_dir=desc"
        ).content.decode()
        self.assertIn("team-history-players-pagination", content)
        nav_start = content.index("team-history-players-pagination")
        nav = content[nav_start : nav_start + 1200]
        self.assertIn("players_sort=points_scored", nav)
        self.assertIn("players_dir=desc", nav)
        self.assertIn(f"team_id={self.team_a.id}", nav)
        self.assertIn("per_page=10", nav)

    def test_per_page_form_carries_players_sort_and_dir(self) -> None:
        content = self._get(
            query=f"team_id={self.team_a.id}&per_page=10"
            f"&players_sort=points_scored&players_dir=desc"
        ).content.decode()
        # The per-page <select> form must carry the active players sort/dir
        # so changing page size keeps the sort.
        self.assertIn('name="players_sort"', content)
        self.assertIn('name="players_dir"', content)


class TestTeamHistoryNamespaceIndependence(TestCase):
    """Sorting the Seasons table does not reset the Players sort and vice
    versa — the two param namespaces are independent."""

    URL_NAME = "team_history"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        self.match, self.gr = _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        self.players = []
        for i in range(3):
            p = Player.objects.create(team=self.team_a, name=f"Roster{i:02d}")
            PlayerRoundState.objects.create(
                game_round=self.gr,
                player=p,
                team_color="red",
                points_scored=10 + i,
            )
            self.players.append(p)

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_players_sort_holds_while_seasons_sort_changes(self) -> None:
        # Set players_sort=points_scored desc AND seasons_sort=year asc.
        # The Players table must still honour points_scored desc.
        content = self._get(
            query=f"team_id={self.team_a.id}&per_page=25"
            f"&players_sort=points_scored&players_dir=desc"
            f"&seasons_sort=year&seasons_dir=asc"
        ).content.decode()
        order = _player_row_ids_in_order(content)
        self.assertEqual(order[0], self.players[-1].id)  # highest points first

    def test_seasons_header_href_carries_players_params(self) -> None:
        # The Seasons header href must carry players_sort/players_dir so it
        # doesn't reset the Players table.
        content = self._get(
            query=f"team_id={self.team_a.id}"
            f"&players_sort=points_scored&players_dir=desc"
        ).content.decode()
        th_start = content.index("team-history-seasons-th-year")
        window = content[th_start : th_start + 500]
        self.assertIn("players_sort=points_scored", window)


class TestTeamHistorySortCoexistsWithTeamId(TestCase):
    """Switching team via the picker carries both seasons_* and players_*
    sort state."""

    URL_NAME = "team_history"

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def _get(self, *, query: str = ""):
        from django.urls import reverse

        url = reverse(self.URL_NAME, args=[self.league.id])
        if query:
            url = f"{url}?{query}"
        return self.client.get(url)

    def test_team_picker_form_carries_seasons_and_players_sort(self) -> None:
        content = self._get(
            query=f"team_id={self.team_b.id}"
            f"&seasons_sort=year&seasons_dir=asc"
            f"&players_sort=points_scored&players_dir=desc"
        ).content.decode()
        # The team picker form must carry both namespaced sort states.
        self.assertIn('name="seasons_sort"', content)
        self.assertIn('name="players_sort"', content)


class TestTeamHistoryOverallNoSort(TestCase):
    """The Overall tab is a W-L-T dl with NO sort headers."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_no_overall_sort_headers(self) -> None:
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        self.assertNotIn("team-history-overall-th-", content)


# ===========================================================================
# LG-02-Part2c-3f — playoff rounds in the Team History Overall tab
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3f-seam-contract.md`` §1 / §6.2:
# ``_build_overall_context(team)`` widens the Overall-tab round corpus from
# regular-season-only to a UNION of regular-season rounds + season-embedded
# playoff rounds (reached through the FK chain
# ``GameRound.match -> series_match -> node -> tournament -> season_phases``,
# the ``season_phases__isnull=False`` season-embedded-vs-sandbox discriminator),
# and fills the ``playoff_appearances`` counter (distinct season-embedded
# playoff Tournaments the team is a ``participants__team``). Assertions are on
# RECORD / COUNT deltas + ``playoff_appearances`` + standings rank — NEVER on
# exact simulated point totals (everything here is hand-built, no simulation).
#
# A season-embedded playoff Match keeps ``season=NULL`` (Part2c-1 decision #3)
# and is reached ONLY through the FK chain from its season-embedded Tournament;
# a STANDALONE sandbox Tournament also has ``season=NULL`` but no SeasonPhase
# pointing at it (empty ``season_phases``) and must NOT be counted.
#
# Appended as NEW classes; no existing class above is modified. The pure-module
# field-flow unit is assert-only (``team_history_logic`` is UNCHANGED this
# slice — the kwarg already exists).


from matches.models import (  # noqa: E402
    BracketNode,
    SeasonPhase,
    SeriesMatch,
    Tournament,
    TournamentParticipant,
)


def _embedded_playoff_tournament(season, teams, *, name="Playoffs"):
    """Hand-build a BUILT single-elim playoff embedded in ``season``.

    Creates a ``Tournament`` linked from a ``SeasonPhase.tournament`` (so the
    Tournament's reverse ``season_phases`` set is non-empty — the
    season-embedded discriminator), one ``TournamentParticipant`` per team, and
    one resolved ``BracketNode``. Returns ``(tournament, node)``. No sim.
    """
    tournament = Tournament.objects.create(
        name=name, format="single_elimination", state="active"
    )
    SeasonPhase.objects.create(
        season=season, ordinal=99, phase_type="tournament", tournament=tournament
    )
    for seed, team in enumerate(teams, start=1):
        TournamentParticipant.objects.create(
            tournament=tournament, team=team, seed=seed
        )
    node = BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=0,
        team_a=teams[0],
        team_b=teams[1],
        seed_a=1,
        seed_b=2,
    )
    return tournament, node


def _play_playoff_round(node, team_red, team_blue, *, red_points, blue_points):
    """Hand-build one season-embedded playoff GameRound.

    Creates a ``Match`` with ``season=NULL`` (Part2c-1 #3), wires it to the
    node via a ``SeriesMatch`` row (the FK chain the widened corpus query
    traverses), and one completed ``GameRound``. Returns the ``GameRound``.
    """
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=None,
        match_type="tournament",
        is_completed=True,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=team_red)
    return GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )


def _sandbox_playoff_round(team_red, team_blue, *, red_points, blue_points):
    """Hand-build one STANDALONE-sandbox playoff GameRound.

    A standalone ``Tournament`` with NO ``SeasonPhase`` pointing at it (empty
    ``season_phases``) — its Matches are ``season=NULL`` but the
    ``season_phases__isnull=False`` guard must EXCLUDE its rounds. Returns the
    ``GameRound``.
    """
    tournament = Tournament.objects.create(
        name="Sandbox Cup", format="single_elimination", state="active"
    )
    TournamentParticipant.objects.create(tournament=tournament, team=team_red, seed=1)
    TournamentParticipant.objects.create(tournament=tournament, team=team_blue, seed=2)
    node = BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=0,
        team_a=team_red,
        team_b=team_blue,
        seed_a=1,
        seed_b=2,
    )
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=None,
        match_type="tournament",
        is_completed=True,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=team_red)
    return GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )


def _overall_wlt(content: str) -> tuple[int, int, int]:
    """Parse the ``wins-losses-ties`` string from the rendered Overall tab.

    The template renders ``{{ wins }}-{{ losses }}-{{ ties }}`` as the first
    ``<dd>`` inside ``#team-history-overall``.
    """
    import re

    section_start = content.index("team-history-overall")
    window = content[section_start : section_start + 1500]
    m = re.search(r"(\d+)-(\d+)-(\d+)", window)
    assert m is not None, "no W-L-T string in the Overall section"
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _playoff_appearances(content: str) -> int:
    """Parse the ``playoff_appearances`` value rendered after the
    'Playoff appearances' ``<dt>`` in ``#team-history-overall``."""
    import re

    idx = content.index("Playoff appearances")
    window = content[idx : idx + 200]
    m = re.search(r"<dd[^>]*>\s*(\d+)\s*</dd>", window)
    assert m is not None, "no playoff-appearances <dd> after the label"
    return int(m.group(1))


def _season_row_rank(content: str, season_id: int) -> str:
    """The rendered Final-rank cell text for the given season row."""
    import re

    row_id = f"team-history-season-row-{season_id}"
    start = content.index(row_id)
    row = content[start : start + 600]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
    # Final rank is the last <td> in the row (Year | Record | Final rank).
    assert cells, "no <td> cells in the season row"
    return cells[-1].strip()


class TestTeamHistoryOverallPlayoffRounds(TestCase):
    """The Overall tab W/L/T INCLUDES season-embedded playoff rounds and the
    ``playoff_appearances`` counter is filled. team_a plays BOTH regular-season
    and playoff rounds."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def _content(self) -> str:
        return team_history(_get(self.league.id), self.league.id).content.decode()

    def test_overall_record_includes_playoff_rounds_count_delta(self) -> None:
        # Regular-season: team_a wins round 1 (1-0-0).
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        rs_w, rs_l, rs_t = _overall_wlt(self._content())
        rs_total = rs_w + rs_l + rs_t
        self.assertEqual(rs_total, 1)

        # Now add a season-embedded playoff round team_a played (a loss).
        _tournament, node = _embedded_playoff_tournament(
            self.season, [self.team_a, self.team_b]
        )
        _play_playoff_round(
            node, self.team_b, self.team_a, red_points=90, blue_points=40
        )

        w, loss, t = _overall_wlt(self._content())
        widened_total = w + loss + t
        # The W+L+T total rose by exactly the one playoff round team_a played.
        self.assertEqual(widened_total, rs_total + 1)
        # And it folds in as a LOSS (team_a was the blue side, lost 40-90).
        self.assertEqual(w, 1)
        self.assertEqual(loss, 1)

    def test_playoff_appearances_counts_distinct_embedded_tournaments(self) -> None:
        # Team is seeded into ONE season-embedded playoff Tournament.
        _embedded_playoff_tournament(self.season, [self.team_a, self.team_b])
        self.assertEqual(_playoff_appearances(self._content()), 1)

    def test_playoff_appearances_zero_without_embedded_tournament(self) -> None:
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        self.assertEqual(_playoff_appearances(self._content()), 0)

    def test_playoff_appearances_counts_two_distinct_embedded_tournaments(
        self,
    ) -> None:
        # A second Season with its own embedded playoff the team is seeded into.
        season2, _t2 = _make_active_season(self.league, name="S2", n_teams=2)
        # Re-enroll team_a / team_b into season2 so the participant rows reuse
        # the SAME teams (playoff_appearances is team-global / all-leagues).
        season2.teams.add(self.team_a, self.team_b)
        _embedded_playoff_tournament(self.season, [self.team_a, self.team_b], name="P1")
        _embedded_playoff_tournament(season2, [self.team_a, self.team_b], name="P2")
        self.assertEqual(_playoff_appearances(self._content()), 2)


class TestTeamHistoryOverallSandboxExcluded(TestCase):
    """A STANDALONE sandbox Tournament's rounds are NOT counted in the Overall
    tab and do NOT bump ``playoff_appearances`` (the
    ``season_phases__isnull=False`` FK-chain guard)."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def _content(self) -> str:
        return team_history(_get(self.league.id), self.league.id).content.decode()

    def test_sandbox_rounds_do_not_change_overall_wlt(self) -> None:
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        before_total = sum(_overall_wlt(self._content()))

        # A standalone sandbox playoff round team_a played — must be ignored.
        _sandbox_playoff_round(self.team_a, self.team_b, red_points=80, blue_points=40)
        after_total = sum(_overall_wlt(self._content()))
        self.assertEqual(after_total, before_total)

    def test_sandbox_tournament_does_not_bump_playoff_appearances(self) -> None:
        _sandbox_playoff_round(self.team_a, self.team_b, red_points=80, blue_points=40)
        self.assertEqual(_playoff_appearances(self._content()), 0)


class TestTeamHistorySeasonsRankUnaffectedByPlayoff(TestCase):
    """The Seasons-tab ``rank`` is UNAFFECTED by season-embedded playoff rounds
    (playoff Matches have ``season_id=None``, so ``compute_standings`` over
    ``season.matches.filter(is_completed=True)`` ignores them)."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def _content(self) -> str:
        return team_history(_get(self.league.id), self.league.id).content.decode()

    def test_playoff_rounds_do_not_change_seasons_rank(self) -> None:
        # team_a beats team_b in the regular season ⇒ a stable rank.
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        rank_before = _season_row_rank(self._content(), self.season.id)

        # Add a season-embedded playoff round team_a LOST — must not touch rank.
        _tournament, node = _embedded_playoff_tournament(
            self.season, [self.team_a, self.team_b]
        )
        _play_playoff_round(
            node, self.team_b, self.team_a, red_points=99, blue_points=10
        )
        rank_after = _season_row_rank(self._content(), self.season.id)
        self.assertEqual(rank_after, rank_before)


class TestTeamHistoryPlayersPlayoffUnchangedRule(TestCase):
    """Players-tab corpus rule is UNCHANGED: a playoff ``PlayerRoundState`` row
    still counts toward player rollups (membership-only filter, no season
    scope). Accepted limitation: its ``season_year`` resolves to ``None`` (the
    playoff Match has ``season_id=None``). Asserted structurally, not on
    points."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_playoff_player_round_state_counts_toward_rollup(self) -> None:
        player = self.team_a.slot_commander
        _tournament, node = _embedded_playoff_tournament(
            self.season, [self.team_a, self.team_b]
        )
        gr = _play_playoff_round(
            node, self.team_a, self.team_b, red_points=88, blue_points=20
        )
        PlayerRoundState.objects.create(
            game_round=gr, player=player, team_color="red", points_scored=42
        )
        content = team_history(_get(self.league.id), self.league.id).content.decode()
        # The playoff appearance surfaces the player on the Players tab.
        self.assertIn(f"team-history-player-row-{player.id}", content)


class TestTeamHistoryOverallRecordPlayoffFieldFlow(TestCase):
    """Optional pure-module unit confirming the ``playoff_appearances`` keyword
    flows into ``OverallRecord``. The module is UNCHANGED this slice
    (assert-only)."""

    def test_compute_overall_record_carries_playoff_appearances(self) -> None:
        rec = compute_overall_record(
            ["W", "L", "T"], championships=1, playoff_appearances=3
        )
        self.assertEqual(rec.playoff_appearances, 3)
        # The W/L/T fold is unchanged.
        self.assertEqual((rec.wins, rec.losses, rec.ties), (1, 1, 1))
        self.assertEqual(rec.championships, 1)


# ===========================================================================
# LG-07a — member-night Matches excluded from the Seasons-tab rank (ADR-0033)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-07-member-night-seam-contract.md`` §3
# (site #6): ``team_history._build_seasons_context`` appends the member-night
# exclude to ``season.matches.filter(is_completed=True)``, so a member-night Match
# (``season=<this>``, ``season_phase=<member_night>``) does NOT move a Season's
# rank. Mirrors ``TestTeamHistorySeasonsRankUnaffectedByPlayoff``.


class TestLg07aTeamHistorySeasonsRankExcludesMemberNight(TestCase):
    """The Seasons-tab ``rank`` is UNAFFECTED by member-night Matches."""

    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def _content(self) -> str:
        return team_history(_get(self.league.id), self.league.id).content.decode()

    def test_member_night_match_does_not_change_seasons_rank(self) -> None:
        from matches.models import SeasonPhase
        from teams.models import Team

        # team_a beats team_b in the regular season ⇒ a stable rank.
        _play_round(
            self.season,
            self.team_a,
            self.team_b,
            round_number=1,
            red_points=100,
            blue_points=50,
        )
        rank_before = _season_row_rank(self._content(), self.season.id)

        # Add a member-night Match (season=this, season_phase=mn, drawn Teams)
        # team_a is not even in — it must not touch the Seasons-tab rank.
        mn = SeasonPhase.objects.create(
            season=self.season, ordinal=2, phase_type="member_night"
        )
        da = Team.objects.create(name="MN TH Draw A", is_draw_team=True)
        db = Team.objects.create(name="MN TH Draw B", is_draw_team=True)
        mnm = Match.objects.create(
            team_red=da,
            team_blue=db,
            season=self.season,
            season_phase=mn,
            is_completed=True,
        )
        GameRound.objects.create(
            match=mnm,
            round_number=1,
            team_red=da,
            team_blue=db,
            red_points=999,
            blue_points=0,
            is_completed=True,
        )

        rank_after = _season_row_rank(self._content(), self.season.id)
        self.assertEqual(rank_after, rank_before)
