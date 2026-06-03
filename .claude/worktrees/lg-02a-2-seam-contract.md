# LG-02a-2 Seam Contract — CSV participant import + async play-all

Two follow-ups to the shipped LG-02a sandbox single-elimination Tournament:
**(1)** CSV participant import (LG-00b reuse) and **(2)** async "play-all" via a
Celery task. All design decisions are LOCKED. Three parallel agents
(Code / Tests / Docs) build against the names below. Where a name was not
dictated upstream it is picked and locked here.

Paths are relative to the nested Django project
`laserforce_simulator/laserforce_simulator/` unless noted. Templates live under
`laserforce_simulator/templates/...`. CONTEXT.md is at the **repo root**.

---

## 0. Scope / decisions summary

- **No model change, no migration, no ADR.** Per-node-atomic follows ADR-0016;
  CSV reuse follows LG-00b — both reversible.
- **Non-deterministic.** `simulate_match` draws fresh per-round seeds, so there
  is **no SIM-07 / SIM-08 contract interaction and NO Score Calibration
  re-baseline**.
- **No edit to `teams/` app** — cross-app **read-only** imports only.
- **No edit** to `simulate_match`, the bracket build/advance logic, or
  `matches/bracket.py` beyond adding the one PURE function `stage_progress`.
- New module `matches/tournament_engine.py` extracts the per-node resolve/advance
  body of the existing inline `tournament_play_next`. The sync view is refactored
  to call it.
- New Celery task `matches/tasks.py::play_tournament_task`.
- Three new views + one private context helper in `matches/tournament_views.py`;
  three new URLs in `matches/tournament_urls.py`; two new template surfaces on
  `tournament_detail.html`; CONTEXT.md **Job** term extended to a 4th kind.

---

## 1. Pure module addition — `matches/bracket.py::stage_progress`

`matches/bracket.py` has a FROZEN import allowlist (`dataclasses`, `typing`,
`math`, `collections` only — **NO Django**), enforced by
`matches/tests/test_bracket.py::TestNoDjangoImportsLeaked` (subprocess fresh-import
+ `sys.modules` walk). `stage_progress` MUST respect it — pure ints/dicts, stdlib
only (`math`/`collections` are already imported; no new import needed).

### Signature

```python
def stage_progress(nodes: list[dict]) -> tuple[int, int]:
    """STAGE-based progress for a Tournament bracket.

    Returns (completed_stages, total_stages):
      - total_stages   = max ``bracket_round`` across ``nodes`` = the number of
                         Bracket rounds = ceil(log2(size)). 0 when ``nodes`` is
                         empty.
      - completed_stages = count of Bracket rounds (1..total) where EVERY
                         non-bye node in that round has ``winner_id`` set.
                         A round with zero non-bye nodes counts as completed.
    """
```

### Algorithm (locked)

1. `nodes` is empty ⇒ return `(0, 0)`.
2. `total = max(nd["bracket_round"] for nd in nodes)`.
3. For each round `r` in `1..total`: gather that round's nodes; among them the
   **non-bye** nodes are those with `not nd["is_bye"]`. The round is "complete"
   iff **every** non-bye node has `nd["winner_id"] is not None`. (A round whose
   nodes are all byes — no non-bye nodes — is vacuously complete.)
4. `completed = count of complete rounds`. Counting is **by membership**, not
   "first incomplete" — but in a single-elim bracket the rounds complete in order
   so the count equals the deepest fully-resolved round.
5. Return `(completed, total)`.

### Dict-key dependency (verified against `matches/models.py::_node_to_dict`)

Operates on the existing flat dicts produced by `_node_to_dict`. The keys read:
**`bracket_round`** (int, 1-based), **`is_bye`** (bool), **`winner_id`**
(`int | None`). No other key is read. These three keys are present and
correctly typed in `_node_to_dict`'s output (confirmed `models.py` lines
~1322-1334).

### Test note

`matches/tests/test_bracket.py` gains a `TestStageProgress` class (pure-unit, no
DB) plus a purity assertion: `stage_progress` must NOT break the existing
`TestNoDjangoImportsLeaked` subprocess check.

