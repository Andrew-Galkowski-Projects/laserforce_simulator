# Shot Resolver Seam Contract — Five-Site Consolidation

**Status:** LOCKED. The Shot module is the first of five sequential
deepening opportunities surfaced by `/improve-codebase-architecture`
(2026-05-27). Names below are frozen. If reality contradicts a name here,
STOP and flag; do not silently drift.

Branch: `shot-resolver-consolidation` (cut from `main` 2026-05-27).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

Prior art: this is the first refactor of the **shallow → deep** type
(per `improve-codebase-architecture`); precedent for pure-helper
extracts is `sim_helpers/highlights.py` (RV-02) and
`sim_helpers/score_calculator.py`.

---

## 0. Resolved decisions (DO NOT re-open)

These are baked into the contract. Re-litigating them belongs in a
follow-up grilling session, not in implementation:

- **Wide Shot.** `resolve_shot` owns hit roll + mutate + emit + Down +
  scheduling of follow-ups and reactions. The five (six with Overwatch)
  call sites become one-line dispatches. Narrow Shot was explicitly
  rejected — the scheduling tails were the main duplication cost.
- **Drift accepted.** `resolve_shot` interleaves roll+mutate+react-roll
  per-attempt in one pass; seeded games **differ from pre-refactor**.
  Folds into the **already-pending post-MOVE-01 Score Calibration
  re-baseline** — no new re-baseline obligation. Internal SIM-07/SIM-08
  contract (same seed + Orientation + rosters + map ⇒ identical game,
  serial == parallel, faithful Replay) holds in form.
- **`RoundContext` dataclass.** Per-round mutable state bundled into one
  struct threaded through `_simulate_round`. Replaces the
  `self._event_log` / `self._pending_nukes` static→instance hack
  introduced by RV-02. Kwarg-passing rejected as too noisy.
- **`record_down` lifts to `sim_helpers/down.py`** as a pure function.
  The **Down** is a domain term in its own right (CONTEXT.md, distinct
  from Hit / Tag / Elimination); shot resolution is its primary caller
  but the missile-completion path and nuke-completion path also down
  players. Single chokepoint, structurally enforced.
- **Hide-50%-miss roll applies uniformly across all four `kind`s.** The
  current asymmetry (initial-tag only) is a latent inconsistency; under
  drift it costs nothing to fix.
- **Ammo-shot-decrement asymmetry fixed.** Initial-tag hit/miss
  branches currently decrement `final_shots` unconditionally even for
  Ammo; the four other sites + `miss_hid` skip the decrement for Ammo.
  Treated as a latent bug — Ammo never decrements `final_shots`
  uniformly inside `resolve_shot`. Flag in PR description.
- **`ShotOutcome` returned, not `None`.** `resolve_shot` returns
  `ShotOutcome(hit: bool, downed: bool, eliminated: bool)` for tests
  and (rarely) caller dispatch. The four `kind`s of the resolver are
  fully self-contained; callers ignore the return in production.
- **No CONTEXT.md edit.** Every term (Shot, Hit, Tag, Down,
  Elimination, Follow-up shot, Reaction shot, Overwatch shot) already
  exists. The `kind` strings are code labels, not new domain terms.
- **No ADR.** Refactor consolidates duplication; no load-bearing
  decision a future architecture review would re-suggest reverting.
- **No new migration.** No model field change. `PlayerState` /
  `PlayerRoundState` / `GameEvent` schemas unchanged.

---

## 1. File layout

### `sim_helpers/round_context.py` (NEW)

Pure Python, no Django imports. Zero-dependency module (mirrors
`time_constants.py` discipline).

```python
from dataclasses import dataclass
from typing import Optional

@dataclass
class RoundContext:
    """Per-round mutable state bundle.

    Built once in BatchSimulator._simulate_round, threaded into every
    sim_helpers function that needs to emit events, schedule pending
    follow-ups / reactions, mutate the medic-under-fire / memory
    state, or look up the live alive set. Replaces the static→instance
    self._event_log / self._pending_nukes stash from RV-02.
    """
    event_log: Optional[list]              # per-round event-dict buffer; None ⇒ batch path
    pending_nukes: list                    # PendingNuke queue; read by record_down (RV-02)
    pending_followups: list                # PendingFollowup queue; resolve_shot appends
    pending_reactions: list                # PendingReaction queue; resolve_shot appends
    all_alive: list                        # live PlayerState list; medic-under-fire + memory
    movement_ctx: object                   # MapContext | None; elevation, LoS, base_in_range
```

