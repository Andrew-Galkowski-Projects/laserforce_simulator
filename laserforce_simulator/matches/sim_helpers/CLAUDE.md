# matches/sim_helpers

Helper modules for `BatchSimulator`, the sole engine. Mostly pure Python, Django-free;
`map_loader.py` alone lazy-imports the ORM. A module change keeps its section in
`docs/agents/matches/sim-helpers.md` true in the same commit.

This file is the **index**. Read the section a pointer names before touching that module.
Paths are relative to the repo root.

- GameEvent dict shape, the 13 event verbs, null-object log — `docs/agents/matches/sim-helpers.md#event_logpy`
- `RoundContext`, the per-round mutable bundle — `docs/agents/matches/sim-helpers.md#round_contextpy`
- life loss, `record_down`, medic-reset chain, nuke cancellation — `docs/agents/matches/sim-helpers.md#downpy`
- Follow rule: Trails, Chases, `may_tag` (MECH-10) — `docs/agents/matches/sim-helpers.md#follow_rulepy`
- Downed option: Stay hidden / Shuffle to cover / Make a trail, the tunable formula (MOVE-08) — `docs/agents/matches/sim-helpers.md#downed_optionpy`
- shot resolver, hit roll, follow-ups, reactions, Overwatch shot — `docs/agents/matches/sim-helpers.md#shotpy`
- `PlayerState` fields, transient caches, uptime counters, baked stats — `docs/agents/matches/sim-helpers.md#player_statepy`
- action weights, weight layout, role baselines, weight-safety rules — `docs/agents/matches/sim-helpers.md#weightspy`
- movement, A*, path and goal commitment, goal selection, teamwork bias, memory — `docs/agents/matches/sim-helpers.md#pathfindingpy`
- shot cooldown, tag / resupply target choice — `docs/agents/matches/sim-helpers.md#mechanicspy`
- `plan_action`, line of sight, windowed walls, base capture, missile lock — `docs/agents/matches/sim-helpers.md#combatpy`
- role stats, max lives / shots, special cost — `docs/agents/matches/sim-helpers.md#role_constantspy`
- tick constants, respawn, endgame rush, staleness — `docs/agents/matches/sim-helpers.md#time_constantspy`
- MVP formula — `docs/agents/matches/sim-helpers.md#score_calculatorpy`
- Highlights — `docs/agents/matches/sim-helpers.md#highlightspy`
- round report PDF and charts — `docs/agents/matches/sim-helpers.md#pdf_reportpy--pdf_chartspy--round_seriespy`
- `MapContext` — `docs/agents/matches/sim-helpers.md#map_contextpy`
- pending missile / nuke / follow-up / reaction queues, their drain, parallel batch workers — `docs/agents/matches/sim-helpers.md#pending_eventspy`
- spawn cells — `docs/agents/matches/sim-helpers.md#spawn_assignerpy`
- resupply requests, Stress failure, combo resupply — `docs/agents/matches/sim-helpers.md#resupply_queuepy`
- map loading, reachable spawn pools, zone from cell — `docs/agents/matches/sim-helpers.md#map_loaderpy`
