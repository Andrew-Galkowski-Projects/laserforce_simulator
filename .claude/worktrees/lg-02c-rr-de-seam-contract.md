# LG-02c · RR→Double-elimination tournament format — Seam Contract

A 4th `Tournament.format`, **`"round_robin_double_elim"`** (label
`"Round robin → Double elimination"`, em-dash arrow U+2192): a round-robin
**Seeding stage** whose final Standings rank seeds a double-elimination **Finals
stage**. Builds on the SHIPPED round-robin (LG-02c) and double-elimination
(LG-02c) formats — reuses their pure + persist machinery verbatim. The new work
is (a) one pure builder that fuses a re-tagged WB + a pre-seeded LB, (b) a
deferred finals build triggered when the last RR node resolves, (c) a shared
persist helper extracted from `lock_and_build`, and (d) the create-form combo +
detail cut-line markers. **Non-deterministic** (`simulate_match` fresh per-round
seeds) ⇒ no SIM-07/08 interaction, **NO Score Calibration re-baseline**. ADR-0021
is EXTENDED for the deferred-build decision; **no new ADR, no new CONTEXT.md
term** beyond the already-written **Round robin → double elimination**.

Three parallel agents (code / tests / docs) build against this. Every new
method name, signature, field, dict key, DOM id, and return shape is pinned
below.

---

## Locked names

### Pure module — `matches/bracket.py`
- **`build_rr_de_finals_bracket(upper_specs: list[ParticipantSpec], lower_specs: list[ParticipantSpec]) -> list[BracketNodeSpec]`** — NEW. The fused WB+LB+GF finals builder. `upper_specs` = WB starters (seeds `1..wb` by RR rank), `lower_specs` = LB pre-seeds (seeds `wb+1..wb+lb` by RR rank). Stays within the frozen import allowlist (`dataclasses`/`typing`/`math`/`collections` only — **no new import**; `TestNoDjangoImportsLeaked` stays green).
- **No other pure-module change.** `build_bracket`, `build_double_elim_bracket`, `BracketNodeSpec` (incl. the LG-02c fields `bracket_type`/`loser_advances_to`/`loser_advances_to_slot`/`depth`), `ParticipantSpec`, `series_length_for_depth`, `series_length_for_round`, `resolve_bye_chain`/`_resolve_bye_chain_de`, `find_next_node`, `_BRACKET_RANK` (already carries `"round_robin": 3`), `advance_winner`, `advance_loser`, `stage_progress`, `break_tie`, `default_seed_order`, `clinch_threshold`, `series_winner_slot` — **all unchanged**.

### Model — `matches/models.py`
- **`Tournament.FORMAT_CHOICES`** gains `("round_robin_double_elim", "Round robin → Double elimination")` as the **4th** entry (label uses em-dash arrow `→` U+2192). The `format` field declaration is otherwise unchanged (`CharField(max_length=32)`, `default="single_elimination"` — `"round_robin_double_elim"` is 23 chars, fits 32).
- **`Tournament.wb_advancers`** — NEW `models.PositiveSmallIntegerField(default=0)`. **No `choices`** (the create form enforces the valid combo; the model field holds a plain resolved int, mirroring how `BracketNode.series_length` carries a resolved int with no choices). Placed immediately AFTER the four `*_series_length` fields.
- **`Tournament.lb_advancers`** — NEW `models.PositiveSmallIntegerField(default=0)`. Same rules, declared immediately after `wb_advancers`.
- Both fields are **create-time only, frozen at lock** — no view rewrites them post-setup. Meaningful only for `format == "round_robin_double_elim"`; other formats leave them `0`.
- **`Tournament.lock_and_build()`** — EXTENDED: the existing `if self.format == "round_robin":` guard widens to `if self.format in ("round_robin", "round_robin_double_elim"):` (the RR-node build is byte-identical for both); the RR branch also validates the lock-time count fit for the RRDE format (see Model spec).
- **`Tournament._persist_elim_specs(self, specs: list[BracketNodeSpec]) -> None`** — NEW private helper. Extracts the DE-node persist loop + the `advances_to`/`loser_advances_to` wiring pass + the `resolve_bye_chain` cascade pass out of `lock_and_build`, so BOTH the existing single/double-elim lock path AND the new deferred finals build reuse it verbatim. (`series_length` stamping stays in `lock_and_build` — the deferred build resolves its own per-depth `series_length` from the spec `depth` before calling the helper; see Model spec for the exact boundary.)
- **`Tournament.build_de_finals_if_rr_finished(self) -> None`** — NEW `@transaction.atomic`. Triggered when the last RR node resolves; builds the DE finals from RR standings rank. Guards + idempotency in Model spec.
- `round_robin_standings()`, `complete_round_robin_if_finished()`, `find_next_playable_node()`, `_node_to_dict()`, `count_series_wins()`, `BracketNode`, `TournamentParticipant`, `SeriesMatch` — **unchanged**.

