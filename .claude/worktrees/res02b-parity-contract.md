# RES-02b — Chart Parity Contract (server snapshots + client refactor)

Extends the RES-02 seam contract. **Supersedes** RES-02's restrictive "MUST NOT carry sp" rule on miss / resupply / combo_resupply / movement / elimination events — they now carry the universal metadata too.

Branch: `res-02-sp-timeline` (already current).

---

## 1. Server metadata convention (universal)

For **every** `event_log.append({...})` site in `matches/simulation.py`, `matches/sim_helpers/combat.py`, and `matches/sim_helpers/resupply_queue.py`:

### Actor block — when the event has a defined actor (always, in practice):
```
"actor_role":   str  # the actor's role
"actor_shots":  int  # post-event final_shots
"actor_lives":  int  # post-event final_lives
"actor_points": int  # post-event points_scored
"sp":           int  # post-event final_special (the RES-02 key; now universal)
```

### Target block — when `target_id is not None`:
```
"target_role":   str
"target_shots":  int
"target_lives":  int
"target_points": int
```

### Multi-target block — for the THREE multi-target `event_type="special"` rows in `simulation.py::_use_special` (medic team-heal, ammo team-ammo) and `simulation.py::_complete_nuke`:
```
"targets": [
    {"pid": int, "shots": int, "lives": int, "points": int},
    ...  # one entry per affected non-actor player
]
```
- For the medic team-heal `special`: include every same-team alive teammate the heal touched.
- For the ammo team-ammo `special`: include every same-team alive teammate the resupply touched.
- For the nuke detonation `special`: include every alive opposing player (the nuke's blast radius covers the whole team).
- The actor itself is NOT in `targets` (their state is in the actor block).

### Event-specific extras
Keep all existing event-specific keys: `is_reaction`, `is_follow_up`, `chain`, `overwatch`, `fires_at`, `base_id`, `amount`, `medic_tag`, `ammo_tag`, `elimination_action`, `actor_role` (already in actor block but harmless to duplicate via the existing inline literal — drop the duplicate when convenient), etc. These are extras, not part of the universal block.

### Implementation hint — helper functions
Add module-level helpers to dedupe the metadata-building boilerplate:

```python
# simulation.py module level
def _actor_meta(actor):
    return {
        "actor_role": actor.role,
        "actor_shots": actor.final_shots,
        "actor_lives": actor.final_lives,
        "actor_points": actor.points_scored,
        "sp": actor.final_special,
    }

def _target_meta(target):
    return {
        "target_role": target.role,
        "target_shots": target.final_shots,
        "target_lives": target.final_lives,
        "target_points": target.points_scored,
    }

def _build_meta(actor, target=None, **extras):
    md = _actor_meta(actor)
    if target is not None:
        md.update(_target_meta(target))
    md.update(extras)
    return md
```

Apply the same pattern in `combat.py` (locally or via import — duplicating ~15 lines is acceptable). `resupply_queue.py`'s `combo_resupply` event has no `target_id` per the current emit shape, but treats the requestor as the target; add `target_shots`/`target_lives`/etc. via `_target_meta(requestor)` even though `target_id` is None in the event row — clients keying off `meta.target_*` work the same.

Actually, for `combo_resupply`: change the emit to set `target_id = requestor.player_id` so the data shape is uniform with single-resupply events. (This is a small `event_log.append` change — `target_id: None → target_id: requestor.player_id`.)

---

## 2. Removed restriction (supersedes RES-02 §1 "MUST NOT carry")

The RES-02 seam contract listed `miss`, `resupply_ammo`, `resupply_lives`, `combo_resupply`, `movement`, `elimination` as MUST NOT carry `"sp"`. **That restriction is removed.** All those events now carry the universal actor block (`sp` included) plus `target_*` when a target is present.

`movement` events are an exception — keep their existing `actor_role`/`start_row`/`start_col`/`end_row`/`end_col`/`cell_row`/`cell_col`/`new_zone` metadata, AND add the actor block. The target block is omitted (movement has no target).

`elimination` events carry both actor and target blocks plus the existing `elimination_action` key.

---

## 3. Test updates

### `matches/tests/test_res02_sp_metadata.py`
- The `test_non_sp_event_types_do_not_carry_sp` test and `test_event_type_partition_sp_key_presence`'s "NON_SP_TYPES" branch are now **wrong** — those events carry sp now. Invert: every event with actor carries `sp` AND `actor_*` AND `sp ∈ [0, 99]`.
- Replace `NON_SP_TYPES` partitioning with a new universal assertion: every event in the simulated round whose `actor_id is not None` carries the full actor block.
- Add: every event whose `target_id is not None` carries the full target block.
- Add: the three multi-target `special` events (medic team-heal, ammo team-ammo, nuke detonation) carry `meta.targets` as a non-empty list of `{pid, shots, lives, points}` dicts.

### `matches/tests/views_tests.py::TestM1EventLogWindowing`
- Mirror the universal contract at the view layer: every `events_data` row carries `meta.actor_role/shots/lives/points/sp` when `ev.aid != -1` (always true in current data shape) and the target block when `ev.tid != -1`.

### New: keep one assertion that `base_capture` no longer carries `special_points`
- This RES-02 sub-rule still holds.

---

## 4. Client chart refactor (template)

Owner: `laserforce_simulator/templates/matches/game_round_events.html`.

### Per-player series build (single pass over ALL)
```js
const playerState = {};  // pid -> {shots, lives, points, sp}
const seriesByChart = { shots: {}, lives: {}, points: {}, sp: {} };
PLAYERS.forEach(p => {
    const pid = String(p.id);
    playerState[pid] = {
        shots: parseInt(p.ss) || 0,
        lives: parseInt(p.sl) || 0,
        points: 0,
        sp: 0,
    };
    Object.keys(seriesByChart).forEach(k => {
        seriesByChart[k][pid] = [{sec: 0, val: playerState[pid][k]}];
    });
});

function pushIfChanged(pid, key, newVal, sec) {
    if (newVal === undefined || newVal === null) return;
    if (playerState[pid][key] === newVal) return;
    playerState[pid][key] = newVal;
    seriesByChart[key][pid].push({sec, val: newVal});
}

ALL.forEach(ev => {
    const sec = ev.sec, meta = ev.meta || {};
    const aid = String(ev.aid), tid = String(ev.tid);
    if (playerState[aid]) {
        pushIfChanged(aid, 'shots',  meta.actor_shots,  sec);
        pushIfChanged(aid, 'lives',  meta.actor_lives,  sec);
        pushIfChanged(aid, 'points', meta.actor_points, sec);
        pushIfChanged(aid, 'sp',     meta.sp,           sec);
    }
    if (ev.tid !== -1 && playerState[tid]) {
        pushIfChanged(tid, 'shots',  meta.target_shots,  sec);
        pushIfChanged(tid, 'lives',  meta.target_lives,  sec);
        pushIfChanged(tid, 'points', meta.target_points, sec);
    }
    if (Array.isArray(meta.targets)) {
        meta.targets.forEach(t => {
            const pid = String(t.pid);
            if (!playerState[pid]) return;
            pushIfChanged(pid, 'shots',  t.shots,  sec);
            pushIfChanged(pid, 'lives',  t.lives,  sec);
            pushIfChanged(pid, 'points', t.points, sec);
        });
    }
});
```

### Per-chart UI factory
Each of the 4 charts has its own block containing:
1. Chart title `<h6>`
2. Filter dropdowns wrapper (`<div id="{chartId}-overlay-controls">`):
   - Teams dropdown (`<div id="{chartId}-filter-teams">`) — red/blue
   - Roles dropdown (`<div id="{chartId}-filter-roles">`) — commander/heavy/scout/medic/ammo
   - Players dropdown (`<div id="{chartId}-filter-players">`) — built from PLAYERS
   - Overlays dropdown (`<div id="{chartId}-filter-overlays">`) — Eliminations / Specials / Nukes, color-coded labels (same `OVERLAY_KIND_STYLE` palette).
   - Team aggregate toggle (`<input id="{chartId}-filter-team-aggregate">`) — single checkbox.
3. Canvas wrapper `<div style="position: relative; height: 400px;"><canvas id="chart-{chart}"></canvas></div>`

Implement `buildPlayerChart({chartId, container, title, seriesByPid, opts})` where:
- `seriesByPid: {pid -> Array<{sec, val}>}` — the per-player series
- `opts.aggregation: 'sum' | 'avg'` — 'sum' for shots/lives/points (team total), 'avg' for sp (team average)
- `opts.capValue?: number` — when set, render a dashed horizontal reference line at y=capValue (SP only, y=99)
- `opts.yScale: object` — chart.js scale options

Returns: `{chart, applyFilters}` — `applyFilters` re-reads checkboxes and updates dataset visibility + aggregation overlay + overlay-event list.

Per-chart `overlayEvents` arrays are closure-locals; each chart's `_overlay_plugin` (registered inline at chart construction via the constructor's `plugins:` array) reads its OWN closure-captured list. Three colour-coded overlay toggles per chart, using the `OVERLAY_KIND_STYLE` palette (red eliminations / orange specials / purple nukes) and rotated player-name annotations (same as the current implementation, just per-chart now).

