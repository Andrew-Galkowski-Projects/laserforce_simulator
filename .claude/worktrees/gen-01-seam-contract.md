# GEN-01 Seam Contract — Three persistence-fidelity tiers off one seed

**Status:** locked design, ready for Code/Tests agents.
**Locked refs:** [ADR-0029](../../docs/adr/0029-persistence-fidelity-tiers-and-faithful-lazy-upgrade.md), CONTEXT.md **Persistence fidelity** term.
**Scope:** persistence-only. The tick loop ALWAYS runs in full; tiers differ ONLY in what `flush_to_db` writes (and, at `scores`, in skipping event-buffer collection). Same seed ⇒ identical game at every tier. **NO Score Calibration re-baseline.**

## 0. The locked design (in one breath)

**Persistence fidelity** is three cumulative tiers `scores` ⊂ `combat` ⊂ `full`:
- `scores` — `GameRound` + `PlayerRoundState` only (final scoreboard).
- `combat` — `+` the combat `GameEvent` rows (tag / missile / resupply / down / elimination / locking / missiled / etc.) `+` `highlights_json`.
- `full` — `+` movement `GameEvent`s `+` per-Advance `metadata["route"]` `+` `cell_occupancy_json`.

Every persisted round (every tier) also stores a `roster_snapshot_json` — the boosted `_PlayerData` sim-stat inputs per side. `BatchSimulator.ensure_fidelity(game_round, target)` re-simulates from `(rng_seed + roster_snapshot_json + arena_map)` — reading the snapshot, NOT live `Team.active_roster` — and **backfills** the missing higher-tier rows onto the EXISTING row, bumping `fidelity`. Idempotent.

**Default tier = `scores` on every path EXCEPT the LG-01i live call sites, which override to `"full"`.** The upgrading views call `ensure_fidelity` on demand.

---

## 1. Model — `GameRound` (`matches/models.py`)

### 1a. Module-level constants (declared just above or inside `GameRound`)

```python
FIDELITY_CHOICES = (
    ("scores", "Scores"),
    ("combat", "Combat"),
    ("full", "Full"),
)
FIDELITY_RANK = {"scores": 1, "combat": 2, "full": 3}
```

- `FIDELITY_CHOICES` — module-level tuple constant, the field's `choices`.
- **`FIDELITY_RANK`** — a **module-level dict** (NOT a `@staticmethod`) keyed `tier_string → int` for ordering tiers. Used by `flush_to_db` gating and by `ensure_fidelity`'s idempotency check (`FIDELITY_RANK[current] >= FIDELITY_RANK[target]`). LOCKED choice: module-level dict, named exactly `FIDELITY_RANK`, living in `matches/models.py` (importable as `from matches.models import FIDELITY_RANK`).

### 1b. Two new fields on `GameRound` (declared after `is_simulated`)

```python
fidelity = models.CharField(
    max_length=6,
    choices=FIDELITY_CHOICES,
    default="full",
)
roster_snapshot_json = models.JSONField(null=True, blank=True, default=None)
```

- `fidelity` — `max_length=6` (longest value `"scores"`/`"combat"` is 6 chars; `"full"` 4). `default="full"` — legacy rows hold events+movement so `full` is TRUE for them (no backfill, ADR-0004).
- `roster_snapshot_json` — `JSONField(null=True, blank=True, default=None)`. `None` ⇒ unupgradeable (only legacy rows, which are already `full` so never reach the re-sim branch — the defensive guard in §5).

### 1c. `roster_snapshot_json` shape (LOCKED)

```python
{
    "red":  [ {"player_id": int, "name": str, "role": str, "stats": {<13 sim-stats>: int}}, ... ],
    "blue": [ {"player_id": int, "name": str, "role": str, "stats": {<13 sim-stats>: int}}, ... ],
}
```

The 13 sim-stat keys inside `"stats"` are exactly `entrypoints._SIMULATION_STATS` (confirmed `matches/simulation/entrypoints.py:113-127`), and each is a real attribute on `PlayerState` (confirmed `sim_helpers/player_state.py`):

```
accuracy, survival, player_awareness, game_awareness, decision_making,
stamina, special_usage, resupply_efficiency, resupply_synergy, teamwork,
communication, resource_awareness, speed
```

