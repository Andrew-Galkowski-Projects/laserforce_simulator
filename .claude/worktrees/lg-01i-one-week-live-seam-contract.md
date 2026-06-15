# LG-01i Seam Contract — Season "One Week (Live)" preview-then-commit replay UI

**Status:** locked design, ready for Code/Tests/Docs agents.
**Depends on:** LG-01d (Play dropdown + dashboards), LG-02-Part2c-1..3f (phase
cursor / playoffs), CAR-01 (`League.current_team`), SIM-05 (client playback engine
in `templates/matches/game_round_events.html`), SIM-07/08 (seed→byte-identical
replay), SIM-09 (`simulate_match` / `simulate_scheduled_round` view-path).

## 0. The locked design (preview-then-commit, NOT tick streaming)

LG-01i is **preview-then-commit live replay**. Only the **manager's** team
(`League.current_team`) game is watched live; the rest of the matchday / bracket
stage is **simmed FRESH at commit, never previewed**.

- **Preview** draws a seed (or a pair), `random.seed()`s it, runs the in-memory
  tick loop via `_simulate_round` (NO DB flush), serializes the events to the
  SIM-05 `events_data` JSON shape, and **pins the captured seed(s) in
  `request.session`** keyed to the current cursor identity.
- **Locked once previewed** — re-opening the preview replays the SAME pinned game;
  Discard clears the pin; the pin **auto-invalidates** when the cursor identity
  changes (matchday/node already played).
- **Commit** re-runs the WATCHED game with the **INJECTED captured seed(s)** and
  flushes (SIM-07 guarantees byte-identical to the watched game), then sims the
  rest of the whole matchday (RR) / whole bracket STAGE (playoff) FRESH, SYNC,
  atomically — exactly like the existing Play One Week / weekly playoff pacing.
- Non-manager fixtures / non-watched nodes are simmed fresh (their seeds are
  drawn normally — NOT injected).

**Two cursor types are watchable:**
1. **RR cursor** — `current_team`'s SINGLE Round of the next unplayed matchday (1
   seed). If `current_team` has a bye that matchday (not in the next matchday's
   fixtures) ⇒ **degrade to plain commit** (no live surface, just play the week).
2. **Playoff cursor** — `current_team`'s next undecided 2-round playoff Match (the
   next undecided `BracketNode` of its bracket node), shown with a round-1/round-2
   toggle (2 seeds). Offered ONLY IF `current_team` is **alive** (has an
   incomplete, non-bye bracket node it participates in) in the active tournament;
   eliminated / not-a-participant ⇒ no live entry.

## 1. The injected-seed seam (additive, keyword-only, `None` ⇒ verbatim today)

### 1a. `_simulate_and_flush_round` (currently draws its own seed, ~line 556)

Current signature:
```python
def _simulate_and_flush_round(
    self, team_red, team_blue, *, match, round_number, movement_ctx, arena_map, zone_size,
) -> "GameRound":
    ...
    seed = random.Random().getrandbits(63)
    random.seed(seed)
    ...
    return self._flush_to_db(..., rng_seed=seed, ...)
```

**ADD** a keyword-only `rng_seed: int | None = None`:
```python
def _simulate_and_flush_round(
    self, team_red, team_blue, *, match, round_number, movement_ctx, arena_map,
    zone_size, rng_seed: int | None = None,
) -> "GameRound":
    ...
    seed = random.Random().getrandbits(63) if rng_seed is None else rng_seed
    random.seed(seed)
    ...
    return self._flush_to_db(..., rng_seed=seed, ...)
```
**Invariant:** `rng_seed is None` ⇒ draws fresh exactly as today ⇒ byte-identical
to every existing caller (no Score Calibration re-baseline). When injected, the
SAME int is `random.seed()`'d AND persisted onto `GameRound.rng_seed` — so a
committed round's `rng_seed == the captured preview seed`.

### 1b. `simulate_scheduled_round` (1 seed, ~line 736)

Current signature (LG-02-Part2c-3a):
```python
def simulate_scheduled_round(
    self, season, team_a, team_b, round_number, *,
    arena_map=None, season_phase=None, leg: int = 1,
) -> "GameRound":
```
**ADD** keyword-only `rng_seed: int | None = None` (appended last). Thread it
straight through to the single `_simulate_and_flush_round` call (BOTH the round-1
and round-2 branches pass `rng_seed=rng_seed`). The Side-agnostic find-or-create,
the post-round hooks (`activate_pending_tournament_phase()` then
`complete_if_finished()`), the per-round colour swap, and the `season_phase`/`leg`
key are UNCHANGED. `None` ⇒ verbatim today.

> Note: an RR round is ONE `GameRound` per `simulate_scheduled_round` call (the
> two rounds of a Match are two separate calls). The RR live surface watches the
> SINGLE next Round, so it carries exactly **1 seed**.

