# Laserforce Simulator

A Django application that simulates competitive laser tag (Laserforce SM5) matches and surfaces the resulting analytics. This is the single domain context for the project; the three Django apps (`teams`, `matches`, `core`) share one ubiquitous language defined below.

## Language

### Match structure

**Match**:
A contest between two teams, decided over exactly two **Rounds**; the teams swap colours between rounds and the winner is decided by rounds won, then cumulative points.

**Round**:
One 15-minute simulated game within a **Match** (persisted as `GameRound`).
_Avoid_: "game" (informal — say **Round**), "match" (a Match is the pair, not one round)

**Roster**:
The six **Players** fielded by a **Team** for a match — one of each role plus one duplicate.

### Teams and players

**Team**:
A named group of exactly six **Players**, one per **Role** plus one duplicate role.

**Player**:
A team member assigned a **Role**, carrying 19 numeric **Stats** that the simulator uses as behavioural weights.

**Role**:
One of the five SM5 positions — **Commander**, **Heavy**, **Scout**, **Medic**, **Ammo** — determining a player's resources, mechanics, and simulated behaviour. Only **Scout** may appear twice in a roster.

**Commander**:
The role that can fire a **Nuke**; high resources, no resupply ability.

**Heavy**:
The heavy-hitter role; always **Downs** a target in one **Hit** but fires at half rate.

**Scout**:
The mobile skirmisher role; its **Special** is **Rapid Fire**, and it is the only duplicable role.

**Medic**:
The support role that **Resupplies** allies' **Lives**.

**Ammo**:
The support role that **Resupplies** allies' **Shots**.

**Stat**:
One of a player's 19 numeric attributes (0–100) that bias simulated decisions; not skill points, never consumed in-game.

**Overall rating**:
The unweighted mean of a player's 19 **Stats**, shown for display only — never used directly by the simulator.

**Preferred-role boost**:
A flat ×1.2 (capped at 100) applied to a player's **Stats** during simulation when their game **Role** is one of their preferred roles; affects simulation only, never the stored stat or **Overall rating**.

### Resources

**Lives**:
A player's remaining respawns this round; losing the last life is **Elimination**.

**Shots**:
A player's remaining ammunition; firing consumes one, reaching zero requires a **Resupply** (or the Ammo role).

**Shields**:
Per-life damage buffer; a **Hit** that takes shields to zero is a **Down** (costs one **Life**, shields reset to max).

**Special points (SP)**:
The charge resource accumulated by scoring that is spent to activate a role's **Special**.
_Avoid_: "special" alone (overloaded — say **SP** for the resource, **Special** for the ability)

**Special**:
A role's activatable ability — the Commander's **Nuke** or the Scout's **Rapid Fire**; "activating the special" spends **SP**.

**Missile**:
A locked, dodgeable long-range attack available to Commander and Heavy, scored separately from tag points.

**Resupply**:
A support transfer of a resource to an ally — **Medic** gives **Lives**, **Ammo** gives **Shots**.

**Combo resupply**:
A single tick in which a requesting player receives both a **Lives** resupply and a **Shots** resupply, from a Medic and an Ammo together.

**Resupply request**:
An action by any player signalling they need a **Resupply**, resolved at end of tick against eligible nearby support players.

### Combat outcomes

The shot → hit → tag → down → elimination ladder is the most-confused cluster in this domain; each rung is distinct.

**Shot**:
The act of firing a tagger once; consumes one **Shot**; may **Miss** or become a **Hit**.

**Hit**:
A **Shot** that connects with a target (reduces **Shields**).

**Tag**:
A **Hit** that lands on a valid enemy and scores — the canonical scoring event.
_Avoid_: using "tag" for any **Hit**, or for the act of firing (that is a **Shot**)

**Down / Downed**:
A **Hit** that takes the target's **Shields** to zero, costing them one **Life** and triggering the **Respawn cooldown**.
_Avoid_: "kill", "eliminate" (a Down costs one life, not the round)

**Elimination**:
A player losing their last **Life** and being out for the remainder of the round.
_Avoid_: "down" (a Down is one life; an Elimination is all of them)

**Follow-up shot**:
An extra shot the attacker may fire after a **Hit** that did *not* **Down** the target (chain depth capped at 2).

