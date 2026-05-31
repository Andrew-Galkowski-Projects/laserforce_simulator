"""LG-01z-f — Django ``TestCase`` tests for the Free Agents league screen.

The view ``matches.league_screens.free_agents.free_agents(request,
league_id)`` is read-only / GET-only. It is NOT yet URL-wired (the
orchestrator wires the ``players_free_agents`` route centrally), so these
tests call the view directly via ``RequestFactory`` with a real session
attached.

Fixtures are hand-constructed League / Season / Team / Player rows —
LG-01z runs NO simulation, so the simulator is never entered.

"Free agent" as implemented = a Player whose Team is the reserved "Free
Agents" pool team (a Player on no competitive roster). Players on any real
Team are NOT free agents.
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.free_agents import free_agents
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/players/free-agents/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "FALeague") -> League:
    """Create a League with its own dedicated free-agent pool Team."""
    league = League.objects.create(name=name)
    pool = Team.objects.create(name=f"{name} Free Agents")
    league.free_agent_pool = pool
    league.save(update_fields=["free_agent_pool"])
    return league


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


class TestFreeAgentsRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = free_agents(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/players/free-agents/")
        )
        response = free_agents(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            free_agents(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        free_agents(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestFreeAgentsEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = free_agents(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("free-agents-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = free_agents(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())

    def test_season_but_no_free_agents_renders_empty_notice(self) -> None:
        league = _make_league()
        # Every Team is enrolled; the pool team has no players → no free agents.
        _make_active_season(league, n_teams=2)
        response = free_agents(_get(league.id), league.id)
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn("free-agents-empty-notice", content)


# ---------------------------------------------------------------------------
# Free-agent definition — pool team OR not enrolled in displayed Season
# ---------------------------------------------------------------------------


class TestFreeAgentsDefinition(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.enrolled_a, self.enrolled_b = self.teams

    def test_pool_team_player_is_a_free_agent(self) -> None:
        pool = self.league.free_agent_pool
        fa = Player.objects.create(team=pool, name="PoolStar")
        response = free_agents(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("PoolStar", content)
        self.assertIn(f"/players/{fa.id}/stats/", content)

    def test_player_on_unenrolled_team_is_not_a_free_agent(self) -> None:
        # A Player on any real Team — even one not enrolled in this Season —
        # has a team, so they are NOT a free agent.
        outsider = Team.objects.create(name="Outsiders")
        Player.objects.create(team=outsider, name="Outsider Ace")
        response = free_agents(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertNotIn("Outsider Ace", content)

    def test_player_on_enrolled_team_is_not_a_free_agent(self) -> None:
        enrolled_player = self.enrolled_a.active_players[0]
        response = free_agents(_get(self.league.id), self.league.id)
        content = response.content.decode()
        # Enrolled-team players must NOT appear in the free-agent listing.
        self.assertNotIn(enrolled_player.name, content)

    def test_another_leagues_pool_player_is_not_a_free_agent(self) -> None:
        # Free agents are per-League: a Player in a DIFFERENT League's pool
        # must NOT appear on this League's Free Agents screen.
        other_league = _make_league("OtherLeague")
        Player.objects.create(team=other_league.free_agent_pool, name="Foreign Agent")
        response = free_agents(_get(self.league.id), self.league.id)
        self.assertNotIn("Foreign Agent", response.content.decode())


# ---------------------------------------------------------------------------
# Body — DOM ids, sidebar_active, career links
# ---------------------------------------------------------------------------


class TestFreeAgentsBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, _ = _make_active_season(self.league, n_teams=2)
        self.pool = self.league.free_agent_pool
        self.fa = Player.objects.create(team=self.pool, name="Agent Smith")

    def test_table_dom_id_present(self) -> None:
        response = free_agents(_get(self.league.id), self.league.id)
        self.assertIn("free-agents-table", response.content.decode())

    def test_sortable_header_dom_ids_present(self) -> None:
        response = free_agents(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("free-agents-th-name", content)
        self.assertIn("free-agents-th-overall_rating", content)
        self.assertIn("free-agents-th-team", content)

    def test_sidebar_active_is_free_agents(self) -> None:
        response = free_agents(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-players-free_agents", response.content.decode())

    def test_player_links_to_career_page(self) -> None:
        response = free_agents(_get(self.league.id), self.league.id)
        self.assertIn(f"/players/{self.fa.id}/stats/", response.content.decode())


# ---------------------------------------------------------------------------
# Sorting — LG-00c forgiving fallback + ORM / Python-sentinel branches
# ---------------------------------------------------------------------------


class TestFreeAgentsSorting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, _ = _make_active_season(self.league, n_teams=2)
        self.pool = self.league.free_agent_pool
        # Three free agents with distinct names + accuracy values.
        self.zed = Player.objects.create(team=self.pool, name="Zed", accuracy=10)
        self.amy = Player.objects.create(team=self.pool, name="Amy", accuracy=90)
        self.bob = Player.objects.create(team=self.pool, name="Bob", accuracy=50)

    def test_default_sort_returns_200(self) -> None:
        response = free_agents(_get(self.league.id), self.league.id)
        self.assertEqual(response.status_code, 200)

    def test_sort_by_name_asc(self) -> None:
        response = free_agents(
            _get(self.league.id, query="sort=name&dir=asc"), self.league.id
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_accuracy_desc(self) -> None:
        response = free_agents(
            _get(self.league.id, query="sort=accuracy&dir=desc"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertLess(content.index("Amy"), content.index("Bob"))
        self.assertLess(content.index("Bob"), content.index("Zed"))

    def test_sort_by_preferred_roles_python_branch(self) -> None:
        self.amy.preferred_roles = ["scout"]
        self.amy.save(update_fields=["preferred_roles"])
        response = free_agents(
            _get(self.league.id, query="sort=preferred_roles&dir=asc"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)

    def test_invalid_sort_falls_back_to_default(self) -> None:
        response = free_agents(
            _get(self.league.id, query="sort=BOGUS&dir=SIDEWAYS"),
            self.league.id,
        )
        self.assertEqual(response.status_code, 200)
