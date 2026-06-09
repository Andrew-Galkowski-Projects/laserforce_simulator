# LG-02-Part2c-3a — double round-robin regular-season format — SEAM CONTRACT

## 1. Scope

First sub-slice of the re-sliced LG-02-Part2c-3. Lands the **first alternative
regular-season `schedule_format`** — **`double_round_robin`** — as a single
`SeasonPhase` format, wiring the Part2b dormant per-phase `schedule_format`
column **end-to-end** for the first time. A `double_round_robin` phase has every
enrolled pair meet **twice within one phase** as **two distinct Matches**,
discriminated by a new `Match.leg` field. `generate_schedule` gains the format
and emits the leg-1 fixtures concatenated with the same fixtures re-emitted as
leg 2 on a sequential matchday calendar; `simulate_scheduled_round` gains a
`leg` find-or-create dimension; the per-phase RR completion / play-loop / pure
helpers / FLAT dashboard overlays gain `leg`; and the create-League composer
serializes a per-token `type:format` wire format so a row can pick
`double_round_robin`. `single_round_robin`, all legacy phase-less Seasons, and
all tournament Matches stay `leg=1` ⇒ **byte-identical to today**. No simulator
mechanics change, no RNG change, no tournament-engine change, no Score
Calibration re-baseline (extend ADR-0023's consequences — no new ADR).

---

## 2. Locked names (NEW / CHANGED symbols)

### 2.1 `Match.leg` field — `matches/models.py` (on `Match`, after `season_phase`)

```python
leg = models.PositiveSmallIntegerField(default=1)
```

- Discriminates the two legs of a `double_round_robin` pairing. `single_round_robin`,
  legacy, and tournament/playoff Matches stay `leg=1` (the default) ⇒ byte-identical.
- No `db_index`, no choices, no `null`/`blank` (defaults to `1`).

### 2.2 Migration — `matches/migrations/0044_match_leg.py`

- **Dependency:** `("matches", "0043_match_season_phase")` (verified: `0043` is the
  latest matches migration).
- **Single op:** `migrations.AddField(model_name="match", name="leg",
  field=models.PositiveSmallIntegerField(default=1))`.
- **NO `RunPython`, NO `RunSQL`, NO backfill, NO data migration** — ADR-0004
  disposable-data posture (same as `0029` / `0041` / `0042` / `0043`). Existing rows
  take the `default=1`.

### 2.3 `ScheduleFixture.leg` — `matches/schedule_generator.py`

`leg` is **appended LAST** with a default, so existing keyword + positional
constructions stay valid:

```python
@dataclass(frozen=True)
class ScheduleFixture:
    matchday: int        # 1-based
    round_number: int    # 1 or 2
    team_a_id: int       # min of the pair
    team_b_id: int       # max of the pair
    leg: int = 1         # NEW — 1 (single-RR / leg 1) or 2 (double-RR leg 2)
```

- Constructed **by keyword everywhere** (`ScheduleFixture(matchday=…, round_number=…,
  team_a_id=…, team_b_id=…, leg=…)`).
- `ScheduleFixture(matchday=m, round_number=r, team_a_id=a, team_b_id=b)` (no `leg`)
  ⇒ `leg == 1` ⇒ equality-identical to every existing test construction.
- **NOTE — existing copy site:** `Season.scheduled_fixtures_by_phase`
  (`matches/models.py` ~L1336) re-constructs each fixture to apply the matchday
  offset. That re-construction **MUST carry `leg=f.leg`** through, else leg-2
  fixtures collapse to leg-1 and the find-or-create key + completion break.

### 2.4 `SCHEDULE_FORMATS` — `matches/schedule_generator.py`

```python
SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin", "double_round_robin")
```

### 2.5 `generate_schedule` `double_round_robin` behaviour (byte-exact)

`generate_schedule(team_ids, "double_round_robin")` returns:

1. The **`single_round_robin` fixture list** for `team_ids`, every fixture carrying
   **`leg=1`**, matchdays `1..2*(N-1)` (the existing circle-method output —
   round_number 1 on matchdays `1..N-1`, round_number 2 on `N..2*(N-1)`).
2. **CONCATENATED** with the **same** single-RR fixtures re-emitted with **`leg=2`**
   and **matchday offset by `2*(N-1)`** (each leg-1 fixture's `matchday + 2*(N-1)`,
   same `round_number` / `team_a_id` / `team_b_id`).

So leg 2 plays **sequentially after** leg 1: one monotonic `1..4*(N-1)` matchday
calendar within the phase. The module stays **Django-free** (frozen
`dataclasses` / `typing` allowlist; no new import). `N` here is the bye-padded
even slot count (the existing `n` variable: `len(slots)`), so the offset is the
existing `2 * (n - 1)`.

- `single_round_robin` path **unchanged byte-for-byte** — its fixtures already carry
  the new `leg=1` default, output identical to today.
- The existing `ValueError` on `schedule_format not in SCHEDULE_FORMATS` and on
  `len(team_ids) < 2` is unchanged; `double_round_robin` is now an accepted format.
- **Final sort** for `double_round_robin`: sort the full concatenated list by
  `(matchday, team_a_id)` (same key the single-RR path uses; leg is implied by
  matchday since the two legs occupy disjoint contiguous matchday ranges).

### 2.6 `simulate_scheduled_round` new signature — `matches/simulation/entrypoints.py`

```python
@transaction.atomic
def simulate_scheduled_round(
    self, season, team_a, team_b, round_number,
    *, arena_map=None, season_phase=None, leg: int = 1,
) -> "GameRound":
```

- `leg` is **keyword-only, appended LAST, default `1`** ⇒ byte-identical to today for
  every existing caller (sandbox, season play, tests passing no `leg`).
- **Find-or-create key becomes** `(season, season_phase, frozenset({team_a_id,
  team_b_id}), leg)`. Both Side-agnostic `.filter(...)` lookups gain `leg=leg`, and
  the round-1 `Match.objects.create(...)` gains `leg=leg`:

```python
match = (
    Match.objects.filter(season=season, season_phase=season_phase, leg=leg)
    .filter(team_red=team_a, team_blue=team_b).first()
) or (
    Match.objects.filter(season=season, season_phase=season_phase, leg=leg)
    .filter(team_red=team_b, team_blue=team_a).first()
)
...
match = Match.objects.create(
    season=season, season_phase=season_phase, leg=leg,
    team_red=team_a, team_blue=team_b, is_completed=False,
)
```

- The `season_phase.pk is None ⇒ season_phase = None` coercion, the round_number
  guards, the per-round colour swap, the post-round hooks
  (`activate_pending_tournament_phase()` then `complete_if_finished()`), and the
  RNG draw are **UNCHANGED**. `leg=1` (default) collapses the key to
  `(season, season_phase, frozenset, 1)` — byte-identical to today's
  `(season, season_phase, frozenset)` plus a constant.

### 2.7 `Season` played-key threading — `matches/models.py`

- **`_is_finished`** (whole-season RR check, legacy/implicit-phase path) — its
  played-keys set gains `leg`, derived from `gr.match.leg`:
  `(frozenset({team ids}), round_number, leg)`; the per-fixture lookup key it
  compares against gains `fixture.leg`. (For a phase-less / single-RR Season every
  `leg == 1` ⇒ byte-identical result.)
- **`_rr_phase_complete`** (per-phase RR completion, `match__season_phase=phase`
  scoped) — same change: played-keys become `(frozenset({team ids}), round_number,
  gr.match.leg)` and the fixture compare key becomes `(frozenset({fixture.team_a_id,
  fixture.team_b_id}), fixture.round_number, fixture.leg)`. This is what makes a
  double-RR phase require **both** legs of every pairing before the phase completes.
- **`_final_standings_for_phase`** — **UNCHANGED**. Standings stay cumulative
  whole-season; both legs' Matches already feed
  `Match.objects.filter(season=self, is_completed=True)` (a double-RR pairing is two
  distinct Matches, each a row in this query, so both count automatically).
- `_fixtures_for_phase` / `scheduled_fixtures_by_phase` / `scheduled_fixtures` need
  no leg-specific edit **beyond carrying `leg=f.leg` through the offset
  re-construction** (§2.3 NOTE) — they already iterate whatever `generate_schedule`
  emits.

### 2.8 `season_dashboard.py` play-loop pure helpers (Django-free, plain ints)

`select_play_fixtures` and `find_next_matchday` per-fixture key gains `leg`:

- **OLD key:** `(phase_id, frozenset({team_a_id, team_b_id}), round_number)`.
- **NEW key:** `(phase_id, frozenset({team_a_id, team_b_id}), round_number, leg)`
  where `leg = fixture.leg`.
- **`played_keys` arg shape:** `set[tuple[int | None, frozenset[int], int, int]]`.
- **`fixtures` arg shape UNCHANGED:** `list[tuple[int | None, ScheduleFixture]]`
  (the `ScheduleFixture` now carries `.leg`).
- Module stays **Django-free** — `leg` is a plain int read off the duck-typed
  fixture; no new import; `TestNoDjangoImportsLeaked` must keep passing.

### 2.9 `season_dashboard.py` FLAT dashboard pure helpers (WIDER RIPPLE)

**REQUIRED** because a double-RR phase now contains the same `(pair, round_number)`
**twice** in the flat list — without `leg` the second leg would be treated as
already-played:

- **`find_next_fixture`** and **`round_progress`** 2-tuple key:
  - **OLD:** `(frozenset({team_a_id, team_b_id}), round_number)`.
  - **NEW:** `(frozenset({team_a_id, team_b_id}), round_number, leg)`.
- `compute_leaders` / `LeaderRow` unchanged. Module stays Django-free.

### 2.10 Play-loop wiring sites (build `played_keys` with leg, pass `leg=`)

- **`play_season_task`** — `matches/tasks.py` (~L199 played_keys, ~L226 loop):
  - `played_keys` entries gain `gr.match.leg`:
    `(gr.match.season_phase_id, frozenset({gr.match.team_red_id,
    gr.match.team_blue_id}), gr.round_number, gr.match.leg)`.
  - The per-fixture call passes `leg=fixture.leg`:
    `BatchSimulator().simulate_scheduled_round(season, team_a, team_b,
    fixture.round_number, arena_map=arena_map,
    season_phase=phase_by_id.get(phase_id), leg=fixture.leg)`.
- **`play_week`** — `matches/league_views.py` (~L1617 played_keys, ~L1641 loop):
  same two changes (4-tuple `played_keys` with `gr.match.leg`; `leg=fixture.leg`
  into `simulate_scheduled_round`).
- **`play_two_months` / `play_until_end`** — UNCHANGED (they enqueue
  `play_season_task`; the `max_matchdays` window carries through unchanged).

### 2.11 FLAT 2-tuple `played` overlay sites (build `(frozenset, round, leg)`)

These three view/context sites build a FLAT `played_keys` / `played_by_key` set
that feeds `find_next_fixture` / `round_progress` (or an inline equivalent over
`scheduled_fixtures()`); each gains `leg` so a double-RR phase's two legs are
distinct:

- **`_build_dashboard_context`** — `matches/league_views.py` (~L674–L688): the
  `played_keys` set entries become `(frozenset({match.team_red_id,
  match.team_blue_id}), game_round.round_number, match.leg)`; the `fixture` compare
  key feeding `round_progress` / `find_next_fixture` gains `fixture.leg`.
- **`season_schedule`** — `matches/league_views.py` (~L387–L405): the
  `played_by_key` dict key + the per-fixture lookup key gain `leg`
  (`game_round.match.leg` / `fixture.leg`).
- **`team_schedule`** — `matches/league_views.py` (~L1861–L1918): the `played_keys`
  set + `fixture_by_key` dict + the per-fixture/per-round lookup keys gain `leg`
  (`gr.match.leg` / `fixture.leg`).

Each of these reads `match.leg` off the `GameRound.match` (the views already
`select_related("match")` or have the Match in scope).

### 2.12 Composer — `matches/phase_composer.py` per-token `type[:format]` wire format

- **Wire format extends** from comma-separated phase-**TYPE** tokens to
  comma-separated **`type[:format]`** tokens, e.g.
  `"round_robin:double_round_robin,tournament"`.
- A **bare `round_robin`** token (no colon) defaults to **`single_round_robin`**
  (Part2b backward-compat — existing serialized values still parse identically).
- A **`tournament`** token carries **no format** ⇒ `PhaseSpec.schedule_format=None`
  (a `tournament:anything` token is malformed — tournament takes no format).
- `parse_phase_composition` reads the per-token format into
  `PhaseSpec.schedule_format` for `round_robin` tokens.
- `PhaseSpec` shape UNCHANGED (`ordinal, phase_type, schedule_format`).
- **NEW pure `ValueError` (byte-exact, LOCKED):** an unknown/unsupported per-phase
  schedule_format token (valid set: `{"single_round_robin", "double_round_robin"}`):

  ```
  f"unknown schedule_format: {fmt!r}"
  ```

  where `fmt` is the offending format substring. Plain `ValueError` (module stays
  Django-free; the form re-wraps as a `forms.ValidationError` on `phases`).
- Existing `ValueError` strings are preserved verbatim:
  - empty token ⇒ `"malformed phase composition"`
  - unknown phase type ⇒ `f"unknown phase type: {token!r}"`
  - zero round_robin ⇒ `"composition must contain at least one round-robin phase"`
  - tournament-before-RR ⇒ `"a tournament phase requires a preceding round-robin phase"`
- **Parse order within a non-empty token:** split on the **first** `:` only into
  `(type_part, format_part)`; reject empty `type_part` (malformed); validate
  `type_part` against `{round_robin, tournament}` (unknown-type `ValueError`); for
  `round_robin`, resolve `schedule_format = format_part or "single_round_robin"`
  then validate it against the valid format set (new unknown-format `ValueError`);
  for `tournament`, `schedule_format = None` (and a present `format_part` ⇒
  malformed). The empty-`raw` short-circuit (single default RR phase) and the
  zero-RR / tournament-before-RR whole-composition checks fire after, unchanged.
- The form's `clean()` call site is unchanged: it still passes
  `parse_phase_composition(cleaned_data.get("phases", "") or "",
  season_schedule_format=...)` — for a `round_robin` token **with** an explicit
  format the per-token format wins; `season_schedule_format` remains the fallback
  for a bare `round_robin` token (i.e. its semantics stay
  "bare round_robin ⇒ single_round_robin" since `season_schedule_format` is the
  locked `"single_round_robin"`).

### 2.13 Template — `templates/leagues/create.html` composer

- The per-phase `league-create-phase-format-{i}` `<select>` (currently
  single-option `single_round_robin`) **gains a `double_round_robin` option**
  (`value="double_round_robin"`, label e.g. `"Double round-robin"`).
- The submit-time `serialize()` JS **serializes each RR row as `type:format`** into
  the hidden `#league-create-phases` input — for a `round_robin` row, emit
  `round_robin:` + the row's `league-create-phase-format-{i}` value; for a
  `tournament` row, emit the bare `tournament` token (no colon). Comma-join the
  ordered tokens (so the default single RR row submits as
  `round_robin:single_round_robin`).
- **All Part2b DOM ids unchanged:** `league-create-phases-composer`,
  `league-create-add-block`, `league-create-phases` (hidden input),
  `league-create-phase-row-{i}`, `league-create-phase-type-{i}`,
  `league-create-phase-format-{i}`, `league-create-member-night-note`, class
  substring `phase-tournament-pending`.

### 2.14 `next_season` carry-forward — NO-OP

`next_season` (`matches/league_views.py`) already copies each source phase's
`schedule_format` **verbatim** into the new draft Season's phases (Part2b
carry-forward loop). A `double_round_robin` phase therefore reproduces
automatically with no edit. **State this as a no-op — make no change here.**

