# LG-02b — Best-of-N series bracket nodes — SEAM CONTRACT

Single source of truth for the 3 parallel build/test/docs agents. Generalises a
**Bracket node** from holding **one** 2-round `Match` to holding a best-of-N
**Series** of Matches. The node resolves when one Team clinches the majority of
Match wins, then Advances. Builds directly on LG-02a (the shipped single-elim
Tournament) and LG-02a-2 (the per-Match engine + async play-all). Every name,
field, signature, dict key, DOM id, and the test boundary below is **locked** —
drift is a failing test, not a judgement call.

Paths are relative to the nested Django project
`laserforce_simulator/laserforce_simulator/` unless prefixed with `templates/`.
CONTEXT.md is at the **repo root** and already carries the **Series** /
**Series length** terms (added at grilling time — do NOT re-add or edit them).

---

## 0. Locked decisions (encoded verbatim — do not relitigate)

1. **Series** = best-of-N **Matches**, counted in **Matches** (never Rounds /
   "games"). Each Match is still the existing 2-round `Match`.
2. **Clinch threshold** = `(series_length // 2) + 1` Match wins. The Series stops
   the moment a Team clinches — **no dead-rubber Matches** are simulated.
3. Tie-break is **per-Match** (`break_tie` reused unchanged); odd N ⇒ no
   Series-level tie is ever possible.
4. **Bo1 (default, `series_length == 1`) MUST be byte-equivalent to today's
   LG-02a behaviour** — one Match, clinch threshold 1, identical advancement.
5. The win tally is **derived** from `SeriesMatch.winner` rows (count per
   team-slot), **never stored** as counters. `node.winner` is stamped only on
   clinch.
6. Play is **one Match per step**, **per-Match-atomic** (extends ADR-0016).
   `play_next_node` resolves the next *undecided Match of the next playable
   node* — not the whole node in one call.
7. Sides are fixed across the Series: every Match is
   `simulate_match(node.team_a, node.team_b, match_type="tournament")` —
   `team_a`/`team_b` argument order is constant for every Match of the Series
   (no home/away alternation).
8. **Non-deterministic** (LG-02a-2 precedent): `simulate_match` draws fresh
   per-round seeds, so a Series is NOT master-seed-replayable. **No SIM-07 /
   SIM-08 interaction, NO Score Calibration re-baseline.**
9. New **ADR-0020** "Best-of-N series bracket nodes" (Docs agent writes it;
   cross-ref ADR-0019 (tournament bracket model) + ADR-0016 (per-step atomic
   play job)).

---

## 1. Models (`matches/models.py`)

Migration: **`matches/migrations/0034_<name>.py`** (next sequential — latest
existing is `0033_tournament.py`). Operations in **pinned order**:
`AddField(Tournament, series_length)` → `CreateModel(SeriesMatch)` →
`RemoveField(BracketNode, match)`. **No `RunPython`, no backfill** (ADR-0004
disposable-sandbox-data precedent — sandbox tournaments are regenerable).
Dependency: the latest `matches` migration at branch-cut (`0033_tournament`) +
the latest `teams` migration.

### 1a. `Tournament.series_length` (AddField on the existing `Tournament`)

```python
series_length = models.PositiveSmallIntegerField(
    choices=((1, "Best of 1"), (3, "Best of 3"), (5, "Best of 5")),
    default=1,
)
```

- Set at create-time only; **immutable once the Tournament leaves `setup`**
  (locked by `lock_and_build`'s existing `state != "setup"` guard — the bracket
  is built and the value is frozen on the setup→active transition; no view ever
  rewrites `series_length` on a non-`setup` Tournament).
- `default=1` (Bo1) — locked-decision-4 byte-equivalence floor.
- Only odd choices (`1`/`3`/`5`) ship — even N is never a valid Series length.

### 1b. `SeriesMatch` (new model, appended after `BracketNode`)

