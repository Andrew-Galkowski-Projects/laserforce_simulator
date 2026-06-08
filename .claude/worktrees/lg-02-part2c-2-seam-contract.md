# LG-02-Part2c-2 — multi-round-robin season (Match.season_phase + multi-RR play loop) (seam contract)

The next slice of **LG-02-Part2c**. It generalises the SHIPPED Part2c-1
single-RR-then-single-elim playoff into a **multi-round-robin** season by adding
a `Match.season_phase` FK, a per-phase find-or-create key, per-phase RR
completion, a **multi-RR play loop**, and **cross-phase global-continuous
matchday offsetting**. The composition supported + tested is **one-or-more RR
phases then an OPTIONAL trailing tournament** (RR1→RR2, RR1→RR2→playoff). The
tournament engine (`simulate_match(match_type="tournament")` / `play_next_node`)
is CONSUMED VERBATIM. Legacy phase-less seasons stay **byte-identical**.

This is a thin orchestration slice. It writes **one Match FK + a single
`AddField` migration**, makes the `simulate_scheduled_round` find-or-create key
phase-aware (a keyword-only `season_phase=None`), makes `Season._phase_complete`
per-phase for RR, adds a NEW `Season.scheduled_fixtures_by_phase()` fixture seam
(offset applied per phase) while keeping the flat `scheduled_fixtures()` as the
concatenation, makes the `season_dashboard.py` pure helpers phase-aware via PLAIN
INT phase-ids, and wires both play-loop sites (`play_season_task`, `play_week`).
**No simulator mechanics change, no tournament-engine change, no Score
Calibration re-baseline, no SIM-07/SIM-08 interaction, no composer/form/template
change.**

Real-code anchors verified before writing this contract:
- `matches/models.py` — `Match.season` FK (`:59-65`, the SET_NULL mirror);
  `SeasonPhase` (`:1257-1311`, fields `season`/`ordinal`/`phase_type`/
  `schedule_format`/`tournament`, `Meta.ordering=["ordinal"]`,
  `uniq_season_phase_ordinal`); `Season.start_season` (`:962`),
  `complete_if_finished` (`:984`, REWRITTEN at Part2c-1), `current_phase` (`:1009`),
  `_phase_complete` (`:1023`), `_preceding_phase` (`:1047`),
  `_final_standings_for_phase` (`:1056`), `activate_pending_tournament_phase`
  (`:1093`), `_stamp_champion_for_final_phase` (`:1134`), `_is_finished` (`:1158`),
  `_implicit_phase` (`:1203`), `ordered_phases` (`:1214`), `scheduled_fixtures`
  (`:1229`).
- `matches/schedule_generator.py` — `generate_schedule(team_ids,
  schedule_format="single_round_robin")` (`:41`), frozen `ScheduleFixture(matchday,
  round_number, team_a_id, team_b_id)` (`:31-38`), the
  `dataclasses`-only frozen import allowlist (`:14-18`).
- `matches/season_dashboard.py` — `find_next_matchday(fixtures, played_keys)`
  (`:161`), `select_play_fixtures(fixtures, played_keys, max_matchdays)` (`:194`),
  `find_next_fixture` (`:132`), `round_progress` (`:246`), the frozen
  `collections`/`dataclasses`/`typing`-only import allowlist (`:23-32`).
- `matches/simulation/entrypoints.py` — `simulate_scheduled_round` (`:737`), the
  Side-agnostic find-or-create (`:787-805`), the two post-round hook sites
  (`:821-822` Round-1, `:849-850` Round-2 — `activate_pending_tournament_phase()`
  then `complete_if_finished()`).
- `matches/tasks.py` — `play_season_task` body (`:179-238`): `scheduled_fixtures()`
  (`:192`), `played_keys` build (`:193-201`), `select_play_fixtures` (`:202`),
  per-fixture `simulate_scheduled_round` loop (`:217-234`).
- `matches/league_views.py` — `play_week` (`:1578-1643`): `scheduled_fixtures()`
  (`:1611`), `played_keys` (`:1612-1620`), `select_play_fixtures(..., 1)` (`:1621`),
  the per-fixture loop (`:1629-1639`); `_build_dashboard_context` fixtures source
  (`:669`, `played_keys` `:674-686`); `season_schedule` (`:381`); `team_schedule`
  (`:1852-1880`, `:1979`).
- `matches/phase_composer.py` — `parse_phase_composition` (`:36-103`): multiple
  `round_robin` tokens ALREADY permitted (`:90`, ≥1 RR, no cap); the Part2c-1
  tournament-must-follow-RR guard (`:93-101`). **No composer change this slice.**
- Latest matches migration: `0042_seasonphase_format_tournament.py` ⇒ the next
  number is **`0043`**.
- `.claude/worktrees/lg-02-part2c-1-seam-contract.md` — the precedent format.

---

## 0. Locked decisions (carried verbatim from the grill — do not relitigate)

1. **`Match.season_phase` FK** mirrors `Match.season`'s SET_NULL:
   `models.ForeignKey("matches.SeasonPhase", null=True, blank=True,
   on_delete=models.SET_NULL, related_name="matches")`. Migration
   `0043_match_season_phase`, dep `0042_seasonphase_format_tournament`, a single
   `AddField`, **NO `RunPython` / NO backfill** (ADR-0004).
