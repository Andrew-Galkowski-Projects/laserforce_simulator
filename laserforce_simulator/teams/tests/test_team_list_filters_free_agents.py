"""LG-00 — Manager + team-list view tests.

Pins:
- ``Team.objects.regular()`` excludes the magic-name ``"Free Agents"`` Team.
- ``Team.objects.all()`` continues to include the Free Agents Team
  (regular() is opt-in; existing call sites are unchanged).
- The ``/teams/`` (URL name ``team_list``) view renders ``.regular()``, so
  the rendered HTML must NOT contain ``"Free Agents"`` and MUST still
  contain a regular team name when one exists.

Seam contract: ``.claude/worktrees/lg-00-seam-contract.md`` §7.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from teams.models import Team, get_free_agents_team


class TestObjectsRegularManagerMethod(TestCase):
    """``Team.objects.regular()`` excludes the Free Agents Team."""

    def test_regular_excludes_free_agents_team(self) -> None:
        """``regular()`` does NOT contain the Free Agents Team."""
        get_free_agents_team()
        Team.objects.create(name="Red Phoenix")
        names = set(Team.objects.regular().values_list("name", flat=True))
        self.assertNotIn("Free Agents", names)
        self.assertIn("Red Phoenix", names)

    def test_objects_all_still_includes_free_agents(self) -> None:
        """``Team.objects.all()`` continues to include the Free Agents Team."""
        get_free_agents_team()
        Team.objects.create(name="Red Phoenix")
        names = set(Team.objects.all().values_list("name", flat=True))
        self.assertIn("Free Agents", names)
        self.assertIn("Red Phoenix", names)


class TestTeamListExcludesFreeAgents(TestCase):
    """The ``team_list`` view filters out the Free Agents Team."""

    def test_team_list_html_does_not_show_free_agents(self) -> None:
        """``GET /teams/`` body does NOT contain the substring ``"Free Agents"``."""
        get_free_agents_team()
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("Free Agents", response.content.decode())

    def test_team_list_still_shows_regular_teams(self) -> None:
        """A regular team's name appears in the rendered list page."""
        get_free_agents_team()
        Team.objects.create(name="Red Phoenix")
        response = self.client.get(reverse("team_list"))
        self.assertEqual(response.status_code, 200)
        body = response.content.decode()
        self.assertIn("Red Phoenix", body)
        self.assertNotIn("Free Agents", body)
