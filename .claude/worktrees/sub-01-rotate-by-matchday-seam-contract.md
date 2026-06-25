# SUB-01 piece 1 — Season `rotate_by_matchday` arena-map mode — SEAM CONTRACT

Extends the shipped **LG-01j** per-Season map config with a 4th `Season.map_mode`
value `rotate_by_matchday`. Author-ordered map list, keyed on **matchday alone**,
fully deterministic, **no RNG**, no Score Calibration re-baseline, `map_pool` M2M
stays EMPTY for this mode. Produce ONLY the named names/signatures/shapes below.

---

## 1. Model fields + MAP_MODE_CHOICES + migration

**`matches/models.py` `Season`** — UNCHANGED existing fields (`map_mode`
`CharField(max_length=32, default="none")`, `map_pool` M2M→`core.ArenaMap`
`related_name="seasons_using_pool"`, `starting_map_pool_ids_json` JSONField
null/blank/default=None). Add **one** choice + **two** JSONFields:

```python
MAP_MODE_CHOICES = (
    ("none", "3-zone fallback"),
    ("single", "Single map"),
    ("random_per_round", "Random per Round"),
    ("rotate_by_matchday", "Rotate by matchday"),   # NEW (4th)
)
# declared after starting_map_pool_ids_json:
map_rotation_ids_json = models.JSONField(null=True, blank=True, default=None)
starting_map_rotation_ids_json = models.JSONField(null=True, blank=True, default=None)
```

- `map_rotation_ids_json` — **live, author-ordered** rotation id list
  (None/blank/default=None pre-author). The order-preserving twin of the M2M.
- `starting_map_rotation_ids_json` — **activation snapshot, author order
  PRESERVED, NOT id-sorted** (the order-preserving twin of the existing
  `starting_map_pool_ids_json`, which IS id-sorted).
- `map_pool` M2M stays **EMPTY** for `rotate_by_matchday` (the ordered JSON is the
  sole source).

`Season.clean()` defensive enum check is UNCHANGED — it already validates
`map_mode in {v for v,_ in MAP_MODE_CHOICES}`, so the new value passes free; no
M2M pool-count rule added model-side.

**Migration `matches/migrations/0054_season_map_rotation.py`** — dep
`("matches", "0053_fin05_luxury_tax_firing")`. Ops in order:
1. `AlterField("season", "map_mode", …)` — choices +1 (the 4-tuple above).
2. `AddField("season", "map_rotation_ids_json", JSONField(null=True, blank=True, default=None))`.
3. `AddField("season", "starting_map_rotation_ids_json", JSONField(null=True, blank=True, default=None))`.

**NO `RunPython` / `RunSQL` / backfill** (ADR-0004 — existing Seasons take the
defaults: `rotate_by_matchday` is opt-in, no historical row needs it).

---

## 2. `_resolve_fixture_map` rotate branch + play-loop bulk-load

**`matches/tasks.py::_resolve_fixture_map(season, fixture, pool_by_id)`** — existing
3 branches (`none`/`single`/`random_per_round`) + `raise ValueError(f"Unknown map_mode: {mode!r}")`
tail are UNCHANGED. Insert the new branch **before** the `ValueError` tail:

```python
if mode == "rotate_by_matchday":
    ids = season.starting_map_rotation_ids_json or []
    if not ids:
        return None
    return pool_by_id.get(ids[fixture.matchday % len(ids)])
```

- Reads `season.starting_map_rotation_ids_json` (NOT the pool snapshot).
- Empty/None ⇒ `None`.
- Index = `fixture.matchday % len(ids)` — **keyed on matchday ALONE** (every
  fixture on matchday N maps to the same `ids[N % len(ids)]`).
- **Consumes NO RNG** (no `random.Random`, unlike `random_per_round`).
- Defensive `.get()` ⇒ `None` when the chosen id was admin-deleted post-activation.
- The helper still touches **no ORM** (duck-typed `season` reads `.id`, `.map_mode`,
  `.starting_map_pool_ids_json`, `.starting_map_rotation_ids_json`; `fixture` reads
  `.matchday`/`.round_number`/`.team_a_id`/`.team_b_id`; `pool_by_id` is the dict).

**Call-site bulk-load change — the two play-loop sites union BOTH snapshots' ids
into ONE `in_bulk`:**

```python
pool_by_id = ArenaMap.objects.in_bulk(
    (season.starting_map_pool_ids_json or [])
    + (season.starting_map_rotation_ids_json or [])
)
```

Exact sites (file::function — verified):
1. **`matches/tasks.py::play_season_task`** — replaces the current
   `pool_ids = season.starting_map_pool_ids_json or []` /
   `ArenaMap.objects.in_bulk(pool_ids)` (~tasks.py:230-231), once OUTSIDE the
   per-fixture loop.
