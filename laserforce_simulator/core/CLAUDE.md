# core/

The `core` app provides a 2D arena map importer and editor used to configure maps for match simulation.

## Models (`core/models.py`)

- **`ArenaMap`**: uploaded map image + pixel dimensions
- **`MapZoneConfig`**: 2D zone grid (`zones` 2D list: 0=wall, 1=floor, 2=red, 3=blue) + `blocked_edges_grid` (dict of edge blockages for sub-cell wall precision). One confirmed config per map per zone_size.
- **`MapBaseConfig`**: pixel-coordinate (x_px, y_px) of each base (red, blue, neutral_1–4). Zone-size independent.
- **`SightLineConfig`**: bidirectional adjacency dict `{"r,c": ["r,c", ...]}` for all non-wall cell pairs. Keyed per (map, zone_size).
- **`BaseSightLineConfig`**: list of cells `[[row, col], ...]` that can tag each base. Keyed per (map, base_type, zone_size). User-defined (bases sit on raised platforms).

## Map Processing (`core/map_processing.py`)

**`detect_zones(image_path, cell_size)`** — classifies each grid cell:
- Uses `create_processed_image()` internally to build a wall mask (CV threshold 210 + connected-component filtering discards text blobs, keeps large wall features)
- Cell is wall if ≥1% of pixels are dark in the wall mask; otherwise checks avg RGB for red/blue zone coloring, defaults to floor
- Returns `zones`, `blocked_edges` (dict), `blocked_edges_grid` (2D array)

**`create_processed_image(image_path)`** — returns a B&W PIL Image: threshold at 210, keep connected components with area ≥ 600 or max dimension ≥ 80px (walls), discard smaller (text). Cached to `media/maps/processed_<id>.png`.

**`_compute_blocked_edges(processed_bw, rows, cols, cell_size)`** — samples the pixel column/row at each cell boundary; marks the edge blocked if ≥30% of edge pixels are dark. Enables sub-cell wall precision for near-miss sight lines.

**`_has_los(zone_data, r1, c1, r2, c2, blocked_edges_grid)`** — Bresenham's line algorithm. Adjacent cells return immediately (checking only their shared edge). Longer paths walk the line and return False on the first wall cell or blocked edge encountered.

**`compute_sight_lines(zone_data, use_quadtree=True)`** — all-pairs LOS. Uses a `QuadtreeNode` spatial index when >50 passable cells: each cell only tests neighbors within `max(rows,cols)//4` radius (50–100× speedup over brute force). Falls back to O(n²) for small maps. Accepts both list and dict `zone_data` formats.

**`compute_single_cell_visibility(r, c, zone_data)`** — O(n) LOS from one cell. Used by the lazy editor endpoint for instant per-click feedback without precomputing all pairs.

## Map Editor UI (`templates/maps/map_editor.html`)

Two modes toggled in the top bar:

**Zones & Bases mode**: zone grid overlay on B&W processed image. Click base-type buttons (Red/Blue/Neutral 1–4) then click a cell to place. Clicking the same cell again removes it. "Save Configuration" POSTs zone_size + base pixel positions.

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