**Reaction shot**:
A shot the defender may fire back after being **Tagged** or **Missed** — it *requires a prior enemy **Shot** at the defender*. Distinct from an **Overwatch shot** (pre-emptive, no prior Shot).

**Overwatch shot**:
The shot a player in **Overwatch** (a **Hold**ing player) fires automatically the moment an enemy enters its **Line of sight**, *with no prior enemy **Shot** against the holder* — a pre-emptive trigger, the inverse of a **Reaction shot**. A full **Shot** in every other respect: consumes one **Shot**, rolls the normal hit chance, can **Tag**/**Down**, chains a **Follow-up shot**, and provokes a **Reaction shot** from its victim.
_Avoid_: calling it a **Reaction shot** (a Reaction shot is retaliation *after* being shot at; an Overwatch shot fires *first*).

**Miss**:
A fired **Shot** that does not connect.

**Nuke**:
The Commander **Special** that, after a **Fuse window**, eliminates lives across the enemy team unless cancelled.

**Fuse window**:
The delay between a **Nuke** being fired and it detonating, during which **Downing** the firing Commander cancels it.

**Rapid Fire**:
The Scout **Special** that removes the shot-rate cooldown while active.

**Locking event**:
The `GameEvent` row emitted at **Missile** fire / lock start (`event_type="locking"`), carrying `metadata = {"actor_role", "target_role"}`. Distinct from the **Missiled event** that fires at resolution: a Locking event marks the *fire tick*, a Missiled event marks the *resolution tick* (hit or miss). Together they replace the pre-RES-03 single `event_type="missile"` row, which collapsed both moments into one and made fired-but-cancelled missiles invisible to the log. If the locking actor is **Down**ed before the missile resolves, the Locking event remains in the log but **no Missiled event fires** (mirrors the **MECH-05** nuke-cancellation precedent: a fired-but-cancelled attack leaves a fire-tick trace, not a resolution-tick one).
_Avoid_: calling the resolution row a "locking" event, or reading a Locking event as proof that the missile landed (the resolution lives on the **Missiled event**).

**Missiled event**:
The `GameEvent` row emitted at **Missile** resolution (`event_type="missiled"`), carrying `metadata = {"result": "hit"|"miss", "friendly_fire": bool, "actor_role", "target_role"}`. One Missiled event per resolved missile; never emitted when the locking actor was **Down**ed before resolution (see **Locking event**). Drives the missile-log surface at `/matches/game-round/<id>/missile-log/` and its **fired / hit / efficiency** summary (efficiency = `hits / fired × 100`, computed view-side; **Friendly fire** hits count as hits). Distinct from the **Locking event** at the fire tick.
_Avoid_: confusing the missile-log row count (Missiled events) with the lock-attempt count (Locking events) — they differ whenever a locker is Downed mid-flight.

**Friendly fire**:
A **Missiled event** where `actor.team_color == target.team_color` — a player hitting their own teammate with a **Missile**. Server-emitted on the event row as `metadata["friendly_fire"]: bool`, never derived view-side, so the view code stays dumb and the contract is single-source. Friendly fire hits **count as hits** in the missile-log efficiency summary — the missile landed; the friendly-fire flag carries the qualitative distinction rather than discounting the hit. Rendered with a CSS class containing the substring `friendly-fire` so the row is visually distinguishable. Tag events have no analogous flag — friendly fire is a missile-specific qualifier in this codebase.
_Avoid_: deriving friendly-fire status from team-colour comparison at the template layer (the server emits the bool); excluding friendly fire from the hit count (it is a hit, just a regrettable one).

### Time

**Tick**:
The simulator's atomic time step **and the canonical time unit of the system**. One tick is 0.5 real seconds; a round is 1800 ticks. Every value the code touches — persisted DB columns, `GameEvent.timestamp`, the survived sentinel, simulator constructor arguments, and the REST API — is in ticks.
_Avoid_: frame, step, iteration; using "seconds" for anything stored, compared, or returned by the API

**Second**:
A **display-only** derivation, `ticks / 2`, applied solely at human-rendered output (HTML templates and the `score_averages` / `game_analysis` CLI). Never persisted, never compared internally, never returned by the API.
_Avoid_: treating seconds as a stored or API unit; the inverse of the pre-TIME-01 rule

**Respawn cooldown**:
The 8 seconds after a **Down** before a player is fully active again, split into the **Not-targetable window** then the **Reset window**.

**Not-targetable window**:
The 0–3 s immediately after a **Down** when the player cannot be **Tagged**.

**Reset window**:
The 4–7 s after a **Down** when the player is **Taggable** again but not yet fully active. Distinct from the **Not-targetable window**.

**Uptime breakdown**:
The per-player split of a round into active, reset-window, not-targetable, and derived dead-time **ticks**; these reconcile to the 1800-tick round duration and drive the `score_averages` view (which divides by 2 for its seconds display).

### Arena and space

**Arena map**:
An uploaded venue layout, processed into a **Cell** grid with precomputed **Sight lines**; optional for a round.

**Cell**:
One square of the arena grid — the unit of player position and movement.
_Avoid_: "tile", "square"

**Zone**:
The territorial classification of a region — **red**, **neutral**, or **blue** — used for scoring and the map-less fallback.
_Avoid_: using "zone" for a **Cell**, for **Zone size**, or for an arbitrary map region

**Zone size**:
The pixel granularity at which an **Arena map** is divided into **Cells**; map configs are keyed by it. Not a **Zone**.

**3-zone fallback**:
The map-less behaviour where the arena is just red/neutral/blue **Zones** with no **Cells** or **Sight lines**; used when a round has no **Arena map**.

**Base**:
A scoring objective on a raised platform — each team's home base plus up to four neutral bases — that can be **Captured**.

**Base capture**:
Interacting with a **Base** in range to score it (also called base destruction for enemy/neutral bases).

**Line of sight (LOS)**:
Whether one **Cell** can see another, after walls and elevation; the basis for **Tag** eligibility when a map is active.

**Sight line**:
A precomputed, possibly one-directional LOS link between two **Cells**, stored on the map.

**Wall**:
A movement/sight obstruction: **high wall** (blocks both), **low wall** (blocks movement, not sight), **windowed wall** (blocks sight but allows tagging through a directional aperture).

**Elevation / High ground**:
A per-cell height value; higher attackers can shoot over **high walls** and are harder to **Hit** from below.

**Spawn cell**:
A precomputed **Cell** near a team's **Base** where its players start the round.

**Strong spot**:
A precomputed high-LOS defensive **Cell** that **Heavies** position toward.

**Movement adjacency**:
The graph of which **Cells** a player can step between (≠ **Sight line** adjacency — LOS is not reachability).

### Simulation model

**Simulator**:
The engine that advances a round tick-by-tick. The project consolidated onto **`BatchSimulator`** as the sole engine (SIM-09, May 2026; [ADR-0002](docs/adr/0002-two-simulation-engines.md) superseded). The legacy `ResourceBasedSimulator` is removed; its view-path responsibilities (`simulate_match`, `simulate_single_round_detailed`, DB-flush) now live on `BatchSimulator`.

**Action**:
The single *deliberate* choice a player makes per tick — **tag**, **only_move**, **hide**, **capture**, **special**, **resupply**, **missile**, **request resupply**, or **hold** — chosen by weighted random. Independent of the always-on **Advance**: movement toward the **Goal cell** happens every non-**stationary** tick regardless of which Action was chosen. The Action does not gate movement.
_Avoid_: treating "move" as something done *instead of* acting — movement is decoupled from the Action choice (MOVE-01); the only movement-flavoured Action is **only_move**, which merely *doubles* that tick's Advance.

**only_move**:
The **Action** that devotes the tick entirely to repositioning: that tick's **Advance** covers **twice** the normal distance and no other deliberate effect is applied. Renamed from the legacy `change_zone`; movement itself is no longer gated by this choice.
_Avoid_: the old name `change_zone` (and reading it as "the action that makes you move" — every tick moves).

**Advance**:
The goal-directed **Cell** movement every non-**stationary** player performs **each tick**, toward their current **Goal cell**, a distance set by their **speed** **Stat**. Always-on and independent of the weighted **Action**; suppressed only while the player is **stationary**. Doubled on a tick whose Action is **only_move**.

**Hold**:
The **Action** of taking up **Overwatch**: the player anchors to its current **Cell** and watches its **Sight lines**. Like **hide**, Hold *carries over* — once rolled it stays in effect (the player remains in Overwatch) until the player rolls a non-Hold Action or is **Down**ed/respawned (a life loss force-clears it, knocked off position). A Hold**ing** player is **Stationary** (does not **Advance**).
_Avoid_: confusing **Hold** with **hide** — hide seeks a low-LOS cell to *avoid* being seen; Hold deliberately watches a sightline to *shoot* anyone who crosses it.

**Overwatch**:
The state of a **Hold**ing player: every tick, the moment any enemy enters (or **Advances** through) the holder's **Line of sight**, the holder fires an **Overwatch shot** at it pre-emptively. Persists for as long as **Hold** is carried over.

**Stationary**:
The state in which a player does **not Advance** this tick. True only while **hiding** (`hide` Action / hide carried over), while in **Overwatch** (`hold` Action / hold carried over — the holder is anchored to its sightline), or when the tick's **Action** is **capture_base** (the player is anchored to the **Base** being captured). Every other Action Advances toward the **Goal cell** while it acts.

**Weight**:
The per-action numeric likelihood, derived from role, situation, and **Stats**, that drives the random **Action** choice.

**Goal cell**:
The **Cell** a player is navigating toward via **Advance**. Held under **Goal commitment** between recomputes (the steady-state positioning cascade — action-driven, role-positioning, enemy-base default — recomputes on a fixed tick cadence rather than every tick); the reactive overrides — nuke-reaction, critical-resource (lives/shots ≤ 30% → seek medic/ammo), and score-broadcast seek-medic — bypass the commitment and may set a fresh Goal cell every tick.

**Goal commitment**:
A player holds its current **Goal cell** for a fixed tick cadence (`GOAL_RECOMPUTE_PERIOD_TICKS = 4`) rather than recomputing every tick. Only the steady-state positioning cascade (action-driven, role-positioning, enemy-base default) is throttled; the reactive overrides — nuke-reaction, critical-resource (lives/shots ≤ 30%), score-broadcast seek-medic — bypass the commitment and may set a fresh Goal cell every tick. Force-cleared on {round start, Goal cell reached, exiting **Stationary** (hide → not-hide, hold → not-hold), a reactive override firing this tick}; on **Down**/respawn the route (**Path commitment**) is always invalidated but the committed goal is cleared **iff** it came from action-driven targeting (tag / missile / resupply / hide) — positioning goals (role-positioning, enemy-base default, `only_move`-driven) survive a Down because the player keeps **Advancing** through the **Respawn cooldown** and the positioning intent is still tactically valid. Distinct from **Path commitment** (the *route* to the goal; both are per-player commitments carried between recomputes, but Path commitment is invalidated whenever a Goal commitment recompute changes the **Goal cell**, while re-picking the same cell leaves the route untouched). Lives on **`BatchSimulator`** (the sole engine post-SIM-09). Internally deterministic (consumes no RNG; serial == parallel, faithful **Replay** still hold) but **not** identical to pre-MOVE-04 seeded games — folded into the same pending post-MOVE-01 Score Calibration re-baseline as MOVE-02 / MOVE-03 (no new obligation).
_Avoid_: confusing **Goal commitment** (commits the destination for a window) with **Path commitment** (commits the route to that destination); reading the cadence as a free optimisation — staler goals deliberately shift pursuit/positioning.