2. **`matches/league_views.py::play_week`** — replaces the current
   `pool_ids = season.starting_map_pool_ids_json or []` /
   `ArenaMap.objects.in_bulk(pool_ids)` (~league_views.py:2543-2544), once outside
   the per-fixture loop.

**`play_week_live` (LG-01i RR branch, `matches/league_views.py` ~2778-2781) ALSO
calls `_resolve_fixture_map` and MUST be updated** — its current
`ArenaMap.objects.in_bulk(season.starting_map_pool_ids_json or [])` becomes the same
union expression. (The `play_week_live` playoff branch does NOT call
`_resolve_fixture_map` — tournament path, untouched.) That is the THIRD call site.

All other args to `simulate_scheduled_round(...)` are unchanged.

---

## 3. `start_season` snapshot branch

**`matches/models.py::Season.start_season()`** — the existing
`self.starting_map_pool_ids_json = sorted(m.id for m in self.map_pool.all())`
line (~models.py:1004) is UNCHANGED for the existing modes. Add (after it, before
`self.state = "active"`):

```python
self.starting_map_rotation_ids_json = list(self.map_rotation_ids_json or [])
```

**Author order PRESERVED** (`list(...)`, NOT `sorted(...)`). Empty/None ⇒ `[]`.
Inside the existing `@transaction.atomic`; the existing `starting_map_pool_ids_json`
snapshot is UNCHANGED (still id-sorted).

---

## 4. `CreateLeagueForm`: hidden field, cleaned key, 4×2 validation matrix

**`matches/forms.py::CreateLeagueForm`** — existing fields UNCHANGED (`map_mode`
`ChoiceField` with widget id `league-create-map-mode`; `map_pool`
`ModelMultipleChoiceField` queryset `_maps_with_confirmed_config()` widget id
`league-create-map-pool`). The `map_mode` choices auto-gain the 4th value
(it pulls `Season._meta.get_field("map_mode").choices`).

**New hidden field** (the serialized ordered-id list from the vanilla-JS composer):

```python
map_rotation = forms.CharField(
    widget=forms.HiddenInput(attrs={"id": "league-create-map-rotation"}),
    required=False,
)
```

**`clean()` change** — preserve the existing 3 LG-01j map_pool rules + the LG-02b
`parse_phase_composition` block verbatim. Parse `map_rotation` **INLINE**
(order-preserving), validate each id against `_maps_with_confirmed_config()`, stash
the ordered id list under the cleaned key **`map_rotation_ids`**:

- Parse: split `cleaned_data.get("map_rotation", "") or ""` on `,`, strip, drop
  empties, `int(...)` each → ordered `list[int]` (order preserved, NOT sorted).
- Validate: every parsed id must be in
  `set(_maps_with_confirmed_config().values_list("id", flat=True))`; an unknown id ⇒
  `self.add_error("map_rotation", forms.ValidationError(...))` matching the existing
  `forms.ValidationError({...})` / `add_error` style in this `clean()`.
- Stash: `cleaned_data["map_rotation_ids"] = <ordered list[int]>`.

**FULL 4×2 cross-guard matrix** (off-input empty + on-input correct count). The
existing 3 rules attach to `map_pool`; the rotation rules attach to `map_rotation`.
Error strings follow the existing byte-locked style (`"Map pool must …"`); the new
rotation strings mirror it:

| `map_mode` | `map_pool` rule (LOCKED existing strings) | `map_rotation` rule |
|---|---|---|
| `none` | pool empty — `"Map pool must be empty when Map mode is '3-zone fallback'."` | rotation empty — `"Map rotation must be empty when Map mode is '3-zone fallback'."` |
| `single` | pool == 1 — `"Map pool must contain exactly 1 map when Map mode is 'Single map'."` | rotation empty — `"Map rotation must be empty when Map mode is 'Single map'."` |
| `random_per_round` | pool ≥ 1 — `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."` | rotation empty — `"Map rotation must be empty when Map mode is 'Random per Round'."` |
| `rotate_by_matchday` | pool empty — `"Map pool must be empty when Map mode is 'Rotate by matchday'."` | rotation ≥ 1 — `"Map rotation must contain at least 1 map when Map mode is 'Rotate by matchday'."` |

Rotation count = `len(cleaned_data.get("map_rotation_ids") or [])`. The existing
`mode is None` early-return guard (skip cross-field rules when `map_mode` failed
field-level validation) is preserved.

Existing field/method names touched: `map_mode`, `map_pool` (existing),
`_maps_with_confirmed_config` (reused, NOT redefined), `clean` (extended).

---

## 5. `league_create` + `next_season` write/carry-forward lines

