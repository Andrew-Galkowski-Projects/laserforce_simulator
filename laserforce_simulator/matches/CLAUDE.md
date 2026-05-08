# matches/

Handles match creation, game round simulation, event logging, and result views.

## Models (`matches/models.py`)

**`Match`**: Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**`GameRound`**: One of the two rounds in a match; represents a 15-minute simulation.

**`PlayerRoundState`**: Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores seconds into the round (901 = survived the full round). The MVP formula is role-specific and weighted heavily toward that role's primary contribution. Also tracks `follow_up_shots`, `reaction_shots`, and uptime breakdown fields (`seconds_active`, `seconds_not_targetable`, `seconds_reset_window`).

**`GameEvent`**: Every action (tag, missile, special, miss, resupply, base capture, elimination) is logged here with an actor, optional target, timestamp in seconds, points, and a JSON `metadata` field.

## Simulation Engine (`matches/simulation.py`)

Two simulators live in `matches/simulation.py`:

**`ResourceBasedSimulator`** — DB-backed, writes `GameEvent` rows and `PlayerRoundState`. Runs in 2-second ticks. Used for full match simulation with event replay. Prefer this when you need the game event log or a persisted round. All match and single-round creation views use this exclusively — the legacy `SimpleMatchSimulator` has been removed.

**`BatchSimulator`** — pure in-memory, no DB writes. Uses `PlayerState` dataclasses (see `matches/sim_helpers/player_state.py`). Runs in **0.5-second ticks** to model real shot speeds. Used by `score_averages` and batch win-rate analysis. A round typically runs in ~25 ms vs ~9 s for the DB-backed simulator.

Both simulators follow the same per-tick loop:

1. Process pending missiles/nukes that have completed their delay
2. Process pending deferred follow-up and reaction shots (shots scheduled by shot-cooldown logic)
3. Each active player picks an action (weighted random by role, zone, remaining resources)
4. Resolve the action — update state and optionally write a `GameEvent`
5. Check for team eliminations

Action weights are in `matches/sim_helpers/weights.py`. See [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) for details.

## Shot Speed & Follow-up Mechanics (BatchSimulator)

Real Laserforce shot speeds are modelled in `BatchSimulator`:

| Class | Shot cooldown | Notes |
|-------|--------------|-------|
| Scout with rapid fire | 0.0 s | Unlimited; follow-ups fire in the same tick |
| All others | 0.5 s | 2 shots/second |
| Heavy | 1.0 s | 1 shot/second |

`_shot_cooldown(player, second)` returns the cooldown. `_plan_action` zeroes the `tag_player` weight when `second - player.last_shot_time < cooldown`. `last_shot_time` is updated on every fired shot (hit, miss, or hidden-miss).

**Follow-up shots**: when a hit does NOT down the defender (shields > 0 after impact), the attacker may fire again. The follow-up is scheduled into `pending_followups` at `second + cooldown` and processed at the start of the next eligible tick. Rapid-fire scouts chain immediately in the same tick. Chain depth is capped at 2. A hit that takes shields to 0 is never eligible — a heavy always downs its target in one shot so never generates follow-ups.

**Reaction shots**: after being tagged or missed, the defender may fire back (rolled against `player_awareness`). Same cooldown scheduling logic applies.

## Role Mechanics

| Role | Shields / Shot Power | Has Missiles | Can Resupply |
|------|---------------------|--------------|--------------|
| Commander | 3 / 2 | Yes | No |
| Heavy | 3 / 3 | Yes | No |
| Scout | 1 / 1 | No | No |
| Medic | 1 / 1 | No | Yes (lives) |
| Ammo | 1 / 1 | No | Yes (shots) |

Shields absorb damage; a hit that reduces shields to 0 costs the defender one life and resets shields to max. Respawn after a life loss requires an 8-second cooldown (4 seconds taggable in the "reset window", 4 more seconds until fully active). Zone values: 0 = red_zone, 1 = neutral_zone, 2 = blue_zone.

**Heavy nerf**: heavies have 1 shot/second (vs 2/s for other roles) and always down their target in one hit, so they never generate follow-up shots.

**Scout rapid fire**: when the scout's special is active (`special_active_until > second`), `_shot_cooldown` returns 0.0, giving unlimited fire rate.

## Score Calibration Targets

Used by `score_averages` to measure simulation accuracy against real-world averages:

| Role | Target score |
|------|-------------|
| Commander | 9,952 |
| Heavy | 6,482 |
| Scout | 5,102 |
| Ammo | 3,242 |
| Medic | 2,282 |

## URLs

```
/matches/                            → match list, create, detail
/matches/create/                     → create a full 2-round match
/matches/single-round/create/        → create a standalone game round (always detailed)
/matches/game-round/<id>/            → detailed round view
/matches/game-round/<id>/events/     → event timeline/filtering
/matches/team/<id>/history/          → team win/loss history
/matches/simulate-batch/             → run N in-memory simulations
```

## Templates

All templates live in `laserforce_simulator/templates/`. The `game_round_events.html` template has event filtering and color-coded display; `game_round_detail.html` shows per-player stats and MVP scores.

## Tests

`matches/tests/simulation_tests.py` — simulator logic, game events, round outcomes.
`matches/tests/views_tests.py` — view behaviour: URL routing, form submissions, context keys.
`matches/tests/conftest.py` — shared `make_team_with_slots(prefix)` helper used by both test modules.

## Sub-packages

- [`sim_helpers/CLAUDE.md`](sim_helpers/CLAUDE.md) — `BatchSimulator` helper modules (`PlayerState`, action weights)
- [`management/commands/CLAUDE.md`](management/commands/CLAUDE.md) — `score_averages` and `game_analysis` management commands