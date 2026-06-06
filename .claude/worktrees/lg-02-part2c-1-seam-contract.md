# LG-02-Part2c-1 — RR → single-elimination playoff embed (seam contract)

First slice of **LG-02-Part2c**. A Season composed of an ordered `round_robin`
phase then a `tournament` phase: play the regular season, **auto-build** a
standings-seeded single-elimination playoff bracket the moment the RR phase
completes (matchups visible **before** any playoff click), then drain the bracket
to crown the **Season champion**.

This is a thin orchestration slice. It writes a **phase cursor** + two **lifecycle
hooks** onto `Season`, an **auto-build** that wires an existing standalone
`Tournament` (consumed VERBATIM) into a `SeasonPhase`, two new play views + a
Celery task that drain the already-shipped tournament engine, dashboard + template
wiring to surface the playoff button group, and one compose-time guard. **No
`Match.season_phase` FK, no Match migration, no simulator change, no tournament
engine change.**

Real-code anchors verified before writing this contract:
- `matches/models.py` — `Season` (`scheduled_fixtures`, `_is_finished`,
  `complete_if_finished`, `_stamp_champion`, `ordered_phases`, `_implicit_phase`,
  `starting_team_ids_json`, `state`); `SeasonPhase` (Part2a/2b fields incl.
  `schedule_format` / `tournament` FK, `Meta.ordering=["ordinal"]`,
  `uniq_season_phase_ordinal`); `Tournament` (`lock_and_build`, `state`, `champion`,
  `format`, `team_assembly`, `find_next_playable_node`); `TournamentParticipant`
  (`tournament` / `team` / `seed`, `uniq_tournament_seed` / `uniq_tournament_team`).
- `matches/standings.py` — `compute_standings(completed_matches, enrolled_teams,
  season_rounds=None)`, the 9-key match dict, the `(id, name)` enrolled tuple,
  `StandingsRow.team_id` / `.rank`. The existing `Season._stamp_champion` assembles
  the match dicts itself and calls `compute_standings(completed_matches,
  enrolled_teams)` (no `season_rounds`).
- `matches/tournament_engine.py` — `play_next_node(tournament) -> BracketNode |
  None` (`@transaction.atomic`, per-Match-atomic; non-deterministic sims).
- `matches/tasks.py` — `play_season_task` / `play_tournament_task` (the deferred-
  import + `update_state` PROGRESS + `finally: django.db.close_old_connections()`
  pattern; `@shared_task(bind=True, name=...)`).
- `matches/bracket.py` — `stage_progress(nodes) -> (completed, total)`,
  `default_seed_order(team_ratings)`. **NOTE: this slice seeds by STANDINGS RANK,
  not ratings — `default_seed_order` is NOT used.** `lock_and_build` reads
  `TournamentParticipant.seed` (confirmed via the single-elim branch building
  `ParticipantSpec(team_id=p.team_id, seed=p.seed)`).
- `matches/league_views.py` — `start_season` / `play_week` / `play_two_months` /
  `play_until_end` / `play_status`, `_build_play_status_response` /
  `_celery_state_to_job_status` (API-03), `_build_dashboard_context` (the 11-key
  body context + `action_button_state` / `action_button_label`),
  `league_dashboard` / `season_dashboard` (+ `_pick_displayed_season`).
- `matches/season_urls.py` — route order + `HttpResponseNotAllowed` idiom.
- `matches/simulation/entrypoints.py` — `simulate_scheduled_round` calls
  `season.complete_if_finished()` after each Round persists (the spot the build hook
  is added alongside).
- `matches/phase_composer.py` — `parse_phase_composition(raw, *,
  season_schedule_format)`, `PhaseSpec`, the 3 existing `ValueError` strings +
  validation order.
- `templates/seasons/dashboard.html` + `templates/leagues/dashboard.html` — the
  action-button slot + LG-01d play-dropdown DOM ids.

---

## 0. Locked decisions (carried verbatim from the grill — do not relitigate)

1. **PHASE CURSOR, not flat fixtures.** `Season.scheduled_fixtures()` stays
   RR-scoped (UNCHANGED — Part2a chokepoint). New `Season.current_phase() ->
   SeasonPhase | None` = first INCOMPLETE phase by ordinal (`None` when all
   complete).
2. **DERIVE completion, NO `SeasonPhase.state` field.** RR phase complete ⇔ all of
   `Season.scheduled_fixtures()` played (Side-agnostic
   `(frozenset({team_red, team_blue}), round_number)` key vs persisted GameRounds).
   Tournament phase complete ⇔ `phase.tournament_id` is set AND
   `phase.tournament.state == "completed"`.
3. **Tournament Matches stay `season=NULL`.** The tournament engine
   (`play_next_node`, `simulate_match(match_type="tournament")`) is consumed
   VERBATIM. **NO `Match.season_phase` FK, NO Match migration this slice.**
