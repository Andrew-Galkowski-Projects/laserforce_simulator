# teams/

Manages teams, players, and rosters. Serves as the homepage (`/`).

## Models (`teams/models.py`)

**`Team`**: Has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role.

**`Player`**: Belongs to a team and has an assigned role. Carries ~20 numeric stats (0–100) used as weights by the simulator. Key stats include `accuracy`, `aggressiveness`, `awareness`, `missile_use`, `special_use`, and role-specific proficiency fields.

`ROLE_STATS` is a module-level dict that maps each role to its starting resources (lives, shots, special charges, missiles) and combat stats (shields, shot_power). The `BatchSimulator` mirrors this in `sim_helpers/player_state.py` to avoid Django imports.

## URLs

```
/           → team list (homepage)
/teams/     → team CRUD, player management
```

## Tests

`teams/tests.py` — team/player model behavior, views.