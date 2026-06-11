"""LG-01z-c — Django ``TestCase`` tests for the Team Roster league screen.

The view ``matches.league_screens.team_roster.team_roster(request,
league_id)`` is read-only / GET-only. It is NOT yet URL-wired (the
orchestrator wires the ``team_roster`` route centrally), so these tests
call the view directly via ``RequestFactory`` with a real session
attached.

Fixtures are hand-constructed League / Season / Team rows — LG-01z runs
NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

from datetime import date

from django.contrib.sessions.middleware import SessionMiddleware
from django.http import Http404
from django.test import RequestFactory, TestCase

from matches.league_screens.team_roster import team_roster
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Player


def _attach_session(request):
    """Run SessionMiddleware so the view's session write succeeds."""
    SessionMiddleware(lambda r: None).process_request(request)
    request.session.save()
    return request


def _get(league_id: int, *, query: str = ""):
    path = f"/leagues/{league_id}/team/roster/"
    if query:
        path = f"{path}?{query}"
    request = RequestFactory().get(path)
    return _attach_session(request)


def _make_league(name: str = "RosterLeague") -> League:
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


# ---------------------------------------------------------------------------
# Routing / method / 404 / session
# ---------------------------------------------------------------------------


class TestTeamRosterRouting(TestCase):
    def test_get_returns_200(self) -> None:
        league = _make_league()
        _make_active_season(league)
        response = team_roster(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        request = _attach_session(
            RequestFactory().post(f"/leagues/{league.id}/team/roster/")
        )
        response = team_roster(request, league.id)
        self.assertEqual(response.status_code, 405)

    def test_bad_league_id_returns_404(self) -> None:
        with self.assertRaises(Http404):
            team_roster(_get(999999), 999999)

    def test_writes_last_league_id_to_session(self) -> None:
        league = _make_league()
        _make_active_season(league)
        request = _get(league.id)
        team_roster(request, league.id)
        self.assertEqual(request.session.get("last_league_id"), league.id)


# ---------------------------------------------------------------------------
# Empty state — no Season
# ---------------------------------------------------------------------------


class TestTeamRosterEmptyState(TestCase):
    def test_no_season_renders_empty_notice(self) -> None:
        league = _make_league()
        response = team_roster(_get(league.id), league.id)
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn("roster-empty-notice", content)
        self.assertIn("No Season", content)

    def test_no_season_still_renders_sidebar(self) -> None:
        league = _make_league()
        response = team_roster(_get(league.id), league.id)
        self.assertIn("league-sidebar", response.content.decode())


# ---------------------------------------------------------------------------
# Body — starting six, bench, DOM ids, sidebar_active
# ---------------------------------------------------------------------------


class TestTeamRosterBody(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, self.team_b = self.teams
        # Pin a default team so the resolver is deterministic.
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_starting_and_bench_table_dom_ids_present(self) -> None:
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("roster-team-picker", content)
        self.assertIn("roster-starting-table", content)
        self.assertIn("roster-bench-table", content)

    def test_starting_six_players_rendered(self) -> None:
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for player in self.team_a.active_players:
            self.assertIn(player.name, content)

    def test_player_links_to_in_league_player_page(self) -> None:
        # LG-06h: player-name link repointed to league_player_detail.
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for player in self.team_a.active_players:
            self.assertIn(f"/leagues/{self.league.id}/players/{player.id}/", content)

    def test_bench_player_rendered(self) -> None:
        bench = Player.objects.create(team=self.team_a, name="Benchwarmer")
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertIn("Benchwarmer", content)
        # LG-06h: player-name link repointed to league_player_detail.
        self.assertIn(f"/leagues/{self.league.id}/players/{bench.id}/", content)

    def test_sidebar_active_is_roster(self) -> None:
        response = team_roster(_get(self.league.id), self.league.id)
        self.assertIn("sidebar-team-roster", response.content.decode())

    def test_bio_and_proxy_columns_present(self) -> None:
        # Both roster tables surface bio columns + the deferred MMR / Rank /
        # Potential proxies (rendered "-" until STAT-PROXY-01).
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for header in (
            "Home Site",
            "Height",
            "Games",
            "Started",
            "MMR",
            "Rank",
            "Potential",
        ):
            self.assertIn(header, content)


# ---------------------------------------------------------------------------
# Team selection — ?team_id= validation + default
# ---------------------------------------------------------------------------


class TestTeamRosterTeamSelection(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.season, teams = _make_active_season(self.league, n_teams=3)
        self.team_a, self.team_b, self.team_c = teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])

    def test_team_id_selects_requested_enrolled_team(self) -> None:
        response = team_roster(
            _get(self.league.id, query=f"team_id={self.team_b.id}"),
            self.league.id,
        )
        content = response.content.decode()
        # Team B's commander should appear; the selected heading is Team B.
        self.assertIn(f"{self.team_b.name} — Roster", content)
        for player in self.team_b.active_players:
            self.assertIn(player.name, content)

    def test_invalid_team_id_falls_back_to_default(self) -> None:
        response = team_roster(
            _get(self.league.id, query="team_id=not-an-int"), self.league.id
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{self.team_a.name} — Roster", content)

    def test_non_enrolled_team_id_falls_back_to_default(self) -> None:
        outsider, _ = make_team_with_slots("Outsider")
        response = team_roster(
            _get(self.league.id, query=f"team_id={outsider.id}"),
            self.league.id,
        )
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        self.assertIn(f"{self.team_a.name} — Roster", content)

    def test_default_is_current_team_when_set(self) -> None:
        self.league.current_team = self.team_c
        self.league.save(update_fields=["current_team"])
        response = team_roster(_get(self.league.id), self.league.id)
        self.assertIn(f"{self.team_c.name} — Roster", response.content.decode())

    def test_default_resolves_when_current_team_unset(self) -> None:
        self.league.current_team = None
        self.league.save(update_fields=["current_team"])
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        self.assertEqual(response.status_code, 200)
        # Alphabetically-first enrolled team is the resolver default.
        self.assertIn("roster-starting-table", content)

    def test_picker_lists_all_enrolled_teams(self) -> None:
        response = team_roster(_get(self.league.id), self.league.id)
        content = response.content.decode()
        for team in (self.team_a, self.team_b, self.team_c):
            self.assertIn(team.name, content)


# ---------------------------------------------------------------------------
# LG-05 — Potential cell renders on the roster + bench tables
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-05-player-potential-seam-contract.md``
# §5 / §6: the trailing Potential placeholder cell in BOTH the starting-roster
# and bench tables is replaced with ``{{ player.potential|floatformat:1|
# default:"—" }}`` — a filled potential renders its value, a ``None`` potential
# renders the em-dash ``—``.
#
# Uses the direct ``RequestFactory`` view call (the existing roster-test
# pattern). ``Player.potential`` is set EXPLICITLY (the fixture bypasses
# ``league_create`` so the field defaults to ``None``). Appended as a NEW class;
# no existing class is modified. These WILL fail until the Code agent lands
# ``Player.potential`` + the live Potential cell — the TDD red state.


class TestTeamRosterPotentialCell(TestCase):
    """LG-05 — the Potential cell renders ``player.potential`` (floatformat) on
    both the starting-roster and bench tables; ``None`` renders ``—``."""

    def setUp(self) -> None:
        self.league = _make_league("RosterPotL")
        self.season, self.teams = _make_active_season(self.league, n_teams=2)
        self.team_a, _ = self.teams
        self.league.current_team = self.team_a
        self.league.save(update_fields=["current_team"])
        # Give one starting player a filled potential, leave another None.
        starters = list(self.team_a.active_players)
        self.starter_with_pot = starters[0]
        self.starter_with_pot.potential = 77.0
        self.starter_with_pot.save(update_fields=["potential"])
        self.starter_none = starters[1]  # potential stays None
        # Bench players: one with a filled potential, one with None.
        self.bench_with_pot = Player.objects.create(
            team=self.team_a, name="BenchPotStar", potential=42.0
        )
        self.bench_none = Player.objects.create(
            team=self.team_a, name="BenchPotNone", potential=None
        )

    def test_starting_table_renders_filled_potential_value(self) -> None:
        content = team_roster(_get(self.league.id), self.league.id).content.decode()
        # floatformat:1 renders 77.0 → "77.0".
        self.assertIn("77.0", content)

    def test_bench_table_renders_filled_potential_value(self) -> None:
        content = team_roster(_get(self.league.id), self.league.id).content.decode()
        self.assertIn("BenchPotStar", content)
        self.assertIn("42.0", content)

    def test_none_potential_renders_em_dash(self) -> None:
        content = team_roster(_get(self.league.id), self.league.id).content.decode()
        # The None-potential players render the em-dash placeholder (literal or
        # HTML entity).
        self.assertIn("BenchPotNone", content)
        self.assertTrue(
            ("—" in content) or ("&#8212;" in content) or ("&mdash;" in content),
            msg="em-dash placeholder for a None potential not found",
        )