```python
class SeriesMatch(models.Model):
    """LG-02b — one Match within a Bracket node's best-of-N Series.

    The node's win tally is DERIVED by counting these rows' ``winner`` per
    team-slot; counters are never stored. ``game_number`` is 1-based and
    sequential within a node. ``winner`` is the per-Match tie-broken decisive
    Team (never NULL once the Match has been resolved).
    """

    node = models.ForeignKey(
        "matches.BracketNode",
        on_delete=models.CASCADE,
        related_name="series_matches",
    )
    match = models.ForeignKey(
        "matches.Match",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="series_match",
    )
    # 1-based position within the node's Series (1, 2, 3, ...).
    game_number = models.PositiveIntegerField()
    # The tie-broken decisive winner of THIS Match. SET_NULL on Team delete.
    winner = models.ForeignKey(
        "teams.Team",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )

    class Meta:
        ordering = ["game_number"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "game_number"],
                name="uniq_seriesmatch_node_game",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.node} game {self.game_number}"
```

**Field justification.** `node` CASCADE (`related_name="series_matches"`) —
deleting a node drops its Series; `match` SET_NULL/nullable mirrors the old
`BracketNode.match` semantics (deleting a Match must not cascade-delete the
Series row); `game_number` 1-based + `UniqueConstraint(node, game_number)`
(name `uniq_seriesmatch_node_game`) pins one row per (node, game);
`Meta.ordering=["game_number"]` so `node.series_matches.all()` iterates in play
order; `winner` SET_NULL = the per-Match decisive Team (the `break_tie` result
when `Match.winner is None`, else `Match.winner`).

### 1c. `RemoveField(BracketNode.match)`

The LG-02a `BracketNode.match` FK (`matches.Match`, `related_name="bracket_node"`)
is **dropped wholesale** — the per-Match link now lives on `SeriesMatch.match`.
Every reader of `BracketNode.match` / `node.match_id` / the `match_id` dict key
moves to the Series-derived path (see §2, §3, §4). No alias is retained.

---

## 2. Pure module `matches/bracket.py`

**Frozen import allowlist unchanged** (`dataclasses`, `typing`, `math`,
`collections` ONLY — NO Django, NO ORM, NO `random`, NO `datetime`, NO I/O, NO
logging). The two new functions add **no new import** (`math` is already
imported). `matches/tests/test_bracket.py::TestNoDjangoImportsLeaked` must keep
passing.

### 2a. `clinch_threshold(series_length: int) -> int`

```python
def clinch_threshold(series_length: int) -> int:
    """Match wins needed to clinch a best-of-N Series: (series_length // 2) + 1.

    Bo1 -> 1, Bo3 -> 2, Bo5 -> 5//2+1 = 3. Pure integer math; no validation of
    odd-ness (callers only ever pass the locked 1/3/5 choices)."""
    return (series_length // 2) + 1
```

### 2b. `series_winner_slot(wins_a, wins_b, series_length) -> Optional[str]`

```python
def series_winner_slot(
    wins_a: int, wins_b: int, series_length: int
) -> Optional[str]:
    """Return the clinching slot ``"a"`` / ``"b"`` for a Series, or ``None``
    when neither team has yet reached the clinch threshold.

    threshold = clinch_threshold(series_length). Returns:
      - "a" when wins_a >= threshold,
      - "b" when wins_b >= threshold,
      - None when neither has reached it (Series still undecided).

    Tie-impossible note: with odd series_length and the per-Match Series-stops-
    on-clinch rule, BOTH slots can never reach the threshold in the same Series
    (the Series halts the moment the first slot clinches, so the loser's count
    is strictly below threshold). The deterministic guard checks ``wins_a``
    FIRST, so even a malformed (both-at-threshold) input resolves to "a" rather
    than raising — pure, total, never raises."""
```

- **Edge cases (locked):** wins below threshold ⇒ `None`; exactly-at or
  above threshold ⇒ that slot. `wins_a` is checked before `wins_b` so the
  function is total and deterministic on every integer input.

### 2c. `_node_to_dict` (in `matches/models.py`) — new derived keys

`_node_to_dict(node)` gains **3 new keys** and **drops `match_id`**:

| key | type | source |
|---|---|---|
| `wins_a` | `int` | `sum(1 for sm in node.series_matches.all() if sm.winner_id == node.team_a_id)` |
| `wins_b` | `int` | `sum(1 for sm in node.series_matches.all() if sm.winner_id == node.team_b_id)` |
| `series_length` | `int` | `node.tournament.series_length` |

- `match_id` is **removed** from the dict (the `BracketNode.match` field is
  gone). `wins_a` / `wins_b` count `SeriesMatch` rows whose `winner_id` equals
  the node's `team_a_id` / `team_b_id` respectively (derived, never stored).
