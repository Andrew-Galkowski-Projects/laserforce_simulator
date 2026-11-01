"""LG-00 — the ``Team.objects.regular()`` manager method.

Pins:
- ``Team.objects.regular()`` excludes the magic-name ``"Free Agents"`` Team.
- ``Team.objects.all()`` continues to include the Free Agents Team
  (regular() is opt-in; existing call sites are unchanged).

The ``/teams/`` HTML view tests went with the template views (ANG-08 D1).

Seam contract: ``.claude/worktrees/lg-00-seam-contract.md`` §7.
"""

from __future__ import annotations

from django.test import TestCase

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

    def test_regular_excludes_per_league_pool_team(self) -> None:
        """A Team referenced as a League's ``free_agent_pool`` is hidden."""
        from matches.models import League

        pool = Team.objects.create(name="Spring Free Agents")
        league = League.objects.create(name="Spring")
        league.free_agent_pool = pool
        league.save(update_fields=["free_agent_pool"])
        Team.objects.create(name="Red Phoenix")
        names = set(Team.objects.regular().values_list("name", flat=True))
        self.assertNotIn("Spring Free Agents", names)
        self.assertIn("Red Phoenix", names)