4. **AUTO-BUILD on RR completion** via `Season.activate_pending_tournament_phase()`
   (`@transaction.atomic`, idempotent).
5. **`Season.complete_if_finished()` REWRITTEN** to flip `state="completed"` +
   stamp champion ONLY when the FINAL phase (last ordinal) is complete. A
   single-RR-phase season (and the implicit phase-less fallback) stays
   BYTE-IDENTICAL to today.
6. **POST-ROUND HOOK split into two methods** (build hook then complete hook), both
   called by `simulate_scheduled_round` — NOT one merged advance method.
7. **PLAY ACTIONS.** RR-scoped play (`play_week` / `play_two_months` /
   `play_until_end`) UNCHANGED behaviour; only the terminal LABEL changes,
   conditional on a following tournament phase. NEW "Play Single Round" (sync) and
   "Play Playoffs" (async).
8. **COMPOSE-TIME GUARD** in `parse_phase_composition`: a `tournament` phase
   requires a preceding `round_robin` phase.
9. **PLAYOFF UI** links to the existing `/tournaments/<id>/` page (do NOT embed the
   bracket).
10. **POLLING REUSE** of the LG-01d `play_status` view + `_build_play_status_response`.
11. **NO Score Calibration re-baseline.** Extend ADR-0023 (no new ADR).

---

## 1. `Season` cursor + lifecycle methods (`matches/models.py`)

All new methods live on `Season`, declared after the existing
`scheduled_fixtures` / `_is_finished` / `_stamp_champion` block.

### 1.1 `Season.current_phase(self) -> "SeasonPhase | None"`

Pure read-derivation, no DB write, no RNG. Returns the **first INCOMPLETE phase by
ordinal**, or `None` when every phase is complete.

```
def current_phase(self) -> "SeasonPhase | None":
    for phase in self.ordered_phases():        # ordinal order (Part2a)
        if not self._phase_complete(phase):
            return phase
    return None
```

- `ordered_phases()` is the Part2a chokepoint (returns the persisted phases, or a
  one-element list with the unsaved implicit `round_robin` fallback `pk is None`).
- A phase-less / single-RR-phase Season returns its (implicit or explicit)
  `round_robin` phase while the RR is unfinished, then `None` once finished —
  exactly mirroring today's "Season is done when all fixtures played".

### 1.2 `Season._phase_complete(self, phase: "SeasonPhase") -> bool`

**Private phase-completion helper — the single derivation site for decision #2.**
Lives on `Season` (NOT `SeasonPhase`) so it can reach `scheduled_fixtures()` /
`_is_finished()` without a back-reference dance.

```
def _phase_complete(self, phase: "SeasonPhase") -> bool:
    if phase.phase_type == "round_robin":
        return self._is_finished()           # existing RR all-fixtures-played check
    if phase.phase_type == "tournament":
        return (
            phase.tournament_id is not None
            and phase.tournament.state == "completed"
        )
    # member_night and any future type are inert this slice ⇒ never block / never
    # complete the cursor here. Treat as complete=False (cursor parks on it) — but
    # the compose guard (§5) forbids composing one this slice, so it is unreachable.
    return False
```

- RR completion REUSES `_is_finished()` verbatim (which itself routes through
  `scheduled_fixtures()` — Part2a). For the one-RR-phase case this is byte-identical
  to today.
- Tournament completion is the decision-#2 rule: built (`tournament_id is not None`)
  AND `tournament.state == "completed"`.
- **NOTE on multi-RR:** this slice composes exactly one RR phase, so `_is_finished()`
  (which covers the whole RR fixture list) is the correct per-phase RR completion.
  Per-phase RR fixture scoping for multi-RR is DEFERRED (see §7).

### 1.3 `Season.activate_pending_tournament_phase(self) -> None`

**The auto-build (decision #4).** `@transaction.atomic`, **idempotent**.

```
@transaction.atomic
def activate_pending_tournament_phase(self) -> None:
    phase = self.current_phase()
    if phase is None:
        return
    if phase.phase_type != "tournament":
        return                                  # cursor isn't on a tournament phase
    if phase.tournament_id is not None:
        return                                  # already built (idempotent no-op)
    if phase.pk is None:
        return                                  # implicit fallback is never a tournament
    prior = self._preceding_phase(phase)
    if prior is None or not self._phase_complete(prior):
        return                                  # RR not yet done ⇒ don't build
    # --- build ---
    rows = self._final_standings_for_phase(prior)   # §1.5 — reuse compute_standings
    if not rows:
        return                                  # defensive: nothing to seed
    tournament = Tournament.objects.create(
        name=f"{self.name} Playoffs",
        format="single_elimination",
        team_assembly="preset",
        state="setup",
    )
    for row in rows:
        TournamentParticipant.objects.create(
            tournament=tournament,
            team_id=row.team_id,
            seed=row.rank,                      # rank 1 -> seed 1 (LOCKED mapping)
        )
    phase.tournament = tournament
    phase.save(update_fields=["tournament"])
    tournament.lock_and_build()                 # setup -> active; builds the bracket
```

Locked details:
- **No-op when** `current_phase()` is `None`, isn't a `tournament` phase, the phase
  is already built (`tournament_id is not None`), the phase is the implicit fallback
  (`pk is None`), or the preceding RR phase isn't complete. (Idempotent: a second
  call after a successful build hits the `tournament_id is not None` guard.)
