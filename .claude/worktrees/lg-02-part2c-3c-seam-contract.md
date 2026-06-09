# LG-02-Part2c-3c · Mid-season tournaments — Seam Contract

Single source of truth for three parallel agents (code / tests / docs). Mid-season
`tournament` SeasonPhase that sits between two `round_robin` phases (or first), shipping
the `strength` + `unseeded` seeding modes (the `tournament_mode` field already exists,
Part2c-3b). DEFERS `random_draw`. Relaxes the Part2c-1 compose guard to standings-only.
Adds a play-loop "barrier" so the RR loop halts at an incomplete tournament phase and the
mid-season bracket drains through the EXISTING `play_single_round` / `play_playoffs` views
before later RR phases play. **No migration. No Score Calibration re-baseline** (tournament
sims already non-deterministic). **Extend [ADR-0023], no new ADR.**

---

## 1. Name table (NEW / CHANGED, public + private)

| Name | Module | Signature / shape | NEW/CHANGED |
|---|---|---|---|
| `parse_phase_composition` | `matches/phase_composer.py` | `(raw: str, *, season_schedule_format: str) -> list[PhaseSpec]` | CHANGED — parse `tournament[:mode]` token |
| `PhaseSpec.tournament_mode` | `matches/phase_composer.py` | field `str = "standings"` (already exists) | UNCHANGED shape; now STAMPED from wire |
| `Season._seed_order_for_phase` | `matches/models.py` | `(self, phase) -> list[int]` | NEW (private) |
| `Season.activate_pending_tournament_phase` | `matches/models.py` | `(self) -> None` `@transaction.atomic` | CHANGED — generalized gate + mode branch + name |
| `Season.start_season` | `matches/models.py` | `(self) -> None` `@transaction.atomic` | CHANGED — adds `activate_pending_tournament_phase()` call inside the atomic block |
| `Season._tournament_barrier_ordinal` | `matches/models.py` | `(self) -> int | None` | NEW (private; may inline) |
| `Season.playable_fixtures_by_phase` | `matches/models.py` | `(self) -> list[tuple[SeasonPhase, list[ScheduleFixture]]]` | NEW |
| `default_seed_order` | `matches/bracket.py` | `(team_ratings: list[tuple[int, float]]) -> list[int]` (already exists) | REUSED verbatim for `strength` |
| `play_season_task` | `matches/tasks.py` | swap `scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()` | CHANGED (one call) |
| `play_week` | `matches/league_views.py` | swap `scheduled_fixtures_by_phase()` → `playable_fixtures_by_phase()` | CHANGED (one call) |
| `_build_dashboard_context` / `_playoff_cursor_keys` | `matches/league_views.py` | terminal-label split (see §7) | CHANGED |

### Explicitly UNCHANGED (byte-for-byte — pinned)
- `matches/season_dashboard.py`: `select_play_fixtures`, `find_next_matchday`,
  `find_next_fixture`, `round_progress`, `compute_leaders`, `LeaderRow` — Django-free purity
  preserved; `TestNoDjangoImportsLeaked` must keep passing. **No edit to this file.**
- `Season.scheduled_fixtures()` and `Season.scheduled_fixtures_by_phase()` — the DISPLAY path
  (`season_schedule`, `_build_dashboard_context`, `league_history`, `team_schedule`,
  `_is_finished`, `_rr_phase_complete`, `_fixtures_for_phase`) reads these UNCHANGED.
- `play_two_months` / `play_until_end` — enqueue `play_season_task`; unchanged.
- The playoff button-group DOM ids + the `play_until_end` action — only the visible label
  text varies.
- `bracket.py` `default_seed_order` body, `Tournament.lock_and_build`,
  `TournamentParticipant`, `simulate_match` / `simulate_scheduled_round`, the tournament
  engine — all consumed verbatim.

---

## 2. Wire-format grammar + ValueError list (`phase_composer.py`)

The `tournament` wire token becomes **`tournament[:mode]`**. Each token is split on the
FIRST `:` into `(type_part, format_part)`; for a `tournament` token, **`format_part` is the
MODE** (versus a `round_robin` token where `format_part` is the schedule format).

