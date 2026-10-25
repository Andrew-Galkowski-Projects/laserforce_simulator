import json

from django import forms
from django.conf import settings
from django.contrib import messages
from django.core.exceptions import NON_FIELD_ERRORS
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
    JsonResponse,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.static import serve

from . import services
from .forms import (
    ComputeSightLinesForm,
    MapUploadForm,
    SightLinesSaveForm,
    StrongSpotsForm,
    ZoneConfigForm,
)
from .models import ArenaMap

# ANG-01c — moved to core/services.py; re-exported because callers (and
# core/tests.py) import them from here.
_get_image_local_path = services._get_image_local_path
_clear_processed_cache = services._clear_processed_cache
_update_spawn_cells_in_zone_data = services.update_spawn_cells
# ANG-06h — the default-map seeding moved to core/services.py
# (`ensure_default_maps`); re-exported for core/tests.py.
_DEFAULT_MAPS = services._DEFAULT_MAPS
_seed_defaults = services._seed_defaults


def _first_form_error(form: forms.BaseForm) -> str:
    """The first error message, in field order (non-field errors last)."""
    errors = form.errors
    for name in form.fields:
        if name in errors:
            return errors[name][0]
    if NON_FIELD_ERRORS in errors:
        return errors[NON_FIELD_ERRORS][0]
    return next(iter(errors.values()))[0]


def _json_object(body: bytes) -> dict:
    """``json.loads`` the body; a non-object is a ``TypeError`` (each view's parse error)."""
    data = json.loads(body)
    if not isinstance(data, dict):
        raise TypeError("Expected a JSON object.")
    return data


def map_list(request):
    services.ensure_default_maps()
    maps = ArenaMap.objects.order_by("created_at")
    return render(request, "maps/map_list.html", {"maps": maps})


# UXP-04 — the map list's labels for the upload form's fields.
_UPLOAD_FIELD_LABELS = {"name": "Map name", "image": "Image file"}


def upload_map(request):
    if request.method != "POST":
        return redirect("map_list")

    form = MapUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        # UXP-04 — say why, instead of silently reloading the list.
        field = next((f for f in form.fields if f in form.errors), None)
        label = _UPLOAD_FIELD_LABELS.get(field, "")
        prefix = f"{label}: " if label else ""
        messages.error(request, f"Map upload failed: {prefix}{_first_form_error(form)}")
        return redirect("map_list")

    try:
        arena_map = services.upload_map(
            form.cleaned_data["name"], form.cleaned_data["image"]
        )
    except services.InvalidMapImage:
        messages.error(
            request, "Map upload failed: the file could not be read as an image."
        )
        return redirect("map_list")

    return redirect("map_editor", map_id=arena_map.pk)


@require_POST
def delete_map(request, map_id):
    """Delete an ArenaMap and its cached processed image.

    Related zone/base/sight-line configs cascade automatically via their FK
    ``on_delete=CASCADE``. ``GameRound.arena_map`` is ``SET_NULL``, so any
    persisted rounds keep their results but lose the (now-deleted) map link.
    ANG-01c — refused (nothing deleted) while a non-completed Season uses it.
    """
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    name = arena_map.name
    try:
        services.delete_map(arena_map)
    except services.MapInUseError:
        messages.error(
            request,
            f"Cannot delete map “{name}”: it is used by a Season that is not completed.",
        )
        return redirect("map_list")
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
    return JsonResponse(services.detect_zones_with_overlay(arena_map, cell_size))


def processed_image(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    processed_path = services.processed_image_path(arena_map)
    return FileResponse(open(processed_path, "rb"), content_type="image/png")


@require_POST
def save_zone_config(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = _json_object(request.body)
        zone_size = int(body.get("zone_size", 50))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"status": "error", "message": "Invalid zone_size"}, status=400
        )

    zone_size = max(10, min(zone_size, 200))

    form = ZoneConfigForm(
        data={
            "zone_size": zone_size,
            "zones": body.get("zones"),
            "wall_meta": body.get("wall_meta"),
            "elevation": body.get("elevation"),
            "red_spawn": body.get("red_spawn"),
            "blue_spawn": body.get("blue_spawn"),
            "bases": body.get("bases", []),
        }
    )
    if not form.is_valid():
        return JsonResponse(
            {"status": "error", "message": _first_form_error(form)}, status=400
        )

    services.save_zone_config(arena_map, **form.cleaned_data)
    return JsonResponse({"status": "ok"})


def get_sight_lines(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    return JsonResponse(services.get_sight_lines(arena_map, zone_size))


@require_POST
def compute_sight_lines_view(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = _json_object(request.body)
        zone_size = int(body.get("zone_size", 50))
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"error": "Invalid zone_size"}, status=400)

    zone_size = max(10, min(zone_size, 200))

    form = ComputeSightLinesForm(
        data={
            "zone_size": zone_size,
            "red_spawn": body.get("red_spawn"),
            "blue_spawn": body.get("blue_spawn"),
        }
    )
    if not form.is_valid():
        return JsonResponse({"error": _first_form_error(form)}, status=400)

    # Unsaved spawn edits are written to the confirmed config with the
    # user-edited lock so the spawn auto-compute won't overwrite them.
    return JsonResponse(
        services.compute_and_store_sight_lines(
            arena_map,
            form.cleaned_data["zone_size"],
            red_spawn=form.cleaned_data["red_spawn"],
            blue_spawn=form.cleaned_data["blue_spawn"],
        )
    )


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

    return JsonResponse(services.compute_single_cell(arena_map, zone_size, r, c))


