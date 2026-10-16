# ANG-01c seam contract — Map editor API

Branch `ang-01c-map-editor-api` (stacked on ANG-01b). Binds the code, tests and docs agents.
Conventions: ADR-0039 + `matches/CLAUDE.md` "API conventions". **Views raise, never build a 4xx
`Response`.** Maps are **shared reference data** (ADR-0038): never ownership-filtered, no `manager`,
and the 14 `get_object_or_404(ArenaMap, ...)` sites in `core/views.py` stay as they are. No
migration, no simulation change, no Score Calibration re-baseline. All paths are relative to
`laserforce_simulator/` (the Django project).

## 0. Facts verified in code (they drive several decisions)

- `_resolve_fixture_map` (matches/tasks.py) reads **`Season.starting_map_pool_ids_json`**, so the
  in-use guard checks it as well as the live `map_pool` M2M (§3).
- `GameRound.arena_map` is `SET_NULL` (matches/models.py:210). Historical rounds **do not** block a
  delete, and they lose the map link as they do today.
- Django `forms.JSONField` treats `[]` / `{}` as *empty* (`EMPTY_VALUES`) and `json.loads` any
  string. Both are wrong here: `red_spawn: []` means "explicitly erased" (`spawn_user_edited`
  tri-state), and the payloads are already-decoded JSON. ⇒ We use a `ParsedJSONField` subclass (§2).
- JSONField `__contains` is **unsupported on SQLite** (NotSupportedError). ⇒ The JSON id-list
  membership check in `map_in_use` runs in Python.
- `compute_single_cell_visibility` already returns `[]` when the origin is out of the grid or
  impassable. ⇒ The API has no grid-bounds check, only `r, c >= 0`.
- The compute and single-cell views resolve zone data from the **latest config of any confirmed
  state** for that `zone_size` (`arena_map.zone_configs.filter(zone_size=...)`), falling back to
  `detect_zones`. Both branches give the same result ⇒ one helper (§4 `_resolve_zone_data`).
- `save_sight_lines(replace=False)` on a **missing** `SightLineConfig` creates it with `{}` and
  **discards** the batch. This is a bug, and §4 fixes it (flagged in §9).
- `wall_meta` is **not** carried forward by `save_zone_config` (`body.get("wall_meta") or {}`).
  `elevation` and the spawns **are** carried forward. A missing `bases` key **deletes all bases**.
  All of this is kept unchanged.
- The test heatmap rounds (`matches/tests/test_heatmap.py::_make_round`) are standalone
  `GameRound`s with no manager ⇒ **Unmanaged** ⇒ still visible after the heatmap scoping (§7).
- DRF 3.17.1: `@x.mapping.put` adds a method to the **same route**. The router builds initkwargs from
  the *original* action's kwargs, so `parser_classes` on the GET decorator also covers PUT/PATCH.
  `self.action` is the mapped function name.
- `settings.MEDIA_ROOT` is a `Path` and the code does `MEDIA_ROOT / "maps"`, so tests must override it
  with a `Path`, not a `str`.
- pytest `python_files = test_*.py *_test.py *tests.py` ⇒ `core/test_*.py` siblings are collected.

## 1. Module map

| Module | Status | Contents |
|---|---|---|
| `core/forms.py` | **new** | `ParsedJSONField`, `MapUploadForm`, `ZoneConfigForm`, `ComputeSightLinesForm`, `SightLinesSaveForm`, `StrongSpotsForm` |
| `core/api_forms.py` | **new** | read-param Forms: `ZoneSizeParamsForm`, `SingleCellParamsForm`, `HeatmapParamsForm` (ANG-01b `matches/api_forms.py` precedent) |
| `core/services.py` | **new** | every map read/write/compute, `map_in_use`, `delete_map`, `upload_map`, `aggregate_map_heatmap`; moved private helpers |
| `core/api_views.py` | changed | `ArenaMapViewSet` gets create + destroy + 10 detail actions; `MapInUse` exception |
| `core/views.py` | changed | HTML views delegate to `services` (called as `services.<name>`, because the names collide with the view names); re-exports |
| `core/serializers.py`, `core/filters.py`, `api_urls.py`, `core/urls.py`, `core/models.py`, template | **unchanged** | the router already registers `maps` / `arenamap` |

`core/services.py` must **not** import `core.views` (`views` imports `services`). It imports
`matches.models` / `accounts.permissions` **lazily inside functions** (the core→matches cycle,
mirroring `map_heatmap_data`).

**Moved into `core/services.py`** (bodies verbatim, names kept): `_get_image_local_path`,
`_clear_processed_cache`. `core/views.py` re-exports both, because `core/tests.py` imports
`_get_image_local_path` from `.views`. `_update_spawn_cells_in_zone_data` moves and becomes public
as `update_spawn_cells` (§4), and `views` keeps `_update_spawn_cells_in_zone_data =
services.update_spawn_cells`. `_seed_defaults` / `_DEFAULT_MAPS` stay in views.

