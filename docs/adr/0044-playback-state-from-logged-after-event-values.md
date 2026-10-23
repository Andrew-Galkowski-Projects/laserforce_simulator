# Playback state is computed on the server from the after-event values the event log already records

**Status:** Accepted (ANG-06f grill, 2026-10-23).

## Context

**Playback** shows each player's live score, **Lives**, **Shots**, missiles and **SP** at any
**Tick** of a Round. The Django page (`templates/matches/game_round_events.html`, `pbApply`)
builds that state in the browser. It resets every player to their starting lives and shots and
re-applies every event up to the cursor, using combat rules copied into JS (`MAX_SH`,
`SHOT_POWER`, `MAX_MSL`, a 20-point tag loss, a 3-shot base-capture cost).

A probe during the ANG-06f grill ran that replay, ported faithfully to Python, over 5 seeded rounds
(60 player-rows, three runs). It compared the end state with the persisted `PlayerRoundState`. The
two matched exactly for score in 45 rows, lives 34, shots 12 and missiles 47. The copied rules had
drifted from the engine:

- resupplies read a metadata key the engine never writes (`target_*_resupplied`; the events carry
  `amount`);
- no −100 for being missiled;
- no team effects from the medic, ammo or nuke specials (`special.targets[]`);
- Ammo charged for shots it never spends.

Every event's `metadata` already carries the post-event values: `actor_lives` / `actor_shots` /
`actor_points` and `sp` for the actor, the matching `target_*` values for the target, and
`lives` / `shots` / `points` for each `special.targets[]` entry. A replay that reads those values,
counting only missiles (one per `locking`, one more per `missiled` hit) and eliminations
(`elimination` events), matched **60/60 on every field**. LFstats takes the same approach: it
precomputes per-player state snapshots on the server and the client looks up the latest one by
binary search.

## Decision

Playback state is a list of **Playback snapshots** per player, computed on the server by the pure
`matches/playback.py::round_playback(game_round)`. It is served at `GET /api/rounds/<id>/playback/`.
Each snapshot is **read from the logged after-event values**, never re-derived from combat rules.
There is a tick-0 opening snapshot per player, then one at each tick where that player changes.
The client re-applies no rules: for each player it looks up the latest snapshot at or before the
cursor tick.

A seeded pytest pins the **final-state invariant**: each player's last snapshot equals their
Round scoreboard row (score, lives, shots, missiles, SP, eliminated, tags / misses). An engine
change that alters a player's state without logging it fails that test.

## Consequences

- No combat rules are copied into TypeScript, and the Python copy is limited to the missile count.
- The snapshot mirrors what the engine persisted, bugs included. Today a hit missile is spent
  twice (MECH-16), so a player can show −1 missiles. When MECH-16 is fixed, `round_playback`
  drops its matching −1 in the same change.
- MECH-15's planned tick-0 `game_start` event will seed the opening snapshot on the server, with
  no change to the client.
- The One Week Live screen (`play_week_live.html`) still carries its own copy of the JS engine. Its
  Angular migration should consume `round_playback` rather than port that copy.
- Rejected: porting `pbApply` to TypeScript (keeps two drifting rule copies); replaying the rules
  in Python (a second engine that must track every mechanic change).
