# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the `laserforce_simulator/` subdirectory (where `manage.py` lives):

```bash
# Development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Create new migrations after model changes
python manage.py makemigrations

# Run all tests with coverage
pytest

# Run a single test file
pytest matches/simulation_tests.py

# Run a specific test class or method
pytest matches/simulation_tests.py::ClassName::method_name
```

CI runs `pytest` with coverage and uploads to Codecov (see `.github/workflows/ci.yml`). Python version is 3.11.

## Test-Driven Development

This project follows TDD. Before implementing any new feature or fixing a bug:

1. **Write the test first.** Add a failing test that describes the expected behavior. Run it to confirm it fails for the right reason.
2. **Implement the minimum code** to make the test pass. Don't add more than needed.
3. **Refactor** if needed, keeping all tests green.

**Test placement:**
- `matches/simulation_tests.py` — simulator logic, game events, round outcomes
- `matches/tests.py` — match/round model behavior, views
- `teams/tests.py` — team/player model behavior, views
- `core/tests.py` — map processing, zone detection, sight line computation

**What to test:**
- Every new public function or method gets at least one test covering the happy path and one covering an edge case or failure mode.
- New Django views get tests for both success responses and invalid input.
- Bug fixes must include a regression test that would have caught the bug.

**Simulation tests** use fixed random seeds (`random.seed(42)`) or inject deterministic player stats to keep results reproducible — avoid asserting on exact point totals from unseeded runs.

**Do not** write tests that only verify mocks return what you told them to return. Prefer testing real behavior with lightweight in-memory objects or Django's `TestCase` with a test database.

## Architecture

This is a Django 5.2 app that simulates competitive laser tag (Laserforce) matches. The root URL serves the `teams` app as the homepage. There are three apps: `teams`, `matches`, and `core` (map editor).

### Data Model Hierarchy

```
Match (2 rounds, winner by rounds then points)
  └── GameRound (1 of the 2 rounds; 15-minute simulation)
        ├── PlayerRoundState (one per player, tracks all resources/stats)
        └── GameEvent (chronological log of every in-game action)
```

**Team/Player** (`teams/`): A `Team` has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role. Players have ~20 numeric stats (0–100) used as weights by the simulator.

**Match** (`matches/models.py`): Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**PlayerRoundState** (`matches/models.py`): Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores seconds into the round (901 = survived the full round). The MVP formula is role-specific and weighted heavily toward that role's primary contribution.

**GameEvent** (`matches/models.py`): Every action (tag, missile, special, miss, resupply, base capture, elimination) is logged here with an actor, optional target, timestamp in seconds, points, and a JSON `metadata` field.

### Simulation Engine (`matches/simulation.py`)

`ResourceBasedSimulator` is the active simulator. It runs in 2-second ticks over 900 seconds:

1. Process pending missiles/nukes that have completed their delay
2. Each active player picks an action (weighted random by role, zone context, remaining resources)
3. Resolve the action — update `PlayerRoundState` fields and write a `GameEvent`
4. Check for eliminations after each tick
5. Return aggregated round results

Action weights are in `matches/sim_helpers/weights.py` — separate functions per role (`_get_medic_weights`, `_get_heavy_weights`, etc.) that return a dict of action → weight. Weights shift based on remaining lives, special charges, zone, and allied presence.

`SimpleMatchSimulator` is an older, simpler fallback; prefer `ResourceBasedSimulator` for new work.

### Role Mechanics

| Role | Shields/Shot Power | Has Missiles | Can Resupply |
|------|-------------------|--------------|--------------|
| Commander | 2 / 3 | Yes | No |
| Heavy | 3 / 3 | Yes | No |
| Scout | 1 / 1 | No | No |
| Medic | 1 / 1 | No | Yes (lives) |
| Ammo | 1 / 1 | No | Yes (shots) |

Shields absorb damage; depleting shields at 0 lives causes elimination. Respawn after being tagged requires an 8-second cooldown. Zone values: 0 = red_zone, 1 = neutral_zone, 2 = blue_zone.

### URL Structure

```
/                    → team list (homepage)
/teams/              → team CRUD, player management
/matches/            → match list, create, detail
/matches/game-round/<id>/        → detailed round view
/matches/game-round/<id>/events/ → event timeline/filtering
/matches/team/<id>/history/      → team win/loss history
/maps/               → map list + upload
/maps/<id>/editor/   → map editor (zones, bases, sight lines)
/maps/<id>/zones/    → AJAX: zone detection for given zone_size
/maps/<id>/processed-image/ → B&W cached map image
/maps/<id>/save/     → POST: save zone config + base positions
/maps/<id>/sight-lines/           → GET: load existing sight line data
/maps/<id>/sight-lines/compute/   → POST: run full all-pairs LOS computation
/maps/<id>/sight-lines/single-cell/ → GET: lazy single-cell LOS (?zone_size=&r=&c=)
/maps/<id>/sight-lines/save/      → POST: save sight lines (batched)
```

