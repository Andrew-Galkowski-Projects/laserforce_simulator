# LG-02-Part2b — Create-League phase composer + dormant phase columns — SEAM CONTRACT

**Status:** locked. **Artifact only** — drives three parallel agents (code / tests / docs). Build against the locked names verbatim.

## 0. Overview / scope

Part2b adds a vanilla-JS **"+" composer** to the create-League form that writes **MULTIPLE ordered `SeasonPhase` rows** (vs Part2a's single implicit/explicit `round_robin`), plus two **dormant** `SeasonPhase` columns: a per-phase `schedule_format` and a NULL-only `SeasonPhase → Tournament` FK. A new **pure module** `matches/phase_composer.py` parses the composer's serialized wire format into ordered `PhaseSpec`s; the form's `clean()` calls it and stashes the result; both `SeasonPhase`-creation sites (`league_create`, `next_season`) loop over the specs.

**Read-path is UNCHANGED.** Part2a's chokepoint `Season.scheduled_fixtures()` already calls `generate_schedule(team_ids, Season.schedule_format)` and **ignores the phase rows** — it plays the RR and treats tournament phases as invisible. Part2b writes more rows but reads none of them. **No simulator change, no RNG, no SIM-07/08 interaction, NO Score Calibration re-baseline, no read-path/chokepoint change.** The `tournament` FK is **ALWAYS NULL** in Part2b (the build is Part2c).

---

## 1. MODEL — `matches/models.py`, `SeasonPhase`

Append **two fields after the existing `phase_type` field**. UNCHANGED: `PHASE_TYPE_CHOICES`, `season`, `ordinal`, `phase_type`, `Meta.ordering = ["ordinal"]`, the `uniq_season_phase_ordinal` constraint, `__str__`.

```python
schedule_format = models.CharField(max_length=32, null=True, blank=True)
tournament = models.ForeignKey(
    "matches.Tournament",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="season_phases",
)
```

- `schedule_format` — **dormant** (nothing reads it this slice). At create: a `round_robin` phase copies `Season.schedule_format` (value `"single_round_robin"`); a `tournament` phase gets `NULL`.
- `tournament` — **ALWAYS NULL in Part2b**. Pin `related_name="season_phases"` (reverse accessor `tournament.season_phases`). Same-app ref (`matches.Tournament`) — no cross-app dep.

## 2. MIGRATION — `matches/migrations/0042_seasonphase_format_tournament.py`

Latest matches migration confirmed `0041_season_phase` ⇒ this is **`0042`**, dependency `("matches", "0041_season_phase")`. Two `AddField` ops (`schedule_format`, then `tournament`). **NO `RunPython`, NO backfill** (ADR-0004). Cross-app: the `tournament` FK references `matches.Tournament` (same app) — no cross-app migration dependency.

## 3. PURE MODULE — `matches/phase_composer.py` (NEW)

**Frozen import allowlist: `dataclasses`, `typing` ONLY.** NO `django`, NO ORM, NO `random`/`datetime`/`json`/I/O/logging. Defended by `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk, mirroring `matches/standings.py` / `matches/schedule_generator.py`).

### Dataclass

```python
@dataclass(frozen=True)
class PhaseSpec:
    ordinal: int               # 1-based, contiguous 1..N in composer order
    phase_type: str            # "round_robin" | "tournament"
    schedule_format: Optional[str]   # season format for RR; None for tournament
```

### Function

```python
def parse_phase_composition(raw: str, *, season_schedule_format: str) -> list[PhaseSpec]:
```

**Wire format (LOCKED): comma-separated tokens** — e.g. `"round_robin,tournament"` — parsed with `str.split(",")` and `str.strip()` per token. Chosen over JSON to keep the allowlist minimal (no `json` import). The template serializes the ordered rows into this exact form.

**Behaviour:**
- **EMPTY / blank `raw`** (`""` / whitespace-only after strip) ⇒ exactly one `PhaseSpec(ordinal=1, phase_type="round_robin", schedule_format=season_schedule_format)` (the Part2a default).
- Otherwise: split on `,`, strip each token; assign **contiguous ordinals 1..N** in composer order; `schedule_format = season_schedule_format` for `round_robin` specs, `None` for `tournament` specs.
- Valid phase types: **`"round_robin"` and `"tournament"` only** (`"member_night"` is NOT selectable in Part2b — it raises unknown-type).

**Raise plain `ValueError`** (NOT `django.core.exceptions.ValidationError` — keep the module Django-free) with these **exact message strings**:

| Condition | Exact `ValueError` message |
|---|---|
| Zero `round_robin` phases in a non-empty composition | `"composition must contain at least one round-robin phase"` |
| Unknown phase type (incl. `"member_night"` or any non-RR/non-tournament token) | `f"unknown phase type: {token!r}"` |
| Malformed input (e.g. an empty token between commas like `"round_robin,,tournament"` or a token that is empty after strip) | `"malformed phase composition"` |

Validation order within a non-empty `raw`: tokenise → reject malformed (empty token) → reject unknown type per token → after building specs, reject if zero `round_robin`. (Empty/blank `raw` short-circuits to the default BEFORE any of these checks — it is never "zero RR".)

## 4. FORM — `matches/forms.py`, `CreateLeagueForm`

Add a hidden field (the existing disabled Season-level `schedule_format` `ChoiceField` **STAYS unchanged** — it is the live read-path source):

```python
phases = forms.CharField(
    widget=forms.HiddenInput(attrs={"id": "league-create-phases"}),
    required=False,
)
```

Extend `clean()` (preserve all existing LG-01j map-mode-vs-pool rules verbatim):

```python
from .phase_composer import parse_phase_composition
...
try:
    specs = parse_phase_composition(
        cleaned_data.get("phases", "") or "",
        season_schedule_format=cleaned_data.get("schedule_format") or "single_round_robin",
    )
except ValueError as exc:
    self.add_error("phases", forms.ValidationError(str(exc)))
else:
    cleaned_data["phase_specs"] = specs
```

- Pin the field name **`phases`** and the `cleaned_data` key **`phase_specs`** (`list[PhaseSpec]`).
- The `season_schedule_format` arg is the form's `schedule_format` value, which is the disabled field's locked `"single_round_robin"`.
- Catch `ValueError` from the pure module and re-raise as `forms.ValidationError` attached to the **`"phases"`** field.

## 5. VIEWS — `matches/league_views.py`

### 5a. `league_create` (~line 553, inside the existing `@transaction.atomic` block)

REPLACE the single `SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")` with a loop over `form.cleaned_data["phase_specs"]`:

```python
for spec in form.cleaned_data["phase_specs"]:
    SeasonPhase.objects.create(
        season=season,
        ordinal=spec.ordinal,
        phase_type=spec.phase_type,
        schedule_format=spec.schedule_format,
        tournament=None,
    )
```

### 5b. `next_season` (~line 1942, inside the existing `@transaction.atomic` block)

`next_season` has **no composer** — it carries the previous Season's composition forward (mirrors the team-id / map-pool carry-forward). REPLACE the single `SeasonPhase.objects.create(season=new_season, ordinal=1, phase_type="round_robin")` with a copy loop over the source Season's phases:

```python
for src in latest_completed.phases.all():   # Meta.ordering=["ordinal"] guarantees order
    SeasonPhase.objects.create(
        season=new_season,
        ordinal=src.ordinal,
        phase_type=src.phase_type,
        schedule_format=src.schedule_format,
        tournament=None,
    )
```

(`latest_completed` is the existing carry-forward source Season variable in `next_season`; copy `ordinal`, `phase_type`, `schedule_format` verbatim; reset `tournament=None`.)

## 6. TEMPLATE — `templates/leagues/create.html`

Vanilla-JS composer (no framework; inline `<script>`, per the LG-01d precedent). Behaviour:

- A **"+ Add block"** button clones a row template into the composer container.
- Each row has a phase-type `<select>` (`round_robin` / `tournament`) and (for a `round_robin` row) a `schedule_format` `<select>` with a single option `single_round_robin` (mirroring the disabled Season-level one). Rows are removable; reorder is optional.
- On form submit, serialize the ordered rows into the hidden `#league-create-phases` input in the **wire format pinned in §3** (`"round_robin,tournament,..."`, comma-joined phase-type tokens in row order).
- A **"member nights coming soon"** note.
- A per-tournament-block **"build coming in a later release"** flag/label.

### LOCKED DOM ids / class substring

| Id / class | Element |
|---|---|
| `league-create-phases-composer` | outer composer container `<div>` |
| `league-create-add-block` | the "+ Add block" button |
| `league-create-phases` | the hidden input (also the form field's widget id, §4) |
| `league-create-phase-row-{i}` | per-row wrapper (`{i}` = 0-based row index assigned by JS) |
| `league-create-phase-type-{i}` | per-row phase-type `<select>` |
| `league-create-phase-format-{i}` | per-row schedule_format `<select>` (round_robin rows) |
| `league-create-member-night-note` | the "member nights coming soon" note element |
| `phase-tournament-pending` (CSS-class **substring**) | the per-tournament-block "build coming in a later release" flag |

**Confirmed against existing create.html ids — DO NOT collide:** `league-create-form`, `league-create-league-name`, `league-create-season-name`, `league-create-start-date`, `league-create-num-teams`, `league-create-schedule-format`, `league-create-mean`, `league-create-std-dev`, `league-create-map-mode`, `league-create-map-pool`, `league-create-submit`. All new ids are net-new.

## 7. ADMIN — `matches/admin.py`

`SeasonPhaseAdmin.list_display` extends from `("season", "ordinal", "phase_type")` to:

```python
list_display = ("season", "ordinal", "phase_type", "schedule_format", "tournament")
```

No other admin change.

## 8. TESTS (name files + classes; do not write here)

- **`matches/tests/test_phase_composer.py`** (NEW, pure-unit + `TestNoDjangoImportsLeaked`): empty `raw` → single RR default; round_robin `schedule_format` copied from `season_schedule_format`; tournament `schedule_format` is `None`; contiguous ordinals 1..N; multi-phase order preserved; zero-RR composition raises `ValueError` (`"composition must contain at least one round-robin phase"`); unknown `phase_type` raises `ValueError`; `member_night` token rejected (unknown-type); malformed raw (empty token) raises `ValueError`; the purity subprocess check.
- **`matches/tests/test_season_phase.py`** (EXTEND): `schedule_format` nullable + default-`None` for a tournament phase; `tournament` FK nullable + `SET_NULL` + `related_name="season_phases"`.
- **`matches/tests/test_league_create.py`** (EXTEND): composer happy path persists multiple ordered `SeasonPhase` rows with correct ordinals/types/schedule_format and `tournament=None`; empty composer ⇒ single `round_robin` (Part2a equivalence); a no-RR composition is rejected at the form layer with **zero** League/Season/phase rows created (transaction atomicity); the existing single-phase tests still pass.
- **`matches/tests/test_league_next_season.py`** (EXTEND): `next_season` copies the previous Season's full phase composition forward (ordinals / types / schedule_format), with `tournament` reset to `NULL`.

---

## 9. Locked names (quick index)

- **Model fields:** `SeasonPhase.schedule_format` (`CharField(max_length=32, null=True, blank=True)`), `SeasonPhase.tournament` (`FK(matches.Tournament, null=True, blank=True, on_delete=SET_NULL, related_name="season_phases")`).
- **Migration:** `matches/migrations/0042_seasonphase_format_tournament.py`, dep `0041_season_phase`, two `AddField`, no `RunPython`.
- **Pure module:** `matches/phase_composer.py`; dataclass `PhaseSpec(ordinal, phase_type, schedule_format)`; fn `parse_phase_composition(raw, *, season_schedule_format) -> list[PhaseSpec]`.
- **Wire format:** comma-separated phase-type tokens (`"round_robin,tournament"`), parsed with `str.split(",")`.
- **ValueError strings:** `"composition must contain at least one round-robin phase"` / `f"unknown phase type: {token!r}"` / `"malformed phase composition"`.
- **Form:** field `phases` (`HiddenInput`, id `league-create-phases`, `required=False`); `cleaned_data["phase_specs"]`; existing disabled `schedule_format` field unchanged.
- **Views:** `league_create` (~553) phase-spec loop; `next_season` (~1942) carry-forward copy loop; both inside existing `@transaction.atomic`; `tournament=None` always.
- **Template DOM ids:** `league-create-phases-composer`, `league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`, `league-create-phase-type-{i}`, `league-create-phase-format-{i}`, `league-create-member-night-note`; class substring `phase-tournament-pending`.
- **Admin:** `SeasonPhaseAdmin.list_display = ("season", "ordinal", "phase_type", "schedule_format", "tournament")`.
- **Tests:** `test_phase_composer.py` (NEW), `test_season_phase.py` (EXTEND), `test_league_create.py` (EXTEND), `test_league_next_season.py` (EXTEND).
- **Read-path UNCHANGED; tournament FK always NULL; no re-baseline.**