**Built from:** the in-memory `red_players` / `blue_players` `PlayerState` lists returned by `_simulate_round` — NOT from the ORM `Player`. For each `PlayerState p` (skip `p.player_id` falsy, mirroring the `flush_to_db` player-skip rule): `{"player_id": p.player_id, "name": p.name, "role": p.role, "stats": {s: getattr(p, s) for s in _SIMULATION_STATS}}`. `team_color` is implicit in the `red`/`blue` key.

**Helper (LOCKED):** a module-level helper in `persistence.py`:
```python
def build_roster_snapshot(red_players, blue_players) -> dict:
```
Returns the dict above. Called inside `flush_to_db` (always — every tier persists the snapshot). The `_SIMULATION_STATS` tuple is imported into `persistence.py` from `entrypoints` (or both import it from a shared spot — Code agent picks; the name `_SIMULATION_STATS` is the single source).

### 1d. Migration

`matches/migrations/0055_gameround_fidelity_roster_snapshot.py`, dependency `("matches", "0054_season_map_rotation")`. **Two `AddField` ops** (`fidelity`, then `roster_snapshot_json`). **NO `RunPython`, NO backfill** (ADR-0004 disposable-data posture).

---

## 2. The `fidelity` selector — threading through the sim methods

The selector defaults `"scores"` everywhere and is **keyword-only** (after the existing `*` where present). Current signatures (verbatim) and the new kwarg:

### 2a. `BatchSimulator.simulate_match` (`entrypoints.py:609`)

Current:
```python
@transaction.atomic
def simulate_match(
    self, team_red, team_blue, match_type: str = "friendly", *,
    arena_map=None, before_round_hook=None,
) -> Match:
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Thread it into BOTH internal `_simulate_and_flush_round` calls (round 1 and round 2): `fidelity=fidelity`.

### 2b. `simulate_single_round_detailed` (`entrypoints.py:711`)

Current:
```python
@transaction.atomic
def simulate_single_round_detailed(self, team_red, team_blue, *, arena_map=None) -> "GameRound":
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Pass `fidelity=fidelity` into its single `_simulate_and_flush_round` call.

### 2c. `simulate_scheduled_round` (`entrypoints.py:737`)

Current:
```python
@transaction.atomic
def simulate_scheduled_round(
    self, season, team_a, team_b, round_number, *,
    arena_map=None, season_phase=None, leg: int = 1,
) -> "GameRound":
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Thread `fidelity=fidelity` into BOTH `_simulate_and_flush_round` calls (the round-1 branch ~`:822` and the round-2 branch ~`:851`).

### 2d. `save_games` (`entrypoints.py:1932`)

Current:
```python
def save_games(self, team_red, team_blue, seeds: list[tuple[int, bool]], n, *, arena_map=None):
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Thread `fidelity=fidelity` into the single `_flush_to_db` call inside the loop (`:1978`). **NOTE:** `save_games` does NOT collect an event buffer per the §4 `event_log=None` choice only inside `_simulate_and_flush_round`; for `save_games` the buffer comes from `replay_round`. To honour `scores` here, `save_games` must call `replay_round` WITHOUT buffer collection at `scores` tier — see §4c.

### 2e. `_simulate_and_flush_round` (`entrypoints.py:556`)

Current:
```python
def _simulate_and_flush_round(
    self, team_red, team_blue, *, match, round_number: int,
    movement_ctx, arena_map, zone_size: int | None,
) -> "GameRound":
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Body changes (§4): choose `events = []` vs `event_log=None`; pass `fidelity=fidelity` into `_flush_to_db`.

### 2f. `_flush_to_db` (`entrypoints.py:1993`, the thin delegator)

Current:
```python
def _flush_to_db(
    self, team_red, team_blue, result, red_players, blue_players, events, *,
    rng_seed: int | None = None, movement_ctx=None, match=None,
    round_number: int = 1, arena_map=None, zone_size: int | None = None,
) -> ...:
```
ADD keyword-only `fidelity: str = "scores"` (appended last). Forward `fidelity=fidelity` into the `persistence.flush_to_db(...)` call.

### 2g. `persistence.flush_to_db` (`persistence.py:25`)

Current:
```python
@transaction.atomic
def flush_to_db(
    team_red, team_blue, result, red_players, blue_players, events, *,
    role_starting_resources: dict, rng_seed: int | None = None,
    movement_ctx=None, match=None, round_number: int = 1,
    arena_map=None, zone_size: int | None = None,
):
```
ADD keyword-only `fidelity: str = "scores"` (appended last, after `zone_size`). Gating body in §4.

### 2h. Tournament path — `tournament_engine.play_next_node` / `play_specific_node`

Current (`tournament_engine.py`):
```python
def play_next_node(tournament: "Tournament") -> "BracketNode | None":   # :88, NO @transaction.atomic; delegates to play_specific_node

