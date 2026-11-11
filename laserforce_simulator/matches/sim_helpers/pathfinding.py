from __future__ import annotations

import heapq
from collections.abc import Callable, Mapping
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .map_context import MapContext

from . import support_survival
from .resupply_visit import freshest_callout
from .downed_option import active_before_nuke, downed_goal, plan_is_live
from .follow_rule import chased_tag_ids
from .goal_scoring import draw_goal, enemy_base_cells
from .modes import (
    DEFENSE_RESUPPLY_SHARE,
    Mode,
    _resupply_share,
    attack_tag_goal,
    base_goal,
    mode_goal,
    trail_costs,
)
from .time_constants import (
    GOAL_RECOMPUTE_PERIOD_TICKS,
    HELD_GOAL_EXPIRY,
    TICK_SECONDS,
)


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
    extra_costs: Mapping[tuple[int, int], float] | None = None,
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

    MECH-07: ``extra_costs`` adds its cost to each step into a listed cell, a
    cost and never a block (``modes.trail_costs``). Omitted, routes are unchanged.
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
            if extra_costs:
                cost += extra_costs.get(neighbor, 0.0)
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
    all_alive: list | None = None,
    tick: float = 0.0,
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

    MECH-07: a recompute routes around the Trails ``modes.trail_costs`` finds in
    ``all_alive`` at ``tick``, and only a recompute reads them: a Trail is not a
    recompute trigger, so a committed route keeps going. With no ``all_alive`` the
    route ignores Trails.

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
            extra_costs=(
                None if all_alive is None else trail_costs(player, all_alive, tick)
            ),
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
    table = support_survival.stale_table(time_domain)
    threshold = table.get(role, table["scout"])
    age = second - entry.get("timestamp", 0)
    if age > threshold:
        return None
    cell = entry.get("cell")
    if cell is None:
        return None
    return (int(cell[0]), int(cell[1]))


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
    table = support_survival.stale_table(time_domain)
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


def _goal_seeing(
    player,
    target: tuple[int, int],
    all_alive: list,
    movement_ctx: "MapContext",
    second: float,
    value_of: "Callable[[tuple[int, int]], float] | None" = None,
) -> tuple[int, int]:
    """MECH-17: a drawn cell that can see *target*, else *target* itself when no such
    cell is reachable (or the map has no sight data). *value_of* scores a candidate;
    every one is worth 1.0 by default."""
    goal = draw_goal(
        player,
        movement_ctx.cells_visible_from(target),
        value_of or (lambda cell: 1.0),
        all_alive,
        movement_ctx,
        second,
    )
    return target if goal is None else goal


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
        # MECH-07: in Attack, the nearest remembered enemy Medic or Ammo comes first.
        if player.mode is Mode.ATTACK:
            target = attack_tag_goal(
                player, all_alive, enemy_color, second, time_domain, exclude=chased
            )
            if target:
                return _goal_seeing(player, target, all_alive, movement_ctx, second)
        # MECH-06: use memory when entries exist.
        known_cells = _known_enemies_from_memory(
            player, enemy_color, second, time_domain, exclude=chased
        )
        if known_cells:
            target = _nearest_cell(cell_row, cell_col, known_cells)
            if target:
                return _goal_seeing(player, target, all_alive, movement_ctx, second)
        # MECH-17: nothing remembered, so search toward the enemy base. No true enemy
        # cell is read here.
        return draw_goal(
            player,
            enemy_base_cells(enemy_color, movement_ctx),
            lambda cell: 1.0,
            all_alive,
            movement_ctx,
            second,
        )

    elif intended_action == "resupply_ally":
        allies = [
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.final_lives > 0
            and p.cell_row is not None
            and p is not player
        ]
        # CAL-03 rule 4: behind the front, so no walk to a teammate a remembered
        # enemy can see; rule 8: the Ammo walks only to whom HOLD lets it Resupply.
        enemy_cells = _known_enemies_from_memory(
            player, enemy_color, second, time_domain
        )

        def exposed(cell: tuple[int, int]) -> bool:
            return any(movement_ctx.can_see(e, cell) for e in enemy_cells)

        # Support-survival switch 4: the walk ends on a hidden cell.
        hidden = (
            (lambda cell: 1.0 - movement_ctx.los_rank(cell))
            if support_survival.AMMO_HIDES_FREELY
            else None
        )
        # CAL-03 rule 7: to the freshest call-out first, rule 4 permitting.
        callout = freshest_callout(player, all_alive, second)
        if callout is not None and not exposed(callout):
            return _goal_seeing(
                player, callout, all_alive, movement_ctx, second, hidden
            )
        # Support-survival switch 3: the Medic holds its position; a low ally
        # comes to it (on a Resupply visit, CAL-03 rule 6).
        if player.role == "medic" and support_survival.MEDIC_HOLDS_POSITION:
            return None
        allies = [
            p
            for p in allies
            if not exposed((p.cell_row, p.cell_col))
            and (
                player.role != "ammo"
                or support_survival.hold_allows(
                    player, p, all_alive, second, movement_ctx
                )
            )
        ]
        if allies:
            shares = {id(p): _resupply_share(p, player.role) for p in allies}
            target = min(allies, key=lambda p: shares[id(p)])
            # MECH-07: in Defense a support walks only to an ally who needs it, and
            # otherwise takes its place around the Heavy (mode_goal).
            if (
                player.mode is Mode.DEFENSE
                and shares[id(target)] > DEFENSE_RESUPPLY_SHARE
            ):
                return None
            return _goal_seeing(
                player,
                (target.cell_row, target.cell_col),
                all_alive,
                movement_ctx,
                second,
                hidden,
            )

    elif intended_action == "hide":
        adj: dict = movement_ctx.get_adjacency()
        neighbors = adj.get((cell_row, cell_col), [])
        if neighbors and cell_los_counts:
            safest = min(
                neighbors, key=lambda rc: cell_los_counts.get(f"{rc[0]},{rc[1]}", 0)
            )
            return safest

    return None


