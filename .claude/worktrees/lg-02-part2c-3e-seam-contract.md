# LG-02-Part2c-3e — Non-single-elim finals embeds — SEAM CONTRACT

Flip the dormant c-3d `SeasonPhase.tournament_format` column **dormant→live** so a
Season `tournament` phase builds using ANY of five formats, and surface FULL
per-format sub-config parity (7 new `SeasonPhase` columns mirroring `Tournament`'s
fields) with the standalone `tournament_create` form. The standalone Tournament
engine already builds + drains all five formats; this slice flips the format live
and wires the sub-config. Validation: SHAPE at the pure parser, COUNT/parity at
`lock_and_build` (defence-in-depth). NO new `clean()` guard. Tournament sims are
non-deterministic ⇒ **NO Score Calibration re-baseline**. Extend ADR-0023
(Part2c-3e addendum), no new ADR.

---

## 1. Model — 7 new `SeasonPhase` columns (`matches/models.py`)

Appended AFTER `tournament_cut` (the c-3d field at L1604), BEFORE `class Meta`
(L1606). `tournament_format` (already exists from c-3d, L1597) flips dormant→live —
**no schema change to it**. The 7 new columns mirror `Tournament`'s fields
byte-for-byte; choices tuples **INLINED on `SeasonPhase`** (NOT referencing
`Tournament.*` — `Tournament` is declared later in the file at L1619; the c-3b/c-3d
inlined-choices precedent).

```python
    # LG-02-Part2c-3e — per-format sub-config, mirroring Tournament's fields
    # byte-for-byte. The four series tiers + wb/lb (RR->DE) + swiss_rounds. The
    # series choices are INLINED here (Tournament is declared later in the file).
    final_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    semifinal_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    quarterfinal_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    earlier_series_length = models.PositiveSmallIntegerField(
        choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
        default=1,
    )
    wb_advancers = models.PositiveSmallIntegerField(default=0)
    lb_advancers = models.PositiveSmallIntegerField(default=0)
    swiss_rounds = models.PositiveSmallIntegerField(default=0)
```

- No constraint, no `db_index`, no `null`/`blank` on any of the 7.
- `tournament_format` (`CharField(max_length=32, choices=TOURNAMENT_FORMAT_CHOICES,
  default="single_elimination")`) and `SeasonPhase.TOURNAMENT_FORMAT_CHOICES` (the
  5-tuple inlined at L1552) are **UNCHANGED** — only their *consumption* in the build
  changes (dormant→live).
- Every existing `SeasonPhase` field (`season`, `ordinal`, `phase_type`,
  `schedule_format`, `tournament`, `tournament_mode`, `tournament_format`,
  `tournament_cut`), `PHASE_TYPE_CHOICES`, `TOURNAMENT_MODE_CHOICES`,
  `TOURNAMENT_FORMAT_CHOICES`, `Meta`, `__str__` is **UNCHANGED**.

### Migration `0047_seasonphase_tournament_subconfig`
- Dep `0046_seasonphase_format_cut`.
- 7× `AddField` in declaration order: `final_series_length`,
  `semifinal_series_length`, `quarterfinal_series_length`, `earlier_series_length`,
  `wb_advancers`, `lb_advancers`, `swiss_rounds`.
- **NO `RunPython`, NO backfill** (ADR-0004 posture — existing tournament phases
  inherit all defaults: series tiers `1`, wb/lb `0`, swiss `0`).
- `tournament_format` already migrated by c-3d's `0046` — no `AlterField`.

---

## 2. PhaseSpec — 8 NEW trailing fields (`matches/phase_composer.py`)

Current `PhaseSpec` (frozen dataclass) fields, in order:
`ordinal: int`, `phase_type: str`, `schedule_format: Optional[str]`,
`tournament_mode: str = "standings"`, `tournament_cut: int = 0`.

Append 8 NEW fields LAST (after `tournament_cut`), each with a default ⇒ existing
keyword constructions stay equality-identical (the c-3a `leg` / c-3b
`tournament_mode` / c-3d `tournament_cut` precedent):

```python
    tournament_format: str = "single_elimination"
    final_series_length: int = 1
    semifinal_series_length: int = 1
    quarterfinal_series_length: int = 1
    earlier_series_length: int = 1
    wb_advancers: int = 0
    lb_advancers: int = 0
    swiss_rounds: int = 0
```

