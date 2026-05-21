# RES-02 Seam Contract — SP Timeline Chart

Scope: per-player SP-over-time chart on `/matches/game-round/<id>/events/`, plus
SP column on the SIM-05 playback scoreboard. Server-snapshot data source; no
migration; no backfill; no view/serializer changes; no ADR; no CONTEXT change.

Branch: `res-02-sp-timeline` (already current).

---

## 1. Event metadata contract

Key: `"sp"`. Type: `int`. Range: `[0, 99]` (cap enforced by existing
`min(max_special, …)` arithmetic at every increment site). Semantic: **post-event**
SP — written *after* the increment or decrement on the actor for that event.

Every SP-changing emit site MUST add `"sp": <actor>.final_special` to its
existing `metadata` dict. Every non-SP-changing emit site MUST NOT carry
`"sp"`.

### MUST carry `metadata["sp"]`

| event_type     | file                                       | emit-site anchor                                  | actor                | SP delta |
|----------------|--------------------------------------------|---------------------------------------------------|----------------------|----------|
| `tag`          | `matches/simulation.py` ~L1849             | main tag (post-increment at L1821, non-heavy)     | `attacker`           | +1       |
| `tag`          | `matches/simulation.py` ~L2010             | reaction tag (post-increment at L1970, non-heavy) | `r_attacker`         | +1       |
| `tag`          | `matches/simulation.py` ~L2136             | follow-up tag (post-increment at L2093, non-heavy)| `fu_attacker`        | +1       |
| `missile`      | `matches/simulation.py` ~L2228             | missile lock complete (post-increment at L2222, non-heavy) | `attacker`  | +2 (heavy 0) |
| `special`      | `matches/simulation.py` ~L2265             | `_use_special` commander branch (nuke activation) | `player`             | −cost    |
| `special`      | `matches/simulation.py` ~L2284             | `_use_special` scout branch (rapid fire)          | `player`             | −cost    |
| `special`      | `matches/simulation.py` ~L2303             | `_use_special` medic branch (team heal)           | `player`             | −cost    |
| `special`      | `matches/simulation.py` ~L2328             | `_use_special` ammo branch (team ammo)            | `player`             | −cost    |
| `special`      | `matches/simulation.py` ~L2345             | `_complete_nuke` (nuke detonation)                | `player`             | 0 (unchanged since activation) |
| `base_capture` | `matches/sim_helpers/combat.py` ~L557      | post-increment at L543                            | `player`             | +5       |

Heavy attackers do **not** increment SP on either `tag` or `missile` events
(both sites are guarded by `attacker.role != "heavy"`). The events are still
emitted and MUST still carry `"sp"` = the attacker's unchanged `final_special`.
The presence of `"sp"` is keyed on event_type, not on whether SP actually
changed for that specific actor — this same rule covers heavy tags, heavy
missile hits, and nuke-detonation `special` rows (SP already spent at
activation).

### MUST NOT carry `metadata["sp"]`

| event_type        | rationale                                                                              |
|-------------------|----------------------------------------------------------------------------------------|
| `miss`            | no SP change                                                                            |
| `resupply_ammo`   | supporter SP unchanged (confirmed in `combat.attempt_resupply`)                         |
| `resupply_lives`  | supporter SP unchanged                                                                  |
| `combo_resupply`  | neither medic nor ammo SP changes                                                       |
| `movement`        | no SP change                                                                            |
| `elimination`     | no SP change                                                                            |

---

## 2. Removed key

`metadata["special_points"]` (currently emitted by `base_capture` at
`matches/sim_helpers/combat.py` ~L557) is **renamed to `metadata["sp"]`**. No
alias retained. Tests assert `"special_points"` is absent from every
`base_capture` event after this task.

---

## 3. View / serializer

No view, serializer, or model changes are needed.

- `matches/views.py` — `game_round_events` view (~L493) already passes
  `{"meta": e.metadata or {}}` through to `events_data`, so the new `"sp"` key
  reaches the client verbatim.
- `matches/serializers.py` — `GameEventSerializer` already serialises
  `metadata` as-is (no field-level filtering), so the REST API picks `"sp"` up
  for free.
- `matches/models.py` — `GameEvent.metadata` is a `JSONField`; no migration.

---

## 4. Template constants (chart-side)

- `MAX_SP = 99` — y-axis ceiling and dashed reference-line value.
- **No** `SP_ROLE_COSTS` constant. The chart and the playback scoreboard both
  read `meta.sp` directly. SP cost rules are server-side only and are not
  reconstructed on the client.

---

## 5. Template DOM ids and CSS hooks

Owner: `laserforce_simulator/templates/matches/game_round_events.html`.

New DOM placed in a new row below the existing Shots / Lives / Points chart row.

| id / class                 | element                                                                 |
|----------------------------|-------------------------------------------------------------------------|
| `chart-sp`                 | `<canvas>` for the SP-over-time chart                                   |
| `sp-overlay-controls`      | wrapper `<div>` for the filter dropdowns + team-average toggle          |
| `sp-filter-teams`          | dropdown — checkboxes for `red`, `blue`                                 |
| `sp-filter-roles`          | dropdown — checkboxes for `commander`, `heavy`, `scout`, `medic`, `ammo` |
| `sp-filter-players`        | dropdown — checkboxes auto-generated from `players_data`                |
| `sp-filter-team-averages`  | single `<input type="checkbox">` toggling the 2 per-team-average lines  |

