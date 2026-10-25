"""ANG-01c — map editor domain services shared by the HTML views and the API.

Every read / write / compute behind the map editor lives here so the HTML AJAX
views (``core/views.py``) and ``ArenaMapViewSet`` (``core/api_views.py``) run one
code path. Every function takes the ``ArenaMap`` instance — the callers do the
lookup (the HTML views keep their ``get_object_or_404``; the API uses
``get_object()``). ArenaMap is shared reference data (ADR-0038): nothing here is
ownership-filtered except the heatmap's GameRound corpus.

Dict key insertion order is pinned: ``JsonResponse`` serialises in insertion
order, so the HTML bodies stay byte-identical.

This module must NOT import ``core.views`` (``core.views`` imports it).
``matches.models`` / ``accounts.permissions`` are imported lazily inside
functions (the core -> matches import cycle).
"""

from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import transaction
from django.db.models.fields.files import FieldFile
from PIL import Image as PILImage, UnidentifiedImageError

from .map_processing import (
    compute_high_los_ranking,
    compute_sight_lines,
    compute_single_cell_visibility,
    compute_spawn_cells,
    create_processed_image,
    detect_zones,
)
from .models import (
    VALID_BASE_TYPES,
    ArenaMap,
    BaseSightLineConfig,
    HeavyStrongSpotsConfig,
    MapBaseConfig,
    MapCellRankingConfig,
    MapZoneConfig,
    SightLineConfig,
)

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser


class MapInUseError(Exception):
    """Raised by ``delete_map`` when a non-completed Season references the map."""


class InvalidMapImage(Exception):
    """Raised by ``upload_map`` when PIL cannot read the stored image file."""


# ---------------------------------------------------------------------------
# Storage helpers (moved verbatim from core/views.py; re-exported there)
# ---------------------------------------------------------------------------


def _get_image_local_path(image_field: FieldFile) -> str:
    """Return a local filesystem path for image_field.

    Works for both local FileSystemStorage (returns .path directly) and remote
    backends such as R2 (downloads to MEDIA_ROOT/maps/_remote_cache/ on first
    access and returns that cached path on subsequent calls).
    Map images are immutable after upload so the cache never goes stale.
    """
    try:
        return image_field.path
    except NotImplementedError:
        local_dir = settings.MEDIA_ROOT / "maps" / "_remote_cache"
        local_dir.mkdir(parents=True, exist_ok=True)
        local_path = local_dir / Path(image_field.name).name
        if not local_path.exists():
            with image_field.open("rb") as remote_f:
                local_path.write_bytes(remote_f.read())
        return str(local_path)


# ---------------------------------------------------------------------------
# Default maps (moved from core/views.py; re-exported there)
# ---------------------------------------------------------------------------

_DEFAULT_MAPS = [
    ("Syracuse Laser Tag", "Syracuse_Laser_Tag_Map_cropped.png"),
    ("San Marcos Laser Tag", "San_Marcos_Laser_Tag_Map.jpg"),
]


def _seed_defaults() -> None:
    from django.core.files.storage import FileSystemStorage, default_storage

    if not isinstance(default_storage, FileSystemStorage):
        return

    screenshots_dir = settings.BASE_DIR.parent / "Screenshots_and_video_examples"
    maps_dir = settings.MEDIA_ROOT / "maps"
    maps_dir.mkdir(parents=True, exist_ok=True)

    for name, filename in _DEFAULT_MAPS:
        src = screenshots_dir / filename
        if not src.exists():
            continue
        dst = maps_dir / filename
        if not dst.exists():
            shutil.copy2(src, dst)
        relative_path = f"maps/{filename}"
        if not ArenaMap.objects.filter(image=relative_path).exists():
            with PILImage.open(dst) as img:
                w, h = img.size
            ArenaMap.objects.create(
                name=name,
                image=relative_path,
                is_default=True,
                img_width=w,
                img_height=h,
            )


def _restore_default_images() -> None:
    """UXP-02 — copy back the image of any existing default map whose file is
    missing (a fresh media volume or a rebuilt container). Never adds rows."""
    from django.core.files.storage import FileSystemStorage, default_storage

    if not isinstance(default_storage, FileSystemStorage):
        return

    screenshots_dir = settings.BASE_DIR.parent / "Screenshots_and_video_examples"
    maps_dir = settings.MEDIA_ROOT / "maps"
    for _name, filename in _DEFAULT_MAPS:
        src = screenshots_dir / filename
        dst = maps_dir / filename
        if dst.exists() or not src.exists():
            continue
        if ArenaMap.objects.filter(image=f"maps/{filename}").exists():
            maps_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)


