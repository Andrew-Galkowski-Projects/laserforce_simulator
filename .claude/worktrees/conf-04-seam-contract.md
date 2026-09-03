# CONF-04 seam contract — the Worlds Tournament phase

The **single source of truth** for every name, signature, field, choice value,
context key and DOM id the Code, Tests and Docs agents share on branch
`conf-04-worlds-tournament`. Every decision below was locked at the CONF-04
grill (2026-09-03) and is recorded with its rationale and rejected alternatives
in
[ADR-0037](../../docs/adr/0037-worlds-is-a-derived-season-phase.md).
The domain language is already written in CONTEXT.md under **Worlds**, **Worlds
phase**, **Worlds qualifier**, **Last-chance qualifier**, **Conference**,
**Regional playoff**, **Conference champion** and **Season phase**. The
CONF-01/CONF-02/CONF-03 foundation this builds on is
[ADR-0034](../../docs/adr/0034-conference-partition.md),
[ADR-0035](../../docs/adr/0035-regional-playoffs-one-tournament-per-conference.md),
[ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md)
and [`.claude/worktrees/conf-03-seam-contract.md`](conf-03-seam-contract.md).

Nothing in this contract is open for renegotiation by an implementing agent. If
a name here turns out to be impossible, stop and report it rather than
inventing a substitute — a silent rename is exactly the failure mode this
document exists to prevent.

---

## 1. Scope

**CONF-04 in one line:** a Season with **two or more** Conferences grows a
**derived, never-authored fifth-mode `SeasonPhase`** — Worlds — that carries a
single Season-wide bracket built from `Season.worlds_qualifiers()`, drains
through the untouched bracket engine, and finally **crowns the Season
champion** that CONF-01, CONF-02 and CONF-03 each left NULL.

**Ship:**

- a new `SeasonPhase.TOURNAMENT_MODE_CHOICES` value, `("worlds", "Worlds")`;
- `Season._ensure_worlds_phase()` — the idempotent, derived phase creator,
  called from `start_season()` and from `activate_pending_tournament_phase()`;
- `Season.build_pending_worlds_bracket()` — the idempotent build seam, called
  from **five** hook sites;
- a keyword-only `minimum` floor on `bracket.build_bracket`,
  `bracket.build_double_elim_bracket` and `Tournament.lock_and_build`, so a
  two-Team Worlds field can build;
- a **two-tier** owner-mood playoff classification for a >= 2-Conference Season,
  done entirely inside `league_views._classify_playoffs_for_team`;
- the Playoffs screen's Worlds bracket section;
- a generalised `following_tournament_is_final`.

**Byte-identical:**

- a Season with **zero or one** Conference is untouched in rows, reads, champion
  stamping and every rendered DOM id — it grows **no** Worlds phase;
- every existing caller of `build_bracket` / `build_double_elim_bracket` /
  `lock_and_build` keeps the default `minimum=4`;
- `matches/owner_mood.py` is **not touched** — no new constants, no new branches,
  no new result strings;
- CONF-03's locked `qualifier_stage` read rule survives intact: there is
  **deliberately NO `qualifier_stage == "worlds"`**.

**Do NOT build (later slices):** per-Conference map pools (CONF-06); any
placement / elimination-depth ranking API on `Tournament`; a persisted qualifier
or Worlds-standings table; a Worlds composer wire token or any authoring UI for
the phase; a data migration or `RunPython` backfill of any kind.

**No Score Calibration re-baseline.** No simulation mechanic changes — the only
shifts are one choices-only `AlterField`, one derived `SeasonPhase` row per
>= 2-Conference Season, and one `Tournament` row per such Season.

---

## 2. New / changed PUBLIC signatures

| # | Module | Signature | Status |
| --- | --- | --- | --- |
| P1 | `matches/models.py` (`Season`) | `def build_pending_worlds_bracket(self) -> bool:` — decorated `@transaction.atomic` | **new** |
| P2 | `matches/bracket.py` | `def build_bracket(participants: list[ParticipantSpec], *, minimum: int = 4) -> list[BracketNodeSpec]:` | changed (additive kw-only) |
| P3 | `matches/bracket.py` | `def build_double_elim_bracket(participants: list[ParticipantSpec], *, minimum: int = 4) -> list[BracketNodeSpec]:` | changed (additive kw-only) |
| P4 | `matches/models.py` (`Tournament`) | `def lock_and_build(self, *, minimum: int = 4) -> None:` | changed (additive kw-only) |
| P5 | `matches/models.py` (`Season`) | `def worlds_qualifiers(self) -> "list[WorldsQualifier]":` | **unchanged** — CONF-03 verbatim. Its *behaviour* shifts only because `_final_tournament_phase()` is narrowed (§3, N1). |
| P6 | `matches/models.py` (`Season`) | `def tournaments_for_phase(self, phase) -> "list[Tournament]":` | **unchanged**, body and ordering verbatim. For a Worlds phase `phase.regional_tournaments` is empty, so it returns `[phase.tournament]` — or `[]` while unbuilt. |
| P7 | `matches/models.py` (`Season`) | `@transaction.atomic def start_season(self) -> None:` | changed (one inserted call, §6 site F) |
| P8 | `matches/models.py` (`Season`) | `@transaction.atomic def activate_pending_tournament_phase(self) -> None:` | changed (two inserted calls at the top, §6 site E) |
| P9 | `matches/models.py` (`Season`) | `@transaction.atomic def complete_if_finished(self) -> None:` | **unchanged** — verified to crown the Worlds champion with no edit (§5.4) |
| P10 | `matches/models.py` (`Season`) | `@transaction.atomic def seed_pending_last_chance_brackets(self, phase) -> int:` | **unchanged** |

`minimum` is **keyword-only** in all three of P2/P3/P4 (a bare `*` before it).
Positional call sites therefore cannot drift.

---

## 3. New / changed PRIVATE signatures

| # | Module | Signature | Status |
| --- | --- | --- | --- |
| N1 | `matches/models.py` (`Season`) | `def _final_tournament_phase(self) -> "SeasonPhase | None":` | changed — **narrowed** to skip `tournament_mode == "worlds"` |
| N2 | `matches/models.py` (`Season`) | `def _ensure_worlds_phase(self) -> "SeasonPhase | None":` | **new** |
| N3 | `matches/models.py` (`Season`) | `def _worlds_phase(self) -> "SeasonPhase | None":` | **new** — the single Worlds-phase resolver |
| N4 | `matches/league_views.py` | `def _classify_playoffs_for_team(season: Season, team_id: int) -> tuple[str, int, int]:` | changed — additive >= 2-Conference branch; **signature and return triple unchanged** |
| N5 | `matches/league_views.py` | `def _playoff_cursor_keys(displayed_season: Optional[Season]) -> tuple[bool, Optional[int], bool, bool, bool]:` | changed — one expression (`following_tournament_is_final`), §9 |
| N6 | `matches/league_views.py` | `def _run_season_rollover(league: League, latest_completed: Season) -> Season:` | changed — one `continue` in the phase-copy loop, §6 site G |
| N7 | `matches/models.py` (`Season`) | `def _build_tournament_for_phase(self, phase, conference=None) -> "Tournament | None":` | **unchanged** — the Worlds build does **not** go through it |
| N8 | `matches/models.py` (`Season`) | `def _stamp_champion_for_final_phase(self, final_phase) -> None:` | **unchanged** (§5.4) |
| N9 | `matches/models.py` (`Season`) | `def _preceding_phase(self, phase) -> "SeasonPhase | None":`, `_phase_complete`, `_tournament_phase_complete`, `_final_standings_for_phase`, `_seed_order_for_phase`, `ordered_phases`, `ordered_conferences`, `conference_by_team_id` | **all unchanged**, signature and body |

### 3.1 N1 — `_final_tournament_phase` narrowing (exact form)

```python
final = None
for phase in self.ordered_phases():
    if phase.pk is None:
        continue
    # CONF-04 — the Worlds phase is a tournament phase, but it is NOT the phase
    # qualification reads from (ADR-0037). Without this skip,
    # ``worlds_qualifiers()`` would read the Worlds phase's OWN bracket — empty
    # before the build, and self-referential after it.
    if phase.tournament_mode == "worlds":
        continue
    if phase.phase_type == "tournament":
        final = phase
return final
```

