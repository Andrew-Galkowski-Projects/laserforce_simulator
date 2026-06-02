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

from matches.models import GameRound, League, Match, Season
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


# ---------------------------------------------------------------------------
# TestLg06gStandingsFormSideDetail (LG-06g)
#
# Standings table gains 8 form / side-detail columns (Match Streak, Match L5,
# Round Streak, Round L5, Red/Blue records, Red/Blue points) and ALL 17 columns
# become sortable. Match-grain columns read completed Matches; Round-grain
# columns (Round form + the 4 side columns) read every persisted Season Round
# including in-progress Matches' Rounds.
# ---------------------------------------------------------------------------


_ALL_STANDINGS_KEYS = (
    "rank",
    "team",
    "matches_played",
    "wins",
    "losses",
    "ties",
    "league_points",
    "round_wins",
    "total_score",
    "match_streak",
    "match_l5",
    "round_streak",
    "round_l5",
    "red_wlt",
    "blue_wlt",
    "red_points_for",
    "blue_points_for",
)


class TestLg06gStandingsFormSideDetail(TestCase):
    """LG-06g — form + side-detail columns and sortable headers."""

    def setUp(self) -> None:
        self.league = League.objects.create(name="Lg06g League")
        self.season = Season.objects.create(
            league=self.league,
            name="Lg06g Season",
            start_date=date(2026, 1, 1),
        )
        self.team_a, _ = make_team_with_slots("Lg06gA")
        self.team_b, _ = make_team_with_slots("Lg06gB")
        self.season.teams.add(self.team_a, self.team_b)
        self.season.start_season()

        # One COMPLETED Match: team_a sweeps team_b 2-0. The Match-level
        # red_round*/blue_round* fields drive winner + match-grain; the two
        # GameRounds carry the physical sides (colours swap R1->R2).
        completed = Match.objects.create(
            team_red=self.team_a,
            team_blue=self.team_b,
            season=self.season,
            red_round1_points=100,
            blue_round1_points=50,
            red_round2_points=90,  # team_a's points while physically BLUE in R2
            blue_round2_points=40,
            is_completed=True,
        )
        # R1: team_a physically RED, wins 100-50.
        GameRound.objects.create(
            match=completed,
            round_number=1,
            team_red=self.team_a,
            team_blue=self.team_b,
            red_points=100,
            blue_points=50,
        )
        # R2: colours swap — team_b physically RED, team_a physically BLUE
        # wins 90-40.
        GameRound.objects.create(
            match=completed,
            round_number=2,
            team_red=self.team_b,
            team_blue=self.team_a,
            red_points=40,
            blue_points=90,
        )

        # One IN-PROGRESS Match (R1 only, is_completed=False): excluded from
        # the Match-grain corpus but its Round counts toward the Round-grain
        # corpus. team_a wins again.
        in_progress = Match.objects.create(
            team_red=self.team_a,
            team_blue=self.team_b,
            season=self.season,
            red_round1_points=70,
            blue_round1_points=30,
            is_completed=False,
        )
        GameRound.objects.create(
            match=in_progress,
            round_number=1,
            team_red=self.team_a,
            team_blue=self.team_b,
            red_points=70,
            blue_points=30,
        )

    def _get(self, **params):
        url = reverse("season_standings", args=[self.season.id])
        return self.client.get(url, params)

    def test_all_17_sort_header_dom_ids_present(self) -> None:
        r = self._get()
        self.assertEqual(r.status_code, 200)
        for key in _ALL_STANDINGS_KEYS:
            self.assertContains(r, f'id="season-standings-th-{key}"')

    def test_default_order_is_rank_ascending(self) -> None:
        r = self._get()
        rows = r.context["rows"]
        self.assertEqual([row.rank for row in rows], [1, 2])
        # team_a swept, so it is rank 1.
        self.assertEqual(rows[0].team_id, self.team_a.id)

    def test_match_grain_counts_completed_matches_only(self) -> None:
        r = self._get()
        by_id = {row.team_id: row for row in r.context["rows"]}
        a = by_id[self.team_a.id]
        # One completed Match won ⇒ match streak ("W", 1), L5 (1,0,0).
        self.assertEqual(a.match_streak, ("W", 1))
        self.assertEqual(a.match_l5, (1, 0, 0))
        self.assertEqual(a.wins, 1)

    def test_round_grain_counts_in_progress_rounds(self) -> None:
        r = self._get()
        by_id = {row.team_id: row for row in r.context["rows"]}
        a = by_id[self.team_a.id]
        # 3 Rounds won (2 from the completed Match + 1 from the in-progress
        # Match) ⇒ round streak ("W", 3) — strictly more than the Match grain.
        self.assertEqual(a.round_streak, ("W", 3))
        self.assertEqual(a.round_l5, (3, 0, 0))

    def test_side_split_uses_physical_sides(self) -> None:
        r = self._get()
        by_id = {row.team_id: row for row in r.context["rows"]}
        a = by_id[self.team_a.id]
        # team_a: RED in R1 (win, 100) + RED in the in-progress R1 (win, 70);
        # BLUE in R2 (win, 90).
        self.assertEqual(a.red_wlt, (2, 0, 0))
        self.assertEqual(a.blue_wlt, (1, 0, 0))
        self.assertEqual(a.red_points_for, 170)
        self.assertEqual(a.blue_points_for, 90)
        b = by_id[self.team_b.id]
        # team_b mirror: 2 red losses-as-blue/red etc.
        self.assertEqual(b.red_wlt, (0, 1, 0))  # physical red only in R2
        self.assertEqual(b.blue_wlt, (0, 2, 0))  # physical blue in R1 + in-progress R1

    def test_sort_reorders_but_rank_stays_frozen(self) -> None:
        # Sort by total_score ascending ⇒ the lower-scoring team_b leads the
        # list, but its frozen standings rank is still 2.
        r = self._get(sort="total_score", dir="asc")
        rows = r.context["rows"]
        self.assertEqual(rows[0].team_id, self.team_b.id)
        self.assertEqual(rows[0].rank, 2)
        self.assertEqual(r.context["sort"], "total_score")
        self.assertEqual(r.context["dir"], "asc")

    def test_invalid_sort_falls_back_to_rank(self) -> None:
        r = self._get(sort="bogus")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["sort"], "rank")
        self.assertEqual([row.rank for row in r.context["rows"]], [1, 2])

    def test_sort_by_record_column_exercises_wins_losses_key(self) -> None:
        # red_wlt sorts by (wins, -losses): team_a (2,0,0) vs team_b (0,1,0).
        # desc ⇒ team_a (more red wins) leads; rank stays frozen.
        r = self._get(sort="red_wlt", dir="desc")
        self.assertEqual(r.status_code, 200)
        rows = r.context["rows"]
        self.assertEqual(rows[0].team_id, self.team_a.id)
        self.assertEqual(r.context["sort"], "red_wlt")

    def test_sort_by_streak_column_exercises_signed_length_key(self) -> None:
        # round_streak: team_a ("W",3) sorts above team_b ("L",3) descending
        # by signed run length (+3 vs -3).
        r = self._get(sort="round_streak", dir="desc")
        self.assertEqual(r.status_code, 200)
        rows = r.context["rows"]
        self.assertEqual(rows[0].team_id, self.team_a.id)

    def test_streak_and_record_display_strings_render(self) -> None:
        r = self._get()
        body = r.content.decode()
        # Match streak "W1" and a record "2-0-0" (team_a red_wlt) appear.
        self.assertIn("W1", body)
        self.assertIn("2-0-0", body)


