"""Per-tick mechanics for ``BatchSimulator._simulate_round``.

Pure tick mechanics — imports from ``sim_helpers/*`` only, no Django ORM
imports. The free functions here are called from
``BatchSimulator._simulate_round`` (in ``entrypoints.py``) at well-defined
points in the per-tick loop:

* memory observation + score / nuke broadcasts (MECH-06)
* nuke-reaction flag application (MECH-04)
* medic-under-fire alert (MECH-06)

History: these lived at module-top in the pre-split ``matches/simulation.py``
and were always free functions (not methods). The split moves them into a
dedicated tick-loop module so the per-tick mechanics are one place to read;
the only sibling-callers are ``BatchSimulator._simulate_round`` and the shot
resolver's lazy import (``sim_helpers/shot.py`` lazy-imports
``_check_medic_under_fire`` / ``_broadcast_communication`` /
``_update_player_memory`` from ``matches.simulation`` to avoid a circular
import; ``__init__.py`` re-exports keep that import line working).
"""

import math as _math
import random

from ..sim_helpers.time_constants import (
    MEDIC_UNDER_FIRE_WINDOW_TICKS,
    SCORE_BROADCAST_PERIOD_TICKS,
)


def _str_tag_id(player) -> str:
    """Return a consistent string tag ID for memory system usage.

    PlayerState objects have a string ``tag_id`` field directly.
    PlayerRoundState objects use ``string_tag_id`` property (added in MECH-06).
    """
    string_tag = getattr(player, "string_tag_id", None)
    if string_tag is not None:
        return string_tag
    return str(getattr(player, "tag_id", f"{player.team_color}_{player.role}"))


def _observe_lives(observer, seen) -> int | None:
    """Roll to observe seen player's current lives based on observer's resource_awareness.

    Chance = min(75, resource_awareness/4 + (resource_awareness/100) * (1-lives_ratio) * 50).
    At ra=100, lives_ratio=0.05 the chance is ~72.5%; at lives_ratio=0 it caps at 75%.
    Returns current lives on success, None on failure.
    """
    ra = getattr(observer, "resource_awareness", 50)
    max_lives = getattr(seen, "max_lives", getattr(seen, "starting_lives", 1))
    lives_ratio = seen.final_lives / max(1, max_lives)
    base_pct = ra / 4.0
    enemy_low_factor = max(0.0, 1.0 - lives_ratio) * (ra / 100.0)
    chance = min(75.0, base_pct + enemy_low_factor * 50.0)
    if random.random() * 100 < chance:
        return seen.final_lives
    return None


def _update_player_memory(observer, seen_players: list, second: float) -> bool:
    """MECH-06: update observer's memory with directly observed players.

    Cell, role, and status are always recorded. Current lives are added when the
    observer wins a resource_awareness roll (see _observe_lives).

    Returns True if any entry was new or had a changed cell or status — i.e. the
    memory actually gained information worth broadcasting to teammates.
    """
    observer_memory = getattr(observer, "player_memory", None)
    if observer_memory is None:
        observer.player_memory = {}
        observer_memory = observer.player_memory
    changed = False
    for seen in seen_players:
        if seen.cell_row is not None:
            tag_id = _str_tag_id(seen)
            new_cell = (seen.cell_row, seen.cell_col)
            if not seen.is_taggable_at(second):
                status = "downed"
            elif not seen.is_active_at(second):
                status = "reset_window"
            else:
                status = "active"
            existing = observer_memory.get(tag_id)
            if (
                existing is None
                or existing.get("cell") != new_cell
                or existing.get("status") != status
            ):
                changed = True
            entry: dict = {
                "cell": new_cell,
                "timestamp": second,
                "role": seen.role,
                "status": status,
            }
            observed_lives = _observe_lives(observer, seen)
            if observed_lives is not None:
                entry["lives"] = observed_lives
            observer_memory[tag_id] = entry
    return changed


def _broadcast_communication(
    actor,
    all_alive: list,
    movement_ctx,
    second: float,
) -> None:
    """MECH-06: per-tick communication broadcast.

    Rolls actor.communication / 100 probability. On success, shares actor's memory
    entries for enemy players with all living allies within the communication range
    (Euclidean half-diagonal of the map).
    """
    communication = getattr(actor, "communication", 0)
    if communication <= 0:
        return
    if random.random() * 100 >= communication:
        return

    # Compute communication range from map dimensions
    if movement_ctx is not None:
        zone_data = (
            movement_ctx.get_zone_data()
            if hasattr(movement_ctx, "get_zone_data")
            else movement_ctx.get("zone_data")
        )
        if zone_data:
            rows = len(zone_data)
            cols = len(zone_data[0]) if rows else 0
            comm_range = _math.sqrt(rows**2 + cols**2) / 2.0
        else:
            comm_range = float("inf")
    else:
        comm_range = float("inf")

    actor_r = actor.cell_row
    actor_c = actor.cell_col
    actor_team = actor.team_color
    actor_memory = getattr(actor, "player_memory", None)
    if not actor_memory:
        return

    # Filter to enemy-only memory entries, then pick the single highest-priority one.
    # Priority order (most tactically important first): heavy → commander → medic → ammo → scout
    _COMM_ROLE_PRIORITY = {
        "heavy": 0,
        "commander": 1,
        "medic": 2,
        "ammo": 3,
        "scout": 4,
    }
    enemy_color = "blue" if actor_team == "red" else "red"
    best_tag_id = None
    best_entry = None
    best_priority = len(_COMM_ROLE_PRIORITY)
    for tag_id, entry in actor_memory.items():
        if not (isinstance(tag_id, str) and tag_id.startswith(enemy_color)):
            continue
        priority = _COMM_ROLE_PRIORITY.get(
            entry.get("role", ""), len(_COMM_ROLE_PRIORITY)
        )
        if priority < best_priority:
            best_priority = priority
            best_tag_id = tag_id
            best_entry = entry
    if best_entry is None:
        return

    for ally in all_alive:
        if ally.team_color != actor_team or ally is actor:
            continue
        if ally.cell_row is None or ally.cell_col is None:
            continue
        # Check distance
        if actor_r is not None and actor_c is not None:
            dist = _math.sqrt(
                (ally.cell_row - actor_r) ** 2 + (ally.cell_col - actor_c) ** 2
            )
            if dist > comm_range:
                continue
        ally_memory = getattr(ally, "player_memory", None)
        if ally_memory is None:
            ally.player_memory = {}
            ally_memory = ally.player_memory
        existing = ally_memory.get(best_tag_id)
        if existing is None or best_entry["timestamp"] > existing["timestamp"]:
            ally_memory[best_tag_id] = dict(best_entry)


