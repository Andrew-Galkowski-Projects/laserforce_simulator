# LG-06a Seam Contract — page-size `<select>` + Team-History Players pagination

UI-only, read-only. **No model change, no migration, no CONTEXT.md, no ADR, no
simulator / RNG, no Score Calibration re-baseline.** Three rating/stats screens
already paginate view-side — they get the page-size `<select>` UI plus a single
`per_page_options` context add. Team History gains Paginator wiring on the
**Players section only** (Overall + Seasons sections untouched).

Coordination artifact for three parallel agents (Code / Tests / Docs). All
names below are pinned against the live code, not guessed.

---

## Shared facts (verified against the code)

- Shared whitelist tuple: `matches.league_views._LG01F_PER_PAGE_OPTIONS = (10, 25, 50, 100)`.
  **Single source — do NOT hardcode the list in any template.** Feed
  `per_page_options` from this tuple. Whether to expose a public alias
  (e.g. `PER_PAGE_OPTIONS = _LG01F_PER_PAGE_OPTIONS`) is the **Code agent's
  call**; the only constraint is one source.
- Reused helpers (verbatim, no new helpers):
  - `matches.league_views._coerce_per_page(raw, default=10) -> int` (whitelists `(10,25,50,100)`)
  - `matches.league_views._coerce_page(raw, default=1) -> int`
- The three rating/stats views already import `_coerce_per_page` / `_coerce_page`
  and already set context keys `per_page`, `page_obj`, `paginator`,
  `querystring_without_page`, `querystring_without_sort_dir_page`.
- LG-01f precedent (`templates/leagues/history.html` lines ~65–73): the page-size
  `<form method="get">` wraps a `<select name="per_page" onchange="this.form.submit()">`
  iterating `per_page_options`, each `<option value="{{ option }}"{% if option == per_page %} selected{% endif %}>`,
  with a `<noscript>` submit button. DOM-id convention:
  `league-history-per-page-form` / `league-history-per-page-select`.
- `team_history.py` does **not** currently import the pagination helpers and has
  **no** `Paginator`; its Players section renders `{% for p in player_rollups %}`
  with a `{% empty %}` fallback over the unbounded list.

---

## Per-file change list

### 1. `matches/league_screens/free_agents.py` (VIEW)
- Add **one** context key: `per_page_options` (= the shared tuple).
- Set it on the **base `context` dict** (before the empty-state early return) so
  the `<select>` renders even in the no-Season empty state if desired — at
  minimum it must be present on the success render. No other view change.

### 2. `matches/league_screens/player_ratings.py` (VIEW)
- Identical to free_agents: add `per_page_options` to context. No other change.

### 3. `matches/league_screens/player_stats.py` (VIEW)
- Identical: add `per_page_options` to context. No other change.

### 4. `templates/leagues/free_agents.html` (TEMPLATE)
- Add a page-size `<form method="get">` + `<select>` inside the `{% else %}`
  (Season-present, rows-present) body block — beside/above the existing
  `id="free-agents-pagination"` nav.
- Form id `free-agents-per-page-form`; select id `free-agents-per-page-select`,
  `name="per_page"`, `onchange="this.form.submit()"`, `<noscript>` submit button.
- Options iterate `per_page_options`; `selected` when `option == per_page`.
- **Hidden inputs (form OMITS `page`):**
  `<input type="hidden" name="sort" value="{{ sort }}">` and
  `<input type="hidden" name="dir" value="{{ dir }}">`.

### 5. `templates/leagues/player_ratings.html` (TEMPLATE)
- Same as free_agents. Form id `player-ratings-per-page-form`; select id
  `player-ratings-per-page-select`. Hidden inputs `sort` + `dir`. Omit `page`.

### 6. `templates/leagues/player_stats.html` (TEMPLATE)
- Same. Form id `player-stats-per-page-form`; select id
  `player-stats-per-page-select`. Hidden inputs `sort` + `dir`. Omit `page`.

### 7. `matches/league_screens/team_history.py` (VIEW — Players pagination wiring)
- Import `Paginator` (`from django.core.paginator import Paginator`) and the
  reused helpers `_coerce_per_page`, `_coerce_page` from `matches.league_views`.
- Compute `per_page = _coerce_per_page(request.GET.get("per_page"))`.
- In the **team-present** branch, wrap the Players rollups in a Paginator:
  `paginator = Paginator(player_rollups, per_page)` then
  `page_obj = paginator.get_page(_coerce_page(request.GET.get("page")))`
  (where `player_rollups` is the list from `_build_players_context`'s
  `{"player_rollups": ...}` — `_build_players_context` itself is UNCHANGED).
- Build a `players_querystring_without_page`: copy `request.GET`, `pop("page")`,
  force `team_id` to the resolved `team.id`, then `.urlencode()` — so Players
  pagination links carry `team_id` and omit `page`.
- Add context keys (team-present branch): `per_page`, `per_page_options`,
  `page_obj`, `paginator`, `players_querystring_without_page`.
- **Empty-state branches** (no Season; no resolvable team): set `page_obj = None`,
  `paginator = None`, `players_querystring_without_page = ""`, plus `per_page`
  (coerced) and `per_page_options`, so the template never `NameError`s.