### 1c. `simulate_match` (a pair, ~line 608) — reached via `play_next_node`

Current signature:
```python
def simulate_match(
    self, team_red, team_blue, match_type="friendly", *,
    arena_map=None, before_round_hook=None,
) -> Match:
```
**ADD** keyword-only `rng_seeds: tuple[int, int] | None = None` (appended last).
The two internal `_simulate_and_flush_round` calls become:
```python
round1 = self._simulate_and_flush_round(
    team_red, team_blue, match=match, round_number=1, ...,
    rng_seed=(None if rng_seeds is None else rng_seeds[0]),
)
...
round2 = self._simulate_and_flush_round(
    team_blue, team_red, match=match, round_number=2, ...,
    rng_seed=(None if rng_seeds is None else rng_seeds[1]),
)
```
`rng_seeds is None` ⇒ both rounds draw fresh ⇒ byte-identical to today. The
`before_round_hook` (LG-02x-1 random-draw) and the per-Match colour swap are
UNCHANGED and orthogonal.

### 1d. `play_next_node` — inject seeds into the watched playoff Match

`matches/tournament_engine.py::play_next_node(tournament)` calls
`BatchSimulator().simulate_match(node.team_a, node.team_b, match_type="tournament")`
(VERBATIM today). LG-01i needs the COMMIT of the watched playoff Match to inject
the captured pair, while every OTHER node sims fresh. Two options — **pick option
B (locked):**

- **Option B (locked):** a NEW thin sibling
  `matches/tournament_engine.py::play_specific_node(node, *, rng_seeds=None)`
  (`@transaction.atomic`) that is the SAME per-Match resolve/advance body as
  `play_next_node` but **takes the node directly** (skips `find_next_playable_node`)
  and passes `rng_seeds=` into `simulate_match`. `play_next_node` is refactored to
  `node = tournament.find_next_playable_node(); if node is None: return None;
  return play_specific_node(node)` — so the existing body is shared, `play_next_node`
  stays byte-identical (calls `play_specific_node(node, rng_seeds=None)`), and the
  commit path calls `play_specific_node(watched_node, rng_seeds=captured_pair)`
  for the watched Match then loops `play_next_node(tournament)` for the rest of the
  stage. **Note:** because a Series node is best-of-N, the watched Match is always
  game 1 of the node's Series (the cursor identity is the next undecided node and
  its game count is 0 at preview time — pin to `series_matches.count() == 0`); the
  injected pair drives that single `simulate_match` call.

  Rationale: keeps `find_next_playable_node` ordering authoritative for the rest
  of the stage, isolates the injected-seed branch to one node, and reuses the
  ENTIRE clinch/advance/drop/grand-final/champion tail unchanged.

## 2. The preview engine methods (sim WITHOUT flush, returns serializable bundle)

Two new `BatchSimulator` methods. Both draw the seed(s), `random.seed()`, run
`_simulate_round` (the pure in-memory tick loop), and serialize the in-memory
`event_log` + `PlayerState` rosters into the **SIM-05 `events_data` / `players_data`
JSON shape** WITHOUT touching the DB.

### 2a. `BatchSimulator.preview_scheduled_round(...)` (RR, 1 seed)

```python
def preview_scheduled_round(
    self, season, team_a, team_b, round_number, *,
    arena_map=None, season_phase=None, leg: int = 1,
) -> dict:
```
Body: `movement_ctx, zone_size = load_map_context(arena_map)`;
`seed = random.Random().getrandbits(63)`; `random.seed(seed)`;
`events = []`; `result, red_players, blue_players = self._simulate_round(
list(team_a.active_roster), list(team_b.active_roster), event_log=events,
movement_ctx=movement_ctx)`; then build and return the **preview bundle** (below).
NO `_flush_to_db`. `team_a` plays red, `team_b` plays blue (matches the round-1
arg order of `simulate_scheduled_round`).

### 2b. `BatchSimulator.preview_tournament_match(...)` (playoff, 2 seeds)

