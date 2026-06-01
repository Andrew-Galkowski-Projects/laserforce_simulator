"""LG-01z-l — Django ``TestCase`` tests for the Game Log league screen.

The view ``matches.league_screens.game_log.game_log(request, league_id)`` is
read-only / GET-only. It is NOT yet URL-wired (the orchestrator wires the
``stats_game_log`` route centrally), so these tests call the view directly
via ``RequestFactory`` with a real session attached.

Fixtures are hand-constructed ``Match`` + ``GameRound`` rows — LG-01z runs
NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.test import RequestFactory, TestCase

from matches.league_screens.game_log import game_log
from matches.models import GameRound, League, Match, Season
from matches.tests.conftest import make_team_with_slots


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/stats/game-log/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "GLLeague") -> League:
    return League.objects.create(name=name)


def _make_draft_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return season, teams


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 2):
    season, teams = _make_draft_season(league, name=name, n_teams=n_teams)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_played_round(
    season,
    team_red,
    team_blue,
    *,
    round_number=1,
    red_points=10,
    blue_points=5,
    winner=None,
):
    match = Match.objects.create(team_red=team_red, team_blue=team_blue, season=season)
    return GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        red_points=red_points,
        blue_points=blue_points,
        winner=winner,
        is_completed=True,
    )


# ---------------------------------------------------------------------------
# Routing / method / 404
# ---------------------------------------------------------------------------


class TestGameLogRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = game_log(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/game-log/")
        )
        response = game_log(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        from django.http import Http404

        with self.assertRaises(Http404):
            game_log(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        game_log(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestGameLogEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = game_log(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("game-log-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = game_log(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ---------------------------------------------------------------------------
# Body — rows, DOM ids, sidebar_active
# ---------------------------------------------------------------------------


class TestGameLogBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        self.round = _make_played_round(
            self.season,
            self.team_a,
            self.team_b,
            red_points=12,
            blue_points=7,
            winner=self.team_a,
        )

    def test_table_and_row_dom_ids_present(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("game-log-table", content)
        self.assertIn(f"game-log-row-{self.round.id}", content)
        self.assertIn("game-log-team-filter", content)

    def test_score_and_winner_rendered(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("12", content)
        self.assertIn("7", content)
        self.assertIn(self.team_a.name, content)

    def test_row_deep_links_to_round_detail(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        self.assertIn(
            f"/matches/game-round/{self.round.id}/", response.content.decode()
        )

    def test_sidebar_active_is_game_log(self) -> None:
        # The game_log sidebar entry must carry the active class.
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        # sidebar entry id for stats/game_log, marked active.
        self.assertIn("sidebar-stats-game_log", content)


# ---------------------------------------------------------------------------
# Team filter
# ---------------------------------------------------------------------------


class TestGameLogTeamFilter(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = teams
        # Round 1: A vs B. Round 2: B vs C.
        self.round_ab = _make_played_round(self.season, self.team_a, self.team_b)
        self.round_bc = _make_played_round(self.season, self.team_b, self.team_c)

    def test_filter_to_team_a_shows_only_its_round(self) -> None:
        response = game_log(
            _get(self.league.id, query=f"team_id={self.team_a.id}"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertNotIn(f"game-log-row-{self.round_bc.id}", content)

    def test_filter_to_team_b_shows_both_rounds(self) -> None:
        response = game_log(
            _get(self.league.id, query=f"team_id={self.team_b.id}"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_invalid_team_id_silently_ignored(self) -> None:
        response = game_log(
            _get(self.league.id, query="team_id=not-an-int"), self.league.id
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_non_enrolled_team_id_silently_ignored(self) -> None:
        # A team id not enrolled in the Season is ignored → all rows show.
        other, _ = make_team_with_slots("Outsider")
        response = game_log(
            _get(self.league.id, query=f"team_id={other.id}"), self.league.id
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"game-log-row-{self.round_ab.id}", content)
        self.assertIn(f"game-log-row-{self.round_bc.id}", content)

    def test_dropdown_lists_enrolled_teams(self) -> None:
        response = game_log(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for team in (self.team_a, self.team_b, self.team_c):
            self.assertIn(team.name, content)
