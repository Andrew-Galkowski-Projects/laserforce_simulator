"""LG-01 — Django ``TestCase`` tests for ``season_standings`` and
``season_schedule`` views.

Gap-filling per code-review WARNING: the seam contract's §6 test plan
covered models / simulator / pure modules but not the two read-only
views. These tests pin 404 behaviour, 200 in each Season state, and
locked DOM-id presence so LG-01a's grilling can build on a verified
surface.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots


def _make_active_season(prefix: str) -> tuple[League, Season]:
    """Helper — build a League + active Season with two slotted Teams."""
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name=f"{prefix} Season", start_date=date(2026, 1, 1)
    )
    team_a, _ = make_team_with_slots(f"{prefix}A")
    team_b, _ = make_team_with_slots(f"{prefix}B")
    season.teams.add(team_a, team_b)
    season.start_season()
    return league, season


class TestSeasonStandingsView(TestCase):
    """``season_standings`` — read-only Standings page."""

    def test_404_on_missing_season_id(self) -> None:
        r = self.client.get(reverse("season_standings", args=[99999]))
        self.assertEqual(r.status_code, 404)

    def test_200_in_draft_with_no_teams_renders_empty_notice(self) -> None:
        league = League.objects.create(name="Empty League")
        season = Season.objects.create(
            league=league, name="Empty Season", start_date=date(2026, 1, 1)
        )
        r = self.client.get(reverse("season_standings", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"season-draft-preview-banner", r.content)
        self.assertIn(b"season-standings-empty", r.content)

    def test_200_in_draft_with_teams_renders_table(self) -> None:
        league = League.objects.create(name="Draft League")
        season = Season.objects.create(
            league=league, name="Draft Season", start_date=date(2026, 1, 1)
        )
        team_a, _ = make_team_with_slots("DraftA")
        team_b, _ = make_team_with_slots("DraftB")
        season.teams.add(team_a, team_b)

        r = self.client.get(reverse("season_standings", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"season-draft-preview-banner", r.content)
        self.assertIn(b"season-standings-table", r.content)
        # Both team names rendered in the table.
        self.assertIn(team_a.name.encode(), r.content)
        self.assertIn(team_b.name.encode(), r.content)

    def test_200_in_active_state_renders_state_badge(self) -> None:
        _league, season = _make_active_season("Active")
        r = self.client.get(reverse("season_standings", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"season-state-badge", r.content)
        self.assertIn(b"active", r.content)
        # Active state does NOT show the draft-preview banner.
        self.assertNotIn(b"season-draft-preview-banner", r.content)

    def test_draft_preview_sorts_by_team_overall_desc(self) -> None:
        """Higher team_overall ranks first in the draft preview."""
        league = League.objects.create(name="Sort League")
        season = Season.objects.create(
            league=league, name="Sort Season", start_date=date(2026, 1, 1)
        )
        # team_a's players get stat boost so its overall is higher.
        team_a, slots_a = make_team_with_slots("SortA")
        team_b, _ = make_team_with_slots("SortB")
        for player in slots_a.values():
            for stat in (
                "accuracy",
                "survival",
                "decision_making",
                "stamina",
                "speed",
                "positioning",
                "communication",
                "teamwork",
            ):
                setattr(player, stat, 90)
            player.save()
        season.teams.add(team_a, team_b)
        r = self.client.get(reverse("season_standings", args=[season.id]))
        body = r.content.decode()
        # team_a should appear before team_b in the rendered HTML.
        self.assertLess(body.index(team_a.name), body.index(team_b.name))


class TestSeasonScheduleView(TestCase):
    """``season_schedule`` — read-only Schedule page."""

    def test_404_on_missing_season_id(self) -> None:
        r = self.client.get(reverse("season_schedule", args=[99999]))
        self.assertEqual(r.status_code, 404)

    def test_200_in_draft_with_no_teams_renders_empty_notice(self) -> None:
        league = League.objects.create(name="Empty League")
        season = Season.objects.create(
            league=league, name="Empty Season", start_date=date(2026, 1, 1)
        )
        r = self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"season-schedule-empty", r.content)

    def test_200_in_draft_with_two_teams_renders_matchday_1(self) -> None:
        league = League.objects.create(name="Sched League")
        season = Season.objects.create(
            league=league, name="Sched Season", start_date=date(2026, 1, 1)
        )
        team_a, _ = make_team_with_slots("SchedA")
        team_b, _ = make_team_with_slots("SchedB")
        season.teams.add(team_a, team_b)

        r = self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        # N=2 → 1 matchday in round 1 + 1 matchday in round 2.
        self.assertIn(b"season-schedule-matchday-1", r.content)
        self.assertIn(b"season-schedule-matchday-2", r.content)
        self.assertIn(b"season-schedule-table", r.content)

    def test_200_in_active_state(self) -> None:
        _league, season = _make_active_season("ActiveSched")
        r = self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"season-schedule-table", r.content)


# ---------------------------------------------------------------------------
# TestLg01fStandingsScheduleWiring (LG-01f — appended per seam contract §9f)
#
# Append sidebar + session-write assertions to the LG-01 standings + schedule
# views.
# ---------------------------------------------------------------------------


class TestLg01fStandingsScheduleWiring(TestCase):
    """LG-01f — Standings + Schedule pages include the 14-entry sidebar
    partial with the correct ``sidebar_active`` literal AND write
    ``request.session["last_league_id"]``.
    """

    # season_standings ---------------------------------------------------

    def test_standings_lg01f_sidebar_partial_rendered(self) -> None:
        _league, season = _make_active_season("LfStPart")
        response = self.client.get(reverse("season_standings", args=[season.id]))
        self.assertContains(response, 'id="league-sidebar"')

    def test_standings_lg01f_sidebar_active_is_standings(self) -> None:
        _league, season = _make_active_season("LfStActive")
        response = self.client.get(reverse("season_standings", args=[season.id]))
        body = response.content.decode()
        idx = body.find('id="sidebar-league-standings"')
        self.assertGreaterEqual(idx, 0)
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertIn("active", element)

    def test_standings_lg01f_session_write_last_league_id(self) -> None:
        _league, season = _make_active_season("LfStSess")
        self.client.get(reverse("season_standings", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    # season_schedule ----------------------------------------------------

    def test_schedule_lg01f_sidebar_partial_rendered(self) -> None:
        _league, season = _make_active_season("LfScPart")
        response = self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertContains(response, 'id="league-sidebar"')

    def test_schedule_lg01f_sidebar_active_is_league_schedule(self) -> None:
        _league, season = _make_active_season("LfScActive")
        response = self.client.get(reverse("season_schedule", args=[season.id]))
        body = response.content.decode()
        idx = body.find('id="sidebar-league-schedule"')
        self.assertGreaterEqual(idx, 0)
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertIn("active", element)
        # Exactly one active entry across the sidebar.
        links = response.context["sidebar_links"]
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["key"], "schedule")

    def test_schedule_lg01f_session_write_last_league_id(self) -> None:
        _league, season = _make_active_season("LfScSess")
        self.client.get(reverse("season_schedule", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)
