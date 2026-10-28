# matches: Tournaments: elimination formats

LG-02a single elimination, LG-02a-2 CSV import + play-all, LG-02b best-of-N, LG-02b-2 Series escalation, LG-02c double elimination.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-02a sandbox single-elimination tournament

A **standalone sandbox Tournament** — a persisted single-elimination bracket
decoupled from any League / Season (`season`-less, no `generate_schedule`). Three
new models, one pure module, six views under a new `/tournaments/` mount, a
bracket-tree detail page, and a sandbox-nav entry. See
[ADR-0019](../../adr/0019-tournament-bracket-model.md) for the persisted
standalone-sandbox decision; seam contract:
[`.claude/worktrees/lg-02a-seam-contract.md`](../../../.claude/worktrees/lg-02a-seam-contract.md).
GLOSSARY.md `### Tournaments` carries the 8 locked terms (**Tournament** /
**Bracket** / **Bracket round** / **Bracket node** / **Bracket seed** /
**Seeding** / **Advancement** / **Bye**). Migration:
`matches/migrations/0033_tournament.py` (new models only — **no `RunPython`, no
backfill**, ADR-0004 precedent; `0032` was taken by `0032_league_free_agent_pool`).

**Models (`matches/models.py`, after `Season`).** `Tournament` (`name`,
`format` enum default `"single_elimination"` — present-but-single, extensible;
3-state `state` machine `setup` → `active` → `completed`; `created_at`;
`champion` FK to `teams.Team` SET_NULL, `related_name="tournaments_won"`).
Methods: `lock_and_build()` (`@transaction.atomic`, `setup` → `active`: validates
participant count **≥ 4**, builds + persists the `BracketNode` tree from the
current Seeding via `matches.bracket.build_bracket`, flips `state="active"`;
raises `django.core.exceptions.ValidationError` on count < 4 or wrong state — the
seeding-editable window closes here, mirroring `Season.start_season`'s draft→active
M2M lock); `is_locked` property (True iff `state != "setup"`);
`find_next_playable_node()` (delegates to `matches.bracket.find_next_node`).
`TournamentParticipant` (`tournament` CASCADE `related_name="participants"`,
`team` CASCADE `related_name="+"`, `seed` `PositiveIntegerField` — 1-based Bracket
seed, lower int = stronger; `Meta.ordering = ["seed"]` so participants iterate in
Seeding order; `UniqueConstraint`s `uniq_tournament_seed` + `uniq_tournament_team`).
`BracketNode` (`tournament` CASCADE `related_name="nodes"`; tree coords
`bracket_round` 1-based + `position` 0-based; team slots `team_a` / `team_b`
nullable SET_NULL; `seed_a` / `seed_b` carry each slot's Bracket seed alongside it
so the tie-break and bye carry-forward need no participant re-query; `match` FK to
`matches.Match` SET_NULL `related_name="bracket_node"` — NULL until played, a bye
node stays NULL; Advancement pointer `advances_to` self-FK `related_name="feeders"`
+ `advances_to_slot` `"a"`/`"b"`; `is_bye` flag; `winner` FK SET_NULL;
`UniqueConstraint` `uniq_tournament_round_position`). **A node = exactly one
2-round `Match`** (no series / best-of-N — deferred LG-02b).

**Pure module `matches/bracket.py`** owns bracket **structure + Seeding + bye
placement + tie-break math** — frozen import allowlist **`dataclasses`, `typing`,
`math`, `collections` ONLY** (NO Django / ORM / `random` / `datetime` / I/O /
logging), enforced by `matches/tests/test_bracket.py::TestNoDjangoImportsLeaked`
(subprocess fresh-import + `sys.modules` walk, mirroring
`matches/standings.py` / `matches/schedule_generator.py`). Frozen dataclasses
`BracketNodeSpec` / `ParticipantSpec`. Functions: `default_seed_order(team_ratings:
list[tuple[int, float]]) -> list[int]` (default Seeding = mean active-player
`overall_rating` **DESC**, then `team_id` ASC tiebreak — the SAME talent ranking
the LG-01c draft-preview standings use); `build_bracket(participants) ->
list[BracketNodeSpec]` (single-elim tree for **arbitrary N ≥ 4**; size = next
power of two ≥ N; standard `1vN, 2v(N-1), …` seed pairing; the top `(size − N)`
Bracket seeds get a round-1 **Bye**; wires `advances_to`/`advances_to_slot`, final
node `advances_to=None`); `find_next_node(nodes) -> dict | None` (lowest
`(bracket_round, position)` playable node — both slots filled, not bye, no
winner/match yet); `advance_winner(nodes, node_position, winner_id, winner_seed) ->
list[dict]` (parent-slot mutation dicts `{"bracket_round","position","slot",
"team_id","seed"}`); `resolve_bye_chain(nodes) -> list[dict]` (cascade byes at
build time into the next round, same mutation shape); `break_tie(seed_a,
best_round_score_a, seed_b, best_round_score_b) -> int` (**tie-break rule:** higher
best single-Round score advances, else the higher Bracket seed = **lower seed
int** — pure integer compare, no re-sim).

**View ↔ pure seam = ints / dicts ONLY.** The pure module never sees a `Team`,
`Match`, or `BracketNode` ORM instance and never returns a Django object. The view
flattens `BracketNode` rows to plain dicts via the private helper
`_node_to_dict(node) -> dict` (keys `bracket_round`, `position`, `team_a_id`,
`team_b_id`, `seed_a`, `seed_b`, `is_bye`, `match_id`, `winner_id`, `advances_to`
= `(bracket_round, position)` tuple or `None`, `advances_to_slot`) before calling
`find_next_node` / `advance_winner` / `resolve_bye_chain`; `default_seed_order`
gets `(team_id, mean_overall_rating)` tuples built from `Team.active_players` +
`Player.overall_rating`; `break_tie` gets four ints. The view applies the returned
mutations to the ORM.

**Views (`matches/tournament_views.py`, NEW) + URLs
(`matches/tournament_urls.py`, NEW — bare names, no `app_name`, mirrors
`season_urls.py`).** Mounted `path("tournaments/", include("matches.tournament_urls"))`
in the project `urls.py` after `matches/` (everything not under `/leagues/` or
`/seasons/` resolves to `app_mode == "sandbox"` via the LG-01k path-prefix
processor — **no processor change**). The six views / URL names:
`tournament_list` (GET, newest-first `order_by("-id")`); `tournament_create`
(GET form / POST `@transaction.atomic` — team source = **select existing** via
`Team.objects.regular()` + **generate** via `from teams.views import
_generate_teams` (the LG-01b cross-app seam, signature unchanged) — creates the
`Tournament(state="setup")` + `TournamentParticipant` rows with default Seeding).
**Roster-eligibility gate:** only Teams with a full valid roster
(`Team.is_valid_roster` — all 6 slots filled, no duplicate player) may enter a
Tournament. `available_teams` is the `Team.objects.regular()` list filtered to
`is_valid_roster` (slot FKs `select_related`-ed so the property adds no
per-Team query), so the select list never offers an incomplete-roster Team; a
tampered/stale POST of an ineligible id is re-validated server-side and rejected
with an error re-render (nothing created); and the generate path clamps
players-per-team to **>= 6** (`generate_ppt = max(6, generate_ppt)`) so it can
never create an unplayable participant *(ANG-01f: no longer clamped — `generate_ppt` < 6 is a
`min_value` form error, absent still means 6; the Team picker is now Account-scoped)*;
`tournament_detail` (GET-only, the bracket tree + seeding-edit form + play
controls); `tournament_reseed` (POST `@transaction.atomic`, persists a manually
reordered Seeding, **rejected once `is_locked`**); `tournament_lock` (POST,
`lock_and_build()`, `ValidationError` → redirect + `messages.error`);
`tournament_play_next` (POST `@transaction.atomic`, state must be `"active"` —
sims **ONE** Match `BatchSimulator().simulate_match(node.team_a, node.team_b,
match_type="tournament")`, resolves the winner incl. the `break_tie` fallback when
`match.winner is None`, Advances into the parent slot, stamps `champion` +
`state="completed"` on the final). Guard idiom: non-allowed method →
`HttpResponseNotAllowed([...])` as the first body line (mirrors `movement_heatmap`
/ `export_round_report`). Play is **synchronous game-by-game** (one POST per
node).

