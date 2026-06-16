"""CAR-02 — view tests for the New-Team picker + reassign endpoints
(seam contract §3.4 / §4.2 / §6.5).

* ``GET /leagues/<int:league_id>/new-team/`` (``new_team_picker``) — the
  eligible list = the worst-``WORST_N_ELIGIBLE`` (5) teams by the just-completed
  Season's final Standings, EXCLUDING the just-left team
  (``league.current_team``); the LOCKED ``new-team-*`` DOM ids.
* ``POST /leagues/<int:league_id>/reassign-team/`` (``reassign_team``) — 302 /
  405 / 400 (out-of-set ``team_id``); sets ``current_team`` to the picked team;
  starts a new tenure (the next ensure pass resets cumulative + grace); the
  shared ``_run_season_rollover`` runs (a new draft Season exists after the POST).

Standings come from hand-built completed Matches (the LG-01c / LG-06g
fixture-pattern) so the worst-N ordering is deterministic; NO simulator, NO
simulated point totals.

These FAIL until the Code agent lands the two views + URLs +
``templates/leagues/new_team.html`` + the model + the rollover helper.
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import GameRound, League, Match, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_team(prefix: str):
    team, _ = make_team_with_slots(prefix)
    return team


def _make_league(name: str, *, current_team=None) -> League:
    return League.objects.create(
        name=name, mode="league", state="active", current_team=current_team
    )


def _make_completed_season(league, *, name, start_date, team_ids):
    return Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
        schedule_format="single_round_robin",
        state="completed",
        starting_team_ids_json=sorted(team_ids),
    )


def _add_match(season, winner, loser, *, win_pts=100, lose_pts=1):
    """A completed Match the ``winner`` wins 2-0 over the ``loser``."""
    match = Match.objects.create(
        team_red=winner,
        team_blue=loser,
        season=season,
        red_round1_points=win_pts,
        blue_round1_points=lose_pts,
        red_round2_points=win_pts,
        blue_round2_points=lose_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=winner,
        team_blue=loser,
        round_number=1,
        red_points=win_pts,
        blue_points=lose_pts,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        team_red=loser,
        team_blue=winner,
        round_number=2,
        red_points=lose_pts,
        blue_points=win_pts,
        is_completed=True,
    )
    return match


def _make_ranked_league(n_teams: int = 8, *, league_name: str = "RankL"):
    """Build a completed Season with ``n_teams`` teams given a strict win
    ordering: team 0 beats all, team 1 beats all but team 0, … so the final
    standings rank is deterministic (team 0 best → team n-1 worst).

    Returns ``(league, season, teams)`` where ``teams[0]`` is the strongest and
    ``teams[-1]`` the weakest. ``league.current_team`` is set to ``teams[0]``
    (the "just-left" team that fired the manager).
    """
    teams = [_make_team(f"{league_name}{i}") for i in range(n_teams)]
    league = _make_league(league_name, current_team=teams[0])
    team_ids = [t.id for t in teams]
    season = _make_completed_season(
        league, name="Season 1", start_date=date(2025, 1, 1), team_ids=team_ids
    )
    # Stronger index beats every weaker index ⇒ wins descend with index.
    for i in range(n_teams):
        for j in range(i + 1, n_teams):
            _add_match(season, teams[i], teams[j])
    return league, season, teams


# ---------------------------------------------------------------------------
# TestNewTeamPickerRouting
# ---------------------------------------------------------------------------


class TestNewTeamPickerRouting(TestCase):
    """GET 200 / 405 on non-GET / reverse."""

    def test_reverse_resolves(self) -> None:
        league, _s, _t = _make_ranked_league()
        self.assertEqual(
            reverse("new_team_picker", kwargs={"league_id": league.id}),
            f"/leagues/{league.id}/new-team/",
        )

    def test_get_returns_200(self) -> None:
        league, _s, _t = _make_ranked_league()
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league, _s, _t = _make_ranked_league()
        response = self.client.post(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_uses_new_team_template(self) -> None:
        league, _s, _t = _make_ranked_league()
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        self.assertTemplateUsed(response, "leagues/new_team.html")

    def test_writes_last_league_id(self) -> None:
        league, _s, _t = _make_ranked_league()
        self.client.get(reverse("new_team_picker", kwargs={"league_id": league.id}))
        self.assertEqual(self.client.session["last_league_id"], league.id)


# ---------------------------------------------------------------------------
# TestNewTeamPickerEligibleList
# ---------------------------------------------------------------------------


class TestNewTeamPickerEligibleList(TestCase):
    """Eligible = worst-5 by final standings, EXCLUDING the just-left team."""

    def test_offers_worst_five_excluding_left_team(self) -> None:
        # 8 teams; the strongest (teams[0]) is current_team (just-left). The
        # worst 5 by standings are teams[3..7]. teams[0] must be excluded even
        # though it is not in the worst-5 anyway; a tighter check is the
        # rendered option set.
        league, _season, teams = _make_ranked_league(8)
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        # The 5 weakest (teams[3..7]) are offered.
        for t in teams[3:8]:
            self.assertIn(f'id="new-team-option-{t.id}"', body)
        # The just-left team (current_team == teams[0]) is NOT offered.
        self.assertNotIn(f'id="new-team-option-{teams[0].id}"', body)

    def test_exactly_five_options_when_more_than_five_teams(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        option_count = sum(1 for t in teams if f'id="new-team-option-{t.id}"' in body)
        self.assertEqual(option_count, 5)

    def test_excludes_left_team_even_when_left_team_is_weak(self) -> None:
        # Make the just-left team the WEAKEST (would otherwise be in worst-5),
        # and assert it is still excluded.
        teams = [_make_team(f"WeakLeft{i}") for i in range(8)]
        league = _make_league("WeakLeftL", current_team=teams[-1])  # weakest
        team_ids = [t.id for t in teams]
        season = _make_completed_season(
            league, name="Season 1", start_date=date(2025, 1, 1), team_ids=team_ids
        )
        for i in range(8):
            for j in range(i + 1, 8):
                _add_match(season, teams[i], teams[j])
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        body = response.content.decode()
        # The weakest team (the just-left team) is excluded despite being worst.
        self.assertNotIn(f'id="new-team-option-{teams[-1].id}"', body)

    def test_form_and_submit_dom_ids_present(self) -> None:
        league, _season, _teams = _make_ranked_league(8)
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        self.assertContains(response, 'id="new-team-picker"')
        self.assertContains(response, 'id="new-team-form"')
        self.assertContains(response, 'id="new-team-submit"')

    def test_form_action_posts_to_reassign_team(self) -> None:
        league, _season, _teams = _make_ranked_league(8)
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        expected = reverse("reassign_team", kwargs={"league_id": league.id})
        self.assertContains(response, f'action="{expected}"')

    def test_csrf_token_present_in_form(self) -> None:
        league, _season, _teams = _make_ranked_league(8)
        response = self.client.get(
            reverse("new_team_picker", kwargs={"league_id": league.id})
        )
        self.assertContains(response, "csrfmiddlewaretoken")


# ---------------------------------------------------------------------------
# TestReassignTeamRouting
# ---------------------------------------------------------------------------


class TestReassignTeamRouting(TestCase):
    """POST 302 / 405 on non-POST / 400 on out-of-set id / reverse."""

    def test_reverse_resolves(self) -> None:
        league, _s, _t = _make_ranked_league()
        self.assertEqual(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            f"/leagues/{league.id}/reassign-team/",
        )

    def test_get_returns_405(self) -> None:
        league, _s, _t = _make_ranked_league()
        response = self.client.get(
            reverse("reassign_team", kwargs={"league_id": league.id})
        )
        self.assertEqual(response.status_code, 405)

    def test_post_valid_pick_returns_302(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        pick = teams[-1]  # a worst-5 team
        response = self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": pick.id},
        )
        self.assertEqual(response.status_code, 302)

    def test_post_out_of_set_id_returns_400(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        # teams[0] is the just-left (excluded) team — out of the eligible set.
        response = self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": teams[0].id},
        )
        self.assertEqual(response.status_code, 400)

    def test_post_unknown_id_returns_400(self) -> None:
        league, _season, _teams = _make_ranked_league(8)
        response = self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": 999_999},
        )
        self.assertEqual(response.status_code, 400)


# ---------------------------------------------------------------------------
# TestReassignTeamEffects
# ---------------------------------------------------------------------------


class TestReassignTeamEffects(TestCase):
    """A valid reassign sets ``current_team`` + runs the shared rollover."""

    def test_sets_current_team_to_picked_team(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        pick = teams[-1]
        self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": pick.id},
        )
        league.refresh_from_db()
        self.assertEqual(league.current_team_id, pick.id)

    def test_rollover_creates_new_draft_season(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        pre_count = league.seasons.count()
        self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": teams[-1].id},
        )
        post_count = league.seasons.count()
        self.assertEqual(post_count, pre_count + 1)
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(new_season.state, "draft")

    def test_redirects_to_new_season_dashboard(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        response = self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": teams[-1].id},
        )
        new_season = league.seasons.order_by("-id").first()
        self.assertEqual(
            response["Location"],
            reverse("season_dashboard", args=[new_season.id]),
        )

    def test_out_of_set_post_does_not_change_current_team_or_roll(self) -> None:
        league, _season, teams = _make_ranked_league(8)
        before_team = league.current_team_id
        pre_count = league.seasons.count()
        self.client.post(
            reverse("reassign_team", kwargs={"league_id": league.id}),
            data={"team_id": teams[0].id},  # excluded
        )
        league.refresh_from_db()
        # current_team unchanged; no new Season rolled.
        self.assertEqual(league.current_team_id, before_team)
        self.assertEqual(league.seasons.count(), pre_count)
