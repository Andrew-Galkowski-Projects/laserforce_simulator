"""LG-03 — Django ``TestCase`` tests for the Season Awards view + cache.

The seam contract is locked at ``.claude/worktrees/lg-03-seam-contract.md``
(§3 cache semantics, §4 view / URL / template, §4.3 GameEvent nuke scan,
§4.4 Finals FK-chain, §8.2 test boundary). The view is read-only at
``GET /seasons/<int:season_id>/awards/`` (URL name ``season_awards``),
renders the 6-category award table + the two headline slots, and warms
``Season.season_awards_json`` lazily on the first GET of a completed Season.

Tests hand-construct ``Match`` / ``GameRound`` / ``PlayerRoundState`` /
``GameEvent`` rows + the full playoff FK chain — LG-03 runs NO simulation, so
the simulator is never entered. Assertions are on winner IDENTITY / category /
DOM ids / cache state / absence — NEVER on exact simulated point totals (the
contract forbids it; playoff sims are non-deterministic).

These FAIL until the Code agent lands the view + URL + template + the
``Season.get_or_compute_awards`` chokepoint + the ``season_awards_json`` field.
"""

from __future__ import annotations

from datetime import date
from unittest import mock

from django.test import TestCase
from django.urls import reverse

from matches.models import (
    BracketNode,
    GameEvent,
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

# ---------------------------------------------------------------------------
# Fixtures / helpers — hand-construct League / Season / Match rows.
# ---------------------------------------------------------------------------


def _make_league(name: str = "AwardsLeague") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _make_teams(prefix: str, n: int) -> list[Team]:
    teams = []
    for i in range(n):
        t, _players = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
    return teams


def _make_completed_season(
    league: League,
    *,
    name: str = "S1",
    teams: list[Team] | None = None,
) -> Season:
    """A ``completed`` Season with its team-id snapshot set."""
    team_ids = sorted(t.id for t in teams) if teams else []
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=date(2026, 1, 1),
        state="completed",
        starting_team_ids_json=team_ids,
    )
    if teams:
        season.teams.add(*teams)
    return season


def _make_draft_season(league: League, *, name: str = "Draft") -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2027, 1, 1),
        state="draft",
    )


def _make_active_season(league: League, *, name: str = "Active") -> Season:
    return Season.objects.create(
        league=league,
        name=name,
        start_date=date(2027, 1, 1),
        state="active",
    )


def _completed_round(
    season: Season,
    team_red: Team,
    team_blue: Team,
    *,
    round_number: int = 1,
) -> GameRound:
    """Persist a completed Match + GameRound under ``season``."""
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=season,
        is_completed=True,
    )
    return GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=round_number,
        red_points=100,
        blue_points=80,
        is_completed=True,
    )


def _prs(
    game_round: GameRound,
    player,
    *,
    color: str = "red",
    role: str = "scout",
    **stats,
) -> PlayerRoundState:
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color=color,
        role=role,
        **stats,
    )


def _awards_url(season_id: int) -> str:
    return reverse("season_awards", args=[season_id])


# ===========================================================================
# Routing / method / 404 / session
# ===========================================================================


class TestSeasonAwardsRouting(TestCase):
    def test_reverse_resolves_to_expected_path(self) -> None:
        league = _make_league("Route")
        season = _make_completed_season(league)
        self.assertEqual(
            reverse("season_awards", args=[season.id]),
            f"/seasons/{season.id}/awards/",
        )

    def test_get_returns_200_for_completed_season(self) -> None:
        league = _make_league("Route200")
        teams = _make_teams("R2", 4)
        season = _make_completed_season(league, teams=teams)
        response = self.client.get(_awards_url(season.id))
        self.assertEqual(response.status_code, 200)

    def test_post_returns_405(self) -> None:
        league = _make_league("Route405")
        season = _make_completed_season(league)
        response = self.client.post(_awards_url(season.id))
        self.assertEqual(response.status_code, 405)

    def test_missing_season_returns_404(self) -> None:
        response = self.client.get(_awards_url(999999))
        self.assertEqual(response.status_code, 404)

    def test_get_writes_last_league_id_to_session(self) -> None:
        league = _make_league("RouteSess")
        season = _make_completed_season(league)
        self.client.get(_awards_url(season.id))
        self.assertEqual(self.client.session["last_league_id"], league.id)

    def test_404_does_not_write_session(self) -> None:
        self.client.get(_awards_url(999999))
        self.assertNotIn("last_league_id", self.client.session)


# ===========================================================================
# DOM ids on a completed Season
# ===========================================================================