**Construction site:** `BatchSimulator._simulate_round`, immediately
after the four pending-queue lists are initialised.

**Threading:** passed through `_resolve_tag_attempts`,
`_collect_overwatch_attempts` (read-only), and the two queue-drain
branches in `_simulate_round`.

### `sim_helpers/down.py` (NEW)

Pure Python, no Django imports.

```python
from .round_context import RoundContext

def record_down(player, tick: int, ctx: RoundContext) -> None:
    """Stamp a life-loss tick at the single chokepoint.

    Replaces BatchSimulator._record_down. Pure function; the
    static→instance hack from RV-02 is structurally unnecessary now
    that the event_log and pending_nukes ride on ctx.

    Behaviour (byte-identical to BatchSimulator._record_down):
      1. RV-02 medic-reset chain: increment down_chain_count
         (fresh down ⇒ 1; re-down before recovery ⇒ +1); emit
         'medic_reset' event once when a Medic reaches 2.
      2. Stamp last_downed_time = tick.
      3. Clear _path_cache (MOVE-02 — knocked off committed route).
      4. Clear is_holding (MOVE-03 — Down ends Overwatch).
      5. Clear _committed_goal iff from_action_driven (MOVE-04).
      6. RV-02 nuke_cancelled: for a Commander, scan
         ctx.pending_nukes; emit 'nuke_cancelled' once per pending
         nuke with cancel_logged=False, set cancel_logged=True,
         leave nuke in queue (MECH-05 reaction/drain unchanged).

    Does NOT touch lives or shields — those mutations differ per
    call site (tag / follow-up / reaction / missile / nuke) and
    happen at the caller.
    """
```

**Callers (3 modules):**
- `sim_helpers/shot.py::resolve_shot` (the primary caller)
- `simulation.py::BatchSimulator._complete_missile`
- `simulation.py::BatchSimulator._complete_nuke`

The legacy `BatchSimulator._record_down` instance method is **removed**
in the same PR; the three callers above are converted.

### `sim_helpers/shot.py` (NEW)

Pure Python, no Django imports.