---

## 2. New module — `matches/tournament_engine.py`

The refactored body of the current inline `tournament_play_next` resolve/advance
logic (read verbatim from `matches/tournament_views.py::tournament_play_next`).

### Signature

```python
from django.db import transaction
from .models import BracketNode, Tournament

@transaction.atomic
def play_next_node(tournament: Tournament) -> "BracketNode | None":
    """Resolve and advance the next playable Bracket node.

    Returns the resolved BracketNode, or None when no node is playable
    (nothing ready / tournament complete). @transaction.atomic — one node =
    one transactional unit (ADR-0016 per-node-atomic precedent).
    """
```

### Behaviour (locked — mirrors the current inline view body)

1. **Defer the heavy import inside the function** (not at module scope):
   `from .simulation.entrypoints import BatchSimulator`.
2. `node = tournament.find_next_playable_node()`. If `None` ⇒ return `None`.
3. Simulate ONE Match:
   `match = BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`;
   `node.match = match`.
4. Resolve winner: `winner_team = match.winner`. If `None` (true tie), use
   `break_tie(node.seed_a, best_a, node.seed_b, best_b)` where
   `best_a = max(match.red_round1_points, match.red_round2_points)` and
   `best_b = max(match.blue_round1_points, match.blue_round2_points)`; map the
   winning seed back to `winner_team` / `winner_seed`. Else map
   `winner_team.id == node.team_a_id` ⇒ `winner_seed = node.seed_a` else
   `node.seed_b`.
5. `node.winner = winner_team`; `node.save(update_fields=["match", "winner"])`.
6. Compute + apply parent mutations:
   `flat = [_node_to_dict(n) for n in tournament.nodes.select_related("advances_to")]`;
   `mutations = advance_winner(flat, (node.bracket_round, node.position), winner_team.id, winner_seed)`;
   for each mutation fetch the parent node and set the `a`/`b` slot's `team_*` +
   `seed_*`, `parent.save(update_fields=["team_a", "team_b", "seed_a", "seed_b"])`.
7. Final node (`node.advances_to_id is None`): stamp champion + complete —
   `tournament.champion = winner_team`; `tournament.state = "completed"`;
   `tournament.save(update_fields=["champion", "state"])`.
8. Return `node`.

Imports used (top of module): `from .bracket import advance_winner, break_tie`,
`from .models import BracketNode, Tournament, _node_to_dict`. `BatchSimulator` is
the **only** deferred import.

### Refactor of the sync view

`matches/tournament_views.py::tournament_play_next` is rewritten to keep its HTTP
shell (POST-only `HttpResponseNotAllowed(["POST"])`, `get_object_or_404`,
`state != "active"` guard with `messages.error` + redirect) and then call
`play_next_node(tournament)`; when it returns `None` (and the state was active)
flash `"No playable match is ready."` and redirect; otherwise redirect to
`tournament_detail`. The inline simulate/resolve/advance block is DELETED from the
view (it now lives only in `tournament_engine.py`).

### Atomicity

`play_next_node` is `@transaction.atomic` — one node per transaction. The Celery
task (§3) and the sync view BOTH rely on this; neither adds an outer atomic.

---

## 3. Celery task — `matches/tasks.py::play_tournament_task`

### Signature

```python
@shared_task(bind=True, name="matches.play_tournament")
def play_tournament_task(self, tournament_id: int) -> dict:
    ...
```

- Celery broker name: **`"matches.play_tournament"`** (locked).
- Return shape on completion: **`{"completed": int, "total": int}`** (stage
  counts).

### Body (locked — study `matches/tasks.py::play_season_task` for the precedent)

1. `import django.db` (for `close_old_connections` in `finally`).
2. Deferred imports inside the body (lean module surface, mirrors
   `_resolve_arena_map` / `play_season_task` precedent):
   `from matches.models import Tournament`,
   `from matches.tournament_engine import play_next_node`,
   `from matches.bracket import stage_progress`,
   and `from matches.models import _node_to_dict`.
