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
        # Always-present DOM ids. LG-01f superseded the LG-01c
        # ``season-dashboard-sidebar*`` ids (the 5-entry season-scoped
        # sidebar is replaced wholesale by the 14-entry league-scoped
        # sidebar partial — see ADR-0017); ``TestLg01fSidebarRendered``
        # below covers the new ``league-sidebar`` / ``sidebar-{section}-{key}``
        # ids and asserts the obsolete LG-01c ids are absent.
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
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
        # LG-01f removed the LG-01c ``season-dashboard-sidebar*`` ids
        # (ADR-0017); the new ``league-sidebar`` ids are asserted by
        # ``TestLg01fSidebarRendered``.
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
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
        # LG-01f removed the LG-01c ``season-dashboard-sidebar*`` ids
        # (ADR-0017); the new ``league-sidebar`` ids are asserted by
        # ``TestLg01fSidebarRendered``.
        for dom_id in (
            "season-dashboard-header",
            "season-dashboard-state-badge",
            "season-dashboard-action-button",
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
# (LG-01f — deleted TestSeasonDashboardSidebar; the LG-01c 5-entry sidebar
# assertions are obsolete under the 14-entry shape. New LG-01f sidebar
# assertions live in the appended TestLg01fSidebarRendered class below.)
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# TestLg01eDashboardWiring (LG-01e — appended per seam contract §7c)
# ---------------------------------------------------------------------------


class TestLg01eDashboardWiring(TestCase):
    """LG-01e + CAR-02 BLAST RADIUS — the ``action_button_state="start_next_season"``
    branch on the season dashboard.

    CAR-02 (§4.3) REROUTES the LG-01e POST ``<form
    id="season-dashboard-next-season-form">`` into a GET ``<a
    id="season-dashboard-owner-evaluation-link">`` to the eval screen — the
    ``data-action-state="start_next_season"`` attribute SURVIVES on the link, but
    the ``-next-season-form`` POST form id is GONE. These LG-01e assertions are
    updated to the rerouted link shape per the documented blast radius — the
    coverage is kept, not deleted.
    """

    def test_completed_renders_owner_evaluation_link_with_correct_href(
        self,
    ) -> None:
        _league, season = _make_completed_season_with_match("LE1eCompleted")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        body = response.content.decode()
        # The rerouted GET eval link replaces the old POST form id.
        self.assertIn('id="season-dashboard-owner-evaluation-link"', body)
        self.assertNotIn('id="season-dashboard-next-season-form"', body)
        # href reverses to the eval screen for THIS Season (displayed_season).
        expected_href = reverse("owner_evaluation", kwargs={"season_id": season.id})
        self.assertIn(f'href="{expected_href}"', body)
        # csrf is no longer required (the control is a GET link, not a POST form).
        # The data-action-state attribute SURVIVES on the link (LG-01c/e scanners).
        self.assertIn('data-action-state="start_next_season"', body)

    def test_completed_renders_past_evaluations_link(self) -> None:
        _league, season = _make_completed_season_with_match("LE1ePastEval")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertContains(response, 'id="season-dashboard-past-evaluations-link"')

    def test_draft_does_not_render_owner_evaluation_link(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("LE1eDraft")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertNotContains(response, 'id="season-dashboard-owner-evaluation-link"')
        self.assertNotContains(response, 'id="season-dashboard-next-season-form"')

    def test_active_does_not_render_owner_evaluation_link(self) -> None:
        _league, season, _teams = _make_active_season("LE1eActive")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertNotContains(response, 'id="season-dashboard-owner-evaluation-link"')


# ---------------------------------------------------------------------------
# TestLg01fSidebarRendered (LG-01f — appended per seam contract §9e)
# ---------------------------------------------------------------------------


class TestLg01fSidebarRendered(TestCase):
    """LG-01f — the season dashboard now renders the 14-entry sidebar
    partial with ``sidebar_active=None`` (no entry matches the season
    dashboard). LG-01c-locked DOM ids (``season-dashboard-sidebar*``) ARE
    REMOVED.
    """

    def test_season_dashboard_renders_sidebar_partial(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("LfSbPart")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertContains(response, 'id="league-sidebar"')

    def test_sidebar_active_is_none_no_entry_active(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("LfSbActive")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertIsNone(response.context["sidebar_active"])
        for entry in response.context["sidebar_links"]:
            self.assertFalse(entry["active"])

    def test_sidebar_links_has_23_entries(self) -> None:
        """LG-01h extends the LG-01f 14-entry sidebar to 23 by appending
        3 PLAYERS entries (Prospects / Watch List / Hall of Fame) and a
        full 6-entry STATS section.
        """
        _league, season, _teams = _make_league_and_draft_season("LfSb23")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(len(response.context["sidebar_links"]), 23)

    def test_lg01c_sidebar_dom_ids_are_absent(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("LfSbOld")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        for obsolete in (
            "season-dashboard-sidebar",
            "season-dashboard-sidebar-standings",
            "season-dashboard-sidebar-schedule",
            "season-dashboard-sidebar-teams",
            "season-dashboard-sidebar-history",
        ):
            self.assertNotContains(response, f'id="{obsolete}"')

    def test_lg01c_sidebar_id_prefix_substring_is_absent(self) -> None:
        """Sentinel: prevent the LG-01c-old IDs from leaking back even
        if a future template uses a different attribute style (e.g.
        ``class="season-dashboard-sidebar"`` or a comment containing the
        literal). LG-01f superseded the entire ``season-dashboard-sidebar``
        DOM-id namespace per ADR-0017 — no rendered HTML on the season
        dashboard should contain that prefix substring at all.
        """
        _league, season, _teams = _make_league_and_draft_season("LfSbSent")
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertNotIn(
            b"season-dashboard-sidebar",
            response.content,
            msg=(
                "LG-01c sidebar markup leaked back into the rendered HTML; "
                "the entire `season-dashboard-sidebar` namespace was "
                "superseded by LG-01f (ADR-0017)."
            ),
        )


# ---------------------------------------------------------------------------
# TestLg01fSessionWrite (LG-01f — appended per seam contract §9e)
# ---------------------------------------------------------------------------


class TestLg01fSessionWrite(TestCase):
    """LG-01f — the season dashboard writes
    ``request.session["last_league_id"] = season.league_id`` after the
    404 guard.
    """

    def test_get_writes_last_league_id_to_session(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("LfSessW")
        self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(self.client.session["last_league_id"], season.league_id)

    def test_404_does_not_write_session(self) -> None:
        self.client.get(reverse("season_dashboard", args=[99999]))
        self.assertNotIn("last_league_id", self.client.session)


# ---------------------------------------------------------------------------
# TestLg01jSeasonDashboardMapConfig (LG-01j — appended per seam contract
# Section 11 Dashboard read-only display + Section 12.2 templates/seasons/
# dashboard.html DOM id ``season-dashboard-map-config``)
# ---------------------------------------------------------------------------


import io as _lg01j_io  # noqa: E402

from django.core.files.uploadedfile import (  # noqa: E402
    SimpleUploadedFile as _Lg01jSimpleUploadedFile,
)

from core.models import ArenaMap as _Lg01jArenaMap  # noqa: E402


def _lg01j_png() -> bytes:
    from PIL import Image as _PILImage

    buf = _lg01j_io.BytesIO()
    _PILImage.new("RGB", (10, 10), color=(50, 100, 150)).save(buf, format="PNG")
    return buf.getvalue()


def _lg01j_arena_map(name: str) -> _Lg01jArenaMap:
    return _Lg01jArenaMap.objects.create(
        name=name,
        image=_Lg01jSimpleUploadedFile(
            f"{name}.png", _lg01j_png(), content_type="image/png"
        ),
        img_width=10,
        img_height=10,
    )


class TestLg01jSeasonDashboardMapConfig(TestCase):
    """LG-01j — ``templates/seasons/dashboard.html`` renders
    ``map_config_label`` inside ``<div id="season-dashboard-map-config">``.

    Tests the 4 label cases plus 2 defensive cases. Label strings are
    byte-equal — locked at seam contract §11 + §13.

    The season dashboard always has a Season (URL-resolved), so
    ``displayed_season is None`` does NOT apply here — that case is the
    league-dashboard branch only. The 3-zone fallback label arises here
    only via ``Season.map_mode == "none"``.
    """

    _DOM_ID = "season-dashboard-map-config"

    def _get_body(self, season: Season) -> str:
        response = self.client.get(reverse("season_dashboard", args=[season.id]))
        self.assertEqual(response.status_code, 200)
        return response.content.decode()

    def test_dom_id_present_in_rendered_template(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjDOM")
        body = self._get_body(season)
        self.assertIn(f'id="{self._DOM_ID}"', body)

    def test_map_mode_none_renders_3_zone_fallback_label(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjModeNone")
        # Default ``map_mode`` is "none" — explicit pin.
        season.map_mode = "none"
        season.save()
        body = self._get_body(season)
        self.assertIn("Map: 3-zone fallback (no map)", body)

    def test_map_mode_single_renders_em_dash_with_map_name(self) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjModeSingle")
        the_map = _lg01j_arena_map("Alpha")
        season.map_pool.add(the_map)
        season.start_season()
        season.map_mode = "single"
        season.save()
        body = self._get_body(season)
        self.assertIn("Map: Single — Alpha", body)

    def test_map_mode_single_with_deleted_map_renders_map_deleted_label(
        self,
    ) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjModeSingleDel")
        season.start_season()
        season.map_mode = "single"
        season.starting_map_pool_ids_json = [999_999]
        season.save()
        body = self._get_body(season)
        self.assertIn("Map: Single — (map deleted)", body)

    def test_map_mode_random_per_round_renders_count_and_names_alphabetical(
        self,
    ) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjModeRand")
        m_charlie = _lg01j_arena_map("Charlie")
        m_alpha = _lg01j_arena_map("Alpha")
        m_bravo = _lg01j_arena_map("Bravo")
        season.map_pool.add(m_charlie, m_alpha, m_bravo)
        season.start_season()
        season.map_mode = "random_per_round"
        season.save()
        body = self._get_body(season)
        self.assertIn(
            "Map: Random per Round (3 maps: Alpha, Bravo, Charlie)",
            body,
        )

    def test_map_mode_random_per_round_with_empty_pool_renders_no_maps(
        self,
    ) -> None:
        _league, season, _teams = _make_league_and_draft_season("SbjModeRandEmpty")
        season.start_season()
        season.map_mode = "random_per_round"
        season.starting_map_pool_ids_json = []
        season.save()
        body = self._get_body(season)
        self.assertIn("Map: Random per Round (no maps)", body)


# ===========================================================================
# LG-02-Part2c-1 — playoff cursor dashboard context keys + DOM ids + relabel
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-1-seam-contract.md`` §6:
#   New context keys ``playoff_phase_active`` / ``playoff_tournament_id`` /
#   ``playoff_completed`` / ``has_following_tournament_phase`` per cursor
#   sub-state; the new DOM ids render only in the tournament-phase sub-state;
#   the View-bracket link points at ``/tournaments/<id>/``; the conditional
#   "Until Playoffs" relabel of the terminal play-dropdown button.
#
# Appended as NEW classes; no existing class above is modified.

from unittest.mock import patch as _Lg02c1_patch  # noqa: E402

from matches.simulation import BatchSimulator as _Lg02c1_BatchSimulator  # noqa: E402
from matches.models import SeasonPhase as _Lg02c1_SeasonPhase  # noqa: E402

_LG02C1_FAST_TICKS = 30


def _lg02c1_rr_tournament_season(name: str = "Pc1"):
    """An active Season: ordinal-1 round_robin + ordinal-2 tournament, 4 teams."""
    league, season, teams = _make_league_and_draft_season(name)
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _lg02c1_play_rr(season, teams):
    by_id = {t.id: t for t in teams}
    sim = _Lg02c1_BatchSimulator()
    with _Lg02c1_patch.object(
        _Lg02c1_BatchSimulator, "ROUND_TICKS", _LG02C1_FAST_TICKS
    ):
        # Mirror the production play loop: tag each Match with its owning RR
        # phase (LG-02-Part2c-2 per-phase completion scopes by season_phase).
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _lg02c1_drain_tournament(tournament):
    from matches.tournament_engine import play_next_node

    with _Lg02c1_patch.object(
        _Lg02c1_BatchSimulator, "ROUND_TICKS", _LG02C1_FAST_TICKS
    ):
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


class TestSeasonDashboardPlayoffContext(TestCase):
    """LG-02-Part2c-1 — playoff cursor context keys per sub-state."""

    def _ctx(self, season):
        return self.client.get(reverse("season_dashboard", args=[season.id])).context

    def test_rr_active_substate_keys(self) -> None:
        # RR phase active, a tournament phase follows.
        _l, season, _teams = _lg02c1_rr_tournament_season("Pc1RR")
        ctx = self._ctx(season)
        self.assertFalse(ctx["playoff_phase_active"])
        self.assertIsNone(ctx["playoff_tournament_id"])
        self.assertFalse(ctx["playoff_completed"])
        self.assertTrue(ctx["has_following_tournament_phase"])

    def test_no_following_tournament_phase_for_single_rr_season(self) -> None:
        # An active single-RR-phase Season: no tournament phase follows.
        _l, season, _teams = _make_active_season("Pc1Single")
        _Lg02c1_SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        ctx = self._ctx(season)
        self.assertFalse(ctx["has_following_tournament_phase"])
        self.assertFalse(ctx["playoff_phase_active"])
        self.assertIsNone(ctx["playoff_tournament_id"])
        self.assertFalse(ctx["playoff_completed"])

    def test_tournament_active_built_substate_keys(self) -> None:
        _l, season, teams = _lg02c1_rr_tournament_season("Pc1Built")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        ctx = self._ctx(season)
        self.assertTrue(ctx["playoff_phase_active"])
        self.assertEqual(ctx["playoff_tournament_id"], tournament_phase.tournament_id)
        self.assertFalse(ctx["playoff_completed"])

    def test_tournament_completed_substate_keys(self) -> None:
        _l, season, teams = _lg02c1_rr_tournament_season("Pc1Done")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _lg02c1_drain_tournament(tournament_phase.tournament)
        season.refresh_from_db()
        ctx = self._ctx(season)
        self.assertFalse(ctx["playoff_phase_active"])
        self.assertEqual(ctx["playoff_tournament_id"], tournament_phase.tournament_id)
        self.assertTrue(ctx["playoff_completed"])


class TestSeasonDashboardPlayoffDomIds(TestCase):
    """LG-02-Part2c-1 — playoff DOM ids render only in the active sub-state."""

    def _body(self, season):
        return self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()

    def test_playoff_buttons_render_in_active_built_substate(self) -> None:
        _l, season, teams = _lg02c1_rr_tournament_season("PdId1")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        body = self._body(season)
        for dom_id in (
            "season-dashboard-play-single-round-form",
            "season-dashboard-play-single-round-submit",
            "season-dashboard-play-playoffs-form",
            "season-dashboard-play-playoffs-submit",
            "season-dashboard-play-playoffs-progress",
            "season-dashboard-view-bracket-link",
        ):
            self.assertIn(f'id="{dom_id}"', body)

    def test_playoff_buttons_absent_in_rr_active_substate(self) -> None:
        _l, season, _teams = _lg02c1_rr_tournament_season("PdId2")
        body = self._body(season)
        for dom_id in (
            "season-dashboard-play-single-round-form",
            "season-dashboard-play-playoffs-form",
        ):
            self.assertNotIn(f'id="{dom_id}"', body)

    def test_view_bracket_link_points_at_league_playoffs(self) -> None:
        _l, season, teams = _lg02c1_rr_tournament_season("PdId3")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        body = self._body(season)
        # The bracket now lives inside the league shell, not the standalone
        # /tournaments/<id>/ page.
        self.assertIn(reverse("league_playoffs", args=[season.league_id]), body)
        self.assertNotIn(f"/tournaments/{tournament_phase.tournament_id}/", body)

    def test_view_bracket_link_renders_when_completed(self) -> None:
        # View-bracket renders for built tournament phase (active OR completed).
        _l, season, teams = _lg02c1_rr_tournament_season("PdId4")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase = season.phases.get(phase_type="tournament")
        tournament_phase.refresh_from_db()
        _lg02c1_drain_tournament(tournament_phase.tournament)
        season.refresh_from_db()
        body = self._body(season)
        self.assertIn('id="season-dashboard-view-bracket-link"', body)
        # The active play buttons are gone once the tournament is completed.
        self.assertNotIn('id="season-dashboard-play-single-round-form"', body)


class TestSeasonDashboardUntilPlayoffsRelabel(TestCase):
    """LG-02-Part2c-1 — terminal play-dropdown 'Until Playoffs' relabel."""

    def _body(self, season):
        return self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()

    def test_until_playoffs_label_when_tournament_phase_follows(self) -> None:
        # RR active with a following tournament phase ⇒ terminal label relabels.
        _l, season, _teams = _lg02c1_rr_tournament_season("RelabelYes")
        body = self._body(season)
        # The until-end button DOM id is UNCHANGED; only the visible text swaps.
        self.assertIn('id="season-dashboard-play-until-end"', body)
        self.assertIn("Until Playoffs", body)

    def test_until_end_label_when_no_tournament_phase_follows(self) -> None:
        _l, season, _teams = _make_active_season("RelabelNo")
        _Lg02c1_SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        body = self._body(season)
        self.assertNotIn("Until Playoffs", body)
        self.assertIn("Until End of Season", body)


# ===========================================================================
# LG-02-Part2c-3c — dashboard terminal-label split: "Until Playoffs" (final
# tournament phase) vs "Until Tournament" (mid-season tournament phase)
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3c-seam-contract.md`` §7 / §9:
#   When the NEXT tournament phase after the current RR phase is the FINAL phase
#   (last ordinal) ⇒ label "Until Playoffs"; when it is mid-season (not last
#   ordinal) ⇒ label "Until Tournament". The terminal play-dropdown button's
#   DOM id (``season-dashboard-play-until-end`` / ``league-dashboard-play-until-end``)
#   and the ``play_until_end`` action are UNCHANGED — only the visible label
#   text varies. Both the Season AND the League dashboard render the split.
#
# Appended as NEW classes; no existing class above is modified. These WILL fail
# until the Code agent lands the Part2c-3c terminal-label split — the TDD red
# state, not a defect in this file.


def _lg3c_final_tournament_season(name: str = "L3cFinal"):
    """An active Season: ordinal-1 round_robin + ordinal-2 (FINAL) tournament,
    4 teams. The current RR phase is followed by a FINAL tournament phase."""
    league, season, teams = _make_league_and_draft_season(name)
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament_mode="standings"
    )
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


def _lg3c_mid_season_tournament_season(name: str = "L3cMid"):
    """An active Season: ordinal-1 round_robin + ordinal-2 (MID-SEASON)
    tournament + ordinal-3 round_robin, 4 teams. The current RR phase is
    followed by a tournament phase that is NOT the final phase."""
    league, season, teams = _make_league_and_draft_season(name)
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament_mode="strength"
    )
    _Lg02c1_SeasonPhase.objects.create(
        season=season, ordinal=3, phase_type="round_robin"
    )
    season.start_season()
    season.refresh_from_db()
    return league, season, teams


class TestSeasonDashboardTerminalLabelSplit(TestCase):
    """LG-02-Part2c-3c — Season-dashboard terminal label split."""

    def _body(self, season):
        return self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()

    def test_until_playoffs_when_final_tournament_phase(self) -> None:
        _l, season, _teams = _lg3c_final_tournament_season("L3cSeasonFinal")
        body = self._body(season)
        # The until-end button DOM id is UNCHANGED; only the visible text swaps.
        self.assertIn('id="season-dashboard-play-until-end"', body)
        self.assertIn("Until Playoffs", body)
        self.assertNotIn("Until Tournament", body)

    def test_until_tournament_when_mid_season_tournament_phase(self) -> None:
        _l, season, _teams = _lg3c_mid_season_tournament_season("L3cSeasonMid")
        body = self._body(season)
        # DOM id + action unchanged; the mid-season label is "Until Tournament".
        self.assertIn('id="season-dashboard-play-until-end"', body)
        self.assertIn("Until Tournament", body)
        self.assertNotIn("Until Playoffs", body)

    def test_play_until_end_action_unchanged_both_cases(self) -> None:
        # The form action still POSTs to play_until_end in BOTH label cases.
        _l, final_season, _t = _lg3c_final_tournament_season("L3cActFinal")
        _l2, mid_season, _t2 = _lg3c_mid_season_tournament_season("L3cActMid")
        final_action = reverse("play_until_end", args=[final_season.id])
        mid_action = reverse("play_until_end", args=[mid_season.id])
        self.assertIn(final_action, self._body(final_season))
        self.assertIn(mid_action, self._body(mid_season))


class TestLeagueDashboardTerminalLabelSplit(TestCase):
    """LG-02-Part2c-3c — League-dashboard terminal label split (mirrors the
    Season dashboard; the League dashboard renders the active Season)."""

    def _body(self, league):
        return self.client.get(
            reverse("league_dashboard", args=[league.id])
        ).content.decode()

    def test_until_playoffs_when_final_tournament_phase(self) -> None:
        league, _season, _teams = _lg3c_final_tournament_season("L3cLeagueFinal")
        body = self._body(league)
        self.assertIn('id="league-dashboard-play-until-end"', body)
        self.assertIn("Until Playoffs", body)
        self.assertNotIn("Until Tournament", body)

    def test_until_tournament_when_mid_season_tournament_phase(self) -> None:
        league, _season, _teams = _lg3c_mid_season_tournament_season("L3cLeagueMid")
        body = self._body(league)
        self.assertIn('id="league-dashboard-play-until-end"', body)
        self.assertIn("Until Tournament", body)
        self.assertNotIn("Until Playoffs", body)


# ===========================================================================
# LG-01i — the "One Week (Live)" Play-dropdown entry on the SEASON dashboard.
#
# Seam contract: ``.claude/worktrees/lg-01i-one-week-live-seam-contract.md``
# §8 (dashboard wiring) + §9 (test boundary).
#
# The ``season-dashboard-play-one-week-live`` entry renders inside the existing
# play dropdown linking to ``play_week_live`` ONLY when ``live_preview_available``
# (``_resolve_live_cursor`` returns an ``"rr"`` / ``"playoff"`` descriptor). It
# is ABSENT on a bye / eliminated / no-``current_team`` Season.
#
# These assertions WILL fail until the Code agent lands the
# ``live_preview_available`` context key + the dropdown-entry template markup +
# the ``play_week_live`` URL; that is the expected TDD red state.
# ===========================================================================


def _lg01i_dash_active_rr_season(name: str, n: int = 2, *, manager_idx: int = 0):
    """An active n-team RR Season; League ``current_team`` = the
    ``manager_idx``-th team (None ⇒ no current_team set)."""
    league = League.objects.create(name=name)
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{name[:3]}D{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    if manager_idx is not None:
        league.current_team = teams[manager_idx]
        league.save(update_fields=["current_team"])
    return league, season, teams


class TestLg01iDashboardEntry(TestCase):
    """LG-01i — the season-dashboard ``play-one-week-live`` dropdown entry."""

    def _body(self, season):
        return self.client.get(
            reverse("season_dashboard", args=[season.id])
        ).content.decode()

    def test_entry_present_and_links_to_play_week_live_when_available(self) -> None:
        _l, season, _teams = _lg01i_dash_active_rr_season(
            "DashEntryYes", n=2, manager_idx=0
        )
        ctx = self.client.get(reverse("season_dashboard", args=[season.id])).context
        self.assertTrue(ctx["live_preview_available"])
        body = self._body(season)
        self.assertIn('id="season-dashboard-play-one-week-live"', body)
        self.assertIn(reverse("play_week_live", args=[season.id]), body)

    def test_entry_absent_when_no_current_team(self) -> None:
        _l, season, _teams = _lg01i_dash_active_rr_season(
            "DashEntryNoTeam", n=2, manager_idx=None
        )
        ctx = self.client.get(reverse("season_dashboard", args=[season.id])).context
        self.assertFalse(ctx["live_preview_available"])
        body = self._body(season)
        self.assertNotIn('id="season-dashboard-play-one-week-live"', body)

    def test_entry_absent_when_current_team_has_bye(self) -> None:
        # Odd-N matchday: one team byes. Set that team as current_team ⇒
        # _resolve_live_cursor returns rr_bye ⇒ entry NOT rendered.
        _l, season, teams = _lg01i_dash_active_rr_season(
            "DashEntryBye", n=3, manager_idx=0
        )
        in_next = set()
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            if not fixtures:
                continue
            first_md = min(f.matchday for f in fixtures)
            for f in fixtures:
                if f.matchday == first_md:
                    in_next.add(f.team_a_id)
                    in_next.add(f.team_b_id)
            break
        bye_team = next((t for t in teams if t.id not in in_next), None)
        self.assertIsNotNone(bye_team)
        season.league.current_team = bye_team
        season.league.save(update_fields=["current_team"])
        ctx = self.client.get(reverse("season_dashboard", args=[season.id])).context
        self.assertFalse(ctx["live_preview_available"])
        body = self._body(season)
        self.assertNotIn('id="season-dashboard-play-one-week-live"', body)

    def test_entry_absent_when_eliminated_in_playoff(self) -> None:
        # RR done + tournament built; current_team eliminated ⇒ no live entry.
        league, season, teams = _lg02c1_rr_tournament_season("DashEntryElim")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tp = season.phases.get(phase_type="tournament")
        tp.refresh_from_db()
        # Find an eliminated team (not in any undecided node).
        from matches.models import BracketNode as _BN

        alive_ids = set()
        for node in _BN.objects.filter(
            tournament=tp.tournament,
            winner__isnull=True,
            is_bye=False,
            team_a__isnull=False,
            team_b__isnull=False,
        ):
            alive_ids.add(node.team_a_id)
            alive_ids.add(node.team_b_id)
        elim = next((t for t in teams if t.id not in alive_ids), None)
        if elim is None:
            self.skipTest("no eliminated team in this non-deterministic bracket")
        league.current_team = elim
        league.save(update_fields=["current_team"])
        ctx = self.client.get(reverse("season_dashboard", args=[season.id])).context
        self.assertFalse(ctx["live_preview_available"])
        body = self._body(season)
        self.assertNotIn('id="season-dashboard-play-one-week-live"', body)