def ensure_default_maps() -> None:
    """ANG-06h — seed the default maps when no map exists, then restore any
    missing default image. Run by both the HTML ``map_list`` and the API list,
    so a fresh database lists the defaults through either. A no-op for a
    non-``FileSystemStorage`` backend."""
    if not ArenaMap.objects.exists():
        _seed_defaults()
    _restore_default_images()


def _clear_processed_cache(map_id: int) -> None:
    """Clear the cached processed image for a map."""
    processed_path = settings.MEDIA_ROOT / "maps" / f"processed_{map_id}.png"
    if processed_path.exists():
        processed_path.unlink()


# ---------------------------------------------------------------------------
# Upload / delete
# ---------------------------------------------------------------------------


@transaction.atomic
def upload_map(name: str, image_file: UploadedFile) -> ArenaMap:
    """Create an ArenaMap from ``image_file`` and record its pixel dimensions.

    Raises ``InvalidMapImage`` (after removing the stored file and the row) when
    PIL cannot read the stored image.
    """
    arena_map = ArenaMap(name=name, image=image_file)
    arena_map.save()

    with arena_map.image.open("rb") as f:
        content = f.read()
    try:
        with PILImage.open(io.BytesIO(content)) as img:
            arena_map.img_width, arena_map.img_height = img.size
    except (UnidentifiedImageError, OSError):
        arena_map.image.delete(save=False)
        arena_map.delete()
        raise InvalidMapImage()
    arena_map.save(update_fields=["img_width", "img_height"])

    # Clear cached processed image
    _clear_processed_cache(arena_map.pk)
    return arena_map


def map_in_use(arena_map: ArenaMap) -> bool:
    """True when a non-completed Season (or its Conference) references the map.

    Checks the live ``Season.map_pool`` M2M, the Season pool snapshot (which
    ``_resolve_fixture_map`` reads), the Season / Conference rotations and their
    snapshots. The JSON id-list membership runs in Python because SQLite has no
    JSONField ``contains``. ``GameRound.arena_map`` alone does not count.
    """
    from matches.models import Conference, Season  # lazy (core -> matches cycle)

    live = Season.objects.exclude(state="completed")  # draft + active
    if live.filter(map_pool=arena_map).exists():  # 1. Season.map_pool (M2M, live)
        return True
    pk = arena_map.pk
    for pool_snap, rot, rot_snap in live.values_list(
        "starting_map_pool_ids_json",  # 2. Season pool snapshot (resolver reads this)
        "map_rotation_ids_json",  # 3. Season rotation (live)
        "starting_map_rotation_ids_json",  # 4. Season rotation snapshot
    ):
        if any(
            isinstance(ids, list) and pk in ids for ids in (pool_snap, rot, rot_snap)
        ):
            return True
    for rot, rot_snap in Conference.objects.exclude(
        season__state="completed"
    ).values_list(
        "map_rotation_ids_json",  # 5. Conference rotation (live)
        "starting_map_rotation_ids_json",  # 6. Conference rotation snapshot
    ):
        if any(isinstance(ids, list) and pk in ids for ids in (rot, rot_snap)):
            return True
    return False


@transaction.atomic
def delete_map(arena_map: ArenaMap) -> None:
    """Delete a map, its processed cache and its image file.

    Raises ``MapInUseError`` (touching nothing) when ``map_in_use``. Configs
    cascade; ``GameRound.arena_map`` is ``SET_NULL``.
    """
    if map_in_use(arena_map):
        raise MapInUseError()
    map_id = arena_map.pk
    image = arena_map.image
    arena_map.delete()

    # Files are not transactional: remove them only once the row delete has
    # committed, so a rolled-back delete never leaves a map without its image.
    def _remove_files() -> None:
        _clear_processed_cache(map_id)
        image.delete(save=False)

    transaction.on_commit(_remove_files)


# ---------------------------------------------------------------------------
# Zones
# ---------------------------------------------------------------------------


def processed_image_path(arena_map: ArenaMap) -> Path:
    """``MEDIA_ROOT/maps/processed_<pk>.png``, built on first request."""
    processed_path = settings.MEDIA_ROOT / "maps" / f"processed_{arena_map.pk}.png"

    if not processed_path.exists():
        (settings.MEDIA_ROOT / "maps").mkdir(parents=True, exist_ok=True)
        img = create_processed_image(_get_image_local_path(arena_map.image))
        img.save(str(processed_path))

    return processed_path


