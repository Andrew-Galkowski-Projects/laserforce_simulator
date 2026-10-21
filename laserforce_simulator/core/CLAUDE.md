# core/

The `core` app provides a 2D arena map importer and editor used to configure maps for match simulation.

## Models (`core/models.py`)

- **`ArenaMap`**: uploaded map image + pixel dimensions
- **`MapZoneConfig`**: 2D zone grid stored in `zone_data` JSON field as `{"zones": [[int, ...], ...], "blocked_edges": {...}, "wall_meta": {...}, "elevation": [[float, ...], ...], "red_spawn": [...], "blue_spawn": [...]}`. Zone cell values: 0=high wall (blocks movement + LOS), 1=floor, 2=red zone (legacy), 3=blue zone (legacy), 4=low wall (blocks movement, transparent to LOS), 5=windowed wall (blocks LOS, but allows directional tagging through an aperture). `wall_meta` is an optional `{"r,c": {"facing": "N"|"S"|"E"|"W", "height": float}}` dict that describes windowed wall aperture directions and wall heights — stored alongside `zones` in the same JSON field (no separate DB column); `height` defaults to 1.0 when absent. `elevation` is a 2D float array (same shape as `zones`) representing the elevation of each cell; defaults to 0.0 for all cells when absent. One confirmed config per map per zone_size.
- **`MapBaseConfig`**: pixel-coordinate (x_px, y_px) of each base (red, blue, neutral_1–4). Zone-size independent.
- **`SightLineConfig`**: bidirectional adjacency dict `{"r,c": ["r,c", ...]}` for all non-wall cell pairs. Keyed per (map, zone_size).
- **`BaseSightLineConfig`**: list of cells `[[row, col], ...]` that can tag each base. Keyed per (map, base_type, zone_size). User-defined (bases sit on raised platforms).
- **`MapCellRankingConfig`**: `ranked_cells` — all passable cells sorted by LOS count descending (`[[row, col], ...]`). Auto-computed when sight lines are saved. Used by Scouts to navigate toward high-visibility positions and by Medic/Ammo to find sheltered vs exposed positions near the allied Heavy. Keyed per (map, zone_size).
- **`HeavyStrongSpotsConfig`**: `cells` — top ~25% of cells by LOS count, representing strategically strong defensive positions for Heavies (`[[row, col], ...]`). Auto-seeded when sight lines are first computed; user-overridable via the map editor. Shared by both team colours (each Heavy picks the nearest spot). Keyed per (map, zone_size).

**Spawn points (MAP-08):** `MapZoneConfig.zone_data` also stores `red_spawn` and `blue_spawn` keys — each a list of `[row, col]` cells representing valid player spawn locations near the respective team's base. Auto-generated when sight lines are saved: all passable cells within Manhattan distance ≤ 5 of the base cell are collected and split into two sub-pools (closer vs farther from the enemy base) for role-aware assignment. The user can override spawn cells in the map editor (painting cells onto a spawn overlay) and save via the existing Save button; manual overrides replace the auto-generated list for the affected team side. No migration is required — spawn data is stored inline in the existing `zone_data` JSON field.

## Map Processing (`core/map_processing.py`)

**`detect_zones(image_path, cell_size)`** — classifies each grid cell:
- Uses `create_processed_image()` internally to build a wall mask (CV threshold 210 + connected-component filtering discards text blobs, keeps large wall features)
- Cell is high wall (0) if ≥1% of pixels are dark in the wall mask; otherwise floor (1). No longer produces legacy 2/3 red/blue zone values — those are user-placed or backward-compat only.
- The original color image is no longer opened by this function (dead pixel-classification code removed); dimensions are read from the B&W processed image.
- Returns `zones`, `blocked_edges` (dict), `blocked_edges_grid` (2D array). Wall types 4/5 are never auto-detected; they are placed manually in the editor.
- Module constant `_LOS_PASSABLE = {1, 2, 3}` defined at module level (floor + legacy zones); used by both `compute_sight_lines` and `compute_single_cell_visibility`.

**`create_processed_image(image_path)`** — returns a B&W PIL Image: threshold at 210, keep connected components with area ≥ 600 or max dimension ≥ 80px (walls), discard smaller (text). Cached to `media/maps/processed_<id>.png`.

**`_compute_blocked_edges(processed_bw, rows, cols, cell_size)`** — samples the pixel column/row at each cell boundary; marks the edge blocked if ≥30% of edge pixels are dark. Enables sub-cell wall precision for near-miss sight lines.

**`_has_los(zone_data, r1, c1, r2, c2, blocked_edges_grid, elevation_grid=None, wall_meta=None)`** — Bresenham's line algorithm. Adjacent cells return immediately (checking only their shared edge). Longer paths walk the line. Wall semantics: high wall (0) and windowed wall (5) both block LOS; low wall (4) is transparent (sight passes through, movement does not). When `elevation_grid` is provided, high wall cells (0) may be shot over using the shoot-over formula: `attacker_elev - wall_cell_elev > wall_height * 0.5` where `wall_height` is read from `wall_meta[r,c]["height"]`; blocks (not shoot-overable) when height key is absent. `_can_shoot_over_high_wall` delegates to the public `can_shoot_over_wall` helper. A high wall cell that satisfies the formula is treated as transparent for that specific attacker–target pair. Returns False on the first non-passable cell or blocked edge encountered.

