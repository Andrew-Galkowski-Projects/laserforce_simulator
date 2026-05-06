# Implemented Features

Current state of the Laserforce Simulator as of May 2026.

---

## Team Management

- **Team CRUD** — create, edit, delete teams; name stored, wins/losses tracked
- **Player management** — add, edit, delete players per team; 19 stat fields (0–100), `overall_rating` averages them
- **Role slots** — 6 fixed slots: Commander, Heavy, Scout ×2, Medic, Ammo; `TeamSlotForm` filters to team's own players
- **Roster validation** — `is_valid_roster` property; `roster_errors` list; displayed on team detail page
- **Preferred roles** — multi-select JSON field on Player; not yet used in simulation weighting

### Player Stats (model only — not yet wired into simulation weights)
`player_awareness`, `game_awareness`, `resource_awareness`, `decision_making`, `positioning`, `stamina`, `speed`, `flexibility`, `adaptability`, `communication`, `teamwork`, `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`, `resupply_synergy`, `resupply_efficiency`, `accuracy` (used in hit-chance formula), `survival` (used in hit-chance formula), `special_usage`

---

## Match Simulation

### Simulation Engine — `ResourceBasedSimulator`
- 2-second ticks over 900 seconds (15 minutes)
- Per-role weighted-random action selection each tick
- `simulate_match()` — runs 2 rounds, swaps team colors between rounds
- `simulate_single_round_detailed()` — standalone round without a Match parent
- Role-based starting resources:

  | Role | Lives | Shots | Missiles |
  |------|-------|-------|----------|
  | Commander | 15 | 30 | 5 |
  | Heavy | 10 | 20 | 5 |
  | Scout | 15 | 30 | 0 |
  | Medic | 20 | 15 | 0 |
  | Ammo | 10 | 15 | 0 |

### Combat Mechanics
- **Tag resolution** — `hit_chance = clamp(10, 95, 70 + attacker.accuracy − defender.survival)`; hiding defender adds 50% miss chance
- **Shield/life system** — shields absorb damage; at 0 shields, player loses 1 life and enters 8-second respawn cooldown
- **Missile strikes** — Commander/Heavy fire up to 5 missiles per round; 4–8 second travel delay; same damage as tag but worth 500 points
- **Nuke (Commander special)** — costs 20 SP; 4–7 second fuse; removes 3 lives from all active enemies; worth 500 points; cancellable by enemy tag during fuse
- **Rapid Fire (Scout special)** — costs 10 SP; allows 2 tags per tick for remainder of round
- **Power Boost (Medic special)** — costs 10 SP; heals all active teammates (commander +4, heavy +3, scout +5, ammo +2 lives)
- **Power Boost (Ammo special)** — costs 15 SP; resupplies all active teammates (commander +5, heavy +5, scout +10, medic +5 shots)
- **Resupply (Medic)** — individual ally heal; same-zone, not-in-cooldown target required
- **Resupply (Ammo)** — individual ally shot resupply; same-zone, not-in-cooldown target required
- **Base capture** — points awarded; tracked via `neutral_base_destroyed` / `opposing_base_destroyed` flags
- **Team elimination** — 10,000-point bonus to winning team when all opponents reach 0 lives

### Zone Movement (3 zones)
- `red_zone` (0), `neutral_zone` (1), `blue_zone` (2)
- Role-driven heuristics for zone selection; scouts/medics prefer neutral, heavies start in own zone

### Action Weights (`matches/sim_helpers/weights.py`)
Per-role weight functions shift based on: zone, remaining lives/shots/special, ally positions, base availability, respawn cooldown. Key behaviors:
- Medic heavily biased toward resupply (+70), hides when low on lives
- Ammo heavily biased toward resupply (+50), seeks heavy when threatened
- Scout mobile (+10 change_zone), uses rapid fire based on shot ratio
- Heavy fires missiles when available (+15), tanks in its zone
- Commander fires missiles when available (+15), nukes more often when fewer enemies are nearby

---

## Data Models

### `Match`
Two `GameRound` children; winner by rounds won then total points; `red/blue_bonus_points` for team elimination.

### `GameRound`
900-second round; `red_points`, `blue_points`; elimination flags and timestamp; parent for `PlayerRoundState` and `GameEvent`.

### `PlayerRoundState`
Full resource snapshot per player per round: starting/final lives, shots, SP, missiles; `tags_made`, `shots_missed`, `times_tagged`, `missiles_landed`, `times_missiled`, `resupplies_given`, `specials_used`, `enemy_nuke_cancels`, `ally_nuke_cancels`; `specific_tags` JSON dict; `was_eliminated_at` (901 = survived); `get_mvp` role-weighted score.

### `GameEvent`
Append-only log: `tag`, `missile`, `special`, `miss`, `resupply_ammo`, `resupply_lives`, `elimination`, `team_elimination`; actor + optional target FKs; `points_awarded`; `metadata` JSON.

