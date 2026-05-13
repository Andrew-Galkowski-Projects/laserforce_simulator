# core/

The `core` app provides a 2D arena map importer and editor used to configure maps for match simulation.

## Models (`core/models.py`)

- **`ArenaMap`**: uploaded map image + pixel dimensions
- **`MapZoneConfig`**: 2D zone grid stored in `zone_data` JSON field as `{"zones": [[int, ...], ...], "blocked_edges": {...}, "wall_meta": {...}}`. Zone cell values: 0=high wall (blocks movement + LOS), 1=floor, 2=red zone (legacy), 3=blue zone (legacy), 4=low wall (blocks movement, transparent to LOS), 5=windowed wall (blocks LOS, but allows directional tagging through an aperture). `wall_meta` is an optional `{"r,c": {"facing": "N"|"S"|"E"|"W"}}` dict that describes windowed wall aperture directions — stored alongside `zones` in the same JSON field (no separate DB column). One confirmed config per map per zone_size.
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

**`_has_los(zone_data, r1, c1, r2, c2, blocked_edges_grid)`** — Bresenham's line algorithm. Adjacent cells return immediately (checking only their shared edge). Longer paths walk the line. Wall semantics: high wall (0) and windowed wall (5) both block LOS; low wall (4) is transparent (sight passes through, movement does not). Returns False on the first blocking cell or blocked edge encountered.

**`compute_sight_lines(zone_data, use_quadtree=True)`** — all-pairs LOS. Uses a `QuadtreeNode` spatial index when >50 passable cells: each cell only tests neighbors within `max(rows,cols)//4` radius (50–100× speedup over brute force). Falls back to O(n²) for small maps. Accepts both list and dict `zone_data` formats.

**`compute_single_cell_visibility(r, c, zone_data)`** — O(n) LOS from one cell. Used by the lazy editor endpoint for instant per-click feedback without precomputing all pairs.

**`compute_high_los_ranking(sight_data)`** — returns all passable cells as `[[row, col], ...]` sorted by LOS count descending (most-visible cell first). Called automatically after sight line saves/computes to populate `MapCellRankingConfig`. The top 25% of this list seeds `HeavyStrongSpotsConfig`.

**`compute_spawn_cells(zone_grid, base_cells, max_distance=5)`** — MAP-08. Returns `{"red": [[r,c],...], "blue": [[r,c],...]}` — all passable floor cells (value 1) within Manhattan distance ≤ `max_distance` of each team's base cell. Called by `_update_spawn_cells_in_zone_data` after every sight-line save or compute; results are stored inline in `MapZoneConfig.zone_data` as `red_spawn`/`blue_spawn`.

## Map Editor UI (`templates/maps/map_editor.html`)

Two modes toggled in the top bar:

**Zones & Bases mode**: zone grid overlay on B&W processed image. Click base-type buttons (Red/Blue/Neutral 1–4) then click a cell to place. Clicking the same cell again removes it. Wall brush buttons (None, Low Wall, Windowed Wall, High Wall, Floor) let the user paint cell types 4/5/0/1 directly onto the grid. When Windowed Wall is selected, a direction picker (N/S/E/W) sets the aperture facing stored in `wall_meta`. Spawn brush buttons (None, Red Spawn, Blue Spawn, Erase Spawn) overlay semi-transparent colored squares on spawn cells; activating a spawn brush dearms the wall and base brushes. Spawn cells auto-load from the server on zone-size selection and are included in the Save payload only when the user has manually edited them (`spawnEdited` flag). "Save Configuration" POSTs `zone_size`, base pixel positions, the full `zones` grid, and (when non-empty) `wall_meta`; additionally sends `red_spawn`/`blue_spawn` arrays when user-edited. On zone-size change, `wallMeta` and spawn cell sets are both reset with a console warning if unsaved placements are discarded.

**Sight Lines mode**:
- *Zone view*: click a cell (highlights yellow) to see its visible cells (green) and blocked cells (faint red). Click any cell to toggle its LOS link with the selected cell.
- *Drag-select bulk edit*: with a cell selected, click-drag to draw a rectangle — all non-wall cells in the rectangle highlight purple. Release to toggle all selected cells at once (bidirectional).
- *Base view* (dropdown): shows cells that can tag a specific base. Click to add/remove.
- "Compute Sight Lines" triggers full all-pairs server computation (~0.1–1s depending on zone size).
- "Save Sight Lines" batches the payload into chunks of 100 keys per POST to avoid the 2.5 MB Django request limit. First batch replaces, subsequent batches merge.

## URLs

```
/maps/                              → map list + upload
/maps/<id>/editor/                  → map editor (zones, bases, sight lines)
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
```

## Storage Backend (`core/views.py`)

**`_get_image_local_path(image_field: FieldFile) -> str`** — storage-agnostic helper used by every view that needs to pass an image path to OpenCV/PIL. For local `FileSystemStorage`, returns `image_field.path` directly. For remote backends (R2/S3), downloads the image to `MEDIA_ROOT/maps/_remote_cache/<filename>` on first access and returns that cached local path on subsequent calls. Map images are treated as immutable after upload so the cache never goes stale.

**`_seed_defaults()`** — skips seeding entirely when the active storage backend is not `FileSystemStorage` (i.e., R2 is configured). Default map images cannot be seeded from the local `Screenshots_and_video_examples/` directory in production.

**`upload_map` view** — reads image dimensions via `image_field.open("rb")` (works for both local and remote storage) instead of `image_field.path`. Handles corrupt/non-image uploads gracefully: deletes the partial record and redirects back to the map list rather than returning a 500.

## Dependencies

`requirements.txt` includes `Pillow>=10.0.0` and `opencv-python-headless>=4.0.0` for image processing, and `django-storages[s3]>=1.14` + `boto3>=1.34` for Cloudflare R2 media storage.

## Tests

`core/tests.py`:
- `GetImageLocalPathTests` — local storage path passthrough, remote download-to-cache, cache reuse (no duplicate downloads)
- `SeedDefaultsTests` — skips seeding when non-`FileSystemStorage` is active
- `UploadMapViewTests` — dimensions stored correctly after upload; corrupt uploads rejected and not persisted

Map-processing tests for MAP-05/07 features live in `matches/tests/test_map.py` alongside the MAP-02–04 tests:
- `TestMap05ComputeHighLosRanking` — sort correctness (highest-LOS cell first), all cells returned, empty input returns empty
- `TestMap05StrongSpotsViews` — GET returns cells, returns `[]` when no config, POST persists cells, POST rejects non-list cells and non-int pairs, GET method not allowed on save endpoint
- `TestMap07WallTypes` — 25 pure unit tests covering: movement adjacency (high/low/windowed wall block, legacy 2/3 passable), LOS (low wall transparent, windowed/high wall block), `compute_sight_lines` passable-origin filtering, `_can_tag_through_windowed_wall` N-S/E-W axis and unknown facing, `_get_los_targets` aperture hit and miss, proximity-based `_zone_from_cell` cases
- `TestMap07DBIntegration` — 2 DB tests: `wall_meta` round-trip through `save_zone_config` → `_resolve_map_data`, and `wall_meta` key present in `_build_movement_ctx` result