## 2. `core/forms.py` (new) — write Forms (strict; HTML pre-normalises, §7)

```python
class ParsedJSONField(forms.JSONField):
    """A value that is ALREADY decoded JSON (DRF JSONParser / the HTML view's json.loads).
    to_python returns the value unchanged (a str is a str, never json.loads'd);
    empty_values = [None] so [] and {} survive. required=False everywhere below."""
```

Every validator returns the cleaned value or raises `ValidationError(<msg>, code="invalid")`. "int"
always means `isinstance(v, int) and not isinstance(v, bool)`, and "number" means int-or-float, also
excluding bool. Messages are pinned exactly as written below.

| Form | Field | Rule | absent / null ⇒ cleaned | message (code `invalid`) |
|---|---|---|---|---|
| `MapUploadForm` | `name` | `CharField(max_length=100)` (strip) | required | Django default |
| | `image` | `forms.ImageField()` | required | Django `invalid_image` ("Upload a valid image. …") |
| `ZoneConfigForm` | `zone_size` | `IntegerField(min_value=10, max_value=200)` | **required** | Django defaults |
| | `zones` | non-empty list of non-empty lists, all the same length, every value an int in `{0,1,2,3,4,5}`. **`[]` ⇒ `None`** (auto-detect, HTML parity) | `None` | `zones must be a non-empty list of equal-length rows of cell values 0-5.` |
| | `wall_meta` | dict; each key matches `^\d+,\d+$`; each value a dict whose keys are a subset of `{"facing","height"}`; `facing` ∈ `{"N","S","E","W"}`; `height` a number `>= 0` | `{}` | `wall_meta must map "r,c" keys to {"facing": N/S/E/W, "height": number >= 0}.` |
| | `elevation` | list of rows ⇒ else msg A; each row a list ⇒ else msg B; each value a number with `0.0 <= v <= 10.0` ⇒ else msg C (first offending value) | `None` | A `Invalid elevation: must be a list of rows` · B `Invalid elevation: each row must be a list` · C `f"Elevation value out of range [0.0, 10.0]: {val}"` (B and C byte-identical to today) |
| | `red_spawn`, `blue_spawn` | list of `[int, int]` (len-2 list) pairs; **`[]` kept as `[]`** | `None` (= "not sent") | `f"{field} must be a list of [int, int] pairs"` |
| | `bases` | list of dicts with exactly `type` ∈ `VALID_BASE_TYPES`, `x_px` int, `y_px` int (extra keys ⇒ invalid); then no `type` may repeat | `[]` | `bases must be a list of {"type", "x_px", "y_px"} objects with a valid base type and integer pixel coordinates.` · repeat: `bases must not repeat a base type.` |
| `ComputeSightLinesForm` | `zone_size` | as above | required | |
| | `red_spawn`, `blue_spawn` | as above | `None` | as above |
| `SightLinesSaveForm` | `zone_size` | as above | required | |
| | `sight_data` | dict; keys and every list item match `^\d+,\d+$`; values are lists of str | `{}` | `sight_data must map "r,c" keys to lists of "r,c" strings.` |
| | `base_sights` | dict; keys ∈ `VALID_BASE_TYPES`; values are lists of `[int, int]` | `{}` | `base_sights must map base types to lists of [int, int] pairs.` |
| `StrongSpotsForm` | `zone_size` | as above | required | |
| | `cells` | list of `[int, int]` (list or tuple, len 2) | **required** (`None` ⇒ `required`) | `cells must be a list of [int, int] pairs` (byte-identical to today) |

Field order = the table order, which also sets the order of errors in the envelope and the HTML
"first error". There is no cross-field shape check (zones vs elevation): it is out of scope and noted
in §9.

## 3. `map_in_use` — the delete guard (exact)

```python
def map_in_use(arena_map: ArenaMap) -> bool:
    from matches.models import Conference, Season           # lazy (cycle)
    live = Season.objects.exclude(state="completed")         # draft + active
    if live.filter(map_pool=arena_map).exists():             # 1. Season.map_pool (M2M, live)
        return True
    pk = arena_map.pk
    for pool_snap, rot, rot_snap in live.values_list(
        "starting_map_pool_ids_json",                        # 2. Season pool snapshot (resolver reads this)
        "map_rotation_ids_json",                             # 3. Season rotation (live)
        "starting_map_rotation_ids_json",                    # 4. Season rotation snapshot
    ):
        if any(isinstance(ids, list) and pk in ids for ids in (pool_snap, rot, rot_snap)):
            return True
    for rot, rot_snap in Conference.objects.exclude(season__state="completed").values_list(
        "map_rotation_ids_json",                             # 5. Conference rotation (live)
        "starting_map_rotation_ids_json",                    # 6. Conference rotation snapshot
    ):
        if any(isinstance(ids, list) and pk in ids for ids in (rot, rot_snap)):
            return True
    return False
```

