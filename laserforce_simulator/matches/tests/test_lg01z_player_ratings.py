"""LG-01z-n — Django ``TestCase`` tests for the Player Ratings league screen.

The view ``matches.league_screens.player_ratings.player_ratings(request,
league_id)`` is read-only / GET-only. It is NOT yet URL-wired (the
orchestrator wires the ``stats_player_ratings`` route centrally), so these
tests call the view directly via ``RequestFactory`` with a real session
attached.

Fixtures are hand-constructed League / Season / Team / Player rows —
LG-01z runs NO simulation, so the simulator is never entered.

Scope under test: players whose Team is enrolled in the displayed Season
appear; players on unenrolled teams (and the Free Agents pool) do NOT.
Columns are the 19 rating attributes + ``overall_rating`` (NOT performance
stats).
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.player_ratings import player_ratings
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team, get_free_agents_team


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/stats/player-ratings/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
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


# ---------------------------------------------------------------------------
# Routing / method / 404 / session
# ---------------------------------------------------------------------------


class TestPlayerRatingsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = player_ratings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/stats/player-ratings/")
        )
        response = player_ratings(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            player_ratings(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        player_ratings(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestPlayerRatingsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = player_ratings(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-ratings-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = player_ratings(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ---------------------------------------------------------------------------
# Scope — enrolled-team players appear, others do not
# ---------------------------------------------------------------------------


class TestPlayerRatingsScope(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.enrolled_a, self.enrolled_b = self.teams

    def test_enrolled_team_player_appears(self) -> None:
        enrolled_player = self.enrolled_a.active_players[0]
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn(enrolled_player.name, content)
        self.assertIn(f"/players/{enrolled_player.id}/stats/", content)

    def test_unenrolled_team_player_excluded(self) -> None:
        outsider = Team.objects.create(name="Outsiders")
        op = Player.objects.create(team=outsider, name="Outsider Ace")
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn("Outsider Ace", content)
        self.assertNotIn(f"/players/{op.id}/stats/", content)

    def test_free_agent_pool_player_excluded(self) -> None:
        pool = get_free_agents_team()
        fa = Player.objects.create(team=pool, name="Pool Drifter")
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn("Pool Drifter", content)
        self.assertNotIn(f"/players/{fa.id}/stats/", content)


# ---------------------------------------------------------------------------
# Body — DOM ids, sidebar_active, rating columns, career links
# ---------------------------------------------------------------------------


class TestPlayerRatingsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.player = self.teams[0].active_players[0]

    def test_table_dom_id_present(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn("player-ratings-table", response.content.decode())

    def test_sortable_header_dom_ids_present(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("player-ratings-th-name", content)
        self.assertIn("player-ratings-th-team", content)
        self.assertIn("player-ratings-th-overall_rating", content)
        # A representative rating attribute column is present.
        self.assertIn("player-ratings-th-accuracy", content)

    def test_sidebar_active_is_player_ratings(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-stats-player_ratings", response.content.decode())

    def test_player_links_to_career_page(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertIn(f"/players/{self.player.id}/stats/", response.content.decode())


# ---------------------------------------------------------------------------
# Sorting — LG-00c forgiving fallback + ORM / Python-sentinel branches
# ---------------------------------------------------------------------------


class TestPlayerRatingsSorting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        # Three players on an enrolled team with distinct names + accuracy.
        team = self.teams[0]
        self.zed = Player.objects.create(team=team, name="Zed", accuracy=10)
        self.amy = Player.objects.create(team=team, name="Amy", accuracy=90)
        self.bob = Player.objects.create(team=team, name="Bob", accuracy=50)

    def test_default_sort_returns_200(self) -> None:
        response = player_ratings(_get(self.league.id), self.league.id)
        self.assertEqual(response.status_code, 200)

    def test_sort_by_name_asc(self) -> None:
        # per_page=100 keeps every row on one page so all three named
        # players render regardless of how many auto-slot players exist.
        response = player_ratings(
            _get(self.league.id, query="sort=name&dir=asc&per_page=100"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_accuracy_desc(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=accuracy&dir=desc&per_page=100"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_overall_rating_desc_uses_annotation(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=overall_rating&dir=desc"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_sort_by_preferred_roles_python_branch(self) -> None:
        self.amy.preferred_roles = ["scout"]
        self.amy.save(update_fields=["preferred_roles"])
        response = player_ratings(
            _get(self.league.id, query="sort=preferred_roles&dir=asc"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_sort_falls_back_to_default(self) -> None:
        response = player_ratings(
            _get(self.league.id, query="sort=BOGUS&dir=SIDEWAYS"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)


# ---------------------------------------------------------------------------
# Pagination
# ---------------------------------------------------------------------------


class TestPlayerRatingsPagination(TestCase):
    def test_per_page_paginates_and_carries_querystring(self) -> None:
        league = _make_league()
        season, teams = _make_active_season(league, n_teams=2)
        team = teams[0]
        for i in range(15):
            Player.objects.create(team=team, name=f"Extra{i:02d}")
        response = player_ratings(
            _get(league.id, query="per_page=10&page=2"), league.id
        )
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("player-ratings-pagination", content)
        # Page-2 navigation links must carry the coerced per_page value.
        self.assertIn("per_page=10", content)
