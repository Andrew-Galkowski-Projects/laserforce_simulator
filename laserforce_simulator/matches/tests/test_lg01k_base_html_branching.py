"""LG-01k — Django ``TestCase`` tests for the ``templates/base.html``
3-mode top-bar branching surface.

The seam contract is locked at ``.claude/worktrees/lg-01k-seam-contract.md``.
LG-01k extends the LG-01h 2-value ``app_mode`` enum to a 3-value
``"start" | "league" | "sandbox"`` enum, rewrites the topnav block
around a 3-way ``{% if %} / {% elif %} / {% else %}`` branch, and
introduces a 4-dropdown structure (League / Team / Players / Stats) in
league mode driven by a single regrouped ``top_bar_links`` context key
plus a leading Dashboard home-icon link.

Tests hand-construct ``League`` + ``Season`` rows — LG-01k runs NO
simulation.
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

# ---------------------------------------------------------------------------
# Locked Tools / Help dropdown child ids (LG-01h, preserved verbatim)
# ---------------------------------------------------------------------------

TOOLS_CHILD_IDS = (
    "tools-achievements-topbar-link",
    "tools-screenshot-topbar-link",
    "tools-debug-mode-topbar-link",
    "tools-reset-db-topbar-link",
)

HELP_CHILD_IDS = (
    "help-overview-topbar-link",
    "help-changes-topbar-link",
    "help-custom-rosters-topbar-link",
    "help-debugging-topbar-link",
    "help-lol-gm-forums-topbar-link",
    "help-zen-gm-forums-topbar-link",
)


# ---------------------------------------------------------------------------
# TestLg01kStartModeTopbar
# ---------------------------------------------------------------------------


class TestLg01kStartModeTopbar(TestCase):
    """GET ``/`` (the LG-01a landing) renders the minimum-viable topnav:
    only ``Tools ▾`` + ``Help ▾``. No Dashboard icon, no section
    dropdowns, no flat sandbox links.
    """

    def test_start_mode_renders_only_tools_and_help(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        # Sanity: we ARE on the LG-01a landing.
        self.assertContains(response, 'id="mode-picker"')
        # Universal Tools / Help toggles present.
        self.assertContains(response, 'id="tools-nav-link"')
        self.assertContains(response, 'id="help-nav-link"')
        # Every league-mode-only DOM id is ABSENT.
        for dom_id in (
            "dashboard-nav-link",
            "league-nav-link",
            "team-nav-link",
            "players-nav-link",
            "stats-nav-link",
        ):
            self.assertNotContains(response, f'id="{dom_id}"')
        # Sandbox-mode-only DOM id is ABSENT.
        self.assertNotContains(response, 'id="player-list-nav-link"')
        # The retired LG-01h League toggle id is ABSENT.
        self.assertNotContains(response, 'id="leagues-nav-link"')
        # The flat sandbox link anchors must not appear in the navbar.
        # ``href="/teams/"`` rendered as a ``nav-link`` is the canonical
        # marker for the sandbox Teams anchor. The LG-01a landing template
        # may render the literal ``Sandbox`` mode-card title in body text
        # (which contains the substring "Teams"-adjacent labelling), so
        # we scope the assertion to the exact navbar anchor markup.
        body = response.content.decode()
        self.assertNotIn(
            f'<a class="nav-link" href="{reverse("team_list")}">',
            body,
        )

    def test_start_mode_tools_dropdown_items_present(self) -> None:
        response = self.client.get("/")
        for dom_id in TOOLS_CHILD_IDS:
            self.assertContains(response, f'id="{dom_id}"')

    def test_start_mode_help_dropdown_items_present(self) -> None:
        response = self.client.get("/")
        for dom_id in HELP_CHILD_IDS:
            self.assertContains(response, f'id="{dom_id}"')


# ---------------------------------------------------------------------------
# TestLg01kSandboxModeTopbar
# ---------------------------------------------------------------------------


class TestLg01kSandboxModeTopbar(TestCase):
    """GET ``/teams/`` (sandbox mode) renders the 6 flat sandbox anchors
    plus ``Tools ▾`` + ``Help ▾``. NO ``League ▾`` dropdown anywhere.
    """

    def test_sandbox_mode_renders_6_flat_links(self) -> None:
        response = self.client.get("/teams/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # The 6 flat sandbox link anchors (scope to the exact navbar
        # anchor markup so sidebar / page-body labels cannot
        # false-positive).
        self.assertIn(
            f'<a class="nav-link" href="{reverse("team_list")}">',
            body,
        )
        self.assertIn(
            f'<a class="nav-link" id="player-list-nav-link" '
            f'href="{reverse("player_list")}">',
            body,
        )
        self.assertIn(
            f'<a class="nav-link" href="{reverse("match_list")}">',
            body,
        )
        self.assertIn(
            f'<a class="nav-link" href="{reverse("simulate_batch")}">',
            body,
        )
        self.assertIn(
            f'<a class="nav-link" href="{reverse("team_create")}">',
            body,
        )
        self.assertIn(
            f'<a class="nav-link" href="{reverse("map_list")}">',
            body,
        )
        # Players link DOM id preserved from LG-01a.
        self.assertContains(response, 'id="player-list-nav-link"')
        # Tools / Help toggles present.
        self.assertContains(response, 'id="tools-nav-link"')
        self.assertContains(response, 'id="help-nav-link"')
        # Every league-mode-only DOM id ABSENT.
        for dom_id in (
            "dashboard-nav-link",
            "league-nav-link",
            "team-nav-link",
            "players-nav-link",
            "stats-nav-link",
        ):
            self.assertNotContains(response, f'id="{dom_id}"')
        # Retired LG-01h League toggle id ABSENT.
        self.assertNotContains(response, 'id="leagues-nav-link"')

    def test_sandbox_mode_tools_before_help(self) -> None:
        """LG-01k swaps the LG-01h Help-then-Tools order to
        Tools-then-Help across all 3 modes.
        """
        response = self.client.get("/teams/")
        body = response.content.decode()
        tools_idx = body.find('id="tools-nav-link"')
        help_idx = body.find('id="help-nav-link"')
        self.assertGreaterEqual(tools_idx, 0, "tools-nav-link not rendered")
        self.assertGreaterEqual(help_idx, 0, "help-nav-link not rendered")
        self.assertLess(
            tools_idx,
            help_idx,
            "tools-nav-link must render BEFORE help-nav-link " "(LG-01k order swap)",
        )

    def test_sandbox_mode_no_league_dropdown(self) -> None:
        """The LG-01h ``League ▾`` dropdown is removed from sandbox mode
        entirely — the U+25BE toggle text must not appear.
        """
        response = self.client.get("/teams/")
        body = response.content.decode()
        self.assertNotIn(
            "League ▾",
            body,
            "League toggle text (U+25BE) leaked into sandbox-mode topnav",
        )


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
            f'<a class="nav-link" href="{reverse("team_list")}">',
            f'<a class="nav-link" href="{reverse("match_list")}">',
            f'<a class="nav-link" href="{reverse("simulate_batch")}">',
            f'<a class="nav-link" href="{reverse("team_create")}">',
            f'<a class="nav-link" href="{reverse("map_list")}">',
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