The membership check runs in Python because SQLite has no JSONField `contains`; this is correct on
both backends, and the loop is bounded by the number of non-completed Seasons/Conferences.
`GameRound.arena_map` and `Season.map_mode` alone do **not** count. A `completed` Season never
blocks, even if its lists name the map.

## 4. `core/services.py` (new) — signatures and return shapes

Every function takes the **`ArenaMap` instance** (the callers do the lookup). The write services are
`@transaction.atomic`. Dict key **insertion order is pinned** (`JsonResponse` serialises in insertion
order, so the HTML bodies stay byte-identical).

```python
class MapInUseError(Exception): ...          # raised by delete_map
class InvalidMapImage(Exception): ...        # raised by upload_map when PIL can't read the stored file

def upload_map(name: str, image_file: UploadedFile) -> ArenaMap
    # Today's upload_map body: create ArenaMap(name, image) -> reopen through storage -> PIL size ->
    # save img_width/img_height; on (UnidentifiedImageError, OSError): image.delete(save=False),
    # row.delete(), raise InvalidMapImage. Then _clear_processed_cache(pk). Returns the row.

def delete_map(arena_map: ArenaMap) -> None
    # map_in_use -> raise MapInUseError (nothing touched). Else _clear_processed_cache(pk),
    # arena_map.image.delete(save=False), arena_map.delete()  (configs CASCADE; GameRound SET_NULL).

def map_in_use(arena_map: ArenaMap) -> bool                        # §3

def processed_image_path(arena_map: ArenaMap) -> Path
    # MEDIA_ROOT/maps/processed_<pk>.png; builds it with create_processed_image if missing (today's code).

def _resolve_zone_data(arena_map: ArenaMap, zone_size: int) -> dict
    # latest zone_configs.filter(zone_size=zone_size).order_by("-created_at").first() (ANY confirmed state);
    # dict -> as is; list -> {"zones": list}; none -> detect_zones(_get_image_local_path(image), zone_size).

def detect_zones_with_overlay(arena_map: ArenaMap, zone_size: int) -> dict
    # = today's process_zones body after the clamp: detect_zones(...) dict (keys rows, cols, cell_size,
    # img_width, img_height, zones, blocked_edges, blocked_edges_grid), then if the latest CONFIRMED config
    # has zone_size == zone_size and dict zone_data: overwrite "zones", and set "wall_meta" / "elevation"
    # when present (same mutation order as today).

def get_zone_config(arena_map: ArenaMap) -> dict
    # From latest_confirmed_config() (legacy list zone_data -> zones=list, the rest default):
    # {"zone_size": int | None, "zones": list | None, "wall_meta": dict ({}), "elevation": list | None,
    #  "red_spawn": list ([]), "blue_spawn": list ([]), "spawn_user_edited": bool,
    #  "bases": [{"type", "x_px", "y_px"}, ...] sorted by type}   # bases from MapBaseConfig, independent of config
    # The shape round-trips: the PUT body may be this dict minus spawn_user_edited.

def save_zone_config(
    arena_map: ArenaMap, *, zone_size: int, zones: list | None, wall_meta: dict,
    elevation: list | None, red_spawn: list | None, blue_spawn: list | None, bases: list[dict],
) -> MapZoneConfig
    # Today's save_zone_config body, verbatim semantics:
    #  zones None -> detect_zones (zones + blocked_edges); else blocked_edges = {}.
    #  elevation None -> carry the existing confirmed config's elevation (if any).
    #  red/blue None -> carry existing; client_sent_spawn = red_spawn is not None or blue_spawn is not None;
    #  spawn_user_edited = (bool(red)|bool(blue)) if client_sent_spawn else existing flag.
    #  Un-confirm all confirmed configs -> create the new confirmed config with payload keys
    #  zones, blocked_edges, [wall_meta if truthy], [elevation if not None], [red_spawn if truthy],
    #  [blue_spawn if truthy], [spawn_user_edited: True if set]  (this order).
    #  Delete all MapBaseConfig -> create one per base (already validated, so no silent skip).
    #  _clear_processed_cache(pk). Returns the new MapZoneConfig.

def get_sight_lines(arena_map: ArenaMap, zone_size: int) -> dict
    # {"zone_size": int, "sight_data": dict | None, "base_sights": {btype: list for btype in VALID_BASE_TYPES}}

def compute_and_store_sight_lines(
    arena_map: ArenaMap, zone_size: int, *, red_spawn: list | None = None, blue_spawn: list | None = None,
) -> dict
    # compute_sight_lines(_resolve_zone_data(...), use_quadtree=True) -> upsert SightLineConfig ->
    # ranked = compute_high_los_ranking -> upsert MapCellRankingConfig(ranked) and
    # HeavyStrongSpotsConfig(ranked[:max(1, len(ranked)//4)]) -> if either spawn is not None and a
    # confirmed config exists: write the given side(s) + spawn_user_edited = bool(red)|bool(blue)
    # (today's semantics) -> update_spawn_cells(arena_map, zone_size).
    # Returns {"sight_data": dict, "zone_size": int}   # this order (the HTML body)

def compute_single_cell(arena_map: ArenaMap, zone_size: int, r: int, c: int) -> dict
    # {"visible_cells": compute_single_cell_visibility(r, c, _resolve_zone_data(...))}  ([] off-grid)

def save_sight_lines(
    arena_map: ArenaMap, zone_size: int, *, sight_data: dict, base_sights: dict,
    mode: Literal["replace", "merge"],
) -> None
    # replace: upsert SightLineConfig(sight_data); if sight_data: rank -> upsert MapCellRankingConfig
    #          + HeavyStrongSpotsConfig(top quarter)   (today).
    # merge:   cfg = get_or_create(defaults={"sight_data": {}}); if sight_data:
    #          cfg.sight_data = {**cfg.sight_data, **sight_data}; save; rank(merged) -> upsert
    #          MapCellRankingConfig only (strong spots NOT reseeded).  ** FIX: also when cfg was just
    #          created (today that discards the batch). **
    # both:    for btype, cells in base_sights.items(): upsert BaseSightLineConfig (other types untouched);
    #          then update_spawn_cells(arena_map, zone_size).

def update_spawn_cells(arena_map: ArenaMap, zone_size: int) -> None   # moved _update_spawn_cells_in_zone_data, verbatim
def get_ranked_cells(arena_map: ArenaMap, zone_size: int) -> dict     # {"ranked_cells": list}  ([] if none)
def get_strong_spots(arena_map: ArenaMap, zone_size: int) -> dict     # {"cells": list}         ([] if none)
def save_strong_spots(arena_map: ArenaMap, zone_size: int, cells: list) -> None   # upsert HeavyStrongSpotsConfig
def get_spawn_cells(arena_map: ArenaMap) -> dict                      # {"red_spawn": list, "blue_spawn": list}

def aggregate_map_heatmap(
    arena_map: ArenaMap, zone_size: int, team_color: str | None, user: "AbstractBaseUser | None",
) -> dict
    # Today's map_heatmap_data aggregation body, except that the rounds queryset gains
    # owned_game_round_q(user):  GameRound.objects.filter(owned_game_round_q(user), arena_map_id=arena_map.pk,
    #   zone_size=zone_size, cell_occupancy_json__isnull=False)
    # round_count counts the SCOPED corpus. Returns
    # {"cell_occupancy": dict, "zone_size": int, "rows": int, "cols": int, "round_count": int}  (this order)
```