### Engine — `matches/tournament_engine.py`
- **`play_next_node`** RR guard generalizes. The current `if tournament.format == "round_robin":` block (after stamping `node.winner`) is rekeyed on **`node.bracket_type == "round_robin"`** and dispatches on format:
  - `tournament.format == "round_robin"` → `tournament.complete_round_robin_if_finished()`
  - `tournament.format == "round_robin_double_elim"` → `tournament.build_de_finals_if_rr_finished()`
  - then `return node` (do NOT fall through to the elim advance/drop/GF-reset/crown block).
- DE-stage nodes (`bracket_type` in `winners`/`losers`/`grand_final`) **fall through to the UNCHANGED elim block** — even for an RRDE tournament. The elim advance/drop/GF-reset/crown logic is byte-unchanged.

### Migration — `matches/migrations/`
- **`0038_tournament_rr_de.py`** (CONFIRMED: latest is `0037_tournament_round_robin.py`). Dep `("matches", "0036_bracketnode_double_elimination")`? — **NO**; dep `("matches", "0037_tournament_round_robin")`. Ops (no `RunPython`, no backfill — ADR-0004):
  1. `AlterField(Tournament.format)` — widen choices to the 4-tuple incl. `("round_robin_double_elim", "Round robin → Double elimination")`.
  2. `AddField(Tournament.wb_advancers)` — `PositiveSmallIntegerField(default=0)`.
  3. `AddField(Tournament.lb_advancers)` — `PositiveSmallIntegerField(default=0)`.

### Create form / view — `matches/tournament_views.py` + `tournament_create.html`
- **POST field `format`** — widened forgiving-fallback parse: accept `single_elimination` / `double_elimination` / `round_robin` / **`round_robin_double_elim`**, anything else → `single_elimination`.
- **POST field `rrde_combo`** — NEW single `<select name="rrde_combo">` (DOM id **`tournament-create-rrde-combo`**) enumerating the valid shape combos. Parsed into `wb_advancers` / `lb_advancers`.
- `Tournament.objects.create(...)` passes `wb_advancers=` / `lb_advancers=` (`0`/`0` for non-RRDE formats).

