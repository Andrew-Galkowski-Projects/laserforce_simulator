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

    # NOTE: ``test_landing_page_renders_sandbox_flat_links`` retired by
    # LG-01k — ``/`` is now start mode (Tools/Help only, no flat links).
    # The replacement is ``TestLg01kStartModeTopbar`` in
    # ``test_lg01k_base_html_branching.py``.

    def test_landing_page_renders_help_dropdown_toggle(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, 'id="help-nav-link"')

    def test_landing_page_renders_tools_dropdown_toggle(self) -> None:
        response = self.client.get("/")
        self.assertContains(response, 'id="tools-nav-link"')

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
        # Plus the Tools / Help dropdown toggles (the LG-01h League
        # dropdown was retired from sandbox by LG-01k — the retired-id
        # assertion lives in test_lg01k_base_html_branching.py).
        self.assertContains(response, 'id="help-nav-link"')
        self.assertContains(response, 'id="tools-nav-link"')


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


# NOTE: the LG-01h ``leagues-nav-link`` toggle id and the 5
# ``league-{standings,playoffs,finances,history,power-rankings}-topbar-link``
# child ids are RETIRED by LG-01k. The replacement assertions on the
# LG-01k 4-section dropdown structure
# (``league-nav-link`` / ``team-nav-link`` / ``players-nav-link`` /
# ``stats-nav-link`` + the ``topbar-{section}-{key}`` per-entry ids)
# live in ``test_lg01k_base_html_branching.py``.
