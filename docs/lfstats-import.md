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
  - **Map:** `data/maps/syracuse.json` is the Syracuse Laser Tag configuration the Test set replays on. It uses 30px cells (auto-detected walls, 2,001 floor cells of 3,200) and red, blue and one neutral base at the image's base markers, as approved by the owner. Export a configured map with `python scripts/lfstats_import/export_map_fixture.py "<map name>" <zone size> <out.json>`.
  - **Baselines:** the snapshot's, not subtracted. Internationals is about 4% of the window's scorecards.
  - **Tests:** `laserforce_simulator/test_lfstats_calibration_data.py`.
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
| accuracy | Hit chance = `70 + accuracy - defender.survival` (clamped 10-95), `matches/sim_helpers/shot.py` | `avg_accuracy`, all positions combined | `round(avg_accuracy * 100)` | Decided |
| survival | Subtracted from attacker hit chance, `matches/sim_helpers/shot.py`; missile dodge `min(20, survival/5)`%, `matches/simulation/entrypoints.py` | `avg_times_hit` per position + `elimination_rate` | Mean of a times-hit part and a non-elimination part (see below) | Decided |
| player_awareness | As the target: return-fire chance, blocks attacker follow-up chains (`shot.py`); nuke reaction `(ga + pa)/200` (`round_loop.py`) | `avg_hit_diff` + `avg_nukes_canceled` per position | Hit-diff part plus a nukes-canceled bonus of up to +10 (see below) | Decided |
| game_awareness | Below 35, sometimes shoots enemies who are already down (`mechanics.py`); commander nuke SP banking tiers 30/50/70 (`weights.py` `_commander_nuke_gate`); half of the nuke-reaction roll | `avg_mvp` per position | `50 + 25 * log2(player_mvp / position_avg_mvp)`, weighted by games (see below) | Decided |
| resource_awareness | None today: `_observe_lives` (`round_loop.py`) records enemy lives, but nothing reads them back | Per-game event timeline (TDF) | `50` for now (see below) | Deferred |
| decision_making | Sharpens action choice: top weight x`(1 + dm/100)`, the rest divided by it (`weights.py`); medic/ammo `_stress_fail` (`resupply_queue.py`; higher lowers the fail chance, SM5-01) | `win_rate`, `avg_penalties`, `avg_shots_hit` per position | Mean of a results part and an activity part (see below) | Decided (revised after the Kodama talk) |
| stamina | Fatigue at each 10% of the round when stamina < checkpoint: -5% hit chance, -10% movement per penalty (`weights.py`, `player_state.py`) | Late- vs early-game performance (TDF) | `50` for now | Deferred |
| speed | Cells per tick `max(1, ceil(speed/100 * max_movement))` (`pathfinding.py`) | None (no position data, even in TDF) | `50` for now (see below) | Deferred |
| positioning | None (only feeds `overall_rating`) | `avg_uptime_pct` + `avg_hit_diff` per position | Mean of an uptime part and a hit-diff part (see below) | Decided |
| adaptability | None (only feeds `overall_rating`) | `avg_mvp` per position, `team_result: loss` scope | `50 + 25 * log2(player_mvp_in_losses / position_avg_mvp_in_losses)`, weighted by games | Decided |
| communication | Each tick and after a tag, with probability `comm/100`, shares enemy memory with allies within half the map diagonal; allies pathfind to known enemies (`round_loop.py`, `pathfinding.py`). Not the nuke call-out: every enemy learns the Commander's cell the tick a nuke starts, with no reaction delay (SM5-07) | `avg_assists` per position (team uplift later) | `50 + 25 * log2(player_assists / position_avg_assists)`, weighted by games (see below) | Decided |
| teamwork | Above 50, with probability `tw/100`, moves to a high-sight cell an ally can see (`pathfinding.py`); medic/ammo `_stress_fail` (higher lowers the fail chance, SM5-01) | `avg_resupplies_given` (ammo/medic), `avg_assists` (others), per position (team uplift later) | Log ratio vs position average, weighted by games (see below) | Decided |
| Offensive_synergy | None (only feeds `overall_rating`) | `avg_medic_hits` + `avg_eliminations` per position | Mean of two log-ratio parts vs position average (see below) | Decided |
| defensive_synergy | None (only feeds `overall_rating`) | `avg_mvp` at ammo, medic and heavy | MVP log ratio vs position average over those games (see below) | Decided |
| midfield_synergy | None (only feeds `overall_rating`) | `avg_mvp` at heavy and scout | MVP log ratio vs position average over those games (see below) | Decided |
| resupply_synergy | Medic/ammo `resupply_ally` weight x`rs/50` (`weights.py`); combo resupply chance `min(0.95, 0.20 + rsA*rsM/1e4 + reA*reM/1e4)` (`resupply_queue.py`) | `avg_resupplies_given` at ammo/medic | Log ratio vs position average over ammo/medic games; others count 50 (see below) | Decided |
| resupply_efficiency | All roles: `request_resupply` weight `re/2` when below max (`weights.py`); part of the combo chance | `avg_uptime_pct` per position | `50 + 100 * log2(player_uptime / position_avg_uptime)`, weighted by games | Decided |
| special_usage | Medic/ammo team boost weight, scout rapid-fire weight, commander nuke weight (`weights.py`); missile lock survives lost line of sight with probability `su/100` (`combat.py`) | `avg_nukes_detonated`, `avg_rapid_fire`, `avg_missiles_hit`, `avg_resupplies_given` | Per-position special log ratio vs position average, weighted by games (see below) | Decided |
| flexibility | None (only feeds `overall_rating`) | Community opinion (later); candidate: `times_hit_by_missile` per game (Kodama: yoga, contorting into angles, dodging missiles) | `50` for now | Deferred |

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

- **Spawns cut off from the floor.** On Syracuse at 30px the corridor by red's base becomes
  two pockets that do not connect to the main floor, and 9 of red's 11 spawn cells were in
  them. Four red players never moved or engaged, which made every earlier run on the map
  invalid. `map_loader.reachable_spawn_pools` now moves such cells to the nearest main-floor
  cell to the base. This also fixes app games on that map.

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
  follow from being hit about twice as often.
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