```python
def preview_tournament_match(
    self, team_red, team_blue, *, arena_map=None,
) -> dict:
```
Body: draws `seed1, seed2 = random.Random().getrandbits(63),
random.Random().getrandbits(63)`; for round 1 `random.seed(seed1)` +
`_simulate_round(red_roster, blue_roster, ...)`; for round 2 `random.seed(seed2)`
+ `_simulate_round(blue_roster, red_roster, ...)` (the per-Match colour swap, args
reversed exactly as `simulate_match`). Returns the preview bundle with **two**
serialized rounds. NO DB. (Match-type / arena are 3-zone fallback `None` per the
tournament path's current `arena_map=None`.)

### 2c. The preview bundle (return-dict shape, LOCKED)

```python
{
    "kind": "rr" | "playoff",
    "seed": int,                  # RR: the single captured seed
    "seeds": [int, int],          # playoff: the captured pair (omit on RR)
    "rounds": [                   # 1 entry for RR, 2 for playoff
        {
            "round_number": int,         # 1 (RR) or 1/2 (playoff)
            "red_team_id": int,          # the team that physically played red
            "red_team_name": str,
            "blue_team_id": int,
            "blue_team_name": str,
            "events_data": [<event-dict>],   # SIM-05 shape, see §2d
            "players_data": [<player-dict>], # SIM-05 shape, see §2e
            "result": {                  # slim, for the preview banner only
                "red_points": int, "blue_points": int,
                "red_eliminated": bool, "blue_eliminated": bool,
            },
        },
    ],
    # cursor identity (for the session pin — see §4):
    "cursor": <cursor-identity-dict>,    # §4 shape
}
```

### 2d. `events_data` per-event dict — IDENTICAL to the persisted events view

The persisted `game_round_events` view (`matches/views.py:882`) emits these 13
keys per event off a `GameEvent` ORM row. The preview must produce the SAME shape
from the **in-memory `event_log`** (7-key dicts: `event_type, actor_id,
target_id, timestamp, points_awarded, description, metadata`) + the in-memory
`PlayerState` objects. Keys (LOCKED, every one read by the SIM-05 JS):

| key  | source (persisted)                  | source (preview, in-memory)                              |
|------|-------------------------------------|----------------------------------------------------------|
| `type` | `e.event_type`                    | `entry["event_type"]`                                    |
| `ts`   | `e.timestamp` (ticks)             | `entry["timestamp"]`                                     |
| `tf`   | `e.formatted_timestamp` (mm:ss)  | replicate: `mm:ss` of `int(ts * 0.5)` (see §2f)          |
| `icon` | `e.get_event_icon()`             | replicate the icon map (see §2f)                         |
| `desc` | `e.description`                  | `entry["description"]`                                   |
| `pts`  | `e.points_awarded`               | `entry["points_awarded"]`                                |
| `aid`  | `e.actor_id` (player_id)         | `entry["actor_id"]`                                      |
| `an`   | `e.actor.name`                   | `players_by_id[actor_id].name`                           |
| `at`   | `e.actor.team_id` (Team id)      | red/blue Team id by the actor's `team_color`             |
| `tid`  | `e.target_id or -1`              | `entry["target_id"]` or `-1`                             |
| `tn`   | `e.target.name or ""`            | `players_by_id[target_id].name` or `""`                  |
| `tt`   | `e.target.team_id or ""`         | red/blue Team id by the target's `team_color`, or `""`   |
| `meta` | `e.metadata or {}`               | `entry["metadata"] or {}`                                |

The SIM-05 JS derives `idx`/`sec` client-side
(`ALL.forEach((e,i)=>{e.idx=i; e.sec=e.ts/2;})`) — **do NOT bake them**.
`at`/`tt` are the actor/target **Team** ids: resolve each in-memory player's
`team_color` (`"red"`/`"blue"`) to the round's red/blue Team id.

### 2e. `players_data` per-player dict — IDENTICAL to the persisted view

Persisted (`matches/views.py:901`): `{id, name, team, role, sl, ss}` —
`team` is the **color string** `"red"`/`"blue"` (NOT a Team id). From the
in-memory `PlayerState`: `id=ps.player_id`, `name=ps.name`, `team=ps.team_color`,
`role=ps.role`, `sl=ps.starting_lives`, `ss=ps.starting_shots`. (Confirm the
`PlayerState` field names against `sim_helpers/player_state.py`; they back the
forwarding properties used elsewhere.)

### 2f. Serialization helper (LOCKED — single source, replicate not persist)

Add a private module-level helper in `matches/simulation/entrypoints.py` (or a
small new `matches/simulation/preview_serialize.py`):
```python
def _serialize_events_for_preview(event_log, red_players, blue_players,
                                  red_team, blue_team) -> tuple[list[dict], list[dict]]:
```
It builds `players_by_id` from the two `PlayerState` lists, a
`team_id_by_color = {"red": red_team.id, "blue": blue_team.id}`, replicates the
icon map (the 11-entry dict from `GameEvent.get_event_icon`, default `"•"`) and
the mm:ss formatter (`total = int(ts * 0.5); f"{total//60:02d}:{total%60:02d}"`),
and returns `(events_data, players_data)`. **Replicate the icon/format logic — do
NOT construct unsaved `GameEvent(...)` instances** (risk of accidental save, and
ORM construction needs FK objects). The icon map + the `÷2` mm:ss are the only two
behaviours that must stay in sync with `GameEvent`; pin them with a test that
asserts the preview icon for every event type equals `GameEvent(event_type=t,
timestamp=0).get_event_icon()`.

## 3. New view functions (`matches/league_views.py`)

All three are appended; reuse the LG-01d guards/redirect idioms.

### 3a. `play_week_live_preview(request, season_id) -> HttpResponse` (GET page)

- `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` (first line).
- `season = get_object_or_404(Season, pk=season_id)`;
  `request.session["last_league_id"] = season.league_id`.
- `season.state != "active"` ⇒ `_render_season_dashboard_error(...)` (400 dashboard
  re-render, the LG-01d `play_error` pattern).
- **Cursor dispatch (§5):** resolve the live cursor for `season.league.current_team`.
  - **No live entry** (no `current_team`, RR bye, eliminated playoff, or no
    tournament) ⇒ `_render_season_dashboard_error(request, season, "<reason>")`
    (400). Defensive — the dropdown entry is gated NOT to render in these states.
  - **RR cursor** or **playoff cursor**:
    - Read the existing session pin (§4). If a VALID pin exists for THIS cursor
      identity ⇒ re-run the matching preview method with the **pinned seed(s)
      injected** (so re-opening replays the same game). Inject via a parallel
      preview path: `preview_scheduled_round` / `preview_tournament_match` accept an
      optional `rng_seed=` / `rng_seeds=` (mirror §1) so a re-open is deterministic.
    - Else run the preview FRESH (draws seeds), then WRITE the pin (§4).
  - Render `templates/seasons/play_week_live.html` with the preview bundle + cursor
    metadata + `csrf` + the playback context vars the SIM-05 engine needs (§8).
- Returns **200** on a successful preview render.

### 3b. `play_week_live_commit(request, season_id) -> HttpResponse` (POST → 302)

- POST only (`HttpResponseNotAllowed(["POST"])` first line); 404 on missing Season.
- `request.session["last_league_id"] = season.league_id`.
- `season.state != "active"` ⇒ `_render_season_dashboard_error(...)` (400).
- Read the session pin (§4). If MISSING or STALE (cursor identity moved) ⇒
  `_render_season_dashboard_error(request, season, "Preview expired — re-open the
  live preview.")` (400) — do NOT silently sim fresh (that would defeat the
  byte-identical guarantee the user watched).
- **RR commit** (inside ONE `transaction.atomic`, mirroring `play_week`):
  - Resolve the next matchday's fixtures (the LG-02-Part2c-2 `by_phase` /
    `played_keys` / `select_play_fixtures(..., 1)` machinery, VERBATIM from
    `play_week`).
  - For the watched fixture (the `current_team` fixture) call
    `simulate_scheduled_round(..., rng_seed=pin.seed, ...)`; for every OTHER fixture
    of that matchday call `simulate_scheduled_round(...)` with NO `rng_seed`
    (fresh). Reuse the same `_resolve_fixture_map` / `in_bulk` / `season_phase` /
    `leg` plumbing as `play_week`.
