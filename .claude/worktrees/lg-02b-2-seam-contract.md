# LG-02b-2 — Per-Bracket-round Series escalation — SEAM CONTRACT

Single source of truth for the 3 parallel build/test/docs agents. Generalises
the LG-02b best-of-N **Series length** from a **single flat per-Tournament
value** applied to every node into a **per-Bracket-round** value anchored to
**depth from the final**. The pure clinch engine (`clinch_threshold`,
`series_winner_slot`, `count_series_wins`, the `SeriesMatch` through-model,
per-Match-atomic `play_next_node`) is **UNCHANGED** — only the *source* of the
`series_length` argument moves from tournament-level to **node-level**. Every
name, field, signature, dict key, DOM id, migration filename, and the test
boundary below is **locked** — drift is a failing test, not a judgement call.

Paths are relative to the nested Django project
`laserforce_simulator/laserforce_simulator/` unless prefixed with `templates/`.
CONTEXT.md `### Tournaments` already carries **Series** / **Series length**
(revised) and **Series escalation** (added at grilling time — do NOT re-add or
edit them). ADR-0020 was extended at grilling time (do NOT re-write it).

---

## 0. Locked decisions (encoded verbatim — do not relitigate)

1. **Series escalation** = the best-of-N **Series length** of a **Bracket
   node** varies by **Bracket round**, anchored to **depth from the final**
   (`depth = total_rounds - bracket_round`): depth 0 = the final, depth 1 =
   semifinal, depth 2 = quarterfinal, depth ≥ 3 = every earlier round.
2. The **resolved N is stamped onto every BracketNode at lock time** — the
   engine and the seam read `node.series_length`, NEVER `node.tournament.*`
   for the per-node Series length any more.
3. The pure clinch math is **untouched**: `clinch_threshold`,
   `series_winner_slot`, `count_series_wins`, `SeriesMatch`, and the
   per-Match-atomic `play_next_node` body all keep their LG-02b behaviour. The
   ONLY change is *where the `series_length` argument comes from*.
4. **No monotonicity constraint** — the four slots may be any of `{1,3,5}` in
   any order (a Bo5 quarterfinal feeding a Bo1 final is permitted; the model
   does not enforce escalation, the user picks four independent values).
5. **Bo1-everywhere (the four fields all `1`, the migration default) is
   byte-equivalent to today's behaviour** — every node stamped `1`,
   `series_winner_slot(1, 0, 1) == "a"`, single-Match clinch.
6. **Pure schema migration, no `RunPython`, no backfill** (ADR-0004
   disposable-sandbox precedent — sandbox tournaments are regenerable). The
   flat `Tournament.series_length` is **dropped wholesale**; no reader of it
   survives.
7. **Non-deterministic** (`simulate_match` draws fresh per-round seeds): **no
   SIM-07 / SIM-08 interaction, NO Score Calibration re-baseline.**

---

## 1. Models (`matches/models.py`)

Migration: **`matches/migrations/0035_*.py`** (next sequential — latest
existing is `0034_tournament_series.py`). **No `RunPython`, no `RunSQL`, no
backfill.** Dependency: `("matches", "0034_tournament_series")`. Operations in
**pinned order** (RemoveField first so the dropped column does not coexist with
the new ones in a half-applied state, then the four `Tournament` AddFields in
slot order, then the `BracketNode` AddField last):

1. `RemoveField(model_name="tournament", name="series_length")`
2. `AddField(Tournament, "final_series_length", …)`
3. `AddField(Tournament, "semifinal_series_length", …)`
4. `AddField(Tournament, "quarterfinal_series_length", …)`
5. `AddField(Tournament, "earlier_series_length", …)`
6. `AddField(BracketNode, "series_length", …)`

### 1a. `Tournament` — DROP the flat field, ADD four slot fields

**DROP** the LG-02b `Tournament.series_length` field entirely (no alias, no
property shim — every reader moves to `node.series_length`, see §3/§4).

**ADD** four fields, each declared identically apart from the name. Field
declaration (the `choices` + `default` are locked verbatim):

```python
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
```