@require_POST
def save_sight_lines(request, map_id):
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = _json_object(request.body)
        zone_size = int(body.get("zone_size", 50))
        sight_data = body.get("sight_data", {})
        base_sights = body.get("base_sights", {})
        replace = body.get("replace", True)
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse({"status": "error"}, status=400)

    zone_size = max(10, min(zone_size, 200))

    form = SightLinesSaveForm(
        data={
            "zone_size": zone_size,
            "sight_data": sight_data,
            "base_sights": base_sights,
        }
    )
    if not form.is_valid():
        return JsonResponse(
            {"status": "error", "message": _first_form_error(form)}, status=400
        )

    services.save_sight_lines(
        arena_map,
        form.cleaned_data["zone_size"],
        sight_data=form.cleaned_data["sight_data"],
        base_sights=form.cleaned_data["base_sights"],
        mode="replace" if replace else "merge",
    )
    return JsonResponse({"status": "ok"})


def get_spawn_cells(request, map_id):
    """Return stored red_spawn / blue_spawn lists for the confirmed zone config.

    Response: {"red_spawn": [[r,c],...], "blue_spawn": [[r,c],...]}
    Spawn data is stored once per confirmed config (not per zone_size).
    """
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    return JsonResponse(services.get_spawn_cells(arena_map))


def get_ranked_cells(request, map_id):
    """Return MapCellRankingConfig ranked_cells for the given zone_size."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    return JsonResponse(services.get_ranked_cells(arena_map, zone_size))


def get_strong_spots(request, map_id):
    """Return current HeavyStrongSpotsConfig cells for the given zone_size."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        zone_size = int(request.GET.get("zone_size", 50))
    except (ValueError, TypeError):
        zone_size = 50
    zone_size = max(10, min(zone_size, 200))

    return JsonResponse(services.get_strong_spots(arena_map, zone_size))


@require_POST
def save_strong_spots(request, map_id):
    """Persist user-edited Heavy strong spots."""
    arena_map = get_object_or_404(ArenaMap, pk=map_id)
    try:
        body = _json_object(request.body)
        zone_size = int(body.get("zone_size", 50))
        cells = body.get("cells", [])
    except (ValueError, TypeError, json.JSONDecodeError):
        return JsonResponse(
            {"status": "error", "message": "Invalid payload"}, status=400
        )

    zone_size = max(10, min(zone_size, 200))

    form = StrongSpotsForm(data={"zone_size": zone_size, "cells": cells})
    if not form.is_valid():
        return JsonResponse(
            {"status": "error", "message": _first_form_error(form)}, status=400
        )

    services.save_strong_spots(
        arena_map, form.cleaned_data["zone_size"], form.cleaned_data["cells"]
    )
    return JsonResponse({"status": "ok"})


def map_heatmap_data(request, map_id: int):
    """RES-04: aggregate cell occupancy across the requester's GameRounds for a map.

    Required query param ``zone_size`` (int). Optional ``team_color`` filter
    (``"red"`` or ``"blue"``). Returns
    ``{"cell_occupancy": {...}, "zone_size": int, "rows": int, "cols": int,
    "round_count": int}``. ANG-01c — rounds are scoped by
    ``owned_game_round_q(request.user)``.
    """
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])

    arena_map = get_object_or_404(ArenaMap, pk=map_id)

    raw_zone_size = request.GET.get("zone_size")
    if raw_zone_size is None:
        return HttpResponseBadRequest("zone_size required")
    try:
        zone_size = int(raw_zone_size)
    except (TypeError, ValueError):
        return HttpResponseBadRequest("zone_size required")

    team_color = request.GET.get("team_color")
    if team_color is not None and team_color != "":
        if team_color not in ("red", "blue"):
            return HttpResponseBadRequest("invalid team_color")
    else:
        team_color = None

    return JsonResponse(
        services.aggregate_map_heatmap(
            arena_map, zone_size, team_color, getattr(request, "user", None)
        )
    )


def landing(request) -> HttpResponse:
    """LG-01a — mode-picker landing page + in-progress League cards.

    Lazy-imports ``League`` inside the function body to mirror the
    ``map_heatmap_data`` precedent and avoid an apps-loading cycle.
    """
    from accounts.permissions import owned_queryset
    from matches.models import League

    # UX-01 — Manager-scoped: this Account's active Leagues plus every
    # **Unmanaged** one. Without this the card list leaked another Account's
    # League name and Season status, even though `/leagues/<id>/` itself
    # correctly 404s (ADR-0038).
    in_progress_leagues = list(
        owned_queryset(
            League.objects.filter(state="active"), getattr(request, "user", None)
        ).order_by("-id")
    )
    return render(
        request,
        "core/landing.html",
        {"in_progress_leagues": in_progress_leagues},
    )


def serve_media(request, path: str) -> HttpResponse:
    """UXP-02 — serve an uploaded file from ``MEDIA_ROOT`` under ``DEBUG`` (dev,
    as ``static()`` did) or when ``SERVE_MEDIA`` is on (self-hosting without
    R2); 404 otherwise. ``serve`` rejects paths that escape the document root."""
    if not (settings.DEBUG or settings.SERVE_MEDIA):
        raise Http404()
    return serve(request, path, document_root=settings.MEDIA_ROOT)