- **Playoff commit** (no outer `transaction.atomic` — per-Match atomicity from the
  engine, mirroring the `play_week` playoff branch):
  - Resolve the watched node = `current_team`'s next undecided bracket node.
  - `play_specific_node(watched_node, rng_seeds=tuple(pin.seeds))` (§1d) — commits
    the watched Match byte-identical to the preview.
  - Then drain the rest of the STAGE: loop `play_next_node(tournament)` until the
    next playable node is no longer in the watched node's
    `(bracket_type, bracket_round)` stage (reuse the `play_next_bracket_round` stage
    logic; the cleanest implementation is: commit the watched node first, then call
    `play_next_bracket_round(tournament)` for the rest — but guard against
    double-committing the watched node, which is already resolved so
    `find_next_playable_node` skips it). Then `season.complete_if_finished()`.
- **Clear the pin** (§4) after a successful commit.
- `return redirect("season_dashboard", season_id=season.id)` (302).

### 3c. `play_week_live_discard(request, season_id) -> HttpResponse` (POST → 302)

- POST only (405 otherwise); 404 on missing Season.
- `request.session["last_league_id"] = season.league_id`.
- **Clear the pin** (§4) for this Season's cursor. NO simulation, NO DB write.
- `return redirect("season_dashboard", season_id=season.id)` (302).

## 4. Session shape (the pin — "locked once previewed")

**Key (LOCKED):** `request.session["live_preview_pin"]` — a `dict` keyed by
`str(season_id)` so multiple Seasons can hold independent pins:
```python
request.session["live_preview_pin"] = {
    "<season_id>": {
        "kind": "rr" | "playoff",
        "current_team_id": int,           # League.current_team.id at pin time
        "cursor": <cursor-identity-dict>, # §5; the auto-invalidation key
        "seed": int,                      # RR only
        "seeds": [int, int],              # playoff only
    },
}
```

**Cursor-identity dict (the invalidation key):**
- **RR:** `{"type": "rr", "season_id": int, "season_phase_id": int | None,
  "matchday": int, "pair": [min_id, max_id], "round_number": int, "leg": int}`
  — the `season_phase_id` + matchday + Side-agnostic team pair + round_number + leg
  uniquely identify the single watched RR Round (mirrors the
  `(season_phase_id, frozenset(pair), round_number, leg)` played-key).
