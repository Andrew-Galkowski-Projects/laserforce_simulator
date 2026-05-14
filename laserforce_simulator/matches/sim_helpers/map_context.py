"""MapContext: typed wrapper around the movement_ctx god-object dict.

Replaces the 11-key dict returned by ``_build_movement_ctx`` with a class
that exposes domain-level accessor methods.  All callers that formerly used
``movement_ctx.get("sight_data")`` etc. should prefer the methods below.

Backward-compatibility helpers:
    ``MapContext.from_dict(d)`` — construct from the legacy dict.
    ``MapContext.to_dict()`` — serialize back to the legacy dict format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class MapContext:
    """All precomputed map data needed during one simulation round.

    Attributes mirror the keys of the legacy movement_ctx dict:

    adj              — 4-connected passable-cell adjacency graph.
    spawn_cells      — {"red": (r,c), "blue": (r,c), ...} base cell map.
    zone_data        — 2-D list of int cell values (None when no map).
    sight_data       — {"r,c": frozenset(["r,c", ...])} normal LOS lookup.
    base_sight_data  — {"base_type": frozenset(["r,c", ...])} base-range lookup.
    cell_los_counts  — {"r,c": int} pre-computed per-cell LOS count.
    high_los_cells   — [(r,c), ...] top-25% cells by LOS count (Scout goal).
    strong_spots     — [(r,c), ...] Heavy defensive position cells.
    wall_meta        — {"r,c": {"facing": str, "height": float}} wall metadata.
    team_spawn_pools — {"red": [(r,c), ...], "blue": [(r,c), ...]} spawn pools.
    elevation_grid   — 2-D list of float cell elevations, or None.
    """

    adj: dict[tuple[int, int], list[tuple[int, int]]]
    spawn_cells: dict[str, tuple[int, int]]
    zone_data: list[list[int]] | None
    sight_data: dict[str, frozenset[str]] | None
    base_sight_data: dict[str, frozenset[str]]
    cell_los_counts: dict[str, int]
    high_los_cells: list[tuple[int, int]]
    strong_spots: list[tuple[int, int]]
    wall_meta: dict[str, Any]
    team_spawn_pools: dict[str, list[tuple[int, int]]]
    elevation_grid: list[list[float]] | None = None

    # ------------------------------------------------------------------
    # Domain-level accessors
    # ------------------------------------------------------------------

    def can_see(self, from_cell: tuple[int, int], to_cell: tuple[int, int]) -> bool:
        """Return True if *to_cell* is in the normal LOS set of *from_cell*.

        Both cells are (row, col) tuples.  Returns False when ``sight_data``
        is absent or *from_cell* has no registered sight lines.
        """
        if self.sight_data is None:
            return False
        from_key = f"{from_cell[0]},{from_cell[1]}"
        to_key = f"{to_cell[0]},{to_cell[1]}"
        return to_key in self.sight_data.get(from_key, frozenset())

    def elevation_at(self, r: int, c: int) -> float:
        """Return the elevation of cell (r, c), defaulting to 0.0.

        Returns 0.0 when ``elevation_grid`` is None or (r, c) is out of bounds.
        """
        if self.elevation_grid is None:
            return 0.0
        rows = len(self.elevation_grid)
        if rows == 0:
            return 0.0
        cols = len(self.elevation_grid[0])
        if 0 <= r < rows and 0 <= c < cols:
            return float(self.elevation_grid[r][c])
        return 0.0

    def base_in_range(self, cell: tuple[int, int]) -> int | None:
        """Return the base_id (15=neutral, 14/13=opposing) visible from *cell*, or None.

        Checks neutral bases first, then opposing base.  Returns None when
        ``base_sight_data`` is empty or the cell has no base in range.

        Note: this method does NOT check player-state flags
        (``neutral_base_destroyed``, ``opposing_base_destroyed``, or
        ``team_color``).  Use ``_get_base_interaction`` in combat.py for the
        full player-aware check.
        """
        if not self.base_sight_data:
            return None
        cell_key = f"{cell[0]},{cell[1]}"
        _NEUTRAL_BASE_TYPES = ("neutral_1", "neutral_2", "neutral_3", "neutral_4")
        for neutral_type in _NEUTRAL_BASE_TYPES:
            if cell_key in self.base_sight_data.get(neutral_type, frozenset()):
                return 15
        for opp_type in ("red", "blue"):
            if cell_key in self.base_sight_data.get(opp_type, frozenset()):
                return 14  # caller must map to 13 for blue attackers
        return None

    def get_adjacency(self) -> dict[tuple[int, int], list[tuple[int, int]]]:
        """Return the 4-connected passable-cell adjacency graph."""
        return self.adj

    def get_spawn_cells(self) -> dict[str, tuple[int, int]]:
        """Return the spawn/base cell dict keyed by team color / base type."""
        return self.spawn_cells

    def get_zone_data(self) -> list[list[int]] | None:
        """Return the 2-D zone grid, or None when no map is active."""
        return self.zone_data

    def get_wall_meta(self) -> dict[str, Any]:
        """Return the wall metadata dict (may be empty)."""
        return self.wall_meta

    def get_los_count(self, cell: tuple[int, int]) -> int:
        """Return the number of cells that can see *cell* (pre-computed LOS count)."""
        return self.cell_los_counts.get(f"{cell[0]},{cell[1]}", 0)

    def get_high_los_cells(self) -> list[tuple[int, int]]:
        """Return the top-25%-LOS cells (Scout sniping positions)."""
        return self.high_los_cells

    def get_strong_spots(self) -> list[tuple[int, int]]:
        """Return the Heavy defensive position cells."""
        return self.strong_spots

    def get_team_spawn_pools(self) -> dict[str, list[tuple[int, int]]]:
        """Return the per-team spawn-point pools."""
        return self.team_spawn_pools

    # ------------------------------------------------------------------
    # Backward-compatibility bridges
    # ------------------------------------------------------------------

    @classmethod
    def from_dict(cls, d: dict) -> "MapContext":
        """Construct a ``MapContext`` from the legacy 11-key movement_ctx dict."""
        return cls(
            adj=d.get("adj", {}),
            spawn_cells=d.get("spawn_cells", {}),
            zone_data=d.get("zone_data"),
            sight_data=d.get("sight_data"),
            base_sight_data=d.get("base_sight_data", {}),
            cell_los_counts=d.get("cell_los_counts", {}),
            high_los_cells=d.get("high_los_cells", []),
            strong_spots=d.get("strong_spots", []),
            wall_meta=d.get("wall_meta", {}),
            team_spawn_pools=d.get("team_spawn_pools", {}),
            elevation_grid=d.get("elevation_grid"),
        )

    def to_dict(self) -> dict:
        """Serialize back to the legacy 11-key movement_ctx dict format."""
        return {
            "adj": self.adj,
            "spawn_cells": self.spawn_cells,
            "zone_data": self.zone_data,
            "sight_data": self.sight_data,
            "base_sight_data": self.base_sight_data,
            "cell_los_counts": self.cell_los_counts,
            "high_los_cells": self.high_los_cells,
            "strong_spots": self.strong_spots,
            "wall_meta": self.wall_meta,
            "team_spawn_pools": self.team_spawn_pools,
            "elevation_grid": self.elevation_grid,
        }

    # ------------------------------------------------------------------
    # Legacy dict-style access (transitional — callers not yet migrated)
    # ------------------------------------------------------------------

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-style ``.get()`` shim for callers not yet migrated to methods.

        Supports all 11 legacy keys.  Prefer the typed accessor methods above.
        """
        return self.to_dict().get(key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-style ``[]`` access shim for callers not yet migrated to methods."""
        d = self.to_dict()
        if key not in d:
            raise KeyError(key)
        return d[key]

    def __contains__(self, key: object) -> bool:
        """Dict-style ``in`` check — returns True for any of the 11 legacy key names."""
        return key in self.to_dict()