@transaction.atomic
def play_specific_node(node: "BracketNode") -> "BracketNode | None":    # :109
    ...
    match = BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament", before_round_hook=hook)   # :130 (random_draw branch)
    ...
    match = BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")                          # :137 (preset branch)
```

`play_next_node` finds the next playable node then `return play_specific_node(node)` (it is the CALLER; `play_specific_node` carries the atomic). **Thread the selector through BOTH:**
- `play_specific_node(node, *, fidelity: str = "scores") -> "BracketNode | None"` — pass `fidelity=fidelity` into BOTH `simulate_match(match_type="tournament", ...)` calls (the `random_draw` branch `:130` and the `preset` branch `:137`).
- `play_next_node(tournament, *, fidelity: str = "scores")` — forward `fidelity=fidelity` into its `play_specific_node(node, fidelity=fidelity)` call.

(Both default `"scores"`. The LG-01i live-playoff path overrides — §3.)

### 2i. Per-call defaults summary

**ALL paths default `fidelity="scores"`** EXCEPT the LG-01i **live** call sites (§3), which pass `fidelity="full"`.

---

## 3. LG-01i live call sites override to `"full"` (LOCKED)

> NOTE: LG-01i is **play-now-and-watch** (CONTEXT.md), NOT preview-then-commit — there is no injected seed. The two live calls below play the manager's game immediately and the watch view replays it from the persisted rows, so those rows MUST be `full`.

In `matches/league_views.py::play_week_live`:

### 3a. RR branch (`:2812`)

Current call:
```python
game_round = BatchSimulator().simulate_scheduled_round(
    season,
    cursor["red_team"],
    cursor["blue_team"],
    cursor["fixture"].round_number,
    arena_map=arena_map,
    season_phase=cursor["season_phase"],
    leg=cursor["fixture"].leg,
)
```
ADD `fidelity="full"` (appended after `leg=...`).

### 3b. Live-playoff branch (`:2828`)

Current call:
```python
play_specific_node(node)
```
CHANGE to:
```python
play_specific_node(node, fidelity="full")
```

**Every OTHER caller of these methods is unchanged** (they inherit the `scores` default): `tasks.py::play_season_task` (`simulate_scheduled_round`, no fidelity kwarg ⇒ `scores`), `league_views.py::play_week` non-live (`simulate_scheduled_round` at `:2575`, ⇒ `scores`), `play_next_node`-driven background drains (⇒ `scores`), the sandbox views (`simulate_match` / `simulate_single_round_detailed`, ⇒ `scores`), `save_games` (⇒ `scores`).

---

## 4. `persistence.flush_to_db` gating + the `scores` no-buffer choice

### 4a. The gate (read the current body — it currently always writes everything)

`flush_to_db` is refactored so its write-blocks are **named reusable helpers** in `persistence.py`, called by BOTH the fresh flush AND the backfill. Gating by `FIDELITY_RANK[fidelity]`:

| Block | Gate | Helper |
|---|---|---|
| `GameRound` create (now also setting `fidelity=fidelity` + `roster_snapshot_json=<snapshot>`) | ALWAYS | inline in `flush_to_db` (the create + winner-calc `save()`) |
| `PlayerRoundState` rows | ALWAYS | `_write_player_states(game_round, red_players, blue_players, role_starting_resources)` |
| combat `GameEvent` rows (from `events`) | `rank >= combat` (≥ 2) | `_write_combat_events(game_round, events, players_by_id)` |
| `highlights_json` (build_highlights + 2nd save) | `rank >= combat` (≥ 2) | `_write_highlights(game_round, events, red_players, blue_players)` |
| movement `GameEvent`s + `metadata["route"]` | `rank == full` (== 3) | `_write_movement_events(game_round, red_players, blue_players, movement_ctx, players_by_id)` |
| `cell_occupancy_json` (+ 2nd save) | `rank == full` (== 3) | `_write_cell_occupancy(game_round, red_players, blue_players, movement_ctx)` |

LOCKED helper names + signatures (all module-level in `persistence.py`, all take an already-built `game_round` + the in-memory lists / `events` / `players_by_id` they need; none re-query the scoreboard):

```python
def build_roster_snapshot(red_players, blue_players) -> dict: ...
def _write_player_states(game_round, red_players, blue_players, role_starting_resources) -> dict:
    """Returns players_by_id (id -> Player ORM) for downstream blocks to reuse."""
