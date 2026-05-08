# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

---

## Phase 0 — Immediate Fixes (blockers)

These are bugs and technical debt that corrupt simulation results or mislead future development. 
Fix before building anything new.

### FIX-01 · Enforce Scout-only role doubling
`teams/models.py` — `Player.clean()` currently allows 2 Commanders, 2 Medics, etc. 
Only Scout may appear twice in an SM5 roster. Fix the validation, show a clear error on the team detail page for any 
existing bad rosters, and add unit tests covering all valid and invalid compositions.
- completed

### FIX-01b · Block match creation on invalid rosters
`matches/views.py` — The match and single-round creation views must check `is_valid_roster` on both teams before 
calling the simulator. Return a form error with the specific composition problem; never pass a
broken roster to `ResourceBasedSimulator`.
- completed, currently we check roster errors and return any for both teams before attempting to run the simulator.

### FIX-02 · Derive shot_power and shield from role
`teams/models.py` — `shot_power` and `shield` are stored as DB columns but should be computed from the player's role. 
Convert to `@property` on `Player`, delete the DB columns, and update any simulator code that reads them directly.

### FIX-03 · Remove SingleRound legacy model and route
`matches/models.py` — `SingleRound` is superseded by `GameRound`. Remove the model, its migration, 
the `/matches/round/<id>/` route, and its view. Update any templates that still link to it.

### FIX-04 · Clean up stale TODO comments in get_mvp
Minor — remove or update the two stale TODO comments in `PlayerRoundState.get_mvp`. 
Add a docstring explaining the weighting formula. No functional change.

---

## Phase 1 — Map–Simulation Integration

The map editor produces a rich cell grid with precomputed sight lines. Currently the simulator ignores it, 
using only 3 abstract zones. This phase replaces the 3-zone model with full map awareness.

### MAP-01 · Player position on the cell grid
Replace `PlayerRoundState.current_zone` (0/1/2) with a `(row, col)` cell coordinate. On round start, 
place players on or near their team's base cell. Persist the active zone_size and map for the round on 
`GameRound` so all queries are keyed consistently.