```python
import random
from dataclasses import dataclass
from .combat import _elevation_hit_modifier
from .down import record_down
from .pending_events import PendingFollowup, PendingReaction
from .round_context import RoundContext

SHOT_KIND_INITIAL    = "initial"
SHOT_KIND_FOLLOW_UP  = "follow_up"
SHOT_KIND_REACTION   = "reaction"
SHOT_KIND_OVERWATCH  = "overwatch"

_VALID_KINDS = frozenset({
    SHOT_KIND_INITIAL,
    SHOT_KIND_FOLLOW_UP,
    SHOT_KIND_REACTION,
    SHOT_KIND_OVERWATCH,
})

@dataclass(frozen=True)
class ShotOutcome:
    hit: bool          # True iff the shot landed (Tag)
    downed: bool       # True iff the hit took shields to 0 (Down)
    eliminated: bool   # True iff the Down was the defender's last life
    # 'invalid' / 'miss_hid' / plain 'miss' are all hit=False, downed=False.

def resolve_shot(
    attacker,
    defender,
    tick: int,
    *,
    kind: str,
    ctx: RoundContext,
    chain_depth: int = 0,    # only meaningful for SHOT_KIND_FOLLOW_UP
) -> ShotOutcome:
    """Resolve one Shot end-to-end. The single source of the
    Shot → Hit → Tag → Down → Elimination ladder.

    Phases (one-pass per call — interleaves RNG draws):
      1. Validity gate: attacker.final_shots > 0 (or role == 'ammo'),
         defender.final_lives > 0. Else return ShotOutcome(False, …).
      2. Hide-miss roll (uniform across all kinds — see §0): if
         defender.is_hiding, random.random() > 0.5 → miss_hid; emit
         'miss' event with metadata={"reason": "hiding"}; decrement
         attacker.final_shots (unless attacker.role == 'ammo');
         stamp last_shot_time; return ShotOutcome(False, …).
      3. Hit roll: random.randint(1, 100) <
         clamp(int((70 + attacker.accuracy - defender.survival)
         * elev_mod * attacker.stamina_hit_modifier), 10, 95).
      4. Counter mutations (per kind):
           - INITIAL: no kind-specific counter
           - FOLLOW_UP: attacker.follow_up_shots += 1
           - REACTION: attacker.reaction_shots += 1
           - OVERWATCH: no kind-specific counter (overwatch is a
             flag on the tag event, not a counter — see §0 of
             ADR-0009)
      5. Decrement attacker.final_shots iff attacker.role != 'ammo'
         (uniform across all kinds — see §0).
      6. Stamp attacker.last_shot_time = tick.
      7. On hit:
           - attacker.tags_made += 1
           - if defender.role == 'medic': attacker.medic_hits += 1
           - if attacker.role != 'heavy':
                 attacker.final_special = min(max_special, +1)
           - attacker.points_scored += 100
           - attacker.last_tagged_id = defender.tag_id
           - defender.times_tagged += 1
           - defender.points_scored -= 20
           - if defender in reset_window: times_tagged_in_reset_window += 1
           - defender.shields = max(0, shields - attacker.shot_power)
           - downed = (defender.shields == 0)
           - on downed:
                 - if defender.role == 'commander' and special_active_until > tick:
                       defender.special_active_until = 0
                 - defender.final_lives = max(0, lives - 1)
                 - record_down(defender, tick, ctx)
                 - defender.shields = defender.max_shields
                 - eliminated = (defender.final_lives <= 0)
                 - on eliminated:
                       - defender.was_eliminated_at = tick
                       - emit 'elimination' event with metadata
                         elimination_action={'tag','follow_up_tag','reaction'}[kind]
                         (OVERWATCH maps to 'tag')
           - emit 'tag' event with kind-appropriate metadata flags:
                 - INITIAL    : (no extra flag)
                 - FOLLOW_UP  : is_follow_up=True, chain=chain_depth
                 - REACTION   : is_reaction=True
                 - OVERWATCH  : overwatch=True
           - medic-under-fire alert (if defender.role == 'medic')
           - memory update + communication broadcast (on hit only)
      8. On miss:
           - attacker.shots_missed += 1
           - emit 'miss' event with same kind metadata as above
      9. Reaction scheduling (any hit or miss, not invalid/miss_hid;
         and not when kind == SHOT_KIND_REACTION — reactions don't
         re-react): defender rolls player_awareness; on success and
         defender has shots/lives, schedule PendingReaction or
         dispatch immediately at cd_ticks == 0.
     10. Follow-up scheduling (on hit AND not downed AND not kind ==
         SHOT_KIND_FOLLOW_UP-with-chain==2 — chain cap 2): defender
         rolls player_awareness (FAILURE triggers follow-up — this
         is the existing semantic); on trigger and attacker has
         shots/lives, schedule PendingFollowup with chain_depth+1 or
         dispatch immediately at cd_ticks == 0.

    Returns ShotOutcome(hit, downed, eliminated).
    """
```

**Internal helpers:** `resolve_shot` calls a private `_emit_tag_event`,
`_emit_miss_event`, `_emit_elimination_event`, `_maybe_schedule_reaction`,
`_maybe_schedule_followup` to keep the function body readable. Those
private helpers stay in `shot.py`; they're not part of the public
contract.

**NOT a method on `BatchSimulator`.** Pure free function so the
sim-helpers Django-free discipline is preserved.

---

## 2. Call-site map (the five — six with Overwatch — sites)

Below is the locked dispatch from `_simulate_round` and `_resolve_tag_attempts`
to `resolve_shot`. The site numbering is the order they appear in
`simulation.py` today.

| # | Site (current location) | New dispatch | `kind` | extras |
|---|------------------------|--------------|--------|--------|
| 1 | `_resolve_tag_attempts`, initial-tag loop | per-attempt `resolve_shot` call | `SHOT_KIND_INITIAL` (or `SHOT_KIND_OVERWATCH` if `a.get("overwatch")`) | `chain_depth=0` |
| 2 | `_resolve_tag_attempts`, immediate follow-up loop | per-attempt `resolve_shot` call | `SHOT_KIND_FOLLOW_UP` | `chain_depth=fu["chain"]` |
| 3 | `_resolve_tag_attempts`, immediate reaction loop | per-attempt `resolve_shot` call | `SHOT_KIND_REACTION` | (none) |
| 4 | `_simulate_round`, `due_rx` queue drain | per-attempt `resolve_shot` call | `SHOT_KIND_REACTION` | (none) |
| 5 | `_simulate_round`, `due_fu` queue drain | per-attempt `resolve_shot` call | `SHOT_KIND_FOLLOW_UP` | `chain_depth=fu.chain_depth` |

