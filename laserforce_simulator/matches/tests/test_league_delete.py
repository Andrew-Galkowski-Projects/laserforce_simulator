"""DEL-01 — Django ``TestCase`` tests for the Delete League full-teardown flow.

Seam contract (LOCKED) at ``.claude/worktrees/del-01-seam-contract.md``. The
feature: ``matches.league_views.league_delete(request, league_id)`` at URL name
``league_delete`` (``/leagues/<id>/delete/``), career-mode (``League.mode ==
"league"``) ONLY.

  - GET on a league-mode League → 200, renders
    ``leagues/league_confirm_delete.html``, context carries ``league`` +
    ``delete_summary`` (keys ``seasons, matches, tournaments, teams, players``).
  - POST → full teardown in one atomic block, then ``redirect("league_list")``
    (302). Teardown: the League's Seasons (cascade → SeasonPhase /
    PlayerSeasonRating / TeamSeasonFinance / OwnerEvaluation), the league's
    season-scoped Matches AND season-embedded-tournament playoff Matches
    (cascade → GameRound / GameEvent / PlayerRoundState), the embedded
    Tournament rows, the League itself, and — guarded — the league's owned Teams
    (cascade → Players) ONLY if each Team has no surviving Match / Season /
    League reference.
  - Non-``league`` mode (sandbox / multiplayer) → 400 on both GET and POST,
    nothing deleted.

These tests are RED until the Code agent lands the view + URL + template (the
TDD red state, not a defect in this file). They assert on row counts /
``.exists()`` / status codes / redirect targets / DOM ids — NEVER on simulated
point totals; fixtures are hand-built (no simulator run).
"""

from __future__ import annotations

from datetime import date

from django.test import TestCase
from django.urls import reverse

from matches.models import (
    BracketNode,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
    SeriesMatch,
    TeamSeasonFinance,
    Tournament,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Player, Team

# ---------------------------------------------------------------------------
# Helpers — all fixtures hand-built; no simulator entered.
# ---------------------------------------------------------------------------

# The 19 stat ints PlayerSeasonRating requires (no defaults) — names mirror
# Player, including the intentional capital-O ``Offensive_synergy``.
_RATING_STAT_FIELDS = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)


def _make_league(name: str = "L", *, mode: str = "league") -> League:
    return League.objects.create(name=name, mode=mode, state="active")


def _make_season(
    league: League, *, name: str = "Season 1", state: str = "completed"
) -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2025, 1, 1),
        state=state,
    )


def _make_rr_phase(season: Season, *, ordinal: int = 1) -> SeasonPhase:
    return SeasonPhase.objects.create(
        season=season,
        ordinal=ordinal,
        phase_type="round_robin",
        schedule_format="single_round_robin",
    )


def _make_season_match(season: Season, team_red: Team, team_blue: Team) -> Match:
    """A season-scoped completed Match + one GameRound (no simulator)."""
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        is_completed=True,
    )
    GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        is_completed=True,
    )
    return match


def _make_rating(player: Player, season: Season) -> PlayerSeasonRating:
    stats = {name: 50 for name in _RATING_STAT_FIELDS}
    return PlayerSeasonRating.objects.create(
        player=player,
        season=season,
        age=25,
        overall_rating=50.0,
        **stats,
    )


# ---------------------------------------------------------------------------
# 1. Routing + GET confirm page
# ---------------------------------------------------------------------------


