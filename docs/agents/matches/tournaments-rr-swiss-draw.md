# matches: Tournaments: round robin, Swiss, random draw

LG-02c round robin, round robin -> double elimination, Swiss, and LG-02x-1 random draw player pool
(renamed **Player-pool draw** by ANG-01f; see the ANG-01f section at the end).
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-02c round robin tournaments

Adds a third `Tournament.format` enum value **`"round_robin"`** (label
`"Round robin"`): a **flat double round-robin** where every enrolled Team plays
every other **twice** (one fixture per leg), with **NO advancement** — the
champion is simply the **Standings** leader once every node is resolved. Unlike
the elim formats, RR has no bracket tree: it is a flat set of `BracketNode` rows
**with no `advances_to` / `loser_advances_to` edges**. It reuses three existing
pure seams verbatim — `matches/schedule_generator.py::generate_schedule` (the
fixture list), `matches/standings.py::compute_standings` (the ranked table), and
the LG-02b `SeriesMatch` clinch engine (Bo1 per fixture) — so it ships as a
**choices widen + two `Tournament` methods + one engine guard + a `_BRACKET_RANK`
entry**, with **no new pure builder** and **no `matches/bracket.py` import-allowlist
change**. The single- and double-elim paths are **byte-unchanged**. See
[ADR-0021](../../adr/0021-double-elimination-bracket.md) (extended — its
Consequences set the "new format = new enum value + reused/new pure seam"
precedent, and the round-robin extension was finalised at grilling time, not
re-touched here); seam contract:
[`.claude/worktrees/lg-02c-round-robin-seam-contract.md`](../../../.claude/worktrees/lg-02c-round-robin-seam-contract.md).
**No new GLOSSARY.md term** — RR reuses **Tournament** / **Bracket node** /
**Standings** vocabulary. Migration:
`matches/migrations/0037_tournament_round_robin.py` (dep
`0036_bracketnode_double_elimination`; two `AlterField`s — `Tournament.format`
choices widen + `BracketNode.bracket_type` choices widen, choices-only, no DB-level
enforcement, included so `makemigrations --check` is clean — **no `RunPython`, no
backfill**, ADR-0004 disposable-sandbox precedent).

**Enum / `_BRACKET_RANK` literals (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("round_robin", "Round robin")` as its **third** entry (field declaration
unchanged — `CharField(max_length=32)`, `default="single_elimination"`).
`BracketNode.bracket_type` choices gain `("round_robin", "Round robin")` as their
**fourth** entry (`"round_robin"` is 11 chars, fits the existing `max_length=12`;
field declaration otherwise unchanged, `default="winners"`).
`matches/bracket.py::_BRACKET_RANK` gains **`"round_robin": 3`** (rank 3) — RR
nodes never coexist with WB/LB/GF nodes in one Tournament, so the absolute rank is
cosmetic, but the entry is **required** so `_BRACKET_RANK.get(...)` never falls back
to the `0` default for an RR node (defence in depth, asserted in the pure test). It
is a pure dict-literal edit — **no new import**, `TestNoDjangoImportsLeaked` stays
green.

**Structure — flat `BracketNode` set, no edges.** A round-robin Tournament is
**one `BracketNode` row per fixture** from the FULL output of
`generate_schedule(team_ids)` — that full output **is** a double round-robin (each
unordered pair appears **twice**, once per `round_number` leg `1`/`2`). For N=4 it
yields 12 fixtures (6 per leg); N=6 → 30; N=8 → 56; odd N drops the bye sentinel
`-1`. Each `ScheduleFixture` is the frozen dataclass `(matchday: int 1-based,
round_number: int 1|2, team_a_id: int = min(pair), team_b_id: int = max(pair))`,
sorted `(matchday, team_a_id)` and a pure function of the *set* of `team_ids`.

**`lock_and_build` third branch (`matches/models.py`).** The fixtures→`BracketNode`
build lives in the **MODEL layer** as a third `format` branch alongside the
existing single/double-elim branches — **no new builder is added to
`matches/bracket.py`**; `generate_schedule` (in the also-frozen
`schedule_generator.py`) is **deferred-imported inside `lock_and_build`** (`from
.schedule_generator import generate_schedule`, joining the existing deferred
`from .bracket import (...)` block). The pre-existing `>= 4`-participant guard and
the `state != "setup"` guard **precede** the dispatch unchanged, and the
`state="active"` + `save(update_fields=["state"])` tail is **shared** and
unchanged. The RR branch builds a `team_by_id` map and a `seed_by_team` map from
`self.participants`, calls `generate_schedule(team_ids)`, and creates one node per
fixture with the **LOCKED kwarg set**: `tournament=self`,
**`bracket_round=fixture.matchday`** (1-based), **`position`** = the **0-based
index of the fixture within its matchday** (enumerate each matchday group in
`generate_schedule` order), **`bracket_type="round_robin"`**,
`team_a=team_by_id[fixture.team_a_id]` / `team_b=team_by_id[fixture.team_b_id]`
(both **FIXED at lock**), `seed_a`/`seed_b` from `seed_by_team`, `is_bye=False`,
`advances_to_slot=None`, `loser_advances_to_slot=None`, `winner=None`, and
**`series_length=1`** (Bo1 — RR is always best-of-1). The `(bracket_round=matchday,
position=index-in-matchday)` pair is unique within `bracket_type="round_robin"`, so
it satisfies the existing `uniq_tournament_bracket_round_position` constraint. After
create the FK pointers `advances_to` / `loser_advances_to` are left **unset (both
`None`)** — RR nodes never advance: the branch runs **NO `advances_to` /
`loser_advances_to` wiring pass and NO `resolve_bye_chain`**. (`generate_schedule`
raises `ValueError` on `len < 2`, but the `>= 4` guard already prevents that — no
extra guard needed.)

**Two new `Tournament` methods (`matches/models.py`) — REUSE `compute_standings`.**
Both deferred-import `from .standings import compute_standings` (itself a frozen
pure module). The reused seam (verified, LG-06g):
`compute_standings(completed_matches, enrolled_teams, season_rounds=None) ->
list[StandingsRow]` — `completed_matches` is a list of **9-key** dicts (`match_id,
team_red_id, team_blue_id, winner_team_id` [`int | None`, `None`=tie],
`red_rounds_won, blue_rounds_won, red_total_points, blue_total_points,
date_played`), `enrolled_teams` is a list of `(team_id, team_name)` tuples (every
enrolled team — teams with no matches get a zero-filled row), `season_rounds` is a
list of **6-key** dicts (`round_id, team_red_id, team_blue_id, red_points,
blue_points, date_played`); it returns the **17-field** frozen `StandingsRow`
(`team_id, matches_played, wins, losses, ties, league_points, round_wins,
total_score, rank, match_streak, match_l5, round_streak, round_l5, red_wlt,
blue_wlt, red_points_for, blue_points_for`), sorted `league_points desc → round_wins
desc → total_score desc → team_name asc` with a 1-based dense `rank`
(`league_points = 3*wins + 1*ties`).

- **`round_robin_standings(self) -> list[StandingsRow]`** assembles the three
  `compute_standings` inputs from this Tournament's RR nodes and returns the ranked
  rows — used by BOTH the engine (champion) and the detail view (standings table),
  and returns a row for **every enrolled team** (zero-filled before any play).
  `enrolled_teams` comes from `self.participants.select_related("team")`
  (`(p.team_id, p.team.name)`); `completed_matches` is one 9-key dict per RR node
  whose `winner_id is not None` (its single Bo1 `SeriesMatch` has been played),
  reading `match_id` / side ids / rounds-won / total-points / `date_played` off the
  played `Match` (side-faithful — `team_a` plays red, `team_b` blue per
  `simulate_match`, but read from the persisted `Match`) with the **LOCKED**
  `winner_team_id = node.winner_id` (the node winner — equals `match.winner_id` on a
  clean win and the `break_tie` result on a true tie, **never `None`** for a resolved
  RR node); `season_rounds` is one 6-key dict per persisted `GameRound` of each
  played node's `Match` (`GameRound.team_red` is the team that PHYSICALLY played red
  — SIM-08 — exactly what the side-split columns key on). **No seed-aware tiebreak
  override** — the champion uses `compute_standings`' built-in `team_name asc` final
  tiebreak (locked).
- **`complete_round_robin_if_finished(self) -> None`** parallels
  `Season.complete_if_finished` (LG-01) and is **idempotent**: no-op unless
  `format == "round_robin"` and `state == "active"`; the RR is finished iff **every**
  RR node (`self.nodes.filter(bracket_type="round_robin")`) has `winner_id is not
  None` (RR nodes are never `is_bye`, so no bye exclusion); if all resolved it sets
  `champion_id = round_robin_standings()[0].team_id` (Standings leader), `state =
  "completed"`, `save(update_fields=["champion", "state"])` — with a defensive `if
  rows:` guard before indexing (mirrors `Season.complete_if_finished`).

**Engine — `play_next_node` RR guard (`matches/tournament_engine.py`).**
`play_next_node` stays **ONE** per-Match-atomic loop for all three formats — its
body is **verbatim** through the clinch check (find next playable node → sim ONE
Match via `BatchSimulator().simulate_match(node.team_a, node.team_b,
match_type="tournament")` → `break_tie` fallback on `match.winner is None` →
`SeriesMatch.objects.create(...)` → recompute via `count_series_wins` →
`series_winner_slot(wins_a, wins_b, node.series_length)`; an RR node is
`series_length == 1`, so it clinches on its single Match exactly like a Bo1 elim
node). On clinch, **AFTER** stamping `node.winner` and **BEFORE** the elim
`_node_to_dict` flatten / `advance_winner` / `advance_loser` / crown block, a
**LOCKED guard `if tournament.format == "round_robin":`** runs
`tournament.complete_round_robin_if_finished()` then `return node` — skipping the
elim advance/crown logic entirely. This guard is **required**: because every RR
node has `advances_to=None`, the elim "crown when `advances_to` is `None`" rule
would otherwise wrongly crown a champion on the **first** resolved node; instead the
champion + completion are decided by `complete_round_robin_if_finished` only after
**all** nodes resolve. **`find_next_node` is UNCHANGED** — its playable predicate
(`team_a_id is not None and team_b_id is not None and not is_bye and
series_winner_slot(...) is None`) treats an unplayed RR node (`wins_a=0, wins_b=0`,
both slots filled, `is_bye=False`, `series_length=1`) as playable and a resolved one
as skipped, and its sort key `(_BRACKET_RANK[bracket_type], bracket_round,
position)` orders RR nodes deterministically by `(3, matchday, position)`.
**Callers unchanged:** `tournament_play_next` (sync view), `play_tournament_task`
(its `while play_next_node(...) is not None` loop drains every RR node one Match at
a time), `tournament_play_all`, `tournament_play_status` keep their URLs + the 5-key
status JSON; `stage_progress` (unchanged) reports per-`(bracket_type, bracket_round)`
group completion — for RR that is **per-matchday** progress, a sensible Play-All
stages readout.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the existing `<select name="format">` (DOM id
**`tournament-create-format`**) gains a third option — value **`"round_robin"`**,
label **`"Round robin"`** — alongside single/double-elim; the view's
forgiving-fallback parse widens to accept `"round_robin"` (anything
absent/tampered still falls back to `"single_elimination"`; *ANG-01f: absent still does, a tampered
value is now rejected*), and the four
`*_series_length` POST fields are still parsed/passed but are **inert** for RR
(RR forces Bo1 at the node level). The four series-length selects
(`tournament-create-final-series-length` / `-semifinal-` / `-quarterfinal-` /
`-earlier-series-length`) are **hidden client-side** when the format select reads
`round_robin` (a small inline `onchange` toggle — behaviour pinned, exact JS at the
Code agent's discretion; no server-side change, the inert values do no harm).
Detail page (`tournament_detail` in `matches/tournament_views.py` /
`tournament_detail.html`): `_build_rounds` **keeps its 3-key elim dict**
`{"winners", "losers", "grand_final"}` (all three lists **empty** for an RR
Tournament — existing elim tests stay green), and RR rides on **two NEW top-level
context keys** added to `_detail_context` (defaulted `rr_crosstable=[]` /
`rr_standings=[]` for elim so the template references them unconditionally,
populated only in the RR branch): **`rr_crosstable`** (the N×N crosstable, a
`list[dict]` of per-team row descriptors in standings order — the precise nesting is
the Code agent's discretion, only the cell-mapping rule below and the DOM ids are
load-bearing) and **`rr_standings`** = `tournament.round_robin_standings()` (the
live standings table, zero-filled in `setup`/early `active`, final once
`completed`). The crosstable is built in a separate helper (suggested
`_build_rr_crosstable`), not by overloading `_build_rounds`.

**Crosstable cell-mapping rule (LOCKED).** The crosstable is N×N indexed
`cell[row_team][col_team]`. Each fixture has two legs (two nodes, `round_number==1`
and `round_number==2`); the node carries `team_a` (=`min(pair)` id) / `team_b`
(=`max(pair)` id) fixed at lock. **Leg `round_number == 1` → `cell[team_a][team_b]`**
(team_a is the row/home team for leg 1); **leg `round_number == 2` →
`cell[team_b][team_a]`** (team_b is the row/home team for the reverse fixture);
**diagonal `cell[t][t]` → always blank**. Because `generate_schedule` does **not**
persist `round_number` onto the `BracketNode` (the node only stores
`bracket_round=matchday` / `position`), the **view must recover each leg's
`round_number`** by re-deriving the schedule (`fixtures = generate_schedule(team_ids)`,
matching each persisted RR node to its fixture by the `(matchday,
position-within-matchday)` key the builder used) and reading `round_number` off the
matched `ScheduleFixture`. Each filled cell shows the leg's score from the **row
team's** perspective and links to the played Match; an unplayed leg renders empty /
"—". The RR branch renders **only** the two RR tables — outer `<table>` DOM ids
**`tournament-rr-crosstable`** and **`tournament-rr-standings`** — plus the reused
play controls; the elim WB/LB/GF section containers (`tournament-bracket*`,
single-elim `tournament-node-*`) are **absent** for RR, and conversely the two RR
ids are absent for elim (the template **branches on `tournament.format`**). RR nodes
are always Bo1, so the detail page renders **no** per-node series-score / Bo-N labels
for RR. **Reused VERBATIM** (no new ids, shared across all formats): the lock control
(`tournament-lock-form` / `-submit`), play-next (`tournament-play-next-form` /
`-submit`), play-all (`tournament-play-all-form` / `-submit` / `-progress` + poll JS),
the import + seeding forms, and the champion banner **`tournament-champion-banner`**
(rendered when `tournament.champion` is set — the RR completion path stamps it
identically). The widened `format` / `bracket_type` choices auto-surface in admin —
no `list_display` / inline / registration change.

**Determinism / scope.** **Non-deterministic** per-Match sims — `simulate_match`
draws fresh per-round seeds, so RR Tournament games are **NOT**
master-seed-replayable ⇒ **no SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline**. **No `simulate_match` / `simulate_scheduled_round` change** (consumed
verbatim, `arena_map=None` 3-zone fallback per node). **No new pure builder, no
`bracket.py` import-allowlist change** (only `_BRACKET_RANK` gains a literal entry).
**No backfill / `RunPython`** (ADR-0004). **No new ADR** (decisions reversible — a
choices widen + two `Tournament` methods + a deferred import). **No new GLOSSARY.md
term** (RR reuses Tournament / Bracket node / Standings).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **RR → double-elimination**
(an RR seeding phase feeding the LG-02c DE bracket as a finals stage — still its own
grill) and **Swiss** (pairings-from-standings, ⌈log₂(N)⌉ rounds) both remain NOT
STARTED; **in-League / in-Season embedding** is out of scope — the RR Tournament
stays standalone and `season`-less, exactly like LG-02a/b/c; **deterministic /
master-seed-replayable Series** (`simulate_match` draws fresh per-round seeds);
**home/away side alternation** (sides fixed `team_a` red / `team_b` blue every Match,
LG-02b locked); any new GLOSSARY.md term or new ADR.

**Tests.** `matches/tests/test_bracket.py` (extend — **`TestBracketRankRoundRobin`**:
`_BRACKET_RANK["round_robin"] == 3`, and a flat list of RR-only node dicts ordered by
`find_next_node` returns the lowest `(matchday, position)` UNPLAYED node and skips
clinched ones; `TestNoDjangoImportsLeaked` still green — no new import).
`matches/tests/test_tournament_models.py` (extend —
**`TestTournamentRoundRobinFormat`** `format` / `bracket_type` accept/persist
`"round_robin"`; **`TestLockAndBuildRoundRobin`** N=4 → 12 nodes all
`bracket_type="round_robin"` / `series_length=1` / `advances_to_id` +
`loser_advances_to_id` `None` / `is_bye=False`, every unordered pair twice, `position`
0-based within each matchday, `state="active"`, N=6 node count;
**`TestRoundRobinStandings`** one `StandingsRow` per enrolled team (zero-filled
pre-play), and after hand-stamping a node's `winner` + a played
`SeriesMatch`/`Match`/`GameRound` the standings reflect the win — assert on
`wins`/`league_points`/`rank` ORDER, never exact points;
**`TestCompleteRoundRobinIfFinished`** no-op when not all resolved, flips to
`"completed"` + `champion == round_robin_standings()[0].team_id` when all resolved,
idempotent, no-op for non-`round_robin` / non-`active`). `test_tournament_engine.py`
(extend — **`TestPlayNextNodeRoundRobinNoEarlyCrown`** the first `play_next_node`
resolves one node but does **NOT** crown / complete despite `advances_to is None`;
**`TestPlayNextNodeRoundRobinCompletes`** draining to `None` resolves every node and
exactly then stamps champion + `"completed"`, no node ever gets an `advance_winner`
mutation). `test_tournament_views.py` (extend — **`TestCreateFormRoundRobin`** the
format select offers `round_robin`, a POST persists `format == "round_robin"`, a
tampered/absent value falls back to `single_elimination`;
**`TestDetailRoundRobinCrosstable`** renders `tournament-rr-crosstable` (leg
`round_number==1` in `cell[team_a][team_b]`, leg `2` in `cell[team_b][team_a]`,
diagonal blank) + `tournament-rr-standings`, elim containers absent, shared lock /
play-next / play-all + champion banner render, the four series-length selects hidden).
`test_tournament_tasks.py` (extend — **`TestPlayTournamentTaskRoundRobin`** under
`CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains an RR Tournament to
completion + stamps champion + `"completed"`, `stage_progress` reports per-matchday
stage counts). Tests assert on the pure functions, persisted node/row shapes,
`node.winner` / `tournament.champion` / `tournament.state`, standings ORDER, and the
DOM ids — **never on exact simulated point totals** (non-deterministic).
`compute_standings` itself is already covered pure-unit by `test_standings.py`
(LG-06g) — no new pure standings tests for RR (the RR methods only *assemble* the
seam dicts; that assembly is DB-level).

