# LG-02c — Double-elimination tournaments — SEAM CONTRACT

Single source of truth for the 3 parallel build/test/docs agents. Extends the
LG-02a/b single-elimination `BracketNode` tree into **two coupled brackets**
(Winners + Losers) joined by a **Grand final with Bracket reset**, as a new
`Tournament.format` enum value driven by a new pure builder, hosting both
sub-brackets in the *existing* `BracketNode` table. The single-elim path is
**byte-unchanged**. Every name, field, signature, dict key, DOM id, migration
filename, and the test boundary below is **locked** — drift is a failing test,
not a judgement call.

Paths are relative to the nested Django project
`laserforce_simulator/laserforce_simulator/` unless prefixed with `templates/`.
ADR-0021 (`docs/adr/0021-double-elimination-bracket.md`) is the authoritative
design and was **already written** (do NOT re-write it). CONTEXT.md
`### Tournaments` already carries **Winners bracket** / **Losers bracket** /
**Drop** / **Grand final** / **Bracket reset** (added at grilling time — do NOT
re-add or edit them).

---

## 0. Locked decisions (encoded verbatim — do not relitigate)

1. **Format:** add `("double_elimination", "Double elimination")` to
   `Tournament.FORMAT_CHOICES`. Single-elim path **byte-unchanged** —
   `build_bracket`, the existing `advance_winner` / `resolve_bye_chain`
   behaviour for `bracket_type="winners"` single-tree rows, `find_next_node`
   ordering for a one-bracket field, every existing DOM id, and the
   `_node_to_dict` single-elim output are all preserved.
2. **Node schema:** `BracketNode` gains `bracket_type` (CharField, choices
   winners/losers/grand_final, default `"winners"`) + `loser_advances_to`
   (self-FK, nullable, SET_NULL, related_name `"loser_feeders"`) +
   `loser_advances_to_slot` (`"a"`/`"b"`, nullable). The existing
   `uniq_tournament_round_position` UniqueConstraint is **widened to include
   `bracket_type`** and **renamed** to `uniq_tournament_bracket_round_position`.
   Single-elim rows default cleanly (`bracket_type="winners"`, loser ptr NULL).
   Migration is **forward-only, NO `RunPython`, NO backfill** (ADR-0004).
3. **N:** arbitrary `>= 4` with byes. WB = the existing single-elim tree (size
   = next pow2 ≥ N, top `(size − N)` seeds get WB byes). LB consumes WB losers
   via a **naive same-position drop** (loser of WB-round-`r` position `i` drops
   to the matching LB slot by position — **NO anti-rematch folding** this
   slice). A WB **Bye** produces no loser → the LB slot collapses (**Drop bye**)
   via a generalized bye cascade.
4. **Grand final = Bracket reset:** GF1 + GF2 both built at lock. On GF1 clinch:
   if GF1 winner == WB champ → stamp `GF2.winner` = that team (inert,
   bye-style auto-resolved so `find_next_node` never returns it) + stamp
   `tournament.champion` + `state="completed"` immediately. If GF1 winner == LB
   champ → Advance both into GF2 (playable); GF2 winner is champion.
5. **Series escalation:** reuse the 4 existing create-time slots
   (`final` / `semifinal` / `quarterfinal` / `earlier_series_length`). Extract
   the depth→slot dispatch from `series_length_for_round` into a NEW pure
   `series_length_for_depth(depth, *, final, semifinal, quarterfinal, earlier)
   -> int`; `series_length_for_round` **delegates** to it (single-elim
   unchanged). For DE, each node's **depth = its distance to GF1** (GF1/GF2 =
   depth 0, WB-final & LB-final = depth 1, etc.). Stamp
   `BracketNode.series_length` at lock for **every** DE node incl. byes.
6. **Engine:** `tournament_engine.play_next_node` stays **ONE** per-Match-atomic
   loop for both formats; on a node's clinch it Advances the winner AND (for a
   WB or GF1 node) Drops the loser into `loser_advances_to`, then stamps
   champion on a resolved Grand final. `find_next_node` gets a deterministic
   total order across both brackets (readiness already gates correctness; the
   tiebreak ordering is `(bracket_type rank winners<losers<grand_final,
   bracket_round asc, position asc)`).
7. **Pure module additions:** `build_double_elim_bracket(participants) ->
   list[BracketNodeSpec]`; `BracketNodeSpec` gains `bracket_type` +
   `loser_advances_to` (tuple coord or None) + `loser_advances_to_slot`; NEW
   pure `advance_loser` (parallel to `advance_winner`, reads the loser-dest
   fields) — `advance_winner` stays **byte-unchanged**; `resolve_bye_chain`
   generalized to collapse Drop byes; `_node_to_dict` gains `bracket_type` +
   loser-dest keys. Frozen import allowlist
   (`dataclasses` / `typing` / `math` / `collections`) **unchanged** +
   `TestNoDjangoImportsLeaked` stays green.
8. **Views/templates:** create form gains a format `<select>` (DOM id
   `tournament-create-format`); `tournament_create` reads + persists it. Detail
   page renders three sections — Winners / Losers / Grand-final — reusing the
   existing node-card + series-score + Bo-N label markup. DOM ids namespaced by
   `bracket_type` (see §4d). **Single-elim keeps its existing
   `tournament-node-{round}-{position}` id; DE uses a NEW
   `tournament-node-{bracket_type}-{round}-{position}` pattern** (LOCKED — §4d).
9. **Async:** `stage_progress` generalized so play-all progress counts stages
   across both brackets + GF. `play_tournament_task` loops `play_next_node`
   unchanged.
10. **Determinism:** non-deterministic sims (`simulate_match` draws fresh
    per-round seeds) → **NO SIM-07/08 interaction, NO Score Calibration
    re-baseline.**

---

## 1. Models (`matches/models.py`)

Migration: **`matches/migrations/0036_*.py`** (next sequential — latest existing
is `0035_tournament_series_escalation.py`). **No `RunPython`, no `RunSQL`, no
backfill.** Dependency: `("matches", "0035_tournament_series_escalation")`.
Operations in **pinned order**:

1. `AlterField(Tournament, "format", …)` — widen `FORMAT_CHOICES` (the column
   type is unchanged `CharField(max_length=32)`; this AlterField only updates
   `choices`, which Django records but does not enforce at the DB level — it is
   included so `makemigrations --check` is clean).
2. `AddField(BracketNode, "bracket_type", …)`
3. `AddField(BracketNode, "loser_advances_to", …)`
4. `AddField(BracketNode, "loser_advances_to_slot", …)`
5. `RemoveConstraint(BracketNode, "uniq_tournament_round_position")`
6. `AddConstraint(BracketNode, uniq_tournament_bracket_round_position)`

### 1a. `Tournament.FORMAT_CHOICES` — add the DE value

```python
FORMAT_CHOICES = (
    ("single_elimination", "Single elimination"),
    ("double_elimination", "Double elimination"),
)
```

- `format` field declaration otherwise **unchanged**
  (`CharField(max_length=32, choices=FORMAT_CHOICES, default="single_elimination")`).
- The default stays `"single_elimination"`.

### 1b. `BracketNode` — three new fields

```python
# LG-02c — sub-bracket discriminator. Single-elim rows default "winners".
bracket_type = models.CharField(
    max_length=12,
    choices=(
        ("winners", "Winners bracket"),
        ("losers", "Losers bracket"),
        ("grand_final", "Grand final"),
    ),
    default="winners",
)
# LG-02c — Drop pointer: where THIS node's LOSER goes (parallels advances_to /
# advances_to_slot which carry the WINNER). NULL for LB nodes (their loser is
# eliminated) and for GF2. SET_NULL — deleting a node must not cascade.
loser_advances_to = models.ForeignKey(
    "self",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="loser_feeders",
)
loser_advances_to_slot = models.CharField(
    max_length=1,
    null=True,
    blank=True,
    choices=(("a", "team_a"), ("b", "team_b")),
)
```

- **`bracket_type` choices/values LOCKED:** `"winners"` / `"losers"` /
  `"grand_final"` (exact strings — the engine ordering, the view section split,
  and the DOM ids all key on these).
- `loser_advances_to` related_name is **`"loser_feeders"`** (LOCKED; parallels
  the existing winner `advances_to` related_name `"feeders"`).
- Single-elim WB nodes set `loser_advances_to = NULL` (their loser is
  eliminated exactly as today) — single-elim is byte-unchanged.

### 1c. `BracketNode.Meta.constraints` — widen + rename the uniqueness constraint

```python
constraints = [
    models.UniqueConstraint(
        fields=["tournament", "bracket_type", "bracket_round", "position"],
        name="uniq_tournament_bracket_round_position",
    ),
]
```

- The old `uniq_tournament_round_position` (fields
  `["tournament", "bracket_round", "position"]`) is **removed**; the new one
  **adds `bracket_type`** so a WB and LB node may share `(round, position)`.
- `Meta.ordering` stays `["bracket_round", "position"]` (unchanged — the engine
  re-sorts via `find_next_node`'s total order; the view groups by
  `bracket_type`).

### 1d. `Tournament.lock_and_build` — branch on format

`lock_and_build` (`@transaction.atomic`) gains a **single dispatch** at the top
of the build:

- `if self.format == "double_elimination":` build via
  `build_double_elim_bracket([ParticipantSpec(team_id=p.team_id, seed=p.seed)
  for p in participants])`; **else** `build_bracket(...)` (the existing path,
  byte-unchanged).
