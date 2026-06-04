# LG-02c Round Robin Tournament — Seam Contract

A new `Tournament.format` value `"round_robin"`: a flat double round-robin where
every enrolled Team plays every other twice (one fixture per leg), NO advancement,
champion = Standings leader after every node is resolved. Builds on the existing
LG-02a/b/c sandbox Tournament model (`Tournament` / `TournamentParticipant` /
`BracketNode` / `SeriesMatch`) in `laserforce_simulator/matches/`.

This artifact is the single source of truth the parallel Code / Tests / Docs
agents share. Every name, signature, dict shape, literal, DOM id, and migration
coordinate below is **LOCKED** — do not rename or re-shape.

---

## 1. Enum / choice string literals

- **`Tournament.format`** gains a third choice. `Tournament.FORMAT_CHOICES`
  becomes (in this order):
  ```python
  FORMAT_CHOICES = (
      ("single_elimination", "Single elimination"),
      ("double_elimination", "Double elimination"),
      ("round_robin", "Round robin"),
  )
  ```
  Display label is the exact string `"Round robin"`. `format` field declaration
  is otherwise unchanged (`CharField(max_length=32, default="single_elimination")`).

- **`BracketNode.bracket_type`** gains a fourth choice. Its `choices` tuple
  becomes (in this order):
  ```python
  choices=(
      ("winners", "Winners bracket"),
      ("losers", "Losers bracket"),
      ("grand_final", "Grand final"),
      ("round_robin", "Round robin"),
  )
  ```
  Field declaration is otherwise unchanged (`CharField(max_length=12,
  default="winners")` — `"round_robin"` is 11 chars, fits `max_length=12`).

- **`matches/bracket.py::_BRACKET_RANK`** gains the entry:
  ```python
  _BRACKET_RANK = {"winners": 0, "losers": 1, "grand_final": 2, "round_robin": 3}
  ```
  Rank 3 is purely a deterministic tiebreak inside `find_next_node`'s sort; RR
  nodes never coexist with WB/LB/GF nodes in one Tournament, so the absolute rank
  value is cosmetic — but the entry is **required** so `_BRACKET_RANK.get(...)`
  never falls back to the `0` default for an RR node (defence in depth, asserted
  in the pure test).

---

## 2. Structure (how RR maps onto `BracketNode`)

A round-robin Tournament is a **flat set of `BracketNode` rows** — one node per
fixture from the FULL output of
`matches/schedule_generator.py::generate_schedule(team_ids)` (the full output IS a
double round-robin: each pair appears twice, once per `round_number` leg).

`generate_schedule(team_ids, schedule_format="single_round_robin")` returns a
`list[ScheduleFixture]` (verified in `matches/schedule_generator.py`). Each
`ScheduleFixture` is the frozen dataclass:
```python
@dataclass(frozen=True)
class ScheduleFixture:
    matchday: int        # 1-based
    round_number: int    # 1 or 2  (leg)
    team_a_id: int       # min of the pair
    team_b_id: int       # max of the pair
```
The output is sorted by `(matchday, team_a_id)` and is a function of the *set* of
`team_ids` (the function sorts ascending internally). For N=4 it yields 12
fixtures (6 per leg); N=8 → 56; odd N drops the bye sentinel `-1`.

**One `BracketNode` per fixture.** The full kwarg set for an RR node created in
`lock_and_build` (locked — these are the exact `BracketNode.objects.create(...)`
kwargs):

| kwarg                     | value                                            |
|---------------------------|--------------------------------------------------|
| `tournament`              | `self`                                           |
| `bracket_round`           | `fixture.matchday` (1-based)                      |
| `position`                | 0-based index of the fixture within its matchday  |
| `bracket_type`            | `"round_robin"`                                  |
| `team_a`                  | `team_by_id[fixture.team_a_id]` (FIXED at lock)   |
| `team_b`                  | `team_by_id[fixture.team_b_id]` (FIXED at lock)   |
| `seed_a`                  | the participant seed of `team_a_id`               |
| `seed_b`                  | the participant seed of `team_b_id`               |
| `is_bye`                  | `False`                                          |
| `advances_to_slot`        | `None`                                           |
| `loser_advances_to_slot`  | `None`                                           |
| `winner`                  | `None`                                           |
| `series_length`           | `1` (Bo1 — RR is always best-of-1)                |

