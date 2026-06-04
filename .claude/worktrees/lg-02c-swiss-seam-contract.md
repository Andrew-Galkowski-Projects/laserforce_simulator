# LG-02c Swiss — Seam Contract

A fifth `Tournament.format` value `"swiss"` (label `"Swiss"`) for the standalone
sandbox Tournament. Builds on the shipped single-elim / double-elim / round-robin
/ RR→DE formats. A **flat, edge-less** bracket: every Swiss node has
`advances_to=None`, `loser_advances_to=None`, `is_bye=False`, `series_length=1`
(Bo1). The champion is the **Standings leader** (Buchholz re-ranked) after the
last Swiss round resolves — NOT a final node.

This file is the SOURCE OF TRUTH for every new name/signature/dict-key/DOM-id/
literal. Where a name was a free choice it is committed here and must match.

> **Determinism:** non-deterministic (`simulate_match` draws fresh per-round
> seeds) ⇒ NO SIM-07/08 interaction, NO Score Calibration re-baseline.
> [ADR-0021](../../docs/adr/0021-double-elimination-bracket.md) **EXTENDED** with a
> Consequences note (no new ADR). CONTEXT.md already updated (Swiss + Buchholz
> terms) — DO NOT touch it.

---

## Locked domain rules (encode EXACTLY — do not re-litigate)

- New format `"swiss"` (label `"Swiss"`) — **5th** `FORMAT_CHOICES` entry.
- New `bracket_type` `"swiss"` (label `"Swiss"`) — **5th** `BracketNode.bracket_type`
  choice (`"swiss"` is 5 chars, fits the existing `max_length=12`).
- `_BRACKET_RANK["swiss"] = 4`.
- **Flat, edge-less:** every Swiss node `advances_to=None`,
  `advances_to_slot=None`, `loser_advances_to=None`, `loser_advances_to_slot=None`,
  `is_bye=False`, `series_length=1` (Bo1), `winner=None` until played.
- **EVEN-N ONLY:** odd participant count raises
  `django.core.exceptions.ValidationError` at lock with the EXACT message string
  `"Swiss requires an even number of participants."`. NO byes ever.
- **Round count** — new field `Tournament.swiss_rounds`
  (`PositiveSmallIntegerField`, `default=0`; `0` = auto). At lock resolve
  `total = swiss_rounds or math.ceil(math.log2(N))`, **clamp to `[1, N-1]`**, then
  **write the resolved value back** into `swiss_rounds` (frozen). Meaningful only
  for `format == "swiss"`; `0` otherwise.
- **R1 pairing = seed "fold":** sort participants by Bracket seed ascending, split
  in half, pair `seed[i]` vs `seed[i + N/2]` for `i` in `0..N/2-1`. Built at LOCK.
