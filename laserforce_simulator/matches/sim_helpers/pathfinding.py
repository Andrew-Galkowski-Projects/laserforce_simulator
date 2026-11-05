from __future__ import annotations

import heapq
import random
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .map_context import MapContext

# Staleness thresholds (SECONDS) by role of the REMEMBERED player.
# Stationary support roles stay valid longer; mobile roles go stale quickly.
# This is the DEFAULT (seconds) table: ResourceBasedSimulator stays
# second-internal and byte-identical, and all existing unit tests pass
# seconds, so they keep using these. The tick-native BatchSimulator passes
# time_domain="ticks" to select _STALE_THRESHOLD_TICKS instead (TIME-01).
_STALE_THRESHOLD = {
    "heavy": 60,
    "medic": 60,
    "ammo": 60,
    "scout": 15,
    "commander": 15,
}

# TIME-01: tick-domain staleness thresholds (STALENESS_SLOW_TICKS=120 for
# slow/stationary roles, STALENESS_FAST_TICKS=30 for mobile roles). Used only
# when time_domain == "ticks" (BatchSimulator).
from .downed_option import active_before_nuke, downed_goal, plan_is_live
from .follow_rule import chased_tag_ids
from .goal_scoring import draw_goal, enemy_base_cells
from .time_constants import (
    GOAL_RECOMPUTE_PERIOD_TICKS,
    STALENESS_FAST_TICKS,
    STALENESS_SLOW_TICKS,
    TICK_SECONDS,
    TICKS_PER_ROUND,
)

#: MOVE-06: the commitment expiry of a drawn positioning goal: after the round,
#: so it is held until reached (action goals keep the 4-tick cadence).
HELD_GOAL_EXPIRY: int = TICKS_PER_ROUND + 1

_STALE_THRESHOLD_TICKS = {
    "heavy": STALENESS_SLOW_TICKS,
    "medic": STALENESS_SLOW_TICKS,
    "ammo": STALENESS_SLOW_TICKS,
    "scout": STALENESS_FAST_TICKS,
    "commander": STALENESS_FAST_TICKS,
}


def _stale_table(time_domain: str) -> dict:
    """Return the staleness threshold table for the caller's time domain."""
    return _STALE_THRESHOLD_TICKS if time_domain == "ticks" else _STALE_THRESHOLD


def _elevation_at(r: int, c: int, elevation_data: dict | None = None) -> float:
    # MAP-09 will populate elevation_data; for now all cells are at elevation 0
    if elevation_data and (r, c) in elevation_data:
        return elevation_data[(r, c)]
    return 0


def _movement_cost(
    from_cell: tuple[int, int],
    to_cell: tuple[int, int],
    elevation_data: dict | None = None,
) -> float:
    # Uphill costs 1.5x; flat/downhill costs 1.0
    if _elevation_at(*to_cell, elevation_data) > _elevation_at(
        *from_cell, elevation_data
    ):
        return 1.5
    return 1.0


# Cells passable for movement: floor (1) and legacy red/blue zones (2, 3 — backward compat).
# High wall (0), low wall (4), and windowed wall (5) all block movement.
_MOVEMENT_PASSABLE = {1, 2, 3}


