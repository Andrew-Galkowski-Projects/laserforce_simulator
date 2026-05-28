# EventLog Seam Contract — 23-Site Emit Consolidation

**Status:** LOCKED. Second of the deepening opportunities surfaced by
`/improve-codebase-architecture` (2026-05-27, candidate #2). Names below
are frozen. If reality contradicts a name here, STOP and flag; do not
silently drift.

Branch: `event-log-consolidation` (cut from `main` 2026-05-28, after
the shot-resolver consolidation merged in commit a3a8471).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

Prior art: this candidate completes the picture the shot-resolver
consolidation started — `_actor_meta` / `_target_meta` / `_build_meta`
were duplicated across `simulation.py`, `shot.py`, and `down.py`; the
`emit_event` callable seam in `combat.py` / `resupply_queue.py` was a
one-adapter stand-in for what is now a real concept.

---

## 0. Resolved decisions (DO NOT re-open)

These are baked into the contract. Re-litigating them belongs in a
follow-up grilling session, not in implementation:

- **All 23 emit sites migrate.** Scope is every event-emission site in
  the sim path: 18 inline dict literals in `simulation.py`, 3 private
  `_emit_*` helpers in `sim_helpers/shot.py`, 2 in `sim_helpers/down.py`,
  and the emit-via-callable paths in `sim_helpers/combat.py` and
  `sim_helpers/resupply_queue.py`. Leaving any site on the old pattern
  defeats the consolidation.
- **Null-object pattern.** `RoundContext.events` is **always
  non-None** — an `EventLog` instance. Persistence is a constructor
  flag: `EventLog(persist=True)` records entries; `EventLog(persist=False)`
  drops them. Every emit site is one unguarded line; the `if event_log
  is not None:` guards delete from all 23 sites. The batch-path overhead
  is one method-call-and-early-return per emit (~3000/round) — negligible.
- **Per-event-type verbs.** One method per event_type: 13 verbs total
  (see §1). Each verb knows the canonical `description`, `points_awarded`,
  metadata schema, and `event_type` string for its kind. Callers pass
  domain objects (`PlayerState`, `tick`) + kind-specific `**extras`.
  EventLog OWNS metadata construction — callers never see `_actor_meta`
  / `_target_meta` / the 7-key dict shape.
- **Collapse the `emit_event` callable seam in helpers.** `combat.py`
  and `resupply_queue.py` STOP taking `emit_event=None` callables and
  START taking `ctx: RoundContext`. Helpers call `ctx.events.tag(...)`
  / `ctx.events.resupply_lives(...)` directly. The `_resupply_event_dict`
  adapter, the `_batch_emit` lambda inside `_simulate_round`, and the
  inline `emit_event=event_log.append` wiring at every call site —
  all delete.
- **Rename `RoundContext.event_log` → `RoundContext.events`.** Field
  type changes from `Optional[list]` to `EventLog` (always non-None).
  Callers read `ctx.events` (the EventLog) and `ctx.events.entries`
  (the underlying list of dicts, for `_flush_to_db` consumption and
  test inspection).
- **`movement` events stay off the EventLog.** Movement is written
  directly to `GameEvent` rows at `_flush_to_db` time from
  `PlayerState.movement_trail` (MOVE-01 / RES-04 — the trail is the
  in-memory source). No `movement` verb on EventLog.
- **Event-dict shape is unchanged.** The 7-key dict shape
  (`event_type`, `actor_id`, `target_id`, `timestamp`, `points_awarded`,
  `description`, `metadata`) and the metadata schemas (5-key actor
  block, 4-key target block, kind-specific extras) are byte-identical
  to today. `_flush_to_db`, `build_highlights`, `game_round_events.html`,
  the missile-log view, and every existing analytics reader keep working
  unchanged. The deepening is a write-side consolidation only.
- **Behaviour-neutral consolidation.** No new behaviour, no new
  metadata fields, no event-type renames. RNG-consumption order is
  unchanged (EventLog verbs consume zero RNG). Seeded games are
  **byte-identical to pre-refactor**. **NO new Score Calibration
  re-baseline obligation.**
- **No CONTEXT.md edit.** No new domain terms — "EventLog" is a code
  artifact, not domain.
- **No ADR.** Refactor consolidates duplication; no load-bearing
  decision a future review would re-suggest reverting.
- **No new migration.** No model field change. `GameEvent` schema
  unchanged.

---

## 1. File layout

### `sim_helpers/event_log.py` (NEW)

Pure Python, no Django imports. Sibling imports: none required by the
class itself (the verbs deal in primitives + duck-typed player state).

```python
from dataclasses import dataclass

@dataclass(frozen=True)
class EventEntry:
    """An emitted event record. Exposed via EventLog.entries[]."""
    # (Internally stored as plain dicts — frozen dataclass shown for
    # documentation; see §1.1 for the actual storage decision.)

class EventLog:
    """Null-object-pattern event recorder.

    Single source of truth for the GameEvent-dict shape. Every emit
    site in the simulation path goes through one of the 13 verbs
    below; the dict shape and metadata schemas are constructed
    internally so no caller sees the 7-key literal.

    ``persist=True`` records every emit into ``self._entries``;
    ``persist=False`` drops them (each verb early-returns). Both
    modes are otherwise indistinguishable to callers.
    """

    def __init__(self, *, persist: bool = True) -> None:
        self._persist: bool = persist
        self._entries: list[dict] = []

    # --- read API ---------------------------------------------------
    @property
    def entries(self) -> list[dict]:
        """The underlying list of event dicts.

        Consumed by ``BatchSimulator._flush_to_db`` (to construct
        ``GameEvent`` rows) and by tests inspecting emitted events.
        Returns the *internal* list (not a copy); callers must not
        mutate it.
        """
        return self._entries

    def __iter__(self): return iter(self._entries)
    def __len__(self) -> int: return len(self._entries)

    # --- 13 per-event-type verbs (see §1.2 for signatures) -----------
```

#### §1.1. Internal storage

Plain `list[dict]` keyed `_entries`. The dict shape mirrors the
pre-refactor 7-key dict byte-for-byte (`event_type`, `actor_id`,
`target_id`, `timestamp`, `points_awarded`, `description`, `metadata`)
so `_flush_to_db` consumption is unchanged. `EventEntry` is shown in
§1 as a frozen-dataclass *concept* for documentation, but the actual
storage stays plain dicts — promoting to dataclass instances would
churn every downstream reader for zero benefit.

#### §1.2. The 13 verbs (frozen signatures)

```python
# --- Shot resolution (3) ----------------------------------------
def tag(self, attacker, defender, tick: int, *,
        kind: str = "initial", chain_depth: int = 0,
        ) -> None:
    """Emits event_type='tag'. points_awarded=100. Description
    differs by kind (see §1.3). Metadata = actor + target blocks
    + kind extras (is_follow_up/chain, is_reaction, overwatch)."""

def miss(self, attacker, defender, tick: int, *,
         kind: str = "initial", chain_depth: int = 0,
         reason: str | None = None,
         ) -> None:
    """Emits event_type='miss'. points_awarded=0. Metadata = actor
    + target blocks + kind extras + optional reason='hiding'."""

def elimination(self, attacker, defender, tick: int, *,
                action: str = "tag",
                ) -> None:
    """Emits event_type='elimination'. points_awarded=0. Metadata =
    actor + target blocks + elimination_action ('tag', 'follow_up_tag',
    'reaction', 'missile', 'nuke')."""

# --- Down chokepoint (2) ----------------------------------------
def nuke_cancelled(self, commander, tick: int) -> None:
    """Emits event_type='nuke_cancelled'. points_awarded=0. Metadata
    = actor block only (no target). RV-02."""

def medic_reset(self, medic, tick: int) -> None:
    """Emits event_type='medic_reset'. points_awarded=0. Metadata =
    actor block only. RV-02."""

# --- Special abilities (1) --------------------------------------
def special(self, actor, tick: int, *,
            description: str,
            points: int = 0,
            metadata_extras: dict | None = None,
            ) -> None:
    """Emits event_type='special'. Covers nuke activation, nuke
    detonation, scout rapid-fire activation, commander shield, etc.
    The five existing 'special' emit sites in simulation.py each pass
    their own description and metadata_extras (e.g. fires_at=...,
    targets=[...]). Metadata = actor block + metadata_extras."""

# --- Missile (3) ------------------------------------------------
def locking(self, attacker, defender, tick: int) -> None:
    """Emits event_type='locking'. RES-03 missile fire tick.
    points_awarded=0. Metadata = {actor_role, target_role}."""

def missiled(self, attacker, defender, tick: int, *,
             result: str,           # 'hit' | 'miss'
             friendly_fire: bool,
             ) -> None:
    """Emits event_type='missiled'. RES-03 missile resolution tick.
    points_awarded=500 if result='hit' else 0. Metadata = actor +
    target blocks + result + friendly_fire + actor_role + target_role
    (the four RES-03-required keys)."""

def missile_dodge(self, defender, attacker, tick: int) -> None:
    """Emits event_type='missile_dodge'. The defender dodged a
    missile via survival roll. points_awarded=0. Note actor=defender,
    target=attacker — the dodging defender is the protagonist."""

# --- Resupply (3) -----------------------------------------------
def resupply_lives(self, supporter, requestor, tick: int) -> None:
    """Emits event_type='resupply_lives'. Medic resupplies an ally.
    points_awarded=0. Metadata = actor + target blocks."""

def resupply_ammo(self, supporter, requestor, tick: int) -> None:
    """Emits event_type='resupply_ammo'. Ammo resupplies an ally.
    points_awarded=0. Metadata = actor + target blocks."""

def combo_resupply(self, requestor, medic, ammo, tick: int) -> None:
    """Emits event_type='combo_resupply'. Both lives + shots same
    tick. points_awarded=0. Metadata = requestor actor block +
    medic_tag + ammo_tag + target block for requestor."""

# --- Base capture (1) -------------------------------------------
def base_capture(self, actor, tick: int, *,
                 base_id: int,
                 points: int,
                 ) -> None:
    """Emits event_type='base_capture'. points_awarded=points.
    Metadata = actor block + base_id."""
```

**Total: 13 verbs.** Movement is intentionally absent (see §0).

#### §1.3. Description templates (frozen, byte-identical to pre-refactor)

Each verb owns its f-string. Examples:

- `tag` (kind=initial): `f"{attacker.name} tags {defender.name}"`
- `tag` (kind=follow_up): `f"{attacker.name} follow-up tags {defender.name}"`
- `tag` (kind=reaction): `f"{attacker.name} reacts to {defender.name}"`
- `tag` (kind=overwatch): `f"{attacker.name} tags {defender.name}"` (same as initial; the `overwatch=True` metadata flag is the only differentiator)
- `miss` (kind=initial, reason=None): `f"{attacker.name} misses {defender.name}"`
- `miss` (kind=initial, reason="hiding"): `f"{attacker.name} misses {defender.name} (hiding)"`
- `miss` (kind=follow_up): `f"{attacker.name} follow-up miss on {defender.name}"`
- `miss` (kind=reaction): `f"{attacker.name} reaction miss on {defender.name}"`
- `elimination` (action=tag): `f"{defender.name} eliminated by {attacker.name}"`
- `elimination` (action=follow_up_tag): `f"{attacker.name} eliminates {defender.name} (follow-up)"`
- `elimination` (action=reaction): `f"{attacker.name} eliminates {defender.name} (reaction)"`
- `nuke_cancelled`: `f"{commander.name} nuke cancelled"`
- `medic_reset`: `f"{medic.name} medic reset (down-chain)"`
- `locking`: `f"{attacker.name} locks onto {defender.name}"` (pre-refactor wording preserved)
- `missiled` (result=hit): `f"{attacker.name} hits {defender.name} with missile"`
- `missiled` (result=miss): `f"{attacker.name} misses {defender.name} with missile"`
- `missile_dodge`: `f"{defender.name} dodges missile from {attacker.name}"`

The description strings ARE part of the seam — tests pin substring
matches against the rendered `game_round_events.html` and the missile
log. Verb implementations preserve the pre-refactor f-strings
byte-for-byte; cross-checked at implementation time.

### `sim_helpers/round_context.py` (MODIFIED)

```python
from dataclasses import dataclass
from typing import Any

from .event_log import EventLog

@dataclass
class RoundContext:
    events: EventLog           # was: event_log: Optional[list]
    pending_nukes: list
    pending_followups: list
    pending_reactions: list
    all_alive: list
    movement_ctx: Any
```

The field rename `event_log` → `events` is a hard rename — no alias
retained. All callers update in lockstep.

### `sim_helpers/down.py` (MODIFIED)

- The private `_actor_meta` helper is **removed**.
- `record_down` emits `medic_reset` and `nuke_cancelled` via
  `ctx.events.medic_reset(...)` / `ctx.events.nuke_cancelled(...)`.
- The `ctx is None` defensive path stays (test compat); when `ctx`
  is None, the emits skip (mirrors today's `event_log is None` skip).
- Public signature unchanged: `record_down(player, tick, ctx)`.

### `sim_helpers/shot.py` (MODIFIED)

- The private `_emit_tag`, `_emit_miss`, `_emit_elimination` helpers
  are **removed**.
- The private `_actor_meta`, `_target_meta`, `_build_meta` helpers
  are **removed**.
- The private `_kind_extras`, `_elimination_action` helpers stay —
  they're the kind→metadata-flag translation, conceptually owned
  by shot.py (not EventLog). When `resolve_shot` calls
  `ctx.events.tag(attacker, defender, tick, kind=kind, chain_depth=chain_depth)`,
  the EventLog `tag` verb internally calls `_kind_extras(kind, chain_depth)`
  — wait, no: `_kind_extras` MOVES into EventLog as a private helper
  too. The kind-to-flags translation is canonical event-shape concern.
- 10-phase resolver body unchanged except for the emit lines:
  - Phase 2 (miss_hid): `ctx.events.miss(attacker, defender, tick, kind=kind, chain_depth=chain_depth, reason="hiding")`
  - Phase 7 hit cascade: `ctx.events.elimination(attacker, defender, tick, action=_elimination_action(kind))` and `ctx.events.tag(attacker, defender, tick, kind=kind, chain_depth=chain_depth)`
  - Phase 8 miss: `ctx.events.miss(attacker, defender, tick, kind=kind, chain_depth=chain_depth)`
- Public signature unchanged: `resolve_shot(attacker, defender, tick, *, kind, ctx, chain_depth=0)`.

### `sim_helpers/combat.py` (MODIFIED)

- `attempt_resupply(tagger, teammate, second, *, emit_event=None)`
  → `attempt_resupply(tagger, teammate, second, *, ctx)`. The
  `emit_event` callable is removed; the helper calls
  `ctx.events.resupply_lives(...)` / `ctx.events.resupply_ammo(...)`
  directly.
- `capture_base(player, base_id, second, movement_ctx=None, *, emit_event=None)`
  → `capture_base(player, base_id, second, movement_ctx=None, *, ctx)`.
  Calls `ctx.events.base_capture(...)`.
- `award_bases(player, second, *, emit_event=None)` →
  `award_bases(player, second, *, ctx)`. Calls
  `ctx.events.base_capture(...)`.
- `start_missile_lock(attacker, defender, second, *, emit_event=None)`
  → `start_missile_lock(attacker, defender, second, *, ctx)`. Calls
  `ctx.events.locking(...)`.
- All four helpers now require `ctx`; the `=None` default is dropped.

### `sim_helpers/resupply_queue.py` (MODIFIED)

- `resolve_resupply_requests(requestors, all_alive, second, movement_ctx, *, emit_event=None)`
  → `resolve_resupply_requests(requestors, all_alive, second, movement_ctx, *, ctx)`.
- The internal `emit_event(event_type, **kwargs)` calls become
  `ctx.events.resupply_lives(...)` / `ctx.events.resupply_ammo(...)`
  / `ctx.events.combo_resupply(...)`.

### `simulation.py` (MODIFIED)

- The module-level `_actor_meta`, `_target_meta`, `_build_meta` are
  **removed** (15+ call sites updated).
- The module-level `_resupply_event_dict` adapter is **removed**.
- The 18 inline `event_log.append({...})` sites in `_simulate_round`,
  `_complete_missile`, `_complete_nuke`, `_use_special`, and
  `_capture_base` collapse to one-line verb calls (see §2 for the
  full map).
- The `_batch_emit` lambda inside `_simulate_round` is **removed**.
- `_simulate_round` signature changes:
  `event_log=None` → `events: EventLog | None = None`. When None,
  the method constructs `EventLog(persist=False)` internally.
- `_flush_to_db` reads `events.entries` instead of the raw `event_log`
  list. The bulk_create logic is unchanged.

---

## 2. Call-site map

The 18 simulation.py emit sites and which verb they migrate to:

| Old emit type | New verb | Location (pre-refactor) |
|---|---|---|
| `missile_dodge` | `ctx.events.missile_dodge(...)` | `_simulate_round` missile-lock processing (~L1386) |
| `missiled` (miss branch — dodge) | `ctx.events.missiled(..., result="miss", friendly_fire=...)` | `_simulate_round` missile-lock processing (~L1402) |
| `nuke_cancelled` (defensive fallback) | `ctx.events.nuke_cancelled(...)` | `_simulate_round` nuke drain (~L1480) |
| `elimination` (missile hit) | `ctx.events.elimination(..., action="missile")` | `_complete_missile` (~L2019) |
| `missiled` (hit branch) | `ctx.events.missiled(..., result="hit", friendly_fire=...)` | `_complete_missile` (~L2044) |
| `special` (nuke activation) | `ctx.events.special(..., description=..., points=0, metadata_extras={"fires_at": ...})` | `_use_special` (~L2077) |
| `special` (rapid fire activation) | `ctx.events.special(..., description=..., points=0)` | `_use_special` (~L2094, ~L2119) |
| `special` (commander shield) | `ctx.events.special(...)` | `_use_special` (~L2163) |
| `special` (nuke detonation) | `ctx.events.special(..., points=500, metadata_extras={"targets": [...]})` | `_complete_nuke` (~L2219) |
| `elimination` (nuke detonation eliminations) | `ctx.events.elimination(..., action="nuke")` | `_complete_nuke` (~L2242) |

shot.py's 3 sites:
| Old emit (private) | New verb |
|---|---|
| `_emit_tag` | `ctx.events.tag(...)` |
| `_emit_miss` | `ctx.events.miss(...)` |
| `_emit_elimination` | `ctx.events.elimination(...)` |

down.py's 2 sites:
| Old emit (inline) | New verb |
|---|---|
| `medic_reset` dict literal | `ctx.events.medic_reset(...)` |
| `nuke_cancelled` dict literal | `ctx.events.nuke_cancelled(...)` |

combat.py / resupply_queue.py — collapse the `emit_event` callable
seam; helpers call verbs directly (see §1).

---

## 3. Removed code

- `simulation.py::_actor_meta`, `_target_meta`, `_build_meta` —
  module-level functions, fully replaced by `EventLog`'s internal
  helpers.
- `simulation.py::_resupply_event_dict` — adapter, no longer needed
  once `resupply_queue.py` calls verbs directly.
- `simulation.py::_simulate_round::_batch_emit` — inner lambda,
  no longer needed once `resupply_queue.py` takes `ctx`.
- `shot.py::_emit_tag`, `_emit_miss`, `_emit_elimination`,
  `_actor_meta`, `_target_meta`, `_build_meta` — private helpers,
  fully absorbed by EventLog.
- `down.py::_actor_meta` — private helper, fully absorbed by
  EventLog.
- `emit_event=None` kwargs on `combat.attempt_resupply`,
  `combat.capture_base`, `combat.award_bases`,
  `combat.start_missile_lock`, and
  `resupply_queue.resolve_resupply_requests` — all replaced by
  required `ctx` kwarg.

Estimated line reduction: **~150-200 lines** of duplicated metadata
construction and emit-dict literals across the four files.

---

## 4. Tests

### `matches/tests/test_event_log.py` (NEW)

Pure-unit tests, no Django ORM, hand-built `PlayerState` instances.
One test class per verb (13 classes) plus a NullLog class.

Required coverage per verb:
- Returns None.
- Persist=True: entry appended to `events.entries`; dict has correct
  `event_type`, `actor_id`, `target_id`, `timestamp`, `points_awarded`,
  `description` (substring or exact), `metadata` (key presence + values).
- Persist=False: no entry appended; `len(events) == 0`.

Plus:
- `TestEventLogConstruction` — `EventLog()` defaults to `persist=True`;
  `EventLog(persist=False)` is a no-op log.
- `TestEventLogIteration` — `iter(events)` and `len(events)` work as
  expected.
- `TestEventLogEntriesIsLive` — `events.entries` returns the internal
  list (not a copy); tests document the contract.
- `TestNoDjangoImportsLeaked` — defensive check mirroring the
  HX-01 / RES-04 precedent.

### Existing tests

The following test files inspect raw `event_log` lists and need a
mechanical migration to `events.entries`:

- `test_record_down.py` — 28 tests, builds `RoundContext(event_log=[...])`,
  inspects `ctx.event_log[i]["event_type"]`. Migrates to
  `RoundContext(events=EventLog(persist=True))` + `ctx.events.entries[i]["event_type"]`.
- `test_shot_resolver.py` — 49 tests, same pattern.
- `test_sim09_consolidation.py` — RV-02 nuke / medic test classes,
  same pattern.
- `test_batch_sim.py`, `test_rv02_highlights.py`, `test_mech*.py`,
  `test_res03_missile_log_spec.py`, `test_move03_hold_overwatch.py`,
  `views_tests.py` — wherever an `event_log: list[dict]` is built or
  inspected.

**The dict shape is unchanged**, so no test asserts on dict structure
break. Migration is the rename `event_log` → `events.entries` plus
RoundContext construction. Bulk-edit via a Python helper script.

### Seeded determinism canary

`test_batch_sim.py::TestSim08SideAlternation::test_strong_team_winpct_not_diluted_by_alternation`
asserts the strong team's team-position win% stays ≥ 55%. **No
threshold change needed** — EventLog consumes zero RNG, so seeded
games are byte-identical to pre-refactor.

---

## 5. Implementation order

1. `sim_helpers/event_log.py` — class + 13 verbs.
2. `matches/tests/test_event_log.py` — pure-unit tests; verify all
   13 verbs emit correctly-shaped dicts.
3. `sim_helpers/round_context.py` — field rename `event_log` →
   `events: EventLog`. Default value is a new `EventLog(persist=False)`
   to keep test factories simple (test callers explicitly construct
   `EventLog(persist=True)` when they want emits).
4. `sim_helpers/shot.py` — drop `_emit_*` + metadata helpers; route
   emits through `ctx.events.*`.
5. `sim_helpers/down.py` — drop `_actor_meta`; route emits through
   `ctx.events.*`.
6. `sim_helpers/combat.py` — collapse `emit_event` kwargs to `ctx`;
   route emits through `ctx.events.*`.
7. `sim_helpers/resupply_queue.py` — same collapse + routing.
8. `simulation.py` — replace 18 inline dict-literal emits with
   `ctx.events.*` calls; drop `_actor_meta`/`_target_meta`/`_build_meta`/`_resupply_event_dict`/`_batch_emit`;
   update `_simulate_round` signature; update `_flush_to_db` to read
   `events.entries`.
9. Bulk-migrate existing tests' `event_log` → `events.entries` access
   patterns and `RoundContext(event_log=...)` → `RoundContext(events=EventLog(persist=True))`.
10. Run black on all touched files.
11. Run full pytest suite; expect **0 new failures**, **0 calibration
    drift** (no RNG-order change).
12. Update `matches/CLAUDE.md` and `sim_helpers/CLAUDE.md` with the
    EventLog section and the verb table.
13. PR template + STOP for user approval.

---

## 6. Locked names (frozen — STOP and flag if reality contradicts)

- Module: `sim_helpers/event_log.py`.
- Class: `EventLog`.
- Constructor: `EventLog(*, persist: bool = True)`.
- Field on `RoundContext`: `events: EventLog` (was `event_log: Optional[list]`).
- Read API: `events.entries: list[dict]`, `iter(events)`, `len(events)`.
- The 13 verb names (all on `EventLog`): `tag`, `miss`, `elimination`,
  `nuke_cancelled`, `medic_reset`, `special`, `locking`, `missiled`,
  `missile_dodge`, `resupply_lives`, `resupply_ammo`, `combo_resupply`,
  `base_capture`.
- Test file: `matches/tests/test_event_log.py`.
- Branch: `event-log-consolidation`.

End of contract.
