"""N+1 guardrail for every ``@action(detail=True)`` GET on the DRF router.

The per-row sibling of ``test_list_query_counts.py``. For each detail GET
action: build one owned row, GET the action and count queries; GROW that row's
per-row data (more GameEvents, PlayerRoundStates, enrolled Teams, Seasons,
bracket nodes, ...) and count again. The counts must be equal -- a per-row read
runs a constant number of queries however much data hangs off the row. (ANG-06f
shipped ``/api/rounds/<id>/playback/`` with one query per event; this catches
that class of bug on every action, not only the ones with a hand-written test.)

Adding a detail GET action means adding one ``SCALERS`` entry (or an ``EXEMPT``
entry with the reason); ``test_every_detail_get_action_has_a_scaler`` fails
until you do.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from itertools import count
from pathlib import Path
from typing import Any

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rest_framework.test import APIClient

from conftest import get_shared_manager
from core.models import (
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapCellRankingConfig,
    MapZoneConfig,
    SightLineConfig,
)
from laserforce_simulator.api.tests.test_list_query_counts import _owned_team
from laserforce_simulator.api_urls import router
from matches.models import (
    BracketNode,
    GameEvent,
    GameRound,
    League,
    Match,
    PlayerRoundState,
    Season,
    SeriesMatch,
    Tournament,
    TournamentPlayerEntry,
    WatchedPlayer,
)
from matches.tests.league_api_fixtures import (
    add_round_states,
    add_win,
    confirmed_map,
    fired_evaluation,
    make_completed_season,
    make_conference,
    make_open_season,
)
from matches.tests.test_playback_map import _make_arena_map
from teams.models import Player, Team

_seq = count()

# How many times ``grow`` runs between the two counts.
GROWTH_STEPS = 3

Ctx = dict[str, Any]


@dataclass(frozen=True)
class Scaler:
    """How to N+1-check one detail action.

    ``build(user)`` makes an owned row (with a little per-row data already, so
    a per-item query is in the first count too) and returns a context dict;
    ``url(ctx)`` is the action URL; ``grow(ctx)`` adds per-row data. When the
    payload legitimately does not change as the data grows, ``static_payload``
    says why (otherwise the test asserts the payload changed, so a ``grow``
    that misses the action's data cannot pass vacuously).
    """

    build: Callable[[Any], Ctx]
    url: Callable[[Ctx], str]
    grow: Callable[[Ctx], None]
    static_payload: str | None = None


# --- rounds: one owned GameRound, grown by events + player states -----------


def _round_world(user, *, mapped: bool = True) -> Ctx:
    """An owned, played GameRound (on a configured ArenaMap unless
    ``mapped=False``) with every player's PlayerRoundState and one batch of
    events."""
    arena_map, zone_size = (
        _make_arena_map(f"DetailArena-{next(_seq)}") if mapped else (None, None)
    )
    red = _owned_team(user, "RndRed")
    blue = _owned_team(user, "RndBlue")
    game_round = GameRound.objects.create(
        round_number=1,
        team_red=red,
        team_blue=blue,
        manager=user,
        arena_map=arena_map,
        zone_size=zone_size,
        red_points=100,
        blue_points=50,
        winner=red,
        is_completed=True,
    )
    ctx: Ctx = {"round": game_round, "red": red, "blue": blue, "tick": count(1)}
    for team, color in ((red, "red"), (blue, "blue")):
        for player in team.players.all():
            _round_state(game_round, player, color)
    _grow_round(ctx)
    return ctx


def _mapless_round_world(user) -> Ctx:
    return _round_world(user, mapped=False)


def _round_state(game_round: GameRound, player: Player, color: str) -> None:
    PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color=color,
        role="scout",
        starting_lives=15,
        starting_shots=30,
        starting_missiles=5,
        cell_row=0,
        cell_col=0,
        points_scored=100,
    )


def _grow_round(ctx: Ctx) -> None:
    """One more of every event kind the round readers fold, plus one more
    player (a fresh Player on each side with its PlayerRoundState)."""
    game_round: GameRound = ctx["round"]
    for team, color in ((ctx["red"], "red"), (ctx["blue"], "blue")):
        extra = Player.objects.create(team=team, name=f"Sub {next(_seq)}")
        _round_state(game_round, extra, color)
    actor = ctx["red"].slot_commander
    target = ctx["blue"].slot_scout_1
    medic = ctx["blue"].slot_medic
    tick = next(ctx["tick"])
    events = [
        (
            "movement",
            actor,
            None,
            {
                "start_row": 0,
                "start_col": 0,
                "end_row": 0,
                "end_col": 1,
                "route": [[0, 1]],
            },
        ),
        ("tag", actor, target, {"actor_shots": 29, "target_lives": 14}),
        ("miss", actor, target, {"actor_shots": 28}),
        ("locking", actor, target, {}),
        ("missiled", actor, target, {"result": "hit", "target_lives": 12}),
        ("resupply_lives", medic, target, {"target_lives": 15}),
        ("special", actor, None, {"sp": 0}),
    ]
    for event_type, who, whom, metadata in events:
        GameEvent.objects.create(
            game_round=game_round,
            timestamp=tick,
            event_type=event_type,
            actor=who,
            target=whom,
            points_awarded=10,
            description=f"{who.name} {event_type}",
            metadata=metadata,
        )
    occupancy = dict(game_round.cell_occupancy_json or {})
    occupancy[str(actor.pk)] = {f"0,{tick % 4}": tick}
    game_round.cell_occupancy_json = occupancy
    game_round.save(update_fields=["cell_occupancy_json"])


def _round_url(suffix: str) -> Callable[[Ctx], str]:
    return lambda ctx: f"/api/rounds/{ctx['round'].pk}/{suffix}/"


# --- players: one owned Player, grown by rounds they played ------------------


def _player_world(user) -> Ctx:
    team = _owned_team(user, "Career")
    opponent = _owned_team(user, "CareerOpp")
    ctx: Ctx = {"user": user, "team": team, "opponent": opponent}
    _grow_player(ctx)
    return ctx


def _grow_player(ctx: Ctx) -> None:
    """One more owned sandbox Round the Player's Team played (all six slots)."""
    team: Team = ctx["team"]
    opponent: Team = ctx["opponent"]
    game_round = GameRound.objects.create(
        round_number=1,
        team_red=team,
        team_blue=opponent,
        manager=ctx["user"],
        red_points=100,
        blue_points=50,
        is_completed=True,
    )
    for side, color in ((team, "red"), (opponent, "blue")):
        for player in side.players.all():
            PlayerRoundState.objects.create(
                game_round=game_round,
                player=player,
                team_color=color,
                role="scout",
                points_scored=1000,
                tags_made=10,
                times_tagged=5,
                shots_missed=3,
            )


# --- maps: one ArenaMap, grown by rounds played on it and bigger configs -----

_ZONE_SIZE = 50


def _map_world(user) -> Ctx:
    arena_map = confirmed_map(f"DetailMap-{next(_seq)}")
    ctx: Ctx = {"user": user, "map": arena_map, "side": 2}
    _grow_map(ctx)
    return ctx


def _grow_map(ctx: Ctx) -> None:
    """Widen every stored per-map blob, add configs at another zone size, an
    older unconfirmed config, and one more owned Round played on the map."""
    arena_map = ctx["map"]
    ctx["side"] += 1
    side = ctx["side"]
    cells = [[r, c] for r in range(side) for c in range(side)]
    zone_data = {
        "zones": [[1] * side for _ in range(side)],
        "red_spawn": cells[:side],
        "blue_spawn": cells[-side:],
    }
    MapZoneConfig.objects.filter(arena_map=arena_map, confirmed=True).update(
        zone_data=zone_data
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map, zone_size=_ZONE_SIZE, zone_data=zone_data, confirmed=False
    )
    sight_data = {f"{r},{c}": [f"{r},{c}"] for r, c in cells}
    SightLineConfig.objects.update_or_create(
        arena_map=arena_map, zone_size=_ZONE_SIZE, defaults={"sight_data": sight_data}
    )
    for base_type in ("red", "blue"):
        BaseSightLineConfig.objects.update_or_create(
            arena_map=arena_map,
            base_type=base_type,
            zone_size=_ZONE_SIZE,
            defaults={"visible_cells": [f"{r},{c}" for r, c in cells]},
        )
    MapCellRankingConfig.objects.update_or_create(
        arena_map=arena_map, zone_size=_ZONE_SIZE, defaults={"ranked_cells": cells}
    )
    HeavyStrongSpotsConfig.objects.update_or_create(
        arena_map=arena_map, zone_size=_ZONE_SIZE, defaults={"cells": cells}
    )
    other_size = _ZONE_SIZE + side
    SightLineConfig.objects.create(
        arena_map=arena_map, zone_size=other_size, sight_data=sight_data
    )
    HeavyStrongSpotsConfig.objects.create(
        arena_map=arena_map, zone_size=other_size, cells=cells
    )
    red = _owned_team(ctx["user"], "MapRed")
    blue = _owned_team(ctx["user"], "MapBlue")
    game_round = GameRound.objects.create(
        round_number=1,
        team_red=red,
        team_blue=blue,
        manager=ctx["user"],
        arena_map=arena_map,
        zone_size=_ZONE_SIZE,
        is_completed=True,
    )
    occupancy = {}
    for team, color in ((red, "red"), (blue, "blue")):
        for player in team.players.all():
            PlayerRoundState.objects.create(
                game_round=game_round, player=player, team_color=color
            )
            occupancy[str(player.pk)] = {f"{side - 1},{side - 1}": side}
    game_round.cell_occupancy_json = occupancy
    game_round.save(update_fields=["cell_occupancy_json"])


def _map_url(suffix: str, query: str = "") -> Callable[[Ctx], str]:
    return lambda ctx: f"/api/maps/{ctx['map'].pk}/{suffix}/{query}"


# --- leagues + seasons: one career League, grown by Teams / rounds / Seasons -


def _league_world(user) -> Ctx:
    """A finance-on career League: a completed Season 1 and an active,
    two-Conference Season 2, each with played rounds (PlayerRoundStates), a
    free-agent pool, a watch list and a bench player on the current Team."""
    teams = [_owned_team(user, "Lg") for _ in range(4)]
    league = League.objects.create(
        name=f"Detail League {next(_seq)}",
        mode="league",
        manager=user,
        current_team=teams[0],
        finance_enabled=True,
    )
    completed = make_completed_season(league, teams, name="Season 1")
    active = make_open_season(league, teams, state="active", name="Season 2")
    conferences = [
        make_conference(active, "East", 1, teams[:2]),
        make_conference(active, "West", 2, teams[2:]),
    ]
    add_round_states(completed, teams[0], teams[1])
    add_round_states(active, teams[0], teams[1])
    league.free_agent_pool = Team.objects.create(
        name=f"Free Agents {next(_seq)}", manager=user
    )
    league.save(update_fields=["free_agent_pool"])
    ctx: Ctx = {
        "user": user,
        "league": league,
        "teams": teams,
        "completed": completed,
        "active": active,
        "conferences": conferences,
        "year": count(2024, -1),
    }
    _grow_league(ctx)
    return ctx


def _grow_league(ctx: Ctx) -> None:
    """Two more Teams enrolled in both Seasons (one per Conference), each with
    played rounds; an older completed Season; one more bench player, free
    agent and watched Player."""
    user = ctx["user"]
    league: League = ctx["league"]
    teams: list[Team] = ctx["teams"]
    current = teams[0]
    newcomers = [_owned_team(user, "LgNew") for _ in range(2)]
    for season in (ctx["completed"], ctx["active"]):
        season.teams.add(*newcomers)
        season.starting_team_ids_json = sorted(
            season.teams.values_list("id", flat=True)
        )
        season.save(update_fields=["starting_team_ids_json"])
    for conference, team in zip(ctx["conferences"], newcomers):
        conference.teams.add(team)
    for team in newcomers:
        add_win(ctx["completed"], current, team)
        add_round_states(ctx["completed"], team, current)
        add_round_states(ctx["active"], team, current)
    teams.extend(newcomers)

    year = next(ctx["year"])
    older = make_completed_season(
        league, teams[:3], name=f"Season {year}", start_date=date(year, 1, 1)
    )
    add_round_states(older, teams[0], teams[1])

    Player.objects.create(team=current, name=f"Bench {next(_seq)}")
    Player.objects.create(team=league.free_agent_pool, name=f"FA {next(_seq)}")
    WatchedPlayer.objects.create(
        account=user, league=league, player=newcomers[0].slot_commander
    )


def _fired_world(user) -> Ctx:
    """A career League whose Manager was fired after its only (completed)
    Season -- what ``new-team-candidates`` needs."""
    teams = [_owned_team(user, "Fired") for _ in range(4)]
    league = League.objects.create(
        name=f"Fired League {next(_seq)}",
        mode="league",
        manager=user,
        current_team=teams[0],
    )
    season = make_completed_season(league, teams)
    fired_evaluation(league, season, teams[0])
    ctx: Ctx = {"user": user, "league": league, "season": season, "teams": teams}
    _grow_fired(ctx)
    return ctx


def _grow_fired(ctx: Ctx) -> None:
    """Two more Teams in the completed Season, losing to every Team so far
    (so they rank last and become candidates)."""
    season: Season = ctx["season"]
    newcomers = [_owned_team(ctx["user"], "FiredNew") for _ in range(2)]
    season.teams.add(*newcomers)
    for team in newcomers:
        for winner in ctx["teams"]:
            add_win(season, winner, team)
    ctx["teams"].extend(newcomers)
    season.starting_team_ids_json = sorted(t.id for t in ctx["teams"])
    season.save(update_fields=["starting_team_ids_json"])


def _league_url(suffix: str) -> Callable[[Ctx], str]:
    return lambda ctx: f"/api/leagues/{ctx['league'].pk}/{suffix}/"


def _league_team_url(suffix: str) -> Callable[[Ctx], str]:
    return lambda ctx: (
        f"/api/leagues/{ctx['league'].pk}/teams/{ctx['teams'][0].pk}/{suffix}/"
    )


def _league_player_url(ctx: Ctx) -> str:
    player = ctx["teams"][0].slot_commander
    return f"/api/leagues/{ctx['league'].pk}/players/{player.pk}/"


def _season_url(season_key: str, suffix: str) -> Callable[[Ctx], str]:
    return lambda ctx: f"/api/seasons/{ctx[season_key].pk}/{suffix}/"


# --- leagues/playoffs: a built Season bracket, grown by nodes + Series games --


def _playoffs_world(user) -> Ctx:
    from matches.tests.test_api_feats_playoffs import _built_flat_season

    season, teams, phase = _built_flat_season(f"DetailPo{next(_seq)}", user)
    tournament = season.tournaments_for_phase(phase)[0]
    ctx: Ctx = {"user": user, "season": season, "tournament": tournament}
    _grow_playoffs(ctx)
    return ctx


def _grow_playoffs(ctx: Ctx) -> None:
    """One more round-1 node (two new Teams) and a played Series game on every
    round-1 node."""
    tournament: Tournament = ctx["tournament"]
    team_a = _owned_team(ctx["user"], "PoA")
    team_b = _owned_team(ctx["user"], "PoB")
    position = tournament.nodes.filter(bracket_round=1).count()
    BracketNode.objects.create(
        tournament=tournament,
        bracket_round=1,
        position=position,
        team_a=team_a,
        team_b=team_b,
    )
    for node in tournament.nodes.filter(bracket_round=1, team_a__isnull=False):
        match = add_win(ctx["season"], node.team_a, node.team_b)
        SeriesMatch.objects.create(
            node=node,
            match=match,
            game_number=node.series_matches.count() + 1,
            winner=node.team_a,
        )


# --- seasons/member-night: a member-night cursor, grown by Sites + games -----


def _member_night_world(user) -> Ctx:
    from matches.tests.test_api_member_night import mn_season

    league = League.objects.create(name=f"MN League {next(_seq)}", manager=user)
    season = mn_season(league, f"DetailMn{next(_seq)}")
    ctx: Ctx = {"user": user, "season": season, "phase": season.current_phase()}
    _grow_member_night(ctx)
    return ctx


def _grow_member_night(ctx: Ctx) -> None:
    """One more viable Site (an enrolled Team whose players live there) and
    one more drawn game shell from it."""
    season: Season = ctx["season"]
    site = f"Site{next(_seq)}"
    enrolled = _owned_team(ctx["user"], "MnEnrolled")
    season.teams.add(enrolled)
    Player.objects.filter(team=enrolled).update(home_site=site)
    drawn_red = _owned_team(ctx["user"], "MnRed")
    drawn_blue = _owned_team(ctx["user"], "MnBlue")
    Player.objects.filter(team__in=[drawn_red, drawn_blue]).update(home_site=site)
    Match.objects.create(
        team_red=drawn_red,
        team_blue=drawn_blue,
        season=season,
        season_phase=ctx["phase"],
    )


# --- tournaments/pool: a Random-Draw pool, grown by entries -------------------


def _pool_world(user) -> Ctx:
    tournament = Tournament.objects.create(name=f"Pool {next(_seq)}", manager=user)
    ctx: Ctx = {"user": user, "tournament": tournament}
    _grow_pool(ctx)
    return ctx


def _grow_pool(ctx: Ctx) -> None:
    """One more drawn Team's six players in the pool."""
    team = _owned_team(ctx["user"], "Pool")
    for tier, player in enumerate(team.players.all(), start=1):
        TournamentPlayerEntry.objects.create(
            tournament=ctx["tournament"], player=player, tier=tier, drawn_team=team
        )


# --- the registry -------------------------------------------------------------

# (route prefix on ``api_urls.router``, action ``url_name``[, variant]) ->
# Scaler. A third ``variant`` element adds a second shape of the same action.
SCALERS: dict[tuple[str, ...], Scaler] = {
    # rounds
    ("rounds", "events"): Scaler(_round_world, _round_url("events"), _grow_round),
    ("rounds", "timeline"): Scaler(_round_world, _round_url("timeline"), _grow_round),
    # Mapless: the event fold alone (``_playback_events``, the ANG-06f bug).
    ("rounds", "playback"): Scaler(
        _mapless_round_world, _round_url("playback"), _grow_round
    ),
    # On a map: adds the movement routes (``build_playback_map``).
    ("rounds", "playback", "map"): Scaler(
        _round_world, _round_url("playback"), _grow_round
    ),
    ("rounds", "missile-log"): Scaler(
        _round_world, _round_url("missile-log"), _grow_round
    ),
    ("rounds", "heatmap"): Scaler(_round_world, _round_url("heatmap"), _grow_round),
    # players
    ("players", "career"): Scaler(
        _player_world,
        lambda ctx: f"/api/players/{ctx['team'].slot_commander.pk}/career/",
        _grow_player,
    ),
    # maps
    ("maps", "zones"): Scaler(
        _map_world, _map_url("zones", f"?zone_size={_ZONE_SIZE}"), _grow_map
    ),
    ("maps", "zone-config"): Scaler(_map_world, _map_url("zone-config"), _grow_map),
    ("maps", "sight-lines"): Scaler(
        _map_world, _map_url("sight-lines", f"?zone_size={_ZONE_SIZE}"), _grow_map
    ),
    ("maps", "sight-lines-cell"): Scaler(
        _map_world,
        _map_url("sight-lines/cell", f"?zone_size={_ZONE_SIZE}&r=0&c=0"),
        _grow_map,
    ),
    ("maps", "ranked-cells"): Scaler(
        _map_world, _map_url("ranked-cells", f"?zone_size={_ZONE_SIZE}"), _grow_map
    ),
    ("maps", "strong-spots"): Scaler(
        _map_world, _map_url("strong-spots", f"?zone_size={_ZONE_SIZE}"), _grow_map
    ),
    ("maps", "spawn-cells"): Scaler(_map_world, _map_url("spawn-cells"), _grow_map),
    ("maps", "heatmap"): Scaler(
        _map_world, _map_url("heatmap", f"?zone_size={_ZONE_SIZE}"), _grow_map
    ),
    # leagues
    ("leagues", "dashboard"): Scaler(
        _league_world, _league_url("dashboard"), _grow_league
    ),
    ("leagues", "play-controls"): Scaler(
        _league_world,
        _league_url("play-controls"),
        _grow_league,
        static_payload="play controls read the Season cursor, not its Teams/rounds",
    ),
    ("leagues", "history"): Scaler(_league_world, _league_url("history"), _grow_league),
    ("leagues", "team-schedule"): Scaler(
        _league_world, _league_team_url("schedule"), _grow_league
    ),
    ("leagues", "new-team-candidates"): Scaler(
        _fired_world, _league_url("new-team-candidates"), _grow_fired
    ),
    ("leagues", "watch-list"): Scaler(
        _league_world, _league_url("watch-list"), _grow_league
    ),
    ("leagues", "player-stats"): Scaler(
        _league_world, _league_url("player-stats"), _grow_league
    ),
    ("leagues", "player-ratings"): Scaler(
        _league_world, _league_url("player-ratings"), _grow_league
    ),
    ("leagues", "leaders"): Scaler(_league_world, _league_url("leaders"), _grow_league),
    ("leagues", "free-agents"): Scaler(
        _league_world, _league_url("free-agents"), _grow_league
    ),
    ("leagues", "league-player"): Scaler(
        _league_world, _league_player_url, _grow_league
    ),
    ("leagues", "team-finances"): Scaler(
        _league_world, _league_team_url("finances"), _grow_league
    ),
    ("leagues", "finances"): Scaler(
        _league_world, _league_url("finances"), _grow_league
    ),
    ("leagues", "statistical-feats"): Scaler(
        _league_world, _league_url("statistical-feats"), _grow_league
    ),
    ("leagues", "playoffs"): Scaler(
        _playoffs_world,
        lambda ctx: f"/api/leagues/{ctx['season'].league_id}/playoffs/",
        _grow_playoffs,
    ),
    ("leagues", "team-roster"): Scaler(
        _league_world, _league_team_url("roster"), _grow_league
    ),
    ("leagues", "team-history"): Scaler(
        _league_world, _league_team_url("history"), _grow_league
    ),
    ("leagues", "team-history-players"): Scaler(
        _league_world, _league_team_url("history/players"), _grow_league
    ),
    ("leagues", "team-stats"): Scaler(
        _league_world, _league_url("team-stats"), _grow_league
    ),
    ("leagues", "power-rankings"): Scaler(
        _league_world, _league_url("power-rankings"), _grow_league
    ),
    ("leagues", "game-log"): Scaler(
        _league_world, _league_url("game-log"), _grow_league
    ),
    # seasons
    ("seasons", "dashboard"): Scaler(
        _league_world, _season_url("active", "dashboard"), _grow_league
    ),
    ("seasons", "standings"): Scaler(
        _league_world, _season_url("active", "standings"), _grow_league
    ),
    # The completed Season: the active one's Conferences carry no starting
    # ids here, so it schedules no fixtures.
    ("seasons", "schedule"): Scaler(
        _league_world, _season_url("completed", "schedule"), _grow_league
    ),
    ("seasons", "awards"): Scaler(
        _league_world,
        _season_url("completed", "awards"),
        _grow_league,
        static_payload="single award winners; the added rounds' fixed stats tie",
    ),
    ("seasons", "owner-evaluation"): Scaler(
        _league_world,
        _season_url("completed", "owner-evaluation"),
        _grow_league,
        static_payload="the evaluation row is written once, on the first read",
    ),
    ("seasons", "conferences"): Scaler(
        _league_world, _season_url("active", "conferences"), _grow_league
    ),
    ("seasons", "member-night"): Scaler(
        _member_night_world,
        lambda ctx: f"/api/seasons/{ctx['season'].pk}/member-night/",
        _grow_member_night,
    ),
    # tournaments
    ("tournaments", "pool"): Scaler(
        _pool_world,
        lambda ctx: f"/api/tournaments/{ctx['tournament'].pk}/pool/",
        _grow_pool,
    ),
}

# Detail GET actions with no N+1 check, and why.
EXEMPT: dict[tuple[str, str], str] = {
    # Raw files, never enveloped: the body is bytes from disk / a renderer.
    ("rounds", "report"): "raw PDF HttpResponse (RV-03), not a JSON read",
    ("maps", "processed-image"): "raw image/png FileResponse from disk",
    # Capability-token job status: read from the Celery result backend, no DB
    # rows hang off the Season / Tournament to grow.
    ("seasons", "play-job"): "Celery AsyncResult status; no per-row DB data",
    ("tournaments", "play-job"): "Celery AsyncResult status; no per-row DB data",
}

# Actions with a known N+1 today: key -> reason. The case is a strict xfail,
# so fixing the N+1 turns it red until the entry is removed.
KNOWN_N_PLUS_ONE: dict[tuple[str, ...], str] = {
    ("leagues", "history"): (
        "N+1 (documented, page-bounded): build_history_row calls "
        "compute_season_award_set per Season row -- Player + Team name SELECTs "
        "per Season on the page"
    ),
    ("leagues", "league-player"): (
        "N+1: build_player_detail loops league.seasons -- build_round_dicts, an "
        "exists() and compute_season_award_set per Season"
    ),
    ("leagues", "team-history"): (
        "N+1: _team_season_rows reads each Season's Team names, Matches and "
        "GameRounds separately -- three SELECTs per Season"
    ),
    ("leagues", "power-rankings"): (
        "N+1: _team_mean_rating reads team.active_players (six slot FK loads) "
        "per enrolled Team -- six teams_player SELECTs per Team"
    ),
    ("seasons", "owner-evaluation"): (
        "N+1: ensure_owner_evaluation -> _ensure_team_finances re-reads every "
        "enrolled Team's TeamSeasonFinance / Matches / phases on each GET, "
        "even when the rows exist -- SELECTs per Team"
    ),
}


def _detail_get_actions() -> set[tuple[str, str]]:
    """Every ``(prefix, url_name)`` detail action that answers GET."""
    found: set[tuple[str, str]] = set()
    for prefix, viewset, _basename in router.registry:
        for action in viewset.get_extra_actions():
            if action.detail and "get" in action.mapping:
                found.add((prefix, action.url_name))
    return found


def _cases() -> list:
    return [
        pytest.param(
            key,
            id="/".join(key),
            marks=(
                [pytest.mark.xfail(strict=True, reason=KNOWN_N_PLUS_ONE[key])]
                if key in KNOWN_N_PLUS_ONE
                else []
            ),
        )
        for key in SCALERS
    ]


def _counted_get(api: APIClient, url: str) -> tuple[int, list[str], bytes]:
    """GET ``url``; return (query count, the SQL, the body)."""
    with CaptureQueriesContext(connection) as ctx:
        response = api.get(url)
    assert response.status_code == 200, (url, response.content)
    return (
        len(ctx.captured_queries),
        [q["sql"] for q in ctx.captured_queries],
        response.content,
    )


@pytest.mark.django_db
@pytest.mark.parametrize("key", _cases())
def test_detail_action_query_count_is_constant(
    key: tuple[str, ...], settings, tmp_path: Path
) -> None:
    settings.MEDIA_ROOT = tmp_path
    user = get_shared_manager()
    api = APIClient()
    api.force_login(user)
    scaler = SCALERS[key]
    ctx = scaler.build(user)
    url = scaler.url(ctx)

    # Warm-up before each count: one-off per-process lookups and lazy
    # first-read writes (owner evaluation, finance / summary snapshots) are not
    # N+1. A per-item read repeats on every request, so it still shows.
    api.get(url)
    queries_small, sql_small, body_small = _counted_get(api, url)

    for _ in range(GROWTH_STEPS):
        scaler.grow(ctx)
    api.get(url)
    queries_large, sql_large, body_large = _counted_get(api, url)

    if scaler.static_payload is None:
        assert body_large != body_small, (
            f"grow() did not change the {url} payload -- it misses the "
            "action's data (or set static_payload with the reason)"
        )
    assert queries_large == queries_small, (
        f"{url} ran {queries_small} queries before growth but {queries_large} "
        f"after {GROWTH_STEPS} grow steps (N+1). Repeated after growth:\n"
        + _grown_queries(sql_small, sql_large)
    )


def _grown_queries(before: list[str], after: list[str]) -> str:
    """The query shapes (literals masked) that ran more often after growth."""
    shape = Counter(re.sub(r"\d+", "N", sql) for sql in before)
    grown = Counter(re.sub(r"\d+", "N", sql) for sql in after) - shape
    return "\n".join(f"+{n}x {sql}" for sql, n in grown.most_common())


def test_every_detail_get_action_has_a_scaler() -> None:
    actions = _detail_get_actions()
    scaled = {key[:2] for key in SCALERS}
    missing = actions - scaled - set(EXEMPT)
    assert not missing, (
        f"Detail GET actions with no N+1 scaler: {sorted(missing)}. Add a SCALERS "
        "entry (or an EXEMPT entry with the reason) in "
        "test_detail_action_query_counts.py."
    )
    stale = (scaled | set(EXEMPT)) - actions
    assert not stale, f"SCALERS / EXEMPT name unknown actions: {sorted(stale)}"
    assert not scaled & set(EXEMPT), "an action is both scaled and EXEMPT"
    assert set(KNOWN_N_PLUS_ONE) <= set(SCALERS), "KNOWN_N_PLUS_ONE names no case"