def build_movement_adjacency(
    zone_data: list[list[int]],
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Return 4-connected passable neighbors for every movement-passable cell.

    Passable cell values: 1 (floor), 2/3 (legacy red/blue zone — backward compat).
    Wall types 0 (high), 4 (low), and 5 (windowed) all block movement.
    Cells excluded from the dict entirely so `cell in adj` is a passability check.
    """
    rows = len(zone_data)
    cols = len(zone_data[0]) if rows else 0
    adj: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for r in range(rows):
        for c in range(cols):
            if zone_data[r][c] not in _MOVEMENT_PASSABLE:
                continue
            neighbors = []
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if (
                    0 <= nr < rows
                    and 0 <= nc < cols
                    and zone_data[nr][nc] in _MOVEMENT_PASSABLE
                ):
                    neighbors.append((nr, nc))
            adj[(r, c)] = neighbors

    return adj


#: MOVE-07: the most a player's route noise adds to one step's cost.
ROUTE_NOISE_MAX: float = 0.5


def route_noise(seed: int, cell: tuple[int, int]) -> float:
    """MOVE-07: a fixed per-player, per-cell number in [0, 1).

    An integer hash of ``(seed, row, col)``, so A* draws no RNG and the same
    seed always gives the same route (the path cache stays valid).
    """
    h = (seed * 0x9E3779B1 + cell[0] * 0x85EBCA77 + cell[1] * 0xC2B2AE3D) & 0xFFFFFFFF
    h ^= h >> 15
    h = (h * 0x2C1B3C6D) & 0xFFFFFFFF
    h ^= h >> 12
    return (h & 0xFFFF) / 65536.0


def astar_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: dict | None = None,
    route_seed: int | None = None,
) -> list[tuple[int, int]]:
    """Return the shortest path from start to goal, excluding start.

    The returned list is the ordered sequence of cells to step through,
    ending at ``goal``. Uses A* with a Manhattan-distance heuristic and
    optional elevation costs. Returns ``[]`` when start == goal, no path
    exists, or start is not in the adjacency graph.

    MOVE-07: with a ``route_seed`` each step into a cell costs up to
    ``ROUTE_NOISE_MAX`` more (``route_noise``), so the route is a
    near-shortest one particular to that player. Every step still costs at
    least 1, so the Manhattan heuristic stays admissible.
    """
    if start == goal or start not in adj:
        return []

    h = abs(goal[0] - start[0]) + abs(goal[1] - start[1])
    heap: list[tuple[float, float, tuple[int, int]]] = [(h, 0.0, start)]
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}

    while heap:
        _, g, current = heapq.heappop(heap)

        if g > g_score.get(current, float("inf")):
            continue

        if current == goal:
            path: list[tuple[int, int]] = []
            node = goal
            while node != start:
                path.append(node)
                node = came_from[node]
            path.reverse()
            return path

        for neighbor in adj.get(current, []):
            cost = g + _movement_cost(current, neighbor, elevation_data)
            if route_seed is not None:
                cost += ROUTE_NOISE_MAX * route_noise(route_seed, neighbor)
            if cost < g_score.get(neighbor, float("inf")):
                g_score[neighbor] = cost
                came_from[neighbor] = current
                nh = abs(goal[0] - neighbor[0]) + abs(goal[1] - neighbor[1])
                heapq.heappush(heap, (cost + nh, cost, neighbor))

    return []


def astar_next_step(
    start: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: dict | None = None,
) -> tuple[int, int]:
    """Return the first step on the shortest path from start toward goal.

    Thin wrapper over :func:`astar_path`; returns ``start`` unchanged when
    start == goal, no path exists, or start is not in the adjacency graph.
    """
    path = astar_path(start, goal, adj, elevation_data)
    return path[0] if path else start


def astar_advance(
    start: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    steps: int,
    elevation_data: dict | None = None,
) -> tuple[int, int]:
    """Return the cell reached after walking up to ``steps`` cells toward goal.

    Walks the A* path; stops early at ``goal`` (no overshoot). Returns
    ``start`` when ``steps <= 0``, no path exists, or start is not navigable.
    """
    if steps <= 0:
        return start
    path = astar_path(start, goal, adj, elevation_data)
    if not path:
        return start
    return path[min(steps, len(path)) - 1]


def astar_advance_cached(
    player: Any,
    current: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    steps: int,
    elevation_data: dict | None = None,
) -> tuple[int, int]:
    """Re-step a goal-keyed cached A* route instead of recomputing per tick.

    MOVE-02 ([ADR-0008](../../../docs/adr/0008-path-commitment-via-goal-keyed-cache.md)).
    ``BatchSimulator`` only — ``ResourceBasedSimulator`` keeps per-tick
    ``astar_advance`` (RBS is DB-bound and removed by SIM-09).

    Manages ``player._path_cache``: a ``(cached_goal, remaining_cells,
    anchor)`` tuple where ``remaining_cells`` is the ordered list of cells
    still to walk (head = next cell), mirroring the route shape ``astar_path``
    returns (excludes the current cell, last element is ``goal``), and
    ``anchor`` is the cell the previous re-step left the player on. A legacy
    2-tuple (no ``anchor``) is tolerated for hand-built test caches.

    A full ``astar_path`` recompute happens iff ANY of:
      1. the cache is ``None`` or empty;
      2. the live ``goal`` differs from the cached goal (goal changed);
      3. the player was Downed/respawned since the last move — the BatchSim
         life-loss sites (``_record_down``) clear ``_path_cache`` to ``None``,
         which falls into case 1 here (knocked off-path);
      4. the player's live ``current`` no longer matches the ``anchor`` —
         some other mechanic displaced it off the committed route without
         clearing the cache (enforces the off-path invariant rather than
         relying on it; no such mechanic exists today);
      5. the next cached cell is no longer in ``adj`` (blocked — a cheap
         future-proof check; map adjacency is immutable per round so this
         never actually fires in production).
    Otherwise the committed route is RE-STEPPED: up to ``steps`` cells are
    popped from the cached list, advancing the player and stopping at ``goal``
    with no overshoot — identical traversal semantics to ``astar_advance``.
    Cache exhaustion and an ``only_move`` 2× ``steps`` are NOT recompute
    triggers; when the cache empties the player has reached ``goal`` and the
    next tick's fresh ``choose_goal_cell`` drives recompute-or-idle.

    Consumes **no RNG** — re-stepping a committed route is fully reproducible,
    so the SIM-07/SIM-08 serial == parallel contract still holds (the round is
    re-simulated in-worker; the transient cache never crosses the process
    boundary). Returns the cell reached (``current`` when ``steps <= 0`` or no
    route exists). ``astar_advance`` itself is left unchanged for RBS + tests.
    """
    # MOVE-03: every call records the cells actually traversed this call onto
    # ``player._last_step_cells`` (intermediate + end cells walked, excluding
    # ``current``); ``[]`` whenever nothing moves. The BatchSimulator Overwatch
    # resolution reads this to detect an enemy crossing a holder's LoS
    # mid-Advance. Set unconditionally before every return so a stale list from
    # a prior tick can never leak into the LoS check. Consumes no RNG.
    player._last_step_cells = []

    if steps <= 0:
        return current

    cache = getattr(player, "_path_cache", None)
    cached_goal = cache[0] if cache else None
    remaining = cache[1] if cache else None
    # Position anchor (cache[2]): the cell the previous re-step left the
    # player on. On a cache hit the player's live `current` MUST still equal
    # it — if any other mechanic moved the player off the committed route
    # without going through clear-on-Down (a future knockback/teleport; none
    # exists today), `current != anchor` forces a fresh recompute instead of
    # silently teleporting along the stale route. This makes the off-path
    # invariant *enforced* rather than relied upon. A legacy/hand-built
    # 2-tuple cache (no anchor) is tolerated — the check is simply skipped.
    anchor = cache[2] if cache and len(cache) > 2 else None

    needs_recompute = (
        not cache
        or not remaining
        or cached_goal != goal
        or (anchor is not None and current != anchor)
        # Future-proof only: map adjacency is immutable per round, so a cached
        # cell never actually leaves `adj` mid-round — kept as a cheap guard.
        or remaining[0] not in adj
    )

    if needs_recompute:
        remaining = astar_path(
            current,
            goal,
            adj,
            elevation_data,
            route_seed=getattr(player, "route_seed", None),
        )
        if not remaining:
            # Unreachable goal: no negative caching — this recomputes again
            # next tick too. Goals are normally reachable cells, so the
            # repeated full A* here stays rare and is not worth caching.
            player._path_cache = None
            return current
        # Copy so the in-place `del` below mutates only our working list,
        # never a list `astar_path` might hand back by reference.
        remaining = list(remaining)

    # Past the recompute guard `remaining` is always a non-empty list (the
    # `not cache`/`not remaining` triggers above guarantee it); assert it so
    # the type narrows from the cache's ``Any | None``.
    assert remaining is not None
    # Re-step the committed route: walk up to `steps` cells, stopping at goal
    # (no overshoot — `remaining` ends at goal, so capping at its length is
    # the same early-stop semantics as astar_advance).
    take = min(steps, len(remaining))
    reached = remaining[take - 1]
    # MOVE-03: the cells popped this call ARE the cells walked this tick
    # (``remaining`` excludes ``current`` and ends at ``goal``, so the slice is
    # the intermediate cells + the cell reached). Snapshot before the in-place
    # ``del`` so the cache mutation below doesn't empty it.
    player._last_step_cells = list(remaining[:take])
    del remaining[:take]
    if not remaining:
        # Route consumed — player is at goal; next tick recomputes/idles.
        player._path_cache = None
    else:
        # Re-anchor to the cell the player will occupy after this re-step
        # (the caller sets player.cell_{row,col} = reached) so the next
        # cache hit can detect off-route displacement. `remaining` is the
        # same list object retained here, so the in-place `del` above
        # persists the route consumption into the cached tuple.
        player._path_cache = (goal, remaining, reached)
    return reached


#: MOVE-08: the fastest a player moves (speed 100), in real-world feet a second.
MAX_PLAYER_FEET_PER_SECOND: float = 10.0


def feet_per_second(player) -> float:
    """MOVE-08: a player's top speed, ``MAX x (0.5 + 0.5 x speed / 100)``:
    5 ft/s at speed 0, 10 ft/s at 100 (replaces STAT-03's cells-per-tick)."""
    speed = getattr(player, "speed", 50)
    return MAX_PLAYER_FEET_PER_SECOND * (0.5 + 0.5 * speed / 100.0)


def squares_this_tick(player, movement_ctx, multiplier: int = 1) -> int:
    """MOVE-08: whole squares ``player`` advances this tick on this map.

    ``feet_per_second x TICK_SECONDS / feet_per_square x multiplier`` plus the
    fraction carried from earlier ticks (``PlayerState.move_carry``); the
    whole part moves and the rest carries, so a 1.5 squares-a-second player
    moves 0, 1, 1, 1 over four ticks. ``only_move`` passes ``multiplier=2``.
    """
    reach = (
        feet_per_second(player)
        * TICK_SECONDS
        / movement_ctx.feet_per_square
        * multiplier
        + player.move_carry
    )
    steps = int(reach)
    player.move_carry = reach - steps
    return steps


def _nearest_cell(
    from_row: int,
    from_col: int,
    cells: list,
) -> tuple[int, int] | None:
    """Return the cell from cells closest to (from_row, from_col) by Manhattan distance."""
    if not cells:
        return None
    best = min(cells, key=lambda rc: abs(rc[0] - from_row) + abs(rc[1] - from_col))
    return (int(best[0]), int(best[1]))


def _cells_from_keys(keys) -> list[tuple[int, int]]:
    """MOVE-06: ``"r,c"`` keys as sorted cells (a ``frozenset``'s order is
    PYTHONHASHSEED-randomised, so sort for serial == parallel)."""
    cells = []
    for k in keys:
        r, c = k.split(",")
        cells.append((int(r), int(c)))
    return sorted(cells)


def _find_role(all_alive: list, team_color: str, role: str) -> Any:
    return next(
        (
            p
            for p in all_alive
            if p.team_color == team_color and p.role == role and p.final_lives > 0
        ),
        None,
    )


def _cell_from_memory(
    player,
    target_tag_id: str,
    second: float,
    time_domain: str = "seconds",
) -> "tuple[int, int] | None":
    """Return the last-known cell for target_tag_id from player's memory, or None.

    Applies role-specific freshness windows (seconds-domain defaults; tick
    constants when time_domain == "ticks"):
      - heavy / medic / ammo: slow-moving support roles stay fresh longer
      - scout / commander:    fast-moving roles go stale quickly
    Returns None when the entry is absent, the cell is None, or the entry is stale.
    """
    memory = getattr(player, "player_memory", None)
    if not memory:
        return None
    entry = memory.get(target_tag_id)
    if entry is None:
        return None
    role = entry.get("role", "scout")  # default to stale-quickly if unknown
    table = _stale_table(time_domain)
    threshold = table.get(role, table["scout"])
    age = second - entry.get("timestamp", 0)
    if age > threshold:
        return None
    cell = entry.get("cell")
    if cell is None:
        return None
    return (int(cell[0]), int(cell[1]))


def _enemy_medic_goal(
    player,
    all_alive: list,
    enemy_color: str,
    second: float,
    time_domain: str,
    chased: set[str],
) -> "tuple[int, int] | None":
    """The Commander's enemy-Medic goal: memory first, then perfect knowledge.

    None when the Commander has Chased that Medic (MECH-10 Follow rule).
    """
    medic_tag = f"{enemy_color}_medic"
    if medic_tag in chased:
        return None
    # MECH-06: prefer memory; fall back to perfect knowledge when memory is empty
    memory_cell = _cell_from_memory(player, medic_tag, second, time_domain)
    if memory_cell:
        return memory_cell
    enemy_medic = _find_role(all_alive, enemy_color, "medic")
    if enemy_medic and enemy_medic.cell_row is not None:
        return (enemy_medic.cell_row, enemy_medic.cell_col)
    return None


def _known_enemies_from_memory(
    player,
    enemy_color: str,
    second: float,
    time_domain: str = "seconds",
    exclude: "set[str] | None" = None,
) -> "list[tuple[int, int]]":
    """Return a list of (row, col) cells for non-stale enemy entries in player's memory.

    ``exclude``: tag ids to leave out (MECH-10: players this one Chased).
    """
    memory = getattr(player, "player_memory", None)
    if not memory:
        return []
    table = _stale_table(time_domain)
    cells = []
    for tag_id, entry in memory.items():
        # Only enemies
        if not tag_id.startswith(enemy_color):
            continue
        if exclude and tag_id in exclude:
            continue
        role = entry.get("role", "scout")
        threshold = table.get(role, table["scout"])
        age = second - entry.get("timestamp", 0)
        if age > threshold:
            continue
        cell = entry.get("cell")
        if cell is not None:
            cells.append((int(cell[0]), int(cell[1])))
    return cells


def _find_enemy_commander_in_memory(
    player,
    enemy_color: str,
    second: float,
    time_domain: str = "seconds",
) -> "tuple[int, int] | None":
    """Return last-known cell for enemy commander if in memory and not stale."""
    commander_tag = f"{enemy_color}_commander"
    return _cell_from_memory(player, commander_tag, second, time_domain)


def _apply_teamwork_bias(
    player,
    goal: "tuple[int, int] | None",
    all_alive: list,
    cell_row: int,
    cell_col: int,
    movement_ctx: "MapContext",
) -> "tuple[int, int] | None":
    """Gently bias goal toward high-LOS cells also visible to ≥1 living ally.

    Only applied when teamwork > 50. Returns a modified goal or the original goal.
    """
    teamwork = getattr(player, "teamwork", 50)
    if teamwork <= 50:
        return goal

    # Find living allies
    allies = [
        p
        for p in all_alive
        if p.team_color == player.team_color
        and p is not player
        and p.final_lives > 0
        and p.cell_row is not None
    ]
    if not allies:
        return goal

    sight_data = movement_ctx.sight_data or {}

    # Check if the current goal is already in LOS of an ally
    if goal is not None:
        goal_key = f"{goal[0]},{goal[1]}"
        for ally in allies:
            ally_key = f"{ally.cell_row},{ally.cell_col}"
            ally_visible = sight_data.get(ally_key, frozenset())
            if goal_key in ally_visible:
                return goal  # already ally-visible, no bias needed

    # Find high-LOS cells also visible to at least one ally
    high_los_cells = movement_ctx.get_high_los_cells()
    if not high_los_cells:
        return goal

    # Build set of cells visible from any ally
    ally_visible_cells: set = set()
    for ally in allies:
        ally_key = f"{ally.cell_row},{ally.cell_col}"
        for cell_str in sight_data.get(ally_key, frozenset()):
            ally_visible_cells.add(cell_str)

    teamwork_cells = [
        rc for rc in high_los_cells if f"{rc[0]},{rc[1]}" in ally_visible_cells
    ]
    if not teamwork_cells:
        return goal

    # With probability teamwork/100, choose the teamwork goal instead
    teamwork_prob = teamwork / 100.0
    if random.random() < teamwork_prob:
        tw_goal = _nearest_cell(cell_row, cell_col, teamwork_cells)
        if tw_goal:
            return tw_goal

    return goal


def _goal_from_action(
    player,
    all_alive: list,
    enemy_color: str,
    cell_row: int,
    cell_col: int,
    intended_action: str,
    movement_ctx: "MapContext",
    second: float = 0.0,
    time_domain: str = "seconds",
) -> tuple[int, int] | None:
    """Return a goal cell driven by the player's last action, or None."""
    cell_los_counts: dict[str, int] = movement_ctx.cell_los_counts

    if intended_action in ("tag_player", "missile_player"):
        # MECH-10: a Chaser's Tag goals skip the players they Chased.
        chased = chased_tag_ids(player, all_alive, second)
        if player.role == "commander":
            medic_goal = _enemy_medic_goal(
                player, all_alive, enemy_color, second, time_domain, chased
            )
            if medic_goal:
                return medic_goal
        # MECH-06: use memory when entries exist; fall back to perfect knowledge otherwise
        known_cells = _known_enemies_from_memory(
            player, enemy_color, second, time_domain, exclude=chased
        )
        if known_cells:
            goal = _nearest_cell(cell_row, cell_col, known_cells)
            if goal:
                return goal
        # Perfect-knowledge fallback when memory is empty or all stale
        enemies = [
            p
            for p in all_alive
            if p.team_color == enemy_color
            and p.final_lives > 0
            and p.cell_row is not None
            and p.tag_id_key not in chased
        ]
        if enemies:
            goal = _nearest_cell(
                cell_row, cell_col, [(p.cell_row, p.cell_col) for p in enemies]
            )
            if goal:
                return goal

    elif intended_action == "resupply_ally":
        allies = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.final_lives > 0
            and p.cell_row is not None
            and p is not player
        ]
        if allies:
            if player.role == "medic":
                target = min(
                    allies,
                    key=lambda p: p.final_lives
                    / max(1, getattr(p, "max_lives", p.starting_lives)),
                )
            else:
                target = min(
                    allies,
                    key=lambda p: p.final_shots
                    / max(1, getattr(p, "max_shots", p.starting_shots)),
                )
            return (target.cell_row, target.cell_col)

    elif intended_action == "hide":
        adj: dict = movement_ctx.get_adjacency()
        neighbors = adj.get((cell_row, cell_col), [])
        if neighbors and cell_los_counts:
            safest = min(
                neighbors, key=lambda rc: cell_los_counts.get(f"{rc[0]},{rc[1]}", 0)
            )
            return safest

    return None


def _goal_from_role(
    player,
    all_alive: list,
    enemy_color: str,
    cell_row: int,
    cell_col: int,
    movement_ctx: "MapContext",
    second: float = 0.0,
    nuke_active: bool = False,
    time_domain: str = "seconds",
) -> tuple[int, int] | None:
    """Return a role-specific goal cell, or None.

    MECH-06: Uses player memory for enemy-targeting when a map is active.
    Teamwork bias applied at the end (when teamwork > 50 and not nuke_active).
    """
    cell_los_counts: dict[str, int] = movement_ctx.cell_los_counts
    high_los_cells: list = movement_ctx.get_high_los_cells()
    strong_spots: list = movement_ctx.get_strong_spots()
    sight_data: dict = movement_ctx.sight_data or {}

    goal: tuple[int, int] | None = None
    # MOVE-06: the positioning picks below are a scored draw (goal_scoring);
    # a drawn goal is held until reached (choose_goal_cell's commitment).
    player._goal_was_drawn = False

    def drawn(candidates, value_of) -> tuple[int, int] | None:
        pick = draw_goal(
            player,
            [tuple(c) for c in candidates],
            value_of,
            all_alive,
            movement_ctx,
            second,
        )
        player._goal_was_drawn = pick is not None
        return pick

    if player.role == "scout":
        if high_los_cells:
            goal = drawn(high_los_cells, movement_ctx.los_rank)

    elif player.role == "heavy":
        max_lives = getattr(player, "max_lives", player.starting_lives)
        max_shots = getattr(player, "max_shots", player.starting_shots)
        healthy = (
            player.final_lives > max_lives * 0.5
            and player.final_shots > max_shots * 0.5
        )
        if healthy and strong_spots:
            goal = drawn(strong_spots, lambda cell: 1.0)
        if goal is None:
            for support_role in ("medic", "ammo"):
                ally = _find_role(all_alive, player.team_color, support_role)
                if ally and ally.cell_row is not None:
                    goal = (ally.cell_row, ally.cell_col)
                    break

    elif player.role == "medic":
        heavy = _find_role(all_alive, player.team_color, "heavy")
        if heavy and heavy.cell_row is not None:
            heavy_key = f"{heavy.cell_row},{heavy.cell_col}"
            heavy_visible = sight_data.get(heavy_key, frozenset())
            if heavy_visible and cell_los_counts:
                # Medic: hidden cells the Heavy can see score higher.
                goal = drawn(
                    _cells_from_keys(heavy_visible),
                    lambda cell: 1.0 - movement_ctx.los_rank(cell),
                )
            if goal is None:
                goal = (heavy.cell_row, heavy.cell_col)

    elif player.role == "ammo":
        heavy = _find_role(all_alive, player.team_color, "heavy")
        if heavy and heavy.cell_row is not None:
            heavy_key = f"{heavy.cell_row},{heavy.cell_col}"
            heavy_visible = sight_data.get(heavy_key, frozenset())
            if heavy_visible and cell_los_counts:
                # Ammo: open cells the Heavy can see score higher.
                goal = drawn(_cells_from_keys(heavy_visible), movement_ctx.los_rank)
            if goal is None:
                goal = (heavy.cell_row, heavy.cell_col)

    elif player.role == "commander":
        # MECH-06 memory-first enemy-Medic goal; MECH-10 skips a Chased Medic.
        goal = _enemy_medic_goal(
            player,
            all_alive,
            enemy_color,
            second,
            time_domain,
            chased_tag_ids(player, all_alive, second),
        )

    # MECH-06: teamwork bias — only when not in a nuke-reaction tick
    if not nuke_active and goal is not None:
        biased = _apply_teamwork_bias(
            player, goal, all_alive, cell_row, cell_col, movement_ctx
        )
        if biased != goal:
            # MOVE-06: the bias's nearest teamwork cell was not drawn, so it
            # keeps the 4-tick cadence instead of being held.
            player._goal_was_drawn = False
        goal = biased

    return goal


def choose_goal_cell(
    player,
    all_alive: list,
    spawn_cells: dict[str, tuple[int, int]],
    movement_ctx: "MapContext | None" = None,
    intended_action: str = "",
    second: float = 0.0,
    time_domain: str = "seconds",
) -> tuple[int, int] | None:
    """Return the cell a player should navigate toward (MAP-05).

    Goal selection is action-aware (uses the player's previous chosen action) and
    role-aware. Priority order:
    1. Critical-resource override: seek medic (low lives) or ammo (low shots) for
       non-support roles — always trumps role/action logic.
    2. Action-driven movement: based on the action chosen last tick (intended_action):
       - tag_player / missile_player: move toward enemies (Commander → enemy medic first).
         MECH-06: uses player memory when map is active.
       - resupply_ally: Medic → neediest ally by lives; Ammo → neediest ally by shots.
       - hide: move to the safest adjacent cell (lowest LOS count).
    3. Role-specific positioning (for only_move, capture_base, use_special, or fallback):
       - Scout: a drawn high-LOS cell (top 25% of cells by sight-line count).
       - Heavy: a drawn strong-spot cell when healthy (>50% lives and shots); otherwise
         seek the allied Medic or Ammo dynamically.
       - Medic: a drawn cell in the allied Heavy's visible set, hidden ones first.
       - Ammo: a drawn cell in the allied Heavy's visible set, open ones first.
       - Commander: enemy medic cell (priority target), else enemy base.
         MECH-06: uses player memory when map is active.
       MOVE-06: "drawn" is goal_scoring.draw_goal, held until reached.
    4. Default: a drawn floor cell within 15 ft of the enemy base.

    MECH-06 additions:
    - teamwork bias applied in _goal_from_role (when teamwork > 50 and not nuke-reacting).
    - score broadcast override: winning + low lives + medic alive + second >= 360 → seek medic.
    """
    enemy_color = "blue" if player.team_color == "red" else "red"
    default_goal: tuple[int, int] | None = spawn_cells.get(enemy_color)

    cell_row: int | None = getattr(player, "cell_row", None)
    cell_col: int | None = getattr(player, "cell_col", None)

    max_lives = getattr(player, "max_lives", player.starting_lives)
    max_shots = getattr(player, "max_shots", player.starting_shots)

    # ── 0. MECH-04: nuke-reaction override (highest priority when active) ────
    # MOVE-08: a Downed player's option holds unless they are Active at least
    # one tick before the Nuke detonates (still time to cancel / resupply).
    nuke_active = getattr(player, "reacting_to_nuke", False)
    if nuke_active and (not plan_is_live(player, second) or active_before_nuke(player)):
        if player.role in ("medic", "ammo"):
            # Support roles: rush toward the neediest ally to maximise resupply
            # output in the ticks before the nuke lands.
            allies = [
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p is not player
                and p.cell_row is not None
            ]
            if allies:
                if player.role == "medic":
                    target = min(
                        allies,
                        key=lambda p: p.final_lives
                        / max(1, getattr(p, "max_lives", p.starting_lives)),
                    )
                else:
                    target = min(
                        allies,
                        key=lambda p: p.final_shots
                        / max(1, getattr(p, "max_shots", p.starting_shots)),
                    )
                # MOVE-04: reactive override fires — clear any committed goal.
                player._committed_goal = None
                return (target.cell_row, target.cell_col)
        elif player.final_lives <= max_lives * 0.3:
            # Non-support, lives critical: seek allied medic for safety
            medic = _find_role(all_alive, player.team_color, "medic")
            if medic and medic.cell_row is not None:
                # MOVE-04: reactive override fires — clear any committed goal.
                player._committed_goal = None
                return (medic.cell_row, medic.cell_col)
        else:
            # MECH-06: if player knows enemy commander location (memory system),
            # set movement goal to enemy commander cell to attempt tag-cancel.
            enemy_cmd_cell = _find_enemy_commander_in_memory(
                player, enemy_color, second, time_domain
            )
            if enemy_cmd_cell is not None:
                # MOVE-04: reactive override fires — clear any committed goal.
                player._committed_goal = None
                return enemy_cmd_cell

    # ── 1. Critical-resource overrides (non-support roles) ───────────────────
    if player.role not in ("medic", "ammo"):
        if player.final_lives <= max_lives * 0.3:
            medic = _find_role(all_alive, player.team_color, "medic")
            if medic and medic.cell_row is not None:
                # MOVE-04: reactive override fires — clear any committed goal.
                player._committed_goal = None
                return (medic.cell_row, medic.cell_col)
        elif player.final_shots <= max_shots * 0.3:
            ammo = _find_role(all_alive, player.team_color, "ammo")
            if ammo and ammo.cell_row is not None:
                # MOVE-04: reactive override fires — clear any committed goal.
                player._committed_goal = None
                return (ammo.cell_row, ammo.cell_col)

    if movement_ctx is None or cell_row is None or cell_col is None:
        return default_goal

    # ── 1a. MOVE-08: the Downed option's goal (Shuffle to cover / Make a
    # trail) while Downed; beats 1b, the commitment and steps 2-4.
    downed = downed_goal(player, second, movement_ctx)
    if downed is not None:
        return downed

    # ── 1b. MECH-06: score broadcast movement override ────────────────────────
    # Winning + low lives + medic alive + enough time still remaining → seek
    # allied medic. Support roles skip this — a Medic seeking "the medic" is a
    # no-op. TIME-01: threshold is 360 in the seconds domain (RBS / tests) and
    # SCORE_BROADCAST_MIN_REMAINING_TICKS (720) in the tick domain (BatchSim).
    if time_domain == "ticks":
        from .time_constants import SCORE_BROADCAST_MIN_REMAINING_TICKS

        score_min_remaining = SCORE_BROADCAST_MIN_REMAINING_TICKS
    else:
        score_min_remaining = 360
    score_state = getattr(player, "score_broadcast_state", None)
    if score_state and player.role not in ("medic", "ammo"):
        winning_team = score_state.get("winning_team", "")
        if winning_team == player.team_color and second >= score_min_remaining:
            low_lives = player.final_lives <= max_lives * 0.3
            if low_lives:
                allied_medic = _find_role(all_alive, player.team_color, "medic")
                if allied_medic is not None and allied_medic.cell_row is not None:
                    # MOVE-04: reactive override fires — clear committed goal.
                    player._committed_goal = None
                    return (allied_medic.cell_row, allied_medic.cell_col)

    # ── MOVE-04: Goal commitment throttle (BatchSimulator only) ──────────────
    # Steps 2–4 below run a fresh goal-selection pass every tick by default;
    # under MOVE-04 / ADR-0010 the tick-domain (BatchSim) caller re-uses the
    # previously committed goal for ``GOAL_RECOMPUTE_PERIOD_TICKS`` ticks
    # before recomputing, provided the player hasn't reached it. Reactive
    # overrides (steps 0/1/1b above) bypass this entirely and clear the
    # commitment as a side effect when they fire. The RBS path
    # (time_domain != "ticks") is unchanged.
    def action_goal() -> tuple[int, int] | None:
        return _goal_from_action(
            player,
            all_alive,
            enemy_color,
            cell_row,
            cell_col,
            intended_action,
            movement_ctx,
            second,
            time_domain,
        )

    early_action: tuple[int, int] | None = None
    action_checked = False
    if time_domain == "ticks":
        committed = player._committed_goal
        if (
            committed is not None
            and (cell_row, cell_col) != committed[0]
            and second < committed[2]
        ):
            if committed[2] != HELD_GOAL_EXPIRY:
                return committed[0]
            # MOVE-06 decision 10: a held (drawn) positioning goal yields to an
            # action goal (chase, resupply, hide); otherwise it is kept.
            early_action = action_goal()
            action_checked = True
            if early_action is None:
                return committed[0]

    # ── 2. Action-driven movement ────────────────────────────────────────────
    goal = early_action if action_checked else action_goal()
    from_action = goal is not None
    if goal is None:
        # ── 3. Role-specific positioning ─────────────────────────────────────
        goal = _goal_from_role(
            player,
            all_alive,
            enemy_color,
            cell_row,
            cell_col,
            movement_ctx,
            second,
            nuke_active=nuke_active,
            time_domain=time_domain,
        )

    # ── 4. Default: enemy base ───────────────────────────────────────────────
    # MOVE-06: a scored draw over the floor cells near the enemy base, so
    # players with no other goal spread out instead of stacking on one cell.
    if goal is None:
        base_cells = enemy_base_cells(enemy_color, movement_ctx)
        goal = draw_goal(
            player, base_cells, lambda cell: 1.0, all_alive, movement_ctx, second
        )
        player._goal_was_drawn = goal is not None
        if goal is None:
            goal = default_goal

    # MOVE-04: commit the freshly chosen goal for the next
    # GOAL_RECOMPUTE_PERIOD_TICKS ticks (BatchSim / tick-domain only).
    # ``from_action`` is True iff step 2 (``_goal_from_action``) produced it;
    # False for step 3 (role positioning), step 4 (enemy-base default), or
    # any None-fallback. ``_record_down`` consults the flag to decide whether
    # a Down clears the commitment (action-driven goals drop; positioning
    # goals survive). MOVE-06: a drawn goal is held until reached (expiry
    # after the round), not re-picked every GOAL_RECOMPUTE_PERIOD_TICKS.
    if time_domain == "ticks" and goal is not None:
        held = not from_action and getattr(player, "_goal_was_drawn", False)
        player._committed_goal = (
            goal,
            from_action,
            HELD_GOAL_EXPIRY if held else int(second) + GOAL_RECOMPUTE_PERIOD_TICKS,
        )

    return goal