**Templates** (`templates/matches/tournament_{list,create,detail}.html`, each
extends `base.html`). Locked DOM ids: list — `tournament-list-table`,
`tournament-list-empty` (`No tournaments yet`), `tournament-create-link`, per-row
`state-badge` class; create — `tournament-create-form`, `tournament-create-name`,
`tournament-create-team-select` (multi-select of `Team.objects.regular()`),
`tournament-create-generate-count`, `tournament-create-generate-ppt` (defaults 6),
`tournament-create-submit`, `tournament-create-no-teams-notice`; detail — the
**bracket tree** container `tournament-bracket` with one column per Bracket round
`tournament-bracket-round-{n}` (1-based) and one node `tournament-node-{bracket_round}-{position}`
(bye node carries the `bye-node` class), the setup-only Seeding form
`tournament-seeding-form` / `tournament-seed-input-{team_id}` /
`tournament-seeding-submit`, play controls `tournament-lock-form` /
`tournament-lock-submit` (setup), `tournament-play-next-form` /
`tournament-play-next-submit` (active), `tournament-champion-banner` (completed,
substring `Champion`), and `tournament-detail-empty` (`No participants`).
`tournament_detail` context keys (frozen): `tournament`, `participants`, `rounds`
(`[{"bracket_round": int, "nodes": list[node_view_dict]}]`), `next_node`,
`is_locked`, `can_play`. Sandbox-nav: a flat anchor
`<a id="tournaments-nav-link" …>Tournaments</a>` in the `{% elif app_mode ==
"sandbox" %}` branch of `templates/base.html` (after `Maps`, before the tools/help
include).

**Admin (`matches/admin.py`, after `SeasonAdmin`).** `TournamentAdmin`
(`list_display` name/format/state/champion/created_at + `TournamentParticipantInline`
/ `BracketNodeInline`), `TournamentParticipantAdmin`, `BracketNodeAdmin`. No
existing registration touched.

**Tests.** `matches/tests/test_bracket.py` (pure-unit, no DB —
`TestDefaultSeedOrder`, `TestBuildBracketPowerOfTwo` N=4/8/16,
`TestBuildBracketWithByes` N=5/6/12, `TestFindNextNode`, `TestAdvanceWinner`,
`TestResolveByeChain`, `TestBreakTie`, `TestNoDjangoImportsLeaked`);
`matches/tests/test_tournament_models.py` (`lock_and_build` transition +
`ValidationError` on N<4, constraints, `find_next_playable_node`);
`matches/tests/test_tournament_views.py` (DOM ids + 200/302/404/405, setup→active→
completed, POST-generate via the **real** `_generate_teams` (no `mock.patch` so
signature drift fails loudly), forced-tie tie-break path, champion stamped on
final).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** CSV participant import +
async "play-all" / Celery (LG-02a-2); series / best-of-N nodes (LG-02b);
double-elimination / round-robin / RR→DE / Swiss (format enum extensible, only
`single_elimination` ships); in-League / in-Season embedding (Tournament is
standalone, `season`-less); batch-N tournament simulation; any
`simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per node); no per-Tournament arena-map config; no
backfill / `RunPython`; no GLOSSARY.md term beyond the 8 `### Tournaments` entries.

## LG-02a-2 CSV participant import + async play-all

Two ergonomics follow-ups over the shipped LG-02a Tournament — **CSV participant
import** (LG-00b reuse) and **async play-all** (Celery). Seam contract:
[`.claude/worktrees/lg-02a-2-seam-contract.md`](../../../.claude/worktrees/lg-02a-2-seam-contract.md).
**No model, no migration, no ADR** — per-node-atomic follows ADR-0016, CSV reuse
follows LG-00b; both reversible. **No edit** to `simulate_match`, the bracket
build/advance logic, or `teams/` (cross-app **read-only** imports only).

**Pure addition — `matches/bracket.py::stage_progress(nodes: list[dict]) ->
tuple[int, int]`.** STAGE-based progress for a bracket: `total` = max
`bracket_round` = ⌈log₂(size)⌉ Bracket rounds (0 when empty); `completed` = count
of rounds where **every non-bye node** (`not is_bye`) has `winner_id is not None`
(a round of all byes is vacuously complete). Reads ONLY `bracket_round` / `is_bye`
/ `winner_id` off the existing `_node_to_dict` flat dicts — respects the frozen
`dataclasses`/`typing`/`math`/`collections`-only allowlist (no new import;
`TestNoDjangoImportsLeaked` still passes).

**Engine module — `matches/tournament_engine.py` (NEW).**
`play_next_node(tournament) -> BracketNode | None` (`@transaction.atomic`)
**extracts** the per-node resolve/advance body out of the old inline
`tournament_play_next`: `find_next_playable_node()` → sim ONE Match
`BatchSimulator().simulate_match(..., match_type="tournament")` (deferred import)
→ resolve winner incl. the `break_tie` fallback on `match.winner is None` → stamp
`winner` + `save(update_fields=["match","winner"])` → `advance_winner` parent-slot
mutations applied to the ORM → stamp `champion` + `state="completed"` on the final
node; returns `None` when nothing is playable. **One node = one transaction**
(ADR-0016 per-node-atomic). The sync `tournament_views.py::tournament_play_next`
is **refactored** to keep its HTTP shell (POST-only, `state != "active"` guard) and
just call `play_next_node`; its inline sim/resolve/advance block is **deleted**.