def _apply_score_broadcast(
    all_alive: list,
    second: float,
    period: int = SCORE_BROADCAST_PERIOD_TICKS,
) -> None:
    """MECH-06: every ``period`` time-units, compute which team is winning and
    update score_broadcast_state.

    Stores {"winning_team": "red"|"blue"|"tied", "timestamp": second} on each
    player. Players whose score_broadcast_next <= second get the update.

    TIME-01: ``period`` is the broadcast cadence in the caller's time domain.
    BatchSimulator (tick-native) uses the default SCORE_BROADCAST_PERIOD_TICKS;
    ResourceBasedSimulator passes its seconds-domain cadence (180) explicitly so
    its internal behaviour stays byte-identical.
    """
    red_pts = sum(p.counters.points_scored for p in all_alive if p.team_color == "red")
    blue_pts = sum(
        p.counters.points_scored for p in all_alive if p.team_color == "blue"
    )
    if red_pts > blue_pts:
        winning_team = "red"
    elif blue_pts > red_pts:
        winning_team = "blue"
    else:
        winning_team = "tied"

    for player in all_alive:
        next_broadcast = getattr(player, "score_broadcast_next", period)
        if second >= next_broadcast:
            player.score_broadcast_state = {
                "winning_team": winning_team,
                "timestamp": second,
            }
            player.score_broadcast_next = next_broadcast + period


def _apply_nuke_activation_broadcast(
    commander,
    target_team_players: list,
    second: float,
) -> None:
    """MECH-06: when a nuke is activated, all alive enemy-team players learn the
    Commander's current cell via memory update.
    """
    if commander.cell_row is None:
        return
    cmd_tag = _str_tag_id(commander)
    for p in target_team_players:
        if p.final_lives <= 0:
            continue
        p_memory = getattr(p, "player_memory", None)
        if p_memory is None:
            p.player_memory = {}
            p_memory = p.player_memory
        p_memory[cmd_tag] = {
            "cell": (commander.cell_row, commander.cell_col),
            "timestamp": second,
            "role": "commander",
        }


def _check_medic_under_fire(
    medic,
    all_alive: list,
    second: float,
    window: int = MEDIC_UNDER_FIRE_WINDOW_TICKS,
) -> None:
    """MECH-06: when a Medic is hit 2× within ``window``, alert all living teammates.

    Appends current second to medic.medic_hit_times, trims entries older than
    ``window``, and if ≥ 2 hits remain, updates all alive teammates' memory
    with the medic's cell.

    TIME-01: ``window`` is in the caller's time domain. BatchSimulator uses the
    default MEDIC_UNDER_FIRE_WINDOW_TICKS; ResourceBasedSimulator passes its
    seconds-domain window (12) explicitly so its behaviour stays byte-identical.
    """
    hit_times = getattr(medic, "medic_hit_times", None)
    if hit_times is None:
        medic.medic_hit_times = []
        hit_times = medic.medic_hit_times
    hit_times.append(second)
    # Trim entries older than the window
    medic.medic_hit_times = [t for t in hit_times if second - t <= window]
    if len(medic.medic_hit_times) >= 2 and medic.cell_row is not None:
        medic_tag = _str_tag_id(medic)
        for p in all_alive:
            if p.team_color != medic.team_color or p is medic:
                continue
            if p.final_lives <= 0:
                continue
            p_memory = getattr(p, "player_memory", None)
            if p_memory is None:
                p.player_memory = {}
                p_memory = p.player_memory
            p_memory[medic_tag] = {
                "cell": (medic.cell_row, medic.cell_col),
                "timestamp": second,
                "role": "medic",
            }


def _apply_nuke_reaction_flags(all_alive: list, pending_nukes: list) -> None:
    """MECH-04: reset then set reacting_to_nuke for all alive players each tick.

    Caches game_awareness and player_awareness once per player so repeated
    @property calls (which hit stat_for_simulation) don't multiply with the
    number of pending nukes.
    """
    for p in all_alive:
        setattr(p, "reacting_to_nuke", False)
    if not pending_nukes:
        return
    awareness = {id(p): (p.game_awareness, p.player_awareness) for p in all_alive}
    for pending_nuke in pending_nukes:
        target_color = "blue" if pending_nuke.player.team_color == "red" else "red"
        for p in all_alive:
            if p.team_color != target_color:
                continue
            ga, pa = awareness[id(p)]
            if random.random() < (ga + pa) / 200.0:
                setattr(p, "reacting_to_nuke", True)
