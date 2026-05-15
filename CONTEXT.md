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
A shot the defender may fire back after being **Tagged** or **Missed**.

**Miss**:
A fired **Shot** that does not connect.

**Nuke**:
The Commander **Special** that, after a **Fuse window**, eliminates lives across the enemy team unless cancelled.

**Fuse window**:
The delay between a **Nuke** being fired and it detonating, during which **Downing** the firing Commander cancels it.

**Rapid Fire**:
The Scout **Special** that removes the shot-rate cooldown while active.

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
The engine that advances a round tick-by-tick; the project deliberately has two (see [ADR-0002](docs/adr/0002-two-simulation-engines.md)).

**Action**:
The single choice a player makes per tick (tag, move, hide, capture, special, resupply, missile, request resupply), chosen by weighted random.

**Weight**:
The per-action numeric likelihood, derived from role, situation, and **Stats**, that drives the random **Action** choice.

**Goal cell**:
The **Cell** a player is currently navigating toward, chosen from their **Action**, **Role**, and **Player memory**.

**MVP score**:
A role-weighted per-player round score emphasising that role's primary contribution; display/ranking only, distinct from points.

**Player memory**:
A player's imperfect, decaying knowledge of other players' last-known **Cells**, refreshed by LOS and **Broadcasts**; replaces perfect knowledge for goal selection (but not for resupply resolution).

**Broadcast**:
A team- or arena-wide event (nuke activation, score update, medic-under-fire) that updates listeners' **Player memory**.

**Staleness**:
The age past which a **Player memory** entry is no longer trusted; threshold depends on the remembered player's **Role**.

## Relationships

- A **Match** has exactly two **Rounds**; a **Round** belongs to one **Match** (or stands alone).
- A **Team** fields a six-player **Roster**; each **Player** has one **Role** for the round.
- A **Shot** may become a **Hit**; a **Hit** on a valid enemy is a **Tag**; a **Hit** that empties **Shields** is a **Down**; losing the last **Life** to a Down is **Elimination**.
- A **Down** starts the **Respawn cooldown** = **Not-targetable window** then **Reset window**.
- Scoring accrues **SP**; spending **SP** activates a **Special** (**Nuke** for Commander, **Rapid Fire** for Scout).
- A **Round** runs on an **Arena map** (→ **Cells**, **Sight lines**, **Bases**, **Elevation**) or on the **3-zone fallback**.
- **LOS** between **Cells** gates **Tag** eligibility; **Movement adjacency** (≠ LOS) gates stepping between cells.
- A **Player** picks an **Action** by **Weight**, then moves toward a **Goal cell** informed by **Player memory**.

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
- **"seconds_active stores ticks vs seconds"** — re-resolved 2026-05-15 by TIME-01 (supersedes the earlier "stores seconds" resolution): the uptime fields are renamed `ticks_*` and store **ticks**; the survived sentinel is `1801`; `GameEvent.timestamp`, constructor args, and the REST API are all ticks. Seconds are a display-only `÷2` at HTML/CLI output. Decision and the API-returns-ticks consequence recorded in [docs/adr/0001-time-unit-seconds-now-tick-native-later.md](docs/adr/0001-time-unit-seconds-now-tick-native-later.md).