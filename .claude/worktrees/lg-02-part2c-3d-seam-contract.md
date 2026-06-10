# LG-02-Part2c-3d — Per-tournament-block configuration — SEAM CONTRACT

Pure orchestration/config slice. Adds **two** per-phase tournament columns to
`SeasonPhase`: **`tournament_format`** (DORMANT — column-only, written-but-unread
by the build this slice) and **`tournament_cut`** (LIVE — top-N participant cut
applied to the seeded order before the bracket builds). NO simulator change, NO
RNG change, NO tournament-engine change, NO Score Calibration re-baseline, NO new
ADR (extend ADR-0023). CONTEXT.md is already updated — do not touch.

Seeding (`tournament_mode`, c-3b/c-3c) is already shipped; `team_assembly` is OUT
of scope (subsumed by the deferred `tournament_mode="random_draw"`).

All file paths below are under
`C:\Users\Andrew Galkowski\Desktop\programming\laserforce_simulator\laserforce_simulator\`
unless noted. Three parallel agents (Code / Tests / Docs) MUST treat every name,
signature, field, dict-shape, DOM-id, and literal below as fixed.

---

## 1. Scope

| In scope | Out of scope |
|---|---|
| `SeasonPhase.tournament_format` CharField (DORMANT) | any simulator / RNG / `BatchSimulator` change |
| `SeasonPhase.tournament_cut` PositiveSmallIntegerField (LIVE) | any tournament-engine change (`play_next_node`, `lock_and_build`) |
| migration `0046_seasonphase_format_cut` (2× `AddField`, no backfill) | `team_assembly` (deferred via `random_draw`) |
| `PhaseSpec.tournament_cut` trailing field + wire grammar `tournament[:mode[:cut]]` | Score Calibration re-baseline |
| `Season.activate_pending_tournament_phase` ONE-LINE cut slice | new ADR (extend ADR-0023 only) |
| `league_create` / `next_season` creation-loop kwargs | CONTEXT.md (already updated) |
| composer template: cut `<input>` + disabled format `<select>` | `_seed_order_for_phase` body (BYTE-IDENTICAL, untouched) |
| `SeasonPhaseAdmin.list_display` append 2 | the build's hardcoded `format="single_elimination"` (STAYS hardcoded) |

---

## 2. Model + Migration

### `SeasonPhase` (`matches/models.py`)

Declared AFTER `Season` (class begins line 1505), BEFORE `Tournament` (line 1589).
Append the two new columns **immediately after the existing `tournament_mode`
field** (which ends at line ~1574). Current verified field order on `SeasonPhase`:
`season` FK → `ordinal` → `phase_type` → `schedule_format` → `tournament` FK →
`tournament_mode` → (append HERE) → `class Meta`.

Existing `tournament_mode` definition (the append anchor), verbatim:

```python
    tournament_mode = models.CharField(
        max_length=16,
        choices=TOURNAMENT_MODE_CHOICES,
        default="standings",
    )
```

**(a) New class attribute `TOURNAMENT_FORMAT_CHOICES`** — INLINED on
`SeasonPhase` (must NOT reference `Tournament.FORMAT_CHOICES`: `Tournament` is
defined LATER in the file, line 1589, so a class-body reference fails at eval).
This mirrors the c-3b precedent of inlining `TOURNAMENT_MODE_CHOICES` on
`SeasonPhase` (verified present, lines 1535–1540). The 5 tuples mirror the
VERIFIED current `Tournament.FORMAT_CHOICES` (lines 1599–1605) **byte-for-byte**:

```python
    TOURNAMENT_FORMAT_CHOICES = (
        ("single_elimination", "Single elimination"),
        ("double_elimination", "Double elimination"),
        ("round_robin", "Round robin"),
        ("round_robin_double_elim", "Round robin → Double elimination"),
        ("swiss", "Swiss"),
    )