The 4 charts are built by calling `buildPlayerChart` four times with appropriate args:
- chart-shots: `seriesByChart.shots`, aggregation='sum', y={beginAtZero: true}
- chart-lives: `seriesByChart.lives`, aggregation='sum', y={beginAtZero: true}
- chart-points: `seriesByChart.points`, aggregation='sum'
- chart-sp: `seriesByChart.sp`, aggregation='avg', capValue=99, y={min: 0, max: 99}

### Remove the global overlay-controls bar
The current page-level `overlay-toggle` bar (dynamically inserted before `chartsRow`) is removed. Overlay toggles now live INSIDE each chart's filter UI.

### Remove dead reconstruction code
The pre-RES-02b client reconstruction (`rawEntries`, `curRedShots`, `cumRedPoints`, `smooth`, etc.) is now dead — delete the whole block. Per-player series are built from server snapshots instead.

### Playback scoreboard SP column
Keep the existing SP column. The `pbApply` SP-update logic still works (reads `ev.meta.sp`).

---

## 5. Test boundary (what tests assert vs internal)

### Tests MUST assert (server contract)
- Every event with `actor_id is not None` carries `meta.actor_role`, `meta.actor_shots`, `meta.actor_lives`, `meta.actor_points`, `meta.sp` — all ints (except role: str), with values in valid ranges (`0 <= sp <= 99`, `shots >= 0`, `lives >= 0`).
- Every event with `target_id is not None` carries the full target block (same int/range rules).
- Multi-target `special` events (medic team-heal, ammo team-ammo, nuke detonation) carry `meta.targets` — a non-empty list of `{pid, shots, lives, points}` dicts with valid ranges.
- `base_capture` events do NOT carry `meta.special_points` (RES-02 rename still holds).

### Tests MUST NOT assert
- Chart.js dataset shape, colour, ordering.
- Filter-dropdown HTML structure.
- Playback scoreboard cell text.
- Aggregate overlay arithmetic (sum vs avg) — internal template logic.

---

## 6. Owning modules per name

| Name | Owner |
|---|---|
| `_actor_meta` / `_target_meta` / `_build_meta` helpers | `matches/simulation.py` (and parallel locals in `combat.py`, `resupply_queue.py`) |
| Server emit-site metadata refactor | `matches/simulation.py`, `matches/sim_helpers/combat.py`, `matches/sim_helpers/resupply_queue.py` |
| Universal contract tests | `matches/tests/test_res02_sp_metadata.py` (update) + new test class for actor/target/targets snapshots |
| View-level assertions | `matches/tests/views_tests.py::TestM1EventLogWindowing` (update) |
| `buildPlayerChart` factory + per-chart UI | `laserforce_simulator/templates/matches/game_round_events.html` |
| Removal of `rawEntries` / `smooth` / global `overlayControls` | same template (cleanup) |
| Docs note | `PLAN.md` RES-02 note (extend); `matches/CLAUDE.md` GameEvent paragraph (extend) |

No CONTEXT.md change (terms unchanged). No ADR.