**Data changes:** `GameRound` gets `arena_map` FK and `zone_size` field. `PlayerRoundState` gets `cell_row` 
and `cell_col` integers. Keep `current_zone` as a derived property (red/neutral/blue based on cell's zone type)
for backwards compatibility with existing views.

### MAP-02 · Cell-aware zone movement
Replace `_change_zone()` with a pathfinding step that moves a player to an adjacent passable cell each tick. 
Use the `SightLineConfig` adjacency data (cells that can see each other share an edge or corridor) 
as a proxy for connectivity, or derive an explicit adjacency list from `MapZoneConfig.zones`. 
Players navigate toward a goal cell (enemy base, ally position, nearest resupply) using a simple weighted heuristic.

**Acceptance:** A player starting at their home base will reach the enemy base in a realistic number of ticks
proportional to map size. Players never move into wall cells.

### MAP-03 · Line-of-sight targeting
Replace the current "same zone = can tag" rule with LOS-based targeting. 
A player can tag any enemy whose cell appears in the `SightLineConfig` adjacency list for the actor's current cell. 
Pull sight data from the precomputed `SightLineConfig` at round start and hold in memory for the duration.

**Acceptance:** Two players separated by a wall cannot tag each other. Players across a corridor can. 
Hit-chance formula remains the same; only target eligibility changes.

### MAP-04 · Base interaction via BaseSightLineConfig
Replace the abstract base-capture zone check with `BaseSightLineConfig` lookups. A player can interact with a base 
(capture, resupply trigger) only if their current cell appears in `visible_cells` for that base. 
Load `BaseSightLineConfig` at round start alongside `SightLineConfig`.

### MAP-05 · Role-aware goal selection
Update the weight functions in `weights.py` to express goals in terms of target cells rather than abstract zones. 
Each role picks a goal cell (enemy base, ally player position, nearest cover) and the movement action moves 
one step toward it. Scouts prioritize high-LOS cells; Heavies hold near chokepoints; 
Medics/Ammos follow the ally they intend to resupply.

### MAP-06 · Fallback for rounds without a map
When `GameRound.arena_map` is null (map not assigned), fall back to the existing 3-zone logic so that existing tests
and simulations without maps continue to work. This is a compatibility shim — new matches should always have a map.

### MAP-07 · Map wall hazards
Maps provided can have multiple different wall types: low walls that block movement but not sight, 
high walls that block both movement and sight, mirrored/reflective walls that shots can be bounced off of to hit
players around corners, and windowed walls that block sight but allow tagging through them. Add a `wall_type` field
to the map data and update the movement and targeting logic to respect these distinctions. 
For example, a player behind a low wall can be targeted if the attacker has LOS to the wall cell, 
but not if there's a high wall in between.

### MAP-08 · Map-based spawn points
Players should spawn within 5-10 cells of their base's cell

### MAP-09 · High Ground
Some maps have multiple levels of elevation. Add a `height` attribute that both modifies hit-chance against players 
on high ground from low ground and also provides a small visibility bonus to players on high ground 
(more cells visible in `SightLineConfig`).
This should allow high ground players to shoot over some high walls
---

## Phase 2 — Player Stats Integration

Most of the 19 player stats exist on the model but are not used in simulation. This phase connects them.

### STAT-01 · Expose all 19 stats in the add/edit player UI
`teams/` — Both the add and edit player forms must render all 19 stat fields grouped by category 
(Awareness, Decision-making, Physical, Team, Role). New players default to 50 for all stats. 
Show `overall_rating` as a live-updating summary. Add a convenience "Set to Average / Elite" bulk preset.

### STAT-02 · Role-preference stat multipliers
Define `ROLE_STAT_WEIGHTS` mapping role → {stat_name: multiplier} for all 19 stats. 
Example: Scout `accuracy` weight = 1.5, Medic `accuracy` weight = 0.4, Medic `resupply_efficiency` weight = 2.0. 
Add `Player.stat_for_simulation(stat_name)` which returns `stat_value × role_weight`. Update `ResourceBasedSimulator`
and `weights.py` to use this method. Keep `overall_rating` as the unweighted display average.

### STAT-03 · Wire stats into action weight functions
Map each relevant stat to a weight modifier in `weights.py`:
- `accuracy` / `survival` — already used in hit-chance formula; confirm they feed in correctly
- `decision_making` — scales the spread between actions 
  - (high decision-making = weights more concentrated on optimal action)
- `positioning` — biases movement toward high-value cells (map integration; pairs with MAP-05)
- `stamina` — degrades action quality / effective hit-chance in second half of round
- `speed` — allows more cells traversed per tick (map integration)
- `special_usage` — scales special activation weight directly
- `resupply_efficiency` / `resupply_synergy` — scale resupply weight for Medic/Ammo
- `teamwork` / `communication` — scale ally-following behavior weight
- `game_awareness` / `player_awareness` — scale reaction to enemy nuke (see Phase 3)

### STAT-04 · Seed stats from match history
`teams/` — "Update stats from history" button on player edit page. Derives `accuracy` from
`tags_made/(tags_made+shots_missed)` ratio, `survival` from avg `was_eliminated_at`, `special_usage` 
from SP spend rate, across the player's `PlayerRoundState` history. Show a diff before applying. 
Only available after minimum 5 games.

---

## Phase 3 — Simulation Mechanics

New and corrected mechanics that make the simulator more faithful to SM5 rules and more interesting strategically.

### MECH-01 · Medic/Ammo follow-up combo tag
A Medic or Ammo can perform a "follow-up" action: tag an ally immediately after a resupply, 
granting that ally both a life restore (Medic) and a shot resupply (Ammo) in the same interaction. 
The ally and support player both receive the benefit. In game terms this represents a `double` since both players
are tagging the target at the same time. Implement as a new action type `combo_resupply` with its own weight 
(high when both Medic and Ammo are in the same cell as a low-resource ally). Create a `GameEvent` of 
type `combo_resupply` with both resource grants logged in `metadata`.

### MECH-02 · Base tag to reset same-target restriction
After tagging an enemy and depleting their shields (scoring a life), 
the attacker normally must wait 8 seconds before tagging the same target again. Implement a rule: 
tagging a neutral or opposing base resets this per-target cooldown for the attacker. 
Similarly, tagging any other enemy or ally in the same cell/radius also resets it. 
Track the "last tagged player" per attacker and clear it on base interaction or zone-wide tag of any other valid target.

### MECH-03 · Commander nuke stacking behavior
Currently Commanders almost never queue a second nuke during the first nuke's fuse window. 
Add a `nuke_aggression` derived value from `special_usage` and `game_awareness` stats. 
High `nuke_aggression` players will attempt to nuke stack (fire a second nuke before the first resolves). 
The weight for `use_special` during an active nuke fuse window should scale with this value rather
than being near-zero for all Commanders.

### MECH-04 · Player reaction to incoming nukes
When a pending nuke is in flight (fuse window active), players should react based on stats. Add a nuke-awareness 
check each tick for all active players on the target team:
- High `game_awareness` + `player_awareness`: player attempts to tag the Commander to cancel the nuke 
- (raises `tag_player` weight toward the Commander specifically, regardless of zone)
- High `survival`: player hides or moves to a different cell to reduce the nuke's impact (hide weight increases)
- Low awareness stats: player ignores the nuke and continues their normal action

### MECH-05 · Nuke cancellation fuse window fix (SIM-03)
Verify and correct the nuke cancellation logic: a nuke must be cancelled if the firing Commander is eliminated 
during the fuse window (not just at exact timestamps). Write a regression test: Commander fires nuke at T=100, 
gets tagged at T=103 (within fuse), nuke must not detonate.

---

## Phase 4 — Analytics & Review

Surfaces the data already being collected. No new simulation work required.

### RES-01 · Accuracy % on round detail (quick win)
Add `accuracy_pct` as a `@property` on `PlayerRoundState`: `tags_made / (tags_made + shots_missed) × 100`. 
Display in the currently-blank Accuracy % column on `/matches/game-round/<id>/`. Covered by a unit test.

### RES-02 · SP timeline chart
Chart SP over time per player on `/matches/game-round/<id>/events/`, sourced from `GameEvent` rows. 
Spending events shown as downward spikes. SP cap (99) shown as a reference line.

### RES-03 · Missile usage log
Filter event log by type `missile`. Each row: timestamp, actor role, target role, result. 
Friendly fire highlighted. Summary: total fired, total hit, efficiency %.

### RES-04 · Zone/cell movement heatmap (post-MAP)
After Phase 1, players have cell coordinates. Aggregate time-in-cell across a round and render as a heatmap overlay
on the map image. Filter by player. Per-zone time-in-zone bar chart as a simpler fallback before full map integration.

### RV-01 · Compare two rounds side by side
`/matches/compare/?round_a=<id>&round_b=<id>` — per-player stat delta table with green/red colouring. 
Points Over Time overlay chart. Rounds must share at least one team.

### RV-02 · Auto-flag highlights
Detect: nuke events, first elimination, largest 30-second point swing, team elimination, base destructions. 
Show as a "Highlights" tab on the events page. Store results in `GameRound.highlights_json` (new field) at round completion.

### RV-03 · Export round report as PDF
`GET /matches/game-round/<id>/export/` — WeasyPrint or ReportLab. Contains round summary, scoreboards, 
per-player table, resource summary. "[Simulated]" watermark on simulator-generated rounds.

### SIM-01 · Document and test action weights
Add docstrings to every weight function in `weights.py`. Cover weight sums with unit tests. 
Provide a clearly documented constant dict so weights are adjustable without touching logic code.

### SIM-02 · Batch simulation mode
`POST /matches/simulate-batch/` — accepts `red_team_id`, `blue_team_id`, `n` (10/50/100/500). 
Runs `ResourceBasedSimulator` n times, returns aggregate stats (win%, avg score, avg survivors, 
score distribution histogram). Background task if sync >5 seconds. Results not stored as permanent Match records.

### SIM-04 · Simulation confidence display
Per-player data source label ("40 real games" vs "Role defaults — no history") on simulation summary. 
Team-level confidence badge: Low (<5 games), Medium (5–20), High (>20). Link to edit stats from confidence panel.

### SIM-05 · Simulation replay playback
Play/Pause/Step/Speed controls on the events page. Event timeline highlights current event; 
live resource counters update per event. State managed client-side; no additional backend endpoint needed.

### HX-01 · Per-player career stats page
`/teams/<id>/player/<pid>/stats/` — aggregated `PlayerRoundState` across all rounds: games played, 
avg points, K/D ratio, avg survival time, avg accuracy, avg SP earned. Per-role breakdown. 
Trend chart: avg points per game over time.

### HX-02 · Role benchmarks
Global benchmark averages per role computed from all `PlayerRoundState` records. 
Player stat shown with +/− delta and percentile rank vs role average. Recomputed on demand or nightly.

### HX-03 · Head-to-head record
`/matches/h2h/?team_a=<id>&team_b=<id>` — W/L record, avg score margin, avg survivors, 
most impactful player across all H2H matches.

### PR-01 · Pre-match win probability forecast
`/matches/forecast/?red=<id>&blue=<id>` — triggers 100-sim batch (requires SIM-02 and STAT-02). 
Shows win% per team, projected score range (10th–90th percentile), projected avg survivors, per-player risk flags.

### PR-02 · Roster composition comparison
Two side-by-side roster selectors vs same opponent, each running 100 sims. Side-by-side win%, avg score, avg survivors.
Recommended scenario highlighted with rationale.

### PR-03 · What-if scenario editor
Fork a real `GameRound`, change one variable (swap role, adjust stat, change player), 
re-simulate, show diff vs original. Forked scenario is temporary, not a permanent Match record.

### STAT-03-UI · Stat derivation from history (pairs with STAT-04)
See Phase 2 entry. Surfaces in Analytics phase as a user-facing button.

---

## Phase 5 — Infrastructure & League System

### API-01 · Migrate to PostgreSQL for production
`settings.py` — read `DATABASE_URL` via `dj-database-url`. SQLite for local dev/CI; PostgreSQL in production. 
Verify all migrations against PostgreSQL. Update GitHub Actions to spin up a Postgres service container.

### API-02 · Read-only REST API
Add Django REST Framework (or django-ninja). Endpoints: `GET /api/teams/`, `GET /api/teams/<id>/`,
`GET /api/matches/<id>/`, `GET /api/rounds/<id>/`, `GET /api/rounds/<id>/events/`. Pagination (default 20). 
Token auth for API consumers; session auth for web views.

### API-03 · Async batch simulation endpoint
`POST /api/simulate-batch/` — returns `job_id` immediately. Background worker (Celery + Redis or Django-Q) processes. 
`GET /api/simulate-batch/<job_id>/` polls status and returns results. Frontend progress bar. Jobs expire after 1 hour.

### LG-00 · Player Generation Tools
There should be a way for players to generate new rosters of players to play in a season/league/tournament.
This can be as simple as "generate league/season/tournament X with Y teams" 
these stats would be randomized on a bell curve with some variance.

### LG-00b · Roster Import from CSV
Allow users to import a roster of players from a CSV file. The CSV should have columns for player name, role, and stats.


### LG-01 · Seasons and standings
New `Season` model: name, start/end dates, enrolled teams (M2M). Standings: W/L/T, points (3W/1T/0L),
round wins, total score. Matches linked to season via FK. Active vs completed states.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory 

### LG-02 · Tournament bracket
Single-elimination bracket for 4/8/16 teams; seeded by standings or manual order. 
Results auto-advance winners. Bracket rendered as a visual tree.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory

### LG-03 · Season-end awards
Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

---

## Phase 6 — Users and Multiplayer

### UX-01 · User accounts and team ownership
Django auth system. Users can see teams and players they have created, along with leagues/seasons/tournaments.
Permissions: only team owners can edit their teams/players; read-only access to others.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory



## Phase 7 — Docker & Production Deployment

The app currently runs only on a local dev machine. This phase makes it deployable as a Docker container
to any cloud host. It can be done as soon as Phase 0 is complete — you don't need to wait for later phases.
Deploy early with the Django template UI; re-deploy again as features land.

**What Docker is:** a container packages the app + all its dependencies into a single portable unit that
runs the same way on any machine or cloud host, eliminating "it works on my machine" problems.

### DEPLOY-01 · Environment variable configuration
`settings.py` currently has `SECRET_KEY`, `DEBUG = True`, and `ALLOWED_HOSTS` hardcoded. In production
these must come from environment variables so secrets are never in the repository.

- Add `python-decouple` to `requirements.txt`
- Rewrite the relevant `settings.py` values to read from env vars with safe defaults:
  `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS`, `DATABASE_URL`
- Add a `.env` file for local development (contains real values, never committed)
- Add a `.env.example` file (placeholder values, committed as documentation)
- Add `.env` to `.gitignore`

### DEPLOY-02 · Production WSGI server (gunicorn)
Django's built-in `runserver` is a dev-only server — it is single-threaded and not safe for production.
`gunicorn` is the standard production server for Django.

- Add `gunicorn` to `requirements.txt`
- Confirm the app starts with: `gunicorn laserforce_simulator.wsgi:application --bind 0.0.0.0:8000`

### DEPLOY-03 · Static file serving (WhiteNoise)
In production, Django does not serve its own CSS/JS/images — a separate web server normally does that.
WhiteNoise lets Django serve them directly from the container without needing a separate nginx process.

- Add `whitenoise` to `requirements.txt`
- Add `WhiteNoiseMiddleware` to `MIDDLEWARE` in `settings.py` (must come directly after `SecurityMiddleware`)
- Set `STATIC_ROOT = BASE_DIR / "staticfiles"` so `collectstatic` knows where to write files
- `collectstatic` will be run during the Docker image build step (DEPLOY-06)

### DEPLOY-04 · Media file storage for map uploads (Cloudflare R2 or S3)
Uploaded map images are "media files" stored on disk by default. In a Docker container the disk is
ephemeral — files written during one deploy disappear when the container restarts. They must be stored
in an external object-storage service instead.

- Recommended: Cloudflare R2 (free tier, S3-compatible API)
- Add `django-storages[s3]` and `boto3` to `requirements.txt`
- Configure `DEFAULT_FILE_STORAGE = "storages.backends.s3boto3.S3Boto3Storage"` in production settings
- Credentials (bucket name, access key, secret) added as environment variables — never hardcoded
- Test: upload a map image in the editor and verify the file URL points to R2, not local disk

### DEPLOY-05 · PostgreSQL database (see also API-01 in Phase 5)
SQLite writes to a single file on disk. Like media files, this disappears when a container restarts and
doesn't support multiple concurrent connections well. PostgreSQL is the production-grade replacement.

- Add `psycopg2-binary` and `dj-database-url` to `requirements.txt`
- Replace the `DATABASES` block in `settings.py` with `dj_database_url.config(default="sqlite:///db.sqlite3")`
  so SQLite is still used locally and PostgreSQL is used in production via `DATABASE_URL` env var
- Run all migrations against PostgreSQL and confirm they pass
- Update GitHub Actions to spin up a `postgres` service container for CI
- Note: this is the same work as API-01 in Phase 5 — the two can be merged/done together

### DEPLOY-06 · Dockerfile
The Dockerfile defines exactly how to build the container image. Uses a two-stage build: the first
stage installs all Python dependencies; the second stage copies only what's needed to run.

```dockerfile
# Stage 1 — install dependencies
FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2 — runtime image
FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY . .
RUN python laserforce_simulator/manage.py collectstatic --noinput
EXPOSE 8000
CMD ["gunicorn", "laserforce_simulator.wsgi:application", "--bind", "0.0.0.0:8000", "--chdir", "laserforce_simulator"]
```

### DEPLOY-07 · docker-compose.yml for local development
`docker-compose` lets you run the full production-like stack locally with one command: app + PostgreSQL together.

- `app` service: builds from the Dockerfile, mounts source code for live edits, loads `.env`
- `db` service: official `postgres:16` image, data volume so the database survives container restarts
- `docker compose up` replaces `python manage.py runserver` for a production-like local environment
- `docker compose run app python manage.py migrate` runs migrations inside the container

### DEPLOY-08 · CI pipeline update
Update `.github/workflows/ci.yml` to build the Docker image and verify it starts cleanly, so a broken
Dockerfile is caught before it reaches a real deployment.

- Add a `docker build` step after the existing `pytest` step
- Add a smoke test: start the container, hit `/`, expect HTTP 200

---

## Phase 8 — Angular Frontend Migration

Replaces Django's server-rendered HTML templates with an Angular single-page application (SPA).
Django becomes a pure API backend; Angular handles all UI in the browser. This phase requires Phase 5's
API-02 (REST API) to be complete and deployed (Phase 7) before starting.

**What Angular is:** a TypeScript framework for building SPAs — the server sends one HTML page and all
navigation/rendering happens in JavaScript in the browser, talking to the backend via API calls.

**Approach:** migrate one feature area at a time. Django templates remain live until the Angular
equivalent is complete and verified. There is no big-bang cutover.

### ANG-01 · Harden and complete the REST API (prerequisite)
API-02 in Phase 5 establishes the REST API skeleton. Before building Angular against it, ensure:

- All endpoints needed by the UI exist: teams, players, matches, rounds, events, maps
- Consistent JSON envelope (data, pagination, errors)
- Filtering and pagination on list endpoints
- Proper HTTP error codes (400 for validation, 404 for missing records, etc.)

### ANG-02 · CORS configuration
During development Angular runs on `http://localhost:4200` and Django runs on `http://localhost:8000`.
Browsers block cross-origin requests by default — CORS headers tell the browser to allow them.

- Add `django-cors-headers` to `requirements.txt`
- Add `CorsMiddleware` to `MIDDLEWARE` (before `CommonMiddleware`)
- Set `CORS_ALLOWED_ORIGINS = ["http://localhost:4200"]` for dev; production domain added when known

### ANG-03 · JWT authentication
Django's default session auth (cookies) doesn't work cleanly for SPAs. JSON Web Tokens (JWTs) are the
standard alternative: login returns a short-lived access token and a longer-lived refresh token;
Angular sends the access token with every API request.

- Add `djangorestframework-simplejwt` to `requirements.txt`
- Add `/api/token/` (login) and `/api/token/refresh/` endpoints
- Angular stores tokens in memory (not `localStorage` — avoids XSS token theft)
- Angular `HttpInterceptor` attaches `Authorization: Bearer <token>` to every API request automatically
- Note: this work is only required if Phase 6 (user accounts) is in scope; skip if the app stays public

### ANG-04 · Angular project scaffold
One-time setup of the Angular application. Lives in a `/frontend/` directory at the repo root,
separate from the Django project.

```bash
# Prerequisites: Node.js LTS + npm
npm install -g @angular/cli
ng new frontend --routing --style=scss --strict
cd frontend
ng add @angular/material   # Material Design component library
```

Key files:
- `frontend/src/app/` — all Angular components and services live here
- `frontend/src/environments/` — `environment.ts` (dev API URL) and `environment.prod.ts` (prod API URL)
- `frontend/angular.json` — build config

### ANG-05 · Angular API services
One Angular service per Django API resource. Each service wraps the HTTP calls and returns typed
observables. Components never call `HttpClient` directly — they go through the service.

```
TeamsService     → GET/POST/PATCH /api/teams/
PlayersService   → GET/POST/PATCH /api/players/
MatchesService   → GET/POST       /api/matches/
RoundsService    → GET            /api/rounds/<id>/
EventsService    → GET            /api/rounds/<id>/events/
MapsService      → GET/POST       /api/maps/
```

### ANG-06 · Migrate views by feature area
Migrate one area at a time in order of complexity. Each item: build the Angular route/component,
verify feature parity with the existing Django template, then remove the Django template + view.

1. **Teams list & detail** — simple CRUD table + form; good first Angular component to build
2. **Player add/edit** — stat form with live `overall_rating` preview
3. **Match list & create** — team picker, match creation, results list
4. **Round detail** — per-player stat table, MVP scores
5. **Event timeline** — filtered event log, color-coded by type (SIM-05 replay controls slot in here)
6. **Map editor** — most complex: canvas overlay, zone painting, sight-line drag-select (migrate last)

### ANG-07 · Serve Angular from Docker
Once Angular is built (`ng build --configuration production`), it produces a `/frontend/dist/` folder
of static HTML/JS/CSS files. Two clean ways to serve it:

**Option A — nginx sidecar (recommended):**
Add a second `nginx` service to `docker-compose.yml`. nginx serves the Angular static files on port 80
and proxies `/api/` requests to the Django container on port 8000. Clean separation of concerns.

**Option B — Django serves Angular:**
Copy the Angular build output into Django's `STATIC_ROOT`. Works but mixes concerns and requires
a full Django rebuild whenever the frontend changes.

Go with Option A. Add `nginx.conf` and update `docker-compose.yml` with the `nginx` service.

### ANG-08 · Remove Django template views
Once each Angular view is verified, delete the corresponding Django template file and its
HTML-serving view function. Keep the API endpoint. Update URL routing to remove the old path.
The app should have zero `.html` template files by the end of this phase (except Django admin).

---

## Sequencing Summary

```
Phase 0 (Fixes)
  → Phase 7 (Docker & Deployment) ← do this early; ship the Django template UI to prod
  → Phase 1 (Map Integration)  ← required for meaningful positional mechanics
    → Phase 2 (Stats Integration)  ← required before Phase 3 stat-driven behaviors
      → Phase 3 (Simulation Mechanics)
        → Phase 4 (Analytics — most of this can run in parallel with Phase 3)
          → Phase 5 (Infrastructure & League)  ← API-02 REST API is required before Phase 8
            → Phase 6 (Users and Multiplayer)
              → Phase 8 (Angular Frontend Migration)
```

Phase 4 items RES-01 (accuracy %), RES-02 (SP chart), RES-03 (missile log), and SIM-01 (document weights)
are quick wins that can be done any time after Phase 0.

Phase 7 (Deployment) can be done in parallel with any feature phase — re-deploy as features land.