**Celery task — `matches/tasks.py::play_tournament_task(self, tournament_id) ->
dict`** (`@shared_task(bind=True, name="matches.play_tournament")`, study
`play_season_task`). Loops `while play_next_node(tournament) is not None`, after
each node recomputing `stage_progress` and `self.update_state(state="PROGRESS",
meta={"completed": int, "total": int})`; returns final `{"completed", "total"}`
(**stage counts, NOT node counts**). Inactive-state (`state != "active"`)
early-returns the current stage progress (no-op). **NO outer
`@transaction.atomic`** — per-node atomicity comes from `play_next_node`'s
decorator (a mid-loop FAILURE leaves every already-resolved node committed →
resumable on re-invoke) (CI-01: no task closes its own DB connection; Celery's worker fixup does).

**Three new views/URLs (`tournament_views.py` / `tournament_urls.py`).**
`tournament_play_all` (POST-only, `HttpResponseNotAllowed(["POST"])` first line;
`play_tournament_task.apply_async((tournament_id,), retry=False)` → `JsonResponse({job_id,
tournament_id}, status=202)`; **409** `{"error": ...}` when `state != "active"`;
*(ANG-01f: also **409** while the Tournament already has a live play run or is embedded — see the
ANG-01f section)*;
**503** `{"error": ...}` when the enqueue raises `kombu.exceptions.OperationalError`
— a clean JSON broker-down error, NOT a 500 HTML page, so the UI never chokes on
the response. `retry=False` so an unreachable broker fails after one bounded attempt
instead of retry-hanging the request);
`tournament_play_status` (GET-only → `JsonResponse(_build_tournament_play_status_response(
AsyncResult(job_id), tournament_id=...))`, the locked 5-key JSON `{status,
completed, total, error, tournament_id}` mirroring
`matches/views.py::_build_play_status_response`, REUSING `_celery_state_to_job_status`
verbatim, stage counts with defensive `int(... or 0)` / `isinstance` guards);
`tournament_import_participants` (POST `@transaction.atomic`). URL order: the three
paths (`<id>/play-all/`, `<id>/play-status/<job_id>/`, `<id>/import-participants/`)
append after the existing `<id>/play-next/`.

**CSV import (full LG-00b reuse).** `tournament_import_participants` is
**setup-only** (`is_locked` ⇒ `messages.error` + redirect, no writes). Happy path:
`RosterImportForm(request.POST, request.FILES)` → `parse_roster_csv` →
`_check_db_slot_collisions` → `_apply_roster` returning `(created_teams,
appended_teams, player_count)` (**ANG-01a:** both now receive
`manager=manager_or_none(request)` — they resolve existing Teams only among the
Account's own + Unmanaged Teams, so a CSV naming another Account's Team creates a
**new Team stamped with the importer** instead of appending to the foreign one, and
tournament-created Teams are now stamped; see `docs/agents/teams/api.md` **Roster-import
ownership fix**); **ONLY `created_teams`** become
`TournamentParticipant`s (brand-new Teams ⇒ no `uniq_tournament_team` collision;
`appended_teams` are created/extended but **NOT auto-added**); then **re-seed the
whole field by talent** (`_team_mean_rating` → `default_seed_order`, rewriting
every `seed` via the same two-phase large-offset write `tournament_reseed` uses to
dodge `uniq_tournament_seed`) → redirect. Error branch (`RosterImportError` OR
form-invalid): `transaction.set_rollback(True)` + **re-render**
`tournament_detail.html` HTTP 200 with the bound form + `exc.errors` (zero writes).
New private `_detail_context(tournament)` helper shares the detail context between
`tournament_detail` and the import-error re-render — the **6 frozen LG-02a keys**
(`tournament` / `participants` / `rounds` / `next_node` / `is_locked` / `can_play`)
**plus** `import_form` (`RosterImportForm()` unbound) and `import_row_errors`
(`list[RowError]`, default `[]`).

**Cross-app / cross-module imports** (read-only, added to `tournament_views.py`):
`from teams.forms import RosterImportForm`; `from teams.roster_importer import
parse_roster_csv, RosterImportError`; `from teams.views import
_check_db_slot_collisions, _apply_roster`; `from matches.views import
_celery_state_to_job_status`. Template-download URL name `import_roster_template`
(LG-00b) is reused.

**Template surfaces (`templates/matches/tournament_detail.html`).** Setup-state
"Import Participants (CSV)" form (`<form enctype="multipart/form-data">`): DOM ids
`tournament-import-form` / `-file` / `-submit` / `-template-link` / `-errors`
(error block, only when `import_row_errors` non-empty) + per-row
`tournament-import-error-{row_num}-{field|"row"}` (mirrors LG-00b
`roster_import.html`). Active-state "Play All": `tournament-play-all-form` /
`-submit` / `-progress` (progress `hidden` by default), with a single inline
**1000 ms poll JS** block (mirrors the LG-01d seasons `dashboard.html`):
on submit it **immediately** disables the button + shows a `Starting…` indicator
(instant in-progress feedback before the first poll), reads the POST response as
**text first** and `try/catch`-parses JSON so a non-JSON error page (e.g. a 500
when the broker is down) shows a **friendly fallback** message — never the cryptic
`Unexpected token '<'` JSON-parse crash — then fetch-POST → `startPolling(job_id)`
against `tournament_play_status`, updates progress as `Running… stage X / Y` from
`completed`/`total`, `reload()` on `status === "complete"`, surfaces `error` +
re-enables on `status === "error"`, swallows network blips. The single-step
`tournament-play-next-form` is **unchanged**.

**Job-term extension.** The GLOSSARY.md **Job** term gains a **4th kind** — a
**Play Tournament job** (play every remaining decisive Bracket node to a champion;
progress = completed Bracket stage) + the `/tournaments/<id>/play-all/` URL. **No
new term** — the **Roster import** term is reused unedited.

**Determinism / scope-out (LOCKED).** **Non-deterministic** — `simulate_match`
draws fresh per-round seeds, so Play Tournament games are NOT
master-seed-replayable: **no SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline**. No CSV preview/commit UI, no per-tournament arena map, no async on
the single-step `tournament-play-next` (stays synchronous). Tests:
`test_bracket.py` (extend — `TestStageProgress` + purity assertion),
`test_tournament_engine.py` (NEW), `test_tournament_tasks.py` (NEW, under
`CELERY_TASK_ALWAYS_EAGER`), `test_tournament_views.py` (extend).

## LG-02b best-of-N series nodes

Generalises a **Bracket node** from holding **one** 2-round `Match` to holding a
best-of-N **Series** of Matches — the node Advances only when one Team **clinches**
the Match-win majority. Builds directly on LG-02a (the persisted single-elim
bracket) and LG-02a-2 (the per-Match engine + async play-all). See
[ADR-0020](../../adr/0020-best-of-n-series-bracket-nodes.md) for the
Series-via-through-model decision; seam contract:
[`.claude/worktrees/lg-02b-seam-contract.md`](../../../.claude/worktrees/lg-02b-seam-contract.md).
GLOSSARY.md `### Tournaments` carries the **Series** / **Series length** terms
(added at grilling time — reuses the existing **Bracket node** / **clinch** /
**Advancement** vocabulary, no new glossary entry beyond those two). Migration:
`matches/migrations/0034_*` (ops in **pinned order** `AddField(Tournament.
series_length)` → `CreateModel(SeriesMatch)` → `RemoveField(BracketNode.match)`
— **no `RunPython`, no backfill**, ADR-0004 disposable-sandbox precedent).

**Model changes (`matches/models.py`).** New `Tournament.series_length`
(`PositiveSmallIntegerField`, choices `(1, "Best of 1")`/`(3, "Best of 3")`/
`(5, "Best of 5")`, `default=1`) — set at create-time only, **immutable once the
Tournament leaves `setup`** (the existing `lock_and_build` `state != "setup"`
guard freezes it on the setup→active transition; no view rewrites it on a
non-`setup` Tournament). **Bo1 (`series_length == 1`) is byte-equivalent to
LG-02a** — one Match, clinch threshold 1, identical Advancement. New `SeriesMatch`
through-model (appended after `BracketNode`): `node` FK CASCADE
`related_name="series_matches"` (deleting a node drops its Series); `match` FK to
`matches.Match` SET_NULL/nullable `related_name="series_match"` (mirrors the old
`BracketNode.match` semantics — deleting a Match must not cascade the Series row);
`game_number` 1-based `PositiveIntegerField`; `winner` FK to `teams.Team` SET_NULL
`related_name="+"` (the per-Match decisive Team — `match.winner` or the `break_tie`
result when `match.winner is None`); `Meta.ordering=["game_number"]` +
`UniqueConstraint(fields=["node","game_number"], name="uniq_seriesmatch_node_game")`
(one row per (node, game)). **The node's win tally is DERIVED** by counting
`SeriesMatch.winner` rows per team-slot — **counters are never stored**;
`node.winner` is stamped only on clinch. The LG-02a `BracketNode.match` FK
(`related_name="bracket_node"`) is **dropped wholesale** — the per-Match link now
lives on `SeriesMatch.match`, every old reader of `node.match` / `node.match_id` /
the `match_id` dict key moves to the Series-derived path. No alias retained.

**Pure `matches/bracket.py` additions** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist unchanged — both functions add **no new import**;
`TestNoDjangoImportsLeaked` still passes). `clinch_threshold(series_length: int)
-> int` = `(series_length // 2) + 1` (Bo1→1, Bo3→2, Bo5→3; pure integer math, no
odd-ness validation — callers only pass the locked `1`/`3`/`5`). `series_winner_slot(
wins_a: int, wins_b: int, series_length: int) -> Optional[str]` returns `"a"` when
`wins_a >= threshold`, `"b"` when `wins_b >= threshold`, else `None` (Series still
undecided) — `wins_a` checked **first** so the function is total and deterministic
on every integer input (a malformed both-at-threshold input resolves to `"a"`
rather than raising; odd N + Series-stops-on-clinch makes both-at-threshold
unreachable in practice). `_node_to_dict` gains **3 derived keys** — `wins_a` /
`wins_b` (`sum(1 for sm in node.series_matches.all() if sm.winner_id ==
node.team_a_id / node.team_b_id)`) and `series_length` (`node.tournament.
series_length`) — and **drops `match_id`** (the field is gone); the caller
prefetches `series_matches` + the tournament's `series_length` so no per-node N+1.
The `find_next_node` playable predicate swaps the old `winner_id IS NULL AND
match_id IS NULL` checks for **`series_winner_slot(wins_a, wins_b, series_length)
is None`** — a node is playable iff both slots are filled, it is not a bye, and the
Series is not yet clinched (for Bo1 this is exactly the old behaviour:
`series_winner_slot(0, 0, 1) is None` ⇒ playable until the first Match is recorded).
**`stage_progress`, `build_bracket`, `advance_winner`, `resolve_bye_chain`,
`break_tie`, `default_seed_order`, and the two dataclasses are unchanged** — in
particular `stage_progress` still reads `winner_id` (stamped only on clinch), so it
keeps reporting Bracket-round completion with zero edits.

