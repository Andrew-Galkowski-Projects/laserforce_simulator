# LG-06b — Team filter on three league screens (seam contract)

## 1. Summary
Add an "All Teams" + per-enrolled-team `<select>` filter (`?team_id=<id>`) to the
three read-only league screens **Player Ratings**, **Player Stats**, and
**Statistical Feats**. UI-only, read-only, forgiving fallback. No model /
migration / simulator / URL / pure-module change. `stat_feats.py` and
`season_player_stats.py` stay untouched.

## 2. New names (owning module per name)

**Shared helper — `matches/league_views.py`** (mirrors `_coerce_per_page`,
single source imported by all 3 screen modules):
```python
def _coerce_team_id(raw: str | None, enrolled_ids: set[int]) -> int | None
```
Returns the int id **iff** `raw` parses as int AND is in `enrolled_ids`; else
`None`. `None` / empty / malformed / non-enrolled ⇒ `None` ("All Teams", no
filter). (`team_roster.py` has an inline precedent for this validation — this
helper consolidates it; `team_roster.py` is NOT edited.)

**New context keys (all 3 screens):**
- `enrolled_teams: list[Team]` — from `displayed_season.teams.order_by("name")`.
- `selected_team_id: int | None` — the `_coerce_team_id(...)` result.

**Locked DOM ids (form / select per screen):**
- `player-ratings-team-filter-form` / `player-ratings-team-filter-select`
- `player-stats-team-filter-form` / `player-stats-team-filter-select`
- `statistical-feats-team-filter-form` / `statistical-feats-team-filter-select`

Pre-existing empty-notice ids are `player-ratings-empty-notice`,
`player-stats-empty-notice`, `stat-feats-empty-notice` — NOT renamed; the new
ids don't collide.

## 3. Per-screen filter application (insertion point)

**Player Ratings** (`league_screens/player_ratings.py`): build `enrolled_teams`
+ `enrolled_ids` from `displayed_season.teams.order_by("name")`, compute
`selected_team_id = _coerce_team_id(request.GET.get("team_id"), enrolled_ids)`.
Apply the filter immediately after `qs = _enrolled_player_queryset(displayed_season)`
and BEFORE the `if sort == "preferred_roles"` split:
`if selected_team_id is not None: qs = qs.filter(team_id=selected_team_id)`.
Covers both the ORM-order branch and the `preferred_roles` python-list branch.
`enrolled_teams` / `selected_team_id` are also exposed in the empty-state branch
(`enrolled_teams=[]`, `selected_team_id=None`).

**Player Stats** (`league_screens/player_stats.py`): filter the aggregated rows
after `rows = aggregate_player_stats(round_dicts)` and before
`sort_player_stats(...)`:
`if selected_team_id is not None: rows = [r for r in rows if r.team_id == selected_team_id]`.
`PlayerStatRow.team_id` exists. `season_player_stats.py` untouched.

**Statistical Feats** (`league_screens/statistical_feats.py`): filter the two
seam inputs before `stat_feats.scan_feats(player_rounds, matches)`: keep
`player_rounds` rows where `team_id == selected_team_id`; keep `matches` entries
where `selected_team_id in {entry["red_team_id"], entry["blue_team_id"]}`. Apply
only when `selected_team_id is not None`. `stat_feats.py` untouched.

## 4. Template / querystring wiring

**Paginated screens (Player Ratings + Player Stats):** add `team_id` to BOTH
querystring-helper variables — `querystring_without_page` and
`querystring_without_sort_dir_page` — by setting
`qs_no_page["team_id"] = str(selected_team_id)` /
`qs_no_sort_dir_page["team_id"] = str(selected_team_id)` ONLY when
`selected_team_id is not None` (so "All Teams" leaves no stray param). Add a
hidden `<input type="hidden" name="team_id" value="{{ selected_team_id }}">`
inside the existing per-page `<form>` (rendered only when `selected_team_id`).

**Team-picker form (all 3 screens):** `<form id="<screen>-team-filter-form"
method="get">` containing `<select id="<screen>-team-filter-select"
name="team_id" onchange="this.form.submit()">` with default
`<option value="">All Teams</option>` (selected when `selected_team_id` is
falsy) plus one `<option value="{{ t.id }}" {% if t.id == selected_team_id %}selected{% endif %}>{{ t.name }}</option>`
per `enrolled_teams`. Picker form carries hidden `sort` / `dir` / `per_page`
inputs where those exist on that screen (Ratings + Stats) and OMITS `page`.
Statistical Feats has no pagination/sort — its picker form needs no hidden
inputs, just the select. Render the picker only when `displayed_season` is
present (inside the existing non-empty guard, above the table/list). A
`<noscript>` submit button matches the existing per-page form pattern.

## 5. Test boundary (assert vs internal)

Extend `matches/tests/test_lg01z_player_ratings.py`,
`test_lg01z_player_stats.py`, `test_lg01z_statistical_feats.py`. Assert:
- `_coerce_team_id`: parses+enrolled ⇒ int; `None`/empty/`"abc"`/non-enrolled ⇒ `None`.
- Each screen: `?team_id=<enrolled>` filters rows/feats to that team only;
  absent/empty/malformed/non-enrolled ⇒ unfiltered (all teams).
- Context keys `enrolled_teams` (ordered by name) and `selected_team_id`.
- DOM ids `<screen>-team-filter-form` / `-select`; default `All Teams` option;
  selected option matches `selected_team_id`.
- Paginated screens: `team_id` in both querystring helpers + hidden per-page-form
  input; picker form omits `page`.

Internal (not asserted): exact queryset SQL, list-comprehension form,
`scan_feats` internals.

## 6. Untouched / out-of-scope
`matches/stat_feats.py`, `matches/season_player_stats.py`, `team_roster.py`
(precedent only), `_enrolled_player_queryset`, `_coerce_per_page` /
`_coerce_page` / `_LG01F_PER_PAGE_OPTIONS` / `_build_league_sidebar_links` /
`_resolve_current_team_for_sidebar`, all URLs / URL names, models, migrations,
simulator, RNG, score re-baseline, CONTEXT.md, ADRs. No new pagination/sort on
Statistical Feats. Empty-notice ids unchanged.