3. `try:` block:
   - `tournament = Tournament.objects.get(id=tournament_id)`.
   - **Early-return guard:** if `tournament.state != "active"` ⇒ return the
     current stage progress immediately:
     `flat = [_node_to_dict(n) for n in tournament.nodes.all()]`;
     `completed, total = stage_progress(flat)`; `return {"completed": completed, "total": total}`.
   - Loop: `while play_next_node(tournament) is not None:` after each resolved
     node, recompute stage progress and emit:
     `flat = [_node_to_dict(n) for n in tournament.nodes.all()]`;
     `completed, total = stage_progress(flat)`;
     `self.update_state(state="PROGRESS", meta={"completed": completed, "total": total})`.
   - After the loop, recompute final `completed, total` and
     `return {"completed": completed, "total": total}`.
4. `finally: django.db.close_old_connections()`.

**NO outer `@transaction.atomic`** on the task body — per-node atomicity comes
from `play_next_node`'s decorator (ADR-0016 precedent: a mid-loop exception
propagates as Celery `FAILURE`; every node already resolved survives because it
was its own atomic commit).

`update_state` meta shape: exactly **`{"completed": int, "total": int}`** (stage
counts, NOT node counts).

---

## 4. Views — `matches/tournament_views.py`

Imports added to the view module (defensive — add only the names not already
imported): `from django.http import JsonResponse` (already
`HttpResponseNotAllowed` / `HttpResponse` present); `from celery.result import AsyncResult`;
`from .tasks import play_tournament_task`;
`from .tournament_engine import play_next_node`;
`from matches.views import _celery_state_to_job_status` (cross-module reuse,
verbatim — see §8). LG-00b reuse imports also added (see §8).

### 4a. `_detail_context(tournament) -> dict` (private helper)

Factor the existing `tournament_detail` context build into a shared helper so both
`tournament_detail` and the import-error re-render produce identical context.

```python
def _detail_context(tournament: Tournament) -> dict:
    """Shared tournament_detail context (LG-02a keys + LG-02a-2 import keys)."""
```

Returns the **existing 6 LG-02a keys** verbatim:
`tournament`, `participants` (`tournament.participants.select_related("team").order_by("seed")`),
`rounds` (via the existing `_build_rounds(tournament)`),
`next_node` (`tournament.find_next_playable_node()`),
`is_locked` (`tournament.is_locked`),
`can_play` (`tournament.state == "active" and next_node is not None`),
**PLUS 2 new import keys**:
- `import_form` — a `RosterImportForm()` instance (unbound on the normal render).
- `import_row_errors` — `list[RowError]`, default `[]` (populated only on the
  CSV-error re-render).

`tournament_detail` is refactored to `return render(request, "matches/tournament_detail.html", _detail_context(tournament))`
(keeping its GET-only `HttpResponseNotAllowed(["GET"])` guard +
`get_object_or_404`).

### 4b. `tournament_import_participants(request, tournament_id) -> HttpResponse`

- **HTTP method:** POST. (No explicit method guard required beyond the
  setup-only/`is_locked` check, but the URL + form are POST-only by construction;
  match the LG-02a convention — the Code agent MAY add
  `if request.method != "POST": return HttpResponseNotAllowed(["POST"])` as the
  first line.)
- **Decorator:** `@transaction.atomic`.
- **Guard (setup-only):** `tournament = get_object_or_404(Tournament, pk=tournament_id)`;
  if `tournament.is_locked` ⇒ `messages.error(request, "Participants can only be imported during setup.")`
  and `return redirect("tournament_detail", tournament_id=tournament.id)`.
