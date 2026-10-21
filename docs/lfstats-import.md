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
stat today. At run time, `Player.stat_for_simulation` multiplies the value by 1.2 (capped at
100) in a preferred role.

| Stat | Sim effect | LFstats source | Formula | Status |
|---|---|---|---|---|
| accuracy | Hit chance = `70 + accuracy - defender.survival` (clamped 10-95), `matches/sim_helpers/shot.py` | `avg_accuracy`, all positions combined | `round(avg_accuracy * 100)` | Decided |
| survival | Subtracted from attacker hit chance, `matches/sim_helpers/shot.py`; missile dodge `min(20, survival/5)`%, `matches/simulation/entrypoints.py` | `avg_times_hit` per position + `elimination_rate` | Mean of a times-hit part and a non-elimination part (see below) | Decided |
| player_awareness | As the target: return-fire chance, blocks attacker follow-up chains (`shot.py`); nuke reaction `(ga + pa)/200` (`round_loop.py`) | `avg_hit_diff` + `avg_nukes_canceled` per position | Hit-diff part plus a nukes-canceled bonus of up to +10 (see below) | Decided |
| game_awareness | Below 35, sometimes shoots enemies who are already down (`mechanics.py`); commander nuke SP banking tiers 30/50/70 (`weights.py` `_commander_nuke_gate`); half of the nuke-reaction roll | `avg_mvp` per position | `50 + 25 * log2(player_mvp / position_avg_mvp)`, weighted by games (see below) | Decided |
| resource_awareness | None today: `_observe_lives` (`round_loop.py`) records enemy lives, but nothing reads them back | Per-game event timeline (TDF) | `50` for now (see below) | Deferred |
| decision_making | Sharpens action choice: top weight x`(1 + dm/100)`, the rest divided by it (`weights.py`); medic/ammo `_stress_fail` (`resupply_queue.py`, looks inverted) | `win_rate`, `avg_penalties`, `avg_shots_hit` per position | Mean of a results part and an activity part (see below) | Decided (revised after the Kodama talk) |
| stamina | Fatigue at each 10% of the round when stamina < checkpoint: -5% hit chance, -10% movement per penalty (`weights.py`, `player_state.py`) | Late- vs early-game performance (TDF) | `50` for now | Deferred |
| speed | Cells per tick `max(1, ceil(speed/100 * max_movement))` (`pathfinding.py`) | None (no position data, even in TDF) | `50` for now (see below) | Deferred |
| positioning | None (only feeds `overall_rating`) | `avg_uptime_pct` + `avg_hit_diff` per position | Mean of an uptime part and a hit-diff part (see below) | Decided |
| adaptability | None (only feeds `overall_rating`) | `avg_mvp` per position, `team_result: loss` scope | `50 + 25 * log2(player_mvp_in_losses / position_avg_mvp_in_losses)`, weighted by games | Decided |
| communication | Each tick and after a tag, with probability `comm/100`, shares enemy memory with allies within half the map diagonal; allies pathfind to known enemies (`round_loop.py`, `pathfinding.py`) | `avg_assists` per position (team uplift later) | `50 + 25 * log2(player_assists / position_avg_assists)`, weighted by games (see below) | Decided |
| teamwork | Above 50, with probability `tw/100`, moves to a high-sight cell an ally can see (`pathfinding.py`); medic/ammo `_stress_fail` (looks inverted) | `avg_resupplies_given` (ammo/medic), `avg_assists` (others), per position (team uplift later) | Log ratio vs position average, weighted by games (see below) | Decided |
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

## Findings to follow up

Found while tracing how the sim uses each stat. Not yet acted on.

- Six stats are never read by the simulation, only by `overall_rating`, which drives salary,
  seeding, rankings and development: `positioning`, `flexibility`, `adaptability`,
  `Offensive_synergy`, `defensive_synergy`, `midfield_synergy`.
- `resource_awareness` has no gameplay effect: `_observe_lives` (`matches/simulation/round_loop.py`)
  records enemy lives in memory, but nothing reads them back.
- `decision_making` and `teamwork` *raise* a support player's resupply stress-fail chance
  (`matches/sim_helpers/resupply_queue.py` `_stress_fail`). This looks inverted.
- The `player_state.py` comment says `resupply_efficiency` "doubles speed for Medic/Ammo".
  This is not implemented.
