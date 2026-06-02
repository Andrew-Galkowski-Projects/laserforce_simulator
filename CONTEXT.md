# Laserforce Simulator

A Django application that simulates competitive laser tag (Laserforce SM5) matches and surfaces the resulting analytics. This is the single domain context for the project; the three Django apps (`teams`, `matches`, `core`) share one ubiquitous language defined below.

## Language

### Match structure

**Match**:
A contest between two teams, decided over exactly two **Rounds**; the teams swap colours between rounds and the winner is decided by rounds won, then cumulative points.

**Round**:
One 15-minute simulated game within a **Match** (persisted as `GameRound`).
_Avoid_: "game" (informal — say **Round**), "match" (a Match is the pair, not one round)

**Simulated round**:
A **Round** produced by the **Simulator**, carrying `GameRound.is_simulated = True` — the provenance distinction that drives the "[Simulated]" watermark on the **Round report** (RV-03). The `is_simulated` `BooleanField(default=True)` is added by RV-03; the simulator paths inherit the default and never set it explicitly, so today *every* persisted Round is a Simulated round (there is no real-game import path yet). The eventual source of truth for provenance is the **Actual game log** pairing: a Round not linked to an `actual_game_log` is simulated (`is_simulated = True`); an imported real-game Round links to its `actual_game_log` and is stored `is_simulated = False` (no watermark). That import path — the `.tdf` parser and the `actual_game_log` link — is **IMPORT-01** (PLAN.md), the first writer of `is_simulated = False`.
_Avoid_: inferring simulated-vs-real from `rng_seed` presence (a null seed means "predates SIM-07", not "real game") — provenance is the explicit `is_simulated` flag.

**Actual game log**:
A real Laserforce SM5 game export (a `.tdf` file — UTF-16, tab-separated, sectioned) imported and stored so a **Round** can represent an *actual* game rather than a **Simulated round**. A Round paired with an Actual game log is `is_simulated = False`; the parser and import tool are **IMPORT-01** (PLAN.md, not yet built). Sample logs live in `Screenshots_and_video_examples/sample_games/`.
_Avoid_: treating an Actual game log as a **Replay** (a Replay re-runs the **Simulator** from an **RNG seed**; an Actual game log is recorded real-world play, never re-simulated).

**Round report**:
The single-**Round** PDF export at `GET /matches/game-round/<id>/export/` (RV-03) — round summary, scoreboards, per-player table, and resource summary, generated server-side with ReportLab. A **Simulated round** is stamped with a "[Simulated]" watermark; charts are deliberately *not* in the first cut (deferred — see PLAN.md).

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