- Frozen import allowlist `dataclasses` + `typing` ONLY — **UNCHANGED** (no `json`,
  no Django); the new code adds NO import. `TestNoDjangoImportsLeaked` stays green.

---

## 3. Wire grammar — tournament branch only (`matches/phase_composer.py`)

The RR branch (`round_robin[:schedule_format]`, 2-way `partition`) is **UNCHANGED**.
The tournament branch extends the c-3d `tournament[:mode[:cut]]` (3-way `split(":")`)
to a **positional trailing-optional 11-field layout**:

```
tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss
parts[0] = "tournament"
parts[1] = mode            default "standings"
parts[2] = cut             default "0"
parts[3] = format          default "single_elimination"
parts[4] = fsl  (final)            default "1"
parts[5] = ssl  (semifinal)        default "1"
parts[6] = qsl  (quarterfinal)     default "1"
parts[7] = esl  (earlier)          default "1"
parts[8] = wb   (wb_advancers)     default "0"
parts[9] = lb   (lb_advancers)     default "0"
parts[10] = swiss (swiss_rounds)   default "0"
```

- Implementation: `parts = token.split(":")`. `len(parts) > 11` ⇒ existing
  `ValueError("malformed phase composition")` (the c-3d `> 3` check widens to `> 11`).
- Each new field trailing-optional with its default (`parts[k].strip() if len(parts)
  > k else <default>`); an empty field (`""` after strip, e.g. a trailing `:` or a
  gap) ⇒ existing `ValueError("malformed phase composition")` (same as the c-3d cut).
- Backward-compat: every c-3d/c-3c serialized value (`tournament`,
  `tournament:strength`, `tournament:standings:8`) parses identically — the missing
  trailing fields take their defaults.

### Validation ORDER (tournament branch, locked)
1. `split(":")` → reject `len(parts) > 11` (`"malformed phase composition"`).
2. mode (parts[1]) membership vs `_VALID_TOURNAMENT_MODES`
   (`("standings", "strength", "unseeded")` — **UNCHANGED**) ⇒ existing
   `ValueError(f"unknown tournament_mode: {mode!r}")`.
3. cut (parts[2]) parse-int (empty/non-int → `"malformed phase composition"`); parsed
   `cut != 0 and cut < 4` ⇒ existing
   `ValueError(f"tournament cut must be 0 or at least 4: {cut}")`.
4. **format (parts[3]) membership** vs the SeasonPhase format choices
   (`{single_elimination, double_elimination, round_robin,
   round_robin_double_elim, swiss}`) ⇒ NEW LOCKED
   `ValueError(f"unknown tournament_format: {fmt!r}")`.
5. **series tiers (parts[4..7]) parse-int + membership** in `{1, 3, 5}` (each: empty
   → default `"1"`; non-int → `"malformed phase composition"`; parsed not in
   `{1,3,5}`) ⇒ NEW LOCKED
   `ValueError(f"series length must be 1, 3, or 5: {n}")` (`n` the parsed int).
6. **wb (parts[8]) / lb (parts[9]) parse-int** (empty → default `"0"`; non-int →
   `"malformed phase composition"`); **the (wb, lb) combo validated ONLY when
   `format == "round_robin_double_elim"`** against the six locked combos
   `{(4,0), (4,2), (8,0), (8,4), (16,0), (16,8)}` ⇒ NEW LOCKED
   `ValueError(f"invalid wb/lb combo for round_robin_double_elim: {wb}/{lb}")`.
   For any non-RR→DE format wb/lb are parsed-and-stored (no combo check — they stay
   inert ints, mirroring how `Tournament` carries `0/0` for non-RR→DE).
7. **swiss (parts[10]) parse-int** (empty → default `"0"`; non-int →
   `"malformed phase composition"`). No range check at the parser (the engine's Swiss
   resolve/clamp/freeze owns it); a negative swiss is impossible (the composer input
   has `min="0"`, and a tampered negative is caught by the existing positive-int
   coerce path → `"malformed phase composition"` if it carries a sign char the
   `int()` accepts but `< 0`; if `int()` accepts it, leave it — the COUNT/parity
   defence at `lock_and_build` is the backstop).