**After this slice `_final_tournament_phase()` means "the final NON-Worlds
tournament phase"** — i.e. the Regional-playoff phase. Both existing callers
(`worlds_qualifiers()` and the Last-chance build gate inside
`activate_pending_tournament_phase`) want exactly that. `complete_if_finished`
does **not** call it (it uses `ordered_phases()[-1]`), so champion stamping is
unaffected.

### 3.2 N3 — `_worlds_phase` (exact form)

```python
def _worlds_phase(self) -> "SeasonPhase | None":
    """CONF-04 — this Season's derived Worlds phase, or ``None`` (ADR-0037).

    The ONE resolver. ``_ensure_worlds_phase``, ``build_pending_worlds_bracket``
    and the owner-mood classifier all go through it, so nobody re-implements the
    ``tournament_mode == "worlds"`` scan. A non-persisted implicit fallback
    phase is never returned — it is always ``round_robin``.
    """
    for phase in self.ordered_phases():
        if phase.pk is not None and phase.tournament_mode == "worlds":
            return phase
    return None
```

At most one Worlds phase can exist per Season (`_ensure_worlds_phase` is
idempotent and the rollover skips it), so the first match is the only match.

### 3.3 N2 — `_ensure_worlds_phase` (exact contract)

```python
def _ensure_worlds_phase(self) -> "SeasonPhase | None":
```

**Return value:** the Season's Worlds phase — the row it created on this call
**or** the pre-existing one — and `None` when the Season is not eligible for
one. Nothing in production code reads the return value; both call sites discard
it. It is returned so Tests have a direct handle (§10 lists this private name as
test-visible by exception).

**Idempotent.** Creates **nothing** unless **all three** hold:

1. `len(self.ordered_conferences()) >= 2`;
2. at least one **persisted** phase with `phase_type == "tournament"` **and**
   `tournament_mode != "worlds"` exists;
3. `self._worlds_phase() is None`.

When (3) fails, return the existing phase. When (1) or (2) fails, return `None`.

**Ordinal:** `max(p.ordinal for p in self.phases.all()) + 1`. Gate (2)
guarantees at least one persisted row, so the `max` never sees an empty
sequence, and the value always respects the `uniq_season_phase_ordinal`
constraint (`UniqueConstraint(fields=["season", "ordinal"])`).

The written columns are **explicit and exhaustive** — §5.2.

---

## 4. New constants / enum values

Exactly one, in `matches/models.py`, appended as the **fifth** entry of
`SeasonPhase.TOURNAMENT_MODE_CHOICES`:

```python
TOURNAMENT_MODE_CHOICES = (
    ("standings", "Season-ending: from Standings"),
    ("strength", "Mid-season: by team strength"),
    ("unseeded", "Mid-season: random seed"),
    ("random_draw", "Mid-season: drawn pool -> RR->DE"),
    # CONF-04 — the DERIVED Worlds phase (ADR-0037). Never authored: there is no
    # composer wire token and no UI control for it; ``_ensure_worlds_phase``
    # appends it for a >= 2-Conference Season and the next-Season rollover skips
    # it. ``tournament_mode`` (the PHASE flavour) is the ONLY discriminator —
    # there is deliberately no ``Tournament.qualifier_stage == "worlds"``.
    ("worlds", "Worlds"),
)
```

- Stored value: exactly `"worlds"` (6 chars, well inside `max_length=16`).
- Label: exactly `"Worlds"`.
- The four existing pairs, their order, and their label strings are
  **byte-unchanged**, including the ASCII arrow in
  `"Mid-season: drawn pool -> RR->DE"`.

**No other constant is added anywhere.** In particular:

- no new `Tournament.QUALIFIER_STAGE_CHOICES` value;
- no new `SeasonPhase.PHASE_TYPE_CHOICES` value;
- no new `owner_mood.py` constant;
- `MIN_BRACKET_PARTICIPANTS` (`matches/models.py:18`, value `4`) is **not
  changed and not re-pointed**; it keeps guarding `_build_tournament_for_phase`
  and `seed_pending_last_chance_brackets` at 4.

---

## 5. The exact rows

### 5.1 The Worlds `SeasonPhase` row — every column

Written by `_ensure_worlds_phase`. Every column is passed **explicitly**; none
is left to a model default, so the row cannot drift if a default later changes.

| Column | Value | Note |
| --- | --- | --- |
| `season` | `self` | |
| `ordinal` | `max(p.ordinal for p in self.phases.all()) + 1` | always the highest ordinal in the Season |
| `phase_type` | `"tournament"` | **not** a new phase type — every `phase_type == "tournament"` site carries it for free |
| `schedule_format` | `None` | a tournament phase contributes no fixtures |
| `tournament` | `None` | the forward embed, filled later by `build_pending_worlds_bracket` |
| `tournament_mode` | `"worlds"` | the ONLY discriminator |
| `tournament_format` | `"single_elimination"` | |
| `tournament_cut` | `0` | never cut the Worlds field |
| `final_series_length` | `1` | |
| `semifinal_series_length` | `1` | |
| `quarterfinal_series_length` | `1` | |
| `earlier_series_length` | `1` | |
| `wb_advancers` | `0` | |
| `lb_advancers` | `0` | |
| `swiss_rounds` | `0` | |

### 5.2 The Worlds `Tournament` row — every written field

Created inside `build_pending_worlds_bracket`. **Both CONF-02 linkage columns
are LEFT UNSET** so they take their `null=True` default, and `qualifier_stage`
is left at its `""` default — structurally identical to the closing playoff of a
flat 0/1-Conference Season.

| Column | Value | Note |
| --- | --- | --- |
| `name` | `f"{self.name} Worlds"` | **no em-dash**, no Conference qualifier — mirrors the flat `f"{self.name} Playoffs"` shape |
| `format` | `"single_elimination"` | the literal, per ADR-0037. Equal to `phase.tournament_format` by §5.1, so either spelling produces the same row; the literal is the pinned one |
| `team_assembly` | `"preset"` | |
| `state` | `"setup"` | flipped to `"active"` by `lock_and_build(minimum=2)` before the method returns |
| `final_series_length` | `phase.final_series_length` (= 1) | |
| `semifinal_series_length` | `phase.semifinal_series_length` (= 1) | |
| `quarterfinal_series_length` | `phase.quarterfinal_series_length` (= 1) | |
| `earlier_series_length` | `phase.earlier_series_length` (= 1) | |
| `wb_advancers` | `phase.wb_advancers` (= 0) | |
| `lb_advancers` | `phase.lb_advancers` (= 0) | |
| `swiss_rounds` | `phase.swiss_rounds` (= 0) | |
| `season_phase` | **NOT PASSED** ⇒ `NULL` | it is **not** a regional row; it must never appear in any `phase.regional_tournaments` queryset |
| `conference` | **NOT PASSED** ⇒ `NULL` | it is Season-wide, not Conference-scoped |
| `qualifier_stage` | **NOT PASSED** ⇒ `""` | CONF-03's read rule stands: `qualifier_stage == "last_chance"` is still the only permitted positive test |

One `TournamentParticipant` per qualifier:

```python
for q in qualifiers:
    TournamentParticipant.objects.create(
        tournament=tournament,
        team_id=q.team_id,
        seed=q.seed,          # the 1..M value order_worlds_qualifiers stamped
    )
```

> **`seed=q.seed`, NOT `position + 1`.** `worlds_qualifiers()` returns the field
> already ordered and already stamped `1..M` by `order_worlds_qualifiers`. Using
> the enumerate index would silently work today (the list is in seed order) and
> break the instant anything filters or re-orders the list. Use the stamped
> value.

### 5.3 `build_pending_worlds_bracket` — the exact contract

```python
@transaction.atomic
def build_pending_worlds_bracket(self) -> bool:
```

**Returns `True` IFF it built the bracket on this call.** `False` in every other
case — no Worlds phase, already built, prior phase not complete, qualification
not ready. **Idempotent:** the second call always returns `False`.

Gate, evaluated in exactly this order:

1. `phase = self._worlds_phase()`; `if phase is None: return False`.
2. `if phase.tournament_id is not None: return False` — already built.
3. `prior = self._preceding_phase(phase)`;
   `if prior is None or not self._phase_complete(prior): return False`.
4. `qualifiers = self.worlds_qualifiers()`;
   `if not qualifiers or len(qualifiers) < 2: return False`.

> **The `>= 2` guard is defensive, not a live branch.** A legitimate
> >= 2-Conference Season always yields at least one qualifier per Conference
> (CONF-03 §2.3: no Conference is ever unrepresented), so `M >= 2` by
> construction. The guard exists so admin-mangled data — a Conference snapshot
> emptied after activation, say — cannot reach `lock_and_build` with a
> one-participant field. Record that in a comment; do not delete it because it
> "cannot happen".

Build, in this order:

5. `Tournament.objects.create(...)` per §5.2.
6. One `TournamentParticipant` per qualifier, per §5.2.
7. `tournament.lock_and_build(minimum=2)`.
8. `phase.tournament = tournament` ; `phase.save(update_fields=["tournament"])`.
9. `return True`.

Steps 5-8 are inside the method's own `@transaction.atomic`, so a failure at any
step leaves **no** partial Worlds bracket and the next hook-site call retries
cleanly. Nested inside an already-atomic caller (`start_season`,
`activate_pending_tournament_phase`) this is a savepoint — the CONF-03
`seed_pending_last_chance_brackets` precedent.

### 5.4 What crowns the champion — VERIFIED, no code change

Traced against the current `models.py` and stated here so no agent "fixes" it:

1. `complete_if_finished()` reads `final_phase = self.ordered_phases()[-1]` —
   which, for a >= 2-Conference Season, is now the **Worlds** phase (highest
   ordinal by §5.1).
2. `_phase_complete(worlds_phase)` ⇒ `_tournament_phase_complete` ⇒
   `tournaments_for_phase(worlds_phase)`. `worlds_phase.regional_tournaments` is
   empty (§5.2), so it returns `[worlds_phase.tournament]`, or `[]` while the
   bracket is unbuilt. `bool([]) is False` ⇒ **the Season parks on an unbuilt
   Worlds phase instead of completing championless.**
3. `_stamp_champion_for_final_phase(worlds_phase)`: `regional = list(
   worlds_phase.regional_tournaments.all())` is `[]`, so the CONF-02
   "a Regional playoff crowns no Season champion" early-return is **skipped** and
   the existing single-bracket branch runs —
   `self.champion_team = worlds_phase.tournament.champion`, `state="completed"`.

**Neither `complete_if_finished` nor `_stamp_champion_for_final_phase` is
edited.** That is the whole point of giving the Worlds row the flat
0/1-Conference shape.

---

## 6. Hook sites

Seven edit sites in total: five calls to `build_pending_worlds_bracket()`
(A-E, the ADR's "five hook sites"), plus the phase-creation call in
`start_season` (F) and the rollover skip (G).

| # | File | Function | Where exactly | Semantics |
| --- | --- | --- | --- | --- |
| A | `matches/tasks.py` | `play_playoffs_task` | inside `if not progressed:`, **after** the existing `seed_pending_last_chance_brackets` clause and **before** the `break` | build ⇒ re-resolve `phase` + `tournaments`, then `continue`; else `break` |
| B | `matches/tasks.py` | `play_season_task._drain_one_stage` | after the existing CONF-03 seed-then-retry block, still inside `_drain_one_stage` | build ⇒ `nonlocal` re-resolve, then one more `sum(play_next_bracket_round(...))` |
| C | `matches/league_views.py` | `play_single_round` | after the CONF-03 `seeded = ...` block, **before** `season.complete_if_finished()` | fire-and-forget; return value discarded |
| D | `matches/league_views.py` | `play_playoffs` | immediately after the `request.session[...]` write, **before** `phase = season.current_phase()` | fire-and-forget; must precede the cursor read |
| E | `matches/models.py` | `Season.activate_pending_tournament_phase` | the **first two statements** of the method, before `phase = self.current_phase()` | `_ensure_worlds_phase()` then `build_pending_worlds_bracket()`; both discarded |
| F | `matches/models.py` | `Season.start_season` | after the Conference-snapshot loop and `self.save()`, **before** the existing `self.activate_pending_tournament_phase()` call | `self._ensure_worlds_phase()` |
| G | `matches/league_views.py` | `_run_season_rollover` | first statement of the `for src in latest_completed.phases.all():` body | `if src.tournament_mode == "worlds": continue` |

### 6.A `play_playoffs_task` — verified enclosing names

The stall branch currently ends `if season.seed_pending_last_chance_brackets(phase) > 0: continue` / `break`. It becomes:

```python
if not progressed:
    if season.seed_pending_last_chance_brackets(phase) > 0:
        continue
    # CONF-04 — the regionals have drained, so the Worlds bracket may now be
    # buildable (ADR-0037). Unlike the Last-chance row it lives on a DIFFERENT
    # phase, so this is the ONE place the loop deliberately crosses the phase
    # boundary: re-resolve the cursor and its bracket list, then retry.
    if season.build_pending_worlds_bracket():
        phase = season.current_phase()
        tournaments = season.tournaments_for_phase(phase) if phase is not None else []
        continue
    break
```

`phase` and `tournaments` are plain locals of `play_playoffs_task`
(assigned at `tasks.py:470` and `:475`) — **no `nonlocal` is needed here**; the
loop is in the function body, not a closure.

### 6.B `play_season_task._drain_one_stage` — verified enclosing names

`_drain_one_stage` is nested inside `play_season_task` (`tasks.py:382`) and
closes over the locals `phase` (`tasks.py:352`) and `tournaments`
(`tasks.py:362`). Rebinding them therefore **requires** a `nonlocal`:

```python
def _drain_one_stage() -> int:
    nonlocal phase, tournaments
    clinched = sum(play_next_bracket_round(t) for t in tournaments)
    if clinched == 0:
        if season.seed_pending_last_chance_brackets(phase) > 0:
            clinched = sum(play_next_bracket_round(t) for t in tournaments)
    if clinched == 0:
        # CONF-04 — see play_playoffs_task: the ONE deliberate phase-boundary
        # crossing. Still one budget unit == one stage, because this fires only
        # when nothing else progressed.
        if season.build_pending_worlds_bracket():
            phase = season.current_phase()
            tournaments = (
                season.tournaments_for_phase(phase) if phase is not None else []
            )
            clinched = sum(play_next_bracket_round(t) for t in tournaments)
    return clinched
```

The two loop bodies (`while _drain_one_stage() > 0:` and
`for _ in range(bracket_budget):`), the budget arithmetic, `rr_weeks_played` and
the PROGRESS emissions are **unchanged**. Keeping the retry inside
`_drain_one_stage` is what leaves both loops untouched — do not lift it out.

> **CONSEQUENCE THE TESTS AGENT MUST KNOW.** `_stage_counts()` (in both tasks)
> closes over the same `tournaments` name. After the deliberate re-resolution it
> aggregates the **Worlds bracket alone**, so the reported
> `{"completed", "total"}` **shrinks** at the phase boundary — the regional
> stages drop out of the count. This is intended: the counts describe the
> **current** phase, matching the CONF-02 "stage counts of the phase being
> drained" contract. Tests must assert the FINAL return (`completed == total`,
> `total > 0`) and the terminal DB state, and must **never** assert that the
> PROGRESS counts increase monotonically across a run.

### 6.C `play_single_round`

```python
    # ... existing CONF-03 seeded / retry block ends here ...
    # CONF-04 — build the Worlds bracket if the click just finished the last
    # Regional playoff (ADR-0037). Without this the regionals-finishing click
    # would leave the cursor on an unbuilt Worlds phase, whose
    # ``tournaments_for_phase`` is ``[]`` — so the NEXT click would 400 with
    # "No active playoff bracket to play." and the user could never start Worlds.
    season.build_pending_worlds_bracket()
    season.complete_if_finished()
    return redirect("season_dashboard", season_id=season.id)
```

The return value is **discarded** and the click deliberately does **not** play a
Worlds node: this click already played its regional node, and the next click
finds `current_phase()` on a built Worlds phase and drains it normally. The
click is never dead.

