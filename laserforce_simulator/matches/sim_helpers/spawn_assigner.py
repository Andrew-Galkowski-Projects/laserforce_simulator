"""Spawn cell assignment logic shared by ResourceBasedSimulator and BatchSimulator.

Extracted from ResourceBasedSimulator._build_spawn_assignments (MAP-08) so that
both the DB-backed and in-memory simulators can call a single implementation.
"""

from collections import deque


def _draw_front(
    pool_deque: deque[tuple[int, int]],
    drawn_cells: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Pop the most-aggressive cell (closest to enemy) from the front."""
    if not pool_deque:
        return None
    cell = pool_deque.popleft()
    drawn_cells.add(cell)
    return cell


def _draw_back(
    pool_deque: deque[tuple[int, int]],
    drawn_cells: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Pop the most-sheltered cell (farthest from enemy) from the back."""
    if not pool_deque:
        return None
    cell = pool_deque.pop()
    drawn_cells.add(cell)
    return cell


def _overflow(
    base_cell: tuple[int, int] | None,
    drawn_cells: set[tuple[int, int]],
) -> tuple[int, int] | None:
    """Return the overflow slot when the pool is exhausted.

    If the base cell has not yet been drawn from the pool it can be shared
    among overflow players.  If the base cell was already drawn from the pool
    there is no safe fallback, so None is returned and the caller falls back
    to 3-zone placement.
    """
    if base_cell is None:
        return None
    if base_cell not in drawn_cells:
        return base_cell
    return None


def assign_spawn_cells(
    roster_roles: list[str],
    team_color: str,
    spawn_cells: dict,
    team_spawn_pools: dict,
) -> dict[int, tuple[int, int] | None]:
    """Pre-compute spawn cell assignments for all players in a team.

    Uses role-aware, no-replacement drawing to ensure unique cells.
    Processes roles in priority order (regardless of roster order):
      1. Commander / Heavy → most-aggressive cells available (front of pool,
         closest to the enemy base)
      2. Medic / Ammo      → most-sheltered cells available (back of pool,
         farthest from the enemy base)
      3. Scouts            → remaining cells

    The pool is sorted by distance to the enemy base (ascending).
    Overflow handling (pool exhausted):
      - If the base cell was NOT drawn from pool: share it (multiple
        overflow players may occupy it).
      - If the base cell WAS drawn from pool: assign None (player falls
        back to 3-zone).

    Args:
        roster_roles: Ordered list of role strings (e.g. ``["commander",
            "scout", "medic"]``), one per player in roster order.
        team_color: ``"red"`` or ``"blue"``.
        spawn_cells: Dict mapping color strings to ``(row, col)`` tuples,
            e.g. ``{"red": (2, 3), "blue": (14, 12)}``.
        team_spawn_pools: Dict mapping color strings to lists of
            ``(row, col)`` tuples representing valid spawn positions,
            e.g. ``{"red": [(2,3),(2,4),...], "blue": [...]}``.

    Returns:
        A dict mapping roster index → ``(row, col) | None``.  ``None``
        means the player should fall back to 3-zone placement.
    """
    pool = team_spawn_pools.get(team_color)
    if not pool:
        return {i: None for i in range(len(roster_roles))}

    base_cell = spawn_cells.get(team_color)
    enemy_color = "blue" if team_color == "red" else "red"
    enemy_base = spawn_cells.get(enemy_color)

    if base_cell is None or enemy_base is None:
        return {i: None for i in range(len(roster_roles))}

    # Sort pool ascending by distance to enemy (most-aggressive first).
    sorted_pool: list[tuple[int, int]] = sorted(
        pool,
        key=lambda cell: (
            abs(cell[0] - enemy_base[0]) + abs(cell[1] - enemy_base[1])
        ),
    )

    # Group roster indices by role priority.
    priority_groups: list[list[int]] = [[], [], []]
    for i, role in enumerate(roster_roles):
        if role in ("commander", "heavy"):
            priority_groups[0].append(i)
        elif role in ("medic", "ammo"):
            priority_groups[1].append(i)
        else:
            priority_groups[2].append(i)

    pool_deque: deque[tuple[int, int]] = deque(sorted_pool)
    drawn_cells: set[tuple[int, int]] = set()

    assignments: dict[int, tuple[int, int] | None] = {}
    for i in priority_groups[0]:
        cell = _draw_front(pool_deque, drawn_cells)
        assignments[i] = cell if cell is not None else _overflow(base_cell, drawn_cells)
    for i in priority_groups[1]:
        cell = _draw_back(pool_deque, drawn_cells)
        assignments[i] = cell if cell is not None else _overflow(base_cell, drawn_cells)
    for i in priority_groups[2]:
        cell = _draw_front(pool_deque, drawn_cells)
        assignments[i] = cell if cell is not None else _overflow(base_cell, drawn_cells)

    return assignments