**Movement trail**:
The ordered list of **Cells** a player occupies across a **Round** — every cell stepped through by each **Advance**, in sequence. The record of where a player has *been*; the basis from which **Cell occupancy** is reconstructed for the **Movement heatmap** (RES-04). Distinct from **Goal cell** (where they are *going*) and **Movement adjacency** (where they *could* step).

**Cell occupancy**:
The tick count a player spent in each **Cell** during a **Round** — the per-cell time-in-cell measurement. Reconstructed from the player's **Movement trail** by A* expansion of the intermediate cells across each **Advance** (the compact start/end pair stored on each movement `GameEvent` is widened back into the full route deterministically, mirroring the **Replay** contract). Persisted per round on `GameRound` as a cached aggregate and aggregated across rounds to drive the **Movement heatmap**. Distinct from **Movement trail** (the ordered sequence of cells) — Cell occupancy is the unordered per-cell sum.

**Movement heatmap**:
The visual aggregation of **Cell occupancy** rendered as a per-cell intensity overlay on the **Arena map** image — high-tick cells appear hot, untouched cells are transparent. Surfaces in two places: a single-**Round** view at `/matches/game-round/<id>/heatmap/` (filterable by player / role / team **Side**), and a multi-round composite on the map editor (every **Round** ever played on that **Arena map**, filterable by team **Side** only). Map-less rounds (3-zone fallback, no `arena_map`) have no Movement heatmap — the view renders a "No map — heatmap unavailable" notice instead of a fallback bar chart (the PLAN.md pre-MAP-integration fallback is dropped; map integration is complete).
_Avoid_: confusing the per-Round heatmap with the multi-round map-editor heatmap (different aggregation scope, different filter set); calling a 3-zone bar chart a "heatmap" (no such fallback exists post-RES-04).

