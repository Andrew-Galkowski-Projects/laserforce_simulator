# CONF-02 seam contract — Per-Conference regional playoffs

The **single source of truth** for every name, signature, field, context key and
DOM id the Code, Tests and Docs agents share on branch
`conf-02-regional-playoffs`. Every decision below was locked at the CONF-02
grill (2026-09-02) and is recorded with its rationale and rejected alternatives
in [ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md).
The domain language is already written in CONTEXT.md under **Regional playoff**,
**Conference champion**, **Conference**, **Season phase** and **Standings**.
The CONF-01 foundation this builds on is
[ADR-0034](../../docs/adr/0034-conference-partition.md) and
[`.claude/worktrees/conf-01-seam-contract.md`](conf-01-seam-contract.md).

Nothing in this contract is open for renegotiation by an implementing agent. If
a name here turns out to be impossible, stop and report it rather than
inventing a substitute — a silent rename is exactly the failure mode this
document exists to prevent.

---

## 1. Scope

**Ship:** in a Season with **two or more** Conferences, a `tournament`
`SeasonPhase` builds **N first-class `Tournament` rows — one per Conference**,
each seeded from that Conference's own corpus, each drained through the
unchanged bracket engine, each crowning one **Conference champion**. The phase
completes only when **every** one of the N brackets has drained.
`Season.champion_team` **stays NULL** for such a Season.

**Byte-identical:** a Season with **zero or one** Conference is untouched in
every respect — one Season-wide bracket, stored on `SeasonPhase.tournament`
exactly as today, seeded from the Season-wide Standings, stamping
`Season.champion_team` exactly as today.

**Do NOT build (later CONF slices):** top-N-per-Conference Worlds qualification
(CONF-03); the cross-Conference Worlds Tournament (CONF-04); any placement /
elimination-depth / top-N ranking API on `Tournament`; a create-League
Conference composer change; per-Conference map pools.

**No Score Calibration re-baseline.** No simulation mechanic changes — the only
shift is how many `Tournament` rows a tournament phase produces and which Match
corpus seeds them.

---

## 2. Model — `laserforce_simulator/matches/models.py`

### 2.1 CHANGED `Tournament` — two additive nullable FKs

Declared inside `class Tournament` (models.py ~line 2118), placed immediately
**after** the existing `champion` FK and **before** the
`final_series_length` block, so the linkage columns read together:

```python
# CONF-02 — regional-playoff linkage (ADR-0035). Both NULL for a sandbox
# Tournament AND for the Season-wide bracket of a 0/1-Conference Season
# (that one is still reached through SeasonPhase.tournament). Non-NULL ONLY
# on a regional Tournament: one per Conference of a >= 2-Conference Season's
# tournament phase.
season_phase = models.ForeignKey(
    "matches.SeasonPhase",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="regional_tournaments",
)
conference = models.ForeignKey(
    "matches.Conference",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="tournaments",
)
```

`on_delete=models.SET_NULL` on both is the house discriminator-FK precedent:
`Match.season_phase` (models.py:70-76), `Match.conference` (models.py:87-93) and
`SeasonPhase.tournament` (the existing embed pointer) all use `SET_NULL`.
Deleting a Season must not cascade a Tournament's history away.

**`Tournament` has no `class Meta`** (verified — it declares fields, `__str__`,
and methods only). **Do not add one.** No new ordering, no new constraint, no
`unique_together` on `(season_phase, conference)`. The build path is guarded by
the idempotence check in §3.3, not by a database constraint; adding one would be
a schema change beyond what ADR-0035 authorised.

> **Naming hazard — read this twice.** `Tournament` now carries BOTH:
> - `tournament.season_phase` — the **new forward FK** added here (the regional
>   link), reverse-accessed as `phase.regional_tournaments`.
> - `tournament.season_phases` — the **pre-existing reverse manager** of
>   `SeasonPhase.tournament` (`related_name="season_phases"`), i.e. "the phases
>   whose single embed pointer targets me".
>
> They differ by one character and mean opposite directions. Code and Tests must
> never substitute one for the other. A regional Tournament has
> `season_phase_id` set and an **empty** `season_phases` manager; a Season-wide
> embedded Tournament has `season_phase_id is None` and a **non-empty**
> `season_phases` manager.

### 2.2 NOT changed — `SeasonPhase.tournament`

`SeasonPhase.tournament` (models.py ~2055, `null=True, blank=True,
on_delete=SET_NULL, related_name="season_phases"`) keeps its exact current
meaning, type, kwargs and related name. It is **not** widened to a M2M, **not**
deprecated, and **not** written for a regional build.

Locked rule, stated so neither agent "helpfully" generalises it:

| Season shape | `phase.tournament_id` | `phase.regional_tournaments` |
| --- | --- | --- |
| 0 or 1 Conference, tournament phase built | the one Tournament's id | empty |
| >= 2 Conferences, tournament phase built | **stays NULL** | N rows, one per Conference |
| any shape, tournament phase not yet built | NULL | empty |

The consequence is a clean discriminator: a non-empty `regional_tournaments`
**is** the "this phase went regional" signal, and the two storage paths never
both hold a row for the same phase.

### 2.3 Team-History FK-chain verification (explicitly required)

The Part2c-3f Team-History chain lives in
`laserforce_simulator/matches/league_screens/team_history.py` at lines 179-186
and 199-204:

```python
Q(match__series_match__node__tournament__season_phases__isnull=False)
Tournament.objects.filter(season_phases__isnull=False, participants__team=team)
```

**Verified: this chain still works and is byte-identical.** It traverses
`season_phases`, the reverse manager of `SeasonPhase.tournament`, which CONF-02
does not touch. The new field is named `season_phase` (singular) with
`related_name="regional_tournaments"`, so it introduces **no accessor collision**
and **no change to the generated SQL** of either query. Every existing row —
sandbox Tournaments (both new columns NULL, `season_phases` empty) and
Season-wide embedded Tournaments (both new columns NULL, `season_phases`
non-empty) — classifies exactly as it does today.

**Known, deliberate, OUT-OF-SCOPE consequence.** A *regional* Tournament is not
reachable through `season_phases`, so Team History will classify its
GameRounds as sandbox rather than season-embedded, and will not count a regional
bracket toward `playoff_appearances`. This is accepted for CONF-02 and must
**not** be "fixed" in this slice: widening that chain touches the Team-History
surface, which is outside the blast radius ADR-0035 authorised. Docs records it
as a known gap; a follow-up slice owns it. Tests must **not** assert Team-History
behaviour for regional tournaments in either direction.

### 2.4 The migration — DESCRIBED ONLY, do not generate it here

The Code agent creates exactly one migration. **Do not run `makemigrations`
while writing or reviewing this contract.**

