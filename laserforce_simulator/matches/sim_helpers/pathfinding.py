from __future__ import annotations

import heapq
import math
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
from .time_constants import STALENESS_FAST_TICKS, STALENESS_SLOW_TICKS

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


def astar_path(
    start: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: dict | None = None,
) -> list[tuple[int, int]]:
    """Return the shortest path from start to goal, excluding start.

    The returned list is the ordered sequence of cells to step through,
    ending at ``goal``. Uses A* with a Manhattan-distance heuristic and
    optional elevation costs. Returns ``[]`` when start == goal, no path
    exists, or start is not in the adjacency graph.
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


def max_movement_for_map(zone_data: list[list[int]] | None) -> int:
    """Cells-per-tick ceiling, scaled by map size (PLAN.md STAT-03 Phase 1).

    Larger maps allow more cells per tick so traversal time stays sane:
    ``max(rows, cols) // 10`` clamped to the documented 5..10 range.
    """
    if not zone_data:
        return 5
    rows = len(zone_data)
    cols = len(zone_data[0]) if rows else 0
    return max(5, min(10, max(rows, cols) // 10))


def cells_to_move(speed: int, zone_data: list[list[int]] | None) -> int:
    """Cells a player traverses this tick: ``ceil(speed/100 * max_movement)``.

    PLAN.md STAT-03 Phase 1 (pairs with MAP-02). Floored at 1 so a moving
    player is never frozen by a low ``speed`` stat.
    """
    mm = max_movement_for_map(zone_data)
    return max(1, math.ceil((speed / 100.0) * mm))


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


def _select_los_cell(
    visible_keys,
    cell_los_counts: dict,
    from_row: int,
    from_col: int,
    *,
    prefer_high: bool,
) -> "tuple[int, int] | None":
    """Pick a cell from ``visible_keys`` (``"r,c"`` strings) by LOS count.

    ``prefer_high`` maximises the sight-line count (Ammo — exposed support),
    else minimises it (Medic — sheltered). Ties are broken by the cell nearest
    ``(from_row, from_col)`` then by ``(r, c)`` — a fully deterministic,
    hash-independent ordering. ``visible_keys`` is a ``frozenset`` whose
    ``str`` iteration order is PYTHONHASHSEED-randomised per process, so a
    naive ``min``/``max`` tie-break makes serial and parallel workers diverge
    (SIM-07/08 determinism; reached every tick under MOVE-01). The
    nearest-bias also keeps support roles tracking the advancing Heavy rather
    than teleporting to a far equal-LOS cell.
    """
    best: tuple[int, int] | None = None
    best_key: tuple | None = None
    for k in visible_keys:
        los = cell_los_counts.get(k, 0)
        r_s, c_s = k.split(",")
        r, c = int(r_s), int(c_s)
        dist = abs(r - from_row) + abs(c - from_col)
        sort_key = (-los if prefer_high else los, dist, r, c)
        if best_key is None or sort_key < best_key:
            best_key = sort_key
            best = (r, c)
    return best


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


def _known_enemies_from_memory(
    player,
    enemy_color: str,
    second: float,
    time_domain: str = "seconds",
) -> "list[tuple[int, int]]":
    """Return a list of (row, col) cells for non-stale enemy entries in player's memory."""
    memory = getattr(player, "player_memory", None)
    if not memory:
        return []
    table = _stale_table(time_domain)
    cells = []
    for tag_id, entry in memory.items():
        # Only enemies
        if not tag_id.startswith(enemy_color):
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
        if player.role == "commander":
            # MECH-06: prefer memory; fall back to perfect knowledge when memory is empty
            enemy_medic_cell = _cell_from_memory(
                player, f"{enemy_color}_medic", second, time_domain
            )
            if enemy_medic_cell:
                return enemy_medic_cell
            # Perfect-knowledge fallback
            enemy_medic = _find_role(all_alive, enemy_color, "medic")
            if enemy_medic and enemy_medic.cell_row is not None:
                return (enemy_medic.cell_row, enemy_medic.cell_col)
        # MECH-06: use memory when entries exist; fall back to perfect knowledge otherwise
        known_cells = _known_enemies_from_memory(
            player, enemy_color, second, time_domain
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

    if player.role == "scout":
        if high_los_cells:
            goal = _nearest_cell(cell_row, cell_col, high_los_cells)

    elif player.role == "heavy":
        max_lives = getattr(player, "max_lives", player.starting_lives)
        max_shots = getattr(player, "max_shots", player.starting_shots)
        healthy = (
            player.final_lives > max_lives * 0.5
            and player.final_shots > max_shots * 0.5
        )
        if healthy and strong_spots:
            goal = _nearest_cell(cell_row, cell_col, strong_spots)
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
                goal = _select_los_cell(
                    heavy_visible,
                    cell_los_counts,
                    cell_row,
                    cell_col,
                    prefer_high=False,
                )
            if goal is None:
                goal = (heavy.cell_row, heavy.cell_col)

    elif player.role == "ammo":
        heavy = _find_role(all_alive, player.team_color, "heavy")
        if heavy and heavy.cell_row is not None:
            heavy_key = f"{heavy.cell_row},{heavy.cell_col}"
            heavy_visible = sight_data.get(heavy_key, frozenset())
            if heavy_visible and cell_los_counts:
                goal = _select_los_cell(
                    heavy_visible,
                    cell_los_counts,
                    cell_row,
                    cell_col,
                    prefer_high=True,
                )
            if goal is None:
                goal = (heavy.cell_row, heavy.cell_col)

    elif player.role == "commander":
        # MECH-06: try memory first, then perfect knowledge fallback
        memory_cell = _cell_from_memory(
            player, f"{enemy_color}_medic", second, time_domain
        )
        if memory_cell:
            goal = memory_cell
        else:
            enemy_medic = _find_role(all_alive, enemy_color, "medic")
            if enemy_medic and enemy_medic.cell_row is not None:
                goal = (enemy_medic.cell_row, enemy_medic.cell_col)

    # MECH-06: teamwork bias — only when not in a nuke-reaction tick
    if not nuke_active and goal is not None:
        goal = _apply_teamwork_bias(
            player, goal, all_alive, cell_row, cell_col, movement_ctx
        )

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
       - Scout: nearest high-LOS cell (top 25% of cells by sight-line count).
       - Heavy: nearest strong-spot cell when healthy (>50% lives and shots); otherwise
         seek nearest allied Medic or Ammo dynamically.
       - Medic: nearest low-LOS cell within the allied Heavy's visible set.
       - Ammo: nearest high-LOS cell within the allied Heavy's visible set.
       - Commander: enemy medic cell (priority target), else enemy base.
         MECH-06: uses player memory when map is active.
    4. Default: enemy base cell.

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
    nuke_active = getattr(player, "reacting_to_nuke", False)
    if nuke_active:
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
                return (target.cell_row, target.cell_col)
        elif player.final_lives <= max_lives * 0.3:
            # Non-support, lives critical: seek allied medic for safety
            medic = _find_role(all_alive, player.team_color, "medic")
            if medic and medic.cell_row is not None:
                return (medic.cell_row, medic.cell_col)
        else:
            # MECH-06: if player knows enemy commander location (memory system),
            # set movement goal to enemy commander cell to attempt tag-cancel.
            enemy_cmd_cell = _find_enemy_commander_in_memory(
                player, enemy_color, second, time_domain
            )
            if enemy_cmd_cell is not None:
                return enemy_cmd_cell

    # ── 1. Critical-resource overrides (non-support roles) ───────────────────
    if player.role not in ("medic", "ammo"):
        if player.final_lives <= max_lives * 0.3:
            medic = _find_role(all_alive, player.team_color, "medic")
            if medic and medic.cell_row is not None:
                return (medic.cell_row, medic.cell_col)
        elif player.final_shots <= max_shots * 0.3:
            ammo = _find_role(all_alive, player.team_color, "ammo")
            if ammo and ammo.cell_row is not None:
                return (ammo.cell_row, ammo.cell_col)

    if movement_ctx is None or cell_row is None or cell_col is None:
        return default_goal

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
                    return (allied_medic.cell_row, allied_medic.cell_col)

    # ── 2. Action-driven movement ────────────────────────────────────────────
    goal = _goal_from_action(
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
    if goal is not None:
        return goal

    # ── 3. Role-specific positioning ─────────────────────────────────────────
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
    if goal is not None:
        return goal

    # ── 4. Default: enemy base ───────────────────────────────────────────────
    return default_goal