```

(The label for `round_robin_double_elim` uses the em-dash arrow `→` U+2192 —
copy it exactly.)

**(b) `tournament_format`** — DORMANT (written-but-unread by the build this slice):

```python
    tournament_format = models.CharField(
        max_length=32,
        choices=TOURNAMENT_FORMAT_CHOICES,
        default="single_elimination",
    )
```

**(c) `tournament_cut`** — LIVE (0 = no cut = all enrolled teams):

```python
    tournament_cut = models.PositiveSmallIntegerField(default=0)
```

No `db_index`, no `null`/`blank`, no constraint on either.

### Migration `matches/migrations/0046_seasonphase_format_cut.py`

- **VERIFIED latest matches migration is `0045_seasonphase_tournament_mode`** (the
  highest-numbered file in `matches/migrations/`). So `0046` is correct and the
  dependency is `("matches", "0045_seasonphase_tournament_mode")`.
- Exactly **two `AddField` ops in order**: `tournament_format` THEN
  `tournament_cut`.
- **NO `RunPython`, NO `RunSQL`, NO backfill, NO data migration** (ADR-0004
  disposable-data posture; existing tournament phases inherit
  `tournament_format="single_elimination"` and `tournament_cut=0`). Mirror the
  `0045` style (verified single `AddField`, no backfill).

---

## 3. Build (`Season.activate_pending_tournament_phase`, `matches/models.py` ~1173)

The method (decorated `@transaction.atomic`) is UNCHANGED except for **ONE
inserted line**. Current verified body sequence: gating guards (`phase is None` /
`phase.phase_type != "tournament"` / `phase.tournament_id is not None` / `phase.pk
is None` / mode-specific prior-complete gate) → `order =
self._seed_order_for_phase(phase)` (line 1214) → `if not order: return` (lines
1215–1216) → name selection → `Tournament.objects.create(...)` → participant loop
(`seed=position + 1`) → `phase.save` → `lock_and_build()`.

**The ONLY change** — insert AFTER `order = self._seed_order_for_phase(phase)`
(line 1214) and BEFORE the existing `if not order: return` (line 1215):

```python
        order = self._seed_order_for_phase(phase)
        if phase.tournament_cut:                 # <-- INSERTED LINE(S)
            order = order[:phase.tournament_cut]  # <-- INSERTED LINE(S)
        if not order:
            return
```

Everything else is verbatim and UNCHANGED, in particular:

- `Tournament.objects.create(name=..., format="single_elimination",
  team_assembly="preset", state="setup")` — **`format="single_elimination"`
  stays HARDCODED** (`tournament_format` is dormant this slice; the build does NOT
  read it).
- the participant loop `for position, team_id in enumerate(order):
  TournamentParticipant.objects.create(tournament=tournament, team_id=team_id,
  seed=position + 1)`.
- `phase.tournament = tournament` / `phase.save(update_fields=["tournament"])` /
  `tournament.lock_and_build()`.

`Season._seed_order_for_phase` (~line 1237) is **BYTE-IDENTICAL / NOT edited** —
the cut applies to its output at the caller, never inside it.

**Cut semantics:** `tournament_cut == 0` ⇒ slice not applied (full participant
set). `tournament_cut > 0` ⇒ keep the top `cut` seeds in the already-ordered
`order` list (dense seeds `1..cut`). `cut > len(order)` ⇒ `order[:cut]` is a
no-op (Python slice past end), i.e. all teams.

---

## 4. Parser (`matches/phase_composer.py`)

The module is Django-free (frozen import allowlist: `dataclasses`, `typing` ONLY —
the new code adds **NO import**, so `TestNoDjangoImportsLeaked` stays green).

### `PhaseSpec` — append trailing field

Current shape (verified, lines 33–54): `ordinal: int`, `phase_type: str`,
`schedule_format: Optional[str]`, `tournament_mode: str = "standings"`.

Append `tournament_cut: int = 0` LAST (default so existing keyword constructions
stay equality-identical — the c-3a `leg` / c-3b `tournament_mode` precedent):

```python
    ordinal: int
    phase_type: str
    schedule_format: Optional[str]
    tournament_mode: str = "standings"
    tournament_cut: int = 0
