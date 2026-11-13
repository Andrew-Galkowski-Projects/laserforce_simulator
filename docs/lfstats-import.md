# LFstats player import — decisions

Estimate simulator stats for real players from their LFstats
performance. Roadmap: [PLAN.md](../PLAN.md).

## Approach (decided 2026-10-21)

- **Data source (for now):** Claude pulls stats through the LFstats connector and writes a
  CSV in the existing roster-import format (`teams/roster_importer.py`, LG-00b). The app has
  no LFstats code yet.
  - *Later:* ask the LFstats creator for API access so the app can fetch current stats itself.
- **Mapping (for now):** fixed formulas per stat.
  - *Later:* calibrate against the sim, so a simulated player reproduces their real LFstats
    numbers. This may need app changes.
- **Stats with no direct LFstats signal:** use a proxy from related metrics where possible.
  Each stat is decided one at a time below.
- **Scope:** SM5, the last 2 years (rolling), social and competitive games, so occasional
  players still have data.
- **Small samples:** no adjustment. Raw averages are used whatever the game count.

## Generated roster (2026-10-21)

- **Players:** every SM5 player with 26+ games from 2024-10-21 to 2026-10-21: 230 players.
- **Files:** `data/lfstats_snapshot_2026-10-21.json` holds the raw LFstats data, and
  `data/lfstats_players.csv` is the importable roster built from it.
- **Rebuild:** `python scripts/lfstats_import/compute.py data/lfstats_snapshot_2026-10-21.json data/lfstats_players.csv`.
  `extract.py` builds a snapshot from the Claude Code session transcripts that fetched the
  data through the LFstats connector. Tests: `laserforce_simulator/test_lfstats_import.py`.