**Free Agents Team**:
The reserved system **Team** named `"Free Agents"`, identified by magic name (no `is_system` field). Holds generated players who were not assigned to a regular Team via the **LG-00 generation** flow's `num_teams = 0` branch. Filtered from the Teams list via the new `Team.objects.regular()` manager method; visible via the Players tab (LG-00c). Has no slot FKs filled — `is_valid_roster` returns False, by design (the Free Agents Team is a player container, not a competitive roster). Auto-created on first use via `Team.objects.get_or_create(name="Free Agents")`.
_Avoid_: treating the Free Agents Team as a playable Team (it can't pass `is_valid_roster`); creating a second Free Agents Team (auto-created by name; reused).

**LG-00 generation**:
The bulk player-creation flow at `GET /teams/generate/` (`POST` to the same URL). Two output modes — `num_teams ≥ 1` creates new Teams with auto-filled rosters + optional bench; `num_teams = 0` creates a flat pool of players on the **Free Agents Team**. Stat values are randomised by Gaussian draw (mean / std-dev user-configurable). Distinct from a **Roster import** (LG-00b) and from the per-player edit form.

**Roster import**:
The CSV-driven bulk player-creation flow at `GET /teams/import/` (`POST` to the same URL), with a `GET /teams/import/template.csv` companion that returns a downloadable header-plus-example template. Rows are routed to **Teams** by a required `team` column — existing Teams are appended to, missing Teams are auto-created. The required `role` column drives **slot assignment only** (the value names the `slot_*` FK the Player fills); an optional `preferred_roles` column carries the comma-separated **Preferred-role boost** roles. All 19 **Stat** columns are optional and default to 50; the five profile columns (`age`, `started_playing_age`, `total_games`, `home_site`, `height`) are required. The import is **all-or-nothing**: the whole `POST` runs under `@transaction.atomic`, and any error — unknown header, invalid role, out-of-range stat, `(team, name)` duplicate, in-file slot over-fill (≥ 2 of any non-Scout role on the same team, or ≥ 3 Scouts), or collision with an already-filled slot on an existing Team — rejects the file with a per-row error list and writes nothing. Distinct from **LG-00 generation** (random Gaussian stat draws, no user-supplied roster) and from the per-player Add Player form (one Player at a time, current-Team only).
_Avoid_: treating Roster import as a way to *overwrite* an existing Team's slots — collisions are rejected, not silently replaced; treating a bench-vs-slot decision as the importer's job — `role` names the slot and an over-fill is an error, not a fallback to bench.

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

**Nuke cancellation**:
A **Nuke** that is dropped without detonating because its firing Commander was **Down**ed (**Shields** to zero) or eliminated during the **Fuse window** — the **MECH-05** rule. A Down clears the Commander's `special_active_until`, disarming the pending nuke. Emitted as a discrete `GameEvent(event_type="nuke_cancelled")` at the **Down/disarm tick** (the dramatic "nuke stopped" moment, RV-02), *not* at the would-be detonation tick. The cancelled nuke is **left in the pending queue** (an emit-only flag prevents a duplicate at drain time) so nuke-reaction behaviour is unchanged — RV-02 records the cancellation without altering any mechanic. Before RV-02 the cancellation left only the activation trace in the log and no resolution row. Distinct from a **Nuke** detonation (`event_type="special"`, description "nuke detonates").
_Avoid_: reading a nuke activation with no detonation as proof of cancellation by inference — the cancellation is server-emitted on its own event row (mirrors the **Locking / Missiled** single-source rule); removing the cancelled nuke from the pending queue at the Down tick (it would change nuke-reaction flags and drift seeded games).

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

### Async execution

**Job**:
A long-running unit of background work wrapped in async execution machinery so its caller does not block. Submitted via POST (returns a **Job id**), then polled via GET against the **Job id** until the **Job status** is terminal. Three kinds today: a **Batch run job** (simulate N games, surface progressive aggregates from `BatchSimulator.run_incremental`), a **Save-games job** (replay a list of `(seed, flipped)` pairs from a prior Batch run and persist them as `GameRound` rows), and a **Play Season job** (run all unplayed Rounds in the next N matchdays of a Season — `N=8` for Two Months, `N=None` for Until End of Season). All three share the same per-Job lifecycle (`running` / `complete` / `error`) and the same expiry-asymmetry (`PENDING` after the 1h TTL is indistinguishable from a never-submitted Job id). All three execute via Celery: the UI POSTs at `/matches/simulate-batch/`, `/matches/save-games/`, `/seasons/<id>/play-two-months/`, and `/seasons/<id>/play-until-end/` and the REST POST at `/api/simulate-batch/` all enqueue Celery tasks and return a Job id. Backed by **Celery + Redis** in production; tests run the task synchronously via `CELERY_TASK_ALWAYS_EAGER = True`.
_Avoid_: calling an in-process thread driving any of the above flows a "Job" (pre-API-03 SIM-10 used `_BATCH_JOBS` / `_SAVE_JOBS` for in-process dicts; both patterns are retired by API-03 and the term is now formalised on Celery); calling a single foreground **Round** persistence call a "Job" (that is `simulate_match` / `simulate_single_round_detailed`, transactional inline work).

**Job id**:
The opaque string returned by the POST endpoint and used to poll a **Job**'s status. A Celery task id (UUID) in production; the same string lives in the URL of `GET /matches/simulate-batch/status/<job_id>/` and `GET /api/simulate-batch/<job_id>/`. Expires from the result backend after **1 hour** (`CELERY_RESULT_EXPIRES = 3600`); polling an expired Job id resolves to **Job status** `running` (the Celery `PENDING` fallback for unknown ids — indistinguishable from "never submitted").
_Avoid_: deriving a Job id from inputs (it is random); persisting one (it is ephemeral, lost on backend expiry).

**Job status**:
One of `running` | `complete` | `error`. The canonical surface across both the UI polling JSON and the REST polling JSON. **Mapped from Celery's native task states at the view boundary**: `PENDING` / `STARTED` / `PROGRESS` → `running`; `SUCCESS` → `complete`; `FAILURE` / `REVOKED` → `error`. The mapped vocabulary predates API-03 (SIM-10's `_BATCH_JOBS` dict used the same three values) and is preserved deliberately so the existing polling UI JS keeps working unchanged.
_Avoid_: exposing raw Celery states (`PENDING`, `SUCCESS`, …) in the polling JSON — the mapped values are the public contract.

### Analytics and review

**Round scoreboard**:
The per-player table rendered by both the HTML **Round** detail page (`/matches/game-round/<id>/`) and the **Round report** PDF (RV-03), plus the eventual REST round endpoint. A frozen flat-dict shape (28 keys: identity + survival + scoring + resources + combat extras + support + display-arithmetic helpers) materialised view-side from each `PlayerRoundState` row by `matches/views.py::_player_row`, then handed to the pure aggregation module `matches/round_summary.py` (`team_totals`, `survivor_count`, `team_eliminated`). The PDF deliberately renders a 14-key subset of the same dict; the HTML renders 27 of the 28 keys; the dict is the single source of truth so the two surfaces cannot drift. Distinct from a **Round report** (the PDF *artifact*) and from a **Highlight** (the events-page reel) — Round scoreboard is the per-player table shape both surfaces share, not a render target. The seam contract pins the key set against the temptation to keep two shapes ([`.claude/worktrees/round-analytics-seam-contract.md`](.claude/worktrees/round-analytics-seam-contract.md)).
_Avoid_: reading Round scoreboard as a synonym for **Round report** (the report is the PDF; the scoreboard is the dict-shaped table both HTML and PDF render); keeping a parallel ORM-row render path on either surface — the PDF and HTML must consume the same view-built dict so drift is structurally impossible.

