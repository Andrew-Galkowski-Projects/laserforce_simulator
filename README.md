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
- **Round playback map** — On the event-log screen, replay the round on the arena map: player markers and fading movement trails animate along each player's true walked path (smoothed into flowing curves) over the processed map image, with the live scoreboard directly below and a play-synced event feed beside it — all driven by one play/pause/scrub clock. Rounds played without a map fall back to the scoreboard + feed
- **Batch simulation** — Run N games between two teams and view aggregate win % / scores. The two teams alternate which physical side (red/blue) they play across the run, so neither team's aggregate is biased by any map-side advantage; per-team stats are reported by team position regardless of side played, with a separate map-side-advantage panel showing the raw red-side vs blue-side signal
- **Sandbox tournaments** — Build a standalone single-elimination, double-elimination, round-robin, round-robin → double-elimination, or Swiss tournament from a seeded bracket: pick existing teams and/or generate new ones, reorder the seeding (default is overall-rating order), choose a series length per round depth (best-of-1, best-of-3, or best-of-5 per matchup), lock the bracket (arbitrary 4+ teams, with byes for the top seeds), then play it match-by-match while each matchup runs until one team clinches the series and winners auto-advance up a visual bracket tree to a crowned champion. Double elimination adds a losers bracket (a first loss drops a team into it rather than eliminating them) and a grand final with bracket reset (the winners-bracket champion must be beaten twice). Round robin instead has every team play every other team twice (one game per leg, best-of-1) and crowns the standings leader once all games are played, with a live standings table and an N×N results crosstable. Round robin → double elimination runs a round-robin seeding stage whose final standings seed a double-elimination finals stage. Swiss pairs an even field by seed fold in round one then by current standings each later round (never repeating an opponent unless forced), runs an auto-calculated ⌈log₂(N)⌉ rounds (admin-overridable), and crowns the standings leader broken by Buchholz. A **player-pool draw** is a separate team-assembly mode (orthogonal to the bracket format): instead of enrolling pre-set teams you register a pool of individual players — selected, generated, or CSV-imported — and the system runs a deterministic tier-balanced draw (six skill tiers, one player per tier per team) into balanced teams that then play the bracket in any of the five formats (played as round-robin → double-elimination, the default when you pick it, it is labelled a **Random Draw**), with each player's role re-assigned every round (either independently per team, or by a shared per-tier mapping so equal-tier players play the same role on both sides)
- **Season awards** — Each Season's awards page (`/seasons/<id>/awards/`) crowns the regular-season Most Points, Best Accuracy, K/D by role, Best Medic, Most Efficient Nuke, and Season MVP, plus a bracket-only Finals MVP; the Season MVP and Finals MVP also appear on the League History table and each player's profile (all recomputed on render — no stored awards)
- **Player development** — Each League's season rollover (`Start Next Season`) ages and develops every player on a ZenGM-style age curve (young players trend up, peak in their late 20s, older players decline), persisting a per-Season ratings snapshot viewable as an overall-rating-over-time trend on each player's League profile
- **One Week (Live)** — In a single-player career League, the season Play dropdown offers a "One Week (Live)" entry that previews your team's next game (the next-matchday Round, or your next playoff Match) in the browser with play/pause/scrub controls; you then commit the watched game (replayed byte-identically) — the rest of the matchday/bracket stage is simulated alongside it — or discard and try again
- **Live multi-game runs + Stop** — While a multi-game advance is running (Two Months / Until End of Season / Play Playoffs), the standings and leader boards update live as each fixture commits, and the Play control becomes a **Stop** button that cooperatively halts the run between games — already-played games stay committed and the run resumes on the next Play; progress survives navigating away or reloading the page
- **Member nights** — A Season's phase flow can include a **member night**: a casual/social play interlude run per Site (a venue's home-site grouping of players) between competitive fixtures. From the `Play ▾` topnav you pick a Site (or "All Sites present") and the system gathers whoever's available (enrolled-team players plus free agents), draws 5–9 ad-hoc games — each splitting 12 players into two attempt-balanced 6-player teams with randomized roles — then drains them in the background with live progress and a Stop button. Member-night games are recorded as part of the Season and show in raw match listings, but never count toward the competitive Standings or power rankings
- **Manager firing (owner mood)** — In a single-player career League, the team owner evaluates you once per completed season on a ZenGM-style cumulative owner-mood model — *wins* (regular-season record vs a .500 baseline) plus *playoffs* (missed / advanced / champion of the season's tournament), each capped so you can't coast on one factor. After a two-season grace period, an overall mood at or below the firing threshold gets you fired; the owner-evaluation screen (`/seasons/<id>/owner-evaluation/`) shows the verdict, a hot-seat warning if you're trending toward the axe, and the per-factor breakdown, and is browsable for past seasons. A fired manager picks a fresh team from the five worst-performing clubs (your old team excluded) on a New Team screen, starting a new tenure and grace period. With team finances enabled, the *money* factor (season profit) joins the mood model, and an optional per-League "fire on luxury tax" rule — set at League creation — fires you outright (independent of mood, but still after the grace period) any season your team pays the luxury tax; the owner-evaluation screen names which trigger fired you.
- **User accounts & ownership** — Email + password sign-in; registration is invite-only (staff create invites in Django admin, single-use by default or shared with a use cap and an expiry; the admin shows the sign-up link to send). Every Team, League, Tournament, sandbox Match and standalone Round belongs to the account that created it, and the whole site sits behind a login. Rows created before you had an account (or by a management command) are *unmanaged* and stay open to any signed-in account until claimed
- **Self-insert** — Link your account to one Player on a team you manage ("This is me" on the player's page) and that Player is flagged with a **You** badge wherever its name appears on a server-rendered page — rosters, leaderboards, stats tables, awards, box scores and tournament pools — with a **My player** shortcut in the account menu. It is purely a vanity link: the Player is not moved, copied or added to any team, and simulations are unaffected
- **Team history** — Win/loss records across all matches
- **REST API** — JSON endpoints at `/api/` for teams and players (read + write: create / edit teams, set roster slots, add / edit / delete players, generate players, import a roster CSV), match / round creation and analytics, read-only events, leagues and seasons (create a league from a template or the advanced form, delete a career league, start the next season or pick a new team after being fired, edit conferences, and read dashboards, history, standings, schedules, awards and owner evaluations, play a season), every League screen (player stats and ratings, free agents, leaders, the League player page, team roster / history / stats, power rankings, game log, statistical feats, playoffs, team and league finances — with finance edits for your current team — and your per-League watch list, which is saved to your account and shared with the web pages), sandbox tournaments (create in any of the five formats with preset teams or a player-pool draw, read the whole bracket and standings, set seeds, import participants, manage the player pool and the draw, lock, play the next match or play the whole tournament as a polled background job — league-embedded tournaments are read-only here), the full map editor (upload / delete maps, zones, sight lines, strong spots, spawns, heatmap — maps are shared by every account), and your account (who you are signed in as, register a new account, change your password, link or unlink your Self-insert), plus JWT sign-in, refresh and sign-out at `/api/token/`. Every response uses one envelope — `{"data": ..., "meta": {...}}` on success, `{"errors": [{"code", "message", "field"}], "meta": {}}` on failure — lists are paginated (20 per page, `?page_size=` up to 100) and filterable / sortable via query params. You only ever see your own account's rows (another account's row is a `404`)

## Tech Stack

- Python 3.12 / Django 5.2
- SQLite (default); PostgreSQL in production via `dj-database-url`
- `python-decouple` for environment variable configuration (`.env` for local, env vars in production)
- `gunicorn` as the production WSGI server (start with `gunicorn laserforce_simulator.wsgi:application --bind 0.0.0.0:8000`)
- `whitenoise` for production static file serving directly from Django
- nginx (`frontend/Dockerfile`) as the Docker stacks' front door: it serves the built Angular SPA at `/app/` and proxies every other path to Django (ADR-0046). Fly.io stays Django-only for now
- `django-storages` + `boto3` for Cloudflare R2 media storage in production (set `R2_*` env vars to activate; falls back to local disk when unset)
- `djangorestframework` + `django-filter` for the JSON API at `/api/`
- `django-cors-headers` so the Angular dev server (`http://localhost:4200`) can call `/api/` cross-origin. Set `CORS_ALLOWED_ORIGINS` (comma-separated) to change the allowed origins; the default is the dev server under `DEBUG` and none otherwise. `Content-Disposition` is exposed, so the SPA can name its downloads.
- `djangorestframework-simplejwt` for JWT sign-in on `/api/`: a short-lived access token in the response body and a rotating refresh token in an httpOnly cookie (ADR-0041)
- `reportlab` + `matplotlib` for the round report PDF and its charts (rendered server-side, Agg, no `pyplot`)
- `psycopg2-binary` for PostgreSQL support in production (CI runs tests against `postgres:16`)
- pytest + pytest-django for testing, with Codecov coverage reporting; CI splits the suite across 4 runners with `pytest-split`
- Docker (multi-stage `python:3.12-slim`); `docker-compose.yml` for local dev with PostgreSQL; deployed to Fly.io via CI on push to `main`

## Getting Started

> ### ⚠️ Upgrading a database created before user accounts (UX-01)
>
> UX-01 introduced a custom user model (`AUTH_USER_MODEL = "accounts.User"`) **after**
> Django's `auth` / `admin` migrations had already been applied against the built-in
> `auth.User`. On **any database created before UX-01**, the next `migrate` therefore
> fails with:
>
> ```
> django.db.migrations.exceptions.InconsistentMigrationHistory:
> Migration admin.0001_initial is applied before its dependency accounts.0001_initial
> ```
>
> **CI and the test suite are unaffected, and that is the trap.** `pytest` builds a
> fresh test database every run, so the suite goes **green** while the local
> `db.sqlite3` and the Fly.io Postgres are both broken. Never read a passing suite as
> proof the deployment is healthy.
>
> **The approved fix is a fresh database, not a data migration.** Simulation data is
> disposable by design
> ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md)), so:
>
> ```bash
> # Local (SQLite)
> rm laserforce_simulator/db.sqlite3
> python laserforce_simulator/manage.py migrate
> python laserforce_simulator/manage.py createsuperuser
>
> # Fly.io: destroy and re-provision the Postgres app, then run the same
> # migrate + createsuperuser against it.
> ```
>
> Do **not** attempt a migration-history rewrite, a `RunPython` copy of the old
> `auth_user` rows, or a `--fake` shim. See
> [ADR-0038](docs/adr/0038-accounts-and-uniform-manager-ownership.md).
> A **new** checkout is unaffected — just follow the steps below.

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

