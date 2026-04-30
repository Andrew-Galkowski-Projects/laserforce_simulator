# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

All commands run from the `laserforce_simulator/` subdirectory (where `manage.py` lives):

```bash
# Development server
python manage.py runserver

# Apply migrations
python manage.py migrate

# Create new migrations after model changes
python manage.py makemigrations

# Run all tests with coverage
pytest

# Run a single test file
pytest matches/tests.py

# Run a specific test class or method
pytest matches/tests.py::ClassName::method_name
```

CI runs `pytest` with coverage and uploads to Codecov (see `.github/workflows/ci.yml`). Python version is 3.11.

## Architecture

This is a Django 5.2 app that simulates competitive laser tag (Laserforce) matches. The root URL serves the `teams` app as the homepage; `matches` and `teams` are the two meaningful apps. The `core` app is a placeholder.

### Data Model Hierarchy

```
Match (2 rounds, winner by rounds then points)
  └── GameRound (1 of the 2 rounds; 15-minute simulation)
        ├── PlayerRoundState (one per player, tracks all resources/stats)
        └── GameEvent (chronological log of every in-game action)
```

**Team/Player** (`teams/`): A `Team` has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role. Players have ~20 numeric stats (0–100) used as weights by the simulator.

**Match** (`matches/models.py`): Two `GameRound`s; teams swap colors between rounds. Winner is determined by rounds won, then total cumulative points. A 10,000-point bonus is awarded for eliminating the opposing team entirely.

**PlayerRoundState** (`matches/models.py`): Starting resources are role-dependent (lives, shots, special, missiles). Tracks final resource counts, tags, misses, zone visits, MVP score. `was_eliminated_at` stores seconds into the round (901 = survived the full round). The MVP formula is role-specific and weighted heavily toward that role's primary contribution.

**GameEvent** (`matches/models.py`): Every action (tag, missile, special, miss, resupply, base capture, elimination) is logged here with an actor, optional target, timestamp in seconds, points, and a JSON `metadata` field.

### Simulation Engine (`matches/simulation.py`)

`ResourceBasedSimulator` is the active simulator. It runs in 2-second ticks over 900 seconds:

1. Process pending missiles/nukes that have completed their delay
2. Each active player picks an action (weighted random by role, zone context, remaining resources)
3. Resolve the action — update `PlayerRoundState` fields and write a `GameEvent`
4. Check for eliminations after each tick
5. Return aggregated round results

Action weights are in `matches/sim_helpers/weights.py` — separate functions per role (`_get_medic_weights`, `_get_heavy_weights`, etc.) that return a dict of action → weight. Weights shift based on remaining lives, special charges, zone, and allied presence.

`SimpleMatchSimulator` is an older, simpler fallback; prefer `ResourceBasedSimulator` for new work.

### Role Mechanics

| Role | Shields/Shot Power | Has Missiles | Can Resupply |
|------|-------------------|--------------|--------------|
| Commander | 2 / 3 | Yes | No |
| Heavy | 3 / 3 | Yes | No |
| Scout | 1 / 1 | No | No |
| Medic | 1 / 1 | No | Yes (lives) |
| Ammo | 1 / 1 | No | Yes (shots) |

Shields absorb damage; depleting shields at 0 lives causes elimination. Respawn after being tagged requires an 8-second cooldown. Zone values: 0 = red_zone, 1 = neutral_zone, 2 = blue_zone.

### URL Structure

```
/                    → team list (homepage)
/teams/              → team CRUD, player management
/matches/            → match list, create, detail
/matches/game-round/<id>/        → detailed round view
/matches/game-round/<id>/events/ → event timeline/filtering
/matches/team/<id>/history/      → team win/loss history
```

### Templates

All templates live in `laserforce_simulator/templates/`. The `game_round_events.html` template has event filtering and color-coded display; `game_round_detail.html` shows per-player stats and MVP scores.