def original_image_path(arena_map: ArenaMap) -> Path | None:
    """ANG-06h — a local path to the map's original uploaded image (through the
    storage-agnostic ``_get_image_local_path``), ``None`` when the map has no
    image or its file is gone."""
    if not arena_map.image:
        return None
    try:
        path = Path(_get_image_local_path(arena_map.image))
    except (FileNotFoundError, OSError):
        return None
    return path if path.is_file() else None


def _resolve_zone_data(arena_map: ArenaMap, zone_size: int) -> dict:
    """The zone data the LOS computes run on for ``zone_size``.

    The latest config of ANY confirmed state at that size (legacy list
    ``zone_data`` wrapped as ``{"zones": list}``), else auto-detection.
    """
    zone_config = (
        arena_map.zone_configs.filter(zone_size=zone_size)
        .order_by("-created_at")
        .first()
    )
    if zone_config:
        zone_data_full = zone_config.zone_data
        if isinstance(zone_data_full, list):
            return {"zones": zone_data_full}
        return zone_data_full
    return detect_zones(_get_image_local_path(arena_map.image), zone_size)


def detect_zones_with_overlay(arena_map: ArenaMap, zone_size: int) -> dict:
    """Auto-detected zones, overlaid with the confirmed config's edits.

    Only applied when the confirmed config's ``zone_size`` matches — different
    sizes are incompatible.
    """
    data = detect_zones(_get_image_local_path(arena_map.image), zone_size)

    # Overlay stored zones, wall_meta, and elevation from the confirmed config so
    # user edits survive page reloads.
    confirmed = arena_map.latest_confirmed_config()
    if (
        confirmed
        and confirmed.zone_size == zone_size
        and isinstance(confirmed.zone_data, dict)
    ):
        stored = confirmed.zone_data
        if "zones" in stored:
            data["zones"] = stored["zones"]
        if "wall_meta" in stored:
            data["wall_meta"] = stored["wall_meta"]
        if "elevation" in stored:
            data["elevation"] = stored["elevation"]

    return data


def get_zone_config(arena_map: ArenaMap) -> dict:
    """The latest confirmed config plus the map's bases.

    The shape round-trips: a zone-config PUT body may be this dict minus
    ``spawn_user_edited``.
    """
    config = arena_map.latest_confirmed_config()
    zone_size: int | None = None
    zones: list | None = None
    wall_meta: dict = {}
    elevation: list | None = None
    red_spawn: list = []
    blue_spawn: list = []
    spawn_user_edited = False
    if config is not None:
        zone_size = config.zone_size
        raw = config.zone_data
        if isinstance(raw, dict):
            zones = raw.get("zones")
            wall_meta = raw.get("wall_meta") or {}
            elevation = raw.get("elevation")
            red_spawn = raw.get("red_spawn", [])
            blue_spawn = raw.get("blue_spawn", [])
            spawn_user_edited = bool(raw.get("spawn_user_edited", False))
        elif isinstance(raw, list):
            zones = raw

    bases = [
        {"type": b.base_type, "x_px": b.x_px, "y_px": b.y_px}
        for b in arena_map.base_configs.order_by("base_type")
    ]
    return {
        "zone_size": zone_size,
        "zones": zones,
        "wall_meta": wall_meta,
        "elevation": elevation,
        "red_spawn": red_spawn,
        "blue_spawn": blue_spawn,
        "spawn_user_edited": spawn_user_edited,
        "bases": bases,
    }


