"""LG-01g — Django ``TestCase`` tests for the per-Team Schedule view at
``GET /leagues/<int:league_id>/team_schedule/<int:team_id>/`` (URL name
``team_schedule``, view ``matches.league_views.team_schedule``).

The seam contract is locked at ``.claude/worktrees/lg-01g-seam-contract.md``
(§9a). The view is read-only; the Upcoming column enumerates unplayed
(fixture, round_number) pairs that involve the picked Team; the
Completed column enumerates persisted GameRounds for Matches involving
the Team. A dropdown above the columns navigates to a different Team's
view inside the same League.

Tests hand-construct Match + GameRound + Team + League + Season rows
directly per §9e — NO simulator touch, NO ORM mock.patch.
"""

from __future__ import annotations

from datetime import date, timedelta

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers — hand-construct League / Season / Team / Match / GameRound rows.
# Tests MUST NOT enter the simulator. Match.<per-Round point fields> are
# set directly via Match.objects.create(...) kwargs.
# ---------------------------------------------------------------------------


def _make_league(name: str = "LG") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_teams(prefix: str, n: int) -> list[Team]:
    teams: list[Team] = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    return teams


def _make_active_season(
    league: League,
    teams: list[Team],
    *,
    name: str = "S1",
    start_date: date = date(2026, 1, 1),
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="active",
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    season.teams.add(*teams)
    return season


def _make_completed_season(
    league: League,
    teams: list[Team],
    *,
    name: str = "Sc",
    start_date: date = date(2025, 1, 1),
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="completed",
        starting_team_ids_json=sorted(t.id for t in teams),
    )
    season.teams.add(*teams)
    return season


def _persist_round(
    season: Season,
    team_red: Team,
    team_blue: Team,
    round_number: int,
    *,
    red_points: int = 0,
    blue_points: int = 0,
    match: Match | None = None,
) -> tuple[Match, GameRound]:
    """Side-agnostic Match find-or-create + persist a GameRound on the
    correct per-Round point columns. Returns ``(match, game_round)``.
    """
    if match is None:
        # Look up via either Side order so a Round-2 swap reuses the
        # Round-1 Match.
        match = (
            Match.objects.filter(
                season=season, team_red=team_red, team_blue=team_blue
            ).first()
            or Match.objects.filter(
                season=season, team_red=team_blue, team_blue=team_red
            ).first()
        )
        if match is None:
            match = Match.objects.create(
                season=season,
                team_red=team_red,
                team_blue=team_blue,
                is_completed=False,
            )
    # Resolve per-Round point columns against the Match's stored sides
    # (the picked physical sides for that Match — may differ from the
    # per-Round sides on a Round-2 colour swap).
    if team_red.id == match.team_red_id:
        red_col_val = red_points
        blue_col_val = blue_points
    else:
        # Round 2 colour swap: team_red is physically red this Round but
        # was team_blue on the Match row. Persist points to the Match's
        # per-Round columns in the Match's frame of reference.
        red_col_val = blue_points
        blue_col_val = red_points
    if round_number == 1:
        match.red_round1_points = red_col_val
        match.blue_round1_points = blue_col_val
    else:
        match.red_round2_points = red_col_val
        match.blue_round2_points = blue_col_val
        match.is_completed = True
    match.save()
    gr = GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        red_points=red_points,
        blue_points=blue_points,
        is_completed=True,
    )
    return match, gr


def _ts_url(league_id: int, team_id: int) -> str:
    return reverse("team_schedule", kwargs={"league_id": league_id, "team_id": team_id})


# ---------------------------------------------------------------------------
# TestTeamScheduleRouting
# ---------------------------------------------------------------------------