### 2.15 Admin — NO CHANGE

`SeasonPhaseAdmin.list_display` already includes `schedule_format` (Part2b). No
`Match.leg` admin surfacing required (auto-appears on the default `Match` change
form if one is registered; not load-bearing).

---

## 3. Key-tuple table (OLD → NEW)

| Site | Module / fn | OLD key | NEW key |
|---|---|---|---|
| Find-or-create (sim) | `entrypoints.simulate_scheduled_round` | `(season, season_phase, frozenset({teams}))` | `(season, season_phase, frozenset({teams}), leg)` |
| Whole-season RR done | `Season._is_finished` | `(frozenset({teams}), round_number)` | `(frozenset({teams}), round_number, leg)` |
| Per-phase RR done | `Season._rr_phase_complete` | `(frozenset({teams}), round_number)` | `(frozenset({teams}), round_number, leg)` |
| Play-loop select | `season_dashboard.select_play_fixtures` | `(phase_id, frozenset, round_number)` | `(phase_id, frozenset, round_number, leg)` |
| Play-loop next-day | `season_dashboard.find_next_matchday` | `(phase_id, frozenset, round_number)` | `(phase_id, frozenset, round_number, leg)` |
| FLAT next fixture | `season_dashboard.find_next_fixture` | `(frozenset, round_number)` | `(frozenset, round_number, leg)` |
| FLAT progress | `season_dashboard.round_progress` | `(frozenset, round_number)` | `(frozenset, round_number, leg)` |
| FLAT overlay (dashboard) | `league_views._build_dashboard_context` | `(frozenset, round_number)` | `(frozenset, round_number, leg)` |
| FLAT overlay (schedule) | `league_views.season_schedule` | `(frozenset, round_number)` | `(frozenset, round_number, leg)` |
| FLAT overlay (team schedule) | `league_views.team_schedule` | `(frozenset, round_number)` | `(frozenset, round_number, leg)` |
| Play-loop played (task) | `tasks.play_season_task` | `(season_phase_id, frozenset, round_number)` | `(season_phase_id, frozenset, round_number, leg)` |
| Play-loop played (view) | `league_views.play_week` | `(season_phase_id, frozenset, round_number)` | `(season_phase_id, frozenset, round_number, leg)` |