- **Happy path (full LG-00b reuse):**
  1. `form = RosterImportForm(request.POST, request.FILES)`.
  2. On `form.is_valid()`:
     - `parsed = parse_roster_csv(form.cleaned_data["csv_file"])`
     - `_check_db_slot_collisions(parsed)`
     - `created_teams, appended_teams, player_count = _apply_roster(parsed)`
     - **ONLY `created_teams`** become `TournamentParticipant`s (brand-new Teams ⇒
       no `uniq_tournament_team` collision possible). `appended_teams` are
       created/extended but **NOT auto-added**.
     - Re-seed the WHOLE field by talent (see below), then
       `return redirect("tournament_detail", tournament_id=tournament.id)`.
  3. On `not form.is_valid()` ⇒ treat as the error branch (re-render detail with
     the bound form's errors + `transaction.set_rollback(True)`).
- **Re-seed (full field, by talent):** after adding the new participants, collect
  ALL current participants' `(team_id, _team_mean_rating(team))` pairs (the
  existing `_team_mean_rating` helper in `tournament_views.py`, mean active-player
  `overall_rating`), call `default_seed_order(team_ratings)`
  (`matches/bracket.py`), and rewrite every `TournamentParticipant.seed` to the
  new 1-based order. Use the same two-phase offset write the existing
  `tournament_reseed` view uses to dodge the `uniq_tournament_seed` constraint
  (offset every seed by a large constant first, then write final values).
- **Error branch (RosterImportError OR form invalid):**
  `transaction.set_rollback(True)`, then re-render the detail page:
  `ctx = _detail_context(tournament)`; `ctx["import_form"] = form`;
  `ctx["import_row_errors"] = exc.errors` (for `RosterImportError`) or
  `[]` (form invalid — field errors live on `import_form`);
  `return render(request, "matches/tournament_detail.html", ctx)` (HTTP 200).
- **Return shape:** 302 redirect to `tournament_detail` on success; 200
  re-render of `tournament_detail.html` on error.

### 4c. `tournament_play_all(request, tournament_id) -> JsonResponse`

- **HTTP method:** POST-only — `HttpResponseNotAllowed(["POST"])` as the **first
  line** of the body.
- **Guard:** `tournament = get_object_or_404(Tournament, pk=tournament_id)`; if
  `tournament.state != "active"` ⇒
  `return JsonResponse({"error": "Tournament is not active."}, status=409)`.
- **Body:** `result = play_tournament_task.delay(tournament_id)`;
  `return JsonResponse({"job_id": result.id, "tournament_id": tournament.id}, status=202)`.
- **Return shape:** `JsonResponse({"job_id", "tournament_id"}, status=202)`.

### 4d. `tournament_play_status(request, tournament_id, job_id) -> JsonResponse`

- **HTTP method:** GET-only — `HttpResponseNotAllowed(["GET"])` as the first line.
- **Body:** `get_object_or_404(Tournament, pk=tournament_id)` (404 on a stale
  tournament id); `async_result = AsyncResult(job_id)`;
  `return JsonResponse(_build_tournament_play_status_response(async_result, tournament_id=tournament_id))`.
- **Return shape:** the 5-key JSON below.

### 4e. `_build_tournament_play_status_response(async_result, *, tournament_id) -> dict`

New private helper (mirrors `matches/views.py::_build_play_status_response` shape
exactly — study that for sourcing). REUSES `_celery_state_to_job_status` (§8).

```python
def _build_tournament_play_status_response(
    async_result: AsyncResult, *, tournament_id: int
) -> dict:
    """Locked 5-key polling JSON for a Play Tournament job."""
```

Returns exactly: `{status, completed, total, error, tournament_id}`.

- `status` — `_celery_state_to_job_status(async_result.state)` ⇒
  `"running"` / `"complete"` / `"error"`.
- `completed` / `total` — **stage counts**:
  - `PROGRESS`: `async_result.info["completed"]` / `["total"]`
    (defensive `int(... or 0)`, `isinstance(info, dict)` guard).
  - `SUCCESS`: `async_result.result["completed"]` / `["total"]` (same defensive
    coercion).
  - everything else: `0` / `0`.
- `error` — `str(async_result.info)` on `FAILURE` / `REVOKED`, else `None`.
- `tournament_id` — echoed from the kwarg (authoritative over any query param).

---

## 5. URLs — `matches/tournament_urls.py`

Bare names (no `app_name`), mirroring the existing file. The existing order is
`[list "", create/, <id>/, <id>/reseed/, <id>/lock/, <id>/play-next/]`. Insert the
three new paths **immediately after the existing `<int:tournament_id>/play-next/`
entry** (and before nothing — they go at the end of `urlpatterns`).

