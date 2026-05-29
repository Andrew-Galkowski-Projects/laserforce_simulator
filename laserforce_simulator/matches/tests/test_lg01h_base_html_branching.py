"""LG-01h — Django ``TestCase`` tests for the ``templates/base.html``
mode-branching surface.

The seam contract is locked at ``.claude/worktrees/lg-01h-seam-contract.md``
(Part a — Base.html mode branching + Top-bar dropdown items). Sandbox-mode
pages render the 6 flat sandbox links plus the ``Help ▾`` / ``Tools ▾`` /
``League ▾`` dropdowns; league-mode pages omit the 6 flat sandbox links
but still render the three dropdowns.

Tests hand-construct ``League`` rows — LG-01h runs NO simulation.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from matches.models import League

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# The 6 LG-01a-locked flat sandbox link DOM substrings — used to assert
# presence (sandbox mode) and absence (league mode). The link is matched
# by ``href="<url>"`` to avoid false positives from sidebar labels.
SANDBOX_FLAT_LINK_HREFS = (
    "/teams/",  # Teams
    "/players/",  # Players
    "/matches/",  # Matches
    "/matches/simulate-batch/",  # Batch Sim
    "/teams/create/",  # Create Team (CHECK actual URL via reverse below)
    "/maps/",  # Maps
)


# ---------------------------------------------------------------------------
# TestBaseHtmlSandboxBranch
# ---------------------------------------------------------------------------


class TestBaseHtmlSandboxBranch(TestCase):
    """``client.get("/")`` returns 200 and renders the 6 sandbox flat links
    + ``Help ▾`` / ``Tools ▾`` / ``League ▾`` dropdown toggles.
    """

    def test_landing_page_renders_sandbox_flat_links(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        # Each sandbox flat link is identified by its href URL substring on
        # an ``<a class="nav-link" href="...">`` so a sidebar "Team" label
        # cannot false-positive.
        self.assertIn(reverse("team_list"), body)
        self.assertIn(reverse("player_list"), body)
        self.assertIn(reverse("match_list"), body)
        self.assertIn(reverse("simulate_batch"), body)
        self.assertIn(reverse("team_create"), body)
        self.assertIn(reverse("map_list"), body)

    def test_landing_page_renders_help_dropdown_toggle(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, 'id="help-nav-link"')

    def test_landing_page_renders_tools_dropdown_toggle(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, 'id="tools-nav-link"')

    def test_landing_page_renders_league_dropdown_toggle(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, 'id="leagues-nav-link"')

    def test_teams_page_renders_sandbox_shell(self) -> None:
        response = self.client.get("/teams/")
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn(reverse("team_list"), body)
        self.assertIn(reverse("player_list"), body)
        self.assertIn(reverse("match_list"), body)
        self.assertIn(reverse("simulate_batch"), body)
        self.assertIn(reverse("team_create"), body)
        self.assertIn(reverse("map_list"), body)
        # Plus the 3 dropdown toggles.
        self.assertContains(response, 'id="help-nav-link"')
        self.assertContains(response, 'id="tools-nav-link"')
        self.assertContains(response, 'id="leagues-nav-link"')


# ---------------------------------------------------------------------------
# TestBaseHtmlLeagueBranch
# ---------------------------------------------------------------------------


class TestBaseHtmlLeagueBranch(TestCase):
    """``client.get(f"/leagues/{league.id}/")`` (LG-01c league dashboard)
    returns 200; the response does NOT contain the 6 sandbox flat-link
    anchors (asserted by ``<a class="nav-link" href="...">`` substring
    rather than loose text — sidebar labels still legitimately say
    "TEAM"); the Help / Tools / League dropdowns are still present.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="LG01hBaseLeague", mode="league", state="active"
        )

    def test_league_dashboard_returns_200(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertEqual(response.status_code, 200)

    def test_league_dashboard_does_not_render_sandbox_flat_link_anchors(self) -> None:
        """The flat sandbox link anchors live in ``base.html``'s
        ``<div class="navbar-nav ms-auto">`` block in sandbox mode. In
        league mode they MUST be absent. Substring-match the full
        anchor markup so a sidebar label ``Teams`` or ``TEAM`` header
        cannot false-positive.
        """
        response = self.client.get(f"/leagues/{self.league.id}/")
        body = response.content.decode()
        for anchor in (
            f'<a class="nav-link" href="{reverse("team_list")}">',
            f'<a class="nav-link" id="player-list-nav-link" '
            f'href="{reverse("player_list")}">',
            f'<a class="nav-link" href="{reverse("match_list")}">',
            f'<a class="nav-link" href="{reverse("simulate_batch")}">',
            f'<a class="nav-link" href="{reverse("team_create")}">',
            f'<a class="nav-link" href="{reverse("map_list")}">',
        ):
            self.assertNotIn(
                anchor,
                body,
                f"sandbox flat-link anchor leaked into league branch: {anchor!r}",
            )

    def test_league_dashboard_renders_help_dropdown_toggle(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, 'id="help-nav-link"')

    def test_league_dashboard_renders_tools_dropdown_toggle(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, 'id="tools-nav-link"')

    def test_league_dashboard_renders_league_dropdown_toggle(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        self.assertContains(response, 'id="leagues-nav-link"')


# ---------------------------------------------------------------------------
# TestLeagueDropdownItems
# ---------------------------------------------------------------------------


class TestLeagueDropdownItems(TestCase):
    """The ``League ▾`` dropdown has 5 LIVE items in the league branch
    (Standings / Playoffs / Finances / History / Power Rankings) with
    the 5 locked DOM ids.
    """

    @classmethod
    def setUpTestData(cls) -> None:
        cls.league = League.objects.create(
            name="LG01hDropdown", mode="league", state="active"
        )

    def test_dropdown_item_dom_ids_present_in_league_branch(self) -> None:
        response = self.client.get(f"/leagues/{self.league.id}/")
        for dom_id in (
            "league-standings-topbar-link",
            "league-playoffs-topbar-link",
            "league-finances-topbar-link",
            "league-history-topbar-link",
            "league-power-rankings-topbar-link",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_dropdown_item_dom_ids_present_in_sandbox_branch(self) -> None:
        """The ``League ▾`` dropdown also renders in sandbox mode per the
        seam contract — so the 5 dropdown DOM ids must be present at
        ``/`` as well.
        """
        response = self.client.get("/")
        for dom_id in (
            "league-standings-topbar-link",
            "league-playoffs-topbar-link",
            "league-finances-topbar-link",
            "league-history-topbar-link",
            "league-power-rankings-topbar-link",
        ):
            self.assertContains(response, f'id="{dom_id}"')