**Path commitment**:
A player follows the single A* route computed when its **Goal cell** was set, re-stepping that cached route each tick rather than re-planning every tick; the route is recomputed only when the **Goal cell** changes, the next route **Cell** is blocked, or the player is knocked off it (Down/respawn). The pre-MOVE-02 behaviour re-derived the route from the current cell every tick and could re-pick among equal-cost routes ("path wobble"); path commitment removes that. Lives on **`BatchSimulator`** (the sole engine post-SIM-09). Internally deterministic (serial == parallel, faithful **Replay**) but **not** identical to pre-MOVE-02 seeded games. Distinct from **Goal cell** (the destination is unchanged; only how the route to it is chosen and held differs).
_Avoid_: reading the path cache as a behaviour-neutral optimisation — it deliberately changes which equal-cost route is walked (MOVE-02 / [ADR-0008](docs/adr/0008-path-commitment-via-goal-keyed-cache.md)).

**MVP score**:
A role-weighted per-player round score emphasising that role's primary contribution; display/ranking only, distinct from points.

**Player memory**:
A player's imperfect, decaying knowledge of other players' last-known **Cells**, refreshed by LOS and **Broadcasts**; replaces perfect knowledge for goal selection (but not for resupply resolution).

**Broadcast**:
A team- or arena-wide event (nuke activation, score update, medic-under-fire) that updates listeners' **Player memory**.