**Engine rewrite (`matches/tournament_engine.py::play_next_node`).** Signature
unchanged (`play_next_node(tournament) -> BracketNode | None`, `@transaction.
atomic`), but the **transaction boundary is now per-MATCH** (one Series Match = one
atomic commit) — extends ADR-0016's per-node-atomic to per-Match-atomic. Algorithm:
`find_next_playable_node()` (now §2d-clinch-aware, skips a clinched node) → compute
the current derived tally from existing `SeriesMatch` rows → sim **ONE** Match
`BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`
(**sides fixed across the Series** — `team_a`/`team_b` argument order constant,
no home/away alternation) → resolve the per-Match decisive winner exactly as
LG-02a-2 (`break_tie(node.seed_a, best_a, node.seed_b, best_b)` on `match.winner is
None`, mapping the seed back to the Team) → `SeriesMatch.objects.create(node=node,
match=match, game_number=node.series_matches.count() + 1, winner=match_winner)` →
recompute the tally → `slot = series_winner_slot(wins_a, wins_b, node.tournament.
series_length)`. **If `slot is None`** (Series not yet clinched) ⇒ **return `node`
now** (no `node.winner` write, no Advancement — the next call resolves the next
Match of the same node). **On clinch** (`slot` is `"a"`/`"b"`): set `node.winner` +
`winner_seed`, `save(update_fields=["winner"])` (the list **drops `"match"`** — no
longer a `BracketNode` field), build the flat `_node_to_dict` list, `advance_winner`
into the parent slot, and stamp `champion` + `state="completed"` when the clinched
node is the final (`advances_to_id is None`). Returns `None` when nothing is
playable. **Bo1 equivalence:** step creates game 1, `series_winner_slot(1, 0, 1) ==
"a"` ⇒ clinch on the first Match ⇒ identical single-Match advance — the only
structural difference vs LG-02a-2 is the played `Match` lives on a `SeriesMatch`
row, not `BracketNode.match`. **Callers unchanged in signature/route:**
`tournament_play_next`, `play_tournament_task` (the Celery `while play_next_node(...)
is not None` loop now iterates **once per Match**, so a Bo3/Bo5 drains over more
steps), `tournament_play_all`, `tournament_play_status` all keep their URLs +
5-key status JSON; `stage_progress` still reports Bracket-round completion.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): reads a new POST field `series_length` (parsed to int;
invalid/absent ⇒ default `1`; only `1`/`3`/`5` accepted, anything else falls back
to `1` — forgiving-fallback precedent; *ANG-01f: absent still means `1`, any other value is now
rejected*), stamped via `Tournament.objects.create(...,
series_length=series_length)`; rendered as a `<select name="series_length">` with
DOM id **`tournament-create-series-length`** (options Bo1 selected / Bo3 / Bo5),
placed in `tournament-create-form` before the submit. Detail page (`_build_rounds`
/ `tournament_detail.html`): each node view-dict gains derived `wins_a` / `wins_b`
(counted from the node's `SeriesMatch` rows, `series_matches` prefetched) and
**drops the `match` key** (per-node link is now per-SeriesMatch — the template
renders a "View match" link per played Series Match, or omits it); a per-node
**Series-score** element with DOM id **`tournament-node-series-score-{bracket_round}-
{position}`** renders the running `{{ node.wins_a }}–{{ node.wins_b }}` (en-dash,
e.g. `2–1`) for every non-bye node (a Bo1 node reads `1–0`/`0–1`/`0–0`). The
**champion** still surfaces via the unchanged `tournament-champion-banner`;
`tournament.series_length` is read directly off the `tournament` object in the
template (no new context key — the frozen `_detail_context` keys are unchanged,
only the shape of each `rounds[*].nodes[*]` dict changes).

**Tests.** `matches/tests/test_bracket.py` (extend — `TestClinchThreshold`,
`TestSeriesWinnerSlot`, Series cases on `TestFindNextNode`, purity still green);
`test_tournament_models.py` (extend — `SeriesMatch` create/ordering/
`uniq_seriesmatch_node_game`/CASCADE/SET_NULL, `series_length` default + choices +
state-immutability, `_node_to_dict` derived keys with no `match_id`);
`test_tournament_engine.py` (extend — one `SeriesMatch` per call + no `node.winner`
until clinch, clinch→advance→champion, Bo1 one-call equivalence, per-Match
`break_tie`, atomicity); `test_tournament_views.py` (extend — create-form select
+ POST persistence + fallback, detail Series-score element); `test_tournament_
tasks.py` (extend — `play_tournament_task` drains a Bo3 to a champion, Advance only
on clinch). Tests assert on the pure functions, `SeriesMatch` rows, `node.winner` /
Advancement, and DOM ids — **never on exact simulated point totals**
(non-deterministic).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **per-Bracket-round Series
escalation** (Bo1 early → Bo5 final — LG-02b applies a single per-Tournament
`series_length` to every node; deferred to LG-02b-2); **home/away side alternation**
(sides fixed `team_a` red / `team_b` blue every Match); **deterministic /
master-seed-replayable Series** (`simulate_match` draws fresh per-round seeds ⇒
**non-deterministic, no SIM-07/08, NO Score Calibration re-baseline**); any
`simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per Match); **backfill / `RunPython`** (none —
pure schema ops, ADR-0004); a **Series-level tiebreaker** (odd N always clinches;
ties broken per-Match by the unchanged `break_tie`); **dead-rubber Matches** (the
Series stops the moment a Team clinches); any new GLOSSARY.md term beyond
**Series** / **Series length** (already written).

## LG-02b-2 per-Bracket-round series escalation

Generalises the LG-02b best-of-N **Series length** from a **single flat
per-Tournament value applied to every node** into a **per-Bracket-round** value
anchored to **depth from the final** — Bo1 early rounds → Bo3 semis → Bo5 final,
or any independent mix. Builds directly on LG-02b: the pure clinch engine
(`clinch_threshold`, `series_winner_slot`, `count_series_wins`, the `SeriesMatch`
through-model, the per-Match-atomic `play_next_node` body) is **UNCHANGED** —
**only the *source* of the `series_length` argument moves from tournament-level to
node-level**. [ADR-0020](../../adr/0020-best-of-n-series-bracket-nodes.md) was
**extended** for the per-round escalation (do NOT re-write it); seam contract:
[`.claude/worktrees/lg-02b-2-seam-contract.md`](../../../.claude/worktrees/lg-02b-2-seam-contract.md).
GLOSSARY.md `### Tournaments` carries **Series length** (revised) + **Series
escalation** (added at grilling time — both already written, not re-touched).
Migration: `matches/migrations/0035_*` (ops in **pinned order**
`RemoveField(Tournament.series_length)` → 4× `AddField(Tournament.*_series_length)`
→ `AddField(BracketNode.series_length)` — **no `RunPython`, no backfill**,
ADR-0004 disposable-sandbox precedent; dep `0034_tournament_series`).

**Depth-from-final anchoring.** A node's Series length is resolved from its
**depth below the final**, `depth = total_rounds - bracket_round` (where
`total_rounds = max(spec.bracket_round …)` for the built bracket): **depth 0** =
the final, **depth 1** = semifinal, **depth 2** = quarterfinal, **depth ≥ 3** =
every earlier round (all collapse to the single `earlier` slot — no fifth tier).
**No monotonicity constraint** — the four slots may be any of `{1,3,5}` in any
order (a Bo5 quarterfinal feeding a Bo1 final is permitted; the model does not
enforce escalation, the user picks four independent values).

**Model changes (`matches/models.py`).** The LG-02b `Tournament.series_length`
flat field is **DROPPED wholesale** — no alias, no property shim; every reader
moves to `node.series_length` (see below). **ADD four
`PositiveSmallIntegerField`s** to `Tournament`, declared in the block where
`series_length` lived, in order `final_series_length`, `semifinal_series_length`,
`quarterfinal_series_length`, `earlier_series_length` — each with the identical
choices `(1, "Best of 1")`/`(3, "Best of 3")`/`(5, "Best of 5")` and `default=1`,
set at create-time only and **immutable once the Tournament leaves `setup`** (the
resolved N is frozen onto each node at lock time; the four fields are never
re-read after lock). **Bo1-everywhere (all four `1`, the migration default) is
byte-equivalent to LG-02b/LG-02a** — every node stamped `1`,
`series_winner_slot(1, 0, 1) == "a"`, single-Match clinch. **ADD
`BracketNode.series_length`** (`PositiveSmallIntegerField`, `default=1`, **no
choices** — it carries the already-resolved int, mirroring how `seed_a`/`seed_b`
carry resolved ints without choices). It is the **resolved** best-of-N for the
node, stamped at lock time on **every** persisted node including bye nodes (bye
nodes get a depth-resolved value but are **inert** — the engine skips `is_bye`).
`SeriesMatch` (model, `related_name="series_matches"`, `uniq_seriesmatch_node_game`,
`Meta.ordering=["game_number"]`) and `count_series_wins(series_matches, team_a_id,
team_b_id) -> tuple[int, int]` are **unchanged**.

**`lock_and_build` stamps the node (`matches/models.py`).** Inside the existing
`@transaction.atomic` `lock_and_build`, after `build_bracket(...)` produces
`specs`, compute `total_rounds = max(spec.bracket_round for spec in specs)` and add
the kwarg `series_length=series_length_for_round(spec.bracket_round, total_rounds,
final=self.final_series_length, semifinal=self.semifinal_series_length,
quarterfinal=self.quarterfinal_series_length, earlier=self.earlier_series_length)`
to the **existing** `BracketNode.objects.create(...)` loop (both `spec.bracket_round`
and `total_rounds` are in scope there — no follow-up pass needed; `series_length_for_round`
is imported alongside the existing `build_bracket`/`resolve_bye_chain`/`ParticipantSpec`
block). This stamps EVERY persisted node incl. byes; the `resolve_bye_chain` cascade
and `advances_to` wiring passes are untouched.

**Pure `matches/bracket.py` addition** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist unchanged — the new function adds **no new import**;
`TestNoDjangoImportsLeaked` still passes). NEW
`series_length_for_round(bracket_round: int, total_rounds: int, *, final: int,
semifinal: int, quarterfinal: int, earlier: int) -> int`: `bracket_round` and
`total_rounds` are **positional**, the four slot args are **keyword-only** (after
`*`). Algorithm (locked, pure, total, never raises): `depth = total_rounds -
bracket_round`; `depth == 0` → `final`; `depth == 1` → `semifinal`; `depth == 2`
→ `quarterfinal`; **`else` (depth ≥ 3, or any defensive out-of-range value) →
`earlier`** (the `if/elif/elif/else` chain makes `earlier` the catch-all; no
validation of the four slot values — callers pass the locked 1/3/5 choices).
`clinch_threshold`, `series_winner_slot`, `count_series_wins`, `find_next_node`,
`build_bracket`, `advance_winner`, `resolve_bye_chain`, `break_tie`,
`stage_progress`, `default_seed_order`, and the two dataclasses are all
**unchanged** — `find_next_node` still reads the `series_length` dict key (now
sourced from `node.series_length` via `_node_to_dict`), no edit to it.