2. **RR Matches keep `season=<season>` AND gain `season_phase=<rr phase>`.**
   Tournament/playoff Matches stay `season=NULL, season_phase=NULL` (engine
   consumed VERBATIM). Legacy phase-less seasons (implicit `pk is None` phase from
   `ordered_phases()`) keep `season_phase=NULL` — byte-identical.
3. **Find-or-create key** in `simulate_scheduled_round` becomes
   `(season, season_phase, frozenset({team_a_id, team_b_id}))` so identical
   pairings in different RR phases are DISTINCT Matches (today's `(season,
   frozenset)` collides across RR phases — the load-bearing reason for the FK).
4. **Standings cumulative** across all RR phases: `_final_standings_for_phase`
   stays whole-season (`Match.objects.filter(season=self, is_completed=True)`) —
   UNCHANGED. A trailing playoff seeds from cumulative standings; an RR-final-phase
   champion = cumulative leader.
5. **Completion per-phase**: `_phase_complete(round_robin phase)` becomes
   per-phase (THIS phase's fixtures all played, scoped by `match__season_phase=phase`)
   EXCEPT the implicit `pk is None` fallback phase, which falls back to whole-season
   `_is_finished()` (byte-identical legacy path). The cursor must finish RR1 before
   RR2 opens.
6. **Matchday global-continuous**: phase k's fixtures are offset by the sum of
   prior phases' matchday counts → one monotonic 1..N calendar. Existing helpers
   (`find_next_matchday`, `select_play_fixtures`, the `date = start_date +
   (matchday-1)*7` derivation) keep working.
7. **Fixture seam**: NEW `Season.scheduled_fixtures_by_phase() ->
   list[tuple[SeasonPhase, list[ScheduleFixture]]]` (offset applied per phase).
   `Season.scheduled_fixtures()` stays the FLAT list (now the concatenation of all
   phases' offset fixtures). `ScheduleFixture` gains NO field; `generate_schedule`
   stays a pure single-RR generator.
8. **`simulate_scheduled_round`** gains a keyword-only `season_phase=None`. Default
   None = legacy/sandbox unchanged. The play loop passes each fixture's owning
   phase. The two post-round hooks
   (`activate_pending_tournament_phase()`/`complete_if_finished()`) are UNCHANGED.
9. **played_keys** discriminator gains the phase. The pure helpers
   `find_next_matchday`/`select_play_fixtures` become phase-aware via PLAIN INT
   phase-ids — they STAY Django-free; `TestNoDjangoImportsLeaked` must keep passing.
10. Play loop sites to wire: `tasks.py::play_season_task` and
    `league_views.py::play_week` (sync). Both iterate by-phase, build phase-aware
    played_keys, pass `season_phase`.
11. **Composer UNCHANGED.** `parse_phase_composition` already permits multiple
    `round_robin` tokens (≥1 RR, no cap). No composer/form/template change, no new
    compose guard. Supported+tested = one-or-more RR then OPTIONAL trailing
    tournament; mid-season tournaments stay deferred/untested.
12. **No simulator mechanics change → NO Score Calibration re-baseline. No
    SIM-07/08 interaction.** Tournament sims stay non-deterministic (assert
    schema-level outcomes, never exact point totals).
13. ADR: EXTEND `docs/adr/0023-season-phase-composable-structure.md` with a
    "Part2c-2 consequences" addendum (NO new ADR). No new CONTEXT.md domain TERM
    (`season_phase` is implementation; Matchday / Season-phase entries get
    behavioural touch-ups at code-land by the Docs agent).

---

## 1. Model + migration (`matches/models.py`)

### 1.1 `Match.season_phase` FK — NEW

Add **immediately after** the existing `Match.season` FK block (`models.py:59-65`),
before `is_completed` (`:66`). LOCKED declaration (mirrors `Match.season`):

```
# LG-02-Part2c-2: optional FK to the owning SeasonPhase. RR Matches gain it;
# tournament/playoff Matches stay season_phase=NULL (engine consumed verbatim).
# Legacy phase-less seasons keep season_phase=NULL. SET_NULL — deleting a
# SeasonPhase must NOT cascade-delete its Matches.
season_phase = models.ForeignKey(
    "matches.SeasonPhase",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="matches",
)
```

- `related_name="matches"` is the SAME label `Match.season` uses for its
  `Season.matches` reverse accessor — but the owning model differs (`Season` vs
  `SeasonPhase`), so the reverse accessors `season.matches` and `phase.matches` do
  not collide. (`SeasonPhase` has no other `matches`-named reverse accessor.)

### 1.2 Migration `0043_match_season_phase`

Single file `matches/migrations/0043_match_season_phase.py`, dependency
`("matches", "0042_seasonphase_format_tournament")`. ONE operation:
`migrations.AddField(model_name="match", name="season_phase", field=...)` with the
field above. **NO `RunPython`, NO `RunSQL`, NO backfill, NO data migration**
(ADR-0004 disposable-data precedent — same posture as `0029` / `0041` / `0042`).

---

## 2. `Season` chokepoint changes (`matches/models.py`)

### 2.1 `Season._phase_complete` — per-phase RR scoping (decision #5)

CURRENT (`models.py:1023-1045`) routes the `round_robin` branch through the
whole-season `_is_finished()`. REWRITE the `round_robin` branch to be **per-phase**
EXCEPT for the implicit fallback (`pk is None`), which keeps the whole-season
byte-identical legacy path:

```
def _phase_complete(self, phase: "SeasonPhase") -> bool:
    if phase.phase_type == "round_robin":
        if phase.pk is None:
            # implicit fallback (phase-less Season) — byte-identical legacy path
            return self._is_finished()
        return self._rr_phase_complete(phase)
    if phase.phase_type == "tournament":
        return (
            phase.tournament_id is not None
            and phase.tournament.state == "completed"
        )
    return False