After create, the FK pointers `advances_to` and `loser_advances_to` are left
unset (both `None`) — RR nodes never advance. Do NOT run the LG-02a/c
`advances_to` / `loser_advances_to` wiring passes or `resolve_bye_chain` for the
RR branch.

- `position` is the **0-based index within that matchday**: iterate the fixtures
  grouped by `matchday` in `generate_schedule` order and enumerate each group from
  0. (A node's identity is therefore the `(bracket_round=matchday,
  position=index-in-matchday)` pair, unique within `bracket_type="round_robin"` —
  satisfies the existing `uniq_tournament_bracket_round_position` constraint.)
- `seed_a` / `seed_b`: build a `seed_by_team_id` map from
  `self.participants.all()` (`{p.team_id: p.seed}`) so the fixed slots carry their
  Bracket seed without a re-query, mirroring the elim builders.

---

## 3. Build location — `Tournament.lock_and_build()` third branch

The fixtures→`BracketNode` build lives in the **MODEL layer** inside
`Tournament.lock_and_build()` (`matches/models.py`) as a third `format` branch,
alongside the existing single/double-elim branches. **No new builder function is
added to `matches/bracket.py`** — its frozen import allowlist
(`dataclasses`/`typing`/`math`/`collections` only) stays untouched, and
`generate_schedule` (which lives in the also-frozen `schedule_generator.py`) is
**deferred-imported inside `lock_and_build`**, joining the existing
`from .bracket import (...)` deferred-import block:
```python
from .schedule_generator import generate_schedule
```

Branch shape (the existing `>= 4` participant guard and `state != "setup"` guard
PRECEDE this dispatch and are unchanged; the `self.state = "active"` +
`self.save(update_fields=["state"])` tail is shared and unchanged):

```python
if self.format == "round_robin":
    team_ids = [p.team_id for p in participants]
    fixtures = generate_schedule(team_ids)            # full double RR
    seed_by_team = {p.team_id: p.seed for p in participants}
    team_by_id = {p.team_id: p.team for p in participants}
    # position = 0-based index within each matchday, in generate_schedule order
    pos_by_matchday = {}
    for fixture in fixtures:
        pos = pos_by_matchday.get(fixture.matchday, 0)
        BracketNode.objects.create(
            tournament=self,
            bracket_round=fixture.matchday,
            position=pos,
            bracket_type="round_robin",
            team_a=team_by_id[fixture.team_a_id],
            team_b=team_by_id[fixture.team_b_id],
            seed_a=seed_by_team[fixture.team_a_id],
            seed_b=seed_by_team[fixture.team_b_id],
            is_bye=False,
            advances_to_slot=None,
            loser_advances_to_slot=None,
            winner=None,
            series_length=1,
        )
        pos_by_matchday[fixture.matchday] = pos + 1
    # NO advances_to / loser_advances_to wiring pass, NO resolve_bye_chain.
elif self.format == "double_elimination":
    ...   # existing branch, unchanged
else:
    ...   # existing single-elim branch, unchanged