| Property | Value |
| --- | --- |
| App | `matches` |
| File | `laserforce_simulator/matches/migrations/0058_tournament_regional_linkage.py` |
| Depends on | `0057_conference_match_conference` (verified as the current latest) |
| Operations | exactly two `migrations.AddField` |
| Field 1 | `model_name="tournament"`, `name="season_phase"`, `field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="regional_tournaments", to="matches.seasonphase")` |
| Field 2 | `model_name="tournament"`, `name="conference"`, `field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="tournaments", to="matches.conference")` |

**There is NO `RunPython`, NO backfill, NO data migration, and no follow-up
migration.** Both columns are nullable and default to NULL on every existing
row, which is the correct terminal state for all of them — per
[ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)'s
disposable-data posture. If Django auto-names the file differently, rename it to
`0058_tournament_regional_linkage.py`; the name is part of this contract so Docs
can reference it.

---

## 3. `Season` — changed and new methods

All of these live on `Season` in `laserforce_simulator/matches/models.py`.
Signatures below are **final**. Where a parameter is added it is **additive and
keyword-defaulted to `None`**, so every existing call site keeps working
unedited and the resulting query is byte-identical when the new argument is
omitted.

### 3.1 `_final_standings_for_phase` — additive `conference` parameter

**Current** (models.py:1280): `def _final_standings_for_phase(self, phase) -> "list[StandingsRow]"`

**Final signature:**

```python
def _final_standings_for_phase(self, phase, conference=None) -> "list[StandingsRow]":
```

Behaviour:

- `conference is None` — **byte-identical to today**. `team_ids =
  self.starting_team_ids_json or []`; `matches_qs = Match.objects.filter(season=self,
  is_completed=True).exclude(season_phase__phase_type="member_night")`. Not one
  character of the resulting query changes.
- `conference is not None` — two scoped substitutions and nothing else:
  - `team_ids = list(conference.starting_team_ids_json or [])`
  - the Match queryset gains `conference=conference` in its `filter(...)`, i.e.
    `Match.objects.filter(season=self, conference=conference,
    is_completed=True).exclude(season_phase__phase_type="member_night")`.

Everything downstream is untouched: the same 8-key match dicts (no
`date_played`), the same tolerant `Team.objects.filter(id__in=team_ids)`
`enrolled_teams` assembly, the same `compute_standings(completed_matches,
enrolled_teams)` call with `season_rounds` omitted. The `phase` argument keeps
its existing forward-compatibility-only role.

**The CONF-01 `season_standings` view is NOT refactored to call this.** See §7.

### 3.2 `_seed_order_for_phase` — additive `conference` parameter (decided: parameter, not a new method)

**Current** (models.py:1392): `def _seed_order_for_phase(self, phase) -> list[int]`

**Final signature:**

```python
def _seed_order_for_phase(self, phase, conference=None) -> list[int]:
```

**Decision, locked:** a new keyword parameter, **not** a sibling
`_regional_seed_order_for_phase` method. A second method would duplicate all
three mode branches and give the two paths room to drift — precisely the
failure mode the CLAUDE.md sub-agent guidance calls out. One method, one
`conference` axis.