```python
path(
    "<int:tournament_id>/play-all/",
    views.tournament_play_all,
    name="tournament_play_all",
),
path(
    "<int:tournament_id>/play-status/<str:job_id>/",
    views.tournament_play_status,
    name="tournament_play_status",
),
path(
    "<int:tournament_id>/import-participants/",
    views.tournament_import_participants,
    name="tournament_import_participants",
),
```

Final `urlpatterns` order:
`[ "", create/, <id>/, <id>/reseed/, <id>/lock/, <id>/play-next/,
   <id>/play-all/, <id>/play-status/<job_id>/, <id>/import-participants/ ]`.

Reverse: `reverse("tournament_play_all", args=[t.id])`,
`reverse("tournament_play_status", args=[t.id, job_id])`,
`reverse("tournament_import_participants", args=[t.id])`.

---

## 6. Templates — `templates/matches/tournament_detail.html`

Two new surfaces added to the existing template; mirror the LG-00b
`roster_import.html` error pattern and the LG-01d seasons `dashboard.html` poll JS.

### 6a. Setup-state: "Import Participants (CSV)" form

Rendered **only when `tournament.state == "setup"`** (sibling to the existing
`tournament-lock-form` / `tournament-seeding-form` blocks). A
`<form method="post" enctype="multipart/form-data">` to
`{% url 'tournament_import_participants' tournament.id %}`.

Locked DOM ids:
- `tournament-import-form` — the `<form>` element.
- `tournament-import-file` — the file `<input>` (`{{ import_form.csv_file }}`
  renders with the LG-00b-locked `roster-import-file` widget id; place the
  `tournament-import-file` id on the surrounding control or, if the Code agent
  prefers, override the widget `id` — tests assert `tournament-import-file` is
  present in the rendered HTML).
- `tournament-import-submit` — the submit `<button>`.
- `tournament-import-template-link` — an `<a>` to
  `{% url 'import_roster_template' %}` ("Download a template CSV").
- `tournament-import-errors` — the error block, rendered **only when
  `import_row_errors`** is non-empty (mirror LG-00b's `roster-import-errors`
  `<ul>`).
- Per-row error `<li id="tournament-import-error-{{ err.row_num }}-{% if err.field %}{{ err.field }}{% else %}row{% endif %}">`
  — mirrors `roster_import.html`'s `roster-import-error-{row_num}-{field|"row"}`
  pattern exactly (field name when `err.field is not None`, else the literal
  `"row"`).
- Form-field errors from `import_form` (the bound-form re-render path) render via
  `{{ import_form.csv_file.errors }}` adjacent to the file input.

### 6b. Active-state: "Play All" control + progress

Rendered **only when `tournament.state == "active"`** (sibling to the existing
`tournament-play-next-form`, which STAYS). Locked DOM ids:
- `tournament-play-all-form` — the form/control wrapping the Play All button.
- `tournament-play-all-submit` — the submit `<button>` ("Play All").
- `tournament-play-all-progress` — a progress element (rendered, `hidden` by
  default; the inline JS reveals + updates it during polling). Mirror the seasons
  dashboard `play-progress-label` / `play-progress-bar` inner-element convention.

The existing single-step `tournament-play-next-form` is unchanged.

### 6c. Inline polling JS contract (mirror seasons `dashboard.html`)

A single inline `<script>` block, rendered only in the active branch. Contract:

- On `tournament-play-all-submit` submit: `e.preventDefault()`, `fetch(form.action,
  {method: "POST", body: new FormData(form), headers: {"X-CSRFToken": <token>},
  credentials: "same-origin"})`, parse JSON, then `startPolling(json.job_id)`.
- `startPolling(jobId)`: `setInterval` at **1000 ms** hitting
  `{% url 'tournament_play_status' tournament.id 'JOB' %}` with `'JOB'` replaced by
  `jobId` client-side (optionally appending `?tournament_id={{ tournament.id }}`
  for the stateless-carry convention; the URL kwarg is authoritative).
- Each poll updates `tournament-play-all-progress` from `data.completed` /
  `data.total`.
