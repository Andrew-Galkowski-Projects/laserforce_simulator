"""ANG-06f: the Playback data seam (ADR-0044, spec #305).

``round_playback`` builds each player's **Playback snapshots** from the
after-event values the event log already records; it never re-applies combat
rules. Pure apart from ORM reads, and it does not import the view modules.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from itertools import groupby
from operator import attrgetter

from django.db.models import QuerySet

from .models import GameEvent, GameRound, PlayerRoundState
from .sim_helpers.time_constants import (
    NOT_TARGETABLE_TICKS,
    RESPAWN_TICKS,
    TICKS_PER_ROUND,
)


def build_playback_map(game_round: GameRound) -> dict | None:
    """Reconstruct corridor-faithful player routes for the Playback map.

    Returns ``{"zone_size": int, "arena_map": {"id", "name"}, "players": [...]}``
    (consumed by the canvas overlay in ``game_round_events.html`` and the SPA's
    beta map), or ``None`` when the round ran without a map (3-zone fallback)
    or its map config can't be resolved. ANG-06f moved it here from
    ``views._build_playback_map`` (still re-exported there) and added
    ``arena_map`` so the SPA can fetch the processed image.

    Each ``event_type="movement"`` ``GameEvent`` records only its Advance's
    start + end cell (MOVE-01); the exact intermediate route is recomputed here
    with the simulator's own A* (``astar_path``) over the map's adjacency +
    elevation, so the trail follows corridors faithfully instead of cutting
    straight lines through walls. Per player the payload carries
    ``spawn = [r, c]`` and ``moves = [[tick, polyline], ...]`` where the
    polyline excludes the start cell and ends at the end cell (the
    ``astar_path`` shape); the client expands these into timed keyframes.
    """
    from .sim_helpers.map_loader import load_map_context
    from .sim_helpers.pathfinding import astar_path

    arena_map = game_round.arena_map
    if arena_map is None:
        return None
    try:
        mctx, zone_size = load_map_context(arena_map)
    except ValueError:
        return None
    if mctx is None or not zone_size:
        return None

    adj = mctx.get_adjacency()
    elevation = mctx.elevation_grid

    # Per player: list of (tick, polyline) where the polyline is the cells
    # walked that Advance (excludes the start, ends at the end cell). Prefer the
    # TRUE route persisted in metadata["route"] (newly-simulated rounds); fall
    # back to a fresh A* reconstruction for older rounds that predate it.
    moves_by_player: dict[int, list] = defaultdict(list)
    # (tick, event id) order; the per-player lists below keep it as-is.
    move_events = (
        game_round.events.filter(event_type="movement").order_by("timestamp", "id")
        # GameEvent reads game_round_id on load; deferring it costs one query
        # per movement event.
        .only("game_round_id", "actor_id", "timestamp", "metadata")
    )
    for ev in move_events:
        m = ev.metadata or {}
        try:
            start = (m["start_row"], m["start_col"])
            end = (m["end_row"], m["end_col"])
        except KeyError:
            continue
        if None in start or None in end:
            continue
        stored = m.get("route")
        if stored:
            polyline = [list(c) for c in stored]
        else:
            route = astar_path(start, end, adj, elevation)
            polyline = [list(c) for c in route] if route else [list(end)]
        moves_by_player[ev.actor_id].append((ev.timestamp, list(start), polyline))

    states = list(game_round.player_states.select_related("player").order_by("id"))

    # Number the two scouts per team (1/2) so the markers can be told apart.
    scout_nums: dict[int, int] = {}
    team_scouts: dict[str, list] = defaultdict(list)
    for ps in states:
        if ps.role == "scout":
            team_scouts[ps.team_color].append(ps)
    for scouts in team_scouts.values():
        for i, ps in enumerate(sorted(scouts, key=lambda s: s.player.name)):
            scout_nums[ps.player_id] = i + 1

    players = []
    for ps in states:
        moves = moves_by_player.get(ps.player_id, [])
        if moves:
            spawn = list(moves[0][1])
        elif ps.cell_row is not None and ps.cell_col is not None:
            spawn = [ps.cell_row, ps.cell_col]
        else:
            # No cell position recorded — defensive; skip on a map round.
            continue
        out_moves = [[ts, polyline] for ts, _start, polyline in moves]
        players.append(
            {
                "id": ps.player_id,
                "name": ps.player.name,
                "team": ps.team_color,
                "role": ps.role,
                "scout": scout_nums.get(ps.player_id, 0),
                "spawn": spawn,
                "moves": out_moves,
            }
        )

    return {
        "zone_size": zone_size,
        "arena_map": {"id": arena_map.pk, "name": arena_map.name},
        "players": players,
    }


def _opening_snapshot(ps: PlayerRoundState) -> dict:
    """The tick-0 snapshot: the ``starting_*`` fields, everything else zero."""
    return {
        "tick": 0,
        "score": 0,
        "lives": ps.starting_lives,
        "shots": ps.starting_shots,
        "missiles": ps.starting_missiles,
        "sp": 0,
        "tags": 0,
        "misses": 0,
        "downed_at": None,
        "eliminated": False,
        "rapid_fire": False,
        "nuking": False,
    }


_RESUPPLY_TYPES = ("resupply_ammo", "resupply_lives")

#: Points a nuke detonation awards (``_complete_nuke``).
NUKE_POINTS = 500


def _special_kind(ev: GameEvent, meta: dict) -> str | None:
    """Recognise a ``special`` event's kind: ``rapid_fire``, ``nuke_armed``,
    ``nuke_detonated`` or ``None`` (team heal / ammo, Commander shield).

    Activation has no structured marker, so the description text written in
    ``_use_special`` is the only signal; that matching lives here alone. A
    detonation is the ``special`` carrying ``targets`` and 500 points.
    """
    description = ev.description or ""
    if description.endswith("activates rapid fire"):
        return "rapid_fire"
    if description.endswith("activates nuke"):
        return "nuke_armed"
    if "targets" in meta and ev.points_awarded == NUKE_POINTS:
        return "nuke_detonated"
    return None


def _set_values(state: dict, lives, shots, points, tick: int) -> None:
    """Apply one player's logged after-event values; a lives decrease is a Down
    (``record_down`` is the engine's life-loss chokepoint)."""
    if lives is not None:
        if lives < state["lives"]:
            state["downed_at"] = tick
        state["lives"] = lives
    if shots is not None:
        state["shots"] = shots
    if points is not None:
        state["score"] = points


def _apply_actor(
    actor: dict, fuses: dict[int, int], ev: GameEvent, meta: dict, tick: int
) -> None:
    """Fold one event into its actor's running state (and the armed-nuke
    ``fuses``, Commander id -> ``fires_at``)."""
    _set_values(
        actor,
        meta.get("actor_lives"),
        meta.get("actor_shots"),
        meta.get("actor_points"),
        tick,
    )
    if meta.get("sp") is not None:
        actor["sp"] = meta["sp"]
    etype = ev.event_type
    if etype == "tag":
        actor["tags"] += 1
    elif etype == "miss":
        actor["misses"] += 1
    elif etype == "locking":
        actor["missiles"] -= 1
    elif etype == "missiled" and meta.get("result") == "hit":
        # MECH-16: the engine spends a hit missile a second time. Drop
        # this -1 in the same change that fixes MECH-16 (ADR-0044).
        actor["missiles"] -= 1
    elif etype == "nuke_cancelled":
        _end_nuke(actor, fuses, ev.actor_id)
    elif etype == "special":
        _apply_special(actor, fuses, ev, meta)


def _apply_special(
    actor: dict, fuses: dict[int, int], ev: GameEvent, meta: dict
) -> None:
    """A ``special`` event's effect on its actor: rapid fire on, nuke armed
    (fuse lit) or nuke detonated (fuse out)."""
    kind = _special_kind(ev, meta)
    if kind == "rapid_fire":
        actor["rapid_fire"] = True
    elif kind == "nuke_armed":
        actor["nuking"] = True
        fuses[ev.actor_id] = meta.get("fires_at", TICKS_PER_ROUND + 1)
    elif kind == "nuke_detonated":
        _end_nuke(actor, fuses, ev.actor_id)


def _end_nuke(actor: dict, fuses: dict[int, int], pid: int) -> None:
    """Turn a Commander's ``nuking`` off and drop its fuse, if any."""
    actor["nuking"] = False
    fuses.pop(pid, None)


def _apply_target(target: dict, ev: GameEvent, meta: dict, tick: int) -> None:
    """Fold one event into its target's running state."""
    _set_values(
        target,
        meta.get("target_lives"),
        meta.get("target_shots"),
        meta.get("target_points"),
        tick,
    )
    if ev.event_type in _RESUPPLY_TYPES:
        # ``attempt_resupply`` stamps ``last_downed_time``.
        target["downed_at"] = tick
        # ... and silently ends a Scout's rapid fire.
        target["rapid_fire"] = False
    elif ev.event_type == "elimination":
        target["eliminated"] = True


def _apply_event(
    state: dict[int, dict], fuses: dict[int, int], ev: GameEvent, tick: int
) -> None:
    """Fold one event into the running per-player state (and the armed-nuke
    ``fuses``, Commander id -> ``fires_at``)."""
    meta = ev.metadata or {}
    actor = state.get(ev.actor_id)
    if actor is not None:
        _apply_actor(actor, fuses, ev, meta, tick)
    target = state.get(ev.target_id) if ev.target_id else None
    if target is not None:
        _apply_target(target, ev, meta, tick)
    _apply_team_targets(state, meta, tick)


def _apply_team_targets(state: dict[int, dict], meta: dict, tick: int) -> None:
    """Team specials (heal / ammo) and nuke detonations carry per-player
    after-event values for teammates and opponents alike."""
    for entry in meta.get("targets") or []:
        affected = state.get(entry.get("pid"))
        if affected is not None:
            _set_values(
                affected,
                entry.get("lives"),
                entry.get("shots"),
                entry.get("points"),
                tick,
            )


@dataclass
class _Timeline:
    """The running fold of one round's event log.

    ``current`` is each player's state after the events folded so far,
    ``snapshots`` the per-player snapshot lists being built, and ``fuses`` the
    armed nukes (Commander id -> ``fires_at``).
    """

    current: dict[int, dict]
    snapshots: dict[int, list[dict]]
    fuses: dict[int, int] = field(default_factory=dict)


def _opening_timeline(states: list[PlayerRoundState]) -> _Timeline:
    """A timeline holding only each player's tick-0 opening snapshot."""
    current = {ps.player_id: _opening_snapshot(ps) for ps in states}
    snapshots = {pid: [dict(snap)] for pid, snap in current.items()}
    return _Timeline(current=current, snapshots=snapshots)


def _flush(timeline: _Timeline, tick: int) -> None:
    """Append a snapshot at ``tick`` for every player whose state changed."""
    for pid, snap in timeline.current.items():
        last = timeline.snapshots[pid][-1]
        if any(snap[k] != last[k] for k in snap if k != "tick"):
            timeline.snapshots[pid].append({**snap, "tick": tick})


def _expire_fuses(timeline: _Timeline, fires_at: int) -> None:
    """A fuse that burns out with no detonation or cancel logged turns
    ``nuking`` off at its ``fires_at``."""
    for pid in [p for p, t in timeline.fuses.items() if t == fires_at]:
        _end_nuke(timeline.current[pid], timeline.fuses, pid)


def _burn_fuses(timeline: _Timeline, before: int) -> None:
    """Expire every fuse whose ``fires_at`` is before ``before``, one snapshot
    tick per distinct ``fires_at``."""
    for fires_at in sorted({t for t in timeline.fuses.values() if t < before}):
        _expire_fuses(timeline, fires_at)
        _flush(timeline, fires_at)


def _playback_events(game_round: GameRound) -> QuerySet[GameEvent]:
    """The round's non-movement events in (tick, event id) order."""
    return (
        game_round.events.exclude(event_type="movement")
        .order_by("timestamp", "id")
        .only(
            # GameEvent reads game_round_id on load; deferring it costs one
            # query per event.
            "game_round_id",
            "timestamp",
            "event_type",
            "actor_id",
            "target_id",
            "points_awarded",
            "description",
            "metadata",
        )
    )


def _fold_events(timeline: _Timeline, game_round: GameRound) -> None:
    """Fold the round's non-movement events into ``timeline``, one snapshot
    tick per event tick (same-tick events collapse into the last state)."""
    for tick, group in groupby(
        _playback_events(game_round), key=attrgetter("timestamp")
    ):
        _burn_fuses(timeline, before=tick)
        _expire_fuses(timeline, tick)
        for ev in group:
            _apply_event(timeline.current, timeline.fuses, ev, tick)
        _flush(timeline, tick)
    _burn_fuses(timeline, before=TICKS_PER_ROUND + 1)


def round_playback(game_round: GameRound) -> dict:
    """The ``GET /api/rounds/<id>/playback/`` payload.

    ``players`` is in ``PlayerRoundState.id`` order. Each player gets a tick-0
    opening snapshot, then one snapshot per tick at which any of their fields
    changed (same-tick events collapse into the state after the last one).
    """
    states = list(game_round.player_states.order_by("id"))
    timeline = _opening_timeline(states)
    _fold_events(timeline, game_round)
    return {
        "duration_ticks": TICKS_PER_ROUND,
        "not_targetable_ticks": NOT_TARGETABLE_TICKS,
        "respawn_ticks": RESPAWN_TICKS,
        "players": [
            {"id": ps.player_id, "snapshots": timeline.snapshots[ps.player_id]}
            for ps in states
        ],
        "map": build_playback_map(game_round),
    }