@transaction.atomic
def save_zone_config(
    arena_map: ArenaMap,
    *,
    zone_size: int,
    zones: list | None,
    wall_meta: dict,
    elevation: list | None,
    red_spawn: list | None,
    blue_spawn: list | None,
    bases: list[dict],
) -> MapZoneConfig:
    """Replace the confirmed zone config and the map's bases.

    ``zones`` None ⇒ auto-detect; ``elevation`` / a spawn side None ⇒ carried
    forward from the existing confirmed config. ``wall_meta`` is never carried.
    """
    # If the client sends a full zones grid (user-edited wall types), use it.
    # Otherwise fall back to server-side auto-detection from the image.
    if zones is not None:
        blocked_edges: dict = {}
    else:
        data = detect_zones(_get_image_local_path(arena_map.image), zone_size)
        zones = data["zones"]
        blocked_edges = data.get("blocked_edges", {})

    # Carry forward any existing confirmed spawn cells and elevation data.
    existing_config = arena_map.latest_confirmed_config()
    existing_zone_data = existing_config.zone_data if existing_config else {}
    existing_is_dict = isinstance(existing_zone_data, dict)
    red_spawn_existing = (
        existing_zone_data.get("red_spawn", []) if existing_is_dict else []
    )
    blue_spawn_existing = (
        existing_zone_data.get("blue_spawn", []) if existing_is_dict else []
    )
    elevation_existing = (
        existing_zone_data.get("elevation") if existing_is_dict else None
    )
    if elevation is None:
        elevation = elevation_existing

    client_sent_spawn = red_spawn is not None or blue_spawn is not None
    if red_spawn is None:
        red_spawn = red_spawn_existing
    if blue_spawn is None:
        blue_spawn = blue_spawn_existing
    # The flag is set when the user explicitly paints cells (non-empty payload),
    # cleared when the user explicitly erases everything (empty payload), and
    # carried forward unchanged when the user didn't touch spawn at all.
    spawn_user_edited_existing = (
        existing_zone_data.get("spawn_user_edited", False)
        if existing_is_dict
        else False
    )
    if client_sent_spawn:
        spawn_user_edited = bool(red_spawn) or bool(blue_spawn)
    else:
        spawn_user_edited = spawn_user_edited_existing

    MapZoneConfig.objects.filter(arena_map=arena_map, confirmed=True).update(
        confirmed=False
    )
    zone_data_payload: dict = {
        "zones": zones,
        "blocked_edges": blocked_edges,
    }
    if wall_meta:
        zone_data_payload["wall_meta"] = wall_meta
    if elevation is not None:
        zone_data_payload["elevation"] = elevation
    if red_spawn:
        zone_data_payload["red_spawn"] = red_spawn
    if blue_spawn:
        zone_data_payload["blue_spawn"] = blue_spawn
    if spawn_user_edited:
        zone_data_payload["spawn_user_edited"] = True
    config = MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data_payload,
        confirmed=True,
    )

    MapBaseConfig.objects.filter(arena_map=arena_map).delete()
    for base in bases:
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type=base["type"],
            x_px=base["x_px"],
            y_px=base["y_px"],
        )

    # Clear cached processed image so it's regenerated with latest data
    _clear_processed_cache(arena_map.pk)
    return config


# ---------------------------------------------------------------------------
# Sight lines
# ---------------------------------------------------------------------------


def get_sight_lines(arena_map: ArenaMap, zone_size: int) -> dict:
    """``{"zone_size", "sight_data", "base_sights"}`` for ``zone_size``."""
    sight_config = SightLineConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()

    stored = {
        bsc.base_type: bsc.visible_cells
        for bsc in BaseSightLineConfig.objects.filter(
            arena_map=arena_map, zone_size=zone_size
        )
    }
    base_sights = {btype: stored.get(btype, []) for btype in VALID_BASE_TYPES}

    return {
        "zone_size": zone_size,
        "sight_data": sight_config.sight_data if sight_config else None,
        "base_sights": base_sights,
    }


def _store_ranking(arena_map: ArenaMap, zone_size: int, sight_data: dict) -> None:
    """Upsert the cell ranking and reseed Heavy strong spots (top quarter)."""
    ranked = compute_high_los_ranking(sight_data)
    top_n = max(1, len(ranked) // 4)
    MapCellRankingConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"ranked_cells": ranked},
    )
    HeavyStrongSpotsConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"cells": ranked[:top_n]},
    )


@transaction.atomic
def compute_and_store_sight_lines(
    arena_map: ArenaMap,
    zone_size: int,
    *,
    red_spawn: list | None = None,
    blue_spawn: list | None = None,
) -> dict:
    """Run the all-pairs LOS compute and persist it with its ranking.

    A spawn side that is not None is written to the confirmed config with the
    user-edited lock, so ``update_spawn_cells`` won't overwrite it.
    """
    sight_data = compute_sight_lines(
        _resolve_zone_data(arena_map, zone_size), use_quadtree=True
    )

    SightLineConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"sight_data": sight_data},
    )
    _store_ranking(arena_map, zone_size, sight_data)

    if red_spawn is not None or blue_spawn is not None:
        confirmed = arena_map.latest_confirmed_config()
        if confirmed:
            raw = (
                dict(confirmed.zone_data)
                if isinstance(confirmed.zone_data, dict)
                else {"zones": confirmed.zone_data}
            )
            if red_spawn is not None:
                raw["red_spawn"] = red_spawn
            if blue_spawn is not None:
                raw["blue_spawn"] = blue_spawn
            raw["spawn_user_edited"] = bool(red_spawn) or bool(blue_spawn)
            confirmed.zone_data = raw
            confirmed.save(update_fields=["zone_data"])

    update_spawn_cells(arena_map, zone_size)

    return {"sight_data": sight_data, "zone_size": zone_size}


