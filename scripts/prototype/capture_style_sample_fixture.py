"""ANG-04b prototype only (never merged): capture the style-sample JSON fixture.

Run from the repo root (SQLite dev DB, after ``migrate``):

    python laserforce_simulator/manage.py shell -c "exec(open(r'scripts/prototype/capture_style_sample_fixture.py').read())"

Generates two Teams with the LG-00 team generator (``teams.services._generate_teams``,
the code behind ``/teams/generate/``), then simulates one round through the real
simulator + persistence path with ``random.seed(seed)`` for seed 42, 43, ... until
the persisted round holds at least one tag, missile, special, nuke, resupply and
elimination. Writes ``frontend/src/app/prototype/style-samples/fixture/style-sample-fixture.json``.
No backend code is changed: this only calls existing functions.
"""

import json
import random
from pathlib import Path

from matches.models import GameEvent
from matches.sim_helpers.map_loader import load_map_context
from matches.simulation import BatchSimulator
from teams.constants import PLAYER_NAMES, TEAM_NAMES
from teams.services import _generate_teams

FIRST_SEED = 42
PLAYERS_PER_TEAM = 7
SLICE_BEFORE = 20
SLICE_AFTER = 20
TICK_SECONDS = 0.5
ROUND_TICKS = 1800
OUT_PATH = Path(
    "frontend/src/app/prototype/style-samples/fixture/style-sample-fixture.json"
)
REQUIRED_KINDS = ("tag", "missile", "special", "nuke", "resupply", "elimination")
ROLE_CODES = {
    "commander": "CMD",
    "heavy": "HVY",
    "scout": "SCT",
    "ammo": "AMO",
    "medic": "MED",
}


def event_kind(ev):
    """Map a GameEvent.event_type to one of the seven --lf-ev-* token kinds."""
    t = ev.event_type
    if t == "special":
        return "nuke" if "nuke" in ev.description.lower() else "special"
    if t == "nuke_cancelled":
        return "nuke"
    if t in ("locking", "missiled"):
        return "missile"
    if t in ("resupply_ammo", "resupply_lives"):
        return "resupply"
    if t in ("elimination", "team_elimination"):
        return "elimination"
    if t == "miss":
        return "miss"
    return "tag"  # tag, medic_reset


def role_code(role):
    return ROLE_CODES.get(str(role).lower(), str(role)[:3].upper())


def simulate_seeded_round(sim, team_red, team_blue, seed):
    """Mirror BatchSimulator._simulate_and_flush_round, but with a fixed seed."""
    movement_ctx, zone_size = load_map_context(None)
    random.seed(seed)
    events = []
    result, red_players, blue_players = sim._simulate_round(
        list(team_red.active_roster),
        list(team_blue.active_roster),
        event_log=events,
        movement_ctx=movement_ctx,
    )
    return sim._flush_to_db(
        team_red,
        team_blue,
        result,
        red_players,
        blue_players,
        events,
        rng_seed=seed,
        movement_ctx=movement_ctx,
        match=None,
        round_number=1,
        arena_map=None,
        zone_size=zone_size,
        fidelity="combat",
    )