- Bare `tournament` ⇒ `tournament_mode="standings"`.
- Valid modes THIS slice: `standings`, `strength`, `unseeded`.
- `random_draw` **AND any unknown string** ⇒ NEW locked
  `ValueError(f"unknown tournament_mode: {mode!r}")`.
- Stamp `PhaseSpec.tournament_mode` (the field already exists, default `"standings"`).
- `member_night` still rejected at the **type** level (`f"unknown phase type: {token!r}"`).
- `PhaseSpec` shape otherwise unchanged: `(ordinal, phase_type, schedule_format,
  tournament_mode)`.

**Parse rule for a `tournament` token (replaces the current "tournament takes no format /
`_sep or format_part` ⇒ malformed" branch):** split on first `:`; `type_part == "tournament"`
⇒ `mode = format_part or "standings"`; if `mode not in {"standings","strength","unseeded"}`
⇒ raise the new ValueError; set `schedule_format=None`, `tournament_mode=mode`.

### Full ValueError string list (all VERBATIM preserved + the one new)
| String | Status |
|---|---|
| `"malformed phase composition"` | preserved (empty token / empty type_part) |
| `f"unknown phase type: {token!r}"` | preserved (non-RR/non-tournament, incl. `member_night`) |
| `f"unknown schedule_format: {fmt!r}"` | preserved (RR token bad format) |
| `"composition must contain at least one round-robin phase"` | preserved |
| `"a tournament phase requires a preceding round-robin phase"` | preserved string, **guard relaxed** (§3) |
| `f"unknown tournament_mode: {mode!r}"` | **NEW** |

Module stays Django-free (allowlist `dataclasses`, `typing`); raises plain `ValueError`; the
form layer re-wraps as a `forms.ValidationError` on `phases`.

---

## 3. Compose guard relaxation (`phase_composer.py`)

- Keep the "≥1 round_robin" rule verbatim: `"composition must contain at least one
  round-robin phase"`.
- **Relax the preceding-RR rule.** The string stays VERBATIM
  (`"a tournament phase requires a preceding round-robin phase"`) but now fires **ONLY** for a
  tournament spec whose `tournament_mode == "standings"`. The current guard walks the specs and
  raises if any `tournament` precedes the first `round_robin`; change the condition so it raises
  only when `spec.phase_type == "tournament" AND spec.tournament_mode == "standings" AND not
  seen_round_robin`.
- `strength` / `unseeded` tournament phases may sit **anywhere, including first**.
- A mid-season `standings` tournament is ALLOWED (there is **no** "standings-must-be-final"
  guard — only "standings-must-have-a-preceding-RR").

---

## 4. Build differential — `Season.activate_pending_tournament_phase` + `_seed_order_for_phase`

Current method (models.py ~L1166–1206) gates on `prior is None or not _phase_complete(prior)`
returning. **Generalize the gate:** `_preceding_phase(phase) is None` is now PERMITTED (for a
non-`standings` first phase the prior-complete check is vacuously true when there is no prior).
Concretely, keep the existing guards `phase is None` / `phase.phase_type != "tournament"` /
`phase.tournament_id is not None` (idempotency) / `phase.pk is None`. Then:

```
prior = self._preceding_phase(phase)
if phase.tournament_mode == "standings":
    if prior is None or not self._phase_complete(prior):
        return
else:
    # strength / unseeded — prior is permitted to be None; when a prior exists it
    # must still be complete (the RR-loop barrier guarantees this, but keep the check).
    if prior is not None and not self._phase_complete(prior):
        return
order = self._seed_order_for_phase(phase)
if not order:
    return
```

### `Season._seed_order_for_phase(self, phase) -> list[int]` (NEW private)
Branch on `phase.tournament_mode`:

- **`standings`** → `[row.team_id for row in self._final_standings_for_phase(prior)]` (rank
  order; requires `prior`, already non-None per the gate above). Byte-identical to today's
  `rows` ordering.
- **`strength`** → `matches.bracket.default_seed_order([(tid, mean_overall_rating) for tid in
  team_ids])` where `team_ids = self.starting_team_ids_json or []` and `mean_overall_rating =
  mean(p.overall_rating for p in Team(tid).active_players)`. Use `teams.models.Team` (already
  imported in models.py), `Team.active_players` (property, `teams/models.py:121`) and
  `Player.overall_rating` (property, `teams/models.py:267`). Mean of an empty active-players
  list ⇒ `0.0` (guard against `ZeroDivisionError`). `default_seed_order` sorts mean DESC then
  `team_id` ASC (bracket.py:54–64).
- **`unseeded`** → a fresh `random.Random()` shuffle of `team_ids` → the shuffled order.
  `team_ids = self.starting_team_ids_json or []`. (Fresh `random.Random()`, NOT the SIM-07 seed
  chain — non-deterministic, no contract interaction.) Import `random` locally inside the method
  (models.py is not in a frozen-allowlist module, so a top-level or local import is fine).

### Shared build tail (mode-independent)
```
tournament = Tournament.objects.create(
    name=<name>,                       # see below
    format="single_elimination",
    team_assembly="preset",
    state="setup",
)
for position, team_id in enumerate(order):
    TournamentParticipant.objects.create(
        tournament=tournament,
        team_id=team_id,
        seed=position + 1,             # 1-based index into the ordered list
    )
phase.tournament = tournament
phase.save(update_fields=["tournament"])
tournament.lock_and_build()
```

`seed = position + 1` is **byte-identical to today's `seed=row.rank`** for `standings`
(`_final_standings_for_phase` returns dense 1..N ranks, so rank == position+1).

### Tournament name
- `standings` → `f"{self.name} Playoffs"` (unchanged from today).
- `strength` / `unseeded` → `f"{self.name} Tournament"`.

---

## 5. Build trigger at `start_season` (`Season.start_season`)

`Season.start_season()` (models.py ~L976–996) gains an
`self.activate_pending_tournament_phase()` call **INSIDE** the existing `@transaction.atomic`
block, **AFTER** the existing snapshot writes (`starting_team_ids_json`,
`starting_map_pool_ids_json`) and the `self.state = "active"` + `self.save()`. This makes a
FIRST-phase mid-season tournament (`strength` / `unseeded`, no preceding RR) build the instant
the Season activates. (For a `standings` first phase the build no-ops — the gate requires a
prior; but a `standings` phase can never be first per the compose guard.)

The existing post-Round hook in `simulate_scheduled_round` (which calls
`activate_pending_tournament_phase()` before `complete_if_finished()` in both Round branches)
is **UNCHANGED** — it covers the mid-season-after-RR case. The method is idempotent, so calling
it at both `start_season` and post-round is safe.

---

## 6. Barrier — `Season.playable_fixtures_by_phase` + two play-loop swaps

### `Season.playable_fixtures_by_phase(self) -> list[tuple[SeasonPhase, list[ScheduleFixture]]]` (NEW)
= `scheduled_fixtures_by_phase()` filtered to RR phases whose `ordinal` is **strictly LESS**
than the first incomplete `tournament` phase's ordinal. When no tournament phase is incomplete,
returns ALL RR phases (the full `scheduled_fixtures_by_phase()` output).

```
barrier = self._tournament_barrier_ordinal()   # int | None
by_phase = self.scheduled_fixtures_by_phase()
if barrier is None:
    return by_phase
return [(phase, fixtures) for phase, fixtures in by_phase if phase.ordinal < barrier]
```

(Note: `scheduled_fixtures_by_phase()` already yields only `round_robin` phases — tournament
phases contribute no fixtures — so the filter reduces to an ordinal comparison.)

### `Season._tournament_barrier_ordinal(self) -> int | None` (NEW private; may inline)
Walk `ordered_phases()`; return the `ordinal` of the FIRST `tournament` phase that is NOT
`_phase_complete(phase)`, else `None`.

```
for phase in self.ordered_phases():
    if phase.phase_type == "tournament" and not self._phase_complete(phase):
        return phase.ordinal
return None
```

### Two play-loop swap sites (NOTHING ELSE in the loops changes)
- `matches/tasks.py::play_season_task` (~L192): `season.scheduled_fixtures_by_phase()` →
  `season.playable_fixtures_by_phase()`. `phase_by_id`, the flat `[(phase.id, fixture)]` build,
  the `leg`-bearing `played_keys`, `select_play_fixtures`, offsets, `arena_map` resolution —
  all UNCHANGED.
- `matches/league_views.py::play_week` (~L1619): same one-line swap. Everything else UNCHANGED.

`play_two_months` / `play_until_end` enqueue `play_season_task` — **UNCHANGED**.

### Unchanged-purity note
`select_play_fixtures` / `find_next_matchday` (the play-loop pure helpers) and the display-path
`scheduled_fixtures()` / `scheduled_fixtures_by_phase()` stay **BYTE-UNCHANGED**. The barrier is
a new READER over those; it never edits them. `matches/season_dashboard.py` is **not touched**
(`TestNoDjangoImportsLeaked` stays green).

**Why the barrier:** with a mid-season tournament, the RR loop must halt before later RR phases
so the bracket (built by the post-RR hook) drains through the existing `play_single_round` /
`play_playoffs` views first. `playable_fixtures_by_phase()` excludes every RR phase at/after the
incomplete tournament phase's ordinal; once the tournament phase completes,
`_tournament_barrier_ordinal()` advances past it and the later RR phases become playable.

---

## 7. Dashboard terminal-label split (`_build_dashboard_context` / template)

Today: the play-dropdown terminal button reads **"Play Until Playoffs"** when
`has_following_tournament_phase` else **"Play Until End of Season"**
(`templates/seasons/dashboard.html:40`; same in the league dashboard template).

**Split rule:** when the NEXT tournament phase after the current RR phase is —
- the **FINAL** phase (last ordinal) ⇒ label **"Until Playoffs"**;
- **mid-season** (not last ordinal) ⇒ label **"Until Tournament"**.

Add minimal context: a `following_tournament_is_final: bool` (or the resolved label string) to
the dashboard context, computed in `_playoff_cursor_keys` (or alongside it). Drive the template
relabel from it:
- `has_following_tournament_phase` gates whether the terminal button shows the tournament-aware
  label at all (unchanged).
- When it does, the new bool/string picks "Until Playoffs" (final) vs "Until Tournament"
  (mid-season).

**Locked: the playoff button-group DOM ids + the `play_until_end` action are UNCHANGED** — only
the visible label text varies. Touch both `templates/seasons/dashboard.html` and
`templates/leagues/dashboard.html` (the league dashboard renders the same play dropdown).

The Part2c-1 playoff keys (`playoff_phase_active` / `playoff_tournament_id` /
`playoff_completed` / `has_following_tournament_phase`) and the Play Single Round / Play Playoffs
/ View bracket controls are otherwise UNCHANGED — they already key off `current_phase()` being a
built tournament phase, so a mid-season bracket drains through them with **no structural change**.

`following_tournament_is_final` derivation: find the next `tournament` phase at an ordinal >
current RR phase's ordinal; it is final iff its ordinal == `ordered_phases()[-1].ordinal`.

---

## 8. Template — phase mode `<select>` (`templates/leagues/create.html`)

A tournament composer row gains a **mode `<select>`** with locked DOM id
**`league-create-phase-mode-{i}`** (`{i}` = the existing 0-based JS `rowSeq` index used for
`league-create-phase-row-{i}` / `-phase-type-{i}` / `-phase-format-{i}`).

- Options: `standings`, `strength`, `unseeded` selectable; **`random_draw` as a DISABLED
  "coming soon"** option (mirrors the `member_night` deferral pattern).
- Shown for `tournament` rows only (hidden for `round_robin` rows, mirroring how
  `phase-format-select` is shown for RR rows only via `applyType()`).
- **`serialize()`** emits `tournament:<mode>` for a tournament row (RR rows still emit
  `round_robin:<format>`). Concretely, in the `else` branch of `serialize()` read the row's
  `.phase-mode-select` value (default `"standings"`) and push `"tournament:" + mode`.
- The per-tournament `phase-tournament-pending` note (class substring) is PRESERVED.

**All existing Part2b/c-3a DOM ids unchanged:** `league-create-phases-composer`,
`league-create-add-block`, `league-create-phases`, `league-create-phase-row-{i}`,
`league-create-phase-type-{i}`, `league-create-phase-format-{i}`,
`league-create-member-night-note`, class `phase-tournament-pending`.

---

## 9. Test boundary

### Pure-unit (`matches/tests/test_phase_composer.py`) — parser + guards + ValueErrors
- `tournament` bare ⇒ `tournament_mode == "standings"`.
- `tournament:strength` / `tournament:unseeded` parse and stamp the mode.
- `tournament:standings` parses to `standings`.
- `tournament:random_draw` ⇒ `ValueError("unknown tournament_mode: 'random_draw'")`.
- `tournament:bogus` ⇒ `ValueError("unknown tournament_mode: 'bogus'")`.
- `member_night` still ⇒ `f"unknown phase type: {token!r}"`.
- All preserved ValueError strings still fire (`malformed`, `unknown schedule_format`,
  `composition must contain at least one round-robin phase`).
- Compose guard relaxation: `"tournament:standings,round_robin"` ⇒ raises
  `"a tournament phase requires a preceding round-robin phase"`; `"tournament:strength,
  round_robin"` and `"tournament:unseeded,round_robin"` ⇒ NO raise (may be first);
  `"round_robin,tournament:standings"` ⇒ NO raise. A mid-season `standings`
  (`"round_robin,tournament:standings,round_robin"`) ⇒ NO raise.
- `TestNoDjangoImportsLeaked` still passes (parser stays Django-free).

### Pure-unit — seed vector (where applicable)
- For `unseeded`: assert the seed order is a **valid permutation of all team ids** with dense
  seeds **1..N** — **NOT** an exact order (it's a fresh-RNG shuffle).
- For `strength`: assert order = `default_seed_order` of `(team_id, mean rating)` (DESC by mean,
  ASC by id tiebreak) — deterministic given fixed ratings.

### DB TestCase (`matches/tests/test_season_playoff.py` or `test_season_phase.py`)
- **Build triggers at `start_season`**: a first-phase `strength`/`unseeded` tournament builds
  the instant the Season activates (phase `tournament_id` set, `Tournament.state == "active"`,
  N `TournamentParticipant`s, seeds dense 1..N).
- **Build triggers post-round** (mid-season after RR): when the preceding RR phase completes,
  the tournament phase builds (existing hook).
- **Barrier excludes post-barrier RR fixtures**: `playable_fixtures_by_phase()` excludes RR
  phases at/after the incomplete tournament phase ordinal; once the tournament completes, later
  RR phases become playable. Assert on the SET of excluded fixtures (phase ordinals present /
  absent), NOT raw point totals.
- **Mid-season `standings` seeds from cumulative standings-so-far** (rank order of
  `_final_standings_for_phase(prior)`).
- **Champion still from final phase** (`complete_if_finished` / `_stamp_champion_for_final_phase`
  UNCHANGED — assert champion id / `state="completed"`).
- **Dashboard label split**: final-phase tournament ⇒ "Until Playoffs"; mid-season tournament ⇒
  "Until Tournament"; button DOM id + `play_until_end` action unchanged.

**Assertion discipline:** assert schema-level outcomes (participant seeds, excluded fixtures,
state flips, champion id, dashboard label string) — **NEVER raw simulated point totals**
(tournament sims are non-deterministic).

---

## 10. Explicit scope-out (LOCKED — DO NOT build here)
- `random_draw` build **deferred** (parser rejects it with a ValueError; the template offers it
  as a disabled "coming soon" option only).
- **No migration** (`tournament_mode` already exists from Part2c-3b).
- **No Score Calibration re-baseline** (tournament sims already non-deterministic; no simulation
  mechanics change).
- Pure helpers (`select_play_fixtures`, `find_next_matchday`, `find_next_fixture`,
  `round_progress`, `compute_leaders`) + `scheduled_fixtures*` left UNTOUCHED.
- **Extend [ADR-0023] (no new ADR).** No new CONTEXT.md term (the `tournament_mode` vocabulary
  already lives in the Season phase glossary entry from Part2c-3b).
