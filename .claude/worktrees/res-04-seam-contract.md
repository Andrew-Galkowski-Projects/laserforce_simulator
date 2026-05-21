# RES-04 Seam Contract — Movement Heatmap / Cell Occupancy

Branch: `res-04-heatmap` (cut from `main`).
Scope locked by the grilling session. No re-litigation; if a fact below is
wrong, surface it to the orchestrator before writing code.

This document is the **single source of truth** that the four parallel agents
(code, tests, docs, frontend/template) all read. Every name, signature, JSON
key, DOM id, URL name, and migration filename below is **canonical**.

---

## 1. `GameRound.cell_occupancy_json` field

### Migration

- **Filename:** `laserforce_simulator/matches/migrations/0026_gameround_cell_occupancy_json.py`
  (verified: the latest existing migration is `0025_alter_gameevent_event_type.py`).
- **Operation:** single `migrations.AddField`.
- **Dependency:** `("matches", "0025_alter_gameevent_event_type")`.
- **Field definition** (on `matches.GameRound`):

  ```python
  cell_occupancy_json = models.JSONField(null=True, blank=True, default=None)
  ```

  - `null=True` + `blank=True` + `default=None`.
  - **No backfill** — pre-RES-04 rows stay `NULL`. Mirrors the
    `GameRound.rng_seed` ADR-0004 precedent.

### JSON shape on disk

The top-level value is a JSON **object** keyed by **stringified player IDs**.
Each value is itself a JSON **object** keyed by **`"r,c"` cell strings** whose
values are **integer tick counts**. Both key types are **strings** (JSON
object keys must be strings under `json` round-trip, so this avoids the
"silently coerced from int to str" footgun on the read side).

```json
{
  "101": {
    "12,7":  18,
    "12,8":  42,
    "13,8":  61
  },
  "102": {
    "15,3":  9,
    "15,4":  37,
    "16,4":  120
  },
  "103": {
    "20,11": 200,
    "21,11": 80,
    "21,12": 14
  }
}
```

### Key/value type lock

| Slot                       | Type     | Rationale                                                                                            |
| -------------------------- | -------- | ---------------------------------------------------------------------------------------------------- |
| Outer key (`player_id`)    | `str`    | JSON object keys are strings under `json.dump`/`json.load` round-trip — codify it.                   |
| Inner key (`cell`)         | `str`    | `"r,c"` comma-string. Same convention as `sight_data` keys (`f"{r},{c}"`).                           |
| Inner value (`tick count`) | `int`    | Final stored unit is integer ticks. Even-split apportionment yields fractional ticks during reconstruction (see §2). |

### Rounding rule (load-bearing)

During reconstruction the per-cell accumulator is a **float**. After the full
trail has been walked, **each cell value is rounded with Python `round()`**
("banker's rounding" — `round(2.5) == 2`, `round(3.5) == 4`) and cast to
`int`. Cells whose final accumulator rounds to `0` are **omitted** from the
output dict (a cell credited 0.4 ticks does not appear). This produces a
sparse JSON object — `{}` is a valid value for a player who never moved off
their spawn and was eliminated at tick 0.

The sum-over-cells of integer values may deviate from the float total by at
most `len(cells_touched) / 2` ticks (rounding slack). The pure-unit test
suite asserts only an inequality; never an exact total (see §7).

---

## 2. `reconstruct_cell_occupancy` free function

### Module path (new file)

```
laserforce_simulator/matches/sim_helpers/cell_occupancy.py
```

Pure Python. **No Django imports.** Easy unit testing (the test file imports
only this function plus the stdlib).

### Exact signature

```python
from __future__ import annotations

from typing import Optional


def reconstruct_cell_occupancy(
    movement_trail: list[tuple[tuple[int, int], tuple[int, int], int]],
    spawn_cell: tuple[int, int],
    round_ticks: int,
    eliminated_at: int,
    adj: dict[tuple[int, int], list[tuple[int, int]]],
    elevation_data: Optional[dict[tuple[int, int], float]] = None,
) -> dict[tuple[int, int], int]:
    ...
```

The parameter names are **canonical** — the test agent and the
`_flush_to_db` integration agent must use these exact names (kwargs).

### Parameter semantics

