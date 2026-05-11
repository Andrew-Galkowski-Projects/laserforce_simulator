import heapq


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


def build_movement_adjacency(
    zone_data: list[list[int]],
) -> dict[tuple[int, int], list[tuple[int, int]]]:
    """Return 4-connected passable neighbors for every non-wall cell.

    Wall cells (value == 0) are excluded from the dict entirely so callers
    can use `cell in adj` as a passability check.
    """
    rows = len(zone_data)
    cols = len(zone_data[0]) if rows else 0
    adj: dict[tuple[int, int], list[tuple[int, int]]] = {}

    for r in range(rows):
        for c in range(cols):
            if zone_data[r][c] == 0:
                continue
            neighbors = []
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < rows and 0 <= nc < cols and zone_data[nr][nc] != 0:
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
    # heap entry: (f_cost, g_cost, current_cell)
    heap: list[tuple[float, float, tuple[int, int]]] = [(h, 0.0, start)]
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    came_from: dict[tuple[int, int], tuple[int, int]] = {}

    while heap:
        _, g, current = heapq.heappop(heap)

        if g > g_score.get(current, float("inf")):
            continue  # stale heap entry

        if current == goal:
            # Walk came_from back to the direct child of start
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


def choose_goal_cell(
    player,
    all_alive: list,
    spawn_cells: dict[str, tuple[int, int]],
) -> tuple[int, int] | None:
    """Return the cell a player should navigate toward.

    Default: enemy base cell. When resources are critical, redirects toward
    the closest allied support player who can help.
    """
    enemy_color = "blue" if player.team_color == "red" else "red"
    goal: tuple[int, int] | None = spawn_cells.get(enemy_color)

    lives_critical = player.max_lives * 0.3
    shots_critical = player.max_shots * 0.3

    if player.final_lives <= lives_critical and player.role != "medic":
        medic = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "medic"
                and p.final_lives > 0
                and p.cell_row is not None
            ),
            None,
        )
        if medic:
            return (medic.cell_row, medic.cell_col)
    elif player.final_shots <= shots_critical:
        ammo = next(
            (
                p
                for p in all_alive
                if p.team_color == player.team_color
                and p.role == "ammo"
                and p.final_lives > 0
                and p.cell_row is not None
            ),
            None,
        )
        if ammo:
            return (ammo.cell_row, ammo.cell_col)

    return goal