- **`Tournament` creation:** `format="single_elimination"`, `team_assembly="preset"`,
  `state="setup"`, `name=f"{self.name} Playoffs"`.
- **One `TournamentParticipant` per season team** drawn from the final standings of
  the preceding phase, **`seed = StandingsRow.rank`** (rank 1 → seed 1). Because
  `compute_standings` returns one row per enrolled team (zero-filled), every season
  team gets a participant. Ranks are 1-based dense, so seeds are `1..N` unique →
  satisfies `uniq_tournament_seed`.
- After wiring `phase.tournament`, call `tournament.lock_and_build()` (the existing
  setup→active transition; validates `>= 4` participants, builds + persists the
  `BracketNode` tree from the seeds). **The bracket is visible immediately** — matchups
  exist before any playoff click.
- `Tournament` / `TournamentParticipant` are imported at module scope already
  (same file). `Tournament.lock_and_build` raises `ValidationError` on `< 4`
  participants — a Season with `< 4` teams cannot reach a playoff (acceptable;
  see §8 test boundary — this is a degenerate config, not asserted as a happy path).

### 1.4 `Season._preceding_phase(self, phase: "SeasonPhase") -> "SeasonPhase | None"`

Private. Returns the phase one ordinal lower than `phase` (the RR phase feeding the
playoff), or `None` if `phase` is the first.

```
def _preceding_phase(self, phase: "SeasonPhase") -> "SeasonPhase | None":
    prior = None
    for candidate in self.ordered_phases():
        if candidate.ordinal == phase.ordinal:
            return prior
        prior = candidate
    return None
```

### 1.5 `Season._final_standings_for_phase(self, phase) -> "list[StandingsRow]"`

**The build/seed helper — assembles the exact `compute_standings` inputs the
existing `_stamp_champion` uses, so Code and Tests agree on keys.** Reuses
`matches.standings.compute_standings`.

```
def _final_standings_for_phase(self, phase) -> "list[StandingsRow]":
    from .standings import compute_standings

    team_ids = self.starting_team_ids_json or []
    matches_qs = Match.objects.filter(season=self, is_completed=True)
    completed_matches: list[dict] = []
    for match in matches_qs:
        completed_matches.append(
            {
                "match_id": match.id,
                "team_red_id": match.team_red_id,
                "team_blue_id": match.team_blue_id,
                "winner_team_id": match.winner_id,
                "red_rounds_won": match.red_rounds_won,
                "blue_rounds_won": match.blue_rounds_won,
                "red_total_points": match.red_total_points,
                "blue_total_points": match.blue_total_points,
            }
        )
    enrolled_teams = list(
        Team.objects.filter(id__in=team_ids).values_list("id", "name")
    )
    return compute_standings(completed_matches, enrolled_teams)
```

**Locked input shape (must match `_stamp_champion` byte-for-byte):**
- `completed_matches` — list of 8-key dicts (the keys above; **NO `date_played`** —
  `_stamp_champion` omits it and `compute_standings` reads it via `.get(...,0)`).
- `enrolled_teams` — list of `(id, name)` tuples from
  `Team.objects.filter(id__in=team_ids).values_list("id", "name")`, where
  `team_ids = self.starting_team_ids_json or []`.
- `season_rounds` — **not passed** (defaults to `None` ⇒ `[]`). The playoff seeding
  only needs the Match-grain rank columns; the Round-grain side-split columns are
  irrelevant to seeding.
- This is identical to the `_stamp_champion` assembly, so the **rank ordering used
  to seed the playoff is the same ordering that crowns a single-RR-phase champion**.

> This slice scopes `phase` so that `_final_standings_for_phase` is always called
> with the season's single RR phase; the `phase` argument is carried for forward
> compatibility (per-phase Match scoping is DEFERRED — §7) but is not used to filter
> the Match query this slice (all season Matches are RR Matches —
> tournament Matches stay `season=NULL`, decision #3, so they never pollute this
> query).

### 1.6 `Season.complete_if_finished` — REWRITTEN (decision #5)

The existing method (no-op on non-`active`, else `_is_finished()` → `_stamp_champion()`)
is rewritten to gate on the **FINAL phase** (last ordinal) being complete and to
stamp the champion from the final phase's type:

```
@transaction.atomic
def complete_if_finished(self) -> None:
    if self.state != "active":
        return
    phases = self.ordered_phases()
    final_phase = phases[-1]
    if not self._phase_complete(final_phase):
        return
    self._stamp_champion_for_final_phase(final_phase)
```

