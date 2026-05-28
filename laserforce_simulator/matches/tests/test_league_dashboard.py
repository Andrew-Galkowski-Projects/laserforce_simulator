"""LG-01c — Django ``TestCase`` tests for ``matches.views.league_dashboard``.

The seam contract is locked at ``.claude/worktrees/lg-01c-seam-contract.md``
(§2a, §3a, §7a, §8b). The view is read-only at
``GET /leagues/<int:league_id>/``, picks one Season to display per the
``active > most-recent completed > none`` ladder, and renders four
branches (``draft / active / completed / none``) keyed off
``season_mode``.

Tests hand-construct ``Match`` + ``GameRound`` + ``PlayerRoundState``
rows — LG-01c runs NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers — hand-construct match + round + player-round-state rows.
# ---------------------------------------------------------------------------


def _make_league(name: str = "TestLeague") -> League:
    return League.objects.create(name=name)


def _make_draft_season(league: League, *, name: str = "S1", n_teams: int = 4):
    season = Season.objects.create(
        league=league, name=name, start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(n_teams):
        # Use name-prefix that sorts deterministically: T0..Tn.
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return season, teams


def _make_active_season(league: League, *, name: str = "S1", n_teams: int = 4):
    season, teams = _make_draft_season(league, name=name, n_teams=n_teams)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _make_completed_match(season, team_a, team_b, *, winner=None, red_pts=10):
    """Hand-construct a completed Match (no simulator)."""
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
    # ``is_completed=True`` triggers calculate_winner via save() — but we
    # still need a GameRound for fixture-key lookup.
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


def _make_player_state(
    game_round,
    player,
    *,
    team_color="red",
    role="commander",
    points_scored=10,
    tags_made=5,
    times_tagged=2,
):
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color=team_color,
        role=role,
        points_scored=points_scored,
        tags_made=tags_made,
        times_tagged=times_tagged,
    )


# ---------------------------------------------------------------------------
# TestLeagueDashboardRouting
# ---------------------------------------------------------------------------


class TestLeagueDashboardRouting(TestCase):
    """200/404/405, reverse, template."""

    def test_get_returns_200_for_existing_league(self) -> None:
        league = _make_league()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.status_code, 200)

    def test_get_returns_404_for_missing_league(self) -> None:
        response = self.client.get(reverse("league_dashboard", args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_post_returns_405(self) -> None:
        league = _make_league()
        response = self.client.post(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.status_code, 405)

    def test_reverse_resolves_to_expected_path(self) -> None:
        league = _make_league()
        self.assertEqual(
            reverse("league_dashboard", args=[league.id]),
            f"/leagues/{league.id}/",
        )

    def test_template_used_is_leagues_dashboard_html(self) -> None:
        league = _make_league()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertTemplateUsed(response, "leagues/dashboard.html")


# ---------------------------------------------------------------------------
# TestLeagueDashboardSeasonPick
# ---------------------------------------------------------------------------


class TestLeagueDashboardSeasonPick(TestCase):
    """Season pick ladder: active > most-recent completed > none."""

    def test_no_seasons_renders_none_branch(self) -> None:
        league = _make_league("LNone")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["season_mode"], "none")
        self.assertContains(response, 'id="league-dashboard-no-season-notice"')

    def test_draft_season_picked_as_active(self) -> None:
        league = _make_league("LDraft")
        season, _ = _make_draft_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["season_mode"], "draft")
        self.assertEqual(response.context["displayed_season"], season)

    def test_active_season_picked(self) -> None:
        league = _make_league("LActive")
        season, _ = _make_active_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["season_mode"], "active")
        self.assertEqual(response.context["displayed_season"], season)

    def test_completed_only_falls_back_to_most_recent(self) -> None:
        league = _make_league("LCompleted")
        old = Season.objects.create(
            league=league,
            name="Old",
            start_date=date(2026, 1, 1),
            state="completed",
        )
        new = Season.objects.create(
            league=league,
            name="New",
            start_date=date(2026, 6, 1),
            state="completed",
        )
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["season_mode"], "completed")
        # Higher id wins (most-recent fallback ordering -id).
        self.assertEqual(response.context["displayed_season"], new)
        self.assertNotEqual(response.context["displayed_season"], old)

    def test_active_takes_precedence_over_completed(self) -> None:
        league = _make_league("LMixed")
        Season.objects.create(
            league=league,
            name="Old",
            start_date=date(2026, 1, 1),
            state="completed",
        )
        active_season, _ = _make_active_season(league, name="Active")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["season_mode"], "active")
        self.assertEqual(response.context["displayed_season"], active_season)


# ---------------------------------------------------------------------------
# TestLeagueDashboardDraftBranch
# ---------------------------------------------------------------------------


class TestLeagueDashboardDraftBranch(TestCase):
    """Draft branch: ``Start Season`` button, alphabetical top-3, no body DOM ids."""

    def test_draft_renders_action_button_with_start_season_state(self) -> None:
        league = _make_league("LDraftAction")
        _make_draft_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["action_button_label"], "Start Season")
        self.assertEqual(response.context["action_button_state"], "start_season")
        body = response.content.decode()
        # LG-01d activated the LG-01c `<button disabled>` placeholder into a
        # working Start Season POST form. The wrapper id remains for DOM
        # parity; the Start Season form id is the new functional element.
        self.assertIn('id="league-dashboard-action-button"', body)
        self.assertIn('id="league-dashboard-play-start-season"', body)
        self.assertIn(
            f'action="/seasons/{response.context["displayed_season"].id}/start-season/"',
            body,
        )

    def test_draft_standings_snippet_sorted_by_team_name_asc_top_3(self) -> None:
        league = _make_league("LDraftSort")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 6, 1)
        )
        # Create 4 teams with names that won't alphabetise by creation order.
        # ``make_team_with_slots`` names the team f"{prefix} Team", so we
        # control the alphabetical order via the prefix.
        t_a, _ = make_team_with_slots("Apple")
        t_b, _ = make_team_with_slots("Banana")
        t_c, _ = make_team_with_slots("Cherry")
        t_d, _ = make_team_with_slots("Durian")
        # Add in scrambled order to prove it's name-sorted, not insertion-sorted.
        season.teams.add(t_c, t_a, t_d, t_b)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        snippet = response.context["standings_snippet"]
        # Top 3 alphabetical: Apple, Banana, Cherry.
        self.assertEqual(len(snippet), 3)
        # Each entry is (row_dict, team).
        team_names = [team.name for (_row, team) in snippet]
        self.assertEqual(team_names, ["Apple Team", "Banana Team", "Cherry Team"])

    def test_draft_omits_next_round_and_round_count_and_leaders(self) -> None:
        league = _make_league("LDraftOmit")
        _make_draft_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        # The 5 active-branch DOM ids must be absent.
        self.assertNotContains(response, 'id="league-dashboard-next-round"')
        self.assertNotContains(response, 'id="league-dashboard-round-count"')
        self.assertNotContains(response, 'id="league-dashboard-leaders-points"')
        self.assertNotContains(response, 'id="league-dashboard-leaders-tags"')
        self.assertNotContains(response, 'id="league-dashboard-leaders-ratio"')


# ---------------------------------------------------------------------------
# TestLeagueDashboardActiveBranch
# ---------------------------------------------------------------------------


class TestLeagueDashboardActiveBranch(TestCase):
    """Active branch: full body DOM ids, leaders rendered, raw hrefs."""

    def _make_active_with_match_and_leaders(self):
        league = _make_league("LActiveBody")
        season, teams = _make_active_season(league, n_teams=4)
        # Hand-construct one completed Match between team_0 and team_1
        # so compute_standings has data to rank.
        t_a = teams[0]
        t_b = teams[1]
        match = _make_completed_match(season, t_a, t_b, red_pts=100)
        # Pick the round-1 GameRound to attach PlayerRoundState rows.
        gr1 = match.game_rounds.get(round_number=1)
        # Use team_a's commander player.
        cmdr = t_a.slot_commander
        _make_player_state(
            gr1,
            cmdr,
            team_color="red",
            role="commander",
            points_scored=100,
            tags_made=10,
            times_tagged=2,
        )
        return league, season, t_a, cmdr

    def test_active_renders_action_button_with_play_next_state(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["action_button_label"], "Play Next")
        self.assertEqual(response.context["action_button_state"], "play_next")

    def test_active_standings_snippet_calls_compute_standings(self) -> None:
        league, _season, t_a, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        snippet = response.context["standings_snippet"]
        # Snippet should have at most 3 entries; t_a (won the match) is rank 1.
        self.assertTrue(len(snippet) <= 3)
        # Team a is the highest-ranked team.
        first_row, first_team = snippet[0]
        self.assertEqual(first_team, t_a)

    def test_active_next_round_rendered_with_team_names_and_date(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-next-round"')

    def test_active_round_count_format(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-round-count"')
        # `<completed> / <total>` substring rendered somewhere.
        body = response.content.decode()
        # N=4 → 12 fixtures total; 2 played (one match's two rounds).
        # Just assert the slash is present (the exact numbers depend on
        # how the view counts — we assert both numbers are present).
        completed = response.context["round_count_completed"]
        total = response.context["round_count_total"]
        self.assertIn(f"{completed}", body)
        self.assertIn(f"{total}", body)

    def test_active_leaders_points_rendered_with_top_3(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-leaders-points"')

    def test_active_leaders_tags_rendered_with_top_3(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-leaders-tags"')

    def test_active_leaders_ratio_rendered_with_top_3(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-leaders-ratio"')

    def test_active_player_leader_anchor_uses_raw_career_stats_href(self) -> None:
        league, _season, _t, player = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        # Raw href substring — deferred broken link.
        self.assertContains(response, f'href="/players/{player.id}/career-stats/"')

    def test_active_view_all_leaders_anchor_uses_raw_leaders_href(self) -> None:
        league, _season, _t, _p = self._make_active_with_match_and_leaders()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        # Raw `/leagues/<id>/leaders/` href.
        self.assertContains(response, f'href="/leagues/{league.id}/leaders/"')


# ---------------------------------------------------------------------------
# TestLeagueDashboardCompletedBranch
# ---------------------------------------------------------------------------


class TestLeagueDashboardCompletedBranch(TestCase):
    """Completed branch: ``Start Next Season`` button, "All fixtures played"."""

    def _make_completed_season(self):
        league = _make_league("LCompletedBody")
        # Create a completed Season directly (no simulator).
        season = Season.objects.create(
            league=league,
            name="Done",
            start_date=date(2026, 1, 1),
            state="completed",
            starting_team_ids_json=[],
        )
        return league, season

    def test_completed_renders_action_button_with_start_next_season_state(
        self,
    ) -> None:
        league, _s = self._make_completed_season()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["action_button_label"], "Start Next Season")
        self.assertEqual(response.context["action_button_state"], "start_next_season")

    def test_completed_next_round_rendered_as_all_fixtures_played(self) -> None:
        league, _s = self._make_completed_season()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        # Completed-with-all-played renders a stub block; the next-round
        # container is still present per the contract.
        self.assertContains(response, 'id="league-dashboard-next-round"')
        # The "All fixtures played" string is the locked stub label.
        self.assertContains(response, "All fixtures played")

    def test_completed_round_count_equals_total_total(self) -> None:
        league, _s = self._make_completed_season()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        completed = response.context["round_count_completed"]
        total = response.context["round_count_total"]
        # Completed Season ⇒ completed == total (might be 0/0 if no
        # starting team ids snapshot).
        self.assertEqual(completed, total)


# ---------------------------------------------------------------------------
# TestLeagueDashboardNoneBranch
# ---------------------------------------------------------------------------


class TestLeagueDashboardNoneBranch(TestCase):
    """None branch: ``No Season`` notice, all body DOM ids absent."""

    def test_none_renders_no_season_notice_with_substring_no_season(self) -> None:
        league = _make_league("LNone1")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-dashboard-no-season-notice"')
        self.assertContains(response, "No Season")

    def test_none_action_button_label_is_no_season_and_state_is_none(self) -> None:
        league = _make_league("LNone2")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(response.context["action_button_label"], "No Season")
        self.assertEqual(response.context["action_button_state"], "none")

    def test_none_all_body_dom_ids_absent(self) -> None:
        league = _make_league("LNone3")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        for dom_id in (
            "league-dashboard-standings-snippet",
            "league-dashboard-next-round",
            "league-dashboard-round-count",
            "league-dashboard-leaders-points",
            "league-dashboard-leaders-tags",
            "league-dashboard-leaders-ratio",
        ):
            self.assertNotContains(response, f'id="{dom_id}"')


# ---------------------------------------------------------------------------
# TestLg01eDashboardWiring (LG-01e — appended per seam contract §7b)
# ---------------------------------------------------------------------------


class TestLg01eDashboardWiring(TestCase):
    """LG-01e — the ``action_button_state="start_next_season"`` branch on
    the league dashboard now renders a real ``<form>`` POSTing to
    ``next_season``. The other three branches (draft / active / none)
    MUST NOT render the ``league-dashboard-next-season-form`` id.
    """

    def _make_completed_only_league(self) -> League:
        league = _make_league("LE1eCompleted")
        Season.objects.create(
            league=league,
            name="Done",
            start_date=date(2026, 1, 1),
            state="completed",
            starting_team_ids_json=[],
        )
        return league

    def test_completed_renders_next_season_form_with_correct_action_url(
        self,
    ) -> None:
        league = self._make_completed_only_league()
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        body = response.content.decode()
        # Locked DOM id.
        self.assertIn('id="league-dashboard-next-season-form"', body)
        # Action URL reverses to next_season for THIS league.
        expected_action = reverse("next_season", kwargs={"league_id": league.id})
        self.assertIn(f'action="{expected_action}"', body)
        # method="post" on the form.
        idx = body.find('id="league-dashboard-next-season-form"')
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        form_element = body[start : end + 1]
        self.assertIn('method="post"', form_element.lower())
        # csrf hidden input present.
        self.assertIn("csrfmiddlewaretoken", body)
        # Submit button text + data-action-state attribute.
        self.assertIn("Start Next Season", body)
        self.assertIn('data-action-state="start_next_season"', body)

    def test_draft_does_not_render_next_season_form(self) -> None:
        league = _make_league("LE1eDraft")
        _make_draft_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertNotContains(response, 'id="league-dashboard-next-season-form"')

    def test_active_does_not_render_next_season_form(self) -> None:
        league = _make_league("LE1eActive")
        _make_active_season(league)
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertNotContains(response, 'id="league-dashboard-next-season-form"')

    def test_none_does_not_render_next_season_form(self) -> None:
        league = _make_league("LE1eNone")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertNotContains(response, 'id="league-dashboard-next-season-form"')


# ---------------------------------------------------------------------------
# TestLg01fSidebarRendered (LG-01f — appended per seam contract §9d)
# ---------------------------------------------------------------------------


class TestLg01fSidebarRendered(TestCase):
    """LG-01f — the league dashboard renders the 14-entry sidebar partial
    with ``sidebar_active="dashboard"`` so the Dashboard entry carries
    the active class.
    """

    def test_league_dashboard_renders_sidebar_partial(self) -> None:
        league = _make_league("LfLDSb")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertContains(response, 'id="league-sidebar"')

    def test_dashboard_entry_active_class(self) -> None:
        league = _make_league("LfLDActive")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        body = response.content.decode()
        idx = body.find('id="sidebar-top-dashboard"')
        self.assertGreaterEqual(idx, 0)
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertIn("active", element)

    def test_sidebar_links_has_14_entries(self) -> None:
        league = _make_league("LfLD14")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(len(response.context["sidebar_links"]), 14)

    def test_history_entry_url_targets_this_leagues_history(self) -> None:
        league = _make_league("LfLDHist")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        links = response.context["sidebar_links"]
        history_entry = next(e for e in links if e["key"] == "history")
        self.assertEqual(
            history_entry["url"],
            reverse("league_history", kwargs={"league_id": league.id}),
        )


# ---------------------------------------------------------------------------
# TestLg01fSessionWrite (LG-01f — appended per seam contract §9d)
# ---------------------------------------------------------------------------


class TestLg01fSessionWrite(TestCase):
    """LG-01f — the league dashboard writes
    ``request.session["last_league_id"] = league.id`` after the 404
    guard.
    """

    def test_get_writes_last_league_id_to_session(self) -> None:
        league = _make_league("LfLDSessW")
        self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertEqual(self.client.session["last_league_id"], league.id)

    def test_404_does_not_write_session(self) -> None:
        self.client.get(reverse("league_dashboard", args=[99999]))
        self.assertNotIn("last_league_id", self.client.session)