- **Formulas:** the stat formulas and their tunables (`ImportConstants`) live in `laserforce_simulator/teams/lfstats_formulas.py`; `compute.py` imports them.
- **Calibration data (LFS-01):**
  - **Test set:** `data/lfstats_test_internationals_2026.json` holds the 165 Internationals 2026 games (each team's colour, score, result, and roster with position, score, MVP and mercenary flag) and every (player, position)'s Internationals averages.
  - **Training set:** `data/lfstats_train_2026-10-21.json` is the snapshot with those games subtracted from every row (average x games), plus window rows for Internationals players outside the 230.
  - **Built by:** `python scripts/lfstats_import/calibration_data.py data/lfstats_snapshot_2026-10-21.json data/lfstats_train_2026-10-21.json data/lfstats_test_internationals_2026.json <transcript.jsonl>`. It reads the LFstats calls the same way `extract.py` does, including results too large for the transcript, which Claude Code saves to `tool-results/` files.
  - **Map:** `data/maps/syracuse.json` is the Syracuse Laser Tag configuration the Test set replays on. It uses 20px cells at `feet_per_pixel` 0.05, so a square is 1 ft (the owner measured 10 ft as about 10 squares on 2026-11-05; the earlier 30px / 5 ft fixture made the sim play an arena 3.3x too large in every feet-based rule), with auto-detected walls and red, blue and one neutral base at the image's base markers. The fixture carries the scale (`export_map_fixture` writes it, `load_map_fixture` reads it). Export a configured map with `python scripts/lfstats_import/export_map_fixture.py "<map name>" <zone size> <out.json>`.
  - **Cell targets:** `data/lfstats_cell_targets.json` is the real side of every Test cell (position, metric, real mean, scorecards and the tolerance band), written by `python manage.py lfstats_calibrate --split targets --out data/lfstats_cell_targets.json` with no simulation. A sweep or a one-off run is measured against it; `test_lfstats_calibrate_command.py::TestCommittedTargets` keeps it equal to what the committed Training and Test files give.
  - **Baselines:** the snapshot's, not subtracted. Internationals is about 4% of the window's scorecards.
  - **Tests:** `laserforce_simulator/test_lfstats_calibration_data.py`.
  - **TDF role targets (CAL-02):** `data/tdf_role_targets_2026.json` is the real per-role table of "Tags and times tagged" below (Tags, times tagged, shots fired, Downs, Active share, Eliminations, the who-tags-whom and who-Downs-whom matrices, shot cadence, enemies on the floor, the Medic-Elimination split), built from the Internationals 2026 TDF event logs by `python -I scripts/tdf_role_stats.py <logs dir> --out data/tdf_role_targets_2026.json [--fetch]`. The logs (165 files, 38 MB) stay out of the repo; `--fetch` downloads the Test file's missing `<slug>.tdf` from `https://lfstats-modern-archive.s3.us-west-1.amazonaws.com/<slug>.tdf`. `test_tdf_role_stats.py::test_committed_targets_match_the_logs` rebuilds the file when `TDF_LOGS_DIR` names the logs folder and skips otherwise.
- **Teams:** grouped by home center (the center where the player plays most, from LFstats).
  Each center fields as many full 6-player rosters as it can (`<Center> 1`, `<Center> 2`, ...):
  1. main role first;
  2. then the second preferred role;
  3. then single-role scouts in any slot. They keep `preferred_roles = scout`, so they would
     get no preferred-role boost in the other role (the calibrated boost is 1.0, no bonus, so
     today this changes nothing in play).
- **Free Agents:** everyone left over goes to the Free Agents pool. The importer skips slot
  limits and slot assignment for `team = Free Agents`. Result: 34 full teams and 26 free
  agents.
- **Profile:**
  - `age` 25 and `started_playing_age` 18 are placeholders, since LFstats has no ages;
  - `total_games` is the all-time SM5 count;
  - `home_site` is the home center;
  - `height` is blank.
- **preferred_roles:** the most-played position, plus the second if it has at least 20% of
  the player's games.
- **Duplicate callsigns:** different players can share one. When two land on the same team,
  the later one gets its IPL id appended.

## Source for what each stat means

The stats come from Kodama's talk "How to Improve in SM5"
([video](https://www.youtube.com/watch?v=SngT6ipJsYU), Force-ProSports SM5 201 playlist). It
splits a player into six skill areas, shown as a pie chart with the largest share first:

| Area (share) | What it covers in the talk | Stats |
|---|---|---|
| Game sense / awareness (largest) | Knowing the flow of the game; always tracking where all 11 other players are; watching your own shots, lives and special points; deciding within about a second instead of hesitating | player_awareness, game_awareness, resource_awareness, decision_making |
| Positioning | "Why am I here, what am I doing?"; knowing angles; holding control zones, not neutral ones; how far forward to play for your shots and lives | positioning |
| Physical ability | Cardio so you don't slow down in the second half; sprints with changes of direction; yoga for contorting into angles and dodging missiles; squats for resupplies holding a corner | stamina, speed, flexibility |
| Adaptability | Adjusting to the game's flow and to unfamiliar teammates; playing every position; trying just as hard when losing or when the game looks won | adaptability |
| Communication | Clear callouts with location ("commander down at big box"); sharing your shots and lives; announcing nukes so teammates react at once | communication, teamwork |
| Mechanical skill (about 5%) | Popping out and angles, juking, missile dodging, laying trails while down, stubborn zone control | accuracy, survival, special_usage |

- The synergy stats (offensive, defensive, midfield, resupply) and resupply_efficiency are not
  covered by the talk.
- **Possible later change:** `overall_rating` is currently a plain mean of all 19 stats. The
  pie chart suggests weighting it by area, with game sense highest and mechanical skill lowest.

## Per-stat decisions

The 0-100 value written to the CSV. "Sim effect" summarises how the simulator reads the
stat today. At run time, `Player.stat_for_simulation` multiplies the value by
`ImportConstants.preferred_role_boost` (capped at 100) in a preferred role. LFS-01's
calibration set that to 1.0, so a preferred role no longer adds a bonus; it was 1.2 (see
**Calibration results**).

| Stat | Sim effect | LFstats source | Formula | Status |
|---|---|---|---|---|
| accuracy | Hit chance = `HIT_BASE + accuracy - defender.survival`, `HIT_BASE` 49 (70 before MECH-23) (clamped 10-95), `matches/sim_helpers/shot.py` | `avg_accuracy`, all positions combined | `round(avg_accuracy * 100)` | Decided |
| survival | Subtracted from attacker hit chance, `matches/sim_helpers/shot.py` (MECH-24: no longer the missile dodge, which moved to flexibility) | `avg_times_hit` per position + `elimination_rate` | Mean of a times-hit part and a non-elimination part (see below) | Decided |
| player_awareness | As the target: return-fire chance, blocks attacker follow-up chains (`shot.py`); nuke reaction `(ga + pa)/200` (`round_loop.py`) | `avg_hit_diff` + `avg_nukes_canceled` per position | Hit-diff part plus a nukes-canceled bonus of up to +10 (see below) | Decided |
| game_awareness | Below 35, sometimes shoots enemies who are already down (`mechanics.py`); commander nuke SP banking tiers 30/50/70 (`weights.py` `_commander_nuke_gate`); half of the nuke-reaction roll | `avg_mvp` per position | `50 + 25 * log2(player_mvp / position_avg_mvp)`, weighted by games (see below) | Decided |
| resource_awareness | None today: `_observe_lives` (`round_loop.py`) records enemy lives, but nothing reads them back | Per-game event timeline (TDF) | `50` for now (see below) | Deferred |
| decision_making | Sharpens action choice: top weight x`(1 + dm/100)`, the rest divided by it (`weights.py`); medic/ammo `_stress_fail` (`resupply_queue.py`; higher lowers the fail chance, SM5-01) | `win_rate`, `avg_penalties`, `avg_shots_hit` per position | Mean of a results part and an activity part (see below) | Decided (revised after the Kodama talk) |
| stamina | Fatigue at each 10% of the round when stamina < checkpoint: -5% hit chance, -10% movement per penalty (`weights.py`, `player_state.py`) | Late- vs early-game performance (TDF) | `50` for now | Deferred |
| speed | Cells per tick `max(1, ceil(speed/100 * max_movement))` (`pathfinding.py`) | None (no position data, even in TDF) | `50` for now (see below) | Deferred |
| positioning | MOVE-06: sharpens the positioning-goal draw, `T = 0.05 + 0.45 x (1 - positioning/100)` (`goal_scoring.py`; high = picks near the best cell); also feeds `overall_rating` | `avg_uptime_pct` + `avg_hit_diff` per position | Mean of an uptime part and a hit-diff part (see below) | Decided |
| adaptability | None (only feeds `overall_rating`) | `avg_mvp` per position, `team_result: loss` scope | `50 + 25 * log2(player_mvp_in_losses / position_avg_mvp_in_losses)`, weighted by games | Decided |
| communication | Each tick and after a tag, with probability `comm/100`, shares enemy memory with allies within half the map diagonal; allies pathfind to known enemies (`round_loop.py`, `pathfinding.py`). Not the nuke call-out: every enemy learns the Commander's cell the tick a nuke starts, with no reaction delay (SM5-07) | `avg_assists` per position (team uplift later) | `50 + 25 * log2(player_assists / position_avg_assists)`, weighted by games (see below) | Decided |
| teamwork | Above 50, with probability `tw/100`, moves to a high-sight cell an ally can see (`pathfinding.py`); medic/ammo `_stress_fail` (higher lowers the fail chance, SM5-01) | `avg_resupplies_given` (ammo/medic), `avg_assists` (others), per position (team uplift later) | Log ratio vs position average, weighted by games (see below) | Decided |
| Offensive_synergy | None (only feeds `overall_rating`) | `avg_medic_hits` + `avg_eliminations` per position | Mean of two log-ratio parts vs position average (see below) | Decided |
| defensive_synergy | None (only feeds `overall_rating`) | `avg_mvp` at ammo, medic and heavy | MVP log ratio vs position average over those games (see below) | Decided |
| midfield_synergy | None (only feeds `overall_rating`) | `avg_mvp` at heavy and scout | MVP log ratio vs position average over those games (see below) | Decided |
| resupply_synergy | Medic/ammo `resupply_ally` weight x`rs/50` (`weights.py`); combo resupply chance `min(0.95, 0.20 + rsA*rsM/1e4 + reA*reM/1e4)` (`resupply_queue.py`) | `avg_resupplies_given` at ammo/medic | Log ratio vs position average over ammo/medic games; others count 50 (see below) | Decided |
| resupply_efficiency | All roles: `request_resupply` weight `re/2` when below max (`weights.py`); part of the combo chance | `avg_uptime_pct` per position | `50 + 100 * log2(player_uptime / position_avg_uptime)`, weighted by games | Decided |
| special_usage | Medic/ammo team boost weight, scout rapid-fire weight, commander nuke weight (`weights.py`); missile lock survives lost line of sight with probability `su/100` (`combat.py`) | `avg_nukes_detonated`, `avg_rapid_fire`, `avg_missiles_hit`, `avg_resupplies_given` | Per-position special log ratio vs position average, weighted by games (see below) | Decided |
| flexibility | The **Dodge** chance, `flexibility / 2`% per Lock on the dodger, `matches/sim_helpers/combat.py` `resolve_dodge` (MECH-24, a sim stat in `_SIMULATION_STATS`); also feeds `overall_rating` | Community opinion (later); candidate: `times_hit_by_missile` per game (Kodama: yoga, contorting into angles, dodging missiles) | `50` for now | Deferred |

### accuracy

- Use the player's raw overall LFstats accuracy, not a value relative to their position. The
  stat should be a near-direct copy of what LFstats shows.
- **Later:** accuracy per position. Real accuracy varies a lot by position: field averages
  over the last 2 years are scout 66%, commander 77%, ammo 79%, heavy 83% and medic 90%.
  Even one player differs per position, and the sim should reproduce that. This needs the app
  to support per-role stat values, which `Player` cannot hold today.

### survival

LFstats has no direct "opponents miss you" metric, so the stat blends two proxies, each
clamped to 0-100:

- **Times-hit part (position-relative):** `50 + 25 * log2(position_avg_times_hit / player_times_hit)`,
  computed per position and averaged weighted by games. Raw `avg_times_hit` mostly reflects
  position (field averages: commander 69, heavy 57, scout 37, ammo 25, medic 11), so each
  position is compared with its own field average. Tagged half as often as that average = 75,
  twice as often = 25.
- **Non-elimination part (raw):** `100 * (1 - elimination_rate)`.
- `survival = round((times_hit_part + non_elim_part) / 2)`.
- Position averages come from the LFstats `baseline` over the same 2-year scope.

### player_awareness

- **Hit-diff part (raw):** `50 + 25 * log2(avg_hit_diff)`, using the overall value across all
  positions. Even trades (1.0) = 50, 2.0 = 75, 0.5 = 25. The field average is 0.92.
- **Nukes-canceled bonus (position-relative, never a penalty):** per position,
  `10 * clamp((player_nc - position_avg_nc) / 0.06, 0, 1)`, averaged weighted by games.
  Cancels are rare (field average 0.016 per game, top players about 0.10) and depend on
  position (commander/heavy about 0.04, scout 0.011, ammo/medic about 0.004). A log formula
  is not usable because many players have 0.
- `player_awareness = round(clamp(hit_diff_part + nukes_bonus, 0, 100))`.

### game_awareness

- MVP is the overall game-impact proxy. It is compared per position because the field
  averages differ: commander 16.5, medic 15.5, heavy 14.3, ammo 14.1, scout 13.0.
- Per position: `50 + 25 * log2(max(player_mvp, 0.5) / position_avg_mvp)`, then averaged
  weighted by games and clamped 0-100. Twice the position average = 75, half = 25. The 0.5
  floor stops the log failing when a player has zero or negative MVP (penalties can push it
  negative); it maps to about 0.

### resource_awareness (deferred)

**Definition:** tracking both the enemy's resources and your own. Signals:

- How often enemies are eliminated *before* the enemy medic (positive).
- How often the player is eliminated early, *before* their own medic (negative).
- How often the player runs out of shots: mid-game and then refilled, or empty at game end
  (negative).
- Uptime %, plus knowing when few more resupplies are needed because the game is nearly
  over (positive).

**Data:** LFstats aggregates have none of this. Per-game scorecards (`lfstats_game_detail`)
cover it only partly: `shots_left == 0`, and `eliminated` flags without their order. The full
signal needs each game's event timeline.

**Decision:** written as `50` for now. The stat has no gameplay effect today.
**Preferred route later:** download and parse each game's TDF event log (linked from
the game detail as `tdf_url`) for exact elimination order, mid-game shot outages and resupply
timing. Cost: one file per game per player (about 150 for a regular player over 2 years).

### decision_making

Kodama treats this as decisiveness: act within about a second and trust your gut. The sim's
effect points the same way, since a high value commits harder to the top action. The stat
blends results with tempo, each part clamped to 0-100:

- **Results part (raw):** `100 * win_rate - 20 * avg_penalties`. This was the first decision.
- **Activity part (position-relative):** per position, `50 + 25 * log2(player_shots_hit / position_avg_shots_hit)`,
  averaged weighted by games. Decisive players engage more often.
- `decision_making = round((results_part + activity_part) / 2)`.

### positioning

Kodama: know why you are where you are, know the angles, hold control zones, and set how far
forward to play from your shots and lives. A well-positioned player lands tags without
taking them and stays up. Both parts are position-relative, computed per position, averaged
weighted by games, and clamped to 0-100:

- **Uptime part:** `50 + 100 * log2(player_uptime / position_avg_uptime)`. Uptime only varies
  by about +-0.1 around each position average (scout 0.50, commander 0.51, heavy 0.55, ammo
  0.61, medic 0.76), so the steeper x100 scale is needed to spread players out.
- **Hit-diff part:** `50 + 25 * log2(player_hit_diff / position_avg_hit_diff)` (averages:
  heavy 0.88, commander 0.89, ammo 0.97, medic 1.08, scout 1.10). This partly overlaps
  player_awareness, which uses the raw overall hit diff.
- `positioning = round((uptime_part + hit_diff_part) / 2)`.

### adaptability

Kodama: adjust to the flow of the game and to unfamiliar teammates, and keep trying when
losing. The measurable part is "keep performing on the back foot":

- Query LFstats with `scope.team_result = "loss"`, so the player's MVP and the position
  baseline both cover only games the player's team lost.
- Per position: `50 + 25 * log2(max(player_mvp_in_losses, 0.5) / position_avg_mvp_in_losses)`,
  averaged weighted by games in losses, clamped 0-100. The 0.5 floor matches game_awareness.
- A player with no losses in scope gets `50`.

### communication

Kodama: clear callouts with location, sharing your shots and lives, announcing nukes, calling
every down. LFstats records no talking, so the stat uses the visible effect: teammates
converting the player's downs into eliminations.

- **Now:** per position, `50 + 25 * log2(max(player_assists, 0.25) / position_avg_assists)`,
  averaged weighted by games, clamped 0-100. The 0.25 floor handles players with no assists.
- **Preferred later:** team uplift, meaning how much more MVP teammates score in games with
  this player than in their other games. Needs per-game teammate data (game detail or TDF).

### teamwork

Kodama pairs this with communication: coordinating nukes and playing off what teammates need.

- **Now:** per position, clamped 0-100 and averaged weighted by games:
  - ammo and medic games: `50 + 25 * log2(max(player_resupplies, 0.25) / position_avg_resupplies)`;
  - commander, heavy and scout games: `50 + 25 * log2(max(player_assists, 0.25) / position_avg_assists)`.
    This overlaps communication.
- **Later:** team uplift from per-game teammate data, the same signal planned for
  communication.

### Zone synergies (Offensive, defensive, midfield)

Not covered by the Kodama talk. These definitions are the user's (2026-10-21). Every part is
per position, clamped 0-100, and averaged weighted by games.

- **Offensive_synergy** (getting deep and finishing): the mean of
  `50 + 25 * log2(max(player_medic_hits, 0.25) / position_avg_medic_hits)` and
  `50 + 25 * log2(max(player_eliminations, 0.25) / position_avg_eliminations)`, over all positions.
- **defensive_synergy** (holding the back line): `50 + 25 * log2(max(player_mvp, 0.5) / position_avg_mvp)`
  over ammo, medic and heavy games only.
- **midfield_synergy** (contesting the middle): the same MVP formula over heavy and scout games
  only.
- A player with no games at the listed positions gets `50` for that synergy.

### Resupplies and specials

Every part is per position, clamped 0-100, averaged weighted by games, with a 0.25 floor on
count metrics.

- **resupply_synergy:** ammo/medic games use `50 + 25 * log2(player_resupplies / position_avg_resupplies)`.
  Commander, heavy and scout games count as `50`. This overlaps teamwork, which uses the same
  metric for supports.
- **resupply_efficiency:** `50 + 100 * log2(player_uptime / position_avg_uptime)` over all
  positions, the same as the positioning uptime part. Little time is lost walking back and
  waiting for resupplies.
- **special_usage:** `50 + 25 * log2(player_special / position_avg_special)`, where the
  special is the position's signature metric:
  - commander: `avg_nukes_detonated`;
  - heavy: `avg_missiles_hit`;
  - scout: `avg_rapid_fire`;
  - ammo and medic: `avg_resupplies_given`, as a stand-in. The team ammo/life boosts are only
    in per-game MVP breakdowns (`ammo_boost`, `life_boost`); switch to them once per-game
    data is fetched.

### stamina (deferred)

- Written as `50` for now, so every imported player fades equally and comparisons between
  them are not distorted.
- **Later (TDF):** measure fade as the player's tag rate in the late game compared with the
  early game. Less drop-off = higher stamina.

### speed (deferred)

- Written as `50` for now. LFstats has no movement data.
- **Later:** poll the community for speed ratings of real players, then test which proxy
  matches the poll best. Candidates:
  - shots-hit volume vs position: `50 + 25 * log2(player_shots_hit / position_avg)`, as a tempo signal;
  - medic hits vs position: `50 + 25 * log2(player_medic_hits / position_avg)`, as a sign of
    getting deep into enemy territory.

## Calibration results (LFS-01, 2026-11-03)

Spec #514, ticket #521. Every number below is from one of the two committed runs:
[`lfstats-calibration/2026-11-03-search-train.txt`](lfstats-calibration/2026-11-03-search-train.txt)
(the search) and
[`lfstats-calibration/2026-11-03-test-internationals.txt`](lfstats-calibration/2026-11-03-test-internationals.txt)
(the single Test run).

### Method

- **Training:** each (player, position) in `data/lfstats_train_2026-10-21.json` with 26+ games
  there plays 3 rounds at that position on Syracuse, with five field-average teammates and six
  field-average opponents built from the position baselines. 542 players across the five
  positions.
- **Search:** a seeded coordinate search over `accuracy_offset` (-20 / -10 / 0) and
  `preferred_role_boost` (1.0 / 1.1 / 1.2), scored on Training only. The objective is the mean
  over (position, metric) cells of `((sim - real) / max(|real|, floor))^2`, with floors 0.5 for
  a count and 0.05 for a fraction. The other three constants stayed at their defaults: a first
  full search moved the objective by about 0.001 over `log_scale`, below the 1-round noise,
  and the owner narrowed it (comment on #514).
- **Test:** each usable Internationals 2026 game is replayed 10 times with its real lineup (Fire
  red, Ice blue), each player built from their Training row at the position played. It ran once,
  after the search, and no constant was changed after it.
- **Gate:** a cell passes when the simulated mean is within 15% of the real mean, or within 0.5
  for a count / 0.05 for a fraction when that is wider. Spearman per player is reported, not
  gated.

### Chosen constants

| Constant | Before | Chosen |
|---|---|---|
| `log_scale` | 25 | 25 |
| `accuracy_offset` | 0 | 0 |
| `accuracy_scale` | 100 | 100 |
| `survival_times_hit_weight` | 0.5 | 0.5 |
| `preferred_role_boost` | 1.2 | **1.0** |

Training objective: 0.2405 at the old defaults, **0.2127** chosen. `preferred_role_boost` 1.1
scored 0.2142, within the noise of 1.0. Lowering accuracy made the fit worse (0.2590 at -10,
0.2850 at -20). Training cells passed: 14 / 36.

The boost is read by `Player.stat_for_simulation` for every Player, so a preferred role gives
no bonus anywhere in the app now (the owner chose this). The stat formulas did not change,
so `data/lfstats_players.csv` is unchanged: rebuilding it from the snapshot is byte-identical.

### Test set (Internationals 2026, Syracuse, 10 rounds per game)

| Position | Metric | Sim | Real | Pass | Rho |
|---|---|---|---|---|---|
| commander | accuracy | 0.793 | 0.841 | PASS | 0.54 |
| commander | hit diff | 1.268 | 0.977 | PASS | 0.63 |
| commander | times hit | 59.3 | 59.1 | PASS | -0.09 |
| commander | MVP | 14.35 | 18.74 | FAIL | 0.64 |
| commander | uptime | 0.434 | 0.542 | FAIL | -0.11 |
| commander | elimination rate | 0.475 | 0.380 | FAIL | 0.59 |
| commander | nukes detonated | 0.50 | 2.79 | FAIL | -0.48 |
| commander | missiles hit | 0.95 | 2.47 | FAIL | 0.45 |
| heavy | accuracy | 0.759 | 0.854 | PASS | 0.31 |
| heavy | hit diff | 1.671 | 0.969 | FAIL | 0.70 |
| heavy | times hit | 50.0 | 47.8 | PASS | -0.09 |
| heavy | MVP | 19.85 | 15.64 | FAIL | 0.53 |
| heavy | uptime | 0.439 | 0.586 | FAIL | 0.39 |
| heavy | elimination rate | 0.507 | 0.429 | FAIL | 0.50 |
| heavy | missiles hit | 0.69 | 1.76 | FAIL | 0.52 |
| scout | accuracy | 0.704 | 0.742 | PASS | 0.77 |
| scout | hit diff | 1.056 | 1.117 | PASS | -0.35 |
| scout | times hit | 39.4 | 33.8 | FAIL | 0.11 |
| scout | MVP | 12.34 | 13.84 | PASS | 0.34 |
| scout | uptime | 0.346 | 0.516 | FAIL | -0.26 |
| scout | elimination rate | 0.613 | 0.447 | FAIL | 0.26 |
| scout | rapid fire | 2.15 | 1.57 | FAIL | 0.13 |
| ammo | accuracy | 0.749 | 0.795 | PASS | 0.60 |
| ammo | hit diff | 0.920 | 1.100 | PASS | -0.19 |
| ammo | times hit | 36.8 | 23.2 | FAIL | 0.07 |
| ammo | MVP | 17.79 | 14.79 | FAIL | 0.11 |
| ammo | uptime | 0.279 | 0.610 | FAIL | 0.10 |
| ammo | elimination rate | 0.864 | 0.447 | FAIL | 0.34 |
| ammo | resupplies given | 70.1 | 36.2 | FAIL | 0.15 |
| medic | accuracy | 0.789 | 0.902 | PASS | -0.12 |
| medic | hit diff | 0.456 | 1.062 | FAIL | -0.35 |
| medic | times hit | 19.7 | 10.2 | FAIL | 0.16 |
| medic | MVP | 13.07 | 15.95 | FAIL | -0.20 |
| medic | uptime | 0.387 | 0.765 | FAIL | 0.05 |
| medic | elimination rate | 0.964 | 0.660 | FAIL | 0.26 |
| medic | resupplies given | 55.5 | 40.8 | FAIL | -0.08 |

- **Cells passed:** 11 / 36. Accuracy passes at every position; scouts match best.
- **Coverage:** 128 of 165 games used; 1 skipped for its lineup, 36 for thin data (a player
  with under 5 Training games at the position played).
- **Match level:** the sim picks the real winner in 61.7% of the 128 games; mean margin error
  8,336 points.
- **Scorecards:** 256 per position (512 scouts), from 128 games x 2 teams.

### Commands

```
python laserforce_simulator/manage.py lfstats_calibrate --split search --search-fields accuracy_offset,preferred_role_boost --rounds 3 --seed 42 --workers 8
python laserforce_simulator/manage.py lfstats_calibrate --split test --preferred-role-boost 1.0 --rounds 10 --seed 42 --workers 8
```

The Test command predates the new default; after this change `--preferred-role-boost 1.0` is
the default and can be dropped.

### Fixed along the way

- **Spawns cut off from the floor.** On Syracuse at the earlier 30px grid the corridor by
  red's base became two pockets that did not connect to the main floor, and 9 of red's 11
  spawn cells were in them. Four red players never moved or engaged, which made every earlier
  run on the map invalid. `map_loader.reachable_spawn_pools` now moves such cells to the
  nearest main-floor cell to the base. This also fixes app games on that map. The 20px fixture
  has no pocket, so the rule leaves its pools unchanged.

### Findings (LFS-02 / CAL-01)

Every failing Test cell, grouped by the mechanic it most likely needs. Import constants could
not close these gaps: they are in how the sim plays, not in the imported stats.

- **Downtime is about double the real figure at every position.** Uptime fails everywhere
  (commander 0.43 vs 0.54, heavy 0.44 vs 0.59, scout 0.35 vs 0.52, ammo 0.28 vs 0.61, medic
  0.39 vs 0.77), even where times hit matches (commander, heavy). Check the respawn timing
  (`RESPAWN_TICKS`, `NOT_TARGETABLE_TICKS`) against SM5, and whether the sim's uptime
  (`ticks_active / live ticks`) matches LFstats' definition.
- **Too many eliminations.** Elimination rate fails at every position, worst for support
  (ammo 0.86 vs 0.45, medic 0.96 vs 0.66). Lives are lost faster than medics resupply them.
- **Support players are too exposed.** Ammo times hit 36.8 vs 23.2 and medic 19.7 vs 10.2.
  `_goal_from_role` sends ammo to the highest-sight cell near the heavy, and medics may also
  stand where enemies can see them. Medic hit diff (0.46 vs 1.06) and MVP (13.1 vs 15.9)
  follow from being hit about twice as often. **Addressed in part by the support-survival
  switches (below, 2026-11-06).**
- **Resupplies are too frequent.** Ammo gives 70.1 vs 36.2, medic 55.5 vs 40.8. Check the
  resupply cooldown and the support `resupply_ally` weights. Ammo MVP (17.8 vs 14.8) is high
  because of it.
- **Commanders detonate too few nukes.** 0.50 vs 2.79 per game. Check SP gain, the nuke gate
  (`_commander_nuke_gate`) and how often a nuke is cancelled. Also, `ctx.pending_nukes` is a
  stale alias that is rebound every tick, so `record_down` never sees a live nuke; LFS-01
  counts the cancel at the drain fallback instead. Commander MVP (14.4 vs 18.7) is low mostly
  because of missing nukes and missiles.
- **Missiles land too rarely.** Commander 0.95 vs 2.47 and heavy 0.69 vs 1.76. Check missile
  count, lock time and lock-break chance.
- **Heavies trade too well.** Hit diff 1.67 vs 0.97 and MVP 19.8 vs 15.6, while times hit
  matches. Heavies tag too often, about 84 tags against 46 implied by LFstats, so check the
  heavy's tag weight and shot cadence.
- **Scouts rapid-fire too often** (2.15 vs 1.57) and are hit a little too much (39.4 vs 33.8).
- **Scout MVP misses the commander / heavy tag bonus.** The batch engine never fills
  `specific_tags`, so the scout's +0.2 per commander or heavy tag never scores. Scout MVP
  passes anyway (12.3 vs 13.8), but the cell is low.

### Support survival (2026-11-06, Syracuse at 20 px / 1 ft)

The map scale fix (20 px squares at 1 ft, from the 30 px / 5 ft fixture) moved the baseline from
11/36 to 14/36 cells at 3 rounds (objective 0.171, winner agreement 54.7%). A sweep of 26
single engine constants found none that beats 16/36; the structural gaps were the supports.
Five switches in `matches/sim_helpers/support_survival.py` were then run in every combination
on the Test set (1 round each, 34 runs, then the best four at 3 rounds), with the letters
T (target priority), H (hidden support cells), M (the Medic holds position), A (the Ammo hides
freely) and C (teamwork call-outs with 30-tick support memory).

| Run (3 rounds) | Cells | Objective | Winner % | Margin error |
|---|---|---|---|---|
| baseline | 14/36 | 0.171 | 54.7 | 8298 |
| combined constants (nuke gate 20 SP, ammo resupply 35, Downed stay 40, missile bump 30) | 15/36 | 0.146 | 55.5 | 8540 |
| TMA | 15/36 | 0.144 | 59.4 | 7978 |
| THMAC | 15/36 | 0.148 | 54.7 | 8497 |
| THMAC + combined constants | 14/36 | 0.125 | 57.0 | 8753 |

- **M and A together are the support fix.** Every 1-round stack with both landed at an
  objective of 0.143 to 0.153; nothing without both beat 0.155. With TMA at 3 rounds, Medic
  elimination went 1.00 -> 0.83 (real 0.66), Medic uptime 0.45 -> 0.63 (real 0.77), Medic
  resupplies 27 -> 30 (real 41), Ammo elimination 0.89 -> 0.80 (real 0.45). Neither alone does it:
  T alone did not save the Medic (the Ammo became the preferred target), M alone moved the Medic
  but cost the team more.
- **T helps a little on top** (Medic hit diff and resupplies) and never hurt. **H and C add
  nothing once M and A are on** (THMAC vs TMA differs in one cell); H alone was the only switch
  that made no cell worse.
- **The cost is a quieter game.** Ammo resupplies given 36 -> 30 (real 36), Ammo times hit 26 ->
  18 (real 23), Heavy elimination 0.48 -> 0.35 (real 0.43), nukes 0.78 -> 0.59 (real 2.8).
- **Stacking the constants on all five** gives the best objective (0.125, nukes 1.34, Medic
  uptime passing) but only 14 cells: the ammo resupply constant and the hiding Ammo both cut
  resupplies given, together to 22.6 against a real 36.2. Drop that constant if the two are
  combined.

**Decision (owner, 2026-11-06):** T, H, M and A are the defaults; C stays off. The nuke gate
(#558), Downed stay (#559) and missile (#560) constants are still to be re-run on top of them.

The side-alternation strength test (`test_batch_sim.py::TestSim08SideAlternation`) fell to a coin
flip once target priority changed the draws. Its "strong" team had survival 0 on the reading that
low survival was hard to miss, but the hit roll was `70 + accuracy - survival`, so both sides hit
at 70% (the base is 49 since MECH-23). All three stats at 100 against 1 wins 100% of games with the switches on, so the test's
matchup was corrected and target priority kept.

### Nukes and missiles (2026-11-06, CAL #558, #559, #560)

The three constants the support-survival sweep found helpful, re-run on top of the THMA defaults
(main `2579f221`, Syracuse at 20 px / 1 ft, Test set, `--rounds 3 --workers 12 --seed 42`, 128
games). The driver mutates one constant in the working tree, runs `lfstats_calibrate --split test`
and restores the file; the baseline is the unmodified tree.

| Run (3 rounds) | Cells | Objective | Winner % | Margin error | Nukes | Cmd missiles | Heavy missiles |
|---|---|---|---|---|---|---|---|
| baseline (THMA) | 14/36 | 0.150 | 59.4 | 8458 | 0.55 | 1.03 | 0.95 |
| nuke gate 20 SP (#558) | 13/36 | 0.140 | 54.7 | 8061 | 1.43 | 1.00 | 0.98 |
| Downed Stay hidden 40 (#559) | 13/36 | 0.150 | 57.8 | 8873 | 0.50 | 0.98 | 0.94 |
| missile bump 30 (#560) | 13/36 | 0.153 | 54.7 | 8772 | 0.50 | 0.94 | 0.79 |
| all three | 13/36 | 0.137 | 53.1 | 8423 | 1.43 | 0.94 | 0.75 |
| **shipped: gate 20 SP + hide while arming + MECH-16** (8 workers) | 14/36 | 0.129 | 55.5 | 8420 | 1.49 | 1.26 | 1.17 |

Real: nukes 2.79, Commander missiles 2.47, Heavy missiles 1.76. The shipped row is the best
objective measured on the Test set so far; against the THMA baseline it is better on nukes, Heavy
missiles, Scout rapid fire and Medic times hit and worse on no cell. Most of its missile gain is
MECH-16 (below); on the full set the hide adds little to detonations over the gate alone (1.43 ->
1.49), less than the 12-round counter promised, so the cancel rate on the full set is still to be
counted (the parallel counter run was cut short by the machine running out of memory).

- **#558 holds.** The gate alone is the one constant that moves the objective (0.150 -> 0.140):
  better on nukes detonated, Scout rapid fire and Medic times hit; worse on Ammo elimination
  (0.81 -> 0.87) and Ammo resupplies given (29 -> 25), which is what more nukes do to supports.
  The lost cell is noise-level (every per-cell move under 3% is).
- **#559 and #560 do not hold on the switches.** Neither moved a cell beyond noise; the missile
  bump made the Heavy's missiles worse (0.95 -> 0.79), as the earlier sweep had warned: rolling
  `missile_player` more does not land more missiles. Both constants stay as they were.

**Where nukes died** (a counter over 12 Test rounds, serial, the engine instrumented at
`_commander_nuke_gate`, `plan_action`, the missile lock and `_complete_missile`; per
Commander-round):

| Engine | Fired | Cancelled by a tag | Detonated | SP unspent at the end |
|---|---|---|---|---|
| as on main | 0.83 | 0.21 | 0.62 | 44.8 |
| nuke gate 20 SP | 2.58 | 1.29 | 1.29 | 11.4 |
| gate 20 + hide while arming | 2.33 | 0.75 | 1.58 | 9.2 |
| gate 20 + walk to cover before arming | 2.25 | 0.96 | 1.29 | 12.8 |
| gate 20 + cover + hide | 2.12 | 0.79 | 1.33 | 9.9 |

- Every Commander in the Test set has 48 to 64 game awareness, so the MECH-03 tiers made them
  wait for 40 or 60 SP: the gate was open on 48 of 29,586 ticks a Commander held 20+ SP, and a
  Commander ended a round holding 45 SP (two nukes' worth). A Commander earns about 55 SP a round
  (49 tags, 2 a missile, 5 a capture), which at 20 SP a nuke is 2.8 nukes: the real figure. So the
  SP economy is right and the gate was the sink.
- Once the gate opens, half the nukes fired are cancelled by a tag during the 8 to 14 tick fuse:
  the Commander keeps shooting (and drawing reactions) while arming, and the MECH-06 activation
  broadcast sends every enemy toward its cell. Owner's direction: find a hidden spot or hide behind
  teammates while nuking. Hiding for the whole fuse (`nuke_arming.HIDE_WHILE_ARMING`: Stationary,
  missed 50%, passed over as a target, no missile lock; MECH-23 later removed all three) cut the cancels 1.29 -> 0.75 and raised
  detonations to 1.58 at a cost of 7 Commander tags. Walking to a low-sight-line cell near a
  teammate first fired fewer nukes and cancelled no fewer, alone or with the hide, so it was not
  kept. The remaining cancels are the enemy rush; the broadcast itself was not changed.

**Where missiles died** (same counter, per round, both players of the role; Commander / Heavy).
This is the pre-MECH-21 engine: the lock is now `LOCK_TICKS` = 2 ticks, nothing is spent until
launch, and every lock that ends without one writes a `lock_cancelled` row
([ADR-0050](adr/0050-a-lock-is-not-a-launch.md)).

| Stage | Commander | Heavy |
|---|---|---|
| `missile_player` rolls that planned a lock | 33.0 | 35.0 |
| ... of which from a Downed player (plan made, lock refused) | 18.6 | 20.3 |
| ... refused because the target was hiding (a gate MECH-23 removed) | 6.5 | 6.7 |
| locks started | 7.9 | 8.1 |
| lost line of sight during the 3 ticks and failed the `special_usage` save | 2.7 | 2.8 |
| completed the lock | 5.3 | 5.1 |
| dodged at impact (survival roll) | 0.5 | 0.6 |
| dropped at impact: attacker Downed during the lock | 1.3 | 1.3 |
| dropped at impact: defender Downed during the lock (not Taggable) | 1.3 | 1.3 |
| landed | 2.2 | 1.8 |

- **MECH-16 was real and is fixed:** a hit spent the missile a second time (`_complete_missile`
  after `start_missile_lock`), so five missiles landed at most three and `final_missiles` went to
  -1. Removing it: locks 7.9 -> 8.9 / 8.1 -> 9.3 a round, landed 1.08 -> 1.25 (Commander) and
  0.92 -> 1.17 (Heavy) a player-round. The playback mirror dropped its matching -1.
- **A third of completed locks vanish at impact** with no event and no counter: `_complete_missile`
  returns when the attacker is no longer Active or the defender no longer Taggable (either was
  Downed during the three ticks). The missile log shows a `locking` row with no resolution. Not
  changed here; it is the next missile sink, with the Downed player's missile rolls (a Downed
  Commander still rolls `missile_player` 18 times a round for nothing).

### Real nukes, from the TDF event logs (2026-11-06)

LFstats serves each game's raw Laserforce TDF event log (`tdf_url` on `lfstats_game_detail`,
`https://lfstats-modern-archive.s3.us-west-1.amazonaws.com/<slug>.tdf`; UTF-16, tab-separated;
event codes 0205 zap without a Down, 0206 zap with a Down, 0404 activates nuke, 0405 detonates
nuke; there is no cancel event, a cancel is an activation with no detonation before the player's
next one). **The sm5-stats row (type 7) is each player's ground truth** (shotsHit, shotsFired,
timesZapped, missileHits, nukesActivated, nukesDetonated, nukeCancels, livesLeft and more; the
`;7/sm5-stats` header row names the columns): derive from the events only what it lacks, and
check each derivation against it. CAL-02's first Elimination rule, read off the state rows, missed
556 of the 917 Eliminations that `livesLeft` shows. All 165 Internationals 2026 games were parsed
(the analysis script lived in the session scratchpad; the figures below are the record). The sim column is the shipped branch over the 128
usable Test games, one round each, both Commanders per round.

| Per game, both teams | Real (TDF) | Sim (branch) |
|---|---|---|
| nukes activated | 5.93 | 4.56 |
| nukes detonated | 5.61 | 2.97 |
| nukes cancelled | 0.32 (5%) | 1.59 (35%) |
| cancelled by a tag: heavy / commander / scout / ammo / medic | 23 / 12 / 8 / 3 / 0 of 46 | 75 / 34 / 13 / 1 / 0 of 123 |
| cancelled by something other than an enemy tag | 6 of 53 (all enemy detonations) | 80 of 203 (see the correction below: the Commander's own supports, not enemy nukes) |
| fuse | 3.5 s (two thirds), 6.0 s (one third) | 4 to 7 s (8 to 14 ticks, uniform) |
| detonations where the Commander took any hit during the fuse | 20 of 925 (2%) | 190 hits on arming Commanders a round of 128 |
| what cancels | a Down (28 of 46 first zaps were Downs; 15 of the 18 shield hits had a Down inside the window; 3 ambiguous) | a Down |

- **Real Commanders arm untouched.** Only 5% of nukes are cancelled and 2% of detonations took
  any hit on the way: Commanders fire from where nobody can shoot them, and the fuse is 3.5 s.
  The sim Commander, even hiding, is shot at 3.3 times and hit 1.5 times a round while arming. The
  cancel rule itself matches (a Down, in both); the exposure does not.
- **Who cancels is the same shape in both:** 3-hit roles (Heavy, Commander) make three quarters of
  the tag cancels in the arena (35 of 46) and nine tenths in the sim (109 of 123). The Heavy is
  the main canceller in both. In the sim this is because a Heavy's shot power 3 Downs a Commander in
  one hit, so it is the one role that can cancel inside the fuse without a run of tags.
- **The sim Commander was tracked.** The MECH-06 activation broadcast wrote the arming Commander's
  cell into every enemy's memory (every one of the 123 tag cancels came from an enemy who held it),
  half the alive enemies are flagged `reacting_to_nuke` on a given arming tick (100.8 of 190.9
  enemy-ticks), and MECH-04's goal override walks a reacting enemy to the enemy Commander's cell to
  tag-cancel. Half the tag cancels (63 of 123) came from a reacting enemy. That broadcast was
  removed on 2026-11-07 (next subsection). The arena has a
  nuke-activation sound but no position; cancels there are opportunistic.
- **The sim Commander's own supports cancel it** (corrected 2026-11-07; the first write-up blamed
  overlapping nukes, which was "every cancel that was not an enemy tag", never measured). With
  every Down and pack reset attributed on the no-broadcast tree (128 rounds): 86 cancels by an
  enemy tag, 80 by the Commander's own Medic or Ammo resupplying it during the fuse (a Resupply
  resets the pack and ends the Nuke, `combat._reset_resupplied`), 7 by an enemy detonation, 1 by a
  missile. The TDF logs show the same rule and a team that knows it: 4 of 978 real activations were
  resupplied inside the fuse, all 4 cancelled; an enemy detonation ended 6. Enemy activations
  inside the same two ticks are 1% of nukes in both the arena and the sim, so overlap is not the
  sim's problem.

**A 1-hit role tagging a 3-hit role down** (a comparison point the owner asked for: a Commander or
Heavy has 3 shields, a Scout, Ammo or Medic 1, so a 1-hit player needs three tags to Down a 3-hit
one). For every Down of a 3-hit player, the zaps on them since their previous Down were read; a
Down is "by one 1-hit player's three tags" when all of them came from that player, and "without
tagging back" when the 3-hit player never zapped that player in between.

| Per game, both teams | Real (TDF) | Sim (branch) |
|---|---|---|
| Downs of a 3-hit player | 95.7 | 57.2 |
| ... with the final tag by a 1-hit player | 31.7 (33%) | 12.1 (21%) |
| ... by one 1-hit player's three tags | 9.3 (10%) | 5.5 (10%) |
| ... of those, without the 3-hit player tagging back | 7.1 (76%) | 4.2 (77%) |
| ... shared between several taggers | 49.9 (52%) | 19.9 (35%) |

The shape of the duel matches (three quarters of such runs go unanswered in both), the volume does
not: the sim has 60% of the real Downs of Commanders and Heavies, and its 1-hit roles finish far
fewer of them (21% against 33%), which is the Scout / Ammo / Medic times-hit and hit-diff gap seen
from the other side.

Two mechanics follow from this. The activation broadcast was removed the next day (below). The
fuse (7 ticks, with a third at 12, against the uniform 8 to 14) was not changed on the branch.

### The activation shares no cell (2026-11-07)

The owner's call after the comparison above: the broadcast should only tell the enemy team that
an opposing nuke is going off, not where the Commander is. `_apply_nuke_activation_broadcast`
(round_loop.py, called at `use_special` in the tick loop) is gone; its unit tests became
`test_player_memory.py::TestNukeActivationSharesNoCell`. What remains at activation is the
MECH-04 `reacting_to_nuke` flag, which already carries "a nuke is going off" to the enemy team.
A reacting enemy still walks to the Commander's cell, but only when their memory holds it from a
sighting or a teammate's call-out (`pathfinding._find_enemy_commander_in_memory`), the way a real
player would have to know.

Same instrumented counter and the same 128 Test games, one round each, before and after:

| Per round, both Commanders | Cell broadcast (branch, 2026-11-06) | No cell broadcast (2026-11-07) |
|---|---|---|
| nukes activated | 4.56 | 4.44 |
| nukes detonated | 2.97 | 3.11 |
| nukes cancelled | 1.59 (35%) | 1.36 (31%) |
| ... by a tag | 0.96 | 0.67 |
| ... not by an enemy tag (the own-team resupply, see "The Commander's own supports" below) | 0.63 | 0.69 |
| tag cancels by heavy / commander / scout / ammo | 75 / 34 / 13 / 1 | 48 / 24 / 13 / 1 |
| tag cancels by a reacting enemy | 63 of 123 | 37 of 86 |
| shots at the arming Commander | 3.27 | 2.16 |
| hits on the arming Commander | 1.48 | 1.02 |
| Commander times tagged, a player-round | 31.9 | 28.4 |

Tag cancels fell by 30% and the Heavy's and Commander's share of them fell most (75 to 48, 34 to
24); the Scout's did not move (13), so the Scouts who cancel were already finding the Commander
by sight. Every remaining tag cancel still comes from an enemy whose memory held the Commander's
cell, which is now just the shot itself: a tag writes the target into the attacker's memory. The
cancels that are not enemy tags did not move and are now half of all cancels; they turned out to
be the Commander's own supports resupplying it mid-fuse (next subsection), not enemy nukes.

Calibration (`lfstats_calibrate --split test --rounds 3 --seed 42`): 14/36 cells either way,
objective 0.129 -> 0.134 (the Medic accuracy and MVP cells moved, within the run-to-run noise
seen across the sweep), Commander `avg_nukes_detonated` 1.49 -> 1.56, winner agreement 55.5%
both. Adopted as a realism decision, not a calibration win.

### When Commanders fire, and the Commander's own supports (2026-11-07)

The owner: "in real games commanders almost never drop immediately on 20; check when commanders
drop in real games". Each real Commander's SP was rebuilt from its TDF events (+1 per zap on an
opponent, +2 per missile, +5 per base destroyed, -20 per activation; the ledger dips a few SP
below zero for a fifth of Commanders, so one small SP source is missing, and the shape below does
not depend on it). The player-state rows (type 9: 3 down, 2 coming back, 0 active; a Down lasts
8 s) give the Commander's own Down timing. 978 activations in 330 Commander-games; the sim column
is the no-broadcast tree over the 128 Test games (569 activations), instrumented at the tick the
fuse starts.

| At activation | Real (TDF) | Sim |
|---|---|---|
| SP 20 / 21 / 22-24 / 25-29 / 30-39 / 40+ | 31% / 13% / 26% / 14% / 9% / 7% (mean 24.3, median 22) | 0% / 74% / 23% / 1% / 0% / 0% (the gate was `sp > 20`) |
| wait since reaching 20 SP: 0-2 s / 2-5 / 5-15 / 15-30 / 30-60 / 60-120 / 120+ | 3% / 10% / 23% / 21% / 16% / 13% / 13% (median 23 s) | 19% / 19% / 34% / 18% / 6% / 2% / 2% |
| time since the Commander's own Down began: 5-10 s / 10-20 / 20-40 / 40+ | 41% / 39% / 17% / 4% | 22% / 34% / 27% / 17% |
| an enemy Commander or Heavy down at that moment | 70% (base rate at any moment of a game: 60%) | not measured |
| first activation of a team | median 219 s (p25 164, p75 285) | mean 103 s |
| nukes a Commander-game | 0: 1%, 1: 8%, 2: 23%, 3: 32%, 4: 33%, 5: 2% | 2.22 fired, 1.55 detonated |
| an enemy activation inside the same two ticks | 1% | 1% |
| resupplied by own team inside the fuse | 4 of 978, all 4 cancelled | 80 cancels of 173 |

Real Commanders bank a little (a third fire at exactly 20, a third at 22 or more), wait a median
23 s after reaching 20, and above all fire right after coming back from a Down: four fifths of
real nukes start within about 12 s of reactivation. The "an enemy 3-hit is down" habit the owner
sees in one teammate is only 10 points above the base rate across the field.

Prototypes on the counter (128 Test rounds, one each; per round, both Commanders):

| Variant | fired | cancelled | detonated | SP left a Commander |
|---|---|---|---|---|
| no cell broadcast (current tree) | 4.44 | 1.36 (31%) | 3.11 | 10.1 |
| + supports never resupply an arming Commander (`is_resupplyable_at` False while arming) | 4.32 | 0.80 (19%) | 3.52 | 10.4 |
| + per-nuke SP bank drawn from the real table, gate `>=` | 3.90 | 0.86 (22%) | 3.04 | 15.7 |
| + fire only in the 10 s after coming back from a Down (hard gate) | 3.68 | 0.78 (21%) | 2.90 | 18.1 |
| SP bank draw alone | 4.00 | 1.24 (31%) | 2.76 | 15.7 |
| 20 cells from every remembered enemy (`MIN_CELLS_FROM_KNOWN_ENEMY`) | 3.98 | 1.14 (29%) | 2.82 | 16.7 |
| 10 cells | 4.46 | 1.24 (28%) | 3.12 | 10.5 |
| an enemy 3-hit down (`REQUIRE_ENEMY_3HIT_DOWN`) | 4.36 | 1.20 (28%) | 3.14 | 10.7 |
| 20 cells + 3-hit down | 3.54 | 1.02 (29%) | 2.52 | 21.9 |
| memory staleness halved (120 / 30 -> 60 / 15 ticks) | 4.42 | 1.38 (31%) | 3.04 | 10.2 |
| per-tick 3% chance once eligible | 4.14 | 1.18 (29%) | 2.96 | 13.6 |

Calibration (3 rounds, Test set) for the four switch variants: memory halved 15/36 obj 0.148,
20 cells 15/36 obj 0.137, 3-hit down 14/36 obj 0.132, both 14/36 obj 0.140 (current tree 14/36
obj 0.134); none moved the Commander nuke cell, and the 20-cell gate was closed on 97% of the
ticks a Commander could have armed on Syracuse. The two gates ship as switches, off.

What the numbers say: the supports are the sim's biggest cancel source and a pure behaviour gap
(the real rule is the same, real teams avoid it), and the SP bank draw reproduces the real
SP-at-activation shape and pushes the first nuke later (103 -> 123 s) at the cost of some unspent
SP. The real "fire after coming back from a Down" signal is strong, but as a hard gate it leaves
SP unspent and does not lower the cancel rate, because the sim Commander comes back next to the
enemies who Downed it; a soft version (a weight boost in that window) is the candidate to measure
next. The sim fires fewer nukes than the arena mostly through SP income (about 54 SP a Commander
round against 67 in the arena): the Commander tags and is tagged far less than real.

Also seen: a missile Down of an arming Commander called `record_down` but did not clear
`special_active_until`, so that nuke still detonated (1 to 3 a round across the runs); the shot
path cleared it. Real rule: any Down cancels. Fixed below.

### The four rules shipped (2026-11-07)

The owner: "go ahead with all 4 proposed", then "there should be some chance to resup during nuke
arming, like 1-2%, based off real games". All four live in `sim_helpers/nuke_arming.py`
(`docs/agents/matches/sim-helpers.md` has the switch table):

1. **No support resupplies an arming Commander** (`NO_RESUPPLY_WHILE_ARMING`): an arming
   Commander is not `is_resupplyable_at`, so the target choice passes it over and
   `attempt_resupply` gives it nothing. Each nuke rolls once, when the fuse starts, whether its
   supports may still resupply it (`RESUPPLY_WHILE_ARMING_CHANCE`, the owner's slip). The roll
   is per nuke, not per attempt: the supports try about five times a nuke (21 refusals a round).
2. **A per-nuke SP bank** (`SP_BANK_DRAW`): each nuke draws its threshold, 20 plus an entry of a
   102-entry table with the real SP-at-activation shape, when the Commander first holds 20 SP,
   and fires at or above it; the gate floor moved from `sp > 20` to `sp >= 20`.
3. **An after-Down boost** (`AFTER_DOWN_BOOST` 2.0 inside the 20 ticks after reactivation):
   the open `use_special` weight is doubled; it never opens a closed gate.
4. **Every Down ends the nuke**: `record_down` zeroes an arming Commander's `special_active_until`,
   so a missile Down cancels as a tag does.

Counter, 128 Test rounds each (per round, both Commanders):

| Tree | fired | cancelled | detonated | SP left | SP at activation 20 / 21 / 22-24 / 25-29 / 30-39 / 40+ | own Down began 5-10 s / 10-20 / 20-40 / 40+ before |
|---|---|---|---|---|---|---|
| before (no cell broadcast) | 4.44 | 1.36 (31%) | 3.11 | 10.1 | 0 / 74 / 23 / 1 / 0 / 0% | 22 / 34 / 27 / 17% |
| rules 1, 2 and 4, boost off | 3.88 | 0.77 (20%) | 3.10 | 16.1 | 26 / 15 / 29 / 17 / 9 / 4% | 24 / 35 / 23 / 18% |
| + boost 2.0 | 3.97 | 0.83 (21%) | 3.14 | 14.4 | 27 / 14 / 26 / 17 / 8 / 4% | 28 / 31 / 22 / 19% |
| + boost 3.0 | 4.01 | 0.85 (21%) | 3.16 | 14.7 | | 27 / 31 / 21 / 20% |
| + boost 2.0, slip flag 1.5% of nukes | 3.91 | 0.80 (20%) | 3.11 | 15.1 | 28 / 15 / 27 / 17 / 9 / 3% | 26 / 33 / 25 / 17% |
| shipped: boost 2.0, slip flag 10% of nukes | 3.93 | 0.87 (22%) | 3.06 | 15.1 | | |
| real (TDF) | 5.93 a game | 0.32 (5%) | 5.61 | | 31 / 13 / 26 / 14 / 9 / 7% | 41 / 39 / 17 / 4% |

The slip: a flagged nuke is reached by a Resupply about one time in ten (a support has to be
beside the Commander inside the 8 to 14 tick fuse), so the 1.5% flag ended no nuke in 500 and the
10% flag ended 6 of 503 (1.2% of nukes, the owner's 1-2%; the arena's 0.4% would be a flag of
about 3%). Of the shipped tree's 0.87 cancels a round, 0.72 are enemy tags (Heavy 0.40, Commander
0.23, Scout 0.05, Ammo 0.01 in the 1.5% run), 0.08 the enemy nuke detonating first, 0.02 a
missile Down (rule 4; these detonated before) and 0.05 the Commander's own supports (rule 1; 0.62
a round before). The SP
shape now matches the arena. The after-Down share moved a little (24% -> 28% at 2.0; 3.0 gave the
same, so 2.0 ships); the real 41% is out of reach by weight alone because the sim Commander comes
back from a Down without the SP for a nuke more often than the real one, which is the SP income
gap (CAL-02, shipped 2026-11-08: "Tags and times tagged" below; its tree lifts detonations to
1.76 a Commander-scorecard). Fewer nukes are fired than before (4.44 -> 3.91) because the bank
holds some SP back, and the same number detonate.

Calibration (3 rounds, Test set): 14/36 cells, objective 0.135 (no-broadcast tree 0.134), winner
agreement 57.0% (55.5%), margin error 7,950 (8,524); Commander nukes detonated 1.56 -> 1.59 a
scorecard (real 2.79); Ammo times hit and resupplies given a little worse (the supports resupply
the Commander less).

### Tags and times tagged (CAL-02, 2026-11-08)

The real side of CAL-02 (spec #563, D1 / D8), committed as `data/tdf_role_targets_2026.json`
and built by `python -I scripts/tdf_role_stats.py <logs dir> --out data/tdf_role_targets_2026.json
[--fetch] [--test-file data/lfstats_test_internationals_2026.json]` from the 165 Internationals
2026 TDF event logs (164 used; one game had 13 players and is skipped). Every figure is a mean per
player-game over 328 player-games a role (656 Scouts), with its standard error in the file.

**What a Tag is not.** A Tag is a zap on an enemy (TDF event codes 0205 without a Down, 0206 with
one, with an enemy target). LFstats `shotsHit` counts Resupplies and Base zaps as well, so it is
not a tag count: the Ammo's 64.7 and the Medic's 54.6 `shotsHit` a game are 22.3 and 8.6 Tags. A
zap on a teammate is a shot, not a Tag (about one a game for a Commander). A Missile hit is not a
time tagged. The grill's "times tagged" column (58.8 / 47.8 / 33.5 / 23.2 / 10.2) was LFstats
`timesZapped`, which also counts a teammate's zaps on the player; the file's `times_tagged` is
enemy Tags only, as the glossary defines it, and carries `timesZapped` beside it as
`times_zapped_lfstats`.

| Role | Tags | Times tagged (enemy; LFstats timesZapped) | Shots fired (at players) | Accuracy on enemies | Downs given / taken | Active share | Tags per Active minute | Eliminated (minute) |
|---|---|---|---|---|---|---|---|---|
| Commander | 54.5 | 57.4 (58.8) | 70.3 (66.7) | 0.83 | 40.9 / 26.5 | 0.49 | 7.9 | 38% (9.7) |
| Heavy | 43.5 | 46.8 (47.8) | 55.4 (52.7) | 0.84 | 43.5 / 20.4 | 0.52 | 6.1 | 43% (9.6) |
| Scout | 36.4 | 32.5 (33.5) | 58.6 (53.7) | 0.70 | 21.5 / 32.5 | 0.47 | 5.5 | 44% (9.9) |
| Ammo | 22.3 | 22.5 (23.2) | 45.8 (41.9) | 0.58 | 12.3 / 22.5 | 0.55 | 3.1 | 44% (9.7) |
| Medic | 8.6 | 9.9 (10.2) | 18.3 (15.0) | 0.58 | 4.7 / 9.9 | 0.58 | 1.2 | 65% (8.6) |

Shots fired are the player's misses (0201), Base zaps (0203) and zaps (0205 / 0206, a teammate's
included); shots at players (`shots_at_players`) leave the Base zaps out, so they are the sim's
Tags plus misses like for like, and the probe's `| real` beside Shots fired reads that key
(CAL-02 review, 2026-11-08). Accuracy on enemies is Tags over Tags plus misses. An Elimination is
ending the game with no lives (`livesLeft` 0 in the sm5-stats row), at the entity-end row's time:
the Elimination Down itself usually writes no player-state row (the rows end at the previous
Down's return, the final zap on the player follows, the entity-end comes about 2 s later), so the
first cut's state-row count found 361 of the 917 Eliminated players and read 30 / 22 / 34 / 32 /
31%. Active share is the time in state 0 of the player-state rows over 15 minutes, stopped at
the Elimination. A team faces 4.98 of 6 enemies on the floor averaged over the game (the sim:
4.0). A game holds 403 Tags, both teams. **Open (2026-11-08):** 110 of the 165 missions ended
before 14:40 (a team wiped out; median mission end 12:26, mean length 79.6% of 15 minutes), while
Active share and enemies on the floor are averaged over the full 15 minutes; whether to average
over the mission's length instead, and how the sim counts a survivor's ticks after a wipe, is
not decided.

**Who tags whom** (Tags per game, both teams; attacker row, target column):

| | Commander | Heavy | Scout | Ammo | Medic | Total |
|---|---|---|---|---|---|---|
| Commander | 28.2 | 22.5 | 34.8 | 15.9 | 7.7 | 109.0 |
| Heavy | 23.0 | 16.6 | 33.1 | 9.9 | 4.4 | 87.0 |
| Scout | 40.1 | 39.4 | 43.8 | 15.8 | 6.6 | 145.7 |
| Ammo | 16.5 | 10.7 | 13.7 | 2.8 | 0.9 | 44.5 |
| Medic | 7.2 | 4.2 | 4.8 | 0.7 | 0.3 | 17.1 |

The who-Downs-whom matrix is in the file: every zap on a 1-hit role (Scout, Ammo, Medic) is a
Down, and the Heavy's shot power 3 Downs anyone in one hit, so its two rows are equal.

**Shot cadence** (gap between one player's consecutive shots; median and the share per bin):

| Role | Median s | <=0.5 | 0.5-1 | 1-2 | 2-5 | 5-10 | 10-30 | 30-60 | 60+ |
|---|---|---|---|---|---|---|---|---|---|
| Commander | 4.5 | 16% | 10% | 10% | 16% | 17% | 25% | 5% | 1% |
| Heavy | 7.7 | 2% | 11% | 11% | 19% | 18% | 31% | 7% | 1% |
| Scout | 3.6 | 27% | 8% | 7% | 12% | 11% | 25% | 7% | 2% |
| Ammo | 3.1 | 32% | 9% | 5% | 9% | 9% | 21% | 10% | 5% |
| Medic | 11.4 | 24% | 7% | 3% | 7% | 7% | 23% | 15% | 14% |

**By the team's own Medic's Elimination minute** (team-games; per player-game Tags / times tagged
for Commander, Heavy, Scout, Ammo, Medic). The Tags follow the Medic: a team whose Medic dies
between 5:00 and 8:00 has the sim's Tags (CAL-03 carries the lives budget).

| Medic out | Team-games | Commander | Heavy | Scout | Ammo | Medic |
|---|---|---|---|---|---|---|
| by 5:00 | 18 | 30.4 / 41.1 | 20.3 / 30.9 | 16.4 / 20.3 | 12.1 / 20.7 | 5.7 / 13.5 |
| 5:00-8:00 | 79 | 42.3 / 52.4 | 34.6 / 44.2 | 25.6 / 28.9 | 19.3 / 24.7 | 7.6 / 13.6 |
| 8:00-12:00 | 87 | 64.5 / 71.9 | 52.4 / 59.4 | 42.0 / 40.3 | 26.7 / 28.9 | 9.6 / 11.7 |
| after 12:00 | 30 | 72.8 / 77.1 | 56.7 / 61.5 | 50.2 / 44.6 | 27.6 / 29.0 | 10.3 / 10.3 |
| survives | 114 | 54.3 / 47.3 | 43.0 / 37.4 | 39.2 / 27.9 | 21.2 / 14.7 | 8.5 / 5.3 |

The sim side is one command (#567): `python manage.py round_probe --lineups test --workers 8`
plays the 128 used Test games' real lineups (one round each, seed 42, about 12 minutes) and
prints every measure above beside this file's real value (`| real`), with the who-tags-whom and
who-Downs-whom matrices and the shot cadence per role; see the command's section in
`laserforce_simulator/matches/management/commands/CLAUDE.md`. The baseline, the prototype
ladder and the shipped tree, read off it, follow.

**The sim side** (128 Test games, one round each, seed 42; per player-round, Commander / Heavy /
Scout / Ammo / Medic). The baseline is `origin/main` at 5d0821a7 on the grill's counter, the
ancestor of the probe mode; the shipped tree is the integration branch on `round_probe --lineups
test` (every standard error under 1):

| Tree | Tags made (real 54.5 / 43.5 / 36.4 / 22.3 / 8.6) | Times tagged (real 57.4 / 46.8 / 32.5 / 22.5 / 9.9) | Tags a round, both teams (real 403) |
|---|---|---|---|
| baseline | 42.0 / 35.7 / 26.1 / 11.3 / 2.1 | 28.9 / 32.6 / 25.7 / 14.9 / 15.5 | 287 |
| bugs A and B (P1) | 43.5 / 33.8 / 26.1 / 12.1 / 2.3 | 28.7 / 34.5 / 25.5 / 14.4 / 15.4 | 288 |
| + the defender learns the attacker (P2) | 44.2 / 34.2 / 27.4 / 13.6 / 2.8 | 31.3 / 35.7 / 26.0 / 15.2 / 15.3 | 299 |
| + the Attack table (P3) | 45.1 / 35.3 / 26.7 / 13.2 / 2.7 | 30.1 / 36.8 / 26.4 / 14.9 / 15.0 | 299 |
| + Medic tag weight 15 (P4, the prototype of the shipped tree) | 44.2 / 34.7 / 27.1 / 13.3 / 4.9 | 30.9 / 37.1 / 26.0 / 15.3 / 15.8 | 302 |
| **shipped** (`round_probe --lineups test`) | 44.2 / 34.7 / 27.1 / 13.3 / 4.9 | 30.9 / 37.1 / 26.0 / 15.3 / 15.8 | 303 |

The shipped tree also prints, per round: Reaction shots Commander 2.4, Heavy 1.0, Scout 0.8, Ammo
0.7, Medic 0.3 (0 for every role but the rapid-fire Scout before bug A); Follow-ups Commander 3.2,
Scout 3.5, Ammo 1.5, Medic 0.5, Heavy 0 (a Heavy's Hit always Downs); Downs given 33.2 / 34.7 /
16.8 / 8.6 / 3.2 against the real 40.9 / 43.5 / 21.5 / 12.3 / 4.7; enemies on the floor per Active
roll 4.4 for the Commander and Heavy, 4.6 for the Scout, against 5.0 in the arena. Who tags whom,
both teams a round: Scouts on Commanders 22.9 (real 40.1), on Heavies 31.8 (39.4); Commanders on
the Medic 12.1 (7.7). Measured and not shipped (PLAN.md, CAL-02, has the rows): Control Mode at a
quarter of resources (the owner ruled Modes must follow goals, MECH-19), the Scout tag weight 60
(zero-sum), the attackers' hide bumps halved (noise), and Syracuse's coloured wall segments as low
walls (they are not low walls).

**Acceptance (spec #563, D9), read off the shipped tree:** every role's Tags made and times
tagged at or above the P4 value less two standard errors; total Tags 303 against the floor of
295; the Medic's Tags 4.9 against 4.2; Reactions Commander 2.4 and Heavy 1.0 against 1.5 and 0.7.
Calibrate (`lfstats_calibrate --split test --rounds 3 --seed 42`): 15/36 cells (14 before),
winner agreement 64.1% (57.0%), mean margin error 7,309 (7,950); Commander `avg_nukes_detonated`
1.76 a scorecard (1.59 before; real 2.79), so about 3.5 nukes a round against the CAL #558 tree's
3.1, up with the SP income; fired 3.98 a round (3.93). On the default-stat rosters (`round_probe`,
seeds 1-12) against the MECH-17 baseline: Still % Commander 31.7 +/- 3.2 (was 27.2 +/- 2.0),
Heavy 38.8 +/- 4.2 (36.2 +/- 3.6), Scout 26.7 +/- 4.1 (25.4 +/- 3.6); squares to target Commander
38.0 +/- 1.6 (39.6 +/- 1.6), Heavy 40.8 +/- 2.1 (41.0 +/- 2.7), Scout 36.3 +/- 1.0 (39.7 +/- 1.1,
closer, the direction MECH-17 wanted), Ammo 38.8 +/- 2.2 (35.5 +/- 1.6), Medic 31.8 +/- 1.9
(34.8 +/- 2.1); read with both standard errors only the Scout's squares is a real move. The
Scout's planned Shots 41.2 +/- 2.9 (floor 40; 45.6 +/- 6.2 on seeds 1-4). The Medic's Still fell
from 48.7 to 31.3: it now shoots, and a tag roll leaves a hide.

### SM5-02: a Downed pack is dark (2026-11-03)

SM5-02 (spec #531) stopped a player who is not Active from firing or resupplying, and stopped
Shots at a player who is not Taggable. Test split, default constants, before (`origin/main`)
and after, both `--split test --rounds 20 --seed 42 --workers 4`:

```
python laserforce_simulator/manage.py lfstats_calibrate --split test --rounds 20 --seed 42 --workers 4
```

| Position | Metric | Before | After | Real | Pass before / after |
|---|---|---|---|---|---|
| commander | times hit | 60.1 | 56.7 | 59.1 | PASS / PASS |
| commander | MVP | 14.31 | 13.52 | 18.74 | FAIL / FAIL |
| commander | nukes detonated | 0.51 | 0.65 | 2.79 | FAIL / FAIL |
| heavy | hit diff | 1.684 | 1.908 | 0.969 | FAIL / FAIL |
| heavy | times hit | 50.4 | 46.4 | 47.8 | PASS / PASS |
| heavy | MVP | 19.76 | 20.75 | 15.64 | FAIL / FAIL |
| scout | times hit | 39.4 | 36.7 | 33.8 | FAIL / **PASS** |
| scout | MVP | 12.36 | 11.56 | 13.84 | PASS / **FAIL** |
| scout | rapid fire | 2.18 | 1.92 | 1.57 | FAIL / **PASS** |
| ammo | uptime | 0.272 | 0.300 | 0.610 | FAIL / FAIL |
| ammo | resupplies given | 70.1 | 72.5 | 36.2 | FAIL / FAIL |
| medic | accuracy | 0.789 | 0.753 | 0.902 | PASS / **FAIL** |
| medic | hit diff | 0.455 | 0.200 | 1.062 | FAIL / FAIL |
| medic | MVP | 12.96 | 10.63 | 15.95 | FAIL / FAIL |
| medic | uptime | 0.388 | 0.437 | 0.765 | FAIL / FAIL |
| medic | resupplies given | 55.6 | 58.6 | 40.8 | FAIL / FAIL |

- **Cells passed:** 11 / 36 before and after (two cells flipped each way).
- **Match level:** winner agreement 58.6% -> 57.8%, mean margin error 8,206 -> 8,433.
- **What moved:** times hit fell at every position except the medic (no Shots in the
  Not-targetable window), and support uptime rose. The medic lost most of its tags (they came
  from shooting while Downed), so its hit diff and MVP fell further from the real figures.
  Resupplies given rose slightly even though Downed supports now give none; the cause was not
  measured (supports are Active for more of the round). No constant was retuned; this shift is
  CAL-01's starting point.
- **Re-run after review (2026-11-04).** The review fix that counts a negative pooled weight as 0
  gave byte-identical output: no Ammo or Medic reaches the drain with a negative slot here.

## Findings to follow up

Found while tracing how the sim uses each stat. Not yet acted on.

- Six stats are never read by the simulation, only by `overall_rating`, which drives salary,
  seeding, rankings and development: `positioning`, `flexibility`, `adaptability`,
  `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`.
- `resource_awareness` has no gameplay effect: `_observe_lives` (`matches/simulation/round_loop.py`)
  records enemy lives in memory, but nothing reads them back.
- The `player_state.py` comment says `resupply_efficiency` "doubles speed for Medic/Ammo".
  This is not implemented.