def compute_single_cell(arena_map: ArenaMap, zone_size: int, r: int, c: int) -> dict:
    """Lazy LOS from one cell; ``[]`` when the origin is off-grid or impassable."""
    zone_data = _resolve_zone_data(arena_map, zone_size)
    return {"visible_cells": compute_single_cell_visibility(r, c, zone_data)}


@transaction.atomic
def save_sight_lines(
    arena_map: ArenaMap,
    zone_size: int,
    *,
    sight_data: dict,
    base_sights: dict,
    mode: Literal["replace", "merge"],
) -> None:
    """Save a batch of sight lines (``replace`` the first chunk, ``merge`` the rest).

    ``replace`` re-ranks and reseeds strong spots; ``merge`` re-ranks only.
    ``base_sights`` upserts per base type (other types untouched).
    """
    if mode == "replace":
        SightLineConfig.objects.update_or_create(
            arena_map=arena_map,
            zone_size=zone_size,
            defaults={"sight_data": sight_data},
        )
        if sight_data:
            _store_ranking(arena_map, zone_size, sight_data)
    else:
        config, _created = SightLineConfig.objects.get_or_create(
            arena_map=arena_map,
            zone_size=zone_size,
            defaults={"sight_data": {}},
        )
        # ANG-01c fix: a merge onto a just-created config keeps the batch
        # (previously it was discarded).
        if sight_data:
            config.sight_data = {**(config.sight_data or {}), **sight_data}
            config.save(update_fields=["sight_data"])
            # Keep cell ranking in sync with the full merged sight data.
            ranked = compute_high_los_ranking(config.sight_data)
            MapCellRankingConfig.objects.update_or_create(
                arena_map=arena_map,
                zone_size=zone_size,
                defaults={"ranked_cells": ranked},
            )

    for btype, cells in base_sights.items():
        BaseSightLineConfig.objects.update_or_create(
            arena_map=arena_map,
            base_type=btype,
            zone_size=zone_size,
            defaults={"visible_cells": cells},
        )

    # Auto-compute spawn cells whenever sight lines are (re)saved.
    update_spawn_cells(arena_map, zone_size)


# ---------------------------------------------------------------------------
# Spawns, ranking, strong spots
# ---------------------------------------------------------------------------


def update_spawn_cells(arena_map: ArenaMap, zone_size: int) -> None:
    """Auto-compute spawn cells and persist them into the confirmed MapZoneConfig.

    Called after sight lines are computed or saved. Skipped entirely when the user
    has explicitly painted spawn cells (zone_data["spawn_user_edited"] is True).
    To reset to auto-generation, erase all spawn cells in the editor and save.

    If base positions are not set, or no confirmed config exists, does nothing.
    """
    config = arena_map.latest_confirmed_config()
    if config is None:
        return

    raw = config.zone_data
    if isinstance(raw, dict) and raw.get("spawn_user_edited"):
        return

    base_cfgs = {
        bc.base_type: bc
        for bc in MapBaseConfig.objects.filter(
            arena_map=arena_map, base_type__in=["red", "blue"]
        )
    }
    if "red" not in base_cfgs or "blue" not in base_cfgs:
        return

    zone_grid = raw["zones"] if isinstance(raw, dict) else raw
    base_cells = {
        color: (
            base_cfgs[color].y_px // zone_size,
            base_cfgs[color].x_px // zone_size,
        )
        for color in ("red", "blue")
    }

    new_spawns = compute_spawn_cells(zone_grid, base_cells)

    updated = dict(raw) if isinstance(raw, dict) else {"zones": raw}
    for color in ("red", "blue"):
        key = f"{color}_spawn"
        candidates = new_spawns.get(color, [])
        if candidates:
            updated[key] = candidates

    config.zone_data = updated
    config.save(update_fields=["zone_data"])