```

- The `tournament` branch and the trailing `return False` are UNCHANGED.
- **`pk is None` ⇒ `_is_finished()`** keeps phase-less and single-explicit-RR-phase
  seasons byte-identical (their one RR phase covers the whole fixture list anyway).

NEW private helper **`Season._rr_phase_complete(self, phase) -> bool`** — per-phase
RR completion, scoped by `match__season_phase=phase`. Mirrors `_is_finished` but
(a) uses `scheduled_fixtures_by_phase()` to get THIS phase's offset fixtures and
(b) scopes the played-rounds query to this phase:

```
def _rr_phase_complete(self, phase: "SeasonPhase") -> bool:
    """True iff every fixture of THIS RR phase has a persisted GameRound.

    Per-phase analogue of _is_finished (decision #5). Scoped by
    match__season_phase=phase so RR1 must finish before RR2 opens. Only
    called for a PERSISTED RR phase (pk is not None); the implicit
    fallback routes through _is_finished in _phase_complete.
    """
    phase_fixtures = self._fixtures_for_phase(phase)
    if not phase_fixtures:
        return False
    rounds_qs = GameRound.objects.filter(
        match__season_phase=phase
    ).select_related("match")
    played_keys: set[tuple[frozenset[int], int]] = set()
    for game_round in rounds_qs:
        match = game_round.match
        if match is None or match.team_red_id is None or match.team_blue_id is None:
            continue
        played_keys.add(
            (
                frozenset({match.team_red_id, match.team_blue_id}),
                game_round.round_number,
            )
        )
    for fixture in phase_fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
        )
        if key not in played_keys:
            return False
    return True
```

- `Season._fixtures_for_phase(self, phase) -> list[ScheduleFixture]` is a private
  helper that returns THIS phase's OFFSET fixtures (the per-phase entry from
  `scheduled_fixtures_by_phase()`). It is the SINGLE place `_rr_phase_complete`
  reads its fixtures from, so completion and play agree on offsets. (Code-agent may
  inline this as a loop over `scheduled_fixtures_by_phase()` selecting the matching
  phase by `pk`; only the returned list shape is pinned.)
- **Side-agnostic key** matches `_is_finished` byte-for-byte (`frozenset` +
  `round_number`). The offset only changes `fixture.matchday`, which the played-key
  does NOT read, so per-phase scoping is purely the `match__season_phase=phase`
  filter — correct because each RR Match carries its owning phase (decision #2).

### 2.2 NEW `Season.scheduled_fixtures_by_phase()` (decision #7)

```
def scheduled_fixtures_by_phase(self) -> "list[tuple[SeasonPhase, list[ScheduleFixture]]]":
    """Return per-phase fixture lists with the global-continuous matchday
    offset applied (decision #6/#7).

    One (phase, fixtures) tuple per round_robin phase in ordinal order
    (tournament phases contribute NO fixtures — they are drained via the
    bracket, not generate_schedule). Phase k's fixtures are offset by the
    SUM of all prior RR phases' matchday counts so the whole season is one
    monotonic 1..N matchday calendar. Each fixture is a NEW ScheduleFixture
    with matchday = original_matchday + offset (round_number / team ids
    unchanged). Returns [] when no RR phase has >= 2 teams.
    """
    from .schedule_generator import generate_schedule, ScheduleFixture

    team_ids = self._scheduled_team_ids()          # §2.4 (extracted helper)
    if len(team_ids) < 2:
        return []

    result: list[tuple[SeasonPhase, list[ScheduleFixture]]] = []
    offset = 0
    for phase in self.ordered_phases():
        if phase.phase_type != "round_robin":
            continue
        base = generate_schedule(team_ids, phase.schedule_format or self.schedule_format)
        if not base:
            continue
        offset_fixtures = [
            ScheduleFixture(
                matchday=f.matchday + offset,
                round_number=f.round_number,
                team_a_id=f.team_a_id,
                team_b_id=f.team_b_id,
            )
            for f in base
        ]
        result.append((phase, offset_fixtures))
        offset += max(f.matchday for f in base)   # advance by THIS phase's span
    return result