- `_is_finished()` is **UNCHANGED** (still the RR all-fixtures-played check, reused
  by `_phase_complete` for RR phases).
- **BYTE-IDENTICAL fallback:** for a single-RR-phase Season (explicit or implicit
  `pk is None`), `final_phase` is that RR phase, `_phase_complete(final_phase) ==
  _is_finished()`, and the champion is stamped from `compute_standings(...)[0]` — the
  same state flip + same `champion_team` id as today.

### 1.7 `Season._stamp_champion_for_final_phase(self, final_phase) -> None`

Replaces the role of the old `_stamp_champion`. The old `_stamp_champion` is
**superseded** — its standings-rank-1 logic moves here behind the RR branch; the
tournament branch reads `phase.tournament.champion`.

```
def _stamp_champion_for_final_phase(self, final_phase) -> None:
    if final_phase.phase_type == "tournament":
        champion = final_phase.tournament.champion   # may be None defensively
        if champion is None:
            return
        self.state = "completed"
        self.champion_team = champion
        self.save()
        return
    # round_robin (or fallback) final phase — same logic as the old _stamp_champion
    rows = self._final_standings_for_phase(final_phase)
    if not rows:
        return
    self.state = "completed"
    self.champion_team = Team.objects.get(pk=rows[0].team_id)
    self.save()
```

- Champion = `phase.tournament.champion` when the final phase is a tournament, else
  `compute_standings(...)[0]` of the final RR phase.
- `phase.tournament.champion` is guaranteed non-`None` when
  `tournament.state == "completed"` (the engine stamps both together), so the
  defensive `None` guard never blocks in practice — but it must exist so a corrupt
  state never raises.
- **The old `Season._stamp_champion` method is removed** (its body is absorbed into
  the RR branch above). Any internal caller (only `complete_if_finished`) is
  rewired.

---

## 2. Post-round hook wiring (`matches/simulation/entrypoints.py`)

`BatchSimulator.simulate_scheduled_round` already calls `season.complete_if_finished()`
after persistence (twice — once in the Round-1 branch, once in the Round-2 branch).
**Each of those two sites gains a `season.activate_pending_tournament_phase()` call
IMMEDIATELY BEFORE the existing `season.complete_if_finished()` call** (decision #6 —
build hook fires first, then completion check).

Round-1 branch (currently lines ~820–822):
```
    self._persist_round_results(match, game_round, round_number=1, swapped=False)
    match.save()
    season.activate_pending_tournament_phase()   # NEW — build hook
    season.complete_if_finished()                # existing — complete hook
    return game_round
```

Round-2 branch (currently lines ~845–848):
```
    self._persist_round_results(match, game_round, round_number=2, swapped=True)
    match.is_completed = True
    match.save()
    season.activate_pending_tournament_phase()   # NEW — build hook
    season.complete_if_finished()                # existing — complete hook
    return game_round
```

- **Ordering is load-bearing:** the build hook runs first so that the moment the last
  RR fixture lands, the tournament phase is built and becomes `current_phase()`; the
  completion check that follows then sees the final phase is the (now-incomplete)
  tournament phase and does NOT prematurely complete the Season.
- Both new calls are idempotent / no-op except at the exact RR-completion boundary,
  so adding them to every persisted Round is safe and cheap.
- **No simulator mechanics change, no RNG.** `simulate_scheduled_round` signature
  unchanged.

---

## 3. Play views (`matches/league_views.py`)

### 3.1 `play_single_round(request, season_id: int) -> HttpResponse` — sync, NEW

```
def play_single_round(request, season_id: int) -> HttpResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id
    phase = season.current_phase()
    if phase is None or phase.phase_type != "tournament" or phase.tournament_id is None:
        return _render_season_dashboard_error(
            request, season, "No active playoff bracket to play."
        )
    from matches.tournament_engine import play_next_node
    play_next_node(phase.tournament)            # ONE playoff Match (per-Match atomic)
    season.complete_if_finished()               # crown the Season if the final landed
    return redirect("season_dashboard", season_id=season.id)
```

- POST-only; `HttpResponseNotAllowed(["POST"])` first line (LG-01d idiom). 405 on GET.
- Guards: `current_phase()` must be a built (`tournament_id is not None`) tournament
  phase. Otherwise 400-equivalent dashboard re-render with `play_error` (reuses
  `_render_season_dashboard_error`).
- Plays **exactly one** playoff Match via `play_next_node` (deferred import).
- After: `season.complete_if_finished()` (the rewritten one — crowns the Season when
  the final bracket node resolves).
- **302 redirect** to `season_dashboard` on success.
- Writes `last_league_id` after the 404 guard (LG-01f session-write precedent).

### 3.2 `play_playoffs(request, season_id: int) -> JsonResponse` — async, NEW