**SHAPE only at the parser** — NO COUNT/parity check here that depends on participant
count (`wb <= n`, `wb + lb <= n`, even-N for Swiss). Those are caught defence-in-depth
by the EXISTING `Tournament.lock_and_build` raises (§5).

### Pre-existing `ValueError` strings — PRESERVED VERBATIM
- `"malformed phase composition"`
- `f"unknown phase type: {token!r}"`  (`member_night` still rejected at type level)
- `f"unknown schedule_format: {schedule_format!r}"`  (RR branch)
- `f"unknown tournament_mode: {mode!r}"`
- `f"tournament cut must be 0 or at least 4: {cut}"`
- `"composition must contain at least one round-robin phase"`
- `"a tournament phase requires a preceding round-robin phase"`  (fires only for
  `tournament_mode == "standings"` with no preceding RR — c-3c, UNCHANGED)

Module stays **Django-free** (plain `ValueError`; the form layer re-wraps as a
`forms.ValidationError` on the `phases` field). Allowlist `dataclasses` + `typing`
ONLY (no `json`) — UNCHANGED.

---

## 4. Build — `Season.activate_pending_tournament_phase` (`matches/models.py`, ~L1173)

ONE changed `Tournament.objects.create(...)` call. Everything else in the method —
the idempotency/gate guards, the c-3d cut slice (`order = order[:phase.tournament_cut]`
before `if not order: return`), `_seed_order_for_phase`, the participant loop
(`seed = position + 1`), the name (`f"{name} Playoffs"` for `standings` else
`f"{name} Tournament"`), `team_assembly="preset"`, `state="setup"`,
`phase.tournament` / `save(update_fields=["tournament"])`, `lock_and_build()` — is
**UNCHANGED**.

**Current (c-3d) create call:**
```python
        tournament = Tournament.objects.create(
            name=name,
            format="single_elimination",
            team_assembly="preset",
            state="setup",
        )
```

**New (c-3e) create call — `format` live + 7 sub-config kwargs from the phase:**
```python
        tournament = Tournament.objects.create(
            name=name,
            format=phase.tournament_format,
            team_assembly="preset",
            state="setup",
            final_series_length=phase.final_series_length,
            semifinal_series_length=phase.semifinal_series_length,
            quarterfinal_series_length=phase.quarterfinal_series_length,
            earlier_series_length=phase.earlier_series_length,
            wb_advancers=phase.wb_advancers,
            lb_advancers=phase.lb_advancers,
            swiss_rounds=phase.swiss_rounds,
        )
```

- `Season._seed_order_for_phase` is **BYTE-IDENTICAL** — NOT edited. The cut slice
  still applies to its output at the caller (c-3d).
- `lock_and_build()` already dispatches on `self.format` for all five formats and
  consumes the 7 sub-config fields (series tiers via
  `series_length_for_round`/`series_length_for_depth` → `_persist_elim_specs`; wb/lb
  for RR→DE via `build_de_finals_if_rr_finished`; `swiss_rounds` for Swiss). Consumed
  VERBATIM — **no engine edit**.
- A `random_draw` `team_assembly` is NOT introduced (`tournament_mode="random_draw"`
  remains parser-rejected per c-3c; the build hardcodes `team_assembly="preset"`).

---

## 5. COUNT/parity defence-in-depth — EXISTING `lock_and_build` raises (no new code)

`Tournament.lock_and_build` (~L1726) already raises
`django.core.exceptions.ValidationError` on:
- `< 4` participants — `"A tournament requires at least 4 participants."`
- `wb_advancers > n` — `"wb_advancers exceeds participant count."`
- `wb_advancers + lb_advancers > n` — `"wb_advancers + lb_advancers exceeds
  participant count."`
- Swiss odd N — `"Swiss requires an even number of participants."`

These fire when the post-cut seeded order doesn't fit the chosen format. The build
caller (`activate_pending_tournament_phase`, `@transaction.atomic`) lets the
`ValidationError` propagate (a degenerate config, not a happy path) — NO new
`Season.clean()` / `SeasonPhase.clean()` guard, NO form cross-field guard.

---

## 6. Composer — `templates/leagues/create.html`

