# LG-06c — Sortable columns on five league-screen tables — SEAM CONTRACT

Status: design-only. This artifact pins every name, query param, sort-key
whitelist, default, DOM id, and test boundary for the Code / Tests / Docs
agents. No production code, no tests, no docs edits are part of this artifact.

## 0. Scope & locked decisions (do NOT relitigate)

LG-06c brings the LG-00c sortable-column-header pattern
(`teams.views.player_list` + `templates/teams/player_list.html`) to the five
league screens that lack it.

1. **Scope = ALL FIVE screens:** Team History, Game Log, League Leaders,
   Watch List, Statistical Feats. (Player Ratings + Player Stats already
   sort+paginate+team-filter — **OUT OF SCOPE, untouched** — reference only.)
2. **Mechanism = IN-MEMORY `sorted(key=...)`** on the already-materialized row
   lists each view builds (dicts / pure-module dataclass records / `Player`
   objects). **NO ORM `.order_by()` rework. NO change to any pure module**
   (`stat_feats.py`, `team_history_logic.py`, `league_leaders_logic.py`,
   `power_rankings_logic.py`, `season_player_stats.py` stay UNTOUCHED —
   sorting happens view-side on their OUTPUT).
3. **New shared helper**
   `matches.league_views._coerce_sort_key(raw: str | None, allowed: frozenset[str], default: str) -> str`
   — mirrors the forgiving-fallback `_coerce_per_page` / `_coerce_team_id`
   pattern already in that file: returns `raw` iff `raw in allowed`, else
   `default`. This is the SINGLE source of sort-key coercion for all 5 screens.
   **REUSE `teams.views._coerce_dir` verbatim** (import it; do NOT duplicate
   `_coerce_dir` and do NOT re-define `_VALID_DIRS = ("asc", "desc")`).
4. **Multi-table screens use NAMESPACED per-table query params** so sorting
   one table never resets a sibling.
5. **Row-tables only.** Team History's Overall tab (a single W-L-T `dl`) gets
   NO sort.
6. **UI-only, read-only.** No model, migration, URL route, simulator, RNG,
   CONTEXT.md, ADR, or score re-baseline. (LG-06a / LG-06b precedent.)

## 1. The shared helper (the single new production symbol)

Append to `matches/league_views.py`, beside `_coerce_per_page` /
`_coerce_page` / `_coerce_team_id`:

```python
def _coerce_sort_key(
    raw: str | None, allowed: frozenset[str], default: str
) -> str:
    """LG-06c — coerce ``?<…>sort=`` to a whitelisted sort key, else default.

    Returns ``raw`` iff ``raw`` is in ``allowed``; otherwise ``default``.
    ``None`` / empty / unknown all map to ``default``. Mirrors the forgiving
    ``_coerce_per_page`` / ``_coerce_team_id`` precedent in this file. The
    single source of sort-key coercion for all five LG-06c screens.
    """
    if raw is not None and raw in allowed:
        return raw
    return default
```

`_coerce_dir` is imported from `teams.views` (do not redefine). Each league
screen module that sorts adds:

```python
from teams.views import _coerce_dir
from matches.league_views import _coerce_sort_key
```

(`matches.league_views` already imports `from teams.views import …` for the
LG-01 generators, so a `teams.views` import in a league-screen module is the
established cross-app direction — no new cycle.)

## 2. Per-screen sort spec