`phase` and `tournaments` in this view were resolved **before** the build and
are stale afterwards; nothing below re-reads them, so do **not** re-resolve.

### 6.D `play_playoffs`

```python
    season = get_object_or_404(Season, pk=season_id)
    request.session["last_league_id"] = season.league_id

    # CONF-04 — build BEFORE the cursor read (ADR-0037). The cursor may already
    # be parked on an unbuilt Worlds phase, whose ``tournaments_for_phase`` is
    # ``[]`` — the guard below would 409 "No active playoff bracket to play."
    # and the drain could never be started.
    season.build_pending_worlds_bracket()

    phase = season.current_phase()
    if (
        phase is None
        or phase.phase_type != "tournament"
        or not season.tournaments_for_phase(phase)
    ):
        return JsonResponse({"error": "No active playoff bracket to play."}, status=409)
```

**The call MUST precede `phase = season.current_phase()`**, not merely the
`if`. `build_pending_worlds_bracket` writes `phase.tournament` on its **own**
freshly-loaded `SeasonPhase` instance; a `phase` object read before the build
carries a stale `tournament_id is None`, and `tournaments_for_phase` reads
exactly that attribute — so a build-after-read would still 409.

### 6.E `Season.activate_pending_tournament_phase`

The two calls are the **first two statements** of the method:

```python
@transaction.atomic
def activate_pending_tournament_phase(self) -> None:
    """..."""
    # CONF-04 — the RECOVERY hooks (ADR-0037). This method runs after every
    # scheduled Round, so a Season already ACTIVE when this slice shipped gains
    # its Worlds phase here rather than through a data migration. Every input
    # ``_ensure_worlds_phase`` reads is frozen at activation, so a late call
    # produces the identical row.
    self._ensure_worlds_phase()
    self.build_pending_worlds_bracket()
    phase = self.current_phase()
    if phase is None:
        return
    ...
```

Placing the build **before** `phase = self.current_phase()` also closes a
structural hazard: were the cursor ever to reach the Worlds phase with the
method running, the `len(conferences) >= 2` branch below would fan regional
brackets out onto it. Building first sets `phase.tournament_id`, so the existing
`if phase.tournament_id is not None: return` guard fires and the fan-out is
unreachable. **Do not add a `tournament_mode` guard to that branch** — the
ordering is the guard.

Everything else in the method — the four gates, the `standings` /
`strength` / `unseeded` mode split, the CONF-02 idempotence guard, the CONF-03
Last-chance loop and the 0/1-Conference tail — is **verbatim unchanged**.

### 6.F `Season.start_season`

```python
        self.state = "active"
        self.save()
        # CONF-04 — Start Season is the EARLIEST moment at which every input is
        # frozen: the Conference partition (snapshotted just above) and the phase
        # composition (authored at create). Appending the Worlds phase here, and
        # not lazily when qualification first resolves, is what stops
        # ``complete_if_finished`` from flipping the Season to ``completed`` with
        # a NULL champion at the instant the regional phase finishes (ADR-0037).
        self._ensure_worlds_phase()
        self.activate_pending_tournament_phase()
```

Insert **between** `self.save()` and the existing
`self.activate_pending_tournament_phase()` call. The gates inside
`_ensure_worlds_phase` make it a no-op for a 0/1-Conference Season, so the flat
path is byte-identical. (`activate_pending_tournament_phase` calls
`_ensure_worlds_phase` again a moment later; it is idempotent and returns the
row just created.)

### 6.G `_run_season_rollover`

```python
    for src in latest_completed.phases.all():
        # CONF-04 — do NOT carry the derived Worlds phase forward (ADR-0037).
        # The rollover carries no Conferences, so a copied Worlds phase would
        # land on a flat Season whose ``worlds_qualifiers()`` returns ``[]``
        # forever, stranding it at ``active``. The new Season grows its own
        # Worlds phase at ``start_season`` if it is ever partitioned.
        if src.tournament_mode == "worlds":
            continue
        SeasonPhase.objects.create(...)
```

Because the Worlds phase always holds the **highest** ordinal (§5.1), skipping
it leaves the copied ordinals contiguous from 1 — **no renumbering is needed and
none may be added.** Every other line of the copy (including the verbatim
`tournament_mode=src.tournament_mode` carry) is unchanged.

---

## 7. The bracket floor — `minimum`

### 7.1 `matches/bracket.py`

```python
def build_bracket(
    participants: list[ParticipantSpec], *, minimum: int = 4
) -> list[BracketNodeSpec]:
    ...
    if len(participants) < minimum:
        raise ValueError("A tournament requires at least 4 participants.")
```

```python
def build_double_elim_bracket(
    participants: list[ParticipantSpec], *, minimum: int = 4
) -> list[BracketNodeSpec]:
    ...
    if len(participants) < minimum:
        raise ValueError("A tournament requires at least 4 participants.")
    ...
    wb_specs = build_bracket(participants, minimum=minimum)   # MUST forward
```

**The ValueError message text is unchanged, literally `"A tournament requires at
least 4 participants."`, even when `minimum != 4`.** It is deliberately NOT
f-stringified: existing tests and callers pin the string, and the only non-4
caller is the Worlds build, whose `>= 2` guard (§5.3 step 4) means the message
is unreachable there.

`build_double_elim_bracket` **must forward `minimum=minimum`** into its internal
`build_bracket(participants)` call (`bracket.py:234`); forgetting it makes a
sub-4 DE build raise from the inner call.

The third caller pair inside `bracket.py` —
`build_rr_de_finals_bracket` (`bracket.py:408` and `:418`) — keeps the
**default**. Do not add `minimum=` there: an RR->DE finals build's `wb` is a
power of two of at least 4 by the create-form shape rule.

### 7.2 `Tournament.lock_and_build`

```python
def lock_and_build(self, *, minimum: int = 4) -> None:
    ...
    if len(participants) < minimum:
        raise ValidationError("A tournament requires at least 4 participants.")
    ...
    if is_de:
        specs = build_double_elim_bracket(part_specs, minimum=minimum)
    else:
        specs = build_bracket(part_specs, minimum=minimum)
```

- The `ValidationError` message is likewise **unchanged**.
- `minimum` is forwarded to the elimination builders only. The `round_robin` /
  `round_robin_double_elim` early return and the `swiss` early return do **not**
  call the elimination builders and are otherwise untouched.
- The `state != "setup"` guard, the RR->DE count validation, the Swiss
  even-N/round-clamp logic, `_persist_elim_specs` and the final `state="active"`
  save are **verbatim**.

### 7.3 Callers

| Caller | Passes | Result |
| --- | --- | --- |
| `Season._build_tournament_for_phase` (`models.py`) — Season-wide and regional builds | default | byte-identical |
| `Season.seed_pending_last_chance_brackets` (`models.py`) — Last-chance build | default | byte-identical |
| `matches/tournament_views.py` and the sandbox `/tournaments/` surface | default | byte-identical |
| `bracket.build_rr_de_finals_bracket` | default | byte-identical |
| **`Season.build_pending_worlds_bracket`** | **`minimum=2`** | the only non-default caller |

**Why 2 is safe.** `M` (the Worlds field) can legitimately be 2 — two
Conferences of 2-4 Teams each send one qualifier apiece, and an 8-Team,
2-Conference League is exactly what the create form produces by default. The
pure builder is already correct below four: `n = 2` yields
`size = 2 ** ceil(log2(2)) = 2`, one round, one node — **that node is the Worlds
final**; `n = 3` yields `size = 4` with seed 1 taking a pre-resolved round-one
bye. The `< 4` guard was inherited sandbox-form policy, not a property of the
maths.

---

## 8. Owner mood — the two-tier classification

**`matches/owner_mood.py` is NOT TOUCHED.** No new constant, no new branch, no
new result string. `compute_playoffs_delta(playoff_result, rounds_won,
num_rounds)` is consumed verbatim; its `"seeded"` branch is already
depth-proportional (`(PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won`), so
feeding it the longer two-bracket path yields the intended ladder for free.

`_classify_playoffs_for_team(season, team_id) -> tuple[str, int, int]` keeps its
signature, its return triple `(playoff_result, rounds_won, num_rounds)` and its
four result strings `"champion"` / `"seeded"` / `"missed"` / `"none"`. Its
single call site (`league_views.py:4616`, the owner-evaluations writer) is
unchanged.