- On `data.status === "complete"` ⇒ `clearInterval` + `window.location.reload()`.
- On `data.status === "error"` ⇒ `clearInterval`, render `data.error` into a
  visible error element, re-enable the button.
- Network blip in the poll ⇒ keep polling (catch + swallow).

No external JS file, no framework — inline only, per the LG-01d precedent.

---

## 7. CONTEXT.md — extend the **Job** term (repo-root `CONTEXT.md`, line ~330)

Extend the existing **Job** entry from "Three kinds today" to a 4th kind. **No new
domain noun, no new term** — just extend the Job definition + the URL list inside
it. CSV import reuses the existing **Roster import** term (NO edit there).

### Current text (quote — `CONTEXT.md` line 330, the `**Job**:` body line)

> A long-running unit of background work wrapped in async execution machinery so
> its caller does not block. Submitted via POST (returns a **Job id**), then
> polled via GET against the **Job id** until the **Job status** is terminal.
> Three kinds today: a **Batch run job** (simulate N games, surface progressive
> aggregates from `BatchSimulator.run_incremental`), a **Save-games job** (replay
> a list of `(seed, flipped)` pairs from a prior Batch run and persist them as
> `GameRound` rows), and a **Play Season job** (run all unplayed Rounds in the
> next N matchdays of a Season — `N=8` for Two Months, `N=None` for Until End of
> Season). All three share the same per-Job lifecycle (`running` / `complete` /
> `error`) and the same expiry-asymmetry (`PENDING` after the 1h TTL is
> indistinguishable from a never-submitted Job id). All three execute via Celery:
> the UI POSTs at `/matches/simulate-batch/`, `/matches/save-games/`,
> `/seasons/<id>/play-two-months/`, and `/seasons/<id>/play-until-end/` and the
> REST POST at `/api/simulate-batch/` all enqueue Celery tasks and return a Job
> id. Backed by **Celery + Redis** in production; tests run the task synchronously
> via `CELERY_TASK_ALWAYS_EAGER = True`.

### Exact replacement (locked)

> A long-running unit of background work wrapped in async execution machinery so
> its caller does not block. Submitted via POST (returns a **Job id**), then
> polled via GET against the **Job id** until the **Job status** is terminal.
> Four kinds today: a **Batch run job** (simulate N games, surface progressive
> aggregates from `BatchSimulator.run_incremental`), a **Save-games job** (replay
> a list of `(seed, flipped)` pairs from a prior Batch run and persist them as
> `GameRound` rows), a **Play Season job** (run all unplayed Rounds in the next N
> matchdays of a Season — `N=8` for Two Months, `N=None` for Until End of
> Season), and a **Play Tournament job** (play every remaining decisive Bracket
> node of a Tournament to a champion; progress reported by completed Bracket
> stage). All four share the same per-Job lifecycle (`running` / `complete` /
> `error`) and the same expiry-asymmetry (`PENDING` after the 1h TTL is
> indistinguishable from a never-submitted Job id). All four execute via Celery:
> the UI POSTs at `/matches/simulate-batch/`, `/matches/save-games/`,
> `/seasons/<id>/play-two-months/`, `/seasons/<id>/play-until-end/`, and
> `/tournaments/<id>/play-all/` and the REST POST at `/api/simulate-batch/` all
> enqueue Celery tasks and return a Job id. Backed by **Celery + Redis** in
> production; tests run the task synchronously via `CELERY_TASK_ALWAYS_EAGER =
> True`.

(The trailing `_Avoid_:` sentence below the entry is unchanged.)

---

## 8. Cross-app / cross-module imports (exact lines)

All **read-only**, no edit to `teams/` (or `matches/views.py`). Add at the top of
`matches/tournament_views.py` (defensive — only the names not already imported):

```python
from teams.forms import RosterImportForm
from teams.roster_importer import parse_roster_csv, RosterImportError
from teams.views import _check_db_slot_collisions, _apply_roster
from matches.views import _celery_state_to_job_status
```