**Seam read-source swap.** The clinch math is untouched; the ONLY change is
*where the `series_length` argument comes from* — tournament-level → **node-level**:
- `_node_to_dict(node)` keeps every key it has today; its `"series_length"` value
  now reads `node.series_length` (was `node.tournament.series_length`). `wins_a` /
  `wins_b` (via `count_series_wins`) and `advances_to` (the self-FK) are
  unchanged. Post-swap `_node_to_dict` has **no** `node.tournament` access.
- `tournament_engine.py::play_next_node` (`@transaction.atomic`, body otherwise
  verbatim): the step-6 clinch check reads `series_winner_slot(wins_a, wins_b,
  node.series_length)` (was `node.tournament.series_length`). Every other step
  (find playable node, sim ONE Match, `break_tie` fallback,
  `SeriesMatch.objects.create`, recompute via `count_series_wins`, stamp
  `node.winner` + `save(update_fields=["winner"])`, `advance_winner`,
  `champion`/`state="completed"` on the final) is unchanged. Because no flatten/
  advance-path code reads `node.tournament` post-swap, `select_related("tournament")`
  **may be dropped** from the `play_next_node` flat-list build and
  `find_next_playable_node` (keep `"advances_to"` + the `series_matches` prefetch)
  — a perf nicety, **not pinned** (no query-count assertion; leaving it in is also
  fine). The Code agent confirms no residual `node.tournament` reader before dropping.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): the single `series_length` POST parse is replaced by
**four** — POST fields `final_series_length` / `semifinal_series_length` /
`quarterfinal_series_length` / `earlier_series_length`, each int-coerced with a
forgiving fallback to `1` on `TypeError`/`ValueError` then forced into `{1,3,5}`
(anything else → `1`) **independently**, **no monotonicity constraint** (*ANG-01f: absent ⇒ `1`
still, but a present value outside 1 / 3 / 5 is now a form error*); the
create call passes all four (`Tournament.objects.create(name=..., state="setup",
final_series_length=..., semifinal_series_length=..., quarterfinal_series_length=...,
earlier_series_length=...)`). Rendered as **four `<select>`s** (each Bo1/Bo3/Bo5,
**Bo1 selected by default**) in `tournament-create-form` before the submit, with
locked DOM ids `tournament-create-final-series-length` /
`tournament-create-semifinal-series-length` /
`tournament-create-quarterfinal-series-length` /
`tournament-create-earlier-series-length` (the old single
`tournament-create-series-length` id is **removed** — no element carries it).
Detail page (`_build_rounds` / `tournament_detail.html`): each node view-dict
**gains** `series_length: int` ← `node.series_length` (read straight off the node
row the loop already iterates); `_detail_context` keeps its frozen LG-02a/LG-02a-2
keys verbatim (`tournament`, `participants`, `rounds`, `next_node`, `is_locked`,
`can_play`, `import_form`, `import_row_errors`) — **no new top-level context key**.
For each **non-bye** node the template renders a Bo-N label beside the existing
`tournament-node-series-score-{br}-{pos}` element, locked DOM id
**`tournament-node-series-length-{bracket_round}-{position}`**, **label text shape
`Bo{n}`** (`Bo{{ node.series_length }}` — e.g. `Bo1`, `Bo3`, `Bo5`); bye nodes have
no Series and get no such label. The existing series-score element and the
`tournament-champion-banner` are unchanged.

**Admin (`matches/admin.py`).** The four new `Tournament` fields and
`BracketNode.series_length` **auto-surface** in the default change forms (editable
`PositiveSmallIntegerField`s with `choices` render as `<select>`s with no
`fields`/`fieldsets` declaration). **No `list_display` change** on `TournamentAdmin`
or `BracketNodeAdmin` (existing tuples stay verbatim).

**Non-deterministic** (`simulate_match` draws fresh per-round seeds) ⇒ **no SIM-07/
SIM-08 interaction, NO Score Calibration re-baseline** (no simulation mechanics
change). **Scope-out (LOCKED — do NOT build):** per-node arbitrary override UI
(escalation is depth-anchored via four slots only); monotonicity enforcement
(none); home/away side alternation (sides fixed); any clinch-engine /
`simulate_match` change (consumed verbatim); deterministic / master-seed-replayable
Series; backfill / `RunPython`; a fifth depth tier (depth ≥ 3 collapse to
`earlier`); any new GLOSSARY.md term / new ADR (Series length revised + Series
escalation added already; ADR-0020 already extended).

**Locked names.** Models: **DROP** `Tournament.series_length`; **ADD**
`Tournament.final_series_length` / `.semifinal_series_length` /
`.quarterfinal_series_length` / `.earlier_series_length` (each
`PositiveSmallIntegerField`, choices `1`/`3`/`5`, default `1`) + `BracketNode.
series_length` (`PositiveSmallIntegerField`, default `1`, no choices, stamped at
`lock_and_build`); migration `matches/migrations/0035_*` (dep
`0034_tournament_series`; ops `RemoveField(Tournament.series_length)` → 4×
`AddField(Tournament.*)` → `AddField(BracketNode.series_length)`; no `RunPython`).
Pure: `series_length_for_round(bracket_round, total_rounds, *, final, semifinal,
quarterfinal, earlier) -> int` (`depth = total_rounds - bracket_round`; 0→final,
1→semifinal, 2→quarterfinal, ≥3→earlier; four slot args keyword-only). Engine:
`play_next_node` clinch check reads `node.series_length`; `select_related(
"tournament")` droppable. Create: POST `final_series_length` /
`semifinal_series_length` / `quarterfinal_series_length` / `earlier_series_length`
(int-coerced, forced into `{1,3,5}`, forgiving fallback `1`, no monotonicity);
selects `tournament-create-{final,semifinal,quarterfinal,earlier}-series-length`
(Bo1 default); old `tournament-create-series-length` removed. Detail:
`_build_rounds` node dict gains `series_length`; per-non-bye-node Bo-N label
`tournament-node-series-length-{bracket_round}-{position}`, text `Bo{n}`.

**Tests.** `matches/tests/test_bracket.py` (extend — `series_length_for_round`
depth boundaries + N=4/8/16 worked cases + keyword-only purity; clinch helpers +
`TestNoDjangoImportsLeaked` still green); `test_tournament_models.py` (extend/migrate
— `lock_and_build` stamps `node.series_length` per depth **incl. byes**, the four
new fields exist/default `1`/carry the 1/3/5 choices, `BracketNode.series_length`
exists/defaults `1`, the old `Tournament.series_length` field is **gone**,
`_node_to_dict` reads `node.series_length`); `test_tournament_views.py` (extend/
migrate — four selects by DOM id default Bo1 + old id absent, POST persists all
four, independent forgiving fallback, detail per-non-bye Bo-N label by DOM id);
`test_tournament_engine.py` (extend/migrate — node reads its **own**
`series_length` not `node.tournament.*`, Bo3 clinches at 2, Bo1 unchanged);
`test_tournament_tasks.py` (migrate the `_active_series_tournament` helper to the
four-field shape). Tests assert on the pure function, the stamped
`BracketNode.series_length`, the four `Tournament` fields, the absence of the old
field, the DOM ids, and `node.winner`/Advancement — **never on exact simulated
point totals** (non-deterministic).

## LG-02c double-elimination tournaments

Extends the LG-02a/b single-elimination `BracketNode` tree into **two coupled
brackets** — a **Winners bracket** and a **Losers bracket** joined by a **Grand
final with Bracket reset** — as a second `Tournament.format` enum value driven by
a new pure builder, hosting **both** sub-brackets in the *existing* `BracketNode`
table (one table + a sub-bracket tag, **not** a second `LoserBracketNode` model).
The single-elim path is **byte-unchanged** end to end. Builds directly on LG-02b
(the `SeriesMatch` clinch engine) and LG-02b-2 (depth-from-final escalation, here
re-anchored to depth-from-Grand-final). See
[ADR-0021](../../adr/0021-double-elimination-bracket.md) for the
two-bracket-one-table / Bracket-reset / naive-same-position-drop decision and its
rejected alternatives (separate LB table, single GF no reset, power-of-two-only,
per-node Series override); seam contract:
[`.claude/worktrees/lg-02c-seam-contract.md`](../../../.claude/worktrees/lg-02c-seam-contract.md).
GLOSSARY.md `### Tournaments` carries **Winners bracket** / **Losers bracket** /
**Drop** / **Grand final** / **Bracket reset** (added at grilling time — already
written, not re-touched; ADR-0021 likewise already written). Migration:
`matches/migrations/0036_*` (dep `0035_tournament_series_escalation`; ops in
**pinned order** `AlterField(Tournament.format)` — choices-only widen, no DB-level
enforcement, included so `makemigrations --check` is clean → 3×
`AddField(BracketNode.bracket_type / loser_advances_to / loser_advances_to_slot)`
→ `RemoveConstraint(uniq_tournament_round_position)` →
`AddConstraint(uniq_tournament_bracket_round_position)` — **no `RunPython`, no
backfill**, ADR-0004 disposable-sandbox precedent).

