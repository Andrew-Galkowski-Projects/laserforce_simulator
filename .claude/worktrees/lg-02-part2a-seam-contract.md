# LG-02-Part2a — `SeasonPhase` foundation slice — SEAM CONTRACT

**Status:** locked by grill 2026-06-04, ADR-0023, CONTEXT.md "Season phase".
**Artifact only** — this contract drives three parallel agents (code / tests /
docs). Build against the locked names verbatim.

## 0. One-paragraph summary

Introduce a `matches.models.SeasonPhase` model and retrofit the Season
read-path so the per-Season schedule is sourced through a single chokepoint on
`Season` instead of reading `Season.schedule_format` (and calling
`generate_schedule(...)`) inline at every site. **Zero user-visible change.**
A Season with **no** `SeasonPhase` rows behaves EXACTLY as today (implicit
single `round_robin` phase via a lightweight in-memory fallback — no DB row).
New Seasons get ONE explicit `round_robin` `SeasonPhase` (ordinal=1) at
creation time. NOT in scope: composer UI, multi-phase play loop, per-phase
format, tournament embed, member-night behaviour (Part2b/Part2c).

---

## 1. LOCKED NAMES

### 1.1 Model — `matches.models.SeasonPhase`

Declared in `matches/models.py` **immediately after the `Season` class** (and
before `Tournament`).

| Field | Type | Notes |
|---|---|---|
| `season` | `ForeignKey("matches.Season", on_delete=models.CASCADE, related_name="phases")` | Parent Season. |
| `ordinal` | `PositiveSmallIntegerField()` | **1-based** ordering within the Season. No default — set explicitly at create. |
| `phase_type` | `CharField(max_length=16, choices=PHASE_TYPE_CHOICES, default="round_robin")` | `max_length=16` headroom (`"member_night"` is 12 chars). |

**`PHASE_TYPE_CHOICES`** — class attribute on `SeasonPhase`, declares ALL THREE
values now (only `round_robin` has behaviour this slice):

```python
PHASE_TYPE_CHOICES = (
    ("round_robin", "Round-robin"),
    ("tournament", "Tournament"),
    ("member_night", "Member night"),
)
```

**`Meta`:**

```python
class Meta:
    ordering = ["ordinal"]
    constraints = [
        models.UniqueConstraint(
            fields=["season", "ordinal"],
            name="uniq_season_phase_ordinal",
        ),
    ]
```

**`__str__`** (locked shape): `f"{self.season} — phase {self.ordinal} ({self.phase_type})"`
(em-dash U+2014, matching the `Season.__str__` convention).

**NO FK to `Tournament` in this slice.** The forward `SeasonPhase → Tournament`
link is Part2b/Part2c. **NO per-phase `schedule_format` field** — the
`round_robin` phase resolves fixtures via the existing `Season.schedule_format`
(legacy, stays as-is).

### 1.2 Migration