| Param            | Type                                                          | Meaning                                                                                                                                       |
| ---------------- | ------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------- |
| `movement_trail` | `list[tuple[(int,int), (int,int), int]]`                      | The player's `PlayerState.movement_trail` — ordered list of `(start_cell, end_cell, ts)` Advance entries. `ts` is **tick units** (TIME-01). Entries are already ordered by `ts` ascending. |
| `spawn_cell`     | `tuple[int, int]`                                             | Cell the player occupies before the first Advance event. Caller (`_flush_to_db`) sources this from `movement_trail[0][0]` when the trail is non-empty, else `(player.cell_row, player.cell_col)`. |
| `round_ticks`    | `int`                                                         | Round length in ticks. Pass `TICKS_PER_ROUND` (1800) from `matches.sim_helpers.time_constants`.                                               |
| `eliminated_at`  | `int`                                                         | Tick of final elimination. Pass `SURVIVED_SENTINEL` (1801) for players who survived. Function will not credit ticks past `min(round_ticks, eliminated_at)`. |
| `adj`            | `dict[tuple[int,int], list[tuple[int,int]]]`                  | Movement adjacency from `pathfinding.build_movement_adjacency(zone_data)`. Used by `astar_path` to expand a multi-cell Advance.               |
| `elevation_data` | `Optional[dict[tuple[int,int], float]]`                       | Optional elevation lookup (matches `pathfinding.astar_path`'s 4th kwarg). Pass `None` to use flat-cost A*.                                    |

### Input invariants (function may assume; do not validate)

- `movement_trail` is sorted by `ts` ascending.
- For each entry `(start, end, ts)`, `0 <= ts <= round_ticks`.
- For `i >= 1`, `movement_trail[i][0] == movement_trail[i-1][1]` (chain).
- `eliminated_at >= 0` and `eliminated_at <= round_ticks + 1` (1801 = sentinel).
- The function **does not import Django**, **does not perform any I/O**, and **consumes no RNG**.

### Algorithm (load-bearing)

Walk the trail. Maintain:

- `accum: dict[tuple[int,int], float]` — per-cell float accumulator.
- `cursor_cell: tuple[int,int]` — current player cell; starts at `spawn_cell`.
- `cursor_tick: int` — last tick whose credit has been applied; starts at `0`.
- `end_tick: int = min(round_ticks, eliminated_at)` — never credit beyond this.

For each `(start_cell, end_cell, ts)` in `movement_trail`:

1. **Stationary slice** `[cursor_tick, ts)` — i.e. the `ts - cursor_tick`
   ticks the player rested on `cursor_cell` (which equals `start_cell` by the
   chain invariant) before this Advance. Credit
   `accum[cursor_cell] += max(0, min(ts, end_tick) - cursor_tick)`. If
   `ts >= end_tick`, return after this step.
2. **Advance slice** at `ts` — `1` tick consumed by the Advance itself.
   Expand the route via
   `route_cells = astar_path(start_cell, end_cell, adj, elevation_data)`
   (which returns the ordered list **excluding** `start_cell`, **including**
   `end_cell`). Let `N = len(route_cells) + 1` (the `+1` is `start_cell` —
   the route's "from" end), so the route walked this Advance is
   `[start_cell, *route_cells]` of length `N`. Apportion the **1 tick** as
   `1 / N` per cell across that route. If `astar_path` returns `[]` (start
   == goal or unreachable), credit `1 / 1 = 1.0` to `cursor_cell` and skip
   the expansion (defensive — should not occur given the chain invariant).
3. Advance the cursor: `cursor_cell = end_cell`, `cursor_tick = ts + 1`.
4. If `cursor_tick >= end_tick`, stop.

After the loop, credit the **trailing stationary slice**
`accum[cursor_cell] += max(0, end_tick - cursor_tick)`.

### Output shape

`dict[tuple[int, int], int]` — **tuple keys** (row, col), **int values**
post-rounding. Cells whose final value rounds to `0` are **omitted**.

The caller (`_flush_to_db`) is responsible for converting:

- tuple keys `(r, c)` → `"r,c"` strings;
- `player_id: int` → `str(player_id)`.

`reconstruct_cell_occupancy` does **not** do that conversion — it stays in
the pure-Python tuple/int domain so it is testable without JSON round-trip.

### Edge cases (function must handle correctly; test coverage in §7)

- **Empty `movement_trail`** + `eliminated_at == round_ticks` → spawn cell
  gets all `round_ticks` ticks → `{spawn_cell: round_ticks}` (after rounding,
  which is exact since the value is integer).
- **Empty `movement_trail`** + `eliminated_at == 0` → empty dict `{}`.
- **Single 1-cell Advance** at `ts` (so the route is `[end_cell]`, `N = 2`):
  the 1 tick splits **0.5** to `start_cell` and **0.5** to `end_cell`. After
  rounding (banker's), each contributes `0` to its cell's running total —
  but the pre-Advance stationary credit on `start_cell` and the post-Advance
  stationary credit on `end_cell` dominate, so the integer output still
  reflects the (start_cell, end_cell) occupancy correctly.
- **Multi-cell Advance** (route length 3, so `N = 4`): each of the 4 cells
  walked gets `0.25` tick credit.
- **Post-elimination cutoff** (`eliminated_at < round_ticks`): no credit
  past `eliminated_at`.
- **Map-less rounds**: **the function is not called.** That is
  `_flush_to_db`'s responsibility (gated on `movement_ctx is not None`).
  Document this here so the code agent knows it does not need to handle
  `adj is None` or `adj == {}` — the contract is "you only call me when a
  map is active."

---

## 3. `_flush_to_db` integration

### File / location

`laserforce_simulator/matches/simulation.py`, method
`BatchSimulator._flush_to_db` (currently spanning roughly L2633–L2792 in the
working tree).

### Insertion point

**Immediately after** the existing per-player movement-event flush block
(the `for p in red_players + blue_players: for start_cell, end_cell, ts in
p.movement_trail:` loop that creates `GameEvent(event_type="movement", ...)`
rows — currently ends around L2790), **before** the final `return game_round`.

### Behaviour

```python
# RES-04: cell-occupancy snapshot. Only populated when a map is active
# (movement_ctx is not None); map-less rounds leave cell_occupancy_json
# null. The map-active gate is required because reconstruct_cell_occupancy
# needs an A* adjacency dict.
if movement_ctx is not None:
    from matches.sim_helpers.cell_occupancy import reconstruct_cell_occupancy
    from matches.sim_helpers.time_constants import TICKS_PER_ROUND

    adj = movement_ctx.get_adjacency()
    elevation_data = movement_ctx.elevation_grid  # may be None — that's fine

    occupancy_json: dict[str, dict[str, int]] = {}
    for p in red_players + blue_players:
        if not p.player_id:
            continue
        spawn_cell = (
            p.movement_trail[0][0]
            if p.movement_trail
            else (p.cell_row, p.cell_col)
        )
        # Skip players who never had a cell position (no map, edge case).
        if spawn_cell[0] is None or spawn_cell[1] is None:
            continue

        per_cell = reconstruct_cell_occupancy(
            movement_trail=p.movement_trail,
            spawn_cell=spawn_cell,
            round_ticks=TICKS_PER_ROUND,
            eliminated_at=p.was_eliminated_at,
            adj=adj,
            elevation_data=elevation_data,
        )

        occupancy_json[str(p.player_id)] = {
            f"{r},{c}": ticks for (r, c), ticks in per_cell.items()
        }

    game_round.cell_occupancy_json = occupancy_json
    game_round.save(update_fields=["cell_occupancy_json"])
```

### Notes for the code agent

- `movement_ctx` is the **already-existing kwarg** on `_flush_to_db`
  (SIM-09). Re-use it; do **not** add a new kwarg.
- `movement_ctx.get_adjacency()` and `movement_ctx.elevation_grid` are
  documented on `MapContext` (see `sim_helpers/map_context.py`). No new
  accessor is required.
- The `update_fields=["cell_occupancy_json"]` save is a **second** save on
  the same `game_round` (the earlier `game_round.save()` in `_flush_to_db`
  triggers winner calculation). The second save is intentional and cheap.
- `p.movement_trail[0][0]` is the **start cell** of the first Advance —
  i.e. the player's spawn cell. The trail invariant guarantees this is the
  cell the player occupied before any movement. Fallback to
  `(p.cell_row, p.cell_col)` only when the trail is empty (player never
  moved).
- `p.player_id` may be `None` (defensive). Skip those rows — they would
  also be skipped by the existing event-flush block above.

---

## 4. Views

### 4.1. `matches/views.py::movement_heatmap`

```python
def movement_heatmap(request, round_id: int):
    """Render the per-round movement heatmap page (RES-04)."""
```

- **URL:** `path("game-round/<int:round_id>/heatmap/", views.movement_heatmap, name="movement_heatmap")`
  (registered in `matches/urls.py` alongside `missile_log`).
- **Template:** `templates/matches/movement_heatmap.html`.
- **Method:** GET only. Non-GET → `HttpResponseNotAllowed(["GET"])` (405).
- **404:** Use `get_object_or_404(GameRound, pk=round_id)`.

#### Context keys (lock these names — the template and JS read them)

| Key                    | Type                                  | Notes                                                                                                  |
| ---------------------- | ------------------------------------- | ------------------------------------------------------------------------------------------------------ |
| `game_round`           | `GameRound`                           | The looked-up round.                                                                                   |
| `cell_occupancy_json`  | `dict` (raw — passed through)         | `game_round.cell_occupancy_json or {}`. Rendered via `{{ ... \|json_script:"cell-occupancy-data" }}`.  |
| `player_roster`        | `list[dict]`                          | One entry per `PlayerRoundState` on the round (both teams). See shape below.                           |
| `has_map`              | `bool`                                | `game_round.arena_map_id is not None`.                                                                 |
| `arena_map`            | `ArenaMap \| None`                    | `game_round.arena_map`.                                                                                |
| `zone_size`            | `int \| None`                         | `game_round.zone_size`.                                                                                |
| `processed_image_url`  | `str \| None`                         | When `has_map`: `reverse("processed_image", args=[arena_map.pk])`. Else `None`.                        |

#### `player_roster` shape (lock these keys)

```python
[
    {
        "id":         101,        # PlayerRoundState.player_id (int)
        "name":       "Alice",    # PlayerRoundState.player.name
        "role":       "scout",    # PlayerRoundState.role
        "team_color": "red",      # PlayerRoundState.team_color ("red" | "blue")
    },
    ...
]
```

Order: red team first, then blue, each ordered by role then name (stable —
the JS dropdown relies on it).

#### Map-less rendering

When `has_map is False`, the template renders the
**"No map — heatmap unavailable"** notice (see §5). No bar-chart fallback,
no aggregation. The context still includes `cell_occupancy_json` (will be
`{}`), `player_roster` (still rendered for completeness), `has_map=False`,
`processed_image_url=None`.

### 4.2. `core/views.py::map_heatmap_data`

```python
def map_heatmap_data(request, map_id: int):
    """Aggregate cell occupancy across all GameRounds for a map (RES-04)."""
```

- **URL:** `path("<int:map_id>/heatmap-data/", views.map_heatmap_data, name="map_heatmap_data")`
  (registered in `core/urls.py`).
- **Method:** GET only. Non-GET → `HttpResponseNotAllowed(["GET"])` (405).
- **404:** `get_object_or_404(ArenaMap, pk=map_id)`.
- **Required query param:** `zone_size` (int). Missing or non-int →
  `HttpResponseBadRequest` (400) with body `"zone_size required"`.
- **Optional query param:** `team_color` — `"red"` or `"blue"`. Any other
  non-empty value → 400 `"invalid team_color"`. Absent → both teams.

#### Aggregation logic

1. Query `GameRound.objects.filter(arena_map_id=map_id, zone_size=zone_size, cell_occupancy_json__isnull=False)`.
2. For each round, look up `PlayerRoundState` rows (FK `game_round`) to map
   `player_id (int)` → `team_color`.
3. Walk each round's `cell_occupancy_json`. For each player_id key, if
   `team_color` filter is set, drop entries whose player's `team_color` does
   not match. Sum the remaining `"r,c"` → ticks entries into the response
   accumulator.

#### Response shape (lock these keys)

```json
{
  "cell_occupancy": {
    "0,0":  3,
    "0,1":  47,
    ...
  },
  "zone_size":   24,
  "rows":        40,
  "cols":        60,
  "round_count": 12
}
```

- `cell_occupancy` — `dict[str, int]` keyed by `"r,c"`, values are summed
  tick counts. Cells with a final sum of `0` are omitted (matches the
  per-round file format).
- `zone_size` — echo of the query param (int).
- `rows` / `cols` — pulled from `MapZoneConfig` (the confirmed config for
  this `(map, zone_size)`). When no `MapZoneConfig` exists, return
  `rows=0, cols=0` (the JS will degrade gracefully).
- `round_count` — number of `GameRound` rows that contributed.

### 4.3. Filter strategy (single-round view)

**Client-side.** The full per-player JSON is rendered into the page via
`{{ cell_occupancy_json|json_script:"cell-occupancy-data" }}`. A small JS
shim sums per-cell across the players the dropdowns select, then re-paints
the canvas. **No server round-trip per filter change** — avoids latency and
keeps the seam narrow.

---

## 5. Templates / Frontend

### 5.1. New: `templates/matches/movement_heatmap.html`

The contract pins **DOM IDs, JSON script ids, and overall structure**. The
JS implementation is the frontend agent's job, but it must hook these IDs
exactly.

Wireframe (no JS; the agent writes the JS):

```django
{% extends "base.html" %}
{% load static %}

{% block title %}Movement Heatmap - Round {{ game_round.id }}{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Movement Heatmap</h1>
    <p>
        <a href="{% url 'game_round_detail' round_id=game_round.id %}">Round detail</a>
        |
        <a href="{% url 'game_round_events' round_id=game_round.id %}">All events</a>
        |
        <a href="{% url 'missile_log' round_id=game_round.id %}">Missile log</a>
    </p>

    {% if not has_map %}
        <div class="alert alert-warning" id="heatmap-no-map-notice">
            No map &mdash; heatmap unavailable.
        </div>
    {% else %}
        {# Filter row #}
        <div class="row mb-3" id="heatmap-filter-row">
            <div class="col-md-4">
                <label for="heatmap-filter-player">Player</label>
                <select id="heatmap-filter-player" class="form-select">
                    <option value="">All players</option>
                    {% for p in player_roster %}
                        <option value="{{ p.id }}">{{ p.name }} ({{ p.team_color }} {{ p.role }})</option>
                    {% endfor %}
                </select>
            </div>
            <div class="col-md-4">
                <label for="heatmap-filter-role">Role</label>
                <select id="heatmap-filter-role" class="form-select">
                    <option value="">All roles</option>
                    <option value="commander">Commander</option>
                    <option value="heavy">Heavy</option>
                    <option value="scout">Scout</option>
                    <option value="medic">Medic</option>
                    <option value="ammo">Ammo</option>
                </select>
            </div>
            <div class="col-md-4">
                <label for="heatmap-filter-team">Team color</label>
                <select id="heatmap-filter-team" class="form-select">
                    <option value="">Both</option>
                    <option value="red">Red</option>
                    <option value="blue">Blue</option>
                </select>
            </div>
        </div>

        {# Canvas overlay on processed map image #}
        <div id="heatmap-stage" style="position: relative; display: inline-block;">
            <img id="heatmap-bg" src="{{ processed_image_url }}"
                 alt="Map background" style="display: block;">
            <canvas id="heatmap-canvas"
                    style="position: absolute; top: 0; left: 0; pointer-events: none;">
            </canvas>
        </div>

        {{ cell_occupancy_json|json_script:"cell-occupancy-data" }}
        {{ player_roster|json_script:"player-roster-data" }}
        <script>window.LF_ZONE_SIZE = {{ zone_size|default:"null" }};</script>
    {% endif %}
</div>
{% endblock %}
```

#### Locked DOM IDs (do not rename)

| Element                                  | ID                            |
| ---------------------------------------- | ----------------------------- |
| Canvas overlay                           | `heatmap-canvas`              |
| Background image                         | `heatmap-bg`                  |
| Stage wrapper (positioning anchor)       | `heatmap-stage`               |
| Player filter dropdown                   | `heatmap-filter-player`       |
| Role filter dropdown                     | `heatmap-filter-role`         |
| Team-color filter dropdown               | `heatmap-filter-team`         |
| Filter row (container)                   | `heatmap-filter-row`          |
| Missing-map notice                       | `heatmap-no-map-notice`       |
| Per-cell occupancy data (json_script id) | `cell-occupancy-data`         |
| Player roster data (json_script id)      | `player-roster-data`          |

`window.LF_ZONE_SIZE` is the **canonical** global for the JS to read the
zone size from. The frontend agent uses `<option value="">` (empty string)
for the "All / Both" sentinel.

### 5.2. `templates/maps/map_editor.html` — Heatmap mode

The editor already has two mode buttons:
- `<button id="mode-zones">Zones & Bases</button>` (active by default)
- `<button id="mode-sight">Sight Lines</button>`

Add a **third** mode button **immediately after** `mode-sight`:

```django
<button id="mode-heatmap" class="btn btn-secondary">Heatmap</button>
```

#### Heatmap-mode controls block

Add a new controls block (mirroring the existing Zones & Bases and Sight
Lines blocks):

```django
{# Heatmap mode controls #}
<div id="heatmap-controls" style="display: none;">
    <label for="heatmap-editor-filter-team" class="me-2">Team:</label>
    <select id="heatmap-editor-filter-team">
        <option value="">Both</option>
        <option value="red">Red</option>
        <option value="blue">Blue</option>
    </select>
    <span id="heatmap-editor-round-count" class="ms-3 text-muted">
        rounds aggregated: 0
    </span>
</div>
```

#### Heatmap-mode behaviour (JS, owned by the frontend agent)

When the `mode-heatmap` button is clicked:

1. Hide `#zones-controls` and `#sight-controls`; show `#heatmap-controls`.
2. Toggle button `active` classes (mirrors the existing
   `mode-zones`/`mode-sight` pattern at L678–L697 in the template).
3. Hide all map-editor editing overlays (zone-paint canvas / sight-line
   canvas) and render the heatmap canvas overlay on `#zone-img`.
4. Fetch `GET /maps/<map_id>/heatmap-data/?zone_size=<currently-selected>&team_color=<filter>`.
5. Render via the **same** canvas-overlay routine as the per-round view
   (factor into shared JS — frontend agent's call where to put it; one
   suggestion is `static/js/heatmap_overlay.js`, but the contract does
   **not** mandate a path).
6. Update `#heatmap-editor-round-count` with `data.round_count`.

#### Locked map-editor DOM IDs

| Element                                  | ID                                   |
| ---------------------------------------- | ------------------------------------ |
| Mode button (Heatmap)                    | `mode-heatmap`                       |
| Heatmap-mode controls wrapper            | `heatmap-controls`                   |
| Team-color filter (editor)               | `heatmap-editor-filter-team`         |
| Round-count display                      | `heatmap-editor-round-count`         |

### 5.3. `templates/matches/game_round_detail.html` — link

Add a link to the round heatmap from the round detail page, **next to
existing event-related links** (the precise location is the agent's call —
the simplest is alongside the events link, mirroring the missile-log link
pattern that already lives there in spirit):

```django
<a href="{% url 'movement_heatmap' round_id=game_round.id %}">Movement heatmap</a>
```

If `game_round.arena_map_id is None`, the link still renders and the target
view will display the "No map" notice — server-side behaviour, not template
behaviour, so the template does not gate on `has_map`.

---

## 6. URL registration

### `matches/urls.py`

Add **after** the existing `missile_log` route:

```python
path(
    "game-round/<int:round_id>/heatmap/",
    views.movement_heatmap,
    name="movement_heatmap",
),
```

### `core/urls.py`

Add **after** the existing `spawn_cells` route:

```python
path(
    "<int:map_id>/heatmap-data/",
    views.map_heatmap_data,
    name="map_heatmap_data",
),
```

---

## 7. Test boundary

### 7.1. `matches/tests/test_res04_cell_occupancy.py` — pure unit

Imports only `reconstruct_cell_occupancy` (and stdlib). **No Django
ORM**, **no DB**.

Required test cases (each gets its own method; class
`TestReconstructCellOccupancy`):

1. **`test_empty_trail_survived_credits_spawn`** — `movement_trail=[]`,
   `round_ticks=1800`, `eliminated_at=1801` → `{spawn_cell: 1800}`.
2. **`test_empty_trail_eliminated_at_zero_yields_empty`** —
   `movement_trail=[]`, `eliminated_at=0` → `{}` (no credit).
3. **`test_single_one_cell_advance`** — Trail `[((0,0), (0,1), 10)]`,
   `round_ticks=20`, `eliminated_at=1801`. Pre-Advance stationary
   `0..10` = 10 ticks on `(0,0)`. Advance at `ts=10` splits `1/2` to
   each of `(0,0)` and `(0,1)` → `0.5` each. Post-Advance stationary
   `11..20` = 9 ticks on `(0,1)`. Float totals: `(0,0) = 10.5`,
   `(0,1) = 9.5`. After banker-rounded `round()` (ties-to-even):
   `(0,0) → 10`, `(0,1) → 10`. **Test asserts that result.**
4. **`test_multi_cell_advance_apportions_evenly`** — Trail
   `[((0,0), (0,3), 0)]` with `adj` set up to route along
   `(0,0) → (0,1) → (0,2) → (0,3)` (4 cells walked, `N=4`),
   `round_ticks=1`, `eliminated_at=1801`. Each cell gets `0.25`. After
   rounding, all 4 round to `0` and the output is `{}`. **Test asserts
   the dict is empty.** (This is the "fractional credit, all swept by
   rounding" boundary — locked behaviour.)
5. **`test_multi_cell_advance_with_long_run`** — Same multi-cell route
   as above but the player rests on `(0,3)` for many ticks afterward
   (e.g. `round_ticks=100`, `eliminated_at=1801`). Asserts the four
   route cells appear in the output exactly when their accumulated
   float rounds to ≥ 1, and that `(0,3)`'s integer value is roughly
   `99 + 0.25 ≈ 99` post-rounding.
6. **`test_stationary_between_two_advances`** — Trail
   `[((0,0), (0,1), 5), ((0,1), (0,2), 15)]`, `round_ticks=20`,
   `eliminated_at=1801`. Asserts `(0,1)` accumulates the 9 ticks
   between `ts=6` and `ts=14` inclusive (i.e. the long rest in the
   middle is credited to the in-between cell).
7. **`test_post_elimination_cutoff`** —
   `eliminated_at=50`, `round_ticks=1800`, trail covers ticks 60–70.
   Asserts no credit accumulates from `ts >= 50`. With a stationary
   trail starting at tick 0, total credit equals 50.
8. **`test_sum_reconciliation_within_rounding_slack`** — For a
   realistic trail (built deterministically inside the test), assert
   `sum(result.values()) <= min(round_ticks, eliminated_at)` and that
   the absolute difference is `<= len(result)` (rounding slack
   bound).
9. **`test_pure_no_django_imports`** — `import matches.sim_helpers.cell_occupancy as m`,
   then assert no attribute named `models` or `django` leaked into its
   module dict (defensive; pins the "pure function" contract).

### 7.2. `matches/tests/test_res04_heatmap_view.py` — DB/view

Uses Django `TestCase`. Required cases (each its own method):

1. **`test_round_heatmap_view_200`** — Create a `GameRound` with
   `arena_map` set and a small synthetic `cell_occupancy_json`. GET the
   `movement_heatmap` URL → `200`, template
   `matches/movement_heatmap.html` rendered. Asserts the three filter
   dropdowns and the canvas tag appear in the response body
   (`heatmap-filter-player`, `heatmap-filter-role`,
   `heatmap-filter-team`, `heatmap-canvas`).
2. **`test_round_heatmap_view_missing_map_notice`** — `GameRound` with
   `arena_map=None`. GET → `200`. Asserts response contains the
   `heatmap-no-map-notice` ID and the literal string
   `"No map &mdash; heatmap unavailable."` (or equivalent post-escape
   — `"No map"` substring is sufficient).
3. **`test_round_heatmap_view_404_for_missing_round`** — GET on a
   bogus PK → `404`.
4. **`test_round_heatmap_view_405_non_get`** — POST → `405`.
5. **`test_map_heatmap_data_endpoint_returns_merged`** — Create two
   rounds on the same map / zone_size, both with non-null
   `cell_occupancy_json`. GET
   `/maps/<id>/heatmap-data/?zone_size=<n>` → `200`,
   `data["round_count"] == 2`, `data["cell_occupancy"]` is the sum.
6. **`test_map_heatmap_data_team_color_filter`** — GET with
   `team_color=red` returns sums only for red-team players. Pin via
   one round with a red player credited cell `(0,0): 100` and a blue
   player credited cell `(1,1): 50` — filtered response excludes
   `(1,1)`.
7. **`test_map_heatmap_data_404_for_missing_map`** — bogus map_id →
   `404`.
8. **`test_map_heatmap_data_400_missing_zone_size`** — no `zone_size`
   query param → `400`.
9. **`test_map_heatmap_data_400_invalid_team_color`** —
   `team_color=purple` → `400`.
10. **`test_map_heatmap_data_405_non_get`** — POST → `405`.

### 7.3. Existing-file extension

In **`matches/tests/test_sim09_consolidation.py`**, extend the
relevant `TestCase` class (the one that already simulates a tiny
on-map round and asserts `_flush_to_db` persistence) with **one**
additional test:

- **`test_flush_to_db_populates_cell_occupancy_json_when_map_active`** —
  Simulate a small on-map round via the existing helper, assert
  `game_round.cell_occupancy_json` is **not None**, that it is a dict,
  that every top-level key is a `str(int)`, that every inner key
  matches `r"^\d+,\d+$"`, and that every inner value is an `int`.
  Also assert that on a map-less round (`arena_map=None`),
  `game_round.cell_occupancy_json is None` (regression — confirms the
  map-active gate).

`test_batch_sim.py` is **not** extended (RES-04 is a `_flush_to_db`
concern, not a per-tick simulator concern).

---

## 8. Out of scope (locked — do not implement)

- ❌ No backfill management command for pre-RES-04 rounds.
- ❌ No time-window slicing on the heatmap view (no `?from=&to=`).
- ❌ No PNG / PDF / CSV export endpoints.
- ❌ No JS unit tests (the frontend agent verifies via manual /
  Chrome-MCP smoke testing if at all).
- ❌ No ADR.
- ❌ No Score Calibration re-baseline.
- ❌ No new `MapContext` accessor — re-use `get_adjacency` and the
  existing `elevation_grid` field.
- ❌ No change to `GameEvent` (movement events stay exactly as RES-04
  found them — RES-04 adds a snapshot, it does not replace the trail).

---

## 9. Glossary cross-reference

- **Cell occupancy** (CONTEXT.md) — per-cell tick counts per player.
- **Movement trail** (CONTEXT.md) — ordered compact `(start, end, ts)`
  list on `event_type="movement"` GameEvents and on the transient
  `PlayerState.movement_trail`.
- **Advance** (CONTEXT.md) — a single Move action that traverses one
  or more cells in one tick.

---

## 10. Quick-reference name table

| Slot                                | Name                                                                       |
| ----------------------------------- | -------------------------------------------------------------------------- |
| Model field                         | `GameRound.cell_occupancy_json`                                            |
| Migration filename                  | `matches/migrations/0026_gameround_cell_occupancy_json.py`                 |
| Pure function module                | `matches/sim_helpers/cell_occupancy.py`                                    |
| Pure function name                  | `reconstruct_cell_occupancy`                                               |
| Per-round view name (Django)        | `movement_heatmap`                                                         |
| Per-round view URL                  | `/matches/game-round/<int:round_id>/heatmap/`                              |
| Per-round template                  | `templates/matches/movement_heatmap.html`                                  |
| Map-aggregate view name (Django)    | `map_heatmap_data`                                                         |
| Map-aggregate view URL              | `/maps/<int:map_id>/heatmap-data/`                                         |
| Pure-unit test file                 | `matches/tests/test_res04_cell_occupancy.py`                               |
| DB/view test file                   | `matches/tests/test_res04_heatmap_view.py`                                 |
| Per-cell occupancy json_script id   | `cell-occupancy-data`                                                      |
| Player-roster json_script id        | `player-roster-data`                                                       |
| Canvas DOM id (round)               | `heatmap-canvas`                                                           |
| Canvas DOM id (editor)              | `heatmap-canvas` (same — the canvas is positioned over `#zone-img`)        |
| Player filter id                    | `heatmap-filter-player`                                                    |
| Role filter id                      | `heatmap-filter-role`                                                      |
| Team-color filter id (round)        | `heatmap-filter-team`                                                      |
| Team-color filter id (editor)       | `heatmap-editor-filter-team`                                               |
| Editor mode button id               | `mode-heatmap`                                                             |
| Editor mode controls wrapper id     | `heatmap-controls`                                                         |
| Editor round-count display id       | `heatmap-editor-round-count`                                               |
| Map-less notice id                  | `heatmap-no-map-notice`                                                    |
