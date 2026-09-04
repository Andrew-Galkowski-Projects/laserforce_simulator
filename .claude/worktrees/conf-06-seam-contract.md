# CONF-06 — Per-Conference rotating map pools — SEAM CONTRACT

Single source of truth for the three parallel agents (Code / Tests / Docs).
Every name, signature, field, error string, DOM id and migration name below is
**locked**. Drift is a failing test, not a judgement call.

**App:** `matches`. **Branch:** `conf-06-conference-map-pools`.
**Repo paths note:** the Django project is nested at
`laserforce_simulator/laserforce_simulator/`. Paths below are app-relative
(`matches/…`, `templates/…`) unless stated otherwise.

**One-line intent:** a fifth `Season.map_mode` — `rotate_by_conference` — under
which each **Conference** carries its own author-ordered `ArenaMap` rotation and
a fixture resolves its map from *its own* Conference's rotation by matchday
(`ids[matchday % len(ids)]`). "Nevada games on the Nevada maps."

---

## 0. Domain vocabulary (verbatim; CONTEXT.md is the authority)

| Term | Meaning |
|---|---|
| **Map mode** | `Season.map_mode` ∈ `{none, single, random_per_round, rotate_by_matchday, rotate_by_conference}`. |
| **Conference rotation** | One Conference's author-ordered list of `ArenaMap` ids. Live = `Conference.map_rotation_ids_json`; frozen = `Conference.starting_map_rotation_ids_json`. |
| **Author order** | The order the human typed the rows in. **NEVER sorted** — the matchday index keys directly into it. |
| **Activation snapshot** | The `starting_*_json` twin written by `Season.start_season()`; the ONLY list the play loop reads. |
| **Matchday** | Season-global, **1-based**. Rotation index is `matchday % len(ids)` applied to the 1-based value directly (see §3, note B). |

---

## 1. Model changes — `matches/models.py`

### 1.1 New enum value on `Season.MAP_MODE_CHOICES`

Appended as the **fifth and last** tuple, exact text:

```python
MAP_MODE_CHOICES = (
    ("none", "3-zone fallback"),
    ("single", "Single map"),
    ("random_per_round", "Random per Round"),
    ("rotate_by_matchday", "Rotate by matchday"),
    ("rotate_by_conference", "Rotate by Conference"),
)
```

| Value | Label |
|---|---|
| `rotate_by_conference` | `Rotate by Conference` |

`Season.clean()` needs **no change** — it derives `valid_map_modes` from
`MAP_MODE_CHOICES`, so the new value validates automatically. Do not touch it.

### 1.2 Two new fields on `Conference`

Declared immediately after the existing `starting_team_ids_json` field, exact text:

```python
    # CONF-06 — author-ordered per-Conference arena-map rotation (live) + its
    # activation snapshot. Read by the ``rotate_by_conference`` map mode;
    # author order is PRESERVED (NOT id-sorted) in both, since the matchday
    # index keys directly into the ordered list. The order-preserving twin of
    # the Season-level SUB-01 pair, one level down on the partition.
    map_rotation_ids_json = models.JSONField(null=True, blank=True, default=None)
    starting_map_rotation_ids_json = models.JSONField(
        null=True, blank=True, default=None
    )
```

| Field | Type | Null | Blank | Default | Written by |
|---|---|---|---|---|---|
| `Conference.map_rotation_ids_json` | `JSONField` | yes | yes | `None` | `manage_conferences` POST |
| `Conference.starting_map_rotation_ids_json` | `JSONField` | yes | yes | `None` | `Season.start_season()` |

`None` is the pre-authoring / pre-activation sentinel; `[]` is an authored-empty
list. Consumers must read `(x or [])`.

### 1.3 Migration — exact filename + dependency

| | |
|---|---|
| File | `matches/migrations/0061_conference_map_rotation.py` |
| Dependency | `("matches", "0060_alter_seasonphase_tournament_mode")` |
| Operations | `AddField(model_name="conference", name="map_rotation_ids_json", …)` then `AddField(model_name="conference", name="starting_map_rotation_ids_json", …)` |

Generate with `python laserforce_simulator/manage.py makemigrations matches
--name conference_map_rotation`. No `AlterField` on `Season.map_mode` is
expected — `choices` changes are non-schema for `CharField`; if `makemigrations`
emits one anyway, keep it **in the same 0061 file**, after the two `AddField`s.

### 1.4 `Season.start_season()` — two edits, both inside the existing method