class TestLeagueDeleteRouting(TestCase):
    """reverse() resolves; GET on a league-mode League renders the confirm page
    with the locked context + DOM ids and a correct delete_summary."""

    def test_reverse_resolves_to_expected_path(self) -> None:
        league = _make_league("RouteRev")
        self.assertEqual(
            reverse("league_delete", args=[league.id]),
            f"/leagues/{league.id}/delete/",
        )

    def test_get_returns_200_for_league_mode(self) -> None:
        league = _make_league("GetOk")
        response = self.client.get(reverse("league_delete", args=[league.id]))
        self.assertEqual(response.status_code, 200)

    def test_get_uses_confirm_template(self) -> None:
        league = _make_league("GetTpl")
        response = self.client.get(reverse("league_delete", args=[league.id]))
        self.assertTemplateUsed(response, "leagues/league_confirm_delete.html")

    def test_get_context_has_league_and_delete_summary_keys(self) -> None:
        league = _make_league("GetCtx")
        response = self.client.get(reverse("league_delete", args=[league.id]))
        self.assertEqual(response.context["league"], league)
        summary = response.context["delete_summary"]
        for key in ("seasons", "matches", "tournaments", "teams", "players"):
            self.assertIn(key, summary, f"delete_summary missing key {key!r}")

    def test_get_delete_summary_counts_match_fixture(self) -> None:
        # 1 completed Season, 2 enrolled Teams (6 players each), 1 season Match,
        # no embedded tournament, no current_team / pool. So:
        #   seasons = 1, matches = 1, tournaments = 0,
        #   teams = 2 (the enrolled candidates), players = 12.
        league = _make_league("GetCounts")
        season = _make_season(league)
        _make_rr_phase(season)
        t1, _ = make_team_with_slots("GcA")
        t2, _ = make_team_with_slots("GcB")
        season.teams.add(t1, t2)
        _make_season_match(season, t1, t2)

        response = self.client.get(reverse("league_delete", args=[league.id]))
        summary = response.context["delete_summary"]
        self.assertEqual(summary["seasons"], 1)
        self.assertEqual(summary["matches"], 1)
        self.assertEqual(summary["tournaments"], 0)
        self.assertEqual(summary["teams"], 2)
        self.assertEqual(summary["players"], 12)

    def test_get_renders_locked_dom_ids(self) -> None:
        league = _make_league("GetDom")
        response = self.client.get(reverse("league_delete", args=[league.id]))
        body = response.content.decode()
        for dom_id in (
            "league-delete-confirm",
            "league-delete-summary",
            "league-delete-form",
            "league-delete-submit",
            "league-delete-cancel",
        ):
            self.assertIn(f'id="{dom_id}"', body, f"missing DOM id {dom_id!r}")


# ---------------------------------------------------------------------------
# 2. Full-teardown happy path + redirect
# ---------------------------------------------------------------------------


class TestLeagueDeleteTeardown(TestCase):
    """POST tears down the whole career League + its owned Teams/Players and
    redirects to ``league_list``."""

    def _build(self):
        league = _make_league("TeardownL")
        season = _make_season(league)
        phase = _make_rr_phase(season)
        t1, _ = make_team_with_slots("TdA")
        t2, _ = make_team_with_slots("TdB")
        season.teams.add(t1, t2)
        # Free-agent pool Team with its own players.
        pool = Team.objects.create(name="TdPool")
        pool_player = Player.objects.create(team=pool, name="TdPoolP")
        league.current_team = t1
        league.free_agent_pool = pool
        league.save(update_fields=["current_team", "free_agent_pool"])

        match = _make_season_match(season, t1, t2)
        rating = _make_rating(t1.slot_commander, season)
        finance = TeamSeasonFinance.objects.create(team=t1, season=season)
        evaluation = OwnerEvaluation.objects.create(
            league=league,
            season=season,
            team_managed=t1,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
            verdict="retained",
        )
        return {
            "league": league,
            "season": season,
            "phase": phase,
            "t1": t1,
            "t2": t2,
            "pool": pool,
            "pool_player": pool_player,
            "match": match,
            "rating": rating,
            "finance": finance,
            "evaluation": evaluation,
        }

    def test_post_redirects_to_league_list(self) -> None:
        f = self._build()
        response = self.client.post(reverse("league_delete", args=[f["league"].id]))
        self.assertEqual(response.status_code, 302)
        self.assertEqual(response["Location"], reverse("league_list"))

    def test_post_deletes_league_and_seasons_and_phases(self) -> None:
        f = self._build()
        league_id, season_id, phase_id = f["league"].id, f["season"].id, f["phase"].id
        self.client.post(reverse("league_delete", args=[league_id]))
        self.assertFalse(League.objects.filter(pk=league_id).exists())
        self.assertFalse(Season.objects.filter(pk=season_id).exists())
        self.assertFalse(SeasonPhase.objects.filter(pk=phase_id).exists())

    def test_post_deletes_matches_and_game_rounds(self) -> None:
        f = self._build()
        match_id = f["match"].id
        round_ids = list(f["match"].game_rounds.values_list("id", flat=True))
        self.assertTrue(round_ids)  # sanity: a GameRound existed
        self.client.post(reverse("league_delete", args=[f["league"].id]))
        self.assertFalse(Match.objects.filter(pk=match_id).exists())
        self.assertFalse(GameRound.objects.filter(id__in=round_ids).exists())

    def test_post_deletes_owned_teams_and_players(self) -> None:
        f = self._build()
        team_ids = [f["t1"].id, f["t2"].id, f["pool"].id]
        player_ids = list(
            Player.objects.filter(team_id__in=team_ids).values_list("id", flat=True)
        )
        self.assertTrue(player_ids)  # sanity: players existed
        self.client.post(reverse("league_delete", args=[f["league"].id]))
        self.assertFalse(Team.objects.filter(id__in=team_ids).exists())
        self.assertFalse(Player.objects.filter(id__in=player_ids).exists())

    def test_post_deletes_season_scoped_career_rows(self) -> None:
        f = self._build()
        rating_id = f["rating"].id
        finance_id = f["finance"].id
        eval_id = f["evaluation"].id
        self.client.post(reverse("league_delete", args=[f["league"].id]))
        self.assertFalse(PlayerSeasonRating.objects.filter(pk=rating_id).exists())
        self.assertFalse(TeamSeasonFinance.objects.filter(pk=finance_id).exists())
        self.assertFalse(OwnerEvaluation.objects.filter(pk=eval_id).exists())


