# LG-01j — Per-Season Arena Map Configuration · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01j ships **per-Season arena map configuration** — every Season grows a new `map_mode` enum + a new `map_pool` M2M to `ArenaMap` + a new `starting_map_pool_ids_json` snapshot. The user picks the mode at create-League time (LG-01b form gains 2 fields). LG-01e (Start Next Season) carries the previous Season's config forward verbatim. The LG-01d simulation entry points (`play_season_task` async + `play_week` sync) resolve each Round's `arena_map` from the frozen snapshot via a new module-level helper `_resolve_fixture_map` and pass the resolved `ArenaMap | None` through the already-supported `BatchSimulator.simulate_scheduled_round(..., arena_map=…)` kwarg (SIM-09). Dashboards (League + Season, LG-01c) render a read-only `map_config_label` string showing the active configuration. Two modes ship at LG-01j — **`none`** (default, 3-zone fallback — LG-01d behaviour today) and **`single`** (one fixed map for every Round of the Season) and **`random_per_round`** (deterministic per-Round draw from a pool, seeded by fixture identity). A third "per-sub-league rotation" mode is **deferred to SUB-01 post-CAR-03** and is explicitly out of scope. Mid-League map-config edits are admin-only (Django admin); no edit URL ships. This contract mirrors the structure of [`.claude/worktrees/lg-01h-seam-contract.md`](lg-01h-seam-contract.md) (DOM-id discipline + locked-label-string precedent + section-by-section seam pinning) and [`.claude/worktrees/lg-01g-seam-contract.md`](lg-01g-seam-contract.md) (per-Season helper signature precedent and in-place body extension of LG-01b form / LG-01e view without renaming). **No new ADR ships at LG-01j** — the decisions are reversible (model fields + a deterministic helper) and the existing CONTEXT.md domain language extension covers the vocabulary.

## Locked design decisions

- **Two modes ship**: `none` (default, 3-zone fallback) and `single` and `random_per_round`. Mode (b) per-sub-league rotation is **deferred to SUB-01 post-CAR-03** — already noted in PLAN.md. No third enum value is reserved at LG-01j (when SUB-01 lands, a new enum value will be added then).
- **Mode enum** (locked literal strings, used everywhere — choices tuple, form, helper, label string, tests): `"none"` / `"single"` / `"random_per_round"`.
- **Mode display labels** (locked, used in `choices=` and dashboards): `"3-zone fallback"` for `none`, `"Single map"` for `single`, `"Random per Round"` for `random_per_round`.
- **Edit window**: create-League time only. No edit URL, no edit view, no edit form, no edit template. Mid-League changes are **admin-only** via Django admin's `SeasonAdmin` (which gains `filter_horizontal=("teams", "map_pool")` and the `map_mode` field via the default model-form render).
- **Carry-forward** (LG-01e): `next_season` copies `map_mode` verbatim from `latest_completed` and rehydrates `map_pool` from `latest_completed.starting_map_pool_ids_json` (NOT from the live `latest_completed.map_pool` M2M — the snapshot is the source of truth post-activation).
- **Frozen-at-activation snapshot**: `Season.start_season()` snapshots `starting_map_pool_ids_json = sorted([m.id for m in self.map_pool.all()])` (sorted ascending for determinism), mirroring the LG-01 `starting_team_ids_json` precedent. The simulator never reads the live M2M during play — it reads the snapshot, so admin-side pool edits to an active Season don't drift the schedule's map sequence.
- **Per-fixture RNG**: `random_per_round` uses a `random.Random` seeded by `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"`. A fresh `Random` per fixture (NOT a long-lived generator) — so map choice is independent of the simulator's RNG and replay-faithful per fixture identity.
- **Defensive fallback**: a deleted-after-activation pool entry resolves to `None` (3-zone fallback), NOT a crash. The simulator already accepts `arena_map=None` (LG-01d default).
- **No simulator mechanics change**: the change folds into the existing pending post-MOVE-01 re-baseline (alongside MOVE-02 / MOVE-03 / MOVE-04 / SIM-09). No Score Calibration re-baseline is *triggered* by LG-01j alone.

## Data model

NEW migration: `laserforce_simulator/matches/migrations/0031_season_map_mode_pool.py` (next sequential migration after the latest LG-01x migration). Adds **3 fields** to `Season`:

1. **`map_mode: models.CharField`**
   - `max_length=32`
   - `choices=[("none", "3-zone fallback"), ("single", "Single map"), ("random_per_round", "Random per Round")]`
   - `default="none"`
   - No `db_index` (low cardinality, not filter-keyed)
   - Field placement: appended after the latest existing `Season` field at LG-01h-tip (Code agent's discretion on exact field-block ordering inside the model body; tests assert via `Season._meta.get_field("map_mode")`).

2. **`map_pool: models.ManyToManyField(ArenaMap, blank=True, related_name="seasons_using_pool")`**
   - `blank=True` so empty pool is valid at the ORM level (form / `save_related` enforces the mode-vs-pool rules)
   - `related_name="seasons_using_pool"` (locked — used in tests and reverse lookups)
   - `ArenaMap` lives in `core.models` — the migration uses the string ref `"core.ArenaMap"` to avoid circular imports.

3. **`starting_map_pool_ids_json: models.JSONField(null=True, blank=True, default=None)`**
   - Default `None` (pre-activation Seasons have `None`; activation flips to `[]` for `none`/`single` empty cases or `[id1, id2, …]` sorted ascending)
   - Mirrors the existing LG-01 `starting_team_ids_json` field signature verbatim
   - No `db_index`

The migration is a **pure schema migration** (no data-migration step needed — pre-LG-01j Seasons take the `map_mode="none"` default + empty M2M + `None` snapshot, which yields the 3-zone fallback at simulation time — the LG-01d behaviour preserved).

## `Season.clean()` extension

`Season.clean()` already enforces the LG-01-locked invariant (≤1 non-completed Season per League). **PRESERVE that rule verbatim.** Append a NEW rule for `map_mode`:

- Validate `map_mode` is one of the 3 enum values — `ValidationError({"map_mode": "Unknown map mode."})` for any other value. (Django's `CharField.choices` already enforces this on `full_clean()`, but the model-level explicit check is defensive against admin-side raw assignments.)

**M2M rules are NOT enforced in `Model.clean()`** because M2M rows aren't visible to `Model.clean()` (they exist only after `save()`). The mode-vs-pool-count rule lives form-side (`CreateLeagueForm.clean()`) and admin-side (`SeasonAdmin.save_related`). The model-level `clean()` only enforces the enum value, NOT pool-count.

## `Season.start_season()` extension

`Season.start_season()` already (LG-01-locked) flips draft → active, snapshots `starting_team_ids_json`, and raises `ValidationError` on `< 2 teams`. **PRESERVE the existing `@transaction.atomic` decorator, the draft→active flip, the `<2 teams` guard, the existing snapshot of `starting_team_ids_json`, and the existing return shape.** Append, after the `starting_team_ids_json` snapshot line and before the existing `self.save()`:

```
self.starting_map_pool_ids_json = sorted([m.id for m in self.map_pool.all()])
```

Locked algorithm details:
- Sorted **ascending by `id`** (determinism — re-activation of a re-drafted Season yields identical snapshot).
- Empty pool ⇒ `[]` (NOT `None`). `None` is reserved for pre-activation; `[]` is "activated with no maps".
- Single ORM query (`self.map_pool.all()` evaluates once into the list comprehension).
- Snapshot happens inside the existing `@transaction.atomic` block, so partial-failure rolls back.

## `CreateLeagueForm` extension (LG-01b)

`matches.forms.CreateLeagueForm` (LG-01b file) is EXTENDED in-place — **DO NOT rename**, **DO NOT add a `Meta` class** (the form is already a plain `forms.Form`, not a `ModelForm`). The existing 7 fields (`league_name` / `season_name` / `start_date` / `num_teams` / `schedule_format` / `mean` / `std_dev`) remain unchanged in name, type, order, and `clean_*` validation — the canonical LG-01b inventory matches `matches/CLAUDE.md` and `.claude/worktrees/lg-01b-seam-contract.md`. Append 2 NEW fields at the END of the field declaration block:

1. **`map_mode = forms.ChoiceField(choices=Season._meta.get_field("map_mode").choices, initial="none", required=True, label="Map mode")`**
   - Pulls choices from the model field (single source of truth)
   - Initial `"none"` matches the model default
   - `required=True` (no blank choice)

2. **`map_pool = forms.ModelMultipleChoiceField(queryset=_maps_with_confirmed_config(), required=False, label="Map pool")`**
   - `required=False` — mode `none` accepts empty pool
   - The queryset helper `_maps_with_confirmed_config()` **already exists** in `matches/forms.py` (used by `MatchSetupForm` / `SingleRoundSetupForm`) and returns only `ArenaMap` objects with at least one confirmed `MapZoneConfig` (a related-table check — NOT a `config_confirmed` boolean on `ArenaMap`). LG-01j **reuses the existing helper verbatim** — do NOT redefine its body, do NOT inline its query. Half-built maps without a confirmed `MapZoneConfig` are excluded from the picker by the existing helper's filter.
   - Widget: default Django `SelectMultiple` (no JS framework — matches LG-01h scope-out)

**Field order, locked, in pinned order**: `league_name` (1) → `season_name` (2) → `start_date` (3) → `num_teams` (4) → `schedule_format` (5) → `mean` (6) → `std_dev` (7) → `map_mode` (8) → `map_pool` (9). Total **9 fields**.

**`CreateLeagueForm.clean()` extension**: the existing `clean()` body is PRESERVED. Append 3 new mode-vs-pool rules, raising `ValidationError({"map_pool": "…"})` (errors attach to `map_pool`, NOT to `map_mode`, so the help text is co-located with the field the user clicked wrong):

- `mode == "none"` and `len(pool) > 0` ⇒ `ValidationError({"map_pool": "Map pool must be empty when Map mode is '3-zone fallback'."})`
- `mode == "single"` and `len(pool) != 1` ⇒ `ValidationError({"map_pool": "Map pool must contain exactly 1 map when Map mode is 'Single map'."})`
- `mode == "random_per_round"` and `len(pool) < 1` ⇒ `ValidationError({"map_pool": "Map pool must contain at least 1 map when Map mode is 'Random per Round'."})`

Defensive read inside `clean()`: `mode = cleaned_data.get("map_mode")` and `pool = cleaned_data.get("map_pool") or []` — when `map_mode` failed its own field-level validation, skip the cross-field rule (return cleaned_data early at the top of the new block if `mode is None`).

`CreateLeagueForm.__init__` is UNCHANGED beyond the implicit queryset binding from the `ModelMultipleChoiceField` declaration.

## `league_create` view extension (LG-01b)

`matches.views.league_create` is EXTENDED in-place — **DO NOT rename**. The existing body (form rendering, POST validation, `@transaction.atomic` block, `Season.objects.create(...)`, `season.teams.add(*created_teams)`, redirect to `season_standings`) is PRESERVED. Inside the existing `@transaction.atomic` body, in pinned order:

1. After the existing `Season.objects.create(league=…, ...)` line, ALSO pass `map_mode=cleaned["map_mode"]` to the create (or assign post-create — Code agent's discretion; tests assert `season.map_mode == cleaned["map_mode"]`).
2. After the existing `season.teams.add(*created_teams)` line, append `season.map_pool.set(cleaned["map_pool"])` — this materialises the M2M rows in the same atomic block.

Redirect target (`season_standings` with `kwargs={"season_id": season.id}`) is **UNCHANGED**. Method-allow guard (`if request.method != "POST"` rendering the form) is UNCHANGED.

## `next_season` view extension (LG-01e)

`matches.views.next_season` is EXTENDED in-place — **DO NOT rename**. The existing body (`@transaction.atomic`, locate `latest_completed`, create `new_season` with `schedule_format=latest_completed.schedule_format` carry-forward, `new_season.teams.add(*teams_qs)`, redirect to `season_dashboard`) is PRESERVED. Inside the existing `@transaction.atomic` body, after the existing `new_season.teams.add(*teams_qs)` line, in pinned order:

1. `new_season.map_mode = latest_completed.map_mode` (verbatim carry-forward — mirrors the existing `schedule_format` carry-forward pattern)
2. `pool_ids = latest_completed.starting_map_pool_ids_json or []` — read from the FROZEN SNAPSHOT, NOT the live M2M (the live M2M may have drifted via admin edits; the snapshot is what the Season ACTUALLY played with)
3. `new_season.map_pool.set(ArenaMap.objects.filter(id__in=pool_ids))` — defensive: deleted maps simply drop out of the queryset
4. `new_season.save()` (the carry-forward assignments need persistence; the existing `next_season` view may already call `save()` — Code agent ensures a single `save()` after all field assignments).

Redirect target (`season_dashboard`) is **UNCHANGED**. The `latest_completed is None` guard ("No completed Season") is UNCHANGED.

## `play_season_task` extension (LG-01d, `matches/tasks.py`)

`matches.tasks.play_season_task` is EXTENDED in-place — **DO NOT rename**. The existing body (load `season`, build `to_play` list of `ScheduleFixture` rows, per-fixture call into `BatchSimulator().simulate_scheduled_round(...)`, the existing deferred imports, the existing progress-write to `MatchJob`) is PRESERVED. Body changes in pinned order:

1. Add a deferred import line alongside the existing deferred-import block: `from core.models import ArenaMap`. Inline-import `_resolve_fixture_map` from `matches.tasks` is NOT needed since the helper lives in the same module — call directly.
2. **After** loading `season` and **before** the per-fixture loop, resolve the map pool once:
   ```
   pool_ids = season.starting_map_pool_ids_json or []
   pool_by_id: dict[int, ArenaMap] = ArenaMap.objects.in_bulk(pool_ids)
   ```
   Single ORM query regardless of `len(to_play)`. Lives OUTSIDE the per-fixture loop.
3. Inside the per-fixture loop, BEFORE the existing `BatchSimulator().simulate_scheduled_round(...)` call, resolve the map:
   ```
   arena_map = _resolve_fixture_map(season, fixture, pool_by_id)
   ```
4. Pass to the simulator (only the new kwarg is added; all other args UNCHANGED):
   ```
   BatchSimulator().simulate_scheduled_round(
       season, team_a, team_b, fixture.round_number,
       arena_map=arena_map,
   )
   ```
   The `BatchSimulator.simulate_scheduled_round` signature already accepts `arena_map: ArenaMap | None = None` per SIM-09 — **no simulator edit is needed**.

The existing `MatchJob` progress writes, the existing exception handling, the existing job-state transitions, and the existing `@shared_task` decorator are all UNCHANGED.

## `play_week` synchronous path (LG-01d)

`matches.views.play_week` (or wherever the synchronous "play this matchday in-request" path lives — LG-01d names this `play_week`; if the actual function name has drifted, Code agent locates by behaviour and applies the same edit). Mirror the `play_season_task` body changes:

1. Add `from core.models import ArenaMap` to the deferred imports.
2. Resolve `pool_by_id` ONCE **outside** the inline `with transaction.atomic():` block (or at the top of the view body before the atomic block — Code agent's discretion as long as the bulk fetch happens once per request, not per fixture).
3. Inside the per-fixture loop, call `arena_map = _resolve_fixture_map(season, fixture, pool_by_id)` — use the **SAME** helper as `play_season_task`.
4. Pass `arena_map=arena_map` into `simulate_scheduled_round`.

The redirect target, the matchday-advance logic, and the `@transaction.atomic` boundary are UNCHANGED.

## `_resolve_fixture_map` helper

NEW module-level flat helper (NO class) in `matches/tasks.py`:

```python
def _resolve_fixture_map(
    season: "Season",
    fixture: "ScheduleFixture",
    pool_by_id: dict[int, "ArenaMap"],
) -> "ArenaMap | None":
    ...
```

**Locked body algorithm** (pinned step-by-step):

1. `mode = season.map_mode`
2. **If `mode == "none"`** ⇒ return `None`.
3. **If `mode == "single"`** ⇒
   - `pool_ids = season.starting_map_pool_ids_json or []`
   - If `not pool_ids` ⇒ return `None` (defensive — admin emptied the M2M but the snapshot somehow has empty too)
   - Otherwise pop the first id: `chosen_id = pool_ids[0]`
   - Return `pool_by_id.get(chosen_id)` — `None` if admin deleted the row after activation.
4. **If `mode == "random_per_round"`** ⇒
   - `pool_ids = season.starting_map_pool_ids_json or []`
   - If `not pool_ids` ⇒ return `None` (defensive)
   - Build seed: `seed_str = f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"` (locked format — pipe-separated, no spaces, in that exact order)
   - `rng = random.Random(seed_str)` (a fresh `Random` per fixture — does NOT share state with the simulator's RNG)
   - `chosen_id = rng.choice(pool_ids)` — note `pool_ids` is already sorted ascending from `start_season()`, so the choice is deterministic
   - Return `pool_by_id.get(chosen_id)` — `None` if admin deleted the row after activation.
5. **Else (unknown `mode`)** ⇒ raise `ValueError(f"Unknown map_mode: {mode!r}")`.

**Purity**: NO Django ORM access inside the helper. The view does the `in_bulk` upfront and passes `pool_by_id` in. The helper consumes only `season.id` / `season.map_mode` / `season.starting_map_pool_ids_json` (duck-typed attributes — works against any object with those 3 attributes) + a `ScheduleFixture`-shaped object exposing `.matchday` / `.round_number` / `.team_a_id` / `.team_b_id` + a `dict[int, ArenaMap]`. This makes the helper **pure unit-testable** with `@dataclass` stubs and zero DB.

**Locked location**: `matches/tasks.py` (NOT `matches/season_dashboard.py` — `season_dashboard.py` has a frozen no-Django-import allowlist from LG-01h scope-out, and adding even a duck-typed helper there muddies that allowlist; `matches/tasks.py` already imports Django + Celery and is the natural home for batch-simulation glue). The helper is module-level, not nested.

**Locked seed-string format**: `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"`. The 5 components, in that exact order, pipe-separated. Tests assert the format verbatim via `random.Random(known_string).choice(known_pool)` equality.

## Dashboard read-only display (LG-01c — modifies `_build_dashboard_context`)

`matches.views._build_dashboard_context(displayed_season, season_mode) -> dict` (LG-01c-locked helper). Existing context grows from **11 keys to 12** — the 11 existing keys are PRESERVED verbatim; ONE new key is added:

- **`map_config_label: str`** — a human-readable summary of the active map configuration. Computed inside `_build_dashboard_context` before the return.

**Locked rendering rules** (4 cases, in this exact precedence — Code agent implements the `if/elif/else` ladder in this order):

1. **`displayed_season is None`** OR **`season_mode == "none"`** (LG-01c season_mode value, NOT `Season.map_mode` — these are unrelated enums; LG-01c's `season_mode` distinguishes "no season picked" from "showing this Season") ⇒
   `"Map: 3-zone fallback (no map)"`

2. **`displayed_season.map_mode == "none"`** ⇒
   `"Map: 3-zone fallback (no map)"`

3. **`displayed_season.map_mode == "single"`** ⇒
   - Resolve the single map: `pool_ids = displayed_season.starting_map_pool_ids_json or []` if season is active or completed; else read from live M2M for draft Seasons.
   - If `pool_ids` is non-empty: `map_obj = ArenaMap.objects.filter(id=pool_ids[0]).first()`; if `map_obj is not None` ⇒ `f"Map: Single — {map_obj.name}"` (em-dash U+2014, SPACE on both sides)
   - If pool empty OR map missing ⇒ `"Map: Single — (map deleted)"`

4. **`displayed_season.map_mode == "random_per_round"`** ⇒
   - Resolve pool: `pool_ids = displayed_season.starting_map_pool_ids_json or []` (for active/completed); else live M2M (for draft).
   - `maps = ArenaMap.objects.filter(id__in=pool_ids).order_by("name")` — sorted by NAME ascending (NOT id)
   - `names = [m.name for m in maps]`
   - If `len(names) == 0` ⇒ `"Map: Random per Round (no maps)"`
   - Else ⇒ `f"Map: Random per Round ({len(names)} maps: {', '.join(names)})"`

**Locked label string literals (4 fully-formed examples)**:
- `"Map: 3-zone fallback (no map)"`
- `"Map: Single — Alpha Arena"` (em-dash U+2014)
- `"Map: Single — (map deleted)"`
- `"Map: Random per Round (3 maps: Alpha Arena, Bravo Arena, Charlie Arena)"`
- `"Map: Random per Round (no maps)"`

The label string itself is the locked seam — tests assert on these strings as substrings inside the rendered template DOM ids below.

## Templates (MODIFIED)

Three templates are EDITED. NO new template ships at LG-01j.

1. **`templates/leagues/dashboard.html`** — render `{{ map_config_label }}` inside `<div id="league-dashboard-map-config">…</div>`. Placement: IMMEDIATELY UNDER the LG-01-f-locked DOM id `league-dashboard-action-button`, and IMMEDIATELY ABOVE the LG-01-c-locked DOM id `league-dashboard-standings-snippet`. Markup template (Code agent uses verbatim):
   ```html
   <div id="league-dashboard-map-config" class="text-muted small mt-1">
     {{ map_config_label }}
   </div>
   ```

2. **`templates/seasons/dashboard.html`** — render `{{ map_config_label }}` inside `<div id="season-dashboard-map-config">…</div>`. Placement: IMMEDIATELY UNDER `season-dashboard-action-button`, IMMEDIATELY ABOVE `season-dashboard-standings-snippet`. Same markup pattern.

3. **`templates/leagues/create.html`** — render TWO new field rows for `{{ form.map_mode }}` and `{{ form.map_pool }}`, each with a `<label>` and a per-field error block. Placement: AFTER the existing `team_stat_std_dev` field row, BEFORE the submit button. New DOM ids:
   - `league-create-map-mode` on the `<select>` for `map_mode`
   - `league-create-map-pool` on the `<select multiple>` for `map_pool`

   Markup template (Code agent uses verbatim — the `id_for_label` overrides ensure the DOM id is exact, not Django's auto-generated `id_map_mode`):
   ```html
   <div class="form-row">
     <label for="league-create-map-mode">Map mode</label>
     <select name="map_mode" id="league-create-map-mode">
       {% for value, label in form.fields.map_mode.choices %}
         <option value="{{ value }}" {% if form.map_mode.value == value %}selected{% endif %}>{{ label }}</option>
       {% endfor %}
     </select>
     {% if form.map_mode.errors %}<div class="errors">{{ form.map_mode.errors }}</div>{% endif %}
   </div>
   <div class="form-row">
     <label for="league-create-map-pool">Map pool</label>
     <select name="map_pool" id="league-create-map-pool" multiple>
       {% for choice in form.fields.map_pool.queryset %}
         <option value="{{ choice.id }}" {% if choice in form.map_pool.value %}selected{% endif %}>{{ choice.name }}</option>
       {% endfor %}
     </select>
     {% if form.map_pool.errors %}<div class="errors">{{ form.map_pool.errors }}</div>{% endif %}
   </div>
   ```

   Code agent may equivalently render via `{{ form.map_mode }}` + `<label for="{{ form.map_mode.id_for_label }}">` if the form override sets `widget=forms.Select(attrs={"id": "league-create-map-mode"})` — tests assert the DOM ids exist, not the exact rendering path. The locked seam is the DOM ids `league-create-map-mode` and `league-create-map-pool`.

## CONTEXT.md additions

`CONTEXT.md` is EDITED — the existing `### League and seasons` section gains **3 new glossary entries** in this exact order, appended AFTER the existing **Team schedule** entry at the end of the section. Each entry follows the existing `**Term name** — Definition.` pattern:

1. **Map mode** — The per-Season enum on `Season.map_mode` ∈ `{none, single, random_per_round}` determining how each Round's `arena_map` is chosen. `none` runs every Round on the 3-zone fallback (the LG-01d default — no `arena_map` attached to the Round). `single` uses one fixed map for every Round of the Season. `random_per_round` draws per Round from `Season.map_pool` deterministically by fixture identity (matchday, round number, both team ids, Season id — see Per-fixture map resolution). Locked at create-League time via the LG-01b `CreateLeagueForm`; mid-League changes are admin-only via Django admin. Distinct from a Round's `GameRound.arena_map` (which is the *result* of the per-fixture resolution, persisted on the Round as a row attribute by the simulator).

2. **Map pool** — The M2M `Season.map_pool` from a Season to `ArenaMap`. Frozen at activation via the JSONField snapshot `Season.starting_map_pool_ids_json` (sorted-ascending list of `ArenaMap` ids — mirrors the LG-01 `starting_team_ids_json` snapshot precedent). Empty pool is valid only with `Map mode = none`. Pool size constraints: exactly 1 entry for `single`, ≥1 entry for `random_per_round`. The simulator never reads the live M2M during play — it reads the frozen snapshot, so admin-side pool edits to an active Season don't drift the schedule's map sequence. A pool entry deleted after activation resolves to `None` (3-zone fallback) at simulation time — defensive, not a crash.

3. **Per-fixture map resolution** — The deterministic per-Round map draw for `Map mode = random_per_round`. Seeded by `random.Random(f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}").choice(pool_ids)`. Re-runs / task resumes pick the same map for the same fixture (replay-faithful). Extends the SIM-07/SIM-08 contract from "same seed + Orientation + rosters + map ⇒ same Round" to "same fixture identity ⇒ same map ⇒ same Round." Consumes a Python stdlib `random.Random` instance separate from the simulator's RNG so map choice does not perturb the simulation's seed chain. Implemented as the module-level `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None` helper, called from both the async `play_season_task` and the synchronous `play_week` path.

## Tests

### NEW test files (2)

1. **`matches/tests/test_lg01j_season_map_config.py`** — Django `TestCase`, exercises model behaviour. Classes:
   - `TestSeasonMapModeField` — field exists, `choices` exactly the 3 locked tuples, `default == "none"`, `max_length == 32`. `Season.objects.create(league=l)` defaults to `"none"`. `Season(map_mode="bogus").full_clean()` raises `ValidationError` on the `map_mode` key.
   - `TestSeasonMapPoolField` — M2M field exists, `related_name == "seasons_using_pool"`, `blank=True`, reverse access via `arena_map.seasons_using_pool.all()` works, adding & removing maps persists, draft Season can `.map_pool.set([m1, m2])`.
   - `TestSeasonStartingMapPoolSnapshot` — field exists, `null=True`, `blank=True`, `default is None`, JSONField type assertion.
   - `TestSeasonStartSeasonSnapshotsMapPool` — `start_season()` populates `starting_map_pool_ids_json` from the live M2M, sorted ascending, BEFORE the `save()` flush. Empty pool ⇒ `[]` (NOT `None`). Pool of 3 maps with ids `[7, 3, 11]` ⇒ snapshot `[3, 7, 11]`. The snapshot is taken inside the existing `@transaction.atomic` block (re-raise a forced exception verifies rollback leaves `starting_map_pool_ids_json` at the pre-call value). Re-activating a draft Season after pool edits re-snapshots correctly.

2. **`matches/tests/test_lg01j_resolve_fixture_map.py`** — pure unit test on `_resolve_fixture_map`. **NO DB**. NO `django.test.TestCase` — use plain `unittest.TestCase` or pytest-style functions. Build a minimal `Season` stub via `@dataclass` exposing `.id` / `.map_mode` / `.starting_map_pool_ids_json`; build a `ScheduleFixture`-shaped stub via `@dataclass` exposing `.matchday` / `.round_number` / `.team_a_id` / `.team_b_id`; build `pool_by_id` as a plain `dict[int, MagicMock(spec=ArenaMap)]` or a `dict[int, SimpleNamespace(id=…, name=…)]`. Classes:
   - `TestResolveFixtureMapNone` — `mode == "none"` ⇒ returns `None` regardless of pool / fixture inputs. Asserts no `ArenaMap.objects.…` call (the helper has no ORM).
   - `TestResolveFixtureMapSingle` — `mode == "single"` ⇒ returns the lone entry in `pool_by_id` (taken via `pool_ids[0]`). Tests: pool of 1 returns that map; pool of 1 with snapshot ids `[42]` but `pool_by_id` missing key 42 ⇒ returns `None` (admin-deleted case); empty snapshot ⇒ returns `None`.
   - `TestResolveFixtureMapRandomPerRound` — `mode == "random_per_round"` ⇒ deterministic by seed-string. Tests: same fixture identity + same pool ⇒ same map across 100 calls (replay equality); different fixtures with same Season + same pool ⇒ varied distribution (NOT all the same map across 50 fixtures, statistical sanity check); empty pool ⇒ returns `None`; pool entry id NOT in `pool_by_id` (admin-deleted) ⇒ returns `None`; the seed-string format is asserted EXACTLY by recomputing `random.Random(f"{1}|{2}|{3}|{4}|{5}").choice([10, 20, 30])` and matching the helper's choice.
   - `TestResolveFixtureMapUnknownMode` — `mode == "bogus"` ⇒ raises `ValueError` with the locked message `f"Unknown map_mode: {'bogus'!r}"`.
   - `TestResolveFixtureMapMissingMap` — covers the defensive `pool_by_id.get(chosen_id)` returning `None` for both `single` and `random_per_round` modes when the map row was deleted between activation and simulation.

### EXTENDED test files (5)

3. **`matches/tests/test_league_create.py`** — extend the LG-01b file. New classes:
   - `TestLeagueCreateMapMode` — form has the `map_mode` field, choices match locked 3 tuples, initial `"none"`, `clean()` accepts each of the 3 valid modes, rejects `"bogus"` with the model-level enum error.
   - `TestLeagueCreateMapPool` — form has the `map_pool` field, queryset is `_maps_with_confirmed_config()` (only `ArenaMap` rows with at least one confirmed `MapZoneConfig`), the 3 mode-vs-pool rules are enforced. Tamper-POST tests: POST `map_mode="none"` + `map_pool=[m1.id]` ⇒ `clean()` raises `ValidationError({"map_pool": "Map pool must be empty…"})`; POST `map_mode="single"` + empty pool ⇒ raises; POST `map_mode="single"` + 2 maps ⇒ raises; POST `map_mode="random_per_round"` + empty ⇒ raises; happy paths for all 3 modes (none+empty, single+1, random+1, random+5) ⇒ form `is_valid()` and after-`league_create`-view the `Season` is created with the correct `map_mode` and `map_pool.all()` set. Atomic rollback unchanged (an invalid form does NOT create the League or its Season).

4. **`matches/tests/test_lg01e_next_season.py`** — new class `TestNextSeasonMapConfigCarryForward`:
   - When `latest_completed.map_mode == "none"` ⇒ `new_season.map_mode == "none"` and `new_season.map_pool.count() == 0`.
   - When `latest_completed.map_mode == "single"` with `starting_map_pool_ids_json=[42]` and ArenaMap 42 exists ⇒ `new_season.map_mode == "single"` and `new_season.map_pool.all()` == `[ArenaMap(id=42)]`.
   - When `latest_completed.map_mode == "random_per_round"` with snapshot `[1, 2, 3]` and all 3 ArenaMaps exist ⇒ `new_season.map_pool.all()` is the 3 maps.
   - Defensive: snapshot `[1, 2, 999]` but map 999 was deleted ⇒ `new_season.map_pool.all()` is `[ArenaMap(id=1), ArenaMap(id=2)]` (999 silently dropped via `filter(id__in=)`).
   - Mode is read from `.map_mode` (live field), POOL is read from `.starting_map_pool_ids_json` (snapshot), NOT from `.map_pool` (live M2M) — verified by mutating the live M2M after activation and asserting the carry-forward uses the snapshot value.

5. **`matches/tests/test_lg01d_tasks.py`** — new class `TestPlaySeasonTaskMapResolution`:
   - `play_season_task` calls `_resolve_fixture_map` once per fixture (mock the helper; assert `call_args_list` matches the fixture order).
   - `play_season_task` calls `ArenaMap.objects.in_bulk(pool_ids)` ONCE (NOT per-fixture) — assert via a patched `ArenaMap.objects.in_bulk` mock that `call_count == 1`.
   - `simulate_scheduled_round` is called with `arena_map=` kwarg matching `_resolve_fixture_map`'s return.
   - When `season.map_mode == "none"` ⇒ all `arena_map=` kwargs are `None` ⇒ existing simulator-call assertions still hold (LG-01d existing tests are unaffected).
   - When `season.map_mode == "single"` with 1 map ⇒ every `arena_map=` is that map.
   - When `season.map_mode == "random_per_round"` ⇒ `arena_map=` varies across fixtures.

6. **`matches/tests/test_league_dashboard.py`** — new class `TestLg01jLeagueDashboardMapConfig`:
   - DOM id `league-dashboard-map-config` is present in the rendered template for all 4 cases.
   - `displayed_season is None` ⇒ label is the locked string `"Map: 3-zone fallback (no map)"`.
   - `Season.map_mode == "none"` ⇒ same label.
   - `Season.map_mode == "single"` with map "Alpha" ⇒ `"Map: Single — Alpha"` (em-dash U+2014 — assert byte-level).
   - `Season.map_mode == "single"` with snapshot `[42]` but map 42 deleted ⇒ `"Map: Single — (map deleted)"`.
   - `Season.map_mode == "random_per_round"` with maps Alpha, Bravo, Charlie ⇒ `"Map: Random per Round (3 maps: Alpha, Bravo, Charlie)"` (alphabetical sort by `name`).
   - Empty pool with `random_per_round` ⇒ `"Map: Random per Round (no maps)"`.
   - The label appears between `league-dashboard-action-button` and `league-dashboard-standings-snippet` (DOM order assertion via simple string-index).

7. **`matches/tests/test_season_dashboard_view.py`** — new class `TestLg01jSeasonDashboardMapConfig`. Same assertions as the League dashboard class but against DOM id `season-dashboard-map-config` rendered inside `templates/seasons/dashboard.html`.

The Tests agent writes failing tests against the locked DOM ids + helper return shape + label strings + form `clean()` errors BEFORE the Code agent lands implementation. Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` body — they assert on the call-site contract (`arena_map=` kwarg) only.

## Scope-out (locked)

- **No edit URL after create** (mid-League changes are admin-only — Django admin gains `filter_horizontal=("teams", "map_pool")` on the existing `SeasonAdmin`, which is the ONLY admin change).
- **No mode (b) per-sub-league rotation** (deferred to SUB-01 post-CAR-03 — already in PLAN.md). No third enum value is reserved at LG-01j; when SUB-01 lands, a new migration will add it.
- **No new ADR** (the decisions are reversible: model fields + a deterministic helper. No architectural shift that needs an ADR record).
- **No `score_averages` / batch-sim path change** (LG-01j is league-only — `score_averages` consumes `BatchSimulator.run` directly, NOT `play_season_task` / `play_week`, and is unaffected).
- **No `master_seed` UI exposure** (the per-fixture seed is computed internally; no user-facing seed input).
- **No edit to LG-01f sidebar / LG-01h top-bar** (no new placeholder URLs ship at LG-01j; the sidebar / top-bar shape from LG-01h is preserved verbatim).
- **No simulation mechanics change** ⇒ **no Score Calibration re-baseline obligation triggered by LG-01j alone** (the change folds into the existing pending post-MOVE-01 re-baseline alongside MOVE-02 / MOVE-03 / MOVE-04 / SIM-09).
- **No edit to `simulate_scheduled_round` / `simulate_match` / `_flush_to_db`** (the simulator already supports the `arena_map=` kwarg per SIM-09).
- **No edit to `select_play_fixtures` / `find_next_matchday` / `matches/season_dashboard.py` pure module** (the frozen no-Django import allowlist in `season_dashboard.py` is preserved — the helper lives in `matches/tasks.py`).
- **No `Season.archive` / "edit-draft" UI** (LG-01j inherits the existing draft-edit story).
- **No edit to `LeagueAdmin`** (the new fields surface via the default `SeasonAdmin` form-render). The ONLY admin change is `SeasonAdmin.filter_horizontal = ("teams", "map_pool")` (extending the existing `("teams",)` tuple).
- **No API / DRF endpoint** (LG-01j is pure server-rendered).
- **No edit to `MatchSetupForm` / `SingleRoundSetupForm`** (sandbox flows already have an `arena_map` field — unchanged).
- **No backfill** (pre-LG-01j Seasons take the `map_mode="none"` default + empty pool + `None` snapshot, which yields 3-zone fallback at simulation time — the LG-01d behaviour preserved).
- **No `django.contrib.messages` flash** (creation success redirects to `season_standings` per LG-01b, no flash message).
- **No JS framework / htmx / Alpine / inline `<script>`** (LG-01h scope-out preserved; default `SelectMultiple` widget is fine for the small confirmed-map list).
- **No new dependency**.
- **No new CONTEXT.md section** (3 entries appended to the existing `### League and seasons` section, NOT a new section).
- **No edit to `MatchJob` model** (the task body change is internal to `play_season_task`; job progress tracking is unaffected).

## Locked Names Index

### Model field names (3 NEW, on `Season`)
- `map_mode` (CharField, choices, default `"none"`, max_length 32)
- `map_pool` (M2M to `ArenaMap`, blank=True, related_name `"seasons_using_pool"`)
- `starting_map_pool_ids_json` (JSONField, null=True, default=None)

### Mode enum literals (3 — locked everywhere)
- `"none"` (display `"3-zone fallback"`)
- `"single"` (display `"Single map"`)
- `"random_per_round"` (display `"Random per Round"`)

### Migration filename
- `laserforce_simulator/matches/migrations/0031_season_map_mode_pool.py`

### Helper signature (NEW)
- `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None`
- Module-level, no class. Pure (no ORM). Lives in `matches/tasks.py`.

### Form field names (2 NEW on `CreateLeagueForm`)
- `map_mode` (`forms.ChoiceField`, choices from model field, initial `"none"`, required=True)
- `map_pool` (`forms.ModelMultipleChoiceField`, queryset `_maps_with_confirmed_config()`, required=False)

### Form helper (NEW)
- `matches.forms._maps_with_confirmed_config() -> QuerySet[ArenaMap]` — module-level helper **already exists** (used by `MatchSetupForm` / `SingleRoundSetupForm`); returns `ArenaMap` rows with at least one confirmed `MapZoneConfig`. LG-01j reuses the existing helper verbatim — no edit, no redefinition.

### View names (NO new URL names ship at LG-01j)
- EXTENDED: `matches.views.league_create` (body extension only)
- EXTENDED: `matches.views.next_season` (body extension only)
- EXTENDED: `matches.views._build_dashboard_context` (1 new context key)
- No new `path(...)` entries, no new URL names.

### Task / sync-path function names
- EXTENDED: `matches.tasks.play_season_task` (body extension only — same `@shared_task` decorator, same name)
- EXTENDED: `matches.views.play_week` (or wherever the LG-01d synchronous path lives — same name, body extension only)

### Template names (3 MODIFIED, 0 NEW)
- `templates/leagues/dashboard.html` (MODIFIED)
- `templates/seasons/dashboard.html` (MODIFIED)
- `templates/leagues/create.html` (MODIFIED)

### DOM ids (4 NEW)
- `league-dashboard-map-config` (on the `<div>` rendering `map_config_label` in the League dashboard)
- `season-dashboard-map-config` (on the `<div>` rendering `map_config_label` in the Season dashboard)
- `league-create-map-mode` (on the `<select>` for `map_mode` in the create form)
- `league-create-map-pool` (on the `<select multiple>` for `map_pool` in the create form)

### Label string literals (5 locked — tests assert byte-equal)
- `"Map: 3-zone fallback (no map)"`
- `"Map: Single — {name}"` (em-dash U+2014, single SPACE on both sides) — e.g. `"Map: Single — Alpha Arena"`
- `"Map: Single — (map deleted)"`
- `"Map: Random per Round ({n} maps: {comma_joined_names})"` (alphabetical by name, ascending) — e.g. `"Map: Random per Round (3 maps: Alpha, Bravo, Charlie)"`
- `"Map: Random per Round (no maps)"`

### Form `clean()` error messages (3 locked — tests assert byte-equal)
- `"Map pool must be empty when Map mode is '3-zone fallback'."`
- `"Map pool must contain exactly 1 map when Map mode is 'Single map'."`
- `"Map pool must contain at least 1 map when Map mode is 'Random per Round'."`

### `_resolve_fixture_map` `ValueError` message (locked)
- `f"Unknown map_mode: {mode!r}"` — for unknown modes; tests assert via `with self.assertRaises(ValueError) as cm: ...; self.assertIn("Unknown map_mode:", str(cm.exception))`.

### Seed-string format (locked, byte-for-byte)
- `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"`
- 5 components, pipe `|`-separated, no spaces, in that exact order.

### Context key (NEW on `_build_dashboard_context`)
- `map_config_label: str` (the 12th key on the existing 11-key dict)

### CONTEXT.md term names (3 NEW)
- `Map mode`
- `Map pool`
- `Per-fixture map resolution`
- All appended to `### League and seasons` section, in that order, AFTER the existing `Team schedule` entry.

### Test file names (2 NEW)
- `matches/tests/test_lg01j_season_map_config.py`
- `matches/tests/test_lg01j_resolve_fixture_map.py`

### Test file names (5 EXTENDED)
- `matches/tests/test_league_create.py`
- `matches/tests/test_lg01e_next_season.py`
- `matches/tests/test_lg01d_tasks.py`
- `matches/tests/test_league_dashboard.py`
- `matches/tests/test_season_dashboard_view.py`

### Test class names (NEW)
- `TestSeasonMapModeField` (in `test_lg01j_season_map_config.py`)
- `TestSeasonMapPoolField` (in `test_lg01j_season_map_config.py`)
- `TestSeasonStartingMapPoolSnapshot` (in `test_lg01j_season_map_config.py`)
- `TestSeasonStartSeasonSnapshotsMapPool` (in `test_lg01j_season_map_config.py`)
- `TestResolveFixtureMapNone` (in `test_lg01j_resolve_fixture_map.py`)
- `TestResolveFixtureMapSingle` (in `test_lg01j_resolve_fixture_map.py`)
- `TestResolveFixtureMapRandomPerRound` (in `test_lg01j_resolve_fixture_map.py`)
- `TestResolveFixtureMapUnknownMode` (in `test_lg01j_resolve_fixture_map.py`)
- `TestResolveFixtureMapMissingMap` (in `test_lg01j_resolve_fixture_map.py`)
- `TestLeagueCreateMapMode` (in `test_league_create.py`)
- `TestLeagueCreateMapPool` (in `test_league_create.py`)
- `TestNextSeasonMapConfigCarryForward` (in `test_lg01e_next_season.py`)
- `TestPlaySeasonTaskMapResolution` (in `test_lg01d_tasks.py`)
- `TestLg01jLeagueDashboardMapConfig` (in `test_league_dashboard.py`)
- `TestLg01jSeasonDashboardMapConfig` (in `test_season_dashboard_view.py`)

### Admin change (only one)
- `matches/admin.py` `SeasonAdmin.filter_horizontal` extends from `("teams",)` to `("teams", "map_pool")`. No other admin change.

### M2M reverse-accessor name
- `arena_map.seasons_using_pool` (locked via `related_name="seasons_using_pool"` on `Season.map_pool`).