# Create your first account (email + password; this one can also reach /admin/)
python manage.py createsuperuser

# Start the dev server
python manage.py runserver
```

Then open http://127.0.0.1:8000/ in your browser.

### Option B — Docker Compose (production-like stack)

```bash
# Copy and configure environment
cp laserforce_simulator/.env.example laserforce_simulator/.env
# Edit .env and set a real SECRET_KEY

# Start nginx + app + PostgreSQL
docker compose up

# (first run) migrations are applied automatically by entrypoint.sh
```

Then open http://localhost:8080/ in your browser: the Django pages, with the Angular SPA at
http://localhost:8080/app/ on the same origin. Django is also reachable directly at
http://localhost:8000/.

### Option C — self-host on a home PC (public HTTPS URL)

`docker-compose.selfhost.yml` runs nginx (the SPA at `/app/`), the app, a Celery worker, Postgres and Redis, and publishes them
at `https://<name>.<tailnet>.ts.net` through Tailscale Funnel. See
[docs/self-hosting.md](docs/self-hosting.md).

### Frontend (Angular, in progress)

Phase 8 is moving the UI to an Angular 22 single-page app in [`frontend/`](frontend/) (see
[ADR-0042](docs/adr/0042-angular-frontend-stack.md)). So far it has a sign-in page, invite-only
registration (`/register?invite=<code>`, signed in straight away) and Change password, an app shell (a
header with an account menu — My player and Unlink Self-insert when linked, Change password, Sign out — and a
Leagues / Teams / Players / Matches / Tournaments / Maps sidebar that becomes a drawer on small screens), the Teams
screens: list, detail (with Remove player), Create, Edit and Assign Slots, plus Add Player and Edit
Player (the 19 Stats with a live Overall rating), the Players screens: the sortable, paged Players
list, player detail (the profile and colour-tiered Stat bars, Delete, and "This is me" to link your
Self-insert), a Player's career stats (totals, per-role tables against the role benchmarks with a
Mean / Median toggle, and a points trend chart) and Role Benchmarks, and the Matches screens: Match History (your Matches
and Single Rounds, paged), New Match and New Single Round (simulate and open the result), Batch Simulate (10 to 500 in-memory games between two Teams with live progress, win-rate cards, averages, the map-side advantage and a score histogram, then save Average or Highlight games as Rounds), and the Match and Round pages: a Match Summary per Round, and
each Round's per-player scoreboard (sortable, with MVP scores and a PDF export) and its Event log (every event, colour-coded and filterable by type and player, with the round's Highlights), and its Playback (watch the round unfold: play / step / 1-8x speed, live per-Side scoreboards, a newest-first event feed and a beta map), and its Charts (Shots, Lives, Points and SP over time per player, with Side totals and Elimination / Special / Nuke overlays, built with Chart.js), its Missile log (fired / hit / efficiency and every missile, Friendly fire highlighted) and its Movement heatmap (where players spent their time on the map, filterable by player, role and team), the analytics screens: Team and Player head-to-head (record cards, per-map / per-role breakdowns, margin charts and every game between them), a Team's match history (record, roster by role, scope filter by Tournament, every Match and Single Round) and Compare Rounds (per-player stat deltas and a points-over-time overlay), and the Maps screens: the Arena map list (upload, delete, configured badge) and the map editor (Zones & Bases, Sight Lines and the multi-round Heatmap, asking before it discards unsaved edits), and the Tournaments screens: the Tournament list, Create Tournament (preset Teams or a Player-pool draw, every bracket format) and the Tournament page: its setup (participants and their CSV import, Seeding, Lock & Build Bracket, and the Player pool: add, generate, import and remove players, draw or re-roll the Teams and set each drawn player's Tier), Play Next Match and Play All with live progress, and every format's results (the Bracket, Winners / Losers / Grand Final, Swiss rounds, the round-robin crosstable and the Standings), and the Leagues screens: the Leagues list (Active and Archived), Create League from a template or the Advanced form (phase and map-rotation composers), Delete (with what it removes), the League dashboard (Start Season, Start next Season, Choose new team for a fired Manager), League History and the new-team picker, and the League stats screens (League Leaders, Player Stats, Player Ratings, Team Stats, Statistical Feats, Game Log, Power Rankings and Playoffs, each with its Season / Career and Team filters, sorting and paging kept in the URL), and the League team and player screens (a Team's Roster, Schedule and History, the League player page with its ratings history and awards, Free Agents and your Watch List, with a star on every League table to watch or unwatch a player), and the League finance screens (every Team's revenue, profit, cash and payroll, and a Team's Finances page: hype, salaries, availability, season history and your own Team's Expense Settings for budgets, ticket price and injury policy), all beside a League menu whose not-yet-ported screens show as "Soon". They sit on the typed API services and
the JWT sign-in / silent-refresh flow. The Django pages stay live until ANG-08; until then the Docker stacks serve the
built SPA at `/app/` beside them (Option B and C above). Styling comes from the app theme in
[ADR-0043](docs/adr/0043-visual-theme-lfstats-layout-and-token-contract.md). The theme follows the
OS light / dark setting and uses JetBrains Mono, self-hosted. It needs **Node 24** (`frontend/.nvmrc`) and npm; the Angular CLI is
a project dependency, so no global install is needed.

```bash
npm --prefix frontend ci          # install exact versions from package-lock.json
npm --prefix frontend start       # dev server on http://localhost:4200
npm --prefix frontend run test:ci # Vitest unit tests, single headless run
npm --prefix frontend run lint    # ESLint (angular-eslint)
npm --prefix frontend run format:check
npm --prefix frontend run build   # production bundle in frontend/dist/
```

In dev the app calls the Django API directly at `http://localhost:8000/api` (CORS allows
`http://localhost:4200`, not `127.0.0.1:4200`). See [`frontend/CLAUDE.md`](frontend/CLAUDE.md).

## Accounts & Ownership

**The whole site is behind a login.** Every page except sign-in, registration and password-change redirects an anonymous visitor to `/accounts/login/`; the JSON API answers an anonymous request with a `401`, except `POST /api/accounts/register/`, which creates an account without signing you in, and the `/api/token/` sign-in endpoints. The API accepts either your browser session or a JWT: `POST /api/token/` with your email and password returns a 15-minute access token (send it as `Authorization: Bearer <token>`) and sets a 7-day refresh cookie that `POST /api/token/refresh/` trades for a new access token.

| URL | What it is |
|-----|------------|
| `/accounts/login/` | Sign in with **email + password** (there are no usernames) |
| `/accounts/register/` | **Invite-only registration** — needs an unused invite code (`?invite=<code>` prefills it) |
| `/accounts/logout/` | Sign out (the top-nav control posts here) |
| `/accounts/password-change/` | Change your password while signed in |
| `/accounts/self-insert/<player_id>/link/` | POST — make that Player your **Self-insert** (the "This is me" button) |
| `/accounts/self-insert/unlink/` | POST — clear your Self-insert (the "Unlink" button) |
| `/admin/` | Django Admin — where an administrator **removes** an account |

Every Team, League, Tournament, sandbox Match and standalone Round records the account that created it. You see your own rows and nothing of anybody else's — another account's row returns **404**, exactly as a nonexistent one would. Arena maps are the deliberate exception: they are shared reference data, visible to every signed-in account, because map pools and rotations reference maps across league boundaries.

**Unmanaged rows.** Anything created before you had an account — or by a management command such as `score_averages`, which runs outside any request — has no owner. Such rows stay **readable, writable and listed for every signed-in account** until somebody claims them. To take ownership of all of them at once:

```bash
# Stamp every unowned Team / League / Tournament / Match / Round to one account.
# Safe to re-run: a second pass finds nothing and reports 0 for every model.
python laserforce_simulator/manage.py claim_unmanaged --user you@example.com
```

**Your Self-insert.** Each account can link **one** Player as "you": open the Player's page (sandbox or League) and press **This is me**; press **Unlink** to clear it. Only a Player on a Team *your account manages* qualifies — a Player on an unmanaged Team must be claimed first (`claim_unmanaged`). If the Player later leaves your ownership the badge and the **My player** menu link simply disappear until it comes back.

**There is no password reset.** The "forgot my password" flow needs an outbound mail provider the deployment does not have, so it is deferred along with Google/OAuth sign-in. Recovery is an operator command:

```bash
python laserforce_simulator/manage.py changepassword you@example.com
```

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

# Run the suite on PostgreSQL, as CI does (throwaway container on port 5433)
docker run -d --name lf-test-pg -e POSTGRES_USER=laserforce -e POSTGRES_PASSWORD=laserforce -e POSTGRES_DB=laserforce_test -p 5433:5432 postgres:16
DATABASE_URL=postgres://laserforce:laserforce@localhost:5433/laserforce_test pytest
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
| `/accounts/login/` | Sign in (email + password) |
| `/accounts/register/` | Create an account (invite-only) |
| `/accounts/password-change/` | Change your password (no reset flow — use `manage.py changepassword`) |
| `/` | Mode-picker landing (Sandbox / Single-player League / Multiplayer — Coming soon) + in-progress Leagues |
| `/teams/` | Team & player management |
| `/leagues/` | Single-player League list (active + archived) |
| `/seasons/<id>/awards/` | Season-end awards (Most Points, K/D by role, Best Medic, Most Efficient Nuke, Best Accuracy, Season MVP, Finals MVP) |
| `/seasons/<id>/owner-evaluation/` | Career owner evaluation — per-factor owner mood, verdict, hot-seat warning (browsable for past seasons) |
| `/leagues/<id>/new-team/` | New Team picker shown after a firing — choose from the five worst-performing eligible teams |
| `/leagues/<id>/delete/` | Delete a career League (confirm step) — fully tears down its Seasons, Matches, embedded playoff tournaments, and generated teams + players |
| `/matches/` | Match list and creation |
| `/matches/create/` | Create a full 2-round match |
| `/matches/single-round/create/` | Create a standalone game round |
| `/matches/simulate-batch/` | Run N in-memory simulations |
| `/matches/game-round/<id>/` | Per-player round stats |
| `/matches/game-round/<id>/events/` | Filterable event timeline |
| `/matches/team/<id>/history/` | Team win/loss history |
| `/tournaments/` | Sandbox tournament list |
| `/tournaments/create/` | Create a single-elim, double-elim, round-robin, RR → double-elim, or Swiss tournament (preset teams or a player-pool draw; a player-pool draw played as RR → double-elim is a "Random Draw") |
| `/tournaments/<id>/` | Bracket tree, seeding, play controls, and (player-pool draw) player-pool intake + draw |
| `/api/teams/` | Team list (paginated JSON; `?name=`, `?include_free_agents=true`) / POST to create a team |
| `/api/teams/<id>/` | Team detail with full player stats / PATCH to rename |
| `/api/teams/<id>/slots/` | PUT to replace the six roster slots |
| `/api/teams/<id>/players/` | POST to add a player to the team |
| `/api/teams/generate/` | POST to generate teams or a free-agent pool |
| `/api/teams/import/` | POST (multipart `csv_file`) to import a roster CSV; `/api/teams/import/template/` downloads the template |
| `/api/players/` | Player list (paginated JSON; `?team=`, `?name=`, `?ordering=`) |
| `/api/players/<id>/` | Player detail / PATCH to edit / DELETE |
| `/api/players/<id>/career/` | Career stats for a player (`?threshold=`, `?display=mean\|median`) |
| `/api/players/benchmarks/` | Per-role stat benchmarks (`?threshold=`, `?display=`) |
| `/api/players/head-to-head/` | Player head-to-head (`?player_a=&player_b=`, optional `?role=`, `?provenance=`, `?from=` / `?to=`) |
| `/api/teams/head-to-head/` | Team head-to-head (`?team_a=&team_b=`, optional `?provenance=`, `?from=` / `?to=`) |
| `/api/teams/<id>/history/` | A Team's match history: record, roster by role, opponents, Matches and Single Rounds (`?scope=freeform` / `?scope=t<tournament id>`, repeatable) |
| `/api/matches/` | Match list (`?team=`, `?season=`, `?match_type=`, `?date_played_after=` / `_before=`) / POST to create and simulate a 2-round match |
| `/api/matches/<id>/` | Match detail with round ID list |
| `/api/rounds/` | Round list / POST to create and simulate a standalone round |
| `/api/rounds/<id>/` | Round detail with player states |
| `/api/rounds/<id>/events/` | Paginated event log for a round (`?event_type=`, `?actor=`, `?target=`, `?timestamp_min=` / `_max=`) |
| `/api/rounds/<id>/playback/` | Per-player Playback snapshots (state at every tick that changed it) plus the map routes |
| `/api/rounds/<id>/missile-log/` | Missile usage log for a round |
| `/api/rounds/<id>/heatmap/` | Movement heatmap data for a round |
| `/api/rounds/<id>/report/` | Round report PDF download |
| `/api/rounds/compare/` | Side-by-side round comparison (`?round_a=&round_b=`) |
| `/api/simulate-batch/` | POST to start a batch simulation; `/api/simulate-batch/<job_id>/` polls it, `/api/simulate-batch/<job_id>/save/` (POST) saves its average / outlier games, `/api/simulate-batch/save/<save_job_id>/` polls the save |
| `/api/leagues/` | League list (`?state=`, `?mode=`, `?ordering=`) / POST to create a league (`template` + `league_name` / `difficulty`, or the full advanced fields) |
| `/api/leagues/templates/` | The five league templates |
| `/api/leagues/<id>/` | League detail / DELETE (career leagues only) |
| `/api/leagues/<id>/delete-summary/` | What deleting a career league removes: season, match, tournament, team and player counts |
| `/api/leagues/<id>/nav/` | League menu context: the league, its displayed season and the TEAM > Schedule team |
| `/api/leagues/<id>/dashboard/` | League dashboard for the displayed season (plus mode, managed team, member-night progress, owner-evaluation and fired-Manager flags) |
| `/api/leagues/<id>/history/` | Paginated season history (champion, runner-up, MVPs) |
| `/api/leagues/<id>/teams/<team_id>/schedule/` | One team's upcoming and completed fixtures |
| `/api/leagues/<id>/next-season/` | POST to roll and start the next season, returned active (a draft when the previous season had conferences) (`409` while a season is in progress, with no completed season, or after being fired) |
| `/api/leagues/<id>/new-team-candidates/` | Teams a career manager can switch to |
| `/api/leagues/<id>/reassign-team/` | POST `team_id` to take over a new team and start the next season |
| `/api/leagues/<id>/player-stats/` | Paginated player stats (`?season=<id>\|career`, `?rate=total\|per_game\|per_10`, `?team_id=`, `?ordering=`) |
| `/api/leagues/<id>/player-ratings/` · `/free-agents/` | Paginated player ratings for the enrolled teams / the free-agent pool (`?ordering=`; ratings also `?team_id=`) |
| `/api/leagues/<id>/leaders/` | The four league-leader boards (`?season=`) |
| `/api/leagues/<id>/watch-list/` | Your watch list for this league as player-stats rows / DELETE to clear it; PUT / DELETE `watch-list/<player_id>/` to add or remove one player |
| `/api/leagues/<id>/players/<player_id>/` | League player page: bio, ratings, per-season + career stats, awards, ratings history |
| `/api/leagues/<id>/teams/<team_id>/roster/` · `/history/` · `/history/players/` | One enrolled team's roster, history (overall + seasons) and paginated history players |
| `/api/leagues/<id>/team-stats/` · `/power-rankings/` · `/game-log/` · `/statistical-feats/` · `/playoffs/` | Season-scoped team screens (`?season=<id>\|career`; playoffs takes no `career`) |
| `/api/leagues/<id>/teams/<team_id>/finances/` | One team's finances (with the budget level bounds and `meta.team_options`) / PATCH budgets, ticket price, injury policy (your current team only; `409` when finances are off) |
| `/api/leagues/<id>/finances/` | Every enrolled team's cash, revenue, profit and payroll |
| `/api/seasons/` | Season list (`?league=`, `?state=`, `?ordering=`) |
| `/api/seasons/<id>/` | Season detail with phases and conferences |
| `/api/seasons/<id>/dashboard/` | Season dashboard |
| `/api/seasons/<id>/standings/` | Standings grouped by conference (`?sort=`, `?dir=asc\|desc`) |
| `/api/seasons/<id>/schedule/` | Season schedule by matchday, plus member nights |
| `/api/seasons/<id>/awards/` | Season awards |
| `/api/seasons/<id>/owner-evaluation/` | Owner evaluation for a completed career season |
| `/api/seasons/<id>/conferences/` | Conference setup / PUT to replace it (draft seasons only) |
| `/api/tournaments/` | Tournament list (`?state=`, `?format=`, `?team_assembly=preset\|player_pool`, `?embedded=true\|false`, `?ordering=`) / POST to create a tournament |
| `/api/tournaments/<id>/` | Tournament detail: participants, every bracket with its nodes, standings, the round-robin crosstable, stage, whether a Match is ready to play |
| `/api/tournaments/<id>/seeds/` · `/lock/` · `/import-participants/` | PUT the full seeding, POST to lock the bracket, POST (multipart `csv_file`) to import participant teams (setup only) |
| `/api/tournaments/<id>/pool/` | Paginated player pool; POST `pool/players/`, `pool/generate/` or `pool/import/` to add players, DELETE `pool/players/<player_id>/` to remove one |
| `/api/tournaments/<id>/draw/` | POST to run or re-roll the draw; PATCH `draw/entries/<player_id>/` to edit one entry's tier / drawn team |
| `/api/tournaments/<id>/play-next/` · `/play-all/` | POST to play the next match, or to start playing the whole tournament as a job (`409` while a run is live); poll `play-jobs/<job_id>/` |
| `/api/maps/` | Arena map list (shared across accounts) / POST (multipart `name` + `image`) to upload a map |
| `/api/maps/<id>/` | Arena map detail / DELETE (refused with `409` while a Season that is not completed uses the map) |
| `/api/maps/<id>/processed-image/` | Black-and-white processed map image (PNG) |
| `/api/maps/<id>/image/` | The original uploaded map image |
| `/api/maps/<id>/zones/` | Detect the zone grid for a zone size (`?zone_size=`) |
| `/api/maps/<id>/zone-config/` | Saved zone configuration (zones, walls, elevation, spawns, bases) / PUT to replace it |
| `/api/maps/<id>/sight-lines/` | Saved sight lines (`?zone_size=`) / PUT to replace / PATCH to merge a batch |
| `/api/maps/<id>/sight-lines/compute/` | POST to compute all sight lines for a zone size |
| `/api/maps/<id>/sight-lines/cell/` | Visible cells from one cell (`?zone_size=&r=&c=`) |
| `/api/maps/<id>/ranked-cells/` | Cells ranked by visibility (`?zone_size=`) |
| `/api/maps/<id>/strong-spots/` | Heavy strong spots (`?zone_size=`) / PUT to replace them |
| `/api/maps/<id>/spawn-cells/` | Red and blue spawn cells |
| `/api/maps/<id>/heatmap/` | Movement heatmap across your rounds on this map (`?zone_size=`, optional `?team_color=red\|blue`) |
| `/api/accounts/me/` | Your account (`id`, `email`, `date_joined`, `is_staff`) and your Self-insert player, or `null` |
| `/api/accounts/register/` | POST `email` / `password1` / `password2` / `invite` to create an account (anonymous allowed, but needs an unused invite code; does not sign you in) |
| `/api/accounts/me/password/` | POST `old_password` / `new_password1` / `new_password2` to change your password (you stay signed in) |
| `/api/accounts/me/self-insert/` | PUT `{"player": <id>}` to link a player on a team you manage as your Self-insert / DELETE to unlink |
| `/api/token/` | POST `email` / `password` to sign in: returns an access token and sets the httpOnly refresh cookie |
| `/api/token/refresh/` | POST (no body) to trade the refresh cookie for a new access token; the cookie is rotated |
| `/api/token/logout/` | POST to sign out: revokes the refresh token and deletes the cookie |