```

### Wire grammar — `tournament[:mode[:cut]]`

The tournament branch switches from the CURRENT `token.partition(":")` (verified
line 105, a 2-way split shared with the RR branch) to a **`split(":")` approach
on the tournament branch only**: `parts = token.split(":")` where `parts[0]` =
type, `parts[1]` = mode (default `"standings"`), `parts[2]` = cut string (default
`"0"`). The `round_robin` branch grammar is **UNCHANGED** (still
`round_robin[:schedule_format]`, max 2 parts; a 3rd part → malformed).

Locked rules on a **tournament** token:

| Condition | Result |
|---|---|
| bare `tournament` | mode `"standings"`, cut `0` |
| `tournament:strength` | mode `"strength"`, cut `0` |
| `tournament:standings:8` | mode `"standings"`, cut `8` |
| `len(parts) > 3` | EXISTING `ValueError("malformed phase composition")` |
| cut field does NOT parse as `int` | EXISTING `ValueError("malformed phase composition")` |
| empty cut field (e.g. `tournament:standings:`) | EXISTING `ValueError("malformed phase composition")` |
| parsed `cut != 0 and cut < 4` | NEW LOCKED `ValueError(f"tournament cut must be 0 or at least 4: {cut}")` |
| mode not in `_VALID_TOURNAMENT_MODES` | EXISTING `ValueError(f"unknown tournament_mode: {mode!r}")` (verbatim) |

Validation order on the tournament branch (locked): split → reject `len(parts) >
3` (malformed) → mode membership check (the existing `f"unknown tournament_mode:
{mode!r}"`) → parse cut (empty / non-int → malformed) → cut-floor check (`cut !=
0 and cut < 4` → the new string). `member_night` is still rejected at the
phase-TYPE level by the existing `f"unknown phase type: {token!r}"` (it is not a
`tournament`/`round_robin` token).

### Existing ValueError strings — PIN VERBATIM (read from the file, do not paraphrase)

All preserved exactly as they appear today:

- `"malformed phase composition"` (lines 103, 109)
- `f"unknown schedule_format: {schedule_format!r}"` (line 114) — RR branch, unchanged
- `f"unknown tournament_mode: {mode!r}"` (line 123) — reused VERBATIM on the new split path
- `f"unknown phase type: {token!r}"` (line 126)
- `"composition must contain at least one round-robin phase"` (line 137)
- `"a tournament phase requires a preceding round-robin phase"` (lines 153–155) — c-3c relaxed guard, unchanged

### NEW ValueError string — LOCKED

```python
ValueError(f"tournament cut must be 0 or at least 4: {cut}")
```

(`cut` is the parsed int; no `!r`.)

The empty `_VALID_TOURNAMENT_MODES = ("standings", "strength", "unseeded")` tuple
(line 30) is UNCHANGED.

### Back-compat invariant for the parser

Bare `tournament` ⇒ mode `standings` + cut `0`; `tournament:strength` ⇒ strength
+ cut `0`. So **every Part2b / c-3a / c-3c serialized value parses unchanged**.

---

## 5. Views (`matches/league_views.py`)

**VERIFIED file:** the `league_create` / `next_season` views and BOTH
`SeasonPhase.objects.create` loops live in `matches/league_views.py` (NOT
`matches/views.py`). `league_create` at line 479; `next_season` at line 2064.

### `league_create` loop (~line 559, inside the existing `@transaction.atomic`)

Current verified kwargs:

```python
        SeasonPhase.objects.create(
            season=season,
            ordinal=spec.ordinal,
            phase_type=spec.phase_type,
            schedule_format=spec.schedule_format,
            tournament=None,
            tournament_mode=spec.tournament_mode,
        )
