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

**`compute_spawn_cells(zone_grid, base_cells, max_distance=5)`** — MAP-08. Returns `{"red": [[r,c],...], "blue": [[r,c],...]}` — all passable floor cells (value 1) within Manhattan distance ≤ `max_distance` of each team's base cell. Called by `_update_spawn_cells_in_zone_data` after every sight-line save or compute; results are stored inline in `MapZoneConfig.zone_data` as `red_spawn`/`blue_spawn`.

## Map Editor UI (`templates/maps/map_editor.html`)

Three modes toggled in the top bar:

**Zones & Bases mode**: zone grid overlay on B&W processed image. Click base-type buttons (Red/Blue/Neutral 1–4) then click a cell to place. Clicking the same cell again removes it. Wall brush buttons (None, Low Wall, Windowed Wall, High Wall, Floor) let the user paint cell types 4/5/0/1 directly onto the grid; all wall brush buttons support bulk drag-select (click-drag draws a rectangle, releasing applies the wall type to all non-base cells in the region). When Windowed Wall is selected, a direction picker (N/S/E/W) sets the aperture facing stored in `wall_meta`. When High Wall is selected, a height input (default 1.0) sets the `height` value stored in `wall_meta[r,c]["height"]` for each painted cell. Spawn brush buttons (None, Red Spawn, Blue Spawn, Erase Spawn) overlay semi-transparent colored squares on spawn cells; activating a spawn brush dearms the wall and base brushes. Spawn cells auto-load from the server on zone-size selection and are included in the Save payload only when the user has manually edited them (`spawnEdited` flag). Elevation tools: the **Elevation Brush** lets the user enter a numeric elevation value and paint it onto individual cells (click or drag-select); the **Ramp Tool** lets the user click two cells to linearly interpolate elevation across all cells between them (both use the existing bulk drag-select pattern). "Save Configuration" POSTs `zone_size`, base pixel positions, the full `zones` grid, (when non-empty) `wall_meta` including any `height` entries, and (when non-empty) the `elevation` 2D float array; additionally sends `red_spawn`/`blue_spawn` arrays when user-edited. On zone-size change, `wallMeta`, `elevation`, and spawn cell sets are all reset with a console warning if unsaved placements are discarded.

**Sight Lines mode**:
- *Zone view*: click a cell (highlights yellow) to see its visible cells (green) and blocked cells (faint red). Click any cell to toggle its LOS link with the selected cell.
- *Drag-select bulk edit*: with a cell selected, click-drag to draw a rectangle — all non-wall cells in the rectangle highlight purple. Release to toggle all selected cells at once (bidirectional).
- *Base view* (dropdown): shows cells that can tag a specific base. Click to add/remove.
- "Compute Sight Lines" triggers full all-pairs server computation (~0.1–1s depending on zone size).
- "Save Sight Lines" batches the payload into chunks of 100 keys per POST to avoid the 2.5 MB Django request limit. First batch replaces, subsequent batches merge.

**Heatmap mode (RES-04)**: a third mode toggle alongside Zones & Bases and Sight Lines, driven by the `mode-heatmap` button. Activating Heatmap mode hides the Zones & Bases and Sight Lines control blocks, shows `#heatmap-controls` (a team-color filter dropdown `heatmap-editor-filter-team` with options Both/Red/Blue, plus a `heatmap-editor-round-count` text span), hides the zone-paint and sight-line overlays, and renders a translucent heatmap canvas over `#zone-img`. The JS fetches `GET /maps/<id>/heatmap-data/?zone_size=<currently-selected>&team_color=<filter>` (URL name `map_heatmap_data`, view `core/views.py::map_heatmap_data`), which aggregates `GameRound.cell_occupancy_json` across every round on this map at the selected zone_size — server-side filtering by `team_color` joins the per-player occupancy entries against `PlayerRoundState.team_color` and sums the matching cells (cells whose final sum is `0` are omitted). The round count from the response populates `#heatmap-editor-round-count`. The same canvas-overlay routine is reused by the per-round view at `/matches/game-round/<id>/heatmap/` (see [`matches/CLAUDE.md`](../matches/CLAUDE.md) — **RES-04 movement heatmap**); only the filter set differs (the editor exposes team color only, the round view exposes player/role/team).

## URLs

```
/maps/                              → map list + upload
/maps/<id>/editor/                  → map editor (zones, bases, sight lines)
/maps/<id>/delete/                  → POST: delete map (cascades configs; image + processed cache cleaned up; GameRound.arena_map SET_NULL)
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
/maps/<id>/heatmap-data/            → GET: RES-04 multi-round movement heatmap aggregate (URL name "map_heatmap_data"; required ?zone_size=<n>, optional &team_color=red|blue)
```

## Storage Backend (`core/views.py`)

**`_get_image_local_path(image_field: FieldFile) -> str`** — storage-agnostic helper used by every view that needs to pass an image path to OpenCV/PIL. For local `FileSystemStorage`, returns `image_field.path` directly. For remote backends (R2/S3), downloads the image to `MEDIA_ROOT/maps/_remote_cache/<filename>` on first access and returns that cached local path on subsequent calls. Map images are treated as immutable after upload so the cache never goes stale.

