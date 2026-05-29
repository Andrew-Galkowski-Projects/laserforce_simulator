"""LG-01f — Django ``TestCase`` tests for
``matches.views._build_league_sidebar_links``.

The seam contract is locked at ``.claude/worktrees/lg-01f-seam-contract.md``
(§0 names, §5b 14-entry list, §5c helper signature + behaviour, §9b
class list). The helper is a flat module-level private function imported
directly via ``from matches.league_views import _build_league_sidebar_links``.

The helper reads
``league.seasons.filter(state="completed").order_by("-id").first()`` for
the displayed-Season fallback when called with ``displayed_season=None``
(via the caller chain), so it touches the DB — Django ``TestCase``.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots
from matches.league_views import _build_league_sidebar_links

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_active_season(league: League, *, name: str = "A") -> Season:
    return Season.objects.create(
        league=league, name=name, start_date=date(2026, 1, 1), state="active"
    )


def _make_draft_season(league: League, *, name: str = "D") -> Season:
    return Season.objects.create(
        league=league, name=name, start_date=date(2026, 1, 1), state="draft"
    )


def _make_completed_season(
    league: League, *, name: str = "C", start_date: date = date(2025, 1, 1)
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="completed",
        starting_team_ids_json=[],
    )


# ---------------------------------------------------------------------------
# TestBuildLeagueSidebarLinks
# ---------------------------------------------------------------------------


class TestBuildLeagueSidebarLinks(TestCase):
    """Per-scenario url + disabled-state assertions."""

    def _entry(self, links, key: str) -> dict:
        for entry in links:
            if entry["key"] == key:
                return entry
        raise AssertionError(f"sidebar key {key!r} not found")

    def test_league_with_active_season_standings_url_targets_active_season(
        self,
    ) -> None:
        league = _make_league("ActSt")
        active = _make_active_season(league)
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "standings")
        self.assertEqual(entry["url"], reverse("season_standings", args=[active.id]))
        self.assertFalse(entry["disabled"])

    def test_league_with_active_season_schedule_url_targets_active_season(
        self,
    ) -> None:
        league = _make_league("ActSc")
        active = _make_active_season(league)
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "schedule")
        self.assertEqual(entry["url"], reverse("season_schedule", args=[active.id]))
        self.assertFalse(entry["disabled"])

    def test_league_with_only_completed_seasons_standings_url_targets_most_recent_completed(
        self,
    ) -> None:
        league = _make_league("CompSt")
        s1 = _make_completed_season(league, name="C1", start_date=date(2024, 1, 1))
        s2 = _make_completed_season(league, name="C2", start_date=date(2025, 1, 1))
        # displayed_season is the most-recent completed (s2 — higher id).
        links = _build_league_sidebar_links(league, s2, None)
        entry = self._entry(links, "standings")
        self.assertEqual(entry["url"], reverse("season_standings", args=[s2.id]))
        # Defensive: must NOT target the older Season.
        self.assertNotEqual(entry["url"], reverse("season_standings", args=[s1.id]))

    def test_league_with_only_completed_seasons_schedule_url_targets_most_recent_completed(
        self,
    ) -> None:
        league = _make_league("CompSc")
        _ = _make_completed_season(league, name="C1", start_date=date(2024, 1, 1))
        s2 = _make_completed_season(league, name="C2", start_date=date(2025, 1, 1))
        links = _build_league_sidebar_links(league, s2, None)
        entry = self._entry(links, "schedule")
        self.assertEqual(entry["url"], reverse("season_schedule", args=[s2.id]))
        self.assertFalse(entry["disabled"])

    def test_league_with_zero_seasons_standings_entry_is_disabled(self) -> None:
        league = _make_league("ZeroSt")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "standings")
        self.assertIsNone(entry["url"])
        self.assertTrue(entry["disabled"])

    def test_league_with_zero_seasons_schedule_entry_is_disabled(self) -> None:
        league = _make_league("ZeroSc")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "schedule")
        self.assertIsNone(entry["url"])
        self.assertTrue(entry["disabled"])

    def test_league_with_draft_season_schedule_url_targets_draft_season(self) -> None:
        league = _make_league("DrSc")
        draft = _make_draft_season(league)
        # The caller's chain picks active_season (which includes draft per LG-01),
        # so the displayed_season passed in for a draft-only League is the draft.
        links = _build_league_sidebar_links(league, draft, None)
        entry = self._entry(links, "schedule")
        self.assertEqual(entry["url"], reverse("season_schedule", args=[draft.id]))
        self.assertFalse(entry["disabled"])

    def test_dashboard_entry_url_always_targets_league_dashboard(self) -> None:
        league = _make_league("DashUrl")
        for displayed in (None, _make_active_season(league)):
            links = _build_league_sidebar_links(league, displayed, None)
            entry = self._entry(links, "dashboard")
            self.assertEqual(
                entry["url"], reverse("league_dashboard", args=[league.id])
            )

    def test_history_entry_url_always_targets_league_history(self) -> None:
        league = _make_league("HistUrl")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "history")
        self.assertEqual(entry["url"], reverse("league_history", args=[league.id]))
        self.assertFalse(entry["disabled"])

    def test_sidebar_active_history_marks_only_history_entry_active(self) -> None:
        league = _make_league("ActHist")
        links = _build_league_sidebar_links(league, None, "history")
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["key"], "history")

    def test_sidebar_active_none_marks_zero_entries_active(self) -> None:
        league = _make_league("ActNone")
        links = _build_league_sidebar_links(league, None, None)
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 0)

    def test_sidebar_active_schedule_marks_only_league_schedule_entry_active(
        self,
    ) -> None:
        league = _make_league("ActSchK")
        active = _make_active_season(league)
        links = _build_league_sidebar_links(league, active, "schedule")
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["key"], "schedule")
        self.assertEqual(active_entries[0]["section"], "league")

    def test_sidebar_active_dashboard_marks_only_dashboard_entry_active(self) -> None:
        league = _make_league("ActDash")
        links = _build_league_sidebar_links(league, None, "dashboard")
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["key"], "dashboard")

    def test_sidebar_active_standings_marks_only_standings_entry_active(self) -> None:
        league = _make_league("ActStK")
        active = _make_active_season(league)
        links = _build_league_sidebar_links(league, active, "standings")
        active_entries = [e for e in links if e["active"]]
        self.assertEqual(len(active_entries), 1)
        self.assertEqual(active_entries[0]["key"], "standings")


# ---------------------------------------------------------------------------
# TestSidebarLinkShape
# ---------------------------------------------------------------------------


class TestSidebarLinkShape(TestCase):
    """14-entry list shape + per-entry 6-key dict + section order."""

    def test_returns_exactly_14_entries_in_pinned_order(self) -> None:
        league = _make_league("Shape14")
        links = _build_league_sidebar_links(league, None, None)
        self.assertEqual(len(links), 14)
        # Pinned key order from §5b.
        expected_keys = [
            "dashboard",
            "standings",
            "schedule",
            "playoffs",
            "finances",
            "history",
            "power_rankings",
            "roster",
            "schedule_team",
            "finances_team",
            "history_team",
            "free_agents",
            "trade",
            "trading_block",
        ]
        self.assertEqual([e["key"] for e in links], expected_keys)

    def test_entries_in_pinned_section_order_top_league_team_players(self) -> None:
        league = _make_league("ShapeSec")
        links = _build_league_sidebar_links(league, None, None)
        sections = [e["section"] for e in links]
        # 1 top + 6 league + 4 team + 3 players.
        self.assertEqual(
            sections,
            ["top"] + ["league"] * 6 + ["team"] * 4 + ["players"] * 3,
        )

    def test_each_entry_has_exactly_6_keys(self) -> None:
        league = _make_league("Shape6Keys")
        links = _build_league_sidebar_links(league, None, None)
        expected_keys = {"key", "label", "section", "url", "disabled", "active"}
        for entry in links:
            self.assertEqual(set(entry.keys()), expected_keys)

    def test_disabled_entries_have_url_none_and_disabled_true(self) -> None:
        league = _make_league("ShapeDis")
        # No Seasons ⇒ standings + schedule also disabled.
        links = _build_league_sidebar_links(league, None, None)
        # The 10 always-disabled entries.
        always_disabled = {
            "playoffs",
            "finances",
            "power_rankings",
            "roster",
            "schedule_team",
            "finances_team",
            "history_team",
            "free_agents",
            "trade",
            "trading_block",
        }
        for entry in links:
            if entry["key"] in always_disabled:
                self.assertIsNone(entry["url"])
                self.assertTrue(entry["disabled"])

    def test_live_entries_have_url_str_and_disabled_false(self) -> None:
        league = _make_league("ShapeLive")
        active = _make_active_season(league)
        links = _build_league_sidebar_links(league, active, None)
        live = {"dashboard", "history", "standings", "schedule"}
        for entry in links:
            if entry["key"] in live:
                self.assertIsInstance(entry["url"], str)
                self.assertFalse(entry["disabled"])

    def test_team_section_schedule_key_is_schedule_team_not_schedule(self) -> None:
        league = _make_league("ShapeTeamSc")
        links = _build_league_sidebar_links(league, None, None)
        team_section = [e for e in links if e["section"] == "team"]
        keys = [e["key"] for e in team_section]
        self.assertIn("schedule_team", keys)
        # The LEAGUE-section Schedule entry exists with key "schedule",
        # but the TEAM-section entry must be "schedule_team".
        self.assertNotIn("schedule", keys)

    def test_team_section_history_key_is_history_team_not_history(self) -> None:
        league = _make_league("ShapeTeamHi")
        links = _build_league_sidebar_links(league, None, None)
        team_section = [e for e in links if e["section"] == "team"]
        keys = [e["key"] for e in team_section]
        self.assertIn("history_team", keys)
        self.assertNotIn("history", keys)


# ---------------------------------------------------------------------------
# LG-01g — TEAM > Schedule entry LIVE wiring (§9d)
# ---------------------------------------------------------------------------


class TestLg01gScheduleTeamEntryLive(TestCase):
    """LG-01g — the ``schedule_team`` sidebar entry flips from LG-01f's
    always-disabled placeholder to LIVE when the resolution chain
    (``league.current_team`` if in Season ⇒ alphabetically-first
    in-Season Team ⇒ ``None``) yields a target Team.

    Locked at ``.claude/worktrees/lg-01g-seam-contract.md`` §4a + §7 + §9d.
    """

    def _entry(self, links, key: str) -> dict:
        for entry in links:
            if entry["key"] == key:
                return entry
        raise AssertionError(f"sidebar key {key!r} not found")

    def test_schedule_team_entry_url_resolves_via_current_team_when_in_season(
        self,
    ) -> None:
        league = _make_league("LiveA")
        active = _make_active_season(league)
        team_in_season, _ = make_team_with_slots("InSeason")
        active.teams.add(team_in_season)
        league.current_team = team_in_season
        league.save()
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "schedule_team")
        self.assertEqual(
            entry["url"],
            reverse(
                "team_schedule",
                kwargs={"league_id": league.id, "team_id": team_in_season.id},
            ),
        )
        self.assertFalse(entry["disabled"])

    def test_schedule_team_entry_url_falls_back_to_first_alphabetical_when_current_team_none(
        self,
    ) -> None:
        league = _make_league("FallNone")
        active = _make_active_season(league)
        # Create teams "B", "A", "C" — alphabetically-first is "A Team"
        # (the conftest helper appends " Team" to the prefix).
        tb, _ = make_team_with_slots("B")
        ta, _ = make_team_with_slots("A")
        tc, _ = make_team_with_slots("C")
        active.teams.add(tb, ta, tc)
        # current_team explicitly None.
        self.assertIsNone(league.current_team)
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "schedule_team")
        self.assertEqual(
            entry["url"],
            reverse(
                "team_schedule",
                kwargs={"league_id": league.id, "team_id": ta.id},
            ),
        )
        self.assertFalse(entry["disabled"])

    def test_schedule_team_entry_url_falls_back_when_current_team_not_in_displayed_season(
        self,
    ) -> None:
        league = _make_league("FallNotIn")
        active = _make_active_season(league)
        ta, _ = make_team_with_slots("InS_A")
        tb, _ = make_team_with_slots("InS_B")
        active.teams.add(ta, tb)
        # current_team is set but NOT enrolled in the Season's M2M.
        team_x, _ = make_team_with_slots("Outside")
        league.current_team = team_x
        league.save()
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "schedule_team")
        # Alphabetically-first in-Season Team is ``InS_A Team``.
        self.assertEqual(
            entry["url"],
            reverse(
                "team_schedule",
                kwargs={"league_id": league.id, "team_id": ta.id},
            ),
        )
        # And NOT the out-of-Season team_x.
        self.assertNotEqual(
            entry["url"],
            reverse(
                "team_schedule",
                kwargs={"league_id": league.id, "team_id": team_x.id},
            ),
        )

    def test_schedule_team_entry_disabled_when_displayed_season_is_none(
        self,
    ) -> None:
        league = _make_league("DisNone")
        links = _build_league_sidebar_links(league, None, None)
        entry = self._entry(links, "schedule_team")
        self.assertIsNone(entry["url"])
        self.assertTrue(entry["disabled"])

    def test_schedule_team_entry_disabled_when_displayed_season_has_no_teams(
        self,
    ) -> None:
        league = _make_league("DisNoTeams")
        active = _make_active_season(league)
        # Season has zero enrolled teams.
        self.assertEqual(active.teams.count(), 0)
        links = _build_league_sidebar_links(league, active, None)
        entry = self._entry(links, "schedule_team")
        self.assertIsNone(entry["url"])
        self.assertTrue(entry["disabled"])