**Overwatch** is **NOT** a separate site — Overwatch tag-attempts are
already collected by `_collect_overwatch_attempts` and merged into the
`tag_attempts` list, then resolved through site #1. The `overwatch`
flag on the attempt dict drives the `kind` selection inside the
initial-tag dispatch (see table row 1).

**Immediate vs queued:** sites #2/#3 fire in the SAME tick as the
trigger (rapid-fire scout chain). Sites #4/#5 are deferred until the
cooldown elapses. Both use the same `resolve_shot` body — the
caller's responsibility is to gate "this attempt is eligible to fire
right now" (lives/shots/active checks); `resolve_shot` does its own
validity-gate inside (defensive; the gates are idempotent).

**After-shot chaining inside `resolve_shot`:** the immediate
follow-up "chain immediately in this loop" pattern (current code at
`_resolve_tag_attempts:~L2619-2625`) is replaced by `resolve_shot`
appending to a thread-local "chain this tick" list inside the
`immediate_follow_ups` loop — OR more cleanly, by `resolve_shot`
calling itself recursively at `cd_ticks == 0` (chain bounded at 2,
recursion depth bounded at 2). Implementation chooses; tests pin the
two-deep chain behaviour either way.

---

## 3. Removed code

The following inline blocks in `simulation.py` are **deleted**, not
left as deprecated shims:

- `BatchSimulator._resolve_tag_attempts` — the body from the
  outcomes loop through the immediate-follow-up loop end. The method
  itself is retained as a thin wrapper that iterates `attempts` and
  calls `resolve_shot` (preserves the public per-tick contract).
- `BatchSimulator._record_down` — removed in favour of
  `down.record_down(player, tick, ctx)`. Three callers updated.
- The `due_rx` resolution block in `_simulate_round` (lines
  ~1421-1524) — replaced by a `for rx in due_rx: resolve_shot(
  rx.attacker, rx.defender, second, kind=SHOT_KIND_REACTION, ctx=ctx)`
  loop.
- The `due_fu` resolution block in `_simulate_round` (lines
  ~1526-1653) — replaced by a `for fu in due_fu: resolve_shot(
  fu.attacker, fu.defender, second, kind=SHOT_KIND_FOLLOW_UP,
  ctx=ctx, chain_depth=fu.chain_depth)` loop.

**`self._event_log` / `self._pending_nukes` instance stash** in
`_simulate_round` (`self._event_log = event_log; self._pending_nukes
= pending_nukes`) — removed. Both ride on `ctx` now.

---

## 4. Tests

### `matches/tests/test_shot_resolver.py` (NEW)

Pure-unit tests, no Django ORM, hand-built `PlayerState` instances. A
`make_ctx(event_log=None)` helper builds a `RoundContext` with empty
queues. Each test seeds `random.seed(<int>)` before `resolve_shot`
to make hit/miss rolls deterministic.

Required test classes:

- `TestResolveShotInitial`
  - hit path: counters, event shape, RNG draw order
  - miss path: counters, event shape
  - miss_hid (defender.is_hiding): 50% miss, event reason
  - invalid gate (final_shots == 0): early return, no event
  - invalid gate (defender already eliminated): early return
  - Ammo attacker never decrements final_shots (the new uniform rule)
- `TestResolveShotFollowUp`
  - chain_depth=1 emits chain=1 metadata
  - chain_depth=2 + not-downed defender: no further chain
  - chain_depth=1 + downed defender: chain stops
- `TestResolveShotReaction`
  - reaction never re-reacts (no PendingReaction spawned)
- `TestResolveShotOverwatch`
  - tag event carries overwatch=True
  - elimination event carries elimination_action="tag" (not
    "overwatch")