**(a) Activation guard.** Placed immediately **after** the existing
`self.teams.count() < 2` check and **before** the `starting_team_ids_json`
assignment:

```python
        if self.map_mode == "rotate_by_conference":
            confs = list(self.conferences.all())
            if len(confs) < 2 or any(
                not (c.map_rotation_ids_json or []) for c in confs
            ):
                raise ValidationError(
                    "A Season with map mode 'Rotate by Conference' requires at "
                    "least 2 conferences, each with at least 1 rotation map."
                )
```

Locked message (one string covering both failure shapes):

> `A Season with map mode 'Rotate by Conference' requires at least 2 conferences, each with at least 1 rotation map.`

**(b) Snapshot inside the EXISTING per-Conference loop.** Extend the loop body
and its `update_fields` — do not add a second loop:

```python
        for conf in self.conferences.all():
            conf.starting_team_ids_json = sorted(t.id for t in conf.teams.all())
            # CONF-06 — snapshot the rotation, author order PRESERVED
            # (``list(...)``, NOT ``sorted(...)``). Empty/None ⇒ ``[]``.
            conf.starting_map_rotation_ids_json = list(conf.map_rotation_ids_json or [])
            conf.save(
                update_fields=[
                    "starting_team_ids_json",
                    "starting_map_rotation_ids_json",
                ]
            )
```

The snapshot is written for **every** Season with Conferences, regardless of
`map_mode` (an unused snapshot is harmless and keeps the loop mode-agnostic).

---

## 2. New pure helper — `matches/forms.py`

Module-level, placed immediately **below** `_maps_with_confirmed_config()`
(before `class MatchSetupForm`). Pure: no ORM, no `self`, no Django form state.

```python
def parse_rotation_ids(
    raw: "str | None", valid_map_ids: "set[int]"
) -> "tuple[list[int], list[str]]":
    """Parse a comma-separated ArenaMap-id string into an AUTHOR-ORDERED id
    list plus a list of human error strings (pure; no ORM, no form state)."""
```

**Locked algorithm** (extracted verbatim from the current `CreateLeagueForm.clean`
body, lines ~372-397):

1. `for token in (raw or "").split(",")` → `token = token.strip()`.
2. Empty token → skip silently (no error, no id).
3. `int(token)` raises `(TypeError, ValueError)` → append
   `"Map rotation contains an invalid id."` to errors, `continue`.
4. `map_id not in valid_map_ids` → append
   `"Map rotation contains an unknown map id."` to errors, `continue`.
5. Otherwise append `map_id` to the id list.
6. Return `(ids, errors)`.

**Locked properties Tests may rely on:**

| Property | Locked behaviour |
|---|---|
| Order | ids come back in **author order**, never sorted. |
| Duplicates | duplicate ids are **kept** (`"3,3"` → `[3, 3]`). |
| Errors per token | one error string **per offending token**, not deduped (`"a,b"` → two identical "invalid id" strings). |
| Mixed | valid ids from a partly-bad string are still returned alongside the errors. |
| `None` / `""` | `([], [])`. |
| Whitespace | `" 3 , 4 "` → `[3, 4]`. |

**Verbatim error strings** (must not be reworded — the existing
`test_league_create.py` assertions already pin them):

| String | Attaches to |
|---|---|
| `Map rotation contains an invalid id.` | `CreateLeagueForm` field `map_rotation`; page-level `errors` list on `manage_conferences` |
| `Map rotation contains an unknown map id.` | same |

---

## 3. Resolver changes — `matches/tasks.py`

### 3.1 `_resolve_fixture_map` gains a fourth, keyword-with-default argument

```python
def _resolve_fixture_map(
    season: "Season",
    fixture: "ScheduleFixture",
    pool_by_id: "dict[int, ArenaMap]",
    conf_by_team: "dict[int, Conference] | None" = None,
) -> "ArenaMap | None":
```

Add `Conference` to the existing `TYPE_CHECKING` import:
`from .models import Conference, Season`.

**Backwards compatibility is load-bearing:** all 37 existing 3-argument call
sites in the test suite (`matches/tests/test_season_map_config.py` and
`matches/tests/test_league_play.py`) must keep working **unchanged** — do not
edit them to pass the new argument. The parameter is optional and `None` is
treated as `{}`.

### 3.2 The new branch — placed **after** the `rotate_by_matchday` branch, before the final `raise`