```

- **Offset = sum of prior RR phases' matchday counts.** `max(f.matchday for f in
  base)` is the un-offset span of one RR phase (round-2 matchdays run `n..2*(n-1)`,
  so the max is the phase's total matchday count). Advancing `offset` by that span
  after each phase makes phase k+1 start at matchday `offset+1` → one monotonic
  1..N calendar (decision #6).
- **`phase.schedule_format or self.schedule_format`**: a persisted RR phase carries
  its own `schedule_format` (Part2b copies `Season.schedule_format` at create);
  the implicit fallback phase has `schedule_format` unset on the model, so it falls
  back to `self.schedule_format`. Both currently resolve to `"single_round_robin"`,
  so output is byte-identical to today for the single-RR case.
- **`team_ids`** is the SAME draft-vs-snapshot rule the current
  `scheduled_fixtures()` applies (§2.4) — every RR phase uses the same enrolled
  field set (per-phase rosters are out of scope this slice).

### 2.3 `Season.scheduled_fixtures()` — now the flat concatenation (decision #7)

REWRITE the body (`models.py:1229-1251+`) so it returns the **flat concatenation**
of all phases' offset fixtures (preserving the existing return type
`list[ScheduleFixture]` and the `[]`-on-`<2`-teams guard, so every existing flat
caller — `_is_finished`, `season_schedule`, `_build_dashboard_context`,
`league_history`, `team_schedule` — is unchanged):

```
def scheduled_fixtures(self) -> list["ScheduleFixture"]:
    """Flat fixture list for this Season's schedule.

    LG-02-Part2c-2: now the concatenation of every RR phase's
    OFFSET fixtures (scheduled_fixtures_by_phase), so the matchday
    numbering is global-continuous across multi-RR. For a single-RR-phase
    (or phase-less) Season the output is byte-identical to before
    (one phase, offset 0). NO matchday offsetting beyond what
    scheduled_fixtures_by_phase applies.
    """
    flat: list["ScheduleFixture"] = []
    for _phase, fixtures in self.scheduled_fixtures_by_phase():
        flat.extend(fixtures)
    return flat
```

- For one RR phase (or the implicit fallback) the loop runs once with offset 0 ⇒
  identical list to today. **Load-bearing**: `_is_finished()` (the legacy
  whole-season check, still used by `_phase_complete` for the `pk is None`
  fallback) reads this flat list — for phase-less seasons that is exactly today's
  output.

### 2.4 `Season._scheduled_team_ids()` — extracted helper (no behaviour change)

The draft-vs-snapshot `team_ids` rule currently inlined at the top of
`scheduled_fixtures()` (`models.py:1246-1249`) is extracted to a private helper so
both `scheduled_fixtures_by_phase()` and `_rr_phase_complete`'s `_fixtures_for_phase`
read one source:

```
def _scheduled_team_ids(self) -> list[int]:
    if self.state == "draft":
        return sorted(t.id for t in self.teams.all())
    return list(self.starting_team_ids_json or [])
```

Behaviour is byte-identical to the current inline rule.

### 2.5 Methods UNCHANGED this slice

`current_phase` (`:1009`), `_preceding_phase` (`:1047`),
`_final_standings_for_phase` (`:1056` — whole-season `Match.objects.filter(
season=self, is_completed=True)`, decision #4),
`activate_pending_tournament_phase` (`:1093`),
`_stamp_champion_for_final_phase` (`:1134`), `complete_if_finished` (`:984`),
`_is_finished` (`:1158`), `ordered_phases` (`:1214`), `_implicit_phase` (`:1203`),
`start_season` (`:962`). `current_phase` already walks `ordered_phases()` calling
`_phase_complete` per phase, so making `_phase_complete` per-phase-RR
automatically makes the cursor finish RR1 before RR2 opens (decision #5) — **no
`current_phase` edit needed**.

---

## 3. `simulate_scheduled_round` signature + find-or-create key (`matches/simulation/entrypoints.py`)

### 3.1 Signature gains keyword-only `season_phase=None` (decision #8)

```
def simulate_scheduled_round(
    self,
    season,
    team_a,
    team_b,
    round_number: int,
    *,
    arena_map=None,
    season_phase=None,
) -> "GameRound":
```

- `arena_map=None` (LG-01j) is UNCHANGED; `season_phase=None` is appended after it,
  still keyword-only. **Default None = legacy/sandbox unchanged** — every existing
  caller that omits it keeps today's behaviour.

### 3.2 Find-or-create key becomes phase-aware (decision #3)

The Side-agnostic lookup (`entrypoints.py:787-805`) gains `season_phase` to the
filter so identical pairings in DIFFERENT RR phases are DISTINCT Matches:

```
match = (
    Match.objects.filter(season=season, season_phase=season_phase)
    .filter(team_red=team_a, team_blue=team_b)
    .first()
) or (
    Match.objects.filter(season=season, season_phase=season_phase)
    .filter(team_red=team_b, team_blue=team_a)
    .first()
)
```

And the Round-1 create (`entrypoints.py:800-805`) stamps the phase:

```
if match is None:
    match = Match.objects.create(
        season=season,
        season_phase=season_phase,
        team_red=team_a,
        team_blue=team_b,
        is_completed=False,
    )
