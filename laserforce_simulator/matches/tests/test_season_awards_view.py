"""LG-03 — Django ``TestCase`` tests for ``matches.league_views.season_awards``.

The awards page is read-only / GET-only at
``GET /seasons/<int:season_id>/awards/`` (URL name ``season_awards``). It
recomputes the season-end awards transiently from the frozen regular-season
``PlayerRoundState`` corpus (``game_round__match__season=season``), seeds the
``finals_mvp`` slot from the championship Match of a BRACKET-format playoff
phase (else ``None``), and renders all the LOCKED DOM ids.

Tests hand-construct League / Season / SeasonPhase / Match / GameRound /
PlayerRoundState rows — LG-03 runs NO simulation, so the simulator is never
entered. The finals case hand-builds the
``Tournament → BracketNode (advances_to None, winner==champion) → SeriesMatch
→ Match → GameRound → PlayerRoundState`` chain.

Assertion discipline (LG-03 §5.4): assert on award WINNER identity / DOM ids /
``finals_mvp`` presence — NEVER on simulated point totals (every fixture row is
hand-built with deterministic stats).

Written test-first against the LG-03 seam contract
(``.claude/worktrees/lg-03-season-awards-seam-contract.md`` §2, §5.2); these
FAIL until the Code agent lands the view + URL + template.
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
    PlayerRoundState,
    Season,
    SeasonPhase,
    SeriesMatch,
    Tournament,
    TournamentParticipant,
)
from matches.tests.conftest import make_team_with_slots
from teams.models import Team

# The 5 role strings stored on ``PlayerRoundState.role``.
ROLES = ("commander", "heavy", "scout", "medic", "ammo")


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_league(name: str = "AwLeague") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_completed_season(
    league: League,
    *,
    name: str = "S1",
    teams: list[Team] | None = None,
) -> Season:
    """A completed Season with a single ordinal-1 round_robin phase."""
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=date(2026, 1, 1),
        state="completed",
        starting_team_ids_json=sorted(t.id for t in teams) if teams else [],
    )
    if teams:
        season.teams.add(*teams)
    SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    return season


def _make_teams(prefix: str, n: int) -> list[Team]:
    teams: list[Team] = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    return teams


def _reg_round_with_states(season, team_red, team_blue, states):
    """Persist a REGULAR-SEASON Match + GameRound under ``season`` plus PRS.

    ``states`` is a list of ``(player, team_color, role, stat_kwargs)``.
    Returns the GameRound. The Match carries ``season=<season>`` so it is in
    the awards corpus (``game_round__match__season=season``).
    """
    match = Match.objects.create(
        team_red=team_red, team_blue=team_blue, season=season, is_completed=True
    )
    game_round = GameRound.objects.create(
        match=match,
        round_number=1,
        team_red=team_red,
        team_blue=team_blue,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )
    for player, color, role, kwargs in states:
        PlayerRoundState.objects.create(
            game_round=game_round,
            player=player,
            team_color=color,
            role=role,
            **kwargs,
        )
    return game_round


def _bracket_playoff_phase(
    season,
    teams,
    *,
    fmt: str = "single_elimination",
    champion_player_states=None,
):
    """Hand-build a built+completed BRACKET playoff phase for ``season``.

    Constructs the full finals chain:
        SeasonPhase(tournament=...) → Tournament(format=fmt, champion=team)
        → BracketNode(advances_to=None, winner=team) → SeriesMatch → Match
        → GameRound → PlayerRoundState

    The championship Match carries ``season=NULL`` (playoff Matches are
    excluded from the regular-season corpus). ``champion_player_states`` is a
    list of ``(player, role, mvp_kwargs)`` placed on the final Match's
    GameRound. Returns ``(tournament, championship_match, final_round)``.
    """
    tournament = Tournament.objects.create(
        name=f"{season.name} Playoffs", format=fmt, state="completed"
    )
    for i, t in enumerate(teams):
        TournamentParticipant.objects.create(tournament=tournament, team=t, seed=i + 1)
    champion_team = teams[0]
    tournament.champion = champion_team
    tournament.save(update_fields=["champion"])

    final_node = BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=0,
        team_a=teams[0],
        team_b=teams[1],
        seed_a=1,
        seed_b=2,
        advances_to=None,
        winner=champion_team,
        bracket_type="winners" if fmt != "double_elimination" else "grand_final",
    )

    # Playoff Match: season=NULL (excluded from the regular-season corpus).
    champ_match = Match.objects.create(
        team_red=teams[0], team_blue=teams[1], season=None, is_completed=True
    )
    SeriesMatch.objects.create(
        node=final_node, match=champ_match, game_number=1, winner=champion_team
    )
    final_round = GameRound.objects.create(
        match=champ_match,
        round_number=1,
        team_red=teams[0],
        team_blue=teams[1],
        red_points=100,
        blue_points=60,
        is_completed=True,
    )
    for player, role, kwargs in champion_player_states or []:
        PlayerRoundState.objects.create(
            game_round=final_round,
            player=player,
            team_color="red",
            role=role,
            **kwargs,
        )

    phase = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament", tournament=tournament
    )
    return tournament, champ_match, final_round


def _url(season_id: int) -> str:
    return reverse("season_awards", args=[season_id])


# ===========================================================================
# Routing / method / 404
# ===========================================================================


class TestSeasonAwardsRouting(TestCase):
    def setUp(self) -> None:
        self.league = _make_league()
        self.teams = _make_teams("R", 2)
        self.season = _make_completed_season(self.league, teams=self.teams)

    def test_reverse_resolves_to_locked_path(self) -> None:
        self.assertEqual(_url(self.season.id), f"/seasons/{self.season.id}/awards/")

    def test_get_returns_200(self) -> None:
        response = self.client.get(_url(self.season.id))
        self.assertEqual(response.status_code, 200)

    def test_missing_season_returns_404(self) -> None:
        response = self.client.get(_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_post_returns_405(self) -> None:
        response = self.client.post(_url(self.season.id))
        self.assertEqual(response.status_code, 405)


# ===========================================================================
# Session write
# ===========================================================================


class TestSeasonAwardsSessionWrite(TestCase):
    def test_get_writes_last_league_id(self) -> None:
        league = _make_league("SessAw")
        teams = _make_teams("SW", 2)
        season = _make_completed_season(league, teams=teams)
        self.client.get(_url(season.id))
        self.assertEqual(self.client.session.get("last_league_id"), league.id)


# ===========================================================================
# Empty-state — no completed regular-season rounds
# ===========================================================================


class TestSeasonAwardsEmptyState(TestCase):
    def test_no_rr_rounds_renders_empty_notice(self) -> None:
        league = _make_league("EmptyAw")
        teams = _make_teams("EA", 2)
        season = _make_completed_season(league, teams=teams)
        # No Matches/GameRounds at all — no regular-season corpus.
        response = self.client.get(_url(season.id))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "season-awards-empty-notice")

    def test_empty_state_omits_awards_table(self) -> None:
        league = _make_league("EmptyAwTbl")
        teams = _make_teams("EAT", 2)
        season = _make_completed_season(league, teams=teams)
        response = self.client.get(_url(season.id))
        self.assertNotContains(response, 'id="season-awards-table"')


# ===========================================================================
# Body — LOCKED DOM ids present when there is a corpus
# ===========================================================================


class TestSeasonAwardsDomIds(TestCase):
    def setUp(self) -> None:
        self.league = _make_league("DomAw")
        self.teams = _make_teams("DA", 2)
        self.season = _make_completed_season(self.league, teams=self.teams)
        self.team_a, self.team_b = self.teams
        # One PRS per role so every kd_by_role slot has a winner.
        self.players_a = self.team_a.active_players
        self.players_b = self.team_b.active_players
        states = []
        for i, role in enumerate(ROLES):
            states.append(
                (
                    self.players_a[i],
                    "red",
                    role,
                    {
                        "points_scored": 500 - i,
                        "tags_made": 10,
                        "times_tagged": 2,
                        "shots_missed": 5,
                        "resupplies_given": 8 if role == "medic" else 0,
                        "specials_used": 4 if role == "commander" else 0,
                        "own_specials_cancelled": 1 if role == "commander" else 0,
                    },
                )
            )
        _reg_round_with_states(self.season, self.team_a, self.team_b, states)

    def test_awards_table_present(self) -> None:
        response = self.client.get(_url(self.season.id))
        self.assertContains(response, 'id="season-awards-table"')

    def test_named_award_dom_ids_present(self) -> None:
        content = self.client.get(_url(self.season.id)).content.decode()
        for dom_id in (
            "season-awards-most-points",
            "season-awards-best-accuracy",
            "season-awards-best-medic",
            "season-awards-most-efficient-nuke",
            "season-awards-season-mvp",
            "season-awards-finals-mvp",
        ):
            self.assertIn(dom_id, content)

    def test_five_kd_by_role_dom_ids_present(self) -> None:
        content = self.client.get(_url(self.season.id)).content.decode()
        for role in ROLES:
            self.assertIn(f"season-awards-kd-{role}", content)


# ===========================================================================
# Awards context shape + winner identity
# ===========================================================================


class TestSeasonAwardsContext(TestCase):
    def setUp(self) -> None:
        self.league = _make_league("CtxAw")
        self.teams = _make_teams("CA", 2)
        self.season = _make_completed_season(self.league, teams=self.teams)
        self.team_a, self.team_b = self.teams
        self.star = self.team_a.active_players[0]
        self.weak = self.team_b.active_players[0]
        _reg_round_with_states(
            self.season,
            self.team_a,
            self.team_b,
            [
                (self.star, "red", "scout", {"points_scored": 5000, "tags_made": 30}),
                (self.weak, "blue", "scout", {"points_scored": 100, "tags_made": 2}),
            ],
        )

    def test_awards_context_present(self) -> None:
        response = self.client.get(_url(self.season.id))
        self.assertIn("awards", response.context)

    def test_most_points_winner_is_the_star(self) -> None:
        response = self.client.get(_url(self.season.id))
        awards = response.context["awards"]
        self.assertIsNotNone(awards.most_points)
        self.assertEqual(awards.most_points.player_id, self.star.id)

    def test_kd_by_role_has_five_keys(self) -> None:
        response = self.client.get(_url(self.season.id))
        awards = response.context["awards"]
        self.assertEqual(set(awards.kd_by_role), set(ROLES))
        self.assertEqual(len(awards.kd_by_role), 5)


# ===========================================================================
# Finals MVP — set on a bracket-format playoff phase
# ===========================================================================


class TestSeasonAwardsFinalsMvpBracket(TestCase):
    def _build(self, fmt: str = "single_elimination"):
        league = _make_league(f"Fin{fmt[:4]}")
        teams = _make_teams(f"F{fmt[:3]}", 2)
        season = _make_completed_season(league, teams=teams)
        # A regular-season round so the page is not empty-state.
        _reg_round_with_states(
            season,
            teams[0],
            teams[1],
            [(teams[0].active_players[0], "red", "scout", {"points_scored": 200})],
        )
        finalist = teams[0].active_players[1]
        _bracket_playoff_phase(
            season,
            teams,
            fmt=fmt,
            champion_player_states=[
                (finalist, "heavy", {"tags_made": 20, "shots_missed": 1}),
            ],
        )
        return season, finalist

    def test_finals_mvp_set_for_single_elimination(self) -> None:
        season, finalist = self._build("single_elimination")
        response = self.client.get(_url(season.id))
        awards = response.context["awards"]
        self.assertIsNotNone(awards.finals_mvp)
        self.assertEqual(awards.finals_mvp.player_id, finalist.id)

    def test_finals_mvp_set_for_double_elimination(self) -> None:
        season, finalist = self._build("double_elimination")
        response = self.client.get(_url(season.id))
        awards = response.context["awards"]
        self.assertIsNotNone(awards.finals_mvp)
        self.assertEqual(awards.finals_mvp.player_id, finalist.id)


# ===========================================================================
# Finals MVP — None for round_robin / swiss / no playoff
# ===========================================================================


class TestSeasonAwardsFinalsMvpNone(TestCase):
    def _season_with_rr_round(self, prefix: str):
        league = _make_league(prefix)
        teams = _make_teams(prefix, 2)
        season = _make_completed_season(league, teams=teams)
        _reg_round_with_states(
            season,
            teams[0],
            teams[1],
            [(teams[0].active_players[0], "red", "scout", {"points_scored": 200})],
        )
        return season, teams

    def test_no_playoff_phase_finals_mvp_none(self) -> None:
        season, _teams = self._season_with_rr_round("NoPlay")
        response = self.client.get(_url(season.id))
        awards = response.context["awards"]
        self.assertIsNone(awards.finals_mvp)

    def test_round_robin_playoff_finals_mvp_none(self) -> None:
        season, teams = self._season_with_rr_round("RRPlay")
        finalist = teams[0].active_players[1]
        _bracket_playoff_phase(
            season,
            teams,
            fmt="round_robin",
            champion_player_states=[(finalist, "heavy", {"tags_made": 20})],
        )
        response = self.client.get(_url(season.id))
        awards = response.context["awards"]
        self.assertIsNone(awards.finals_mvp)

    def test_swiss_playoff_finals_mvp_none(self) -> None:
        season, teams = self._season_with_rr_round("SwPlay")
        finalist = teams[0].active_players[1]
        _bracket_playoff_phase(
            season,
            teams,
            fmt="swiss",
            champion_player_states=[(finalist, "heavy", {"tags_made": 20})],
        )
        response = self.client.get(_url(season.id))
        awards = response.context["awards"]
        self.assertIsNone(awards.finals_mvp)

    def test_finals_mvp_dom_id_present_even_when_none(self) -> None:
        # The -finals-mvp row renders with an em-dash when None.
        season, _teams = self._season_with_rr_round("NoneDom")
        content = self.client.get(_url(season.id)).content.decode()
        self.assertIn("season-awards-finals-mvp", content)