class TestLg06gStandingsDraftPreview(TestCase):
    """LG-06g — draft preview renders all 17 columns zeroed and still sorts."""

    def setUp(self) -> None:
        self.league = League.objects.create(name="Lg06g Draft League")
        self.season = Season.objects.create(
            league=self.league,
            name="Lg06g Draft Season",
            start_date=date(2026, 1, 1),
        )
        team_a, _ = make_team_with_slots("Lg06gDraftA")
        team_b, _ = make_team_with_slots("Lg06gDraftB")
        self.season.teams.add(team_a, team_b)  # left in draft (not started)

    def test_draft_renders_all_17_headers(self) -> None:
        r = self.client.get(reverse("season_standings", args=[self.season.id]))
        self.assertEqual(r.status_code, 200)
        for key in _ALL_STANDINGS_KEYS:
            self.assertContains(r, f'id="season-standings-th-{key}"')

    def test_draft_cells_zeroed(self) -> None:
        r = self.client.get(reverse("season_standings", args=[self.season.id]))
        body = r.content.decode()
        self.assertIn("—", body)  # em-dash for empty streaks
        self.assertIn("0-0-0", body)  # zeroed records / L5

    def test_draft_is_sortable(self) -> None:
        url = reverse("season_standings", args=[self.season.id])
        r = self.client.get(url, {"sort": "team", "dir": "desc"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.context["sort"], "team")
        self.assertEqual(r.context["dir"], "desc")