**Highlight**:
An auto-flagged notable moment in a **Round**, surfaced on the events-page "Highlights" tab and persisted on `GameRound.highlights_json` at round completion. Six kinds: a **Nuke** detonation, a **Nuke cancellation**, the round's first **Elimination**, a **team Elimination** (a whole **Team** wiped out — the 10,000-point-bonus moment), the single largest 30-second **scoring burst** (the 60-tick window in which **one Team** scored the most points), and a **Medic reset chain** (a Medic re-**Down**ed before recovering). Each Highlight carries its tick (for `÷2`-to-mm:ss display) and the players/team involved. **Base captures are deliberately *not* a Highlight kind** — they are routine, frequent point-grabs (a dozen-plus per round), so they live in the events-log timeline (filterable by the "Base Capture" type) rather than the highlight reel; their points still count toward the **scoring burst**.
_Avoid_: reading "point swing" as a lead/differential change — it is the gross single-team scoring burst; expecting a per-base-capture Highlight — base captures are surfaced only in the event log.

**Medic reset chain**:
A **Medic** that is **Down**ed **two or more times in one unbroken recovery chain** — re-Downed while still in the **Respawn cooldown** (tagged in the **Reset window**), before ever returning to fully active. The "spawn-camped medic" moment. The chain count is per-recovery: it resets to zero the moment the Medic returns to fully active, so a Medic Downed twice across the round with a full recovery between is **not** a reset chain. Server-detected at the life-loss chokepoint and emitted as a discrete `GameEvent` so the **Highlight** builder reads it single-source (never reconstructed from tag/elimination rows).
_Avoid_: counting the Medic's round-cumulative `times_tagged_in_reset_window` as a reset chain — the chain is one unbroken recovery, not a round total.

**Scoring burst**:
The 30-second (60-tick) sliding window in which a **single Team** scored the most cumulative points — the measure behind the "largest 30-second point swing" **Highlight**. Gross single-team points, *not* a change in the red−blue lead (a deliberate RV-02 choice: a burst is the biggest scoring run, not a momentum reversal).

**Tag ratio**:
A **Player**'s career **Tag**-balance summary: `sum(tags_made) / max(sum(times_tagged), 1)` aggregated across every **Round** the Player appeared in. The career-page analogue of the colloquial "K/D ratio" — "kill" is not domain language here, so the label is **Tag ratio**. Symmetric (Tags landed vs Tags taken), drawn from two stored `PlayerRoundState` counters, and combines cleanly across rounds as sum/sum (not a mean of per-round ratios, which would over-weight low-volume rounds).
_Avoid_: "K/D ratio" / "kill/death" (no domain "kill"); a mean of per-round `tags_made/times_tagged` ratios (correct form is sum-of-numerators over sum-of-denominators).

**Role benchmark**:
The role-conditional distribution of a **Stat** across **Players**, used to answer "how does this Medic's avg points compare to other Medics?" — the HX-02 surface. One population per (**Role**, stat) pair; each data point is one **Player**'s career-average for that stat *when playing that Role* (per-player aggregation, **not** per-round samples — a player with 80 Heavy rounds contributes one data point, same as a player with 5). Only players with ≥ `min_rounds` in the **Role** are included (`min_rounds` is user-configurable on the page, default 5; the threshold filters both the population and the per-player percentile eligibility, so a player below threshold sees their stats but not a delta/percentile). The benchmark surfaces both **mean** and **median** with user-toggleable display; counter-like stats (points_scored, tags_made, …) aggregate as per-round mean over the player's role-rounds, ratio-like stats (accuracy, Tag ratio) aggregate as sum/sum per the **Tag ratio** precedent.
_Avoid_: building the benchmark population from per-round `PlayerRoundState` rows (would over-weight prolific players — each player gets exactly one data point); using "league average" as a synonym (league-wide ignores the **Role** split that the term explicitly fixes).