def get_ranked_cells(arena_map: ArenaMap, zone_size: int) -> dict:
    """``{"ranked_cells": list}`` (``[]`` when not computed)."""
    config = MapCellRankingConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    return {"ranked_cells": config.ranked_cells if config else []}


def get_strong_spots(arena_map: ArenaMap, zone_size: int) -> dict:
    """``{"cells": list}`` (``[]`` when not seeded)."""
    config = HeavyStrongSpotsConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()
    return {"cells": config.cells if config else []}


@transaction.atomic
def save_strong_spots(arena_map: ArenaMap, zone_size: int, cells: list) -> None:
    """Persist user-edited Heavy strong spots."""
    HeavyStrongSpotsConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"cells": cells},
    )


def get_spawn_cells(arena_map: ArenaMap) -> dict:
    """``{"red_spawn", "blue_spawn"}`` from the confirmed config (per config, not per size)."""
    config = arena_map.latest_confirmed_config()
    red_spawn: list = []
    blue_spawn: list = []
    if config and isinstance(config.zone_data, dict):
        raw = config.zone_data
        red_spawn = raw.get("red_spawn", [])
        blue_spawn = raw.get("blue_spawn", [])
    return {"red_spawn": red_spawn, "blue_spawn": blue_spawn}


# ---------------------------------------------------------------------------
# RES-04 multi-round heatmap aggregate
# ---------------------------------------------------------------------------


def aggregate_map_heatmap(
    arena_map: ArenaMap,
    zone_size: int,
    team_color: str | None,
    user: "AbstractBaseUser | None",
) -> dict:
    """RES-04: cell occupancy summed across the requester's visible rounds.

    The GameRound corpus is scoped by ``owned_game_round_q(user)`` (own +
    Unmanaged, through Match / League); ``round_count`` counts that scoped
    corpus. ``team_color`` filters per-player contributions, not the count.
    """
    # Lazy import to avoid a core -> matches import cycle at module load.
    from accounts.permissions import owned_game_round_q
    from matches.models import GameRound, PlayerRoundState

    scope_user = user if getattr(user, "is_authenticated", False) else None
    rounds = list(
        GameRound.objects.filter(
            owned_game_round_q(scope_user),
            arena_map_id=arena_map.pk,
            zone_size=zone_size,
            cell_occupancy_json__isnull=False,
        )
    )

    # Batch-load player team_colors for every round in one query
    # (was N+1: one query per round). Bucket into {round_id: {player_id: color}}.
    round_ids = [gr.id for gr in rounds]
    team_lookups: dict[int, dict[int, str]] = {rid: {} for rid in round_ids}
    if round_ids:
        for round_id, player_id, color in PlayerRoundState.objects.filter(
            game_round_id__in=round_ids
        ).values_list("game_round_id", "player_id", "team_color"):
            team_lookups[round_id][player_id] = color

    cell_totals: dict[str, int] = {}
    round_count = 0
    for game_round in rounds:
        occupancy = game_round.cell_occupancy_json or {}
        if not isinstance(occupancy, dict):
            continue

        team_lookup = team_lookups.get(game_round.id, {})

        for player_id_str, per_cell in occupancy.items():
            if not isinstance(per_cell, dict):
                continue
            try:
                player_id = int(player_id_str)
            except (TypeError, ValueError):
                continue
            if team_color is not None:
                player_team = team_lookup.get(player_id)
                if player_team != team_color:
                    continue
            for cell_key, ticks in per_cell.items():
                if not isinstance(ticks, int):
                    continue
                cell_totals[cell_key] = cell_totals.get(cell_key, 0) + ticks

        # Count every round with a non-null cell_occupancy_json. The
        # team_color filter suppresses individual players' contributions
        # to cell totals but does not change the round count.
        round_count += 1

    # Drop any cells with a final sum of zero (matches per-round format).
    cell_occupancy = {k: v for k, v in cell_totals.items() if v != 0}

    # Pull rows/cols from the confirmed MapZoneConfig for (map, zone_size).
    rows = 0
    cols = 0
    zone_config = MapZoneConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size, confirmed=True
    ).first()
    if zone_config is not None and isinstance(zone_config.zone_data, dict):
        zones = zone_config.zone_data.get("zones")
        if isinstance(zones, list):
            rows = len(zones)
            if rows > 0 and isinstance(zones[0], list):
                cols = len(zones[0])

    return {
        "cell_occupancy": cell_occupancy,
        "zone_size": zone_size,
        "rows": rows,
        "cols": cols,
        "round_count": round_count,
    }