## 5. `core/api_forms.py` (new) — API read params

These are bound as `Form(data=plain_form_data(Form, request.query_params))` and then
`validate_form_or_raise`.

| Form | Field | Rule |
|---|---|---|
| `ZoneSizeParamsForm` | `zone_size` | `IntegerField(min_value=10, max_value=200)`, required |
| `SingleCellParamsForm` | `zone_size` | as above |
| | `r`, `c` | `IntegerField(min_value=0)`, required |
| `HeatmapParamsForm` | `zone_size` | as above |
| | `team_color` | `ChoiceField(choices=[("red","red"),("blue","blue")], required=False)`; `clean_team_color` returns `None` for `""` |

Codes are Django's own: `required` / `invalid` / `min_value` / `max_value` / `invalid_choice`.

## 6. `ArenaMapViewSet` (core/api_views.py)

```python
class MapInUse(APIException):
    status_code = 409
    default_code = "map_in_use"
    default_detail = "This map is used by a Season that is not completed."

@method_decorator(login_not_required, name="dispatch")
class ArenaMapViewSet(ListOnlyFilterMixin, mixins.ListModelMixin, mixins.RetrieveModelMixin,
                      mixins.DestroyModelMixin, viewsets.GenericViewSet):
    parser_classes = [MultiPartParser, FormParser]      # create; JSON actions override per action
    # queryset / serializer / filterset / ordering / get_queryset: unchanged (NOT ownership-filtered)
    def create(self, request: Request) -> Response
    def perform_destroy(self, instance: ArenaMap) -> None   # services.delete_map; MapInUseError -> raise MapInUse
```