**Percentile rank**:
The position of a **Player**'s career-average in the **Role benchmark** population for one stat, by the **nearest-rank** convention — `floor(rank / n × 100)` where `rank` is the 1-based ascending position of the player's value in the sorted sample list (the subject's own value counts). Integer 0–100. Pure stdlib (`bisect.bisect_left` on a cached sorted-sample list), deterministic across Python versions, and trivial to assert in tests with hand-computed values. Distinct from the linear-interpolation convention used by `numpy.percentile` / `statistics.quantiles` — the project uses nearest-rank everywhere a percentile is displayed.
_Avoid_: linear-interpolation percentiles (float-precision drift in tests); reading "85th percentile" as "beat 85% of role-mates" (the subject's own value counts, so the player at the maximum is at 100, not at `(n-1)/n × 100`).

**Head-to-head record**:
The aggregated history between two **Teams** — the HX-03 surface at `/matches/h2h/?team_a=<id>&team_b=<id>`. Spans **both** every **Match** whose `{team_red, team_blue} == {team_a, team_b}` **and** every standalone **Round** (no `Match` parent) between them; the two corpora drive two distinct records on the page. **Match record** = W/L/T from `Match.winner` over the H2H Matches only. **Round record** = W/L/T per **Round** over the unified basket (the 2 Rounds of each H2H Match + every standalone H2H Round); a Round's winner is the higher-scoring side, equal scores are a tie. **Side-agnostic Team-id overlap** (the RV-01 precedent — orientation-independent): a Team that played red in one game and blue in another still pairs by Team id, not by **Side**. **Avg score margin** = mean of `(team_a_score − team_b_score)` per **Round** across the unified basket, signed from team_a's perspective. **Avg survivors** = per-team mean of `count(PlayerRoundState.final_lives > 0)` per Round (two numbers — team_a's avg, team_b's avg). **Most impactful player** = cumulative `get_mvp` across every H2H Round each player appeared in, reported one per team (the role-weighted **MVP score** prevents the points-scored Commander bias). A `?provenance=all|real|sim` query param (default `all`) filters by `GameRound.is_simulated` so the surface is forward-compatible with the post-IMPORT-01 **Actual game log** corpus. Distinct from a **Player head-to-head record** (HX-04), which fixes two **Players** rather than two Teams and gates on per-Round opposite-team membership.
_Avoid_: building the Match record over standalone Rounds (standalone Rounds have no `Match.winner`); using **Side** (red/blue) to pair the two teams — pair by Team id; absolute (unsigned) margin (it drops the directional signal the signed-from-team_a view exposes); points-scored (instead of MVP) for "most impactful" — it biases toward the Commander role.

**Player head-to-head record**:
The aggregated direct-rivalry history between two **Players** when they appeared on opposite teams — the HX-04 surface at `/matches/h2h/player/?player_a=<id>&player_b=<id>`. Distinct from a **Head-to-head record** (HX-03), which is between two **Teams**: a Player head-to-head is a per-**Round** view gated by **opposite-team membership**, not Team-id pairing. **Corpus** = every **Round** in which both Players appeared with different `PlayerRoundState.team_color`; same-team Rounds (where both Players were on the same `team_color`) are **excluded entirely** (no "same-team" count, no fallback display) — the relationship being measured only exists when they are facing each other. **Round record** = per-Round W/L/T from player_a's team's perspective (the team player_a sat on for that Round). **Avg score margin** = mean of `(player_a_team_score − player_b_team_score)` per Round, signed from player_a's perspective. **Avg tags vs tagged** = two per-Round means: `mean(GameEvent.objects.filter(actor=player_a, target=player_b, event_type="tag"))` per Round, and the symmetric `player_b → player_a` mean (one number per direction, two together). A **`?role=<role>`** query param (default *any*) filters the corpus to Rounds where **both** Players played that **Role** — the "both" semantic is intentional (the page is about how this matchup plays out at a specific role pairing); since a Round binds each Player to one Role each, "Alice as Commander vs Bob as Heavy" is a different question that the v1 dropdown does not answer (deferred to a future per-Player-role-pair filter if needed). `?provenance` and `?from`/`?to` mirror the **Head-to-head record** filters exactly. The surface ships the same chart pair (margin over time + cumulative W/L), a per-Round detail list, a per-`Role` breakdown, and a per-`Arena map` breakdown.
_Avoid_: counting same-team Rounds in the basket (the "opposite teams" gate is the defining property — including them mixes two different relationships); deriving the per-Round tag counts from `PlayerRoundState.tags_made` (that counter aggregates Tags against **all** enemies; the directional A→B / B→A split lives on `GameEvent.actor`/`target`); using `Player.preferred_roles` for the `?role` filter (that is the player's **profile** preference, not the **Role** they played that Round — filter on `PlayerRoundState.role`); pairing two Players by their respective **Team** (a Player may have switched teams between Rounds, and same-Team Rounds are excluded anyway by the opposite-team gate, not by Team id).

**Statistical feat**:
A single (**Player**, **Round**) performance notable enough to list on the Statistical Feats screen — the reference product's "single-game feats" analogue (LG-06e). A (Player, Round) qualifies either by **crossing a per-game bar** for a tracked feat (a triple-**Nuke** game, a **Medic** shutout, a perfect-accuracy **Heavy** round, or a high single-game count of **Tags** / points / **MVP** / resupplies / **Missiles**) **or** by being a **Season-best** — the single highest MVP / points / Tags performance in scope, always listed even when it crosses no bar and tagged "season best". Each qualifying row shows that Round's box-score line (the same per-player stat set as the **Round scoreboard**) plus the opposing **Team**, the row's per-**Round** result (W/L/T — the single Round's higher-scoring side, *not* the **Match** outcome), and its **Season**, deep-linking to the **Round**. One row per qualifying (Player, Round); a single Round can carry several feat badges for the same player. A **comeback win** (a **Team** that won the **Match** after trailing in round 1) is a *team* event, not a per-player feat — it is surfaced in a separate "Team feats" section, not in the per-player feed.
_Avoid_: the pre-LG-06e "category best" model (one row = the single best of each feat kind, superseded by this per-game feed); reading the per-**Round** **Result** as the **Match** outcome; listing a standalone **Round** (the feed is **Season**-scoped via the Round's `Match.season`).

### League and seasons

**League**:
The persistent container that owns one or more **Seasons** — the user-facing "competition" in **single-player league mode**. A League has a name, a `mode` (`sandbox` for the existing pre-LG-01 flows / `league` for the new single-player surface / `multiplayer` reserved for deferred Phase 6 work), a state (`active` / `archived`), and a chain of Seasons (Season FK → League). Persistent across cycles: when a Season completes, the next Season inside the *same* League inherits the team list (the seed of multi-season continuity — manager identity is layered on top in CAR-01). A **Team** is **not** owned by a League — Teams stay global so the existing sandbox flows (LG-00 generation, LG-00b roster import, per-team pages) keep working unchanged; a Team can be enrolled in multiple Leagues' Seasons simultaneously, with enrollment tracked on each Season's M2M. Owner / per-User scoping is **deferred to UX-01 + CAR-01** (there is no User model yet); for now Leagues are global.
_Avoid_: treating a League as a single competition (it is the *chain* — one League may span many Seasons across many years); treating a League as the unit that owns Teams (Teams are global, enrollment lives on the Season's M2M); equating League with Tournament (a Tournament — LG-02 — is a bracketed format that *runs inside* a Season, not its own container).

**Season**:
One cycle within a **League** — the bounded competition that produces a **Standings** table from its completed **Matches**. Has a name, start/end dates, a FK to its **League**, an M2M of enrolled **Teams** (the active roster for *this* cycle), a state (`draft` / `active` / `completed`), and a `schedule_format` enum (v1 only ships `single_round_robin`; the field is extensible for future formats). A **Match** is attached to at most one Season via a nullable FK; standalone **Rounds** (no `Match` parent) are **not** part of Season standings. Pre-LG-01 Matches stay `season=NULL` ([ADR-0004](docs/adr/0004-simulation-data-is-disposable.md) disposable-data precedent — no backfill).
**The scheduling unit is the Round, not the Match** — the two **Rounds** of one **Match** are scheduled separately in time (round 1 early in the Season, round 2 later) so the team roster can change between them; the per-Match colour swap (`team_red` plays red in round 1, blue in round 2) is preserved verbatim from `BatchSimulator.simulate_match`. A Match is therefore **partial-completable**: `is_completed=True` is set only after the second Round persists; round 1 alone leaves the Match with `*_round1_*` populated and `is_completed=False`. The schedule itself is **computed on demand** (no `ScheduleEntry` rows pre-created — pure module `matches/schedule_generator.py` returns the fixture list deterministically from the enrolled team ids + format); Match rows are find-or-created at "Play Round" time, keyed Side-agnostically by `(season_id, frozenset({team_red_id, team_blue_id}))`. New simulator entry point `BatchSimulator.simulate_scheduled_round(season, team_a, team_b, round_number, *, arena_map=None) -> GameRound` (`@transaction.atomic`) runs **one** Round of an existing-or-created Match and is the sole writer for Season Matches; the existing `simulate_match` stays as the sandbox-Match entry point (sandbox Matches have `season=NULL`).
_Avoid_: counting standalone Rounds toward Standings; treating Season as a tournament (Tournament is LG-02 — bracketed, advancement; Season is a flat league with cumulative standings); treating Season as the persistent container (Season is one cycle; the persistent container is the **League**); using `simulate_match` for a Season Match (it would run both Rounds atomically, defeating the round-keyed scheduling); pre-creating `Match` rows at Season activation (Match rows are find-or-created at "Play Round" time — the schedule itself is recomputed each render).

**Standings**:
The ranked table of enrolled **Teams** within one **Season**, computed from that Season's completed **Matches** only — `is_completed=True`, `season=<this>`. **Match-keyed**: each Match contributes one outcome row per team (W / L / T) — Team's W = `Match.winner_id == team.id`, T = `Match.winner_id IS NULL`, L = otherwise. League points are **3W / 1T / 0L** applied to Match outcomes (not Round outcomes); "round wins" and "total score" are **secondary columns and tiebreakers**, not part of the points formula. **Tiebreak ladder** when two or more teams are tied on league points: (1) **Round wins** — sum of `Match.red_rounds_won` / `blue_rounds_won` over the team's Season Matches (each Match contributes 0 / 1 / 2 to one team's total), higher wins; (2) **Total score** — cumulative Match points scored *for*, summed across the team's Season Matches (the team's side of `Match.red_total_points` / `blue_total_points`, which includes the 10,000-point team-elim bonus), higher wins; (3) **alphabetical by Team name** — stable, deterministic, cheap (mirrors the HX-02 / LG-00c deterministic-tiebreak precedent).
_Avoid_: counting league points per **Round** (3-per-Round would imply up to 6 points per Match — Standings is Match-keyed; round wins is a tiebreaker, not a point source); including standalone Rounds (no `Match.winner`); including incomplete Matches (`is_completed=False` rows are excluded entirely from the table); using point differential or head-to-head as a tiebreaker (PLAN names round wins + total score only; head-to-head is the separate HX-03 surface).

**Matchday**:
One slot in the `generate_schedule(...)` mirror pattern. The 1-based ``matchday`` field on `ScheduleFixture`. Multiple fixtures share a matchday — one per pairing (round-1 matchdays `1..N-1` carry the first leg; round-2 matchdays `N..2*(N-1)` carry the mirrored leg). The scheduling unit is the **Round**, not the matchday — a matchday holds multiple `ScheduleFixture` rows. The **Play One Week** action plays every Round in a single Matchday; **Play Two Months** plays the next 8 Matchdays; **Play Until End of Season** plays every unplayed Round regardless of Matchday. The calendar date of a Matchday is `Season.start_date + (matchday - 1) * 7 days`.

**Current team**:
The **Team** within a **League** that the user manages — the one whose **Players** they can edit. Persisted as `League.current_team` `ForeignKey(Team, null=True, blank=True, on_delete=SET_NULL, related_name="managed_in_leagues")` on the `League` model. Set by **LG-01b** at League create time to the alphabetically-first Team produced by `_generate_teams` (`league.current_team = sorted(created_teams, key=lambda t: t.name)[0]` inside the `@transaction.atomic` body, between the `League.objects.create(...)` and `Season.objects.create(...)` calls). `SET_NULL` on **Team** delete means deleting a Team nulls the FK on every League pointing at it without cascading the League out of history. **LG-01g** uses `Current team` as the default target of the **LG-01f** sidebar's TEAM > Schedule entry via the order-(a)-(b)-(c) fallback chain (`league.current_team` if enrolled in the displayed **Season** → first alphabetical in-Season Team → `None`/disabled); on a hand-typed `team_schedule` URL the picked Team is whatever the URL says, and `Current team` is shipped in the view context as a forward-compat "your team" hint on the picker dropdown. The reverse accessor `team.managed_in_leagues` is plural because a single Team may plausibly be the `Current team` of multiple Leagues (no DB-level uniqueness constraint). **CAR-01** (PLAN.md, deferred) will replace the LG-01b auto-set with manager-driven assignment — at that point `Current team` becomes the manager's Team selection rather than an alphabetical pick.
_Avoid_: equating **Current team** with "the manager" (the manager identity itself is **CAR-01** territory — `Current team` is the *Team* the manager controls, not the manager); assuming uniqueness (`related_name` is plural — a single Team can be the `Current team` of multiple Leagues, and the LG-01g sidebar fallback chain depends on this being unconstrained); reading `Current team` as the same Team for every **Season** in a League (it is a single FK on the League, but it is *recomputed* per render via the LG-01g order-(a)-(b)-(c) chain to defend against admin actions that remove the Team from the displayed Season's M2M — so the *effective* Team picked for the sidebar link may differ from `league.current_team` on Seasons where the latter is no longer enrolled); treating LG-01g's auto-set as a permanent assignment (it is the LG-01b create-time default; the user — or CAR-01 later — may overwrite `league.current_team` to any Team).

**Team schedule**:
The per-**Team** view of one **Season**'s schedule — the LG-01g surface at `/leagues/<league_id>/team_schedule/<team_id>/`, occupying the LG-01f sidebar's TEAM > Schedule slot. League-scoped URL with the Season resolved implicitly from `League.active_season` (falling back to the most-recent completed Season, mirroring the LG-01c `displayed_season` chain). Renders two columns — **Upcoming Games** (unplayed `(fixture, round_number)` pairs from `generate_schedule(...)` filtered to fixtures involving this Team) and **Completed Games** (one row per persisted `GameRound` for a Match where `team ∈ {team_red, team_blue}`) — at per-**Round** granularity, so the two **Rounds** of one **Match** appear as two separate entries on their respective matchdays. A partial Match (Round 1 played, Round 2 not) naturally splits: its Round 1 lands in Completed, its Round 2 in Upcoming. Each entry renders as `(R) <red_team> VS (B) <blue_team>` with the per-Round Side annotation; for Upcoming Round 2 entries the schedule's `team_a` / `team_b` are swapped view-side so the displayed Sides reflect the per-**Match** colour swap (the round-2 simulator call reverses team args, so the displayed Sides match what will actually be persisted). The Team picker (top-right dropdown) is scoped to the displayed Season's enrolled Teams; switching navigates to `/leagues/<league_id>/team_schedule/<new_team_id>/`. Distinct from the **League schedule** (`/seasons/<id>/schedule/`, all fixtures across all Teams).
_Avoid_: keying the URL on `season_id` (the URL is League-scoped; the Season is implicit); rolling Round 1 + Round 2 into one row (per-Round granularity preserves the Side annotation and the W/L per-Round; partial Matches lose their natural split otherwise); reading Round 2 fixture Sides off `ScheduleFixture.team_a` / `team_b` directly (the schedule normalises `team_a = min(pair)` regardless of round — Round 2 Sides must be flipped view-side to match the per-Match colour swap).

**Map mode**:
The per-**Season** enum on `Season.map_mode` ∈ `{none, single, random_per_round}` determining how each **Round**'s **Arena map** is chosen. `none` (default) runs every Round on the **3-zone fallback** — the LG-01d behaviour today, with no `arena_map` attached to the Round. `single` uses one fixed Arena map for every Round of the Season. `random_per_round` draws per Round from `Season.map_pool` deterministically by fixture identity — Season id, matchday, round number, and both team ids — see **Per-fixture map resolution**. Locked at create-**League** time via the LG-01b `CreateLeagueForm` (the form gains `map_mode` + `map_pool` fields, with 3 cross-field `clean()` rules enforcing the mode-vs-pool-count invariant); mid-League changes are admin-only via Django admin's `SeasonAdmin`. Distinct from a `GameRound.arena_map` — that field is the *result* of the per-fixture resolution, persisted on the Round as a row attribute by `_flush_to_db`, NOT the per-Season configuration.
_Avoid_: confusing **Map mode** with `schedule_format` (the latter controls fixture generation — `single_round_robin` is the only v1 value; **Map mode** controls per-Round map choice and is orthogonal); reading `GameRound.arena_map IS NULL` as proof the Season is `map_mode=none` (a `random_per_round` Season with an admin-deleted pool entry resolves defensively to `None` for that one fixture too — the Round-level `arena_map` is a per-fixture outcome, not a per-Season configuration); treating the third "per-sub-league rotation" enum value as reserved at LG-01j (deferred to SUB-01 post-CAR-03 — no third enum value is reserved, the migration will add it when SUB-01 lands).

**Map pool**:
The M2M `Season.map_pool` from a **Season** to **Arena map** (`core.models.ArenaMap`). The form-side picker is restricted to Arena maps with at least one confirmed `MapZoneConfig` via the existing `matches.forms._maps_with_confirmed_config()` helper (also used by `MatchSetupForm` / `SingleRoundSetupForm`) — half-built maps without a confirmed config are excluded. Frozen at **Season** activation via the JSONField snapshot `Season.starting_map_pool_ids_json` (sorted-ascending list of `ArenaMap` ids — mirrors the LG-01 `starting_team_ids_json` snapshot precedent and the "snapshot at activation" pattern for ensuring deterministic mid-cycle reads). Pool-size constraints by **Map mode**: must be empty when `none`, exactly 1 when `single`, ≥1 when `random_per_round`. The simulator never reads the live `Season.map_pool` M2M during play — it reads the frozen snapshot via `season.starting_map_pool_ids_json`, so admin-side pool edits to an `active` or `completed` Season don't drift the schedule's map sequence. Cleared / re-set by the LG-01b create form at League create time and by LG-01e's `next_season` carry-forward only — `next_season` reads `latest_completed.starting_map_pool_ids_json` (the snapshot) NOT `latest_completed.map_pool` (the live M2M), then `Season.objects.filter(id__in=pool_ids)` rehydrates the new Season's pool (admin-deleted rows silently drop out). A pool entry deleted after activation resolves to `None` (3-zone fallback) at simulation time — defensive, not a crash; the existing `arena_map=None` simulator default covers this case verbatim.
_Avoid_: reading the live M2M to determine which maps a Round was simulated with (use `GameRound.arena_map` on the Round itself, or `Season.starting_map_pool_ids_json` for the per-Season snapshot — the live `Season.map_pool` M2M may have drifted via admin edits); treating a `[]` snapshot as "pre-activation" (pre-activation is `starting_map_pool_ids_json IS NULL`; activated-with-no-maps is `[]` — the two states are deliberately distinct); modifying the M2M to "change the schedule's map sequence on an active Season" (the snapshot is the source of truth post-activation; admin pool edits to an active Season change only the read-only `Map mode`-label display, not the simulator's per-fixture map draw).

**Per-fixture map resolution**:
The deterministic per-**Round** **Arena map** draw for `Map mode = random_per_round`. Performed by the module-level helper `matches.tasks._resolve_fixture_map(season, fixture, pool_by_id) -> ArenaMap | None`. Seeds a fresh `random.Random` instance per fixture with the byte-locked string `f"{season.id}|{fixture.matchday}|{fixture.round_number}|{fixture.team_a_id}|{fixture.team_b_id}"` (5 components, pipe-separated, no spaces, exact order), then `.choice(pool_ids)` picks one id from the frozen snapshot. Re-runs, task resumes, and **Replays** of the same fixture pick the same map (replay-faithful per fixture identity). Extends the SIM-07 / SIM-08 contract from "same seed + **Orientation** + rosters + map ⇒ same Round" to "same fixture identity ⇒ same map ⇒ same Round" — fixture identity is now the upstream replay anchor for `random_per_round` Seasons. Lives in `matches/tasks.py` (NOT `matches/season_dashboard.py`, where LG-01h pinned a frozen no-Django import allowlist that would be broken by even a duck-typed helper). Consumes a Python stdlib `random.Random` instance separate from the simulator's RNG, so map choice does NOT perturb the SIM-07 seed chain (the simulator's `rng_seed` is unchanged and deterministic across `random_per_round` Seasons). Called from both the async `play_season_task` (Celery) and the synchronous `play_week` (in-request) paths via the same module-level entry point — both resolve the `pool_by_id` dict ONCE upfront via `ArenaMap.objects.in_bulk(starting_map_pool_ids_json)` (one ORM query regardless of fixture count) and pass it into the per-fixture helper call.
_Avoid_: re-using the simulator's `rng_seed` to derive the map (the two RNGs are deliberately independent so map identity is fixture-keyed, not seed-keyed — changing the per-Round simulator seed must not change which map that Round runs on); calling the helper inside `select_play_fixtures` or `matches/season_dashboard.py` (the helper takes a `Season` instance + `pool_by_id` dict — the LG-01c pure module's no-Django allowlist would be broken by importing `random` plus the implicit `Season` / `ArenaMap` types); confusing the per-fixture seed with the simulator's `rng_seed` (the per-fixture seed is a string consumed by `random.Random.__init__`'s seed hashing; the simulator's `rng_seed` is a small int passed to `random.seed()` — distinct mechanisms per [ADR-0005](docs/adr/0005-rng-seed-not-state-for-replay.md)).

**Per-10-minute rate**:
The third option of the LG-06d **Player Stats** rate toggle (alongside *Totals* and *Per Game*) — the laser-tag analogue of ZenGM's "Per 36". A summed count stat is normalised to a 10-real-minute window of the player's *actual playing time*: `count × 600 / total_uptime_seconds`, where `total_uptime_seconds` is the player's summed per-**Round** survival time across the scope being viewed (each Round's survival time is `min(was_eliminated_at, 1800) ÷ 2`, the same display-seconds derivation the **Uptime breakdown** uses; ÷2 is the TIME-01 tick→second boundary). 600 s = 10 min. The denominator is *uptime*, not scheduled round length, so an early-eliminated player's rate is not deflated by dead time — the intent is "production per 10 minutes of being in the game". 10 min was chosen over ZenGM's 36 because a 15-minute **Round** means most players survive at least that far, keeping the extrapolation modest. Applies **only** to the summed count columns; the averaged/ratio columns (**MVP score**, accuracy %, tag ratio, survival) are never rate-transformed. A player with zero total uptime defensively yields `0.0`. Sorting operates on the rate-adjusted (displayed) value, not the underlying total.
_Avoid_: using scheduled round length (`GP × 15 min`) as the denominator (that makes Per-10 a constant ×0.667 of Per Game — no new information); rate-transforming MVP / accuracy / tag ratio / survival (they are already per-Round means or ratios); reading "Per 10" as 10 *ticks* (it is 10 real minutes = 1200 ticks = 600 display-seconds).

**Career view (league-scoped)**:
The "Career" entry in the LG-06d `?season=` selector on the season-derived league screens — an aggregation scope that sums across **every Season of the current League** (not a single **Season**, and not all-time across all Leagues). Distinct from the global per-**Player** career page (HX-01, `/players/<id>/stats/`) which spans every Round the player ever played in any context; the league Career view is bounded to the one League whose screen is being viewed. Selecting Career swaps the screen's source queryset from `…match__season = <chosen season>` to `…match__season__league = <this league>` — the existing pure aggregation modules are reused unchanged (they consume a flat list of per-Round dicts and are indifferent to whether it spans one Season or all). The default scope when no `?season=` param is present remains the screen's `displayed_season` (active Season, else most-recent completed), **not** Career.
_Avoid_: aggregating all-time across Leagues (that is the HX-01 global career page — the league Career view is League-bounded); treating Career as the default (the default is `displayed_season`); reading Career as a separate UI control (it is one option inside the single `?season=` selector, mirroring ZenGM folding "Career Totals" into the season dropdown).

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

- **"team_elimination" event** — `GameEvent.EVENT_TYPES` declares a `team_elimination` choice, but the simulator **never emits it** (it is dead since at least the BatchSim consolidation). Resolved 2026-05-21 by RV-02: the **team Elimination Highlight** is derived from `GameRound.red_team_eliminated` / `blue_team_eliminated` + `eliminated_at`, **not** from a `team_elimination` event row. Do not assume a `team_elimination` `GameEvent` exists; do not resurrect it for RV-02 (out of scope).
- **"point swing" vs "scoring burst"** — resolved 2026-05-21 by RV-02: the "largest 30-second point swing" **Highlight** flags the gross **Scoring burst** (most points by *one* Team in any 60-tick window), **not** the largest change in the red−blue lead/differential. "Swing" was ambiguous; the chosen meaning is the scoring run, not a momentum reversal.

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