class TestTeamScheduleRouting(TestCase):
    """URL reverse + 200/404/405 + 404 chain."""

    def test_get_returns_200_with_valid_ids(self) -> None:
        league = _make_league("R1")
        teams = _make_teams("R1T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)

    def test_get_returns_405_on_post(self) -> None:
        league = _make_league("R2")
        teams = _make_teams("R2T", 2)
        _make_active_season(league, teams)
        response = self.client.post(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 405)

    def test_get_returns_404_on_missing_league(self) -> None:
        teams = _make_teams("R3T", 2)
        response = self.client.get(_ts_url(99999, teams[0].id))
        self.assertEqual(response.status_code, 404)

    def test_get_returns_404_on_missing_team(self) -> None:
        league = _make_league("R4")
        teams = _make_teams("R4T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, 99999))
        self.assertEqual(response.status_code, 404)

    def test_get_returns_404_when_no_season_in_league(self) -> None:
        league = _make_league("R5")
        teams = _make_teams("R5T", 2)
        # No Season created on this League.
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 404)
        self.assertContains(response, "No Season in this League.", status_code=404)


# ---------------------------------------------------------------------------
# TestTeamScheduleSeasonResolution
# ---------------------------------------------------------------------------


class TestTeamScheduleSeasonResolution(TestCase):
    """displayed_season chain: active > most-recent completed > 404."""

    def test_displayed_season_is_active_when_one_exists(self) -> None:
        league = _make_league("SR1")
        teams = _make_teams("SR1T", 2)
        completed = _make_completed_season(
            league, teams, name="Old", start_date=date(2024, 1, 1)
        )
        active = _make_active_season(
            league, teams, name="Active", start_date=date(2026, 1, 1)
        )
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["displayed_season"].id, active.id)
        self.assertNotEqual(response.context["displayed_season"].id, completed.id)

    def test_displayed_season_falls_back_to_latest_completed(self) -> None:
        league = _make_league("SR2")
        teams = _make_teams("SR2T", 2)
        older = _make_completed_season(
            league, teams, name="C1", start_date=date(2023, 1, 1)
        )
        newer = _make_completed_season(
            league, teams, name="C2", start_date=date(2024, 1, 1)
        )
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["displayed_season"].id, newer.id)
        self.assertNotEqual(response.context["displayed_season"].id, older.id)

    def test_displayed_season_is_none_returns_404(self) -> None:
        league = _make_league("SR3")
        teams = _make_teams("SR3T", 2)
        # League exists, Team exists, no Seasons.
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 404)


# ---------------------------------------------------------------------------
# TestTeamScheduleRowGranularity
# ---------------------------------------------------------------------------


class TestTeamScheduleRowGranularity(TestCase):
    """Per-Round split between Upcoming and Completed."""

    def test_upcoming_row_per_unplayed_fixture(self) -> None:
        league = _make_league("RG1")
        teams = _make_teams("RG1T", 2)  # N=2: 2 fixtures total (R1 + R2).
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)
        upcoming = response.context["upcoming_rows"]
        # 2 fixtures (R1 + R2), both unplayed ⇒ 2 Upcoming rows.
        self.assertEqual(len(upcoming), 2)
        self.assertEqual(len(response.context["completed_rows"]), 0)

    def test_completed_row_per_persisted_game_round(self) -> None:
        league = _make_league("RG2")
        teams = _make_teams("RG2T", 2)
        season = _make_active_season(league, teams)
        # Persist both Rounds. Round 2 swaps sides (per Match colour swap).
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        _persist_round(season, teams[1], teams[0], 2, red_points=30, blue_points=40)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(len(response.context["upcoming_rows"]), 0)

    def test_partial_match_round1_in_completed_round2_in_upcoming(self) -> None:
        league = _make_league("RG3")
        teams = _make_teams("RG3T", 2)
        season = _make_active_season(league, teams)
        # Round 1 played; Round 2 not yet played.
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)
        upcoming = response.context["upcoming_rows"]
        completed = response.context["completed_rows"]
        # 1 in each column.
        self.assertEqual(len(completed), 1)
        self.assertEqual(len(upcoming), 1)
        # Completed row is Round 1; Upcoming row is Round 2.
        self.assertEqual(completed[0]["round_number"], 1)
        self.assertEqual(upcoming[0]["round_number"], 2)


# ---------------------------------------------------------------------------
# TestTeamScheduleSideAnnotation
# ---------------------------------------------------------------------------