The three `sp-filter-*` dropdowns MUST mirror the existing
`event-type-filters` / `player-filters` dropdown DOM structure exactly
(same wrapper class, same `<details>`/`<summary>` or button+menu pattern,
same checkbox label markup) for visual consistency.

Dashed reference line at y=99 is drawn via the existing `_overlay_plugin`
Chart.js plugin pattern in the template (search the template for
`_overlay_plugin`); no new plugin.

Chart datasets:

- 10 per-player lines (red players in shades of red, blue players in shades
  of blue). `stepped: true`. No rolling-average smoothing.
- 2 per-team-average lines (toggled by `sp-filter-team-averages`).
- y-axis fixed `[0, 99]`.

### Playback scoreboard column

In both `pb-sb-red` and `pb-sb-blue` scoreboard tables:

- New `<th>` header text: `SP`.
- Per-row cell: a `<td>` whose text content is the integer
  `pbPlayers[playerId].sp` (range `[0, 99]`).

Header insertion position is at the end of the existing column set (after
the last existing scoreboard column), so existing column selectors are
unchanged.

---

## 6. JS state shape

All names below are inside `game_round_events.html` `<script>` — no module
boundary, no export. Owner: that template.

### Per-player playback SP state

```
pbPlayers[id] = {
  // ... existing fields unchanged ...
  sp: number   // current SP, integer in [0, 99]
}
```

- Initialised to `0` in `pbReset` for every player id.
- Advanced inside `pbApply(ev)`:
  - If `ev.type ∈ {"tag", "missile", "special", "base_capture"}` AND
    `ev.aid` matches a player id AND `ev.meta && typeof ev.meta.sp === "number"`,
    set `pbPlayers[ev.aid].sp = ev.meta.sp`.
  - Otherwise leave `sp` unchanged.
- The playback scoreboard `<td>` for each row reads `pbPlayers[id].sp` at
  render time.

### Chart-side per-player series

Built once at chart-construction time by walking the chronological
`events_data` array:

```
spSeries[playerId] = Array<{ sec: number, sp: number }>
```

- One entry pushed per SP-changing event for that player (same event_type
  set as above), with `sec = ev.tf` (the existing display-seconds float)
  and `sp = ev.meta.sp`.
- A synthetic `{ sec: 0, sp: 0 }` entry is prepended for every player so the
  stepped line starts at the origin (consistent with the other charts).
- Chart.js datasets are derived from `spSeries`. The two per-team-average
  lines are derived per-tick by averaging the currently-visible per-player
  SP values within each team (visibility respects the team / role / player
  filter checkboxes; toggle `sp-filter-team-averages` shows/hides the two
  overlay datasets only).

No global SP cache outlives chart construction; `spSeries` may be a local
inside the chart-init closure.

---

## 7. Test boundary

### Tests MUST assert

- Server contract — for every emit site in §1 "MUST carry":
  - `metadata["sp"]` is present
  - `isinstance(metadata["sp"], int)`
  - `0 <= metadata["sp"] <= 99`
  - on `base_capture` events, `"special_points"` is **absent**
- Server contract — for every emit site in §1 "MUST NOT carry":
  - `"sp" not in metadata`
- View-level — extend `TestM1EventLogWindowing`
  (`laserforce_simulator/matches/tests/views_tests.py`):
  - `events_data` rows of type `tag`, `missile`, `special`, and
    `base_capture` carry `meta.sp` as an integer in `[0, 99]`. (All
    `special` rows — both activation and detonation — carry sp; the
    heavy-tag precedent applies here too: presence is keyed on event_type,
    not on whether SP actually changed.)
  - Rows of type `miss`, `resupply_ammo`, `resupply_lives`, `combo_resupply`,
    `movement`, and `elimination` do NOT carry `meta.sp`.

### Tests MUST NOT assert

- Chart.js dataset shape, colour, ordering, or rendering.
- Filter-dropdown HTML structure or checkbox labels.
- Playback scoreboard `<td>` text or column placement.
- The internal `pbPlayers[id].sp` or `spSeries[playerId]` shapes (these are
  template-internal and may be refactored freely).

No JS tests are added (matches the precedent set by the existing three
charts).

---

## 8. Owning modules per name

| Name                                         | Owner                                                                  |
|----------------------------------------------|------------------------------------------------------------------------|
| `metadata["sp"]` emission (tag / missile / special activation) | `laserforce_simulator/matches/simulation.py`                           |
| `metadata["sp"]` emission (base_capture) + removal of `"special_points"` | `laserforce_simulator/matches/sim_helpers/combat.py`                   |
| `chart-sp` canvas, `MAX_SP` constant, dashed y=99 reference, filter dropdowns, `spSeries`, playback `SP` column, `pbPlayers[id].sp`, `pbReset`/`pbApply` SP handling | `laserforce_simulator/templates/matches/game_round_events.html`        |
| Server-side metadata assertions              | new test class in `laserforce_simulator/matches/tests/test_batch_sim.py` (or a new `test_res02_sp_metadata.py` in the same package) |
| View-level `events_data` SP assertions       | extension of `TestM1EventLogWindowing` in `laserforce_simulator/matches/tests/views_tests.py` |
| Docs note (one-paragraph addition only)      | `laserforce_simulator/matches/CLAUDE.md` GameEvent metadata paragraph (note that `tag` / `missile` / `special` activation / `base_capture` carry `"sp"`; replaces former `"special_points"` on `base_capture`) |

No CONTEXT.md change. No ADR. No migration. No new view, no new URL, no
serializer change.