- The caller is responsible for prefetching `series_matches` + `tournament` (or
  passing `series_length`) so `_node_to_dict` issues no per-node N+1 — see §3
  for the locked prefetch (`prefetch_related("series_matches")` +
  `select_related("tournament")` or carrying the tournament's `series_length`
  inward). The seam shape is what's pinned, not the query strategy.
- The other LG-02a keys (`bracket_round`, `position`, `team_a_id`, `team_b_id`,
  `seed_a`, `seed_b`, `is_bye`, `winner_id`, `advances_to`, `advances_to_slot`)
  are **unchanged**.

### 2d. `find_next_node` revised playable predicate

The LG-02a predicate

```
team_a_id is not None AND team_b_id is not None
  AND not is_bye AND winner_id is None AND match_id is None
```

becomes

```
team_a_id is not None AND team_b_id is not None
  AND not is_bye
  AND series_winner_slot(wins_a, wins_b, series_length) is None
```

i.e. **the old `winner_id IS NULL AND match_id IS NULL` checks are replaced by
`series_winner_slot(...) is None`**. A node is playable iff both slots are
filled, it is not a bye, and the Series is not yet clinched. (For Bo1 this is
exactly the old behaviour: `series_winner_slot(0, 0, 1) is None` ⇒ playable
until one Match is recorded, after which one slot hits threshold 1.) Ordering
remains lowest `(bracket_round, position)` first. `find_next_node` reads the new
dict keys `wins_a` / `wins_b` / `series_length`; it no longer reads `match_id`
or `winner_id`.

### 2e. Unchanged pure functions

`build_bracket`, `advance_winner`, `resolve_bye_chain`, `break_tie`,
`default_seed_order`, `stage_progress`, the two dataclasses (`BracketNodeSpec`,
`ParticipantSpec`) — all **unchanged**. In particular **`stage_progress`
semantics are unchanged** (Bracket-round-level: a round completes when every
non-bye node has `winner_id` set) — clinching a Series sets `node.winner`, so
`stage_progress` keeps working off `winner_id` with zero edits.

---

## 3. Engine `matches/tournament_engine.py` — revised `play_next_node`

**Signature unchanged:** `play_next_node(tournament: Tournament) -> "BracketNode
| None"`, decorated `@transaction.atomic`. **The transaction boundary is now
per-MATCH** (one Series Match = one atomic commit) — extends ADR-0016's
per-node-atomic to per-Match-atomic. Returns the `BracketNode` whose Series was
advanced this step (whether or not the Match clinched), or `None` when no node
is playable.

Revised algorithm (locked, in order):

1. `node = tournament.find_next_playable_node()`. If `None` ⇒ return `None`.
   (`find_next_playable_node` now uses the §2d predicate, so a node already
   clinched is skipped.)
2. Compute the current Series tally from the node's existing `SeriesMatch`
   rows: `wins_a = count(sm.winner_id == node.team_a_id)`,
   `wins_b = count(sm.winner_id == node.team_b_id)`.
3. Simulate **ONE** Match (sides fixed):
   `match = BatchSimulator().simulate_match(node.team_a, node.team_b,
   match_type="tournament")` (deferred import of `BatchSimulator`, as today).
4. Resolve the **per-Match** decisive winner exactly as LG-02a-2 did: if
   `match.winner is None`, `best_a = max(match.red_round1_points,
   match.red_round2_points)`, `best_b = max(match.blue_round1_points,
   match.blue_round2_points)`, `winning_seed = break_tie(node.seed_a, best_a,
   node.seed_b, best_b)`, map the seed back to `node.team_a`/`node.team_b`;
   else `match_winner = match.winner`.
5. Create the `SeriesMatch` row with the next `game_number`:
   `next_game = node.series_matches.count() + 1`;
   `SeriesMatch.objects.create(node=node, match=match, game_number=next_game,
   winner=match_winner)`.