- **Filename:** `matches/migrations/0041_season_phase.py`
- **Dependency:** `("matches", "0040_tournament_random_draw")` — **CONFIRMED**
  the latest matches migration is `0040_tournament_random_draw.py` (verified by
  Glob of `matches/migrations/` + reading `0040`'s header).
- **Operations:** a **single `CreateModel(SeasonPhase)`** carrying the
  `UniqueConstraint` and `Meta.ordering`. **NO `RunPython`, NO `RunSQL`, NO
  backfill, NO data migration** (ADR-0004 disposable-data precedent — same as
  the LG-01 `0029` and every prior `Season`/`Match` add).

### 1.3 Chokepoint on `Season` — exact signatures + return types

Two methods on `Season` (declared near the existing `_is_finished` /
`complete_if_finished` block). Both are **read-only / pure-derivation** (no DB
write, no RNG).

```python
def ordered_phases(self) -> list["SeasonPhase"]:
    """Return this Season's phases in ordinal order.

    When the Season has >= 1 persisted SeasonPhase row, returns
    list(self.phases.all()) (Meta.ordering = ["ordinal"] guarantees
    ordinal order). When the Season has ZERO rows, returns a one-element
    list holding a SINGLE UNSAVED implicit fallback phase (see 1.4) so a
    phase-less Season is indistinguishable downstream from a Season with
    one explicit round_robin phase.
    """

def scheduled_fixtures(self) -> list["ScheduleFixture"]:
    """Return the flat fixture list for this Season's schedule.

    THIS SLICE: returns exactly the round_robin phase's fixture list,
    sourced via generate_schedule(team_ids, self.schedule_format) where
    team_ids is resolved by the existing rule (see 3.0). Exactly ONE
    round_robin phase exists this slice (explicit or implicit fallback),
    so the return is the single RR fixture list. NO cross-phase
    composition, NO matchday offsetting (Part2c).

    Returns [] when team_ids has < 2 entries (mirrors the current
    guard at every existing call site) — never raises.
    """
```

- `ScheduleFixture` is the existing frozen dataclass from
  `matches/schedule_generator.py` — **consumed verbatim**, NOT redefined.
- `scheduled_fixtures()` is the single place that calls
  `generate_schedule(team_ids, self.schedule_format)` for the Season read-path
  after this slice (the model `_is_finished` site and the view/task sites all
  route through it — see §3).

### 1.4 Implicit-fallback representation (LOCKED)

The phase-less fallback is a **real but UNSAVED `SeasonPhase` instance** — NOT
a separate sentinel class, NOT a dict. Built by `ordered_phases()` as:

```python
SeasonPhase(season=self, ordinal=1, phase_type="round_robin")
```

constructed with **no `.save()`** (so `pk is None`; requires no DB row). Locked
rationale: a real instance keeps the downstream type uniform
(`list[SeasonPhase]`), exposes `.phase_type` / `.ordinal` / `.season` for
Part2b/2c without a shim, and `pk is None` is the unambiguous "implicit" marker
a test can assert on. A helper-builder may be factored as a private
`Season._implicit_phase()` (Code-agent discretion) but the returned object type
is locked to unsaved `SeasonPhase`.

### 1.5 Creation call sites (one explicit `round_robin` phase per new Season)

A new Season created in the **draft** flow gets ONE explicit row:

```python
SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
```

inserted **inside the existing `@transaction.atomic` block**, **immediately
after** the `Season.objects.create(...)` call (and after the `season.teams.add`
/ `season.map_pool.set` M2M lines are fine too — order within the atomic block
is not load-bearing, but it MUST be inside the same atomic block so a rollback
drops the phase too), at BOTH:

- `matches.league_views.league_create` — after the `Season.objects.create(...)`
  at `league_views.py:527` (the `@transaction.atomic`-decorated view, decorator
  at `:465`).
- `matches.league_views.next_season` — after the `Season.objects.create(...)`
  at `league_views.py:1900` (the `@transaction.atomic`-decorated view,
  decorator at `:1859`).

**No other creation site** writes a Season this slice (the LG-01b/LG-01e tests
exercise these two). Admin-created Seasons (`SeasonAdmin`) get the fallback
behaviour for free (zero rows ⇒ implicit phase) — no admin-side creation hook.

### 1.6 Admin

Register `SeasonPhase` for visibility/debug, inserted in `matches/admin.py`
**after** the existing `SeasonAdmin` registration (no existing registration
modified):

```python
@admin.register(SeasonPhase)
class SeasonPhaseAdmin(admin.ModelAdmin):
    list_display = ("season", "ordinal", "phase_type")
```

Plus a `SeasonPhaseInline(admin.TabularInline)` (`model = SeasonPhase`,
`extra = 0`) added to `SeasonAdmin.inlines`. **Locked decision:** the inline is
**OPTIONAL** (Code-agent discretion — if added, `SeasonAdmin.inlines =
(SeasonPhaseInline,)`; the standalone `SeasonPhaseAdmin` registration is the
mandatory part). No edit to `SeasonAdmin.list_display` / `filter_horizontal`.

---

## 2. SEAM CONTRACT — what crosses each boundary

| Producer | Consumer | Shape |
|---|---|---|
| `Season.ordered_phases()` | model `_is_finished` (future Part2c), Part2b/2c phase loop | `list[SeasonPhase]` (≥1; implicit member has `pk is None`) |
| `Season.scheduled_fixtures()` | `_is_finished`, `play_season_task`, `season_schedule`, `_build_dashboard_context`, `league_history` Play-Week preview, `team_schedule` | `list[ScheduleFixture]` (sorted `(matchday, team_a_id)`, `[]` when `< 2` teams) |
| `generate_schedule(team_ids, schedule_format)` | `Season.scheduled_fixtures()` ONLY (after this slice, for the Season read-path) | unchanged `list[ScheduleFixture]` |
| `season_dashboard.py` pure fns (`find_next_fixture`, `round_progress`, `find_next_matchday`, `select_play_fixtures`) | views / task | take a **fixtures list** as input — STAY PURE (see §4) |

---

## 3. CALL-SITE MAP — every file:function that changes

### 3.0 `team_ids` resolution rule (unchanged, centralised into the chokepoint)

The existing per-site rule resolves `team_ids` as: **draft Season** ⇒
`sorted(t.id for t in season.teams.all())`; **active/completed Season** ⇒
`list(season.starting_team_ids_json or [])`. The chokepoint
`scheduled_fixtures()` (and `_is_finished` for its own guard) **MUST preserve
this exact rule** — the implicit-fallback Season is almost always `draft`
(phases are auto-created at draft creation), but `scheduled_fixtures()` is
state-agnostic and applies the same draft-vs-snapshot rule the call sites use
today. `scheduled_fixtures()` returns `[]` when `len(team_ids) < 2`.

### 3.1 `matches/models.py`

| Function | Before | After (intent) |
|---|---|---|
| `Season._is_finished` (`:1001`) | `fixtures = generate_schedule(team_ids, self.schedule_format)` (`:1015`) with inline `team_ids = self.starting_team_ids_json or []` guard | `fixtures = self.scheduled_fixtures()`; the `< 2`-team / empty-fixtures early-`False` guard becomes `if not fixtures: return False`. **Behaviour-equivalence is load-bearing here:** a phase-less Season MUST return today's `_is_finished` result (the implicit RR phase produces the identical fixture list). |
| `Season.complete_if_finished` (`:984`) | calls `self._is_finished()` | **UNCHANGED** (routes through `_is_finished` which now uses the chokepoint). |
| `Season.ordered_phases` / `Season.scheduled_fixtures` | — | **NEW** (see 1.3). `scheduled_fixtures` is the sole `generate_schedule(...)` caller for the Season read-path. |
| `SeasonPhase` model | — | **NEW** (see 1.1). |

> Note: `Tournament.lock_and_build` (`models.py:1233`) also calls
> `generate_schedule(team_ids)` but for a **standalone Tournament**, NOT a
> Season — **DO NOT touch it.** The chokepoint is Season-only.

### 3.2 `matches/tasks.py`

| Function | Before | After (intent) |
|---|---|---|
| `play_season_task` (`:153`) | `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)` (`:190-192`) | `fixtures = season.scheduled_fixtures()`. The downstream `played_keys` build, `select_play_fixtures(fixtures, played_keys, max_matchdays)`, and the per-fixture `simulate_scheduled_round(...)` loop are **UNCHANGED**. The `from matches.schedule_generator import generate_schedule` deferred import (`:183`) is dropped from this function (the chokepoint owns it) — Code agent removes it only if no other use remains in the task body. |

### 3.3 `matches/league_views.py` (the season views live here, not `views.py`)

| Function | Before | After (intent) |
|---|---|---|
| `season_schedule` (`:335`) | `fixtures = generate_schedule(team_ids, season.schedule_format)` (`:371`), with the `< 2`-team empty-render guard above it (`:360`) | `fixtures = season.scheduled_fixtures()`. **Keep** the `< 2`-team early empty-`matchdays` render branch (the view's own short-circuit) — or rely on `scheduled_fixtures()` returning `[]`; Code-agent discretion, but the rendered output MUST be byte-identical. |
| `_build_dashboard_context` (`:550`) | `fixtures = generate_schedule(team_ids, displayed_season.schedule_format)` else `[]` (`:645-648`) | `fixtures = displayed_season.scheduled_fixtures()`. The `round_progress(fixtures, played_keys)` / `find_next_fixture(...)` calls downstream are **UNCHANGED**. |
| `league_history` (`:1280`) — Play-Week-preview block (`:1512`) | `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)` (`:1512-1514`) | `fixtures = season.scheduled_fixtures()`. Downstream `played_keys` + `select_play_fixtures(fixtures, played_keys, 1)` UNCHANGED. |
| `team_schedule` (`:1794`) | `fixtures = generate_schedule(team_ids, displayed_season.schedule_format)` (`:1822`), with inline draft-vs-snapshot `team_ids` resolve above (`:1817-1820`) | `fixtures = displayed_season.scheduled_fixtures()`. The per-team filtering of played rounds downstream is UNCHANGED. |
| `league_create` (`:466`) | `Season.objects.create(...)` at `:527` | **ADD** one `SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")` inside the existing atomic block after the create (see 1.5). |
| `next_season` (`:1860`) | `Season.objects.create(...)` at `:1900` | **ADD** the same `SeasonPhase.objects.create(...)` inside the existing atomic block after the create (see 1.5). |

> The top-of-file `from .schedule_generator import generate_schedule`
> (`league_views.py:37`) MAY remain if any non-Season-read use survives;
> Code agent removes it only when every `league_views` use is gone. (Likely
> all four reads route through the chokepoint, so it can be dropped — confirm.)

### 3.4 `matches/season_dashboard.py` — DO NOT EDIT (frozen pure module)

`find_next_fixture`, `round_progress`, `find_next_matchday`,
`select_play_fixtures`, `compute_leaders`, `LeaderRow` are **pure functions
with a FROZEN no-Django import allowlist** (`dataclasses`, `typing`,
`collections`), defended by `TestNoDjangoImportsLeaked`. They take a **fixtures
list** as input and stay pure. The chokepoint MUST NOT be called from inside
this module — the **caller** (view/task) builds the fixtures via
`season.scheduled_fixtures()` and passes the list in, exactly as today. **No
edit to this file.**

### 3.5 `matches/admin.py`

| Before | After |
|---|---|
| imports `League, Season, Tournament, ...` (`:3-10`); `SeasonAdmin` at `:20` | add `SeasonPhase` to the model import; add `SeasonPhaseAdmin` registration + optional `SeasonPhaseInline` after `SeasonAdmin` (see 1.6). No existing registration touched. |

### 3.6 Sites deliberately NOT changed

- `Tournament.lock_and_build` (`models.py:1233`) — standalone Tournament, not a
  Season. Untouched.
- `tournament_views.py:435` `generate_schedule(team_ids)` — RR crosstable for a
  standalone Tournament. Untouched.
- `Season.schedule_format` field declaration (`models.py:903`) — **stays
  as-is** (legacy; the RR phase reads it). No new per-phase format field.
- `forms.py` `CreateLeagueForm.schedule_format` — untouched (creation form).
- `matches/migrations/0029` etc. — untouched.

---

## 4. TEST BOUNDARY

### 4.1 What Tests assert against (public contract)

**Model (`matches/tests/test_season_phase.py`, NEW — pure model + DB):**
- `SeasonPhase` fields exist with locked types/defaults; `phase_type` default
  is `"round_robin"`; `PHASE_TYPE_CHOICES` declares all three values.
- `Meta.ordering == ["ordinal"]` (a Season with phases ordinal 2,1,3 iterates
  1,2,3).
- The `UniqueConstraint(fields=["season","ordinal"], name="uniq_season_phase_ordinal")`
  rejects a duplicate `(season, ordinal)` (IntegrityError) but allows the same
  `ordinal` across different Seasons.
- `season.phases` reverse accessor works; CASCADE delete (deleting the Season
  drops its phases).
- `Season.ordered_phases()`: a Season with explicit phases returns them in
  ordinal order; a phase-less Season returns a one-element list whose member is
  an **unsaved** `SeasonPhase` (`pk is None`), `phase_type == "round_robin"`,
  `ordinal == 1`, `season == self`.
- **Behaviour-equivalence guarantee (load-bearing):** for a `Season` with
  exactly ONE explicit `round_robin` phase vs an otherwise-identical phase-less
  `Season`, assert `scheduled_fixtures()` returns the **identical fixture
  list**, and `_is_finished()` / `complete_if_finished()` produce the
  **identical result** (e.g. both auto-complete after the same set of
  GameRounds; both stamp the same `champion_team`). Use a small enrolled-team
  set (N=2/N=3) with `starting_team_ids_json` snapshotted and hand-built
  `Match`/`GameRound` rows, asserting on schema-level outcomes (state flip,
  champion id) — **not** exact simulated point totals.
- `scheduled_fixtures()` returns `[]` for a `< 2`-team Season (no raise).

**Creation-on-Season-create (extend existing files, NO new file):**
- `matches/tests/test_league_create.py` — extend (append a test class or
  methods, no existing class modified): a successful `league_create` POST
  creates the Season AND exactly one `SeasonPhase(ordinal=1,
  phase_type="round_robin")` linked to it; a rolled-back create (the existing
  `Season.objects.create`-patched rollback test) leaves **zero**
  `SeasonPhase` rows (the phase is inside the same atomic block).
- `matches/tests/test_league_next_season.py` — extend: `next_season` creates
  the new draft Season AND its one `round_robin` `SeasonPhase`.

**Read-path equivalence at the view/task layer (extend existing files):**
- `matches/tests/views_tests.py` (or the LG-01-era season-view test file
  exercising `season_schedule`/`season_dashboard`) — assert the rendered
  schedule/dashboard for a phase-less Season (legacy data, no phase rows) is
  byte-identical to one with an explicit RR phase (proves the chokepoint
  fallback). A test class `TestSeasonPhaseReadPathEquivalence` is acceptable.
- `matches/tests/test_league_play.py` — `play_season_task` over a phase-less
  Season plays the same fixtures it does today (the chokepoint sources the same
  list). Run under the existing `CELERY_TASK_ALWAYS_EAGER` conftest.

### 4.2 What is internal (NOT asserted)

- Whether `ordered_phases()` builds the implicit phase via a private
  `_implicit_phase()` helper or inline (only the returned **type + field
  values + `pk is None`** are pinned).
- The exact order of the `SeasonPhase.objects.create(...)` line relative to the
  `teams.add` / `map_pool.set` lines within the atomic block (only "inside the
  same atomic block, after the Season create" is pinned).
- Whether `season_schedule`'s `< 2`-team short-circuit stays in the view or
  relies on `scheduled_fixtures()` returning `[]` (only byte-identical render
  is pinned).
- Whether the top-of-file `generate_schedule` import is dropped from
  `league_views.py` / `tasks.py` (only "the Season read-path routes through the
  chokepoint" is pinned).
- The optional `SeasonPhaseInline` on `SeasonAdmin`.

### 4.3 Proposed test files (summary)

- **NEW** `matches/tests/test_season_phase.py` — model fields / constraint /
  ordering / fallback representation / behaviour-equivalence (the bulk).
- **EXTEND** `matches/tests/test_league_create.py` — creation-on-create +
  rollback.
- **EXTEND** `matches/tests/test_league_next_season.py` — creation-on-next.
- **EXTEND** `matches/tests/views_tests.py` (read-path equivalence at the view
  layer) + `matches/tests/test_league_play.py` (task-layer equivalence).

---

## 5. SCOPE-OUT (LOCKED — DO NOT build here)

- **Composer UI** (picking/ordering phases at create-League time) — Part2b.
- **Per-phase `schedule_format`** field — none; the RR phase reads
  `Season.schedule_format`.
- **Multi-phase play loop** / cross-phase matchday offsetting / per-phase
  standings scoping — Part2c. `scheduled_fixtures()` returns exactly ONE RR
  fixture list this slice.
- **`SeasonPhase → Tournament` FK** and tournament embed — Part2b/2c.
- **`member_night` / `tournament` phase behaviour** — declared in the enum,
  inert this slice (only `round_robin` resolves fixtures).
- **`RunPython` / backfill / data migration** — none (ADR-0004; the
  `CreateModel`-only migration is the whole schema change; legacy phase-less
  Seasons rely on the in-memory fallback forever, no backfill).
- **CONTEXT.md edit** — already done ("Season phase" term written at grilling).
- **ADR** — already written as **ADR-0023**.
- **Any `simulate_scheduled_round` / `simulate_match` / simulator change** —
  none.

---

## 6. DETERMINISM NOTE

No simulator change, no RNG consumed by any new code (the model methods are
pure derivations over the ORM + the existing deterministic
`generate_schedule`). **No SIM-07 / SIM-08 contract interaction. NO Score
Calibration re-baseline.** `generate_schedule` is a pure function of the *set*
of `team_ids`, so routing it through the chokepoint changes nothing about which
fixtures are produced.

---

## 7. LOCKED-NAMES QUICK INDEX

- Model: `matches.models.SeasonPhase`; fields `season`
  (`FK(Season, CASCADE, related_name="phases")`), `ordinal`
  (`PositiveSmallIntegerField`, 1-based), `phase_type`
  (`CharField(max_length=16, choices=PHASE_TYPE_CHOICES, default="round_robin")`).
- `SeasonPhase.PHASE_TYPE_CHOICES = (("round_robin","Round-robin"),
  ("tournament","Tournament"), ("member_night","Member night"))`.
- `SeasonPhase.Meta.ordering = ["ordinal"]`;
  `UniqueConstraint(fields=["season","ordinal"], name="uniq_season_phase_ordinal")`.
- Reverse accessor: `Season.phases`.
- Chokepoint: `Season.ordered_phases() -> list[SeasonPhase]`;
  `Season.scheduled_fixtures() -> list[ScheduleFixture]`.
- Implicit fallback: **unsaved** `SeasonPhase(season=self, ordinal=1,
  phase_type="round_robin")` (`pk is None`); optional builder
  `Season._implicit_phase()`.
- Creation sites: `matches.league_views.league_create` (after `Season.objects.create`
  at `:527`), `matches.league_views.next_season` (after `:1900`) — each
  `SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")`
  inside the existing `@transaction.atomic`.
- Migration: `matches/migrations/0041_season_phase.py`, dep
  `("matches", "0040_tournament_random_draw")`, `CreateModel(SeasonPhase)` only.
- Admin: `matches.admin.SeasonPhaseAdmin`
  (`list_display = ("season", "ordinal", "phase_type")`); optional
  `SeasonPhaseInline` on `SeasonAdmin`.
- Read-path routed through chokepoint: `Season._is_finished` (`models.py:1015`),
  `play_season_task` (`tasks.py:190`), `season_schedule` (`league_views.py:371`),
  `_build_dashboard_context` (`:646`), `league_history` Play-Week preview (`:1512`),
  `team_schedule` (`:1822`).
- UNCHANGED pure module: `matches/season_dashboard.py`
  (`find_next_fixture` / `round_progress` / `find_next_matchday` /
  `select_play_fixtures` take a fixtures list, stay pure).
- Test files: NEW `matches/tests/test_season_phase.py`; EXTEND
  `matches/tests/test_league_create.py`,
  `matches/tests/test_league_next_season.py`,
  `matches/tests/views_tests.py`, `matches/tests/test_league_play.py`.