def _write_combat_events(game_round, events, players_by_id) -> None: ...
def _write_highlights(game_round, events, red_players, blue_players) -> None: ...
def _write_movement_events(game_round, red_players, blue_players, movement_ctx, players_by_id) -> None: ...
def _write_cell_occupancy(game_round, red_players, blue_players, movement_ctx) -> None: ...
```

- `combat` rows = the existing `events`-loop block (`persistence.py:120-137`) — EXCLUDING the movement block. The current `events` buffer already contains ONLY non-movement events (movement rows are written separately from `movement_trail`, NOT in `events`), so `_write_combat_events` is the existing `for ev in events:` loop verbatim, gated.
- `_write_movement_events` is the existing `movement_trail`/`movement_routes` block (`persistence.py:139-185`), gated `rank == full`.
- `_write_cell_occupancy` is the existing RES-04 block (`persistence.py:187-223`), gated `rank == full` **AND** the existing `movement_ctx is not None` guard (both must hold — a `full` map-less round still leaves `cell_occupancy_json` null, matching today).
- `_write_highlights` is the existing RV-02 block (`persistence.py:225-244`), gated `rank >= combat`. At `scores`, `highlights_json` stays `None` (no backfill until upgrade).
- The HX-02 `invalidate_role_benchmarks()` tail (`persistence.py:251-253`) stays at the END of `flush_to_db`, ALWAYS (cheap cache bump; runs on every tier).

`flush_to_db` ALWAYS calls `build_roster_snapshot(...)` and sets `game_round.roster_snapshot_json` + `game_round.fidelity = fidelity` on the `GameRound.objects.create(...)` (so they land in the first save, before winner calc).

### 4b. `scores` runs with `event_log=None` (no buffer collection)

In `_simulate_and_flush_round` (`entrypoints.py:586-592`): the `events = []` / `_simulate_round(..., event_log=events, ...)` is conditioned on fidelity. **Pin the logic location here** (`_simulate_and_flush_round` chooses `events = []` vs `None`):

```python
if FIDELITY_RANK[fidelity] >= FIDELITY_RANK["combat"]:
    events: list | None = []
else:
    events = None   # scores tier: EventLog(persist=False), no buffer