6. **Recompute** the tally including the new row (`wins_a`/`wins_b` += the new
   Match's slot).
7. `slot = series_winner_slot(wins_a, wins_b, node.tournament.series_length)`.
   **If `slot is None`** (Series not yet clinched) ⇒ return `node` now (the
   step resolved one Match; the next call resolves the next Match of the same
   node). **No `node.winner` write, no advancement.**
8. **Series clinched** (`slot` is `"a"`/`"b"`): set `node.winner` to the
   clinching Team (`node.team_a` if `slot == "a"` else `node.team_b`) and
   `winner_seed` to the matching `node.seed_*`; `node.save(update_fields=
   ["winner"])` (NOTE: `match` is no longer a field on `BracketNode` — the
   update_fields list drops `"match"` and keeps only `"winner"`).
9. Compute + apply parent mutations exactly as LG-02a-2: build the flat
   `_node_to_dict` list over `tournament.nodes` (with the §2c prefetch), call
   `advance_winner(flat, (node.bracket_round, node.position), winner_team.id,
   winner_seed)`, apply each mutation to the parent `BracketNode`
   (`team_*`/`seed_*`, `save(update_fields=["team_a","team_b","seed_a","seed_b"])`).
10. If the clinched node is the final (`node.advances_to_id is None`):
    `tournament.champion = winner_team`; `tournament.state = "completed"`;
    `tournament.save(update_fields=["champion", "state"])`.
11. Return `node`.

**Bo1 equivalence (locked-decision-4):** with `series_length == 1`, step 5
creates game 1, step 6 gives the winner's slot `wins == 1`, step 7
`series_winner_slot(1, 0, 1) == "a"` (or `"b"`) ⇒ clinch on the first Match ⇒
identical single-Match advance to LG-02a. The only structural difference vs
LG-02a-2 is that the played `Match` now lives on a `SeriesMatch` row instead of
`BracketNode.match`.

**Callers unchanged in signature/route — they just call the revised step:**
`tournament_play_next` (sync POST view), `play_tournament_task` (Celery loop
`while play_next_node(tournament) is not None`), `tournament_play_all`,
`tournament_play_status`, and `stage_progress`-based progress reporting all keep
their existing signatures, URLs, and 5-key status JSON. The Celery loop now
calls `play_next_node` once **per Match** rather than once per node — it keeps
looping until no node is playable, so a Bo3/Bo5 simply takes more iterations to
drain; `stage_progress` still reports Bracket-round completion (a round
completes when every non-bye node has clinched / has `winner_id`).

---

## 4. Views / template (`matches/tournament_views.py`,
`templates/matches/tournament_create.html`, `tournament_detail.html`)

### 4a. Create form — series_length select

`tournament_create` reads a new POST field and stamps it on the Tournament.

- **POST field name:** `series_length` (parsed to int; invalid / absent ⇒
  default `1`; only `1`/`3`/`5` accepted, anything else falls back to `1` —
  forgiving-fallback precedent).
- Set on create: `Tournament.objects.create(name=name, state="setup",
  series_length=series_length)`.
- **DOM id (template `tournament_create.html`):**
  `tournament-create-series-length` — a `<select name="series_length">` with the
  three options (`1` "Best of 1" selected by default, `3` "Best of 3", `5`
  "Best of 5"). Placed inside the existing `tournament-create-form`, before the
  submit button.

### 4b. Detail page — per-node Series score

`_build_rounds` (the per-node view-dict builder in `tournament_views.py`) adds
the derived per-node win tally to each `node_view_dict`:

- New keys on each node view-dict: `wins_a: int`, `wins_b: int` (counted from
  the node's `SeriesMatch` rows, same derivation as §2c — the view prefetches
  `series_matches` to avoid N+1). The existing keys (`bracket_round`,
  `position`, `team_a`, `team_b`, `seed_a`, `seed_b`, `is_bye`, `winner`) stay;
  the **`match` key is removed** (the per-node link is now per-SeriesMatch — the
  template renders a "View match" link per played Series Match, or omits it).
- **DOM id (template `tournament_detail.html`):**
  `tournament-node-series-score-{bracket_round}-{position}` — an element inside
  each `tournament-node-{bracket_round}-{position}` card rendering the running
  Series score as `{{ node.wins_a }}–{{ node.wins_b }}` (en-dash U+2013, e.g.
  `2–1`). Rendered for every non-bye node (a bye node has no Series). For a Bo1
  node this reads `1–0` / `0–1` / `0–0` (degenerate but valid).
