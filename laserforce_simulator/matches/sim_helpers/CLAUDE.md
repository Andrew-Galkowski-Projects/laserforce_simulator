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
- scored positioning-goal draw, held goals, the tunable formula (MOVE-06+07) — `docs/agents/matches/sim-helpers.md#goal_scoringpy`
- Player Modes: Mode state, switching triggers, goal rules, the teamwork closeness term, the weight nudge (MECH-07) — `docs/agents/matches/sim-helpers.md#modespy`
- shot resolver, hit roll, follow-ups, reactions, Overwatch shot — `docs/agents/matches/sim-helpers.md#shotpy`
- `PlayerState` fields, transient caches, uptime counters, baked stats — `docs/agents/matches/sim-helpers.md#player_statepy`
- action weights, weight layout, role baselines, weight-safety rules — `docs/agents/matches/sim-helpers.md#weightspy`
- movement, A*, path and goal commitment, goal selection, memory — `docs/agents/matches/sim-helpers.md#pathfindingpy`
- Support-survival switches: target priority, hidden support cells, the Medic holding position, the Ammo hiding freely, teamwork call-outs (2026-11-06); CAL-03's permanent support rules (doubles window, idle trade, Ammo anchor, Medic back, HOLD `hold_allows`, the support link `medic_with_ammo`); CAL-04's visit pacing constants, Join and the Ammo leash, `SUPPORT_ROLES` / `partner_role` / `own_teammate` — `docs/agents/matches/sim-helpers.md#support_survivalpy`
- CAL-03's Resupply visits (`visit_step`, the wait / go / at phases, the cooldown, partner follows), the 1-3 lives call-out, the backline position (#591); CAL-04's visit end reasons (`VisitEnd`), the Critical visit cooldown, Satisfied, the doubles counted on a visit, `is_critical`, `nearest_cell` / `manhattan` / `chebyshev` / `sees` — `docs/agents/matches/sim-helpers.md#resupply_visitpy`
- Nuke arming: the 20 SP gate, the Commander hides while the fuse runs, the distance and enemy-3-hit-down gates (switches, off), no resupply while arming, the per-nuke SP bank draw, the after-Down boost (CAL #558, 2026-11-06/07) — `docs/agents/matches/sim-helpers.md#nuke_armingpy`
- Engagement switches: the defender learns the attacker at every Hit on a map (CAL-02 D4, 2026-11-08); the Attack table for every role and the Medic's tag weight are under `modes.py` and `weights.py` — `docs/agents/matches/sim-helpers.md#engagementpy`
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