Enable the format select (currently the disabled placeholder
`league-create-phase-tournament-format-{i}`, L182-190) and add the 4 series-length
selects + wb/lb combo + swiss-rounds input. The `serialize()` emits the full
positional token. `applyType()` show/hide-by-format JS (mirror
`tournament_create.html`'s `tournamentCreateToggle`).

### NEW per-tournament-row controls (all tournament-rows-only, hidden for RR via
`applyType()`)
- **Format select** — flip `league-create-phase-tournament-format-{i}` from `disabled`
  to enabled; class `phase-tournament-format-select`; 5 options matching
  `SeasonPhase.TOURNAMENT_FORMAT_CHOICES` (`single_elimination` selected /
  `double_elimination` / `round_robin` / `round_robin_double_elim` / `swiss`).
- **4 series-length selects** — DOM ids `league-create-phase-final-sl-{i}` /
  `league-create-phase-semifinal-sl-{i}` / `league-create-phase-quarterfinal-sl-{i}`
  / `league-create-phase-earlier-sl-{i}`; class `phase-series-length-select` (or a
  per-tier class); each Bo1/Bo3/Bo5 (`1`/`3`/`5`), Bo1 selected; shown for
  `single_elimination` / `double_elimination` / `round_robin_double_elim`.
- **wb/lb combo select** — DOM id `league-create-phase-rrde-combo-{i}`; class
  `phase-rrde-combo-select`; six locked combo option value-strings mirroring
  `tournament_create.html` (`4/0`, `4/2`, `8/0`, `8/4`, `16/0`, `16/8` — the exact
  value-string format mirrors the standalone form's rrde-combo options); shown for
  `round_robin_double_elim` ONLY. `serialize()` splits the combo on `/` into wb + lb.
- **swiss-rounds input** — DOM id `league-create-phase-swiss-rounds-{i}`; class
  `phase-swiss-rounds-input`; `<input type="number" min="0" value="0">`; shown for
  `swiss` ONLY.

### `applyType()` + format-change toggle
- RR row: format/series/combo/swiss all hidden, schedule-format select shown (c-3a).
- Tournament row: format select shown; the four series selects shown for SE/DE/RR→DE;
  rrde-combo shown for RR→DE only; swiss-rounds shown for Swiss only. The
  tournament-format select's `change` re-runs the sub-config show/hide (mirror
  `tournamentCreateToggle`).
- Existing mode select (`league-create-phase-mode-{i}`, c-3c) + cut input
  (`league-create-phase-cut-{i}`, c-3d) + `phase-tournament-pending` note are
  **PRESERVED**.

### `serialize()` — emit the full positional token
For a tournament row, read `.phase-mode-select` (default `standings`),
`.phase-cut-input` (default `"0"`), the now-enabled
`.phase-tournament-format-select` (default `single_elimination`), the 4
`.phase-series-length-select` tiers (default `1` each), `.phase-rrde-combo-select`
(split on `/` into wb/lb, default `0/0`), `.phase-swiss-rounds-input` (default `0`),
and emit:
```
tournament:<mode>:<cut>:<format>:<fsl>:<ssl>:<qsl>:<esl>:<wb>:<lb>:<swiss>
```
RR rows still emit `round_robin:<format>` (c-3a) UNCHANGED.

### Preserved DOM ids (Part2b / c-3a / c-3c / c-3d)
`league-create-phases-composer`, `league-create-add-block`, `league-create-phases`,
`league-create-phase-row-{i}`, `league-create-phase-type-{i}`,
`league-create-phase-format-{i}`, `league-create-phase-mode-{i}`,
`league-create-phase-cut-{i}`, `league-create-phase-tournament-format-{i}` (now
ENABLED), `league-create-member-night-note`, class `phase-tournament-pending`. The
existing create-form ids (`league-create-form`, `-league-name`, `-season-name`,
`-start-date`, `-num-teams`, `-schedule-format`, `-mean`, `-std-dev`, `-map-mode`,
`-map-pool`, `-submit`) UNCHANGED.

**Test boundary:** tests assert the new DOM ids exist + the serialized token shape —
NOT exact CSS.

---

## 7. Creation / carry-forward (`matches/league_views.py`)

Both `SeasonPhase.objects.create(...)` loops sit inside their existing
`@transaction.atomic` blocks.