### Detail page — `matches/tournament_views.py` + `tournament_detail.html`
- NEW context keys on `_detail_context(...)`: **`tournament_stage`** (`str`) and **`cut_labels`** (`dict[int, str]`, `team_id -> "wb" | "lb" | "out"`). The existing keys (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`, `import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`) are **unchanged**.
- NEW DOM ids: **`tournament-stage-badge`** (the stage badge) and a per-standings-row cut class substring (`tournament-standings-cut-wb` / `-lb` / `-out`) applied to each RR standings row in the seeding stage.
- Reused VERBATIM: `tournament-rr-crosstable`, `tournament-rr-standings`, `tournament-bracket-winners`, `tournament-bracket-losers`, `tournament-bracket-grand-final`, the per-section round columns `tournament-bracket-{bracket_type}-round-{n}`, `tournament-champion-banner`, and every play/lock/import/seeding control id.

### Tests
- `matches/tests/test_bracket.py` — EXTEND (pure builder).
- `matches/tests/test_tournament_models.py` — EXTEND (fields + deferred-build + seeding-from-standings + lock-time count validation + shared persist helper byte-identity).
- `matches/tests/test_tournament_engine.py` — EXTEND (RR→DE transition + champion crowned by GF).
- `matches/tests/test_tournament_views.py` — EXTEND (create combo parse/persist + detail cut markers + stage badge).
- `matches/tests/test_tournament_tasks.py` — EXTEND (play-all drains seeding then finals to a champion).

---

## Pure builder spec — `build_rr_de_finals_bracket`

```python
def build_rr_de_finals_bracket(
    upper_specs: list[ParticipantSpec],
    lower_specs: list[ParticipantSpec],
) -> list[BracketNodeSpec]:
    ...
```

**Inputs.** `upper_specs` are the Winners-bracket starters, RR-rank-ordered with
`seed = RR rank` (rank 1 → WB seed 1, …, rank `wb` → WB seed `wb`). `lower_specs`
are the Losers-bracket pre-seeds, RR-rank-ordered with `seed = wb+1 .. wb+lb`.
Each is a `ParticipantSpec(team_id, seed)`. `len(upper_specs)` is a power of two
(== `wb_advancers ∈ {4, 8, 16}`); `len(lower_specs)` is either `0` or
`wb_advancers // 2`.

**Output.** A `list[BracketNodeSpec]` consumed by the SAME persist+wire path as
`build_double_elim_bracket` output — every spec carries `bracket_type`
(`winners`/`losers`/`grand_final`), `advances_to`/`advances_to_slot` (2-tuple
`(bracket_round, position)` + slot), `loser_advances_to`/`loser_advances_to_slot`
(3-tuple `(bracket_type, bracket_round, position)` + slot), `is_bye`,
`winner_id`, `seed_a`/`seed_b`/`team_a_id`/`team_b_id`, and **`depth`** (distance
to GF1: GF1/GF2 = 0, WB-final & LB-final = 1, earlier deeper). **No byes anywhere
in the output** (the WB is exactly a power of two of real teams; the LB R1 is
exactly filled).

### `lower_specs == []` — plain DE delegation
When there are no LB pre-seeds the finals are a plain top-`wb`
double-elimination. The builder **MUST produce EXACTLY
`build_double_elim_bracket(upper_specs)`** — delegate to it directly (the
simplest, locked, choice) so the result is provably identical (the test asserts
spec-list equality). No re-tagging, no LB pre-fill.

### `lower_specs` non-empty — fused WB+LB
The WB is `build_bracket(upper_specs)` **re-tagged `bracket_type="winners"`**
(reuse `build_double_elim_bracket`'s own WB re-tag pass — same seeding/pairing,
no byes since `len(upper_specs)` is a power of two). The difference from a plain
DE is **only the LB round-1 wiring**:

- **LB round 1** has `wb/2` nodes (== `lb_advancers`). Each LB-R1 node has its
  **slot "a" PRE-FILLED with a `lower_spec`** (`team_a_id`/`seed_a` set,
  `team_b_id`/`seed_b` left `None` until a WB-R1 dropper arrives) — the
  `lb_advancers` pre-seeds fill LB-R1 slot "a" in seed order (LB pre-seed
  `wb+1` → LB-R1 pos 0 slot "a", `wb+2` → pos 1 slot "a", …).
- **Each WB-R1 node's `loser_advances_to`** points at the **matching LB-R1 node's
  slot "b"** — one pre-seed (slot "a") vs one WB-R1 dropper (slot "b"). The
  WB-R1 → LB-R1 mapping is the naive same-position drop (WB-R1 pos `i` → LB-R1
  pos `i`, slot "b"); `wb/2` WB-R1 nodes ↔ `wb/2` LB-R1 nodes, exactly paired.
- **WB-R(r≥2) losers** drop into the LB exactly as in `build_double_elim_bracket`
  (a WB-R`r` loser feeds the minor LB round that consumes that WB round's
  losers — the existing naive same-position drop; **no anti-rematch folding**,
  inherited limitation).

**LB topology (prose, for `lb = wb/2`).** Let `W = log2(wb)` (number of WB
rounds). The LB has `2W - 1` rounds: round 1 pairs each pre-seed (slot "a")
against the matching WB-R1 dropper (slot "b") — `wb/2` nodes; thereafter the LB
alternates **minor** rounds (consume the next WB round's losers against LB
survivors) and **major** rounds (LB-vs-LB), the final LB round consuming the
WB-final loser, narrowing to one LB champion. This is the SAME LB shape
`build_double_elim_bracket` produces for a power-of-two field of size `wb`,
**except** that LB-R1's slot "a" is pre-filled with the `lb` pre-seeds instead of
receiving a second WB-R1 dropper. (Equivalently: a plain top-`wb` DE has LB-R1
pairing two WB-R1 droppers per node; the RRDE variant pairs ONE WB-R1 dropper
against ONE RR-ranked pre-seed.)

**Grand final.** GF1 (`bracket_type="grand_final"`, the lower `bracket_round`)
takes the WB champion (slot "a") + LB champion (slot "b"); GF1's
`loser_advances_to` points at **GF2** (so the LB-champ path Advances both into
GF2 on a GF1 LB-champ win — the Bracket reset). GF2 (the higher `bracket_round`)
has `advances_to = None` (final node) and `loser_advances_to = None`. **GF1/GF2
`depth = 0`.**

**`depth` values.** `depth` = distance to GF1. GF1 = GF2 = 0; WB-final &
LB-final = 1; each earlier round +1. These feed `series_length_for_depth` at
build/deferred-build time exactly as a plain DE does.

**No-byes invariant.** Because `wb` is a power of two filled by exactly `wb` real
teams, and `lb = wb/2` exactly fills LB-R1 slot "a" (or `lb = 0` ⇒ plain DE with
no byes for a power-of-two field), the output has **zero `is_bye` nodes** and
the deferred-build's `resolve_bye_chain` pass is a no-op (returns `[]`). Tests
assert no spec has `is_bye=True`.

**Import-allowlist guarantee.** The builder adds **no new import** — it composes
`build_bracket` / `build_double_elim_bracket` and constructs `BracketNodeSpec`s,
all already in-module. `TestNoDjangoImportsLeaked` stays green.

---

## Model spec — `matches/models.py`

### Field placement
`wb_advancers` and `lb_advancers` are declared **immediately after the
`earlier_series_length` field** (the end of the `*_series_length` block) and
before `created_at`/`champion`. Both `PositiveSmallIntegerField(default=0)`, **no
`choices`** — the create form's `rrde_combo` select is the single source of valid
shape combos; the model holds the resolved ints (mirrors `BracketNode.series_length`).

### `lock_and_build` branch dispatch
The existing RR guard widens:

```python
if self.format in ("round_robin", "round_robin_double_elim"):
    # ... build ONLY the round-robin Seeding nodes (REUSE the existing
    #     round_robin branch verbatim: one BracketNode per generate_schedule
    #     fixture, bracket_type="round_robin", series_length=1, no advance edges) ...
    #
    # RRDE-only lock-time COUNT validation (before / right after the RR node
    # build, inside the same @transaction.atomic), raising
    # django.core.exceptions.ValidationError on a bad fit:
    if self.format == "round_robin_double_elim":
        n = len(participants)
        if self.wb_advancers > n:
            raise ValidationError("wb_advancers exceeds participant count.")
        if self.wb_advancers + self.lb_advancers > n:
            raise ValidationError(
                "wb_advancers + lb_advancers exceeds participant count."
            )
    self.state = "active"
    self.save(update_fields=["state"])
    return
```

The **SHAPE** of `(wb_advancers, lb_advancers)` (`wb ∈ {4,8,16}`,
`lb ∈ {0, wb//2}`) is validated in the create form; the **COUNT fit** (`wb <= n`
and `wb + lb <= n`) is validated here and raises
`django.core.exceptions.ValidationError`, surfaced by the existing lock view via
`messages.error` (LG-02a precedent). The RRDE lock builds ONLY the RR nodes — the
DE finals are NOT built at lock; they are deferred (see below). The elim branch
(single/double-elim) is unchanged.

### Shared persist helper `_persist_elim_specs(self, specs)`
Extract the existing `lock_and_build` elim machinery into a private helper so
BOTH the single/double-elim lock path AND `build_de_finals_if_rr_finished` reuse
it verbatim:

1. Persist loop — `BracketNode.objects.create(...)` per spec (carrying
   `bracket_type`, `team_a`/`team_b` via a `team_by_id` map, `seed_a`/`seed_b`,
   `is_bye`, `advances_to_slot`, `loser_advances_to_slot`, `winner`, and the
   `series_length` the CALLER resolved onto the spec — see boundary note).
2. `advances_to` wiring pass (2-tuple coord → `_node_at` bracket-type resolution).
3. `loser_advances_to` wiring pass (3-tuple coord → `node_by_pos` lookup).
4. `resolve_bye_chain` cascade pass (winner-side byes + DE Drop-bye collapse).

**Boundary note (series_length).** `series_length` stamping stays in the CALLER,
not the helper — the single/double-elim `lock_and_build` resolves
`series_length_for_round` / `series_length_for_depth` per spec before persisting,
and the deferred finals build resolves `series_length_for_depth(spec.depth, ...)`
per spec the same way. The simplest pinned approach: the helper signature is
`_persist_elim_specs(self, specs, series_length_by_spec)` where the caller passes
a parallel resolved-length lookup (e.g. a `dict` keyed on
`(bracket_type, bracket_round, position)` or a per-spec list); OR the helper
takes the four resolved `*_series_length` ints + an `is_de` flag and resolves
internally. **Code agent's discretion on the exact length-resolution boundary**,
but the persist + 3 wiring passes MUST move into the helper unchanged, and the
existing single/double-elim `lock_and_build` behaviour MUST stay **byte-identical**
(same nodes, same edges, same series_length, same byes — pinned by a
`TestLockAndBuildSingleElimUnchanged` / `...DoubleElimUnchanged` regression test).
`team_by_id` is built inside the helper from `self.participants` for the lock
path; for the deferred build the upper/lower specs reference RR participants, so
the helper resolves teams from `self.participants` the same way (every finalist
is an enrolled participant).

### Stage is DERIVED, not stored
"Finals stage built" iff `self.nodes.exclude(bracket_type="round_robin").exists()`
(or equivalent). **No `stage` column.** The Tournament stays `state="active"`
across the seeding→finals transition; the champion is crowned later by the DE
Grand final.

### `build_de_finals_if_rr_finished(self) -> None` — `@transaction.atomic`
Guards (in order), no-op unless ALL hold:
1. `self.format == "round_robin_double_elim"`.
2. `self.state == "active"`.
3. every `bracket_type="round_robin"` node has `winner_id is not None`
   (`not self.nodes.filter(bracket_type="round_robin", winner__isnull=True).exists()`).
4. **Idempotency:** the finals are not already built —
   `not self.nodes.exclude(bracket_type="round_robin").exists()`. If finals nodes
   already exist, no-op.

When all guards pass:
- `rows = self.round_robin_standings()` (the SHIPPED RR standings — RR-rank
  ordered, `rows[0]` is RR rank 1).
- `upper = [ParticipantSpec(team_id=rows[i].team_id, seed=i + 1) for i in range(self.wb_advancers)]` (top `wb` RR-ranked teams → WB seeds 1..wb; seed = 1-based RR rank).
- `lower = [ParticipantSpec(team_id=rows[self.wb_advancers + j].team_id, seed=self.wb_advancers + j + 1) for j in range(self.lb_advancers)]` (next `lb` RR-ranked teams → LB pre-seeds, seeds wb+1..wb+lb).
- the rest of `rows` are **eliminated** (never enter the finals).
- `specs = build_rr_de_finals_bracket(upper, lower)`.
- resolve each spec's `series_length` via `series_length_for_depth(spec.depth, final=self.final_series_length, semifinal=self.semifinal_series_length, quarterfinal=self.quarterfinal_series_length, earlier=self.earlier_series_length)`.
- persist + wire via `self._persist_elim_specs(specs, ...)`.

The Tournament STAYS `state="active"`. The champion is crowned by the DE Grand
final later (via `play_next_node`'s unchanged crown block). **No byes** in the
finals, so the `resolve_bye_chain` pass inside the helper is a no-op.

### Lock-time count validation summary
Raised at `lock_and_build` (the RRDE branch), `ValidationError`:
- `wb_advancers > participant_count`.
- `wb_advancers + lb_advancers > participant_count`.
(The SHAPE — power-of-two `wb`, `lb ∈ {0, wb//2}` — is enforced in the create
form, not re-validated here.)

---

## Engine spec — `matches/tournament_engine.py`

The current RR guard (after `node.save(update_fields=["winner"])`):

```python
if tournament.format == "round_robin":
    tournament.complete_round_robin_if_finished()
    return node
```

generalizes to key on **`node.bracket_type`** and dispatch on format:

```python
if node.bracket_type == "round_robin":
    if tournament.format == "round_robin":
        tournament.complete_round_robin_if_finished()
    elif tournament.format == "round_robin_double_elim":
        tournament.build_de_finals_if_rr_finished()
    return node
```

- A resolved RR (seeding-stage) node NEVER falls through to the elim
  advance/drop/GF-reset/crown block.
- When the LAST RR node resolves in an RRDE tournament,
  `build_de_finals_if_rr_finished()` persists the DE finals; the tournament stays
  `active` and the next `play_next_node` call finds the first playable DE node.
- DE-stage nodes (`bracket_type in winners/losers/grand_final`) **fall through to
  the UNCHANGED elim block** — winner advance, loser Drop, GF1 Bracket-reset, GF2
  / single-elim-final crown. **Confirmed: the elim block is byte-unchanged.**
- `play_next_node` callers (`tournament_play_next`, `play_tournament_task`,
  `tournament_play_all`, `tournament_play_status`) keep their URLs + 5-key status
  JSON; `stage_progress` reports completion across the RR groups THEN the WB/LB/GF
  groups (the finals groups appear only after the deferred build, so Play-All
  progress naturally extends mid-run as the finals materialize).

---

## View / form / template spec

### Create form (`tournament_create` + `tournament_create.html`)
- The `<select name="format">` (DOM id `tournament-create-format`) gains a 4th
  option — value **`round_robin_double_elim`**, label **`Round robin →
  Double elimination`** (em-dash arrow U+2192).
- The view's forgiving-fallback parse widens to accept `round_robin_double_elim`
  (absent/tampered → `single_elimination`).
- **NEW `<select name="rrde_combo">`** (DOM id **`tournament-create-rrde-combo`**)
  enumerating the valid `(wb, lb)` shape combos, one option per locked combo —
  **6 options**: value/label pairs (Code agent picks the exact value string
  format, e.g. `"4/0"`):
  - `4/0` (4 WB, 0 LB) · `4/2` · `8/0` · `8/4` · `16/0` · `16/8`.
  The view parses the selected value into `wb_advancers` / `lb_advancers`. Parse
  is forgiving: an absent/invalid `rrde_combo` on an RRDE create falls back to the
  first combo `(4, 0)`; on a non-RRDE create the combo is ignored and both
  advancers persist `0`.
- **Client-side visibility:** `tournament-create-rrde-combo` is shown only when
  the format select reads `round_robin_double_elim` (hidden otherwise) — exactly
  like the four series-length selects are hidden when format is `round_robin`
  (reuse the existing inline `onchange` toggle; extend it to also show/hide the
  combo row). Behaviour pinned; exact JS at Code agent's discretion.
- `Tournament.objects.create(...)` passes `wb_advancers=wb`, `lb_advancers=lb`
  (both `0` for non-RRDE).

### Detail page (`_detail_context` + `tournament_detail.html`)
- **Stage derivation:** `tournament_stage` is a `str` derived (NOT stored):
  - `"setup"` when `tournament.state == "setup"`.
  - `"seeding"` when RRDE, active, and NO finals nodes exist
    (`not nodes.exclude(bracket_type="round_robin").exists()`).
  - `"finals"` when RRDE and finals nodes exist.
  - `"completed"` when `tournament.state == "completed"`.
  (For non-RRDE formats `tournament_stage` may be set to a benign value such as
  the format name or `""`; the badge only renders meaningfully for RRDE — Code
  agent's discretion, tests only assert the RRDE seeding/finals strings.)
- **Cut-line markers:** `cut_labels` is a `dict[int, str]` mapping
  `team_id -> "wb" | "lb" | "out"`, built from `round_robin_standings()`:
  top `wb_advancers` ranked rows → `"wb"`, next `lb_advancers` → `"lb"`, the rest
  → `"out"`. Computed only in the seeding stage of an RRDE tournament (empty `{}`
  otherwise). The standings template applies a per-row class substring keyed on
  the cut: `tournament-standings-cut-wb` / `-lb` / `-out` (each RR standings row
  in the seeding stage carries the matching substring so tests can assert which
  teams are tagged Winners / Losers / Eliminated).
- **Stage badge:** DOM id **`tournament-stage-badge`** renders `tournament_stage`
  (e.g. "Seeding stage" / "Finals stage").
- During seeding, reuse the RR crosstable + standings VERBATIM
  (`tournament-rr-crosstable` / `tournament-rr-standings`), ADD the cut-line
  class to standings rows + the stage badge. Once finals are built, ALSO render
  the DE three-section tree reusing the EXISTING DE rendering
  (`tournament-bracket-winners` / `-losers` / `-grand-final` + per-section round
  columns) — `_build_rounds` already returns the 3-key
  `{"winners", "losers", "grand_final"}` dict, which is populated once finals
  nodes exist.
- **`_build_rounds` / `_build_rr_crosstable` are unchanged** — they already
  handle both the RR-node set (crosstable) and the elim-node set (3-key dict). The
  template branches: for `round_robin_double_elim` it renders the RR crosstable +
  standings (with cut markers + stage badge) AND, when finals nodes exist, the DE
  sections. The existing RR + DE DOM ids are reused verbatim.
- `_detail_context` gains `tournament_stage` + `cut_labels`; its existing keys
  (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`,
  `import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`) stay.

---

## Test boundary

- **`test_bracket.py`** (pure-unit, no DB) — `TestBuildRrDeFinalsBracket`:
  - `lower_specs == []` ⇒ result EQUALS `build_double_elim_bracket(upper_specs)`
    (spec-list equality) for `wb = 4 / 8 / 16`.
  - `lower_specs` non-empty (`lb = wb/2`) for `wb = 4 / 8`: WB re-tagged
    `winners`, LB-R1 slot "a" pre-filled with `lower_specs` in seed order, each
    WB-R1 `loser_advances_to` points at the matching LB-R1 slot "b", GF1/GF2
    wiring + `depth` values, **no `is_bye` nodes anywhere**, LB-round count ==
    `2W - 1`.
  - `TestNoDjangoImportsLeaked` stays green (no new import).
- **`test_tournament_models.py`** — fields exist/default `0`/no choices; the
  `format` enum accepts `round_robin_double_elim`; `lock_and_build` builds ONLY RR
  nodes for an RRDE tournament (no elim nodes at lock) and raises
  `ValidationError` on the two count-fit failures; `build_de_finals_if_rr_finished`
  is a no-op unless all guards hold and is idempotent (second call no-op); after
  all RR nodes resolve it builds the finals from RR standings rank (top `wb` →
  WB seeds, next `lb` → LB pre-seeds, the rest eliminated), seeding seed = 1-based
  RR rank; `_persist_elim_specs` extraction keeps single/double-elim
  `lock_and_build` byte-identical (`TestLockAndBuildSingleElimUnchanged` /
  `...DoubleElimUnchanged` regression). Assert on persisted `BracketNode` rows /
  edges / `bracket_type` / `series_length`, **never on exact simulated point
  totals**.
- **`test_tournament_engine.py`** — `play_next_node` over an RRDE tournament:
  resolving the LAST RR node triggers the deferred finals build (finals nodes now
  exist, tournament stays `active`); subsequent calls drain the DE finals; the
  champion is crowned by the GF crown block (`tournament.champion` set,
  `state="completed"`); an RR (seeding) node never gets an `advance_winner`
  mutation; a DE-stage node DOES advance/drop/reset normally.
- **`test_tournament_views.py`** — create form offers the `round_robin_double_elim`
  option + the `tournament-create-rrde-combo` select with the 6 combos; a POST
  persists `format`, `wb_advancers`, `lb_advancers` from the combo, with forgiving
  fallback; detail page renders `tournament-stage-badge` (seeding vs finals
  strings) and the standings cut-line class substrings
  (`tournament-standings-cut-{wb|lb|out}`) tagging the right teams; reused RR +
  DE DOM ids present.
- **`test_tournament_tasks.py`** — under `CELERY_TASK_ALWAYS_EAGER`,
  `play_tournament_task` drains an RRDE tournament through BOTH stages (seeding
  RR nodes THEN the auto-built DE finals) to a champion + `state="completed"`;
  `stage_progress` reports per-group completion across the RR groups then the
  WB/LB/GF groups.

**INTERNAL (not asserted):** the WB/LB `(bracket_round, position)` numbering
inside the builder (only the cross-bracket wiring coords + `depth` are asserted);
the exact `series_length`-resolution boundary inside `_persist_elim_specs`; the
exact `rrde_combo` value-string format; exact simulated point totals
(non-deterministic). **Never assert on exact simulated point totals.**

---

## Scope-out (LOCKED — DEFERRED, do NOT build)

- **Anti-rematch LB folding** — the LB consumes WB losers via the naive
  same-position drop (inherited from `build_double_elim_bracket`); folding stays
  deferred.
- **Swiss** seeding stage.
- **In-League / in-Season embedding** — the Tournament stays standalone,
  `season`-less.
- **Fully-general `wb`/`lb` counts** — only the 6 locked combos
  (`4/0, 4/2, 8/0, 8/4, 16/0, 16/8`) ship; arbitrary advancer counts deferred.
- **Home/away side alternation** — sides fixed (`team_a` red / `team_b` blue
  every Match), LG-02b locked.
- **Deterministic / master-seed-replayable Series** — `simulate_match` draws
  fresh per-round seeds ⇒ non-deterministic; no SIM-07/08 interaction, **NO Score
  Calibration re-baseline**.
- **Backfill / `RunPython`** — none (pure forward-only schema ops, ADR-0004).
- **New ADR / CONTEXT.md term** — none. ADR-0021 is EXTENDED for the
  deferred-build decision; the **Round robin → double elimination** term is
  already written.