- The persist loop, `advances_to` wiring pass, `resolve_bye_chain` cascade pass,
  and `series_length` stamping pass are **shared** across both formats with
  these additions for DE specs:
  - persist `bracket_type=spec.bracket_type` on every `BracketNode.objects.create(...)`.
  - a **third wiring pass** (after the existing `advances_to` pass) wires
    `loser_advances_to` self-FKs from `spec.loser_advances_to` (a
    `(bracket_type, bracket_round, position)` triple coord — see §2a) +
    `loser_advances_to_slot = spec.loser_advances_to_slot`. The `node_by_pos`
    map key becomes the **triple** `(bracket_type, bracket_round, position)` for
    DE (single-elim keeps the existing 2-tuple key OR adopts the triple with
    `"winners"` — Code agent's discretion, internal, not asserted).
  - `series_length` stamping uses `series_length_for_depth(spec.depth, …)` for
    DE (the spec carries `depth` directly — §2b) and the existing
    `series_length_for_round(spec.bracket_round, total_rounds, …)` for
    single-elim. **Single-elim stamping is byte-unchanged.**
- The participant-count guard (`>= 4`, `ValidationError`), the `state != "setup"`
  guard, and the `state="active"` flip are **unchanged**.

### 1e. `Tournament.find_next_playable_node` — unchanged signature

`find_next_playable_node()` keeps delegating to `find_next_node` over the
flattened nodes. The **prefetch** widens to include `loser_advances_to` so
`_node_to_dict` reads it without an N+1:
`self.nodes.select_related("advances_to", "loser_advances_to")
.prefetch_related("series_matches")`. The match-back loop keys on
`(bracket_type, bracket_round, position)` (DE) — for single-elim the result's
`bracket_type` is always `"winners"`, so the existing 2-field match still
resolves uniquely; the Code agent adds `bracket_type` to the comparison.

### 1f. `count_series_wins` / `SeriesMatch` — UNCHANGED

`SeriesMatch` (model, `related_name="series_matches"`,
`uniq_seriesmatch_node_game`, `Meta.ordering=["game_number"]`) and
`count_series_wins(series_matches, team_a_id, team_b_id) -> tuple[int, int]` are
**unchanged**.

### 1g. `_node_to_dict` — three new keys

`_node_to_dict(node)` keeps every existing key
(`bracket_round`, `position`, `team_a_id`, `team_b_id`, `seed_a`, `seed_b`,
`is_bye`, `wins_a`, `wins_b`, `series_length`, `winner_id`, `advances_to`,
`advances_to_slot`) and **gains three**:

| new key | value |
|---|---|
| `bracket_type` | `node.bracket_type` (str: `"winners"`/`"losers"`/`"grand_final"`) |
| `loser_advances_to` | `(node.loser_advances_to.bracket_type, node.loser_advances_to.bracket_round, node.loser_advances_to.position)` **triple** or `None` |
| `loser_advances_to_slot` | `node.loser_advances_to_slot` (`"a"`/`"b"`/`None`) |

- The existing `advances_to` key **stays a 2-tuple** `(bracket_round, position)`
  for back-compat with single-elim `advance_winner`. **`loser_advances_to` is a
  3-tuple** including `bracket_type` (the WB→LB Drop crosses brackets, so the
  coord must carry the destination bracket). This asymmetry is LOCKED.
- For single-elim rows the three new keys are
  `("winners", None, None)` → no behaviour change downstream.

---

## 2. Pure module `matches/bracket.py`

**Frozen import allowlist unchanged** (`dataclasses`, `typing`, `math`,
`collections` ONLY — NO Django, NO ORM, NO `random`, NO `datetime`, NO I/O, NO
logging). The new functions add **no new import**.
`matches/tests/test_bracket.py::TestNoDjangoImportsLeaked` must keep passing.

### 2a. `BracketNodeSpec` — three new dataclass fields

```python
@dataclass(frozen=True)
class BracketNodeSpec:
    bracket_round: int
    position: int
    team_a_id: Optional[int]
    team_b_id: Optional[int]
    seed_a: Optional[int]
    seed_b: Optional[int]
    is_bye: bool
    advances_to: Optional[tuple[int, int]]      # (bracket_round, position) | None
    advances_to_slot: Optional[str]
    winner_id: Optional[int]
    # --- LG-02c additions (appended; default values keep single-elim call-sites valid) ---
    bracket_type: str = "winners"
    loser_advances_to: Optional[tuple[str, int, int]] = None  # (bracket_type, round, position)
    loser_advances_to_slot: Optional[str] = None
    depth: Optional[int] = None  # distance to GF1 (DE only); None for single-elim
```

- **The three LG-02c fields are appended WITH DEFAULTS** so `build_bracket`'s
  existing `BracketNodeSpec(...)` construction (which passes the first 10 fields
  positionally/by-keyword) stays valid byte-for-byte. `build_bracket` does NOT
  set the new fields → they default (`"winners"` / `None` / `None` / `None`).
- `depth` is the **distance-to-GF1** carried by DE specs so `lock_and_build`
  stamps `series_length_for_depth(spec.depth, …)` without re-deriving. For
  single-elim it is `None` (the single-elim stamp path uses
  `series_length_for_round`, not `series_length_for_depth`).
- `ParticipantSpec` is **unchanged**.

### 2b. NEW `series_length_for_depth`

```python
def series_length_for_depth(
    depth: int,
    *,
    final: int,
    semifinal: int,
    quarterfinal: int,
    earlier: int,
) -> int:
    """Resolve a best-of-N Series length from a node's depth below the final
    (DE: depth = distance to GF1). depth 0 -> final, 1 -> semifinal,
    2 -> quarterfinal, depth >= 3 -> earlier. Pure integer dispatch; total,
    never raises (the if/elif/elif/else chain makes `earlier` the catch-all)."""
```

- **Signature LOCKED:** `depth` positional; the four slot args keyword-only.
- `series_length_for_round` is **refactored to delegate**:
  `return series_length_for_depth(total_rounds - bracket_round, final=final,
  semifinal=semifinal, quarterfinal=quarterfinal, earlier=earlier)`. Its public
  signature + behaviour are **byte-identical** to today (the single-elim stamp
  path and every existing `test_bracket.py` case stay green).

### 2c. NEW `build_double_elim_bracket`

```python
def build_double_elim_bracket(
    participants: list[ParticipantSpec],
) -> list[BracketNodeSpec]:
    """Build the full two-tree (Winners + Losers + Grand final) node-spec list
    for arbitrary N >= 4 with byes.

    - Winners bracket = the existing single-elim tree (bracket_type="winners",
      size = next pow2 >= N, top (size-N) seeds get WB byes). Reuses build_bracket's
      seeding/pairing/bye logic.
    - Losers bracket (bracket_type="losers") consumes WB-round losers via a NAIVE
      same-position drop (loser of WB-round-r position i -> the matching LB slot by
      position; NO anti-rematch folding). Each WB node's loser_advances_to points
      at its LB destination (bracket_type="losers", ...) + loser_advances_to_slot.
    - Grand final: GF1 (bracket_type="grand_final", the lower bracket_round) takes
      the WB champion (slot "a") + LB champion (slot "b"); GF2 (the higher
      bracket_round) is built but conditionally inert (the Bracket reset). GF1's
      loser_advances_to points at GF2 (so the LB-champ path Advances both into GF2);
      GF2.advances_to is None (final node).
    - Every spec carries bracket_type, loser_advances_to (triple coord or None),
      loser_advances_to_slot, and depth (distance to GF1).

    Returns BracketNodeSpec list. Raises ValueError on len < 4 or duplicate
    seeds/team_ids (mirrors build_bracket)."""
```

- **WB byes produce no loser** → the WB bye node's `loser_advances_to` is set,
  but since the bye node is pre-resolved (winner only, no loser), the LB
  destination slot stays empty and **`resolve_bye_chain` collapses it as a Drop
  bye** (§2e).
- `(bracket_round, position)` numbering within each bracket is the builder's
  internal choice; only the cross-bracket wiring (the `loser_advances_to` /
  `advances_to` coords) and the `depth` values are asserted by tests (§6a). The
  **GF1/GF2 depth is 0**, WB-final & LB-final depth 1, and so on.

### 2d. NEW `advance_loser` (parallel to `advance_winner`)

```python
def advance_loser(
    nodes: list[dict],
    node_position: tuple[str, int, int],
    loser_id: int,
    loser_seed: int,
) -> list[dict]:
    """Given the flattened node dicts and the (bracket_type, bracket_round,
    position) of a node that just resolved, return the parent-slot mutations
    that DROP the loser into that node's loser_advances_to slot.

    Each mutation dict: {"bracket_type", "bracket_round", "position", "slot",
    "team_id", "seed"}. Empty list when loser_advances_to is None (LB nodes,
    GF2, or a single-elim WB node). Pure: reads loser_advances_to /
    loser_advances_to_slot off the resolved node carried in `nodes`."""
```

- **DECISION LOCKED: a SEPARATE `advance_loser`** (NOT a generalization of
  `advance_winner`). Rationale: keeps `advance_winner` byte-unchanged (its
  mutation dicts have **no `bracket_type` key**, preserving single-elim
  behaviour and every existing `test_advance_winner` case); the engine makes two
  explicit calls (`advance_winner` then `advance_loser`) on a WB/GF1 clinch.
- `node_position` is the **triple** `(bracket_type, bracket_round, position)`
  (DE keys cross brackets); the returned mutation dicts carry a `bracket_type`
  key (the LB destination's). `advance_winner`'s `node_position` stays a 2-tuple
  and its mutations stay **without** `bracket_type` — the engine maps a
  winner-mutation's parent within the same bracket using the resolved node's
  `bracket_type` (see §3).

### 2e. `resolve_bye_chain` — generalized to collapse Drop byes

`resolve_bye_chain(nodes)` keeps its **single-elim behaviour byte-identical**
(every existing `test_resolve_bye_chain` case stays green) and gains DE Drop-bye
collapse:

- When a WB node is a **Bye** (produces a winner but **no loser**), the LB slot
  its `loser_advances_to` points at receives no Drop. If that LB slot's only
  feeder is the bye's loser-drop, the LB node has one empty slot whose feeder
  can never fill → it **collapses** (the surviving opponent auto-advances,
  `is_bye=True`, `winner_id` set), exactly as the existing winner-side bye
  cascade collapses an unopposed slot.
- The mutation dicts it returns gain a `bracket_type` key **only for
  loser-drop / LB collapse mutations**; winner-side mutations (single-elim and
  DE WB winner promotion) keep their existing shape **without** `bracket_type`.
  (Equivalently: the Code agent may emit `bracket_type` on every DE mutation and
  omit it on single-elim; the single-elim shape is what the existing tests
  assert.) The exact mutation-dict shape for DE collapse is asserted by §6a.
- The function still returns `[]` when there are no byes.

### 2f. `find_next_node` — total order across both brackets

`find_next_node(nodes)` keeps its **playable predicate byte-identical** (both
slots filled, `not is_bye`, `series_winner_slot(wins_a, wins_b, series_length)
is None`) and changes **only its sort key** from `(bracket_round, position)` to:

```python
_BRACKET_RANK = {"winners": 0, "losers": 1, "grand_final": 2}
playable.sort(key=lambda nd: (
    _BRACKET_RANK.get(nd.get("bracket_type", "winners"), 0),
    nd["bracket_round"],
    nd["position"],
))
```

- For a single-elim field every node is `bracket_type="winners"` (rank 0) → the
  order collapses to `(bracket_round, position)` exactly as today. **Single-elim
  ordering is byte-unchanged.**
- Readiness already gates correctness (a node is only playable once both slots
  are filled); the tiebreak makes the choice **deterministic** when multiple
  nodes across brackets are simultaneously ready.

### 2g. UNCHANGED pure functions

`clinch_threshold`, `series_winner_slot`, `count_series_wins`, `advance_winner`,
`break_tie`, `default_seed_order`, `build_bracket`, and `ParticipantSpec` are
**unchanged**. `stage_progress` is extended (§2h).

### 2h. `stage_progress` — count stages across both brackets + GF

`stage_progress(nodes) -> tuple[int, int]` keeps its single-elim behaviour
byte-identical and generalizes for DE:

- **`total`** = count of distinct `(bracket_type, bracket_round)` groups across
  `nodes` (was `max(bracket_round)`). For a single-elim field every group is
  `("winners", r)` so `total` = number of WB rounds = the old `max(bracket_round)`
  value → **byte-unchanged for single-elim**.
- **`completed`** = count of those groups where **every non-bye, non-inert** node
  has `winner_id is not None`. A group with zero such nodes counts as completed
  (mirrors the existing all-bye-round rule). "inert" = a GF2 node auto-resolved
  by the Bracket reset (it has `winner_id` set but was never played) — it
  already satisfies `winner_id is not None`, so no special-case is needed; the
  existing `winner_id is not None` check covers it.
- Returns `(0, 0)` on empty input.

---

## 3. Engine `matches/tournament_engine.py`

`play_next_node(tournament: Tournament) -> "BracketNode | None"`
(`@transaction.atomic`) stays **ONE per-Match-atomic loop for both formats**.
The body is **otherwise verbatim** through the clinch check; the changes are at
the clinch/advance tail:

- Steps 1–6 (find next playable node, simulate ONE Match, `break_tie` fallback,
  `SeriesMatch.objects.create`, recompute via `count_series_wins`,
  `series_winner_slot(wins_a, wins_b, node.series_length)` clinch check, return
  `node` un-advanced when `slot is None`) are **unchanged**.
- **On clinch** (`slot` is `"a"`/`"b"`), in addition to the existing
  winner-advance:
  1. Stamp `node.winner` + `winner_seed`, `save(update_fields=["winner"])`
     (unchanged).
  2. Build the flat list via
     `_node_to_dict(n) for n in tournament.nodes.select_related("advances_to",
     "loser_advances_to").prefetch_related("series_matches")` (adds
     `"loser_advances_to"` to the existing `select_related`).
  3. **Winner advance** (unchanged shape): `advance_winner(flat,
     (node.bracket_round, node.position), winner_team.id, winner_seed)` → apply
     each mutation to the parent node found by `(bracket_type, bracket_round,
     position)` where `bracket_type == node.bracket_type` (the winner stays in
     the same bracket EXCEPT the GF1→GF2 case, which `advance_winner` handles
     via the GF1 node's own `advances_to` pointing at GF2 with
     `bracket_type="grand_final"` — the winner-mutation parent lookup keys on the
     resolved node's `advances_to` coord, and for cross-bracket safety the Code
     agent resolves the parent by the **node's own advances_to target's
     bracket_type**, read off `flat`). For single-elim the parent is always the
     same `"winners"` bracket → byte-unchanged.
  4. **Loser Drop** (DE only): when `node.bracket_type in ("winners",
     "grand_final")` AND `node.loser_advances_to_id is not None`, compute the
     loser team + seed (the non-winning slot) and call `advance_loser(flat,
     (node.bracket_type, node.bracket_round, node.position), loser_team.id,
     loser_seed)`; apply each mutation to the LB/GF2 parent found by
     `(mut["bracket_type"], mut["bracket_round"], mut["position"])`. For a
     **single-elim WB node** `loser_advances_to_id is None` → `advance_loser` is
     not called (or returns `[]`) → the loser is eliminated exactly as today.
  5. **Grand final resolution** (the Bracket reset): when the clinched node is
     **GF1** (`bracket_type="grand_final"` AND it is the GF1 node — i.e. its
     `advances_to` points at GF2): if the GF1 **winner == the WB champion**
     (the team that occupied GF1 slot "a"), stamp `GF2.winner = winner_team`
     (inert auto-resolve — a `save(update_fields=["winner"])` so `find_next_node`
     never returns GF2), then stamp `tournament.champion = winner_team` +
     `state="completed"`. If the GF1 winner == the LB champion (slot "b"), the
     loser-Drop in step 4 has already Advanced the WB champ into GF2 slot "a"
     and the winner-advance into GF2 slot "b" → GF2 is now playable; do **not**
     stamp champion yet.
  6. **Final node** (single-elim OR GF2): when the clinched node's
     `advances_to_id is None`, stamp `tournament.champion = winner_team` +
     `state="completed"` (unchanged shape; for single-elim this is the WB final,
     for DE this is GF2 OR the GF1-WB-champ-wins short-circuit in step 5).
- Returns `node` on advance, `None` when nothing playable. **Callers
  unchanged:** `tournament_play_next`, `play_tournament_task`,
  `tournament_play_all`, `tournament_play_status` keep their URLs + 5-key status
  JSON.

> **Internal, not asserted:** the exact parent-lookup query strategy
> (`tournament.nodes.get(...)` vs. an in-memory map), the precise local-variable
> names for loser team/seed, and whether the GF1/GF2 discrimination reads
> `advances_to_id` vs. a `bracket_round` comparison. The **state transitions**
> (`node.winner`, the LB/GF2 slot fills, `tournament.champion`, `state`) are what
> tests assert (§6d).

---

## 4. Views / templates

### 4a. Create view (`tournament_create`)

`tournament_create` reads a new POST field **`format`** and persists it. Parse
rule (forgiving-fallback, mirroring the series-length parses): read
`request.POST.get("format")`; accept **only** `"single_elimination"` or
`"double_elimination"`; anything else (absent, tampered) falls back to
`"single_elimination"`. Pass it into the existing
`Tournament.objects.create(name=..., state="setup", format=<parsed>,
final_series_length=..., …)` call (add the `format=` kwarg; everything else
unchanged). The four series-length parses + default Seeding pass are
**unchanged**.

### 4b. Create template (`tournament_create.html`)

Add ONE `<select>` for the format, placed inside `tournament-create-form` (above
the series-length selects). **Locked DOM id + name + options:**

| DOM id | `name` | options (values) |
|---|---|---|
| `tournament-create-format` | `format` | `single_elimination` (selected, label "Single elimination"), `double_elimination` (label "Double elimination") |

- Default selected = `single_elimination`. The existing create-form DOM ids
  (`tournament-create-name`, `-team-select`, `-generate-count`, `-generate-ppt`,
  the four `-*-series-length`, `-submit`, `-no-teams-notice`) are **unchanged**.

### 4c. Detail view (`_build_rounds` / `_detail_context`)

`_build_rounds(tournament)` keeps its existing per-node view-dict keys
(`bracket_round`, `position`, `team_a`, `team_b`, `seed_a`, `seed_b`, `is_bye`,
`wins_a`, `wins_b`, `series_length`, `series_matches`, `winner`) and **gains one
key**:

- `bracket_type: str` ← `node.bracket_type`.

`_build_rounds` **return shape changes** to group by bracket: instead of a flat
`[{bracket_round, nodes}]`, it returns a **3-key dict** keyed by section:

```python
{
    "winners": [{"bracket_round": r, "nodes": [...]}, ...],
    "losers":  [{"bracket_round": r, "nodes": [...]}, ...],
    "grand_final": [{"bracket_round": r, "nodes": [...]}, ...],
}
```

- For a **single-elim** tournament `"losers"` and `"grand_final"` are **empty
  lists** and `"winners"` carries the full tree (so the WB section renders
  exactly the old bracket). **No single-elim render regression** — the WB
  section reuses the existing node-card markup.
- `_detail_context` keeps its frozen LG-02a/LG-02a-2 keys
  (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`,
  `import_form`, `import_row_errors`) — the **value** of `rounds` changes shape
  to the 3-key dict above; **no new top-level context key**.

### 4d. Detail template (`tournament_detail.html`)

Render **three sections** — Winners, Losers, Grand final — each iterating its
slice of `rounds`. **Locked container + node DOM ids:**

| element | DOM id |
|---|---|
| Winners section container | `tournament-bracket-winners` |
| Losers section container | `tournament-bracket-losers` |
| Grand-final section container | `tournament-bracket-grand-final` |
| per-round column (within a section) | `tournament-bracket-{bracket_type}-round-{n}` (e.g. `tournament-bracket-winners-round-1`) |
| **DE** node card | `tournament-node-{bracket_type}-{bracket_round}-{position}` |
| series-score (DE non-bye node) | `tournament-node-series-score-{bracket_type}-{bracket_round}-{position}` |
| series-length Bo-N label (DE non-bye node) | `tournament-node-series-length-{bracket_type}-{bracket_round}-{position}` |

- **Single-elim id preservation (LOCKED):** when `tournament.format ==
  "single_elimination"`, nodes keep the **existing** ids
  `tournament-node-{bracket_round}-{position}`,
  `tournament-node-series-score-{bracket_round}-{position}`,
  `tournament-node-series-length-{bracket_round}-{position}`, and the bracket
  container stays `tournament-bracket` with per-round
  `tournament-bracket-round-{n}`. The template branches on `tournament.format`:
  the single-elim branch renders the legacy ids (every LG-02a/b view test stays
  green); the DE branch renders the `-{bracket_type}-`-namespaced ids above.
  The Losers / Grand-final sections render **only** in the DE branch.
- The existing `tournament-champion-banner`, the per-node winner display, the
  bye-node `bye-node` class, the Play controls (`tournament-lock-form` /
  `-play-next-form` / `-play-all-form` / `-play-all-progress` / `-play-all-error`
  + the inline poll JS), the import form ids, and the seeding form ids are
  **unchanged**.
- A DE Grand-final section renders both GF1 and GF2 cards; the inert
  auto-resolved GF2 (WB-champ-wins-GF1 case) renders with its `winner` shown and
  no playable Series — it carries the `bye-node` class (it auto-resolved without
  a played Series) so the existing "no series-score for bye nodes" template
  branch suppresses its Bo-N label cleanly.

### 4e. Sandbox-nav — unchanged

The `tournaments-nav-link` anchor is unchanged (DE is the same `/tournaments/`
surface).

---

## 5. Admin (`matches/admin.py`)

- `bracket_type`, `loser_advances_to`, `loser_advances_to_slot` **auto-surface**
  in the default `BracketNodeAdmin` change form (editable fields; the FK + the
  `choices` CharFields render with no `fields`/`fieldsets` declaration needed).
- **Do NOT change** `BracketNodeAdmin.list_display`,
  `TournamentAdmin.list_display`, `TournamentParticipantAdmin.list_display`, the
  inlines (`TournamentParticipantInline` / `BracketNodeInline`), or any existing
  registration. The `format` choice widening surfaces automatically in
  `TournamentAdmin`'s change form (it is already an editable `choices` field).

---

## 6. Test boundary

What Tests assert against (the seam) vs. what is internal.

### 6a. `matches/tests/test_bracket.py` (pure-unit, no DB, no Django)

New classes:

- **`TestSeriesLengthForDepth`** — depth boundaries (0→final, 1→semifinal,
  2→quarterfinal, 3/4→earlier), keyword-only enforcement, and a delegation check
  that `series_length_for_round(bracket_round, total_rounds, …) ==
  series_length_for_depth(total_rounds - bracket_round, …)` for representative
  `(bracket_round, total_rounds)` pairs.
- **`TestBuildDoubleElimBracket`** — N=4 / N=8 (power-of-two, no byes) and
  N=5 / N=6 (with WB byes):
  - exactly the expected WB / LB / GF node counts per bracket
    (`bracket_type` partition);
  - every WB non-bye node carries a `loser_advances_to` triple coord whose
    `bracket_type == "losers"` and a `loser_advances_to_slot in ("a","b")`;
  - WB **bye** nodes produce no loser drop that fills an LB slot (the matching
    LB slot is a Drop-bye target — see `TestResolveByeChain` DE cases);
  - GF1 carries `loser_advances_to` pointing at GF2
    (`bracket_type="grand_final"`); GF2 carries `advances_to=None` and
    `loser_advances_to=None`;
  - each spec's `depth` equals its distance-to-GF1 (GF1/GF2 depth 0, WB-final &
    LB-final depth 1, etc.);
  - `ValueError` on `len < 4` and on duplicate seeds/team_ids.
- **`TestAdvanceLoser`** — given a flat node list with a resolved WB node, the
  returned mutation drops the loser into the `loser_advances_to` LB slot
  (mutation dict has keys `bracket_type`/`bracket_round`/`position`/`slot`/
  `team_id`/`seed`); empty list when `loser_advances_to is None` (LB node, GF2,
  single-elim WB node).
- **`TestResolveByeChainDropBye`** — a WB bye whose LB destination has no other
  feeder collapses that LB slot (the surviving LB opponent auto-advances,
  `is_bye=True`, `winner_id` set); the returned mutation carries `bracket_type`.
- **`TestFindNextNodeBracketOrder`** — with simultaneously-ready WB, LB, and GF
  nodes, the `(winners<losers<grand_final, bracket_round asc, position asc)`
  tiebreak picks the lowest; a single-elim-only list (all `winners`) collapses
  to `(bracket_round, position)` order.
- **`TestStageProgressDoubleElim`** — `total` = distinct
  `(bracket_type, bracket_round)` groups; a group of all byes / all-inert counts
  complete; `completed` advances as groups finish.
- **`TestNoDjangoImportsLeaked`** — still green (no new import).
- **Existing** `TestBuildBracket*` / `TestAdvanceWinner` / `TestResolveByeChain`
  (single-elim) / `TestFindNextNode` / `TestSeriesLengthForRound` /
  `TestStageProgress` cases — **unchanged and still green** (single-elim is
  byte-unchanged; `series_length_for_round` delegation is transparent).

### 6b. `matches/tests/test_tournament_models.py` (Django `TestCase`)

- **`TestBracketNodeDoubleElimFields`** — `bracket_type` defaults `"winners"`,
  carries the 3 choices; `loser_advances_to` / `loser_advances_to_slot` default
  NULL; the renamed constraint `uniq_tournament_bracket_round_position` allows a
  WB and LB node to share `(bracket_round, position)` and rejects a duplicate
  within the same `bracket_type`.
- **`TestLockAndBuildDoubleElim`** — a DE tournament (N=4 and N=6) locks to
  `active`, persists WB+LB+GF nodes with correct `bracket_type`,
  `loser_advances_to` self-FKs wired (a WB node's `loser_advances_to` is an LB
  node), GF1's `loser_advances_to` is GF2, and **every** node's
  `series_length` is stamped per **depth** (incl. byes) using a known
  four-field config (e.g. final=5, semifinal=3, quarterfinal=1, earlier=1 →
  GF1/GF2 Bo5, WB/LB-final Bo3, …).
- **`TestLockAndBuildSingleElimUnchanged`** — a single-elim lock still produces
  the all-`winners` tree, `loser_advances_to` NULL on every node,
  `series_length` stamped via the depth-from-final path **byte-identical** to
  LG-02b-2 (regression guard).
- **`Test_node_to_dict` (extended)** — a DE node produces `bracket_type`, a
  3-tuple `loser_advances_to` `(bracket_type, round, position)`, and
  `loser_advances_to_slot`; a single-elim node produces
  `("winners", None, None)` for the three new keys and is otherwise unchanged.

### 6c. `matches/tests/test_tournament_views.py` (Django `TestCase`)

- **`TestCreateFormFormat`** — GET renders the `tournament-create-format`
  select (default `single_elimination` selected); POST `format=double_elimination`
  persists `tournament.format == "double_elimination"`; a tampered/absent
  `format` falls back to `single_elimination`.
- **`TestDetailDoubleElimSections`** — a locked DE tournament renders the three
  containers `tournament-bracket-winners` / `-losers` / `-grand-final`, DE node
  ids `tournament-node-{bracket_type}-{round}-{position}`, and per-non-bye-node
  `tournament-node-series-score-{bracket_type}-{round}-{position}` +
  `tournament-node-series-length-{bracket_type}-{round}-{position}` Bo-N labels.
- **`TestDetailSingleElimIdsUnchanged`** — a single-elim tournament still renders
  the legacy `tournament-bracket`, `tournament-bracket-round-{n}`,
  `tournament-node-{round}-{position}`,
  `tournament-node-series-score-{round}-{position}`, and
  `tournament-node-series-length-{round}-{position}` ids, and does **NOT** render
  the `-losers` / `-grand-final` containers (regression guard).

### 6d. `matches/tests/test_tournament_engine.py` (Django `TestCase`)

- **`TestPlayNextNodeDoubleElimDrop`** — clinching a WB node Advances the winner
  into its WB parent slot AND Drops the loser into its `loser_advances_to` LB
  slot (assert the LB node's filled slot + seed), asserted on the resolved
  tree / `SeriesMatch` rows — **not** on point totals.
- **`TestPlayNextNodeGrandFinalReset`** — two scenarios:
  - **WB champ wins GF1:** `GF2.winner` is stamped inert (GF2 never becomes
    playable — `find_next_playable_node()` returns `None` after GF1),
    `tournament.champion` == WB champ, `state == "completed"`.
  - **LB champ wins GF1:** both teams Advance into GF2 (GF2 playable), no
    champion yet; clinching GF2 stamps `tournament.champion` == GF2 winner +
    `state == "completed"`.
- **`TestPlayNextNodeSingleElimUnchanged`** — a single-elim node clinch advances
  the winner and **does not** call `advance_loser` (loser eliminated); Bo1 / Bo3
  clinch behaviour byte-identical to LG-02b (regression guard).
- The engine reads `node.series_length` (NOT `node.tournament.*`) — preserved
  from LG-02b-2.

### 6e. `matches/tests/test_tournament_tasks.py` (Django `TestCase`, EAGER)

- **`TestPlayTournamentTaskDoubleElim`** — `play_tournament_task` drains a full
  DE tournament (N=4) to a champion (`state == "completed"`,
  `tournament.champion` set), and the final `{"completed", "total"}` reflects
  `stage_progress` over **both brackets + GF** (stage counts, not node counts).
- The single-elim `play_tournament_task` cases (LG-02a-2 / LG-02b) stay green.

### 6f. Blast-radius

- `_build_rounds`'s return-shape change (flat list → 3-key dict) touches every
  test that introspects `rounds`. Enumerated: `test_tournament_views.py`'s
  existing detail-render assertions read DOM ids (not the context shape
  directly), so they survive; any test that asserts on the **context** `rounds`
  shape migrates to the 3-key dict (single-elim: `rounds["winners"]` carries the
  tree, `rounds["losers"] == rounds["grand_final"] == []`).
- `find_next_playable_node`'s widened `select_related`/`prefetch_related` is a
  perf detail — **not** asserted (no query-count assertion).

**Internal (NOT asserted across the seam):** the WB/LB `(bracket_round,
position)` numbering scheme inside `build_double_elim_bracket` (only the
cross-bracket wiring coords + `depth` are pinned); the `node_by_pos` map key
shape in `lock_and_build`; the engine's parent-lookup query strategy; whether
GF1/GF2 are discriminated by `advances_to_id` or `bracket_round`; Bootstrap
class names on the new sections / format select. Tests assert on the pure
functions, the persisted `BracketNode` fields (`bracket_type`,
`loser_advances_to`, `series_length`), `node.winner` / both-bracket Advancement /
`tournament.champion` / `state`, and the DOM ids — **never** on exact simulated
point totals (non-deterministic).

---

## 7. Scope-out (LOCKED — do NOT build)

- **Anti-rematch folding** in the Losers bracket — the LB consumes WB losers via
  a **naive same-position drop** only; folding is deferred to a follow-up.
- **Round robin / RR→double-elim / Swiss** — the `format` enum is extensible but
  only `single_elimination` + `double_elimination` ship.
- **Score Calibration re-baseline** — none (no simulation mechanics change).
- **Any `simulate_match` / `simulate_scheduled_round` change** — consumed
  verbatim, `arena_map=None` 3-zone fallback per Match.
- **No backfill / `RunPython`** — pure forward-only schema migration (ADR-0004).
- **No new CONTEXT.md term** beyond Winners bracket / Losers bracket / Drop /
  Grand final / Bracket reset (already written).
- **No new ADR** — ADR-0021 is already written.
- **In-League / in-Season tournament embedding** — Tournament stays standalone
  (`season`-less).
- **Deterministic / master-seed-replayable Series** — `simulate_match` draws
  fresh per-round seeds; non-deterministic, no SIM-07/08 interaction.
- **A single Grand final (no reset)** — rejected; the Bracket reset (GF1+GF2) is
  the locked design (ADR-0021).
- **A separate `LoserBracketNode` model / second table** — rejected; one table +
  `bracket_type` tag (ADR-0021).
- **Home/away side alternation across a Series** — sides stay fixed (`team_a`
  red, `team_b` blue) every Match (LG-02b locked).
- **`advance_winner` generalization** — `advance_winner` stays byte-unchanged; a
  separate `advance_loser` carries the loser path (locked-decision-7).

---

## 8. Locked-names index (quick reference)

**Models (`matches/models.py`):** `Tournament.FORMAT_CHOICES` gains
`("double_elimination", "Double elimination")`. `BracketNode` gains
`bracket_type` (`CharField(max_length=12)`, choices winners/losers/grand_final,
default `"winners"`) + `loser_advances_to` (self-FK, SET_NULL, related_name
`"loser_feeders"`) + `loser_advances_to_slot` (`CharField(max_length=1)`, choices
a/b, nullable). Constraint renamed `uniq_tournament_round_position` →
`uniq_tournament_bracket_round_position` (fields gain `bracket_type`).
`lock_and_build` branches on `format` → `build_double_elim_bracket` for DE,
wires `loser_advances_to`, stamps `series_length` via `series_length_for_depth`.
`_node_to_dict` gains `bracket_type` + `loser_advances_to` (3-tuple `(bracket_type,
round, position)`) + `loser_advances_to_slot`. `SeriesMatch` / `count_series_wins`
unchanged. Migration `matches/migrations/0036_*.py` (dep
`0035_tournament_series_escalation`; ops `AlterField(Tournament.format)` →
`AddField(BracketNode.bracket_type)` → `AddField(BracketNode.loser_advances_to)`
→ `AddField(BracketNode.loser_advances_to_slot)` →
`RemoveConstraint(uniq_tournament_round_position)` →
`AddConstraint(uniq_tournament_bracket_round_position)`; **no `RunPython`, no
backfill**).

**Pure module (`matches/bracket.py`):** NEW `series_length_for_depth(depth, *,
final, semifinal, quarterfinal, earlier) -> int` (`series_length_for_round`
delegates to it). NEW `build_double_elim_bracket(participants) ->
list[BracketNodeSpec]`. NEW `advance_loser(nodes, node_position=(bracket_type,
bracket_round, position), loser_id, loser_seed) -> list[dict]` (mutations carry a
`bracket_type` key). `BracketNodeSpec` gains `bracket_type="winners"` +
`loser_advances_to=None` (3-tuple) + `loser_advances_to_slot=None` + `depth=None`
(appended WITH defaults). `resolve_bye_chain` generalized to collapse Drop byes.
`find_next_node` sort key → `(bracket_type rank winners<losers<grand_final,
bracket_round asc, position asc)`. `stage_progress` counts distinct
`(bracket_type, bracket_round)` groups. `advance_winner`, `build_bracket`,
`clinch_threshold`, `series_winner_slot`, `count_series_wins`, `break_tie`,
`default_seed_order`, `ParticipantSpec` UNCHANGED. Frozen import allowlist
unchanged; `TestNoDjangoImportsLeaked` green.

**Engine (`matches/tournament_engine.py`):** `play_next_node` stays ONE
per-Match-atomic loop; on a WB/GF1 clinch Advances the winner (`advance_winner`)
AND Drops the loser (`advance_loser`); widens `select_related` to include
`"loser_advances_to"`; on GF1 resolves the Bracket reset (WB-champ-wins →
inert-stamp GF2 + champion + completed; LB-champ-wins → both Advance into GF2);
stamps champion + `state="completed"` on the resolved final (single-elim final OR
GF2). Body otherwise verbatim; callers + status JSON unchanged.

**Create view/template:** POST field `format` (parsed to
`single_elimination`/`double_elimination`, forgiving fallback to
`single_elimination`); select DOM id `tournament-create-format` (default
single-elim). Other create-form ids unchanged.

**Detail view/template:** `_build_rounds` node view-dict gains `bracket_type`;
`_build_rounds` returns a 3-key dict `{"winners", "losers", "grand_final"}`
(single-elim: only `"winners"` non-empty). `_detail_context` frozen keys
unchanged (only `rounds` value-shape changes). DE DOM ids
`tournament-bracket-winners` / `-losers` / `-grand-final`,
`tournament-bracket-{bracket_type}-round-{n}`,
`tournament-node-{bracket_type}-{round}-{position}`,
`tournament-node-series-score-{bracket_type}-{round}-{position}`,
`tournament-node-series-length-{bracket_type}-{round}-{position}`. **Single-elim
keeps the legacy** `tournament-bracket` / `tournament-bracket-round-{n}` /
`tournament-node-{round}-{position}` / `-series-score-{round}-{position}` /
`-series-length-{round}-{position}` ids (template branches on
`tournament.format`). `tournament-champion-banner`, play controls, import + seed
form ids unchanged.

**Admin (`matches/admin.py`):** `bracket_type` / `loser_advances_to` /
`loser_advances_to_slot` auto-surface in `BracketNodeAdmin`; the widened `format`
choices auto-surface in `TournamentAdmin`. **No `list_display` / inline /
registration change.**

**ADR / CONTEXT.md:** ADR-0021 + CONTEXT.md `### Tournaments` (Winners bracket /
Losers bracket / Drop / Grand final / Bracket reset) ALREADY DONE — not
re-touched.

**Test files:** `test_bracket.py` (extend — `series_length_for_depth`,
`build_double_elim_bracket`, `advance_loser`, Drop-bye cascade,
`find_next_node` bracket order, `stage_progress` DE; single-elim cases stay
green), `test_tournament_models.py` (extend — DE fields/constraint, DE
`lock_and_build` incl. depth-stamped byes, single-elim regression,
`_node_to_dict` DE keys), `test_tournament_views.py` (extend — format select +
persist + fallback, DE three-section render, single-elim id regression),
`test_tournament_engine.py` (extend — WB loser Drop, Grand-final Bracket reset
both branches, single-elim regression), `test_tournament_tasks.py` (extend —
`play_tournament_task` drains a DE bracket, stage counts over both brackets).