- `TestResolveShotDownChain`
  - Heavy one-shots: downed=True, eliminated=False if lives>1
  - Heavy one-shots: eliminated=True if lives==1; was_eliminated_at
    stamped
  - Down clears _path_cache, is_holding, action-driven
    _committed_goal
  - Medic re-down within respawn cooldown emits medic_reset event
    (once per chain)
  - Commander down during own nuke fuse emits nuke_cancelled
- `TestRecordDown`
  - All six behaviours from §1 enumerated
  - Pure function: same inputs → same effects, no hidden state

### `matches/tests/test_record_down.py` (NEW)

Subset of `TestRecordDown` above promoted to its own file — pure
unit, no `resolve_shot` involvement.

### Existing tests

`matches/tests/test_batch_sim.py` and the SIM-07/SIM-08 determinism
tests will see **diffs in seeded outputs**. The expected behaviour:

- Serial == parallel for the same `master_seed` still holds
  (`resolve_shot` is deterministic; consumes RNG only via
  `random.random()` and `random.randint`, in the documented order).
- Pre-refactor recorded fixtures will need re-baseline. Folds into
  the **already-pending post-MOVE-01 Score Calibration re-baseline**
  — no new obligation.

A new explicit regression: `test_resolve_shot_serial_equals_parallel`
running a 10-game batch with `workers=2` and asserting team-position
aggregate equality with the same `master_seed`.

---

## 5. Out of scope (deliberate)

- **EventLog module** (deepening candidate #2). Each `resolve_shot`
  emit-site still uses the inline 7-key dict literal. Candidate #2
  collapses those into domain verbs. Keeping them inline here means
  `resolve_shot` looks ugly until #2 lands — accepted, because
  bundling #1 and #2 in one PR would be a 1000-line diff.
- **Missile shots.** `_complete_missile` is its own shot-like
  resolution but the rules differ (no follow-up, no reaction, dodge
  roll happens earlier). Out of scope; missile resolution stays
  inline. (`record_down` is shared.)
- **Nuke detonation per-player elimination.** `_complete_nuke` runs
  through `record_down` but doesn't go through `resolve_shot` — it
  has no attacker-side Shot.
- **`_attempt_resupply` / `_capture_base`.** Not Shot-shaped.
  Unchanged.
- **MVP / scoring weight changes.** Out of scope.
- **`ResourceBasedSimulator` parity.** RBS was removed by SIM-09;
  this refactor only touches `BatchSimulator`.

---

## 6. Implementation order

1. `sim_helpers/round_context.py` — dataclass + import alias.
2. `sim_helpers/down.py` — `record_down` lifted from
   `BatchSimulator._record_down` byte-for-byte (no behaviour
   change). Tests pin behaviour (`test_record_down.py`).
3. Convert the three `record_down` callers (`resolve_tag_attempts`,
   `_complete_missile`, `_complete_nuke`) to pass `ctx`.
   `BatchSimulator._record_down` deleted.
4. `sim_helpers/shot.py` — `resolve_shot` + four `SHOT_KIND_*`
   constants + `ShotOutcome` + private emit/schedule helpers. Tests
   pin every branch (`test_shot_resolver.py`). This step folds in
   the two behaviour changes (uniform hide, uniform Ammo non-decrement)
   and the drift.
5. Convert the five sites in `simulation.py` one at a time. After
   each, run the full pytest suite and update fixtures alongside.
6. Remove the `self._event_log` / `self._pending_nukes` stash from
   `_simulate_round`.
7. Run `python -m black laserforce_simulator` per project tooling.
8. Update `matches/CLAUDE.md` and `sim_helpers/CLAUDE.md` to point
   at the new modules.
9. PR template + STOP for user review.

---

## 7. Locked names (frozen — STOP and flag if reality contradicts)

- Modules: `sim_helpers/round_context.py`, `sim_helpers/down.py`,
  `sim_helpers/shot.py`.
- Classes: `RoundContext`, `ShotOutcome`.
- Functions: `record_down`, `resolve_shot`.
- Constants: `SHOT_KIND_INITIAL`, `SHOT_KIND_FOLLOW_UP`,
  `SHOT_KIND_REACTION`, `SHOT_KIND_OVERWATCH`, `_VALID_KINDS`.
- Test files: `matches/tests/test_shot_resolver.py`,
  `matches/tests/test_record_down.py`.
- Branch: `shot-resolver-consolidation`.

End of contract.