class TestSeasonAwardsDomIds(TestCase):
    def setUp(self) -> None:
        self.league = _make_league("DomLeague")
        self.teams = _make_teams("Dom", 4)
        self.season = _make_completed_season(self.league, teams=self.teams)
        ta, tb = self.teams[0], self.teams[1]
        gr = _completed_round(self.season, ta, tb)
        _prs(
            gr,
            ta.slot_scout_1,
            color="red",
            role="scout",
            points_scored=500,
            tags_made=12,
            times_tagged=3,
        )

    def test_root_container_present(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        self.assertIn('id="season-awards"', content)

    def test_table_present(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        self.assertIn('id="season-awards-table"', content)

    def test_per_category_containers_present(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        for key in (
            "most_points",
            "tag_ratio",
            "most_resupplies",
            "longest_survival",
            "most_efficient_nuke",
            "best_accuracy",
        ):
            self.assertIn(f'id="season-awards-category-{key}"', content)

    def test_mvp_headline_slot_present(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        self.assertIn('id="season-awards-mvp"', content)

    def test_finals_mvp_headline_slot_present(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        self.assertIn('id="season-awards-finals-mvp"', content)

    def test_not_yet_absent_on_awarded_season(self) -> None:
        content = self.client.get(_awards_url(self.season.id)).content.decode()
        self.assertNotIn('id="season-awards-not-yet"', content)


# ===========================================================================
# Not-yet-awarded empty state for a draft / active Season
# ===========================================================================


class TestSeasonAwardsNotYet(TestCase):
    def test_draft_season_shows_not_yet(self) -> None:
        league = _make_league("DraftNotYet")
        season = _make_draft_season(league)
        content = self.client.get(_awards_url(season.id)).content.decode()
        self.assertIn('id="season-awards-not-yet"', content)
        self.assertNotIn('id="season-awards-table"', content)

    def test_active_season_shows_not_yet(self) -> None:
        league = _make_league("ActiveNotYet")
        season = _make_active_season(league)
        content = self.client.get(_awards_url(season.id)).content.decode()
        self.assertIn('id="season-awards-not-yet"', content)


# ===========================================================================
# Cache semantics — warm on first GET, read on second, never warm draft
# ===========================================================================


class TestSeasonAwardsCache(TestCase):
    def test_first_get_warms_cache(self) -> None:
        league = _make_league("CacheWarm")
        teams = _make_teams("CW", 4)
        season = _make_completed_season(league, teams=teams)
        gr = _completed_round(season, teams[0], teams[1])
        _prs(gr, teams[0].slot_scout_1, role="scout", points_scored=300)
        self.assertIsNone(season.season_awards_json)

        self.client.get(_awards_url(season.id))

        season.refresh_from_db()
        self.assertIsNotNone(season.season_awards_json)
        self.assertIsInstance(season.season_awards_json, dict)

    def test_second_get_reads_cache_without_recompute(self) -> None:
        league = _make_league("CacheRead")
        teams = _make_teams("CR", 4)
        season = _make_completed_season(league, teams=teams)
        gr = _completed_round(season, teams[0], teams[1])
        _prs(gr, teams[0].slot_scout_1, role="scout", points_scored=300)

        # First GET warms the cache (compute path runs once).
        self.client.get(_awards_url(season.id))

        # Spy on the pure compute path; a second GET must NOT recompute.
        with mock.patch("matches.season_awards.compute_season_awards") as spy:
            self.client.get(_awards_url(season.id))
        spy.assert_not_called()

    def test_draft_season_never_warms_cache(self) -> None:
        league = _make_league("CacheDraft")
        season = _make_draft_season(league)
        self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        self.assertIsNone(season.season_awards_json)

    def test_active_season_never_warms_cache(self) -> None:
        league = _make_league("CacheActive")
        season = _make_active_season(league)
        self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        self.assertIsNone(season.season_awards_json)


# ===========================================================================
# GameEvent nuke scan — Commander actor wins Most Efficient Nuke
# ===========================================================================


class TestSeasonAwardsNukeScan(TestCase):
    def test_nuke_actor_commander_wins_most_efficient_nuke(self) -> None:
        league = _make_league("NukeLeague")
        teams = _make_teams("Nuke", 4)
        season = _make_completed_season(league, teams=teams)
        ta, tb = teams[0], teams[1]
        gr = _completed_round(season, ta, tb)
        commander = ta.slot_commander
        opp = tb.slot_scout_1
        # The Commander must appear in the round corpus as a commander.
        _prs(gr, commander, color="red", role="commander", points_scored=200)
        _prs(gr, opp, color="blue", role="scout", points_scored=100)

        # Two nuke-eliminations by this Commander (actor=Commander).
        for tick in (500, 900):
            GameEvent.objects.create(
                game_round=gr,
                timestamp=tick,
                event_type="elimination",
                actor=commander,
                target=opp,
                metadata={"elimination_action": "nuke"},
            )

        response = self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        win = season.season_awards_json["most_efficient_nuke"]
        self.assertIsNotNone(win)
        self.assertEqual(win["player_id"], commander.id)
        self.assertEqual(response.status_code, 200)

    def test_non_nuke_eliminations_ignored(self) -> None:
        league = _make_league("NukeIgnore")
        teams = _make_teams("NI", 4)
        season = _make_completed_season(league, teams=teams)
        ta, tb = teams[0], teams[1]
        gr = _completed_round(season, ta, tb)
        commander = ta.slot_commander
        opp = tb.slot_scout_1
        _prs(gr, commander, color="red", role="commander")
        _prs(gr, opp, color="blue", role="scout")
        # A plain (non-nuke) elimination must NOT count toward the nuke award.
        GameEvent.objects.create(
            game_round=gr,
            timestamp=300,
            event_type="elimination",
            actor=commander,
            target=opp,
            metadata={},
        )
        self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        self.assertIsNone(season.season_awards_json["most_efficient_nuke"])


# ===========================================================================
# Finals FK-chain resolution
# ===========================================================================


def _build_single_elim_final_node(
    tournament: Tournament,
    *,
    team_a: Team,
    team_b: Team,
    winner: Team,
) -> BracketNode:
    """A single deciding node (advances_to=None) with two participants."""
    TournamentParticipant.objects.create(tournament=tournament, team=team_a, seed=1)
    TournamentParticipant.objects.create(tournament=tournament, team=team_b, seed=2)
    node = BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=0,
        team_a=team_a,
        team_b=team_b,
        seed_a=1,
        seed_b=2,
        advances_to=None,
        bracket_type="winners",
        winner=winner,
    )
    return node


def _playoff_round_for_node(
    node: BracketNode,
    *,
    team_red: Team,
    team_blue: Team,
) -> GameRound:
    """A playoff Match (season=NULL) wired to ``node`` via SeriesMatch."""
    match = Match.objects.create(
        team_red=team_red,
        team_blue=team_blue,
        season=None,  # playoff Matches keep season=NULL
        is_completed=True,
    )
    SeriesMatch.objects.create(node=node, match=match, game_number=1, winner=team_red)
    return GameRound.objects.create(
        match=match,
        team_red=team_red,
        team_blue=team_blue,
        round_number=1,
        red_points=120,
        blue_points=90,
        is_completed=True,
    )


class TestSeasonAwardsFinalsFkChain(TestCase):
    def test_finals_mvp_is_champion_team_player_over_deciding_node(self) -> None:
        league = _make_league("FinalsLeague")
        teams = _make_teams("Fin", 4)
        season = _make_completed_season(league, teams=teams)
        champion, runner_up = teams[0], teams[1]
        season.champion_team = champion
        season.save(update_fields=["champion_team"])

        # A regular-season round so the rest of the awards have a corpus.
        rs = _completed_round(season, champion, runner_up)
        _prs(rs, champion.slot_scout_1, role="scout", points_scored=300)

        # Full embedded playoff FK chain:
        # SeasonPhase(tournament=…) -> Tournament(season_phases non-empty)
        #   -> BracketNode(advances_to=None) -> SeriesMatch -> Match(season=NULL)
        #   -> GameRound.
        tournament = Tournament.objects.create(
            name="Playoffs", format="single_elimination", state="completed"
        )
        SeasonPhase.objects.create(
            season=season,
            ordinal=2,
            phase_type="tournament",
            tournament=tournament,
        )
        node = _build_single_elim_final_node(
            tournament, team_a=champion, team_b=runner_up, winner=champion
        )
        gr = _playoff_round_for_node(node, team_red=champion, team_blue=runner_up)
        champ_player = champion.slot_heavy
        other_player = runner_up.slot_heavy
        # The champ player is the SOLE champion-team finalist; the Finals MVP
        # filters to the champion team, so a losing-team finalist is excluded
        # regardless of its (computed) MVP — the champ player wins by identity.
        _prs(gr, champ_player, color="red", role="heavy", points_scored=400)
        _prs(gr, other_player, color="blue", role="heavy", points_scored=900)

        self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        finals = season.season_awards_json["finals_mvp"]
        self.assertIsNotNone(finals)
        self.assertEqual(finals["player_id"], champ_player.id)

    def test_finals_mvp_absent_for_rr_only_season(self) -> None:
        league = _make_league("RrOnly")
        teams = _make_teams("RR", 4)
        season = _make_completed_season(league, teams=teams)
        gr = _completed_round(season, teams[0], teams[1])
        _prs(gr, teams[0].slot_scout_1, role="scout", points_scored=300)
        self.client.get(_awards_url(season.id))
        season.refresh_from_db()
        self.assertIsNone(season.season_awards_json["finals_mvp"])