**Model changes (`matches/models.py`).** `Tournament.FORMAT_CHOICES` gains
`("double_elimination", "Double elimination")` — the `format` field declaration is
otherwise unchanged (`CharField(max_length=32)`, `default="single_elimination"`).
`BracketNode` gains three fields: **`bracket_type`** (`CharField(max_length=12)`,
choices `"winners"`/`"losers"`/`"grand_final"`, **`default="winners"`** so
single-elim rows default cleanly — the exact strings are LOCKED; the engine
ordering, the view section split, and the DOM ids all key on them); a
**loser-destination** pointer **`loser_advances_to`** (self-FK, `null=True`,
`on_delete=SET_NULL`, related_name **`"loser_feeders"`** — parallels the winner
`advances_to` / related_name `"feeders"`) carrying where **THIS node's loser**
Drops; and **`loser_advances_to_slot`** (`CharField(max_length=1)`, choices
`a`/`b`, nullable). LB nodes set `loser_advances_to = NULL` (their loser is
eliminated); single-elim WB nodes likewise NULL (loser eliminated exactly as
today — byte-unchanged). The LG-02a `uniq_tournament_round_position` constraint
(fields `["tournament", "bracket_round", "position"]`) is **removed** and replaced
by **`uniq_tournament_bracket_round_position`** which **adds `bracket_type`** to
the field tuple — so a WB and an LB node may now share `(bracket_round, position)`
while a duplicate within one `bracket_type` is still rejected. `Meta.ordering`
stays `["bracket_round", "position"]` (the engine re-sorts via `find_next_node`'s
total order; the view groups by `bracket_type`). `SeriesMatch` /
`count_series_wins` / the `series_matches` join are **UNCHANGED**.

**`lock_and_build` branches on `format` (`matches/models.py`).** A **single
dispatch** at the top of the `@transaction.atomic` build: `if self.format ==
"double_elimination":` build via `build_double_elim_bracket([ParticipantSpec(...)
for p in participants])`, **else** `build_bracket(...)` (the existing path,
byte-unchanged). The persist loop, the `advances_to` wiring pass, the
`resolve_bye_chain` cascade pass, and the `series_length` stamping pass are
**shared** across both formats, with three DE-only additions: (1) persist
`bracket_type=spec.bracket_type` on every `BracketNode.objects.create(...)`; (2) a
**third wiring pass** (after the `advances_to` pass) wires the
`loser_advances_to` self-FK from `spec.loser_advances_to` — a **triple**
`(bracket_type, bracket_round, position)` coord, since a WB→LB Drop crosses
brackets — plus `loser_advances_to_slot = spec.loser_advances_to_slot` (the
`node_by_pos` key becomes the triple for DE; single-elim may keep the 2-tuple or
adopt `("winners", …)` — internal, not asserted); (3) `series_length` stamping
uses **`series_length_for_depth(spec.depth, …)`** for DE (the spec carries `depth`
directly) versus the existing `series_length_for_round(spec.bracket_round, …)` for
single-elim, which is **byte-unchanged**. Every DE node — **including byes** — is
stamped. The `>= 4` participant guard, the `state != "setup"` guard, and the
`state="active"` flip are unchanged. `find_next_playable_node` keeps its
signature; its prefetch **widens** to `select_related("advances_to",
"loser_advances_to")` so `_node_to_dict` reads the loser pointer without an N+1,
and the match-back loop adds `bracket_type` to the comparison key.

**`_node_to_dict` gains three keys (`matches/models.py`).** On top of every
existing key it adds **`bracket_type`** (`node.bracket_type`),
**`loser_advances_to`** — a **3-tuple** `(node.loser_advances_to.bracket_type,
.bracket_round, .position)` or `None` — and **`loser_advances_to_slot`**
(`"a"`/`"b"`/`None`). **Deliberate asymmetry (LOCKED):** the existing
`advances_to` key **stays a 2-tuple** `(bracket_round, position)` (back-compat with
single-elim `advance_winner`, whose winner stays in the same bracket), while
`loser_advances_to` is a **3-tuple** carrying the destination bracket (the Drop
crosses brackets). For single-elim rows the three new keys read
`("winners", None, None)` ⇒ no downstream behaviour change.

