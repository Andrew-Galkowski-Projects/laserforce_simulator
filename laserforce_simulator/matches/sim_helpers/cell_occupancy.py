"""RES-04: pure-Python cell occupancy reconstruction.

Given a player's compact movement trail (``[(start_cell, end_cell, ts), ...]``
of Advance steps in tick units), apportion the player's per-cell time across
the round and return ``{(r, c): tick_count}`` integer counts.

Algorithm:

* Walk the trail. For each ``(start, end, ts)`` entry, credit the stationary
  slice ``[cursor_tick, ts)`` to ``cursor_cell``, then credit the 1-tick
  Advance evenly across all ``len(route)+1`` cells walked (route = A* from
  ``start`` to ``end``, prefix-extended with ``start``).
* After the loop, credit the trailing stationary slice up to
  ``min(round_ticks, eliminated_at)``.
* Banker-round each per-cell float to an int; drop zero-rounded cells.

The function is pure: no Django imports, no I/O, no RNG.
"""

from __future__ import annotations

from typing import Optional

from .pathfinding import astar_path


def reconstruct_cell_occupancy(
    movement_trail: list[tuple[tuple[int, int], tuple[int, int], int]],
    spawn_cell: tuple[int, int],
    round_ticks: int,
    eliminated_at: int,
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: Optional[dict[tuple[int, int], float]] = None,
) -> dict[tuple[int, int], int]:
    """Reconstruct per-cell tick counts from a player's movement trail.

    See module docstring for algorithm. Caller is responsible for converting
    tuple keys/int player IDs into the JSON-friendly ``"r,c"`` / ``str(id)``
    forms; this function stays in the pure-Python tuple/int domain so it is
    testable without JSON round-trip.
    """
    accum: dict[tuple[int, int], float] = {}
    cursor_cell: tuple[int, int] = spawn_cell
    cursor_tick: int = 0
    end_tick: int = min(round_ticks, eliminated_at)

    for start_cell, end_cell, ts in movement_trail:
        # 1. Stationary slice [cursor_tick, ts) on cursor_cell.
        stationary_end = min(ts, end_tick)
        stationary = stationary_end - cursor_tick
        if stationary > 0:
            accum[cursor_cell] = accum.get(cursor_cell, 0.0) + stationary

        # If the stationary slice already runs past the end_tick, stop.
        if ts >= end_tick:
            return _finalize(accum)

        # 2. Advance slice at ts (consumes 1 tick).
        route_cells = astar_path(start_cell, end_cell, adj, elevation_data)
        if route_cells:
            n = len(route_cells) + 1  # +1 for start_cell
            share = 1.0 / n
            accum[start_cell] = accum.get(start_cell, 0.0) + share
            for cell in route_cells:
                accum[cell] = accum.get(cell, 0.0) + share
        else:
            # Defensive: no route returned (start == goal or unreachable).
            # Credit the whole tick to cursor_cell and skip expansion.
            accum[cursor_cell] = accum.get(cursor_cell, 0.0) + 1.0

        # 3. Advance the cursor.
        cursor_cell = end_cell
        cursor_tick = ts + 1

        # 4. Stop if we're past the end_tick.
        if cursor_tick >= end_tick:
            return _finalize(accum)

    # Trailing stationary slice after the last Advance.
    trailing = max(0, end_tick - cursor_tick)
    if trailing > 0:
        accum[cursor_cell] = accum.get(cursor_cell, 0.0) + trailing

    return _finalize(accum)


def _finalize(accum: dict[tuple[int, int], float]) -> dict[tuple[int, int], int]:
    """Banker-round float accumulators to ints, drop zero-rounded cells."""
    out: dict[tuple[int, int], int] = {}
    for cell, value in accum.items():
        rounded = int(round(value))
        if rounded != 0:
            out[cell] = rounded
    return out