`leg` is sourced from `gr.match.leg` on the persisted side and `fixture.leg` on
the schedule side (both plain `int`, default `1`).

---

## 4. Test boundary

Verified existing test files (extend these — all confirmed present):
`matches/tests/test_schedule_generator.py`, `test_phase_composer.py`,
`test_league_play.py`, `test_season_multi_rr.py`, `test_league_create.py`,
`test_season_dashboard_logic.py`. (`test_season_phase.py` also exists if a
model-level field test is wanted.)

**Pure-unit (assert against, no DB):**
- `test_schedule_generator.py` — `generate_schedule(team_ids,
  "double_round_robin")`: total fixture count = `2 ×` the single-RR count;
  matchdays span `1..4*(N-1)` monotonic; leg-1 fixtures carry `leg=1` and occupy
  matchdays `1..2*(N-1)`; leg-2 fixtures carry `leg=2`, are the SAME
  `(round_number, team_a_id, team_b_id)` set as leg 1, and occupy matchdays
  `2*(N-1)+1 .. 4*(N-1)`; `SCHEDULE_FORMATS == ("single_round_robin",
  "double_round_robin")`; `single_round_robin` output byte-identical to today
  (every fixture `leg == 1`); `ScheduleFixture(...)` without `leg` defaults to 1.
