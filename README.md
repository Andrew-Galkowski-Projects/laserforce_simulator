# Laserforce Simulator

A Django web app that simulates competitive [Laserforce](https://www.laserforce.com/) (laser tag) matches. Build teams, assign player stats, and run tick-by-tick match simulations with full event logs and MVP scoring.

## Features

- **Team & player management** — Create teams with 6 role-based player slots (Commander, Heavy, Scout, Medic, Ammo, + one duplicate)
- **Match simulation** — 2-round matches, 15 minutes each, simulated tick-by-tick (1 tick = 0.5 s, 1800 ticks/round) with role-specific action weights; seconds are a display-only conversion
- **Arena map support** — Optionally attach a confirmed arena map to any match or round; on the map every non-stationary player advances via A* pathfinding on the real cell grid toward their goal (enemy base, a critical ally, etc.) every tick — independent of their chosen action — recording a compact start/end `movement` event whenever their cell changes, from which the full path is reconstructed on replay
- **Wall hazards** — Three wall types on the map grid: high walls (block movement + sight), low walls (block movement, transparent to LOS), and windowed walls (block sight but allow directional tagging through a user-placed aperture). Wall types are painted in the map editor; windowed wall facing (N/S/E/W) determines which axis an attack can pass through
- **Role mechanics** — Shields, lives, missiles/nukes, resupply, special charges, and zone movement all modeled
- **Hold / overwatch** — A player can take up a stationary *hold*: it stops advancing and watches its line of sight, automatically firing a pre-emptive shot at the first enemy that enters — or moves through — its sight (distinct from a retaliatory reaction shot). Overwatch fire is resolved in batch simulations
- **Game event log** — Every tag, miss, missile, resupply, and base capture is recorded with tick timestamps and points (the JSON API returns raw ticks; the UI divides by 2 for seconds)
- **Highlights tab** — A "Highlights" tab on the event timeline auto-flags the key moments of a round: nuke detonations and cancellations, the first elimination, a team wipe, base captures, the biggest 30-second scoring burst, and medic reset chains
- **Round report PDF** — Export any round as a downloadable PDF (round summary, both scoreboards, a per-player stat table, and a per-team resource summary), generated server-side with ReportLab. Simulator-generated rounds are stamped with a diagonal "[Simulated]" watermark
- **MVP scoring** — Role-specific formulas weighted toward each role's primary contribution
- **Game replay** — Step through match events chronologically with per-player stat tracking; each persisted round also stores the RNG seed it was simulated with, so the exact game can be re-run (faithful while its rosters, map config, and side orientation are unchanged)
- **Batch simulation** — Run N games between two teams and view aggregate win % / scores. The two teams alternate which physical side (red/blue) they play across the run, so neither team's aggregate is biased by any map-side advantage; per-team stats are reported by team position regardless of side played, with a separate map-side-advantage panel showing the raw red-side vs blue-side signal
- **Sandbox tournaments** — Build a standalone single-elimination, double-elimination, round-robin, round-robin → double-elimination, or Swiss tournament from a seeded bracket: pick existing teams and/or generate new ones, reorder the seeding (default is overall-rating order), choose a series length per round depth (best-of-1, best-of-3, or best-of-5 per matchup), lock the bracket (arbitrary 4+ teams, with byes for the top seeds), then play it match-by-match while each matchup runs until one team clinches the series and winners auto-advance up a visual bracket tree to a crowned champion. Double elimination adds a losers bracket (a first loss drops a team into it rather than eliminating them) and a grand final with bracket reset (the winners-bracket champion must be beaten twice). Round robin instead has every team play every other team twice (one game per leg, best-of-1) and crowns the standings leader once all games are played, with a live standings table and an N×N results crosstable. Round robin → double elimination runs a round-robin seeding stage whose final standings seed a double-elimination finals stage. Swiss pairs an even field by seed fold in round one then by current standings each later round (never repeating an opponent unless forced), runs an auto-calculated ⌈log₂(N)⌉ rounds (admin-overridable), and crowns the standings leader broken by Buchholz. **Random Draw** is a separate team-assembly mode (orthogonal to the bracket format): instead of enrolling pre-set teams you register a pool of individual players — selected, generated, or CSV-imported — and the system runs a deterministic tier-balanced draw (six skill tiers, one player per tier per team) into balanced teams that then play the round-robin → double-elimination bracket, with each player's role re-assigned every round (either independently per team, or by a shared per-tier mapping so equal-tier players play the same role on both sides)
- **Season awards** — Each Season's awards page (`/seasons/<id>/awards/`) crowns the regular-season Most Points, Best Accuracy, K/D by role, Best Medic, Most Efficient Nuke, and Season MVP, plus a bracket-only Finals MVP; the Season MVP and Finals MVP also appear on the League History table and each player's profile (all recomputed on render — no stored awards)
- **Player development** — Each League's season rollover (`Start Next Season`) ages and develops every player on a ZenGM-style age curve (young players trend up, peak in their late 20s, older players decline), persisting a per-Season ratings snapshot viewable as an overall-rating-over-time trend on each player's League profile
- **One Week (Live)** — In a single-player career League, the season Play dropdown offers a "One Week (Live)" entry that previews your team's next game (the next-matchday Round, or your next playoff Match) in the browser with play/pause/scrub controls; you then commit the watched game (replayed byte-identically) — the rest of the matchday/bracket stage is simulated alongside it — or discard and try again
- **Manager firing (owner mood)** — In a single-player career League, the team owner evaluates you once per completed season on a ZenGM-style cumulative owner-mood model — *wins* (regular-season record vs a .500 baseline) plus *playoffs* (missed / advanced / champion of the season's tournament), each capped so you can't coast on one factor. After a two-season grace period, an overall mood at or below the firing threshold gets you fired; the owner-evaluation screen (`/seasons/<id>/owner-evaluation/`) shows the verdict, a hot-seat warning if you're trending toward the axe, and the per-factor breakdown, and is browsable for past seasons. A fired manager picks a fresh team from the five worst-performing clubs (your old team excluded) on a New Team screen, starting a new tenure and grace period. (A *money* factor and luxury-tax firing are reserved for a future finance subsystem.)
- **Team history** — Win/loss records across all matches
- **Read-only REST API** — JSON endpoints for teams, players, matches, rounds, and events at `/api/` (paginated, 20 per page)

## Tech Stack

- Python 3.11 / Django 5.2
- SQLite (default); PostgreSQL in production via `dj-database-url`
- `python-decouple` for environment variable configuration (`.env` for local, env vars in production)
- `gunicorn` as the production WSGI server (start with `gunicorn laserforce_simulator.wsgi:application --bind 0.0.0.0:8000`)
- `whitenoise` for production static file serving directly from Django (no separate nginx required)
- `django-storages` + `boto3` for Cloudflare R2 media storage in production (set `R2_*` env vars to activate; falls back to local disk when unset)
- `djangorestframework` for the read-only JSON API at `/api/`
- `psycopg2-binary` for PostgreSQL support in production (CI runs tests against `postgres:16`)
- pytest + pytest-django for testing, with Codecov coverage reporting
- Docker (multi-stage `python:3.11-slim`); `docker-compose.yml` for local dev with PostgreSQL; deployed to Fly.io via CI on push to `main`

## Getting Started

### Option A — local Python

```bash
# Clone the repo
git clone <repo-url>
cd laserforce_simulator

# Create and activate a virtual environment
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # macOS/Linux

# Install dependencies
pip install -r laserforce_simulator/requirements.txt

# Configure environment variables
cp laserforce_simulator/.env.example laserforce_simulator/.env
# Edit .env and set a real SECRET_KEY (the example value is a placeholder)

# Apply migrations
cd laserforce_simulator
python manage.py migrate

# Start the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

### Option B — Docker Compose (production-like stack)

```bash
# Copy and configure environment
cp laserforce_simulator/.env.example laserforce_simulator/.env
# Edit .env and set a real SECRET_KEY

# Start app + PostgreSQL
docker compose up

# (first run) migrations are applied automatically by entrypoint.sh
```

Then open http://localhost:8000/ in your browser.

## Running Tests

```bash
# From the laserforce_simulator/ directory
cd laserforce_simulator

# Run all tests with coverage
pytest

# Run a single test file
pytest matches/tests/test_batch_sim.py

# Run a specific test class or method
pytest matches/tests/test_map.py::TestMap02CellMovement
```

## Project Structure

```
laserforce_simulator/
├── matches/               # Match, GameRound, PlayerRoundState, GameEvent models
│   ├── simulation.py      # BatchSimulator (sole engine post-SIM-09) + MapData
│   ├── sim_helpers/
│   │   ├── combat.py      # Shared combat resolution (LOS, action planning, resupply, missiles)
│   │   ├── mechanics.py   # Pure game mechanics (shot cooldown, target selection, zone change)
│   │   ├── pathfinding.py # A* movement, adjacency building, goal selection
│   │   ├── map_loader.py  # Map-loading helpers (load_map_context, zone_from_cell) — SIM-09
│   │   ├── player_state.py# In-memory PlayerState dataclass for BatchSimulator
│   │   ├── role_constants.py # Canonical ROLE_STATS, MAX_LIVES, MAX_SHOTS, SPECIAL_COST
│   │   ├── score_calculator.py # calculate_mvp() pure function (extracted from PlayerRoundState)
│   │   └── weights.py     # Per-role action weight functions
│   ├── models.py
│   ├── views.py
│   └── tests/
├── teams/                 # Team and Player models, CRUD views
└── laserforce_simulator/  # Django project settings and URLs
```

### Simulation Engine

`BatchSimulator` runs an 1800-tick round (1 tick = 0.5 s; seconds are display-only, see [ADR-0001](docs/adr/0001-time-unit-seconds-now-tick-native-later.md)):

1. Resolve any pending missiles/nukes whose delay has elapsed
2. Each active player picks an action (weighted random by role, zone, remaining resources)
3. Resolve the action — update player state and write a `GameEvent`
4. On a map, every non-stationary player also advances toward their goal cell this tick — movement is decoupled from the chosen action; a player is stationary only while hiding, holding (overwatch), or capturing a base
5. Check for eliminations; award a 10,000-point bonus for wiping the opposing team

Action weights live in `matches/sim_helpers/weights.py` and shift dynamically based on remaining lives, special charges, current zone, and allied presence.

### Role Reference

| Role | Shields | Shot Power | Missiles | Resupply |
|------|---------|------------|----------|----------|
| Commander | 3 | 2 | Yes | — |
| Heavy | 3 | 3 | Yes | — |
| Scout | 1 | 1 | — | — |
| Medic | 1 | 1 | — | Lives |
| Ammo | 1 | 1 | — | Shots |

## URL Reference

| URL | Description |
|-----|-------------|
| `/` | Mode-picker landing (Sandbox / Single-player League / Multiplayer — Coming soon) + in-progress Leagues |
| `/teams/` | Team & player management |
| `/leagues/` | Single-player League list (active + archived) |
| `/seasons/<id>/awards/` | Season-end awards (Most Points, K/D by role, Best Medic, Most Efficient Nuke, Best Accuracy, Season MVP, Finals MVP) |
| `/seasons/<id>/owner-evaluation/` | Career owner evaluation — per-factor owner mood, verdict, hot-seat warning (browsable for past seasons) |
| `/leagues/<id>/new-team/` | New Team picker shown after a firing — choose from the five worst-performing eligible teams |
| `/matches/` | Match list and creation |
| `/matches/create/` | Create a full 2-round match |
| `/matches/single-round/create/` | Create a standalone game round |
| `/matches/simulate-batch/` | Run N in-memory simulations |
| `/matches/game-round/<id>/` | Per-player round stats |
| `/matches/game-round/<id>/events/` | Filterable event timeline |
| `/matches/team/<id>/history/` | Team win/loss history |
| `/tournaments/` | Sandbox tournament list |
| `/tournaments/create/` | Create a single-elim, double-elim, round-robin, RR → double-elim, or Swiss tournament (preset teams or a Random Draw player pool) |
| `/tournaments/<id>/` | Bracket tree, seeding, play controls, and (Random Draw) player-pool intake + draw |
| `/api/teams/` | Team list (paginated JSON) |
| `/api/teams/<id>/` | Team detail with full player stats |
| `/api/players/` | Player list (paginated JSON) |
| `/api/matches/<id>/` | Match detail with round ID list |
| `/api/rounds/<id>/` | Round detail with player states |
| `/api/rounds/<id>/events/` | Paginated event log for a round |