### 8.1 Branch selection

```python
if len(season.ordered_conferences()) >= 2:
    # the CONF-04 two-tier path (§8.3)
else:
    # TODAY'S BODY, VERBATIM (§8.2)
```

### 8.2 The 0/1-Conference path — verbatim, byte-identical

Unchanged, line for line: the first `tournament` phase whose `tournament_id is
not None`; `None` ⇒ `("none", 0, 0)`; champion ⇒ `("champion", 0, num_rounds)`;
non-participant ⇒ `("missed", 0, num_rounds)`; else
`("seeded", rounds_won, num_rounds)`.

> Note the flat path deliberately keeps the `tournament_id is not None` scan and
> is deliberately **not** routed through `_worlds_phase()` — a flat Season never
> grows one, so the scan can never pick up a Worlds phase.

### 8.3 The >= 2-Conference path — the two brackets

Resolution, in order:

1. `conference = season.conference_by_team_id().get(team_id)`.
2. `regional_phase = season._final_tournament_phase()` — post-narrowing this is
   the **Regional-playoff** phase (§3.1).
3. `regional = None` unless both 1 and 2 resolved, in which case
   ```python
   regional = (
       regional_phase.regional_tournaments
       .filter(conference=conference)
       .exclude(qualifier_stage="last_chance")   # CONF-03 read rule
       .first()
   )
   ```
   The `.exclude(qualifier_stage="last_chance")` **is** CONF-03's rule: a
   Regional playoff is `conference_id is not None and qualifier_stage !=
   "last_chance"`, which correctly picks up an un-backfilled `""` row.
4. `worlds_phase = season._worlds_phase()`;
   `worlds = worlds_phase.tournament if worlds_phase is not None else None`.

Counting:

```python
regional_nodes = list(regional.nodes.all()) if regional is not None else []
worlds_nodes = list(worlds.nodes.all()) if worlds is not None else []

num_rounds = (
    max((n.bracket_round for n in regional_nodes), default=0)
    + max((n.bracket_round for n in worlds_nodes), default=0)
)
rounds_won = (
    len({n.bracket_round for n in regional_nodes if n.winner_id == team_id})
    + len({n.bracket_round for n in worlds_nodes if n.winner_id == team_id})
)
```

> **Count per bracket, then ADD — never union the raw `bracket_round`
> integers.** The two brackets number their rounds independently from 1, so a
> Team that won round 1 of its region and round 1 of Worlds has won **two**
> rounds; a set union would collapse them to one.

`num_rounds` is **the same maximum path for every Team in that Conference**, so
the denominator is fair within a region even when Conferences differ in size.

**The Last-chance bracket is excluded from BOTH the numerator and the
denominator** — the `.exclude(qualifier_stage="last_chance")` in step 3 is the
only place that needs saying so. A Team cannot ride the Last-chance bracket past
the maximum path its Conference offers.

### 8.4 The decision table

Evaluated top to bottom; the first matching row wins.

| # | Condition | `playoff_result` | `rounds_won` | `num_rounds` |
| --- | --- | --- | --- | --- |
| 1 | `conference is None` (Team in no Conference — admin-mangled) | `"none"` | `0` | `0` |
| 2 | `regional is None and worlds is None` (no bracket at all: the phase never built, or the Conference was too small for a Regional playoff **and** Worlds is unbuilt) | `"none"` | `0` | `0` |
| 3 | `worlds is not None and worlds.champion_id == team_id` | `"champion"` | `0` | as §8.3 |
| 4 | Team is a participant of **either** bracket | `"seeded"` | as §8.3 | as §8.3 |
| 5 | otherwise (participant of neither) | `"missed"` | `0` | as §8.3 |

Participation for row 4 is:

```python
in_regional = regional is not None and regional.participants.filter(team_id=team_id).exists()
in_worlds = worlds is not None and worlds.participants.filter(team_id=team_id).exists()
```

Row 1 returning `"none"` (the neutral `0.0` delta) rather than `"missed"` (the
`-0.2` penalty) is a **pinned defensive choice**: an unpartitioned Team in a
partitioned Season is broken data, and broken data must not fire a Manager.

Rows 2 and 5 are the ones that keep the pre-CONF-04 accident from returning: a
>= 2-Conference career League previously scored `("none", 0, 0)` on this axis
forever, because the regional phase leaves `tournament_id` NULL. Adding the
Worlds phase — which does set it — would, without §8.1's branch, have flipped the
axis on and charged every non-qualifier the full `"missed"` penalty regardless
of how far it went in its own region.

---

## 9. UI

### 9.1 `matches/league_screens/playoffs.py` — the bracket dict

Both derivations gain a Worlds branch that is evaluated **FIRST**, because the
Worlds `Tournament`'s `conference` is `None` and would otherwise fall into the
Season-wide branch:

```python
conference = tournament.conference
is_worlds = phase.tournament_mode == "worlds"          # the PHASE flavour
is_last_chance = tournament.qualifier_stage == "last_chance"   # the BRACKET stage
if is_worlds:
    key = f"{phase.ordinal}-worlds"
    stage = "worlds"
elif conference is None:
    key = str(phase.ordinal)
    stage = ""
elif is_last_chance:
    key = f"{phase.ordinal}-{conference.ordinal}-lc"
    stage = "last_chance"
else:
    key = f"{phase.ordinal}-{conference.ordinal}"
    stage = "regional_playoff"

if is_worlds:
    stage_label = "Worlds"
elif is_last_chance:
    stage_label = "Last Chance Qualifier"
else:
    stage_label = ""
```

### 9.2 The four built-bracket shapes, side by side

| Key | Season-wide (0/1-Conference) | Regional playoff | Last-chance | **Worlds** |
| --- | --- | --- | --- | --- |
| `phase` | the `SeasonPhase` | same | same | the Worlds `SeasonPhase` |
| `tournament` | the `Tournament` | same | same | the Worlds `Tournament` |
| `name` | `tournament.name` | `tournament.name` | `tournament.name` | `tournament.name` (= `"<season> Worlds"`) |
| `rounds` | `_build_rounds(t)["winners"]` | same | same (`[]` while unseeded) | same |
| `champion` | `tournament.champion` | same | same | same — **the Season champion** |
| `pending` | `tournament.state == "setup"` | same | same | same (always `False` in practice: the row is `lock_and_build`-ed in the same transaction that wires `phase.tournament`) |
| `conference` | `None` | the `Conference` | the `Conference` | **`None`** |
| `key` | `str(phase.ordinal)` | `"<ord>-<conf ord>"` | `"<ord>-<conf ord>-lc"` | **`"<ord>-worlds"`** |
| `stage` | `""` | `"regional_playoff"` | `"last_chance"` | **`"worlds"`** |
| `stage_label` | `""` | `""` | `"Last Chance Qualifier"` | **`"Worlds"`** |

Every 0/1-Conference `key`, every CONF-02 regional `key` and every CONF-03
`-lc` `key` is **byte-identical**, and so is every DOM id built from them.

### 9.3 The pending stub

The unbuilt-phase stub gains the same treatment, so a phase's DOM id is stable
across the unbuilt -> built transition:

| Key | Generic pending stub (unchanged) | **Worlds pending stub** |
| --- | --- | --- |
| `phase` | the `SeasonPhase` | the Worlds `SeasonPhase` |
| `tournament` | `None` | `None` |
| `name` | `"Playoffs"` | **`"Worlds"`** |
| `rounds` | `[]` | `[]` |
| `champion` | `None` | `None` |
| `pending` | `True` | `True` |
| `conference` | `None` | `None` |
| `key` | `str(phase.ordinal)` | **`f"{phase.ordinal}-worlds"`** |
| `stage` | `""` | **`"worlds"`** |
| `stage_label` | `""` | **`"Worlds"`** |

```python
is_worlds = phase.tournament_mode == "worlds"
brackets.append(
    {
        "phase": phase,
        "tournament": None,
        "name": "Worlds" if is_worlds else "Playoffs",
        "rounds": [],
        "champion": None,
        "pending": True,
        "conference": None,
        "key": f"{phase.ordinal}-worlds" if is_worlds else str(phase.ordinal),
        "stage": "worlds" if is_worlds else "",
        "stage_label": "Worlds" if is_worlds else "",
    }
)
```