**`compute_sight_lines(zone_data, use_quadtree=True)`** — all-pairs LOS. Uses a `QuadtreeNode` spatial index when >50 passable cells: each cell only tests neighbors within `max(rows,cols)//4` radius (50–100× speedup over brute force). Falls back to O(n²) for small maps. Accepts both list and dict `zone_data` formats. LOS is direction-aware: A→B and B→A are each checked independently so that asymmetric elevation (elevated attacker can see over a wall that blocks the lower defender's view back) is correctly reflected — a link is added only in the direction that has LOS.

**`compute_single_cell_visibility(r, c, zone_data)`** — O(n) LOS from one cell. Used by the lazy editor endpoint for instant per-click feedback without precomputing all pairs.

**`compute_high_los_ranking(sight_data)`** — returns all passable cells as `[[row, col], ...]` sorted by LOS count descending (most-visible cell first). Called automatically after sight line saves/computes to populate `MapCellRankingConfig`. The top 25% of this list seeds `HeavyStrongSpotsConfig`.

**`compute_spawn_cells(zone_grid, base_cells, max_distance=5)`** — MAP-08. Returns `{"red": [[r,c],...], "blue": [[r,c],...]}` — all passable floor cells (value 1) within Manhattan distance ≤ `max_distance` of each team's base cell. Called by `core.services.update_spawn_cells` (formerly `_update_spawn_cells_in_zone_data`, still aliased in `core/views.py`) after every sight-line save or compute; results are stored inline in `MapZoneConfig.zone_data` as `red_spawn`/`blue_spawn`.

## Map Editor UI (`templates/maps/map_editor.html`)

Three modes toggled in the top bar:

**Zones & Bases mode**: zone grid overlay on B&W processed image. Click base-type buttons (Red/Blue/Neutral 1–4) then click a cell to place. Clicking the same cell again removes it. Wall brush buttons (None, Low Wall, Windowed Wall, High Wall, Floor) let the user paint cell types 4/5/0/1 directly onto the grid; all wall brush buttons support bulk drag-select (click-drag draws a rectangle, releasing applies the wall type to all non-base cells in the region). When Windowed Wall is selected, a direction picker (N/S/E/W) sets the aperture facing stored in `wall_meta`. When High Wall is selected, a height input (default 1.0) sets the `height` value stored in `wall_meta[r,c]["height"]` for each painted cell. Spawn brush buttons (None, Red Spawn, Blue Spawn, Erase Spawn) overlay semi-transparent colored squares on spawn cells; activating a spawn brush dearms the wall and base brushes. Spawn cells auto-load from the server on zone-size selection and are included in the Save payload only when the user has manually edited them (`spawnEdited` flag). Elevation tools: the **Elevation Brush** lets the user enter a numeric elevation value and paint it onto individual cells (click or drag-select); the **Ramp Tool** lets the user click two cells to linearly interpolate elevation across all cells between them (both use the existing bulk drag-select pattern). "Save Configuration" POSTs `zone_size`, base pixel positions, the full `zones` grid, (when non-empty) `wall_meta` including any `height` entries, and (when non-empty) the `elevation` 2D float array; additionally sends `red_spawn`/`blue_spawn` arrays when user-edited. On zone-size change, `wallMeta`, `elevation`, and spawn cell sets are all reset with a console warning if unsaved placements are discarded.

**Sight Lines mode**:
- *Zone view*: click a cell (highlights yellow) to see its visible cells (green) and blocked cells (faint red). Click any cell to toggle its LOS link with the selected cell.
- *Drag-select bulk edit*: with a cell selected, click-drag to draw a rectangle — all non-wall cells in the rectangle highlight purple. Release to toggle all selected cells at once (bidirectional).
- *Base view* (dropdown): shows cells that can tag a specific base. Click to add/remove.
- "Compute Sight Lines" triggers full all-pairs server computation (~0.1–1s depending on zone size).
- "Save Sight Lines" batches the payload into chunks of 100 keys per POST to avoid the 2.5 MB Django request limit. First batch replaces, subsequent batches merge.

**Heatmap mode (RES-04)**: a third mode toggle alongside Zones & Bases and Sight Lines, driven by the `mode-heatmap` button. Activating Heatmap mode hides the Zones & Bases and Sight Lines control blocks, shows `#heatmap-controls` (a team-color filter dropdown `heatmap-editor-filter-team` with options Both/Red/Blue, plus a `heatmap-editor-round-count` text span), hides the zone-paint and sight-line overlays, and renders a translucent heatmap canvas over `#zone-img`. The JS fetches `GET /maps/<id>/heatmap-data/?zone_size=<currently-selected>&team_color=<filter>` (URL name `map_heatmap_data`, view `core/views.py::map_heatmap_data`), which aggregates `GameRound.cell_occupancy_json` across every round on this map at the selected zone_size **that the requester can see** (`owned_game_round_q`, since ANG-01c — see **Heatmap scoping**; delegates to `services.aggregate_map_heatmap`) — server-side filtering by `team_color` joins the per-player occupancy entries against `PlayerRoundState.team_color` and sums the matching cells (cells whose final sum is `0` are omitted). The round count from the response populates `#heatmap-editor-round-count`. The same canvas-overlay routine is reused by the per-round view at `/matches/game-round/<id>/heatmap/` (see [`docs/agents/matches/round-views-and-analytics.md`](../../docs/agents/matches/round-views-and-analytics.md) — **RES-04 movement heatmap**); only the filter set differs (the editor exposes team color only, the round view exposes player/role/team).

## URLs

```
/maps/                              → map list + upload
/maps/<id>/editor/                  → map editor (zones, bases, sight lines)
/maps/<id>/delete/                  → POST: delete map (cascades configs; image + processed cache cleaned up; GameRound.arena_map SET_NULL); refused with an error message + redirect while a non-completed Season uses it (ANG-01c)
/maps/<id>/zones/                   → AJAX: zone detection for given zone_size
/maps/<id>/processed-image/         → B&W cached map image
/maps/<id>/save/                    → POST: save zone config + base positions
/maps/<id>/sight-lines/             → GET: load existing sight line data
/maps/<id>/sight-lines/compute/     → POST: run full all-pairs LOS computation
/maps/<id>/sight-lines/single-cell/ → GET: lazy single-cell LOS (?zone_size=&r=&c=)
/maps/<id>/sight-lines/save/        → POST: save sight lines (batched)
/maps/<id>/strong-spots/            → GET: current HeavyStrongSpotsConfig cells (?zone_size=)
/maps/<id>/strong-spots/save/       → POST: persist user-edited heavy strong spots
/maps/<id>/spawn-cells/             → GET: red_spawn/blue_spawn lists from confirmed zone_data (no zone_size param — data is per-config)
/maps/<id>/heatmap-data/            → GET: RES-04 multi-round movement heatmap aggregate (URL name "map_heatmap_data"; required ?zone_size=<n>, optional &team_color=red|blue; rounds scoped to the requester since ANG-01c)

/api/maps/                                  → GET list (ANG-01a) / POST multipart upload, 201 (ANG-01c)   route "arenamap-list"
/api/maps/<id>/                             → GET detail (ANG-01a) / DELETE 204, 409 map_in_use (ANG-01c) route "arenamap-detail"
/api/maps/<id>/processed-image/             → GET raw image/png, not enveloped                           route "arenamap-processed-image"
/api/maps/<id>/zones/                       → GET zone detection + confirmed overlay (?zone_size=)        route "arenamap-zones"
/api/maps/<id>/zone-config/                 → GET confirmed config + bases / PUT full replace            route "arenamap-zone-config"
/api/maps/<id>/sight-lines/                 → GET (?zone_size=) / PUT replace / PATCH merge               route "arenamap-sight-lines"
/api/maps/<id>/sight-lines/compute/         → POST synchronous all-pairs compute                          route "arenamap-sight-lines-compute"
/api/maps/<id>/sight-lines/cell/            → GET single-cell LOS (?zone_size=&r=&c=)                     route "arenamap-sight-lines-cell"
/api/maps/<id>/ranked-cells/                → GET MapCellRankingConfig (?zone_size=)                      route "arenamap-ranked-cells"
/api/maps/<id>/strong-spots/                → GET (?zone_size=) / PUT replace                             route "arenamap-strong-spots"
/api/maps/<id>/spawn-cells/                 → GET red_spawn / blue_spawn (no params)                      route "arenamap-spawn-cells"
/api/maps/<id>/heatmap/                     → GET scoped multi-round aggregate (?zone_size=[&team_color=]) route "arenamap-heatmap"
```

ANG-01c adds 16 method + path routes on top of the ANG-01a list / detail GETs: `POST /api/maps/`,
`DELETE /api/maps/<id>/`, `GET …/processed-image/`, `GET …/zones/`, `GET` / `PUT …/zone-config/`,
`GET` / `PUT` / `PATCH …/sight-lines/`, `POST …/sight-lines/compute/`, `GET …/sight-lines/cell/`,
`GET …/ranked-cells/`, `GET` / `PUT …/strong-spots/`, `GET …/spawn-cells/`, `GET …/heatmap/`. PUT /
PATCH on `/api/maps/<id>/` itself stay **405**.

## REST API — maps (`core/api_views.py`, `core/serializers.py`, `core/filters.py`)

**ANG-01a** added the read side (list / detail); **ANG-01c** made it the full map-editor API. It is
registered in `laserforce_simulator/api_urls.py` as
`router.register("maps", ArenaMapViewSet, basename="arenamap")` and follows the project
**API conventions** (envelope, error codes, `django-filter`, ADR-0039 — see
[`../matches/CLAUDE.md`](../matches/CLAUDE.md)): views **raise**, never build a 4xx `Response`; writes
bind the shared `core/forms.py` Forms; query params bind the strict `core/api_forms.py` Forms; all
logic lives in `core/services.py` (shared with the HTML views). Seam contract:
[`.claude/worktrees/ang-01c-seam-contract.md`](../../.claude/worktrees/ang-01c-seam-contract.md).

- **`ArenaMapViewSet(ListOnlyFilterMixin, ListModelMixin, RetrieveModelMixin, DestroyModelMixin,
  GenericViewSet)`** — `@method_decorator(login_not_required, name="dispatch")` + settings-level
  `IsAuthenticated` (anonymous ⇒ 401 `not_authenticated`, ADR-0041). `get_queryset()` annotates
  `has_confirmed_config = Exists(MapZoneConfig.objects.filter(arena_map=OuterRef("pk"), confirmed=True))`
  — true iff `latest_confirmed_config() is not None`. Ordering `name` (default), `created_at`, `id`;
  tiebreak `id`. Class-level `parser_classes = [MultiPartParser, FormParser]` (for `create`); every
  JSON action sets `parser_classes=[JSONParser]` on its GET decorator, which also covers its
  `.mapping.put` / `.mapping.patch` methods (DRF builds the route's initkwargs from the original
  action). No `update` / `partial_update`, so PUT / PATCH on the detail ⇒ **405**. Every action is
  `detail=True` and resolves the map through `self.get_object()` (unknown pk ⇒ 404 `not_found`).
- **Not ownership-filtered — maps are shared.** Every authenticated Account sees, uploads, edits and
  deletes every map (HTML parity) — the UX-01 posture below (ArenaMap is shared reference data,
  ADR-0038) carries over unchanged. No `manager` column.
- **`ArenaMapSerializer`** fields, exactly: `id`, `name`, `image` (absolute URL when the request is in
  serializer context, else `null`), `is_default`, `img_width`, `img_height`, `created_at`,
  `has_confirmed_config` (both `image` and `has_confirmed_config` read-only).
- **`ArenaMapFilterSet`**: `is_default` and `has_confirmed_config` (booleans — `true/false/1/0`;
  junk values are ignored, not 400).

**Endpoints (ANG-01c)** — read `data` equals the HTML JSON byte for byte (one service); write
endpoints return the **saved state** (the matching GET's `data`), never `{"status": "ok"}`.

| Method + path | Handler | Binds | `data` |
|---|---|---|---|
| `POST /api/maps/` | `create` | `MapUploadForm` (multipart) | 201, the 8 serializer fields (`has_confirmed_config` false) |
| `DELETE /api/maps/<id>/` | `destroy` → `perform_destroy` | — | 204, empty; in use ⇒ **409 `map_in_use`** |
| `GET …/processed-image/` | `processed_image` | — | raw `FileResponse` `image/png`, **not enveloped** (send `Accept: */*`; `image/png` ⇒ 406) |
| `GET …/zones/` | `zones` | `ZoneSizeParamsForm` | `rows, cols, cell_size, img_width, img_height, zones, blocked_edges, blocked_edges_grid` [+ `wall_meta`, `elevation`] |
| `GET …/zone-config/` | `zone_config` | — | `zone_size, zones, wall_meta, elevation, red_spawn, blue_spawn, spawn_user_edited, bases` |
| `PUT …/zone-config/` | `replace_zone_config` | `ZoneConfigForm` | as the GET |
| `GET …/sight-lines/` | `sight_lines` | `ZoneSizeParamsForm` | `zone_size, sight_data, base_sights` |
| `PUT …/sight-lines/` | `replace_sight_lines` | `SightLinesSaveForm` (`mode="replace"`) | as the GET |
| `PATCH …/sight-lines/` | `merge_sight_lines` | `SightLinesSaveForm` (`mode="merge"`) | as the GET |
| `POST …/sight-lines/compute/` | `compute_sight_lines` | `ComputeSightLinesForm` | `sight_data, zone_size` (synchronous) |
| `GET …/sight-lines/cell/` | `sight_line_cell` | `SingleCellParamsForm` | `visible_cells` (off-grid / impassable ⇒ `[]`, 200) |
| `GET …/ranked-cells/` | `ranked_cells` | `ZoneSizeParamsForm` | `ranked_cells` |
| `GET …/strong-spots/` | `strong_spots` | `ZoneSizeParamsForm` | `cells` |
| `PUT …/strong-spots/` | `replace_strong_spots` | `StrongSpotsForm` | `cells` |
| `GET …/spawn-cells/` | `spawn_cells` | — | `red_spawn, blue_spawn` |
| `GET …/heatmap/` | `heatmap` | `HeatmapParamsForm` | `cell_occupancy, zone_size, rows, cols, round_count` (scoped, below) |

- **`zone-config` PUT** is full-replace with the HTML carry-forward rules kept: absent `zones` ⇒
  auto-detected from the image (`[]` too); absent `bases` ⇒ **all bases deleted**; absent `wall_meta`
  ⇒ dropped; absent `elevation` / spawns ⇒ carried forward from the confirmed config; spawns sent
  (even `[]`) ⇒ `spawn_user_edited = bool(red) or bool(blue)`. The GET shape round-trips (PUT body =
  GET `data` minus `spawn_user_edited`).
- **Sight-line batching**: clients send PUT for the first chunk and PATCH for the rest (the editor's
  pattern). Replace re-ranks and reseeds Heavy strong spots (top quarter) when `sight_data` is
  non-empty; merge re-ranks only. `base_sights` upserts per base type, other types untouched; spawn
  cells are then recomputed (unless user-locked).
- **Errors** (beyond the ANG-01a table): JSON body on upload / multipart body on a JSON write ⇒ 415
  `unsupported_media_type`; non-object JSON body ⇒ 400 `parse_error`; missing / non-int / out-of-range
  `zone_size` (every endpoint except upload, delete, processed-image, zone-config GET, spawn-cells) ⇒
  400 `required` / `invalid` / `min_value` / `max_value`; bad `r` / `c` ⇒ `required` / `invalid` /
  `min_value`; bad `team_color` ⇒ `invalid_choice`; malformed payload fields ⇒ 400 `invalid` with the
  field name and the pinned `core/forms.py` message; `cells` absent ⇒ `required`; upload: missing
  `name` / `image` ⇒ `required`, `name` > 100 ⇒ `max_length`, non-image / corrupt ⇒ `invalid_image`
  (raised as `EnvelopeValidationError` when `services.upload_map` raises `InvalidMapImage`).
- **`MapInUse(APIException)`** — `status_code = 409`, `default_code = "map_in_use"`,
  `default_detail = "This map is used by a Season that is not completed."`; `perform_destroy` maps
  `services.MapInUseError` to it.

## Map Forms (`core/forms.py`, `core/api_forms.py`) — ANG-01c

**`core/forms.py`** — strict write Forms shared by the HTML views and the API. **`ParsedJSONField`**
(a `forms.JSONField` subclass) takes **already-decoded** JSON (DRF `JSONParser` / the HTML view's
`json.loads`): `to_python` returns the value unchanged (a string is never `json.loads`'d) and
`empty_values = [None]`, so `[]` / `{}` survive (`red_spawn: []` means "explicitly erased"). "int"
excludes `bool` everywhere. Field order sets the envelope error order and the HTML "first error".

- **`MapUploadForm`** — `name` (`CharField(max_length=100)`), `image` (`ImageField`).
- **`ZoneConfigForm`** — `zone_size` (`IntegerField(10..200)`, required); `zones` (non-empty list of
  equal-length non-empty rows of ints in 0..5; `[]` ⇒ `None` = auto-detect); `wall_meta` (`"r,c"` keys
  → `{"facing": N|S|E|W, "height": number >= 0}`, **only those two keys**; default `{}`); `elevation`
  (list of rows of numbers in `[0.0, 10.0]`; the "each row must be a list" / "Elevation value out of
  range [0.0, 10.0]: <val>" messages are byte-identical to the pre-ANG-01c view); `red_spawn` /
  `blue_spawn` (list of `[int, int]`; absent ⇒ `None` = not sent, `[]` kept); `bases` (list of
  `{"type", "x_px", "y_px"}`, exact keys, valid base type, int pixels, no repeated type; default `[]`).
- **`ComputeSightLinesForm`** — `zone_size`, `red_spawn`, `blue_spawn`.
- **`SightLinesSaveForm`** — `zone_size`, `sight_data` (`"r,c"` → list of `"r,c"`; default `{}`),
  `base_sights` (base type → list of `[int, int]`; default `{}`).
- **`StrongSpotsForm`** — `zone_size`, `cells` (required list of `[int, int]`; message
  `cells must be a list of [int, int] pairs`, byte-identical to before; bool pairs now rejected).

No cross-field zones↔elevation shape check and no grid-bounds check of cells (PLAN.md Deferred Items).

**`core/api_forms.py`** — strict API read-param Forms (the ANG-01b `matches/api_forms.py` precedent),
bound as `Form(data=plain_form_data(Form, request.query_params))` + `validate_form_or_raise`:
`ZoneSizeParamsForm` (`zone_size` 10..200, required), `SingleCellParamsForm` (+ `r`, `c`
`IntegerField(min_value=0)`, required), `HeatmapParamsForm` (+ optional `team_color` red / blue; `""`
⇒ `None`).

## Map services (`core/services.py`) — ANG-01c

Every map read / write / compute, shared by the HTML views and the API. Every function takes the
**`ArenaMap` instance** (callers do the `get_object_or_404` / `get_object()` lookup); writes are
`@transaction.atomic`; dict key insertion order is pinned so the HTML JSON bodies stay byte-identical.
`services` never imports `core.views` (views import services, calling them as `services.<name>`
because the names collide with view names) and imports `matches.models` / `accounts.permissions`
**lazily inside functions** (the core→matches cycle).

- **Upload / delete** — `upload_map(name, image_file) -> ArenaMap` (reads dimensions through storage;
  unreadable image ⇒ file + row deleted, raises **`InvalidMapImage`**); `delete_map(arena_map)` (raises
  **`MapInUseError`** and touches nothing when `map_in_use`, else deletes the row — configs CASCADE,
  `GameRound.arena_map` SET_NULL — and removes the processed cache + image file in a
  `transaction.on_commit` callback, so a rolled-back delete never strands a map without its image; tests
  that assert the files are gone wrap the delete in `captureOnCommitCallbacks(execute=True)`); `map_in_use(arena_map) -> bool`
  (below); `processed_image_path(arena_map) -> Path` (builds `processed_<pk>.png` if missing).
- **Zones** — `detect_zones_with_overlay(arena_map, zone_size)` (detection + the confirmed config's
  zones / `wall_meta` / `elevation` overlaid when its zone_size matches); `get_zone_config(arena_map)`;
  `save_zone_config(arena_map, *, zone_size, zones, wall_meta, elevation, red_spawn, blue_spawn,
  bases) -> MapZoneConfig` (un-confirms the old config, creates the new confirmed one, replaces all
  `MapBaseConfig` rows, clears the processed cache; carry-forward rules as in the API section).
- **Sight lines** — `get_sight_lines`, `compute_and_store_sight_lines(arena_map, zone_size, *,
  red_spawn=None, blue_spawn=None)` (computes from `_resolve_zone_data` — the latest config of **any**
  confirmed state for that zone_size, else `detect_zones` — then stores sight lines, ranking, strong
  spots, optional spawn override, spawn cells), `compute_single_cell`, `save_sight_lines(arena_map,
  zone_size, *, sight_data, base_sights, mode="replace" | "merge")`. **Bug fix:** a merge onto a
  **missing** `SightLineConfig` now stores the batch (previously it created `{}` and discarded it).
- **Derived reads / writes** — `update_spawn_cells(arena_map, zone_size)` (the old
  `_update_spawn_cells_in_zone_data`, verbatim; `views` keeps that name as an alias),
  `get_ranked_cells`, `get_strong_spots` / `save_strong_spots`, `get_spawn_cells`.
- **Heatmap** — `aggregate_map_heatmap(arena_map, zone_size, team_color, user)` (below).
- **Moved helpers** — `_get_image_local_path` and `_clear_processed_cache` now live here and are
  **re-exported from `core/views.py`** (`core/tests.py` imports `_get_image_local_path` from `.views`).
  `_seed_defaults` / `_DEFAULT_MAPS` stay in views.

### Delete guard — `map_in_use` (ANG-01c)

A map **cannot be deleted while a non-completed (draft or active) Season references it**, through
any of six references: (1) `Season.map_pool` (M2M), (2) `Season.starting_map_pool_ids_json` (the
snapshot `_resolve_fixture_map` actually reads), (3) `Season.map_rotation_ids_json`, (4)
`Season.starting_map_rotation_ids_json`, (5) `Conference.map_rotation_ids_json`, (6)
`Conference.starting_map_rotation_ids_json` (Conferences of a non-completed Season). The JSON id-list
membership check runs **in Python** (SQLite has no JSONField `contains`). A completed Season never
blocks, even if its lists name the map; historical `GameRound`s and `Season.map_mode` alone do not
count. API ⇒ **409 `map_in_use`**; HTML `delete_map` ⇒ `messages.error("Cannot delete map “<name>”:
it is used by a Season that is not completed.")` + redirect to `/maps/`. Nothing is deleted either
way.

### Heatmap scoping (ANG-01c behaviour change)

`aggregate_map_heatmap` filters its rounds with `owned_game_round_q(user)` (own + Unmanaged + own /
Unmanaged Match / League rounds), so **both** the API `…/heatmap/` and the HTML `map_heatmap_data`
now aggregate only rounds **visible to the requester**, and `round_count` is the scoped count. The
map itself stays unfiltered — only the round corpus is scoped. The HTML endpoint keeps its lenient
parse (plain-text `"zone_size required"` / `"invalid team_color"` 400s, any-int zone_size).

### HTML vs API strictness

The API is strict (`zone_size` required, 10..200). The HTML map views keep their lenient parse first
and unchanged (`json.loads`, `int(zone_size)` with each view's existing parse-error body, then the
clamp `max(10, min(zs, 200))` or default 50 on GETs), and only then bind the strict Form with the
clamped `zone_size` and HTML defaults injected. Valid editor payloads ⇒ identical DB writes and
**byte-identical** success bodies. Malformed payloads (non-list / ragged / out-of-set zones, bad
`wall_meta`, non-list elevation, bad spawn pairs, unknown / duplicate base types, non-int pixels,
malformed sight-line keys, bool cell pairs, non-object JSON bodies — previously saved, ignored or a
500) are now 400 with each view's **existing** error body shape: `{"status": "error", "message":
<first form error>}` (zone-config / sight-line / strong-spot saves; the sight-line save previously
sent no `message`) or `{"error": <first form error>}` (compute). HTML upload with an invalid form
(incl. a name > 100) or an unreadable image redirects to `/maps/` with no row created, and (UXP-04)
flashes `Map upload failed: <field label>: <first form error>` (or `…: the file could not be read as
an image.`).

## Storage Backend (`core/services.py`, re-exported from `core/views.py`)

**`_get_image_local_path(image_field: FieldFile) -> str`** — storage-agnostic helper (moved to `core/services.py` by ANG-01c, still importable from `core.views`) used by every view that needs to pass an image path to OpenCV/PIL. For local `FileSystemStorage`, returns `image_field.path` directly. For remote backends (R2/S3), downloads the image to `MEDIA_ROOT/maps/_remote_cache/<filename>` on first access and returns that cached local path on subsequent calls. Map images are treated as immutable after upload so the cache never goes stale.

**`_seed_defaults()`** — skips seeding entirely when the active storage backend is not `FileSystemStorage` (i.e., R2 is configured). Default map images cannot be seeded from the local `Screenshots_and_video_examples/` directory in production.

**`serve_media` view (UXP-02)** — `re_path(r"^media/(?P<path>.*)$")` in the project URLconf, replacing the DEBUG-only `static()` helper. Under `DEBUG` (dev) or when `settings.SERVE_MEDIA` (env `SERVE_MEDIA`, default False; the self-host compose file sets it) is on it delegates to `django.views.static.serve` with `MEDIA_ROOT`, else 404. Read at request time, so `override_settings` works in tests (CI runs with `DEBUG=True`). Behind the global login gate. **`_restore_default_images()`** — called by `map_list` on every request: copies back a default map's image from `Screenshots_and_video_examples/` when its row exists but the file is gone (fresh media volume / rebuilt container); never creates rows.

**`upload_map` view** — binds `MapUploadForm` and delegates to `services.upload_map`, which reads image dimensions via `image_field.open("rb")` (works for both local and remote storage) instead of `image_field.path`. Handles corrupt/non-image uploads gracefully: deletes the partial record and redirects back to the map list rather than returning a 500.

**`save_zone_config` view** — binds `ZoneConfigForm` and delegates to `services.save_zone_config`. Accepts an optional `elevation` 2D float array from the client. Server-side validation rejects payloads where any elevation value is outside `[0.0, 10.0]` (returns HTTP 400). Elevation is carried forward from the existing confirmed config when not sent by the client, and persisted inline in `MapZoneConfig.zone_data` under the `"elevation"` key.

## Dependencies

`requirements.txt` includes `Pillow>=10.0.0` and `opencv-python-headless>=4.0.0` for image processing, and `django-storages[s3]>=1.14` + `boto3>=1.34` for Cloudflare R2 media storage.

## Tests

`core/tests.py`:
- `GetImageLocalPathTests` — local storage path passthrough, remote download-to-cache, cache reuse (no duplicate downloads)
- `SeedDefaultsTests` — skips seeding when non-`FileSystemStorage` is active
- `UploadMapViewTests` — dimensions stored correctly after upload; corrupt uploads rejected and not persisted
- `DeleteMapViewTests` — `delete_map` POST removes map + redirects to list, related configs cascade, GET → 405, missing map → 404, list page renders the Delete control

`core/test_maps_api.py` (ANG-01a; a sibling of `core/tests.py` since `core` has no `tests/` package) — `/api/maps/` list / detail envelope, exact serializer fields, absolute `image` URL, `has_confirmed_config` true / false, `is_default` / `has_confirmed_config` filters, same maps visible to two different Accounts, anonymous 401, PUT / PATCH on the detail 405 (POST / DELETE coverage moved to `test_maps_api_writes.py` by ANG-01c).

ANG-01c siblings (image fixtures are a tiny in-memory PIL PNG uploaded under `override_settings(MEDIA_ROOT=Path(tempfile.mkdtemp()))` — a `Path`, not a `str` — with OpenCV run for real):
- `core/test_map_forms.py` (SimpleTestCase) — every `core/forms.py` field rule and pinned message (spawn `[]` vs absent, `zones: []` ⇒ `None`, ragged / out-of-set / bool values, `wall_meta` key / facing / height / extra key, elevation messages, duplicate / unknown base type, `sight_data` / `base_sights` shapes, `cells` message + `required`), `zone_size` bounds, `ParsedJSONField` not decoding strings, `MapUploadForm` `invalid_image`, and the `core/api_forms.py` param Forms
- `core/test_map_services.py` (TestCase) — `save_zone_config` carry-forward / spawn tri-state / bases replace / cache clear; `save_sight_lines` replace vs merge (incl. the **merge-onto-missing-config regression**); `compute_and_store_sight_lines`; `compute_single_cell` off-grid ⇒ `[]`; `get_zone_config` shape; **`map_in_use` true for each of the 6 references** and false for completed Seasons / GameRound-only references; `delete_map`; `aggregate_map_heatmap` scoping
- `core/test_maps_api_writes.py` (TestCase + APIClient) — all 10 actions by route name; upload 201 / 400s / 415; DELETE 204 / 409 / 404; PUT / PATCH detail 405; per-GET `data` key sets; zone-config PUT round-trip + auto-detect; sight-lines PUT then PATCH; compute ⇒ ranked cells / strong spots; strong-spots round-trip; heatmap scoping + `invalid_choice`; the error table; cross-Account shared writes; anonymous 401; processed-image PNG not enveloped
- `core/test_map_views_html.py` (TestCase) — HTML delete of an in-use map (302 + error message, map survives); malformed HTML save payloads ⇒ 400 with the existing body shapes; valid editor payloads ⇒ exact success bytes; HTML heatmap excludes a foreign round; 101-char upload name ⇒ redirect, no row
- `core/map_test_fixtures.py` — shared test-support module (not collected — its name matches no `python_files` glob) for the four ANG-01c files: the in-memory border-only PNG builder (zone_size 50 ⇒ a 4×4 grid with a 2×2 floor interior, so compute yields a non-empty ranking), `TempMediaMixin` (a `Path` `MEDIA_ROOT` under a temp dir + `rmtree` cleanup), and the Season / Match / round builders used by the `map_in_use` and heatmap-scoping tests

Map-processing tests for MAP-05/07 features live in `matches/tests/test_map.py` alongside the MAP-02–04 tests:
- `TestMap05ComputeHighLosRanking` — sort correctness (highest-LOS cell first), all cells returned, empty input returns empty
- `TestMap05StrongSpotsViews` — GET returns cells, returns `[]` when no config, POST persists cells, POST rejects non-list cells and non-int pairs, GET method not allowed on save endpoint
- `TestMap07WallTypes` — 25 pure unit tests covering: movement adjacency (high/low/windowed wall block, legacy 2/3 passable), LOS (low wall transparent, windowed/high wall block), `compute_sight_lines` passable-origin filtering, `_can_tag_through_windowed_wall` N-S/E-W axis and unknown facing, `_get_los_targets` aperture hit and miss, proximity-based `_zone_from_cell` cases
- `TestMap07DBIntegration` — 2 DB tests: `wall_meta` round-trip through `save_zone_config` → `_resolve_map_data`, and `wall_meta` key present in `_build_movement_ctx` result

MAP-09 tests live in `matches/tests/test_map_high_ground.py`:
- `TestMap09ElevationStorage` (DB) — elevation 2D array and wall height round-trip through `MapZoneConfig.zone_data`; `_elevation_at` helper; `_resolve_map_data` returns `elevation_grid` at index 9
- `TestMap09WallHeight` (DB) — `wall_meta` height key persists; missing height key returns None (callers treat as impassable)
- `TestMap09ShootOver` (pure) — 8 tests for `can_shoot_over_wall`: threshold boundary (strict `>`), elevated wall base, short/zero-height walls
- `TestMap09LOSWithElevation` (pure) — 8 tests for `_has_los` with elevation: baseline blocking, flat elevation no help, elevated attacker opens LOS, insufficient elevation still blocked, windowed wall unaffected, missing height blocks, **asymmetric LOS** (`compute_sight_lines` gives A→B but not B→A when only A is elevated enough to shoot over the wall)
- `TestMap09HitChanceModifier` (pure) — 10 tests for `elevation_hit_modifier`: level/downhill=1.0, uphill 1/3/5/6/large units, fractional diff, multiplicative application
- `TestMap09BackwardsCompat` (pure) — 5 tests: `_elevation_at(None)`, empty dict, zero elevations, missing height, `_has_los` without new kwargs

## LG-01a landing view

Mode-picker landing page at `GET /` (replaces the previous `path("", include("teams.urls"))` homepage redirect to the team list). Three mode cards — **Sandbox** (links to `/teams/`), **Single-player League** (links to `/leagues/`), **Multiplayer** (greyed `Coming soon`, non-anchor) — plus an in-progress Leagues card grid populated from `League.objects.filter(state="active")`. The `/leagues/` index itself lives in [`docs/agents/matches/league-foundation.md`](../../docs/agents/matches/league-foundation.md) **LG-01a leagues list**; this section covers only the landing view.

**URL.** Mounted in `laserforce_simulator/urls.py` as `path("", core_views.landing, name="landing")` (after adding `from core import views as core_views` to the project URLconf imports). The previous `path("", include("teams.urls"))` line is replaced; the `path("teams/", include("teams.urls"))` mount is untouched so `{% url 'team_list' %}` still reverses to `/teams/`. The HX-01 ordering comment on `path("players/", ...)` stays accurate — there is still a `path("", ...)` catch-all, it just now points at `core_views.landing`.

**View.** `core.views.landing(request) -> HttpResponse` — undecorated, GET-driven (no explicit method allowlist), placed at the bottom of `core/views.py` after `map_heatmap_data`. Body: **lazy-import `from matches.models import League` INSIDE the function body** (mirrors the existing `map_heatmap_data` lazy-import precedent in the same file — avoids the `core ↔ matches` apps-loading cycle), run **one ORM query** `in_progress_leagues = list(League.objects.filter(state="active").order_by("-id"))`, then `render("core/landing.html", {"in_progress_leagues": in_progress_leagues})`. Context (frozen): `{in_progress_leagues}`. **No `select_related` / `prefetch_related` on `League.active_season`** — it is a `@property` (not an FK), so per-card iteration issues one extra `Season.objects.exclude(state="completed").order_by("-id").first()` query; the LG-01a decision is to accept that cost (the landing list is user-bounded and the optimisation is non-breaking to add later via `Prefetch` if it ever bites).

**Template.** NEW `templates/core/landing.html` extends `base.html`, `{% block title %}Laserforce Manager{% endblock %}`. Locked DOM ids: `mode-picker` (outer wrapper section/div), `mode-card-sandbox` (anchor `<a>` reversing `{% url 'team_list' %}`, title text contains `Sandbox`), `mode-card-league` (anchor reversing `{% url 'league_list' %}`, title text contains `Single-player League`), `mode-card-multiplayer` (**non-anchor `<div>`** with `aria-disabled="true"` + a `<span class="badge bg-secondary">Coming soon</span>`, visually greyed via Bootstrap `opacity-50` or equivalent — must NOT be wrapped in `<a>`), `in-progress-leagues` (wrapping `<section>` rendered ONLY when `in_progress_leagues` is non-empty — the empty branch emits no notice, the substring `id="in-progress-leagues"` must NOT appear in the body when zero active Leagues exist), and `in-progress-league-card-{league.id}` (one anchor per active League, `href="/leagues/{{ league.id }}/"` — **raw URL string**, NOT `{% url ... %}`). Each in-progress card renders the League name, either `Season: {{ league.active_season.name }}` (when `league.active_season` is truthy) or the literal substring `No active season` (otherwise), and a state badge whose `class` contains `state-badge`. **Deferred broken-link decision (locked):** the per-card `/leagues/<id>/` hrefs are known-broken until LG-01c — the `league_detail` URL name doesn't yet exist; raw strings are deliberate.

**Navbar patch** (`templates/base.html`, two edits, no other nav-link touched). `navbar-brand` href: `{% url 'team_list' %}` → `{% url 'landing' %}` (the visible `⚡ Laserforce Manager` text + unicode `⚡` are unchanged). New `<a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>` inserted as the FIRST child of `<div class="navbar-nav ms-auto">` above the existing `Teams` link.

**Determinism / scope.** Read-only view — no writes, no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline. **No model change, no migration, no ADR, no GLOSSARY.md edit, no new domain term** (League / Season / Standings already defined under `### League and seasons` from LG-01), no new aggregation module, no JS, no new dependency. **No change to `/teams/`, the `team_list` view, the `team_list` URL name, or any existing `{% url 'team_list' %}` reference** — removing the duplicate `path("", include("teams.urls"))` mount does not affect the standalone `path("teams/", include("teams.urls"))` mount. **Tests.** Extended `core/tests.py` with 12 view tests covering 200 status + `in_progress_leagues` context key, the three mode-card DOM ids, sandbox → `team_list` and league → `league_list` href resolution, the multiplayer-card non-anchor + `Coming soon` + `aria-disabled="true"` shape, omission of `in-progress-leagues` when no active Leagues, active-League cards sorted `-id` with both ids present, deferred `/leagues/<id>/` href per card, `Season: <name>` shown when `active_season` exists, `No active season` shown otherwise, archived Leagues excluded from the in-progress section, and `reverse("landing") == "/"`. The navbar regression test (`test_base_html_navbar_brand_links_to_landing_and_leagues_nav_link_present` — `leagues-nav-link` present + brand href `/`) lives in either `core/tests.py` or `matches/tests/test_league_list.py`.

**Locked names.** URL `GET /` (URL name `landing`); view `core.views.landing`; template `templates/core/landing.html`; DOM ids `mode-picker` / `mode-card-sandbox` / `mode-card-league` / `mode-card-multiplayer` / `in-progress-leagues` / `in-progress-league-card-{league.id}` / `leagues-nav-link`; context key `in_progress_leagues`. Seam contract: [`.claude/worktrees/lg-01a-seam-contract.md`](../../.claude/worktrees/lg-01a-seam-contract.md).
- `TestMap09ResolvesElevationFromZoneData` (DB) — `_resolve_map_data` returns correct elevation array; returns None when key absent

## UX-01 ownership — `ArenaMap` is deliberately NOT an Ownership root

**`core` is the one app UX-01 did not touch.** `ArenaMap` and **all six** map-config models
(`MapZoneConfig`, `MapBaseConfig`, `SightLineConfig`, `BaseSightLineConfig`, `MapCellRankingConfig`,
`HeavyStrongSpotsConfig`) carry **no `manager` column**, appear in **no** `_PARENT_FIELD` entry, and
resolve to `None` from `accounts.permissions.ownership_root` — they have **no ownership axis** at
all, which `is_owned_by` treats as *always allowed* (shared reference data).

Concretely, and verified as part of the slice:

- **No migration.** `core` stays at `0004_map05_cell_ranking_and_strong_spots`.
- **No filtering.** `map_list` is **not** wrapped in `owned_queryset`; every `/maps/` route returns
  the same maps to every Account.
- **No conversions.** All **14** `get_object_or_404(ArenaMap, ...)` sites in `core/views.py` are left
  **byte-for-byte unchanged** — they are the 14 of the project's 97 sites that were *not* converted to
  `get_owned_or_404` (the other 83 were).
- `/maps/` is still behind the **global login gate** (`LoginRequiredMiddleware`) with **no**
  `@login_not_required` exemption: authenticated, but unfiltered.
- **ANG-01a** keeps the same posture on the API: `ArenaMapViewSet` (`/api/maps/`) is authenticated
  (DRF `IsAuthenticated`; it carries the usual API `login_not_required` middleware exemption so an
  anonymous caller gets a 401 JSON (403 before ANG-03) rather than a 302) but its queryset is **not** ownership-filtered.
- **ANG-01c** keeps it for writes: any authenticated Account may upload / edit / delete any map, via
  HTML or API (the only delete restriction is the Season-based `map_in_use` guard, not ownership).
  The one ownership-aware piece is the **multi-round heatmap aggregate**: the *map* is unfiltered,
  but the *rounds* it sums are now scoped by `owned_game_round_q(request.user)` (rounds are owned
  data). This does not add a `manager` column or convert any of the 14 lookups.

**Why** ([ADR-0038](../../docs/adr/0038-accounts-and-uniform-manager-ownership.md)): maps are
referenced **across League boundaries** by `ArenaMap.is_default`, `Season.map_mode`, the CONF-06
per-Conference **Map pools** and SUB-01's `rotate_by_matchday` / CONF-06's `rotate_by_conference`
rotations. A privately-owned map would therefore silently break *another* Manager's season rotation —
`_resolve_fixture_map` would quietly return `None` for every fixture rather than raise. A per-map
`is_public` flag is the correct end state and was **deferred** rather than invented here (PLAN.md
**Deferred Items**), because the cross-League referencing rules have to be settled first: what a
running rotation does when a map it references goes private.

> Adding a `manager` FK to `ArenaMap` or any map-config model, filtering a `/maps/` queryset, or
> converting one of those 14 lookups **breaks the UX-01 contract**. See
> [`../accounts/CLAUDE.md`](../accounts/CLAUDE.md) and seam contract §2.4.