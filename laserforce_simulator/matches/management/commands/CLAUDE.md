# matches/management/commands

Django management commands for offline simulation analysis. Run from the `laserforce_simulator/` directory (where `manage.py` lives).

## score_averages.py

Batch-simulates N rounds using `BatchSimulator` (pure in-memory, no DB writes) and prints aggregate stats per role.

```bash
python manage.py score_averages --rounds 50
python manage.py score_averages --rounds 100 --team-red "Team A" --team-blue "Team B" --seed 42
python manage.py score_averages --rounds 500 --map "Arena Name" --workers 8
```

Defaults to the first two teams in the DB with active rosters. Use `--seed` for reproducible runs.

**`--map <name>`:** resolves an `ArenaMap` by `name` and runs with cell-aware A* pathfinding instead of the 3-zone fallback (omit for no map). Resolution uses `load_map_context` from [`matches.sim_helpers.map_loader`](../../sim_helpers/CLAUDE.md) (SIM-09 extracted this from the removed `ResourceBasedSimulator`; behaviour unchanged). A missing/duplicate name, or a map missing its confirmed zone config / bases / sight lines, raises a clean `CommandError` (the `load_map_context` `ValueError` is caught and re-raised). The `MapContext` is built once in the parent and threaded into both the serial loop and the parallel workers (`score_round_worker` takes it as a 4th arg element), so `--map` works under `--workers > 1`. This is the only `score_round_worker` change — seeding stays `random.getstate()`-based and out of SIM-07/SIM-08 scope (no int seed, no Side flip). **MOVE-04:** `--map` runs now also exercise the **Goal commitment** path (`PlayerState._committed_goal`, 4-tick steady-state recompute cadence, reactive overrides per-tick — [ADR-0010](../../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md)); perf measurement (cells/tick recompute ratio, ms/round delta vs the MOVE-02 baseline) is captured here.

**Parallel-path stat bug (fixed alongside `--map`):** `_precompute_roster` (the `--workers > 1` path) builds picklable `_PlayerData` from `_SIMULATION_STATS`, but `_make_players` also reads `game_awareness` and `resource_awareness`, which were absent from that tuple — `_PlayerData.stat_for_simulation` does a hard dict lookup, so any `--workers > 1` run raised `KeyError` regardless of `--map` (the serial path reads stats live off the ORM `Player` and was unaffected). Both keys were added to `_SIMULATION_STATS` (and later `speed`, when STAT-03 Phase 1 multi-cell movement wired `PlayerState.speed`); regression test: `matches/tests/test_batch_sim.py::TestPrecomputeRosterParity`. Rule: every stat `_make_players` reads must be in `_SIMULATION_STATS`.

**Time unit (TIME-01):** `BatchSimulator` accumulates uptime in **ticks** (`ticks_active` / `ticks_not_targetable` / `ticks_reset_window`); dead-time is derived as `1800 - was_eliminated_at`. `score_averages` divides ticks by 2 at the **display boundary only** — this command and `game_analysis` are the sole `÷2` sites besides HTML templates. The uptime aggregation reads the renamed `ticks_*` fields and reports the seconds figures for human readability.

### Output sections

1. **Score summary** — avg score vs calibration target, avg tags made, avg times tagged. Colour-coded: green = within 500 of target, yellow = 500–1000 over, red = >1000 over.
2. **Missile breakdown** — avg missile points, % of total score, avg missiles hit per round.
3. **Reset-window tags** — avg times tagged while in the 4–7 s respawn window (informational; helps tune survival stats).
4. **Uptime breakdown** — avg seconds in each state (active / reset-window / dead / not-targetable) and percentages. Source values are ticks (`ticks_*` + `1800 - was_eliminated_at`); shown in seconds via the `÷2` display conversion. Useful for diagnosing whether a role is dying too fast or spending too much time in transit.
5. **Follow-up & reaction shots** — avg follow-up shots per round, follow-ups as % of total tags, same for reaction shots. Use this to check whether follow-up eligibility logic is working correctly (e.g. heavy should always show 0.0% follow-up).

### Calibration targets

Real-world average scores used to evaluate simulation accuracy:

| Role | Target |
|------|--------|
| Commander | 9,952 |
| Heavy | 6,482 |
| Scout | 5,102 |
| Ammo | 3,242 |
| Medic | 2,282 |

---

## game_analysis.py

Analyses events from a completed (DB-persisted) `GameRound` and prints per-player uptime breakdowns reconstructed from `player_downed` and resupply events.

```bash
python manage.py game_analysis --round-id <game_round_pk>
```

Useful for verifying that uptime numbers from persisted DB rounds reconcile against the `BatchSimulator` in-memory accumulators; before SIM-09, the persisted rounds came from `ResourceBasedSimulator`'s event log — post-SIM-09 every persisted round is written by `BatchSimulator._flush_to_db` and this command reads the same `GameEvent` rows regardless. **TIME-01:** `GameEvent.timestamp` and `was_eliminated_at` read here are in ticks; like `score_averages`, this command divides by 2 at the display boundary only (dead-time = `1800 - was_eliminated_at`).

---

## seed_browser_check.py