A >= 2-Conference Season therefore shows a **"Worlds" pending section from the
moment the Season starts**. That is intended — the phase is derived at
`start_season`, and the panel tells the user Worlds is coming.

### 9.4 Resulting DOM ids — no template change

`templates/leagues/playoffs.html` is **NOT EDITED**. Every id falls out of the
existing `{{ bracket.key }}` interpolations, and the existing
`{% if bracket.stage_label %}` badge block renders the Worlds badge for free.

| Element | id for the Worlds bracket |
| --- | --- |
| Section | `league-playoffs-phase-<ord>-worlds` |
| Conference sub-heading | **absent** (`bracket.conference` is `None`) |
| Stage badge | `league-playoffs-stage-<ord>-worlds`, text `Worlds` |
| Champion alert | `league-playoffs-champion-<ord>-worlds` |
| Bracket container | `league-playoffs-bracket-<ord>-worlds` |
| Round column | `league-playoffs-round-<ord>-worlds-<bracket_round>` |
| Node | `league-playoffs-node-<ord>-worlds-<bracket_round>-<position>` |
| Node score | `league-playoffs-node-score-<ord>-worlds-<bracket_round>-<position>` |

The Worlds pending stub reaches the existing `{% if bracket.stage ==
"last_chance" %}` alert's **`{% else %}` branch** and renders the existing text
verbatim ("The bracket is not seeded yet — the regular season is still in
progress."). That is accepted; **do not add a Worlds-specific alert branch** —
the template stays untouched this slice.

> **DOM-id hazard.** `id="league-playoffs-worlds"` is **already taken** by
> CONF-03's Worlds *qualification* table (the seed/team/conference/provenance
> panel below the brackets). The CONF-04 bracket section is
> `league-playoffs-phase-<ord>-worlds`. They coexist on the same page and must
> never be conflated in an assertion.

`matches/league_screens/playoffs.py`'s context dict is otherwise **unchanged**,
`worlds_qualifiers` included.

### 9.5 `following_tournament_is_final` — generalised

In `league_views._playoff_cursor_keys` (`league_views.py:2076`) the expression:

```python
last_ordinal = phases[-1].ordinal
following_tournament_is_final = min(following_tournament_ordinals) == last_ordinal
```

becomes:

```python
# CONF-04 — generalised from "the next tournament phase IS the last phase" to
# "nothing but tournament phases follows it" (ADR-0037). Appending the Worlds
# phase after the Regional-playoff phase would otherwise flip a season-ending
# playoff back to the mid-season "Until Tournament" label.
next_tournament_ordinal = min(following_tournament_ordinals)
following_tournament_is_final = all(
    p.phase_type == "tournament"
    for p in phases
    if p.ordinal > next_tournament_ordinal
)
```

**Verified to reproduce today's result on both pre-CONF-04 shapes:**

| Phase composition | cursor | `following_tournament_ordinals` | old value | new value |
| --- | --- | --- | --- | --- |
| `RR(1) -> tournament(2)` | phase 1 | `[2]` | `min=2 == last=2` ⇒ **True** | no phase has `ordinal > 2` ⇒ vacuously **True** |
| `RR(1) -> t(2) -> RR(3) -> t(4)` | phase 1 | `[2, 4]` | `min=2 != last=4` ⇒ **False** | phase 3 is `round_robin` ⇒ **False** |
| `RR(1) -> t(2) -> worlds(3)` (new) | phase 1 | `[2, 3]` | `min=2 != last=3` ⇒ False (**wrong**) | phase 3 is `tournament` ⇒ **True** (correct) |

`has_following_tournament_phase` and every other value the helper returns are
**unchanged**.

---

## 10. Migration

| Property | Value |
| --- | --- |
| App | `matches` |
| File | `laserforce_simulator/matches/migrations/0060_alter_seasonphase_tournament_mode.py` |
| Depends on | `0059_tournament_qualifier_stage` — **verified** as the current latest (the directory ends `...0057_conference_match_conference`, `0058_tournament_regional_linkage`, `0059_tournament_qualifier_stage`) |
| Operations | exactly **one** `migrations.AlterField` |

```python
migrations.AlterField(
    model_name="seasonphase",
    name="tournament_mode",
    field=models.CharField(
        choices=[
            ("standings", "Season-ending: from Standings"),
            ("strength", "Mid-season: by team strength"),
            ("unseeded", "Mid-season: random seed"),
            ("random_draw", "Mid-season: drawn pool -> RR->DE"),
            ("worlds", "Worlds"),
        ],
        default="standings",
        max_length=16,
    ),
)
```

**There is NO `RunPython`, NO backfill, NO data migration and no second
migration.** A choices-only `AlterField` has no database-level effect on
PostgreSQL or SQLite. Per [ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md),
an already-active Season gains its Worlds phase through the
`activate_pending_tournament_phase` recovery hook (§6.E) on its next scheduled
Round; an already-**completed** Season stays completed and championless.

If Django auto-names the file differently, rename it to
`0060_alter_seasonphase_tournament_mode.py` — the name is part of this contract
so Docs can reference it.

`python laserforce_simulator/manage.py makemigrations --check --dry-run` must
report no further changes.

---

## 11. Test boundary

### 11.1 The no-simulation rule (carried forward from CONF-02 §9.1 / CONF-03 §9.1)

**Brackets are drained by driving the engine, never by running the simulator.**
Two permitted techniques: call
`matches.tournament_engine.play_next_bracket_round` / `play_next_node` directly
(under a small `ROUND_TICKS` patch) when exercising the drain; or stamp
`BracketNode.winner` / `Tournament.champion` / `Tournament.state = "completed"`
on the persisted rows when exercising a *gate* or a *derivation*.

**Forbidden `mock.patch` targets:** `build_pending_worlds_bracket`,
`_ensure_worlds_phase`, `_worlds_phase`, `_final_tournament_phase`,
`worlds_qualifiers`, `_classify_playoffs_for_team`, `lock_and_build`,
`build_bracket`, `build_double_elim_bracket`, `seed_pending_last_chance_brackets`,
`tournaments_for_phase`, `play_next_node`, `play_next_bracket_round`, plus
everything on the CONF-02 / CONF-03 lists. Patching `ROUND_TICKS` for speed
remains fine.

**Never assert on exact simulated point totals.** Assertions are schema-level:
row counts, ids, seeds, states, ordinals, booleans, context keys, DOM ids,
status codes, return values.

### 11.2 PUBLIC / asserted-against

`matches/models.py`:

- `Season.build_pending_worlds_bracket()` — the **returned bool** and its effects
  (the `Tournament` row's every field per §5.2, the `TournamentParticipant`
  seeds, `phase.tournament_id`, the `BracketNode` tree)
- `Season._ensure_worlds_phase()` — **private by name but test-visible by
  exception** (the ADR gives it no public caller): assert the returned phase and
  every column of §5.1, its idempotence, and that it creates nothing for a
  0/1-Conference Season or a Season with no non-`worlds` tournament phase
- `Season.worlds_qualifiers()` — contents and order (the narrowing of
  `_final_tournament_phase` is asserted **only** through this)
- `Season.tournaments_for_phase(phase)` — contents and order, for the Worlds
  phase and the regional phase
- `Season.complete_if_finished()` / `Season.champion_team` / `Season.state` /
  `Season.current_phase()` / `Season.ordered_phases()`
- `SeasonPhase.tournament` / `tournament_id`, `SeasonPhase.tournament_mode`,
  `SeasonPhase.ordinal`, `SeasonPhase.regional_tournaments`
- `Tournament.champion`, `.state`, `.name` (**containment only**),
  `.season_phase_id`, `.conference_id`, `.qualifier_stage`, `.participants`,
  `.nodes`
- `Tournament.lock_and_build(minimum=...)`, `bracket.build_bracket(...,
  minimum=...)`, `bracket.build_double_elim_bracket(..., minimum=...)` — the
  accepted/rejected counts and the unchanged error message strings

`matches/league_views.py`:

- `_classify_playoffs_for_team(season, team_id)` — the full returned triple, on
  both branches. (Private by name, test-visible by exception — the existing
  `test_owner_evaluations_writer.py` already asserts on it.)
- `play_single_round`'s 302 / 400; `play_playoffs`'s 202 / 409
- the Season dashboard's `following_tournament_is_final` context value
- the owner-evaluation rows the writer produces (`playoffs_delta`, `verdict`)

Callers and UI:

- `play_playoffs_task` / `play_season_task` return dicts (`completed`, `total`,
  `cancelled`)
- the Playoffs-screen `brackets` entries of §9.2-9.3 and the DOM ids of §9.4

### 11.3 INTERNAL — Tests must NOT assert on these

- `Season._final_tournament_phase()` and `Season._worlds_phase()` — assert their
  **observable effects** (`worlds_qualifiers()`, the built bracket, the
  classifier triple), never call them directly.
- The drain loops' local re-resolution of `phase` / `tournaments` — assert the
  **terminal** state and the final return dict, never the intermediate PROGRESS
  counts and never their monotonicity (§6.B).
- Query counts, SQL text, `select_related` / `prefetch_related` choices.
- The insertion order of `TournamentParticipant` rows (assert `seed` values).
- The Worlds `Tournament.name` beyond containment (`"Worlds" in t.name`).
- `matches/owner_mood.py` internals — it is not edited; assert through
  `_classify_playoffs_for_team` and the writer.
- The generic pending alert's wording for a Worlds stub (§9.4) — it is the
  existing text and is not part of this contract's surface.

### 11.4 File placement

**NEW file — `laserforce_simulator/matches/tests/test_worlds_tournament.py`.**
The slice's own home: phase derivation, the build seam, the drain to a champion,
and the Playoffs-screen Worlds section.

**APPENDED classes** — add **one** new `TestCase` class per file; **do not
restructure** the existing contents:

| File | Lines today | What it gains |
| --- | --- | --- |
| `matches/tests/test_regional_playoffs_drain.py` | 728 | both task hook sites (§6.A, §6.B): a single `play_playoffs_task` / `play_season_task` invocation drains the regionals, seeds and drains Last-chance, **builds and drains Worlds**, and completes the Season with a champion; the budgeted branch still spends one unit per stage |
| `matches/tests/test_season_playoffs.py` | 975 | `complete_if_finished` / `_stamp_champion_for_final_phase` end-to-end: the Season parks on an unbuilt Worlds phase and crowns `champion_team` only when the Worlds bracket completes; the 0/1-Conference path unchanged |
| `matches/tests/test_owner_evaluations_writer.py` | 833 | the §8.4 decision table through the writer, incl. the two-bracket `num_rounds` / `rounds_won` arithmetic and the byte-identical flat path |
| `matches/tests/test_league_next_season.py` | 2572 | `_run_season_rollover` skips the `worlds` phase (§6.G): the new Season's phase count, ordinals and modes |
| `matches/tests/test_bracket.py` | 3029 | the `minimum=` kwarg on `build_bracket` / `build_double_elim_bracket`: default still rejects `n = 3` with the unchanged message, `minimum=2` accepts `n = 2` (one node, one round) and `n = 3` (size 4, seed 1 byes), `minimum` is keyword-only, and DE forwards it to its inner `build_bracket` |

`test_bracket.py` is the **verified** name of the bracket test file (it holds
`TestBuildBracketErrors` at line 266, `TestBuildDoubleElimBracket` at 1461 and
the `TestNoDjangoImportsLeaked` subprocess class at 755).

`Tournament.lock_and_build(minimum=...)` coverage belongs in the new
`test_worlds_tournament.py` (it needs a DB), not in `test_bracket.py` (which is
`SimpleTestCase`, no DB).

### 11.5 Required coverage highlights

1. **Phase derivation.** `_ensure_worlds_phase` creates exactly one row with
   every column of §5.1 for a 2-Conference Season; **nothing** for 0 or 1
   Conference; **nothing** for a >= 2-Conference Season composed of RR phases
   only; **nothing** on a second call. Ordinal is `max + 1`.
2. **Recovery hook.** A Season activated *before* the phase existed (simulate by
   deleting the row) gains it on the next
   `activate_pending_tournament_phase()`, with an identical row.
3. **Build gate.** `build_pending_worlds_bracket()` returns `False` while the
   regional phase is incomplete, `False` while `worlds_qualifiers()` is `[]`,
   `True` exactly once, and `False` on every call thereafter — creating no
   second `Tournament` and no extra participants.
4. **`seed=q.seed`.** The participants' seeds equal the qualifiers' stamped
   1..M seeds, asserted against a field deliberately built so that seed order
   and list index would differ if `position + 1` were used.
5. **M = 2.** A 2-Conference Season of 4 Teams each builds a **one-node**
   Worlds bracket and crowns a champion — the `minimum=2` path end to end.
6. **M = 3.** A size-4 bracket in which the top seed byes into the final.
7. **Non-power-of-two M** (5 or 7) builds with byes, inherited from
   `build_bracket`.
8. **Champion.** After the Worlds drain, `season.state == "completed"` and
   `season.champion_team_id == worlds_tournament.champion_id`.
9. **The Worlds `Tournament` row's shape.** `season_phase_id is None`,
   `conference_id is None`, `qualifier_stage == ""`, and it does **not** appear
   in any `phase.regional_tournaments` queryset — while
   `tournaments_for_phase(worlds_phase) == [worlds_tournament]`.
10. **`play_playoffs` is not a 409 trap.** With the cursor parked on an unbuilt
    Worlds phase, `POST /…/play-playoffs` returns **202**, not 409.
11. **`play_single_round` is not a dead click.** The click that resolves the
    last regional node leaves the Worlds bracket `active`; the next click plays
    a Worlds node.
12. **Byte-identity pin — 0/1 Conference.** No Worlds phase, no Worlds
    `Tournament`, `champion_team` still stamped from the single bracket,
    `_classify_playoffs_for_team` triple unchanged, and no `-worlds` substring
    anywhere in the rendered Playoffs screen.
13. **Byte-identity pin — CONF-02/CONF-03 DOM ids.** On a >= 2-Conference
    Season, every regional `key` is still `"<ord>-<conf ord>"` and every
    Last-chance `key` still `"<ord>-<conf ord>-lc"`; the only new ids carry the
    `-worlds` suffix.
14. **`_final_tournament_phase` narrowing, asserted only through
    `worlds_qualifiers()`.** With the Worlds phase present and its bracket
    **built**, `worlds_qualifiers()` still returns the same field it returned
    before the build — proving qualification did not redirect at the Worlds
    phase's own bracket.
15. **Rollover.** `_run_season_rollover` copies every phase but the `worlds`
    one; the new Season's ordinals are contiguous from 1.

---

## 12. Byte-identity invariants — what MUST NOT change

1. **A Season with 0 or 1 Conference is byte-identical** in rows, reads, champion
   stamping and rendered DOM ids. `_ensure_worlds_phase` returns `None`,
   `build_pending_worlds_bracket()` returns `False`, no `Tournament` and no
   `SeasonPhase` row is added, and the Playoffs screen contains no `-worlds`
   substring.
2. **Every CONF-02 / CONF-03 DOM id is unchanged.** The `-worlds` suffix appears
   only on a Worlds entry; `stage_label` stays `""` for a Season-wide and a
   regional bracket, so no new element renders on a CONF-02 Season.
   `id="league-playoffs-worlds"` continues to mean **CONF-03's qualification
   table** and nothing else.
3. **Every existing caller of `build_bracket` / `build_double_elim_bracket` /
   `lock_and_build` keeps `minimum=4`** and its behaviour is unchanged, error
   message strings included. `minimum` is keyword-only so no positional call can
   drift. `build_rr_de_finals_bracket` does **not** forward it.
4. **`matches/owner_mood.py` is not touched.** No new constant, no new branch, no
   new `playoff_result` value. `_classify_playoffs_for_team` keeps its signature,
   its return triple and its four result strings, and its 0/1-Conference branch
   is verbatim.