- `test_phase_composer.py` — per-token wire format:
  `"round_robin:double_round_robin"` ⇒ spec `schedule_format=="double_round_robin"`;
  bare `"round_robin"` ⇒ `"single_round_robin"` (backward-compat);
  `"round_robin:single_round_robin,tournament"` ⇒ 2 specs, tournament
  `schedule_format=None`; an unknown format ⇒ the new
  `ValueError("unknown schedule_format: …")`; existing zero-RR / unknown-type /
  malformed / tournament-before-RR errors still fire; purity check still green.
- `test_season_dashboard_logic.py` — `select_play_fixtures` /
  `find_next_matchday` 4-tuple key + `find_next_fixture` / `round_progress`
  3-tuple key, exercised via **locally-stubbed** frozen `@dataclass` fixtures
  carrying `.leg` (keeping the Django-free allowlist — no
  `matches.schedule_generator` import); assert that two same-pair-same-round
  fixtures differing only by `leg` are treated as DISTINCT (one played, one not);
  `TestNoDjangoImportsLeaked` still passes.

**DB-level (Django `TestCase`):**
- `test_season_multi_rr.py` / `test_league_play.py` — a `double_round_robin`
  phase: `simulate_scheduled_round` with `leg=1` then `leg=2` for the same pair
  creates **two distinct Matches** (`leg` differs, both `season`/`season_phase`
  equal); `_rr_phase_complete` returns `False` until **both** legs of every
  pairing are played and `True` once both land; `play_season_task` /
  `play_week` drain both legs (small-N seeded sim); cumulative standings
  (`_final_standings_for_phase`) count both legs' Matches.