```

**Add `tournament_cut=spec.tournament_cut`.** Do NOT set `tournament_format` (let
the column default `"single_elimination"` apply — there is no `PhaseSpec.tournament_format`).

### `next_season` carry-forward copy loop (~line 2137, inside the existing `@transaction.atomic`)

Current verified kwargs (source phase is `src`):

```python
        SeasonPhase.objects.create(
            season=new_season,
            ordinal=src.ordinal,
            phase_type=src.phase_type,
            schedule_format=src.schedule_format,
            tournament=None,
            tournament_mode=src.tournament_mode,
        )
```

**Add BOTH** `tournament_cut=src.tournament_cut` AND
`tournament_format=src.tournament_format` (carry forward verbatim — `next_season`
copies from the persisted source `SeasonPhase` row, which has both real columns).

---

## 6. Composer template (`templates/leagues/create.html`)

Inline vanilla-JS composer (verified). Row-building lives in `buildRow()` (line
123), indices assigned by `var i = rowSeq++` (lines 121, 124). `serialize()`
(line 215) emits comma-joined tokens. `applyType()` (line 194) drives per-row
show/hide. All existing Part2b / c-3a / c-3c DOM ids UNCHANGED:
`league-create-phases-composer`, `league-create-add-block`,
`league-create-phases` (hidden input), `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}` (the RR schedule
`<select>`), `league-create-phase-mode-{i}` (the tournament mode `<select>`),
`league-create-member-night-note`, and the `phase-tournament-pending` class.

### (a) Cut input — NEW

A `<input type="number" min="0">` with id **`league-create-phase-cut-{i}`**
(`{i}` = the same `rowSeq` index), class hook `phase-cut-input`, **default value
`0`**. Shown for **tournament rows only** — same show/hide rule as the mode
`<select>` in `applyType()` (verified: `modeSelect.style.display = isRR ? "none"
: ""`). Wire its `change` listener to `serialize()` (mirroring
`modeSelect.addEventListener("change", serialize)` at line 202).

### (b) Disabled tournament-format `<select>` — NEW (DORMANT, serializes NOTHING)

A DISABLED `<select>` with id **`league-create-phase-tournament-format-{i}`** —
DISTINCT from the RR `league-create-phase-format-{i}`. Single visible option text
**"Single elimination (more formats coming soon)"**. `disabled` attribute present.
Shown for tournament rows only (same rule). It serializes **NOTHING** — `serialize()`
must NOT read it (the build hardcodes `format="single_elimination"`; the column
default covers persistence; this select is a visual placeholder, the
`phase-tournament-pending` / disabled-`random_draw`-mode-option precedent).

### (c) `serialize()` — emit `tournament:<mode>:<cut>` for a tournament row

Current verified tournament emit (lines 230–234):

```javascript
            } else {
                var modeSelect = rows[k].querySelector(".phase-mode-select");
                var mode = modeSelect ? modeSelect.value : "standings";
                tokens.push("tournament:" + mode);
            }
```

Change to also read the row's `.phase-cut-input` and append `:<cut>`:

```javascript
            } else {
                var modeSelect = rows[k].querySelector(".phase-mode-select");
                var mode = modeSelect ? modeSelect.value : "standings";
                var cutInput = rows[k].querySelector(".phase-cut-input");
                var cut = cutInput ? cutInput.value : "0";
                tokens.push("tournament:" + mode + ":" + cut);
            }
