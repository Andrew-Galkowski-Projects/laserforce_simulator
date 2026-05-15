from __future__ import annotations

import heapq
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from .map_context import MapContext


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


def astar_next_step(
    start: tuple[int, int],
    goal: tuple[int, int],
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: dict | None = None,
) -> tuple[int, int]:
    """Return the first step on the shortest path from start toward goal.

    Uses A* with Manhattan-distance heuristic and optional elevation costs.
    Returns start unchanged when: start == goal, no path exists, or start
    is not in the adjacency graph.
    """
    if start == goal or start not in adj:
        return start

    h = abs(goal[0] - start[0]) + abs(goal[1] - start[1])
    heap: list[tuple[float, float, tuple[int, int]]] = [(h, 0.0, start)]
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}

    while heap:
        _, g, current = heapq.heappop(heap)

        if g > g_score.get(current, float("inf")):
            continue

        if current == goal:
            node = goal
            while came_from.get(node) != start:
                node = came_from[node]
            return node

        for neighbor in adj.get(current, []):
            cost = g + _movement_cost(current, neighbor, elevation_data)
            if cost < g_score.get(neighbor, float("inf")):
                g_score[neighbor] = cost
                came_from[neighbor] = current
                nh = abs(goal[0] - neighbor[0]) + abs(goal[1] - neighbor[1])
                heapq.heappush(heap, (cost + nh, cost, neighbor))

    return start


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


def _goal_from_action(
    player,
    all_alive: list,
    enemy_color: str,
    cell_row: int,
    cell_col: int,
    intended_action: str,
    movement_ctx: "MapContext",
) -> tuple[int, int] | None:
    """Return a goal cell driven by the player's last action, or None."""
    cell_los_counts: dict[str, int] = movement_ctx.cell_los_counts

    if intended_action in ("tag_player", "missile_player"):
        if player.role == "commander":
            enemy_medic = _find_role(all_alive, enemy_color, "medic")
            if enemy_medic and enemy_medic.cell_row is not None:
                return (enemy_medic.cell_row, enemy_medic.cell_col)
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
) -> tuple[int, int] | None:
    """Return a role-specific goal cell, or None."""
    cell_los_counts: dict[str, int] = movement_ctx.cell_los_counts
    high_los_cells: list = movement_ctx.get_high_los_cells()
    strong_spots: list = movement_ctx.get_strong_spots()
    sight_data: dict = movement_ctx.sight_data or {}

    if player.role == "scout":
        if high_los_cells:
            goal = _nearest_cell(cell_row, cell_col, high_los_cells)
            if goal:
                return goal

    elif player.role == "heavy":
        max_lives = getattr(player, "max_lives", player.starting_lives)
        max_shots = getattr(player, "max_shots", player.starting_shots)
        healthy = (
            player.final_lives > max_lives * 0.5
            and player.final_shots > max_shots * 0.5
        )
        if healthy and strong_spots:
            goal = _nearest_cell(cell_row, cell_col, strong_spots)
            if goal:
                return goal
        for support_role in ("medic", "ammo"):
            ally = _find_role(all_alive, player.team_color, support_role)
            if ally and ally.cell_row is not None:
                return (ally.cell_row, ally.cell_col)

    elif player.role == "medic":
        heavy = _find_role(all_alive, player.team_color, "heavy")
        if heavy and heavy.cell_row is not None:
            heavy_key = f"{heavy.cell_row},{heavy.cell_col}"
            heavy_visible = sight_data.get(heavy_key, frozenset())
            if heavy_visible and cell_los_counts:
                best = min(heavy_visible, key=lambda k: cell_los_counts.get(k, 0))
                r, c = best.split(",")
                return (int(r), int(c))
            return (heavy.cell_row, heavy.cell_col)

    elif player.role == "ammo":
        heavy = _find_role(all_alive, player.team_color, "heavy")
        if heavy and heavy.cell_row is not None:
            heavy_key = f"{heavy.cell_row},{heavy.cell_col}"
            heavy_visible = sight_data.get(heavy_key, frozenset())
            if heavy_visible and cell_los_counts:
                best = max(heavy_visible, key=lambda k: cell_los_counts.get(k, 0))
                r, c = best.split(",")
                return (int(r), int(c))
            return (heavy.cell_row, heavy.cell_col)

    elif player.role == "commander":
        enemy_medic = _find_role(all_alive, enemy_color, "medic")
        if enemy_medic and enemy_medic.cell_row is not None:
            return (enemy_medic.cell_row, enemy_medic.cell_col)

    return None


def choose_goal_cell(
    player,
    all_alive: list,
    spawn_cells: dict[str, tuple[int, int]],
    movement_ctx: "MapContext | None" = None,
    intended_action: str = "",
) -> tuple[int, int] | None:
    """Return the cell a player should navigate toward (MAP-05).

    Goal selection is action-aware (uses the player's previous chosen action) and
    role-aware. Priority order:
    1. Critical-resource override: seek medic (low lives) or ammo (low shots) for
       non-support roles — always trumps role/action logic.
    2. Action-driven movement: based on the action chosen last tick (intended_action):
       - tag_player / missile_player: move toward enemies (Commander → enemy medic first).
       - resupply_ally: Medic → neediest ally by lives; Ammo → neediest ally by shots.
       - hide: move to the safest adjacent cell (lowest LOS count).
    3. Role-specific positioning (for change_zone, capture_base, use_special, or fallback):
       - Scout: nearest high-LOS cell (top 25% of cells by sight-line count).
       - Heavy: nearest strong-spot cell when healthy (>50% lives and shots); otherwise
         seek nearest allied Medic or Ammo dynamically.
       - Medic: nearest low-LOS cell within the allied Heavy's visible set.
       - Ammo: nearest high-LOS cell within the allied Heavy's visible set.
       - Commander: enemy medic cell (priority target), else enemy base.
    4. Default: enemy base cell.
    """
    enemy_color = "blue" if player.team_color == "red" else "red"
    default_goal: tuple[int, int] | None = spawn_cells.get(enemy_color)

    cell_row: int | None = getattr(player, "cell_row", None)
    cell_col: int | None = getattr(player, "cell_col", None)

    max_lives = getattr(player, "max_lives", player.starting_lives)
    max_shots = getattr(player, "max_shots", player.starting_shots)

    # ── 0. MECH-04: nuke-reaction override (highest priority when active) ────
    if getattr(player, "reacting_to_nuke", False):
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
            # TODO MECH-06: if player knows enemy commander location (memory system),
            #     set movement goal to enemy commander cell to attempt tag-cancel.
            #     For now: no action override — hook wired but left empty.
            pass

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

    # ── 2. Action-driven movement ────────────────────────────────────────────
    goal = _goal_from_action(
        player,
        all_alive,
        enemy_color,
        cell_row,
        cell_col,
        intended_action,
        movement_ctx,
    )
    if goal is not None:
        return goal

    # ── 3. Role-specific positioning ─────────────────────────────────────────
    goal = _goal_from_role(
        player, all_alive, enemy_color, cell_row, cell_col, movement_ctx
    )
    if goal is not None:
        return goal

    # ── 4. Default: enemy base ───────────────────────────────────────────────
    return default_goal