Throwaway data for a browser check in a worktree's disposable database (ADR-0004).

```bash
python manage.py seed_browser_check          # Accounts browser-a / browser-b@example.com, password browser-check-pass
python manage.py seed_browser_check --league # also seed Account A "Browser Check League"
python manage.py seed_browser_check --tournaments # also seed Account A one Tournament per format and stage
python manage.py seed_browser_check --clean  # delete both Accounts, every row they own, the League and the map
```

Account A gets four valid Teams, a simulated sandbox Match, a map-less Single Round, a **mapped
Single Round** and an unfinished Match (round 1 only, `is_completed=False`, no winner: what a League
Match looks like mid-Season). The mapped Single Round plays on "Browser Check Arena", a 30x20-cell
map (zone size 20) whose PNG is drawn in code with PIL (no `media/` file needed) and uploaded via
`core/services.py::upload_map`, then given a confirmed zone config with red/blue bases, sight lines
and base sight lines, so `load_map_context` resolves. The Round is upgraded to `fidelity="full"`
(movement events) for Playback's map, the heatmap and map-editor checks. Its first missile hit
(or first missile) is flagged `friendly_fire` by `mark_friendly_fire_missile`, so the Missile log
shows one tinted Friendly-fire row; only the flag changes, actor and target stay as simulated
(ANG-06-10 retro). The map-less Single Round
is upgraded to `"full"` too, and the two share their Teams: they are the Compare Rounds pair, with a
points chart on both sides (ANG-06-9 retro). The summary prints the map id and the mapped Round id. `--clean` removes the map through `delete_map` (image and processed
cache go on commit). Each `removed` count is that model's own rows (`_deleted`), not
`delete()[0]`'s cascade total. Account B owns nothing, for cross-Account checks. A second seed without
`--clean` is refused.

**`--league`** (for League-screen checks: History, player page, Team history, Power Rankings,
standings, awards, owner evaluation, finances) also gives Account A "Browser Check League"
(`manager` = A, `mode="league"` so `is_career_league` holds, `finance_enabled=True`,
`current_team` = A's first Team) over A's four Teams: two completed single-round-robin Seasons
(every pair plays a 2-0 Match, a different champion each Season) and one active Season at its
playoffs (ANG-06-15 retro): a round-robin phase hand-played through the real schedule (the first
Team best), then a `standings` tournament phase whose seeded bracket
`activate_pending_tournament_phase` builds, so the Playoffs screen has a bracket to draw (no bracket
games played). Every GameRound carries 12 hand-built `PlayerRoundState` rows, so standings, awards
and player stats are non-empty; no simulator runs. ANG-06-16 retro additions:
- **Free-agent pool:** the League's `free_agent_pool` is "Browser Check Free Agents", an A-owned
  Team holding three "FA <name>" copies of the first Team's Players, so Free Agents has a table.
- **Ratings history:** every Player gets one `PlayerSeasonRating` per Season, Overall rising 2.0 a
  Season up to the live rating, so the League player ratings-history chart draws a line.
ANG-06-17 retro addition:
- **Settled finances:** the real `_ensure_team_finances` writes a `TeamSeasonFinance` row per Team
  for both completed Seasons and carries their profit into each Team's cash, and the first Team's
  second scout is injured for 2 games, so Team Finances shows tiles, Season history and Player
  availability with figures (League Finances' table stays `—`: it shows the active Season). The rows are built by
`_browser_check_league.py` (a copy of the `league_api_fixtures.py` shapes: production code never
imports test modules; the leading underscore keeps it out of the command list). The summary gains
`League <id> (Browser Check League), Seasons <id>, <id>, <id> (completed, completed, active)`.
`--clean` deletes the League's Matches and then the League, which cascades its Seasons (deleting
the Account alone would not: `League.manager` and `Match.season` are `SET_NULL`), plus any
Unmanaged League of that name, and reports `Leagues: N` first; with no League the `removed` line is
unchanged. It does not call `league_services.teardown_league`: that one's orphan-Team sweep would
delete A's Teams (and their Single Rounds) before they are counted.
A `--league` seed is refused while a League of that name exists. Without `--league` the seed is
unchanged.

**`--tournaments`** (for Tournament-screen checks, ANG-06-13b retro) also gives Account A seven
sandbox Tournaments over newly generated Teams, built by `_browser_check_tournaments.py` through the
API's own services (`create_tournament`, `lock_tournament`, `play_next`, the pool and `run_draw`):
"BC Single Elim" (8 Teams, Bo3 Final, 5 Matches played, Play Next live), "BC Double Elim" (played
out, champion), "BC Round Robin" (5 of 12 played), "BC RR to DE" (seeding stage, cut labels),
"BC Swiss" (round 1 played, round 2 built), "BC Setup" (4 Teams, unlocked) and "BC Player Pool"
(24 generated pool Players, drawn). Matches run one at a time with `play_next`, so no Celery broker
is needed; the seed takes about 25 s. `--clean` deletes them first, with their Matches (found
through their Series, not a manager) and the generated pool Players (on the global Free Agents
Team), and reports `Tournaments: N` only when there were any.
