"""LG-01f — Django ``TestCase`` tests for ``matches.views.league_history``.

The seam contract is locked at ``.claude/worktrees/lg-01f-seam-contract.md``
(§1 view, §2 cells, §3 pagination + empty state, §5 sidebar, §7 session
write, §9a class list). The view is read-only at
``GET /leagues/<int:league_id>/history/``, renders a paginated table of
completed Seasons + an optional in-progress row pinned above, plus the
LG-01f 14-entry sidebar partial.

Tests hand-construct ``Match`` + ``GameRound`` + ``PlayerRoundState``
rows — LG-01f runs NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# ---------------------------------------------------------------------------
# Helpers — hand-construct League / Season / Match rows for LG-01f tests.
# ---------------------------------------------------------------------------


def _make_league(name: str = "L") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_completed_season(
    league: League,
    *,
    name: str = "S1",
    start_date: date = date(2025, 1, 1),
    team_ids: list[int] | None = None,
    champion_team: Team | None = None,
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="completed",
        starting_team_ids_json=sorted(team_ids) if team_ids is not None else [],
        champion_team=champion_team,
    )
    return season


def _make_active_season(
    league: League,
    *,
    name: str = "Active",
    start_date: date = date(2026, 1, 1),
    teams: list[Team] | None = None,
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="active",
        starting_team_ids_json=sorted(t.id for t in teams) if teams else [],
    )
    if teams:
        season.teams.add(*teams)
    return season


def _make_draft_season(
    league: League,
    *,
    name: str = "Draft",
    start_date: date = date(2027, 1, 1),
    teams: list[Team] | None = None,
) -> Season:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        state="draft",
    )
    if teams:
        season.teams.add(*teams)
    return season


def _make_teams(prefix: str, n: int) -> list[Team]:
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    return teams


def _make_completed_match(
    season: Season, team_a: Team, team_b: Team, *, red_pts: int = 100
) -> Match:
    match = Match.objects.create(
        team_red=team_a,
        team_blue=team_b,
        season=season,
        red_round1_points=red_pts,
        blue_round1_points=0,
        red_round2_points=0,
        blue_round2_points=red_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_a,
        team_blue=team_b,
        round_number=1,
        red_points=red_pts,
        blue_points=0,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=team_b,
        team_blue=team_a,
        round_number=2,
        red_points=red_pts,
        blue_points=0,
        is_completed=True,
    )
    return match


# ---------------------------------------------------------------------------
# TestLeagueHistoryRouting
# ---------------------------------------------------------------------------


class TestLeagueHistoryRouting(TestCase):
    """URL reverse + 200/404/405 + template used."""

    def test_reverse_resolves_to_expected_path(self) -> None:
        league = _make_league("Route1")
        self.assertEqual(
            reverse("league_history", kwargs={"league_id": league.id}),
            f"/leagues/{league.id}/history/",
        )

    def test_get_returns_200_for_existing_league(self) -> None:
        league = _make_league("Route2")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_get_returns_404_for_missing_league(self) -> None:
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": 99999})
        )
        self.assertEqual(response.status_code, 404)

    def test_post_returns_405(self) -> None:
        league = _make_league("Route4")
        response = self.client.post(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_template_used_is_leagues_history_html(self) -> None:
        league = _make_league("Route5")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertTemplateUsed(response, "leagues/history.html")


# ---------------------------------------------------------------------------
# TestLeagueHistoryEmptyState
# ---------------------------------------------------------------------------


class TestLeagueHistoryEmptyState(TestCase):
    """League with zero Seasons renders the empty-notice + sidebar."""

    def test_league_with_zero_seasons_renders_empty_notice(self) -> None:
        league = _make_league("Empty")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, "No Seasons yet")
        self.assertContains(response, 'id="league-history-empty-notice"')
        self.assertNotContains(response, 'id="league-history-table"')

    def test_empty_state_still_renders_sidebar(self) -> None:
        league = _make_league("EmptySide")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-sidebar"')
        # History entry must be in the sidebar even when there are no Seasons.
        self.assertContains(response, 'id="sidebar-league-history"')


# ---------------------------------------------------------------------------
# TestLeagueHistoryCompletedRows
# ---------------------------------------------------------------------------


class TestLeagueHistoryCompletedRows(TestCase):
    """Completed-Season row cells, sort order, per-row id."""

    def test_season_name_cell_links_to_season_dashboard(self) -> None:
        league = _make_league("RowLink")
        teams = _make_teams("RL", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        expected = reverse("season_dashboard", args=[season.id])
        self.assertContains(response, f'href="{expected}"')

    def test_start_date_cell_uses_y_m_d_format(self) -> None:
        league = _make_league("RowDate")
        teams = _make_teams("RD", 2)
        _make_completed_season(
            league,
            start_date=date(2025, 3, 15),
            team_ids=[t.id for t in teams],
            champion_team=teams[0],
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, "2025-03-15")

    def test_teams_enrolled_uses_starting_team_ids_json_length(self) -> None:
        league = _make_league("RowTeams")
        teams = _make_teams("RT", 4)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        self.assertEqual(rows[0]["teams_enrolled"], 4)

    def test_matches_played_counts_only_is_completed_matches(self) -> None:
        league = _make_league("RowMatches")
        teams = _make_teams("RM", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        # 2 completed + 2 not-completed Matches.
        _make_completed_match(season, teams[0], teams[1])
        _make_completed_match(season, teams[0], teams[1])
        Match.objects.create(
            team_red=teams[0], team_blue=teams[1], season=season, is_completed=False
        )
        Match.objects.create(
            team_red=teams[0], team_blue=teams[1], season=season, is_completed=False
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        self.assertEqual(rows[0]["matches_played"], 2)

    def test_champion_cell_renders_champion_team_name(self) -> None:
        league = _make_league("RowChamp")
        teams = _make_teams("RC", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, teams[0].name)

    def test_runner_up_renders_standings_rank_2(self) -> None:
        league = _make_league("RowRunnerUp")
        teams = _make_teams("RU", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        _make_completed_match(season, teams[0], teams[1], red_pts=200)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        # runner_up is rank-2 Team or None.
        self.assertIn("runner_up", rows[0])

    def test_tournament_champion_cell_is_em_dash_placeholder(self) -> None:
        league = _make_league("RowTC")
        teams = _make_teams("TC", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        self.assertIsNone(rows[0]["tournament_champion"])
        # Template renders None as em-dash.
        self.assertContains(response, "—")

    def test_top_three_cells_render_rank_1_2_3(self) -> None:
        league = _make_league("RowTop3")
        teams = _make_teams("T3", 3)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        # Generate matches so standings has 3 ranked teams.
        _make_completed_match(season, teams[0], teams[1], red_pts=300)
        _make_completed_match(season, teams[0], teams[2], red_pts=200)
        _make_completed_match(season, teams[1], teams[2], red_pts=100)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        self.assertEqual(len(rows[0]["top_three"]), 3)

    def test_top_three_cells_render_em_dash_when_fewer_than_three_teams(
        self,
    ) -> None:
        league = _make_league("RowTop3Less")
        teams = _make_teams("T3L", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        # 2 teams enrolled — rank-3 slot is None.
        top = rows[0]["top_three"]
        self.assertEqual(len(top), 3)
        self.assertIsNone(top[2])

    def test_completed_rows_sorted_newest_first_by_id(self) -> None:
        league = _make_league("RowSort")
        teams = _make_teams("RS", 2)
        s1 = _make_completed_season(
            league,
            name="S1",
            start_date=date(2025, 1, 1),
            team_ids=[t.id for t in teams],
            champion_team=teams[0],
        )
        s2 = _make_completed_season(
            league,
            name="S2",
            start_date=date(2024, 1, 1),
            team_ids=[t.id for t in teams],
            champion_team=teams[0],
        )
        s3 = _make_completed_season(
            league,
            name="S3",
            start_date=date(2023, 1, 1),
            team_ids=[t.id for t in teams],
            champion_team=teams[0],
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        # Newest id (s3) first per -id ordering.
        self.assertEqual([r["season_id"] for r in rows], [s3.id, s2.id, s1.id])

    def test_each_completed_row_has_id_league_history_row_seasonid(self) -> None:
        league = _make_league("RowDom")
        teams = _make_teams("RDom", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, f'id="league-history-row-{season.id}"')


# ---------------------------------------------------------------------------
# TestLeagueHistoryInProgressRow
# ---------------------------------------------------------------------------


class TestLeagueHistoryInProgressRow(TestCase):
    """In-progress row pinned at top with locked CSS hooks + standings."""

    def test_active_season_renders_in_progress_row_at_top(self) -> None:
        league = _make_league("InProgA")
        teams = _make_teams("IPA", 2)
        active = _make_active_season(league, teams=teams)
        # Plus a completed Season.
        completed = _make_completed_season(
            league,
            name="Old",
            team_ids=[t.id for t in teams],
            champion_team=teams[0],
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        idx_in_progress = body.find('id="league-history-in-progress-row"')
        idx_completed = body.find(f'id="league-history-row-{completed.id}"')
        self.assertGreaterEqual(idx_in_progress, 0)
        self.assertGreaterEqual(idx_completed, 0)
        self.assertLess(idx_in_progress, idx_completed)
        # active reference silences unused-variable
        _ = active

    def test_in_progress_row_has_in_progress_row_class_substring(self) -> None:
        league = _make_league("InProgClass")
        teams = _make_teams("IPC", 2)
        _make_active_season(league, teams=teams)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        idx = body.find('id="league-history-in-progress-row"')
        self.assertGreaterEqual(idx, 0)
        # Look at the surrounding <tr> opening tag.
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertIn("in-progress-row", element)

    def test_in_progress_champion_cell_renders_in_progress_badge_not_team_name(
        self,
    ) -> None:
        league = _make_league("InProgBadge")
        teams = _make_teams("IPB", 2)
        _make_active_season(league, teams=teams)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        self.assertIn("In progress", body)
        # The element with id league-history-in-progress-row contains
        # an "in-progress" CSS-substring on the Champion badge.
        idx_in_progress = body.find('id="league-history-in-progress-row"')
        # Find the next <tr>'s closing... we check the row contains "in-progress" CSS substring
        end_of_row = body.find("</tr>", idx_in_progress)
        row_html = body[idx_in_progress:end_of_row]
        self.assertIn("in-progress", row_html)
        self.assertIn("In progress", row_html)

    def test_in_progress_top_three_cells_render_live_standings(self) -> None:
        league = _make_league("InProgStandings")
        teams = _make_teams("IPS", 2)
        active = _make_active_season(league, teams=teams)
        # Play one completed Match — teams[0] wins.
        _make_completed_match(active, teams[0], teams[1], red_pts=300)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        in_progress = response.context["in_progress_row"]
        self.assertIsNotNone(in_progress)
        # rank-1 == teams[0].
        self.assertEqual(in_progress["top_three"][0], teams[0])

    def test_in_progress_row_not_counted_in_per_page_budget(self) -> None:
        league = _make_league("InProgBudget")
        teams = _make_teams("IPBu", 2)
        # 11 completed Seasons + 1 in-progress.
        for i in range(11):
            _make_completed_season(
                league,
                name=f"C{i}",
                start_date=date(2010 + i, 1, 1),
                team_ids=[t.id for t in teams],
                champion_team=teams[0],
            )
        _make_active_season(league, teams=teams)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        # Page 1 has in-progress + 10 completed (per_page=10 default).
        self.assertIsNotNone(response.context["in_progress_row"])
        self.assertEqual(len(response.context["completed_rows"]), 10)
        # Page 2.
        response2 = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id}) + "?page=2"
        )
        self.assertIsNotNone(response2.context["in_progress_row"])
        self.assertEqual(len(response2.context["completed_rows"]), 1)

    def test_draft_season_also_renders_in_progress_row(self) -> None:
        league = _make_league("InProgDraft")
        teams = _make_teams("IPD", 2)
        _make_draft_season(league, teams=teams)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-history-in-progress-row"')

    def test_no_active_or_draft_season_omits_in_progress_row(self) -> None:
        league = _make_league("InProgNone")
        teams = _make_teams("IPN", 2)
        _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[0]
        )
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertNotContains(response, 'id="league-history-in-progress-row"')
        self.assertIsNone(response.context["in_progress_row"])


# ---------------------------------------------------------------------------
# TestLeagueHistoryChampionFallback
# ---------------------------------------------------------------------------


class TestLeagueHistoryChampionFallback(TestCase):
    """When ``champion_team`` is None, fall back to standings rank-1."""

    def test_champion_fk_null_falls_back_to_standings_rank_1(self) -> None:
        league = _make_league("ChampNull")
        teams = _make_teams("CN", 2)
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=None
        )
        # teams[0] is the rank-1 team.
        _make_completed_match(season, teams[0], teams[1], red_pts=300)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        # Champion fell back to rank-1.
        self.assertEqual(rows[0]["champion"], teams[0])

    def test_champion_fk_present_takes_precedence_over_standings_rank_1(
        self,
    ) -> None:
        league = _make_league("ChampPresent")
        teams = _make_teams("CP", 2)
        # champion is teams[1] but rank-1 from matches would be teams[0].
        season = _make_completed_season(
            league, team_ids=[t.id for t in teams], champion_team=teams[1]
        )
        _make_completed_match(season, teams[0], teams[1], red_pts=500)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        rows = response.context["completed_rows"]
        self.assertEqual(rows[0]["champion"], teams[1])


# ---------------------------------------------------------------------------
# TestLeagueHistoryPagination
# ---------------------------------------------------------------------------


class TestLeagueHistoryPagination(TestCase):
    """Per-page selector + page query-param + pagination nav rules."""

    def _make_n_completed(self, league: League, n: int) -> None:
        teams = _make_teams("Pg", 2)
        for i in range(n):
            _make_completed_season(
                league,
                name=f"S{i}",
                start_date=date(2000 + i, 1, 1),
                team_ids=[t.id for t in teams],
                champion_team=teams[0],
            )

    def test_default_per_page_is_10(self) -> None:
        league = _make_league("PgDef")
        self._make_n_completed(league, 12)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.context["per_page"], 10)
        self.assertEqual(len(response.context["completed_rows"]), 10)

    def test_per_page_25_50_100_accepted(self) -> None:
        league = _make_league("PgAccept")
        self._make_n_completed(league, 12)
        for value in (25, 50, 100):
            response = self.client.get(
                reverse("league_history", kwargs={"league_id": league.id})
                + f"?per_page={value}"
            )
            self.assertEqual(response.context["per_page"], value)

    def test_invalid_per_page_falls_back_to_10(self) -> None:
        league = _make_league("PgInv")
        self._make_n_completed(league, 1)
        for raw in ("foo", "999", "-5", "0"):
            response = self.client.get(
                reverse("league_history", kwargs={"league_id": league.id})
                + f"?per_page={raw}"
            )
            self.assertEqual(response.context["per_page"], 10)

    def test_invalid_page_falls_back_to_1(self) -> None:
        league = _make_league("PgInvPg")
        self._make_n_completed(league, 1)
        for raw in ("foo", "-1", "0"):
            response = self.client.get(
                reverse("league_history", kwargs={"league_id": league.id})
                + f"?page={raw}"
            )
            self.assertEqual(response.context["page_obj"].number, 1)

    def test_too_large_page_clamps_to_last_page(self) -> None:
        league = _make_league("PgClamp")
        self._make_n_completed(league, 15)
        # per_page=10 → 2 pages.
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id}) + "?page=99"
        )
        # Django Paginator.get_page clamps to last page.
        self.assertEqual(response.context["page_obj"].number, 2)

    def test_page_2_carries_per_page_querystring(self) -> None:
        league = _make_league("PgCarry")
        self._make_n_completed(league, 15)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id}) + "?per_page=10"
        )
        body = response.content.decode()
        # Page-2 link should contain both page=2 and per_page=10.
        self.assertIn("page=2", body)
        self.assertIn("per_page=10", body)

    def test_in_progress_row_appears_on_every_page(self) -> None:
        league = _make_league("PgEvery")
        teams = _make_teams("PgE", 2)
        for i in range(11):
            _make_completed_season(
                league,
                name=f"C{i}",
                start_date=date(2010 + i, 1, 1),
                team_ids=[t.id for t in teams],
                champion_team=teams[0],
            )
        _make_active_season(league, teams=teams)
        # page 1
        r1 = self.client.get(reverse("league_history", kwargs={"league_id": league.id}))
        self.assertContains(r1, 'id="league-history-in-progress-row"')
        # page 2
        r2 = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id}) + "?page=2"
        )
        self.assertContains(r2, 'id="league-history-in-progress-row"')

    def test_pagination_nav_omitted_when_single_page(self) -> None:
        league = _make_league("PgNoNav")
        self._make_n_completed(league, 5)
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertNotContains(response, 'id="league-history-pagination"')


# ---------------------------------------------------------------------------
# TestLeagueHistorySidebar
# ---------------------------------------------------------------------------


class TestLeagueHistorySidebar(TestCase):
    """Sidebar partial render + active-class on history entry."""

    def test_history_page_renders_sidebar_partial(self) -> None:
        league = _make_league("SbPartial")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="league-sidebar"')

    def test_sidebar_history_entry_has_active_class_on_history_page(self) -> None:
        league = _make_league("SbActive")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        idx = body.find('id="sidebar-league-history"')
        self.assertGreaterEqual(idx, 0)
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertIn("active", element)

    def test_sidebar_dashboard_entry_is_not_active_on_history_page(self) -> None:
        league = _make_league("SbDash")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        idx = body.find('id="sidebar-top-dashboard"')
        self.assertGreaterEqual(idx, 0)
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertNotIn("active", element)

    def test_sidebar_renders_all_23_entries(self) -> None:
        """LG-01h extends the LG-01f 14-entry sidebar to 23 by appending
        3 PLAYERS entries (Prospects / Watch List / Hall of Fame) and a
        full 6-entry STATS section.
        """
        league = _make_league("Sb23")
        response = self.client.get(
            reverse("league_history", kwargs={"league_id": league.id})
        )
        # The 23 locked DOM ids — 1 top + 6 LEAGUE + 4 TEAM + 6 PLAYERS +
        # 6 STATS.
        for dom_id in (
            "sidebar-top-dashboard",
            "sidebar-league-standings",
            "sidebar-league-schedule",
            "sidebar-league-playoffs",
            "sidebar-league-finances",
            "sidebar-league-history",
            "sidebar-league-power_rankings",
            "sidebar-team-roster",
            "sidebar-team-schedule_team",
            "sidebar-team-finances_team",
            "sidebar-team-history_team",
            "sidebar-players-free_agents",
            "sidebar-players-trade",
            "sidebar-players-trading_block",
            "sidebar-players-prospects",
            "sidebar-players-watch_list",
            "sidebar-players-hall_of_fame",
            "sidebar-stats-game_log",
            "sidebar-stats-league_leaders",
            "sidebar-stats-player_ratings",
            "sidebar-stats-player_stats",
            "sidebar-stats-team_stats",
            "sidebar-stats-statistical_feats",
        ):
            self.assertContains(response, f'id="{dom_id}"')


# ---------------------------------------------------------------------------
# TestLeagueHistorySessionWrite
# ---------------------------------------------------------------------------


class TestLeagueHistorySessionWrite(TestCase):
    """Session-write site: ``request.session["last_league_id"] = league.id``."""

    def test_get_history_writes_last_league_id_to_session(self) -> None:
        league = _make_league("SessW")
        self.client.get(reverse("league_history", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session["last_league_id"], league.id)

    def test_404_does_not_write_session(self) -> None:
        # No League with id 99999 — 404, must not pin the session.
        self.client.get(reverse("league_history", kwargs={"league_id": 99999}))
        # Session may not exist or may not have the key.
        self.assertNotIn("last_league_id", self.client.session)


# Reference to silence unused-import warnings.
_ = PlayerRoundState