```

`generate_schedule` raises `ValueError` on `len(team_ids) < 2`; the pre-existing
`>= 4` participant guard in `lock_and_build` already prevents that, so no extra
guard is needed (RR still requires ≥ 4 participants, same as elim).

---

## 4. Champion + standings — REUSE `matches/standings.py::compute_standings`

### 4.1 Verified `compute_standings` signature & shapes (quoted from `matches/standings.py`)

```python
def compute_standings(
    completed_matches: list,
    enrolled_teams: list,
    season_rounds: list | None = None,
) -> list:   # -> list[StandingsRow]
```

**`completed_matches`** — list of dicts, **9 keys** (LG-06g), one per completed
Match:
```
match_id, team_red_id, team_blue_id, winner_team_id (int | None — None = tie),
red_rounds_won, blue_rounds_won, red_total_points, blue_total_points, date_played
```
`date_played` is read via `m.get("date_played", 0)` — optional; supplying it
orders streak/L5 chronologically, omitting it falls to `match_id` order.

**`enrolled_teams`** — list of `(team_id, team_name)` tuples (every enrolled
team; teams with no matches get a zero-filled row; the `team_name` drives the
alphabetical final tiebreak).

**`season_rounds`** — list of dicts, **6 keys**, one per persisted Round
(includes Rounds of in-progress Matches):
```
round_id, team_red_id, team_blue_id, red_points, blue_points, date_played
```
`date_played` here is **required** (sorted via `(r["date_played"],
r["round_id"])`, no `.get`). Optional param overall — defaults to `[]`. The
side-split / round-grain columns come back zeroed when `[]` is passed.

**Sort ladder** (built into `compute_standings`): `league_points desc`,
`round_wins desc`, `total_score desc`, `team_name asc`. `rank` is 1-based, dense,
in iteration order. `league_points = 3*wins + 1*ties`.

**`StandingsRow`** — frozen dataclass, **17 fields, pinned order** (LG-06g):
```
team_id, matches_played, wins, losses, ties, league_points, round_wins,
total_score, rank, match_streak, match_l5, round_streak, round_l5, red_wlt,
blue_wlt, red_points_for, blue_points_for
```
`team_id` (int), `rank` (int), `match_streak`/`round_streak` are
`tuple[str, int]`, the `*_l5` / `*_wlt` are `tuple[int, int, int]`,
`red_points_for`/`blue_points_for` ints.

**Tiebreak note:** `compute_standings`' final tiebreak is `team_name asc`. The RR
champion = `round_robin_standings()[0].team_id` uses this built-in tiebreak — **no
seed-aware override** (locked).

### 4.2 NEW `Tournament` methods (in `matches/models.py`)

```python
def round_robin_standings(self) -> list["StandingsRow"]:
    ...

def complete_round_robin_if_finished(self) -> None:
    ...