**Staleness**:
The age past which a **Player memory** entry is no longer trusted; threshold depends on the remembered player's **Role**.

### Reproducibility

**Side** (a.k.a. **Colour**):
The red or blue assignment a **Team** plays for one **Round** — which physical half of the arena and **Base** it starts from. Distinct from **Zone** (the red/neutral/blue *territorial* classification of a region). "The teams swap colours between rounds" (the per-**Match** rule) is one specific reuse of this assignment.
_Avoid_: using "side" for a **Zone** or a **Cell**; conflating the per-**Match** colour swap with **Side alternation**

**Side alternation**:
The **Batch run** policy of flipping which **Team** takes the red **Side** on successive games (game 0 canonical, game 1 flipped, …), so neither team's aggregated stats are biased by any map-side advantage. Deterministic by game index — it never consumes the RNG. Distinct from the per-**Match** colour swap, which flips colours between the *two Rounds of one Match*; side alternation flips between the *independent single Rounds of one Batch run*.

**Orientation**:
The per-game side assignment under **Side alternation**, recorded as a `flipped` boolean. The reproducible unit of a batch game is the pair *(RNG seed, orientation)*: replay is faithful only when the orientation is reproduced alongside the seed, rosters, and map. A persisted flipped game stores the *actual* sides (its `team_red` is the team that really played red).

**RNG seed**:
A single integer used to initialise the random number generator before one **Round** is simulated, persisted on that round. Reseeding from it reproduces that round's event log exactly, *provided the rosters, map, and **Orientation** are unchanged*. Distinct from an **RNG state**.
_Avoid_: calling the stored `random.getstate()` snapshot a "seed" — that is an **RNG state**

**RNG state**:
The full internal generator snapshot (`random.getstate()` — a version + Mersenne-Twister vector + Gaussian cache). Not what is persisted; the project stores an **RNG seed** instead.

