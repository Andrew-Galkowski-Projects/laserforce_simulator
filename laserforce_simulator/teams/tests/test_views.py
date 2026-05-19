"""View/template rendering regression tests for the teams app."""

import re

from django.test import TestCase
from django.urls import reverse

from teams.models import Player, Team


class TeamDetailRosterStatusTest(TestCase):
    """Regression: T-3 — Roster Status Scout row must read '2 slots', not
    a leftover active-roster count concatenated before it ('6 2 slots')."""

    def setUp(self):
        self.team = Team.objects.create(name="Roster Status Team")
        # Three players assigned to non-Scout slots so active_roster has a
        # distinctive, non-zero length that would leak into the Scout badge
        # if the dead placeholder code were still present.
        cmd = Player.objects.create(team=self.team, name="Cmd")
        hvy = Player.objects.create(team=self.team, name="Hvy")
        med = Player.objects.create(team=self.team, name="Med")
        self.team.slot_commander = cmd
        self.team.slot_heavy = hvy
        self.team.slot_medic = med
        self.team.save()

    def _scout_badge_text(self):
        url = reverse("team_detail", kwargs={"team_id": self.team.id})
        html = self.client.get(url).content.decode()
        # The Roster Status card pairs a role label span with a badge span.
        m = re.search(
            r"<span>\s*Scout\s*</span>\s*" r'<span class="badge[^"]*">(.*?)</span>',
            html,
            re.DOTALL,
        )
        self.assertIsNotNone(m, "Scout row not found in Roster Status card")
        return re.sub(r"\s+", " ", m.group(1)).strip()

    def test_scout_row_reads_exactly_two_slots(self):
        self.assertEqual(self._scout_badge_text(), "2 slots")

    def test_no_placeholder_artifact_in_response(self):
        url = reverse("team_detail", kwargs={"team_id": self.team.id})
        self.assertNotContains(self.client.get(url), "<!-- placeholder -->")
