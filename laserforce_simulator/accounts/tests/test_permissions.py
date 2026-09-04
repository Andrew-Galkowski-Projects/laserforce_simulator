"""UX-01 — the permission seam (`accounts.permissions`).

Seam contract: `.claude/worktrees/ux-01-seam-contract.md` §3 (the whole public
surface), §3.1 (the ownership-root traversal table) and §11.3 (test boundary).

Boundary discipline, per §11.3 — this module asserts **behaviour** only:

* traversal is exercised through `ownership_root`; the module-private
  `_PARENT_FIELD` dict, `_MAX_TRAVERSAL_DEPTH` and `_has_manager` are never
  imported or asserted against;
* `owned_match_q` / `owned_game_round_q` are asserted on the **rows they
  return**, never on the shape of the returned `Q` tree;
* no query counts, no SQL text, no `str(queryset.query)`.

Two-Account isolation is the core of this file: every access rule is proved in
**both** directions (A cannot reach B's row, B cannot reach A's row) so a
one-sided bug cannot hide.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import Http404
from django.test import RequestFactory, TestCase

from accounts.permissions import (
    ROOT_MODELS,
    get_owned_or_404,
    is_owned_by,
    manager_or_none,
    owned_game_round_q,
    owned_match_q,
    owned_queryset,
    ownership_root,
    stamp_manager,
)
from core.models import ArenaMap, MapBaseConfig, MapZoneConfig
from matches.models import (
    BracketNode,
    Conference,
    GameEvent,
    GameRound,
    League,
    Match,
    OwnerEvaluation,
    PlayerRoundState,
    PlayerSeasonRating,
    Season,
    SeasonPhase,
    SeriesMatch,
    TeamSeasonFinance,
    Tournament,
    TournamentParticipant,
    TournamentPlayerEntry,
)
from teams.models import Player, Team

User = get_user_model()

# The 19 per-season stat columns on `PlayerSeasonRating`, all required.
_RATING_STATS: tuple[str, ...] = (
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


def _rating_kwargs() -> dict[str, Any]:
    kwargs: dict[str, Any] = {stat: 50 for stat in _RATING_STATS}
    kwargs["overall_rating"] = 50.0
    return kwargs


class OwnershipTestBase(TestCase):
    """Two Accounts, A and B, plus a request factory."""

    def setUp(self) -> None:
        super().setUp()
        self.user_a = User.objects.create_user(
            email="account-a@example.com", password="pw-A-12345"
        )
        self.user_b = User.objects.create_user(
            email="account-b@example.com", password="pw-B-12345"
        )
        self.factory = RequestFactory()

    def request_for(self, user: Any) -> Any:
        request = self.factory.get("/")
        request.user = user
        return request


# ---------------------------------------------------------------------------
# ROOT_MODELS
# ---------------------------------------------------------------------------


class TestRootModels(OwnershipTestBase):
    """§3 — the five Ownership roots, in contract order."""

    def test_root_models_is_the_five_roots_in_contract_order(self) -> None:
        self.assertEqual(
            tuple(ROOT_MODELS), (Team, League, Tournament, Match, GameRound)
        )

    def test_every_root_model_carries_a_manager_field(self) -> None:
        for model in ROOT_MODELS:
            with self.subTest(model=model.__name__):
                field = model._meta.get_field("manager")
                self.assertTrue(
                    field.null, f"{model.__name__}.manager must be nullable"
                )

    def test_arena_map_is_not_a_root_model(self) -> None:
        """§2.4 — `ArenaMap` is deliberately NOT an Ownership root."""
        self.assertNotIn(ArenaMap, ROOT_MODELS)


# ---------------------------------------------------------------------------
# ownership_root — one case per row kind in the §3.1 table
# ---------------------------------------------------------------------------


class TestOwnershipRootAlwaysRoots(OwnershipTestBase):
    """Team / League / Tournament have no parent FK — always their own root."""

    def test_team_is_its_own_root(self) -> None:
        team = Team.objects.create(name="Root Team", manager=self.user_a)
        self.assertEqual(ownership_root(team), team)

    def test_league_is_its_own_root(self) -> None:
        league = League.objects.create(name="Root League", manager=self.user_a)
        self.assertEqual(ownership_root(league), league)

    def test_tournament_is_its_own_root(self) -> None:
        tourney = Tournament.objects.create(name="Root Cup", manager=self.user_a)
        self.assertEqual(ownership_root(tourney), tourney)

    def test_unmanaged_root_still_resolves_to_itself(self) -> None:
        """A NULL `manager` does not make a root stop being a root."""
        team = Team.objects.create(name="Unmanaged Team")
        self.assertEqual(ownership_root(team), team)
        self.assertIsNone(team.manager_id)


class TestOwnershipRootTournamentEmbedded(OwnershipTestBase):
    """§2.3 — an embedded Tournament is STILL its own root."""

    def test_tournament_with_season_phase_is_still_its_own_root(self) -> None:
        league = League.objects.create(name="Embed League", manager=self.user_a)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        phase = SeasonPhase.objects.create(season=season, ordinal=1)
        tourney = Tournament.objects.create(
            name="Embedded Cup", season_phase=phase, manager=self.user_a
        )
        self.assertEqual(ownership_root(tourney), tourney)

    def test_embedded_tournament_root_is_not_the_league(self) -> None:
        league = League.objects.create(name="Embed League 2", manager=self.user_a)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        phase = SeasonPhase.objects.create(season=season, ordinal=1)
        tourney = Tournament.objects.create(
            name="Embedded Cup 2", season_phase=phase, manager=self.user_a
        )
        self.assertNotEqual(ownership_root(tourney), league)


class TestOwnershipRootConditionalRoots(OwnershipTestBase):
    """Match and GameRound are roots exactly when their parent FK is NULL."""

    def setUp(self) -> None:
        super().setUp()
        self.red = Team.objects.create(name="Cond Red")
        self.blue = Team.objects.create(name="Cond Blue")
        self.league = League.objects.create(name="Cond League", manager=self.user_a)
        self.season = Season.objects.create(
            league=self.league, name="S1", start_date=date(2026, 1, 1)
        )

    def test_sandbox_match_is_its_own_root(self) -> None:
        match = Match.objects.create(team_red=self.red, team_blue=self.blue)
        self.assertIsNone(match.season_id)
        self.assertEqual(ownership_root(match), match)

    def test_season_match_root_is_the_league(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season
        )
        self.assertEqual(ownership_root(match), self.league)

    def test_standalone_game_round_is_its_own_root(self) -> None:
        game_round = GameRound.objects.create(round_number=1)
        self.assertIsNone(game_round.match_id)
        self.assertEqual(ownership_root(game_round), game_round)

    def test_game_round_of_sandbox_match_roots_at_the_match(self) -> None:
        match = Match.objects.create(team_red=self.red, team_blue=self.blue)
        game_round = GameRound.objects.create(round_number=1, match=match)
        self.assertEqual(ownership_root(game_round), match)

    def test_game_round_of_season_match_roots_at_the_league(self) -> None:
        match = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season
        )
        game_round = GameRound.objects.create(round_number=1, match=match)
        self.assertEqual(ownership_root(game_round), self.league)


class TestOwnershipRootDerivedRows(OwnershipTestBase):
    """Every `manager`-less row in the §3.1 table traverses to a root."""

    def setUp(self) -> None:
        super().setUp()
        self.team = Team.objects.create(name="Derived Team", manager=self.user_a)
        self.player = Player.objects.create(team=self.team, name="Derived Player")
        self.league = League.objects.create(name="Derived League", manager=self.user_a)
        self.season = Season.objects.create(
            league=self.league, name="S1", start_date=date(2026, 1, 1)
        )
        self.tourney = Tournament.objects.create(
            name="Derived Cup", manager=self.user_a
        )

    def test_player_roots_at_its_team(self) -> None:
        self.assertEqual(ownership_root(self.player), self.team)

    def test_season_roots_at_its_league(self) -> None:
        self.assertEqual(ownership_root(self.season), self.league)

    def test_conference_roots_at_the_league(self) -> None:
        conference = Conference.objects.create(
            season=self.season, name="East", ordinal=1
        )
        self.assertEqual(ownership_root(conference), self.league)

    def test_season_phase_roots_at_the_league(self) -> None:
        phase = SeasonPhase.objects.create(season=self.season, ordinal=1)
        self.assertEqual(ownership_root(phase), self.league)

    def test_player_season_rating_roots_at_the_league_not_the_team(self) -> None:
        """§3.1 — `PlayerSeasonRating.season` is the parent, not `.player`."""
        rating = PlayerSeasonRating.objects.create(
            player=self.player, season=self.season, **_rating_kwargs()
        )
        self.assertEqual(ownership_root(rating), self.league)
        self.assertNotEqual(ownership_root(rating), self.team)

    def test_owner_evaluation_roots_at_the_league(self) -> None:
        """The fictional **Owner** row still derives its **Manager** by FK."""
        evaluation = OwnerEvaluation.objects.create(
            league=self.league,
            season=self.season,
            wins_delta=0.0,
            playoffs_delta=0.0,
            wins_total=0.0,
            playoffs_total=0.0,
        )
        self.assertEqual(ownership_root(evaluation), self.league)

    def test_team_season_finance_roots_at_the_league_not_the_team(self) -> None:
        """§3.1 — `TeamSeasonFinance.season` is the parent, not `.team`."""
        finance = TeamSeasonFinance.objects.create(season=self.season, team=self.team)
        self.assertEqual(ownership_root(finance), self.league)

    def test_player_round_state_roots_through_its_game_round(self) -> None:
        game_round = GameRound.objects.create(round_number=1, manager=self.user_a)
        state = PlayerRoundState.objects.create(
            game_round=game_round, player=self.player
        )
        self.assertEqual(ownership_root(state), game_round)

    def test_game_event_roots_through_its_game_round(self) -> None:
        game_round = GameRound.objects.create(round_number=1, manager=self.user_a)
        event = GameEvent.objects.create(
            game_round=game_round,
            timestamp=1,
            actor=self.player,
            event_type="tagged",
        )
        self.assertEqual(ownership_root(event), game_round)

    def test_game_event_roots_all_the_way_to_the_league(self) -> None:
        """The deepest real chain: GameEvent -> GameRound -> Match -> Season -> League."""
        red = Team.objects.create(name="Deep Red")
        blue = Team.objects.create(name="Deep Blue")
        match = Match.objects.create(team_red=red, team_blue=blue, season=self.season)
        game_round = GameRound.objects.create(round_number=1, match=match)
        event = GameEvent.objects.create(
            game_round=game_round,
            timestamp=1,
            actor=self.player,
            event_type="tagged",
        )
        self.assertEqual(ownership_root(event), self.league)

    def test_tournament_participant_roots_at_the_tournament(self) -> None:
        participant = TournamentParticipant.objects.create(
            tournament=self.tourney, team=self.team, seed=1
        )
        self.assertEqual(ownership_root(participant), self.tourney)

    def test_bracket_node_roots_at_the_tournament(self) -> None:
        node = BracketNode.objects.create(
            tournament=self.tourney, bracket_round=1, position=0
        )
        self.assertEqual(ownership_root(node), self.tourney)

    def test_series_match_roots_through_its_node(self) -> None:
        node = BracketNode.objects.create(
            tournament=self.tourney, bracket_round=1, position=0
        )
        series = SeriesMatch.objects.create(node=node, game_number=1)
        self.assertEqual(ownership_root(series), self.tourney)

    def test_tournament_player_entry_roots_at_the_tournament(self) -> None:
        entry = TournamentPlayerEntry.objects.create(
            tournament=self.tourney, player=self.player
        )
        self.assertEqual(ownership_root(entry), self.tourney)


class TestOwnershipRootNoAxis(OwnershipTestBase):
    """§2.4 / §3.1 — map rows have no ownership axis at all."""

    def _make_map(self, name: str = "NoAxis") -> ArenaMap:
        return ArenaMap.objects.create(name=name, image="maps/noaxis.png")

    def test_arena_map_has_no_ownership_root(self) -> None:
        self.assertIsNone(ownership_root(self._make_map()))

    def test_map_zone_config_has_no_ownership_root(self) -> None:
        arena = self._make_map("NoAxisZone")
        config = MapZoneConfig.objects.create(
            arena_map=arena, zone_size=50, zone_data={}
        )
        self.assertIsNone(ownership_root(config))

    def test_map_base_config_has_no_ownership_root(self) -> None:
        arena = self._make_map("NoAxisBase")
        config = MapBaseConfig.objects.create(
            arena_map=arena, base_type="red", x_px=1, y_px=1
        )
        self.assertIsNone(ownership_root(config))


class TestOwnershipRootSeasonDeletionDemotesMatch(OwnershipTestBase):
    """§3.1 — `Match.season` is SET_NULL: a deleted Season demotes its Matches
    to Unmanaged sandbox roots. Accepted behaviour, asserted so it stays known.
    """

    def test_deleting_the_season_makes_the_match_an_unmanaged_root(self) -> None:
        league = League.objects.create(name="Doomed League", manager=self.user_a)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        red = Team.objects.create(name="Doom Red")
        blue = Team.objects.create(name="Doom Blue")
        match = Match.objects.create(team_red=red, team_blue=blue, season=season)
        self.assertEqual(ownership_root(match), league)

        season.delete()
        match.refresh_from_db()

        self.assertIsNone(match.season_id)
        self.assertEqual(ownership_root(match), match)
        self.assertIsNone(match.manager_id)
        # Unmanaged => reachable by BOTH Accounts.
        self.assertTrue(is_owned_by(match, self.user_a))
        self.assertTrue(is_owned_by(match, self.user_b))


# ---------------------------------------------------------------------------
# is_owned_by
# ---------------------------------------------------------------------------


class TestIsOwnedBy(OwnershipTestBase):
    """§3.3 — the read+write predicate, proved in both directions."""

    def test_owner_may_access_their_own_root(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        self.assertTrue(is_owned_by(team, self.user_a))

    def test_other_account_may_not_access_that_root(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        self.assertFalse(is_owned_by(team, self.user_b))

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        team_b = Team.objects.create(name="B Team", manager=self.user_b)
        self.assertTrue(is_owned_by(team_b, self.user_b))
        self.assertFalse(is_owned_by(team_b, self.user_a))

    def test_unmanaged_row_is_accessible_to_every_account(self) -> None:
        team = Team.objects.create(name="Open Team")
        self.assertTrue(is_owned_by(team, self.user_a))
        self.assertTrue(is_owned_by(team, self.user_b))

    def test_row_with_no_ownership_axis_is_always_accessible(self) -> None:
        arena = ArenaMap.objects.create(name="Shared", image="maps/shared.png")
        self.assertTrue(is_owned_by(arena, self.user_a))
        self.assertTrue(is_owned_by(arena, self.user_b))

    def test_anonymous_user_is_refused_a_managed_row(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        self.assertFalse(is_owned_by(team, AnonymousUser()))

    def test_none_user_is_refused_a_managed_row(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        self.assertFalse(is_owned_by(team, None))

    def test_derived_row_inherits_the_roots_verdict_both_ways(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        player = Player.objects.create(team=team, name="A Player")
        self.assertTrue(is_owned_by(player, self.user_a))
        self.assertFalse(is_owned_by(player, self.user_b))

    def test_season_match_inherits_its_leagues_verdict(self) -> None:
        league = League.objects.create(name="A League", manager=self.user_a)
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        red = Team.objects.create(name="Sm Red")
        blue = Team.objects.create(name="Sm Blue")
        match = Match.objects.create(team_red=red, team_blue=blue, season=season)
        self.assertTrue(is_owned_by(match, self.user_a))
        self.assertFalse(is_owned_by(match, self.user_b))


# ---------------------------------------------------------------------------
# get_owned_or_404
# ---------------------------------------------------------------------------


class TestGetOwnedOr404(OwnershipTestBase):
    """§3.3 — 404, never 403; returns the ROW, not its root."""

    def test_returns_the_row_for_its_own_account(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        got = get_owned_or_404(Team, self.request_for(self.user_a), pk=team.pk)
        self.assertEqual(got, team)

    def test_returns_the_row_not_the_root_for_a_derived_row(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        player = Player.objects.create(team=team, name="A Player")
        got = get_owned_or_404(Player, self.request_for(self.user_a), pk=player.pk)
        self.assertEqual(got, player)
        self.assertNotEqual(got, team)

    def test_other_account_gets_404_not_403(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        with self.assertRaises(Http404):
            get_owned_or_404(Team, self.request_for(self.user_b), pk=team.pk)

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        team_b = Team.objects.create(name="B Team", manager=self.user_b)
        with self.assertRaises(Http404):
            get_owned_or_404(Team, self.request_for(self.user_a), pk=team_b.pk)

    def test_missing_row_also_raises_http404(self) -> None:
        """Another Account's row must be indistinguishable from a missing one."""
        with self.assertRaises(Http404):
            get_owned_or_404(Team, self.request_for(self.user_a), pk=999_999)

    def test_unmanaged_row_is_returned_to_any_account(self) -> None:
        team = Team.objects.create(name="Open Team")
        self.assertEqual(
            get_owned_or_404(Team, self.request_for(self.user_a), pk=team.pk), team
        )
        self.assertEqual(
            get_owned_or_404(Team, self.request_for(self.user_b), pk=team.pk), team
        )

    def test_accepts_a_queryset_as_well_as_a_model(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        got = get_owned_or_404(
            Team.objects.all(), self.request_for(self.user_a), pk=team.pk
        )
        self.assertEqual(got, team)

    def test_multiple_lookup_kwargs_are_all_applied(self) -> None:
        """The paired `Player, id=..., team=...` conversion in §5.2."""
        team = Team.objects.create(name="A Team", manager=self.user_a)
        other = Team.objects.create(name="Other Team", manager=self.user_a)
        player = Player.objects.create(team=team, name="A Player")
        got = get_owned_or_404(
            Player, self.request_for(self.user_a), id=player.id, team=team
        )
        self.assertEqual(got, player)
        with self.assertRaises(Http404):
            get_owned_or_404(
                Player, self.request_for(self.user_a), id=player.id, team=other
            )

    def test_anonymous_request_gets_404_for_a_managed_row(self) -> None:
        team = Team.objects.create(name="A Team", manager=self.user_a)
        with self.assertRaises(Http404):
            get_owned_or_404(Team, self.request_for(AnonymousUser()), pk=team.pk)

    def test_arena_map_is_reachable_by_every_account(self) -> None:
        """§2.4 — maps are shared reference data with no ownership axis."""
        arena = ArenaMap.objects.create(name="Shared", image="maps/shared.png")
        for user in (self.user_a, self.user_b):
            with self.subTest(user=user.email):
                self.assertEqual(
                    get_owned_or_404(ArenaMap, self.request_for(user), pk=arena.pk),
                    arena,
                )


# ---------------------------------------------------------------------------
# owned_queryset
# ---------------------------------------------------------------------------


class TestOwnedQueryset(OwnershipTestBase):
    """§3.4 — own rows plus Unmanaged rows; nothing from another Account."""

    def setUp(self) -> None:
        super().setUp()
        self.team_a = Team.objects.create(name="Q A Team", manager=self.user_a)
        self.team_b = Team.objects.create(name="Q B Team", manager=self.user_b)
        self.team_open = Team.objects.create(name="Q Open Team")

    def test_root_model_with_empty_path(self) -> None:
        got = set(owned_queryset(Team.objects.all(), self.user_a))
        self.assertEqual(got, {self.team_a, self.team_open})

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        got = set(owned_queryset(Team.objects.all(), self.user_b))
        self.assertEqual(got, {self.team_b, self.team_open})

    def test_unmanaged_row_is_listed_to_both_accounts(self) -> None:
        for user in (self.user_a, self.user_b):
            with self.subTest(user=user.email):
                self.assertIn(self.team_open, owned_queryset(Team.objects.all(), user))

    def test_anonymous_user_sees_only_unmanaged_rows(self) -> None:
        got = set(owned_queryset(Team.objects.all(), None))
        self.assertEqual(got, {self.team_open})

    def test_path_traverses_to_the_root_for_player(self) -> None:
        player_a = Player.objects.create(team=self.team_a, name="PA")
        player_b = Player.objects.create(team=self.team_b, name="PB")
        player_open = Player.objects.create(team=self.team_open, name="PO")
        got = set(owned_queryset(Player.objects.all(), self.user_a, path="team"))
        self.assertEqual(got, {player_a, player_open})
        self.assertNotIn(player_b, got)

    def test_path_traverses_two_hops_for_season_scoped_rows(self) -> None:
        league_a = League.objects.create(name="QL A", manager=self.user_a)
        league_b = League.objects.create(name="QL B", manager=self.user_b)
        season_a = Season.objects.create(
            league=league_a, name="S", start_date=date(2026, 1, 1)
        )
        season_b = Season.objects.create(
            league=league_b, name="S", start_date=date(2026, 1, 1)
        )
        conf_a = Conference.objects.create(season=season_a, name="E", ordinal=1)
        conf_b = Conference.objects.create(season=season_b, name="E", ordinal=1)
        got = set(
            owned_queryset(Conference.objects.all(), self.user_a, path="season__league")
        )
        self.assertEqual(got, {conf_a})
        self.assertNotIn(conf_b, got)

    def test_returns_a_queryset_that_still_chains(self) -> None:
        chained = owned_queryset(Team.objects.all(), self.user_a).order_by("name")
        self.assertEqual(list(chained), [self.team_a, self.team_open])


# ---------------------------------------------------------------------------
# owned_match_q / owned_game_round_q — asserted on ROWS, never on the Q tree
# ---------------------------------------------------------------------------


class TestOwnedMatchQ(OwnershipTestBase):
    """§3.4 — the conditional-root predicate for `Match`."""

    def setUp(self) -> None:
        super().setUp()
        self.red = Team.objects.create(name="MQ Red")
        self.blue = Team.objects.create(name="MQ Blue")
        self.league_a = League.objects.create(name="MQ A", manager=self.user_a)
        self.league_b = League.objects.create(name="MQ B", manager=self.user_b)
        self.league_open = League.objects.create(name="MQ Open")
        self.season_a = Season.objects.create(
            league=self.league_a, name="S", start_date=date(2026, 1, 1)
        )
        self.season_b = Season.objects.create(
            league=self.league_b, name="S", start_date=date(2026, 1, 1)
        )
        self.season_open = Season.objects.create(
            league=self.league_open, name="S", start_date=date(2026, 1, 1)
        )

        self.sandbox_a = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_a
        )
        self.sandbox_b = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_b
        )
        self.sandbox_open = Match.objects.create(team_red=self.red, team_blue=self.blue)
        self.season_match_a = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season_a
        )
        self.season_match_b = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season_b
        )
        self.season_match_open = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season_open
        )

    def test_account_a_sees_its_own_and_unmanaged_matches_only(self) -> None:
        got = set(Match.objects.filter(owned_match_q(self.user_a)))
        self.assertEqual(
            got,
            {
                self.sandbox_a,
                self.sandbox_open,
                self.season_match_a,
                self.season_match_open,
            },
        )

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        got = set(Match.objects.filter(owned_match_q(self.user_b)))
        self.assertEqual(
            got,
            {
                self.sandbox_b,
                self.sandbox_open,
                self.season_match_b,
                self.season_match_open,
            },
        )

    def test_other_accounts_sandbox_match_is_excluded(self) -> None:
        got = set(Match.objects.filter(owned_match_q(self.user_a)))
        self.assertNotIn(self.sandbox_b, got)

    def test_other_accounts_season_match_is_excluded(self) -> None:
        got = set(Match.objects.filter(owned_match_q(self.user_a)))
        self.assertNotIn(self.season_match_b, got)

    def test_anonymous_user_sees_only_unmanaged_matches(self) -> None:
        got = set(Match.objects.filter(owned_match_q(None)))
        self.assertEqual(got, {self.sandbox_open, self.season_match_open})

    def test_no_row_is_returned_twice(self) -> None:
        """The two branches of the predicate must not overlap."""
        qs = Match.objects.filter(owned_match_q(self.user_a))
        self.assertEqual(qs.count(), len(set(qs)))