There is no `update` / `partial_update`, so PUT/PATCH on `/api/maps/<pk>/` ⇒ **405
`method_not_allowed`** (Allow: GET, DELETE, HEAD, OPTIONS). Every action is `detail=True` and
resolves the map with `self.get_object()` (unknown pk ⇒ **404 `not_found`**). Anonymous ⇒ 403
`not_authenticated`. Any authenticated Account may write any map (HTML parity).

| # | Method + path | handler (`@action` / mapping) | `url_path` | route name | parsers | OK |
|---|---|---|---|---|---|---|
| 1 | `POST /api/maps/` | `create` | — | `arenamap-list` | multipart, form | 201 |
| 2 | `DELETE /api/maps/<pk>/` | `destroy` | — | `arenamap-detail` | — | 204 |
| 3 | `GET …/processed-image/` | `processed_image` | `processed-image` | `arenamap-processed-image` | — | 200 png |
| 4 | `GET …/zones/` | `zones` | `zones` | `arenamap-zones` | — | 200 |
| 5 | `GET …/zone-config/` | `zone_config` | `zone-config` | `arenamap-zone-config` | JSON | 200 |
| 6 | `PUT …/zone-config/` | `@zone_config.mapping.put` → `replace_zone_config` | 〃 | 〃 | JSON | 200 |
| 7 | `GET …/sight-lines/` | `sight_lines` | `sight-lines` | `arenamap-sight-lines` | JSON | 200 |
| 8 | `PUT …/sight-lines/` | `@sight_lines.mapping.put` → `replace_sight_lines` | 〃 | 〃 | JSON | 200 |
| 9 | `PATCH …/sight-lines/` | `@sight_lines.mapping.patch` → `merge_sight_lines` | 〃 | 〃 | JSON | 200 |
| 10 | `POST …/sight-lines/compute/` | `compute_sight_lines` | `sight-lines/compute` | `arenamap-sight-lines-compute` | JSON | 200 |
| 11 | `GET …/sight-lines/cell/` | `sight_line_cell` | `sight-lines/cell` | `arenamap-sight-lines-cell` | — | 200 |
| 12 | `GET …/ranked-cells/` | `ranked_cells` | `ranked-cells` | `arenamap-ranked-cells` | — | 200 |
| 13 | `GET …/strong-spots/` | `strong_spots` | `strong-spots` | `arenamap-strong-spots` | JSON | 200 |
| 14 | `PUT …/strong-spots/` | `@strong_spots.mapping.put` → `replace_strong_spots` | 〃 | 〃 | JSON | 200 |
| 15 | `GET …/spawn-cells/` | `spawn_cells` | `spawn-cells` | `arenamap-spawn-cells` | — | 200 |
| 16 | `GET …/heatmap/` | `heatmap` | `heatmap` | `arenamap-heatmap` | — | 200 |

Each `@action` passes explicit `detail=True, methods=["get"|"post"], url_path=, url_name=` (`url_name`
= `url_path` with `/` → `-`). "JSON" means `parser_classes=[JSONParser]` on the original action. That
applies to its mapped methods too, so a multipart PUT ⇒ **415 `unsupported_media_type`**. The write
handlers bind `Form(data=plain_form_data(Form, request.data))`, which turns a non-object JSON body
into 400 `parse_error`.

### Per-endpoint behaviour and `data`

1. **create** — `MapUploadForm(data=request.data, files=request.FILES)` → `validate_form_or_raise`
   → `services.upload_map(name, image)`. If that raises `InvalidMapImage`, raise
   `EnvelopeValidationError([{"code": "invalid_image", "message": MapUploadForm.base_fields["image"].error_messages["invalid_image"], "field": "image"}])`.
   Then re-fetch through `self.get_queryset().get(pk=…)` (to get the annotation) →
   `ArenaMapSerializer(context=…)`. `data` = the 8 serializer fields (`has_confirmed_config` false). A
   JSON body ⇒ **415**.
2. **destroy** — `data` none (204, empty body). In use ⇒ **409 `map_in_use`**, and the map, configs
   and files are untouched.
3. **processed-image** — `FileResponse(open(services.processed_image_path(m), "rb"), content_type="image/png")`.
   This is a raw response and **not enveloped** (the ANG-01b report precedent). As there, `Accept:
   image/png` gets 406 from DRF negotiation, so clients should send `*/*`.
4. **zones** — `ZoneSizeParamsForm` → `data = services.detect_zones_with_overlay(m, zone_size)`
   verbatim (keys `rows, cols, cell_size, img_width, img_height, zones, blocked_edges,
   blocked_edges_grid` [+ `wall_meta`, `elevation`]).