```
def play_playoffs(request, season_id: int) -> JsonResponse:
    if request.method != "POST":
        return HttpResponseNotAllowed(["POST"])
    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id
    phase = season.current_phase()
    if phase is None or phase.phase_type != "tournament" or phase.tournament_id is None:
        return JsonResponse({"error": "No active playoff bracket to play."}, status=409)
    result = play_playoffs_task.delay(season.id)
    return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)
```

- POST-only; 405 on other methods.
- Guard mismatch ⇒ **409** JSON `{"error": ...}` (mirrors `tournament_play_all`'s
  inactive-state 409 precedent — async endpoints return JSON, not a dashboard
  re-render).
- Happy path ⇒ `play_playoffs_task.delay(season_id)` → **202** JSON `{job_id,
  season_id}` (mirrors `play_two_months` / `play_until_end` exactly).

### 3.3 Polling — REUSE the LG-01d `play_status` view (decision #10)

`play_status` + `_build_play_status_response` are reused **verbatim** for the playoff
job (same URL name, same 5-key JSON `{status, completed, total, error, season_id}`).
The playoff task returns `{"completed", "total"}` **stage counts** (via
`stage_progress`), which `_build_play_status_response` already reads from
`async_result.info["completed"]/["total"]` on PROGRESS and
`async_result.result["completed"]/["total"]` on SUCCESS. **No change to `play_status`
or `_build_play_status_response`.** The inline poll JS hits the same `play_status`
URL with the job id returned by `play_playoffs`.

---

## 4. Celery task (`matches/tasks.py`)

### `play_playoffs_task` — NEW

```
@shared_task(bind=True, name="matches.play_playoffs")
def play_playoffs_task(self, season_id: int) -> dict:
    import django.db
    try:
        from matches.bracket import stage_progress
        from matches.models import Season, _node_to_dict
        from matches.tournament_engine import play_next_node

        season = Season.objects.get(id=season_id)
        phase = season.current_phase()
        if phase is None or phase.phase_type != "tournament" or phase.tournament_id is None:
            return {"completed": 0, "total": 0}
        tournament = phase.tournament

        def _stage_counts() -> tuple[int, int]:
            flat = [
                _node_to_dict(n)
                for n in tournament.nodes.select_related(
                    "advances_to", "tournament"
                ).prefetch_related("series_matches")
            ]
            return stage_progress(flat)

        while play_next_node(tournament) is not None:
            completed, total = _stage_counts()
            self.update_state(
                state="PROGRESS",
                meta={"completed": completed, "total": total},
            )

        season.complete_if_finished()           # crown the Season once drained
        completed, total = _stage_counts()
        return {"completed": completed, "total": total}
    finally:
        django.db.close_old_connections()
```

- **Decorator + name LOCKED:** `@shared_task(bind=True, name="matches.play_playoffs")`.
- **Return shape LOCKED:** `{"completed": int, "total": int}` — STAGE counts from
  `stage_progress` (reused VERBATIM), NOT node counts. Matches the
  `play_tournament_task` shape exactly so `_build_play_status_response` reads it
  unchanged.
- **Body pattern mirrors `play_tournament_task`:** deferred imports, `while
  play_next_node(...) is not None` drains the bracket one Match at a time, emits
  stage-progress `update_state` after each, `finally: close_old_connections()`.
- **NO outer `@transaction.atomic`** — `play_next_node` is already
  `@transaction.atomic` per Match (ADR-0016 precedent); a mid-drain failure leaves
  every resolved node committed and is resumable.
- After draining, `season.complete_if_finished()` crowns the Season champion (the
  final phase — the tournament — is now complete; `tournament.champion` is set).
- Inactive / unbuilt guard returns `{"completed": 0, "total": 0}` (no-op) — mirrors
  `play_tournament_task`'s inactive-state early return.

---

## 5. Compose-time guard (`matches/phase_composer.py`) — decision #8

`parse_phase_composition(raw, *, season_schedule_format)` gains ONE new rule: a
`tournament` phase requires a **preceding** `round_robin` phase. New `ValueError`
string (LOCKED, byte-equal):

```
"a tournament phase requires a preceding round-robin phase"
```

**Where it fires in the validation order.** The existing order is: (1) empty `raw`
short-circuit; (2) per-token malformed (`"malformed phase composition"`); (3) per-token
unknown type (`f"unknown phase type: {token!r}"`); (4) after building specs, zero-RR
check (`"composition must contain at least one round-robin phase"`). The new guard
fires **after the zero-RR check** (step 5) — once all tokens are known-valid and at
least one RR exists, walk the specs in order and raise if any `tournament` spec is
seen before the first `round_robin` spec:

```
    # ... existing per-token loop builds `specs` ...
    if not any(spec.phase_type == "round_robin" for spec in specs):
        raise ValueError("composition must contain at least one round-robin phase")

    # NEW (Part2c-1): a tournament phase must follow a round-robin phase.
    seen_round_robin = False
    for spec in specs:
        if spec.phase_type == "round_robin":
            seen_round_robin = True
        elif spec.phase_type == "tournament" and not seen_round_robin:
            raise ValueError(
                "a tournament phase requires a preceding round-robin phase"
            )

    return specs
```