5. **CONF-03's `qualifier_stage` read rule survives.** Every read still tests
   only `== "last_chance"`. **There is deliberately no
   `qualifier_stage == "worlds"`** and no new `QUALIFIER_STAGE_CHOICES` value.
   The Worlds bracket is identified from `phase.tournament_mode == "worlds"`.
6. **The bracket engine is untouched.** `play_next_node`, `play_specific_node`,
   `play_next_bracket_round`, `find_next_node`, `advance_winner`,
   `advance_loser`, `stage_progress`, `series_length_for_round`,
   `Tournament.find_next_playable_node`, `_persist_elim_specs` — reused verbatim.
7. **`complete_if_finished` and `_stamp_champion_for_final_phase` are not
   edited.** The Worlds row's flat shape (§5.2) is what makes the existing
   champion path fire (§5.4).
8. **`templates/leagues/playoffs.html` is not edited.**
9. **`matches/worlds.py` is not edited.** CONF-03's pure module stays pure —
   frozen import allowlist (`dataclasses`, `typing`), no Django, no ORM.
10. **Exactly one migration, choices-only, no `RunPython`.** No column is added,
    dropped or re-typed anywhere.
11. **No Score Calibration re-baseline.** No simulation mechanic changes.

---

## 13. Naming hazards

Carried forward from CONF-03, plus this slice's own. Every one of these is a
pair a Code agent and a Tests agent could plausibly confuse.

1. **`Tournament.season_phase` vs `Tournament.season_phases`** (carried forward).
   `season_phase` is the CONF-02 **back-linkage FK** (`related_name=
   "regional_tournaments"`) written **only** on a Conference-scoped row.
   `season_phases` is the **reverse accessor of the forward embed**
   `SeasonPhase.tournament`. The Worlds `Tournament` has
   `season_phase_id is None` **and** `worlds_tournament.season_phases.first()`
   **is** the Worlds phase. Never use one to test for the other.
2. **`tournament_mode == "worlds"` (the PHASE flavour) vs `qualifier_stage` (the
   BRACKET stage).** They live on different models and answer different
   questions. **There is deliberately NO `qualifier_stage == "worlds"`** — the
   Worlds `Tournament` carries `qualifier_stage == ""`. Anything that asks "is
   this the Worlds bracket?" asks the **phase**.
3. **`_final_tournament_phase()` now means "the final NON-Worlds tournament
   phase"** — the Regional-playoff phase. It is **not** `ordered_phases()[-1]`
   any more for a >= 2-Conference Season. Use `_worlds_phase()` to reach the
   Worlds phase and `ordered_phases()[-1]` where "the last phase" is meant
   (`complete_if_finished` does exactly that, and is unchanged).
4. **`minimum` (the builder/lock kwarg, default `4`) vs
   `MIN_BRACKET_PARTICIPANTS` (`matches/models.py:18`, value `4`).** They are
   **not** wired together: `matches/bracket.py` is a pure module and must not
   import from `models.py`, so the builders' default is the literal `4`.
   `MIN_BRACKET_PARTICIPANTS` is **not changed** and keeps guarding
   `_build_tournament_for_phase` and `seed_pending_last_chance_brackets`.
5. **`PROVENANCE_LAST_CHANCE` (`"last_chance"`, a *qualifier's* provenance) vs
   `Tournament.qualifier_stage == "last_chance"` (a *bracket's* stage)**
   (carried forward). Same string, different axes; neither may stand in for the
   other, and `matches/worlds.py` must not import from `matches/models.py`.
6. **`worlds_qualifiers` (CONF-03's derived field, and the Playoffs-screen
   context key) vs the Worlds `Tournament`'s `participants`.** The first is a
   list of frozen `WorldsQualifier` dataclasses with a `seed` attribute; the
   second is a `TournamentParticipant` queryset. They carry the same team ids
   and the same seeds after the build, and are still different objects.
7. **`league-playoffs-worlds` (CONF-03's qualification table) vs
   `league-playoffs-phase-<ord>-worlds` (CONF-04's bracket section).** Both
   render on the same page for a fully-resolved Season.
8. **`build_pending_worlds_bracket` (returns `bool`) vs
   `seed_pending_last_chance_brackets` (returns `int`).** Both are idempotent
   "do it if ready" seams and sit side by side at four of the same hook sites;
   their return types differ. `if season.build_pending_worlds_bracket():` and
   `if season.seed_pending_last_chance_brackets(phase) > 0:` are both correct as
   written — do not "harmonise" them.
9. **`_ensure_worlds_phase` (creates the PHASE) vs
   `build_pending_worlds_bracket` (creates the BRACKET).** Both are called at
   the top of `activate_pending_tournament_phase`, in that order, and only that
   order works: the build resolves the phase the ensure just created.

---

## 14. File ownership — three parallel agents, zero collisions

Every file below is owned by exactly one agent. **Do not edit a file you do not
own**; if you believe a change is needed in someone else's file, report it
instead.

### Code agent — owns

- `laserforce_simulator/matches/models.py` (§3, §4, §5, §6.E, §6.F, §7.2)
- `laserforce_simulator/matches/bracket.py` (§7.1)
- `laserforce_simulator/matches/migrations/0060_alter_seasonphase_tournament_mode.py`
  (§10, **new file**)
- `laserforce_simulator/matches/tasks.py` (§6.A, §6.B)
- `laserforce_simulator/matches/league_views.py` — **only**
  `play_single_round` (§6.C), `play_playoffs` (§6.D), `_run_season_rollover`
  (§6.G), `_classify_playoffs_for_team` (§8) and `_playoff_cursor_keys` (§9.5).
  Every other function in this file is off-limits.
- `laserforce_simulator/matches/league_screens/playoffs.py` (§9.1-9.3)

Runs `python -m black laserforce_simulator` on its own files when done.

**Must NOT touch:** `matches/worlds.py`, `matches/owner_mood.py`,
`matches/tournament_engine.py`, `matches/standings.py`,
`matches/tournament_views.py`, `templates/leagues/playoffs.html`.

### Tests agent — owns

- `laserforce_simulator/matches/tests/test_worlds_tournament.py` (**new**)
- `laserforce_simulator/matches/tests/test_regional_playoffs_drain.py` (append one class)
- `laserforce_simulator/matches/tests/test_season_playoffs.py` (append one class)
- `laserforce_simulator/matches/tests/test_owner_evaluations_writer.py` (append one class)
- `laserforce_simulator/matches/tests/test_league_next_season.py` (append one class)
- `laserforce_simulator/matches/tests/test_bracket.py` (append one class)

Touches no production file.

### Docs agent — owns

- `PLAN.md` — flip the **CONF-04** bullet (line ~887) from `[NOT STARTED]` to
  done. Do not touch the CONF-03 or CONF-05/06 bullets' substance.
- `laserforce_simulator/matches/CLAUDE.md` — a **CONF-04** subsection covering
  the derived Worlds phase and its two creation hooks, the flat Worlds
  `Tournament` shape and why it makes champion stamping work unedited, the five
  build hook sites and the deliberate phase-boundary re-resolution, the
  `minimum` floor, and the two-tier owner-mood classification.
- `PLAN-completed.md` — if the house convention moves the finished bullet there.

Docs must **NOT** rewrite:

- `docs/adr/0037-worlds-is-a-derived-season-phase.md` — already written and
  Accepted. Reference it; do not edit it.
- `docs/adr/0034`, `0035`, `0036` — closed.
- `CONTEXT.md` — the **Worlds** / **Worlds phase** terms were updated for this
  slice in the working tree already. Do not re-word them.
- This contract.

---

## 15. Definition of done

- One migration, `0060_alter_seasonphase_tournament_mode`, exactly one
  `AlterField`, no `RunPython`.
- `python laserforce_simulator/manage.py makemigrations --check --dry-run`
  reports no further changes.
- `python -m black laserforce_simulator` is clean.
- The full `pytest` suite passes, reported with **exact** pass/fail counts (e.g.
  "N passed, 0 failed"), not "tests pass".
- A 0-Conference and a 1-Conference Season behave identically to
  `conf-03-worlds-qualification` in rows, reads, champion stamping and rendered
  DOM ids.
- A >= 2-Conference Season **crowns a Season champion** — the rule CONF-01
  opened and CONF-02 and CONF-03 each carried forward is closed.