**Master seed**:
The root integer of a **Batch run** from which every per-round **RNG seed** is derived in a fixed order. Random per batch run by default; may be supplied explicitly to make an entire batch reproducible. Not persisted.

**Seed chain**:
The deterministic sequence of per-round **RNG seeds** produced from a **Master seed** — "same master seed ⇒ same chain ⇒ same games."

**Replay**:
Re-running a single persisted **Round** from its stored **RNG seed** to reproduce its exact event log. Faithful only while that round's rosters, map, and **Orientation** are unchanged; the seed captures randomness only, not world state.

**Batch run**:
Simulating the same two **Teams** N times to sample the outcome distribution (win %, score variance); remains a variance sampler — a fresh random **Master seed** per run unless one is supplied. Applies **Side alternation** so per-team aggregates are not biased by map-side advantage; aggregate keys are **team-position keyed** (`red_*` = the team passed as the `team_red` argument, whichever **Side** it played), with a separate side-advantage breakdown for the raw red/blue-side signal.

## Relationships

- A **Match** has exactly two **Rounds**; a **Round** belongs to one **Match** (or stands alone).
- A **Team** fields a six-player **Roster**; each **Player** has one **Role** for the round.
- A **Shot** may become a **Hit**; a **Hit** on a valid enemy is a **Tag**; a **Hit** that empties **Shields** is a **Down**; losing the last **Life** to a Down is **Elimination**.
- A **Down** starts the **Respawn cooldown** = **Not-targetable window** then **Reset window**.
- Scoring accrues **SP**; spending **SP** activates a **Special** (**Nuke** for Commander, **Rapid Fire** for Scout).
- A **Round** runs on an **Arena map** (→ **Cells**, **Sight lines**, **Bases**, **Elevation**) or on the **3-zone fallback**.
- **LOS** between **Cells** gates **Tag** eligibility; **Movement adjacency** (≠ LOS) gates stepping between cells.
- A **Player** picks an **Action** by **Weight** and, unless **stationary**, **Advances** each tick along its committed **Goal cell** (held under **Goal commitment**; reactive overrides — nuke-reaction, critical-resource, score-broadcast seek-medic — bypass the commitment and may set a fresh Goal cell every tick); **Player memory** informs goal selection. The **only_move** Action doubles that tick's Advance. The path of cells walked accumulates into the player's **Movement trail**.
- A **Hold**ing player is in **Overwatch** and **Stationary**: any enemy entering or **Advancing** through its **Line of sight** draws a pre-emptive **Overwatch shot** (a full **Shot**, distinct from a **Reaction shot**).

## Example dialogue

> **Dev:** "When a Heavy tags a Scout, does the Scout lose a life?"
> **Domain expert:** "Only if it's a **Down** — the **Hit** has to take **Shields** to zero. A Heavy always Downs in one Hit, so yes for a Heavy. For a Scout attacker it usually takes several **Hits** first; each Hit is still a **Tag** and scores, but only the Hit that empties shields is the Down."
> **Dev:** "And losing that life eliminates them?"
> **Domain expert:** "No — a **Down** costs one **Life** and starts the **Respawn cooldown**. **Elimination** is only when the *last* life goes."

## Flagged ambiguities