- The **final node's champion** still surfaces via the existing
  `tournament-champion-banner` (unchanged) — the Series-score element is the
  per-node running tally, the banner is the Tournament-level crown.

### 4c. Context keys

`tournament_detail` / `_detail_context` keeps its existing frozen keys
(`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`,
`import_form`, `import_row_errors`). The only change is the **shape of each
`rounds[*].nodes[*]` dict** (gains `wins_a`/`wins_b`, drops `match`). No new
top-level context key. `tournament.series_length` is read directly off the
`tournament` object in the template where the configuration needs displaying
(no separate context key required).

---

## 5. Test boundary

What Tests assert against (the seam) vs. what is internal:

### 5a. `matches/tests/test_bracket.py` (pure-unit, no DB, no Django)

- `TestClinchThreshold` — `clinch_threshold(1) == 1`, `(3) == 2`, `(5) == 3`.
- `TestSeriesWinnerSlot` — below-threshold ⇒ `None`; `a`-clinch; `b`-clinch;
  the `(N//2)+1` boundary for Bo3/Bo5; the deterministic both-at-threshold guard
  resolves to `"a"`.
- `TestFindNextNode` (extend) — Series cases on the new predicate: a node with
  `wins_a`/`wins_b` below threshold + both slots filled is playable; a clinched
  node is NOT returned; a half-played Bo3 (`1–0`) is still playable; Bo1 with one
  recorded Match is not playable. Built from hand-crafted flat dicts carrying the
  new `wins_a`/`wins_b`/`series_length` keys (no `match_id`).
- `TestNoDjangoImportsLeaked` — must still pass (the two new functions add no
  import).

### 5b. `matches/tests/test_tournament_models.py` (Django `TestCase`)

- `SeriesMatch` model: row create, `Meta.ordering` by `game_number`, the
  `uniq_seriesmatch_node_game` constraint (duplicate `(node, game_number)`
  raises `IntegrityError`), CASCADE on node delete, SET_NULL on Match/Team
  delete, `related_name="series_matches"`.
- `Tournament.series_length`: default `1`, the `1`/`3`/`5` choices,
  immutability-by-state (a `lock_and_build`'d Tournament rejects re-lock —
  reuses the existing guard).
- `_node_to_dict` derived keys: a node with N `SeriesMatch` rows produces the
  correct `wins_a`/`wins_b`/`series_length` and **no `match_id` key**.

### 5c. `matches/tests/test_tournament_engine.py` (Django `TestCase`)

- **Per-Match step:** `play_next_node` on a Bo3 node creates exactly ONE
  `SeriesMatch` row per call, increments `game_number`, and does NOT stamp
  `node.winner` until clinch.
- **Clinch + advance:** after the clinching Match, `node.winner` is set, the
  winner is Advanced into the parent slot (`team_*`/`seed_*`), and the final
  node clinch stamps `champion` + `state="completed"`.
- **Bo1 equivalence:** a Bo1 Tournament drives node→clinch→advance in exactly
  one `play_next_node` call per node, matching LG-02a behaviour (one Match, one
  advance) — asserted on the resolved tree shape, not on point totals.
- **Tie-break path:** when `match.winner is None`, the per-Match `break_tie`
  resolves the `SeriesMatch.winner` (forced-tie fixture).
- `play_next_node` returns `None` when no node is playable.
- Per-Match atomicity (one Match = one transaction).

### 5d. `matches/tests/test_tournament_views.py` (Django `TestCase`)

- Create-form: GET renders the `tournament-create-series-length` select; POST
  with `series_length=3` persists `tournament.series_length == 3`; invalid
  value falls back to `1`.
- Detail page: the `tournament-node-series-score-{br}-{pos}` element renders the
  running `wins_a–wins_b` for non-bye nodes; the champion still surfaces via
  `tournament-champion-banner` on a completed Bo-N Tournament.

### 5e. `matches/tests/test_tournament_tasks.py` (Django `TestCase`, under
`CELERY_TASK_ALWAYS_EAGER`)

- `play_tournament_task` plays a **Bo3** Tournament to a champion (drains every
  Series via repeated per-Match steps), stamps `champion` + `state="completed"`,
  and returns the final stage `{"completed", "total"}`.
- A node only Advances once its Series clinches (no advancement on a non-final
  Match of the Series).

