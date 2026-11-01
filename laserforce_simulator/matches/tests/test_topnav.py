"""Tests for the top navigation: the ``app_mode`` context processor and the
``templates/base.html`` 3-mode (start / sandbox / league) top-bar branching.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season

# ---------------------------------------------------------------------------
# Locked retired DOM ids (LG-01h surfaces removed by LG-01k)
# ---------------------------------------------------------------------------

RETIRED_LG01H_LEAGUE_IDS = (
    "leagues-nav-link",
    "league-standings-topbar-link",
    "league-playoffs-topbar-link",
    "league-finances-topbar-link",
    "league-history-topbar-link",
    "league-power-rankings-topbar-link",
)


# The LG-01k start-mode (landing ``/``) top-bar tests went with the landing
# page (ANG-08 D1).


# The LG-01k sandbox-mode top-bar tests went with the last sandbox page
# (``/matches/``, ANG-08 D2): no surviving page renders the sandbox branch.


# ---------------------------------------------------------------------------
# TestLg01kLeagueModeTopbar
# ---------------------------------------------------------------------------


class TestLg01kLeagueModeTopbar(TestCase):
    """GET ``/leagues/<id>/`` (league mode) renders the leading Dashboard
    home-icon link + 4 section dropdown toggles (League / Team / Players
    / Stats) + Tools ▾ + Help ▾. No flat sandbox links. Each section
    dropdown surfaces at least one LIVE ``topbar-{section}-{key}``
    entry. The top Dashboard entry of ``top_bar_links`` is filtered out
    of the regrouped iteration.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="LG01kLeagueMode", mode="league", state="active"
        )
        # Active Season with at least one Team enrolled keeps the
        # ``displayed_season`` chain in the "active" branch so the
        # ``_build_league_sidebar_links`` helper emits Standings /
        # Schedule with LIVE URLs (the test assertions tolerate either
        # LIVE or disabled, but a populated Season exercises the full
        # 23-entry shape).
        cls.season = Season.objects.create(
            league=cls.league,
            name="S1",
            start_date=date(2025, 1, 1),
            state="active",
            schedule_format="single_round_robin",
            starting_team_ids_json=[],
        )

    def test_league_mode_renders_dashboard_icon(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertEqual(response.status_code, 200)
        # Dashboard nav-link DOM id present.
        self.assertContains(response, 'id="dashboard-nav-link"')
        # Locked text content: the literal U+2302 HOUSE character inside
        # the anchor.
        self.assertContains(response, "⌂")
        # Href resolves to the league dashboard.
        body = response.content.decode()
        dash_url = reverse("league_dashboard", kwargs={"league_id": self.league.id})
        self.assertIn(
            f'id="dashboard-nav-link" href="{dash_url}"',
            body,
            "dashboard-nav-link href did not resolve to league_dashboard",
        )

    def test_league_mode_renders_4_section_toggles(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        for dom_id in (
            "league-nav-link",
            "team-nav-link",
            "players-nav-link",
            "stats-nav-link",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_league_mode_renders_tools_help(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, 'id="tools-nav-link"')
        self.assertContains(response, 'id="help-nav-link"')

    def test_league_mode_no_flat_sandbox_links(self) -> None:
        """The 6 LG-01a flat sandbox anchors must NOT appear inside the
        ``<div class="navbar-nav ms-auto">`` block in league mode. Scope
        the assertion to the navbar slice so a sidebar TEAM section
        header cannot false-positive.
        """
        response = self.client.get(f"/leagues/{self.league.id}/")
        body = response.content.decode()
        # The Players link's DOM id is the only flat-anchor DOM id —
        # absent in league mode.
        self.assertNotContains(response, 'id="player-list-nav-link"')
        # Slice the HTML around the navbar's ``ms-auto`` block so the
        # sandbox-link assertions cannot be tricked by sidebar / body
        # markup.
        navbar_marker = '<div class="navbar-nav ms-auto"'
        start_idx = body.find(navbar_marker)
        if start_idx == -1:
            self.fail(
                "navbar ms-auto block not found in rendered HTML — "
                "base.html structure changed unexpectedly"
            )
        # The closing ``</div>`` for ms-auto comes before the closing of
        # the collapse navbar — use a bounded slice.
        slice_end = body.find("</nav>", start_idx)
        navbar_slice = body[start_idx:slice_end]
        # Every sandbox flat-anchor marker absent from the navbar slice.
        for anchor in (
            # ANG-08 D3: literal paths, the Matches / Batch URL names are
            # deleted (D2).
            '<a class="nav-link" href="/teams/">',
            '<a class="nav-link" href="/matches/">',
            '<a class="nav-link" href="/matches/simulate-batch/">',
            '<a class="nav-link" href="/teams/create/">',
            '<a class="nav-link" href="/maps/">',
        ):
            self.assertNotIn(
                anchor,
                navbar_slice,
                f"sandbox flat-link anchor leaked into league navbar: " f"{anchor!r}",
            )

    def test_league_mode_topbar_links_iteration(self) -> None:
        """Each of the 4 section dropdowns surfaces at least one LIVE
        ``topbar-{section}-{key}`` DOM id.
        """
        response = self.client.get(f"/leagues/{self.league.id}/")
        # One representative LIVE key per section (history / roster /
        # free_agents / game_log are all ``coming_soon_*`` LIVE entries,
        # so they render as ``<a id="topbar-..." href="..."``).
        for dom_id in (
            "topbar-league-history",
            "topbar-team-roster",
            "topbar-players-free_agents",
            "topbar-stats-game_log",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_league_mode_dashboard_entry_not_in_dropdowns(self) -> None:
        """The top Dashboard entry of ``top_bar_links``
        (``section="top", key="dashboard"``) is rendered as the leading
        home-icon link, NOT inside any section dropdown — so the
        ``topbar-top-dashboard`` DOM id is never emitted.
        """
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertNotContains(response, "topbar-top-dashboard")

    def test_league_mode_retired_ids_absent(self) -> None:
        """Every LG-01h retired DOM id is ABSENT from the league-mode
        rendered HTML.
        """
        response = self.client.get(f"/leagues/{self.league.id}/")
        for dom_id in RETIRED_LG01H_LEAGUE_IDS:
            self.assertNotContains(
                response,
                f'id="{dom_id}"',
                msg_prefix=f"retired LG-01h DOM id {dom_id!r} leaked",
            )


# ===== app_mode context processor =====
from django.test import RequestFactory, TestCase

# ---------------------------------------------------------------------------
# TestAppModeContextProcessor
# ---------------------------------------------------------------------------


class TestAppModeContextProcessor(TestCase):
    """Exercise the processor function directly via ``RequestFactory()``.

    The locked literals are ``"league"`` and ``"sandbox"``; the locked
    context key is ``"app_mode"``; the returned dict has EXACTLY that
    one key.
    """

    def setUp(self) -> None:
        self.factory = RequestFactory()
        # Late import to ensure the Code agent's new function is resolved
        # at test time; tests fail before the Code agent lands.
        from core.context_processors import app_mode

        self.app_mode = app_mode

    # -- Sandbox branch ------------------------------------------------------

    # NOTE: ``test_root_path_is_sandbox`` retired by LG-01k — ``/`` now
    # resolves to ``"start"``. The replacement assertion lives at
    # ``test_start_mode_for_exact_root_path`` below.

    def test_teams_path_is_sandbox(self) -> None:
        request = self.factory.get("/teams/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_players_path_is_sandbox(self) -> None:
        request = self.factory.get("/players/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_matches_path_is_sandbox(self) -> None:
        request = self.factory.get("/matches/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_maps_path_is_sandbox(self) -> None:
        request = self.factory.get("/maps/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_help_overview_path_is_sandbox(self) -> None:
        request = self.factory.get("/help/overview/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_tools_achievements_path_is_sandbox(self) -> None:
        request = self.factory.get("/tools/achievements/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    # -- League branch -------------------------------------------------------

    def test_leagues_index_path_is_league(self) -> None:
        request = self.factory.get("/leagues/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_league_detail_path_is_league(self) -> None:
        request = self.factory.get("/leagues/1/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_league_history_path_is_league(self) -> None:
        request = self.factory.get("/leagues/1/history/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_season_detail_path_is_league(self) -> None:
        request = self.factory.get("/seasons/1/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    def test_season_standings_path_is_league(self) -> None:
        request = self.factory.get("/seasons/1/standings/")
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "league")

    # -- Edge cases ----------------------------------------------------------

    def test_empty_path_is_sandbox(self) -> None:
        """An empty ``request.path`` does not start with ``/leagues/`` or
        ``/seasons/`` so it should resolve to sandbox without crashing.
        """
        request = self.factory.get("/")
        request.path = ""
        result = self.app_mode(request)
        self.assertEqual(result["app_mode"], "sandbox")

    def test_missing_path_attribute_does_not_crash(self) -> None:
        """A request without a ``.path`` attribute must not crash — the
        processor reads via ``getattr(request, "path", "/")`` per the
        seam contract.
        """
        request = self.factory.get("/")
        # Defensively remove the path attribute. ``del`` is the explicit
        # way to recreate a no-path request that ``getattr`` must handle.
        try:
            del request.path
        except AttributeError:
            # Some Django versions store ``path`` as a property — fall
            # back to setting it via the dict directly. Either way the
            # processor must not raise AttributeError.
            request.__dict__.pop("path", None)
        result = self.app_mode(request)
        self.assertIn("app_mode", result)
        # Default ("/" via getattr) ⇒ sandbox.
        self.assertEqual(result["app_mode"], "sandbox")

    # -- Returned shape ------------------------------------------------------

    def test_returned_dict_has_exactly_one_key_app_mode(self) -> None:
        request = self.factory.get("/")
        result = self.app_mode(request)
        self.assertEqual(list(result.keys()), ["app_mode"])

    def test_returned_value_is_one_of_the_three_literals(self) -> None:
        # LG-01k extended the enum from 2 literals to 3 by adding ``"start"``.
        for path in ("/", "/teams/", "/leagues/", "/seasons/1/", "/help/overview/"):
            request = self.factory.get(path)
            result = self.app_mode(request)
            self.assertIn(result["app_mode"], ("start", "league", "sandbox"))

    # -- LG-01k 3-mode extension --------------------------------------------

    def test_start_mode_for_exact_root_path(self) -> None:
        """LG-01k — an exact ``"/"`` path resolves to the new
        ``"start"`` mode (replaces the LG-01h ``"sandbox"`` fallback
        for the root path).
        """
        request = self.factory.get("/")
        result = self.app_mode(request)
        self.assertEqual(result, {"app_mode": "start"})

    def test_sandbox_mode_for_empty_path(self) -> None:
        """LG-01k — an explicit empty-string ``request.path`` does NOT
        match the ``"/"`` exact-match rule (the LG-01k Code agent must
        distinguish missing/empty path from explicit ``/``) and
        therefore resolves to ``"sandbox"``.
        """
        request = self.factory.get("/")
        request.path = ""
        result = self.app_mode(request)
        self.assertEqual(result, {"app_mode": "sandbox"})

    def test_sandbox_mode_for_missing_path_attribute(self) -> None:
        """LG-01k — a raw object with no ``.path`` attribute resolves
        to ``"sandbox"`` (the missing-attribute case must not crash and
        must NOT spuriously resolve to ``"start"`` via the LG-01h
        ``or "/"`` fallback).
        """
        request = type("R", (), {})()
        result = self.app_mode(request)
        self.assertEqual(result, {"app_mode": "sandbox"})


# The UX-01 top-nav auth-control tests went with the Accounts template views
# (ANG-08 D1): its sign-in / register / sign-out / password links point at
# deleted pages until base.html goes in D3.