**Pure `matches/bracket.py` additions** (frozen `dataclasses`/`typing`/`math`/
`collections`-only allowlist **unchanged** — the new functions add **no new
import**; `TestNoDjangoImportsLeaked` stays green). `BracketNodeSpec` gains four
**appended-with-defaults** fields (`bracket_type="winners"`,
`loser_advances_to=None` 3-tuple `(bracket_type, round, position)`,
`loser_advances_to_slot=None`, `depth=None` — distance-to-GF1) so `build_bracket`'s
existing first-10-field construction stays valid **byte-for-byte**.
**`series_length_for_depth(depth, *, final, semifinal, quarterfinal, earlier) ->
int`** is extracted as the pure depth→slot dispatch (0→final, 1→semifinal,
2→quarterfinal, ≥3→earlier via an if/elif catch-all — total, never raises; the
four slot args keyword-only); **`series_length_for_round` is refactored to
delegate** to it (`series_length_for_depth(total_rounds - bracket_round, …)`) with
**byte-identical** public signature + behaviour (single-elim escalation
transparent). **`build_double_elim_bracket(participants) -> list[BracketNodeSpec]`**
emits the full two-tree node-spec list for arbitrary **N ≥ 4 with byes**: the WB
is the existing single-elim tree (size = next pow2 ≥ N, top `(size − N)` seeds get
WB byes, reusing `build_bracket`'s seeding/pairing); the LB consumes WB-round
losers via a **naive same-position drop** (loser of WB-round-*r* position *i* →
the matching LB slot by position — **NO anti-rematch folding** this slice, a known
limitation deferred); each WB node's `loser_advances_to` points at its LB
destination + slot; GF1 (the lower `bracket_round`, `bracket_type="grand_final"`)
takes the WB champ (slot "a") + LB champ (slot "b"), GF1's `loser_advances_to`
points at **GF2** (so the LB-champ path Advances both into GF2), GF2's
`advances_to`/`loser_advances_to` are `None` (final node); **GF1/GF2 depth = 0**,
WB-final & LB-final depth 1, etc. Raises `ValueError` on `len < 4` or duplicate
seeds/team_ids (mirrors `build_bracket`). The WB/LB `(bracket_round, position)`
numbering is the builder's internal choice — only the cross-bracket wiring coords
+ `depth` are asserted.

**`advance_loser` is a SEPARATE pure function (`matches/bracket.py`).**
`advance_loser(nodes, node_position=(bracket_type, bracket_round, position),
loser_id, loser_seed) -> list[dict]` returns the parent-slot mutations that Drop
the loser into the resolved node's `loser_advances_to` slot — each mutation dict
carries `bracket_type`/`bracket_round`/`position`/`slot`/`team_id`/`seed`; empty
list when `loser_advances_to is None` (LB node, GF2, single-elim WB node).
**DECISION LOCKED: this is NOT a generalization of `advance_winner`** —
`advance_winner` stays **byte-unchanged** (its mutation dicts have **no
`bracket_type` key**, preserving single-elim and every existing
`test_advance_winner` case), and the engine makes **two explicit calls**
(`advance_winner` then `advance_loser`) on a WB/GF1 clinch. `resolve_bye_chain` is
**generalized to collapse Drop byes**: a WB **Bye** produces a winner but **no
loser**, so the LB slot its `loser_advances_to` points at receives no Drop — if
that slot's only feeder is the bye's loser-drop, the LB node has a permanently
empty slot and **collapses** (surviving opponent auto-advances, `is_bye=True`,
`winner_id` set), exactly as the existing winner-side bye cascade collapses an
unopposed slot. Its single-elim behaviour is **byte-identical** (every existing
`test_resolve_bye_chain` case green; still returns `[]` with no byes); DE
collapse/loser-drop mutations gain a `bracket_type` key, winner-side mutations
keep their existing shape. `find_next_node` keeps its **playable predicate
byte-identical** and changes **only its sort key** to `(_BRACKET_RANK
winners<losers<grand_final, bracket_round asc, position asc)` — for a single-elim
field every node is rank-0 `"winners"` so the order **collapses to
`(bracket_round, position)`** exactly as today; the bracket rank is purely a
deterministic tiebreak when multiple cross-bracket nodes are simultaneously ready.
`stage_progress` generalizes: **`total`** = count of distinct
`(bracket_type, bracket_round)` groups (was `max(bracket_round)`; single-elim
collapses to the same number, byte-unchanged), **`completed`** = count of groups
where every non-bye, non-inert node has `winner_id is not None` (an inert
auto-resolved GF2 already satisfies `winner_id is not None`, so no special-case),
`(0, 0)` on empty. `clinch_threshold`, `series_winner_slot`, `count_series_wins`,
`advance_winner`, `build_bracket`, `break_tie`, `default_seed_order`, and
`ParticipantSpec` are **UNCHANGED**.

**Engine (`matches/tournament_engine.py::play_next_node`).** Stays **ONE**
per-Match-atomic loop for **both** formats — the body is **verbatim** through the
clinch check (find next playable node → sim ONE Match → `break_tie` fallback →
`SeriesMatch.objects.create` → recompute via `count_series_wins` →
`series_winner_slot(…, node.series_length)` → return `node` un-advanced when
`slot is None`). On clinch the tail changes: (1) stamp `node.winner` +
`winner_seed`, `save(update_fields=["winner"])` (unchanged); (2) build the flat
list via `_node_to_dict` over `tournament.nodes.select_related("advances_to",
"loser_advances_to").prefetch_related("series_matches")` (adds
`"loser_advances_to"`); (3) **winner advance** (`advance_winner` — unchanged
shape, parent resolved within the winner's own `advances_to` target bracket so the
GF1→GF2 cross is handled by GF1's own pointer); (4) **loser Drop** (DE only) —
when `node.bracket_type in ("winners", "grand_final")` AND
`node.loser_advances_to_id is not None`, compute the non-winning slot's team+seed
and call `advance_loser`, applying each mutation to the LB/GF2 parent keyed by
`(mut["bracket_type"], mut["bracket_round"], mut["position"])`; a **single-elim WB
node** has `loser_advances_to_id is None` ⇒ `advance_loser` is skipped / returns
`[]` ⇒ loser eliminated exactly as today; (5) **Grand-final Bracket reset** — on a
clinched **GF1** (its `advances_to` points at GF2): if the GF1 winner **==** the
WB champ (slot "a"), stamp **`GF2.winner` inert** (`save(update_fields=["winner"])`
so `find_next_node` never returns GF2 — the bye-style auto-resolve precedent) +
`tournament.champion` + `state="completed"` immediately; if the GF1 winner == the
LB champ (slot "b"), the step-4 Drop has already Advanced the WB champ into GF2
slot "a" and the winner-advance into GF2 slot "b" ⇒ **GF2 is now playable**, no
champion yet; (6) **final node** (single-elim final OR GF2, `advances_to_id is
None`) stamps `tournament.champion` + `state="completed"` (unchanged shape).
**Callers unchanged in signature/route:** `tournament_play_next`,
`play_tournament_task` (loops `play_next_node` unchanged), `tournament_play_all`,
`tournament_play_status` keep their URLs + 5-key status JSON; `stage_progress`
now reports stage completion across **both brackets + GF**.

**View / template surface.** Create form (`tournament_create` /
`tournament_create.html`): reads a new POST field **`format`** (forgiving-fallback
mirroring the series-length parses — accept only `"single_elimination"` /
`"double_elimination"`, anything absent/tampered falls back to
`"single_elimination"`; *ANG-01f: absent still does, a tampered value is now rejected*) and passes it as the `format=` kwarg into the existing
`Tournament.objects.create(...)`; rendered as one `<select name="format">` with
DOM id **`tournament-create-format`** (options "Single elimination" selected /
"Double elimination"), placed above the four series-length selects — every other
create-form id (`-name`, `-team-select`, `-generate-*`, the four `-*-series-length`,
`-submit`, `-no-teams-notice`) unchanged. Detail page (`_build_rounds` /
`tournament_detail.html`): each node view-dict gains **`bracket_type`**, and
`_build_rounds`'s **return shape changes** from a flat `[{bracket_round, nodes}]`
to a **3-key dict** `{"winners": […], "losers": […], "grand_final": […]}` — for a
single-elim tournament `"losers"` and `"grand_final"` are **empty lists** and
`"winners"` carries the full tree (no single-elim render regression). The frozen
`_detail_context` keys are unchanged — **only the *value-shape* of `rounds`
changes**, no new top-level context key. The template renders **three sections**
(DOM ids `tournament-bracket-winners` / `-losers` / `-grand-final`, per-round
column `tournament-bracket-{bracket_type}-round-{n}`) reusing the existing
node-card + series-score + Bo-N-label markup. **Single-elim keeps the legacy DOM
ids (LOCKED):** the template **branches on `tournament.format`** — single-elim
renders `tournament-bracket` / `tournament-bracket-round-{n}` /
`tournament-node-{round}-{position}` / `-series-score-{round}-{position}` /
`-series-length-{round}-{position}` (every LG-02a/b view test stays green, Losers
& Grand-final sections **absent**), while DE renders the namespaced
**`tournament-node-{bracket_type}-{bracket_round}-{position}`** /
`-series-score-{bracket_type}-{br}-{pos}` / `-series-length-{bracket_type}-{br}-{pos}`.
The Grand-final section renders both GF1 and GF2 cards; the inert auto-resolved
GF2 (WB-champ-wins case) carries the `bye-node` class so the existing "no
series-score for bye nodes" branch suppresses its Bo-N label cleanly.
`tournament-champion-banner`, the Play controls (`-lock-form` / `-play-next-form`
/ `-play-all-form` / `-play-all-progress` / `-play-all-error` + the poll JS), the
import + seeding form ids, and `tournaments-nav-link` are **unchanged**. The three
new `BracketNode` fields auto-surface in the default `BracketNodeAdmin` change
form and the widened `format` choices in `TournamentAdmin` — **no `list_display` /
inline / registration change**.

**Tests.** `matches/tests/test_bracket.py` (extend — `TestSeriesLengthForDepth`
depth boundaries + keyword-only + delegation parity with
`series_length_for_round`; `TestBuildDoubleElimBracket` N=4/8 no-bye + N=5/6 WB-bye
node counts / loser-drop coords / GF1→GF2 / depth / `ValueError`;
`TestAdvanceLoser`; `TestResolveByeChainDropBye` collapse; `TestFindNextNodeBracketOrder`;
`TestStageProgressDoubleElim`; `TestNoDjangoImportsLeaked` + every existing
single-elim case still green); `test_tournament_models.py` (extend —
`TestBracketNodeDoubleElimFields` defaults/choices/renamed constraint,
`TestLockAndBuildDoubleElim` N=4/6 wiring + per-depth `series_length` incl. byes,
`TestLockAndBuildSingleElimUnchanged` regression, extended `Test_node_to_dict` DE
3-tuple keys + single-elim `("winners", None, None)`); `test_tournament_views.py`
(extend — `TestCreateFormFormat` select + persist + fallback,
`TestDetailDoubleElimSections` three containers + DE ids,
`TestDetailSingleElimIdsUnchanged` legacy-id regression);
`test_tournament_engine.py` (extend — `TestPlayNextNodeDoubleElimDrop` WB loser
Drop, `TestPlayNextNodeGrandFinalReset` both reset branches,
`TestPlayNextNodeSingleElimUnchanged` regression); `test_tournament_tasks.py`
(extend — `TestPlayTournamentTaskDoubleElim` drains a DE bracket to a champion,
stage counts over both brackets + GF). Tests assert on the pure functions, the
persisted `BracketNode` fields (`bracket_type` / `loser_advances_to` /
`series_length`), `node.winner` / both-bracket Advancement / `tournament.champion`
/ `state`, and the DOM ids — **never on exact simulated point totals**
(non-deterministic ⇒ **no SIM-07/SIM-08 interaction, NO Score Calibration
re-baseline**).

**Scope-out (LOCKED — DEFERRED, do NOT build here):** **anti-rematch folding** in
the Losers bracket (LB consumes WB losers via a naive same-position drop only —
folding deferred to a follow-up); **round robin / RR→double-elim / Swiss** (the
`format` enum is extensible but only `single_elimination` + `double_elimination`
ship); a **single Grand final (no reset)** (rejected — the Bracket reset GF1+GF2
is the locked ADR-0021 design); a **separate `LoserBracketNode` model / second
table** (rejected — one table + `bracket_type` tag); **`advance_winner`
generalization** (stays byte-unchanged; a separate `advance_loser` carries the
loser path); **home/away side alternation** (sides fixed `team_a` red / `team_b`
blue every Match, LG-02b locked); **deterministic / master-seed-replayable
Series** (`simulate_match` draws fresh per-round seeds ⇒ non-deterministic);
any `simulate_match` / `simulate_scheduled_round` change (consumed verbatim,
`arena_map=None` 3-zone fallback per Match); **backfill / `RunPython`** (none —
pure forward-only schema ops, ADR-0004); in-League / in-Season tournament
embedding (Tournament stays standalone, `season`-less); any new GLOSSARY.md term
beyond **Winners bracket** / **Losers bracket** / **Drop** / **Grand final** /
**Bracket reset** (already written) and any new ADR (ADR-0021 already written).

## ANG-01f tournaments API (sandbox Tournament notes)

The full slice (endpoints, codes, packages) is the **ANG-01f tournaments API** section of
[`api.md`](api.md); the Player-pool notes are at the end of
[`tournaments-rr-swiss-draw.md`](tournaments-rr-swiss-draw.md). What changed for the sandbox
Tournament pages described above:

- **Code moved.** `matches/tournament_views.py` is now the `matches/tournament_views/` package
  (`list_create`, `detail`, `setup`, `draw`, `play`); URL names and templates are unchanged. The logic
  lives in `matches/tournament_services/` (`errors`, `detail`, `seeding`, `create`, `setup`, `draw`,
  `play`), shared with `/api/tournaments/`. Old private helpers were moved and renamed, not
  re-exported: `_build_rounds` ⇒ `detail.build_rounds(t, bracket_type)` (now one bracket type per call;
  `league_screen_services` imports it from here), `_detail_context` ⇒ `detail.build_detail_context(t,
  *, user)`, `_team_mean_rating` ⇒ `seeding.team_mean_rating`, `_build_tournament_play_status_response`
  ⇒ `play.build_play_status`. Patch `matches.tournament_services.<module>.*` in tests.
- **Rename.** `team_assembly` `"random_draw"` ⇒ `"player_pool"` (migration `0064`); "Random Draw" is
  the derived label for a Player-pool draw played as RR→DE.
- **Embedded guard.** An embedded (in-League) Tournament — referenced by a `SeasonPhase` or carrying a
  `season_phase` — is read-only on `/tournaments/`: reseed, lock, import, pool, draw and play-next
  flash the error and redirect; play-all answers 409 JSON. Before, the sandbox pages could play an
  in-League bracket and bypass the Season's single play run.
- **Single active play run** ([ADR-0040](../../adr/0040-single-active-play-run-per-season.md)
  **Tournaments**). New nullable `Tournament.active_play_job_id`. `tournament_play_all` claims the
  column **before** `apply_async(task_id=…)`; while it is set, play-all is 409 JSON and play-next
  flashes. `play_tournament_task`'s `finally` clears it only while it holds its own id; a terminal
  (`SUCCESS` / `FAILURE` / `REVOKED`) Job is released by the next play, `PENDING` stays blocked, and
  there is no cancel. Play-all keeps its 202 `{job_id, tournament_id}` and its 503 broker-down JSON
  (`BROKER_UNAVAILABLE_MESSAGE`); the 409 / flash texts now come from the service exception defaults
  (e.g. "The Tournament is not active.").
- **Strict Forms.** Create binds `TournamentCreateForm`: absent fields keep their defaults, but a
  present bad `format`, series length, `rrde_combo`, `swiss_rounds` or `generate_ppt` < 6 (no longer
  clamped) is an error, flashed on the re-rendered form. The Team picker offers only the Account's own
  and Unmanaged valid-Roster Teams (a leak fix). Reseed binds `SeedsForm`: seeds must be exactly 1..N
  over the current participants, so a missing or duplicate seed is flashed instead of the old 500
  `IntegrityError`.
- **Detail page.** New `#tournament-format-label` badge (the derived label); the list shows the same
  label.

## ANG-06-13a Angular Tournament list, create, detail shell and Player pool

`/tournaments`, `/tournaments/new` and `/tournaments/:id` in the SPA (spec #396, tickets #397-#400)
port `tournament_list.html`, `tournament_create.html` and the setup half of `tournament_detail.html`
over the existing ANG-01f `/api/tournaments/` endpoints; no backend change. Sidebar link after
Matches. The Django pages stay until ANG-08.

- **Seam:** `frontend/src/app/api/tournaments.service.ts` (`list`, `get`, `create`, `pool`,
  `addPoolPlayers`, `removePoolPlayer`, `generatePool`, `importPool`, `importParticipants`) +
  `tournaments.models.ts` (`Tournament`, `TournamentDetail`: `brackets`, `rr_standings`,
  `swiss_standings`, `cut_labels` were typed `unknown` until ANG-06-13b; `PoolEntry`,
  `TournamentCreateInput`, `PoolGenerateInput`, `TOURNAMENT_STATE_LABELS`, `TEAM_ASSEMBLY_LABELS`).
- **Client-side derivations** (no API field): the State label, locked = `state !== 'setup'`, pool
  controls shown = not locked and not `embedded`, the "You" badge from `self_insert`.
- **List:** paginated Name / Format / State badge / Created, newest first, no filters; in-League
  Tournaments stay listed; a failed load shows an alert.
- **Create:** the template's show / hide rules from one `visibleFields(format, assembly)` predicate;
  Player pool switches the format to RR -> DE and (an owner-approved departure) hides Teams and the
  generate fields; hidden fields are disabled so they are left out of the body. The Team picker is
  `listAll(teams)` filtered on `is_valid_roster`; with none, a `too_few_teams` error shows under the
  no-teams notice.
- **Detail** is a shell: the host loads `GET tournaments/:id/` and each section is a child that
  emits `changed` to reload it. 13a has the header (State badge, assembly label, champion, an "In
  League" note), read-only Participants, `participant-import` (only setup + preset + sandbox, an
  owner-approved departure: the API refuses Player-pool) and `tournament-pool` (Player pool only).
  ANG-06-13b added Seeding / lock / draw / play / results as further sections (below).
- **Pool:** size warning unless a multiple of 6 and >= 24; the table loads every page; add-existing
  leaves out already-pooled Players (departure); Generate checks the Form's bounds on the client;
  Remove has no confirm. The three CSV cards (roster import, participant import, pool import) share
  `shared/csv-import.ts` (`CsvImport`).
- **Out (logged):** generated / imported pool Players land on the global Free Agents team
  (ANG01f5-D1); pool import skips the slot-collision check (ANG01f5-D2).

## ANG-06-13b Angular Tournament detail (Seeding, lock, draw, play, results)

The rest of `tournament_detail.html` and `_tournament_round.html` in the SPA's `/tournaments/:id`
(spec #403, tickets #404-#406). Parity means the same features, not the same look (ADR-0043). The
Django page stays until ANG-08.

- **API (the only backend change):** the detail payload gains `rr_crosstable` and `can_play`
  (`api.md`, Tournaments). `_build_rr_crosstable` now prefetches `series_matches__match`, which also
  removes one query per played node from the Django page.
- **Seam:** `TournamentsService` gains `saveSeeds` (PUT `seeds/`), `lock`, `draw`, `editDrawEntry`
  (PATCH `draw/entries/<pid>/`), `playNext`, `playAll` and `playJob` (GET `play-jobs/<id>/`).
  `tournaments.models.ts` types `BracketGroup` / `BracketRound` / `BracketNode`,
  `TournamentStandingsRow`, `CutLabel`, `CrosstableRow` / `CrosstableCell` / `CrosstableLeg`,
  `PlayNextResult`, `PlayJobRef` and `PlayJobStatus` (reusing Batch's `JobStatus`).
- **Host sections:** each emits `changed`, and the host reloads the detail (no section patches it).
  Write controls are hidden when `embedded` or the state doesn't allow them.
  - `tournament-setup`: Seeding (preset only, number inputs `required` / `min="1"`, PUT, server 400s in
    an error box) and Lock & Build Bracket (asks through `ConfirmDialog`).
  - `tournament-pool` (13a's) gains Draw teams / Re-roll draw (disabled by `poolSizeInvalid`, shared
    in `tournaments/pool-size.ts`) and the Drawn Teams cards with a Set tier input (1-6) per Player.
    It reuses the pool entries it already loads, so the host's load sends no extra GET.
  - `tournament-play`: Play Next Match (disabled unless `can_play`; the snackbar is
    `playedMessage`, the Django `_played_message` wording) and Play All. Play All polls
    `play-jobs/<id>/` with `timer(1000)` after each answer. An HTTP failure on a poll schedules the
    next poll, a 409 `play_in_progress` resumes the job named in its `job:<id>` field, and a 503
    shows the server's message. It reads "Starting…" then "Running… stage c / t".
  - `tournament-results`: the per-format layout. The shared `bracket` / `node-card` / `crosstable` /
    `standings-table` components live under it. Single elimination names its columns Final /
    Semifinals / Quarterfinals / Round N (`round-names.ts`) with h3 headings, and every other group
    says "Round N" (h4 under its sub-bracket h3). RR->DE shows the Finals Bracket only once those groups
    exist. The Standings add a Cut column (WB / LB / Out, plus a `data-cut` tint) only when
    `cut_labels` is non-empty. The bracket and crosstable scroll sideways in their own box.
  - Host header: the stage badge (RR->DE and Swiss only), "No participants in this tournament.", and
    the champion banner only when `completed`.
- **Server order:** participants arrive by seed, so Seeding rows and Drawn Teams keep the detail's
  order and never re-sort.
- **Out:** cancel / delete (neither surface has them), moving a Player between drawn Teams, resuming a
  run after a reload beyond the 409, the streak / L5 Standings columns, and `next_node`.
- **Known gap (pre-existing):** without Redis, Play All returns an unhandled 500 (a redis
  `ConnectionError` from `apply_async`) instead of the 503 `broker_unavailable`, because
  `enqueue_play_all` catches only kombu's `OperationalError`. It is logged in `PLAN.md`.