```python
    if mode == "rotate_by_conference":
        conf = (conf_by_team or {}).get(fixture.team_a_id)
        ids = (conf and conf.starting_map_rotation_ids_json) or []
        if not ids:
            return None
        return pool_by_id.get(ids[fixture.matchday % len(ids)])
```

**Notes, both locked:**

- **(A) No RNG.** This branch never touches `random`. It cannot perturb the
  SIM-07 seed chain.
- **(B) 1-based matchday, modulo applied directly.** `fixture.matchday` is
  1-based and `matchday % len(ids)` is applied to it unchanged. This
  deliberately mirrors the shipped `rotate_by_matchday` formula byte-for-byte.
  **Do NOT "fix" it to `(matchday - 1) % len(ids)`.** With a 2-map rotation,
  matchday 1 → `ids[1]`, matchday 2 → `ids[0]`. That is correct-by-contract.

**Defensive `None` returns** (never an exception): missing/`None` `conf_by_team`;
`team_a_id` absent from the map; `conf.starting_map_rotation_ids_json` is
`None`/`[]`; the resolved id was deleted from `ArenaMap` after activation
(`pool_by_id.get` misses).

Unknown modes still `raise ValueError(f"Unknown map_mode: {mode!r}")` — unchanged.

### 3.3 New pure helper `_fixture_map_ids`

Module-level, placed **immediately after** `_resolve_fixture_map`. No ORM.

```python
def _fixture_map_ids(
    season: "Season", conf_by_team: "dict[int, Conference] | None" = None
) -> "list[int]":
    """Union of every ArenaMap id the fixture resolver can reach this Season —
    the single argument to the play loops' one ``ArenaMap.objects.in_bulk``."""
```

**Locked algorithm** (order-preserving concatenation; duplicates are harmless
for `in_bulk` and are NOT deduped):

1. `ids = list(season.starting_map_pool_ids_json or [])`
2. `ids += list(season.starting_map_rotation_ids_json or [])`
3. Iterate `(conf_by_team or {}).values()` in dict order, skipping any
   Conference whose `conf.id` was already seen (dedupe **Conferences** by id,
   because the same Conference appears once per member team); for each first
   sighting, `ids += list(conf.starting_map_rotation_ids_json or [])`.
4. Return `ids`.

**Locked properties Tests may rely on:** flat `list[int]`; Season pool ids
first, then Season rotation ids, then per-Conference rotation ids in
first-seen Conference order; each Conference contributes its ids exactly once
even when it has N member teams; map-id duplicates across sources are retained;
`_fixture_map_ids(season)` with no Conferences equals the old
`(pool or []) + (rotation or [])` expression exactly.

---

## 4. Call-site edits — three sites, identical shape

Every site must end up in this order: **build `conf_by_team` → call
`_fixture_map_ids` → `in_bulk` → per-fixture `_resolve_fixture_map(...,
conf_by_team=conf_by_team)`**. All three currently build `conf_by_team` *after*
the `in_bulk`, so all three need the two-line reorder.

| # | File | Anchor | Edit |
|---|---|---|---|
| 1 | `matches/tasks.py` | `play_season_task`, ~lines 288-315 | Move `conf_by_team = season.conference_by_team_id()` **above** the `pool_by_id` assignment; replace the `in_bulk((… or []) + (… or []))` argument with `_fixture_map_ids(season, conf_by_team)`; add `conf_by_team=conf_by_team` to the `_resolve_fixture_map(...)` call. |
| 2 | `matches/league_views.py` | `play_week`, ~lines 3200-3212 | Same three changes. Extend the deferred import to `from matches.tasks import _fixture_map_ids, _resolve_fixture_map`. |
| 3 | `matches/league_views.py` | manager live-game path, ~lines 3783-3792 | Same three changes (`conf_by_team` currently sits two lines *below* the resolver call — move it above `pool_by_id`). Extend the deferred import likewise. |

No other behaviour at these sites changes. For every pre-existing Season
(`map_mode != "rotate_by_conference"`) the `in_bulk` argument and the resolved
map are byte-identical to today.

### 4.1 Rollover downgrade — `matches/league_views.py::_run_season_rollover` (~line 4838)

In the `Season.objects.create(...)` call, replace the `map_mode` kwarg:

```python
        # CONF-06 — the rollover carries no Conferences, so a carried
        # ``rotate_by_conference`` mode would resolve None for every fixture
        # all season. Downgrade it to the 3-zone fallback; carry every other
        # mode verbatim.
        map_mode=(
            "none"
            if latest_completed.map_mode == "rotate_by_conference"
            else latest_completed.map_mode
        ),
```