- **Playoff:** `{"type": "playoff", "tournament_id": int, "bracket_type": str,
  "bracket_round": int, "position": int}` — the watched node's coords.

**Lifecycle (LOCKED):**
- **Write** on a fresh preview render (§3a): set the pin with the freshly-drawn
  seed(s) + the current cursor identity + `current_team_id`; set
  `request.session.modified = True`.
- **Read & validate** on re-open (§3a) and commit (§3b): a pin is VALID iff its
  `cursor` dict EQUALS the freshly-recomputed cursor identity AND its
  `current_team_id` equals `season.league.current_team_id`. A valid pin ⇒ inject
  its seed(s); an invalid/absent pin ⇒ (preview) draw fresh + rewrite; (commit)
  400 "preview expired".
- **Auto-invalidate:** there is no explicit invalidation step — the cursor identity
  EQUALITY check is the invalidation. Once the watched matchday/node is played, the
  recomputed cursor moves on, so the stale pin no longer matches and is treated as
  absent (overwritten on the next preview, or 400 on a commit).
- **Clear:** Discard (§3c) and a successful commit (§3b) both
  `request.session["live_preview_pin"].pop(str(season_id), None)` + set
  `modified = True`.

## 5. Cursor dispatch + bye / eliminated degradation + alive-in-playoff helper

A NEW module-level helper resolves the live cursor:
```python
def _resolve_live_cursor(season) -> dict | None:
```
Returns a **cursor descriptor** or `None` (no live entry). Algorithm (LOCKED order):
1. `team = season.league.current_team`. If `team is None` ⇒ return `None`.
2. `phase = season.current_phase()`.
   - **Playoff cursor:** if `phase` is a built+active tournament phase
     (`phase.phase_type == "tournament" and phase.tournament_id is not None and
     phase.tournament.state == "active"`):
     - Resolve `team`'s next undecided, non-bye bracket node it participates in via
       the NEW `_alive_playoff_node(phase.tournament, team)` (§5a). If `None`
       (eliminated / not a participant / no undecided node) ⇒ fall through to step 3
       (a mid-season tournament could still have an RR phase elsewhere, but in
       practice an active tournament phase with the manager eliminated ⇒ no live
       entry; return `None`). Else return a `{"kind": "playoff", "node": node,
       "tournament": phase.tournament, "cursor": <playoff identity §4>,
       "red_team": node.team_a, "blue_team": node.team_b}` descriptor.
   - **RR cursor:** if `phase` is an RR phase (or the implicit fallback):
     - Compute the next unplayed matchday's fixtures (reuse the `play_week`
       `by_phase` / `played_keys` / `select_play_fixtures(..., 1)` machinery).
     - Find the fixture whose Side-agnostic pair contains `team.id`. **If none
       (manager has a bye this matchday)** ⇒ return a special
       `{"kind": "rr_bye"}` descriptor → the view degrades to a plain commit (no
       live surface). **If found** ⇒ return `{"kind": "rr", "fixture": fixture,
       "season_phase": phase_for_fixture, "cursor": <rr identity §4>,
       "red_team": team_a, "blue_team": team_b}` (team_a plays red).
3. Anything else ⇒ return `None`.

> The view (§3a) maps: `None`/`rr_bye` outside the dropdown gate ⇒ the dropdown
> entry never renders (§9); a `rr_bye` reached via a direct POST ⇒ commit degrades
> to `play_week`'s plain matchday sim (no pin, no preview). `"rr"` / `"playoff"` ⇒
> the live preview page.

### 5a. Alive-in-playoff detection helper

```python
def _alive_playoff_node(tournament, team) -> "BracketNode | None":
```
Returns `team`'s next undecided, non-bye bracket node it currently occupies, or
`None`. Rule: among `tournament.nodes`, find a node where `winner_id is None`,
`is_bye is False`, both slots filled, and (`team_a_id == team.id` or
`team_b_id == team.id`), AND it is the next PLAYABLE node for that team (lowest
`(_BRACKET_RANK[bracket_type], bracket_round, position)`). Pin to a Series that has
not started (`node.series_matches.count() == 0`) so the watched Match is game 1.
`None` ⇒ the team is eliminated, not a participant, or has no undecided node ⇒ no
live entry.

## 6. URL names + paths (`matches/season_urls.py`)

Insert BEFORE the `standings/` / `schedule/` entries (first-match resolution),
adjacent to the LG-01d play routes. Bare names (no `app_name`):
```python
path("<int:season_id>/play-week-live/", league_views.play_week_live_preview, name="play_week_live"),
path("<int:season_id>/play-week-live/commit/", league_views.play_week_live_commit, name="play_week_live_commit"),
path("<int:season_id>/play-week-live/discard/", league_views.play_week_live_discard, name="play_week_live_discard"),
```
URL names: `play_week_live`, `play_week_live_commit`, `play_week_live_discard`.

