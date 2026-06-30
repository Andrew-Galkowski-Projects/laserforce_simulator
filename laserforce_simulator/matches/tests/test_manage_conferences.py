"""CONF-05 — tests for the draft-Season Manage Conferences composer.

Covers the pure partition validator (`_validate_conference_partition`), the
`manage_conferences` view (GET draft composer / GET active read-only / POST
create-replace-clear / validation errors / draft-only + method guards /
session write), and the draft-only dashboard entry link. Builds on the CONF-01
`Conference` model; no model/migration. Schema-level assertions only.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.league_views import _validate_conference_partition
from matches.models import Conference, League, Season
from matches.tests.conftest import make_team_with_slots


def _draft_season_with_teams(prefix: str, n: int):
    """A draft Season enrolling ``n`` fully-slotted Teams (no Conferences)."""
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        team, _ = make_team_with_slots(f"{prefix}t{i}")
        season.teams.add(team)
        teams.append(team)
    return season, teams


# ---------------------------------------------------------------------------
# Pure validator
# ---------------------------------------------------------------------------


class TestValidateConferencePartition(TestCase):
    def test_empty_submission_is_zero_conference(self):
        errors, normalized = _validate_conference_partition([], {}, {1, 2, 3, 4})
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [])

    def test_blank_only_names_is_zero_conference(self):
        # No conference names at all (the composer submits none) ⇒ flat Season.
        errors, normalized = _validate_conference_partition(
            [], {1: None, 2: None}, {1, 2}
        )
        self.assertEqual((errors, normalized), ([], []))

    def test_valid_full_partition(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"],
            {1: 0, 2: 0, 3: 1, 4: 1},
            {1, 2, 3, 4},
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("West", [1, 2]), ("East", [3, 4])])

    def test_single_conference_with_all_teams_is_valid(self):
        errors, normalized = _validate_conference_partition(
            ["Only"], {1: 0, 2: 0, 3: 0}, {1, 2, 3}
        )
        self.assertEqual(errors, [])
        self.assertEqual(normalized, [("Only", [1, 2, 3])])

    def test_unassigned_team_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: None}, {1, 2, 3, 4}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)

    def test_conference_under_two_teams_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "East"], {1: 0, 2: 0, 3: 1, 4: 0}, {1, 2, 3, 4}
        )
        self.assertIn("Each conference needs at least 2 teams.", errors)
        self.assertIsNone(normalized)

    def test_empty_name_rejected(self):
        errors, normalized = _validate_conference_partition(
            ["West", "  "], {1: 0, 2: 0, 3: 1, 4: 1}, {1, 2, 3, 4}
        )
        self.assertIn("Conference names cannot be empty.", errors)
        self.assertIsNone(normalized)

    def test_out_of_range_index_counts_as_unassigned(self):
        errors, normalized = _validate_conference_partition(
            ["West"], {1: 0, 2: 0, 3: 5}, {1, 2, 3}
        )
        self.assertIn("Every team must be assigned to a conference.", errors)
        self.assertIsNone(normalized)


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


class TestManageConferencesView(TestCase):
    def _url(self, season):
        return reverse("manage_conferences", args=[season.id])

    def test_get_draft_renders_composer(self):
        season, teams = _draft_season_with_teams("get", 4)
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        self.assertTemplateUsed(resp, "seasons/manage_conferences.html")
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-form"', html)
        self.assertIn('id="manage-conferences-add"', html)
        self.assertIn('id="manage-conferences-submit"', html)
        for team in teams:
            self.assertIn(f'id="manage-conferences-team-{team.id}"', html)

    def test_get_writes_last_league_id(self):
        season, _ = _draft_season_with_teams("sess", 2)
        self.client.get(self._url(season))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_missing_season_404(self):
        resp = self.client.get(reverse("manage_conferences", args=[999999]))
        self.assertEqual(resp.status_code, 404)

    def test_disallowed_method_405(self):
        season, _ = _draft_season_with_teams("meth", 2)
        resp = self.client.generic("DELETE", self._url(season))
        self.assertEqual(resp.status_code, 405)

    def test_post_valid_creates_partition(self):
        season, teams = _draft_season_with_teams("post", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "1",
            f"team_{teams[3].id}_conference": "1",
        }
        resp = self.client.post(self._url(season), data)
        self.assertRedirects(resp, self._url(season))
        confs = list(season.conferences.order_by("ordinal"))
        self.assertEqual([c.name for c in confs], ["West", "East"])
        self.assertEqual([c.ordinal for c in confs], [1, 2])
        self.assertEqual(
            set(confs[0].teams.values_list("id", flat=True)),
            {teams[0].id, teams[1].id},
        )
        self.assertEqual(
            set(confs[1].teams.values_list("id", flat=True)),
            {teams[2].id, teams[3].id},
        )

    def test_post_replaces_existing_conferences(self):
        season, teams = _draft_season_with_teams("replace", 4)
        stale = Conference.objects.create(season=season, name="Stale", ordinal=1)
        stale.teams.set([teams[0].id, teams[1].id])
        data = {
            "conference_name": ["North", "South"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "1",
            f"team_{teams[2].id}_conference": "0",
            f"team_{teams[3].id}_conference": "1",
        }
        self.client.post(self._url(season), data)
        self.assertFalse(season.conferences.filter(name="Stale").exists())
        self.assertEqual(
            list(season.conferences.order_by("ordinal").values_list("name", flat=True)),
            ["North", "South"],
        )

    def test_post_empty_clears_conferences(self):
        season, teams = _draft_season_with_teams("clear", 4)
        conf = Conference.objects.create(season=season, name="Gone", ordinal=1)
        conf.teams.set([t.id for t in teams])
        resp = self.client.post(self._url(season), {})
        self.assertRedirects(resp, self._url(season))
        self.assertEqual(season.conferences.count(), 0)

    def test_post_unassigned_team_re_renders_with_error_no_write(self):
        season, teams = _draft_season_with_teams("err", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "1",
            # teams[3] unassigned
        }
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn('id="manage-conferences-errors"', resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_post_under_two_per_conference_error(self):
        season, teams = _draft_season_with_teams("small", 4)
        data = {
            "conference_name": ["West", "East"],
            f"team_{teams[0].id}_conference": "0",
            f"team_{teams[1].id}_conference": "0",
            f"team_{teams[2].id}_conference": "0",
            f"team_{teams[3].id}_conference": "1",  # East has only 1
        }
        resp = self.client.post(self._url(season), data)
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Each conference needs at least 2 teams.", resp.content.decode())
        self.assertEqual(season.conferences.count(), 0)

    def test_post_on_active_season_rejected(self):
        season, _ = _draft_season_with_teams("active", 4)
        season.start_season()
        resp = self.client.post(self._url(season), {"conference_name": ["X", "Y"]})
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(season.conferences.count(), 0)

    def test_get_active_season_is_read_only(self):
        season, teams = _draft_season_with_teams("ro", 4)
        conf = Conference.objects.create(season=season, name="West", ordinal=1)
        conf.teams.set([teams[0].id, teams[1].id])
        conf2 = Conference.objects.create(season=season, name="East", ordinal=2)
        conf2.teams.set([teams[2].id, teams[3].id])
        season.start_season()
        resp = self.client.get(self._url(season))
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('id="manage-conferences-readonly"', html)
        self.assertNotIn('id="manage-conferences-form"', html)
        self.assertIn("West", html)
        self.assertIn("East", html)


# ---------------------------------------------------------------------------
# Dashboard entry link
# ---------------------------------------------------------------------------


class TestManageConferencesDashboardLink(TestCase):
    def test_draft_dashboard_shows_link(self):
        season, _ = _draft_season_with_teams("dlink", 2)
        resp = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'id="season-dashboard-manage-conferences-link"', resp.content.decode()
        )

    def test_active_dashboard_hides_link(self):
        season, _ = _draft_season_with_teams("alink", 2)
        season.start_season()
        resp = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(
            'id="season-dashboard-manage-conferences-link"', resp.content.decode()
        )


class TestManageConferencesLeagueDashboardLink(TestCase):
    """CONF-05 — the draft-only Manage Conferences link also renders on the
    LEAGUE dashboard (/leagues/<id>/), where the create flow lands."""

    def test_draft_league_dashboard_shows_link(self):
        season, _ = _draft_season_with_teams("ldlink", 2)
        resp = self.client.get(reverse("league_dashboard", args=[season.league_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertIn(
            'id="league-dashboard-manage-conferences-link"', resp.content.decode()
        )

    def test_active_league_dashboard_hides_link(self):
        season, _ = _draft_season_with_teams("lalink", 2)
        season.start_season()
        resp = self.client.get(reverse("league_dashboard", args=[season.league_id]))
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn(
            'id="league-dashboard-manage-conferences-link"', resp.content.decode()
        )
