"""Map-loading helpers extracted from ``ResourceBasedSimulator`` (SIM-09).

Free-function module that owns every map-data ORM query plus the
:class:`MapContext` construction. Both simulators (currently only
``BatchSimulator``, post-SIM-09) and the ``score_averages`` command call
``load_map_context`` to turn an :class:`~core.models.ArenaMap` into a
ready-to-use ``(MapContext, zone_size)`` pair.

The five public functions mirror the former ``ResourceBasedSimulator.*``
static helpers byte-for-byte (signatures + behaviour). They are exposed as
module-level functions so callers can ``from matches.sim_helpers.map_loader
import load_map_context`` without going through a stale class.

Django ORM imports are kept lazy / module-scoped: ``core.models`` is
imported at module import time (matching the original layout in
``matches/simulation.py``), which is safe because ``matches`` already
depends on ``core``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from core.models import (
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    SightLineConfig,
)

from .map_context import MapContext
from .pathfinding import build_movement_adjacency
from .spawn_assigner import assign_spawn_cells


@dataclass
class MapData:
    """All map-derived data needed for one simulation round.

    Retained for backward-compat with the legacy two-step
    ``resolve_map_data`` -> ``build_movement_ctx`` pipeline. New callers
    should use :func:`load_map_context` directly.
    """

    zone_size: int | None
    spawn_cells: dict
    zone_data: list | None
    sight_data: dict | None
    base_sight_data: dict
    cell_ranking: list = field(default_factory=list)
    strong_spots: list = field(default_factory=list)
    wall_meta: dict = field(default_factory=dict)
    spawn_pools: dict = field(default_factory=dict)
    elevation_grid: list | None = None


def resolve_map_data(arena_map) -> MapData:
    """Load all map-derived data from the DB into a :class:`MapData` dataclass.

    Returns a ``MapData`` with ``zone_size=None`` / empty fields when
    ``arena_map`` is ``None``. Raises ``ValueError`` when the map lacks its
    confirmed zone config, a red/blue base placement, or computed sight
    lines / base sight lines.
    """
    if arena_map is None:
        return MapData(
            zone_size=None,
            spawn_cells={},
            zone_data=None,
            sight_data=None,
            base_sight_data={},
        )

    config = arena_map.latest_confirmed_config()
    if config is None:
        raise ValueError(
            f"Map '{arena_map.name}' has no confirmed zone configuration. "
            "Please confirm a zone config in the map editor before simulating."
        )

    zone_size = config.zone_size
    raw = config.zone_data
    zone_grid = raw["zones"] if isinstance(raw, dict) else raw
    wall_meta: dict = raw.get("wall_meta", {}) if isinstance(raw, dict) else {}
    elevation_grid = raw.get("elevation") if isinstance(raw, dict) else None

    base_cfgs = {
        bc.base_type: bc
        for bc in MapBaseConfig.objects.filter(
            arena_map=arena_map, base_type__in=["red", "blue"]
        )
    }

    spawn_cells: dict = {}
    for color in ("red", "blue"):
        base_cfg = base_cfgs.get(color)
        if base_cfg is None:
            raise ValueError(
                f"Map '{arena_map.name}' has no {color} base placed. "
                "Place a red and blue base in the map editor before simulating."
            )
        spawn_cells[color] = (
            base_cfg.y_px // zone_size,
            base_cfg.x_px // zone_size,
        )

    sight_config = SightLineConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    if sight_config is None:
        raise ValueError(
            f"Map '{arena_map.name}' has no sight lines computed for zone size "
            f"{zone_size}px. Click 'Compute Sight Lines' in the map editor before simulating."
        )
    sight_data = {k: frozenset(v) for k, v in sight_config.sight_data.items()}

    base_sight_configs = list(
        BaseSightLineConfig.objects.filter(arena_map=arena_map, zone_size=zone_size)
    )
    if not base_sight_configs:
        raise ValueError(
            f"Map '{arena_map.name}' has no base sight lines computed for zone size "
            f"{zone_size}px. Click 'Compute Sight Lines' in the map editor before simulating."
        )
    base_sight_data = {
        bsc.base_type: frozenset(f"{r},{c}" for r, c in bsc.visible_cells)
        for bsc in base_sight_configs
    }

    ranking_config = MapCellRankingConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    cell_ranking = ranking_config.ranked_cells if ranking_config else []

    strong_spots_config = HeavyStrongSpotsConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    strong_spots = strong_spots_config.cells if strong_spots_config else []

    spawn_pools: dict[str, list[tuple[int, int]]] = {}
    if isinstance(raw, dict):
        for color in ("red", "blue"):
            pool = raw.get(f"{color}_spawn", [])
            if pool:
                spawn_pools[color] = [tuple(rc) for rc in pool]

    return MapData(
        zone_size=zone_size,
        spawn_cells=spawn_cells,
        zone_data=zone_grid,
        sight_data=sight_data,
        base_sight_data=base_sight_data,
        cell_ranking=cell_ranking,
        strong_spots=strong_spots,
        wall_meta=wall_meta,
        spawn_pools=spawn_pools,
        elevation_grid=elevation_grid,
    )


def build_movement_ctx(
    zone_data,
    spawn_cells,
    *,
    sight_data=None,
    base_sight_data=None,
    cell_los_counts=None,
    high_los_cells=None,
    strong_spots=None,
    wall_meta=None,
    team_spawn_pools=None,
    elevation_grid=None,
) -> MapContext | None:
    """Build a :class:`MapContext` from pre-resolved map data.

    Returns ``None`` when ``zone_data`` is ``None`` (no map / 3-zone
    fallback). ``cell_los_counts`` and ``high_los_cells`` may be passed in
    pre-computed; when omitted they are derived from ``sight_data`` and the
    cell-ranking-driven top-25% list respectively (consistent with the
    inlined logic in :func:`load_map_context`).
    """
    if zone_data is None:
        return None

    if cell_los_counts is None:
        cell_los_counts = (
            {k: len(v) for k, v in sight_data.items()} if sight_data else {}
        )

    return MapContext(
        adj=build_movement_adjacency(zone_data),
        spawn_cells=spawn_cells,
        zone_data=zone_data,
        sight_data=sight_data,
        base_sight_data=base_sight_data or {},
        cell_los_counts=cell_los_counts,
        high_los_cells=[tuple(rc) for rc in (high_los_cells or [])],
        strong_spots=[tuple(rc) for rc in (strong_spots or [])],
        wall_meta=wall_meta or {},
        team_spawn_pools=team_spawn_pools or {},
        elevation_grid=elevation_grid,
    )


def load_map_context(
    arena_map,
) -> tuple[MapContext | None, int | None]:
    """Load all map data from DB and build the movement context in one step.

    Returns ``(movement_ctx, zone_size)`` where ``movement_ctx`` is a
    :class:`MapContext` ready for use in the simulation tick loop, or
    ``None`` when ``arena_map`` is ``None`` (3-zone fallback). ``zone_size``
    is the integer pixel size of one cell, or ``None`` for the fallback.

    Internally delegates to :func:`resolve_map_data` for the ORM queries
    then derives the two computed ``MapContext`` fields
    (``cell_los_counts``, ``high_los_cells``) from its return — one source
    of truth for the map-config requirements.

    Raises:
        ValueError: when the map lacks its confirmed zone config, a red /
            blue base placement, computed sight lines, or computed base
            sight lines.
    """
    if arena_map is None:
        return None, None

    md = resolve_map_data(arena_map)

    cell_los_counts: dict[str, int] = (
        {k: len(v) for k, v in md.sight_data.items()} if md.sight_data else {}
    )
    top_n = max(1, len(md.cell_ranking) // 4) if md.cell_ranking else 0
    high_los_cells: list[tuple[int, int]] = [
        tuple(rc) for rc in md.cell_ranking[:top_n]
    ]

    movement_ctx = MapContext(
        adj=build_movement_adjacency(md.zone_data),
        spawn_cells=md.spawn_cells,
        zone_data=md.zone_data,
        sight_data=md.sight_data,
        base_sight_data=md.base_sight_data,
        cell_los_counts=cell_los_counts,
        high_los_cells=high_los_cells,
        strong_spots=[tuple(rc) for rc in md.strong_spots],
        wall_meta=md.wall_meta,
        team_spawn_pools=md.spawn_pools,
        elevation_grid=md.elevation_grid,
    )

    return movement_ctx, md.zone_size


def zone_from_cell(row: int, col: int, spawn_cells: dict | None) -> int:
    """Return zone index (0=red, 1=neutral, 2=blue) via proximity to base cells.

    Nearest base type determines the zone. Neutral bases take precedence
    over team bases when equidistant or closer. Returns ``1`` (neutral)
    when ``spawn_cells`` is ``None``/empty or either team base is missing.
    """
    if not spawn_cells:
        return 1
    red_base = spawn_cells.get("red")
    blue_base = spawn_cells.get("blue")
    if red_base is None or blue_base is None:
        return 1
    dist_red = abs(row - red_base[0]) + abs(col - red_base[1])
    dist_blue = abs(row - blue_base[0]) + abs(col - blue_base[1])
    neutral_bases = [
        spawn_cells[f"neutral_{i}"]
        for i in range(1, 5)
        if f"neutral_{i}" in spawn_cells
    ]
    dist_neutral = min(
        (abs(row - nb[0]) + abs(col - nb[1]) for nb in neutral_bases),
        default=float("inf"),
    )
    if dist_neutral < dist_red and dist_neutral < dist_blue:
        return 1  # nearest to a neutral base
    if dist_red < dist_blue:
        return 0  # red zone
    if dist_blue < dist_red:
        return 2  # blue zone
    return 1  # equidistant = neutral


def build_spawn_assignments(
    roster_roles: list[str],
    team_color: str,
    spawn_cells: dict,
    team_spawn_pools: dict,
) -> dict[int, tuple[int, int] | None]:
    """Pre-compute spawn cell assignments for all players in a team.

    Thin delegation shim over
    :func:`matches.sim_helpers.spawn_assigner.assign_spawn_cells`, kept for
    callers that want to reach the role-priority spawn logic by the same
    name as the former ``ResourceBasedSimulator._build_spawn_assignments``.
    """
    return assign_spawn_cells(roster_roles, team_color, spawn_cells, team_spawn_pools)