## 7. New template `templates/seasons/play_week_live.html`

Extends `base.html`, `d-flex` + `{% include "_partials/league_sidebar.html" %}`
shell (the league-screen convention), `sidebar_active=None`. Embeds the SIM-05
playback engine fed by IN-MEMORY events JSON (cannot reuse
`/matches/game-round/<id>/events/` — no persisted round id exists at preview time).

### SIM-05 reuse decision (LOCKED): **duplicate the playback JS, do NOT extract.**

Rationale: the SIM-05 playback engine in `game_round_events.html` is INLINE and
tightly coupled to page-specific context that the preview page does NOT have —
it reads Django context vars directly inside the `<script>`
(`'{{ round.team_red.id }}'`, `'{{ round.team_blue.id }}'`,
`'{{ round.team_red.name|escapejs }}'`, `'{{ round.team_blue.name|escapejs }}'`),
and it is interleaved with the kill-feed, the three Chart.js charts, and the M-1
timeline window pager. A clean shared partial would require threading those four
team identity values + decoupling the charts/kill-feed — out of scope for LG-01i.
**Duplicate the minimal playback block** (the scrubber DOM + `pbPlayers`/`pbEvts`/
`pbApply`/`pbSetTime`/`pbRefresh` engine, the `MAX_SH`/`MAX_LIVES` role constants,
and the two `JSON.parse(getElementById('events-data'/'players-data'))` reads) into
the new template, dropping the charts / kill-feed / timeline-window code. Feed the
team identity values from the preview bundle's per-round
`red_team_id`/`red_team_name`/`blue_team_id`/`blue_team_name` instead of
`{{ round.* }}`. (A future refactor MAY extract a shared `_partials/playback.html`;
LG-01i does not.)

### LOCKED DOM ids (preview page)

- `play-week-live-container` — outer wrapper for the preview surface.
- The reused SIM-05 playback ids — **keep IDENTICAL** so the duplicated JS works:
  `pb-time-display`, `pb-scrubber`, `pb-step-back`, `pb-play`, `pb-step-fwd`,
  `pb-speed`, `pb-sb-red`, `pb-sb-blue`.
- `play-week-live-commit-form` / `play-week-live-commit-submit` — the Commit
  `<form method="post" action="{% url 'play_week_live_commit' season.id %}">` +
  submit button (`{% csrf_token %}`).
- `play-week-live-discard-form` / `play-week-live-discard-submit` — the Discard
  `<form method="post" action="{% url 'play_week_live_discard' season.id %}">` +
  submit button (`{% csrf_token %}`).
- `play-week-live-round-toggle` — the playoff round-1/round-2 toggle (e.g. two
  radio/`<button>`s `play-week-live-round-1` / `play-week-live-round-2` that swap
  which round's JSON the playback engine reads). RENDERED ONLY when
  `kind == "playoff"` (RR has a single round).
- `play-week-live-bye-notice` — the empty/bye notice (substring `"bye"`), rendered
  when the page is reached on a `rr_bye` cursor (defensive; normally the dropdown
  degrades before reaching the page).

### `json_script` feed ids (LOCKED)

For an RR preview (single round):
```django
{{ preview_round.events_data|json_script:"events-data" }}
{{ preview_round.players_data|json_script:"players-data" }}
```
The SIM-05 JS reads `events-data` / `players-data` (UNCHANGED ids). For a playoff
preview (two rounds), emit BOTH rounds under round-suffixed ids
(`events-data-1` / `players-data-1` / `events-data-2` / `players-data-2`) and have
the duplicated JS re-init the engine off the toggled round's pair. (The simplest
locked approach: the round toggle swaps the active `ALL`/`PLAYERS` arrays and
re-runs `pbReset()` + `pbRefresh()`.)

## 8. Dashboard wiring — the "One Week (Live)" Play-dropdown entry

A NEW context key on BOTH dashboards, computed in `_build_dashboard_context`
(currently builds the 11-key body + the LG-02-Part2c playoff cursor keys):
- **`live_preview_available: bool`** — `True` iff `_resolve_live_cursor(season)`
  returns a descriptor whose `kind` is `"rr"` or `"playoff"` (NOT `None`, NOT
  `"rr_bye"`). Computed via the same `_resolve_live_cursor` helper (§5); add it to
  the context dict alongside `playoff_phase_active` etc. (Optionally also expose a
  `live_preview_kind: str | None` for the label, but `live_preview_available` is
  the gate.)

New Play-dropdown entry DOM ids (mirror the LG-01d play-dropdown ids; the entry
links to `{% url 'play_week_live' season.id %}` — a GET to the preview page):
- Season dashboard: `season-dashboard-play-one-week-live` (the dropdown
  `<a>`/`<button>` inside the existing `season-dashboard-play-dropdown`).