### Templates

All templates live in `laserforce_simulator/templates/`. The `game_round_events.html` template has event filtering and color-coded display; `game_round_detail.html` shows per-player stats and MVP scores. Map editor UI lives in `templates/maps/map_editor.html`.

---

## Core App — Map Editor (`core/`)

The `core` app provides a 2D arena map importer and editor used to configure maps for match simulation.

### Models (`core/models.py`)

- **`ArenaMap`**: uploaded map image + pixel dimensions
- **`MapZoneConfig`**: 2D zone grid (`zones` 2D list: 0=wall, 1=floor, 2=red, 3=blue) + `blocked_edges_grid` (dict of edge blockages for sub-cell wall precision). One confirmed config per map per zone_size.
- **`MapBaseConfig`**: pixel-coordinate (x_px, y_px) of each base (red, blue, neutral_1–4). Zone-size independent.
- **`SightLineConfig`**: bidirectional adjacency dict `{"r,c": ["r,c", ...]}` for all non-wall cell pairs. Keyed per (map, zone_size).
- **`BaseSightLineConfig`**: list of cells `[[row, col], ...]` that can tag each base. Keyed per (map, base_type, zone_size). User-defined (bases sit on raised platforms).

### Map Processing (`core/map_processing.py`)

**`detect_zones(image_path, cell_size)`** — classifies each grid cell:
- Uses `create_processed_image()` internally to build a wall mask (CV threshold 210 + connected-component filtering discards text blobs, keeps large wall features)
- Cell is wall if ≥1% of pixels are dark in the wall mask; otherwise checks avg RGB for red/blue zone coloring, defaults to floor
- Returns `zones`, `blocked_edges` (dict), `blocked_edges_grid` (2D array)

**`create_processed_image(image_path)`** — returns a B&W PIL Image: threshold at 210, keep connected components with area ≥ 600 or max dimension ≥ 80px (walls), discard smaller (text). Cached to `media/maps/processed_<id>.png`.

**`_compute_blocked_edges(processed_bw, rows, cols, cell_size)`** — samples the pixel column/row at each cell boundary; marks the edge blocked if ≥30% of edge pixels are dark. Enables sub-cell wall precision for near-miss sight lines.

**`_has_los(zone_data, r1, c1, r2, c2, blocked_edges_grid)`** — Bresenham's line algorithm. Adjacent cells return immediately (checking only their shared edge). Longer paths walk the line and return False on the first wall cell or blocked edge encountered.

**`compute_sight_lines(zone_data, use_quadtree=True)`** — all-pairs LOS. Uses a `QuadtreeNode` spatial index when >50 passable cells: each cell only tests neighbors within `max(rows,cols)//4` radius (50–100× speedup over brute force). Falls back to O(n²) for small maps. Accepts both list and dict `zone_data` formats.

**`compute_single_cell_visibility(r, c, zone_data)`** — O(n) LOS from one cell. Used by the lazy editor endpoint for instant per-click feedback without precomputing all pairs.

### Map Editor UI (`templates/maps/map_editor.html`)

Two modes toggled in the top bar:

**Zones & Bases mode**: zone grid overlay on B&W processed image. Click base-type buttons (Red/Blue/Neutral 1–4) then click a cell to place. Clicking the same cell again removes it. "Save Configuration" POSTs zone_size + base pixel positions.

**Sight Lines mode**:
- *Zone view*: click a cell (highlights yellow) to see its visible cells (green) and blocked cells (faint red). Click any cell to toggle its LOS link with the selected cell.
- *Drag-select bulk edit*: with a cell selected, click-drag to draw a rectangle — all non-wall cells in the rectangle highlight purple. Release to toggle all selected cells at once (bidirectional).
- *Base view* (dropdown): shows cells that can tag a specific base. Click to add/remove.
- "Compute Sight Lines" triggers full all-pairs server computation (~0.1–1s depending on zone size).
- "Save Sight Lines" batches the payload into chunks of 100 keys per POST to avoid the 2.5 MB Django request limit. First batch replaces, subsequent batches merge.

### Dependencies

`requirements.txt` includes `Pillow>=10.0.0` and `opencv-python-headless>=4.0.0` for image processing.