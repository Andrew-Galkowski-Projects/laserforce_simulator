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

**`--map <name>`:** resolves an `ArenaMap` by `name` and runs with cell-aware A* pathfinding instead of the 3-zone fallback (omit for no map). Resolution uses `load_map_context` from [`matches.sim_helpers.map_loader`](../../../../docs/agents/matches/sim-helpers.md#map_loaderpy) (SIM-09 extracted this from the removed `ResourceBasedSimulator`; behaviour unchanged). A missing/duplicate name, or a map missing its confirmed zone config / bases / sight lines, raises a clean `CommandError` (the `load_map_context` `ValueError` is caught and re-raised). The `MapContext` is built once in the parent and threaded into both the serial loop and the parallel workers (`score_round_worker` takes it as a 4th arg element), so `--map` works under `--workers > 1`. This is the only `score_round_worker` change — seeding stays `random.getstate()`-based and out of SIM-07/SIM-08 scope (no int seed, no Side flip). **MOVE-04:** `--map` runs now also exercise the **Goal commitment** path (`PlayerState._committed_goal`, 4-tick steady-state recompute cadence, reactive overrides per-tick — [ADR-0010](../../../docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md)); perf measurement (cells/tick recompute ratio, ms/round delta vs the MOVE-02 baseline) is captured here.

**Parallel-path stat bug (fixed alongside `--map`):** `_precompute_roster` (the `--workers > 1` path) builds picklable `_PlayerData` from `_SIMULATION_STATS`, but `_make_players` also reads `game_awareness` and `resource_awareness`, which were absent from that tuple — `_PlayerData.stat_for_simulation` does a hard dict lookup, so any `--workers > 1` run raised `KeyError` regardless of `--map` (the serial path reads stats live off the ORM `Player` and was unaffected). Both keys were added to `_SIMULATION_STATS` (and later `speed`, when STAT-03 Phase 1 multi-cell movement wired `PlayerState.speed`); regression test: `matches/tests/test_batch_sim.py::TestPrecomputeRosterParity`. Rule: every stat `_make_players` reads must be in `_SIMULATION_STATS`.

**Time unit (TIME-01):** `BatchSimulator` accumulates uptime in **ticks** (`ticks_active` / `ticks_not_targetable` / `ticks_reset_window`); dead-time is derived as `1800 - was_eliminated_at`. `score_averages` divides ticks by 2 at the **display boundary only** — this command and `game_analysis` are the sole `÷2` sites besides HTML templates. The uptime aggregation reads the renamed `ticks_*` fields and reports the seconds figures for human readability.

### Output sections

1. **Score summary** — avg score vs calibration target, avg tags made, avg times tagged. Colour-coded: green = within 500 of target, yellow = 500–1000 over, red = >1000 over.
2. **Missile breakdown** — avg missile points, % of total score, avg missiles hit per round.
3. **Reset-window tags** — avg times tagged while in the 4–7 s respawn window (informational; helps tune survival stats).
3b. **Follow rule** (MECH-10) — per Role: avg Downs taken and the **resupply death %** (Downs that land inside a Respawn cooldown a Resupply started, i.e. within `RESPAWN_TICKS` of receiving it, as a share of all Downs; `-` with no Downs). Then `Medic survival (s): <mean> of 900`: the mean Medic Elimination tick, 1800 for a Medic who survived, shown ÷ 2. Inputs are the transient `PlayerState` fields `downs_taken` / `downs_in_resupply_cooldown` / `last_resupplied_at` (no DB column), carried by `score_round_worker` for `--workers`.
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

## lfstats_calibrate.py

The LFS-01 **Calibration harness** (spec #514): simulates imported Players and compares what they do in the Simulator with their real LFstats numbers, per Role and metric. A thin wrapper over `matches.lfstats_calibration` (`run_test`, `run_train`, `search`).

```bash
python manage.py lfstats_calibrate --split train --rounds 20 --workers 8
python manage.py lfstats_calibrate --split test --rounds 20 --workers 8 --accuracy-offset -10
python manage.py lfstats_calibrate --split search --rounds 20 --seed 42 --workers 8
```

**`--split` (required):**
- `train`: each Training (player, position) with at least `MIN_TRAIN_GAMES` (26) games there plays `--rounds` rounds at that Role as red, the other five red and all six blue built from the per-position baselines (field-average teams). Compared with the player's Training averages; prints the per-Role table and the objective.
- `test`: replays each usable Internationals 2026 game `--rounds` times with its real lineup (Fire red, Ice blue). Skips a game whose lineup is not commander / heavy / two scouts / ammo / medic on both sides, or where a player has fewer than `TEST_MIN_POSITION_GAMES` (5) Training games at the position played. Prints the table, then the coverage line and the match level (winner agreement, mean margin error).
- `search`: a coordinate search over the `ImportConstants` fields on the Training set only (never reads `--test`), starting from the defaults plus any constant flags. Its grid is `DEFAULT_GRID` in the command, three values around each default; `--search-fields accuracy_offset,preferred_role_boost` narrows it to those constants (an unknown name is a `CommandError`). Prints the table for the chosen constants, a Start / Chosen constants table, the objective, and the history (every candidate tried, by pass, with `yes` where it was kept).
- `targets`: the real side of every Test cell with no simulation (`real_targets` / `targets_payload`): per Role, `Metric`, `Real` (the scorecard-weighted mean of the used games, exactly `test`'s `Real` column) and `Tolerance` (the widest `|sim - real|` that passes), then the coverage line. `--out <path>` also writes the JSON (`coverage`, `tolerance`, `cells`); the committed copy is `data/lfstats_cell_targets.json` (`DEFAULT_TARGETS`), which `test_lfstats_calibrate_command.py::TestCommittedTargets` keeps equal to the committed data. Needs no map fixture and no database.

```bash
python manage.py lfstats_calibrate --split targets --out data/lfstats_cell_targets.json
```

**Other flags:** `--train` / `--test` (default the committed `data/lfstats_train_2026-10-21.json` / `data/lfstats_test_internationals_2026.json`), `--map-fixture` (default `data/maps/syracuse.json`; a missing path is a `CommandError` naming it), `--rounds` (20), `--seed` (42), `--workers` (1), and one float flag per `ImportConstants` field: `--log-scale`, `--accuracy-offset`, `--accuracy-scale`, `--survival-times-hit-weight`, `--preferred-role-boost`.

**No database writes.** The map fixture is loaded by `load_map_fixture` inside `transaction.atomic()` with `transaction.set_rollback(True)`, and only `load_map_context(arena_map)[0]` (an in-memory `MapContext`) leaves the block. Players are `_PlayerData` stand-ins built by `player_data`, never ORM rows. The tests pin the `ArenaMap`, `Team` and `Player` counts unchanged.

**A Role that never engages is an error.** When no player at a position tagged or was tagged in any simulated round, `run_train` / `run_test` raise (a `CommandError` here): the sim never reached them, so the numbers are no result. LFS-01's first searches ran for hours on a map whose red spawns sat in pockets cut off from the floor.

**Memory.** Measured on Syracuse (2026-11-03, `--split train --rounds 1 --workers 2`): each worker peaks at about **90 MB** and the parent at about **140 MB**, so `--workers 14` needs about 1.5 GB (it held flat over the first minutes; a run of hours was not measured). LFS-01's 14-worker search was still killed for low memory, so most of the machine's RAM was going elsewhere: check free RAM before a long run.

**Runtime.** `--split test --rounds 20 --workers 4` takes 50 to 60 minutes (three SM5-02 runs, 2026-11-03/04), and it prints only when it finishes, so run it in the background. Before re-running after a small fix, count how often the changed branch fires in a short probe: SM5-02 re-ran for an hour after a clamp that never fired, and the output was byte-identical.

**Determinism.** Per-round seeds come from `random.Random(--seed).getrandbits(63)` (game then round order for `test`, sorted player key then round for `train`), so `--workers 1` and `--workers N` print the same numbers. Every `search` evaluation uses the same seed, and a candidate is simulated once (later repeats come from a cache).

### Output

1. **Per Role** (`<role> (n=<count>)`, the count being used scorecards for `test` and players for `train`): `Metric`, `Sim`, `Real`, `Diff` (sim - real), `Pass` (`within_tolerance`: within 15% of real, or 0.5 for a count / 0.05 for a fraction), `Rho` (Spearman per player, `-` when undefined). Then `Cells passed: x / y`.
2. **`test`:** `Coverage: <used> of <total> games used, <n> skipped (lineup), <n> skipped (thin data ...)` and `Match level: ...`.
3. **`train`:** `Objective: <x>`, the mean over cells of `(err / max(|real|, floor)) ** 2`.
4. **`search`:** the constants table, `Objective: <x>`, and the history table.

ASCII only (cp1252 console). The command writes no files except `targets` with `--out`; copy the output into `docs/lfstats-import.md`.

---

## round_probe.py

Measures how players move, choose goals and use their Actions over seeded rounds: the numbers behind a simulation behaviour decision. A thin wrapper over `matches.round_probe` (`run_probe`).

```bash
python manage.py round_probe                  # seeds 1-12 on data/maps/syracuse.json, about 90 s
python manage.py round_probe --seeds 24 --map-fixture <path>
python manage.py round_probe --lineups test --workers 8   # CAL-02: the 128 Test games, about 12 min
python manage.py round_probe --lineups test --games 2 --seed 7 --targets <json>
```

Prints, per Role, each measure as `mean (min-max)`: the mean pools every seed and the range is across the seeds (`ProbeResult.roles` and `by_seed`). A measure a target can read adds its standard error, `mean +/- se (min-max)`: the seeds' sample deviation over the root of their count, 0 with one seed. A move inside about two of them can be seed noise (MECH-17: Scout tags read 67.1 to 71.5 across runs against a floor of 70), so state a target with this margin.

**`--lineups test` (CAL-02, #567):** instead of the default-stat rosters on seeds 1..N, play the used Test games' real lineups (`run_probe(movement_ctx, seeds, lineups=...)`), one round per game, built the way the Calibration harness builds them (`lfstats_calibration.used_lineups(train, test, limit=None)`: `run_test`'s used-games filter, rosters in position order, `DEFAULT_CONSTANTS`; `calibration_lineups` in the command module loads the two files and calls it). Per-game seeds come from `random.Random(--seed)` (default 42) in game order (`probe_seeds`), `--games N` keeps the first N games, and `--workers N` plays the rounds across N processes with `worker_django_init`: each worker installs the observers in its own process and returns plain per-round tallies the parent merges, so serial and parallel print the same numbers (`TestLineupsProbe`). `--seeds` is ignored with `--lineups`. The `Per seed:` line then lists the 63-bit per-game seeds.

**`--targets <json>`** (default `data/tdf_role_targets_2026.json`, the committed TDF per-role targets of CAL-02c): each role line with a real counterpart ends `| real <mean>`, and each matrix row gets a `real` row under it. The counterparts: Tags made <-> `tags`, Times tagged <-> `times_tagged` (enemy Tags only, as the sim counts), Shots fired <-> `shots_at_players` (the real zaps on players, enemy or teammate, plus misses: the sim's Tags plus misses like for like; the file's `shots_fired` also counts Base zaps and stays for the record), Downs given / taken, Active % <-> `active_share`, Tags per Active minute <-> `tags_per_active_min`, Eliminated % <-> `eliminated`, Enemies on the floor per Active roll <-> `enemies_on_floor` (the real one is averaged over the game, the sim's over this player's Active rolls), and the two matrices. A metric with no real figure prints as without targets. A missing file is a `CommandError`.

**The tag measures (CAL-02):** per Role, each as `mean +/- se (min-max)` where the standard error is over the **player-rounds** (the counter's convention, so it compares with the targets file's `se`) and the range is across the seeds' means. **Times tagged per round** (`counters.times_tagged`), **Shots fired per round** (`tags_made + shots_missed`), **Misses at a hiding target per round** (a miss whose defender `is_hiding` before the resolve), **Downs given / taken per round** (`ShotOutcome.downed` of a Hit, to the attacker and the defender; a Missile or Nuke Down is not one), **Active %** (`ticks_active / 1800`), **Tags per Active minute**, **Reaction / Follow-up shots per round** (`counters.reaction_shots`, `follow_up_shots`), **Enemies on the floor per Active roll** (at each Active `plan_action`, the enemies in `all_alive` with lives), **Mode % of round** (`mode_ticks` over the round's 1800 ticks; `Mode %` is over the Mode ticks, which stop at Elimination) and **Shot cadence, ticks** (the median gap between a player's consecutive shots and the share per bin: `<=1t`, `2t`, `3-4t`, `5-10t`, `11-20t`, `21-60t`, `61-120t`, `120t+`; `none` with no gap). Then the two matrices per round, both teams, attacker row by defender column with a total: **Who tags whom** and **Who Downs whom**. The shot observers wrap `resolve_shot` in `entrypoints` (the tick loop's Shots) and in `sim_helpers.shot` (a Reaction or Follow-up whose cooldown rounds to 0 ticks).

- Time in each Mode, where goals come from (action, positioning, override), the share of ticks not Active (time after Elimination counts), squares to the Role's target (Commander, Heavy, Scout: the nearest enemy Medic or Ammo; Medic, Ammo: the own Heavy) and mean points.
- **Action %:** the share of the Role's Active rolls per Action. **Empty rolls %:** per Action, the share of that Action's Active rolls that planned nothing; a seed that never rolled the Action is left out of its range. Only Actions the Role rolled print.
- **Still %:** Active ticks where the player is hiding, holding, or rolled `hide` or `capture_base`. The roll counts because the dispatch sets `is_hiding` after `plan_action` and before the Advance, so the player does not move on that tick.
- **Hold %** and **Hold run, ticks:** Active ticks only. A run ends when the player stops holding; a tick not Active while still holding neither extends nor ends it.
- **Tags per round (planned Shots)** and **Resupplies per round (planned):** per player-round, the `tag` and `resupply_*` plans. These are what the rolls asked for, not outcomes: a planned Shot may miss, and `resupplies_given` also counts Resupply requests answered, so it reads higher (Medic 68.8 against 61.5).
- **Tags made per round:** the `tags_made` counter per player-round: what landed, and the number that compares with LFStats' shots hit.
- **Live captures per round:** successful `capture_base` calls per player-round. The round-end award to survivors does not count.
- **Eliminated %** and **Elimination tick:** the share of player-rounds that ended Eliminated, and their mean tick (`none` with no Elimination; a seed with none is left out of the range). Compare with LFStats' `elimination_rate`.

Then the team-wide lines: live captures per round for each team (`ProbeResult.team_captures_by_seed`), and goal sharing: the mean of the rounds, min, max, rounds at 15% or more, and each seed's value. Single rounds vary widely (MECH-07: goal sharing from 9% to 24%), so read the spread before trusting a difference. `test_teammates_rarely_share_a_goal` pools all the ticks instead, so it reads slightly differently (14.5% against 14.6%).

Six Players per team on the model's default stats (or the Test lineups), no database writes (the fixture loads in a rolled-back transaction), and observing draws no RNG: it wraps `plan_action`, `choose_goal_cell`, the capture and `resolve_shot` in `entrypoints` (and `resolve_shot` in `sim_helpers.shot`), and `test_observing_draws_no_rng` checks the RNG ends where an unobserved round leaves it. Each round is one task (`_probe_round`) that installs the observers, seeds, plays and puts them back, in this process or in a worker. Two copies at once on one SQLite database collide on the fixture load, so run them one at a time per database.

**Scoring a mutation:** `python scripts/mutate.py <file> <mutations.json> --measure -- python laserforce_simulator/manage.py round_probe --seeds 4` prints the numbers on the unchanged code and under each mutation.

**Comparing with a baseline:** a detached worktree at the commit to compare, with its own database. It needs a commit that has this command.

```bash
git worktree add --detach .claude/worktrees/<task>-baseline <commit>
# copy laserforce_simulator/.env into it, run migrate there, then its manage.py round_probe
```

The main checkout's `db.sqlite3` may be behind on migrations, so use a worktree rather than the main checkout. Remove the worktree when done.

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
python manage.py seed_browser_check --league # also seed Account A "Browser Check League" and "Browser Check Conferences"
python manage.py seed_browser_check --tournaments # also seed Account A one Tournament per format and stage
python manage.py seed_browser_check --clean  # delete both Accounts, every row they own, both Leagues and the map
```

Account A gets four valid Teams, a simulated sandbox Match, a map-less Single Round, a **mapped
Single Round** and an unfinished Match (round 1 only, `is_completed=False`, no winner: what a League
Match looks like mid-Season). The mapped Single Round plays on "Browser Check Arena", a 30x20-cell
map (zone size 20) whose PNG is drawn in code with PIL (no `media/` file needed) and uploaded via
`core/services.py::upload_map`, then given a confirmed zone config with red/blue bases, sight lines
and base sight lines, so `load_map_context` resolves. The Round is upgraded to `fidelity="full"`
(movement events) for Playback's map, the heatmap and map-editor checks. A real Round can fire no
missile, so `create_mapped_round` simulates up to `MISSILE_ROUND_ATTEMPTS` (5) times. If none fires, it
keeps the last Round and the summary prints a warning: a short Round (the Tournament tests' 40 ticks)
fires none, and the rest of the seed must still run. Its first missile hit
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
  availability with figures (League Finances' table stays `—`: it shows the active Season).
ANG-06-18 addition:
- **Draft Conference League:** a second League, "Browser Check Conferences" (`manager` = A,
  `mode="sandbox"`, so not career), whose only Season is a **draft** over A's same four Teams with
  `map_mode="rotate_by_conference"`, split by `ConferencePartitionForm` + `replace_conferences`
  into "North" (first two Teams, rotation = the Browser Check Arena once) and "South" (last two,
  the Arena twice), so Manage Conferences is editable and Standings shows the draft preview. The
  summary gains `League <id> (Browser Check Conferences), Season <id> (draft, 2 Conferences)`.

The rows are built by
`_browser_check_league.py` (a copy of the `league_api_fixtures.py` shapes: production code never
imports test modules; the leading underscore keeps it out of the command list). The summary gains
`League <id> (Browser Check League), Seasons <id>, <id>, <id> (completed, completed, active)`.
`--clean` deletes both Leagues' Matches and then the Leagues, which cascade their Seasons and
Conferences (deleting the Account alone would not: `League.manager` and `Match.season` are
`SET_NULL`), plus any Unmanaged League of either name, and reports `Leagues: N` first (2 after a
`--league` seed); with no League the `removed` line is unchanged. It does not call
`league_services.teardown_league`: that one's orphan-Team sweep would delete A's Teams (and their
Single Rounds) before they are counted.
A `--league` seed is refused while a League of either name exists. Without `--league` the seed is
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