### Arena Map Models (`core/`)
- `ArenaMap` — uploaded image + pixel dimensions
- `MapZoneConfig` — 2D zone grid (0=wall, 1=floor, 2=red, 3=blue) + `blocked_edges_grid`; one confirmed config per map per zone_size
- `MapBaseConfig` — pixel coordinates for red, blue, neutral_1–4 bases
- `SightLineConfig` — precomputed all-pairs visibility dict keyed per (map, zone_size)
- `BaseSightLineConfig` — cells that can tag each base, keyed per (map, base_type, zone_size)

---

## Map Editor (`core/`)

- **Map upload** — image stored, pixel dimensions recorded; two default maps seeded (Syracuse, San Marcos)
- **Zone detection** (`detect_zones`) — classifies grid cells as wall/floor/red/blue using B&W wall mask + avg RGB
- **Image processing** (`create_processed_image`) — grayscale threshold at 210; connected-component filtering removes text blobs, keeps walls; cached to `media/maps/processed_<id>.png`
- **Blocked edge detection** — samples pixel boundaries between cells; marks edge blocked if ≥30% dark; enables sub-cell wall precision
- **All-pairs sight lines** (`compute_sight_lines`) — Bresenham LOS; quadtree spatial acceleration when >50 passable cells (~50–100× speedup); saves to `SightLineConfig`
- **Single-cell lazy LOS** (`compute_single_cell_visibility`) — O(n) from one cell; used by editor click for instant feedback
- **Base placement** — click to place/remove red, blue, neutral_1–4 bases; pixel coords saved to `MapBaseConfig`
- **Base sight lines** — manual user-defined cells that can tag each base; saved per (map, base_type, zone_size)
- **Drag-select bulk edit** — rectangle selection in sight lines editor; toggles all non-wall cells in rect bidirectionally
- **Batched save** — sight line payloads chunked at 100 keys per POST to avoid Django's 2.5 MB limit

### Editor UI Modes
- **Zones & Bases mode** — zone grid overlay on B&W image; click to place bases
- **Sight Lines mode** — zone view (yellow = selected, green = visible, faint red = blocked); base view per base type

---

## Views & URLs

| Path | Purpose |
|------|---------|
| `/` | Team list (homepage) |
| `/teams/create/` | Create team |
| `/teams/<id>/` | Team detail, roster, validation |
| `/teams/<id>/slots/` | Assign players to role slots |
| `/teams/<id>/add-player/` | Add player |
| `/teams/<id>/player/<pid>/edit/` | Edit player stats |
| `/teams/<id>/player/<pid>/delete/` | Remove player |
| `/matches/create/` | Simulate 2-round match |
| `/matches/single-round/create/` | Simulate standalone round |
| `/matches/<id>/` | Match result detail |
| `/matches/game-round/<id>/` | Per-player round stats |
| `/matches/game-round/<id>/events/` | Filterable event timeline |
| `/matches/team/<id>/history/` | Team win/loss history |
| `/matches/simulate-batch/` | Batch simulation UI |
| `/matches/save-batch-games/` | Start background batch save |
| `/matches/save-batch-status/<job_id>/` | Poll batch job status |
| `/maps/` | Map list + upload |
| `/maps/<id>/editor/` | Map editor |
| `/maps/<id>/zones/` | AJAX zone detection |
| `/maps/<id>/processed-image/` | Cached B&W image |
| `/maps/<id>/save/` | Save zone config + bases |
| `/maps/<id>/sight-lines/` | Load sight line data |
| `/maps/<id>/sight-lines/compute/` | Run all-pairs LOS |
| `/maps/<id>/sight-lines/single-cell/` | Lazy per-click LOS |
| `/maps/<id>/sight-lines/save/` | Save sight lines (batched) |

---

## MVP Scoring

Role-specific formula applied to `PlayerRoundState`:
- All roles: +0.1 per 1% accuracy, +1 per enemy Medic hit, +3 per enemy nuke cancelled, −3 per own nuke cancelled, −1 per time missiled, −1 if eliminated, +4 + time_remaining/60 for team elimination bonus
- Commander: +1/missile, +1/successful nuke, +1/1000 pts over 10k
- Heavy: +2/missile, +1/1000 pts over 7k
- Scout: +0.2/tag on Commander or Heavy, +1/1000 pts over 6k
- Ammo: +3/special, +1/1000 pts over 3k
- Medic: +3/special, +2 if alive at end, no elimination penalty, +2/1000 pts over 2k

---

## Test Coverage

- `matches/simulation_tests.py` — simulation logic, event correctness, round outcomes (referenced; uses fixed seeds)
- `core/tests.py` — placeholder
- `teams/tests.py` — placeholder
- CI: GitHub Actions, `pytest --cov`, coverage uploaded to Codecov