class TestTeamScheduleSideAnnotation(TestCase):
    """Side resolution for Upcoming (fixture-driven) and Completed
    (persisted-GameRound-driven), including the Round-2 per-Match
    colour swap.
    """

    def test_round1_upcoming_renders_team_a_red_team_b_blue(self) -> None:
        league = _make_league("SA1")
        teams = _make_teams("SA1T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        upcoming = response.context["upcoming_rows"]
        r1 = next(r for r in upcoming if r["round_number"] == 1)
        # Round 1: team_a_id (the min) is red, team_b_id (the max) is blue.
        team_a_id = min(teams[0].id, teams[1].id)
        team_b_id = max(teams[0].id, teams[1].id)
        self.assertEqual(r1["red_team_id"], team_a_id)
        self.assertEqual(r1["blue_team_id"], team_b_id)

    def test_round2_upcoming_renders_team_b_red_team_a_blue_per_match_colour_swap(
        self,
    ) -> None:
        league = _make_league("SA2")
        teams = _make_teams("SA2T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        upcoming = response.context["upcoming_rows"]
        r2 = next(r for r in upcoming if r["round_number"] == 2)
        # Round 2 colour swap: team_b_id (the max) is red, team_a_id (min) blue.
        team_a_id = min(teams[0].id, teams[1].id)
        team_b_id = max(teams[0].id, teams[1].id)
        self.assertEqual(r2["red_team_id"], team_b_id)
        self.assertEqual(r2["blue_team_id"], team_a_id)

    def test_completed_row_reads_persisted_game_round_team_red_blue(self) -> None:
        league = _make_league("SA3")
        teams = _make_teams("SA3T", 2)
        season = _make_active_season(league, teams)
        # Persist Round 1 with teams[0] red.
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(len(completed), 1)
        row = completed[0]
        self.assertEqual(row["red_team_id"], teams[0].id)
        self.assertEqual(row["blue_team_id"], teams[1].id)


# ---------------------------------------------------------------------------
# TestTeamScheduleOutcome
# ---------------------------------------------------------------------------


class TestTeamScheduleOutcome(TestCase):
    """Per-Round outcome from the picked Team's perspective."""

    def test_outcome_W_when_picked_team_side_per_round_points_higher(self) -> None:
        league = _make_league("OW")
        teams = _make_teams("OWT", 2)
        season = _make_active_season(league, teams)
        # Picked team (teams[0]) plays red, scores 50 vs 10.
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(completed[0]["outcome"], "W")

    def test_outcome_L_when_picked_team_side_per_round_points_lower(self) -> None:
        league = _make_league("OL")
        teams = _make_teams("OLT", 2)
        season = _make_active_season(league, teams)
        # Picked team (teams[0]) plays red, scores 10 vs 50.
        _persist_round(season, teams[0], teams[1], 1, red_points=10, blue_points=50)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(completed[0]["outcome"], "L")

    def test_outcome_T_on_equal_per_round_points(self) -> None:
        league = _make_league("OT")
        teams = _make_teams("OTT", 2)
        season = _make_active_season(league, teams)
        _persist_round(season, teams[0], teams[1], 1, red_points=25, blue_points=25)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(completed[0]["outcome"], "T")

    def test_outcome_is_per_round_not_per_match_winner(self) -> None:
        """Construct a Match where Match.winner = team_a (won Round 1
        50-10, lost Round 2 30-40 — A wins on rolled-up points and rounds
        1-1 + total 80-50). The picked Round-2 row from team_a's
        perspective must be ``"L"`` even though the Match rolled up as
        a win.
        """
        league = _make_league("OPM")
        teams = _make_teams("OPMT", 2)
        season = _make_active_season(league, teams)
        # Round 1: teams[0] red, 50-10 (team_a wins this Round).
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        # Round 2: teams[1] red (colour swap), 40-30 (team_a loses; team_b wins).
        _persist_round(season, teams[1], teams[0], 2, red_points=40, blue_points=30)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        # Two completed rows (Round 1 + Round 2).
        self.assertEqual(len(completed), 2)
        round2_row = next(r for r in completed if r["round_number"] == 2)
        # team[0] played BLUE in Round 2 and scored 30; opponent scored 40 ⇒ "L".
        self.assertEqual(round2_row["outcome"], "L")
        # Sanity: Match-level rollup says team[0] won the Match overall.
        match = Match.objects.get(season=season)
        match.refresh_from_db()
        self.assertEqual(match.winner, teams[0])


# ---------------------------------------------------------------------------
# TestTeamScheduleSorting
# ---------------------------------------------------------------------------


class TestTeamScheduleSorting(TestCase):
    """Row sort orders for both columns."""

    def test_completed_rows_sorted_by_game_round_id_asc(self) -> None:
        league = _make_league("ST1")
        teams = _make_teams("ST1T", 2)
        season = _make_active_season(league, teams)
        # Persist Round 1 (lower id) then Round 2 (higher id).
        _, gr1 = _persist_round(
            season, teams[0], teams[1], 1, red_points=50, blue_points=10
        )
        _, gr2 = _persist_round(
            season, teams[1], teams[0], 2, red_points=30, blue_points=40
        )
        response = self.client.get(_ts_url(league.id, teams[0].id))
        completed = response.context["completed_rows"]
        self.assertEqual(len(completed), 2)
        self.assertEqual(completed[0]["game_round_id"], gr1.id)
        self.assertEqual(completed[1]["game_round_id"], gr2.id)
        self.assertLess(completed[0]["game_round_id"], completed[1]["game_round_id"])

    def test_upcoming_rows_sorted_by_matchday_then_round_number_asc(self) -> None:
        league = _make_league("ST2")
        teams = _make_teams("ST2T", 4)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        upcoming = response.context["upcoming_rows"]
        # Verify the rows are sorted by (matchday, round_number) asc.
        keys = [(r["matchday"], r["round_number"]) for r in upcoming]
        self.assertEqual(keys, sorted(keys))


# ---------------------------------------------------------------------------
# TestTeamScheduleDropdown
# ---------------------------------------------------------------------------


class TestTeamScheduleDropdown(TestCase):
    """Picker form, dropdown select, navigation."""

    def test_team_picker_lists_displayed_season_enrolled_teams_alphabetical(
        self,
    ) -> None:
        league = _make_league("DR1")
        teams = _make_teams("DR1T", 4)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        options = list(response.context["team_picker_options"])
        names = [t.name for t in options]
        self.assertEqual(names, sorted(names))
        self.assertEqual(set(t.id for t in options), set(t.id for t in teams))

    def test_team_picker_select_has_locked_dom_id(self) -> None:
        league = _make_league("DR2")
        teams = _make_teams("DR2T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-team-picker"')

    def test_team_picker_form_navigates_to_new_team_id_url(self) -> None:
        league = _make_league("DR3")
        teams = _make_teams("DR3T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        # The form's inline JS substitutes the picked option's id into
        # the URL path. Assert both team ids appear as <option value="N">.
        body = response.content.decode()
        for t in teams:
            self.assertIn(f'value="{t.id}"', body)
        # Assert the picker form id is present (the form is the
        # navigation surface).
        self.assertContains(response, 'id="team-schedule-team-picker-form"')


# ---------------------------------------------------------------------------
# TestTeamScheduleEmptyStates
# ---------------------------------------------------------------------------


class TestTeamScheduleEmptyStates(TestCase):
    """Empty-state notices in both columns."""

    def test_no_upcoming_games_renders_notice_with_locked_substring(self) -> None:
        league = _make_league("ES1")
        teams = _make_teams("ES1T", 2)
        season = _make_active_season(league, teams)
        # Persist BOTH Rounds so upcoming is empty.
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        _persist_round(season, teams[1], teams[0], 2, red_points=30, blue_points=40)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, "No upcoming games")
        self.assertContains(response, 'id="team-schedule-upcoming-empty"')

    def test_no_completed_games_renders_notice_with_locked_substring(self) -> None:
        league = _make_league("ES2")
        teams = _make_teams("ES2T", 2)
        _make_active_season(league, teams)
        # No persisted GameRounds.
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, "No completed games")
        self.assertContains(response, 'id="team-schedule-completed-empty"')


# ---------------------------------------------------------------------------
# TestTeamScheduleContextKeys
# ---------------------------------------------------------------------------


class TestTeamScheduleContextKeys(TestCase):
    """All 9 frozen context keys + sidebar_active literal."""

    def test_view_ships_nine_frozen_context_keys(self) -> None:
        league = _make_league("CK1")
        teams = _make_teams("CK1T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        expected_keys = {
            "league",
            "displayed_season",
            "team",
            "upcoming_rows",
            "completed_rows",
            "team_picker_options",
            "sidebar_links",
            "sidebar_active",
            "current_team",
        }
        for key in expected_keys:
            self.assertIn(key, response.context, f"missing context key {key!r}")

    def test_sidebar_active_equals_schedule_team(self) -> None:
        league = _make_league("CK2")
        teams = _make_teams("CK2T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.context["sidebar_active"], "schedule_team")


# ---------------------------------------------------------------------------
# TestTeamScheduleSidebarWiring
# ---------------------------------------------------------------------------


class TestTeamScheduleSidebarWiring(TestCase):
    """The TEAM > Schedule sidebar entry is LIVE + active on this page."""

    def _sidebar_entry(self, response, key: str) -> dict:
        for entry in response.context["sidebar_links"]:
            if entry["key"] == key:
                return entry
        raise AssertionError(f"sidebar key {key!r} not in sidebar_links")

    def test_schedule_team_entry_is_live_on_team_schedule_page(self) -> None:
        league = _make_league("SW1")
        teams = _make_teams("SW1T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        entry = self._sidebar_entry(response, "schedule_team")
        self.assertIsNotNone(entry["url"])
        self.assertFalse(entry["disabled"])

    def test_schedule_team_entry_active_true_on_team_schedule_page(self) -> None:
        league = _make_league("SW2")
        teams = _make_teams("SW2T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        entry = self._sidebar_entry(response, "schedule_team")
        self.assertTrue(entry["active"])

    def test_schedule_team_entry_disabled_when_no_season_in_league(self) -> None:
        # The team_schedule view itself 404s in this case (per the rule-3
        # 404 guard), so we exercise the helper indirectly via the league
        # dashboard which renders the sidebar with displayed_season=None.
        league = _make_league("SW3")
        # No Season created in this League.
        response = self.client.get(
            reverse("league_dashboard", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)
        sidebar = response.context["sidebar_links"]
        entry = next(e for e in sidebar if e["key"] == "schedule_team")
        self.assertIsNone(entry["url"])
        self.assertTrue(entry["disabled"])


# ---------------------------------------------------------------------------
# TestTeamScheduleSessionWrite
# ---------------------------------------------------------------------------


class TestTeamScheduleSessionWrite(TestCase):
    """Session write of last_league_id fires after the guards."""

    def test_session_last_league_id_written_after_guards(self) -> None:
        league = _make_league("SE1")
        teams = _make_teams("SE1T", 2)
        _make_active_season(league, teams)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(self.client.session["last_league_id"], league.id)


# ---------------------------------------------------------------------------
# TestTeamScheduleDomIds — one assertion per locked DOM id (§6c).
# ---------------------------------------------------------------------------


class TestTeamScheduleDomIds(TestCase):
    """Locked DOM ids per §6c."""

    def _happy_path(self) -> tuple:
        league = _make_league("DID")
        teams = _make_teams("DIDT", 2)
        season = _make_active_season(league, teams)
        return league, teams, season

    def test_team_schedule_header_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-header"')

    def test_team_schedule_team_picker_form_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-team-picker-form"')

    def test_team_schedule_team_picker_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-team-picker"')

    def test_team_schedule_team_picker_apply_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-team-picker-apply"')

    def test_team_schedule_upcoming_section_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-upcoming-section"')

    def test_team_schedule_upcoming_list_id_present_when_rows(self) -> None:
        league, teams, _ = self._happy_path()
        # Active Season with N=2 ⇒ both Upcoming Rounds exist (none played).
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-upcoming-list"')
        self.assertNotContains(response, 'id="team-schedule-upcoming-empty"')

    def test_team_schedule_upcoming_empty_id_present_when_no_rows(self) -> None:
        league, teams, season = self._happy_path()
        # Persist both Rounds so Upcoming is empty.
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        _persist_round(season, teams[1], teams[0], 2, red_points=30, blue_points=40)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-upcoming-empty"')
        self.assertNotContains(response, 'id="team-schedule-upcoming-list"')

    def test_team_schedule_completed_section_id_present(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-completed-section"')

    def test_team_schedule_completed_list_id_present_when_rows(self) -> None:
        league, teams, season = self._happy_path()
        _persist_round(season, teams[0], teams[1], 1, red_points=50, blue_points=10)
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-completed-list"')
        self.assertNotContains(response, 'id="team-schedule-completed-empty"')

    def test_team_schedule_completed_empty_id_present_when_no_rows(self) -> None:
        league, teams, _ = self._happy_path()
        # No persisted GameRounds.
        response = self.client.get(_ts_url(league.id, teams[0].id))
        self.assertContains(response, 'id="team-schedule-completed-empty"')
        self.assertNotContains(response, 'id="team-schedule-completed-list"')

    def test_team_schedule_upcoming_row_id_format_matchday_round(self) -> None:
        league, teams, _ = self._happy_path()
        response = self.client.get(_ts_url(league.id, teams[0].id))
        upcoming = response.context["upcoming_rows"]
        self.assertTrue(len(upcoming) > 0)
        row = upcoming[0]
        expected_id = (
            f'id="team-schedule-upcoming-row-'
            f'{row["matchday"]}-{row["round_number"]}"'
        )
        self.assertContains(response, expected_id)

    def test_team_schedule_completed_row_id_format_game_round_id(self) -> None:
        league, teams, season = self._happy_path()
        _, gr = _persist_round(
            season, teams[0], teams[1], 1, red_points=50, blue_points=10
        )
        response = self.client.get(_ts_url(league.id, teams[0].id))
        expected_id = f'id="team-schedule-completed-row-{gr.id}"'
        self.assertContains(response, expected_id)


# Silence unused-import warnings (timedelta is reserved for future date
# regression tests; date is used in helpers).
_ = timedelta