- League dashboard: `league-dashboard-play-one-week-live` (mirror inside
  `league-dashboard-play-dropdown`).

Render the entry ONLY when `live_preview_available` (so a manager with a bye or an
eliminated bracket sees no live option — they use plain Play One Week / Play
Single Round). It sits alongside the existing LG-01d "One Week" / "Two Months" /
"Until End"/"Until Playoffs" entries.

## 9. Test boundary

| File | Class | Covers |
|------|-------|--------|
| `matches/tests/test_simulation_view_paths.py` | `TestLg01iInjectedSeedDefault` | `simulate_scheduled_round(..., rng_seed=None)` and `simulate_match(..., rng_seeds=None)` are byte-identical to a no-kwarg call given the same `random.seed` setup (the verbatim-default regression — protects Score Calibration). `_simulate_and_flush_round(rng_seed=None)` draws fresh as today. |
| `matches/tests/test_simulation_view_paths.py` | `TestLg01iSeedInjection` | Injecting a captured seed: a committed `simulate_scheduled_round(..., rng_seed=S)` persists `GameRound.rng_seed == S`; the committed event log equals the previewed event log (capture the preview events via `preview_scheduled_round`, inject the captured seed, flush, assert the persisted `GameEvent` rows' `(event_type, timestamp, actor_id, target_id, points_awarded)` tuples equal the preview `events_data`'s `(type, ts, aid, tid, pts)` — the SIM-07 byte-identical guarantee). Same for `simulate_match(rng_seeds=(s1,s2))` ↔ `preview_tournament_match`. |
| `matches/tests/test_simulation_view_paths.py` | `TestLg01iPreviewBundle` | `preview_scheduled_round` / `preview_tournament_match` return the §2c bundle: `events_data` carries the 13 keys; `players_data` carries `{id,name,team,role,sl,ss}`; the preview icon for every event type equals `GameEvent(event_type=t, timestamp=0).get_event_icon()`; `tf` matches the `÷2` mm:ss formatter; NO `GameRound`/`GameEvent` rows were created (assert `GameRound.objects.count()` unchanged). |
| `matches/tests/views_tests.py` (or `test_league_play.py`) | `TestLg01iCursorDispatch` | `_resolve_live_cursor`: RR cursor when `current_team` is in the next matchday; `rr_bye` when it has a bye; `None` when no `current_team`; playoff cursor when alive; `None` when eliminated. `_alive_playoff_node` returns the next undecided non-bye node the team occupies, `None` when eliminated. |
| `matches/tests/views_tests.py` (or `test_league_play.py`) | `TestLg01iLivePreviewView` | `play_week_live_preview` GET → **200** on a valid RR/playoff cursor; **400** dashboard re-render on non-active Season / no live entry; **405** on POST. The pin is written to `request.session["live_preview_pin"][str(season_id)]` with the drawn seed(s) + cursor identity. Re-opening replays the SAME seed (assert the pinned seed is reused, not redrawn). |
| `matches/tests/views_tests.py` (or `test_league_play.py`) | `TestLg01iLiveCommit` | `play_week_live_commit` POST → **302** + rows created: the watched fixture's persisted round `rng_seed` == the pinned seed; the rest of the matchday is simmed (more `GameRound` rows than just the watched one). Playoff commit advances the watched node + drains the rest of the stage. Missing/stale pin ⇒ 400. Pin cleared after commit. **No exact-point-total assertions** (RR is seeded so deterministic, but assert seed equality / row counts; playoff sims are non-deterministic for the un-watched nodes). |
| `matches/tests/views_tests.py` (or `test_league_play.py`) | `TestLg01iLiveDiscard` | `play_week_live_discard` POST → **302** + **zero** new rows (assert `GameRound.objects.count()` unchanged); the pin is cleared from the session. **405** on GET. |
| `matches/tests/test_league_dashboard.py` / `test_season_dashboard_view.py` | `TestLg01iDashboardEntry` | `live_preview_available` True ⇒ the `season-dashboard-play-one-week-live` / `league-dashboard-play-one-week-live` entry renders linking to `play_week_live`; bye / eliminated / no-current_team ⇒ the entry is ABSENT. |

**Assertion discipline:** the determinism guarantee is asserted as
(committed round `rng_seed` == captured seed) AND (committed event-log tuples ==
previewed `events_data` tuples). The injected-seed-default regression asserts a
no-kwarg call and a `None`-kwarg call produce identical games under the same
`random.seed`. NO exact-point-total assertions (tournament sims are
non-deterministic; RR is seeded — assert seed/row/identity, not raw totals).

## 10. Scope-out (LOCKED — DO NOT build)

- **No migration, no model change** (no new `GameRound`/`Match`/`Season` field —
  the seed is held in `request.session`, the committed round persists its seed via
  the existing `GameRound.rng_seed`).