Each screen below pins: query param(s), allowed sort-key frozenset (REAL
field/attr names verified against the code), default key + default direction
(preserving the screen's CURRENT order), the `(key, label)` display tuple and
where it lives, the `<th>` DOM ids, and how sort coexists with existing params.

Arrow glyphs (locked, verbatim from LG-00c): active-ascending header appends
` ↑` (U+2191), active-descending appends ` ↓` (U+2193), with a single leading
space, exactly as `templates/teams/player_list.html` renders them. Each
sortable `<th>` flips direction on the active column (asc↔desc) and links new
columns at `asc`.

The sort is **stable**: every view applies `sorted(rows, key=…, reverse=
(direction == "desc"))`. Where the current default order is meaningful, the
default key reproduces it (see per-screen notes). Secondary tiebreak: each
key tuple ends with a deterministic discriminator (noted per screen) so equal
primaries never reorder nondeterministically.

---

### 2a. Game Log — `matches/league_screens/game_log.py`

Single-table screen. Current order: `GameRound.objects…order_by("id")` →
chronological by id. Row dicts (verified keys):
`{round_id, matchday, date_played, team_red, team_blue, red_points, blue_points, winner}`
(`team_red` / `team_blue` / `winner` are `Team` objects or `None`;
`matchday` is `game_round.round_number`).

- **Query params:** single `?sort=&dir=` (single-table screen).
- **Allowed sort-key frozenset** (`_GAME_LOG_SORT_KEYS`, module-level in
  `game_log.py`):
  `frozenset({"matchday", "date_played", "team_red", "team_blue", "score", "winner"})`
  — derived from the rendered columns. Sort-value extraction per key:
  - `matchday` → `row["matchday"]` (int)
  - `date_played` → `row["date_played"]` (date; `None`-safe — see tiebreak)
  - `team_red` → `(row["team_red"].name if row["team_red"] else "")`
  - `team_blue` → `(row["team_blue"].name if row["team_blue"] else "")`
  - `score` → `row["red_points"] + row["blue_points"]` (total combined points)
  - `winner` → `(row["winner"].name if row["winner"] else "")`
- **Default key / dir:** `"date_played"` / `"asc"` — reproduces the current
  chronological-by-id order (date ascending; `row["round_id"]` is the
  always-appended secondary tiebreak so equal dates keep id order, i.e. the
  current behaviour byte-for-byte).
- **Display tuple** (`_GAME_LOG_SORT_KEYS_DISPLAY`, module-level inline tuple
  in `game_log.py`, mirrors `_SORT_KEYS_DISPLAY`):
  `(("matchday", "Matchday"), ("date_played", "Date"), ("team_red", "Red"), ("team_blue", "Blue"), ("score", "Score"), ("winner", "Winner"))`
- **`<th>` DOM ids:** `game-log-th-<key>` →
  `game-log-th-matchday` / `game-log-th-date_played` / `game-log-th-team_red`
  / `game-log-th-team_blue` / `game-log-th-score` / `game-log-th-winner`.
  (The existing `<table id="game-log-table">` and per-row
  `game-log-row-{round_id}` ids are unchanged.)
- **Coexists with `?team_id=`:** Game Log already has the LG-06b-style
  `?team_id=` filter (inline coercion against `enrolled_ids`). Sort applies
  AFTER the queryset filter, on the materialized `rows` list. The header
  hrefs MUST carry the current `team_id` (and `sort`/`dir`); the team-filter
  `<select>` form MUST carry the current `sort`/`dir` via hidden inputs so
  changing the team keeps the sort. Build the header href via a
  `querystring_without_sort_dir` helper (a `request.GET.copy()` with `sort` /
  `dir` popped, then `team_id` re-set to the coerced value) — the LG-00c
  `querystring_without_sort_dir_page` precedent, minus the `page` key (Game
  Log is not paginated).

---

### 2b. League Leaders — `matches/league_screens/league_leaders.py`

Multi-table screen — **four independent boards** keyed
`avg_tags` / `avg_score` / `fewest_tagged` / `tag_ratio`. Each board is a
`list[LeaderRow]` (the LG-01c dataclass:
`LeaderRow(player_id, player_name, role, team_id, team_name, value, games_played, rank)`).
Each board's CURRENT order is its own metric in its natural direction
(`avg_tags` desc, `avg_score` desc, `fewest_tagged` asc, `tag_ratio` desc) —
already ranked by the pure module with `rank` populated 1-based.

**`rank` is canonical and frozen.** Sorting re-orders the DISPLAYED rows but
the `LeaderRow.rank` field (the leaderboard standing) is NOT recomputed — it
keeps the metric-rank assigned by `compute_leaderboards`. So sorting the
"Average Tags" board by player name shows the rows name-ordered while each row
still displays its true tag-rank. (This is the locked semantics; the pure
module is untouched, so `rank` is whatever it computed.)

- **Query params:** NAMESPACED per-board —
  `?avg_tags_sort=&avg_tags_dir=`, `?avg_score_sort=&avg_score_dir=`,
  `?fewest_tagged_sort=&fewest_tagged_dir=`,
  `?tag_ratio_sort=&tag_ratio_dir=`. Sorting one board never resets a sibling.
- **Allowed sort-key frozenset** — SHARED across all four boards
  (`_LEADERS_SORT_KEYS`, module-level in `league_leaders.py`), derived from
  `LeaderRow` attribute names:
  `frozenset({"rank", "player_name", "team_name", "role", "value", "games_played"})`
  Sort-value extraction: `getattr(row, key)` for each (all are real
  `LeaderRow` attributes). `player_name` / `team_name` / `role` are strings;
  `rank` / `games_played` are ints; `value` is a float.
- **Default key / dir per board** (preserves each board's natural order):
  - `avg_tags` board → default `"rank"` / `"asc"` (rank 1 first = highest avg
    tags first — reproduces current desc-by-value display)
  - `avg_score` board → default `"rank"` / `"asc"`
  - `fewest_tagged` board → default `"rank"` / `"asc"` (rank 1 = least tagged)
  - `tag_ratio` board → default `"rank"` / `"asc"`
  Using `"rank"`/`"asc"` as the default for every board reproduces the exact
  current display order without re-deriving the metric direction view-side
  (the pure module already encoded each board's natural direction into
  `rank`). Secondary tiebreak: `row.player_id` (always appended).
- **Display tuple** — SHARED `_LEADERS_SORT_KEYS_DISPLAY` (module-level inline
  tuple in `league_leaders.py`):
  `(("rank", "#"), ("player_name", "Player"), ("team_name", "Team"), ("role", "Role"), ("value", "Value"), ("games_played", "GP"))`
  Each board renders the subset of these columns it actually shows; the
  current League Leaders template shows a 2-column `(player, value)` layout per
  board, so the **minimum** sortable headers are `player_name` and `value`
  (`rank` is rendered inline as `{{ row.rank }}.`). The Code agent MAY surface
  the full 6 columns per board; the locked requirement is that the two
  rendered sortable columns (`player_name`, `value`) carry the namespaced sort
  links + `<th>` DOM ids below.
- **`<th>` DOM ids:** `league-leaders-<board>-th-<key>` where `<board>` ∈
  `{avg_tags, avg_score, fewest_tagged, tag_ratio}` —
  e.g. `league-leaders-avg_tags-th-player_name`,
  `league-leaders-avg_tags-th-value`,
  `league-leaders-tag_ratio-th-player_name`, etc. (The existing board
  `<table>` ids `leaders-avg-tags` / `leaders-avg-score` /
  `leaders-fewest-tagged` / `leaders-tag-ratio` are unchanged.)
- **Coexists with existing params:** League Leaders has no `team_id` /
  pagination today. The four namespaced param-pairs are the only query state;
  each board's header href carries ALL eight params (so flipping board A's
  sort preserves boards B/C/D's current sort) via a single
  `querystring_without` helper that pops only that board's own
  `<board>_sort` / `<board>_dir` before re-encoding.

---

### 2c. Team History — `matches/league_screens/team_history.py`

Multi-table screen with TWO sortable row-tables (**Seasons** + **Players**)
and one NON-sortable surface (**Overall** — a single `dl` W-L-T record, gets
NO sort per locked decision #5). The Players table is **already paginated**
(LG-06a: `page_obj` / `paginator` / `players_querystring_without_page`).

#### Seasons table — `season_rows: list[SeasonRow]`
`SeasonRow(season_id, year, wins, losses, ties, rank)`. Current order: the
view sorts Seasons **newest-first by id** before calling `compute_season_rows`
(`sorted(…, key=lambda s: s.id, reverse=True)`).

- **Query params:** `?seasons_sort=&seasons_dir=`.
- **Allowed sort-key frozenset** (`_SEASONS_SORT_KEYS`):
  `frozenset({"year", "wins", "losses", "ties", "rank"})`
  Sort-value: `getattr(row, key)`. `rank` and `year` are `int | None` →
  `None`-safe extraction (see §3 None handling); `wins`/`losses`/`ties` ints.
- **Default key / dir:** `"year"` / `"desc"` — reproduces newest-first
  (current order is newest-Season-first; year desc matches, with
  `row.season_id` desc as the always-appended secondary tiebreak so two
  Seasons in the same calendar year keep newest-id-first).
- **Display tuple** (`_SEASONS_SORT_KEYS_DISPLAY`, inline in
  `team_history.py`):
  `(("year", "Year"), ("record", "Record (W-L-T)"), ("rank", "Final rank"))`
  — NOTE the Record column is the rendered `{{wins}}-{{losses}}-{{ties}}`
  triple; its `<th>` is sortable on the `wins` key (locked: the Record header
  sorts by `wins`). So the rendered header→key map is
  `Year→year`, `Record (W-L-T)→wins`, `Final rank→rank`. (`losses`/`ties`
  remain in the allowed set for completeness/forgiving-fallback but are not
  surfaced as their own headers.)
- **`<th>` DOM ids:** `team-history-seasons-th-<key>` →
  `team-history-seasons-th-year`, `team-history-seasons-th-wins`,
  `team-history-seasons-th-rank`. (`<table id="team-history-seasons-table">`
  and `team-history-season-row-{season_id}` unchanged.)
- **Coexists with `?team_id=`:** Team History resolves the displayed team from
  `?team_id=` (validated against enrolment). The Seasons-table header hrefs
  MUST carry `team_id` (and `players_sort`/`players_dir` so they don't reset
  the Players table). Build via a `seasons_querystring_without_sort` helper
  (pop `seasons_sort`/`seasons_dir`, keep `team_id` + the players params).

#### Players table — `page_obj` over `player_rollups: list[PlayerRollup]`
`PlayerRollup(player_id, name, on_team, games_played, last_season_year, stats)`
where `stats` is a dict with keys
`points_scored, tags_made, times_tagged, missiles_landed, resupplies_given, specials_used`.
Current order: the pure module returns rollups sorted `(name, player_id)` asc.

- **Query params:** `?players_sort=&players_dir=` (+ existing `?page=` +
  `?team_id=` + `?per_page=`).
- **Allowed sort-key frozenset** (`_TH_PLAYERS_SORT_KEYS`):
  `frozenset({"name", "games_played", "points_scored", "tags_made", "times_tagged", "missiles_landed", "resupplies_given", "specials_used", "last_season_year"})`
  Sort-value extraction (mixed top-level attrs + nested `stats` keys):
  - `name` → `row.name`
  - `games_played` → `row.games_played`
  - `last_season_year` → `row.last_season_year` (`int | None`, None-safe)
  - the six stat keys → `row.stats.get(<key>, 0)`
- **Default key / dir:** `"name"` / `"asc"` — reproduces the pure module's
  `(name, player_id)` order; `row.player_id` is the always-appended secondary
  tiebreak.
- **Display tuple** (`_TH_PLAYERS_SORT_KEYS_DISPLAY`, inline):
  `(("name", "Name"), ("games_played", "Games"), ("points_scored", "Points"), ("tags_made", "Tags"), ("times_tagged", "Times tagged"), ("missiles_landed", "Missiles"), ("resupplies_given", "Resupplies"), ("specials_used", "Specials"), ("last_season_year", "Last season"))`
  — exactly the nine rendered Players-table columns, in order.
- **`<th>` DOM ids:** `team-history-players-th-<key>` →
  `team-history-players-th-name`, `…-games_played`, `…-points_scored`,
  `…-tags_made`, `…-times_tagged`, `…-missiles_landed`, `…-resupplies_given`,
  `…-specials_used`, `…-last_season_year`. (`<table id="team-history-players-table">`,
  `team-history-player-row-{player_id}`, `team-history-players-pagination`,
  `team-history-per-page-form`, `team-history-per-page-select` unchanged.)
- **SORT-BEFORE-PAGINATION (load-bearing):** the view MUST
  `player_rollups = sorted(player_rollups, key=…, reverse=…)` **BEFORE**
  constructing `Paginator(player_rollups, per_page)`. The whole rollup list is
  already materialized in memory (it is the pure module's return), so this is
  the same "sort the full list, then paginate" shape LG-06a established.
- **Sort change MUST reset to page 1.** The Players `<th>` header hrefs MUST
  drop `page=` (so re-sorting lands on page 1), while CARRYING `team_id`,
  `per_page`, `seasons_sort`, `seasons_dir`. The existing
  `players_querystring_without_page` helper (LG-06a — carries `team_id`, omits
  `page`) MUST be extended to ALSO carry `players_sort` / `players_dir` so
  pagination Previous/Next links preserve the sort, AND a sibling
  `players_querystring_without_sort_page` helper (pop `players_sort` /
  `players_dir` / `page`, keep `team_id` + `per_page` + `seasons_*`) MUST back
  the column-header hrefs so clicking a header resets to page 1. The per-page
  `<select>` form's hidden inputs MUST additionally carry `players_sort` /
  `players_dir` (it already carries `team_id`) so changing page size keeps the
  sort. This mirrors the LG-00c
  `querystring_without_page` / `querystring_without_sort_dir_page` split.
- **Overall tab:** NO sort, NO params, NO `<th>` ids changed.

---

### 2d. Watch List — `matches/league_screens/watch_list.py`

Single sortable row-table — the `watched_players` list. Rows are **`Player`
ORM objects** (not dicts): rendered columns are `player.name`,
`player.team.name`, `player.overall_rating`. Current order: session-list
insertion order (the order ids were added to `request.session["watch_list"]`).

- **Query params:** single `?sort=&dir=` (single-table screen).
- **Allowed sort-key frozenset** (`_WATCH_LIST_SORT_KEYS`):
  `frozenset({"name", "team", "overall_rating"})`
  Sort-value extraction (on `Player` objects):
  - `name` → `player.name`
  - `team` → `player.team.name` (the `Player.team` FK is `select_related`-ed)
  - `overall_rating` → `player.overall_rating` (the `@property`, float)
- **Default key / dir:** `"name"` / `"asc"`. **RATIONALE for not preserving
  session order:** session insertion order is not a stable column to sort by
  (it is not a rendered field), so the default becomes name-ascending — a
  deterministic, user-meaningful default. This is an intentional behavioural
  refinement noted here so the Tests + Docs agents expect name-asc default
  rather than insertion order. Secondary tiebreak: `player.id`.
- **Display tuple** (`_WATCH_LIST_SORT_KEYS_DISPLAY`, inline in
  `watch_list.py`):
  `(("name", "Player"), ("team", "Team"), ("overall_rating", "Overall"))`
  — the three sortable columns; the 4th column ("Action" — the Remove button)
  is NOT sortable and gets no header link / DOM id.
- **`<th>` DOM ids:** `watch-list-th-<key>` → `watch-list-th-name`,
  `watch-list-th-team`, `watch-list-th-overall_rating`. (`<table
  id="watch-list-table">`, `watch-list-row-{player.id}`,
  `watch-list-remove-all`, `watch-list-add*`, `watch-list-empty-notice`
  unchanged.)
- **`addable_players` stays name-ordered** — NO sort applied to the add
  control's `<select>`; only `watched_players` is sorted.
- **GET-toggle behaviour UNCHANGED:** the existing
  `?action=add|remove|clear` GET toggle still redirects to the **bare**
  watch-list URL (`/leagues/<id>/players/watch-list/`). Sort params (`sort` /
  `dir`) are DROPPED on a mutation redirect — **this is acceptable and
  locked** (a mutation resets to the default sort; the user re-sorts after).
  No change to the toggle/redirect code path.
- **Sort applies on the plain-GET render path only**, after the
  `watched_players` list is built, before the context is assembled. Header
  hrefs carry `sort` / `dir` (no other params on this screen).

---

### 2e. Statistical Feats — `matches/league_screens/statistical_feats.py`

The Feats surface is **a flat ordered `<ul id="stat-feats-list">` of
`FeatRecord`s, NOT a `<table>`.** `FeatRecord(kind, label, name, value,
round_id)` where `value` is a **string** (e.g. `"12"`, `"45.3"`,
`"Comeback"`). The current order is `scan_feats`'s stable predicate-listing
order (documented in `stat_feats.py`).

**LG-06c treatment:** because it is a list of heterogeneous records (not a
columnar table), Statistical Feats gets a SINGLE sort control over the feat
records — a `?sort=&dir=` pair driving a sortable header row rendered ABOVE
the list (or a small `<thead>`-like control). Sortable on the three
record-level attributes that are uniform across all feats:

- **Query params:** single `?sort=&dir=`.
- **Allowed sort-key frozenset** (`_FEATS_SORT_KEYS`):
  `frozenset({"kind", "name", "value"})`
  Sort-value extraction (on `FeatRecord`):
  - `kind` → `feat.kind` (str)
  - `name` → `feat.name` (str — player or team name)
  - `value` → numeric-aware: `_feat_value_sort_key(feat.value)` — try
    `float(feat.value)`; on `ValueError` fall back to a sentinel that sorts
    non-numeric values together (e.g. `(1, feat.value)` vs numeric
    `(0, float_val)`) so `"Comeback"` doesn't crash the sort. This tuple-pair
    approach keeps the sort total and deterministic.
- **Default key / dir:** `"kind"` / `"asc"`. **RATIONALE:** the current
  predicate-listing order is not a sortable field; `kind`-ascending is the
  deterministic default that groups same-kind feats together. The Tests +
  Docs agents expect kind-asc default, NOT the predicate order. Secondary
  tiebreak: `feat.label` (always appended; stable for equal kind/value).
- **Display tuple** (`_FEATS_SORT_KEYS_DISPLAY`, inline in
  `statistical_feats.py`):
  `(("kind", "Feat"), ("name", "Who"), ("value", "Value"))`
- **`<th>` / control DOM ids:** `statistical-feats-th-<key>` →
  `statistical-feats-th-kind`, `statistical-feats-th-name`,
  `statistical-feats-th-value`. (The existing `stat-feats-list`,
  `stat-feat-{kind}`, `stat-feats-empty-notice`,
  `statistical-feats-team-filter-form` / `-select` are unchanged. The sort
  headers attach to a small sort-control bar the Code agent adds above the
  `<ul>` — the locked requirement is the three `statistical-feats-th-<key>`
  ids carrying the sort links.)
- **Coexists with `?team_id=`:** Statistical Feats already has the LG-06b
  `?team_id=` filter (via `_coerce_team_id`). Sort applies AFTER
  `stat_feats.scan_feats(...)` returns, on the `feats` list. Header hrefs
  carry the current `team_id` (and `sort`/`dir`); the team-filter `<select>`
  form carries the current `sort`/`dir` via hidden inputs. Build via a
  `querystring_without_sort` helper (pop `sort`/`dir`, keep `team_id`).

## 3. Cross-cutting rules

- **`None`-safe sort keys.** `date_played`, `year`, `rank`,
  `last_season_year` are `… | None`. The sort key function MUST wrap each in a
  `(is_none_flag, value)` tuple — e.g.
  `key=lambda r: (r.rank is None, r.rank if r.rank is not None else 0)` —
  so `None` rows sort to one end deterministically and never raise
  `TypeError: '<' not supported between NoneType and int`. (Locked: `None`
  sorts LAST in ascending order — `True > False` puts the none-flag group at
  the end.)
- **Stable secondary tiebreak per screen** (always appended to the key tuple,
  listed per screen above): Game Log `round_id`; League Leaders `player_id`;
  Team History Seasons `season_id` (desc-paired with the default), Players
  `player_id`; Watch List `player.id`; Feats `feat.label`.
- **Coerce BEFORE building querystring helpers** (LG-00c-7 fix precedent): the
  view assigns the COERCED `sort` / `dir` (and namespaced variants) back into
  the `QueryDict` copy before `.urlencode()`, so an invalid
  `?sort=BOGUS&dir=SIDEWAYS` never survives into the rendered header /
  pagination hrefs.
- **Template active-header rendering** mirrors `player_list.html` exactly:
  for the active column, the header `<a>` flips `dir` and appends ` ↑`
  (currently asc) or ` ↓` (currently desc); inactive columns link at `asc`
  with no glyph.
- **Display tuples live where the keys live.** Single-screen tuples are
  module-level inline tuples in each screen module (the LG-00c
  `_SORT_KEYS_DISPLAY` precedent — NOT a shared constant), because each
  screen's column set is unique. There is NO cross-screen shared display
  constant. The only shared symbol is the coercion helper `_coerce_sort_key`
  (+ the imported `_coerce_dir`).

## 4. Context keys added per screen (the view ↔ template seam)

Each sortable table adds, to its existing context dict, the coerced sort
state + its display tuple + the querystring helper(s) the headers/pagination
need. Naming mirrors LG-00c (`sort`, `dir`, `sort_keys`,
`querystring_without_sort_dir_page`). Namespaced screens prefix the keys.

- **Game Log:** `sort`, `dir`, `sort_keys` (the display tuple),
  `querystring_without_sort_dir` (carries `team_id`, omits `sort`/`dir`).
- **League Leaders:** per board `<board>_sort`, `<board>_dir`,
  `leaders_sort_keys` (shared display tuple), plus per-board
  `<board>_querystring_without_sort` helpers (pop only that board's pair).
- **Team History:** `seasons_sort`, `seasons_dir`, `seasons_sort_keys`,
  `seasons_querystring_without_sort`; `players_sort`, `players_dir`,
  `players_sort_keys`, `players_querystring_without_sort_page` (column
  headers, resets page), and the EXTENDED `players_querystring_without_page`
  (pagination links — now also carries `players_sort`/`players_dir`).
- **Watch List:** `sort`, `dir`, `sort_keys` (no querystring helper needed —
  only `sort`/`dir` exist; headers link `?sort=…&dir=…` directly).
- **Statistical Feats:** `sort`, `dir`, `sort_keys`,
  `querystring_without_sort` (carries `team_id`).

## 5. Files touched

**Production (12 files):**
- `matches/league_views.py` — add `_coerce_sort_key` (the single new helper).
- `matches/league_screens/game_log.py` — sort `rows`; add context keys.
- `matches/league_screens/league_leaders.py` — sort each of the 4 boards
  (view-side, on the pure-module output); add namespaced context keys.
- `matches/league_screens/team_history.py` — sort `season_rows` (Seasons) and
  `player_rollups` BEFORE pagination (Players); add namespaced context keys +
  extend querystring helpers.
- `matches/league_screens/watch_list.py` — sort `watched_players`; add context
  keys.
- `matches/league_screens/statistical_feats.py` — sort `feats`; add context
  keys.
- `templates/leagues/game_log.html` — sortable `<th>`s + glyphs.
- `templates/leagues/league_leaders.html` — namespaced sortable `<th>`s per
  board + glyphs.
- `templates/leagues/team_history.html` — sortable Seasons + Players `<th>`s,
  page-reset header hrefs, per-page form hidden inputs carry sort.
- `templates/leagues/watch_list.html` — sortable `<th>`s on watched table.
- `templates/leagues/statistical_feats.html` — sort-control bar with the three
  `statistical-feats-th-<key>` headers above the `<ul>`.
- (Helper import line `from teams.views import _coerce_dir` /
  `from matches.league_views import _coerce_sort_key` added to each of the 5
  screen modules.)

**Tests (5 files — append, do not rewrite):**
- `matches/tests/test_lg01z_game_log.py`
- `matches/tests/test_lg01z_league_leaders.py`
- `matches/tests/test_lg01z_team_history.py`
- `matches/tests/test_lg01z_watch_list.py`
- `matches/tests/test_lg01z_statistical_feats.py`

**NOT touched (explicit):**
- Pure modules: `matches/stat_feats.py`, `matches/team_history_logic.py`,
  `matches/league_leaders_logic.py`, `matches/power_rankings_logic.py`,
  `matches/season_player_stats.py`, `matches/team_stats_logic.py`,
  `matches/standings.py`, `matches/season_dashboard.py` (incl. `LeaderRow`).
- `teams/views.py` (`_coerce_dir` / `_VALID_DIRS` imported verbatim, NOT
  edited).
- Player Ratings + Player Stats screens / templates (already sortable —
  reference pattern only).
- Power Rankings, Team Roster, Free Agents, Team Stats screens — NOT in the
  LG-06c five (Power Rankings already has its own `SORT_KEYS`; the others are
  out of LG-06c scope).
- Models, migrations, URL routes (`matches/league_urls.py`),
  `_FEATURE_REGISTRY`, sidebar/topbar builders, simulator, RNG, CONTEXT.md,
  ADRs, score calibration.

## 6. Test boundary (what the Tests agent asserts, per screen + file)

Shared assertion shapes (mirror LG-00c `TestPlayerListView` /
`TestCoerceSortAndDir`):

**Helper unit tests** (in `test_lg01z_game_log.py` or wherever convenient —
the helper is shared, one home suffices): `_coerce_sort_key` accepts every key
in a given `allowed` frozenset; falls back to `default` on `None` / empty
string / unknown value. (`_coerce_dir` is already covered by LG-00c — no need
to re-test, but a smoke assert that the import resolves is fine.)

**Per-screen view tests** (`reverse(<url_name>, args=[league_id])` + test
client, real ORM, no mocks):

- **Game Log** (`test_lg01z_game_log.py`):
  - default order == `date_played` asc (chronological, == current by-id order)
    on a fixture with ≥2 played Rounds across ≥2 dates.
  - each key in `_GAME_LOG_SORT_KEYS` sorts asc and desc (assert first row).
  - invalid `?sort=BOGUS` → falls back to `date_played`; invalid `?dir=NOPE`
    → falls back to `asc`.
  - `?sort=&dir=` coexists with `?team_id=`: GET `?team_id=<id>&sort=score&dir=desc`
    returns only that team's Rounds, score-desc; the header hrefs carry
    `team_id`; the team-filter form hidden inputs carry `sort`/`dir`.
  - active `<th id="game-log-th-score">` renders ` ↑`/` ↓`.

- **League Leaders** (`test_lg01z_league_leaders.py`):
  - default order per board == current `rank`-asc order on a fixture
    populating all four boards.
  - **namespaced-param independence:** GET
    `?avg_tags_sort=player_name&avg_tags_dir=asc` re-orders ONLY the avg_tags
    board; the other three boards stay in `rank` order. Assert a row from a
    sibling board is unmoved.
  - each shared sort key sorts the avg_tags board asc/desc.
  - invalid `?avg_tags_sort=BOGUS` → falls back to `rank`.
  - `<th id="league-leaders-avg_tags-th-player_name">` present + glyph on
    active.

- **Team History** (`test_lg01z_team_history.py`):
  - **Seasons:** default `year` desc (newest first); each `_SEASONS_SORT_KEYS`
    key asc/desc; `None` year/rank rows sort last in asc; invalid fallback.
  - **Players:** default `name` asc; each `_TH_PLAYERS_SORT_KEYS` key asc/desc
    (incl. nested `stats.*` keys like `points_scored`, `tags_made`).
  - **sort-before-pagination:** on a fixture with > `per_page` players, GET
    `?players_sort=points_scored&players_dir=desc&page=1` puts the GLOBAL
    highest-points player on page 1 (proves the full list is sorted before
    `Paginator`, not just the current page).
  - **sort change resets to page 1:** the Players `<th>` href omits `page=`.
  - **pagination preserves sort:** Previous/Next hrefs carry
    `players_sort`/`players_dir` + `team_id` + `per_page`.
  - **namespaced independence:** `?seasons_sort=…` does not reset
    `players_sort` and vice versa.
  - `?team_id=` coexistence: switching team via the picker form carries both
    `seasons_*` and `players_*` sort state.
  - Overall tab has NO sort headers (assert no `team-history-overall-th-*`).

- **Watch List** (`test_lg01z_watch_list.py`):
  - default `name` asc on the `watched_players` table (seed the session with
    ≥2 ids, assert name-ordered render).
  - each key (`name`/`team`/`overall_rating`) sorts asc/desc.
  - invalid `?sort=`/`?dir=` → fall back to `name`/`asc`.
  - `addable_players` order is UNAFFECTED by `?sort=` (stays name-ordered).
  - a mutation (`?action=add&player_id=<id>`) still 302-redirects to the bare
    URL and DROPS `sort`/`dir` (assert the redirect target has no query
    string).
  - `<th id="watch-list-th-overall_rating">` glyph on active.

- **Statistical Feats** (`test_lg01z_statistical_feats.py`):
  - default `kind` asc on a fixture producing ≥2 distinct feat kinds.
  - sort by `name` asc/desc; sort by `value` asc/desc with a MIX of numeric
    (`"12"`, `"45.3"`) and non-numeric (`"Comeback"`) values — assert no
    crash and a deterministic total order (non-numeric grouped at one end).
  - invalid `?sort=`/`?dir=` → fall back to `kind`/`asc`.
  - `?team_id=` coexistence: GET `?team_id=<id>&sort=value&dir=desc` filters
    the feat inputs (player_rounds + matches) AND sorts; header hrefs carry
    `team_id`; team-filter form hidden inputs carry `sort`/`dir`.
  - `<th id="statistical-feats-th-value">` present + glyph on active.

All five files: assert the screen still returns 200 / 405 (non-GET) / 404
(missing League) / empty-state (no Season) exactly as before — the sort
addition must not regress the LG-01z shared-contract behaviour.