### `league_create` (~L559) — set all 8 new fields from `spec`
Add to the existing create call (which already passes `tournament_cut=spec.
tournament_cut` and lets `tournament_format` take the column default in c-3d):
```python
            tournament_format=spec.tournament_format,
            final_series_length=spec.final_series_length,
            semifinal_series_length=spec.semifinal_series_length,
            quarterfinal_series_length=spec.quarterfinal_series_length,
            earlier_series_length=spec.earlier_series_length,
            wb_advancers=spec.wb_advancers,
            lb_advancers=spec.lb_advancers,
            swiss_rounds=spec.swiss_rounds,
```
(`tournament_format` now comes from `spec` — there IS a `PhaseSpec.tournament_format`
this slice, so the c-3d "left to column default" note no longer applies.)

### `next_season` (~L2141) — carry all 8 forward from `src` verbatim
The c-3d call already carries `tournament_cut=src.tournament_cut` +
`tournament_format=src.tournament_format`. Add:
```python
            final_series_length=src.final_series_length,
            semifinal_series_length=src.semifinal_series_length,
            quarterfinal_series_length=src.quarterfinal_series_length,
            earlier_series_length=src.earlier_series_length,
            wb_advancers=src.wb_advancers,
            lb_advancers=src.lb_advancers,
            swiss_rounds=src.swiss_rounds,
```
(`tournament_format` already carried verbatim from c-3d — UNCHANGED.)

---

## 8. Admin (`matches/admin.py`)

`SeasonPhaseAdmin.list_display` — append the 7 new sub-config columns after
`tournament_cut`. (`tournament_format` already present from c-3d.) Final order:
`("season", "ordinal", "phase_type", "tournament_mode", "tournament_format",
"tournament_cut", "final_series_length", "semifinal_series_length",
"quarterfinal_series_length", "earlier_series_length", "wb_advancers",
"lb_advancers", "swiss_rounds")`. (Exact tail order at Code-agent discretion;
all 7 must appear.)

---

## 9. UNCHANGED (state explicitly)

- **Completion** `Season._phase_complete` — UNCHANGED (tournament phase complete ⇔
  `phase.tournament_id is not None AND phase.tournament.state == "completed"`; the
  five formats all reach `state="completed"` via the engine).
- **Champion** `Season._stamp_champion_for_final_phase` — UNCHANGED (champion from
  `final_phase.tournament.champion`).
- **Drain views** `play_single_round` / `play_playoffs` + `play_playoffs_task` —
  UNCHANGED (they drain via `play_next_node`, which dispatches per format).
- **`Season._seed_order_for_phase`** — BYTE-IDENTICAL, NOT edited.
- **Cut slice** (c-3d) — UNCHANGED.
- **Tournament engine** (`lock_and_build`, `build_bracket`,
  `build_double_elim_bracket`, `build_rr_de_finals_bracket`, `build_swiss_round`,
  `find_next_node`, `advance_winner`, `advance_loser`, `resolve_bye_chain`,
  `series_length_for_round`, `series_length_for_depth`, `_persist_elim_specs`,
  `build_de_finals_if_rr_finished`, `advance_swiss_if_round_finished`) — consumed
  VERBATIM.
- **Simulator / RNG / `Match` model** — UNCHANGED.
- **NO re-baseline** (tournament sims non-deterministic; no simulation mechanics
  change).
- **`tournament_create.html`** standalone form — UNCHANGED (read-only reference for
  the embed composer's parity).

---

## 10. Test boundary — files that extend + what they assert

- **`test_phase_composer.py`** — parser output incl. the 8 new `PhaseSpec` fields
  (full token round-trips per format; defaults when trailing fields omitted); NEW
  `ValueError`s (`unknown tournament_format`, `series length must be 1, 3, or 5`,
  `invalid wb/lb combo for round_robin_double_elim` — the wb/lb combo validated ONLY
  when `format == "round_robin_double_elim"`); `len(parts) > 11` ⇒
  `"malformed phase composition"`; every pre-existing `ValueError` preserved;
  backward-compat of every c-3d/c-3c serialized token; `TestNoDjangoImportsLeaked`.
- **`test_season_phase.py`** — the 7 new columns' defaults (series tiers `1`, wb/lb
  `0`, swiss `0`) + choices (series tiers `{1,3,5}`; wb/lb/swiss no choices);
  `tournament_format` still defaults `single_elimination` with the 5-tuple choices.