Nothing else in the rollover changes: no per-Conference rotation is carried, and
the Season-level `map_rotation_ids_json` carry-forward stays as-is.

---

## 5. Form guards — `matches/forms.py::CreateLeagueForm.clean`

### 5.1 Refactor (behaviour-preserving)

Replace the inline token loop with:

```python
        rotation_ids, rotation_errors = parse_rotation_ids(
            cleaned_data.get("map_rotation", ""), valid_map_ids
        )
        for message in rotation_errors:
            self.add_error("map_rotation", forms.ValidationError(message))
        cleaned_data["map_rotation_ids"] = rotation_ids
```

`valid_map_ids` is still
`set(_maps_with_confirmed_config().values_list("id", flat=True))`.
`cleaned_data["map_rotation_ids"]` keeps its exact current shape/name.

### 5.2 New `rotate_by_conference` guard block

Placed **after** the existing `rotate_by_matchday` block and **before**
`return cleaned_data`. Uses `add_error` (NOT `raise`) so all three can surface
together in one submission:

```python
        if mode == "rotate_by_conference":
            if pool_count > 0:
                self.add_error(
                    "map_pool",
                    forms.ValidationError(
                        "Map pool must be empty when Map mode is "
                        "'Rotate by Conference'."
                    ),
                )
            if rotation_count > 0:
                self.add_error(
                    "map_rotation",
                    forms.ValidationError(
                        "Map rotation must be empty when Map mode is "
                        "'Rotate by Conference'."
                    ),
                )
            if (cleaned_data.get("number_of_conferences") or 0) < 2:
                self.add_error(
                    "number_of_conferences",
                    forms.ValidationError(
                        "Map mode 'Rotate by Conference' requires at least "
                        "2 conferences."
                    ),
                )
```

| Field key | Verbatim error string | Trigger |
|---|---|---|
| `map_pool` | `Map pool must be empty when Map mode is 'Rotate by Conference'.` | any Season-level pool map selected |
| `map_rotation` | `Map rotation must be empty when Map mode is 'Rotate by Conference'.` | any Season-level rotation id submitted |
| `number_of_conferences` | `Map mode 'Rotate by Conference' requires at least 2 conferences.` | `number_of_conferences` missing, `0`, or `1` |

The four existing per-mode blocks are untouched.

---

## 6. `manage_conferences` — view + context — `matches/league_views.py`

Import extension at line ~43:
`from .forms import CreateLeagueForm, _maps_with_confirmed_config, parse_rotation_ids`

### 6.1 `_manage_conferences_context` — new parameter + new context keys

```python
def _manage_conferences_context(
    season: Season,
    teams: "list",
    is_editable: bool,
    *,
    submitted_names: "list[str] | None" = None,
    submitted_assignments: "dict[int, int | None] | None" = None,
    submitted_rotations: "list[str] | None" = None,
    errors: "list[str] | None" = None,
) -> dict:
```

New/changed context keys (existing keys keep their exact current shape,
`conf_names` included — the team `<select>` options still read it):

| Key | Shape | Meaning |
|---|---|---|
| `conf_rows` | `list[dict]` — `{"name": str, "rotation": str}` | One row per Conference, index-aligned with `conf_names`. `rotation` is the comma-joined id string (`"3,7,3"`), from `submitted_rotations[i]` on a failed POST, else `",".join(str(i) for i in conf.map_rotation_ids_json or [])`. Missing index ⇒ `""`. |
| `confirmed_maps` | `list[ArenaMap]` | `list(_maps_with_confirmed_config())` — the picker's allowed maps. |
| `map_mode` | `str` | `season.map_mode`; the template shows the rotation UI's "required" hint only when it equals `"rotate_by_conference"`. |
| `readonly_groups[i]["rotation_names"]` | `list[str]` | Author-ordered map names resolved from `conf.starting_map_rotation_ids_json` (fall back to `map_rotation_ids_json` when the snapshot is `None`); deleted ids drop out silently. |

### 6.2 POST handling — locked order of operations

1. Read `names = request.POST.getlist("conference_name")` (unchanged) and
   **`rotations = request.POST.getlist("conference_rotation")`** (new). Index
   `i` of `rotations` aligns with index `i` of `names` (both inputs live inside
   the same `.conference-row`, so browsers submit them in matching document
   order). Missing index ⇒ treat as `""`.
