import io
import json
import shutil
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.db.models.fields.files import FieldFile
from django.http import FileResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from PIL import Image as PILImage, UnidentifiedImageError

from .map_processing import (
    compute_high_los_ranking,
    compute_sight_lines,
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

_DEFAULT_MAPS = [
    ("Syracuse Laser Tag", "Syracuse_Laser_Tag_Map_cropped.png"),
    ("San Marcos Laser Tag", "San_Marcos_Laser_Tag_Map.jpg"),
]


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


def _clear_processed_cache(map_id):
    """Clear the cached processed image for a map."""
    processed_path = settings.MEDIA_ROOT / "maps" / f"processed_{map_id}.png"
    if processed_path.exists():
        processed_path.unlink()


def _seed_defaults():
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


def map_list(request):
    if not ArenaMap.objects.exists():
        _seed_defaults()
    maps = ArenaMap.objects.order_by("created_at")
    return render(request, "maps/map_list.html", {"maps": maps})


def upload_map(request):
    if request.method != "POST":
        return redirect("map_list")

    name = request.POST.get("name", "").strip()
    image_file = request.FILES.get("image")

    if not name or not image_file:
        return redirect("map_list")

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
        return redirect("map_list")
    arena_map.save(update_fields=["img_width", "img_height"])

    # Clear cached processed image
    _clear_processed_cache(arena_map.pk)

    return redirect("map_editor", map_id=arena_map.pk)


@require_POST
def delete_map(request, map_id):
    """Delete an ArenaMap and its cached processed image.

    Related zone/base/sight-line configs cascade automatically via their FK
    ``on_delete=CASCADE``. ``GameRound.arena_map`` is ``SET_NULL``, so any
    persisted rounds keep their results but lose the (now-deleted) map link.
    """
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    name = arena_map.name
    _clear_processed_cache(arena_map.pk)
    arena_map.image.delete(save=False)
    arena_map.delete()
    messages.success(request, f"Deleted map “{name}”.")
    return redirect("map_list")


def map_editor(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    confirmed_config = arena_map.latest_confirmed_config()
    saved_zone_size = confirmed_config.zone_size if confirmed_config else 50
    base_data = {
        b.base_type: {"x_px": b.x_px, "y_px": b.y_px}
        for b in arena_map.base_configs.all()
    }
    return render(
        request,
        "maps/map_editor.html",
        {
            "arena_map": arena_map,
            "saved_zone_size": saved_zone_size,
            "bases_json": json.dumps(base_data),
        },
    )


def process_zones(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        cell_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        cell_size = 50
    cell_size = max(10, min(cell_size, 200))
    data = detect_zones(_get_image_local_path(arena_map.image), cell_size)

    # Overlay stored zones, wall_meta, and elevation from the confirmed config so
    # user edits survive page reloads.  Only apply when the stored config's zone_size
    # matches the requested one — different sizes are incompatible.
    confirmed = arena_map.latest_confirmed_config()
    if (
        confirmed
        and confirmed.zone_size == cell_size
        and isinstance(confirmed.zone_data, dict)
    ):
        stored = confirmed.zone_data
        if "zones" in stored:
            data["zones"] = stored["zones"]
        if "wall_meta" in stored:
            data["wall_meta"] = stored["wall_meta"]
        if "elevation" in stored:
            data["elevation"] = stored["elevation"]

    return JsonResponse(data)


def processed_image(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    processed_path = settings.MEDIA_ROOT / "maps" / f"processed_{map_id}.png"

    if not processed_path.exists():
        (settings.MEDIA_ROOT / "maps").mkdir(parents=True, exist_ok=True)
        img = create_processed_image(_get_image_local_path(arena_map.image))
        img.save(str(processed_path))

    return FileResponse(open(processed_path, "rb"), content_type="image/png")


@require_POST
def save_zone_config(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = json.loads(request.body)
        zone_size = int(body.get("zone_size", 50))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"status": "error", "message": "Invalid zone_size"}, status=400
        )

    zone_size = max(10, min(zone_size, 200))

    # If the client sends a full zones grid (user-edited wall types), use it.
    # Otherwise fall back to server-side auto-detection from the image.
    client_zones = body.get("zones")
    wall_meta: dict = body.get("wall_meta") or {}
    if client_zones and isinstance(client_zones, list):
        zones = client_zones
        blocked_edges: dict = {}
    else:
        data = detect_zones(_get_image_local_path(arena_map.image), zone_size)
        zones = data["zones"]
        blocked_edges = data.get("blocked_edges", {})

    # Carry forward any existing confirmed spawn cells and elevation data, then apply client overrides.
    existing_config = arena_map.latest_confirmed_config()
    existing_zone_data = existing_config.zone_data if existing_config else {}
    red_spawn_existing = (
        existing_zone_data.get("red_spawn", [])
        if isinstance(existing_zone_data, dict)
        else []
    )
    blue_spawn_existing = (
        existing_zone_data.get("blue_spawn", [])
        if isinstance(existing_zone_data, dict)
        else []
    )
    # Carry forward elevation data from existing config (or accept client-sent elevation).
    elevation_existing = (
        existing_zone_data.get("elevation")
        if isinstance(existing_zone_data, dict)
        else None
    )
    client_elevation = body.get("elevation")
    if isinstance(client_elevation, list):
        for row in client_elevation:
            if not isinstance(row, list):
                return JsonResponse(
                    {
                        "status": "error",
                        "message": "Invalid elevation: each row must be a list",
                    },
                    status=400,
                )
            for val in row:
                if not isinstance(val, (int, float)) or not (0.0 <= float(val) <= 10.0):
                    return JsonResponse(
                        {
                            "status": "error",
                            "message": f"Elevation value out of range [0.0, 10.0]: {val}",
                        },
                        status=400,
                    )
    elevation = (
        client_elevation if isinstance(client_elevation, list) else elevation_existing
    )

    # Client may send user-edited spawn overrides (list of [r, c] pairs).
    client_red_spawn = body.get("red_spawn")
    client_blue_spawn = body.get("blue_spawn")
    client_sent_spawn = isinstance(client_red_spawn, list) or isinstance(
        client_blue_spawn, list
    )
    red_spawn = (
        client_red_spawn if isinstance(client_red_spawn, list) else red_spawn_existing
    )
    blue_spawn = (
        client_blue_spawn
        if isinstance(client_blue_spawn, list)
        else blue_spawn_existing
    )
    # Determine whether to protect these spawn cells from future auto-regeneration.
    # The flag is set when the user explicitly paints cells (non-empty client payload),
    # cleared when the user explicitly erases everything (empty client payload),
    # and carried forward unchanged when the user didn't touch spawn at all.
    spawn_user_edited_existing = (
        existing_zone_data.get("spawn_user_edited", False)
        if isinstance(existing_zone_data, dict)
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
    MapZoneConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        zone_data=zone_data_payload,
        confirmed=True,
    )

    MapBaseConfig.objects.filter(arena_map=arena_map).delete()
    for b in body.get("bases", []):
        btype = b.get("type", "")
        if btype in VALID_BASE_TYPES:
            MapBaseConfig.objects.create(
                arena_map=arena_map,
                base_type=btype,
                x_px=int(b.get("x_px", 0)),
                y_px=int(b.get("y_px", 0)),
            )

    # Clear cached processed image so it's regenerated with latest data
    _clear_processed_cache(arena_map.pk)

    return JsonResponse({"status": "ok"})


def get_sight_lines(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    sight_config = SightLineConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()

    base_sights = {}
    for btype in VALID_BASE_TYPES:
        bsc = BaseSightLineConfig.objects.filter(
            arena_map=arena_map, base_type=btype, zone_size=zone_size
        ).first()
        base_sights[btype] = bsc.visible_cells if bsc else []

    return JsonResponse(
        {
            "zone_size": zone_size,
            "sight_data": sight_config.sight_data if sight_config else None,
            "base_sights": base_sights,
        }
    )


@require_POST
def compute_sight_lines_view(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = json.loads(request.body)
        zone_size = int(body.get("zone_size", 50))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid zone_size"}, status=400)

    zone_size = max(10, min(zone_size, 200))

    zone_config = (
        arena_map.zone_configs.filter(zone_size=zone_size)
        .order_by("-created_at")
        .first()
    )
    if zone_config:
        zone_data_full = zone_config.zone_data
        # Rebuild blocked_edges_grid from stored zone_data if available
        if isinstance(zone_data_full, dict) and "blocked_edges_grid" in zone_data_full:
            zone_data = zone_data_full
        else:
            # Fall back to stored zones
            zone_data = (
                {"zones": zone_data_full}
                if isinstance(zone_data_full, list)
                else zone_data_full
            )
    else:
        zone_data = detect_zones(_get_image_local_path(arena_map.image), zone_size)

    sight_data = compute_sight_lines(zone_data, use_quadtree=True)

    SightLineConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"sight_data": sight_data},
    )

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

    # If the client sent unsaved spawn edits, write them to the confirmed config
    # with the user-edited lock so _update_spawn_cells_in_zone_data won't overwrite.
    client_red_spawn = body.get("red_spawn")
    client_blue_spawn = body.get("blue_spawn")
    if isinstance(client_red_spawn, list) or isinstance(client_blue_spawn, list):
        confirmed = arena_map.latest_confirmed_config()
        if confirmed:
            raw = (
                dict(confirmed.zone_data)
                if isinstance(confirmed.zone_data, dict)
                else {"zones": confirmed.zone_data}
            )
            if isinstance(client_red_spawn, list):
                raw["red_spawn"] = client_red_spawn
            if isinstance(client_blue_spawn, list):
                raw["blue_spawn"] = client_blue_spawn
            raw["spawn_user_edited"] = bool(client_red_spawn) or bool(client_blue_spawn)
            confirmed.zone_data = raw
            confirmed.save(update_fields=["zone_data"])

    _update_spawn_cells_in_zone_data(arena_map, zone_size)

    return JsonResponse({"sight_data": sight_data, "zone_size": zone_size})


def compute_single_cell_sight(request, map_id):
    """Lazy sight line compute: get visibility from ONE clicked cell (fast!).

    Query params: zone_size, r, c (cell coordinates)
    Returns: {"visible_cells": ["r,c", ...]}

    ~1000x faster than all-pairs for large maps.
    """
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
        r = int(request.GET.get("r", 0))
        c = int(request.GET.get("c", 0))
    except (ValueError, TypeError):
        return JsonResponse({"error": "Invalid parameters"}, status=400)

    zone_size = max(10, min(zone_size, 200))

    zone_config = (
        arena_map.zone_configs.filter(zone_size=zone_size)
        .order_by("-created_at")
        .first()
    )
    if zone_config:
        zone_data_full = zone_config.zone_data
        if isinstance(zone_data_full, dict) and "zones" in zone_data_full:
            zone_data = zone_data_full
        else:
            zone_data = (
                {"zones": zone_data_full}
                if isinstance(zone_data_full, list)
                else zone_data_full
            )
    else:
        zone_data = detect_zones(_get_image_local_path(arena_map.image), zone_size)

    from .map_processing import compute_single_cell_visibility

    visible = compute_single_cell_visibility(r, c, zone_data)

    return JsonResponse({"visible_cells": visible})


@require_POST
def save_sight_lines(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = json.loads(request.body)
        zone_size = int(body.get("zone_size", 50))
        sight_data = body.get("sight_data", {})
        base_sights = body.get("base_sights", {})
        replace = body.get("replace", True)
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"status": "error"}, status=400)

    zone_size = max(10, min(zone_size, 200))

    if replace:
        SightLineConfig.objects.update_or_create(
            arena_map=arena_map,
            zone_size=zone_size,
            defaults={"sight_data": sight_data},
        )
        if sight_data:
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
    else:
        config, created = SightLineConfig.objects.get_or_create(
            arena_map=arena_map,
            zone_size=zone_size,
            defaults={"sight_data": {}},
        )
        if not created and sight_data:
            config.sight_data = {**config.sight_data, **sight_data}
            config.save(update_fields=["sight_data"])
            # Keep cell ranking in sync with the full merged sight data.
            ranked = compute_high_los_ranking(config.sight_data)
            MapCellRankingConfig.objects.update_or_create(
                arena_map=arena_map,
                zone_size=zone_size,
                defaults={"ranked_cells": ranked},
            )

    for btype, cells in base_sights.items():
        if btype in VALID_BASE_TYPES:
            BaseSightLineConfig.objects.update_or_create(
                arena_map=arena_map,
                base_type=btype,
                zone_size=zone_size,
                defaults={"visible_cells": cells},
            )

    # Auto-compute spawn cells whenever sight lines are (re)saved.
    _update_spawn_cells_in_zone_data(arena_map, zone_size)

    return JsonResponse({"status": "ok"})


def get_spawn_cells(request, map_id):
    """Return stored red_spawn / blue_spawn lists for the confirmed zone config.

    Response: {"red_spawn": [[r,c],...], "blue_spawn": [[r,c],...]}
    Spawn data is stored once per confirmed config (not per zone_size).
    """
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    config = arena_map.latest_confirmed_config()
    red_spawn: list = []
    blue_spawn: list = []
    if config and isinstance(config.zone_data, dict):
        raw = config.zone_data
        red_spawn = raw.get("red_spawn", [])
        blue_spawn = raw.get("blue_spawn", [])

    return JsonResponse({"red_spawn": red_spawn, "blue_spawn": blue_spawn})


def _update_spawn_cells_in_zone_data(arena_map, zone_size: int) -> None:
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


def get_ranked_cells(request, map_id):
    """Return MapCellRankingConfig ranked_cells for the given zone_size."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    config = MapCellRankingConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()

    return JsonResponse({"ranked_cells": config.ranked_cells if config else []})


def get_strong_spots(request, map_id):
    """Return current HeavyStrongSpotsConfig cells for the given zone_size."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    config = HeavyStrongSpotsConfig.objects.filter(
        arena_map=arena_map, zone_size=zone_size
    ).first()

    return JsonResponse({"cells": config.cells if config else []})


@require_POST
def save_strong_spots(request, map_id):
    """Persist user-edited Heavy strong spots."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = json.loads(request.body)
        zone_size = int(body.get("zone_size", 50))
        cells = body.get("cells", [])
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"status": "error", "message": "Invalid payload"}, status=400
        )

    if not isinstance(cells, list) or not all(
        isinstance(rc, (list, tuple))
        and len(rc) == 2
        and all(isinstance(v, int) for v in rc)
        for rc in cells
    ):
        return JsonResponse(
            {"status": "error", "message": "cells must be a list of [int, int] pairs"},
            status=400,
        )

    zone_size = max(10, min(zone_size, 200))

    HeavyStrongSpotsConfig.objects.update_or_create(
        arena_map=arena_map,
        zone_size=zone_size,
        defaults={"cells": cells},
    )
    return JsonResponse({"status": "ok"})