# ---------------------------------------------------------------------------
# 3. Cross-context SAFETY (the load-bearing test)
# ---------------------------------------------------------------------------


class TestLeagueDeleteCrossContextSafety(TestCase):
    """The zero-reference guard must NOT cascade a Team that is still
    referenced by a surviving Season / Match outside the deleted League."""

    def test_team_enrolled_in_another_league_survives(self) -> None:
        # Shared Team T enrolled in BOTH the deleted league's Season AND a
        # surviving league's Season → exercises the enrolled_seasons guard.
        league_a = _make_league("CrossA")
        season_a = _make_season(league_a, state="completed")
        league_b = _make_league("CrossB")
        season_b = _make_season(league_b, state="active")
        shared, _ = make_team_with_slots("Shared")
        season_a.teams.add(shared)
        season_b.teams.add(shared)

        self.client.post(reverse("league_delete", args=[league_a.id]))

        # Deleted league gone, surviving league + season + shared team intact.
        self.assertFalse(League.objects.filter(pk=league_a.id).exists())
        self.assertTrue(League.objects.filter(pk=league_b.id).exists())
        self.assertTrue(Season.objects.filter(pk=season_b.id).exists())
        self.assertTrue(Team.objects.filter(pk=shared.id).exists())
        # Still enrolled in the surviving league's Season only.
        self.assertEqual(
            set(shared.enrolled_seasons.values_list("id", flat=True)),
            {season_b.id},
        )

    def test_team_with_sandbox_match_survives_with_its_match(self) -> None:
        # T is a candidate of the deleted league (enrolled in its Season) AND
        # played a sandbox Match (season=NULL, not this league) → exercises the
        # red_matches/blue_matches guard. T and the sandbox Match both survive.
        league_a = _make_league("SandboxA")
        season_a = _make_season(league_a, state="completed")
        candidate, _ = make_team_with_slots("SbCand")
        opponent, _ = make_team_with_slots("SbOpp")
        season_a.teams.add(candidate)
        sandbox_match = Match.objects.create(
            team_red=candidate,
            team_blue=opponent,
            season=None,  # sandbox — never this league
            is_completed=True,
        )

        self.client.post(reverse("league_delete", args=[league_a.id]))

        self.assertFalse(League.objects.filter(pk=league_a.id).exists())
        # The candidate Team survives (a surviving Match references it).
        self.assertTrue(Team.objects.filter(pk=candidate.id).exists())
        # The sandbox Match survives (not season-scoped to the deleted league).
        self.assertTrue(Match.objects.filter(pk=sandbox_match.id).exists())
        # The opponent (never a candidate) is untouched.
        self.assertTrue(Team.objects.filter(pk=opponent.id).exists())


# ---------------------------------------------------------------------------
# 4. Embedded-tournament / playoff teardown
# ---------------------------------------------------------------------------