All three `tournament_mode` branches split (ADR-0035: "all three seeding modes
split"):

| `tournament_mode` | `conference is None` (unchanged) | `conference is not None` |
| --- | --- | --- |
| `standings` | `[row.team_id for row in self._final_standings_for_phase(prior)]` | `[row.team_id for row in self._final_standings_for_phase(prior, conference=conference)]` |
| `strength` | `default_seed_order` over `self.starting_team_ids_json` | `default_seed_order` over `conference.starting_team_ids_json` |
| `unseeded` | `random.Random().shuffle` of `self.starting_team_ids_json` | `random.Random().shuffle` of `conference.starting_team_ids_json` |
| anything else | `[]` | `[]` |

Implementation note that pins the team-id source (decision 4): the single line

```python
team_ids = self.starting_team_ids_json or []
```

becomes

```python
if conference is not None:
    team_ids = list(conference.starting_team_ids_json or [])
else:
    team_ids = self.starting_team_ids_json or []
```

placed exactly where the current line sits — after the `standings` branch
returns, before the `strength` branch. The `strength` branch's bulk
`Team.objects.filter(id__in=team_ids).select_related(...).in_bulk()` load, its
`mean_overall = 0.0`-on-empty rule, and its `default_seed_order(team_ratings)`
call are **verbatim unchanged**; only `team_ids` differs. Same for `unseeded`'s
`random.Random()` shuffle (still non-deterministic, still NOT the SIM-07 seed
chain).

`prior` is resolved inside the `standings` branch by the existing
`self._preceding_phase(phase)` call — unchanged, and Conference-agnostic (phases
are a Season-level axis; Conferences are orthogonal).

### 3.3 NEW `_build_tournament_for_phase` — builds exactly ONE bracket

This is the helper the task brief demands be pinned so Code and Tests cannot
disagree. It is the extraction of today's inline build body in
`activate_pending_tournament_phase`, parameterised by Conference.

```python
def _build_tournament_for_phase(self, phase, conference=None) -> "Tournament | None":
```

Placed on `Season`, declared **immediately after**
`activate_pending_tournament_phase` and **before** `_seed_order_for_phase`.
Private (leading underscore): it is an internal build step, not a caller seam.

Contract:

1. `order = self._seed_order_for_phase(phase, conference=conference)`
2. `if phase.tournament_cut: order = order[: phase.tournament_cut]` — the cut
   applies **per Conference** (a cut of 4 in a 2-Conference Season yields two
   4-team brackets, not one 4-team bracket).
3. `if not order: return None` — **no `Tournament` row is created**, no
   participants, no linkage write. An empty order is a no-op, exactly as today.
4. Name:
   - `conference is None` — `f"{self.name} Playoffs"` when
     `phase.tournament_mode == "standings"`, else `f"{self.name} Tournament"`
     (**byte-identical to today's naming**).
   - `conference is not None` — `f"{self.name} — {conference.name} Playoffs"`
     when `phase.tournament_mode == "standings"`, else
     `f"{self.name} — {conference.name} Tournament"`. The separator is an
     **em-dash U+2014 surrounded by single spaces**, matching
     `Conference.__str__` and `SeasonPhase.__str__`.
5. `Tournament.objects.create(...)` with, verbatim from today,
   `format=phase.tournament_format`, `team_assembly="preset"`, `state="setup"`,
   `final_series_length`, `semifinal_series_length`,
   `quarterfinal_series_length`, `earlier_series_length`, `wb_advancers`,
   `lb_advancers`, `swiss_rounds` — all copied off `phase` — **plus**, and only
   when `conference is not None`, `season_phase=phase` and
   `conference=conference`. When `conference is None` both new columns are left
   at their NULL default (§2.2).
6. One `TournamentParticipant.objects.create(tournament=..., team_id=team_id,
   seed=position + 1)` per entry of `order`, in order. Seeding restarts at 1 for
   **each** Conference's bracket.
7. `tournament.lock_and_build()`.
8. `return tournament`.

The helper does **not** write `phase.tournament`. Wiring the single-bracket
embed pointer is the caller's job (§3.4), which is what keeps the two storage
paths from ever both firing.

### 3.4 CHANGED `activate_pending_tournament_phase` — branches on Conference count

**Signature unchanged:**

```python
@transaction.atomic
def activate_pending_tournament_phase(self) -> None:
```

Everything up to and including the mode gate is **unchanged**: the
`current_phase()` lookup, `phase.phase_type != "tournament"` guard, the
`phase.tournament_id is not None` early return, the `phase.pk is None` guard,
`prior = self._preceding_phase(phase)`, and the `standings`-requires-a-complete-prior
vs `strength`/`unseeded`-permits-`None`-prior gate.

The body **after** those gates is replaced by:

```python
conferences = self.ordered_conferences()
if len(conferences) >= 2:
    # CONF-02 — regional playoffs: one Tournament per Conference (ADR-0035).
    if phase.regional_tournaments.exists():
        return                      # idempotence guard for the regional path
    for conference in conferences:
        self._build_tournament_for_phase(phase, conference=conference)
    return

tournament = self._build_tournament_for_phase(phase)
if tournament is None:
    return
phase.tournament = tournament
phase.save(update_fields=["tournament"])
```

Pinned details:

- `len(conferences) >= 2` is the branch predicate. A **one**-Conference Season
  takes the Season-wide path — matching CONF-01, where a Season has zero or
  two-or-more Conferences and a stray single Conference degenerates gracefully.
- The regional idempotence guard `phase.regional_tournaments.exists()` lives
  **inside** the `>= 2` branch, deliberately: the 0/1-Conference path then runs
  the same number of queries it runs today (only `ordered_conferences()` is
  added). "Byte-identical" in ADR-0035 means rows, reads and behaviour — one
  extra `conferences` read on the multi-Conference branch predicate is expected
  and is not a violation.
- The regional loop is **all-or-nothing**: the method is already
  `@transaction.atomic`, so a failure part-way through N builds rolls all N back
  and the next activation call retries cleanly.
- A Conference whose seed order is empty simply produces no Tournament for that
  Conference (`_build_tournament_for_phase` returns `None`). The phase then holds
  fewer than N brackets; the completion predicate (§3.5) is defined over the rows
  that exist, so this degrades safely rather than deadlocking the cursor.
- The three existing call sites are **unchanged**:
  `Season.start_season` (models.py:1086) and
  `matches/simulation/entrypoints.py:955` + `:984`.

### 3.5 NEW `tournaments_for_phase` — the one caller seam

This is the single public accessor every drain caller and the Playoffs screen
uses. Nobody else re-implements the "regional else single" fallback.

```python
def tournaments_for_phase(self, phase) -> "list[Tournament]":
```

Placed on `Season`, declared immediately after `_build_tournament_for_phase`.
**Public** (no leading underscore) because `matches/tasks.py`,
`matches/league_views.py` and `matches/league_screens/playoffs.py` all call it,
and because Tests assert against it directly (§9).

Contract:

- `phase.pk is None` (the implicit `round_robin` fallback phase) ⇒ `[]`.
- `phase.phase_type != "tournament"` ⇒ `[]`.
- Otherwise, regional first:
  `regional = list(phase.regional_tournaments.select_related("conference").order_by("conference__ordinal", "id"))`
  — `if regional: return regional`.
- Else the Season-wide embed:
  `return [phase.tournament] if phase.tournament_id is not None else []`.

Ordering is **by Conference ordinal**, so the N brackets always present, drain
and render in the Conferences' declared display order. `id` is the deterministic
tiebreak.

Return shape is a plain `list` of `Tournament` instances (not a queryset), so
callers can iterate it repeatedly without re-querying.

### 3.6 NEW `_tournament_phase_complete` — the named completion predicate

The task brief asks the completion predicate for an N-bracket phase to be named.
It is:

```python
def _tournament_phase_complete(self, phase) -> bool:
```

Placed on `Season` beside its two siblings `_rr_phase_complete` (models.py:1216)
and `_member_night_phase_complete` (models.py:1158), whose naming it mirrors.

```python
tournaments = self.tournaments_for_phase(phase)
return bool(tournaments) and all(t.state == "completed" for t in tournaments)
```

That is the completion gate of decision 5: **the phase does not advance until
every one of its N regional brackets has drained.**

`Season._phase_complete` (models.py:1128) changes only its `tournament` arm.
Today:

```python
if phase.phase_type == "tournament":
    return (
        phase.tournament_id is not None
        and phase.tournament.state == "completed"
    )
```

becomes:

```python
if phase.phase_type == "tournament":
    return self._tournament_phase_complete(phase)
```

**This is byte-identical for a 0/1-Conference Season**, by inspection: with no
regional rows, `tournaments_for_phase` returns `[phase.tournament]` exactly when
`phase.tournament_id is not None` and `[]` otherwise, so
`bool(ts) and all(...)` reduces to `phase.tournament_id is not None and
phase.tournament.state == "completed"`. The `round_robin`, `member_night` and
fall-through-`False` arms of `_phase_complete` are untouched.

### 3.7 CHANGED `_stamp_champion_for_final_phase` — multi-Conference branch

**Signature unchanged:**

```python
def _stamp_champion_for_final_phase(self, final_phase) -> None:
```

Only the `tournament` arm changes. Today (models.py:1453):

```python
if final_phase.phase_type == "tournament":
    champion = final_phase.tournament.champion
    if champion is None:
        return
    self.state = "completed"
    self.champion_team = champion
    self.save()
    return
```

becomes:

```python
if final_phase.phase_type == "tournament":
    regional = list(final_phase.regional_tournaments.all())
    if regional:
        # CONF-02 — a Regional playoff crowns a Conference champion, NOT a
        # Season champion (ADR-0035). champion_team stays NULL until Worlds.
        if any(t.champion_id is None for t in regional):
            return
        self.state = "completed"
        self.save()
        return
    champion = final_phase.tournament.champion
    if champion is None:
        return
    self.state = "completed"
    self.champion_team = champion
    self.save()
    return
```

Pinned details:

- The multi-Conference branch **never assigns `self.champion_team`**. It stays
  NULL, exactly as CONF-01 already leaves it for a multi-Conference RR-final
  Season (the existing `self.conferences.count() >= 2` block further down at
  models.py:1477-1481, which is **unchanged**).
- `self.save()` with no `update_fields` matches the surrounding style.
- The `any(t.champion_id is None ...)` check is defensive and mirrors the
  existing single-bracket `if champion is None: return` guard. In practice it
  never blocks: `_stamp_champion_for_final_phase` is reached only after
  `_phase_complete(final_phase)` is `True`, and the engine stamps `champion`
  together with `state="completed"`.
- The `round_robin` / implicit-fallback arm below is **entirely unchanged**,
  including CONF-01's `self.conferences.count() >= 2` NULL-champion block and
  the `compute_standings(...)[0]` stamp.

`Season.complete_if_finished` (models.py:1089) is **unchanged** — it already
gates on `_phase_complete(final_phase)` and delegates to
`_stamp_champion_for_final_phase`.

### 3.8 Summary table of the `Season` seam

| Method | Status | Final signature |
| --- | --- | --- |
| `_final_standings_for_phase` | changed (additive param) | `(self, phase, conference=None) -> "list[StandingsRow]"` |
| `_seed_order_for_phase` | changed (additive param) | `(self, phase, conference=None) -> list[int]` |
| `_build_tournament_for_phase` | **new** | `(self, phase, conference=None) -> "Tournament \| None"` |
| `activate_pending_tournament_phase` | changed (body) | `(self) -> None` |
| `tournaments_for_phase` | **new, public** | `(self, phase) -> "list[Tournament]"` |
| `_tournament_phase_complete` | **new** | `(self, phase) -> bool` |
| `_phase_complete` | changed (tournament arm delegates) | `(self, phase: "SeasonPhase") -> bool` |
| `_stamp_champion_for_final_phase` | changed (tournament arm) | `(self, final_phase) -> None` |
| `_preceding_phase` | **unchanged** | `(self, phase) -> "SeasonPhase \| None"` |
| `complete_if_finished` | **unchanged** | `(self) -> None` |
| `ordered_conferences` | **unchanged** | `(self) -> "list[Conference]"` |

---

## 4. The drain — callers generalise, the engine does not

`matches/tournament_engine.py::play_next_bracket_round(tournament: Tournament)
-> int` (line 391) and `play_next_node(tournament)` (line 88) take **one**
Tournament and are **NOT CHANGED**. Neither is `lock_and_build`,
`find_next_node`, `_collapse_drop_byes`, `stage_progress`, or any of the five
bracket formats. Every generalisation is in the callers.

The locked pacing rule, which follows CONF-01's parallel-overlay calendar: **one
stage-step advances every regional bracket by one stage.** California and Nevada
play their semifinals in the same week, not one after the other. Each caller
therefore wraps its per-tournament call in a sum over the phase's tournaments.

There are exactly three drain callers. All three were located by grepping
`play_next_bracket_round` / `play_next_node` across `laserforce_simulator/`
excluding tests.

### 4.1 `matches/tasks.py::play_playoffs_task` (line ~410, body at ~420-475)

Changes:

- Replace `tournament = phase.tournament` with
  `tournaments = season.tournaments_for_phase(phase)`.
- The guard becomes
  `if phase is None or phase.phase_type != "tournament" or not tournaments:
  return {"completed": 0, "total": 0}` (the `phase.tournament_id is None` clause
  is subsumed by `not tournaments`).
- `_stage_counts()` **aggregates across all N tournaments**: flatten each
  tournament's nodes, call `stage_progress(flat)` per tournament, and return the
  element-wise sums as `(sum_completed, sum_total)`. For N == 1 this returns
  exactly today's tuple.
- The drain loop keeps its node granularity and its two PLAY-01 cancel checks
  (top, and between nodes) verbatim, but round-robins across the tournaments:

  ```python
  while True:
      if _play_cancel_requested(season_id):
          completed, total = _stage_counts()
          return {"completed": completed, "total": total, "cancelled": True}
      progressed = False
      for tournament in tournaments:
          if play_next_node(tournament) is not None:
              progressed = True
      if not progressed:
          break
      completed, total = _stage_counts()
      self.update_state(state="PROGRESS", meta={"completed": completed, "total": total})
  ```

- `season.complete_if_finished()` after the loop, and the `finally:` block
  clearing `active_play_job_id`, are **unchanged**.
- Return shape is **unchanged**: `{"completed": int, "total": int}` STAGE counts,
  plus `"cancelled": True` on a cancel.

### 4.2 `matches/tasks.py::play_season_task` — the phase-aware tail (~lines 337-390)

Changes:

- The gate `phase is not None and phase.phase_type == "tournament" and
  phase.tournament_id is not None` becomes `phase is not None and
  phase.phase_type == "tournament" and bool(tournaments)`, where
  `tournaments = season.tournaments_for_phase(phase)` is resolved just above it.
- `_stage_counts()` aggregates across all N, exactly as in §4.1.
- Add a local stage-step helper:

  ```python
  def _drain_one_stage() -> int:
      return sum(play_next_bracket_round(t) for t in tournaments)
  ```

  `sum` over a generator evaluates **every** term, so all N brackets advance one
  stage per call. Do not rewrite this as `any(...)` or a short-circuiting
  expression.
- The unbounded branch becomes `while _drain_one_stage() > 0:` and the budgeted
  branch becomes `for _ in range(bracket_budget): if _drain_one_stage() == 0:
  break`. **One budget unit = one stage across all N brackets** — the whole point
  of the parallel-overlay rule.
- `rr_weeks_played`, `max_matchdays`, the shared-budget arithmetic
  `max(0, max_matchdays - rr_weeks_played)`, the PROGRESS emissions,
  `season.complete_if_finished()`, and the RR-shape `{"completed": n, "total": n}`
  return when no tournament tail runs are all **unchanged**.

### 4.3 `matches/league_views.py::play_week` — the playoff branch (~lines 3074-3086)

Changes:

- The gate `phase.tournament_id is not None` becomes `bool(tournaments)` with
  `tournaments = season.tournaments_for_phase(phase)` resolved above it.
- `play_next_bracket_round(phase.tournament)` becomes

  ```python
  for tournament in tournaments:
      play_next_bracket_round(tournament)
  ```

  so "Play One Week" advances **every** Conference's bracket by one stage.
- `season.complete_if_finished()` and the
  `redirect("season_dashboard", season_id=season.id)` are **unchanged**, as is
  the deferred import and the "no `transaction.atomic` wrapper needed" comment
  (each `play_next_node` inside remains per-Match atomic, ADR-0016).

### 4.4 The activation callers — unchanged

`matches/simulation/entrypoints.py` lines 955 and 984 call
`season.activate_pending_tournament_phase()` followed by
`season.complete_if_finished()` after Round 1 and Round 2 of a scheduled Match.
Both are **unchanged** — the generalisation lives entirely inside the method.
`Season.start_season`'s call at models.py:1086 is likewise unchanged.

---

## 5. Playoffs screen — `matches/league_screens/playoffs.py`

The view builds a `brackets` list, one entry per rendered bracket. Today that is
one entry per tournament phase. **A multi-Conference phase now appends one entry
per regional Tournament**, each carrying its Conference.

### 5.1 Changed loop

The per-phase loop body is replaced. The phase queryset
(`view_season.phases.filter(phase_type="tournament").select_related("tournament").order_by("ordinal")`)
and the deferred `from matches.tournament_views import _build_rounds` import are
**unchanged**. For each phase:

```python
tournaments = view_season.tournaments_for_phase(phase)
if not tournaments:
    brackets.append({ ...pending stub... })
    continue
for tournament in tournaments:
    rounds = _build_rounds(tournament)["winners"]
    brackets.append({ ...built entry... })
```

`_build_rounds` is called **per Tournament**, unchanged, and the `["winners"]`
slice is kept (embedded season tournaments are single-elimination).

### 5.2 Exact `brackets` entry shape

Every entry — pending and built — carries these keys. The three existing keys
keep their exact current values; **two keys are new**.

| Key | Pending stub | Built entry |
| --- | --- | --- |
| `phase` | the `SeasonPhase` | the `SeasonPhase` |
| `tournament` | `None` | the `Tournament` |
| `name` | `"Playoffs"` | `tournament.name` |
| `rounds` | `[]` | `_build_rounds(tournament)["winners"]` |
| `champion` | `None` | `tournament.champion` |
| `pending` | `True` | `False` |
| **`conference`** *(new)* | `None` | `tournament.conference` (a `Conference`, or `None` for a Season-wide bracket) |
| **`key`** *(new)* | `str(phase.ordinal)` | see below |

`key` is the DOM-id discriminator, and it is what stops the N regional brackets
of one phase from colliding on `phase.ordinal`:

- `tournament.conference is None` ⇒ `key = str(phase.ordinal)` — **every existing
  DOM id on a 0/1-Conference Season is byte-identical to today.**
- `tournament.conference is not None` ⇒
  `key = f"{phase.ordinal}-{tournament.conference.ordinal}"`.

The remaining context keys — `league`, `displayed_season`, `sidebar_links`,
`sidebar_active`, `season_options`, `view_season`, `selected_season_id`,
`brackets` — are **unchanged**, as are `_coerce_view_season`, the
`displayed_season` fallback, and the GET-only guard.

### 5.3 Template — `laserforce_simulator/templates/leagues/playoffs.html`

Two changes, nothing else.

**(a) Every `bracket.phase.ordinal` in a DOM id becomes `bracket.key`.** There
are five occurrences, all inside the `{% for bracket in brackets %}` block:

| Element | Today | After |
| --- | --- | --- |
| `<section>` | `id="league-playoffs-phase-{{ bracket.phase.ordinal }}"` | `id="league-playoffs-phase-{{ bracket.key }}"` |
| champion banner | `id="league-playoffs-champion-{{ bracket.phase.ordinal }}"` | `id="league-playoffs-champion-{{ bracket.key }}"` |
| bracket wrapper | `id="league-playoffs-bracket-{{ bracket.phase.ordinal }}"` | `id="league-playoffs-bracket-{{ bracket.key }}"` |
| round column | `id="league-playoffs-round-{{ bracket.phase.ordinal }}-{{ round.bracket_round }}"` | `id="league-playoffs-round-{{ bracket.key }}-{{ round.bracket_round }}"` |
| node card + node score | `id="league-playoffs-node-{{ bracket.phase.ordinal }}-..."` and `id="league-playoffs-node-score-{{ bracket.phase.ordinal }}-..."` | same with `{{ bracket.key }}` |

Because `key == str(phase.ordinal)` whenever `conference is None`, **no existing
rendered id changes** for a 0/1-Conference Season.

**(b) A Conference sub-heading**, inserted immediately after the existing
`<h2 class="h4">{{ bracket.name }}</h2>`:

```html
{% if bracket.conference %}
<div class="text-muted small mb-2" id="league-playoffs-conference-{{ bracket.key }}">{{ bracket.conference.name }}</div>
{% endif %}
```

It renders **only** for a regional bracket, so a 0/1-Conference Season's markup
is unchanged. `id="league-playoffs-conference-<key>"` is the stable hook Tests
assert on for "N labelled brackets".

Everything else in the template — the season filter form, the empty-state
notice, the pending stub alert, the node cards, seeds, scores, game links and
winner lines — is **unchanged**.

---

## 6. Worked example — a 2-Conference Season

Setup: League *Pacific*, Season *2027* with 8 enrolled Teams, two Conferences —
**California** (ordinal 1, 4 teams: CA-A, CA-B, CA-C, CA-D) and **Nevada**
(ordinal 2, 4 teams: NV-A, NV-B, NV-C, NV-D). Two phases: ordinal 1
`round_robin` (`single_round_robin`), ordinal 2 `tournament` with
`tournament_mode="standings"`, `tournament_format="single_elimination"`,
`tournament_cut=0`.

**Regular season.** `start_season()` snapshots
`Season.starting_team_ids_json` (all 8) and each
`Conference.starting_team_ids_json` (4 each). `scheduled_fixtures_by_phase()`
emits two intra-Conference round-robins overlaid on the same matchday calendar
(CONF-01, unchanged). Each Match is stamped with its `conference` at Round-1
create (CONF-01, unchanged). Twelve Matches total: six California, six Nevada.

**The build.** When the last RR Match completes, `simulate_scheduled_round`
calls `activate_pending_tournament_phase()`. `current_phase()` returns the
ordinal-2 tournament phase; `phase.tournament_id is None`; the `standings` gate
passes because `_phase_complete(prior)` is `True` (both Conferences' RRs are
done). `ordered_conferences()` has length 2, `phase.regional_tournaments` is
empty, so the loop builds one bracket per Conference.

Rows after the build:

| Table | Count | Detail |
| --- | --- | --- |
| `Tournament` | **2** | `"2027 — California Playoffs"` (`season_phase_id=<phase>`, `conference_id=<California>`, `format="single_elimination"`, `state="active"` after lock) and `"2027 — Nevada Playoffs"` (same, `conference_id=<Nevada>`) |
| `TournamentParticipant` | **8** | 4 per Tournament. California seeds 1-4 = California's Standings rank order over its six Matches only. Nevada seeds 1-4 = Nevada's rank order over its six. Seeding restarts at 1 in each bracket. |
| `BracketNode` | **6** | 3 per Tournament (two semifinals + one final) |
| `SeasonPhase.tournament_id` | — | **stays NULL** |
| `phase.regional_tournaments` | 2 | California first, Nevada second (ordered by `conference__ordinal`) |
| `Season.champion_team` | — | NULL |

No California team ever appears in Nevada's bracket, and no bracket node pairs a
California team against a Nevada team — the intra-Conference invariant holds
through the playoff.

**The completion sequence.** With "Play One Week" (`play_week`), or
`play_season_task`'s budgeted tail, or `play_playoffs_task`'s unbounded drain:

1. **Stage-step 1.** `play_next_bracket_round` runs once on each Tournament.
   Both California semifinals and both Nevada semifinals clinch — 4 nodes.
   `_stage_counts()` aggregates to `(2, 4)` (one completed stage of two, per
   bracket, summed). `_tournament_phase_complete(phase)` is `False`: neither
   Tournament is `completed`. `complete_if_finished()` no-ops.
   `Season.champion_team` NULL.
2. **Stage-step 2.** Both finals clinch. Each Tournament stamps its `champion`
   and flips `state="completed"`. Suppose CA-B and NV-C win: those are the two
   **Conference champions**, reachable as
   `phase.regional_tournaments.get(conference=<California>).champion` and
   `...get(conference=<Nevada>).champion`.
3. **Gate.** `_tournament_phase_complete(phase)` is now `True` (both drained), so
   `_phase_complete(final_phase)` is `True` and `complete_if_finished()` proceeds
   to `_stamp_champion_for_final_phase`. The `regional` list is non-empty and no
   `champion_id` is NULL, so it sets `state="completed"` and **returns without
   touching `champion_team`**.
4. **Terminal state.** `season.state == "completed"`,
   `season.champion_team is None`, two Conference champions crowned, and
   `current_phase()` returns `None`.

Had only California's bracket drained, `_tournament_phase_complete` would be
`False`, the Season would stay `active`, and the cursor would stay parked on the
tournament phase. That is the completion gate.

**The Playoffs screen** for this Season renders two `<section>`s under the one
phase: `#league-playoffs-phase-2-1` headed "2027 — California Playoffs" with
`#league-playoffs-conference-2-1` reading "California", and
`#league-playoffs-phase-2-2` / `#league-playoffs-conference-2-2` for Nevada.

---

## 7. Explicitly NOT changed

Touching anything on this list is a contract violation. If a change here looks
necessary, stop and report rather than making it.

| Surface | Location | Why it stays |
| --- | --- | --- |
| `SeasonPhase.tournament` | models.py ~2055 | Keeps its exact meaning, type, kwargs and `related_name="season_phases"`. It is the 0/1-Conference storage path and nothing else. |
| The bracket engine | `matches/tournament_engine.py`, `matches/bracket.py` | `play_next_bracket_round`, `play_next_node`, `play_specific_node`, `find_next_node`, `advance_winner`, `advance_loser`, `stage_progress`, `_collapse_drop_byes`, `series_length_for_round` — reused **verbatim**. |
| `Tournament.lock_and_build` | models.py:2225 | Reused verbatim for every regional bracket; all five formats unchanged. |
| The CONF-01 `season_standings` view | `matches/league_views.py` ~390-460 | The per-Conference `standings_groups` block is **not** refactored to call `_final_standings_for_phase`. The two Conference-scoped queries coexist; ADR-0035 records consolidation as a follow-up. Not one line of that block, nor of `templates/seasons/standings.html`, changes. |
| `Match.conference` stamping | models.py:87-93; `matches/simulation/entrypoints.py` Round-1 create; the three play-loop `conference=conf_by_team.get(...)` sites | CONF-02 **reads** the discriminator; it never changes how it is written. |
| `compute_standings` | `matches/standings.py` | Unchanged. CONF-02 only changes which Matches and which team ids are fed to it. |
| The simulator | `matches/simulation/*`, tick engine, scoring, MVP | No mechanic changes. **No Score Calibration re-baseline.** |
| 0/1-Conference behaviour | everywhere | One bracket, on `SeasonPhase.tournament`, Season-wide seeding, `champion_team` stamped from `tournament.champion`, identical DOM ids. |
| Team History | `matches/league_screens/team_history.py` | The `season_phases__isnull=False` chain and the `playoff_appearances` count are untouched (§2.3). |
| `Tournament` sandbox surfaces | `matches/tournament_views.py` | `tournaments = Tournament.objects.order_by("-id")` (line 107) already lists Season-embedded Tournaments alongside sandbox ones; regional Tournaments appearing there is existing behaviour, not a regression. No filter is added. |
| `Season.complete_if_finished`, `_preceding_phase`, `_rr_phase_complete`, `_member_night_phase_complete`, `_fixtures_for_phase`, `scheduled_fixtures*`, `ordered_conferences`, `_scheduled_conference_partitions`, `conference_by_team_id`, `start_season` | models.py | Unchanged. |
| `Conference` model | models.py:1746 | No new fields, no Meta change. |
| `Tournament.Meta` | — | Does not exist and is not added (§2.1). |

---

## 8. File ownership — three parallel agents, zero collisions

Every file below is owned by exactly one agent. **Do not edit a file you do not
own**; if you believe a change is needed in someone else's file, report it
instead.

### Code agent — owns

- `laserforce_simulator/matches/models.py` (§2.1, §3)
- `laserforce_simulator/matches/migrations/0058_tournament_regional_linkage.py` (§2.4, new file)
- `laserforce_simulator/matches/tasks.py` (§4.1, §4.2)
- `laserforce_simulator/matches/league_views.py` — **only** the `play_week`
  playoff branch at ~3074-3086 (§4.3). The `season_standings` block at ~390-460
  is off-limits.
- `laserforce_simulator/matches/league_screens/playoffs.py` (§5.1, §5.2)
- `laserforce_simulator/templates/leagues/playoffs.html` (§5.3)

Runs `python -m black laserforce_simulator` on its own files when done.

### Tests agent — owns

- `laserforce_simulator/matches/tests/test_regional_playoffs.py` (new)
- `laserforce_simulator/matches/tests/test_regional_playoffs_drain.py` (new)
- `laserforce_simulator/matches/tests/test_league_playoffs.py` (existing, 187
  lines — **append** one new `TestCase` class; do not restructure the file)

Touches no production file.

### Docs agent — owns

- `PLAN.md` — flip the **CONF-02** bullet (line ~611) from `[NOT STARTED]` to
  done, and retire the plural "regional qualifiers" wording per ADR-0035 in
  favour of one **Conference champion** per Conference. Do not touch the CONF-03
  or CONF-04 bullets' substance.
- `laserforce_simulator/matches/CLAUDE.md` — a **CONF-02** subsection covering
  the two new `Tournament` FKs, the naming hazard of §2.1, the
  `tournaments_for_phase` caller seam, and the completion gate.
- `PLAN-completed.md` — if the house convention moves the finished bullet there.

Docs must **NOT** rewrite:

- `docs/adr/0035-regional-playoffs-one-tournament-per-conference.md` — already
  written and Accepted at the grill. Reference it; do not edit it.
- `docs/adr/0034-conference-partition.md` — CONF-01's ADR, closed.
- `CONTEXT.md` — the **Regional playoff**, **Conference champion** and
  **Conference** terms are already written (lines ~431-439) and already describe
  CONF-02's behaviour. Do not re-word them.
- This contract.

---

## 9. Test boundary

### 9.1 The no-simulation rule

**Brackets are drained by driving the engine, never by running the simulator.**
A real 2-Conference playoff would add minutes to the suite. Two permitted
techniques:

1. **Engine-driven** — call
   `matches.tournament_engine.play_next_bracket_round(tournament)` or
   `play_next_node(tournament)` directly, under a small `ROUND_TICKS` patch, when
   the test is actually exercising the drain.
2. **Stamp node winners directly** — set `BracketNode.winner` /
   `Tournament.champion` / `Tournament.state = "completed"` on the persisted rows
   when the test is exercising the *gate* rather than the drain. This is the
   preferred technique for the completion-gate tests, because it isolates
   `_tournament_phase_complete` and `_stamp_champion_for_final_phase` from any
   simulation at all.

**Forbidden:** `mock.patch` of `_seed_order_for_phase`,
`_build_tournament_for_phase`, `activate_pending_tournament_phase`,
`tournaments_for_phase`, `lock_and_build`, `play_next_bracket_round`, or
`play_next_node`. Tests must exercise the real code path. Patching `ROUND_TICKS`
for speed is fine and is existing house practice; patching the seam under test
is not.

**Never assert on exact simulated point totals.** Assertions are schema-level:
row counts, ids, seeds, states, booleans, context keys, DOM ids, status codes.

### 9.2 Public names Tests MAY assert against

- `Tournament.season_phase` / `Tournament.season_phase_id`
- `Tournament.conference` / `Tournament.conference_id`
- `SeasonPhase.regional_tournaments` (the reverse manager)
- `Conference.tournaments` (the reverse manager)
- `SeasonPhase.tournament` / `tournament_id` (that it stays NULL in the regional
  case, and holds the one bracket in the 0/1-Conference case)
- `Season.tournaments_for_phase(phase)` — list contents and **order**
- `Season.activate_pending_tournament_phase()` — its effects
- `Season.complete_if_finished()` — its effects
- `Season.state`, `Season.champion_team`, `Season.current_phase()`
- `Tournament.champion`, `Tournament.state`, `TournamentParticipant.seed` /
  `team_id`
- `play_playoffs_task` / `play_season_task` return dicts (`completed`, `total`,
  `cancelled`) and the `play_week` view's 302
- The Playoffs-screen context keys of §5.2 and the DOM ids of §5.3

### 9.3 Internal detail Tests must NOT assert on

- `_build_tournament_for_phase`, `_seed_order_for_phase`,
  `_final_standings_for_phase`, `_tournament_phase_complete`,
  `_stamp_champion_for_final_phase`, `_phase_complete`, `_preceding_phase` —
  private. Assert their **observable effects** (rows built, seeds, completion,
  champion) through the public surface above. The one permitted exception:
  the byte-identity regression pins in §9.4 may call
  `_final_standings_for_phase` and `_seed_order_for_phase` directly with and
  without `conference=`, because proving those two signatures are additive is the
  point of the pin.
- Query counts, SQL text, `select_related` / `prefetch_related` choices.
- The internal ordering of `TournamentParticipant` row creation (assert the
  `seed` values, not the insertion order).
- Tournament `name` strings beyond the fact that a regional name contains its
  Conference's name (do not hard-code the em-dash separator into an equality
  assertion; use `in`).
- Team History behaviour for regional Tournaments (§2.3 — a known gap, in either
  direction).

### 9.4 Required coverage

**`matches/tests/test_regional_playoffs.py`** — model, build, seeding, gate.

1. **N tournaments built.** A 2-Conference Season with a trailing `standings`
   tournament phase builds exactly 2 `Tournament` rows; `phase.tournament_id is
   None`; `season.tournaments_for_phase(phase)` returns both in Conference-ordinal
   order; each has `season_phase_id == phase.id` and the right `conference_id`.
2. **Per-Conference participants and seeds.** Each Tournament's participant
   `team_id`s are exactly its own Conference's snapshot ids and no other's; seeds
   are `1..n` in each bracket independently; no bracket contains a team from the
   other Conference.
3. **All three modes split.** One test per `tournament_mode` (`standings`,
   `strength`, `unseeded`) asserting 2 brackets with disjoint,
   Conference-correct participant sets. For `strength`, inject deterministic
   player stats so the rank order is predictable; for `unseeded`, assert set
   membership only, never order.
4. **The `tournament_cut` applies per Conference.** A cut of 2 in a
   4-teams-per-Conference Season yields two 2-team orders (note: `lock_and_build`
   requires >= 4 participants, so exercise the cut through
   `_seed_order_for_phase`'s output or a Conference large enough to survive the
   cut — do not assert a 2-participant bracket builds).
5. **Idempotence.** Calling `activate_pending_tournament_phase()` twice builds no
   extra Tournament, participant or node rows.
6. **The completion gate.** Draining only one of the two brackets leaves
   `_phase_complete` false via the public surface: `season.current_phase()` still
   returns the tournament phase, `season.state == "active"`. Draining both flips
   `season.state == "completed"` and leaves `season.champion_team is None`. Both
   Conference champions are non-NULL and reachable off `regional_tournaments`.
7. **Byte-identical regression pin — 0 Conferences.** A Season with **no**
   Conferences and the same phase composition builds **exactly one** Tournament,
   reachable via `phase.tournament`, with `season_phase_id is None` and
   `conference_id is None`; `phase.regional_tournaments` is empty;
   `tournaments_for_phase` returns that single bracket; and on drain
   `season.champion_team == tournament.champion`.
8. **Byte-identical regression pin — 1 Conference.** Same as (7) for a Season
   with exactly one Conference (the `>= 2` predicate must not fire).
9. **Additive-signature pin.** `_final_standings_for_phase(phase)` and
   `_seed_order_for_phase(phase)` called with no `conference` return the same
   values on a multi-Conference Season as they do today (Season-wide), proving
   the parameter is additive and the default path is unscoped.

**`matches/tests/test_regional_playoffs_drain.py`** — the three callers.

10. `play_playoffs_task` on a 2-Conference phase drains **both** brackets to
    champions, returns aggregated `{"completed", "total"}`, and completes the
    Season with `champion_team` NULL.
11. `play_playoffs_task` guard: a phase with no tournaments returns
    `{"completed": 0, "total": 0}`.
12. `play_week` (POST) on a 2-Conference tournament phase advances **both**
    brackets by one stage in a single request (assert the node-resolution delta
    on each Tournament), returns 302, and does not complete the Season early.
13. `play_season_task`'s tournament tail with `max_matchdays` set spends one
    budget unit per stage **across** both brackets (assert both advanced equally),
    and with `max_matchdays=None` drains both to completion.
14. A 0-Conference regression pin through at least one caller — the drain path is
    unchanged for a single bracket.

**`matches/tests/test_league_playoffs.py`** — append one `TestCase`.

15. The Playoffs screen for a 2-Conference Season yields **N labelled brackets**:
    `len(response.context["brackets"]) == 2`, each entry's `conference` is the
    right `Conference`, each `key` is `"<phase.ordinal>-<conference.ordinal>"`,
    and the rendered HTML contains
    `id="league-playoffs-conference-2-1"` / `-2-2"` plus each Conference's name.
16. The 0-Conference regression pin: one bracket entry, `conference is None`,
    `key == str(phase.ordinal)`, and the rendered DOM ids are exactly the ones the
    existing tests already assert (no `league-playoffs-conference-` element
    present).

### 9.5 Why these test file placements

Per CLAUDE.md, simulator logic goes in `matches/tests/simulation_tests.py` and
match/round model + view behaviour in the `matches` tests package. Within that
package the house convention is one file per slice, and the relevant existing
homes are large: `test_season_playoffs.py` (975 lines, LG-02-Part2c-1's
`play_playoffs_task` + play views), `test_season_phase.py` (1991 lines),
`test_conference.py` (567 lines, CONF-01's model seams),
`test_tournament_models.py` (2511 lines). Appending a whole slice to any of
those buries it and creates a merge hot-spot for the parallel agents.
`test_manage_conferences.py` (277 lines) covers the CONF-05 manage-Conferences
view and is unrelated. So CONF-02 gets **two new files**, mirroring how CONF-01
took `test_conference.py`. The one exception is the Playoffs *screen*:
`test_league_playoffs.py` is only 187 lines, is exactly and only about that
screen, and already carries the compose-a-Season-with-a-tournament-phase
fixture pattern the new cases need — so the screen cases append there rather
than fragmenting screen coverage across two files.

---

## 10. Definition of done

- One migration, `0058_tournament_regional_linkage`, two `AddField`s, no
  `RunPython`.
- `python laserforce_simulator/manage.py makemigrations --check --dry-run`
  reports no further changes.
- `python -m black laserforce_simulator` is clean.
- The full `pytest` suite passes, reported with exact counts (e.g. "N passed, 0
  failed"), not "tests pass".
- A 0-Conference and a 1-Conference Season behave identically to `main` in rows,
  reads, champion stamping and rendered DOM ids.

---

## 11. AMENDMENT (post-approval) — Team History counts regional playoffs

**Status:** added after the contract was reviewed. The original §2.3 recorded the
Team-History blind spot as out of scope; the maintainer elected to close it inside
CONF-02. This section supersedes that "out of scope" note. Everything else in
§2.3 stands — in particular, the existing `season_phases` chain is still
byte-identical and is NOT replaced, only supplemented.

### 11.1 Why the gap exists

`matches/league_screens/team_history.py::_build_overall_context` identifies a
season-embedded playoff through `tournament.season_phases` — the reverse manager
of `SeasonPhase.tournament`. A **regional** Tournament is linked the other way,
via the new `Tournament.season_phase` forward FK (§2.1), and no `SeasonPhase`
points at it. So without this amendment a regional playoff Round would be
classified as standalone-sandbox play and excluded, and regional brackets would
not count toward `playoff_appearances`.

### 11.2 The two changed queries — exact shape

Both live in `_build_overall_context`. `Q` is already imported in this module.

**(a) The Round corpus.** The third `Q` term is ADDED to the existing `Q` pair;
the `match__is_completed=True` kwarg and the `.only(...)` / `.distinct()` tail
are unchanged:

```python
rounds = (
    GameRound.objects.filter(
        Q(match__season__isnull=False)
        | Q(match__series_match__node__tournament__season_phases__isnull=False)
        # CONF-02 — a REGIONAL playoff Tournament is linked by the forward FK
        # Tournament.season_phase (ADR-0035), not by the season_phases reverse
        # manager, so it needs its own term. Note the one-character difference.
        | Q(match__series_match__node__tournament__season_phase__isnull=False),
        match__is_completed=True,
    )
    .filter(Q(team_red=team) | Q(team_blue=team))
    .only("team_red_id", "team_blue_id", "red_points", "blue_points")
    .distinct()
)
```

**(b) `playoff_appearances`.** The bare `season_phases__isnull=False` kwarg
becomes a two-term `Q` OR. Positional `Q` objects MUST precede the
`participants__team=team` keyword argument or Python raises `SyntaxError`:

```python
playoff_appearances = (
    Tournament.objects.filter(
        Q(season_phases__isnull=False) | Q(season_phase__isnull=False),
        participants__team=team,
    )
    .distinct()
    .count()
)
```

The existing `.distinct()` is load-bearing on both and must survive: the added
term introduces another to-many join and would otherwise duplicate rows.

### 11.3 What must NOT change

- The `season_phases` term stays exactly as it is. A Season-wide embedded
  playoff is still found by it; the new term is purely additional.
- `championships` (`Season.objects.filter(champion_team=team).count()`) is
  untouched. A **Conference champion is not a Season champion** — a regional
  playoff win must NOT increment `championships`. `Season.champion_team` stays
  NULL for multi-Conference Seasons (decision 5), so this counter naturally
  stays 0 for them, which is correct and intended.
- `_build_seasons_context` and every other tab are untouched.
- Standalone sandbox Tournaments still have BOTH `season_phase_id IS NULL` and
  an empty `season_phases`, so they remain excluded by both terms.

### 11.4 Ownership and tests

`matches/league_screens/team_history.py` moves into the **Code agent's** lane
(added to §8). The Docs agent must mention the fix in the `matches/CLAUDE.md`
CONF-02 subsection.

Tests for this amendment go in the Tests agent's new
`matches/tests/test_regional_playoffs.py` (NOT in the existing Team-History test
file, keeping the ownership split in §8 intact), as a dedicated class. Required
coverage:

- A team seeded into a regional Tournament has `playoff_appearances == 1`.
- A team in BOTH a Season-wide embedded playoff (a different, single-Conference
  Season) and a regional one counts **2** — proving the two terms OR rather
  than shadow each other.
- A team in a standalone **sandbox** Tournament still counts **0** — the
  discriminator still discriminates.
- A completed regional playoff Round the team physically played is included in
  the Overall-tab round corpus (W-L-T reflects it), and `.distinct()` keeps it
  counted exactly ONCE (assert the total round count, not just membership).
- `championships` stays **0** for a team that won its regional playoff in a
  multi-Conference Season.

These are DB tests built with the same fixtures as the rest of the file, and the
same no-simulation rule applies (§9.1) — drain brackets by driving the engine or
stamping node winners, never by running the simulator.