class TestOwnedGameRoundQ(OwnershipTestBase):
    """§3.4 — the three-branch predicate for `GameRound`."""

    def setUp(self) -> None:
        super().setUp()
        self.red = Team.objects.create(name="GQ Red")
        self.blue = Team.objects.create(name="GQ Blue")
        self.league_a = League.objects.create(name="GQ A", manager=self.user_a)
        self.league_b = League.objects.create(name="GQ B", manager=self.user_b)
        self.season_a = Season.objects.create(
            league=self.league_a, name="S", start_date=date(2026, 1, 1)
        )
        self.season_b = Season.objects.create(
            league=self.league_b, name="S", start_date=date(2026, 1, 1)
        )

        # Branch 1 — standalone rounds (match IS NULL).
        self.standalone_a = GameRound.objects.create(
            round_number=1, manager=self.user_a
        )
        self.standalone_b = GameRound.objects.create(
            round_number=1, manager=self.user_b
        )
        self.standalone_open = GameRound.objects.create(round_number=1)

        # Branch 2 — round of a sandbox Match (match.season IS NULL).
        sandbox_a = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_a
        )
        sandbox_b = Match.objects.create(
            team_red=self.red, team_blue=self.blue, manager=self.user_b
        )
        sandbox_open = Match.objects.create(team_red=self.red, team_blue=self.blue)
        self.sandbox_round_a = GameRound.objects.create(round_number=1, match=sandbox_a)
        self.sandbox_round_b = GameRound.objects.create(round_number=1, match=sandbox_b)
        self.sandbox_round_open = GameRound.objects.create(
            round_number=1, match=sandbox_open
        )

        # Branch 3 — round of a Season Match.
        season_match_a = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season_a
        )
        season_match_b = Match.objects.create(
            team_red=self.red, team_blue=self.blue, season=self.season_b
        )
        self.season_round_a = GameRound.objects.create(
            round_number=1, match=season_match_a
        )
        self.season_round_b = GameRound.objects.create(
            round_number=1, match=season_match_b
        )

    def test_account_a_sees_all_three_branches_of_its_own_plus_unmanaged(self) -> None:
        got = set(GameRound.objects.filter(owned_game_round_q(self.user_a)))
        self.assertEqual(
            got,
            {
                self.standalone_a,
                self.standalone_open,
                self.sandbox_round_a,
                self.sandbox_round_open,
                self.season_round_a,
            },
        )

    def test_isolation_holds_in_the_reverse_direction(self) -> None:
        got = set(GameRound.objects.filter(owned_game_round_q(self.user_b)))
        self.assertEqual(
            got,
            {
                self.standalone_b,
                self.standalone_open,
                self.sandbox_round_b,
                self.sandbox_round_open,
                self.season_round_b,
            },
        )

    def test_other_accounts_rounds_are_excluded_in_every_branch(self) -> None:
        got = set(GameRound.objects.filter(owned_game_round_q(self.user_a)))
        for other in (
            self.standalone_b,
            self.sandbox_round_b,
            self.season_round_b,
        ):
            with self.subTest(row=other.pk):
                self.assertNotIn(other, got)

    def test_anonymous_user_sees_only_unmanaged_rounds(self) -> None:
        got = set(GameRound.objects.filter(owned_game_round_q(None)))
        self.assertEqual(got, {self.standalone_open, self.sandbox_round_open})

    def test_no_row_is_returned_twice(self) -> None:
        """The three branches of the predicate must be mutually exclusive."""
        qs = GameRound.objects.filter(owned_game_round_q(self.user_a))
        self.assertEqual(qs.count(), len(set(qs)))