2. Run `_validate_conference_partition(...)` exactly as today → `errors`,
   `normalized`. **`_validate_conference_partition` itself is UNCHANGED** —
   no new parameter, no new error string inside it.
3. Parse each row: `per_conf_ids[i], errs = parse_rotation_ids(raw_i,
   valid_map_ids)` where `valid_map_ids =
   set(_maps_with_confirmed_config().values_list("id", flat=True))`. Append
   `errs` to the page-level `errors` list, in submitted Conference order. This
   runs **for every map mode** — a malformed id is invalid regardless of mode.
4. Empty-rotation guard, appended **once** (not per Conference) and **only**
   when `season.map_mode == "rotate_by_conference"` and `names` is non-empty
   and any parsed row is empty:
   `errors.append("Each conference needs at least 1 rotation map.")`
5. If `errors`: re-render with `submitted_rotations=[r for r in rotations]`
   alongside the existing `submitted_names` / `submitted_assignments`. Same
   template, same 200 response as today.
6. On success, inside the existing `transaction.atomic()` block, set the
   rotation when creating each Conference — `normalized` preserves submitted
   index order, so index `i` maps straight across:

```python
            for ordinal, (name, team_ids) in enumerate(normalized, start=1):
                conf = Conference.objects.create(
                    season=season,
                    name=name,
                    ordinal=ordinal,
                    map_rotation_ids_json=per_conf_ids[ordinal - 1],
                )
                conf.teams.set(team_ids)
```

Stored value is the parsed list verbatim — `[]` for an authored-empty rotation
(never `None` once the page has been saved).

7. `messages.success(request, "Conferences saved.")` + redirect — unchanged.
8. Non-draft POST still returns `HttpResponseBadRequest("Conferences can only
   be edited while the Season is in draft.")` — unchanged. The rotation is
   therefore editable **only** while the Season is `draft`, inheriting the
   page's existing frozen-partition behaviour.

### 6.3 Locked error strings on this page

| String | Where |
|---|---|
| `Each conference needs at least 1 rotation map.` | page-level `errors` list, `#manage-conferences-errors` |
| `Map rotation contains an invalid id.` | same list (from `parse_rotation_ids`) |
| `Map rotation contains an unknown map id.` | same list (from `parse_rotation_ids`) |

Existing partition strings are unchanged and must not be reworded:
`Conference names cannot be empty.` / `Every team must be assigned to a
conference.` / `Each conference needs at least 2 teams.`

---

## 7. Template — `templates/seasons/manage_conferences.html`

Mirrors the `league-create-map-rotation` precedent in
`templates/leagues/create_advanced.html`: a hidden comma-joined input per
Conference row, filled by a vanilla-JS composer on `change` / `submit`.

### 7.1 Locked DOM ids

| id | Element | Notes |
|---|---|---|
| `manage-conferences-confirmed-maps` | `<script type="application/json">` | `[{"id": …, "name": …}, …]` from `confirmed_maps`; names via `\|escapejs`. Rendered once, in the editable branch. |
| `manage-conferences-rotation-{i}` | `<input type="hidden" name="conference_rotation">` | `i` = `forloop.counter0` over `conf_rows`. `value="{{ row.rotation }}"`. Inside `.conference-row`. |
| `manage-conferences-rotation-composer-{i}` | `<div class="conference-rotation-composer">` | Holds the map rows for Conference `i`. |
| `manage-conferences-rotation-add-{i}` | `<button type="button">` | Label text `+ Add map`. |
| `manage-conferences-rotation-row-{i}-{j}` | `<div class="conference-rotation-row">` | `j` = per-composer row sequence. |
| `manage-conferences-rotation-select-{i}-{j}` | `<select class="conference-rotation-select">` | Options = `confirmed_maps`; `aria-label="Rotation map"`. |
| `manage-conferences-readonly-rotation-{i}` | `<ul>` (or `<ol>`) | `i` = `forloop.counter0` over `readonly_groups`; lists `rotation_names` in author order. Rendered inside the existing `#manage-conferences-readonly` block. |

Existing ids are **unchanged and must survive**:
`manage-conferences-empty`, `manage-conferences-errors`,
`manage-conferences-form`, `manage-conferences-list`,
`manage-conferences-name-{i}`, `manage-conferences-add`,
`manage-conferences-team-{team_id}`, `manage-conferences-submit`,
`manage-conferences-readonly`.