- Overall + Seasons context (`overall_record`, `season_rows`) UNCHANGED.

### 8. `templates/leagues/team_history.html` (TEMPLATE)
- Inside `<section id="team-history-players">` (table id
  `team-history-players-table` is preserved): change the Players `<tbody>` loop
  from `{% for p in player_rollups %}` to `{% for p in page_obj %}`, **keeping
  the existing `{% empty %}` fallback row** (colspan 9).
- Add a page-size `<form method="get">` + `<select>` adjacent to the Players
  table (within the Players section). Form id `team-history-per-page-form`;
  select id `team-history-per-page-select`, `name="per_page"`,
  `onchange="this.form.submit()"`, `<noscript>` submit. Options iterate
  `per_page_options`; `selected` when `option == per_page`.
  - **Hidden input (form OMITS `page`):**
    `<input type="hidden" name="team_id" value="{{ team.id }}">`.
- Add a Players pagination `<nav id="team-history-players-pagination">`, rendered
  only when `paginator.num_pages > 1` (or `page_obj.has_other_pages`). Prev/Next
  links use `?{{ players_querystring_without_page }}&page=...` so each link
  carries `team_id` and omits the old `page`.
- **The team-picker `<form>` (`?team_id=`, select id `team-history-team-picker`)
  gains a hidden `per_page` input** so switching team preserves the chosen page
  size: `<input type="hidden" name="per_page" value="{{ per_page }}">`
  (page still resets — the picker form does not carry `page`).
- Overall + Seasons sections (`team-history-overall`, `team-history-seasons`,
  `team-history-seasons-table`) UNTOUCHED.

---

## New DOM ids (per screen)

| Screen        | per-page form id                | per-page select id                 | players pagination nav id              |
|---------------|---------------------------------|------------------------------------|----------------------------------------|
| Free Agents   | `free-agents-per-page-form`     | `free-agents-per-page-select`      | (existing `free-agents-pagination`)    |
| Player Ratings| `player-ratings-per-page-form`  | `player-ratings-per-page-select`   | (existing `player-ratings-pagination`) |
| Player Stats  | `player-stats-per-page-form`    | `player-stats-per-page-select`     | (existing `player-stats-pagination`)   |
| Team History  | `team-history-per-page-form`    | `team-history-per-page-select`     | `team-history-players-pagination` (NEW)|

## Hidden-input set (per page-size form)

| Screen        | Hidden inputs (form OMITS `page`) |
|---------------|-----------------------------------|
| Free Agents   | `sort`, `dir`                     |
| Player Ratings| `sort`, `dir`                     |
| Player Stats  | `sort`, `dir`                     |
| Team History  | `team_id`                         |

Team-History **team-picker** form (`team-history-team-picker`) additionally gains
a hidden `per_page` input (still omits `page`).

---

## Test boundary

Extend the existing per-screen test files; add the new assertions to the
existing `*Pagination` class where one exists, or a small new
`Test<Screen>PerPageSelector` class.

- `matches/tests/test_lg01z_free_agents.py` (NEW pagination/per-page class —
  this file currently has none): assert `free-agents-per-page-select` present;
  selected `<option>` reflects the requested `per_page`; hidden `sort` + `dir`
  inputs present in the per-page form.
- `matches/tests/test_lg01z_player_ratings.py` (extend `TestPlayerRatingsPagination`):
  assert `player-ratings-per-page-select` present; selected option reflects
  `per_page`; hidden `sort` + `dir` inputs present.
- `matches/tests/test_lg01z_player_stats.py` (extend `TestPlayerStatsPagination`):
  assert `player-stats-per-page-select` present; selected option reflects
  `per_page`; hidden `sort` + `dir` inputs present.
- `matches/tests/test_lg01z_team_history.py` (extend `TestTeamHistoryPlayers`,
  or add `TestTeamHistoryPlayersPagination`):
  - `team-history-per-page-select` present; selected option reflects `per_page`;
    hidden `team_id` input present in the per-page form.
  - team-picker form carries a hidden `per_page` input.
  - **>10-player pagination test:** seed > 10 distinct players for the team,
    GET `?per_page=10&page=2`, assert `team-history-players-pagination` renders
    and the page-2 (Prev/Next) link href **carries `team_id=<id>`** and does not
    carry a stale `page`. Page 1 shows 10 rows; page 2 shows the remainder.

Tests must not enter the simulator (no `simulate_*` / `save_games`).

---

## Explicit scope-out

- No model change, no migration, no CONTEXT.md edit, no ADR, no simulator / RNG
  touch, no Score Calibration re-baseline.
- Team History **Overall + Seasons** sections untouched (no pagination, no
  context change). Only the **Players** section (`player_rollups`) paginates.
- The three rating/stats **views** get ONLY the `per_page_options` context add —
  no change to their existing pagination logic, querystring helpers, or sort
  handling.
- `_build_players_context` (returns `{"player_rollups": ...}`) is unchanged —
  pagination happens in the `team_history` view body, not in the helper.
- No new pagination helpers; `_coerce_per_page` / `_coerce_page` reused verbatim.
