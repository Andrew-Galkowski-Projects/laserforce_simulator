# LG-06d seam contract — Season selector + rate toggle

Status: agreed at grill (2026-06-02). UI-only / read-only. No model, migration,
simulator, RNG, or Score-Calibration re-baseline. No ADR. CONTEXT.md terms
**Per-10-minute rate** + **Career view (league-scoped)** already added.

## 0. Scope

- **Season selector `?season=`** on **6 screens**: `player_stats`, `team_stats`,
  `league_leaders`, `statistical_feats`, `game_log`, `power_rankings`.
  Options = each of this League's Seasons (newest-first) + a **Career** entry.
  Default (no `?season=` param) = current `displayed_season` → backward-compatible.
- **Rate toggle `?rate=`** on **`player_stats` only**: Totals / Per Game / Per 10 min.
- **Team History is OUT** (natively all-time; its Seasons tab is the per-season view).

## 1. New shared coercers — `matches/league_views.py`

Mirror the existing `_coerce_team_id` / `_coerce_per_page` forgiving precedent.

```python
def _coerce_season(raw, valid_season_ids, default):
    """raw -> int season id | "career" | default.

    Returns "career" iff raw == "career"; else int(raw) iff it parses AND is in
    valid_season_ids (a set[int]); else default. default is the caller-supplied
    fallback (the displayed_season id, or None when the League has no Season).
    """
```
- `CAREER` sentinel is the literal string `"career"` (locked).

```python
def _coerce_rate(raw, default="total"):
    """raw -> "total" | "per_game" | "per_10"; anything else -> default."""
```
- `RATE` literals locked: `"total"`, `"per_game"`, `"per_10"`.

## 2. New pure fn — `matches/season_player_stats.py`

```python
def apply_rate(rows, rate):  # rows: list[PlayerStatRow] -> list[PlayerStatRow]
```
- Transforms **`SUMMED_KEYS` only** (the 10 count keys). Returns NEW
  `PlayerStatRow`s (frozen dataclass) with `stats` copied + summed keys replaced;
  `AVERAGED_KEYS` (`mvp`, `accuracy`) and `DERIVED_KEYS` (`tag_ratio`, `survival`)
  pass through untouched. `games` unchanged.
- `rate == "total"`  → identity (summed values unchanged).
- `rate == "per_game"` → `value / games` (games ≥ 1 always for a row here).
- `rate == "per_10"` → `value * 600 / total_uptime_seconds`, where
  `total_uptime_seconds = stats["survival"] * games` (survival is the per-Round
  mean survival-seconds, so ×games rebuilds the summed uptime). Guard:
  `total_uptime_seconds <= 0` → `0.0`.
- Pure: no Django / ORM / RNG / I/O. Lives beside `aggregate_player_stats`.

## 3. Per-screen change pattern (all 6 views)

Each view currently does:
```python
displayed_season = league.active_season or league.seasons.filter(
    state="completed").order_by("-id").first()
```
LG-06d keeps that as the **default** and adds, after it:
```python
seasons = list(league.seasons.order_by("-start_date", "-id"))   # selector options
valid_ids = {s.id for s in seasons}
default_id = displayed_season.id if displayed_season is not None else None
selected_season = _coerce_season(request.GET.get("season"), valid_ids, default_id)
```
Then the screen's existing round/match queryset filter is built from the scope:
- `selected_season == "career"` → filter `...match__season__league=league`
- else (int id) → resolve that `Season` and filter `...match__season=<season>`
  (when `selected_season is None`, i.e. League has no Season → existing
  empty-state path is taken unchanged).

The **existing pure aggregation modules are reused VERBATIM** — they consume a
flat list of per-Round / per-Match dicts and are indifferent to one-season vs
all-seasons. Concrete aggregation entry points (locate + repoint the queryset
feeding each; do NOT change the pure module):
- `player_stats.py` → `_build_round_dicts` → `season_player_stats.aggregate_player_stats`
- `team_stats.py` → `team_stats_logic.aggregate_team_stats`
- `league_leaders.py` → `league_leaders_logic.compute_leaderboards`
- `statistical_feats.py` → `stat_feats.scan_feats`
- `game_log.py` → in-view round-row build
- `power_rankings.py` → `power_rankings_logic.compute_power_rankings`

## 4. Player Stats pipeline order (locked)

`aggregate_player_stats` → `apply_rate(rows, rate)` → team filter (`team_id`) →
`sort_player_stats` → `Paginator`. Sort therefore runs on the **rate-adjusted**
values (ZenGM behaviour).

## 5. Context keys

All 6 screens add:
- `season_options`: `list[dict]` — one `{"id": int, "name": str, "year": int|None}`
  per Season (newest-first) PLUS the Career option (rendered with value `"career"`;
  the Code agent renders the `<option value="career">Career</option>` in the template,
  so `season_options` may stay the Season list and the template appends Career, OR
  the list carries a career marker — Code agent's discretion, template-only).
- `selected_season`: `int | "career"` (or `None` in the empty-state path).

`player_stats` additionally adds:
- `rate`: the coerced `"total"|"per_game"|"per_10"`.
- `rate_options`: the 3 `(value, label)` pairs for the toggle.

Existing querystring helpers + hidden form inputs (per-page form, team-filter
form, sort-header hrefs) on every touched screen are extended to carry `season`
(and `rate` on player_stats). Changing `season` or `rate` **omits `page`** so it
resets to page 1 (LG-06a/b/c precedent).

## 6. DOM ids (locked)

- 6 screens: `<screen>-season-filter-form`, `<screen>-season-filter-select`
  (screen prefixes: `player-stats`, `team-stats`, `league-leaders`,
  `statistical-feats`, `game-log`, `power-rankings`).
- `player_stats` also: `player-stats-rate-form`, `player-stats-rate-select`.

## 7. Test boundary

- **Pure-unit** (`test_*` against the pure modules, no DB): `apply_rate` for each
  mode incl. zero-uptime guard and summed-only invariant (averaged/derived
  untouched, sort-on-displayed); `_coerce_season` (career sentinel / valid id /
  invalid → default / None default); `_coerce_rate`.
- **View tests** (Django `TestCase`, hand-built League with ≥2 Seasons of
  persisted rounds): per screen — selector renders with the right options +
  Career; `?season=<id>` scopes data to that Season; `?season=career` aggregates
  across all the League's Seasons; no-param default == `displayed_season`;
  invalid `?season=` falls back to default; `season` (and `rate`) carried across
  per-page / team-filter / sort links with page reset. `player_stats`: each
  `?rate=` mode changes the displayed numbers + the sort order.
- Blast radius: existing LG-01z / LG-06a/b/c screen view tests (querystring +
  context-key assertions) — update honestly, don't weaken guards.

## 8. Out of scope

Team History; current-state screens (Player Ratings, Free Agents, Team Roster,
Watch List); rate toggle on any screen but Player Stats; Per 36; Playoffs/
Regular toggle (C3 → LG-02); any model/migration/simulator change.