**`_seed_defaults()`** — skips seeding entirely when the active storage backend is not `FileSystemStorage` (i.e., R2 is configured). Default map images cannot be seeded from the local `Screenshots_and_video_examples/` directory in production.

**`upload_map` view** — reads image dimensions via `image_field.open("rb")` (works for both local and remote storage) instead of `image_field.path`. Handles corrupt/non-image uploads gracefully: deletes the partial record and redirects back to the map list rather than returning a 500.

**`save_zone_config` view** — accepts an optional `elevation` 2D float array from the client. Server-side validation rejects payloads where any elevation value is outside `[0.0, 10.0]` (returns HTTP 400). Elevation is carried forward from the existing confirmed config when not sent by the client, and persisted inline in `MapZoneConfig.zone_data` under the `"elevation"` key.

## Dependencies

`requirements.txt` includes `Pillow>=10.0.0` and `opencv-python-headless>=4.0.0` for image processing, and `django-storages[s3]>=1.14` + `boto3>=1.34` for Cloudflare R2 media storage.

## Tests

`core/tests.py`:
- `GetImageLocalPathTests` — local storage path passthrough, remote download-to-cache, cache reuse (no duplicate downloads)
- `SeedDefaultsTests` — skips seeding when non-`FileSystemStorage` is active
- `UploadMapViewTests` — dimensions stored correctly after upload; corrupt uploads rejected and not persisted
- `DeleteMapViewTests` — `delete_map` POST removes map + redirects to list, related configs cascade, GET → 405, missing map → 404, list page renders the Delete control

Map-processing tests for MAP-05/07 features live in `matches/tests/test_map.py` alongside the MAP-02–04 tests:
- `TestMap05ComputeHighLosRanking` — sort correctness (highest-LOS cell first), all cells returned, empty input returns empty
- `TestMap05StrongSpotsViews` — GET returns cells, returns `[]` when no config, POST persists cells, POST rejects non-list cells and non-int pairs, GET method not allowed on save endpoint
- `TestMap07WallTypes` — 25 pure unit tests covering: movement adjacency (high/low/windowed wall block, legacy 2/3 passable), LOS (low wall transparent, windowed/high wall block), `compute_sight_lines` passable-origin filtering, `_can_tag_through_windowed_wall` N-S/E-W axis and unknown facing, `_get_los_targets` aperture hit and miss, proximity-based `_zone_from_cell` cases
- `TestMap07DBIntegration` — 2 DB tests: `wall_meta` round-trip through `save_zone_config` → `_resolve_map_data`, and `wall_meta` key present in `_build_movement_ctx` result

MAP-09 tests live in `matches/tests/test_map09_high_ground.py`:
- `TestMap09ElevationStorage` (DB) — elevation 2D array and wall height round-trip through `MapZoneConfig.zone_data`; `_elevation_at` helper; `_resolve_map_data` returns `elevation_grid` at index 9
- `TestMap09WallHeight` (DB) — `wall_meta` height key persists; missing height key returns None (callers treat as impassable)
- `TestMap09ShootOver` (pure) — 8 tests for `can_shoot_over_wall`: threshold boundary (strict `>`), elevated wall base, short/zero-height walls
- `TestMap09LOSWithElevation` (pure) — 8 tests for `_has_los` with elevation: baseline blocking, flat elevation no help, elevated attacker opens LOS, insufficient elevation still blocked, windowed wall unaffected, missing height blocks, **asymmetric LOS** (`compute_sight_lines` gives A→B but not B→A when only A is elevated enough to shoot over the wall)
- `TestMap09HitChanceModifier` (pure) — 10 tests for `elevation_hit_modifier`: level/downhill=1.0, uphill 1/3/5/6/large units, fractional diff, multiplicative application
- `TestMap09BackwardsCompat` (pure) — 5 tests: `_elevation_at(None)`, empty dict, zero elevations, missing height, `_has_los` without new kwargs

## LG-01a landing view

Mode-picker landing page at `GET /` (replaces the previous `path("", include("teams.urls"))` homepage redirect to the team list). Three mode cards — **Sandbox** (links to `/teams/`), **Single-player League** (links to `/leagues/`), **Multiplayer** (greyed `Coming soon`, non-anchor) — plus an in-progress Leagues card grid populated from `League.objects.filter(state="active")`. The `/leagues/` index itself lives in [`matches/CLAUDE.md`](../matches/CLAUDE.md) **LG-01a leagues list**; this section covers only the landing view.

**URL.** Mounted in `laserforce_simulator/urls.py` as `path("", core_views.landing, name="landing")` (after adding `from core import views as core_views` to the project URLconf imports). The previous `path("", include("teams.urls"))` line is replaced; the `path("teams/", include("teams.urls"))` mount is untouched so `{% url 'team_list' %}` still reverses to `/teams/`. The HX-01 ordering comment on `path("players/", ...)` stays accurate — there is still a `path("", ...)` catch-all, it just now points at `core_views.landing`.