**Locked names (quick index).** Enum: `Tournament.format == "round_robin"` (label
`"Round robin"`); `BracketNode.bracket_type == "round_robin"` (label `"Round
robin"`); `_BRACKET_RANK["round_robin"] = 3`. Model methods:
`Tournament.round_robin_standings(self) -> list[StandingsRow]`;
`Tournament.complete_round_robin_if_finished(self) -> None`. Build: third `format`
branch in `Tournament.lock_and_build()`; deferred import `from .schedule_generator
import generate_schedule`; RR node kwarg set (`bracket_type="round_robin"`,
`series_length=1`, `advances_to`/`loser_advances_to` left `None`, `is_bye=False`,
`bracket_round=matchday`, `position=index-in-matchday`). Engine: `play_next_node` RR
guard (`if tournament.format == "round_robin":` stamp winner →
`complete_round_robin_if_finished()` → `return node`); `find_next_node` UNCHANGED.
Reused pure seam: `compute_standings(completed_matches, enrolled_teams,
season_rounds)` — 9-key match dict / 6-key season_rounds dict / `(id, name)` enrolled
tuple / 17-field `StandingsRow`. Migration:
`matches/migrations/0037_tournament_round_robin.py`, dep
`0036_bracketnode_double_elimination`, two `AlterField`s, no `RunPython`. NEW DOM
ids: `tournament-rr-crosstable`, `tournament-rr-standings`. Reused verbatim:
`tournament-create-format`, `tournament-champion-banner`, `tournament-lock-*`,
`tournament-play-next-*`, `tournament-play-all-*`. NEW context keys: `rr_crosstable`,
`rr_standings` (empty for elim; existing `rounds` 3-key dict shape unchanged).
Crosstable rule: leg `round_number==1` → `cell[team_a][team_b]`; leg `2` →
`cell[team_b][team_a]`; diagonal blank.

## LG-02c round robin → double elimination tournaments