- Pure `ValueError` (module stays Django-free; the form layer re-wraps as a
  `forms.ValidationError` attached to `phases`). Frozen import allowlist unchanged
  (`dataclasses`, `typing`) — `TestNoDjangoImportsLeaked` still passes.
- `member_night` remains rejected at step 3 (unknown type) this slice.

---

## 6. Dashboard context + templates

### 6.1 `_build_dashboard_context` additions (`matches/league_views.py`)

The existing 11-key body context grows by **playoff-cursor keys**, computed from
`displayed_season.current_phase()`. The new keys (added to the dict for **both** the
League and Season dashboards, since both render `_build_dashboard_context`):

| key | type | value |
|---|---|---|
| `playoff_phase_active` | `bool` | `True` iff `current_phase()` is a tournament phase that is **built + active** (`tournament_id is not None` AND `tournament.state == "active"`) |
| `playoff_tournament_id` | `int \| None` | `phase.tournament_id` when a tournament phase is built (active **or** completed), else `None` |
| `playoff_completed` | `bool` | `True` iff a tournament phase exists, is built, and `tournament.state == "completed"` |
| `has_following_tournament_phase` | `bool` | `True` iff the season's phase list contains a `tournament` phase at an ordinal **after** the current RR phase (drives the terminal-label rule, §6.3) |

**Per cursor sub-state:**
- **RR-active** (`current_phase()` is the RR phase): `playoff_phase_active=False`,
  `playoff_tournament_id=None`, `playoff_completed=False`,
  `has_following_tournament_phase=True` iff a tournament phase follows.
- **tournament-active-built** (`current_phase()` is the tournament phase,
  `state="active"`): `playoff_phase_active=True`,
  `playoff_tournament_id=<id>`, `playoff_completed=False`.
- **tournament-completed** (`current_phase()` is `None` and the final phase is a
  completed tournament): `playoff_phase_active=False`,
  `playoff_tournament_id=<id>`, `playoff_completed=True`. (Computed by inspecting
  the final phase when `current_phase()` is `None`.)

The existing `action_button_state` / `action_button_label` keys are UNCHANGED in
name. The "Play Next" / "Start Next Season" placeholder logic is unchanged; the new
playoff buttons are a SEPARATE group rendered from the new keys (they do not replace
the action-button slot).

### 6.2 New DOM ids — BOTH season and league dashboards

`templates/seasons/dashboard.html` and `templates/leagues/dashboard.html` each gain a
playoff button group rendered when `playoff_phase_active` (the two play buttons) and a
"View bracket" link rendered when `playoff_tournament_id is not None`. The ids stack
**underneath** the LG-01c/d action-button + play-dropdown slot (they do not collide).

| Surface | Element | Season DOM id | League DOM id |
|---|---|---|---|
| Play Single Round form | `<form method="post">` | `season-dashboard-play-single-round-form` | `league-dashboard-play-single-round-form` |
| Play Single Round submit | submit `<button>` | `season-dashboard-play-single-round-submit` | `league-dashboard-play-single-round-submit` |
| Play Playoffs form | `<form method="post">` | `season-dashboard-play-playoffs-form` | `league-dashboard-play-playoffs-form` |
| Play Playoffs submit | submit `<button>` | `season-dashboard-play-playoffs-submit` | `league-dashboard-play-playoffs-submit` |
| Play Playoffs progress | progress `<div>` (hidden by default) | `season-dashboard-play-playoffs-progress` | `league-dashboard-play-playoffs-progress` |
| View bracket link | `<a href="/tournaments/<id>/">` | `season-dashboard-view-bracket-link` | `league-dashboard-view-bracket-link` |