- Set at create-time only; **immutable once the Tournament leaves `setup`**
  (the resolved N is frozen onto each `BracketNode.series_length` at
  `lock_and_build` time — the four `Tournament` fields are never re-read after
  lock).
- `default=1` (Bo1) on all four — locked-decision-5 byte-equivalence floor.
- Only odd choices (`1`/`3`/`5`) ship.
- **Field order on the model** (locked): the four fields are declared in the
  block where `series_length` used to live, in the order `final_series_length`,
  `semifinal_series_length`, `quarterfinal_series_length`,
  `earlier_series_length`.

### 1b. `BracketNode.series_length` (AddField on the existing `BracketNode`)

```python
series_length = models.PositiveSmallIntegerField(default=1)
```

- The **resolved** best-of-N for this node, stamped at lock time by
  `lock_and_build` via `series_length_for_round(...)` (see §1c). `default=1` so
  a row created before stamping (or a bye node) reads Bo1.
- No `choices` on the node field (the four `Tournament` fields own the
  validation; the node carries the already-resolved int — which is always one
  of `1`/`3`/`5` but is not choice-constrained, mirroring how `seed_a`/`seed_b`
  carry resolved ints without choices).
- Stamped on **every** persisted node, including bye nodes (inert — the engine
  skips `is_bye`).

### 1c. `lock_and_build` — stamp `node.series_length` at lock time

In `Tournament.lock_and_build` (`@transaction.atomic`), after
`build_bracket(...)` produces `specs`:

- Compute `total_rounds = max(spec.bracket_round for spec in specs)`.
- In the **existing** `BracketNode.objects.create(...)` loop (both
  `spec.bracket_round` and `total_rounds` are known there — no follow-up pass
  needed), add the kwarg
  `series_length=series_length_for_round(spec.bracket_round, total_rounds,
  final=self.final_series_length, semifinal=self.semifinal_series_length,
  quarterfinal=self.quarterfinal_series_length,
  earlier=self.earlier_series_length)`.
- This stamps EVERY persisted node (incl. byes). Import
  `series_length_for_round` alongside the existing
  `from .bracket import build_bracket, resolve_bye_chain, ParticipantSpec`
  block.