5. **zone-config GET** — `data = services.get_zone_config(m)` (keys from §4).
6. **zone-config PUT** — `ZoneConfigForm` → `services.save_zone_config(m, **cleaned)` → `data =
   services.get_zone_config(m)`. This is full-replace semantics with the HTML carry-forward rules
   kept:
   - absent `zones` ⇒ **auto-detect from the image** (the fallback is kept, because a fresh map has
     no grid until it is detected);
   - absent `bases` ⇒ **all bases deleted**;
   - absent `wall_meta` ⇒ dropped;
   - absent `elevation` / spawns ⇒ carried forward;
   - spawns sent (even `[]`) ⇒ the `spawn_user_edited` rule.
7. **sight-lines GET** — `ZoneSizeParamsForm` → `data = services.get_sight_lines(m, zone_size)`
   (`zone_size, sight_data, base_sights`).
8. **sight-lines PUT** — `SightLinesSaveForm` → `save_sight_lines(..., mode="replace")` → `data =
   get_sight_lines(m, zone_size)`.
9. **sight-lines PATCH** — same, with `mode="merge"`. Batch clients use PUT for the first chunk and
   PATCH for the rest (the editor's batching pattern).
10. **compute** — `ComputeSightLinesForm` → `data = services.compute_and_store_sight_lines(m,
    zone_size, red_spawn=, blue_spawn=)` = `{sight_data, zone_size}`. Runs synchronously.
11. **cell** — `SingleCellParamsForm` → `data = services.compute_single_cell(...)` =
    `{visible_cells}`. Off-grid or impassable ⇒ `[]` (200, not 400).
12. **ranked-cells** — `ZoneSizeParamsForm` → `{ranked_cells}`.
13. **strong-spots GET** — `ZoneSizeParamsForm` → `{cells}`.
14. **strong-spots PUT** — `StrongSpotsForm` → `save_strong_spots` → `data = get_strong_spots(m,
    zone_size)` = `{cells}`.
15. **spawn-cells** — no params → `{red_spawn, blue_spawn}`.
16. **heatmap** — `HeatmapParamsForm` → `data = services.aggregate_map_heatmap(m, zone_size,
    team_color, request.user)` = `{cell_occupancy, zone_size, rows, cols, round_count}`, scoped to the
    requester's visible rounds.

Read `data` equals the HTML JSON byte for byte (one service). Write endpoints return the **saved
state** (the matching GET's `data`), never `{"status": "ok"}`.

### Error table

| Case | Status | code | field |
|---|---|---|---|
| unknown map pk (any detail route) | 404 | `not_found` | null |
| anonymous | 403 | `not_authenticated` | null |
| PUT/PATCH `/api/maps/<pk>/`; POST on a GET-only action; DELETE on an action | 405 | `method_not_allowed` | null |
| (1) JSON body · (6/8/9/10/14) multipart body | 415 | `unsupported_media_type` | null |
| non-object JSON body | 400 | `parse_error` | null |
| (1) missing name / image | 400 | `required` | `name` / `image` |
| (1) name > 100 | 400 | `max_length` | `name` |
| (1) non-image / corrupt file | 400 | `invalid_image` | `image` |
| missing `zone_size` (every endpoint but 1, 2, 3, 5, 15) | 400 | `required` | `zone_size` |
| `zone_size` non-int / <10 / >200 | 400 | `invalid` / `min_value` / `max_value` | `zone_size` |
| (11) `r` / `c` missing / non-int / negative | 400 | `required` / `invalid` / `min_value` | `r` / `c` |
| (16) bad `team_color` | 400 | `invalid_choice` | `team_color` |
| malformed zones / wall_meta / elevation / spawn / bases / sight_data / base_sights / cells | 400 | `invalid` | the field (message per §2) |
| (14) `cells` absent or null | 400 | `required` | `cells` |
| (2) map in use (§3) | 409 | `map_in_use` | null |

## 7. HTML views (core/views.py) — what changes

Rule: the existing lenient parse (`json.loads`, `int(zone_size)` with each view's **existing** parse
error body, then the clamp `max(10, min(zs, 200))` or the default 50 on GETs) runs **first and
unchanged**. Only then is the strict Form bound, with the **clamped** `zone_size` and the HTML
defaults injected. Valid editor payloads ⇒ the same DB writes and **byte-identical** success bodies.
New: a JSON body that is not an object (today an `AttributeError` 500) ⇒ that view's existing
parse-error body, 400. Helper: `_first_form_error(form) -> str`, the first message in field order.

| View | Binds / calls | Valid input | Malformed input (new) |
|---|---|---|---|
| `upload_map` | `MapUploadForm(request.POST, request.FILES)` → `services.upload_map` | redirect `map_editor` (same) | invalid form or `InvalidMapImage` ⇒ redirect `map_list` (same as today for missing / corrupt; now also name > 100) |
| `delete_map` | `services.delete_map` | success message + redirect `map_list` (same) | **in use** ⇒ `messages.error(request, f"Cannot delete map “{name}”: it is used by a Season that is not completed.")` + redirect `map_list`; nothing deleted |
| `process_zones` | `services.detect_zones_with_overlay` | byte-identical | — (lenient GET kept) |
| `processed_image` | `services.processed_image_path` | identical bytes | — |
| `save_zone_config` | `ZoneConfigForm` (zone_size = clamped; `bases` default `[]`) → `services.save_zone_config` | `{"status": "ok"}` | `{"status": "error", "message": _first_form_error(form)}` 400. Elevation messages B/C are unchanged. **Newly rejected** (today saved or ignored): non-list `zones` (today: silent auto-detect), bad zone values / ragged rows, bad `wall_meta`, non-list `elevation` (today ignored), bad spawn pairs, unknown or duplicate base types (today skipped / IntegrityError 500), non-int pixels |
| `get_sight_lines` | `services.get_sight_lines` | byte-identical | — |
| `compute_sight_lines_view` | `ComputeSightLinesForm` → `services.compute_and_store_sight_lines` | `{"sight_data", "zone_size"}` byte-identical | `{"error": _first_form_error(form)}` 400 (the view's existing error key) |
| `compute_single_cell_sight` | `services.compute_single_cell` | byte-identical | — (existing `{"error": "Invalid parameters"}`) |
| `save_sight_lines` | `SightLinesSaveForm` (`sight_data` / `base_sights` default `{}`); `mode = "replace" if body.get("replace", True) else "merge"` → `services.save_sight_lines` | `{"status": "ok"}` | `{"status": "error", "message": …}` 400 (today `{"status": "error"}`, with no message, only for a bad zone_size). Malformed keys (today a 500 inside the ranking) and unknown base types (today skipped) are now rejected |
| `get_spawn_cells`, `get_ranked_cells`, `get_strong_spots` | services | byte-identical | — |
| `save_strong_spots` | `StrongSpotsForm` (`cells` default `[]`) → `services.save_strong_spots` | `{"status": "ok"}` | `{"status": "error", "message": …}` 400. The cells message is byte-identical to today. Bool pairs are now rejected |
| `map_heatmap_data` | lenient parse **unchanged** (`"zone_size required"` / `"invalid team_color"` plain-text 400s, 405, any-int zone_size) → `services.aggregate_map_heatmap(..., request.user)` | same keys | **Behaviour change:** rounds scoped by `owned_game_round_q(request.user)` (own + Unmanaged + own/Unmanaged Match/League); `round_count` = the scoped count |

`map_list`, `map_editor` and `landing` are unchanged. The 14 `get_object_or_404(ArenaMap, …)` lines
stay in their views (UX-01 contract).

## 8. Test boundary

The fixture is a tiny real PNG built in memory: `PILImage.new("RGB", (200, 200), "white")` with a
black border/bar drawn on it. It is uploaded through the API or HTML under
`override_settings(MEDIA_ROOT=Path(tempfile.mkdtemp()))` (a `Path`), with cleanup in `tearDown` /
`addCleanup(shutil.rmtree, …)`. OpenCV runs for real (zone_size 50 ⇒ a 4×4 grid). Hand-built maps
with `zone_data` rows still work for the service tests that don't touch the image. The cross-Account
fixture is a second `User` plus `APIClient.force_login`. Assertions go on status, the envelope `data`
key sets and `errors[*].{code,field,message}`.

| File (new) | Asserts |
|---|---|
| `core/test_map_forms.py` (SimpleTestCase) | Per §2 field: happy path; `[]` spawns kept as `[]` vs absent ⇒ `None`; `zones: []` ⇒ `None`; ragged / out-of-set / bool values rejected; wall_meta bad key / facing / height / extra key; elevation messages A/B/C exact (C with the offending value); duplicate base type; bad base type; `sight_data` bad key / item; `base_sights` unknown type; `cells` exact message, and absent ⇒ `required`; `zone_size` required / min / max; `ParsedJSONField` does not decode the string `"[1]"`; `MapUploadForm` corrupt ⇒ `invalid_image`; the `api_forms` param Forms (codes, `team_color ""` ⇒ None) |
| `core/test_map_services.py` (TestCase) | `save_zone_config`: un-confirms the previous config; elevation carried forward; wall_meta NOT carried; spawn tri-state (sent non-empty ⇒ flag, sent `[]` ⇒ cleared, absent ⇒ carried); bases replaced (absent ⇒ all deleted); processed cache deleted. `save_sight_lines`: replace ranks + reseeds strong spots; empty replace leaves the ranking alone; merge ranks without reseeding; **merge onto a missing config keeps the batch** (regression); base_sights per type; spawns auto-computed unless locked. `compute_and_store_sight_lines`: stored grid used, ranking/spots persisted, spawn override locks. `compute_single_cell` off-grid ⇒ `[]`. `get_zone_config` shape, incl. the no-config defaults and legacy list zone_data. **`map_in_use`: true for each of the 6 references** (draft and active Season); false for a completed Season and for a Conference of a completed Season; false for a GameRound-only reference. `delete_map` raises `MapInUseError` and leaves the row; otherwise it deletes the image + cache. `aggregate_map_heatmap`: own + Unmanaged counted, a foreign standalone round, a foreign Match round and a foreign-League Season round excluded; `round_count` scoped |
| `core/test_maps_api_writes.py` (TestCase + APIClient) | `reverse("arenamap-<url_name>", args=[pk])` for all 10 actions; upload 201 + 8 fields + dims + file on disk, 400 `required` / `invalid_image` / `max_length`, JSON ⇒ 415; DELETE 204 (configs cascade, files gone), 409 `map_in_use` (map survives), 404 unknown; PUT/PATCH detail ⇒ 405; each GET's `data` key set; zone-config PUT ⇒ data equals the following GET, absent zones ⇒ auto-detected grid; sight-lines PUT then PATCH merge, compute (real OpenCV) ⇒ ranked-cells / strong-spots non-empty; cell off-grid ⇒ `[]`; strong-spots PUT round-trip; heatmap scoping (a foreign round excluded) and `invalid_choice`; the error table rows (missing zone_size, multipart PUT ⇒ 415, array body ⇒ `parse_error`); a second Account can upload / edit / delete a map made by the first (shared posture); anonymous 403; processed-image `Content-Type: image/png`, body starts with `\x89PNG`, not enveloped |
| `core/test_map_views_html.py` (TestCase) | HTML delete of an in-use map ⇒ 302 to `/maps/`, the error message in `messages`, the map survives; the HTML save endpoints with malformed payloads ⇒ 400 with the §7 body shapes (and non-object body ⇒ the parse-error body); valid editor payloads ⇒ the exact `{"status": "ok"}` / compute body bytes; the HTML heatmap excludes a foreign round and `round_count` drops; HTML upload with a 101-char name ⇒ redirect `/maps/` and no row |

**Existing tests that must change (blast radius):**
- `core/test_maps_api.py::TestMapsSharedAndAuth::test_writes_are_405` — POST with `format="json"` is
  now **415**, and DELETE is now **204** (it deletes `alpha`). Rewrite it so that PUT/PATCH on the
  detail ⇒ 405 `method_not_allowed`, and move POST/DELETE coverage to `test_maps_api_writes.py`. Also
  fix the module docstring ("read-only").
- `matches/tests/test_map.py::TestMap05StrongSpotsViews` — all of them still pass. The non-list and
  non-int cases are still 400; the string `"not-a-list"` now gets the pinned cells message, not a JSON
  decode error. The MAP-08 spawn/compute/save tests send valid payloads (`compute_sight_lines` output,
  base_sights `{"red": [], "blue": []}`, int spawn pairs) ⇒ unaffected.
  `TestMap07DBIntegration` and `test_map_high_ground.py` don't call the views ⇒ unaffected.
- `matches/tests/test_heatmap.py::TestMapHeatmapDataEndpoint` — the rounds are Unmanaged and the
  client is the shared manager ⇒ unaffected (counts unchanged). The plain-text 400s are unchanged.
- `core/tests.py` `UploadMapViewTests` (the corrupt upload still redirects with no row) and
  `DeleteMapViewTests` (no Seasons ⇒ unaffected); the `_get_image_local_path` import works through the
  re-export.
- **Docs (docs agent):** in `core/CLAUDE.md`, the "REST API — maps" "read-only / writes ⇒ 405"
  wording, the URL list (the 16 API routes), the Tests section, the new `forms.py` / `api_forms.py` /
  `services.py`, the delete guard and the heatmap scoping. In `matches/CLAUDE.md`, the API conventions
  table if it lists `/api/maps/` as read-only. PLAN.md: mark ANG-01c done.

## 9. Out of scope / flagged (orchestrator may promote)

- **Behaviour fix:** a merge save onto a missing `SightLineConfig` now stores the batch (§4). The
  editor never hits this path (its first chunk is always a replace).
- There is no zones↔elevation shape check, and no bounds check of spawn/strong-spot/base-sight cells
  against the grid. A map with no image file (hand-built fixtures) still 500s on the
  detect/processed-image paths, as it does today.
- `wall_meta` is validated strictly (only `facing` / `height`). A stored legacy config with other keys
  would fail to re-save from the editor until it is repainted. Risk is low: the editor only writes
  these two keys.
- HTML GET endpoints stay lenient (default 50, clamp). The API is strict (`zone_size` required).
- Iteration order of `base_sights` stays `VALID_BASE_TYPES` (a set) as today, so key order in that
  sub-dict is not pinned. Tests compare parsed JSON for it.