```

Deferred-import `from .standings import compute_standings` inside whichever method
uses it (mirrors the `from .schedule_generator import generate_schedule` deferred
import; `standings.py` is itself a frozen pure module).

#### `round_robin_standings(self) -> list[StandingsRow]`

Builds the three `compute_standings` seam inputs from this Tournament's RR nodes
and returns the ranked rows. Used by BOTH the engine (champion) and the detail
view (standings table). Algorithm (locked):

1. `participants = list(self.participants.select_related("team"))`;
   `enrolled_teams = [(p.team_id, p.team.name) for p in participants]`.
2. Gather the RR nodes:
   `nodes = list(self.nodes.filter(bracket_type="round_robin")
   .select_related("team_a", "team_b").prefetch_related("series_matches__match__game_rounds"))`
   (or whatever prefetch the Code agent finds minimal — the **shape** of the
   inputs below is what is pinned, not the query).
3. **`completed_matches`** — one 9-key dict per RR node whose `winner_id is not
   None` (i.e. its single Bo1 `SeriesMatch` has been played). For each such node
   resolve its played `Match` via its `SeriesMatch` row
   (`node.series_matches.all()[0].match` — RR is Bo1, so exactly one
   `SeriesMatch` once played). Build:
   - `match_id` = the `Match.id`
   - `team_red_id` / `team_blue_id` = the played `Match.team_red_id` /
     `team_blue_id` (the ACTUAL physical sides the Match was simulated with — the
     node's `team_a` plays red, `team_b` plays blue per `simulate_match`, but read
     from the persisted `Match` to be side-faithful)
   - `winner_team_id` = `match.winner_id` (the `Match.winner` is the decisive
     Team; for the `match.winner is None` true-tie case the engine's `break_tie`
     already stamped `node.winner` — pass `node.winner_id` so the standings count a
     decisive result rather than a tie). **Locked rule:** `winner_team_id =
     node.winner_id` (the node winner, which equals `match.winner_id` on a clean
     win and the `break_tie` result on a true tie — never `None` for a resolved RR
     node).
   - `red_rounds_won` / `blue_rounds_won` = `match.red_rounds_won` /
     `blue_rounds_won` (the existing `Match` properties/fields used everywhere)
   - `red_total_points` / `blue_total_points` = `match.red_total_points` /
     `blue_total_points` (the existing team-elim-bonus-inclusive properties)
   - `date_played` = `match.date_played` (or the `Match` timestamp field; pass it
     through for chronological streak ordering)
4. **`season_rounds`** — one 6-key dict per persisted `GameRound` of each played
   RR node's `Match`. For each `GameRound gr`:
   `round_id=gr.id`, `team_red_id=gr.team_red_id`, `team_blue_id=gr.team_blue_id`,
   `red_points=gr.red_points`, `blue_points=gr.blue_points`,
   `date_played=<gr timestamp / match.date_played>`. (`GameRound.team_red` is the
   team that PHYSICALLY played red — SIM-08 — which is exactly what the side-split
   columns key on.)
5. `return compute_standings(completed_matches, enrolled_teams, season_rounds)`.

The method must return rows for **every enrolled team** (zero-filled before any
node is played) — `enrolled_teams` guarantees that via `compute_standings`.

#### `complete_round_robin_if_finished(self) -> None`

Parallel to `Season.complete_if_finished` (LG-01). Idempotent. Algorithm (locked):

1. No-op unless `self.format == "round_robin"` and `self.state == "active"`.
2. Consider every RR node:
   `nodes = self.nodes.filter(bracket_type="round_robin")`. The RR is finished iff
   **every** node has `winner_id is not None` (every fixture resolved). (RR nodes
   are never `is_bye`, so no bye exclusion is needed.)
3. If not all resolved → return (no-op).
4. If all resolved:
   ```python
   rows = self.round_robin_standings()
   self.champion_id = rows[0].team_id     # Standings leader; compute_standings
                                          # tiebreak is team_name asc (no override)
   self.state = "completed"
   self.save(update_fields=["champion", "state"])
   ```
   Guard `if rows:` defensively before indexing `rows[0]` (a ≥4-participant RR
   always has ≥4 rows, but the guard mirrors `Season.complete_if_finished`).

---

## 5. Engine — `matches/tournament_engine.py::play_next_node`

`play_next_node(tournament: Tournament) -> BracketNode | None` (`@transaction.atomic`)
gets an RR guard in its **clinch tail**. The body THROUGH the clinch check is
unchanged for all formats: find next playable node → sim ONE Match
(`BatchSimulator().simulate_match(node.team_a, node.team_b,
match_type="tournament")`) → `break_tie` fallback on `match.winner is None`
(mapping the winning seed back to `node.team_a` / `node.team_b`) →
`SeriesMatch.objects.create(node=node, match=match, game_number=<count+1>,
winner=match_winner)` → recompute via `count_series_wins` → `slot =
series_winner_slot(wins_a, wins_b, node.series_length)`. For an RR node
`series_length == 1`, so `series_winner_slot(1, 0, 1) == "a"` (or `"b"`) — the
node clinches on its single Match, exactly like a Bo1 elim node.

**On clinch, branch on `tournament.format == "round_robin"`** (locked):

```python
# 7. Series clinched -> stamp node.winner (winner_team / winner_seed resolved as
#    today from slot "a"/"b").
node.winner = winner_team
node.save(update_fields=["winner"])

if tournament.format == "round_robin":
    # RR: every node has advances_to=None, so the elim "crown on advances_to is
    # None" rule would wrongly crown on the FIRST resolved node. SKIP the
    # advance_winner / advance_loser / crown-on-None block entirely. Champion +
    # completion are decided by complete_round_robin_if_finished after ALL nodes.
    tournament.complete_round_robin_if_finished()
    return node