- **`test_season_playoffs.py`** (NOTE trailing `s`) — the built `Tournament`'s
  `format` + all 7 sub-config fields match the phase for each of the five formats;
  the non-single-elim brackets ACTUALLY build (double_elim ⇒ winners/losers/
  grand_final `BracketNode`s; round_robin ⇒ `round_robin` nodes; swiss ⇒ `swiss`
  nodes; rr→de ⇒ RR seeding nodes then deferred DE finals); drain-to-champion via
  the play loop crowns the Season champion; `cut` interacts (post-cut count fits the
  format or raises the EXISTING `lock_and_build` `ValidationError`).
- **`test_league_create.py`** — a composed tournament phase persists all 8 new fields
  from `spec`.
- **`test_league_next_season.py`** — `next_season` carries all 8 forward verbatim
  (hand-set a source phase's sub-config via ORM, assert the copy preserves every
  field).

**Assertion discipline:** schema-level outcomes only (built `format`, sub-config
field values, `BracketNode` shapes per format, champion id, parsed spec fields,
ValueErrors, DOM ids) — **NEVER raw simulated point totals** (tournament sims are
non-deterministic).

---

## 11. Locked names (quick index)

- **Model:** 7 new `SeasonPhase` columns `final_series_length` /
  `semifinal_series_length` / `quarterfinal_series_length` / `earlier_series_length`
  (`PositiveSmallIntegerField`, choices `{1,3,5}`, default `1`) + `wb_advancers` /
  `lb_advancers` / `swiss_rounds` (`PositiveSmallIntegerField`, no choices, default
  `0`), appended after `tournament_cut`. `tournament_format` flips dormant→live (no
  schema change). Migration `0047_seasonphase_tournament_subconfig`, dep
  `0046_seasonphase_format_cut`, 7× `AddField`, no `RunPython`.
- **PhaseSpec:** 8 new trailing fields `tournament_format="single_elimination"`,
  `final_series_length=1`, `semifinal_series_length=1`,
  `quarterfinal_series_length=1`, `earlier_series_length=1`, `wb_advancers=0`,
  `lb_advancers=0`, `swiss_rounds=0`.
- **Wire grammar:** `tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss`
  (positional, trailing-optional). `len(parts) > 11` ⇒
  `"malformed phase composition"`. NEW `ValueError`s:
  `f"unknown tournament_format: {fmt!r}"`, `f"series length must be 1, 3, or 5: {n}"`,
  `f"invalid wb/lb combo for round_robin_double_elim: {wb}/{lb}"` (six combos
  `{(4,0),(4,2),(8,0),(8,4),(16,0),(16,8)}`, checked only for RR→DE). Module stays
  Django-free; allowlist `dataclasses` + `typing` (no `json`).
- **Build:** `Season.activate_pending_tournament_phase` — one changed
  `Tournament.objects.create(format=phase.tournament_format, ...)` + 7 sub-config
  kwargs; rest UNCHANGED; `_seed_order_for_phase` BYTE-IDENTICAL.
- **COUNT/parity:** existing `Tournament.lock_and_build` `ValidationError`s
  (`< 4` / `wb > n` / `wb + lb > n` / Swiss-even-N) — no new guard.
- **Composer DOM ids (new):** `league-create-phase-final-sl-{i}` /
  `-semifinal-sl-{i}` / `-quarterfinal-sl-{i}` / `-earlier-sl-{i}` /
  `-rrde-combo-{i}` / `-swiss-rounds-{i}`; format select
  `league-create-phase-tournament-format-{i}` (ENABLED). `serialize()` emits the
  full positional token.
- **Creation/carry-forward:** `league_create` sets all 8 from `spec`; `next_season`
  carries all 8 from `src`; both inside `@transaction.atomic`.
- **Admin:** `SeasonPhaseAdmin.list_display` appends the 7 sub-config columns.
- **Unchanged:** `_phase_complete`, `_stamp_champion_for_final_phase`,
  `play_single_round` / `play_playoffs` / `play_playoffs_task`, simulator / RNG /
  tournament engine, `Match` model, `tournament_create.html`. No re-baseline.
- **ADR:** extend ADR-0023 (Part2c-3e addendum), no new ADR.