- Both playoff buttons (`-play-single-round-*` / `-play-playoffs-*`) render **only
  when `playoff_phase_active`** (decision #7 — built + active tournament phase).
- The "View bracket" link renders **whenever `playoff_tournament_id is not None`**
  (built tournament phase, active OR completed — decision #9), `href` =
  `{% url 'tournament_detail' playoff_tournament_id %}` (the existing standalone
  bracket page; do NOT embed). The reverse name is the LG-02a `tournament_detail`.
- Forms POST to the new URL names (§7 routes): single-round →
  `{% url 'play_single_round' season.id %}` (or `displayed_season.id` on the league
  dashboard), playoffs → `{% url 'play_playoffs' season.id %}`.

### 6.3 Conditional terminal-label rule (decision #7)

The LG-01d terminal play-dropdown button labeled **"Until End of Season"** is
relabeled to **"Until Playoffs"** **iff `has_following_tournament_phase` is true**.
The form action / behaviour (`play_until_end`) is UNCHANGED — only the visible label
text swaps. When no tournament phase follows (single-RR-phase season), the label
stays "Until End of Season". The button DOM id (`{season,league}-dashboard-play-until-end`)
is UNCHANGED.

### 6.4 Inline poll JS

The Play Playoffs form intercepts submit, fetch-POSTs, reads the 202 `{job_id,
season_id}`, then polls `play_status` (the reused LG-01d endpoint) on a 1000 ms
interval, updating `-play-playoffs-progress` from `{completed, total}` and reloading
on `status === "complete"`. This mirrors the LG-01d `play_two_months` / `play_until_end`
inline JS verbatim (the JS is duplicated per template, no shared partial — LG-01d
precedent). The Play Single Round form submits synchronously (server-side 302 redirect).

---

## 7. URL routes (`matches/season_urls.py`)

Two new path entries inserted **BEFORE** the LG-01 `<int:season_id>/standings/` and
`<int:season_id>/schedule/` entries (first-match resolution, alongside the LG-01d
play routes). Final order:

```
[
  <int:season_id>/                              (LG-01c season_dashboard)
  <int:season_id>/start-season/                 (LG-01d)
  <int:season_id>/play-week/                     (LG-01d)
  <int:season_id>/play-two-months/               (LG-01d)
  <int:season_id>/play-until-end/                (LG-01d)
  <int:season_id>/play-single-round/   <-- NEW   play_single_round
  <int:season_id>/play-playoffs/       <-- NEW   play_playoffs
  <int:season_id>/play-status/<str:job_id>/      (LG-01d — reused for playoff job)
  <int:season_id>/standings/                     (LG-01)
  <int:season_id>/schedule/                      (LG-01)
]
```

```
path("<int:season_id>/play-single-round/", league_views.play_single_round,
     name="play_single_round"),
path("<int:season_id>/play-playoffs/", league_views.play_playoffs,
     name="play_playoffs"),
```

- Bare URL names (no `app_name`): `play_single_round`, `play_playoffs`.
- `play_status` is **reused** for the playoff job — no new status route.

---

## 8. Test boundary

**What Tests assert against (the public seam):**

*Pure (`matches/tests/test_phase_composer.py`, extend):*
- A `tournament`-before-`round_robin` composition (e.g. `"tournament,round_robin"`)
  raises `ValueError("a tournament phase requires a preceding round-robin phase")`.
- A `"round_robin,tournament"` composition parses to 2 ordered specs (no raise).
- The zero-RR string still fires before the new guard; the new guard fires only when
  ≥ 1 RR exists. Purity (`TestNoDjangoImportsLeaked`) still passes.

*DB (`matches/tests/test_season_phase.py` / a new `test_season_playoff.py`):*
- **Cursor/completion derivation:** `current_phase()` returns the RR phase while
  fixtures are unplayed, the tournament phase once RR completes + is built, and
  `None` once the tournament completes. `_phase_complete` returns the correct bool per
  phase type (RR via `_is_finished`, tournament via `tournament_id is not None AND
  state=="completed"`).
- **Auto-build on RR completion seeds by standings rank:** after the last RR fixture
  lands, `activate_pending_tournament_phase()` creates a `Tournament(format=
  "single_elimination", team_assembly="preset")`, one `TournamentParticipant` per
  season team with `seed == StandingsRow.rank` (rank 1 → seed 1), wires
  `phase.tournament`, and locks+builds (bracket nodes exist). Idempotent: a second
  call is a no-op.
- **`complete_if_finished` champion = tournament champion:** for a built+drained
  tournament final phase, the Season flips `state="completed"` and
  `champion_team == phase.tournament.champion`. For a single-RR-phase season the
  champion is `compute_standings(...)[0]` and the behaviour is byte-identical to
  today (regression).
- **`play_playoffs_task` drains to champion** under `CELERY_TASK_ALWAYS_EAGER`:
  draining via `play_next_node` resolves every bracket node, crowns
  `tournament.champion`, and `season.complete_if_finished()` stamps the Season
  champion. Assert on `state` / `champion_team` id / bracket-node winners — **NEVER
  on exact simulated point totals** (tournament sims are non-deterministic).
- **The two views' status codes:** `play_single_round` → 302 on a built+active
  playoff, 400-equivalent dashboard re-render (`play_error`) when no built playoff,
  405 on GET. `play_playoffs` → 202 `{job_id, season_id}` on a built+active playoff,
  409 `{"error"}` when no built playoff, 405 on GET.
- **Dashboard context keys** per cursor sub-state (RR-active / tournament-active-built
  / tournament-completed) carry the §6.1 values; the playoff DOM ids render only in
  the active sub-state; the "View bracket" link renders for built (active or
  completed); the terminal label reads "Until Playoffs" iff a tournament phase
  follows.

**What is internal (NOT asserted directly):** `_preceding_phase`,
`_final_standings_for_phase` assembly internals (asserted via the seeds it produces),
`_stamp_champion_for_final_phase` (asserted via `complete_if_finished` outcomes),
`_phase_complete`'s `member_night` branch (unreachable this slice).

**Determinism:** RR sims unchanged (byte-identical); tournament sims
non-deterministic (`simulate_match` draws fresh per-round seeds). **No SIM-07 / SIM-08
interaction, NO Score Calibration re-baseline.** Extend ADR-0023 (no new ADR).

---

## 9. SCOPE-OUT (DEFERRED — do NOT build here)

- **Multi-RR play loop** + `Match.season_phase` FK + cross-phase matchday
  offsetting (Tournament Matches stay `season=NULL` this slice; RR Match scoping is
  whole-season).
- **Per-phase `schedule_format` wiring** (the RR phase still reads
  `Season.schedule_format`; `SeasonPhase.schedule_format` stays dormant).
- **Per-phase seeding-mode field** + mid-season tournaments (this slice is always
  standings-rank-seeded, season-ending).
- **Per-tournament-block config** (format / top-N cut) — this slice is always
  full-field single-elimination.
- **Non-single-elim embeds** (double-elim / RR / Swiss / RR→DE as a Season finals
  stage).
- **Season-linked playoff Match history** (playoff Matches stay `season=NULL`; no
  Season game-log surface for them).
- **Weekly playoff pacing** (the playoff plays one Match per "Play Single Round"
  click or the whole bracket per "Play Playoffs"; no per-week tournament cadence).

---

## 10. Locked names (quick index)

**`Season` methods (`matches/models.py`):**
`current_phase(self) -> SeasonPhase | None`;
`_phase_complete(self, phase) -> bool`;
`_preceding_phase(self, phase) -> SeasonPhase | None`;
`activate_pending_tournament_phase(self) -> None` (`@transaction.atomic`, idempotent);
`_final_standings_for_phase(self, phase) -> list[StandingsRow]`;
`complete_if_finished(self) -> None` (REWRITTEN, `@transaction.atomic`);
`_stamp_champion_for_final_phase(self, final_phase) -> None` (replaces the removed
`_stamp_champion`).

**Seed mapping:** `TournamentParticipant.seed = StandingsRow.rank` (rank 1 → seed 1).
**Tournament create:** `format="single_elimination"`, `team_assembly="preset"`,
`state="setup"`, `name=f"{season.name} Playoffs"`; then `tournament.lock_and_build()`.

**`compute_standings` build inputs (8-key match dict — no `date_played`):**
`match_id, team_red_id, team_blue_id, winner_team_id, red_rounds_won, blue_rounds_won,
red_total_points, blue_total_points`; `enrolled_teams = list(Team.objects.filter(
id__in=starting_team_ids_json or []).values_list("id","name"))`; `season_rounds`
omitted (defaults `None`).

**Phase-completion rule:** RR ⇔ `_is_finished()`; tournament ⇔
`phase.tournament_id is not None AND phase.tournament.state == "completed"` — lives in
`Season._phase_complete`.

**Hook wiring (`matches/simulation/entrypoints.py`):** `simulate_scheduled_round`
calls `season.activate_pending_tournament_phase()` then `season.complete_if_finished()`
after persistence in BOTH the Round-1 and Round-2 branches.

**Celery task (`matches/tasks.py`):** `play_playoffs_task`,
`@shared_task(bind=True, name="matches.play_playoffs")`, signature
`(self, season_id: int) -> dict`, returns `{"completed": int, "total": int}` (stage
counts from `matches.bracket.stage_progress`).

**Views (`matches/league_views.py`):** `play_single_round(request, season_id) ->
HttpResponse` (sync POST, 302 / dashboard-error / 405);
`play_playoffs(request, season_id) -> JsonResponse` (async POST, 202 / 409 / 405).
**Reused:** `play_status` + `_build_play_status_response` + `_celery_state_to_job_status`.

**URL names + order (`matches/season_urls.py`):** `play_single_round`
(`<int:season_id>/play-single-round/`), `play_playoffs`
(`<int:season_id>/play-playoffs/`), inserted before `standings/` / `schedule/`,
after the LG-01d play routes.

**Compose guard ValueError (`matches/phase_composer.py`):**
`"a tournament phase requires a preceding round-robin phase"`, fired after the
zero-RR check, before `return specs`.

**New dashboard context keys:** `playoff_phase_active`, `playoff_tournament_id`,
`playoff_completed`, `has_following_tournament_phase`.

**New DOM ids (season / league):**
`{season,league}-dashboard-play-single-round-form` / `-submit`;
`{season,league}-dashboard-play-playoffs-form` / `-submit` / `-progress`;
`{season,league}-dashboard-view-bracket-link`. Terminal label "Until Playoffs" iff
`has_following_tournament_phase`. View-bracket link → `{% url 'tournament_detail'
playoff_tournament_id %}`.

**ADR:** extend ADR-0023 (no new ADR). **No re-baseline, no `Match.season_phase` FK,
no Match migration, no simulator/engine change.**
