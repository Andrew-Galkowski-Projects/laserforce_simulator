# Round Analytics Extraction — Seam Contract

Lift the per-round analytics scattered across `matches/views.py` into three pure modules, matching the established `h2h_stats.py` pattern. The driver is the **Round scoreboard** domain concept (CONTEXT.md, `### Analytics and review`) — the per-player table that both HTML round-detail and the Round report PDF render, currently sourced from two unrelated code paths.

This is a **behaviour-neutral refactor**. No new mechanic, no new HTTP surface, no model change, no migration, no RNG, no SIM-07 / SIM-08 contract interaction, **no Score Calibration re-baseline**. Output JSON and rendered HTML are byte-identical pre/post — pinned by existing view tests.

## Scope

Three clusters lifted in one PR:

| Cluster | New pure module | View consumers |
|---|---|---|
| A. **Round scoreboard** | `matches/round_summary.py` | `game_round_detail` (HTML, flipped to dict), `export_round_report` (PDF, dict already), future REST |
| B. **Round comparison** | `matches/round_comparison.py` | `compare_rounds` |
| C. **Missile log summary** | `matches/missile_log_stats.py` | `missile_log` |

`movement_heatmap` is **out of scope** (cell-occupancy aggregation already lives in `sim_helpers/cell_occupancy.py`; the view is a thin renderer).

## Pure-module contract (all three)

Same rules as `h2h_stats.py` / `player_h2h_stats.py` / `season_dashboard.py`:

- **Frozen import allowlist:** `dataclasses`, `typing`, `collections` only. **NO** Django, NO ORM, NO `random`, NO `datetime`, NO file I/O, NO logging, NO cross-module imports from `matches/*`.
- Every public function accepts `list[dict]` (or `Iterable[Mapping]`) and returns plain `dict` / `list[dict]` / scalar — never an ORM object, never a `QuerySet`.
- Empty input ⇒ zeros (or `[]` / `0.0` / `None`), never raises.
- Defensive `TestNoDjangoImportsLeaked` walks `sys.modules` from a subprocess fresh-import.

## Cluster A — `matches/round_summary.py`

### `PLAYER_ROW_KEYS` — frozen 28-key tuple, pinned order

The **Round scoreboard** row. HTML renders all 26; PDF renders 14 of them (the RV-03 subset, marked `*` below); a future REST endpoint can return all 26.

