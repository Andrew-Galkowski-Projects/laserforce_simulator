# teams/

Manages teams, players, and rosters. Serves as the homepage (`/`).

## Models (`teams/models.py`)

**`Team`**: Has exactly 6 `Player` slots — one each of Commander, Heavy, Scout, Medic, Ammo, plus one duplicate role.

**`Player`**: Belongs to a team and has an assigned role. Carries ~20 numeric stats (0–100) used as weights by the simulator. Key stats include `accuracy`, `aggressiveness`, `awareness`, `missile_use`, `special_use`, and role-specific proficiency fields.

`ROLE_STATS` is imported from `matches.sim_helpers.role_constants` — the canonical source for all role-level constants (`ROLE_STATS`, `MAX_LIVES`, `MAX_SHOTS`, `SPECIAL_COST`). Both `teams/models.py` and `sim_helpers/player_state.py` import from there; the duplicate definition that previously lived in `player_state.py` has been removed.

## REST API (`teams/serializers.py`, `teams/api_views.py`)

Read-only DRF endpoints registered under `/api/`:

| Endpoint | Serializer | Notes |
|----------|-----------|-------|
| `GET /api/teams/` | `TeamListSerializer` | Slim — nested players include id/name/preferred_roles only |
| `GET /api/teams/<id>/` | `TeamSerializer` | Full — nested players include all 19 stats |
| `GET /api/players/` | `PlayerSerializer` | Paginated, ordered by team then name |
| `GET /api/players/<id>/` | `PlayerSerializer` | Full player detail |

**Serializer split:** `TeamListSerializer` (list) nests `PlayerInlineSerializer` (id, name, preferred_roles) to keep list payloads small. `TeamSerializer` (detail) nests the full `PlayerSerializer` with explicit stat fields. Both share `_TEAM_BASE_FIELDS` and `_PLAYER_STAT_FIELDS` constants so the field lists are defined once.

**`PlayerInlineSerializer`** — minimal player representation (id, name, preferred_roles) for use anywhere a full player is not needed.

**`PlayerSerializer`** — all 19 stats; explicit field list guarding against accidental exposure of future model fields.

## URLs

```
/           → team list (homepage)
/teams/     → team CRUD, player management

/api/teams/         → TeamViewSet (list, detail)
/api/players/       → PlayerViewSet (list, detail)
```

## Tests

`teams/tests/` package — split by concern:
- `test_models.py` — roster validation (FIX-01 coverage)
- `test_serializers.py` — PlayerSerializer, PlayerInlineSerializer, TeamSerializer, TeamListSerializer
- `test_apis.py` — HTTP-level tests for `/api/teams/` and `/api/players/`