**`matches/league_views.py::league_create`** — inside the existing
`@transaction.atomic`, after `season.map_pool.set(cleaned["map_pool"])`
(~league_views.py:970):

```python
season.map_rotation_ids_json = cleaned["map_rotation_ids"]
season.save(update_fields=["map_rotation_ids_json"])
```

(or pass `map_rotation_ids_json=cleaned["map_rotation_ids"]` into the existing
`Season.objects.create(...)` — Code agent discretion; the cleaned key is
`map_rotation_ids`, the column is `map_rotation_ids_json`.)

**`matches/league_views.py::next_season`** — inside the existing
`@transaction.atomic`, alongside the existing
`map_mode=latest_completed.map_mode` carry-forward (~league_views.py:3637) and the
map_pool rehydrate (~3652-3654). `map_mode` is ALREADY carried. Add the rotation
carry-forward **verbatim** (no re-sort):

```python
new_season.map_rotation_ids_json = list(latest_completed.map_rotation_ids_json or [])
new_season.save(update_fields=["map_rotation_ids_json"])
```

---

## 6. `_build_dashboard_context` `map_config_label` 5th branch

**`matches/league_views.py::_build_map_config_label(displayed_season, season_mode)`**
— the existing 4-case ladder is UNCHANGED (case 1 None/`season_mode=="none"` ⇒
`"Map: 3-zone fallback (no map)"`; case 2 `map_mode=="none"` ⇒ same; case 3
`"single"` ⇒ `f"Map: Single — {name}"` / `"Map: Single — (map deleted)"`; case 4
`"random_per_round"` ⇒ `f"Map: Random per Round ({n} maps: {names})"` /
`"Map: Random per Round (no maps)"`; em-dash U+2014). Add the **5th branch** before
the defensive `return "Map: 3-zone fallback (no map)"` tail:

```python
if mode == "rotate_by_matchday":
    # Author-ordered ids: active/completed read the snapshot, draft reads live.
    if season_mode in ("active", "completed"):
        ids = displayed_season.starting_map_rotation_ids_json or []
    else:
        ids = displayed_season.map_rotation_ids_json or []
    names_by_id = dict(
        ArenaMap.objects.filter(id__in=ids).values_list("id", "name")
    )
    names = [names_by_id[i] for i in ids if i in names_by_id]  # AUTHOR order
    if not names:
        return "Map: Rotating (no maps)"
    return f"Map: Rotating ({len(names)} maps: {', '.join(names)})"
```

- Label format: `"Map: Rotating (N maps: a, b, c)"` in **AUTHOR order** (NOT
  alphabetical — unlike `random_per_round`, which sorts by name).
- **Season-field-per-state:** draft Season reads the live `map_rotation_ids_json`;
  active/completed reads `starting_map_rotation_ids_json` (matching how the existing
  branches read live M2M vs `starting_map_pool_ids_json`).

---

## 7. Template DOM ids (`templates/leagues/create.html`)

Existing ids UNCHANGED: `league-create-map-mode`, `league-create-map-pool`, the
LG-02b phase composer (`league-create-phases-composer`, `league-create-add-block`,
`league-create-phase-row-{i}`, `league-create-phase-type-{i}`,
`league-create-phases`). The "+ Add map" rotation composer mirrors the LG-02b
phase-composer pattern (vanilla JS, `rowSeq` index, `serialize()` on change/submit
into a hidden field). LOCKED ids:

| id | element |
|---|---|
| `league-create-map-rotation-composer` | outer composer container `<div>` |
| `league-create-add-map` | the "+ Add map" `<button type="button">` |
| `league-create-map-rotation-row-{i}` | per-row wrapper (`{i}` = 0-based `rowSeq`) |
| `league-create-map-rotation-select-{i}` | per-row map `<select>` (options = `_maps_with_confirmed_config()` maps) |
| `league-create-map-rotation` | the hidden input (== the `map_rotation` form-field widget id) |

`serialize()` joins the ordered rows' selected map ids into the hidden
`#league-create-map-rotation` input as a **comma-joined id list in row order**
(e.g. `"7,3,12"`), parsed order-preserving by `CreateLeagueForm.clean()`.

`templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` already
render `{{ map_config_label }}` (confirmed — both files match `map_config_label`):
**no new DOM id, no template edit beyond what already exists** (the 5th branch is a
view-side label change only).

---

## 8. Admin

**`matches/admin.py::SeasonAdmin`** — the 2 JSON fields (`map_rotation_ids_json`,
`starting_map_rotation_ids_json`) auto-surface on the default change form;
`map_mode` already renders its (now 4-value) `choices` select. `filter_horizontal`
stays `("teams", "map_pool")` (the rotation list is a JSON column, NOT an M2M, so it
is not added to `filter_horizontal`). **No `SeasonAdmin` change.**