result, red_players, blue_players = self._simulate_round(
    red_roster, blue_roster, event_log=events, movement_ctx=movement_ctx,
)
return self._flush_to_db(
    ..., events, ..., fidelity=fidelity, ...
)
```

`_simulate_round(event_log=None)` already builds the null-object `EventLog(persist=False)` (confirmed `entrypoints.py:1065-1068`), so `scores` collects no buffer. `flush_to_db` must tolerate `events=None`: the combat/highlights helpers only run at `rank >= combat` where `events` is a real list, so guard their bodies on the rank (a `None` `events` never reaches `_write_combat_events`). `build_highlights` is NOT called at `scores`.

### 4c. `save_games` no-buffer at `scores`

`save_games` builds its buffer via `replay_round` (`entrypoints.py:1960`), which always does `events = []`. To honour `scores`, gate the buffer there too: at `scores`, `save_games` calls `_simulate_round` with `event_log=None` (i.e. add a fidelity-aware path — Code agent may add a thin `replay_round(..., fidelity=...)` keyword that chooses `events = [] if rank>=combat else None`, OR inline the choice in `save_games`). LOCKED rule: at `scores`, `save_games` MUST NOT collect movement/combat buffers and MUST pass `fidelity="scores"` to `_flush_to_db`. (Code agent picks the inline-vs-`replay_round`-kwarg shape; the no-buffer-at-scores behaviour is the pin.)

---

## 5. `ensure_fidelity` — the lazy-upgrade primitive (`entrypoints.py`)

New `BatchSimulator` method, `@transaction.atomic`:

```python
@transaction.atomic
def ensure_fidelity(self, game_round, target_fidelity: str) -> "GameRound":
```

**Algorithm (LOCKED):**

1. **Idempotent / defensive no-op** — `return game_round` unchanged iff
   `FIDELITY_RANK[game_round.fidelity] >= FIDELITY_RANK[target_fidelity]`
   **OR** `game_round.roster_snapshot_json is None` (the defensive guard — a `fidelity < full` row with a null snapshot is impossible in practice, but never crash; render scores-only by returning as-is).
2. Else rebuild the `_PlayerData` lists from `roster_snapshot_json` (see §5a).
3. `movement_ctx, _ = load_map_context(game_round.arena_map)` (`arena_map` may be `None` ⇒ 3-zone fallback `(None, None)` — matches every other path).
4. `random.seed(game_round.rng_seed)`.
5. `result, red_players, blue_players = self._simulate_round(red_data, blue_data, event_log=[], movement_ctx=movement_ctx)` — re-run with `event_log=[]` (full buffer collection; we are upgrading TO at least `combat`/`full`).
6. **Backfill** the delta-tier rows onto the EXISTING `game_round` via the shared `_write_*` helpers (§5b), set `game_round.fidelity = target_fidelity`, save. **MUST NOT** rewrite the scoreboard (`GameRound` scalar columns) or `PlayerRoundState` (those stay byte-identical from the original `scores` flush — re-sim off the same seed+snapshot+map reproduces them exactly, so re-writing them is forbidden, not just unnecessary).

### 5a. `_PlayerData` reconstruction (LOCKED — read `_PlayerData` + `_precompute_roster` in entrypoints)

`_PlayerData` (`entrypoints.py:97-110`):
```python
class _PlayerData:
    def __init__(self, player_id: int, name: str, stats: dict) -> None:
        self.id = player_id
        self.name = name
        self._stats = stats
    def stat_for_simulation(self, stat_name: str, role: str) -> int:
        return self._stats[stat_name]
```

`_simulate_round` → `_make_players` reads each roster as a list of `(role, player_model)` tuples and calls `player_model.stat_for_simulation(stat, role)` (confirmed `entrypoints.py:946,981` and `_precompute_roster:130-142`). So rebuild each side as a `list[tuple[role, _PlayerData]]`:

```python
def _roster_from_snapshot(side_list) -> list[tuple[str, _PlayerData]]:
    return [
        (entry["role"], _PlayerData(entry["player_id"], entry["name"], entry["stats"]))
        for entry in side_list
    ]