Adds a fourth `Tournament.format` enum value
**`"round_robin_double_elim"`** (label `"Round robin → Double elimination"`,
em-dash arrow `→` U+2192): a **two-stage** format that composes the two shipped
LG-02c formats. A round-robin **Seeding stage** (the SHIPPED double round-robin,
verbatim — one `bracket_type="round_robin"` node per `generate_schedule` fixture,
no edges, Bo1) plays to completion; its final **Standings rank** then seeds a
double-elimination **Finals stage** (the SHIPPED ADR-0021 WB+LB+Grand-final tree)
built **lazily** when the last Seeding node resolves. Builds on both predecessors'
pure + persist machinery verbatim — the new work is (a) **one** pure builder that
fuses a re-tagged Winners bracket with a pre-seeded Losers bracket, (b) a
**deferred finals build** triggered when the last RR node resolves, (c) a shared
persist helper **extracted** from `lock_and_build`, and (d) the create-form combo
select + detail cut-line markers. **Non-deterministic** (`simulate_match` draws
fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. ADR-0021 is **EXTENDED** for the deferred-build decision; **no new
ADR, no new GLOSSARY.md term** beyond the already-written **Round robin → double
elimination**. Seam contract:
[`.claude/worktrees/lg-02c-rr-de-seam-contract.md`](../../../.claude/worktrees/lg-02c-rr-de-seam-contract.md).

**Enum / fields (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("round_robin_double_elim", "Round robin → Double elimination")` as its **fourth**
entry (label uses the em-dash arrow `→` U+2192); the `format` field declaration is
otherwise unchanged (`CharField(max_length=32)`, `default="single_elimination"` —
`"round_robin_double_elim"` is 23 chars, fits 32). Two NEW
`PositiveSmallIntegerField(default=0)` fields, declared **immediately after the
four `*_series_length` fields** (before `created_at`/`champion`):
**`Tournament.wb_advancers`** (how many top-ranked teams enter the Winners bracket)
and **`Tournament.lb_advancers`** (how many next-ranked teams pre-seed the Losers
bracket). **Neither carries `choices`** — the create form's `rrde_combo` select is
the single source of valid shape combos; the model holds the resolved ints (mirrors
how `BracketNode.series_length` carries a resolved int with no choices). Both are
**create-time only, frozen at lock** — no view rewrites them post-setup; meaningful
only for `format == "round_robin_double_elim"`, left `0` for every other format. No
`bracket_type` change (the Finals stage reuses `winners`/`losers`/`grand_final`); no
`_BRACKET_RANK` change (`"round_robin": 3` already present). Migration
`matches/migrations/0038_tournament_rr_de.py` (dep
`0037_tournament_round_robin`; three ops — `AlterField(Tournament.format)` choices
widen to the 4-tuple → `AddField(wb_advancers)` → `AddField(lb_advancers)`; **no
`RunPython`, no backfill**, ADR-0004 disposable-sandbox precedent).

**Locked constraints (shape vs count).** The valid `(wb_advancers, lb_advancers)`
combos are exactly six: **`wb ∈ {4, 8, 16}`** (a power of two) and
**`lb ∈ {0, wb // 2}`** — `4/0, 4/2, 8/0, 8/4, 16/0, 16/8`. The **SHAPE**
(power-of-two `wb`, `lb ∈ {0, wb//2}`) is enforced at the **create form** (the
`rrde_combo` select enumerates only the six combos); the **COUNT fit** (`wb <= n`
and `wb + lb <= n`, where `n = len(participants)`) is validated at
**`lock_and_build`** and raises `django.core.exceptions.ValidationError`
(`"wb_advancers exceeds participant count."` / `"wb_advancers + lb_advancers
exceeds participant count."`), surfaced by the existing lock view via
`messages.error` (LG-02a precedent).

**Seeding-stage build at lock (`matches/models.py`).** The existing RR guard in
`lock_and_build` widens from `if self.format == "round_robin":` to **`if
self.format in ("round_robin", "round_robin_double_elim"):`** — the RR-node build
is **byte-identical** for both formats (one `BracketNode` per `generate_schedule`
fixture, `bracket_type="round_robin"`, `series_length=1`, no advance edges, no
`resolve_bye_chain`). Inside this branch, the RRDE format additionally runs the
lock-time count validation above (raising `ValidationError` on a bad fit) before
the shared `state="active"` + `save(update_fields=["state"])` tail. **The RRDE lock
builds ONLY the RR Seeding nodes — the DE Finals are NOT built at lock**; they are
deferred (below). The elim branch (single/double-elim) is unchanged.

**Deferred Finals build — `build_de_finals_if_rr_finished(self) -> None`
(`@transaction.atomic`).** Triggered when the **last** Seeding node resolves; no-op
unless ALL guards hold (in order): (1) `self.format == "round_robin_double_elim"`;
(2) `self.state == "active"`; (3) every `bracket_type="round_robin"` node has
`winner_id is not None` (`not self.nodes.filter(bracket_type="round_robin",
winner__isnull=True).exists()`); (4) **idempotency** — the finals are not already
built (`not self.nodes.exclude(bracket_type="round_robin").exists()`; a second call
no-ops). When all pass: read `rows = self.round_robin_standings()` (the SHIPPED
RR-rank-ordered standings, `rows[0]` is RR rank 1), then split by rank —
`upper = [ParticipantSpec(team_id=rows[i].team_id, seed=i + 1) for i in
range(self.wb_advancers)]` (top `wb` → WB seeds `1..wb`, seed = 1-based RR rank),
`lower = [ParticipantSpec(team_id=rows[self.wb_advancers + j].team_id,
seed=self.wb_advancers + j + 1) for j in range(self.lb_advancers)]` (next `lb` → LB
pre-seeds, seeds `wb+1..wb+lb`), and **the rest of `rows` are eliminated** (never
enter the Finals). Call `specs = build_rr_de_finals_bracket(upper, lower)`, resolve
each spec's `series_length` via `series_length_for_depth(spec.depth, final=...,
semifinal=..., quarterfinal=..., earlier=...)` from the four create-time slots
(distance-to-GF1 escalation, exactly as a plain DE), and persist + wire via
`self._persist_elim_specs(specs, ...)`. The Tournament **STAYS `state="active"`**
across the seeding→finals transition; the champion is crowned later by the DE Grand
final (via `play_next_node`'s unchanged crown block). **No byes** in the finals
(see invariant below), so the `resolve_bye_chain` pass inside the helper is a no-op.

**NEW pure builder — `build_rr_de_finals_bracket(upper_specs, lower_specs) ->
list[BracketNodeSpec]` (`matches/bracket.py`).** The fused WB+LB+GF finals builder.
`upper_specs` are the Winners-bracket starters (RR-rank-ordered, `seed = RR rank`,
`len == wb_advancers ∈ {4, 8, 16}`, a power of two); `lower_specs` are the
Losers-bracket pre-seeds (RR-rank-ordered, `seed = wb+1 .. wb+lb`, `len` either `0`
or `wb_advancers // 2`). Output is a `list[BracketNodeSpec]` consumed by the **SAME**
persist+wire path as `build_double_elim_bracket` output — every spec carries
`bracket_type` (`winners`/`losers`/`grand_final`), 2-tuple `advances_to` +
`advances_to_slot`, 3-tuple `loser_advances_to` `(bracket_type, bracket_round,
position)` + `loser_advances_to_slot`, `is_bye`, `winner_id`,
`seed_a`/`seed_b`/`team_a_id`/`team_b_id`, and **`depth`** (distance to GF1: GF1/GF2
= 0, WB-final & LB-final = 1, earlier rounds deeper). Two cases:

- **`lower_specs == []` — plain DE delegation.** With no LB pre-seeds the finals are
  a plain top-`wb` double-elimination: the builder **MUST produce EXACTLY
  `build_double_elim_bracket(upper_specs)`** — it **delegates directly** (the
  simplest, locked choice), so the result is provably identical (the pure test
  asserts spec-list equality). No re-tagging, no LB pre-fill.
- **`lower_specs` non-empty — fused WB+LB.** The WB is `build_bracket(upper_specs)`
  **re-tagged `bracket_type="winners"`** (reuse `build_double_elim_bracket`'s own WB
  re-tag pass — same seeding/pairing, no byes since `len(upper_specs)` is a power of
  two). The **only** difference from a plain DE is the LB round-1 wiring: **LB-R1**
  has `wb/2` nodes (== `lb_advancers`), each with **slot "a" PRE-FILLED with a
  `lower_spec`** (`team_a_id`/`seed_a` set; `team_b_id`/`seed_b` left `None` until a
  WB-R1 dropper arrives) in seed order (LB pre-seed `wb+1` → LB-R1 pos 0 slot "a", …);
  and **each WB-R1 node's `loser_advances_to` points at the matching LB-R1 node's
  slot "b"** (naive same-position drop, WB-R1 pos `i` → LB-R1 pos `i` slot "b" —
  `wb/2` WB-R1 nodes ↔ `wb/2` LB-R1 nodes, exactly paired). WB-R(r≥2) losers drop
  into the LB exactly as `build_double_elim_bracket` wires them (the existing naive
  same-position drop — **NO anti-rematch folding**, inherited limitation). GF1/GF2
  are emitted as in a plain DE (GF1 = WB champ slot "a" + LB champ slot "b", GF1's
  `loser_advances_to` → GF2 for the Bracket reset; GF2 final, both pointers `None`;
  GF1/GF2 `depth = 0`). **No-byes invariant:** `wb` is a power of two filled by
  exactly `wb` real teams, and `lb = wb/2` exactly fills LB-R1 slot "a" (or `lb = 0`
  ⇒ plain DE over a power-of-two field), so the output has **zero `is_bye` nodes**
  and the deferred build's `resolve_bye_chain` pass is a no-op. **Import-allowlist
  guarantee:** the builder adds **no new import** — it composes `build_bracket` /
  `build_double_elim_bracket` and constructs `BracketNodeSpec`s, all in-module;
  `TestNoDjangoImportsLeaked` stays green.

**Extracted shared persist helper — `_persist_elim_specs(self, specs, ...) -> None`
(`matches/models.py`).** The DE-node persist loop + the `advances_to` /
`loser_advances_to` wiring passes + the `resolve_bye_chain` cascade pass are
**extracted** out of `lock_and_build` into this private helper so BOTH the existing
single/double-elim lock path AND the deferred finals build reuse it **verbatim**.
The helper does: (1) the `BracketNode.objects.create(...)` persist loop (carrying
`bracket_type`, `team_a`/`team_b` via a `team_by_id` map built from
`self.participants`, `seed_a`/`seed_b`, `is_bye`, `advances_to_slot`,
`loser_advances_to_slot`, `winner`, and the caller-resolved `series_length`); (2)
the `advances_to` wiring pass (2-tuple coord); (3) the `loser_advances_to` wiring
pass (3-tuple coord); (4) the `resolve_bye_chain` cascade. **Boundary note:**
`series_length` stamping stays in the **CALLER** — the single/double-elim
`lock_and_build` resolves `series_length_for_round` / `series_length_for_depth` per
spec before persisting, and the deferred build resolves
`series_length_for_depth(spec.depth, ...)` the same way; the exact length-resolution
boundary (a parallel lookup vs four ints + an `is_de` flag) is **Code agent's
discretion**, but the persist + three wiring passes MUST move into the helper
unchanged and the existing single/double-elim `lock_and_build` behaviour MUST stay
**byte-identical** (same nodes, edges, `series_length`, byes — pinned by
`TestLockAndBuildSingleElimUnchanged` / `...DoubleElimUnchanged`). **Stage is
DERIVED, not stored:** "Finals built" iff
`self.nodes.exclude(bracket_type="round_robin").exists()` — **no `stage` column**.

**Engine — `play_next_node` guard keyed on `node.bracket_type`
(`matches/tournament_engine.py`).** The current RR guard (after the `node.winner`
stamp) **rekeys** from `if tournament.format == "round_robin":` to
**`if node.bracket_type == "round_robin":`** and dispatches on format:
`format == "round_robin"` → `tournament.complete_round_robin_if_finished()`;
`format == "round_robin_double_elim"` → `tournament.build_de_finals_if_rr_finished()`;
then **`return node`** (a resolved Seeding node NEVER falls through to the elim
advance/drop/GF-reset/crown block). DE-stage nodes (`bracket_type in
winners`/`losers`/`grand_final`) **fall through to the UNCHANGED elim block** — even
inside an RRDE tournament: winner advance, loser Drop, GF1 Bracket-reset, GF2 crown,
all **byte-unchanged**. When the last RR node resolves in an RRDE tournament,
`build_de_finals_if_rr_finished()` persists the Finals, the tournament stays
`active`, and the next `play_next_node` call finds the first playable DE node.
**Callers unchanged** (`tournament_play_next`, `play_tournament_task`,
`tournament_play_all`, `tournament_play_status` keep their URLs + 5-key status JSON);
`stage_progress` reports completion across the RR groups THEN the WB/LB/GF groups —
the finals groups appear only after the deferred build, so Play-All progress
naturally extends mid-run as the Finals materialize. `find_next_node` is unchanged.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the `<select name="format">` (DOM id
**`tournament-create-format`**) gains a fourth option — value
**`round_robin_double_elim`**, label **`Round robin → Double elimination`**
(em-dash arrow U+2192); the view's forgiving-fallback parse widens to accept it
(absent/tampered → `single_elimination`). NEW single `<select name="rrde_combo">`
(DOM id **`tournament-create-rrde-combo`**) enumerating the six valid combos
(`4/0, 4/2, 8/0, 8/4, 16/0, 16/8` — exact value-string format at Code agent's
discretion), parsed into `wb_advancers` / `lb_advancers`; parse is forgiving (absent/
invalid on an RRDE create → first combo `(4, 0)`; ignored on a non-RRDE create,
both advancers persist `0`). *(ANG-01f: the forgiving `_parse_rrde_combo` is gone — absent still
means `4/0`, but an unknown combo is now a 400 / form error from `TournamentCreateForm`.)* The combo select is shown client-side **only** when the
format select reads `round_robin_double_elim` (hidden otherwise — reuse the existing
inline `onchange` toggle that hides the series-length selects for `round_robin`;
behaviour pinned, exact JS at Code agent's discretion). `Tournament.objects.create(...)`
passes `wb_advancers=` / `lb_advancers=` (`0`/`0` for non-RRDE). Detail page
(`_detail_context` / `tournament_detail.html`): two NEW context keys —
**`tournament_stage`** (`str`, DERIVED not stored: `"setup"` / `"seeding"` /
`"finals"` / `"completed"`) and **`cut_labels`** (`dict[int, str]`,
`team_id -> "wb" | "lb" | "out"`, built from `round_robin_standings()`: top
`wb_advancers` → `"wb"`, next `lb_advancers` → `"lb"`, rest → `"out"`; populated only
in the seeding stage of an RRDE tournament, `{}` otherwise). The existing keys
(`tournament`, `participants`, `rounds`, `next_node`, `is_locked`, `can_play`,
`import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`) are
**unchanged**. NEW DOM ids: **`tournament-stage-badge`** (renders `tournament_stage`,
e.g. "Seeding stage" / "Finals stage") and a per-standings-row cut class substring
**`tournament-standings-cut-wb`** / **`-lb`** / **`-out`** on each RR standings row in
the seeding stage (so tests can assert which teams are tagged Winners / Losers /
Eliminated). During seeding, reuse the RR crosstable + standings VERBATIM
(`tournament-rr-crosstable` / `tournament-rr-standings`), adding the cut class + the
stage badge; once Finals are built, ALSO render the DE three-section tree reusing the
EXISTING DE rendering (`tournament-bracket-winners` / `-losers` / `-grand-final` +
the per-section `tournament-bracket-{bracket_type}-round-{n}` columns +
`tournament-champion-banner`). `_build_rounds` / `_build_rr_crosstable` are
**unchanged** — `_build_rounds` already returns the 3-key
`{"winners", "losers", "grand_final"}` dict, populated once finals nodes exist; the
template branches on `tournament.format`.

**Determinism / scope.** **Non-deterministic** per-Match sims (`simulate_match`
draws fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. No `simulate_match` change. ADR-0021 **EXTENDED** for the
deferred-build decision (no new ADR — reversible: a fused builder + a deferred build
+ an extracted helper); **no new GLOSSARY.md term** (the **Round robin → double
elimination** term was finalised at grilling time). **Scope-out (LOCKED — DEFERRED):**
**anti-rematch LB folding** (the LB consumes WB losers via the naive same-position
drop, inherited from `build_double_elim_bracket`); **Swiss** seeding stage;
**in-League / in-Season embedding** (the Tournament stays standalone, `season`-less);
**fully-general `wb`/`lb` counts** (only the six locked combos ship); **home/away
side alternation** (sides fixed, LG-02b locked); **deterministic / master-seed-
replayable Series**.

**Tests:** `matches/tests/test_bracket.py` (extend —
`TestBuildRrDeFinalsBracket`: `lower_specs == []` ⇒ result EQUALS
`build_double_elim_bracket(upper_specs)` for `wb = 4/8/16`; non-empty `lb = wb/2`
for `wb = 4/8` — WB re-tagged `winners`, LB-R1 slot "a" pre-filled in seed order,
each WB-R1 `loser_advances_to` → matching LB-R1 slot "b", GF1/GF2 wiring + `depth`,
**no `is_bye` nodes**, LB-round count `== 2W - 1`; `TestNoDjangoImportsLeaked` green).
`matches/tests/test_tournament_models.py` (extend — `wb_advancers`/`lb_advancers`
exist/default `0`/no choices; `format` accepts `round_robin_double_elim`;
`lock_and_build` builds ONLY RR nodes for RRDE and raises `ValidationError` on the two
count-fit failures; `build_de_finals_if_rr_finished` no-op unless all guards hold +
idempotent + seeds Finals from RR standings rank; `_persist_elim_specs` extraction
keeps single/double-elim `lock_and_build` byte-identical via
`TestLockAndBuildSingleElimUnchanged` / `...DoubleElimUnchanged`).
`matches/tests/test_tournament_engine.py` (extend — resolving the LAST RR node
triggers the deferred Finals build, tournament stays `active`; draining the Finals
crowns the champion via the GF crown block; a Seeding node never gets an
`advance_winner` mutation; a DE-stage node advances/drops/resets normally).
`matches/tests/test_tournament_views.py` (extend — create form offers the
`round_robin_double_elim` option + the `tournament-create-rrde-combo` select with the
six combos; POST persists `format`/`wb_advancers`/`lb_advancers` with forgiving
fallback; detail renders `tournament-stage-badge` seeding-vs-finals strings + the
`tournament-standings-cut-{wb|lb|out}` substrings tagging the right teams; reused RR +
DE DOM ids present). `matches/tests/test_tournament_tasks.py` (extend — under
`CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains an RRDE tournament through
BOTH stages — seeding RR nodes THEN the auto-built DE finals — to a champion +
`state="completed"`; `stage_progress` reports per-group completion across the RR
groups then the WB/LB/GF groups). Tests assert on pure functions, persisted
node/row/edge shapes, `bracket_type` / `series_length`, `node.winner` / `champion` /
`state`, standings ORDER, and DOM ids — **never on exact simulated point totals**.

## LG-02c swiss tournaments

Adds a **fifth** `Tournament.format` enum value **`"swiss"`** (label `"Swiss"`):
a **flat, edge-less** Swiss-system format for the standalone sandbox Tournament,
built on the four shipped LG-02c formats. Every Swiss node is a Bo1 pairing with
`advances_to=None`, `loser_advances_to=None`, `is_bye=False`, `series_length=1` —
there is **no advancement tree** and **no final node**; the champion is the
**Standings leader (Buchholz re-ranked)** after the last Swiss round resolves. The
new work is (a) **two** pure functions — a single round builder that serves both
the round-1 seed fold and the later greedy ranked sweep, plus a Buchholz re-rank
layer — (b) a **per-round DEFERRED build** triggered each time a round's last node
resolves, (c) a `compute_standings`-input assembly helper **extracted** from
`round_robin_standings`, and (d) the create-form round-count input + the per-round
pairings/standings detail page. **Non-deterministic** (`simulate_match` draws fresh
per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. ADR-0021 is **EXTENDED** for the per-round deferred build + Buchholz
re-rank decision; **no new ADR**, and **no new GLOSSARY.md term** beyond the
already-written **Swiss** + **Buchholz** terms (GLOSSARY.md already carries them — do
not edit). Seam contract:
[`.claude/worktrees/lg-02c-swiss-seam-contract.md`](../../../.claude/worktrees/lg-02c-swiss-seam-contract.md).

**Enum / bracket_type / field (LOCKED).** `Tournament.FORMAT_CHOICES` gains
`("swiss", "Swiss")` as its **fifth** entry (`format` field declaration otherwise
unchanged — `CharField(max_length=32)`, `"swiss"` is 5 chars). `BracketNode`'s
`bracket_type` choices gain `("swiss", "Swiss")` as their **fifth** entry
(declaration unchanged: `CharField(max_length=12)`, `default="winners"` —
`"swiss"` fits 12), and `matches/bracket.py`'s `_BRACKET_RANK` gains
**`"swiss": 4`** (so `find_next_node`'s `_BRACKET_RANK.get(bracket_type, 0)` sort
key slots Swiss in with **no `find_next_node` edit**; its playable predicate already
treats an unplayed Bo1 Swiss node as playable and a resolved one as skipped). One
NEW field: **`Tournament.swiss_rounds`**
(`PositiveSmallIntegerField(default=0)`, declared **immediately after
`lb_advancers`**, **no `choices`**) — the total number of Swiss rounds; `0` = auto.
It is **create-time, resolved-and-frozen at lock**: at lock
`total = swiss_rounds or math.ceil(math.log2(N))`, **clamped to `[1, N-1]`**, then
**written back** into `swiss_rounds` so the played-out round count is fixed.
Meaningful only for `format == "swiss"`; left `0` for every other format. Migration
`matches/migrations/0039_tournament_swiss.py` (dep `0038_tournament_rr_de`; three
ops in pinned order — `AlterField(Tournament.format)` choices-widen to the 5-tuple →
`AlterField(BracketNode.bracket_type)` choices-widen to add `("swiss", "Swiss")` →
`AddField(Tournament.swiss_rounds)`; **no `RunPython`, no backfill**, ADR-0004
disposable-sandbox precedent).

**EVEN-N only — no byes ever (LOCKED).** Swiss admits any **even** participant
count; an **odd** count raises `django.core.exceptions.ValidationError` at
`lock_and_build` with the EXACT message
**`"Swiss requires an even number of participants."`** (surfaced by the lock view
via `messages.error`, LG-02a precedent). There are **no bye nodes** at any point —
every Swiss node is a real pairing, so `resolve_bye_chain` is never invoked (flat,
like the RR branch).

**Round-1 build at lock = seed "fold" (`matches/models.py`).** Inside the existing
`@transaction.atomic` `lock_and_build`, AFTER the `setup`-guard and the `>= 4`
participant guard, a **dedicated `if self.format == "swiss":` branch** (its own
branch — NOT folded into the RR branch, because it needs the even-N guard + the
round-count freeze + the fold pairing + the `bracket_type="swiss"` tag) does:
raise on odd N; resolve+clamp+freeze `swiss_rounds`; compute the **seed fold** —
sort participants by Bracket `seed` ascending, split in half, and interleave so
consecutive pairs are `(seed[i], seed[i + N/2])` for `i` in `0..N/2-1`; then call
`build_swiss_round(fold_order, seed_by_team, set(), bracket_round=1)` and persist
each spec as a `BracketNode` (`bracket_type="swiss"`, `series_length=1`,
`is_bye=False`, all advance/loser slots `None`, `winner=None`). Tail:
`state="active"` then `save(update_fields=["state", "swiss_rounds"])`.
`build_swiss_round` is **deferred-imported inside the branch** (mirrors the RR
branch's deferred `from .schedule_generator import generate_schedule`); `import math`
is used (already at module scope, or added). The R1 build emits **only** the
round-1 nodes (`N/2` of them) — later rounds are deferred.

**Later rounds DEFERRED — `advance_swiss_if_round_finished(self) -> None`
(`@transaction.atomic`).** Triggered when the **current (highest) Swiss round's last
node resolves**; no-op unless `format == "swiss"` and `state == "active"`. It finds
the highest `bracket_round` among the Swiss nodes; if any node in that round still
has `winner_id is None`, it no-ops (round not finished). When the round IS resolved:
if `current_round < self.swiss_rounds` it builds the **next** round's nodes via a
**greedy ranked sweep** — `rows = self.swiss_standings()`, `ranked_team_ids =
[row.team_id for row in rows]`, `played_pairs = self._swiss_played_pairs()`, then
`build_swiss_round(ranked_team_ids, seed_by_team, played_pairs,
bracket_round=current_round + 1)` — persists the new nodes, and the Tournament
**STAYS `active`**. If `current_round == self.swiss_rounds` it crowns:
`champion_id = swiss_standings()[0].team_id`, `state="completed"`,
`save(update_fields=["champion", "state"])`. The pairing walks
`swiss_standings()` top-down, pairing each unpaired team with the **next unpaired
team it has NOT already played**; if the trailing teams can only be paired by
replaying, it **ALLOWS the rematch** (no backtracking — the last fallback pair is the
next unpaired team regardless of `played_pairs`).

**No draws ⇒ `league_points = 3 * wins`.** Each Bo1 Match resolves to a single
winner via the inherited `break_tie` (unchanged in `play_next_node`), so there are
no draws and a team's `league_points` are purely `3 * wins`.

**Buchholz tiebreak — ORDERING-ONLY, over the frozen `compute_standings`.** A team's
Buchholz = the **sum of its opponents' final `league_points`** across all Swiss
pairings it played (a rematch counts twice — Buchholz sums per played pairing). The
**Swiss ranking ladder** is `league_points desc → Buchholz desc → round_wins desc →
total_score desc → team_name asc`. Buchholz is **NOT a displayed column** and
`matches/standings.py::compute_standings` is a **FROZEN shared module — NOT
modified**; instead a separate **pure re-rank layer** takes the `compute_standings`
rows + the played-pairs opponent graph and re-sorts them. Because the input rows are
already in `compute_standings`' final order (which ends `team_name asc`), the re-rank
uses a **STABLE sort** keyed on `(-league_points, -buchholz, -round_wins,
-total_score)` and the pre-existing `team_name asc` survives as the stable tiebreak —
so **no team-name lookup crosses the pure seam**.

**NEW pure functions — `matches/bracket.py` (frozen import allowlist UNCHANGED;
`TestNoDjangoImportsLeaked` stays green).** Both add **no new import** (only
`dataclasses` / `typing` / `math` / `collections`, all already present):

- **`build_swiss_round(ranked_team_ids, seed_by_team, played_pairs, bracket_round)
  -> list[BracketNodeSpec]`** — **ONE** function for BOTH the R1 fold and the later
  greedy sweep; the variant is selected by the **caller** via what it passes. **R1
  (fold):** caller passes `ranked_team_ids` = the pre-computed fold order +
  `played_pairs = set()` — with an empty `played_pairs` the "not yet played" check
  never fires, so greedily pairing consecutive teams reproduces exactly the fold
  pairing. **Later rounds (greedy sweep):** caller passes `ranked_team_ids` = the
  current `swiss_standings()` rank order + the non-empty `played_pairs`; the function
  walks top-down, pairing each unpaired team with the next unpaired team it has NOT
  already played, allowing a rematch only as the trailing fallback (no backtracking).
  `seed_by_team` (`dict[int, int]`, **every** team id → its Bracket seed) is used
  ONLY to stamp `seed_a`/`seed_b` on each node (so the engine's seed-keyed tie-break
  works even though pairing order is rank/fold-based, not seed-based). `played_pairs`
  is a `set[frozenset[int]]` (side-agnostic). Returns one `BracketNodeSpec` per
  pairing, `position` 0-based ascending in pairing order, each
  `bracket_type="swiss"`, `is_bye=False`, all advance/loser slots `None`,
  `winner_id=None`, `depth=None` (the spec carries **no `series_length`** — the node
  row gets `series_length=1` at create). **Pure / total** (never raises): an odd
  `len(ranked_team_ids)` is the caller's responsibility (the even-N guard fires at
  lock); defensively the trailing unpaired team is dropped.
- **`swiss_buchholz_rerank(rows, opponents_by_team) -> list[StandingsRow]`** — the
  pure Buchholz re-rank layer. `rows` is the `StandingsRow` list from
  `compute_standings` (the 17-field frozen dataclass); `opponents_by_team` is
  `team_id -> list[opponent_team_id]` (one entry per played pairing — a rematch
  appears twice). Builds `points_by_team = {row.team_id: row.league_points}`, then
  `buchholz(t) = sum(points_by_team.get(opp, 0) for opp in
  opponents_by_team.get(t, []))`, re-sorts via the **STABLE sort** on
  `(-league_points, -buchholz, -round_wins, -total_score)`, and returns a NEW list of
  `StandingsRow` with `rank` renumbered **1-based dense**
  (`dataclasses.replace(row, rank=i + 1)`), every other field copied verbatim — **no
  Buchholz value leaks into the row**. Empty `rows` ⇒ `[]`. (`StandingsRow` is never
  imported at module scope in `bracket.py`; the return annotation is a string
  forward-ref, no import added.)

The `BracketNodeSpec` / `ParticipantSpec` dataclasses and every existing pure
function (`build_bracket` / `build_double_elim_bracket` /
`build_rr_de_finals_bracket` / `find_next_node` / `advance_winner` /
`advance_loser` / `resolve_bye_chain` / `series_length_for_*` /
`default_seed_order` / `stage_progress` / `count_series_wins` / `break_tie`) are
**UNCHANGED**.

**Model helpers — `matches/models.py`.** The `compute_standings`-input assembly is
**extracted** out of `round_robin_standings()` into a private
**`_standings_over_nodes(self, node_qs) -> list[StandingsRow]`** (builds
`enrolled_teams` + `completed_matches` + `season_rounds` from a queryset of resolved
Bo1 nodes and returns `compute_standings(...)`); `round_robin_standings()` then
becomes a one-liner over `self.nodes.filter(bracket_type="round_robin")` and its
**external behaviour stays byte-identical** (pinned by a regression test). NEW
**`swiss_standings(self) -> list[StandingsRow]`** = `_standings_over_nodes` over the
Swiss nodes, fed through `swiss_buchholz_rerank` with the opponent graph. NEW private
**`_swiss_opponent_graph(self) -> dict[int, list[int]]`** (builds the played-pairs
opponent graph from every Swiss node with both slots filled — a rematch contributes
to both lists each time) and **`_swiss_played_pairs(self) -> set[frozenset[int]]`**
(side-agnostic frozenset pairing keys from the Swiss nodes). `_node_to_dict`,
`find_next_playable_node`, `count_series_wins`,
`complete_round_robin_if_finished`, `build_de_finals_if_rr_finished`,
`_persist_elim_specs` are UNCHANGED — `_node_to_dict` already yields
`bracket_type="swiss"` / `advances_to=None` / `loser_advances_to=None` /
`series_length=1` with no edit.

**Engine — `play_next_node` Swiss guard (`matches/tournament_engine.py`).** The body
is verbatim through the clinch check + `node.winner` stamp. A Swiss branch is added
**alongside** the existing RR/RR→DE `node.bracket_type == "round_robin"` guard and
**before** the elim `_node_to_dict` flatten / `advance_winner` / `advance_loser` /
crown block: `if node.bracket_type == "swiss":
tournament.advance_swiss_if_round_finished(); return node`. The `return node` means a
resolved Swiss node NEVER reaches the elim advance/crown block — without it the
"crown when `advances_to is None`" elim rule would wrongly crown on the FIRST resolved
Swiss node. Callers (`tournament_play_next`, `play_tournament_task`,
`tournament_play_all`, `tournament_play_status`) are UNCHANGED in
signature/route; `play_tournament_task`'s `while play_next_node(...) is not None`
loop drains every Swiss node one Match at a time and naturally extends mid-run as each
deferred round's pairings materialize; `stage_progress` (unchanged) reports
per-`(bracket_type, bracket_round)` group completion — for Swiss, per-round progress.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the `<select name="format">` (DOM id
**`tournament-create-format`**) gains a fifth `<option value="swiss">Swiss</option>`;
the view's format whitelist appends `"swiss"` (absent/tampered → `single_elimination`
fallback). NEW numeric input (DOM id **`tournament-create-swiss-rounds`**, name
`swiss_rounds`, `min="0"`, `value="0"`) wrapped in a `.tournament-create-swiss-rounds-row`
(mirrors the `*-series-length-row` / `*-rrde-combo-row` pattern), shown client-side
**only** when the format select reads `swiss`. A forgiving **`_parse_swiss_rounds(raw)
-> int`** coerces the POST field to a non-negative int (absent/blank/invalid/negative
⇒ `0` = auto; clamping happens at lock), passed as `swiss_rounds=` into
`Tournament.objects.create(...)` (`0` and harmless for non-Swiss). *(ANG-01f: superseded —
`_parse_swiss_rounds` is gone; the strict `tournament_services.create.TournamentCreateForm` field
`swiss_rounds` (`min_value=0`) keeps absent / blank ⇒ `0` = auto, but a non-integer or negative value
is now a form error. Lock-time clamping is unchanged. See **ANG-01f** below.)* The
`tournamentCreateToggle(value)` JS shows `.tournament-create-swiss-rounds-row` only
for `swiss`, and **hides** the series-length selects AND the rrde-combo control for
`swiss` (Swiss is always Bo1, no DE finals — the existing `value === "round_robin"`
hide rule widens to `value === "round_robin" || value === "swiss"`). Detail page
(`_detail_context` / `tournament_detail.html`): two NEW context keys — **`swiss_rounds_view`**
(`list[{round_number, pairings}]`, the Swiss nodes grouped by `bracket_round`, each
pairing a node-view dict in the SAME shape `_build_rounds` builds — reusing the
node-card include — assembled by a small helper, suggested
`_build_swiss_rounds(tournament)`, NOT by overloading the 3-key elim `_build_rounds`)
and **`swiss_standings`** (`list[(StandingsRow, Team)]`, the Buchholz-ranked rows
paired with their Team via the LG-01 `rows_with_teams` precedent). Both default to
`[]` for non-Swiss (mirrors `rr_crosstable=[]` / `rr_standings=[]`); every existing
context key (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`,
`can_play`, `import_form`, `import_row_errors`, `rr_crosstable`, `rr_standings`,
`tournament_stage`, `cut_labels`) is UNCHANGED. `_tournament_stage` gains an explicit
`if tournament.format == "swiss": return "swiss"` for the active case (so the
`tournament-stage-badge` reads "Swiss stage"; the badge guard widens to fire for
swiss). NEW DOM ids (LOCKED): **`tournament-swiss-rounds`** (outer container of the
per-round pairing sections), **`tournament-swiss-round-{n}`** (one section per Swiss
round, `n` = 1-based), **`tournament-swiss-standings`** (the Buchholz-ranked standings
table), and per-pairing **`tournament-node-swiss-{bracket_round}-{position}`** cards
(reusing the existing `tournament-node-{bracket_type}-{bracket_round}-{position}`
convention; Swiss nodes are Bo1 ⇒ NO per-node Series-score / Bo-N label, same as RR).
The Swiss render block is gated `{% elif tournament.format == "swiss" %}` in the
existing format ladder. **REUSED VERBATIM** (no new ids): `tournament-champion-banner`
(stamped identically on the Swiss completion path), the lock control
(`tournament-lock-form`/`-submit`), play-next (`tournament-play-next-form`/`-submit`),
play-all (`tournament-play-all-form`/`-submit`/`-progress` + poll JS), the import +
seeding forms, and `tournaments-nav-link`. The elim WB/LB/GF containers
(`tournament-bracket*`) and the RR ids
(`tournament-rr-crosstable`/`tournament-rr-standings`) are ABSENT for Swiss; the Swiss
ids are ABSENT for every other format (the template branches on `tournament.format`).
`_build_rounds`'s 3-key `{"winners", "losers", "grand_final"}` return is UNCHANGED —
all three lists are empty for a Swiss Tournament (Swiss nodes are never bucketed into
its three sections).

**Determinism / scope.** **Non-deterministic** per-Match sims (`simulate_match` draws
fresh per-round seeds) ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**. No `simulate_match` change. ADR-0021 **EXTENDED** for the per-round
deferred build + the Swiss-only Buchholz re-rank layer (no new ADR); **no new
GLOSSARY.md term** (the **Swiss** + **Buchholz** terms were finalised at grilling and
GLOSSARY.md is left untouched). With Swiss shipped, all **four** LG-02c bracket
formats are complete; the LG-02x player-pool formats remain the next LG-02 Part-1
work.

**Tests:** `matches/tests/test_bracket.py` (extend — `TestBuildSwissRound`: R1 fold
for N=4/8/16 emits the exact fold pairing from the interleaved order + empty
`played_pairs`, `bracket_type="swiss"`, advance/loser slots `None`, `is_bye=False`,
no `series_length` on the spec, 0-based `position`, seeds from `seed_by_team`;
later-round greedy sweep pairs each unpaired team with the next not-yet-played team;
allow-rematch fallback forces a trailing rematch with no crash/no dropped team for
even N. `TestSwissBuchholzRerank`: ladder correctness — Buchholz breaks an equal-points
tie, then `round_wins`/`total_score`, `team_name asc` surviving via the stable sort;
ORDERING-ONLY — all 17 fields preserved except `rank` renumbered 1-based dense, no
Buchholz value in the row; empty input ⇒ `[]`. `TestBracketRankSwiss`:
`_BRACKET_RANK["swiss"] == 4`. `TestNoDjangoImportsLeaked` STILL green).
`matches/tests/test_tournament_models.py` (extend — `TestSwissRoundsField`
(exists/default `0`/no choices); `TestSwissLockAndBuild` (even-N happy path builds
ONLY R1 nodes, count `N/2`, all `swiss`/Bo1/no-edges/no-bye, `state="active"`, fold
pairing; round-count resolve/clamp/freeze written back; odd-N ⇒ `ValidationError`
with the EXACT message); `TestStandingsOverNodesExtraction` (`round_robin_standings()`
byte-identical post-refactor); `TestSwissStandingsBuchholz` (hand-stamped Swiss nodes
across ≥2 rounds, `swiss_standings()` ORDER reflects the Buchholz ladder — NOT exact
points); `TestAdvanceSwissIfRoundFinished` (unfinished round ⇒ no-op; resolved +
`current < swiss_rounds` ⇒ next round built greedily, stays active; resolved +
`current == swiss_rounds` ⇒ champion = `swiss_standings()[0]`, `state="completed"`;
played-pairs rematch only as trailing fallback)). `matches/tests/test_tournament_engine.py`
(extend — `TestPlayNextNodeSwiss`: a resolved Swiss node never gets an
`advance_winner` mutation; resolving the round's LAST node triggers the next-round
build, stays active; the final round's completion crowns `swiss_standings()[0]` and
flips `state="completed"`). `matches/tests/test_tournament_views.py` (extend —
`TestCreateFormSwiss`: the format select offers `swiss`, a `swiss_rounds` input with
DOM id `tournament-create-swiss-rounds` exists, a POST persists
`format == "swiss"` + the coerced `swiss_rounds`, forgiving parse, format fallback;
`TestDetailSwiss`: detail renders `tournament-swiss-rounds` with per-round
`tournament-swiss-round-{n}` sections + `tournament-node-swiss-{br}-{pos}` cards +
`tournament-swiss-standings`, the series-length selects + rrde-combo hidden for swiss,
the reused champion/lock/play-next/play-all ids present, elim + RR ids absent).
`matches/tests/test_tournament_tasks.py` (extend — `TestPlayTournamentTaskSwiss`:
under `CELERY_TASK_ALWAYS_EAGER`, `play_tournament_task` drains a full Swiss
Tournament (all rounds) to a champion + `state="completed"`; `stage_progress` reports
per-round stage counts). Tests assert on pure functions, persisted node/row shapes,
`bracket_type`/`series_length`, `node.winner`/`champion`/`state`, standings ORDER,
and DOM ids — **never** on exact simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02c-swiss-seam-contract.md`](../../../.claude/worktrees/lg-02c-swiss-seam-contract.md)
for the authoritative list of every name / signature / dict-key / DOM-id / literal
(format `"swiss"` + label, `_BRACKET_RANK["swiss"] = 4`, `swiss_rounds` field,
`build_swiss_round` / `swiss_buchholz_rerank`, `_standings_over_nodes` /
`swiss_standings` / `_swiss_opponent_graph` / `_swiss_played_pairs` /
`advance_swiss_if_round_finished`, the new DOM ids + context keys, the even-N error
string, and migration `0039_tournament_swiss`).

## LG-02x-1 random draw player-pool tournament

> **Renamed by ANG-01f.** The `team_assembly` value `"random_draw"` below is now **`"player_pool"`**
> (label "Player pool draw"; migration `0064` rewrote the rows), and a Player-pool draw may use **any**
> format — only `player_pool` + `round_robin_double_elim` is called **Random Draw** (a derived
> `format_label`). Read every `team_assembly == "random_draw"` below as `"player_pool"`. The views
> moved to `matches/tournament_views/draw.py` on `matches/tournament_services/draw.py`. See **ANG-01f
> tournaments API** at the end of this doc.

Adds a NEW **orthogonal** `Tournament.team_assembly` mode **`"random_draw"`** (vs the
default `"preset"`): instead of enrolling existing `Team`s, a **player pool** registers,
the system runs a **deterministic tier-balanced draw** into marked `is_draw_team`
Teams, and each game **Round** assigns roles dynamically before it sims. The draw
Tournament then runs the **shipped LG-02c RR→DE bracket unchanged** —
`team_assembly` is a SEPARATE field, **NOT a new `format` value**: a Random Draw
Tournament keeps `format == "round_robin_double_elim"`, so every RR→DE path
(`lock_and_build`, `_persist_elim_specs`, `round_robin_standings`,
`build_de_finals_if_rr_finished`, `play_next_node`, `stage_progress`, the detail
crosstable / cut-labels / DE-finals surfaces) is **byte-unchanged**. Pool intake,
the draw, the relaxed roster rule, and per-Round dynamic roles **all key off
`team_assembly == "random_draw"`**. **Non-deterministic** (the role draw consumes a
fresh `random.Random()`, the per-Match sims draw fresh per-round seeds) ⇒ **no
SIM-07 / SIM-08 interaction, NO Score Calibration re-baseline** (no simulation
*mechanics* change — the hook only swaps which Player occupies each role slot before a
normal round). **NEW [ADR-0022](../../adr/0022-random-draw-player-pool-tournament.md)**
records the tier-balanced draw + per-Round dynamic roles + relaxed draw-team roster
ownership; **no new GLOSSARY.md term** beyond the already-finalised **Player pool /
Drawn-team membership / Random Draw / Tier / Role assignment mode** terms (GLOSSARY.md
carries them — do not edit). Duos / Trios + a `TournamentSubGroup` model are
**DEFERRED to LG-02x-2**. Seam contract:
[`.claude/worktrees/lg-02x-1-seam-contract.md`](../../../.claude/worktrees/lg-02x-1-seam-contract.md).

**Two NEW `Tournament` fields (LOCKED) — `matches/models.py`.**
**`team_assembly`** (`CharField(max_length=16, choices=TEAM_ASSEMBLY_CHOICES,
default="preset")`) where `TEAM_ASSEMBLY_CHOICES = (("preset", "Preset teams"),
("random_draw", "Random draw player pool"))`. **`role_assignment_mode`**
(`CharField(max_length=16, choices=ROLE_ASSIGNMENT_CHOICES, default="random")`) where
`ROLE_ASSIGNMENT_CHOICES = (("random", "Random per team per Round"), ("per_tier",
"Per-tier bijection (both teams)"))`. Both are **create-time only**, declared
**immediately after** the `wb_advancers` / `lb_advancers` / `swiss_rounds` block and
**before** `created_at` / `champion`. `team_assembly` default `"preset"` ⇒ every
existing Tournament is `preset`, byte-unchanged; meaningful for any `format` but the
pool/draw/per-Round-roles machinery fires **only** when `== "random_draw"`.
`role_assignment_mode` is **meaningful only when `team_assembly == "random_draw"`**
(ignored for `preset`).

**NEW model `TournamentPlayerEntry` (LOCKED) — `matches/models.py`, after
`BracketNode` / `SeriesMatch`.** The durable **pool registration AND draw result** —
the source of truth for `(player, tier, drawn_team)`. A drawn Team's `slot_*` FKs hold
only the **transient** per-Round role assignment; the (player, tier, team) truth lives
here. Fields: `tournament` (FK `matches.Tournament`, **CASCADE**,
`related_name="player_entries"`), `player` (FK `teams.Player`, **CASCADE**,
`related_name="tournament_entries"`), `tier`
(`PositiveSmallIntegerField(null=True, blank=True)` — `null` after pool intake /
before the draw, `1..6` after, **tier 1 = strongest band**), `drawn_team` (FK
`teams.Team`, **SET_NULL**, nullable, `related_name="drawn_player_entries"` — a drawn
Team deleted out of band leaves the entry's tier intact). `Meta.ordering =
["tournament_id", "tier", "player_id"]` (deterministic tier-ascending, player-id
tiebreak — matches the draw's tiebreak) and a **`UniqueConstraint(fields=["tournament",
"player"], name="uniq_tournament_player_entry")`** — the structural guarantee a Player
cannot sit in two drawn Teams of the **same** Tournament; across **different**
Tournaments a Player may belong to many drawn Teams (the cross-tournament sharing rule).

**NEW `Team.is_draw_team` field + relaxed `roster_errors` — `teams/models.py`.**
`is_draw_team = models.BooleanField(default=False)` marks a drawn Team (**NO FK to
Tournament / Match** — the durable link lives on `TournamentPlayerEntry`, avoiding a
`teams → matches` dependency inversion). The "all players belong to this team"
`roster_errors` check is **relaxed for draw teams only** — the existing
`for player, role, slot_name in filled:` belongs-to-team loop (the
`player.team_id != self.pk` check) is wrapped in `if not self.is_draw_team:`. **Kept
unchanged** for draw teams: the all-6-slots-filled check, the duplicate-player check,
and the role-distribution (Scout-only-twice) check — only the ownership check is
relaxed. See [`docs/agents/teams/models-and-fields.md`](../teams/models-and-fields.md) `### Team.is_draw_team`.

**NEW pure module `matches/draw.py`.** Tier-balanced draw math + the two
role-assignment-mode bijection builders — **pure Python, no Django / ORM / I/O /
logging** (frozen import allowlist: `dataclasses`, `typing`, `random`, `collections`;
NO `django.*`, NO `datetime`, NO file I/O), defended by `TestNoDjangoImportsLeaked`
mirroring `matches/bracket.py` / `standings.py` / `schedule_generator.py`. `random`
is allowlisted because the role-assignment builders consume an **injected**
`random.Random` (the per-Round role draw); the **draw computation itself consumes NO
RNG**. Public surface:

- **`ROLE_SLOTS: tuple[str, ...] = ("commander", "heavy", "scout_1", "scout_2",
  "medic", "ammo")`** — the 6 `Team.slot_*` suffixes, fixed order.
- **`@dataclass(frozen=True) DrawnTeamPlan`** — `team_index: int` (0-based draw order),
  `player_ids: tuple[int, ...]` (the 6 player ids, tier 1..6 order),
  `tiers: tuple[int, ...]` (parallel to `player_ids`, the tier 1..6 of each).
- **`compute_draw(pool: list[tuple[int, float]]) -> list[DrawnTeamPlan]`** — **STRAIGHT
  TIERS + GREEDY BALANCE, deterministic, consumes NO RNG.** `pool` is `(player_id,
  overall_rating)`. Precondition (caller-validated): `len(pool) % 6 == 0` and
  `len(pool) >= 24` (≥ 4 teams) — raises `ValueError` otherwise. Algorithm: (1) sort
  by `overall_rating` **DESC**, then `player_id` **ASC** (tiebreak); (2) `T =
  len(pool) // 6` teams, form **6 contiguous tiers** of `T` players (tier 1 =
  strongest band = first `T`, …, tier 6 = weakest); (3) for each tier 1..6 in order,
  assign the strongest-remaining tier player to the **currently-weakest team** (lowest
  running total rating across already-processed tiers; `team_index` ASC tiebreak),
  one player per team per tier; (4) return one `DrawnTeamPlan` per team
  (`team_index` 0..T-1, `player_ids`/`tiers` ordered tier 1..6). **Idempotent** — same
  pool → identical output (a re-roll is a no-op; **admin hand-edits are the variation
  mechanism**).
- **`build_random_role_assignment(tier_player_ids: list[int], rng: random.Random) ->
  dict[str, int]`** — `random` mode, per TEAM independently: shuffle the team's 6
  tier-ordered ids into the 6 `ROLE_SLOTS`, returns `{slot_suffix: player_id}` over all
  6 slots (one rng shuffle).
- **`build_per_tier_role_assignment(rng: random.Random) -> dict[int, str]`** —
  `per_tier` mode: draw ONE `{tier (1..6): slot_suffix}` bijection for the Round
  (one rng shuffle), applied to **BOTH** teams (equal-tier players play the same role);
  the caller applies it to each team's tier→player map.

**Simulator seam — `BatchSimulator.simulate_match` (`matches/simulation/entrypoints.py`).**
The signature gains an **additive, keyword-only** `before_round_hook=None`
(`Optional[Callable[[int, Team, Team], None]]`): `simulate_match(self, team_red,
team_blue, match_type="friendly", *, arena_map=None, before_round_hook=None) ->
Match`. Default `None` ⇒ **byte-unchanged** for every existing caller (preset
tournaments, sandbox, season play). Callable signature **`before_round_hook(round_number:
int, team_red, team_blue) -> None`** where `round_number` is `1` or `2` and the Teams
are **as passed into that round's internal `_simulate_and_flush_round` call** (round 2
receives the swapped order). The hook mutates the drawn Teams' `slot_*` FKs **in
memory** before the round sims. **Two insertion points:** immediately after
`match = Match.objects.create(...)` and **before** the round-1
`_simulate_and_flush_round(team_red, team_blue, ...)` call →
`if before_round_hook is not None: before_round_hook(1, team_red, team_blue)`; after
the round-1 column-copy block and **before** the round-2
`_simulate_and_flush_round(team_blue, team_red, ...)` call →
`if before_round_hook is not None: before_round_hook(2, team_blue, team_red)`
(**same `(team_blue, team_red)` order** `simulate_match` uses for round 2). The hook
fires **once per round** so the 2 Rounds of one Match get **independent** role
assignments (re-draw every Round); `_simulate_and_flush_round` reads `team.active_roster`
off the now-mutated `slot_*` FKs, so the in-memory rewrite takes effect for that
round's sim. **No change to `_simulate_and_flush_round` itself.**

**Engine seam — `play_next_node` draw branch + `_build_role_hook`
(`matches/tournament_engine.py`).** `play_next_node`'s single
`BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`
call becomes a `team_assembly`-keyed branch: when `tournament.team_assembly ==
"random_draw"`, build `hook = _build_role_hook(tournament)` and pass it as
`before_round_hook=hook`; the `else` branch is **byte-identical** to today (preset path
unchanged). Every other line of `play_next_node` (per-Match-atomic body, clinch,
advance, RR / Swiss / RR→DE guards, crown) is **untouched**. The NEW module-level
helper **`_build_role_hook(tournament) -> Callable`** reads
`tournament.role_assignment_mode` and returns a closure `(round_number, team_red,
team_blue)` that: (1) for each of the two drawn Teams loads its
`TournamentPlayerEntry` rows (`tournament.player_entries.filter(drawn_team=team)`) and
builds the tier→player_id map; (2) draws a **fresh per-Round RNG** `rng =
random.Random()` (default OS entropy, fresh every call — tournament sims are
non-deterministic); (3) **`random` mode:** call `build_random_role_assignment` **per
team independently**; **`per_tier` mode:** call `build_per_tier_role_assignment`
**once** and apply that single `{tier: slot}` bijection to BOTH teams' tier→player
maps; (4) rewrite **both** drawn Teams' `slot_*` FKs **in memory** from the resulting
`{slot_suffix: player_id}` maps (`team.slot_commander_id` … `team.slot_ammo_id`) —
**no `.save()`** (transient per-Round assignment; the durable truth is the
`TournamentPlayerEntry` tier + `drawn_team`). The role draw uses a fresh
`random.Random()`, **NOT the SIM-07 seed chain** — no SIM-07/08 interaction, no Score
Calibration re-baseline.

**Views / URLs (`matches/tournament_views.py`, `matches/tournament_urls.py`).** All new
URL names are **bare** (no `app_name`, mounted at `/tournaments/`).

- **CHANGED — `tournament_create`.** *(ANG-01f: now binds the strict `TournamentCreateForm`; a
  tampered `team_assembly` / `role_assignment_mode` is rejected, and "Player pool draw" preselects
  RR→DE but any format is allowed.)* Reads a new POST field **`team_assembly`**
  (forgiving fallback: only `"preset"` / `"random_draw"`, else `"preset"`) and
  **`role_assignment_mode`** (only `"random"` / `"per_tier"`, else `"random"`); stamps
  both via `Tournament.objects.create(...)`. The `format` select is **unchanged** — a
  `random_draw` Tournament uses `format="round_robin_double_elim"` (the `rrde_combo`
  wb/lb select is reused verbatim). For `random_draw` **participants are NOT chosen at
  create time** — the Tournament is created in `setup` with an **empty** pool, filled
  on the detail page. NEW create-form DOM ids: **`tournament-create-team-assembly`**
  (the `<select name="team_assembly">`), **`tournament-create-role-assignment-mode`**
  (the `<select name="role_assignment_mode">`, shown by the existing
  `tournamentCreateToggle` JS only when `team_assembly == "random_draw"`). Every
  existing create-form id is unchanged.
- **NEW — pool intake (three sources), draw, re-roll, hand-edit.** Mirror the
  LG-02a/a-2 Team-intake at **Player** granularity. All POST, `@transaction.atomic`,
  **setup-only** (reject with `messages.error` + redirect once `tournament.is_locked`).
  All create `TournamentPlayerEntry` rows (tier `null`, `drawn_team` `null` until the
  draw). Generated / CSV Players are created on the Free Agents Team via
  `teams.models.get_free_agents_team()`.

  | URL name | Path | View fn | What it does |
  |---|---|---|---|
  | `tournament_pool_add_existing` | `<id>/pool/add-existing/` | `tournament_pool_add_existing` | Add selected existing `Player`s as pool entries. |
  | `tournament_pool_generate` | `<id>/pool/generate/` | `tournament_pool_generate` | Generate N fresh Players via the LG-00 pure generator (`draw_stats` / `draw_preferred_roles`) on the Free Agents Team, add as pool entries. |
  | `tournament_pool_import` | `<id>/pool/import/` | `tournament_pool_import` | CSV import via the LG-00b `RosterImportForm` + `parse_roster_csv`; **each CSV row = one pool Player** (team-grouping IGNORED). |
  | `tournament_pool_remove` | `<id>/pool/remove/` | `tournament_pool_remove` | Remove a pool entry (by `player_id` / entry id) while in setup. |
  | `tournament_draw` | `<id>/draw/` | `tournament_draw` | Run / re-roll the draw: validate pool size, build drawn Teams + participants, persist. |
  | `tournament_draw_edit` | `<id>/draw/edit/` | `tournament_draw_edit` | Admin hand-edit of a drawn entry's `tier` / `drawn_team` (the variation mechanism). |

  Place these adjacent to the other `<int:tournament_id>/…` routes (after
  `import-participants/`). The existing **`tournament_lock`** view is **reused
  unchanged** to reach `active`: `lock_and_build()` takes the RR→DE branch over the
  drawn Teams (now `TournamentParticipant` rows), validating `>= 4` participants as
  today — **no `lock_and_build` change** (the draw must have produced participants +
  drawn Teams before lock).

- **CSV-for-a-player-pool reconciliation (`tournament_pool_import`).** The LG-00b /
  LG-02a-2 `parse_roster_csv` groups rows by team (`ParsedRoster.by_team`); a player
  pool needs players, not team-grouped rosters. So `tournament_pool_import` calls
  `parse_roster_csv(decoded_text)` (reuse — header validation, per-row coercion,
  bundled `RosterImportError`), **ignores `by_team`**, iterates `parsed.rows` (flat
  CSV-order) and treats **each `ParsedRow` as one pool Player** —
  `Player.objects.create(team=get_free_agents_team(), name=row.name,
  preferred_roles=row.preferred_roles, **row.profile, **row.stats)` then a
  `TournamentPlayerEntry`. The CSV `role` (slot intent) and `team` columns are **NOT
  used** (slots are per-Round draw assignments; team membership is the pool). Does
  **NOT** call `_check_db_slot_collisions` / `_apply_roster` (roster-slot helpers
  irrelevant to a flat pool). Error branch: `transaction.set_rollback(True)` + re-render
  the detail page (**HTTP 200**) with the bound form + `exc.errors` (mirrors
  `tournament_import_participants`).

- **`tournament_draw` persistence.** (1) Validate pool: `N =
  tournament.player_entries.count()`; require `N % 6 == 0` AND `N >= 24` (≥ 4 teams) —
  else `messages.error` + redirect, **no writes**. (2) Build the `(player_id,
  overall_rating)` pool list (`entry.player.overall_rating`), call `compute_draw(pool)`
  (pure). (3) **Re-roll cleanup:** if drawn Teams already exist, delete them
  (`is_draw_team=True` for this tournament) + their `TournamentParticipant` rows and
  null the entries' `tier` / `drawn_team` (idempotent — `compute_draw` is
  deterministic, so a re-roll reproduces the same split; hand-edits are the variation).
  (4) For each `DrawnTeamPlan`: create `Team.objects.create(name="<Draw Team N>",
  is_draw_team=True)`, set the 6 `slot_*` FKs from the tier-ordered `player_ids` via an
  **initial valid no-duplicate assignment** (e.g. tier order → `ROLE_SLOTS` order;
  satisfies the relaxed `roster_errors`), `team.save()`; create
  `TournamentParticipant(tournament=tournament, team=team, seed=N+1)` (1-based draw
  order / RR seed, mirrors LG-02a); fill each member entry's `tier` + `drawn_team`. (5)
  **Does NOT reassign `Player.team`** — drawn Teams reference borrowed Players via slot
  FKs only; `PlayerRoundState` references the real Player so career stats stay unified.

- **`_detail_context(tournament)` additions.** Keeps its existing 14 keys verbatim and
  adds (empty/defaulted for `preset`): **`team_assembly`** (`str`),
  **`role_assignment_mode`** (`str`), **`pool_entries`**
  (`list[TournamentPlayerEntry]`, `select_related("player", "drawn_team")`, ordered
  tier-then-player-id; `[]` for preset), **`pool_size`** (`int`), **`is_drawn`**
  (`bool` = `tournament.player_entries.filter(drawn_team__isnull=False).exists()`),
  **`pool_import_form`** (`RosterImportForm()`) and **`pool_import_row_errors`**
  (`list[RowError]`, default `[]`) — the player-pool CSV intake form + errors (parallel
  to the existing `import_form` / `import_row_errors`).

**Template (`templates/matches/tournament_detail.html`) — new surface, LOCKED DOM
ids.** Rendered **only** when `team_assembly == "random_draw"`. Pool intake (setup):
`tournament-pool-section` (wrapper); `tournament-pool-add-existing-form` / `-select` /
`-submit`; `tournament-pool-generate-form` / `-count` / `-mean` / `-std-dev` /
`-submit`; `tournament-pool-import-form` / `-file` / `-submit` / `-template-link` /
`-errors` + per-row `tournament-pool-import-error-{row_num}-{field|"row"}`;
`tournament-pool-table` (one row `tournament-pool-entry-{player_id}` each) with
per-entry `tournament-pool-remove-{player_id}`; `tournament-pool-size` (renders
`pool_size`); `tournament-pool-invalid-notice` (shown when `pool_size % 6 != 0 or
pool_size < 24`, carrying the substrings `divisible by 6` / `at least 24`). Draw:
`tournament-draw-form` / `-submit` (enabled only when pool size valid + setup);
`tournament-draw-reroll-submit` (re-roll, same endpoint, shown once drawn);
`tournament-draw-table` (one section `tournament-draw-team-{team_id}` per drawn team,
one row per member tagged with its `tier`); the hand-edit
`tournament-draw-edit-form` / per-entry `tournament-draw-edit-{player_id}`. **Reused
verbatim:** the lock control (`tournament-lock-form` / `-submit`), play controls
(`tournament-play-next-*` / `tournament-play-all-*`), and the champion banner
(`tournament-champion-banner`). Once drawn + locked, the Tournament renders the
existing RR→DE crosstable / cut-labels / DE-finals surfaces over the drawn Teams
**unchanged**.

**Admin (`matches/admin.py`).** `TournamentPlayerEntry` registered after the existing
tournament admins — `list_display = ("tournament", "player", "tier", "drawn_team")`.
The two new `Tournament` fields and `Team.is_draw_team` auto-surface on the existing
change forms; existing inlines reused; no existing registration touched.

**Migrations (no `RunPython`, no backfill — ADR-0004 disposable-sandbox precedent).**
**`matches/migrations/0040_tournament_random_draw.py`** (dep `0039_tournament_swiss`
+ the new `teams` migration — cross-app dependency, since
`TournamentPlayerEntry.drawn_team` and the draw both reference the new `Team` field):
ops in pinned order — `AddField(Tournament.team_assembly)` →
`AddField(Tournament.role_assignment_mode)` → `CreateModel(TournamentPlayerEntry)`
(incl. the `UniqueConstraint` + `Meta.ordering`). **`teams/migrations/00XX_team_is_draw_team.py`**
(next sequential `teams` number, resolved via `makemigrations teams`): a single
`AddField(Team.is_draw_team)`.

**Determinism / scope.** **The draw is deterministic** (straight-tiers + greedy
balance, no RNG — re-roll idempotent; admin hand-edit is the variation mechanism); the
**per-Round role draw is non-deterministic** (fresh `random.Random()` every Round). The
per-Match sims stay non-deterministic ⇒ **no SIM-07 / SIM-08 interaction, NO Score
Calibration re-baseline** (no simulation mechanics change — only which Player occupies
each role slot). **Players are referenced, not reassigned** — `Player.team` stays put;
ownership lives on `TournamentPlayerEntry`; a Player may be on draw teams across
different Tournaments but **never two in the same Tournament** (the
`unique(tournament, player)` constraint). **Scope-out (DEFERRED to LG-02x-2):** Duos /
Trios player pairs/triples on 6v6 teams, the `TournamentSubGroup` model, and
per-subgroup stat tracking — this slice is **single-Player pool only**.

**Tests:** `matches/tests/test_draw.py` (NEW — pure, no DB: `compute_draw`
straight-tier formation + greedy-balance to the weakest team + deterministic /
idempotent + rating-DESC / player-id-ASC sort + `ValueError` on `N % 6 != 0` and
`N < 24` + N=24/30/48 worked cases; `build_random_role_assignment` permutation of all
6 `ROLE_SLOTS` / one shuffle / no dup; `build_per_tier_role_assignment` `{tier: slot}`
bijection / one shuffle; `TestNoDjangoImportsLeaked`). `test_tournament_models.py`
(extend — `team_assembly` / `role_assignment_mode` choices + defaults;
`TournamentPlayerEntry` create / `unique(tournament, player)` rejection / CASCADE on
tournament delete / SET_NULL on team delete / `Meta.ordering`). `teams/tests/test_models.py`
(extend — relaxed roster rule: a draw team with borrowed Players has no "does not
belong" error, but a duplicate player OR a 3rd non-Scout role **still** errors, and a
non-draw team with a foreign player **still** errors; `Team.is_draw_team` default +
persistence). `test_simulation_view_paths.py` (extend — `before_round_hook=None` is
byte-unchanged; a hook is invoked once per round with `(round_number, team_red,
team_blue)`, round 2 receiving swapped `(team_blue, team_red)`; a hook rewriting
`slot_*` FKs changes the roster the round sims against). `test_tournament_engine.py`
(extend — a `random_draw` Tournament routes `play_next_node` through the hook path; a
`preset` Tournament's `simulate_match` call is unchanged, no hook). `test_tournament_views.py`
(extend — create-form selects + POST persistence + fallback; pool intake (existing /
generate / CSV) creates entries on the Free Agents Team; CSV error branch re-renders
200 with `exc.errors` and zero writes; `tournament_draw` validates `N % 6` / `N >= 24`,
builds drawn Teams + participants + fills tier/drawn_team, re-roll idempotent,
hand-edit mutates a single entry; lock reached via `tournament_lock` over drawn Teams;
the new `_detail_context` keys + pool/draw DOM ids render). `test_tournament_tasks.py`
(extend — `play_tournament_task` drains a `random_draw` RR→DE Tournament to a champion
under `CELERY_TASK_ALWAYS_EAGER`, **non-deterministic** — assert champion stamped +
`state="completed"`, never exact point totals). Tests assert on pure functions,
persisted row shapes, constraints, the hook contract, DOM ids — **never** on exact
simulated point totals.

**Locked names:** see the seam contract
[`.claude/worktrees/lg-02x-1-seam-contract.md`](../../../.claude/worktrees/lg-02x-1-seam-contract.md)
for the authoritative list of every name / signature / dict-key / DOM-id / literal
(`team_assembly` / `role_assignment_mode` + choices, `TournamentPlayerEntry`,
`compute_draw` / `DrawnTeamPlan` / `ROLE_SLOTS` / `build_random_role_assignment` /
`build_per_tier_role_assignment`, `simulate_match(before_round_hook=...)`,
`_build_role_hook`, the 7 view fns + URL names, `Team.is_draw_team`, the DOM ids +
`_detail_context` keys, the N-divisibility rule, and migrations
`0040_tournament_random_draw` + `teams 00XX_team_is_draw_team`).

## ANG-01f tournaments API (RR / Swiss / Player-pool notes)

The full slice (endpoints, codes, packages) is the **ANG-01f tournaments API** section of
[`api.md`](api.md). What changed for the formats in this doc:

- **Rename.** `Tournament.team_assembly` `"random_draw"` ⇒ **`"player_pool"`** ("Player pool draw";
  migration `0064_tournament_player_pool_and_active_play_job` rewrites the rows and reverses). The
  engine's role hook (`tournament_engine.play_specific_node`) and the detail template now key off
  `"player_pool"`. **Random Draw** is now a derived label (`tournament_services.detail.format_label`):
  `player_pool` + `round_robin_double_elim` reads "Random Draw" in the list, the detail badge
  `#tournament-format-label` and the API `format_label`; any other format reads its plain label.
  A drawn pool can be played as **any** of the five formats (each is tested to a champion through the
  API play-all; an odd drawn field on Swiss is a 400 at lock). `SeasonPhase.tournament_mode =
  "random_draw"` is unchanged.
- **Create form.** The team-assembly option reads "Player pool draw" and picking it preselects RR→DE
  (`tournamentAssemblyChanged` JS); the Manager may change the format.
- **Strict Forms, no forgiving parses.** `_parse_rrde_combo` and `_parse_swiss_rounds` are gone. The
  create page binds `tournament_services.create.TournamentCreateForm`: absent fields keep their old
  defaults (`4/0`, `0` = auto Swiss rounds), but an unknown `rrde_combo`, a negative / non-integer
  `swiss_rounds` or an unknown `format` is an error, flashed on the re-rendered form. Pool generate
  binds `PoolGenerateForm` (`count` 1..1000, `mean` 0..100, `std_dev` 1..40), pool add binds
  `PoolAddPlayersForm` (Account-scoped Players, not already pooled), and draw edit binds
  `DrawEntryForm` (`tier` 1..6, `drawn_team` one of this Tournament's drawn Teams — a leak fix).
- **Guards.** Every pool / draw write runs `require_pool_setup`: embedded ⇒ `tournament_embedded`,
  not in setup ⇒ `tournament_not_setup`, preset ⇒ "This Tournament does not use a Player-pool draw."
  (HTML flashes; API 409 / 400). Participant CSV import on a Player-pool Tournament is refused ("This
  Tournament uses a Player-pool draw; add Players to its pool instead."). The add-existing picker is
  scoped to the Account (`build_detail_context(t, *, user)`).
- **Single active run / embedded guard** apply to these formats too — see `tournaments-elimination.md`
  **ANG-01f** and ADR-0040 **Tournaments**.
- **Logged, not fixed** (`issues.md`): ANG01f5-D1 generated / imported pool Players still land on the
  global Free Agents Team; ANG01f5-D2 pool import skips the DB slot-collision check.