- **No re-baseline** — the injected-seed kwargs default to `None` ⇒ existing seeded
  games are byte-identical ⇒ no Score Calibration re-baseline.
- **No server-side tick streaming / WebSocket / SSE** — this is preview-then-commit,
  the client-side SIM-05 engine plays the pre-baked JSON.
- **No watching non-manager games** — only `League.current_team`'s game is
  previewed; every other fixture / node is simmed fresh at commit.
- **No re-roll within a pin** — re-opening replays the SAME pinned game; the only
  way to a new game is Discard (clears the pin) then re-open (draws fresh).
- **No playoff-live when eliminated** — `_alive_playoff_node` returns `None` ⇒ no
  live entry; the manager uses plain Play Single Round / Play Playoffs.
- **RR + alive-playoff cursors ONLY** — no live surface for `member_night` / inert
  phases, no whole-bracket live drain (only the single watched Match is previewed;
  the rest of the stage commits fresh).
- **No SIM-05 partial extraction** — the playback JS is DUPLICATED into the new
  template (§7), not factored into a shared `_partials/playback.html`.
- **No ADR, no CONTEXT.md term** — preview/commit/pin are implementation language;
  reuses the existing **Matchday** / **Season phase** / **Job** vocabulary.

## 11. Locked names (quick index)

- **Engine seam:** `_simulate_and_flush_round(..., rng_seed: int | None = None)`;
  `simulate_scheduled_round(..., rng_seed: int | None = None)`;
  `simulate_match(..., rng_seeds: tuple[int, int] | None = None)`;
  `tournament_engine.play_specific_node(node, *, rng_seeds=None)` (extracted from
  `play_next_node`, which becomes its caller).
- **Preview methods:** `BatchSimulator.preview_scheduled_round(season, team_a,
  team_b, round_number, *, arena_map=None, season_phase=None, leg=1,
  rng_seed=None) -> dict`; `BatchSimulator.preview_tournament_match(team_red,
  team_blue, *, arena_map=None, rng_seeds=None) -> dict`; private
  `_serialize_events_for_preview(...) -> (events_data, players_data)`.
- **Views:** `matches.league_views.play_week_live_preview` (GET 200 /
  400-dashboard / 405); `play_week_live_commit` (POST 302 / 400 / 405);
  `play_week_live_discard` (POST 302 / 405).
- **Cursor helpers:** `matches.league_views._resolve_live_cursor(season) -> dict |
  None`; `_alive_playoff_node(tournament, team) -> BracketNode | None`.
- **URLs:** `play_week_live` (`/seasons/<id>/play-week-live/`),
  `play_week_live_commit` (`.../commit/`), `play_week_live_discard`
  (`.../discard/`) — in `matches/season_urls.py`, before `standings/`/`schedule/`.
- **Session:** `request.session["live_preview_pin"][str(season_id)]` =
  `{kind, current_team_id, cursor, seed | seeds}`; cursor identity dicts per §4.
- **Bundle keys:** `kind, seed|seeds, rounds:[{round_number, red_team_id,
  red_team_name, blue_team_id, blue_team_name, events_data, players_data, result}],
  cursor`. `events_data` = the 13-key SIM-05 shape; `players_data` =
  `{id,name,team,role,sl,ss}`.
- **Template:** `templates/seasons/play_week_live.html`; DOM ids
  `play-week-live-container`, the reused `pb-*` ids
  (`pb-time-display`/`pb-scrubber`/`pb-step-back`/`pb-play`/`pb-step-fwd`/`pb-speed`/
  `pb-sb-red`/`pb-sb-blue`), `play-week-live-commit-form`/`-commit-submit`,
  `play-week-live-discard-form`/`-discard-submit`, `play-week-live-round-toggle`
  (+ `play-week-live-round-1`/`-round-2`), `play-week-live-bye-notice`;
  `json_script` ids `events-data`/`players-data` (RR) and
  `events-data-1`/`players-data-1`/`events-data-2`/`players-data-2` (playoff).
- **Dashboard:** context key `live_preview_available: bool` (+ optional
  `live_preview_kind`), computed in `_build_dashboard_context`; entry DOM ids
  `season-dashboard-play-one-week-live` / `league-dashboard-play-one-week-live`,
  rendered only when `live_preview_available`.
- **Tests:** `TestLg01iInjectedSeedDefault` / `TestLg01iSeedInjection` /
  `TestLg01iPreviewBundle` in `test_simulation_view_paths.py`;
  `TestLg01iCursorDispatch` / `TestLg01iLivePreviewView` / `TestLg01iLiveCommit` /
  `TestLg01iLiveDiscard` in `views_tests.py` (or `test_league_play.py`);
  `TestLg01iDashboardEntry` in `test_league_dashboard.py` /
  `test_season_dashboard_view.py`.