```

- **`season_phase=None`** (legacy/sandbox/the implicit-fallback play path) keeps
  the key `(season, NULL, frozenset)` — byte-identical to today's `(season,
  frozenset)` because NULL is the only phase value used pre-Part2c-2.
- The Round-2 lookup (which uses the SAME `match` variable resolved at the top) now
  also filters by `season_phase`, so it resolves to the same row Round-1 created.

### 3.3 Post-round hooks UNCHANGED (decision #8)

The two hook sites (`entrypoints.py:821-822`, `:849-850`) —
`season.activate_pending_tournament_phase()` then `season.complete_if_finished()`
— are UNCHANGED. `complete_if_finished` already gates on the FINAL phase, and
`_phase_complete` now resolves per-phase RR completion, so the season completes
only when the truly-final phase finishes.

**No simulator mechanics change, no new RNG draw.** The per-Round RNG / `_flush_to_db`
path is untouched; only the Match the round attaches to changes.

---

## 4. Pure phase-aware helpers (`matches/season_dashboard.py`)

The module STAYS Django-free (frozen `collections`/`dataclasses`/`typing`
allowlist — `season_dashboard.py:23-32`); `TestNoDjangoImportsLeaked`
(`test_league_play.py:874`) must keep passing. The two helpers gain phase-awareness
via **PLAIN INT phase-ids** — no Django, no `SeasonPhase` import.

### 4.1 played_keys shape gains the phase (decision #9)

The phase-aware played-key tuple is **`(season_phase_id, frozenset({team_red_id,
team_blue_id}), round_number)`** where `season_phase_id` is a plain `int | None`
(`match.season_phase_id`). The caller builds this set; the helpers compare against
it.

### 4.2 `select_play_fixtures` becomes phase-aware via `(phase_id, ScheduleFixture)` pairs

NEW phase-aware signature (decision #9) — carry `(phase_id, fixture)` tuples and
`(phase_id, frozenset, round_number)` keys, both plain-int-keyed:

```
def select_play_fixtures(
    fixtures: "list",
    played_keys: "set",
    max_matchdays: "Optional[int]",
) -> "list":
```

- **`fixtures`** is now a `list[tuple[int | None, ScheduleFixture]]` — each entry
  `(phase_id, fixture)`. (Pre-Part2c-2 callers passed a flat
  `list[ScheduleFixture]`; the play-loop callers in §5 now pass the
  `(phase_id, fixture)` pairs built from `scheduled_fixtures_by_phase()`.)
- **`played_keys`** is now a `set[tuple[int | None, frozenset[int], int]]`.
- The per-fixture key built inside the sweep becomes
  `(phase_id, frozenset({fixture.team_a_id, fixture.team_b_id}),
  fixture.round_number)`.
- **Matchday distinctness is global-continuous** (decision #6): because the play
  loop feeds OFFSET fixtures (their `matchday` is already global), the existing
  "next `max_matchdays` distinct matchdays" sweep over `fixture.matchday` selects a
  contiguous global window that naturally spans the RR1→RR2 boundary. The output is
  the unplayed `(phase_id, fixture)` pairs in iteration order.

> Implementation note: the Code-agent may keep the existing single-sweep
> distinct-matchday algorithm verbatim, swapping the per-iteration `fixture` for
> `phase_id, fixture = entry` unpacking and the 2-tuple key for the 3-tuple key.
> The `max_matchdays is None` (Play Until End) branch returns ALL unplayed pairs.

### 4.3 `find_next_matchday` becomes phase-aware

```
def find_next_matchday(
    fixtures: "list",
    played_keys: "set",
) -> "Optional[int]":
```

- Same shape swap: `fixtures` is `list[tuple[int | None, ScheduleFixture]]`,
  `played_keys` is `set[tuple[int | None, frozenset[int], int]]`. Returns the
  global `matchday` of the first unplayed `(phase_id, fixture)` pair (or `None`).

### 4.4 `find_next_fixture` / `round_progress` — out of this slice's hot path

`find_next_fixture` and `round_progress` are consumed by `_build_dashboard_context`
(`league_views.py:686-688`) over the FLAT `scheduled_fixtures()` + the flat
`played_keys` it builds (`:674-686`). **They stay on the FLAT 2-tuple key shape**
`(frozenset, round_number)` — the dashboard's next-fixture/round-progress display
reads the whole-season flat list, which is already global-continuous via the
offset, so no phase-aware variant is needed for them. (`compute_leaders` /
`LeaderRow` are untouched.)

> **Seam discipline:** `select_play_fixtures` and `find_next_matchday` move to the
> 3-tuple/`(phase_id, fixture)` shape because the PLAY loop must attribute each
> simulated Round to its owning phase; `find_next_fixture` / `round_progress` stay
> on the 2-tuple flat shape because the DASHBOARD only needs whole-season
> next/progress. Both shapes are plain-int / frozenset / dataclass — Django-free.

---

## 5. Play-loop wiring (decision #10)

Both sites iterate **by phase**, build phase-aware `played_keys`, pass
`season_phase` into `simulate_scheduled_round`.

### 5.1 `play_season_task` (`matches/tasks.py:179-238`)

Replace the flat `scheduled_fixtures()` + flat `played_keys` + flat
`select_play_fixtures` block (`:192-202`) with the by-phase form:

```
    season = Season.objects.get(id=season_id)

    # LG-02-Part2c-2 — by-phase fixtures (global-continuous matchday offset
    # already applied) + phase-aware played_keys.
    by_phase = season.scheduled_fixtures_by_phase()
    phase_by_id = {phase.id: phase for phase, _ in by_phase}
    fixtures = [
        (phase.id, fixture)
        for phase, phase_fixtures in by_phase
        for fixture in phase_fixtures
    ]
    played_keys = {
        (
            gr.match.season_phase_id,
            frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
            gr.round_number,
        )
        for gr in GameRound.objects.filter(match__season=season).select_related(
            "match"
        )
    }
    to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)
    n = len(to_play)

    if n == 0:
        return {"completed": 0, "total": 0}