Identity / role (3): `name`, `role`, `team_color`.
Survival (3): `was_eliminated_at` (tick, `1801` = survived sentinel — TIME-01), `eliminated_timestamp` (mm:ss display string; `""` when survived), `final_lives*`.
Scoring (5): `points_scored*`, `mvp*` (from `get_mvp`), `tags_made*`, `times_tagged*`, `accuracy*` (from `get_accuracy`, 0–100 int).
Resources (5): `final_shots`, `final_special`, `shots_used`, `missiles_used`, `starting_missiles`.
Combat extras (6): `missiles_landed*`, `times_missiled`, `final_medic_hits`, `medic_lives_removed_from_nuke` (Commander-only; `0` otherwise), `follow_up_shots*`, `reaction_shots*`.
Support (3): `resupplies_given*`, `specials_used*`, `combo_resupply_count*`.
Display arithmetic (1): `special_cost` (per-role constant, kept in the dict so the template doesn't need `mul_int:perf.special_cost` against an ORM attr).

The `specific_tags|length` template expression collapses to a derived **`specific_tags_count`** int in the dict (the underlying `specific_tags` JSONField is not exposed — the template only ever reads its length).

### Functions (3)

```python
def team_totals(player_rows: Iterable[Mapping], team_points: int) -> dict:
    """RV-03 carry-over. Returns 6-key dict — keys preserved verbatim:
    resupplies_given, missiles_landed, specials_used, tags_made, survivors, team_points."""

def survivor_count(player_rows: Iterable[Mapping]) -> int:
    """Replaces the `count_survivors` template filter. count(p["final_lives"] > 0)."""

def team_eliminated(player_rows: Iterable[Mapping]) -> bool:
    """Single source of truth for the team-elimination 10,000-pt-bonus branch.
    True iff survivor_count(player_rows) < 1. The template currently re-evaluates
    `red_performances|count_survivors < 1` ~4 times per page — collapsing to one
    bool concentrates the rule."""
```

### View-side materialisation

`game_round_detail` and `export_round_report` both build the same 26-key dict per `PlayerRoundState` via a module-level helper in `matches/views.py`:

```python
def _player_row(state: PlayerRoundState) -> dict:
    return {
        "name": state.player.name,
        "role": state.role,
        "team_color": state.team_color,
        "was_eliminated_at": state.was_eliminated_at,
        "eliminated_timestamp": state.eliminated_timestamp,  # @property
        "final_lives": state.final_lives,
        "points_scored": state.points_scored,
        "mvp": state.get_mvp,
        "tags_made": state.tags_made,
        "times_tagged": state.times_tagged,
        "accuracy": state.get_accuracy,
        "final_shots": state.final_shots,
        "final_special": state.final_special,
        "shots_used": state.shots_used,
        "missiles_used": state.missiles_used,
        "starting_missiles": state.starting_missiles,
        "missiles_landed": state.missiles_landed,
        "times_missiled": state.times_missiled,
        "final_medic_hits": state.final_medic_hits,
        "medic_lives_removed_from_nuke": state.medic_lives_removed_from_nuke,
        "follow_up_shots": state.follow_up_shots,
        "reaction_shots": state.reaction_shots,
        "resupplies_given": state.resupplies_given,
        "specials_used": state.specials_used,
        "combo_resupply_count": state.combo_resupply_count,
        "specific_tags_count": len(state.specific_tags or {}),
        "special_cost": state.special_cost,
    }
```

The existing `views.py::_player_row` (14 keys, PDF-only) is **replaced** by this 26-key version. Both consumers read the same dict; the PDF builder ignores the 12 extras.

### Template flip — `templates/matches/game_round_detail.html`

- Remove `perf.player.name` → `perf.name`; `perf.eliminated_timestamp`, `perf.final_lives`, etc. read flat keys instead of ORM attrs.
- Replace `perf.specific_tags|length` with `perf.specific_tags_count`.
- Replace `perf.tags_made|div:perf.times_tagged|floatformat:2` with `perf.accuracy` style — actually the existing `div` filter is on integer counts, **keep `div`** (it's used as `X/Y` display, not as the percent in `accuracy`). The `div` template filter stays.
- Replace each `red_performances|count_survivors`/`is_eliminated` chain. Two cleanest moves:
  - `count_survivors` collapses to a context key `red_survivors` / `blue_survivors` (view computes via `survivor_count(...)`); template just renders `{{ red_survivors }}` and tests `{% if red_survivors < 1 %}`.
  - `is_eliminated` filter (applied to each row's `<tr>` class) reads `perf.was_eliminated_at and perf.was_eliminated_at < 1801` — collapses to a derived dict key `is_eliminated: bool` populated by `_player_row` (add to the dict as a 27th derived key — re-deriving in template is fragile).
- The two custom template filters (`count_survivors`, `is_eliminated`) become **dead** post-flip — drop their definitions from `teams/templatetags/team_extras.py`. `mul_int` and `div` stay.

**Revised key count: 28** (26 base + `specific_tags_count` + derived `is_eliminated` bool). PDF still renders its 14-subset; HTML renders 27 of 28 (skipping `team_color` which is implicit from the table heading).

### View body shape after the flip

```python
def game_round_detail(request, round_id):
    game_round = get_object_or_404(GameRound, id=round_id)
    red_states = (game_round.player_states
                  .filter(player__team=game_round.team_red)
                  .select_related("player")
                  .order_by("-points_scored", "role", "player__name"))
    blue_states = (game_round.player_states
                   .filter(player__team=game_round.team_blue)
                   .select_related("player")
                   .order_by("-points_scored", "role", "player__name"))
    red_players = [_player_row(s) for s in red_states]
    blue_players = [_player_row(s) for s in blue_states]
    context = {
        "round": game_round,
        "red_players": red_players,
        "blue_players": blue_players,
        "red_survivors": survivor_count(red_players),
        "blue_survivors": survivor_count(blue_players),
        "red_eliminated": team_eliminated(red_players),
        "blue_eliminated": team_eliminated(blue_players),
        "round1_winner": ...,  # match_detail-style helper unchanged
        "round2_winner": ...,
    }
    return render(request, "matches/enhanced_match_detail.html", context)
```

`export_round_report` calls the same `_player_row` builder, and its `report_data` dict's `red_totals` / `blue_totals` come from `team_totals(red_players, game_round.red_points)`.

## Cluster B — `matches/round_comparison.py`

### Constants moved from `views.py`

`_COMPARE_STAT_KEYS` (the 12-key delta-table column order) and `_COMPARE_FIELD_STAT_KEYS` (the 10 IntegerField names, distinct from `mvp` / `accuracy` properties) move into the new module as the public `COMPARE_STAT_KEYS` and `COMPARE_FIELD_STAT_KEYS`.

### Functions (3)

```python
def stat_values(player_state_row: Mapping) -> dict:
    """Returns the 12-key flat dict (mvp, accuracy, plus the 10 IntegerField names).
    Input: a dict already materialised by the view from PlayerRoundState
    — view reads ps.get_mvp / ps.get_accuracy / ps.<field>, builds dict, passes in."""

def player_stat_deltas(rows_a: list[dict], rows_b: list[dict]) -> list[dict]:
    """Returns the per-player paired delta rows.
    Input is already-paired list[dict] keyed by player_id; the view is responsible
    for the {player_id: row} maps and team-filter."""

def cumulative_team_points(team_events: Iterable[tuple[int, int]]) -> list[list[int]]:
    """Walks (timestamp, points_awarded) pairs (already ordered, points coalesced
    to 0) and returns [[tick, cumulative]]. The view runs the ORM query and feeds
    a list of tuples."""
```

### View-side change

`compare_rounds` materialises a per-PlayerRoundState comparison-row dict (16 keys: `player_id`, `name`, `role`, `team_color`, plus the 12 `stat_values` keys) via a `_comparison_row(ps)` helper, builds `{player_id: row}` maps for each round filtered to shared teams, calls `player_stat_deltas(rows_a, rows_b)`. For `points_series`, the view runs the existing ORM query, materialises `[(timestamp, points_or_0) for ...]`, and calls `cumulative_team_points(...)`.

## Cluster C — `matches/missile_log_stats.py`

### `MissileRow` dataclass — frozen 7-key shape

```python
@dataclass(frozen=True)
class MissileRow:
    timestamp: int       # raw ticks
    timestamp_mmss: str  # display "MM:SS"
    actor_role: str
    target_role: str
    result: str          # "hit" | "miss"
    friendly_fire: bool
    description: str
    points: int          # GameEvent.points_awarded (or 0)
    row_class: str       # "missile-row" | "missile-row friendly-fire"
```

### Function (1)

```python
def summarize_missile_log(events: Iterable[Mapping]) -> dict:
    """Input: missiled-event dicts {timestamp, metadata, description, points_awarded}.
    Output: {"fired": int, "hit": int, "efficiency": float, "rows": list[MissileRow]}.
    Locking events are filtered OUT before the call (view-side); only missiled rows
    cross the seam. Empty input ⇒ {"fired": 0, "hit": 0, "efficiency": 0.0, "rows": []}.
    Friendly-fire hits count as hits — preserves CONTEXT.md Friendly fire contract."""
```

### Template change — `templates/matches/missile_log.html`

`row.event.description` → `row.description`; `row.event.points_awarded` → `row.points`. The raw `GameEvent` ORM ref drops out of the row dict. Two-line template diff; no JS, no DOM-id change.

### View body shape after the flip

```python
def missile_log(request, round_id):
    game_round = get_object_or_404(GameRound, id=round_id)
    events_qs = (GameEvent.objects.filter(game_round=game_round, event_type="missiled")
                 .order_by("timestamp"))
    events = [{"timestamp": e.timestamp, "metadata": e.metadata or {},
               "description": e.description, "points_awarded": e.points_awarded or 0}
              for e in events_qs]
    summary = summarize_missile_log(events)
    return render(request, "matches/missile_log.html",
                  {"round": game_round, **summary})
```

The view drops the `locking` filter (it was kept for "future fired-but-cancelled" — that future is not now; can resurrect by widening the queryset and a second pure function later).

## Tests

Three NEW pure-unit test files (mirror `test_h2h_stats.py` / `test_season_dashboard.py` exactly), each carrying `TestNoDjangoImportsLeaked`:

- `matches/tests/test_round_summary.py` — `TestPlayerRowKeys` (the 27-key contract pinned by a frozen tuple in the module), `TestTeamTotals` (empty input zeros, summing happy path, survivors-from-final-lives, team_points pass-through), `TestSurvivorCount` (empty 0, mixed survivors/eliminated count), `TestTeamEliminated` (single survivor False, all eliminated True), `TestNoDjangoImportsLeaked`.
- `matches/tests/test_round_comparison.py` — `TestStatValues` (12 keys present, mvp + accuracy carry through), `TestPlayerStatDeltas` (paired empty `[]`, both-present delta math, one-side-missing yields `None` delta and `None` side fields, ordering by name), `TestCumulativeTeamPoints` (empty `[]`, null-points coalesce to 0, running sum across ticks), `TestNoDjangoImportsLeaked`.
- `matches/tests/test_missile_log_stats.py` — `TestSummarizeMissileLog` (empty zeros + `0.0` efficiency no div-by-zero, hit + miss row counts, efficiency `hits/fired*100`, friendly-fire counted as hit, `row_class` substring `"friendly-fire"`, mm:ss formatting at tick→s `÷2`), `TestNoDjangoImportsLeaked`.

### Existing view-tests touched

- `views_tests.py::TestRV01CompareRounds` — unchanged assertions; the view still returns the same JSON `compare-points-series` and `compare-deltas` shape.
- `views_tests.py::TestRV03ExportRoundReport` — unchanged assertions (the PDF byte-prefix + Content-Type tests).
- `views_tests.py::TestRES03MissileLog` — `row.event.description` template assertions update to `row.description`; the summary `fired` / `hit` / `efficiency` context keys keep their names.
- `views_tests.py::TestGameRoundDetail` (or whatever covers `game_round_detail` today) — updated to assert the new context keys (`red_players`, `blue_players`, `red_survivors`, `red_eliminated`, etc.) and template assertions read flat dict keys instead of ORM attrs.

### Dead-code deletion

`teams/templatetags/team_extras.py::count_survivors` and `is_eliminated` are deleted. Their unit tests (if any in `teams/tests`) are also deleted.

## What stays put

- `matches/h2h_stats.py`, `matches/player_h2h_stats.py`, `matches/standings.py`, `matches/season_dashboard.py` — untouched (already pure).
- `matches/sim_helpers/pdf_report.py` — untouched. It receives the existing `report_data` dict from `export_round_report`; the dict shape is unchanged.
- `matches/views.py` `_shared_team_ids` — stays in `views.py` (single caller, 4 lines of set arithmetic; deletion test concentrates nothing).
- `mul_int` / `div` template filters — stay (used in expressions like `perf.specials_used|mul_int:perf.special_cost` for the SP display arithmetic; these are display-layer concerns).
- The existing `views.py` ORM-query shapes, `select_related`, ordering — all preserved verbatim.

## Locked names

URL routes unchanged; URL names unchanged; template paths unchanged; DOM ids unchanged; context-key names for analytics surfaces unchanged on the consumer side except where explicitly noted (`game_round_detail` gains `red_players` / `blue_players` / `red_survivors` / `blue_survivors` / `red_eliminated` / `blue_eliminated`, drops `red_performances` / `blue_performances`).

New module names: `matches.round_summary` / `matches.round_comparison` / `matches.missile_log_stats`. New public functions: `team_totals` / `survivor_count` / `team_eliminated` / `stat_values` / `player_stat_deltas` / `cumulative_team_points` / `summarize_missile_log`. New dataclass: `MissileRow`. New constants: `PLAYER_ROW_KEYS` (frozen 27-tuple), `COMPARE_STAT_KEYS` (12-tuple), `COMPARE_FIELD_STAT_KEYS` (10-tuple).

New view helper: `matches.views._player_row` (replaces the existing 14-key version with the 27-key version). New view helper: `matches.views._comparison_row` (16-key per-PlayerRoundState dict for round comparison).

CONTEXT.md gains the **Round scoreboard** term in `### Analytics and review`.

`docs/architecture-improvements.md` candidate #1 marked **designed** with a link to this contract.

No ADR (reversible refactor; established `h2h_stats` precedent; no new mechanism or contract that future explorers couldn't re-derive).