red_data  = _roster_from_snapshot(game_round.roster_snapshot_json["red"])
blue_data = _roster_from_snapshot(game_round.roster_snapshot_json["blue"])
```

`_PlayerData.stat_for_simulation` returns `self._stats[stat_name]` ignoring `role` — so `_make_players`'s 13 `stat_for_simulation(name, role)` reads resolve straight against the snapshot's `"stats"` dict. The snapshot's 13 keys are EXACTLY the keys `_make_players` reads (the `_SIMULATION_STATS` set), so no `KeyError`. **The snapshot already holds the boosted (post-`stat_for_simulation`) values** (it was built from `PlayerState` whose stats were baked through `stat_for_simulation` in `_make_players`), so `_PlayerData.stat_for_simulation` returning them verbatim re-bakes IDENTICAL `PlayerState`s — exact reproduction. (A private module helper `_roster_from_snapshot` is Code-agent discretion; only the rebuild shape is pinned.)

### 5b. Backfill entry point (LOCKED — pick ONE)

**LOCKED:** `ensure_fidelity` calls the shared `persistence._write_*` helpers DIRECTLY (NOT a separate `persistence.backfill_fidelity(...)`). Rationale: the helpers already take an existing `game_round` + in-memory lists and write their own gated block; a wrapper would just re-implement the rank gate. `ensure_fidelity` re-derives `players_by_id` for the event/movement helpers via the same `_write_player_states`-returned map — BUT must NOT re-create `PlayerRoundState` rows. So:

- `ensure_fidelity` builds `players_by_id` itself with a tiny read-only query (`{p.id: p for p in Player.objects.filter(id__in=all_pids)}` — the same one-query map `flush_to_db` builds at `persistence.py:80-81`), NOT by calling `_write_player_states` (which would duplicate scoreboard rows).
- Then, gated on `FIDELITY_RANK[target]`:
  - `rank >= combat`: `_write_combat_events(game_round, events, players_by_id)` + `_write_highlights(game_round, events, red_players, blue_players)`.
  - `rank == full`: `_write_movement_events(game_round, red_players, blue_players, movement_ctx, players_by_id)` + `_write_cell_occupancy(game_round, red_players, blue_players, movement_ctx)`.
- Then `game_round.fidelity = target_fidelity`; `game_round.save(update_fields=["fidelity"])` (plus the `update_fields` the `_write_highlights` / `_write_cell_occupancy` helpers already do their own `save(update_fields=...)` for — those stay self-contained).

**Helper signatures are reused verbatim from §4a** — `ensure_fidelity` and the fresh `flush_to_db` share one source for every write-block.

> The `players_by_id` build is the one read-only query `ensure_fidelity` issues itself; expose it as a tiny `persistence._players_by_id(red_players, blue_players) -> dict` (lifted from the existing `flush_to_db` lines 80-81) so both call sites share it. (Code-agent discretion on whether to factor this micro-helper; the no-`PlayerRoundState`-rewrite rule is the pin.)

---

## 6. View wiring (`matches/views.py`)

Insert `BatchSimulator().ensure_fidelity(game_round, <tier>)` **after the `get_object_or_404(...)`, before building context**:

| View | Line (current `get_object_or_404`) | Call |
|---|---|---|
| `game_round_events` | `:1020` `game_round = get_object_or_404(GameRound, id=round_id)` | `BatchSimulator().ensure_fidelity(game_round, "full")` — insert immediately after `:1020`, BEFORE `round_playback_payload(game_round)` (`:1021`) so the persisted movement/occupancy exists when the payload + `_build_playback_map` read it |
| `movement_heatmap` | `:1091` `game_round = get_object_or_404(GameRound, pk=round_id)` | `BatchSimulator().ensure_fidelity(game_round, "full")` — insert after `:1091` (after the 405 guard at `:1088-1089`), before the `has_map` / occupancy reads |
| `missile_log` | `:1055` `game_round = get_object_or_404(GameRound, id=round_id)` | `BatchSimulator().ensure_fidelity(game_round, "combat")` — insert after `:1055`, before the `GameEvent.objects.filter(... event_type="missiled")` query (`:1057`) |
| `game_round_detail` | `:320` | **UNCHANGED** — stays `scores` (scoreboard / MVP already live from `PlayerRoundState`; no `ensure_fidelity` call) |

Object var name is `game_round` in all three. Import `BatchSimulator` at the call site (`from matches.simulation.entrypoints import BatchSimulator`) — Code agent checks for an existing import and does not duplicate.

---

## 7. Test boundary

### 7a. The load-bearing equivalence test (single trustworthiness property)

A round flushed DIRECTLY at `full` vs a round flushed at `scores` then `ensure_fidelity("full")` ⇒ **IDENTICAL** combat + movement `GameEvent`s (by `event_type` / `timestamp` / `metadata`), `cell_occupancy_json`, and `highlights_json` — given the SAME seed + SAME snapshot + SAME map.

**How to pin the seed (LOCKED note):** the sim methods draw a fresh `random.Random().getrandbits(63)` internally (`entrypoints.py:583`), so a view-path round's seed is not caller-injected. `ensure_fidelity` re-seeds from the STORED `rng_seed`. Therefore equivalence is checked as **sim-direct-`full` vs sim-`scores`-then-`ensure_fidelity("full")` on the SAME persisted `rng_seed`**:
1. Run a `full` round → capture its persisted `GameRound` (call it `gr_full`).
2. Build a second `GameRound` that is a `scores` flush carrying the **same `rng_seed` and same `roster_snapshot_json`** as `gr_full` (simplest: re-flush `gr_full`'s captured `result/red_players/blue_players/events` at `scores`, OR run a `scores` round and assert/copy its seed). The reliable construction: capture `gr_full.rng_seed` + `gr_full.roster_snapshot_json`, hand-build a `scores` `GameRound` with those, then `ensure_fidelity("full")`.
3. Assert the upgraded row's `GameEvent` tuples (`event_type`, `timestamp`, `metadata`) + `cell_occupancy_json` + `highlights_json` equal `gr_full`'s.

Because `ensure_fidelity` re-seeds from the stored `rng_seed` and reads the stored snapshot + the round's `arena_map`, the re-sim is byte-identical to the direct-`full` run.

### 7b. Fidelity gating unit tests

- `scores`: a flushed round has **0** combat `GameEvent`s, **0** movement `GameEvent`s, `cell_occupancy_json is None`, `highlights_json is None`; `fidelity == "scores"`; `roster_snapshot_json` is a non-null dict with `red`/`blue` lists carrying the 13-key `stats`.
- `combat`: combat `GameEvent`s present + `highlights_json` non-null, BUT **0** movement `GameEvent`s + `cell_occupancy_json is None`; `fidelity == "combat"`.
- `full`: all rows present (combat + movement + occupancy + highlights); `fidelity == "full"`.
- (Gating tests use a map-active round so the `full` occupancy block actually fires; a map-less `full` round still has `cell_occupancy_json is None` — pin that edge too.)

### 7c. `ensure_fidelity` idempotency + defensive no-op

- No-op AT target (`ensure_fidelity(full_round, "full")` ⇒ no new rows, `fidelity` unchanged).
- No-op ABOVE target (`ensure_fidelity(full_round, "combat")` ⇒ unchanged).
- Calling `ensure_fidelity("full")` TWICE on a `scores` round ⇒ first upgrades, second is a no-op (no DUPLICATE `GameEvent` / occupancy / highlight rows).
- Snapshot-`None` defensive no-op: a `fidelity="scores"` row with `roster_snapshot_json=None` (hand-built) ⇒ `ensure_fidelity("full")` returns it unchanged, writes nothing, does not crash.

### 7d. Roster-snapshot faithfulness test

Persist a `scores` round, then **mutate the live `Player` stat fields** (e.g. via the LG-04 develop path or a raw stat write), THEN `ensure_fidelity("full")`. Assert the re-sim still matches the stored scoreboard (`GameRound` points / `PlayerRoundState` rows unchanged) — because the re-sim reads `roster_snapshot_json`, NOT the now-mutated live stats. This is the test that proves the snapshot (not live `Team.active_roster`) drives the upgrade.

### 7e. Default-tier regression (protect the existing test corpus / ADR-0029 blast radius)

Defaulting the sandbox create paths to `scores` breaks every existing test that calls `simulate_match` / `simulate_single_round_detailed` directly then asserts on `GameEvent` / movement / `cell_occupancy_json` / `highlights_json`. Those tests are updated to either pass `fidelity="full"` to the create call OR call `ensure_fidelity(gr, "full")` before asserting. This is the honest, accepted cost (ADR-0029 Consequences). The Tests agent must triage and update these in-place (do NOT loosen them).

### 7f. Test file placement (per project CLAUDE.md `matches/tests/`)

- **NEW** `matches/tests/test_fidelity.py` — the gating unit tests (§7b), `ensure_fidelity` idempotency + defensive no-op (§7c), the roster-snapshot faithfulness test (§7d), and the load-bearing equivalence test (§7a). Classes: `TestFidelityGating`, `TestEnsureFidelityIdempotency`, `TestEnsureFidelitySnapshotGuard`, `TestRosterSnapshotFaithfulness`, `TestFidelityEquivalence`.
- **EXTEND** `matches/tests/test_simulation_view_paths.py` — the `fidelity`/`roster_snapshot_json` persistence on each create path + the default-`scores` regression updates (§7e).
- **EXTEND** `matches/tests/views_tests.py` (or the existing per-view test file) — the three views call `ensure_fidelity` (events/heatmap → `full` ⇒ a `scores` round becomes `full` on view hit; missile-log → `combat`; detail → stays `scores`).

---

## 8. Locked names (quick index)

- **Constants (`matches/models.py`):** `FIDELITY_CHOICES` (`(("scores","Scores"),("combat","Combat"),("full","Full"))`); `FIDELITY_RANK = {"scores":1,"combat":2,"full":3}` (module-level dict).
- **`GameRound` fields:** `fidelity = CharField(max_length=6, choices=FIDELITY_CHOICES, default="full")`; `roster_snapshot_json = JSONField(null=True, blank=True, default=None)`.
- **Snapshot shape:** `{"red":[{"player_id":int,"name":str,"role":str,"stats":{<13 _SIMULATION_STATS>:int}}],"blue":[...]}`. 13 stats: `accuracy, survival, player_awareness, game_awareness, decision_making, stamina, special_usage, resupply_efficiency, resupply_synergy, teamwork, communication, resource_awareness, speed`.
- **Migration:** `matches/migrations/0055_gameround_fidelity_roster_snapshot.py`, dep `0054_season_map_rotation`, two `AddField`, no `RunPython`.
- **Selector kwarg:** keyword-only `fidelity: str = "scores"` on `simulate_match`, `simulate_single_round_detailed`, `simulate_scheduled_round`, `save_games`, `_simulate_and_flush_round`, `_flush_to_db` (`entrypoints.py`), `persistence.flush_to_db`, `tournament_engine.play_next_node`, `tournament_engine.play_specific_node`.
- **Live overrides (`league_views.py::play_week_live`):** RR `simulate_scheduled_round(..., fidelity="full")` (`:2812`); playoff `play_specific_node(node, fidelity="full")` (`:2828`).
- **Persistence helpers (`persistence.py`):** `build_roster_snapshot(red_players, blue_players) -> dict`; `_players_by_id(red_players, blue_players) -> dict`; `_write_player_states(game_round, red_players, blue_players, role_starting_resources) -> dict`; `_write_combat_events(game_round, events, players_by_id) -> None`; `_write_highlights(game_round, events, red_players, blue_players) -> None`; `_write_movement_events(game_round, red_players, blue_players, movement_ctx, players_by_id) -> None`; `_write_cell_occupancy(game_round, red_players, blue_players, movement_ctx) -> None`.
- **New primitive:** `BatchSimulator.ensure_fidelity(self, game_round, target_fidelity: str) -> "GameRound"` (`@transaction.atomic`); idempotent no-op when `FIDELITY_RANK[gr.fidelity] >= FIDELITY_RANK[target]` OR `roster_snapshot_json is None`; rebuild `_PlayerData` via `_roster_from_snapshot`, `load_map_context(gr.arena_map)`, `random.seed(gr.rng_seed)`, `_simulate_round(..., event_log=[], movement_ctx=...)`, backfill via the shared `_write_*` helpers, set `fidelity = target`. **Never rewrites scoreboard / `PlayerRoundState`.**
- **`_PlayerData` rebuild key:** `_PlayerData(player_id, name, stats)` whose `stat_for_simulation(stat, role)` returns `stats[stat]`; rosters are `list[tuple[role, _PlayerData]]`.
- **`scores` no-buffer:** `_simulate_and_flush_round` (and `save_games`) choose `event_log = [] if FIDELITY_RANK[fidelity] >= FIDELITY_RANK["combat"] else None`.
- **View wiring (`matches/views.py`):** `game_round_events` (`:1020`) → `ensure_fidelity(game_round, "full")`; `movement_heatmap` (`:1091`) → `"full"`; `missile_log` (`:1055`) → `"combat"`; `game_round_detail` UNCHANGED (`scores`).
- **Test files:** NEW `matches/tests/test_fidelity.py` (`TestFidelityGating`, `TestEnsureFidelityIdempotency`, `TestEnsureFidelitySnapshotGuard`, `TestRosterSnapshotFaithfulness`, `TestFidelityEquivalence`); EXTEND `matches/tests/test_simulation_view_paths.py` (persistence + default-`scores` regression); EXTEND `matches/tests/views_tests.py` (the three views' `ensure_fidelity` wiring).