```

The per-fixture loop (`:217-234`) unpacks the `(phase_id, fixture)` pair and passes
the resolved phase:

```
    team_ids = {f.team_a_id for _pid, f in to_play} | {f.team_b_id for _pid, f in to_play}
    team_by_id = Team.objects.in_bulk(team_ids)
    pool_ids = season.starting_map_pool_ids_json or []
    pool_by_id = ArenaMap.objects.in_bulk(pool_ids)

    for k, (phase_id, fixture) in enumerate(to_play):
        team_a = team_by_id[fixture.team_a_id]
        team_b = team_by_id[fixture.team_b_id]
        arena_map = _resolve_fixture_map(season, fixture, pool_by_id)
        BatchSimulator().simulate_scheduled_round(
            season,
            team_a,
            team_b,
            fixture.round_number,
            arena_map=arena_map,
            season_phase=phase_by_id.get(phase_id),
        )
        self.update_state(state="PROGRESS", meta={"completed": k + 1, "total": n})

    return {"completed": n, "total": n}
```

- `phase_by_id.get(phase_id)` resolves the `SeasonPhase` object (`phase_id` is
  always a real persisted id here — `scheduled_fixtures_by_phase()` only yields
  persisted RR phases; the implicit fallback has `pk is None` only for phase-less
  seasons, which still run through this loop with a single phase whose `id` is
  `None` → `phase_by_id.get(None)` → `None`, i.e. `season_phase=None`,
  byte-identical legacy behaviour). **NOTE:** for a phase-less season,
  `scheduled_fixtures_by_phase()` yields one tuple `(implicit_phase, fixtures)`
  where `implicit_phase.id is None`; `phase_by_id` keys on `None`, and
  `season_phase=None` flows through — preserving legacy `season_phase=NULL` Matches.
- The `_resolve_fixture_map` / `in_bulk` / progress / `finally
  close_old_connections` machinery is otherwise UNCHANGED.

### 5.2 `play_week` (`matches/league_views.py:1578-1643`)

Mirror the by-phase change inside the existing `with transaction.atomic():` block,
replacing the flat fixtures/played_keys/select block (`:1611-1621`):

```
            by_phase = season.scheduled_fixtures_by_phase()
            phase_by_id = {phase.id: phase for phase, _ in by_phase}
            fixtures = [
                (phase.id, fixture)
                for phase, phase_fixtures in by_phase
                for fixture in phase_fixtures
            ]
            played_keys = {
                (
                    gr.match.season_phase_id,
                    frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
                    gr.round_number,
                )
                for gr in GameRound.objects.filter(
                    match__season=season
                ).select_related("match")
            }
            to_play = select_play_fixtures(fixtures, played_keys, 1)
            if not to_play:
                return redirect("season_dashboard", season_id=season.id)
```

The per-fixture loop (`:1629-1639`) unpacks `(phase_id, fixture)` and passes the
phase, exactly as §5.1:

```
            team_ids = {f.team_a_id for _pid, f in to_play} | {f.team_b_id for _pid, f in to_play}
            team_by_id = Team.objects.in_bulk(team_ids)
            pool_ids = season.starting_map_pool_ids_json or []
            pool_by_id = ArenaMap.objects.in_bulk(pool_ids)
            for phase_id, fixture in to_play:
                team_a = team_by_id[fixture.team_a_id]
                team_b = team_by_id[fixture.team_b_id]
                arena_map = _resolve_fixture_map(season, fixture, pool_by_id)
                BatchSimulator().simulate_scheduled_round(
                    season,
                    team_a,
                    team_b,
                    fixture.round_number,
                    arena_map=arena_map,
                    season_phase=phase_by_id.get(phase_id),
                )