- **"zone"** — used loosely for the red/neutral/blue classification, the `zone_size` granularity, the legacy `current_zone`/`zone_fallback` field, and arbitrary map regions. Resolved: **Zone** = the red/neutral/blue territorial classification *only*; the playable grid unit is a **Cell**; `zone_size` is **Zone size** (cell granularity), not a Zone.
- **"tag" vs "hit" vs "shot"** — resolved: **Shot** = firing once; **Hit** = a Shot that connects; **Tag** = a Hit on a valid enemy that scores. Do not use "tag" for the act of firing or for any Hit.
- **"down" vs "eliminate" / "kill"** — resolved: a **Down** costs one **Life** (shields to zero); **Elimination** is losing the last life. "Kill" is not domain language.
- **"special"** — resolved: **Special** = the role ability (Nuke / Rapid Fire); **Special points (SP)** = the charge resource; "use_special" / activating the special = spending SP to trigger the ability.
- **"round" vs "match" vs "game"** — resolved: a **Match** is the two-round contest; a **Round** is one 15-minute simulation; "game" is informal and should be avoided in favour of **Round**.
- **"seed" vs "state"** — resolved 2026-05-15 by SIM-07: the project persists an **RNG seed** (a small integer passed to `random.seed()`), *not* an **RNG state** (`random.getstate()` snapshot). `GameRound.rng_seed` is genuinely a seed. Do not call a getstate() tuple a "seed"; do not try `random.seed()` on a state tuple. Decision and rejected alternatives recorded in [docs/adr/0005-rng-seed-not-state-for-replay.md](docs/adr/0005-rng-seed-not-state-for-replay.md).
- **"side / colour swap" vs "side alternation"** — resolved 2026-05-15 by SIM-08: the per-**Match** colour swap (red/blue flip between the *two Rounds of one Match*) and **Side alternation** (flip between the *independent single Rounds of one Batch run*) are different mechanisms — do not conflate. Batch aggregate keys are **team-position keyed**, not side-keyed; the reproducible unit of a batch game is *(RNG seed, **Orientation**)*. Decision and rejected alternatives (seed-derived parity; persist-only) recorded in [docs/adr/0006-batch-side-alternation.md](docs/adr/0006-batch-side-alternation.md).
- **"seconds_active stores ticks vs seconds"** — re-resolved 2026-05-15 by TIME-01 (supersedes the earlier "stores seconds" resolution): the uptime fields are renamed `ticks_*` and store **ticks**; the survived sentinel is `1801`; `GameEvent.timestamp`, constructor args, and the REST API are all ticks. Seconds are a display-only `÷2` at HTML/CLI output. Decision and the API-returns-ticks consequence recorded in [docs/adr/0001-time-unit-seconds-now-tick-native-later.md](docs/adr/0001-time-unit-seconds-now-tick-native-later.md).
- **"path caching is behaviour-neutral"** — resolved 2026-05-17 by MOVE-02: goal-keyed A* path caching is **not** free of behavioural change. A grid has many equal-cost shortest paths; the pre-MOVE-02 per-tick recompute could re-pick among them ("wobble"), a goal-keyed cache commits to one route (**Path commitment**). Both are fully deterministic (`astar_path` heap orders on int tuples, PYTHONHASHSEED-independent; serial == parallel holds either way), but they produce different cell sequences ⇒ different seeded games. MOVE-02's contract is *internal* determinism, **not** identity to pre-MOVE-02 games; the delta is absorbed by the already-pending post-MOVE-01 Score Calibration re-baseline (no new obligation). The PLAN.md "no behavioural change / identical games" wording was contradictory and is superseded. Goal-recompute throttling is a separate *behavioural* perf lever, resolved 2026-05-20 by MOVE-04 — see the **"Goal cell is recomputed every tick"** entry below and [docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md](docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md) (**Goal commitment**); `hold`/overwatch is split to MOVE-03. Decision and rejected alternatives in [docs/adr/0008-path-commitment-via-goal-keyed-cache.md](docs/adr/0008-path-commitment-via-goal-keyed-cache.md).
- **"move" / `change_zone` / Advance** — resolved 2026-05-17 by MOVE-01: movement is **decoupled** from the weighted **Action**. Every non-**Stationary** player **Advances** toward their **Goal cell** every tick (formerly movement only happened when the weighted roll picked `change_zone`, so zero-`change_zone`-weight roles never moved). The legacy `change_zone` Action is renamed **only_move** and now only *doubles* that tick's Advance distance — it no longer gates movement. **Stationary** = hiding or capturing a **Base**. Cells stepped through accumulate into a per-player **Movement trail**. Do not call movement an Action, and do not read `only_move` as "the action that makes you move" (every tick moves). Recorded in [docs/adr/0007-movement-decoupled-from-action.md](docs/adr/0007-movement-decoupled-from-action.md).
- **"Goal cell is recomputed every tick"** — resolved 2026-05-20 by MOVE-04 (supersedes the pre-MOVE-04 every-tick recompute wording in the **Goal cell** entry). The `choose_goal_cell` cascade splits into a *reactive* layer that **does** fire every tick (steps 0/1/1b — nuke-reaction, critical-resource lives/shots ≤ 30%, score-broadcast seek-medic) and a *steady-state positioning* layer that is held under **Goal commitment** between recomputes (steps 2/3/4 — action-driven, role-positioning, enemy-base default), cadence `GOAL_RECOMPUTE_PERIOD_TICKS = 4` ticks (2 s). **Force-recompute triggers** beyond the cadence: {round start (no prior commitment), Goal cell reached, exiting **Stationary** (hide/hold → not), a reactive override firing, **Down**/respawn iff the committed goal came from action-driven targeting — positioning goals survive a Down because the player keeps **Advancing** through the **Respawn cooldown**}. Phase is **expiry-based** (`expires_at_tick = tick + N` set on each recompute), not `tick % N == 0` — load is naturally staggered per-player without hashing. **`BatchSimulator` only**; consumes **no RNG** so the SIM-07/SIM-08 contract (serial == parallel, faithful **Replay**) holds in form, but deliberately changes which goals are held between recomputes ⇒ seeded games differ from pre-MOVE-04, folded into the same pending post-MOVE-01 Score Calibration re-baseline as MOVE-02 / MOVE-03 (no new obligation). Decision and rejected alternatives (per-role N, map-size-scaled N, whole-cascade throttle including reactive overrides, `tick % N == 0` global phase, source-blind Down-clear, reusing `_path_cache[0]` instead of a dedicated `_committed_goal` field) recorded in [docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md](docs/adr/0010-goal-commitment-via-tick-cadence-throttling.md).
- **"hold" vs "hide"; "Overwatch shot" vs "Reaction shot"** — resolved 2026-05-18 by MOVE-03: **Hold** is a new 9th **Action** (a carried-over, **Stationary** posture like hide, also force-cleared on **Down**/respawn) that puts the player in **Overwatch**; an enemy entering or **Advancing** through the holder's **Line of sight** draws a pre-emptive **Overwatch shot**. An **Overwatch shot** is a full **Shot** (consumes a Shot, normal hit roll, can **Tag**/**Down**, chains a **Follow-up shot**, provokes a victim **Reaction shot**) but is **not** a **Reaction shot**: a Reaction shot requires a prior enemy Shot at the defender, an Overwatch shot fires *first*. **Hide** seeks a low-LOS cell to avoid being seen; **Hold** watches a sightline to shoot. Overwatch *resolution* (traversed-cell LoS check via the path-commitment cache) lives on **`BatchSimulator`** (the sole engine post-SIM-09). The legacy RBS *was* a Stationary no-op for `hold`; SIM-09 removed it. Behavioural change ⇒ folds into the already-pending post-MOVE-01 Score Calibration re-baseline (no new obligation). Decision and rejected alternatives recorded in [docs/adr/0009-hold-overwatch.md](docs/adr/0009-hold-overwatch.md).
- **"two simulation engines"** — resolved 2026-05-20 by SIM-09 (supersedes the pre-SIM-09 [ADR-0002](docs/adr/0002-two-simulation-engines.md) two-engines decision). The project now has a single engine, **`BatchSimulator`**. Mechanics that were previously duplicated between RBS and BatchSim via the duck-typed `sim_helpers/` interface are now single-sourced; the view path (`create_match`, `create_single_round`, batch + save) all go through `BatchSimulator`. `BatchSimulator` gained `simulate_match` / `simulate_single_round_detailed` / an extended `_flush_to_db(..., match, round_number, arena_map, zone_size)` to absorb RBS's view-path responsibilities; the per-Match colour swap is preserved by argument-order swap in `simulate_match` (round 2 args reversed; `match.red_round2_points = round2.blue_points`) and is **distinct from SIM-08 Orientation** (batch-only `run` / `save_games`). The five former `ResourceBasedSimulator.@staticmethod` map-loading helpers move to `matches/sim_helpers/map_loader.py` as free functions (`load_map_context`, `resolve_map_data`, `build_movement_ctx`, `zone_from_cell`, `build_spawn_assignments`); behaviour and signatures unchanged. Decision and the cascade of "RBS-only" / "BatchSim-only" caveats in other CONTEXT.md entries collapsing to plain statements recorded in PLAN.md SIM-09 note and [ADR-0002](docs/adr/0002-two-simulation-engines.md) (superseded).