- **Later rounds DEFERRED** (built when the prior round's LAST node resolves):
  greedy ranked-sweep from CURRENT Swiss standings — walk `swiss_standings()`
  top-down, pair each unpaired team with the next unpaired team it has NOT already
  played; if the trailing teams can only be paired by replaying, **ALLOW the
  rematch** (no backtracking).
- **No draws:** `break_tie` forces a per-Match winner (inherited from
  `play_next_node`, unchanged) ⇒ `league_points` are purely `3 * wins`.
- **Buchholz tiebreak:** a team's Buchholz = sum of its opponents' final
  `league_points` across all Swiss rounds played. **Swiss ranking ladder:**
  `league_points desc → Buchholz desc → round_wins desc → total_score desc →
  team_name asc`. **ORDERING-ONLY** (Buchholz is NOT a displayed column).
  `compute_standings` is a FROZEN shared module — NOT modified; Buchholz is a
  separate re-rank layer taking `compute_standings` rows + the played-pairs
  opponent graph.

---

## PURE — `matches/bracket.py`

Frozen import allowlist UNCHANGED (`dataclasses` / `typing` / `math` /
`collections` ONLY). Both new functions + the re-rank add **NO new import** —
`TestNoDjangoImportsLeaked` MUST stay green.

### `_BRACKET_RANK` literal edit

```python
_BRACKET_RANK = {"winners": 0, "losers": 1, "grand_final": 2, "round_robin": 3, "swiss": 4}
```

### `build_swiss_round(...)` — ONE function for BOTH R1 fold and later greedy sweep

```python
def build_swiss_round(
    ranked_team_ids: list[int],
    seed_by_team: dict[int, int],
    played_pairs: set[frozenset[int]],
    bracket_round: int,
) -> list[BracketNodeSpec]:
    ...
```

- **Single function, both variants** (decided: one function). The variant is
  selected by the CALLER via what it passes in `ranked_team_ids` + `played_pairs`:
  - **R1 (fold):** caller passes `ranked_team_ids` = the **seed "fold" order**
    (it pre-computes the fold itself — see Model `lock_and_build` below: sort by
    seed asc, then for `i in 0..N/2-1` emit `seed[i]` then `seed[i + N/2]`,
    interleaved so consecutive pairs are the fold pairs) and `played_pairs = set()`
    (empty). The function then greedily pairs consecutive unpaired teams in the
    given order — with an empty `played_pairs` the "not yet played" check never
    fires, so it produces exactly the fold pairing.
  - **Later rounds (greedy sweep):** caller passes `ranked_team_ids` = the current
    **Swiss-standings rank order** (`[row.team_id for row in swiss_standings()]`)
    and the non-empty `played_pairs`. The function walks top-down, pairing each
    unpaired team with the **next unpaired team it has NOT already played**;
    if the trailing teams can only be paired by a rematch, it ALLOWS the rematch
    (no backtracking — the last fallback pair is the next unpaired team regardless
    of `played_pairs`).
- `seed_by_team`: maps **every** team id → its Bracket seed, used ONLY to stamp
  `seed_a` / `seed_b` on each node (Swiss pairing order is rank/fold-based, not
  seed-based, but each node still carries the slot teams' Bracket seeds so the
  engine's seed-keyed tie-break works).
- `played_pairs`: a `set[frozenset[int]]` (each entry `frozenset({team_id_a,
  team_id_b})`). Side-agnostic.
- Returns the round's `BracketNodeSpec` list, one spec per pairing, ordered by
  `position` (0-based, in pairing order). Each spec sets:
  `bracket_round=bracket_round`, `position=<0-based index>`,
  `team_a_id=<first of pair>`, `team_b_id=<second of pair>`,
  `seed_a=seed_by_team[team_a_id]`, `seed_b=seed_by_team[team_b_id]`,
  `is_bye=False`, `advances_to=None`, `advances_to_slot=None`, `winner_id=None`,
  `bracket_type="swiss"`, `loser_advances_to=None`, `loser_advances_to_slot=None`,
  `depth=None`.
- Pure: no Django, no ORM, no RNG. Total (never raises) — an odd
  `len(ranked_team_ids)` is the caller's responsibility (the EVEN-N guard fires at
  lock before this is ever called); defensively the trailing unpaired team (if
  any) is dropped.

### `swiss_buchholz_rerank(...)` — pure Buchholz re-rank layer

```python
def swiss_buchholz_rerank(
    rows: list,                          # list[StandingsRow] from compute_standings
    opponents_by_team: dict[int, list[int]],
) -> list:                               # re-sorted list[StandingsRow], rank renumbered 1-based
    ...
```

- `rows`: the `StandingsRow` list returned by `compute_standings` (17-field frozen
  dataclass — `team_id`, `league_points`, `round_wins`, `total_score`, `rank`, …).
- `opponents_by_team`: `team_id -> list[opponent_team_id]` (one entry per Swiss
  pairing the team played; a rematch appears twice — Buchholz sums per played
  pairing, so duplicates ARE counted).
- Builds `points_by_team = {row.team_id: row.league_points for row in rows}`,
  then `buchholz(team_id) = sum(points_by_team.get(opp, 0) for opp in
  opponents_by_team.get(team_id, []))`.
- Re-sorts `rows` by the LOCKED ladder:
  `(-league_points, -buchholz, -round_wins, -total_score, team_name asc)`.
  **`team_name` is NOT a `StandingsRow` field** — the input `rows` are already in
  `compute_standings`' final order (which ends with `team_name asc`), so the
  re-rank uses a **STABLE sort** keyed on `(-league_points, -buchholz,
  -round_wins, -total_score)` and the pre-existing `team_name asc` ordering of
  `rows` survives as the stable tiebreak. (No team-name lookup crosses this seam —
  keeps the function in the pure int/dataclass domain.)
- Returns a NEW list of `StandingsRow` instances **with `rank` renumbered 1-based
  dense** in the re-sorted order (every other field copied verbatim —
  `dataclasses.replace(row, rank=i + 1)`).
- Pure: no Django, no ORM, no RNG. Empty `rows` ⇒ `[]`.

> The two `BracketNodeSpec` dataclass + `ParticipantSpec` are UNCHANGED.
> `build_bracket` / `build_double_elim_bracket` / `build_rr_de_finals_bracket` /
> `find_next_node` / `advance_winner` / `advance_loser` / `resolve_bye_chain` /
> `series_length_for_*` / `break_tie` / `default_seed_order` / `stage_progress` /
> `count_series_wins` are UNCHANGED. `find_next_node`'s sort key already reads
> `_BRACKET_RANK.get(bracket_type, 0)`, so the new `"swiss": 4` entry slots in
> with no edit to `find_next_node`; its playable predicate
> (`series_winner_slot(...) is None` on an unplayed Bo1 node, both slots filled,
> not bye) treats an unplayed Swiss node as playable and a resolved one as
> skipped, with no edit.

---

## MODEL — `matches/models.py`

### New field on `Tournament`

Declared **immediately after `lb_advancers`** (before `created_at`/`champion` is
fine — match the existing block placement of the RR→DE advancer fields):

```python
# LG-02c (Swiss) — total number of Swiss rounds. 0 = auto (resolved at lock to
# ceil(log2(N)), clamped to [1, N-1], then written back here — frozen). No
# choices. Meaningful only for format == "swiss"; 0 otherwise.
swiss_rounds = models.PositiveSmallIntegerField(default=0)
```

### `FORMAT_CHOICES` — 5th entry

```python
FORMAT_CHOICES = (
    ("single_elimination", "Single elimination"),
    ("double_elimination", "Double elimination"),
    ("round_robin", "Round robin"),
    ("round_robin_double_elim", "Round robin → Double elimination"),
    ("swiss", "Swiss"),
)
```

### `BracketNode.bracket_type` choices — 5th entry

Add `("swiss", "Swiss")` to the existing `bracket_type` `choices` tuple
(declaration otherwise unchanged: `CharField(max_length=12)`,
`default="winners"`).

### Refactor: extract `_standings_over_nodes`

Extract the `compute_standings`-input assembly out of the body of
`round_robin_standings()` into a private helper. **`round_robin_standings()`'s
external behaviour must stay byte-identical** (a regression test pins this).

```python
def _standings_over_nodes(self, node_qs) -> "list[StandingsRow]":
    """Assemble the three compute_standings seam inputs from a queryset of
    resolved Bracket nodes (Bo1) and return the ranked rows. Shared by
    round_robin_standings (RR nodes) and swiss_standings (Swiss nodes).
    """
    from .standings import compute_standings
    participants = list(self.participants.select_related("team"))
    enrolled_teams = [(p.team_id, p.team.name) for p in participants]
    nodes = list(
        node_qs.select_related("team_a", "team_b")
        .prefetch_related("series_matches__match__game_rounds")
    )
    completed_matches: list[dict] = []
    season_rounds: list[dict] = []
    for node in nodes:
        if node.winner_id is None:
            continue
        series = list(node.series_matches.all())
        if not series:
            continue
        match = series[0].match            # Bo1 — exactly one played SeriesMatch
        if match is None:
            continue
        completed_matches.append({
            "match_id": match.id,
            "team_red_id": match.team_red_id,
            "team_blue_id": match.team_blue_id,
            "winner_team_id": node.winner_id,   # node winner (== match.winner or break_tie); never None
            "red_rounds_won": match.red_rounds_won,
            "blue_rounds_won": match.blue_rounds_won,
            "red_total_points": match.red_total_points,
            "blue_total_points": match.blue_total_points,
            "date_played": match.date_played,
        })
        for gr in match.game_rounds.all():
            season_rounds.append({
                "round_id": gr.id,
                "team_red_id": gr.team_red_id,
                "team_blue_id": gr.team_blue_id,
                "red_points": gr.red_points,
                "blue_points": gr.blue_points,
                "date_played": gr.date_played,
            })
    return compute_standings(completed_matches, enrolled_teams, season_rounds)
```

`round_robin_standings()` becomes:

```python
def round_robin_standings(self) -> "list[StandingsRow]":
    return self._standings_over_nodes(self.nodes.filter(bracket_type="round_robin"))
```

> The forward-ref `"list[StandingsRow]"` string annotation is the SAME quoting
> `round_robin_standings` already uses — `StandingsRow` stays imported lazily
> inside the method body via `from .standings import compute_standings` (the
> name `StandingsRow` is NEVER imported at module scope; the string annotation
> needs no import). Keep it that way.

### `swiss_standings(self) -> list[StandingsRow]`

```python
def swiss_standings(self) -> "list[StandingsRow]":
    """LG-02c (Swiss) — Buchholz-re-ranked Standings for this Swiss Tournament.

    Base rows come from _standings_over_nodes over the Swiss nodes; the
    Buchholz re-rank layer (pure) re-sorts them on the locked Swiss ladder.
    """
    from .bracket import swiss_buchholz_rerank
    rows = self._standings_over_nodes(self.nodes.filter(bracket_type="swiss"))
    opponents_by_team = self._swiss_opponent_graph()
    return swiss_buchholz_rerank(rows, opponents_by_team)
```

### `_swiss_opponent_graph(self) -> dict[int, list[int]]` (private helper)

Builds the played-pairs opponent graph from the Swiss nodes (each Swiss node =
one pairing `team_a` / `team_b`). Only nodes whose `team_a_id` and `team_b_id`
are both set count; a rematch contributes to both lists each time it was played.
(The graph is built over ALL Swiss nodes that have both slots filled — a node
need not be resolved to have been a played pairing, but in practice deferred
rounds only exist once prior rounds resolved.)

```python
def _swiss_opponent_graph(self) -> dict[int, list[int]]:
    graph: dict[int, list[int]] = {}
    for node in self.nodes.filter(bracket_type="swiss"):
        a, b = node.team_a_id, node.team_b_id
        if a is None or b is None:
            continue
        graph.setdefault(a, []).append(b)
        graph.setdefault(b, []).append(a)
    return graph
```

### `lock_and_build` — Swiss branch

Add a Swiss branch. **Decided: it sits as its OWN branch** (a `setup`-guard +
`>= 4` guard already precede the format dispatch; Swiss is NOT folded into the RR
branch because it needs the even-N guard + round-count freeze + fold pairing +
its own `bracket_type`). Sketch (inside the existing `@transaction.atomic`,
AFTER the `>= 4` participant guard, alongside the RR / RR→DE branch):

```python
if self.format == "swiss":
    from .bracket import build_swiss_round   # joins the existing deferred import block

    n = len(participants)
    if n % 2 != 0:
        raise ValidationError("Swiss requires an even number of participants.")

    # Resolve + clamp + freeze the round count.
    total = self.swiss_rounds or math.ceil(math.log2(n))
    total = max(1, min(total, n - 1))
    self.swiss_rounds = total

    # R1 = seed "fold". Sort by Bracket seed asc, split in half, interleave so
    # consecutive pairs are (seed[i], seed[i + n//2]).
    by_seed = sorted(participants, key=lambda p: p.seed)
    half = n // 2
    fold_order: list[int] = []
    for i in range(half):
        fold_order.append(by_seed[i].team_id)
        fold_order.append(by_seed[i + half].team_id)
    seed_by_team = {p.team_id: p.seed for p in participants}
    team_by_id = {p.team_id: p.team for p in participants}

    specs = build_swiss_round(fold_order, seed_by_team, set(), bracket_round=1)
    for spec in specs:
        BracketNode.objects.create(
            tournament=self,
            bracket_round=spec.bracket_round,
            position=spec.position,
            bracket_type="swiss",
            team_a=team_by_id[spec.team_a_id],
            team_b=team_by_id[spec.team_b_id],
            seed_a=spec.seed_a,
            seed_b=spec.seed_b,
            is_bye=False,
            advances_to_slot=None,
            loser_advances_to_slot=None,
            winner=None,
            series_length=1,
        )
    self.state = "active"
    self.save(update_fields=["state", "swiss_rounds"])
    return
```

> `import math` is already at module scope in `matches/models.py`? — VERIFY at code
> time; if absent, add `import math` to the module imports (Swiss is the only model
> consumer). `build_swiss_round` is deferred-imported inside the branch (mirrors
> the RR branch's deferred `from .schedule_generator import generate_schedule`).
> NO `advances_to` / `loser_advances_to` wiring pass, NO `resolve_bye_chain`
> (flat, like the RR branch). `_persist_elim_specs` is NOT used (Swiss is
> edge-less).

### `advance_swiss_if_round_finished(self) -> None` (`@transaction.atomic`)

```python
@transaction.atomic
def advance_swiss_if_round_finished(self) -> None:
    """LG-02c (Swiss) — build the next Swiss round, or crown, when the current
    (highest) Swiss round's last node resolves.

    No-op unless format == "swiss" and state == "active". Determine the current
    (highest) Swiss bracket_round; if NOT all its nodes have a winner, no-op.
    If resolved AND current_round < swiss_rounds, build + persist the next
    round's nodes (greedy ranked sweep from swiss_standings + played_pairs).
    If resolved AND current_round == swiss_rounds, crown swiss_standings()[0]
    and complete.
    """
    if self.format != "swiss" or self.state != "active":
        return

    swiss_nodes = self.nodes.filter(bracket_type="swiss")
    current_round = max((n.bracket_round for n in swiss_nodes), default=0)
    if current_round == 0:
        return
    current_nodes = [n for n in swiss_nodes if n.bracket_round == current_round]
    if any(n.winner_id is None for n in current_nodes):
        return   # round not finished

    if current_round < self.swiss_rounds:
        from .bracket import build_swiss_round
        rows = self.swiss_standings()
        ranked_team_ids = [row.team_id for row in rows]
        played_pairs = self._swiss_played_pairs()
        seed_by_team = {p.team_id: p.seed for p in self.participants.all()}
        team_by_id = {p.team_id: p.team for p in self.participants.all()}
        specs = build_swiss_round(
            ranked_team_ids, seed_by_team, played_pairs,
            bracket_round=current_round + 1,
        )
        for spec in specs:
            BracketNode.objects.create(
                tournament=self,
                bracket_round=spec.bracket_round,
                position=spec.position,
                bracket_type="swiss",
                team_a=team_by_id[spec.team_a_id],
                team_b=team_by_id[spec.team_b_id],
                seed_a=spec.seed_a,
                seed_b=spec.seed_b,
                is_bye=False,
                advances_to_slot=None,
                loser_advances_to_slot=None,
                winner=None,
                series_length=1,
            )
        # Tournament STAYS active.
    else:
        rows = self.swiss_standings()
        if rows:
            self.champion_id = rows[0].team_id
            self.state = "completed"
            self.save(update_fields=["champion", "state"])
```

### `_swiss_played_pairs(self) -> set[frozenset[int]]` (private helper)

Derives `played_pairs` from existing Swiss nodes' `team_a_id` / `team_b_id`
(every node IS a pairing). Side-agnostic frozenset keys.

```python
def _swiss_played_pairs(self) -> set[frozenset[int]]:
    pairs: set[frozenset[int]] = set()
    for node in self.nodes.filter(bracket_type="swiss"):
        if node.team_a_id is not None and node.team_b_id is not None:
            pairs.add(frozenset({node.team_a_id, node.team_b_id}))
    return pairs
```

> `find_next_playable_node`, `_node_to_dict`, `count_series_wins`,
> `complete_round_robin_if_finished`, `build_de_finals_if_rr_finished`,
> `_persist_elim_specs`, `round_robin_standings` (post-extraction body) are
> otherwise UNCHANGED. `_node_to_dict` already returns `bracket_type` +
> `advances_to`(None for Swiss) etc. — Swiss rows yield
> `bracket_type="swiss"`, `advances_to=None`, `loser_advances_to=None`,
> `series_length=1` with no `_node_to_dict` edit.

---

## ENGINE — `matches/tournament_engine.py::play_next_node`

The body is VERBATIM through the clinch check. The existing RR/RR→DE dispatch
guard keys on `node.bracket_type == "round_robin"` (around lines 94-99 today).
ADD a Swiss branch **alongside** that guard — AFTER the `node.winner` stamp
(`node.save(update_fields=["winner"])`) and BEFORE the elim
`_node_to_dict` flatten / `advance_winner` / `advance_loser` / crown block:

```python
# 7a. Round-robin (seeding-stage) node ... (existing)
if node.bracket_type == "round_robin":
    if tournament.format == "round_robin":
        tournament.complete_round_robin_if_finished()
    elif tournament.format == "round_robin_double_elim":
        tournament.build_de_finals_if_rr_finished()
    return node

# 7b. LG-02c (Swiss) — a Swiss node has advances_to=None, so the elim
# "crown on advances_to is None" rule would wrongly crown on the FIRST resolved
# node. SKIP the advance/crown block entirely; build the next round (or crown)
# only when the CURRENT round's last node resolves.
if node.bracket_type == "swiss":
    tournament.advance_swiss_if_round_finished()
    return node
```

The Swiss branch `return node` means a resolved Swiss node NEVER reaches
`advance_winner` / `advance_loser` / the elim crown block. Callers
(`tournament_play_next`, `play_tournament_task`, `tournament_play_all`,
`tournament_play_status`) are UNCHANGED in signature/route; `play_tournament_task`'s
`while play_next_node(...) is not None` loop drains every Swiss node one Match at a
time, and the deferred next-round build means the loop naturally extends mid-run as
each round's pairings materialize. `stage_progress` (unchanged) reports per-
`(bracket_type, bracket_round)` group completion — for Swiss that is per-round
progress.

---

## VIEW + TEMPLATE — `matches/tournament_views.py` + templates

### `tournament_create` (`matches/tournament_views.py`)

- Add `"swiss"` to the format whitelist (the existing `if tournament_format not in
  (...)` tuple — append `"swiss"`).
- Parse a new POST field `swiss_rounds` (FORGIVING):

```python
def _parse_swiss_rounds(raw: "str | None") -> int:
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        value = 0
    return value if value > 0 else 0   # absent/blank/invalid/negative -> 0 (auto)
```

  (Clamping happens at lock; the view just coerces to a non-negative int, `0`
  meaning auto.)
- Pass `swiss_rounds=swiss_rounds` into the existing `Tournament.objects.create(...)`
  call (alongside `wb_advancers` / `lb_advancers`). For non-Swiss creates it
  persists whatever was parsed (harmless — only read at lock when
  `format == "swiss"`).

### `tournament_create.html`

- Add a 5th `<option value="swiss">Swiss</option>` to the existing
  `<select id="tournament-create-format" name="format" onchange="tournamentCreateToggle(this.value)">`.
- Add a numeric input row for the Swiss round count, wrapped in a row that the
  toggle shows ONLY for `swiss`:

```html
<div class="mb-3 tournament-create-swiss-rounds-row" style="display:none">
    <label for="tournament-create-swiss-rounds" class="form-label">Swiss rounds (0 = auto)</label>
    <input type="number" class="form-control" id="tournament-create-swiss-rounds"
           name="swiss_rounds" min="0" value="0">
</div>
```

  DOM id of the input: **`tournament-create-swiss-rounds`** (LOCKED). The wrapper
  class: `tournament-create-swiss-rounds-row` (mirrors the `*-series-length-row` /
  `*-rrde-combo-row` pattern).
- Extend the existing `tournamentCreateToggle(value)` JS:
  - show `.tournament-create-swiss-rounds-row` ONLY when `value === "swiss"`,
    hide otherwise.
  - HIDE `.tournament-create-series-length-row` AND `.tournament-create-rrde-combo-row`
    when `value === "swiss"` (Swiss is always Bo1, no DE finals). Mirror the
    existing `hideSeries = value === "round_robin"` rule — extend it to
    `value === "round_robin" || value === "swiss"`.

### `_detail_context` (`matches/tournament_views.py`)

- Add a Swiss branch producing `swiss_rounds_view` (the per-round pairing
  sections) and `swiss_standings` (the Buchholz-ranked rows paired with Teams).
  Default both to `[]` for non-Swiss formats so the template references them
  unconditionally (mirrors the `rr_crosstable=[]` / `rr_standings=[]` defaults).

New context keys (added to the returned dict; every existing key UNCHANGED):

| key | type | value |
|---|---|---|
| `swiss_rounds_view` | `list[dict]` | per Swiss round, in `bracket_round` order: `{"round_number": int, "pairings": [<node_view_dict>, ...]}` — `[]` for non-Swiss |
| `swiss_standings` | `list[tuple[StandingsRow, Team]]` | `tournament.swiss_standings()` rows paired with their Team (the LG-01 `rows_with_teams` precedent — `StandingsRow` carries only `team_id`); `[]` for non-Swiss |

The per-pairing `<node_view_dict>` reuses the SAME shape `_build_rounds` builds for
RR/elim nodes (`bracket_round`, `position`, `bracket_type`, `team_a`, `team_b`,
`seed_a`, `seed_b`, `is_bye`, `wins_a`, `wins_b`, `series_length`,
`series_matches`, `winner`) so the template's node-card include is reused. Build
it via a small helper (suggested `_build_swiss_rounds(tournament)`) that groups
the Swiss nodes by `bracket_round` (NOT by overloading `_build_rounds`, which
returns the 3-key elim dict). `_build_rounds`'s existing 3-key
`{"winners", "losers", "grand_final"}` return is UNCHANGED — all three lists are
empty for a Swiss Tournament (Swiss nodes are `bracket_type="swiss"`, which
`_build_rounds` simply never buckets into its three known sections), so the elim
sections render empty and the template branches on `tournament.format`.

The frozen `_detail_context` keys (`tournament`, `participants`, `rounds`,
`next_node`, `is_locked`, `can_play`, `import_form`, `import_row_errors`,
`rr_crosstable`, `rr_standings`, `tournament_stage`, `cut_labels`) are UNCHANGED;
`swiss_rounds_view` + `swiss_standings` are ADDED.

`_tournament_stage` (`matches/tournament_views.py`): extend so a Swiss Tournament
returns a meaningful stage. For `format == "swiss"` the existing helper already
returns `"setup"` (state==setup) / `"completed"` (state==completed) via its early
branches; for the active case it currently returns `tournament.format` (the
benign fallback). Add an explicit `if tournament.format == "swiss": return "swiss"`
so the `tournament-stage-badge` reads `"swiss"` (the template renders a "Swiss
stage" label off it). The stage badge currently renders only for
`round_robin_double_elim` — extend the template `{% if tournament.format == ... %}`
guard around `tournament-stage-badge` to also fire for `"swiss"`.

### `tournament_detail.html`

Add a Swiss render block, gated `{% elif tournament.format == "swiss" %}` in the
same `{% if/elif %}` ladder that already branches `round_robin` /
`round_robin_double_elim` / `double_elimination` / single-elim. New DOM ids
(LOCKED):

| DOM id | element | when |
|---|---|---|
| `tournament-swiss-rounds` | outer container of the per-round pairing sections | Swiss only |
| `tournament-swiss-round-{n}` | one section per Swiss round (`n` = 1-based `round_number`) | Swiss only |
| `tournament-swiss-standings` | outer `<table>` of the Buchholz-ranked standings | Swiss only |

Per-pairing node DOM id — reuse the **DE-namespaced pattern** so it stays
consistent with the existing `tournament-node-{bracket_type}-{bracket_round}-
{position}` convention:

| DOM id | element |
|---|---|
| `tournament-node-swiss-{bracket_round}-{position}` | one card per Swiss pairing |

(Swiss nodes are always Bo1 ⇒ render NO per-node series-score / Bo-N label, same
as RR nodes.)

REUSE VERBATIM (no new ids): `tournament-champion-banner` (rendered when
`tournament.champion` is set — the Swiss completion path stamps it identically),
the shared lock control (`tournament-lock-form` / `-submit`), play-next
(`tournament-play-next-form` / `-submit`), play-all (`tournament-play-all-form` /
`-submit` / `-progress` + the poll JS), and the import + seeding forms. The elim
WB/LB/GF section containers (`tournament-bracket*`) and the RR ids
(`tournament-rr-crosstable` / `tournament-rr-standings`) are ABSENT for Swiss; the
two Swiss ids are ABSENT for every other format (template branches on
`tournament.format`).

`tournaments-nav-link` (sandbox nav) UNCHANGED.

---

## MIGRATION — `matches/migrations/0039_tournament_swiss.py`

- **Dependency:** `("matches", "0038_tournament_rr_de")` (VERIFIED — `0038` is the
  latest `matches` migration on disk).
- Operations in PINNED order (no `RunPython`, no backfill, ADR-0004 disposable-
  sandbox precedent):
  1. `AlterField(model_name="tournament", name="format", ...)` — widen `choices`
     to the 5-tuple (choices-only; no DB-level enforcement, included so
     `makemigrations --check` is clean).
  2. `AlterField(model_name="bracketnode", name="bracket_type", ...)` — widen
     `choices` to add `("swiss", "Swiss")` (5th).
  3. `AddField(model_name="tournament", name="swiss_rounds",
     field=models.PositiveSmallIntegerField(default=0))`.

---

## TEST BOUNDARY

Tests assert on the PURE functions, persisted node/row shapes,
`node.winner` / `tournament.champion` / `tournament.state`, standings ORDER, and
DOM ids — **NEVER on exact simulated point totals** (non-deterministic).

### Pure-unit — `matches/tests/test_bracket.py` (extend)

- `TestBuildSwissRound`:
  - R1 fold for N=4 / 8 / 16 — given the interleaved fold order + empty
    `played_pairs`, the function emits the EXACT fold pairing
    (`seed[i]` vs `seed[i+N/2]`), `bracket_type="swiss"`, `advances_to=None`,
    `loser_advances_to=None`, `is_bye=False`, `series_length` is NOT set by the
    builder (the spec leaves `series_length` to the persist layer — the spec field
    list has no `series_length`; the node row gets `series_length=1` at create),
    `position` 0-based ascending, `seed_a`/`seed_b` from `seed_by_team`.
  - Later-round greedy sweep with a non-empty `played_pairs` — pairs each unpaired
    team with the next unpaired team it has NOT already played.
  - Allow-rematch fallback — a `played_pairs` set that forces the trailing teams
    to rematch produces a rematch pair (no crash, no dropped team for even N).
- `TestSwissBuchholzRerank`:
  - Ladder correctness — higher Buchholz breaks an equal-`league_points` tie;
    `round_wins` / `total_score` lower tiebreaks; `team_name asc` survives via the
    stable sort on the pre-ordered input.
  - ORDERING-ONLY — every returned row is a `StandingsRow` with all 17 fields
    preserved except `rank` (renumbered 1-based dense); no Buchholz value leaks
    into the row.
  - Empty input ⇒ `[]`.
- `TestBracketRankSwiss`: `_BRACKET_RANK["swiss"] == 4`.
- `TestNoDjangoImportsLeaked` — STILL green (no new import in `bracket.py`).

### Model — `matches/tests/test_tournament_models.py` (extend)

- `TestSwissRoundsField` — `swiss_rounds` exists, defaults `0`, no choices.
- `TestSwissLockAndBuild`:
  - even-N happy path — builds ONLY R1 nodes; count == `N/2`; all
    `bracket_type="swiss"` / `series_length=1` / `advances_to_id is None` /
    `loser_advances_to_id is None` / `is_bye=False`; `state="active"`; the fold
    pairing (seed `i` vs `i+N/2`).
  - round-count resolve/clamp/freeze — `swiss_rounds=0` ⇒ resolved to
    `ceil(log2(N))` clamped to `[1, N-1]` and written back; an explicit
    `swiss_rounds` out of range is clamped + frozen.
  - odd-N ⇒ `ValidationError` with the EXACT message
    `"Swiss requires an even number of participants."`.
- `TestStandingsOverNodesExtraction` — `round_robin_standings()` output is
  byte-identical to its pre-refactor result (regression: build a small RR
  Tournament, hand-stamp resolved nodes, assert the ranked rows are unchanged).
- `TestSwissStandingsBuchholz` — hand-stamp resolved Swiss nodes across ≥2 rounds,
  assert `swiss_standings()` ORDER reflects the Buchholz ladder (NOT exact points).
- `TestAdvanceSwissIfRoundFinished`:
  - current round NOT all resolved ⇒ no-op (no new nodes, state unchanged).
  - current round resolved AND `current_round < swiss_rounds` ⇒ next round's nodes
    built (greedy sweep from `swiss_standings` + `played_pairs`),
    `bracket_round == current+1`, tournament STAYS active.
  - current round resolved AND `current_round == swiss_rounds` ⇒
    `champion_id == swiss_standings()[0].team_id`, `state="completed"`.
  - `played_pairs` derivation — assert a rematch is allowed only as a trailing
    fallback.

### Engine — `matches/tests/test_tournament_engine.py` (extend)

- `TestPlayNextNodeSwiss`:
  - a resolved Swiss node NEVER gets an `advance_winner` mutation (no parent slot
    filled).
  - resolving the current round's LAST node triggers the next round build
    (`advance_swiss_if_round_finished`), tournament stays active.
  - the final round's completion crowns `swiss_standings()[0]` and flips
    `state="completed"`.

### Views — `matches/tests/test_tournament_views.py` (extend)

- `TestCreateFormSwiss` — the format select offers `swiss`; a `swiss_rounds`
  numeric input exists with DOM id `tournament-create-swiss-rounds`; a POST
  persists `format == "swiss"` + the coerced `swiss_rounds`; forgiving parse
  (absent/blank/invalid/negative ⇒ `0`); a tampered/absent format falls back to
  `single_elimination`.
- `TestDetailSwiss` — detail renders `tournament-swiss-rounds` with per-round
  `tournament-swiss-round-{n}` sections + per-pairing
  `tournament-node-swiss-{br}-{pos}` cards + `tournament-swiss-standings`; the four
  series-length selects AND the rrde-combo control are hidden for swiss (assert the
  toggle JS / the row classes); the shared `tournament-champion-banner` /
  `tournament-lock-*` / `tournament-play-next-*` / `tournament-play-all-*` ids
  render; elim + RR ids absent.

### Tasks — `matches/tests/test_tournament_tasks.py` (extend)

- `TestPlayTournamentTaskSwiss` — under `CELERY_TASK_ALWAYS_EAGER`,
  `play_tournament_task` drains a full Swiss Tournament (all rounds) to a champion
  + `state="completed"`; `stage_progress` reports per-round stage counts.

---

## Locked-names quick index

- **Format / bracket_type:** `Tournament.format == "swiss"` (label `"Swiss"`,
  5th `FORMAT_CHOICES`); `BracketNode.bracket_type == "swiss"` (label `"Swiss"`,
  5th choice); `_BRACKET_RANK["swiss"] = 4`.
- **Field:** `Tournament.swiss_rounds = PositiveSmallIntegerField(default=0)`.
- **Pure (`matches/bracket.py`):**
  `build_swiss_round(ranked_team_ids, seed_by_team, played_pairs, bracket_round) -> list[BracketNodeSpec]`
  (ONE function, R1 fold via empty `played_pairs`, later rounds via rank order +
  filled `played_pairs`, allow-rematch fallback);
  `swiss_buchholz_rerank(rows, opponents_by_team) -> list[StandingsRow]`
  (ladder `league_points desc → Buchholz desc → round_wins desc → total_score desc
  → team_name asc` via stable sort, `rank` renumbered 1-based; ORDERING-ONLY).
- **Model (`matches/models.py`):** `_standings_over_nodes(self, node_qs)`
  (extracted); `round_robin_standings()` (calls it over RR nodes — byte-identical);
  `swiss_standings()` (calls it over Swiss nodes + `swiss_buchholz_rerank`);
  `_swiss_opponent_graph()` → `dict[int, list[int]]`; `_swiss_played_pairs()` →
  `set[frozenset[int]]`; `lock_and_build` Swiss branch (even-N
  `ValidationError`, resolve+clamp+freeze `swiss_rounds`, build R1 via
  `build_swiss_round`, persist flat, `state="active"`,
  `save(update_fields=["state", "swiss_rounds"])`);
  `advance_swiss_if_round_finished(self) -> None` (`@transaction.atomic`).
- **Engine (`matches/tournament_engine.py`):** `play_next_node` Swiss branch —
  `if node.bracket_type == "swiss": tournament.advance_swiss_if_round_finished(); return node`
  (added after the RR/RR→DE guard, before the elim block).
- **View (`matches/tournament_views.py`):** `tournament_create` whitelist +=
  `"swiss"`; `_parse_swiss_rounds(raw) -> int`; `swiss_rounds=` into
  `Tournament.objects.create(...)`; `_detail_context` adds `swiss_rounds_view` +
  `swiss_standings`; `_build_swiss_rounds(tournament)` helper; `_tournament_stage`
  returns `"swiss"` for the active Swiss case.
- **DOM ids (new):** `tournament-create-swiss-rounds`; `tournament-swiss-rounds`;
  `tournament-swiss-round-{n}`; `tournament-swiss-standings`;
  `tournament-node-swiss-{bracket_round}-{position}`. **Reused verbatim:**
  `tournament-create-format`, `tournament-champion-banner`, `tournament-stage-badge`
  (guard extended to swiss), `tournament-lock-*`, `tournament-play-next-*`,
  `tournament-play-all-*`.
- **Context keys (new):** `swiss_rounds_view` (`list[{round_number, pairings}]`),
  `swiss_standings` (`list[(StandingsRow, Team)]`).
- **Migration:** `matches/migrations/0039_tournament_swiss.py`, dep
  `0038_tournament_rr_de`, ops `AlterField(Tournament.format)` →
  `AlterField(BracketNode.bracket_type)` → `AddField(Tournament.swiss_rounds)`,
  no `RunPython`.
- **EXACT literals:** even-N error `"Swiss requires an even number of
  participants."`; format value `"swiss"`; label `"Swiss"`;
  `swiss_rounds` default `0` (auto); round-count formula
  `swiss_rounds or math.ceil(math.log2(N))` clamped `[1, N-1]`.