```

- `select_play_fixtures(..., 1)` (one matchday) still selects the next single
  global matchday — which, at the RR1→RR2 boundary, is the first matchday of RR2
  once RR1 is fully played, because the offset makes RR2's matchdays globally later.
- The 405 guard, `last_league_id` write, non-active 400 re-render, the
  `except (ValidationError, ValueError)` re-render, and the success 302 are
  UNCHANGED.

### 5.3 `play_two_months` / `play_until_end` (`league_views.py:1646`, `:1670`)

UNCHANGED — they enqueue `play_season_task.delay(season_id, max_matchdays=8|None)`;
all by-phase logic lives in the task body (§5.1). The `max_matchdays=8` window and
`None` (until end) semantics carry through `select_play_fixtures` unchanged
(global-continuous matchdays).

---

## 6. SCOPE-OUT (DEFERRED — do NOT build here)

- **Per-phase `schedule_format` wiring beyond the read** — `scheduled_fixtures_by_phase`
  reads `phase.schedule_format or self.schedule_format`, but the **first alternative
  RR format** itself (anything other than `single_round_robin`) is Part2c-3+.
- **Per-phase seeding-mode field** on `SeasonPhase` (season-ending vs mid-season
  tournament seeding) — Part2c-3+.
- **Mid-season tournaments** (a `tournament` phase between two RR phases) — NOT
  supported / NOT tested; the composer's tournament-must-follow-RR guard permits it
  structurally but the play loop + completion are only exercised for one-or-more RR
  then an OPTIONAL trailing tournament.
- **Per-tournament-block config** (format / top-N cut) — Part2c-3+; the trailing
  playoff stays full-field single-elimination (Part2c-1 `activate_pending_tournament_phase`
  consumed verbatim).
- **Non-single-elim embeds** (double-elim / RR / Swiss / RR→DE as a finals stage) —
  Part2c-3+.
- **Season-linked playoff Match-history surface** — playoff Matches stay
  `season=NULL, season_phase=NULL`; no season game-log surface for them.
- **Weekly playoff pacing** — the playoff plays one Match per "Play Single Round" /
  the whole bracket per "Play Playoffs" (Part2c-1), unchanged.
- **Tournament engine** (`play_next_node`, `simulate_match(match_type="tournament")`)
  — CONSUMED VERBATIM, no change.
- **`_final_standings_for_phase` per-phase scoping** — stays whole-season
  (decision #4); cumulative standings are the intended behaviour.

---

## 7. Test boundary

**What Tests assert against (the public seam):**

*Model / migration (`matches/tests/test_season_phase.py`, extend; or a new
`test_season_multi_rr.py`):*
- `Match.season_phase` FK exists, nullable, `SET_NULL`, reverse accessor
  `phase.matches`; deleting a `SeasonPhase` SET_NULLs its Matches (does not cascade).
- Migration `0043_match_season_phase` is a single `AddField`, no `RunPython`
  (assert via `makemigrations --check` staying clean + the migration op list).
- **Per-phase RR completion:** for a two-RR-phase Season,
  `_phase_complete(rr1)` is True only when all RR1 fixtures are played (scoped by
  `match__season_phase=rr1`), and `current_phase()` returns RR2 only after RR1 is
  complete (the cursor finishes RR1 before RR2 opens). A phase-less Season's
  implicit-fallback `_phase_complete` routes through `_is_finished()` byte-identically.
- **Find-or-create distinctness:** simulating the SAME pairing in RR1 and RR2
  creates TWO DISTINCT `Match` rows (different `season_phase`), each
  `season=<season>`; a re-run of the same `(season, phase, pairing)` finds the
  existing Match (idempotent). Legacy / `season_phase=None` keeps one Match per
  `(season, NULL, pairing)`.
- **Cumulative standings:** `_final_standings_for_phase(prior)` aggregates Matches
  across BOTH RR phases (whole-season filter) → a trailing playoff seeds from the
  cumulative leader; an RR-final-phase champion = cumulative leader.

*Fixture seam (`test_season_phase.py` / pure-adjacent):*
- `scheduled_fixtures_by_phase()` returns one `(phase, fixtures)` tuple per RR phase
  in ordinal order; phase 2's `matchday`s are offset by phase 1's span so the
  concatenation is a monotonic 1..N calendar with no overlap.
- `scheduled_fixtures()` (flat) == concatenation of the by-phase offset fixtures;
  for a single-RR-phase (or phase-less) Season it is byte-identical to before this
  slice (offset 0, one phase) — REGRESSION.
- `< 2` teams ⇒ both seams return `[]`.

*Pure helpers (`matches/tests/test_league_play.py`, extend the
`TestSelectPlayFixtures` / `TestFindNextMatchday` classes; purity class
`TestNoDjangoImportsLeaked` STILL passes):*
- `select_play_fixtures` over `(phase_id, fixture)` pairs + 3-tuple `played_keys`
  selects the next `max_matchdays` distinct GLOBAL matchdays, returns `(phase_id,
  fixture)` pairs in order, correctly spanning the RR1→RR2 boundary; `None` returns
  all unplayed pairs.
- `find_next_matchday` returns the global matchday of the first unplayed pair.
- Phase discrimination: an identical `(frozenset, round_number)` played in RR1 does
  NOT mark the RR2 pairing played (different `phase_id` in the key).
- **`TestNoDjangoImportsLeaked` (subprocess fresh-import of
  `matches.season_dashboard`) STILL passes** — the helpers stay Django-free
  (plain ints / frozensets / the duck-typed `ScheduleFixture`).

*Play loop (`test_league_play.py` Celery-EAGER classes + `views_tests.py`
`play_week`):*
- `play_season_task` over a two-RR-phase Season plays RR1 fully before any RR2
  fixture (matchday order), attributes each Round's Match to the correct
  `season_phase`, and the Season completes only when RR2 (the final RR) finishes (or
  the trailing playoff drains). Small-N seeded sims (N=2/3); assert on Match counts,
  `season_phase_id` attribution, and `state` — NEVER on exact point totals.
- `play_week` plays exactly the next single global matchday and advances across the
  RR1→RR2 boundary on the boundary click.
- **RR1→RR2→playoff composition** (`activate_pending_tournament_phase` consumed
  verbatim): once RR2 completes, the playoff auto-builds seeded by CUMULATIVE
  standings; draining it crowns the Season champion (assert `state` / `champion_team`
  id / bracket-node winners — non-deterministic tournament sims, never point totals).

*Regression:*
- A single-RR-phase Season (and the phase-less implicit fallback) is byte-identical
  end-to-end: same fixtures, same Matches (`season_phase=NULL` for the fallback),
  same completion + champion (existing LG-01 / Part2a / Part2c-1 tests stay green).

**What is internal (NOT asserted directly):** `_rr_phase_complete` /
`_fixtures_for_phase` / `_scheduled_team_ids` (asserted via `current_phase()` /
completion / fixture outcomes), the offset arithmetic internals (asserted via the
monotonic-calendar property), the `_phase_complete` `member_night` branch
(unreachable this slice).

**Determinism:** RR sims unchanged (byte-identical per Round). Tournament sims
non-deterministic. **No SIM-07 / SIM-08 interaction, NO Score Calibration
re-baseline.** Extend ADR-0023 (no new ADR).

---

## 8. Locked names (quick index)

**Model + migration (`matches/models.py`):**
`Match.season_phase = models.ForeignKey("matches.SeasonPhase", null=True,
blank=True, on_delete=models.SET_NULL, related_name="matches")`; reverse accessor
`SeasonPhase.matches`; migration `matches/migrations/0043_match_season_phase.py`
(dep `0042_seasonphase_format_tournament`, single `AddField`, NO `RunPython`).

**`Season` methods (`matches/models.py`):**
`scheduled_fixtures_by_phase(self) -> list[tuple[SeasonPhase, list[ScheduleFixture]]]`
(NEW — per-phase offset fixtures);
`scheduled_fixtures(self) -> list[ScheduleFixture]` (REWRITTEN — flat concatenation
of by-phase offset fixtures);
`_phase_complete(self, phase) -> bool` (REWRITTEN — RR branch per-phase via
`_rr_phase_complete`, `pk is None` ⇒ `_is_finished()`);
`_rr_phase_complete(self, phase) -> bool` (NEW — per-phase RR, scoped
`match__season_phase=phase`);
`_fixtures_for_phase(self, phase) -> list[ScheduleFixture]` (NEW private — THIS
phase's offset fixtures);
`_scheduled_team_ids(self) -> list[int]` (NEW private — extracted draft-vs-snapshot
rule).
UNCHANGED: `current_phase`, `_preceding_phase`, `_final_standings_for_phase`
(whole-season, decision #4), `activate_pending_tournament_phase`,
`_stamp_champion_for_final_phase`, `complete_if_finished`, `_is_finished`,
`ordered_phases`, `_implicit_phase`, `start_season`.

**Matchday offset (decision #6):** phase k offset = sum of prior RR phases'
matchday spans; per-phase span = `max(f.matchday for f in <un-offset base>)`;
result is one monotonic 1..N calendar; `date = start_date + (matchday-1)*7`
derivation keeps working unchanged.

**Find-or-create key (`matches/simulation/entrypoints.py`):**
`(season, season_phase, frozenset({team_a_id, team_b_id}))` — both lookup queries
filter `season=season, season_phase=season_phase`; Round-1 create stamps
`season_phase=season_phase`. Signature gains keyword-only `season_phase=None`:
`simulate_scheduled_round(self, season, team_a, team_b, round_number, *,
arena_map=None, season_phase=None) -> GameRound`. Post-round hooks
(`activate_pending_tournament_phase()` then `complete_if_finished()`) UNCHANGED.

**Pure helpers (`matches/season_dashboard.py`) — phase-aware, Django-free:**
`select_play_fixtures(fixtures, played_keys, max_matchdays) -> list` where
`fixtures: list[tuple[int | None, ScheduleFixture]]`,
`played_keys: set[tuple[int | None, frozenset[int], int]]`, output
`list[tuple[int | None, ScheduleFixture]]`;
`find_next_matchday(fixtures, played_keys) -> Optional[int]` (same shapes).
UNCHANGED on the FLAT 2-tuple shape: `find_next_fixture`, `round_progress`,
`compute_leaders`, `LeaderRow`. `TestNoDjangoImportsLeaked`
(`test_league_play.py:874`) must keep passing.

**played_keys shape (play loop):** `(season_phase_id, frozenset({team_red_id,
team_blue_id}), round_number)` built from `match.season_phase_id` (plain `int |
None`). FLAT dashboard `played_keys` (in `_build_dashboard_context`) stays the
2-tuple `(frozenset, round_number)`.

**Play-loop sites (decision #10):**
`matches/tasks.py::play_season_task` and
`matches/league_views.py::play_week` — both build `by_phase =
season.scheduled_fixtures_by_phase()`, a `phase_by_id` map, a flat
`[(phase.id, fixture)]` list, phase-aware `played_keys`, call
`select_play_fixtures(...)`, and pass `season_phase=phase_by_id.get(phase_id)` into
`simulate_scheduled_round`. `play_two_months` / `play_until_end` UNCHANGED (enqueue
the task).

**Composer / form / template:** UNCHANGED. `parse_phase_composition`
(`matches/phase_composer.py`) already permits multiple `round_robin` tokens (≥1 RR,
no cap, `phase_composer.py:90`); the Part2c-1 tournament-must-follow-RR guard
(`:93-101`) stays. No new compose guard.

**ADR:** EXTEND `docs/adr/0023-season-phase-composable-structure.md` with a
"Part2c-2 consequences" addendum (no new ADR). No new CONTEXT.md domain TERM.
**No simulator mechanics change, no tournament-engine change, no Score Calibration
re-baseline, no SIM-07/08 interaction.**
