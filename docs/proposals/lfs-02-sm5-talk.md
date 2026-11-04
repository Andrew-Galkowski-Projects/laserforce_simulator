# LFS-02: app changes suggested by Kodama's "How to Improve in SM5"

**Status:** proposal, reviewed in the LFS-02 grill (2026-11-03). Each accepted suggestion is a
`PLAN.md` entry (section *SM5 talk follow-ups*, `SM5-01`…`SM5-09`) or a bullet on an existing
MECH item. Every entry gets its own grill and spec when `/next-feature` reaches it; the numbers
below are starting points for those grills, not decisions.

**Source:** the talk ([video](https://www.youtube.com/watch?v=SngT6ipJsYU)), summarised in
`docs/lfstats-import.md` *Source for what each stat means*. Code facts were read off `main` at
`97d2edc6`; paths are under `laserforce_simulator/`.

## Priority rule

1. Bugs, where the code contradicts SM5 outright.
2. An inert stat gets an effect, cheapest first.
3. New mechanics.
4. `overall_rating` weighting (no sim effect).

Every entry that changes the sim changes seeded outcomes and folds into the **CAL-01**
re-baseline (the MECH-16 precedent). LFS-01's `lfstats_calibrate` harness should be re-run
after each one.

## Suggestions

| # | Talk point | Code today | Disposition | Priority |
|---|---|---|---|---|
| 1 | Decisive, team-minded supports resupply reliably | `_stress_fail` (`matches/sim_helpers/resupply_queue.py:99`) fails with `(dm + tw)/10 * prior_requests` %: higher decision_making and teamwork fail **more** | **SM5-01** (bug): flip to `(200 - dm - tw)/10 * prior_requests` | 1 |
| 2 | A downed pack cannot fire | Neither the `tag_player` plan (`sim_helpers/combat.py:359`) nor `resolve_shot` (`sim_helpers/shot.py:212`) checks the shooter is active; Commander keeps 10 tag weight, Medic 5, while down | **SM5-02** (bug): a non-active player cannot tag | 2 |
| 3 | "Look at your gun": aggression set by your own shots and lives; 9-13 lives safe, 6-7 head home, adjusted for the enemy commander's likely nukes | resource_awareness is read only by `_observe_lives` (`simulation/round_loop.py:43`), which writes `entry["lives"]` that nothing reads. Retreat is a flat 30% lives/shots rule (`sim_helpers/weights.py:254`, `sim_helpers/pathfinding.py:796`) | **SM5-03**: (a) lives bands replace the flat 30% rule, resource_awareness sets how reliably they are followed; (b) remembered enemy lives are read back, preferring low-lives targets | 3 |
| 4 | Yoga: contorting into angles, dodging missiles | Missile dodge is `min(20, survival/5)` % (`simulation/entrypoints.py:1216`); flexibility is never read | **SM5-04**: dodge moves to `min(20, flexibility/5)`; survival keeps only the hit roll | 4 |
| 5 | Adapt to the flow of the game; try as hard when losing | adaptability is never read. The score broadcast (`round_loop.py:193`) already tells a team it is behind | **SM5-05**: losing-state modifier, where low adaptability costs hit chance and decision sharpness while behind | 5 |
| 6 | Adapt to unfamiliar teammates | The sim has no notion of familiarity | **Rejected**: nothing to key it off | - |
| 7 | "Why am I here?": hold control zones, not neutral ones; how far forward to play for your shots and lives | positioning is never read | **SM5-06**: bias movement goals toward control cells; its grill defines a control zone in the zone/LOS model and reconciles "how far forward" with SM5-03(a) | 6 |
| 8 | (not in the talk) | Offensive/defensive/midfield synergies are never read | **Rejected for LFS-02**: the talk does not cover them. Candidate zone-based modifiers once SM5-06 defines zones | - |
| 9 | Announcing nukes removes teammates' 1-3 s reaction delay | Every enemy learns the Commander's cell the tick a nuke starts (`round_loop.py:230`) regardless of communication; the reaction roll `(ga + pa)/200` has no delay (`round_loop.py:296`) | **SM5-07**: unseen nukes start with a 2-6 tick reaction delay; a teammate who sees the Commander and passes the communication roll calls it out, removing it | 7 |
| 10 | "Commander down at big box"; sharing your shots and lives | Memory entries carry `downed` / `reset_window` status and broadcasts copy it, but no decision reads it | **Fold into MECH-14** (bullets added there); MECH-13 comes first | - |
| 11 | Sprinting misses information | No pace choice: cells per tick come only from speed (`sim_helpers/pathfinding.py:320`) | **SM5-08**: sprint (full speed, lower memory-update chance) vs jog (about 60% speed, full awareness) | 8 |
| 12 | Stamina: don't slow down in the second half | Fatigue checkpoints at each 10% of the round past the stamina value (`weights.py:900`) | **No change**: already matches | - |
| 13 | Laying trails while down | Downed players move and keep positioning goals (`pathfinding.py:884`) | **No change**: already works | - |
| 14 | Stubborn zone control: about 3 resets before leaving | No reset-persistence logic | **Fold into MECH-09** (bullet added there) | - |
| 15 | Missile-dodging technique | See #4 | **Covered by SM5-04** | - |
| 16 | Pie chart: game sense largest, mechanical skill about 5% | `overall_rating` is a plain mean of 19 stats (`teams/models.py:329`); feeds salary, seeding, draws, development, never the sim | **SM5-09**: weight it by the talk's six areas. The other area shares are not recorded; its grill settles them | 9 |

## Overlap with existing MECH items

- **MECH-08** (reset-timing miss): unchanged; the talk adds nothing to its open question.
- **MECH-09** (reset re-tag): gains the "about 3 resets before leaving" persistence rule.
- **MECH-10** (follow rule): unchanged; SM5-03's retreat bands are a separate lever on the
  pursued player, not the pursuer.
- **MECH-13** (per-player information table): prerequisite for the MECH-14 bullets and for
  SM5-03(b), which reads memory rather than true lives.
- **MECH-14** (memory/comms-driven behaviour): gains down call-outs with location and shared
  shots/lives.

## Notes for the follow-up grills

- **Importer data.** LFS-01 writes `flexibility = 50` and `resource_awareness = 50` for every
  imported player, so SM5-03 and SM5-04 change nothing between imported players until a source
  exists (`times_hit_by_missile` per game for flexibility; the TDF event log for
  resource_awareness, see `docs/lfstats-import.md`).
- **RNG stream.** `_observe_lives` draws one `random.random()` per observation today. Any change
  to it shifts every seeded game, even one that leaves its result unused.
- **Six stats are outside the engine.** `_SIMULATION_STATS` (`simulation/entrypoints.py:111`)
  copies 13 stats into `PlayerState`; SM5-04, SM5-05 and SM5-06 each add theirs to that tuple
  and to the `PlayerRoundState` forwarding properties (`matches/models.py:626`).