def _link_return_goal(
    player, all_alive: list, movement_ctx: "MapContext", second: float
) -> tuple[int, int] | None:
    """CAL-03 rule 9: ``LINK_RETURN_TICKS`` after the Ammo left its Medic
    (``link_left_at``), the nearest cell (Manhattan, then the cell) that sees the
    Medic's remembered cell, or that cell when none does. None for anyone else,
    before then, or with no living Medic remembered. No RNG."""
    left = getattr(player, "link_left_at", None)
    if player.role != "ammo" or left is None:
        return None
    if second - left < support_survival.LINK_RETURN_TICKS:
        return None
    medic = _find_role(all_alive, player.team_color, "medic")
    entry = player.player_memory.get(medic.tag_id_key) if medic is not None else None
    if entry is None or entry.get("cell") is None:
        return None
    target = (int(entry["cell"][0]), int(entry["cell"][1]))
    here = (player.cell_row, player.cell_col)
    return min(
        movement_ctx.cells_visible_from(target),
        key=lambda c: (abs(c[0] - here[0]) + abs(c[1] - here[1]), c),
        default=target,
    )


def _goal_from_role(
    player,
    all_alive: list,
    enemy_color: str,
    movement_ctx: "MapContext",
    second: float = 0.0,
    time_domain: str = "seconds",
) -> tuple[int, int] | None:
    """Return the positioning goal cell, or None: MECH-07's ``modes.mode_goal``."""
    return mode_goal(player, all_alive, enemy_color, movement_ctx, second, time_domain)


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
    1. A Commander's, Heavy's or Scout's Resupply visit goal (CAL-03 rule 6,
       ``resupply_visit``) — always trumps role/action logic. A low Ammo walks to
       its Medic (rule 3). After 1a, an Ammo away from its Medic goes back (rule 9).
    1c. A Commander with 3+ Shots and a Base left: ``modes.base_goal``, held until
       reached. It does not stop the Commander shooting.
    2. Action-driven movement: based on the action chosen last tick (intended_action):
       - tag_player / missile_player: a drawn cell that sees, in Attack, the nearest
         remembered enemy Medic or Ammo, then the nearest remembered enemy. With none
         remembered, a drawn floor cell near the enemy base.
       - resupply_ally: a drawn cell that sees the neediest ally (Medic: by lives; Ammo:
         by shots). In Defense, only when that ally is at or below 75% of the resource.
       - hide: move to the safest adjacent cell (lowest LOS count).
    3. Positioning (for only_move, capture_base, use_special, or fallback), by the
       player's Mode: ``modes.mode_goal``, written out in
       docs/agents/matches/sim-helpers.md.
       MOVE-06: its draws are goal_scoring.draw_goal, held until reached.
    4. Default: a drawn floor cell within 15 ft of the enemy base.

    MECH-06 addition:
    - score broadcast override: winning + low lives + medic alive + second >= 360 → seek medic.
    """
    enemy_color = "blue" if player.team_color == "red" else "red"
    default_goal: tuple[int, int] | None = spawn_cells.get(enemy_color)

    cell_row: int | None = getattr(player, "cell_row", None)
    cell_col: int | None = getattr(player, "cell_col", None)

    max_lives = getattr(player, "max_lives", player.starting_lives)

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

    # ── 1. A Resupply visit's goal (CAL-03 rule 6, non-support roles) ─────────
    # It replaced step 1's walk to a support's true cell at 30% lives or shots
    # (decision 51): a visit starts there instead (``resupply_visit.visit_step``).
    visit = getattr(player, "visit", None)
    if player.role not in ("medic", "ammo"):
        if visit is not None and visit.goal is not None:
            player._committed_goal = None
            return visit.goal
    elif (
        player.role == "ammo"
        and player.final_lives <= support_survival.AMMO_SEEK_MEDIC_LIVES
    ):
        # CAL-03 rule 3: a low Ammo walks to its Medic, like the other roles.
        medic = _find_role(all_alive, player.team_color, "medic")
        if medic and medic.cell_row is not None:
            player._committed_goal = None
            return (medic.cell_row, medic.cell_col)

    if movement_ctx is None or cell_row is None or cell_col is None:
        return default_goal

    # ── 1a. MOVE-08: the Downed option's goal (Shuffle to cover / Make a
    # trail) while Downed; beats rule 9, 1b, the commitment and steps 2-4.
    downed = downed_goal(player, second, movement_ctx)
    if downed is not None:
        return downed

    # CAL-03 rule 9: an Ammo away from its Medic for LINK_RETURN_TICKS goes back.
    link_goal = _link_return_goal(player, all_alive, movement_ctx, second)
    if link_goal is not None:
        player._committed_goal = None
        return link_goal

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
        # ── 1c. MECH-17: a Commander goes for its Bases, before any action goal.
        base = base_goal(player, all_alive, enemy_color, movement_ctx, second)
        if base is not None:
            player._committed_goal = (base, False, HELD_GOAL_EXPIRY)
            return base
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
            movement_ctx,
            second,
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