class TestLeagueDeleteTournamentTeardown(TestCase):
    """A Season with a SeasonPhase.tournament embed → the Tournament, its
    BracketNodes / SeriesMatches, and the playoff Match (season=NULL) + its
    GameRounds are all deleted."""

    def test_embedded_tournament_and_playoff_match_deleted(self) -> None:
        league = _make_league("TourneyL")
        season = _make_season(league, state="completed")
        _make_rr_phase(season, ordinal=1)

        t1, _ = make_team_with_slots("TtA")
        t2, _ = make_team_with_slots("TtB")
        season.teams.add(t1, t2)

        tournament = Tournament.objects.create(
            name="Playoffs", format="single_elimination", state="active"
        )
        # The SeasonPhase pointing at the embedded Tournament.
        SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament=tournament,
        )
        node = BracketNode.objects.create(
            tournament=tournament,
            bracket_round=1,
            position=0,
            team_a=t1,
            team_b=t2,
        )
        # A playoff Match reachable via series_match__node__tournament; season=NULL.
        playoff_match = Match.objects.create(
            team_red=t1,
            team_blue=t2,
            season=None,
            is_completed=True,
        )
        playoff_round = GameRound.objects.create(
            match=playoff_match,
            round_number=1,
            team_red=t1,
            team_blue=t2,
            is_completed=True,
        )
        series = SeriesMatch.objects.create(
            node=node, match=playoff_match, game_number=1
        )

        self.client.post(reverse("league_delete", args=[league.id]))

        self.assertFalse(League.objects.filter(pk=league.id).exists())
        self.assertFalse(Tournament.objects.filter(pk=tournament.id).exists())
        self.assertFalse(BracketNode.objects.filter(pk=node.id).exists())
        self.assertFalse(SeriesMatch.objects.filter(pk=series.id).exists())
        self.assertFalse(Match.objects.filter(pk=playoff_match.id).exists())
        self.assertFalse(GameRound.objects.filter(pk=playoff_round.id).exists())


# ---------------------------------------------------------------------------
# 5. Mode gate — non-career League → 400, nothing deleted
# ---------------------------------------------------------------------------


class TestLeagueDeleteModeGate(TestCase):
    """A non-``league``-mode League (sandbox / multiplayer) returns 400 on both
    GET and POST and deletes nothing."""

    def _build(self, mode: str):
        league = _make_league(f"Gate{mode}", mode=mode)
        season = _make_season(league, state="completed")
        t1, _ = make_team_with_slots(f"Gt{mode}A")
        season.teams.add(t1)
        return league, season, t1

    def test_post_sandbox_returns_400(self) -> None:
        league, _season, _t1 = self._build("sandbox")
        response = self.client.post(reverse("league_delete", args=[league.id]))
        self.assertEqual(response.status_code, 400)

    def test_get_sandbox_returns_400(self) -> None:
        league, _season, _t1 = self._build("sandbox")
        response = self.client.get(reverse("league_delete", args=[league.id]))
        self.assertEqual(response.status_code, 400)

    def test_post_multiplayer_returns_400(self) -> None:
        league, _season, _t1 = self._build("multiplayer")
        response = self.client.post(reverse("league_delete", args=[league.id]))
        self.assertEqual(response.status_code, 400)

    def test_post_sandbox_deletes_nothing(self) -> None:
        league, season, t1 = self._build("sandbox")
        self.client.post(reverse("league_delete", args=[league.id]))
        self.assertTrue(League.objects.filter(pk=league.id).exists())
        self.assertTrue(Season.objects.filter(pk=season.id).exists())
        self.assertTrue(Team.objects.filter(pk=t1.id).exists())


# ---------------------------------------------------------------------------
# 6. Entry points render the delete link only for league-mode Leagues
# ---------------------------------------------------------------------------


class TestLeagueDeleteEntryPoints(TestCase):
    """The dashboard + list delete links render for a league-mode League and
    are ABSENT for a non-league-mode League."""

    def test_dashboard_renders_delete_link_for_league_mode(self) -> None:
        league = _make_league("DashLink")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertIn('id="league-dashboard-delete-link"', response.content.decode())

    def test_dashboard_omits_delete_link_for_non_league_mode(self) -> None:
        league = _make_league("DashNoLink", mode="sandbox")
        response = self.client.get(reverse("league_dashboard", args=[league.id]))
        self.assertNotIn('id="league-dashboard-delete-link"', response.content.decode())

    def test_list_renders_delete_link_for_league_mode_row_only(self) -> None:
        league_mode = _make_league("ListLeague", mode="league")
        sandbox = _make_league("ListSandbox", mode="sandbox")
        response = self.client.get(reverse("league_list"))
        body = response.content.decode()
        self.assertIn(f"league-list-delete-link-{league_mode.id}", body)
        self.assertNotIn(f"league-list-delete-link-{sandbox.id}", body)