Already present at the top of `tournament_views.py` (verified — reuse, do NOT
re-import): `from .bracket import advance_winner, break_tie, default_seed_order`,
`from .models import BracketNode, Tournament, TournamentParticipant, _node_to_dict`,
`from teams.models import Team`, `from teams.views import _generate_teams`,
`from .simulation.entrypoints import BatchSimulator`. (`default_seed_order` is
already imported — the re-seed path reuses it.)

Signatures of the reused seam (verified against `teams/views.py`):
- `_apply_roster(parsed) -> tuple[list[Team], list[Team], int]`
  i.e. `(created_teams, appended_teams, player_count)`.
- `_check_db_slot_collisions(parsed) -> None` (raises `RosterImportError`).
- `parse_roster_csv(text: str) -> ParsedRoster` (caller passes
  `form.cleaned_data["csv_file"]`, already a decoded `str`).
- `RosterImportError.errors -> list[RowError]`.
- Template-download URL name: **`import_roster_template`** (`/teams/import/template.csv`).
- `_celery_state_to_job_status(state: str) -> str` (verbatim, no fork — verified
  `matches/views.py` line 36).

---

## 9. Determinism / scope-out (LOCKED)

- **Non-deterministic** — `simulate_match` draws fresh per-round 63-bit seeds, so
  Play Tournament games are NOT replayable from a master seed. **No SIM-07 / SIM-08
  contract interaction. NO Score Calibration re-baseline.**
- **No model change, no migration, no new ADR** (per-node-atomic follows
  ADR-0016; CSV reuse follows LG-00b — both reversible).
- **No edit to `teams/` app** (cross-app read-only imports only).
- **No edit** to `simulate_match`, the bracket build/advance functions, or any
  existing `matches/bracket.py` function — the ONLY bracket addition is the pure
  `stage_progress`.
- **No CSV preview/commit UI, no per-tournament arena map, no async on the
  single-step `tournament-play-next`** (that stays synchronous).
- **No new CONTEXT.md term** — only the existing **Job** entry is extended; the
  **Roster import** term is reused unedited.

---

## 10. Test files + classes

- **Extend** `matches/tests/test_bracket.py`:
  - `TestStageProgress` — pure-unit: empty ⇒ `(0,0)`; single-round 4-team bracket
    no winners ⇒ `(0, total)`; all round-1 winners set ⇒ stage 1 complete;
    full bracket all winners ⇒ `(total, total)`; a round of all-bye nodes counts
    complete; a partially-resolved round is NOT complete.
  - A purity assertion that `stage_progress` does not break the existing
    `TestNoDjangoImportsLeaked` subprocess check (the new fn imports nothing new).
- **NEW** `matches/tests/test_tournament_engine.py`:
  - `play_next_node` resolves exactly one node and stamps its winner;
  - advances the winner into the parent slot (`team_*` + `seed_*`);
  - stamps `champion` + `state="completed"` on the final node;
  - the tie-break path (`match.winner is None`) resolves via `break_tie`;
  - returns `None` when no node is playable;
  - per-node atomicity (one node = one transaction).
- **NEW** `matches/tests/test_tournament_tasks.py` (under
  `CELERY_TASK_ALWAYS_EAGER` via the existing `LF_CELERY_EAGER=1` conftest):
  - `play_tournament_task` plays an active tournament to a champion;
  - stage-progress `update_state` meta is emitted (`{"completed","total"}`);
  - resumable after a partial run (re-invoking finishes the rest);
  - inactive-state (`setup` / `completed`) ⇒ early-return no-op returning current
    stage progress, no nodes played;
  - returns final `{"completed","total"}`.
- **Extend** `matches/tests/test_tournament_views.py`:
  - import happy-path: only `created_teams` become `TournamentParticipant`s and
    the whole field is re-seeded by `_team_mean_rating` talent order;
  - setup-only guard: an import POST on a locked (active/completed) tournament is
    rejected (flash + redirect, no writes);
  - CSV error re-renders `tournament_detail.html` with per-row errors
    (`tournament-import-error-{n}-{field|row}`) and **zero writes**
    (`transaction.set_rollback(True)`);
  - play-all enqueues ⇒ HTTP 202 + `{"job_id","tournament_id"}` JSON;
  - play-all on a non-active tournament ⇒ 409;
  - play-status ⇒ 5-key JSON `{status, completed, total, error, tournament_id}`;
  - the refactored sync `tournament_play_next` still resolves one node (no
    regression).