### 7.2 Locked JS behaviour

- Each `.conference-row` owns one composer. Adding/removing a map row
  re-serializes **that row's** hidden input to the comma-joined ids in
  document order.
- The `+ Add conference` button must create the full new row markup —
  name input **and** hidden rotation input **and** composer **and** add-map
  button — with the next `i` index, then wire it. Existing `rebuildSelects()`
  behaviour for the team `<select>`s is untouched.
- Removing a Conference row removes its rotation input with it (it is a child
  of `.conference-row`), keeping `conference_name` / `conference_rotation`
  getlists index-aligned.
- All hidden inputs re-serialize on `form` `submit`.
- A `value` pre-filled from `conf_rows[i].rotation` must be **rehydrated into
  composer rows on page load**, so an existing partition round-trips without
  the user re-picking maps.
- ASCII only in the JS and in any console output (Windows cp1252 console).

### 7.3 Copy (form-text under the rotation composer)

> Author-ordered rotation for this conference, used when Map mode is
> "Rotate by Conference". A Round's map is
> `rotation[matchday % len(rotation)]`.

---

## 8. Test boundary — `matches/tests/`, **no new test file**

Tests assert **only** against the public seam below. Tests must **NOT** reach
into: the JS composer internals, `_manage_conferences_context`'s private
keyword names beyond those listed, the migration file's internal AST, or the
line ordering inside the play loops.

| File | Owns |
|---|---|
| `matches/tests/test_season_map_config.py` | Pure unit tests, **no DB**: the `rotate_by_conference` branch of `_resolve_fixture_map` with hand-built stub `season` / `fixture` / `Conference` objects (incl. the 1-based-modulo expectation from §3.2 note B, and every defensive-`None` path); `_fixture_map_ids` (order, Conference dedupe by `conf.id`, no-Conference equivalence to the old expression); `parse_rotation_ids` (all six locked properties in §2 + both error strings). |
| `matches/tests/test_conference.py` | DB: `start_season()` writes `starting_map_rotation_ids_json` in **author order** (assert a deliberately non-ascending list survives); the snapshot is `[]` when the live list is `None`/`[]`; the activation `ValidationError` fires for `rotate_by_conference` with 0 Conferences and with any empty rotation, and does **not** fire for other modes. |
| `matches/tests/test_manage_conferences.py` | View: GET (draft) renders `manage-conferences-rotation-0` and `manage-conferences-confirmed-maps`; POST saves per-Conference `map_rotation_ids_json` in submitted order; POST under `rotate_by_conference` with an empty rotation re-renders 200 with `Each conference needs at least 1 rotation map.`; POST with a bad token re-renders with the verbatim parse error; non-draft GET renders `manage-conferences-readonly-rotation-0` and no editable rotation input; non-draft POST still 400s. |
| `matches/tests/test_league_next_season.py` | Rollover downgrade: a completed `rotate_by_conference` Season rolls to a new Season with `map_mode == "none"`; every other mode still carries verbatim. |
| `matches/tests/test_league_create.py` | `CreateLeagueForm` guards: all three §5.2 error strings under their exact field keys; a valid `rotate_by_conference` submission (empty pool, empty rotation, `number_of_conferences >= 2`) passes `is_valid()`. |

**Regression guard (any of the above files):** an existing
`rotate_by_matchday` / `random_per_round` / `single` / `none` Season resolves
byte-identical maps with and without a `conf_by_team` argument, and every
existing 3-argument `_resolve_fixture_map(...)` call still type-checks and runs.

---

## 9. Out of scope — do not implement

- **Per-fixture map override.** Deferred: fixtures are not persisted rows, so
  there is nowhere to hang an override.
- **Bracket / tournament map handling.** Regional-playoff and Worlds `Match`
  rows never carry an `arena_map`; they always run the 3-zone fallback.
  `rotate_by_conference` governs regular-season fixtures only.
- **Score re-baseline.** The new branch is unreachable without the new mode,
  and `_fixture_map_ids` reduces to the current expression when there are no
  Conferences, so every existing seeded outcome stays byte-identical. No
  fixture/expected-score file is to be regenerated.
- **`SeasonAdmin` / Django-admin surfaces** for the per-Conference rotation.
- **Carrying Conferences (or their rotations) through `next_season`.** The
  rollover downgrade in §4.1 is the whole of the rollover story.
- **Any change to `_validate_conference_partition`'s signature or strings.**