- The `resolve_bye_chain` cascade pass and the `advances_to` wiring pass are
  **unchanged** (they don't touch `series_length`).

### 1d. `count_series_wins` / `SeriesMatch` — UNCHANGED

`SeriesMatch` (model, `related_name="series_matches"`, constraint
`uniq_seriesmatch_node_game`, `Meta.ordering=["game_number"]`) and the
`count_series_wins(series_matches, team_a_id, team_b_id) -> tuple[int, int]`
free function are **unchanged**. `wins_a`/`wins_b` derivation is untouched.

### 1e. `_node_to_dict` — read `node.series_length` (seam read-source swap)

`_node_to_dict(node)` keeps every key it has today; the **only change** is the
source of the `"series_length"` value:

| key | OLD source (LG-02b) | NEW source (LG-02b-2) |
|---|---|---|
| `series_length` | `node.tournament.series_length` | `node.series_length` |

- `wins_a` / `wins_b` (via `count_series_wins(node.series_matches.all(), …)`)
  are **unchanged**.
- `advances_to` still reads `node.advances_to` (the self-FK) — keep correct.
- **`node.tournament` is no longer read by `_node_to_dict` for series_length.**
  Confirm `_node_to_dict` has **no other** `node.tournament` access (it does
  not, post-swap — `advances_to` reads the self-FK, not the tournament). This
  is what licenses dropping `select_related("tournament")` in §3.

---

## 2. Pure module `matches/bracket.py`

**Frozen import allowlist unchanged** (`dataclasses`, `typing`, `math`,
`collections` ONLY — NO Django, NO ORM, NO `random`, NO `datetime`, NO I/O, NO
logging). The new function adds **no new import**.
`matches/tests/test_bracket.py::TestNoDjangoImportsLeaked` must keep passing.

### 2a. NEW `series_length_for_round`

```python
def series_length_for_round(
    bracket_round: int,
    total_rounds: int,
    *,
    final: int,
    semifinal: int,
    quarterfinal: int,
    earlier: int,
) -> int:
    """Resolve a Bracket node's best-of-N Series length from its depth below
    the final.

    depth = total_rounds - bracket_round. depth 0 -> final, 1 -> semifinal,
    2 -> quarterfinal, depth >= 3 -> earlier. Pure integer dispatch; no
    validation of the four slot values (callers pass the locked 1/3/5 choices).
    """
```

- **Signature is locked**: `bracket_round` and `total_rounds` are positional;
  the four slot args (`final`, `semifinal`, `quarterfinal`, `earlier`) are
  **keyword-only** (after `*`).
- Algorithm (locked): `depth = total_rounds - bracket_round`; `depth == 0` →
  `final`; `depth == 1` → `semifinal`; `depth == 2` → `quarterfinal`;
  `depth >= 3` → `earlier`. Pure, total, never raises on any int input
  (negative `depth` cannot occur given `bracket_round <= total_rounds`, but is
  not guarded — falls through nothing; the chain is `if/elif/elif/else` with
  `earlier` as the `else`, so any `depth >= 3` OR a defensive out-of-range
  value resolves to `earlier`).

### 2b. UNCHANGED pure functions

`clinch_threshold(series_length) -> int`, `series_winner_slot(wins_a, wins_b,
series_length) -> Optional[str]`, `find_next_node`, `build_bracket`,
`advance_winner`, `resolve_bye_chain`, `break_tie`, `stage_progress`,
`default_seed_order`, and the two dataclasses (`BracketNodeSpec`,
`ParticipantSpec`) — **all unchanged**. In particular `find_next_node` keeps
reading the `series_length` dict key (now sourced from `node.series_length` via
`_node_to_dict`, §1e) — no edit to `find_next_node` itself.

---

## 3. Engine `matches/tournament_engine.py` — read `node.series_length`

`play_next_node(tournament: Tournament) -> "BracketNode | None"`
(`@transaction.atomic`) — **body otherwise verbatim**. The ONLY change is the
clinch-check line:

| line | OLD (LG-02b) | NEW (LG-02b-2) |
|---|---|---|
| step 6 clinch check | `series_winner_slot(wins_a, wins_b, node.tournament.series_length)` | `series_winner_slot(wins_a, wins_b, node.series_length)` |

- Every other step (find next playable node, simulate ONE Match, `break_tie`
  fallback, `SeriesMatch.objects.create(...)`, recompute via
  `count_series_wins`, stamp `node.winner` + `save(update_fields=["winner"])`,
  `advance_winner` parent mutations, champion/`state="completed"` on the final)
  is **unchanged**.
- **`select_related("tournament")` may be dropped** from the two ORM queries:
  - the flat-list build in `play_next_node` (currently
    `tournament.nodes.select_related("advances_to", "tournament")
    .prefetch_related("series_matches")`) → drop `"tournament"`, keep
    `"advances_to"` + the `series_matches` prefetch.
  - `Tournament.find_next_playable_node` (currently
    `self.nodes.select_related("advances_to", "tournament")
    .prefetch_related("series_matches")`) → drop `"tournament"`.
  - **Verified safe**: post-§1e, `_node_to_dict` no longer touches
    `node.tournament` (it reads `node.series_length` directly), and no other
    code in the flatten/advance path reads `node.tournament`. The Code agent
    MUST confirm this is still true at implement time before dropping; if any
    residual `node.tournament` reader is found, keep `select_related`. Dropping
    it is a perf nicety, not load-bearing — the test boundary does not assert
    query counts, so leaving `select_related("tournament")` in place is also
    acceptable (no test fails either way). Recommended: drop it.

---

## 4. Views / templates

### 4a. Create view (`tournament_create`)

Replace the single `series_length` POST parse with **four** parses. Each is
read from POST, coerced to int with a forgiving fallback to `1` on
`TypeError`/`ValueError`, then forced into `{1,3,5}` (anything else → `1`) —
the LG-02b precedent, applied four times. **No monotonicity constraint.**

POST field names (locked): `final_series_length`, `semifinal_series_length`,
`quarterfinal_series_length`, `earlier_series_length`.

The old single `series_length` POST parse + the
`Tournament.objects.create(..., series_length=series_length)` kwarg are
**removed**; the create call passes all four:
`Tournament.objects.create(name=name, state="setup",
final_series_length=final_series_length,
semifinal_series_length=semifinal_series_length,
quarterfinal_series_length=quarterfinal_series_length,
earlier_series_length=earlier_series_length)`.

### 4b. Create template (`tournament_create.html`)

Replace the single `<select id="tournament-create-series-length">` block with
**four** `<select>` fields (each Bo1/Bo3/Bo5, **Bo1 selected by default**),
placed inside `tournament-create-form` before the submit button. **Locked DOM
ids + name attributes**:

| DOM id | `name` | label |
|---|---|---|
| `tournament-create-final-series-length` | `final_series_length` | Final series length |
| `tournament-create-semifinal-series-length` | `semifinal_series_length` | Semifinal series length |
| `tournament-create-quarterfinal-series-length` | `quarterfinal_series_length` | Quarterfinal series length |
| `tournament-create-earlier-series-length` | `earlier_series_length` | Earlier rounds series length |

The old `tournament-create-series-length` id is **removed** (no element carries
it post-LG-02b-2).

### 4c. Detail view (`_build_rounds` / `_detail_context`)

`_build_rounds(tournament)` keeps its existing per-node view-dict keys
(`bracket_round`, `position`, `team_a`, `team_b`, `seed_a`, `seed_b`, `is_bye`,
`wins_a`, `wins_b`, `series_matches`, `winner`) and **gains one key**:

- `series_length: int` ← `node.series_length` (read straight off the node row;
  the node loop already iterates `tournament.nodes...`).

`_detail_context` keeps its frozen LG-02a/LG-02a-2 keys verbatim (`tournament`,
`participants`, `rounds`, `next_node`, `is_locked`, `can_play`, `import_form`,
`import_row_errors`) — **no new top-level context key**. The per-node Bo-N
label reads `node.series_length` off each `rounds[*].nodes[*]` dict.

### 4d. Detail template (`tournament_detail.html`)

For each **non-bye** node, render a Bo-N label beside the existing
`tournament-node-series-score-{bracket_round}-{position}` element, with locked
DOM id **`tournament-node-series-length-{bracket_round}-{position}`**. **Label
text shape (locked): `Bo{n}`** — e.g. `Bo1`, `Bo3`, `Bo5` (rendered as
`Bo{{ node.series_length }}`). The label is rendered only for non-bye nodes
(bye nodes have no Series; they stay in the existing `{% if node.is_bye %}`
branch and get no series-length label). The existing
`tournament-node-series-score-{br}-{pos}` element (`{{ node.wins_a }}–{{
node.wins_b }}`) and the champion banner (`tournament-champion-banner`) are
**unchanged**.

---

## 5. Admin (`matches/admin.py`)

The four new `Tournament` fields (`final_series_length`,
`semifinal_series_length`, `quarterfinal_series_length`,
`earlier_series_length`) **auto-surface in the default `TournamentAdmin` change
form** — they are editable `PositiveSmallIntegerField`s with `choices`, so
Django's default ModelAdmin renders them as `<select>`s with no `fields` /
`fieldsets` declaration needed.

- **Do NOT add them to `TournamentAdmin.list_display`** — the existing
  `list_display = ("name", "format", "state", "champion", "created_at")` stays
  verbatim (four extra columns would clutter the changelist for no value;
  recommend leaving `list_display` alone).
- **`BracketNode.series_length` auto-surfaces** in the default `BracketNodeAdmin`
  change form too; do NOT add it to `BracketNodeAdmin.list_display` either
  (the existing list_display tuple is unchanged).
- No other admin change. `TournamentParticipantAdmin` / `LeagueAdmin` /
  `SeasonAdmin` are untouched.

---

## 6. Test boundary

What Tests assert against (the seam) vs. what is internal:

### 6a. `matches/tests/test_bracket.py` (pure-unit, no DB, no Django)

- NEW `series_length_for_round` cases (the existing flat-dict helpers
  `_node_dict` / clinch helpers stay):
  - **Depth boundaries**: depth 0 → `final`, depth 1 → `semifinal`, depth 2 →
    `quarterfinal`, depth 3 / 4 → `earlier` (the depth-≥3 fall-through).
  - **Worked cases** (`bracket_round` swept across a full bracket):
    - `total_rounds = 2` (N=4): round 2 = final (depth 0), round 1 =
      semifinal (depth 1). With `earlier`/`quarterfinal` distinct from
      `final`/`semifinal`, assert each round resolves to the right slot.
    - `total_rounds = 3` (N=8): round 3 final, round 2 semifinal, round 1
      quarterfinal (depth 2).
    - `total_rounds = 4` (N=16): round 4 final, round 3 semifinal, round 2
      quarterfinal, round 1 = `earlier` (depth 3).
  - **Keyword-only enforcement**: the four slot args must be passed by keyword
    (a positional call past `total_rounds` is a `TypeError` — optional to
    assert, but the signature pins it).
- `TestNoDjangoImportsLeaked` — still green (the new function adds no import).
- `clinch_threshold` / `series_winner_slot` / `find_next_node` existing cases —
  unchanged and still green.

### 6b. `matches/tests/test_tournament_models.py` (Django `TestCase`)

- `lock_and_build` stamps `node.series_length` **per depth** for a known
  four-field configuration (e.g. final=5, semifinal=3, quarterfinal=1,
  earlier=1 on an N=8 bracket → round-3 nodes Bo5, round-2 nodes Bo3, round-1
  nodes Bo1) — assert the stamped `BracketNode.series_length` for representative
  nodes at each depth, **including bye nodes** (a bye node still gets a
  depth-resolved value even though it is inert).
- The four new `Tournament` fields **exist, default to `1`, and carry the
  `(1,"Best of 1"),(3,"Best of 3"),(5,"Best of 5")` choices**.
- `BracketNode.series_length` **exists and defaults to `1`**.
- The old `Tournament.series_length` field **is gone** (e.g. accessing it
  raises / the model has no such field — assert via `Tournament._meta` field
  introspection or a `FieldDoesNotExist` / `AttributeError` on a fresh
  instance).
- `_node_to_dict` derived keys: a node with stamped `series_length` produces
  `d["series_length"] == node.series_length` (NOT
  `node.tournament.*`) and the correct `wins_a`/`wins_b`.

### 6c. `matches/tests/test_tournament_views.py` (Django `TestCase`)

- Create form GET renders **all four** selects by DOM id
  (`tournament-create-final-series-length`,
  `tournament-create-semifinal-series-length`,
  `tournament-create-quarterfinal-series-length`,
  `tournament-create-earlier-series-length`), each defaulting to Bo1 selected;
  the old `tournament-create-series-length` id is **absent**.
- POST with the four fields persists all four on the `Tournament`
  (`final_series_length`/`semifinal_series_length`/`quarterfinal_series_length`/
  `earlier_series_length`).
- Forgiving fallback: a tampered value (`"4"`, `"abc"`, missing) on any of the
  four falls back to `1` independently.
- Detail page: a locked/active tournament renders the per-non-bye-node Bo-N
  label by DOM id `tournament-node-series-length-{br}-{pos}` with text
  `Bo{n}` matching the stamped `node.series_length`; bye nodes have no such
  label. The existing series-score element + champion banner still render.

### 6d. `matches/tests/test_tournament_engine.py` (Django `TestCase`)

- A node stamped Bo3 (`node.series_length == 3`) reads `node.series_length` and
  clinches at 2 Match wins (advance only on clinch, not before) — asserted on
  the resolved tree / `SeriesMatch` rows, not on point totals.
- A node stamped Bo1 (`node.series_length == 1`) clinches on the first Match
  (LG-02b Bo1-equivalence preserved).
- The engine reads `node.series_length` (NOT `node.tournament.*`) — a node
  whose stamped value differs from any tournament-level value clinches per the
  **node** value (this is the load-bearing read-source assertion; construct a
  tournament whose four fields differ across depths and verify a deep node
  clinches at its own depth's N).

### 6e. Blast-radius — existing LG-02b tests that reference the flat field

Every reference to `Tournament.series_length` (the dropped flat field) must
migrate to the four-field + node-field shape. Enumerated (from a grep of
`matches/tests/`):

- **`test_tournament_engine.py`** — `_series_tournament(n, series_length, …)`
  helper (`t.series_length = …`, `t.save(update_fields=["series_length"])`) →
  rewrite to set the four `*_series_length` fields (or pass them into
  `Tournament.objects.create`), then `lock_and_build()` stamps the nodes. The
  helper's `series_length` parameter becomes "the N to apply to all four slots"
  (simplest migration) OR the helper takes four args — Code/Tests agree the
  helper sets all four slots to the single passed value so the existing per-N
  assertions (`len(series) <= t.<slot>_series_length`) read the node's stamped
  value instead (`<= node.series_length`).
- **`test_tournament_tasks.py`** — `_active_series_tournament(n, series_length,
  …)` (`Tournament.objects.create(name=name, series_length=series_length)` +
  `assert len(series) <= t.series_length`) → create with the four fields set to
  the single value; assert against `node.series_length`.
- **`test_tournament_models.py`** — `TestSeriesLengthField` (default + 1/3/5
  choices + lock immutability) and `Test…NodeToDict` (`_node_to_dict` carries
  `series_length`) → rewrite to the four `Tournament` fields + the
  `BracketNode.series_length` field; `_node_to_dict` now reads
  `node.series_length`.
- **`test_tournament_views.py`** — `TestTournamentCreateSeriesLength` (the five
  POST tests reading/writing `series_length`) and
  `TestTournamentDetailSeriesScore` (`t.series_length = 3; t.save(...)`) →
  rewrite to the four POST fields + four model fields; the Bo3 detail helper
  sets the four fields (e.g. all to 3, or final/semifinal=3) then locks.
- **`test_bracket.py`** — the pure-unit `_node_dict(..., series_length=…)`
  helper and the `series_length` dict key it builds are **unchanged** (the seam
  dict key name `series_length` is preserved; only its ORM *source* moved,
  which the pure module never sees). These tests stay green as-is; the only
  ADDITIONS are the `series_length_for_round` cases (§6a).

**Internal (NOT asserted across the seam):** the exact `select_related`
strategy in `play_next_node` / `find_next_playable_node` (dropping
`"tournament"` is a nicety, not pinned — no query-count assertion); whether
`lock_and_build` stamps inline in the create loop vs a follow-up pass (only the
resulting stamped `node.series_length` values are pinned); the Bootstrap class
names on the four create-form selects and the detail Bo-N label (only the DOM
ids + label text shape `Bo{n}` are pinned). Tests assert on the pure function,
the stamped `BracketNode.series_length`, the four `Tournament` fields, the
absence of the old field, the DOM ids, and `node.winner`/advancement — **never**
on exact simulated point totals (non-deterministic, locked-decision-7).

---

## 7. Scope-out (LOCKED — do NOT build)

- **Per-node arbitrary override UI** (picking N node-by-node) — escalation is
  anchored to depth-from-final via four slots only.
- **Monotonicity enforcement** — none; the four slots are independent
  (locked-decision-4).
- **Home/away (side) alternation across the Series** — sides stay fixed
  (`team_a` red, `team_b` blue) every Match (LG-02b locked).
- **Any change to the clinch engine** — `clinch_threshold`,
  `series_winner_slot`, `count_series_wins`, `SeriesMatch`, per-Match-atomic
  `play_next_node` body are all consumed verbatim (locked-decision-3).
- **Any `simulate_match` / `simulate_scheduled_round` change** — consumed
  verbatim (`match_type="tournament"`, `arena_map=None` 3-zone fallback).
- **Deterministic / master-seed-replayable Series** — `simulate_match` draws
  fresh per-round seeds; non-deterministic (locked-decision-7).
- **Backfill / `RunPython`** — none (ADR-0004; pure RemoveField + AddFields).
- **Score Calibration re-baseline** — none (no simulation mechanics change).
- **New CONTEXT.md term / new ADR** — none; **Series length** (revised) +
  **Series escalation** are already in CONTEXT.md and ADR-0020 was already
  extended at grilling time. The Docs agent does NOT touch CONTEXT.md /
  ADR-0020 again — it only marks PLAN.md LG-02b-2 complete.
- **A fifth depth tier** (e.g. a distinct "round of 16" slot) — depth ≥ 3 all
  collapse to `earlier`.

---

## 8. Locked-names index (quick reference)

**Models (`matches/models.py`):** **DROP** `Tournament.series_length`. **ADD**
`Tournament.final_series_length` / `Tournament.semifinal_series_length` /
`Tournament.quarterfinal_series_length` / `Tournament.earlier_series_length`
(each `PositiveSmallIntegerField`, choices `1`/`3`/`5`, default `1`). **ADD**
`BracketNode.series_length` (`PositiveSmallIntegerField`, default `1`,
no choices). `lock_and_build` stamps every node's `series_length` via
`series_length_for_round(...)` using `total_rounds = max(spec.bracket_round …)`.
`count_series_wins` + `SeriesMatch` + `_node_to_dict` keys **unchanged in
shape**; `_node_to_dict["series_length"]` now reads `node.series_length`.
Migration `matches/migrations/0035_*.py` (dep `0034_tournament_series`; ops
`RemoveField(Tournament.series_length)` → 4× `AddField(Tournament.*)` →
`AddField(BracketNode.series_length)`; **no `RunPython`, no backfill**).

**Pure module (`matches/bracket.py`):** NEW
`series_length_for_round(bracket_round, total_rounds, *, final, semifinal,
quarterfinal, earlier) -> int` (depth = `total_rounds - bracket_round`; 0→final,
1→semifinal, 2→quarterfinal, ≥3→earlier; four slot args keyword-only). Frozen
import allowlist unchanged. All other pure functions UNCHANGED.

**Engine (`matches/tournament_engine.py`):** `play_next_node` clinch check reads
`series_winner_slot(wins_a, wins_b, node.series_length)` (was
`node.tournament.series_length`); body otherwise verbatim. `select_related(
"tournament")` droppable in `play_next_node` + `find_next_playable_node`
(verify no residual `node.tournament` reader; nicety, not pinned).

**Create view/template:** four POST fields `final_series_length` /
`semifinal_series_length` / `quarterfinal_series_length` /
`earlier_series_length` (each int-coerced, forced into `{1,3,5}`, forgiving
fallback to `1`, no monotonicity); four selects DOM ids
`tournament-create-final-series-length` /
`tournament-create-semifinal-series-length` /
`tournament-create-quarterfinal-series-length` /
`tournament-create-earlier-series-length` (Bo1 default selected); old
`tournament-create-series-length` id removed.

**Detail view/template:** `_build_rounds` node view-dict gains
`series_length` (from `node.series_length`); per-non-bye-node Bo-N label DOM id
`tournament-node-series-length-{bracket_round}-{position}`, text `Bo{n}`
(`Bo{{ node.series_length }}`). `_detail_context` frozen keys + the existing
`tournament-node-series-score-{br}-{pos}` element + `tournament-champion-banner`
unchanged.

**Admin (`matches/admin.py`):** four new `Tournament` fields +
`BracketNode.series_length` auto-surface in the default change forms; **no
`list_display` change** on `TournamentAdmin` / `BracketNodeAdmin`.

**ADR / CONTEXT.md:** ADR-0020 (extended) + CONTEXT.md **Series length**
(revised) / **Series escalation** (added) ALREADY DONE — not re-touched.

**Test files:** `test_bracket.py` (extend — `series_length_for_round` depth
boundaries + N=4/8/16 worked cases + purity), `test_tournament_models.py`
(extend/migrate — `lock_and_build` stamps per depth incl. byes, four new fields
+ node field exist/default/choices, old field gone, `_node_to_dict` reads
node), `test_tournament_views.py` (extend/migrate — four selects + POST persist
+ fallback + detail Bo-N label), `test_tournament_engine.py` (extend/migrate —
node reads its own `series_length`, Bo3 clinch at 2, Bo1 unchanged),
`test_tournament_tasks.py` (migrate the `_active_series_tournament` helper to
the four-field shape).