# ... existing elim tail (flatten, advance_winner, advance_loser, GF reset,
#     crown-on-advances_to-None) runs only for non-RR formats ...
```

The RR guard must be placed AFTER the `node.winner` stamp and BEFORE the
`_node_to_dict` flatten / `advance_winner` / `advance_loser` / final-node crown
block, and must `return node` so none of that elim logic executes.

**`find_next_node` is UNCHANGED.** Verified in `matches/bracket.py`: its playable
predicate is `team_a_id is not None and team_b_id is not None and not is_bye and
series_winner_slot(wins_a, wins_b, series_length) is None`. Every RR node has both
slots filled at lock time, `is_bye=False`, and `series_length=1`, so an unplayed
RR node (`wins_a=0, wins_b=0`) is playable and a resolved one
(`series_winner_slot(1,0,1)=="a"`) is skipped. The sort key
`(_BRACKET_RANK[bracket_type], bracket_round, position)` orders RR nodes by
`(3, matchday, position)` — deterministic. No edit to `find_next_node`.

**Callers unchanged:** `tournament_play_next` (sync view), `play_tournament_task`
(the Celery `while play_next_node(...) is not None` loop drains every RR node one
Match at a time), `tournament_play_all`, `tournament_play_status` keep their URLs
+ the 5-key status JSON. `stage_progress` (unchanged) reports per-`(bracket_type,
bracket_round)` group completion — for RR that is per-matchday progress, which is
a sensible "stages" readout for the Play-All progress bar.

---

## 6. Migration

**`matches/migrations/0037_tournament_round_robin.py`** (next sequential after the
latest existing migration `0036_bracketnode_double_elimination.py`).

```python
dependencies = [("matches", "0036_bracketnode_double_elimination")]
```

Two `AlterField` operations (choices-widen only — no DB-level enforcement of
choices in SQLite/Postgres, but included so `makemigrations --check --dry-run` is
clean), **no `RunPython`, no backfill** (ADR-0004 disposable-sandbox precedent):

1. `AlterField(model_name="tournament", name="format", field=models.CharField(
   choices=[("single_elimination","Single elimination"),
   ("double_elimination","Double elimination"), ("round_robin","Round robin")],
   default="single_elimination", max_length=32))`
2. `AlterField(model_name="bracketnode", name="bracket_type",
   field=models.CharField(choices=[("winners","Winners bracket"),
   ("losers","Losers bracket"), ("grand_final","Grand final"),
   ("round_robin","Round robin")], default="winners", max_length=12))`

---

## 7. Detail page (view + template)

`tournament_detail` (in `matches/tournament_views.py`) and the template
`templates/matches/tournament_detail.html` branch on `tournament.format`.

### 7.1 Context shape (how RR data rides the existing `_detail_context`)

The current `_detail_context(tournament)` (verified) returns the LG-02a/02a-2 keys
`tournament, participants, rounds, next_node, is_locked, can_play, import_form,
import_row_errors`, where `rounds = _build_rounds(tournament)` is the **3-key
dict** `{"winners": [...], "losers": [...], "grand_final": [...]}`.

**Locked decision — RR carries data WITHOUT breaking the elim 3-key `rounds`
shape:** `_build_rounds` keeps returning its dict; for an RR Tournament the three
elim keys are empty lists. RR adds **two NEW top-level context keys**, populated
only in the RR branch (and absent / empty for elim):

- **`rr_crosstable`** — the N×N crosstable (see 7.2). A `list[dict]` of row
  descriptors (one per team, in standings order), each
  `{"team": <Team>, "cells": [<cell>, ...]}` where `cells` is the N-long row of
  per-opponent cells in the same team order. Each `<cell>` is either `None`
  (diagonal — team vs itself, rendered blank) or a dict
  `{"opponent_team_id": int, "leg1": <leg_dict|None>, "leg2": <leg_dict|None>}`
  where a `<leg_dict>` is `{"node_id": int, "team_score": int|None,
  "opp_score": int|None, "played": bool, "match_id": int|None}` from this row
  team's perspective. (The Code agent may flatten this differently as long as the
  template can render the locked DOM ids in 7.3 and the leg→cell mapping rule in
  7.2 is honoured — the **mapping rule** is the load-bearing lock, not the precise
  nesting.)
- **`rr_standings`** — `list[StandingsRow]` from `tournament.round_robin_standings()`
  (the live standings table; works at any state — zero-filled in `setup`/early
  `active`, final once `completed`).

`_build_rounds` is NOT extended to carry the crosstable — keep it the 3-key elim
dict so existing elim tests stay green. The RR crosstable + standings are built in
a separate helper (suggested: `_build_rr_crosstable(tournament)` and a direct
`tournament.round_robin_standings()` call) and merged into the context only when
`tournament.format == "round_robin"`. `_detail_context` adds both keys with safe
defaults (`rr_crosstable=[]`, `rr_standings=[]`) for elim so the template can
reference them unconditionally.

### 7.2 Crosstable cell-mapping rule (LOCKED, precise)

The crosstable is N×N indexed `cell[row_team][col_team]`. Each RR fixture node has
two legs (two nodes — `round_number==1` and `round_number==2` from
`generate_schedule`). The node carries `team_a` (= `min(pair)` id) and `team_b`
(= `max(pair)` id) FIXED at lock time. The mapping:

- **Leg with `round_number == 1`** → fills `cell[team_a][team_b]` (team_a is the
  home/row team for leg 1).
- **Leg with `round_number == 2`** → fills `cell[team_b][team_a]` (team_b is the
  home/row team for leg 2 — the reverse fixture).
- **Diagonal** `cell[t][t]` → always blank (`None`).

Because `generate_schedule` does not persist `round_number` onto the `BracketNode`
(the node only stores `bracket_round=matchday` / `position`), the **view must
recover each leg's `round_number`** by re-deriving the schedule:
`fixtures = generate_schedule(team_ids)` and matching each persisted RR node back
to its fixture by `(matchday, position-within-matchday)` (the exact key the
builder used in §3), reading `round_number` off the matched `ScheduleFixture`. The
view holds `team_ids` from `tournament.participants` (or
`starting_team_ids_json`-equivalent — there is none for a sandbox Tournament, so
use the participant team-ids). Build the crosstable from this leg→cell mapping.

Each filled cell shows the leg's score from the **row team's** perspective
(row-team score – opponent score) and links to the played Match when
`match_id is not None`; an unplayed leg renders empty / "—".

### 7.3 Template branch + DOM ids

`templates/matches/tournament_detail.html` branches on `tournament.format`. Locked
NEW DOM ids (RR section only):

- **`tournament-rr-crosstable`** — the outer `<table>` rendering the N×N
  crosstable (owned by the RR template branch).
- **`tournament-rr-standings`** — the outer `<table>` rendering the live
  `rr_standings` table (owned by the RR template branch).

The RR branch renders ONLY these two tables plus the reused play controls; the
elim WB/LB/GF section containers (`tournament-bracket*`, single-elim
`tournament-bracket` / `tournament-node-*`) are **absent** for an RR Tournament,
and conversely the two RR ids are absent for elim. **Reuse VERBATIM** (no new ids,
shared across all formats): the lock control (`tournament-lock-form` /
`tournament-lock-submit`), play-next (`tournament-play-next-form` /
`tournament-play-next-submit`), play-all (`tournament-play-all-form` /
`-submit` / `-progress` + the inline poll JS), the import + seeding forms, and the
champion banner `tournament-champion-banner` (substring `Champion`, rendered when
`tournament.champion` is set — the RR completion path stamps it identically).

Series-length create-form selects are **hidden when `format == "round_robin"`**
(see §8) — and RR nodes are always Bo1, so the detail page renders no per-node
series-score / Bo-N labels for RR.

---

## 8. Create form

The create form's `format` select **(DOM id `tournament-create-format`, verified
present in `tournament_create.html` per LG-02c)** gains a `"Round robin"` option
(value `"round_robin"`, label `"Round robin"`), alongside the existing
`single_elimination` / `double_elimination` options.

`tournament_create` (view) parsing (verified — the existing forgiving-fallback
block):
```python
tournament_format = request.POST.get("format")
if tournament_format not in ("single_elimination", "double_elimination"):
    tournament_format = "single_elimination"
