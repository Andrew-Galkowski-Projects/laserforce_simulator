# Laserforce Simulator

A Django web app that simulates competitive [Laserforce](https://www.laserforce.com/) (laser tag) matches. Build teams, assign player stats, and run tick-by-tick match simulations with full event logs and MVP scoring.

## Features

- **Team & player management** — Create teams with 6 role-based player slots (Commander, Heavy, Scout, Medic, Ammo, + one duplicate)
- **Match simulation** — 2-round matches, 15 minutes each, simulated in 2-second ticks with role-specific action weights
- **Role mechanics** — Shields, lives, missiles/nukes, resupply, special charges, and zone movement all modeled
- **Game event log** — Every tag, miss, missile, resupply, and base capture is recorded with timestamps and points
- **MVP scoring** — Role-specific formulas weighted toward each role's primary contribution
- **Game replay** — Step through match events chronologically with per-player stat tracking
- **Team history** — Win/loss records across all matches

## Tech Stack

- Python 3.11 / Django 5.2
- SQLite (default); PostgreSQL in production via `dj-database-url`
- `python-decouple` for environment variable configuration (`.env` for local, env vars in production)
- `gunicorn` as the production WSGI server (start with `gunicorn laserforce_simulator.wsgi:application --bind 0.0.0.0:8000`)
- `whitenoise` for production static file serving directly from Django (no separate nginx required)
- `django-storages` + `boto3` for Cloudflare R2 media storage in production (set `R2_*` env vars to activate; falls back to local disk when unset)
- pytest + pytest-django for testing, with Codecov coverage reporting

## Getting Started

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

## Running Tests

```bash
# From the laserforce_simulator/ directory
cd laserforce_simulator

# Run all tests with coverage
pytest

# Run a single test file
pytest matches/tests/simulation_tests.py

# Run a specific test class or method
pytest matches/tests/simulation_tests.py::ClassName::method_name
```

## Project Structure

```
laserforce_simulator/
├── matches/               # Match, GameRound, PlayerRoundState, GameEvent models
│   ├── simulation.py      # ResourceBasedSimulator — main simulation engine
│   ├── sim_helpers/
│   │   └── weights.py     # Per-role action weight functions
│   ├── models.py
│   ├── views.py
│   └── tests/
├── teams/                 # Team and Player models, CRUD views
└── laserforce_simulator/  # Django project settings and URLs
```

### Simulation Engine

`ResourceBasedSimulator` runs a 900-second round in 2-second ticks:

1. Resolve any pending missiles/nukes whose delay has elapsed
2. Each active player picks an action (weighted random by role, zone, remaining resources)
3. Resolve the action — update player state and write a `GameEvent`
4. Check for eliminations; award a 10,000-point bonus for wiping the opposing team

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
| `/` | Team list (homepage) |
| `/teams/` | Team & player management |
| `/matches/` | Match list and creation |
| `/matches/create/` | Create a full 2-round match |
| `/matches/single-round/create/` | Create a standalone game round |
| `/matches/simulate-batch/` | Run N in-memory simulations |
| `/matches/game-round/<id>/` | Per-player round stats |
| `/matches/game-round/<id>/events/` | Filterable event timeline |
| `/matches/team/<id>/history/` | Team win/loss history |