Tests exercise the **real** `_generate_teams` / `_apply_roster` / `simulate_match`
seams (no `mock.patch` on them) so signature drift fails loudly.

---

## 11. Locked-names quick-reference index

| Kind | Name | Location |
|---|---|---|
| Pure fn | `stage_progress(nodes: list[dict]) -> tuple[int, int]` | `matches/bracket.py` |
| Module | `matches/tournament_engine.py` | NEW |
| Engine fn | `play_next_node(tournament) -> BracketNode \| None` (`@transaction.atomic`) | `matches/tournament_engine.py` |
| Celery task | `play_tournament_task(self, tournament_id) -> dict` | `matches/tasks.py` |
| Task `@shared_task` name | `"matches.play_tournament"` | — |
| Task return | `{"completed": int, "total": int}` | — |
| `update_state` meta | `{"completed": int, "total": int}` (PROGRESS) | — |
| View | `tournament_play_all(request, tournament_id) -> JsonResponse` (POST, 202) | `matches/tournament_views.py` |
| View | `tournament_play_status(request, tournament_id, job_id) -> JsonResponse` (GET) | `matches/tournament_views.py` |
| View | `tournament_import_participants(request, tournament_id) -> HttpResponse` (POST, `@transaction.atomic`) | `matches/tournament_views.py` |
| Helper | `_detail_context(tournament) -> dict` (6 LG-02a keys + `import_form`, `import_row_errors`) | `matches/tournament_views.py` |
| Helper | `_build_tournament_play_status_response(async_result, *, tournament_id) -> dict` | `matches/tournament_views.py` |
| Reused helper | `_celery_state_to_job_status(state) -> str` (verbatim) | `matches/views.py` |
| URL name | `tournament_play_all` → `<int:tournament_id>/play-all/` | `matches/tournament_urls.py` |
| URL name | `tournament_play_status` → `<int:tournament_id>/play-status/<str:job_id>/` | `matches/tournament_urls.py` |
| URL name | `tournament_import_participants` → `<int:tournament_id>/import-participants/` | `matches/tournament_urls.py` |
| Play status JSON | `{status, completed, total, error, tournament_id}` (status ∈ `running`/`complete`/`error`) | — |
| Play-all JSON | `{job_id, tournament_id}` status 202 | — |
| DOM id | `tournament-import-form` | tournament_detail.html (setup) |
| DOM id | `tournament-import-file` | tournament_detail.html (setup) |
| DOM id | `tournament-import-submit` | tournament_detail.html (setup) |
| DOM id | `tournament-import-template-link` | tournament_detail.html (setup) |
| DOM id | `tournament-import-errors` | tournament_detail.html (setup, on error) |
| DOM id | `tournament-import-error-{row_num}-{field\|"row"}` | tournament_detail.html (per row error) |
| DOM id | `tournament-play-all-form` | tournament_detail.html (active) |
| DOM id | `tournament-play-all-submit` | tournament_detail.html (active) |
| DOM id | `tournament-play-all-progress` | tournament_detail.html (active) |
| Cross-app import | `from teams.forms import RosterImportForm` | tournament_views.py |
| Cross-app import | `from teams.roster_importer import parse_roster_csv, RosterImportError` | tournament_views.py |
| Cross-app import | `from teams.views import _check_db_slot_collisions, _apply_roster` | tournament_views.py |
| Cross-module import | `from matches.views import _celery_state_to_job_status` | tournament_views.py |
| Template-download URL name | `import_roster_template` | reused (LG-00b) |
| CONTEXT.md | **Job** term extended to 4th kind (**Play Tournament job**) | repo-root `CONTEXT.md` |
| Test file | `matches/tests/test_bracket.py` (extend — `TestStageProgress`) | — |
| Test file | `matches/tests/test_tournament_engine.py` (NEW) | — |
| Test file | `matches/tests/test_tournament_tasks.py` (NEW) | — |
| Test file | `matches/tests/test_tournament_views.py` (extend) | — |