```

(The RR branch `tokens.push("round_robin:" + fmt)` at line 229 is UNCHANGED.)
Empty cut field ⇒ the parser raises `"malformed phase composition"` (the form
re-wraps as a `forms.ValidationError` on `phases`); a value `< 4 and != 0` ⇒ the
new floor `ValueError`. The Code agent may default the JS-side cut to `"0"` when
the input is blank to avoid the malformed path for an untouched row — at agent
discretion, but the default `value="0"` on the input already guarantees a
parseable `"0"` for an unmodified tournament row.

---

## 7. Admin (`matches/admin.py`)

`SeasonPhaseAdmin.list_display` (verified lines 40–47, currently 6 entries) —
APPEND `"tournament_format"`, `"tournament_cut"`:

```python
    list_display = (
        "season",
        "ordinal",
        "phase_type",
        "schedule_format",
        "tournament",
        "tournament_mode",
        "tournament_format",
        "tournament_cut",
    )
```

No other admin change.

---

## 8. Backward-compat invariants (state these in docs + assert in tests)

- `tournament_cut == 0` (default) ⇒ **byte-identical to today** (full participant
  set; `order[:cut]` slice not applied).
- bare `tournament` / `tournament:strength` wire tokens ⇒ parse identically to
  c-3c (mode resolved, cut `0`).
- `tournament_format` is **written-but-unread** by the build this slice — the
  build hardcodes `format="single_elimination"`. An admin can set a phase's
  `tournament_format="swiss"` and the playoff still builds single-elim (a known,
  ACCEPTABLE admin foot-gun this slice).
- `cut > enrolled-team-count` ⇒ the `order[:cut]` slice is a no-op (all teams).
- `cut` leaving `< 4` participants at runtime ⇒ the EXISTING
  `Tournament.lock_and_build` `>= 4`-participant `ValidationError` fires (no new
  handling added). The parser-side floor (`cut != 0 and cut < 4`) catches the
  config error at compose time; the runtime guard is defence-in-depth.
- Validation is **PARSER-ONLY** — NO `Season.clean()` / `SeasonPhase.clean()`
  guard is added.

---

## 9. Test boundary

What the Tests agent asserts (boundary) vs internal:

- **Pure-unit on `phase_composer`** (`matches/tests/test_phase_composer.py`,
  VERIFIED EXISTS): the cut grammar (`tournament:standings:8` ⇒ cut 8;
  `tournament:strength` ⇒ cut 0; bare `tournament` ⇒ cut 0), the floor
  (`tournament:standings:3` ⇒ the new floor `ValueError`; `:0` ⇒ accepted),
  malformed (`len(parts) > 3`, empty cut, non-int cut ⇒ `"malformed phase
  composition"`), back-compat (every c-3c serialized value parses unchanged),
  `PhaseSpec` default (`tournament_cut == 0`), and the purity subprocess
  (`TestNoDjangoImportsLeaked` — no import added).
- **Model field tests** (`matches/tests/test_season_phase.py`, VERIFIED EXISTS):
  `tournament_format` default `"single_elimination"` + `TOURNAMENT_FORMAT_CHOICES`
  has the 5 tuples + `max_length==32`; `tournament_cut` default `0` +
  `PositiveSmallIntegerField`.
- **DB build test** (`matches/tests/test_season_playoffs.py` — NOTE the actual
  file is `test_season_playoffs.py` WITH the trailing `s`; the c-3d task brief's
  `test_season_playoff.py` is a typo, use the real name): assert participant
  COUNT == `cut` + dense seeds `1..N` + champion stamped + the built tournament's
  `format` stays `"single_elimination"`; `cut=0` ⇒ full participant set;
  `cut > enrolled` ⇒ all teams. **NEVER assert exact simulated point totals** —
  tournament sims are non-deterministic.
- **`test_league_create.py`** (VERIFIED EXISTS): a composed tournament phase
  persists its `tournament_cut`; `tournament_format` defaults to
  `"single_elimination"`.
- **`test_league_next_season.py`** (VERIFIED EXISTS): the carry-forward copies
  BOTH `tournament_cut` AND `tournament_format` verbatim — hand-set a source
  phase's `tournament_cut=8` + `tournament_format` (e.g. `"swiss"`) via ORM,
  assert the new draft Season's copied phase preserves both.