# ---------------------------------------------------------------------------
# stamp_manager / manager_or_none
# ---------------------------------------------------------------------------


class TestStampManager(OwnershipTestBase):
    """§3.5 — post-hoc stamping for rows created without a request."""

    def test_stamps_and_persists_the_manager(self) -> None:
        team = Team.objects.create(name="Stamp Me")
        stamp_manager(team, self.user_a)
        team.refresh_from_db()
        self.assertEqual(team.manager_id, self.user_a.pk)

    def test_returns_the_object(self) -> None:
        team = Team.objects.create(name="Stamp Return")
        self.assertIs(stamp_manager(team, self.user_a), team)

    def test_anonymous_user_leaves_the_row_unmanaged(self) -> None:
        team = Team.objects.create(name="Stamp Anon")
        stamp_manager(team, AnonymousUser())
        team.refresh_from_db()
        self.assertIsNone(team.manager_id)

    def test_none_user_leaves_the_row_unmanaged(self) -> None:
        team = Team.objects.create(name="Stamp None")
        stamp_manager(team, None)
        team.refresh_from_db()
        self.assertIsNone(team.manager_id)

    def test_stamping_clears_a_previous_manager_when_given_none(self) -> None:
        team = Team.objects.create(name="Stamp Clear", manager=self.user_a)
        stamp_manager(team, None)
        team.refresh_from_db()
        self.assertIsNone(team.manager_id)

    def test_stamped_row_becomes_invisible_to_the_other_account(self) -> None:
        match = Match.objects.create(
            team_red=Team.objects.create(name="SR"),
            team_blue=Team.objects.create(name="SB"),
        )
        self.assertTrue(is_owned_by(match, self.user_b))
        stamp_manager(match, self.user_a)
        match.refresh_from_db()
        self.assertTrue(is_owned_by(match, self.user_a))
        self.assertFalse(is_owned_by(match, self.user_b))


class TestManagerOrNone(OwnershipTestBase):
    """§3.5 — `request.user` when authenticated, else `None`."""

    def test_authenticated_request_returns_the_user(self) -> None:
        self.assertEqual(manager_or_none(self.request_for(self.user_a)), self.user_a)

    def test_anonymous_request_returns_none(self) -> None:
        self.assertIsNone(manager_or_none(self.request_for(AnonymousUser())))

    def test_request_without_a_user_attribute_returns_none(self) -> None:
        request = self.factory.get("/")
        self.assertIsNone(manager_or_none(request))

    def test_request_with_user_none_returns_none(self) -> None:
        self.assertIsNone(manager_or_none(self.request_for(None)))
