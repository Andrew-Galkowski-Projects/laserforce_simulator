"""LG-01c — Django ``TestCase`` tests for ``matches.views.season_dashboard``.

The seam contract is locked at ``.claude/worktrees/lg-01c-seam-contract.md``
(§2b, §3b, §7b, §8c). The view is read-only at
``GET /seasons/<int:season_id>/``, renders the shared dashboard body plus
a 5-entry sidebar (overview / standings / schedule / teams / history) with
the standings + schedule entries linked and teams + history disabled
``<span>``s.

Tests hand-construct ``Match`` + ``GameRound`` + ``PlayerRoundState``
rows — LG-01c runs NO simulation, so the simulator is never entered.
"""

from __future__ import annotations

import re
from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, PlayerRoundState, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers — hand-construct match + round + player-round-state rows.
# ---------------------------------------------------------------------------


def _make_league_and_draft_season(name: str = "LG"):
    league = League.objects.create(name=name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 6, 1)
    )
    teams = []
    for i in range(4):
        t, _ = make_team_with_slots(f"{name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return league, season, teams


def _make_active_season(name: str = "LGActive"):
    league, season, teams = _make_league_and_draft_season(name)
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _make_completed_season_with_match(name: str = "LGCompleted"):
    """Manufacture a completed season with one persisted Match."""
    league = League.objects.create(name=name)
    season = Season.objects.create(
        league=league,
        name="Done",
        start_date=date(2026, 1, 1),
        state="completed",
        starting_team_ids_json=[],
    )
    return league, season


def _make_completed_match(season, team_a, team_b, *, red_pts=100):
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


def _make_player_state(
    game_round,
    player,
    *,
    team_color="red",
    role="commander",
    points_scored=100,
    tags_made=10,
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
# TestSeasonDashboardRouting
# ---------------------------------------------------------------------------


class TestSeasonDashboardRouting(TestCase):
    """200/404/405, reverse, template."""

    def test_get_returns_200_for_existing_season(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Route1")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(response.status_code, 200)

    def test_get_returns_404_for_missing_season(self) -> None:
        response = self.client.get(reverse("season_dashboard", args=[99999]))
        self.assertEqual(response.status_code, 404)

    def test_post_returns_405(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Route3")
        response = self.client.post(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(response.status_code, 405)

    def test_reverse_resolves_to_expected_path(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Route4")
        self.assertEqual(
            reverse("season_dashboard", args=[season.id]),
            f"/seasons/{season.id}/",
        )

    def test_template_used_is_seasons_dashboard_html(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Route5")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertTemplateUsed(response, "seasons/dashboard.html")


# ---------------------------------------------------------------------------
# TestSeasonDashboardStateMatrix
# ---------------------------------------------------------------------------


class TestSeasonDashboardStateMatrix(TestCase):
    """Per-state DOM-id matrix + action button label/state."""

    def test_draft_renders_all_locked_dom_ids(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Matrix1")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        # Always-present DOM ids.
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
            "season-dashboard-sidebar",
            "season-dashboard-sidebar-standings",
            "season-dashboard-sidebar-schedule",
            "season-dashboard-sidebar-teams",
            "season-dashboard-sidebar-history",
            "season-dashboard-standings-snippet",
        ):
            self.assertContains(response, f'id="{dom_id}"')
        # Active-only DOM ids ABSENT in draft.
        for dom_id in (
            "season-dashboard-next-round",
            "season-dashboard-round-count",
            "season-dashboard-leaders-points",
            "season-dashboard-leaders-tags",
            "season-dashboard-leaders-ratio",
        ):
            self.assertNotContains(response, f'id="{dom_id}"')

    def test_active_renders_all_locked_dom_ids(self) -> None:
        _league, season, _teams = _make_active_season("Matrix2")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
            "season-dashboard-sidebar",
            "season-dashboard-sidebar-standings",
            "season-dashboard-sidebar-schedule",
            "season-dashboard-sidebar-teams",
            "season-dashboard-sidebar-history",
            "season-dashboard-standings-snippet",
            "season-dashboard-next-round",
            "season-dashboard-round-count",
            "season-dashboard-leaders-points",
            "season-dashboard-leaders-tags",
            "season-dashboard-leaders-ratio",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_completed_renders_all_locked_dom_ids(self) -> None:
        _league, season = _make_completed_season_with_match("Matrix3")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
            "season-dashboard-sidebar",
            "season-dashboard-sidebar-standings",
            "season-dashboard-sidebar-schedule",
            "season-dashboard-sidebar-teams",
            "season-dashboard-sidebar-history",
            "season-dashboard-standings-snippet",
            "season-dashboard-next-round",
            "season-dashboard-round-count",
            "season-dashboard-leaders-points",
            "season-dashboard-leaders-tags",
            "season-dashboard-leaders-ratio",
        ):
            self.assertContains(response, f'id="{dom_id}"')

    def test_action_button_label_per_state(self) -> None:
        # Draft → "Start Season"
        _l1, s1, _t = _make_league_and_draft_season("Action1")
        r1 = self.client.get(reverse("season_dashboard", args=[s1.id]))
        self.assertEqual(r1.context["action_button_label"], "Start Season")
        # Active → "Play Next"
        _l2, s2, _t = _make_active_season("Action2")
        r2 = self.client.get(reverse("season_dashboard", args=[s2.id]))
        self.assertEqual(r2.context["action_button_label"], "Play Next")
        # Completed → "Start Next Season"
        _l3, s3 = _make_completed_season_with_match("Action3")
        r3 = self.client.get(reverse("season_dashboard", args=[s3.id]))
        self.assertEqual(r3.context["action_button_label"], "Start Next Season")

    def test_action_button_state_data_attribute_per_state(self) -> None:
        _l1, s1, _t = _make_league_and_draft_season("DataA1")
        r1 = self.client.get(reverse("season_dashboard", args=[s1.id]))
        self.assertEqual(r1.context["action_button_state"], "start_season")
        self.assertContains(r1, 'data-action-state="start_season"')

        _l2, s2, _t = _make_active_season("DataA2")
        r2 = self.client.get(reverse("season_dashboard", args=[s2.id]))
        self.assertEqual(r2.context["action_button_state"], "play_next")
        self.assertContains(r2, 'data-action-state="play_next"')

        _l3, s3 = _make_completed_season_with_match("DataA3")
        r3 = self.client.get(reverse("season_dashboard", args=[s3.id]))
        self.assertEqual(r3.context["action_button_state"], "start_next_season")
        self.assertContains(r3, 'data-action-state="start_next_season"')


# ---------------------------------------------------------------------------
# TestSeasonDashboardSidebar
# ---------------------------------------------------------------------------


class TestSeasonDashboardSidebar(TestCase):
    """5-entry sidebar shape + per-entry link/span rule."""

    def test_sidebar_links_has_five_entries_in_pinned_order(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar1")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        links = response.context["sidebar_links"]
        self.assertEqual(len(links), 5)
        keys = [entry["key"] for entry in links]
        self.assertEqual(
            keys, ["overview", "standings", "schedule", "teams", "history"]
        )

    def test_sidebar_active_is_overview(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar2")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(response.context["sidebar_active"], "overview")

    def test_sidebar_standings_link_reverses_to_season_standings_url(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar3")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        expected = reverse("season_standings", args=[season.id])
        self.assertContains(response, f'href="{expected}"')

    def test_sidebar_schedule_link_reverses_to_season_schedule_url(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar4")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        expected = reverse("season_schedule", args=[season.id])
        self.assertContains(response, f'href="{expected}"')

    def test_sidebar_teams_renders_as_disabled_span_no_href(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar5")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        body = response.content.decode()
        # Locate the teams sidebar entry and confirm it is rendered as a
        # <span>, NOT an <a href>.
        m = re.search(r'<([a-z]+)\b[^>]*id="season-dashboard-sidebar-teams"', body)
        self.assertIsNotNone(m, "season-dashboard-sidebar-teams id not found")
        self.assertEqual(
            m.group(1),
            "span",
            f"sidebar-teams must be a <span>, found <{m.group(1)}>",
        )
        # Defensive — make sure no `href=` shows up on this element's tag.
        idx = body.find('id="season-dashboard-sidebar-teams"')
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertNotIn("href=", element)

    def test_sidebar_history_renders_as_disabled_span_no_href(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("Sidebar6")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        body = response.content.decode()
        m = re.search(r'<([a-z]+)\b[^>]*id="season-dashboard-sidebar-history"', body)
        self.assertIsNotNone(m, "season-dashboard-sidebar-history id not found")
        self.assertEqual(
            m.group(1),
            "span",
            f"sidebar-history must be a <span>, found <{m.group(1)}>",
        )
        idx = body.find('id="season-dashboard-sidebar-history"')
        start = body.rfind("<", 0, idx)
        end = body.find(">", idx)
        element = body[start : end + 1]
        self.assertNotIn("href=", element)


# ---------------------------------------------------------------------------
# TestSeasonDashboardBody
# ---------------------------------------------------------------------------


class TestSeasonDashboardBody(TestCase):
    """Body integration: leaders use compute_leaders, next-fixture omitted
    on all-played completed, raw-href patterns.
    """

    def test_leaders_use_compute_leaders_pure_module(self) -> None:
        """Integration: an active Season with one persisted Match + one
        PlayerRoundState row produces a top LeaderRow whose name +
        ``points_per_game`` value appear in the rendered HTML.
        """
        _league, season, teams = _make_active_season("BodyLeaders")
        t_a = teams[0]
        t_b = teams[1]
        match = _make_completed_match(season, t_a, t_b, red_pts=100)
        gr1 = match.game_rounds.get(round_number=1)
        cmdr = t_a.slot_commander
        # Known PlayerRoundState: 100 points, 10 tags, 2 tagged.
        # games_played=1 → value=100/1=100.00.
        _make_player_state(
            gr1,
            cmdr,
            role="commander",
            team_color="red",
            points_scored=100,
            tags_made=10,
            times_tagged=2,
        )
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        body = response.content.decode()
        # Player name rendered in the leaders-points block.
        self.assertIn(cmdr.name, body)
        # Value rendered with floatformat:2.
        self.assertContains(response, "100.00")

    def test_next_fixture_omitted_when_completed_and_all_played(self) -> None:
        _league, season = _make_completed_season_with_match("BodyAllPlayed")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        # Container is present (per the season dashboard's always/active+completed
        # rule), and `next_fixture` context is None → renders the
        # "All fixtures played" stub label.
        self.assertIsNone(response.context["next_fixture"])
        self.assertContains(response, "All fixtures played")

    def test_player_leader_anchor_uses_raw_career_stats_href(self) -> None:
        _league, season, teams = _make_active_season("BodyHref1")
        t_a = teams[0]
        t_b = teams[1]
        match = _make_completed_match(season, t_a, t_b, red_pts=50)
        gr1 = match.game_rounds.get(round_number=1)
        cmdr = t_a.slot_commander
        _make_player_state(
            gr1,
            cmdr,
            role="commander",
            team_color="red",
            points_scored=50,
            tags_made=5,
            times_tagged=1,
        )
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertContains(response, f'href="/players/{cmdr.id}/career-stats/"')

    def test_view_all_leaders_anchor_uses_raw_per_season_leaders_href(
        self,
    ) -> None:
        _league, season, teams = _make_active_season("BodyHref2")
        t_a = teams[0]
        t_b = teams[1]
        match = _make_completed_match(season, t_a, t_b, red_pts=50)
        gr1 = match.game_rounds.get(round_number=1)
        cmdr = t_a.slot_commander
        _make_player_state(
            gr1,
            cmdr,
            role="commander",
            team_color="red",
            points_scored=50,
            tags_made=5,
            times_tagged=1,
        )
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        # Raw /seasons/<id>/leaders/ placeholder anchor.
        self.assertContains(response, f'href="/seasons/{season.id}/leaders/"')