All five named test files VERIFIED to exist under `matches/tests/`
(`test_phase_composer.py`, `test_season_phase.py`, `test_season_playoffs.py`,
`test_league_create.py`, `test_league_next_season.py`).

---

## 10. Locked names quick index

| Kind | Name / value |
|---|---|
| model field (DORMANT) | `SeasonPhase.tournament_format = CharField(max_length=32, choices=TOURNAMENT_FORMAT_CHOICES, default="single_elimination")` |
| model field (LIVE) | `SeasonPhase.tournament_cut = PositiveSmallIntegerField(default=0)` |
| model class attr | `SeasonPhase.TOURNAMENT_FORMAT_CHOICES` = the 5 tuples mirroring `Tournament.FORMAT_CHOICES` (single_elimination / double_elimination / round_robin / round_robin_double_elim / swiss; `→` U+2192 in the rr-de label) |
| append anchor | after the existing `SeasonPhase.tournament_mode` field, before `class Meta` |
| migration | `matches/migrations/0046_seasonphase_format_cut.py`, dep `("matches", "0045_seasonphase_tournament_mode")`, 2× `AddField` (tournament_format then tournament_cut), NO RunPython/backfill |
| dataclass field | `PhaseSpec.tournament_cut: int = 0` (trailing, default) |
| wire grammar | tournament token `tournament[:mode[:cut]]` via `split(":")`; RR token unchanged `round_robin[:schedule_format]` via `partition(":")` |
| NEW ValueError | `f"tournament cut must be 0 or at least 4: {cut}"` |
| reused ValueErrors (verbatim) | `"malformed phase composition"`, `f"unknown tournament_mode: {mode!r}"`, `f"unknown phase type: {token!r}"`, `"composition must contain at least one round-robin phase"`, `"a tournament phase requires a preceding round-robin phase"`, `f"unknown schedule_format: {schedule_format!r}"` |
| build insertion | `if phase.tournament_cut: order = order[:phase.tournament_cut]` — after `order = self._seed_order_for_phase(phase)` (models.py ~1214), before `if not order: return` |
| build hardcode (STAYS) | `Tournament.objects.create(..., format="single_elimination", team_assembly="preset", state="setup")` |
| untouched | `Season._seed_order_for_phase` (~1237) BYTE-IDENTICAL; tournament engine; simulator; RNG |
| views file | `matches/league_views.py` (NOT `matches/views.py`) — `league_create` (L479), `next_season` (L2064) |
| `league_create` loop kwarg | add `tournament_cut=spec.tournament_cut` (do NOT set `tournament_format`) — loop at L559 |
| `next_season` loop kwargs | add `tournament_cut=src.tournament_cut` AND `tournament_format=src.tournament_format` — loop at L2137 |
| composer DOM id (cut) | `league-create-phase-cut-{i}` — `<input type="number" min="0">`, default `0`, class `phase-cut-input`, tournament-rows-only |
| composer DOM id (format) | `league-create-phase-tournament-format-{i}` — DISABLED `<select>`, single option "Single elimination (more formats coming soon)", serializes NOTHING, tournament-rows-only |
| composer serialize | tournament row ⇒ `tournament:<mode>:<cut>`; RR row ⇒ `round_robin:<format>` (unchanged) |
| composer unchanged ids | `league-create-phases-composer`, `league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`, `league-create-phase-type-{i}`, `league-create-phase-format-{i}`, `league-create-phase-mode-{i}`, `league-create-member-night-note`, class `phase-tournament-pending` |
| admin | `SeasonPhaseAdmin.list_display` append `"tournament_format"`, `"tournament_cut"` |
| ADR | extend ADR-0023 (no new ADR) |
| CONTEXT.md | already updated — do not touch |
| test files (all exist) | `test_phase_composer.py`, `test_season_phase.py`, `test_season_playoffs.py` (NOTE trailing `s`), `test_league_create.py`, `test_league_next_season.py` |