**View.** `core.views.landing(request) -> HttpResponse` — undecorated, GET-driven (no explicit method allowlist), placed at the bottom of `core/views.py` after `map_heatmap_data`. Body: **lazy-import `from matches.models import League` INSIDE the function body** (mirrors the existing `map_heatmap_data` lazy-import precedent in the same file — avoids the `core ↔ matches` apps-loading cycle), run **one ORM query** `in_progress_leagues = list(League.objects.filter(state="active").order_by("-id"))`, then `render("core/landing.html", {"in_progress_leagues": in_progress_leagues})`. Context (frozen): `{in_progress_leagues}`. **No `select_related` / `prefetch_related` on `League.active_season`** — it is a `@property` (not an FK), so per-card iteration issues one extra `Season.objects.exclude(state="completed").order_by("-id").first()` query; the LG-01a decision is to accept that cost (the landing list is user-bounded and the optimisation is non-breaking to add later via `Prefetch` if it ever bites).

**Template.** NEW `templates/core/landing.html` extends `base.html`, `{% block title %}Laserforce Manager{% endblock %}`. Locked DOM ids: `mode-picker` (outer wrapper section/div), `mode-card-sandbox` (anchor `<a>` reversing `{% url 'team_list' %}`, title text contains `Sandbox`), `mode-card-league` (anchor reversing `{% url 'league_list' %}`, title text contains `Single-player League`), `mode-card-multiplayer` (**non-anchor `<div>`** with `aria-disabled="true"` + a `<span class="badge bg-secondary">Coming soon</span>`, visually greyed via Bootstrap `opacity-50` or equivalent — must NOT be wrapped in `<a>`), `in-progress-leagues` (wrapping `<section>` rendered ONLY when `in_progress_leagues` is non-empty — the empty branch emits no notice, the substring `id="in-progress-leagues"` must NOT appear in the body when zero active Leagues exist), and `in-progress-league-card-{league.id}` (one anchor per active League, `href="/leagues/{{ league.id }}/"` — **raw URL string**, NOT `{% url ... %}`). Each in-progress card renders the League name, either `Season: {{ league.active_season.name }}` (when `league.active_season` is truthy) or the literal substring `No active season` (otherwise), and a state badge whose `class` contains `state-badge`. **Deferred broken-link decision (locked):** the per-card `/leagues/<id>/` hrefs are known-broken until LG-01c — the `league_detail` URL name doesn't yet exist; raw strings are deliberate.

**Navbar patch** (`templates/base.html`, two edits, no other nav-link touched). `navbar-brand` href: `{% url 'team_list' %}` → `{% url 'landing' %}` (the visible `⚡ Laserforce Manager` text + unicode `⚡` are unchanged). New `<a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>` inserted as the FIRST child of `<div class="navbar-nav ms-auto">` above the existing `Teams` link.

**Determinism / scope.** Read-only view — no writes, no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline. **No model change, no migration, no ADR, no CONTEXT.md edit, no new domain term** (League / Season / Standings already defined under `### League and seasons` from LG-01), no new aggregation module, no JS, no new dependency. **No change to `/teams/`, the `team_list` view, the `team_list` URL name, or any existing `{% url 'team_list' %}` reference** — removing the duplicate `path("", include("teams.urls"))` mount does not affect the standalone `path("teams/", include("teams.urls"))` mount. **Tests.** Extended `core/tests.py` with 12 view tests covering 200 status + `in_progress_leagues` context key, the three mode-card DOM ids, sandbox → `team_list` and league → `league_list` href resolution, the multiplayer-card non-anchor + `Coming soon` + `aria-disabled="true"` shape, omission of `in-progress-leagues` when no active Leagues, active-League cards sorted `-id` with both ids present, deferred `/leagues/<id>/` href per card, `Season: <name>` shown when `active_season` exists, `No active season` shown otherwise, archived Leagues excluded from the in-progress section, and `reverse("landing") == "/"`. The navbar regression test (`test_base_html_navbar_brand_links_to_landing_and_leagues_nav_link_present` — `leagues-nav-link` present + brand href `/`) lives in either `core/tests.py` or `matches/tests/test_league_list.py`.

**Locked names.** URL `GET /` (URL name `landing`); view `core.views.landing`; template `templates/core/landing.html`; DOM ids `mode-picker` / `mode-card-sandbox` / `mode-card-league` / `mode-card-multiplayer` / `in-progress-leagues` / `in-progress-league-card-{league.id}` / `leagues-nav-link`; context key `in_progress_leagues`. Seam contract: [`.claude/worktrees/lg-01a-seam-contract.md`](../../.claude/worktrees/lg-01a-seam-contract.md).
- `TestMap09ResolvesElevationFromZoneData` (DB) — `_resolve_map_data` returns correct elevation array; returns None when key absent