---

## 9. Test-file boundary

| file | NEW / EXTENDED | asserts |
|---|---|---|
| `matches/tests/test_season_map_config.py` | **EXTENDED** | the LG-01j pure-unit `_resolve_fixture_map` file (stubs `_SeasonStub`/`_FixtureStub`/`_MapStub`). The `_SeasonStub` gains a `starting_map_rotation_ids_json` attr. New class `TestResolveFixtureMapRotateByMatchday`: empty/None ⇒ None; `ids[matchday % len(ids)]` indexing; **matchday-only** keying (same matchday across distinct round_number/teams ⇒ same map); **no-RNG** (the `random.seed`-isolation test analogue); missing-id `.get` ⇒ None. New model classes `TestSeasonMapRotationFields` (the 2 JSONFields: null/blank/default=None) + `TestSeasonStartSeasonSnapshotsMapRotation` (`start_season` snapshots `starting_map_rotation_ids_json = list(map_rotation_ids_json or [])`, **author order preserved NOT sorted**, empty ⇒ `[]`, frozen after activation). `TestSeasonMapModeField.test_field_choices_are_the_three_locked_tuples` becomes a 4-tuple assertion. |
| `matches/tests/test_league_create.py` | **EXTENDED** | `rotate_by_matchday` create persists `map_rotation_ids_json` in author order + `map_pool` empty; the 4×2 validation matrix (each off-input-empty + on-input-correct-count); the `league-create-map-rotation*` DOM ids render. |
| `matches/tests/test_league_next_season.py` | **EXTENDED** | `next_season` carries `map_rotation_ids_json` forward verbatim (author order) + `map_mode`. |
| `matches/tests/test_league_play.py` | **EXTENDED** | `play_season_task` over a `rotate_by_matchday` Season resolves each Round's map by matchday (assert the per-matchday map id, NOT point totals); the union `in_bulk` covers rotation ids. |
| `matches/tests/test_league_dashboard.py` + `test_season_dashboard_view.py` | **EXTENDED** | the 5th `map_config_label` branch — `"Map: Rotating (N maps: …)"` in author order, draft reads live / active reads snapshot, `"Map: Rotating (no maps)"` empty. |

**Internal (NOT asserted at the seam):** the rotation composer JS, the exact
`in_bulk` union expression, the per-row Bootstrap classes.

---

## 10. Determinism / scope note

`rotate_by_matchday` is **fully deterministic, consumes NO RNG**, makes **no
simulator change**, triggers **no Score Calibration re-baseline**; `map_pool` M2M is
**EMPTY** for this mode (ordered JSON is the sole source); tournament/playoff
fixtures are **unaffected** (only `_resolve_fixture_map` over RR `ScheduleFixture`s).

---

## 11. Locked names index

- **Model:** `Season.MAP_MODE_CHOICES` (+`("rotate_by_matchday", "Rotate by matchday")`),
  `Season.map_rotation_ids_json` / `Season.starting_map_rotation_ids_json`
  (both `JSONField(null=True, blank=True, default=None)`).
- **Migration:** `matches/migrations/0054_season_map_rotation.py` (dep
  `0053_fin05_luxury_tax_firing`; 1× `AlterField(map_mode)` + 2× `AddField`).
- **Helper branch:** `matches.tasks._resolve_fixture_map` rotate branch
  (`ids[fixture.matchday % len(ids)]`, reads `starting_map_rotation_ids_json`).
- **Bulk-load (3 sites):** `in_bulk((starting_map_pool_ids_json or []) + (starting_map_rotation_ids_json or []))`
  in `tasks.play_season_task`, `league_views.play_week`, `league_views.play_week_live` (RR branch).
- **Snapshot:** `start_season` ⇒ `self.starting_map_rotation_ids_json = list(self.map_rotation_ids_json or [])`.
- **Form:** `CreateLeagueForm.map_rotation` (hidden, widget id `league-create-map-rotation`),
  cleaned key `cleaned_data["map_rotation_ids"]` (ordered `list[int]`), the 4×2 matrix.
- **Views:** `league_create` writes `map_rotation_ids_json`; `next_season` carries it forward.
- **Label:** `_build_map_config_label` 5th branch — `"Map: Rotating (N maps: a, b, c)"`
  (author order) / `"Map: Rotating (no maps)"`.
- **DOM ids:** `league-create-map-rotation-composer` / `league-create-add-map` /
  `league-create-map-rotation-row-{i}` / `league-create-map-rotation-select-{i}` /
  `league-create-map-rotation` (hidden).
- **Admin:** no change (`SeasonAdmin.filter_horizontal` stays `("teams", "map_pool")`).