- `test_league_create.py` — a composer POST with a `round_robin:double_round_robin`
  row persists a `SeasonPhase` with `schedule_format="double_round_robin"`; a bare
  composer (default row) still persists `single_round_robin`; an
  unknown-format token is rejected at the form layer leaving zero rows.

**INTERNAL / not asserted:** exact simulated point totals (sims are
non-deterministic where tournament-adjacent; RR sims byte-identical but tests
assert schema-level outcomes — Match counts, `leg` values, completion flags,
standings ORDER — never raw points). The matchday-offset arithmetic and the
fixture sort order are asserted via the generated list, not via the inner
circle-method internals.

---

## 5. Backward-compat invariants ("stays byte-identical")

1. **`single_round_robin`** — every fixture `leg=1`; `generate_schedule(...,
   "single_round_robin")` output identical to today (the `leg=1` default is the
   only addition, invisible to existing equality on the four prior fields when
   `leg` is also defaulted).
2. **Legacy / phase-less Season** — find-or-create key
   `(season, None, frozenset, 1)` collapses to today's `(season, None,
   frozenset)` plus a constant `leg=1`; `_is_finished` played-keys all `leg=1`.
3. **Tournament / playoff Matches** — `simulate_match` /
   `simulate_match(match_type="tournament")` never set `leg` ⇒ default `1`;
   `season=NULL, season_phase=NULL` unchanged.
4. **Bare `round_robin` wire token** ⇒ `single_round_robin` (existing serialized
   Part2b values parse unchanged).
5. **`ScheduleFixture(...)`** without `leg` ⇒ `leg == 1`, equality-identical to
   every existing test construction and to the offset re-construction once it
   carries `leg=f.leg`.

---

## 6. Migration (restated)

- **Filename:** `matches/migrations/0044_match_leg.py`.
- **Dependency:** `0043_match_season_phase`.
- **Operation:** single `AddField(Match.leg,
  PositiveSmallIntegerField(default=1))`.
- **No `RunPython`, no backfill** (ADR-0004).

---

## 7. Out of scope (other LG-02-Part2c-3 sub-slices — DO NOT build here)

- Per-phase **seeding-mode** field (season-ending Standings-seeded vs mid-season
  strength-/un-seeded).
- **Mid-season tournaments** (a tournament phase between two RR phases).
- **Per-tournament-block config** (format / `team_assembly` / seeding / top-N cut).
- **Non-single-elim embeds** (double-elim / RR / Swiss / RR→DE as a finals stage).
- **Season-linked playoff Match history** surface.
- **Weekly playoff pacing.**

CONTEXT.md note: the **Matchday** / **Season phase** / schedule-format vocabulary
may want a one-line touch for `double_round_robin` — the Docs agent handles it;
this contract only NOTES it.