```
becomes:
```python
tournament_format = request.POST.get("format")
if tournament_format not in (
    "single_elimination", "double_elimination", "round_robin"
):
    tournament_format = "single_elimination"
```
(forgiving fallback to `single_elimination` on a tampered / absent value — the
existing pattern, extended). `Tournament.objects.create(..., format=tournament_format,
...)` is unchanged otherwise; the four `*_series_length` fields are still parsed
and passed (they default to `1` and are simply unused by RR — RR forces Bo1 at
the node level, so the slot values are inert for RR).

The four series-length selects (`tournament-create-final-series-length` /
`-semifinal-` / `-quarterfinal-` / `-earlier-series-length`) are **hidden via a
client-side affordance when the format select reads `round_robin`** (a small
inline `onchange` toggle in `tournament_create.html` — the Code agent's
discretion on exact JS; only the behaviour is pinned: RR hides the four selects,
elim shows them). No server-side change is required for hiding — the inert values
do no harm.

---

## 9. Determinism / scope

- **Non-deterministic per-Match sims** — `simulate_match` draws fresh per-round
  seeds, so RR Tournament games are NOT master-seed-replayable: **no SIM-07 /
  SIM-08 interaction, NO Score Calibration re-baseline.**
- **No new pure builder, no `bracket.py` import-allowlist change** (only
  `_BRACKET_RANK` gains an entry — a pure dict literal edit).
- **No `simulate_match` / `simulate_scheduled_round` change** (consumed verbatim,
  `arena_map=None` 3-zone fallback per node).
- **No backfill / `RunPython`.** **No ADR** (decisions reversible — a choices
  widen + two `Tournament` methods + a deferred import). **No new CONTEXT.md term**
  (RR reuses **Tournament** / **Bracket node** / **Standings** vocabulary).
- **In-League / in-Season embedding is out of scope** — the RR Tournament stays
  standalone and `season`-less, exactly like LG-02a/b/c.

---

## 10. Test boundary (pure-unit vs DB-level) + file → class mapping

Mirror the existing tournament test-class naming conventions
(`Test<Subject><Behaviour>`). NEVER assert on exact simulated point totals
(non-deterministic) — assert on pure functions, persisted node/row shapes,
`node.winner` / `tournament.champion` / `tournament.state`, standings ordering,
and DOM ids.

### Pure-unit (no DB) — `matches/tests/test_bracket.py` (EXTEND)

- **`TestBracketRankRoundRobin`** — `_BRACKET_RANK["round_robin"] == 3`; a flat
  list of RR-only node dicts (`bracket_type="round_robin"`, both slots filled,
  `is_bye=False`, `series_length=1`, varying `wins_a`/`wins_b`) ordered by
  `find_next_node` returns the lowest `(matchday, position)` UNPLAYED node and
  skips clinched ones; `TestNoDjangoImportsLeaked` still green (no new import
  added to `bracket.py`).

`compute_standings` itself is already covered by `matches/tests/test_standings.py`
(pure-unit, LG-06g) — no new pure standings tests are required for RR (the RR
methods only *assemble* the seam dicts; the assembly is DB-level).

### DB-level — Django `TestCase`

- **`matches/tests/test_tournament_models.py` (EXTEND):**
  - `TestTournamentRoundRobinFormat` — `Tournament.format` accepts/persists
    `"round_robin"`; `BracketNode.bracket_type` accepts `"round_robin"`.
  - `TestLockAndBuildRoundRobin` — `lock_and_build` on an N=4 RR Tournament
    builds 12 `BracketNode` rows all `bracket_type="round_robin"`, all
    `series_length=1`, all `advances_to_id is None` / `loser_advances_to_id is
    None` / `is_bye=False`, `team_a`/`team_b` fixed, `seed_a`/`seed_b` populated;
    every unordered pair appears exactly twice; `position` is 0-based within each
    `bracket_round` (matchday); `state` flips to `"active"`; N=6 yields the
    expected double-RR node count.
  - `TestRoundRobinStandings` — `round_robin_standings()` returns one
    `StandingsRow` per enrolled team (zero-filled before any play), ranked by the
    `compute_standings` ladder; after hand-stamping a node's `winner` + a played
    `SeriesMatch`/`Match`/`GameRound`, the standings reflect the win (assert on
    `wins`/`league_points`/`rank` ORDER, never on exact points).
  - `TestCompleteRoundRobinIfFinished` — no-op when not all nodes resolved
    (`state` stays `"active"`, `champion` stays `None`); when EVERY node has
    `winner` set, `state` flips to `"completed"` and `champion ==
    round_robin_standings()[0].team_id`; idempotent on a second call; no-op for a
    non-`round_robin` format / non-`active` state.

- **`matches/tests/test_tournament_engine.py` (EXTEND):**
  - `TestPlayNextNodeRoundRobinNoEarlyCrown` — on a locked RR Tournament, the
    FIRST `play_next_node` resolves one node (stamps `node.winner`, writes one
    `SeriesMatch`) and **does NOT** crown a champion or complete the Tournament
    (`tournament.champion is None`, `state == "active"`) despite every RR node
    having `advances_to is None` — the elim crown-on-None rule must be skipped.
  - `TestPlayNextNodeRoundRobinCompletes` — draining `play_next_node` until it
    returns `None` resolves every node and exactly then stamps `champion`
    (= standings leader) + `state == "completed"`; no node ever gets an
    `advance_winner` mutation (RR nodes' parents stay empty).

- **`matches/tests/test_tournament_views.py` (EXTEND):**
  - `TestCreateFormRoundRobin` — the `format` select (`tournament-create-format`)
    offers a `round_robin` option; a POST with `format=round_robin` persists a
    Tournament with `format == "round_robin"`; a tampered/absent `format` falls
    back to `single_elimination`.
  - `TestDetailRoundRobinCrosstable` — an RR detail page renders
    `tournament-rr-crosstable` (N×N: a leg `round_number==1` lands in
    `cell[team_a][team_b]`, leg `round_number==2` in `cell[team_b][team_a]`,
    diagonal blank) and `tournament-rr-standings`; the elim section containers are
    absent; the shared lock / play-next / play-all controls + champion banner
    render; the four series-length selects are hidden for RR.

- **`matches/tests/test_tournament_tasks.py` (EXTEND):**
  - `TestPlayTournamentTaskRoundRobin` — under `CELERY_TASK_ALWAYS_EAGER`,
    `play_tournament_task` drains an RR Tournament to completion (every node
    resolved, `champion` stamped, `state == "completed"`); `stage_progress`
    reports per-matchday stage counts during the drain.

### Migration sanity

`python laserforce_simulator/manage.py makemigrations --check --dry-run` is clean
after `0037_tournament_round_robin.py` lands (the two `AlterField`s capture the
choices widen).

---

## 11. Locked-name index (quick scan)

- Enum literals: `Tournament.format == "round_robin"` (label `"Round robin"`);
  `BracketNode.bracket_type == "round_robin"` (label `"Round robin"`);
  `_BRACKET_RANK["round_robin"] = 3`.
- Model methods: `Tournament.round_robin_standings(self) -> list[StandingsRow]`;
  `Tournament.complete_round_robin_if_finished(self) -> None`.
- Build: third `format` branch in `Tournament.lock_and_build()`; deferred import
  `from .schedule_generator import generate_schedule`; RR node kwarg set per §2
  (`bracket_type="round_robin"`, `series_length=1`, `advances_to`/`loser_advances_to`
  left `None`, `is_bye=False`, `bracket_round=matchday`, `position=index-in-matchday`).
- Engine: `play_next_node` RR guard (`if tournament.format == "round_robin":`
  stamp winner → `complete_round_robin_if_finished()` → `return node`, skipping the
  elim advance/crown block). `find_next_node` UNCHANGED.
- Reused pure seam: `compute_standings(completed_matches, enrolled_teams,
  season_rounds)` — 9-key match dict / 6-key season_rounds dict / `(id, name)`
  enrolled tuple / 17-field `StandingsRow`.
- Migration: `matches/migrations/0037_tournament_round_robin.py`, dep
  `("matches", "0036_bracketnode_double_elimination")`, two `AlterField`s, no
  `RunPython`.
- DOM ids (NEW): `tournament-rr-crosstable`, `tournament-rr-standings`. Reused
  verbatim: `tournament-create-format`, `tournament-champion-banner`,
  `tournament-lock-*`, `tournament-play-next-*`, `tournament-play-all-*`.
- Context keys (NEW): `rr_crosstable`, `rr_standings` (added to `_detail_context`,
  empty for elim). Existing `rounds` 3-key dict shape unchanged.
- Crosstable rule: leg `round_number==1` → `cell[team_a][team_b]`; leg
  `round_number==2` → `cell[team_b][team_a]`; diagonal blank.
- Tests: `test_bracket.py::TestBracketRankRoundRobin`;
  `test_tournament_models.py::{TestTournamentRoundRobinFormat,
  TestLockAndBuildRoundRobin, TestRoundRobinStandings,
  TestCompleteRoundRobinIfFinished}`;
  `test_tournament_engine.py::{TestPlayNextNodeRoundRobinNoEarlyCrown,
  TestPlayNextNodeRoundRobinCompletes}`;
  `test_tournament_views.py::{TestCreateFormRoundRobin,
  TestDetailRoundRobinCrosstable}`;
  `test_tournament_tasks.py::TestPlayTournamentTaskRoundRobin`.