def capture():
    rng = random.Random(FIRST_SEED)
    team_names = list(TEAM_NAMES)
    player_names = list(PLAYER_NAMES)
    random.Random(FIRST_SEED).shuffle(team_names)
    random.Random(FIRST_SEED + 1).shuffle(player_names)
    teams = _generate_teams(
        2,
        PLAYERS_PER_TEAM,
        rng=rng,
        mean=50,
        std_dev=15,
        team_names_pool=team_names,
        player_names_pool=player_names,
    )
    team_red, team_blue = teams

    sim = BatchSimulator()
    seed = FIRST_SEED
    while True:
        game_round = simulate_seeded_round(sim, team_red, team_blue, seed)
        all_events = list(
            GameEvent.objects.filter(game_round=game_round)
            .select_related("actor", "target")
            .order_by("timestamp", "id")
        )
        kinds = {event_kind(ev) for ev in all_events}
        missing = [k for k in REQUIRED_KINDS if k not in kinds]
        if not missing:
            break
        print(f"seed {seed}: missing {missing}; trying the next seed")
        game_round.delete()
        seed += 1

    states = list(game_round.player_states.select_related("player"))
    side_of_player = {s.player_id: s.team_color for s in states}

    first_elim = next(
        i for i, ev in enumerate(all_events) if ev.event_type == "elimination"
    )
    lo = max(0, first_elim - SLICE_BEFORE)
    sliced = all_events[lo : first_elim + SLICE_AFTER]
    # So every --lf-ev-* colour shows up in the timeline, pull in the first event
    # of any required kind the contiguous window happens to miss.
    for kind in REQUIRED_KINDS:
        if not any(event_kind(ev) == kind for ev in sliced):
            sliced.append(next(ev for ev in all_events if event_kind(ev) == kind))
    sliced.sort(key=lambda ev: (ev.timestamp, ev.id))

    winner = None
    if game_round.winner_id == team_red.pk:
        winner = "red"
    elif game_round.winner_id == team_blue.pk:
        winner = "blue"

    def player_state(s):
        shots = s.tags_made + s.shots_missed
        eliminated = s.was_eliminated_at <= ROUND_TICKS
        return {
            "playerName": s.player.name,
            "side": s.team_color,
            "role": role_code(s.role),
            "points": s.points_scored,
            "mvp": round(float(s.get_mvp), 2),
            "tagsMade": s.tags_made,
            "timesTagged": s.times_tagged,
            "hitDiff": round(s.tags_made / max(1, s.times_tagged), 2),
            "accuracy": round(s.tags_made / shots * 100) if shots else 0,
            "medicHits": s.final_medic_hits,
            "lives": s.final_lives,
            "startingLives": s.starting_lives,
            "shotsFired": shots,
            "missilesLanded": s.missiles_landed,
            "timesMissiled": s.times_missiled,
            "specialsUsed": s.specials_used,
            "resuppliesGiven": s.resupplies_given,
            "eliminatedAtTick": s.was_eliminated_at if eliminated else None,
            "survivedSeconds": (
                int(s.was_eliminated_at * TICK_SECONDS)
                if eliminated
                else int(ROUND_TICKS * TICK_SECONDS)
            ),
        }

    def event_row(ev):
        return {
            "tick": ev.timestamp,
            "type": event_kind(ev),
            "rawType": ev.event_type,
            "actorName": ev.actor.name,
            "actorSide": side_of_player.get(ev.actor_id),
            "targetName": ev.target.name if ev.target else None,
            "targetSide": side_of_player.get(ev.target_id) if ev.target else None,
            "points": ev.points_awarded,
            "description": ev.description,
        }

    def team_row(team):
        return {
            "id": team.pk,
            "name": team.name,
            "wins": team.wins,
            "losses": team.losses,
            "players": [
                {
                    "name": p.name,
                    "preferredRoles": [role_code(r) for r in (p.preferred_roles or [])],
                }
                for p in team.players.order_by("pk")
            ],
        }

    side_order = {"red": 0, "blue": 1}
    fixture = {
        "capturedFrom": {
            "seed": seed,
            "generatorSeed": FIRST_SEED,
            "eventCountTotal": len(all_events),
        },
        "teams": [team_row(team_red), team_row(team_blue)],
        "round": {
            "roundNumber": game_round.round_number,
            "redTeamName": team_red.name,
            "blueTeamName": team_blue.name,
            "redPoints": game_round.red_points,
            "bluePoints": game_round.blue_points,
            "redEliminated": game_round.red_team_eliminated,
            "blueEliminated": game_round.blue_team_eliminated,
            "eliminationBonus": sim.elimination_bonus,
            "eliminatedAtTick": (
                game_round.eliminated_at
                if game_round.eliminated_at <= ROUND_TICKS
                else None
            ),
            "winner": winner,
            "playerStates": [
                player_state(s)
                for s in sorted(
                    states, key=lambda s: (side_order[s.team_color], -s.points_scored)
                )
            ],
        },
        "events": [event_row(ev) for ev in sliced],
    }

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(fixture, indent=2) + "\n", encoding="utf-8")

    round_counts = {}
    for ev in all_events:
        k = event_kind(ev)
        round_counts[k] = round_counts.get(k, 0) + 1
    slice_counts = {}
    for ev in sliced:
        k = event_kind(ev)
        slice_counts[k] = slice_counts.get(k, 0) + 1
    print(f"seed used: {seed}")
    print(f"teams: {team_red.name} (red) vs {team_blue.name} (blue)")
    print(f"score: {game_round.red_points} - {game_round.blue_points}")
    print(f"round event counts: {sorted(round_counts.items())}")
    print(f"slice event counts ({len(sliced)}): {sorted(slice_counts.items())}")
    print(f"wrote {OUT_PATH}")


capture()