**Internal (NOT asserted across the seam):** the exact prefetch/query strategy
inside `_node_to_dict` / `_build_rounds` / `play_next_node`; the in-memory
mutation-application order; the exact `next_game` derivation
(`count() + 1` vs `max(game_number) + 1`) — only the resulting `game_number`
sequence + uniqueness is pinned. Tests assert on the pure functions, the
`SeriesMatch` rows, `node.winner` / advancement, and the DOM ids — never on
exact simulated point totals (non-deterministic per locked-decision-8).

---

## 6. Scope-out (LOCKED — do NOT build)

- **Per-Bracket-round Series escalation** (Bo1 early rounds → Bo5 final) — a
  deferred follow-up; LG-02b ships a single per-Tournament `series_length`
  applied to every node.
- **Home/away (side) alternation across the Series** — sides are fixed
  (`team_a` red, `team_b` blue) for every Match (locked-decision-7).
- **Deterministic / master-seed-replayable Series** — `simulate_match` draws
  fresh per-round seeds; the Series is non-deterministic (locked-decision-8).
- **Any `simulate_match` / `simulate_scheduled_round` change** — consumed
  verbatim (`match_type="tournament"`, `arena_map=None` 3-zone fallback per
  Match).
- **Backfill / `RunPython`** — none (ADR-0004; the `RemoveField` + `CreateModel`
  + `AddField` are pure schema ops).
- **Score Calibration re-baseline** — none (no simulation mechanics change).
- **Any new CONTEXT.md term beyond Series / Series length** — both already
  written; no further glossary edit.
- **A Series-level tiebreaker** — odd N always clinches; ties are broken
  per-Match by the unchanged `break_tie`.
- **Dead-rubber Matches** — the Series stops the moment a Team clinches.

---

## 7. Locked-names index (quick reference)

**Models:** `matches.models.SeriesMatch` (fields `node` / `match` /
`game_number` / `winner`; `related_name="series_matches"` on `node`;
constraint `uniq_seriesmatch_node_game`; `Meta.ordering=["game_number"]`);
`Tournament.series_length` (`PositiveSmallIntegerField`, choices
`1`/`3`/`5`, default `1`); **dropped** `BracketNode.match`. Migration
`matches/migrations/0034_<name>.py` (ops: `AddField(Tournament.series_length)`
→ `CreateModel(SeriesMatch)` → `RemoveField(BracketNode.match)`).

**Pure module (`matches/bracket.py`):** `clinch_threshold(series_length) ->
int`; `series_winner_slot(wins_a, wins_b, series_length) -> Optional[str]`
(returns `"a"`/`"b"`/`None`); revised `find_next_node` predicate
(`series_winner_slot(...) is None` replaces `match_id IS NULL` + `winner_id IS
NULL`). Frozen import allowlist unchanged.

**Model seam helper (`matches/models.py`):** `_node_to_dict` gains `wins_a`,
`wins_b`, `series_length`; drops `match_id`.

**Engine (`matches/tournament_engine.py`):** `play_next_node(tournament) ->
BracketNode | None` (`@transaction.atomic`, now **per-Match**: sim one Match →
per-Match `break_tie` → create `SeriesMatch` → recompute tally → clinch ⇒
`node.winner` + `advance_winner` + champion/completed on final, else return the
node).

**Views/template:** create-form POST field `series_length`, DOM id
`tournament-create-series-length`; detail per-node Series-score DOM id
`tournament-node-series-score-{bracket_round}-{position}` (renders
`wins_a–wins_b`); `_build_rounds` node-dict gains `wins_a`/`wins_b`, drops
`match`. Champion via the unchanged `tournament-champion-banner`.

**ADR:** ADR-0020 "Best-of-N series bracket nodes" (cross-ref ADR-0019 +
ADR-0016).

**Test files:** `matches/tests/test_bracket.py` (extend — clinch helpers +
`find_next_node` Series cases + purity), `test_tournament_models.py` (extend —
`SeriesMatch` + `series_length` + `_node_to_dict`), `test_tournament_engine.py`
(extend — per-Match step + clinch-advance + Bo1-equivalence + tie-break),
`test_tournament_views.py` (extend — create-field + detail Series-score),
`test_tournament_tasks.py` (extend — play-all over a Bo3 Series).
