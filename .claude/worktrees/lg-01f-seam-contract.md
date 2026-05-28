# LG-01f — League History · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01f
ships the **read-only League History page** plus the **new 14-entry
sidebar partial** (LEAGUE / TEAM / PLAYERS sections), the **top-bar
"League" dropdown** that replaces the LG-01a `Leagues` link, the **session
write site** on every League-context view, and the **`league_nav` context
processor** that resolves the top-bar History link's per-user `League`
target. **No model change, no migration, no new pure module, no
simulator touch, no JS, no Celery, no `messages.*`, no API endpoint, no
new dependency, no admin change, no CONTEXT.md edit.**

The design decisions live in
[`docs/adr/0017-league-context-nav-shape.md`](../../docs/adr/0017-league-context-nav-shape.md)
(ADR-0017). This contract cites the ADR; it does **not** re-justify
decisions.

This contract mirrors the structure of
[`.claude/worktrees/lg-01c-seam-contract.md`](lg-01c-seam-contract.md)
(read-only dashboard precedent — view body, template + DOM ids, pure
module avoidance) and
[`.claude/worktrees/lg-01e-seam-contract.md`](lg-01e-seam-contract.md)
(URL-insertion + LG-01c sidebar replacement precedent). It extends the
LG-01 / LG-01a / LG-01b / LG-01c / LG-01d / LG-01e foundation.

---

## 0. Locked Names (front-loaded — copy-paste source for Step 7 agents)

Every public name LG-01f introduces or pins, in one place. Every name
below is final; any drift in Step 7 is rework.

| Kind | Name | Notes |
|---|---|---|
| URL path | `/leagues/<int:league_id>/history/` | Inserted AFTER LG-01e `<int:league_id>/next-season/` and BEFORE LG-01a `""` in `matches/league_urls.py` |
| URL name | `league_history` | Bare name, no `app_name`. `reverse("league_history", kwargs={"league_id": league.id})` |
| URL file edit | `matches/league_urls.py` | Single-line insert; final order `[create/, <int:league_id>/, <int:league_id>/next-season/, <int:league_id>/history/, ""]` |
| View | `matches.views.league_history` | `(request: HttpRequest, league_id: int) -> HttpResponse`, no decorator (read-only), GET-only via `HttpResponseNotAllowed(["GET"])` as the first line of the body |
| Helper | `matches.views._build_history_row` | Module-level flat helper, `(season: Season, teams_by_id: dict[int, Team], *, is_in_progress: bool) -> dict`, returns the 11-key row dict (§4) |
| Helper | `matches.views._build_league_sidebar_links` | Module-level flat helper, `(league: League, displayed_season: Season \| None, sidebar_active: str \| None) -> list[dict]`, returns the 14-entry sidebar list (§5) |
| Helper | `matches.views._coerce_per_page` | Module-level flat helper, `(raw: str \| None, default: int = 10) -> int`, whitelist `(10, 25, 50, 100)` |
| Helper | `matches.views._coerce_page` | Module-level flat helper, `(raw: str \| None, default: int = 1) -> int`, Django `Paginator` semantics; invalid ⇒ 1 |
| Helper DELETED | `matches.views._season_sidebar_links` | The LG-01c 5-entry helper is removed wholesale (replaced by `_build_league_sidebar_links`) |
| Context processor (NEW) | `core.context_processors.league_nav` | `(request: HttpRequest) -> dict`, returns `{"top_bar_history_url": str}` |
| Context processor file | `core/context_processors.py` | NEW; single function `league_nav`; registered in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` |
| Sidebar partial (NEW) | `templates/_partials/league_sidebar.html` | Iterates `sidebar_links`; renders 4 sections (top / LEAGUE / TEAM / PLAYERS) |
| Template (NEW) | `templates/leagues/history.html` | Extends `base.html`; `{% block title %}{{ league.name }} — History{% endblock %}` (em-dash U+2014) |
| Template MODIFIED | `templates/base.html` | Replace LG-01a `Leagues` `<a>` with Bootstrap dropdown |
| Template MODIFIED | `templates/leagues/dashboard.html` | Include sidebar partial; set `sidebar_active="dashboard"` |
| Template MODIFIED | `templates/seasons/dashboard.html` | Include sidebar partial; set `sidebar_active=None` |
| Template MODIFIED | `templates/seasons/standings.html` | Include sidebar partial; set `sidebar_active="standings"` |
| Template MODIFIED | `templates/seasons/schedule.html` | Include sidebar partial; set `sidebar_active="schedule"` |
| `sidebar_active` literal | `"dashboard"` | League dashboard active value |
| `sidebar_active` literal | `"standings"` | Season standings active value |
| `sidebar_active` literal | `"schedule"` | Season schedule active value (LEAGUE section); the Season Schedule page sets this and it matches the LEAGUE > Schedule sidebar entry |
| `sidebar_active` literal | `"playoffs"` | Reserved (LG-02+); never set by LG-01f |
| `sidebar_active` literal | `"finances"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `"history"` | League history active value |
| `sidebar_active` literal | `"power_rankings"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `"roster"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `"schedule_team"` | Reserved (TEAM section's Schedule entry); never set by LG-01f |
| `sidebar_active` literal | `"finances_team"` | Reserved (TEAM section's Finances); never set by LG-01f |
| `sidebar_active` literal | `"history_team"` | Reserved (TEAM section's History); never set by LG-01f |
| `sidebar_active` literal | `"free_agents"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `"trade"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `"trading_block"` | Reserved; never set by LG-01f |
| `sidebar_active` literal | `None` | Used by season dashboard (no sidebar entry matches the LG-01c Overview slot post-LG-01f) |
| Sidebar section literal | `"top"` | The top-level Dashboard row (single entry, above section headers) |
| Sidebar section literal | `"league"` | LEAGUE section (6 entries: standings, schedule, playoffs, finances, history, power_rankings) |
| Sidebar section literal | `"team"` | TEAM section (4 entries: roster, schedule_team, finances_team, history_team) |
| Sidebar section literal | `"players"` | PLAYERS section (3 entries: free_agents, trade, trading_block) |
| Session key | `request.session["last_league_id"]` | Integer; written by every League-context view; read by `league_nav` |
| Context key (history view) | `league` | The resolved `League` object |
| Context key (history view) | `in_progress_row` | `dict \| None` — the in-progress Season's row (None if no active/draft Season) |
| Context key (history view) | `completed_rows` | `list[dict]` — the current page's completed Season rows |
| Context key (history view) | `page_obj` | Django `Paginator` page object for completed Seasons |
| Context key (history view) | `paginator` | Django `Paginator` |
| Context key (history view) | `per_page` | int (one of 10 / 25 / 50 / 100) |
| Context key (history view) | `per_page_options` | `(10, 25, 50, 100)` tuple — for the per-page selector |
| Context key (history view) | `sidebar_links` | list of 14 dicts from `_build_league_sidebar_links` |
| Context key (history view) | `sidebar_active` | Literal `"history"` (locked) |
| Context key (top-bar, from context processor) | `top_bar_history_url` | str — resolved by `league_nav` |
| DOM id | `league-sidebar` | Outer `<nav>` / `<aside>` wrapping the sidebar partial |
| DOM id | `sidebar-top-dashboard` | Top-level Dashboard entry |
| DOM id | `sidebar-league-standings` | LEAGUE > Standings |
| DOM id | `sidebar-league-schedule` | LEAGUE > Schedule |
| DOM id | `sidebar-league-playoffs` | LEAGUE > Playoffs (disabled) |
| DOM id | `sidebar-league-finances` | LEAGUE > Finances (disabled) |
| DOM id | `sidebar-league-history` | LEAGUE > History |
| DOM id | `sidebar-league-power_rankings` | LEAGUE > Power Rankings (disabled) |
| DOM id | `sidebar-team-roster` | TEAM > Roster (disabled) |
| DOM id | `sidebar-team-schedule_team` | TEAM > Schedule (disabled) — note `_team` suffix on `key` (NOT `schedule`) to disambiguate from LEAGUE Schedule |
| DOM id | `sidebar-team-finances_team` | TEAM > Finances (disabled) |
| DOM id | `sidebar-team-history_team` | TEAM > History (disabled) |
| DOM id | `sidebar-players-free_agents` | PLAYERS > Free Agents (disabled) |
| DOM id | `sidebar-players-trade` | PLAYERS > Trade (disabled) |
| DOM id | `sidebar-players-trading_block` | PLAYERS > Trading Block (disabled) |
| DOM id | `league-history-table` | Outer `<table>` (only when at least 1 row to display) |
| DOM id | `league-history-empty-notice` | Empty-state `<div>` (only when no Seasons exist at all); substring `"No Seasons yet"` |
| DOM id | `league-history-in-progress-row` | `<tr>` of the in-progress row (only when an active/draft Season exists) |
| DOM id | `league-history-row-{season_id}` | One `<tr>` per completed Season on the current page (Season id substituted) |
| DOM id | `league-history-pagination` | Pagination `<nav>` (only when `paginator.num_pages > 1`) |
| DOM id | `league-history-per-page-form` | The `<form method="get">` wrapping the per-page selector |
| DOM id | `league-history-per-page-select` | The `<select name="per_page">` element |
| DOM id (top-bar dropdown) | `leagues-nav-link` | Preserved from LG-01a — the dropdown TOGGLE `<a class="nav-link dropdown-toggle">` carries this id; clicking still navigates to `/leagues/` (the list) |
| DOM id (top-bar dropdown) | `league-history-topbar-link` | The History `<a class="dropdown-item">` inside the dropdown menu; `href="{{ top_bar_history_url }}"` |
| CSS class substring | `"active"` | Applied to the sidebar entry whose `key` matches `sidebar_active` |
| CSS class substring | `"disabled"` | Applied to disabled sidebar entries (rendered as `<span>`, NOT `<a>`) |
| CSS class substring | `"in-progress"` | Applied to the in-progress Season row's Champion-cell badge |
| CSS class substring | `"in-progress-row"` | Applied to the in-progress Season's `<tr>` (row-level styling hook) |
| Literal | `"In progress"` | Champion cell content for the in-progress row |
| Literal | `"—"` (em-dash U+2014) | Tournament Champion placeholder cell (locked, LG-02 fills later) AND fallback for empty top-3 ranks |
| Literal | `"No Seasons yet"` | Empty-notice substring |
| Per-page whitelist | `(10, 25, 50, 100)` | Frozen tuple; invalid ⇒ default 10 |
| Query param | `?per_page=` | Per-page selector; whitelisted via `_coerce_per_page` |
| Query param | `?page=` | Page selector; coerced via `_coerce_page` |
| Test file (NEW) | `matches/tests/test_league_history.py` | Django `TestCase` for the view |
| Test file (NEW) | `matches/tests/test_league_sidebar.py` | Django `TestCase` for `_build_league_sidebar_links` (the helper reads `League.seasons.filter(...)`, so DB-touching) |
| Test file (NEW) | `matches/tests/test_league_nav_context_processor.py` | Django `TestCase` for `league_nav` |
| Test file EXTENDED | `matches/tests/test_league_dashboard.py` | Append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite` |
| Test file EXTENDED | `matches/tests/test_season_dashboard_view.py` | DELETE `TestSeasonDashboardSidebar` (LG-01c 5-entry shape obsolete); append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite` |
| Test file EXTENDED | `matches/tests/views_tests.py` | Append sidebar + session-write assertions to LG-01 standings + schedule tests |
| Test file EXTENDED | `matches/tests/test_lg01d_*` (every LG-01d file that exercises `start_season`, `play_week`, `play_two_months`, `play_until_end`, `play_status`) | Append a single `test_session_writes_last_league_id` per LG-01d view-test class |
| Test file EXTENDED | `matches/tests/test_lg01e_next_season.py` | Append a single `test_session_writes_last_league_id_before_redirect` test |
| Test class | `TestLeagueHistoryRouting` | in `test_league_history.py` |
| Test class | `TestLeagueHistoryEmptyState` | in `test_league_history.py` |
| Test class | `TestLeagueHistoryCompletedRows` | in `test_league_history.py` |
| Test class | `TestLeagueHistoryInProgressRow` | in `test_league_history.py` |
| Test class | `TestLeagueHistoryChampionFallback` | in `test_league_history.py` |
| Test class | `TestLeagueHistoryPagination` | in `test_league_history.py` |
| Test class | `TestLeagueHistorySidebar` | in `test_league_history.py` |
| Test class | `TestLeagueHistorySessionWrite` | in `test_league_history.py` |
| Test class | `TestBuildLeagueSidebarLinks` | in `test_league_sidebar.py` |
| Test class | `TestSidebarLinkShape` | in `test_league_sidebar.py` |
| Test class | `TestLeagueNavContextProcessor` | in `test_league_nav_context_processor.py` |
| Test class | `TestLg01fSidebarRendered` | appended to `test_league_dashboard.py`, `test_season_dashboard_view.py`, and `views_tests.py` (LG-01 standings/schedule) |
| Test class | `TestLg01fSessionWrite` | appended to `test_league_dashboard.py`, `test_season_dashboard_view.py` |
| Test class DELETED | `TestSeasonDashboardSidebar` | LG-01c 5-entry assertions in `test_season_dashboard_view.py`; obsolete under 14-entry shape |

---

## 1. Page surface

### 1a. URL

A single new path entry in `matches/league_urls.py`. **No new URL
include file.** The LG-01a-mounted file is already routed by
`laserforce_simulator/urls.py`.

**Insertion point:** the new `path(...)` line is inserted **AFTER** the
LG-01e `path("<int:league_id>/next-season/", ...)` entry and **BEFORE**
the LG-01a `path("", views.league_list, name="league_list")` entry.
Django URL resolution is first-match — every typed `<int:league_id>/...`
pattern is more specific than the empty `""` pattern (the LG-01a list).

**Final `urlpatterns` order** (LG-01b top + LG-01c dashboard + LG-01e
next-season + LG-01f addition + LG-01a tail):

1. `path("create/", views.league_create, name="league_create")` *(LG-01b)*
2. `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` *(LG-01c)*
3. `path("<int:league_id>/next-season/", views.next_season, name="next_season")` *(LG-01e)*
4. `path("<int:league_id>/history/", views.league_history, name="league_history")` *(NEW — LG-01f)*
5. `path("", views.league_list, name="league_list")` *(LG-01a)*

- **URL name** is `league_history` (bare, **no `app_name`**) — mirrors
  every LG-01x bare-name precedent.
- **HTTP methods:** GET only. Non-GET ⇒ **405** via
  `HttpResponseNotAllowed(["GET"])` as the **first** line of the view
  body (LG-01c / LG-01d / LG-01e locked pattern).

### 1b. View signature

```python
def league_history(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01f — League History page.

    Read-only paginated table of every Season in ``league_id``. The
    in-progress Season (if any) is pinned at the top of the table with
    an "In progress" badge in the Champion cell and live standings in
    the top-3 cells. Completed Seasons paginate newest-first by id.
    """
```

- **No decorator.** Read-only; no `@transaction.atomic`. No
  `@require_GET` — the explicit `HttpResponseNotAllowed(["GET"])` guard
  is the locked pattern.
- **405 guard:** first line of the body, before any ORM hit.
- **404 guard:** `league = get_object_or_404(League, pk=league_id)`.

### 1c. View body — pinned step order

1. **405 guard:** `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`.
2. **404 guard:** `league = get_object_or_404(League, pk=league_id)`.
3. **Materialise Seasons queryset:**
   ```python
   seasons_qs = (
       league.seasons
       .select_related("champion_team")
       .prefetch_related("matches", "teams")
       .filter(state__in=["active", "draft", "completed"])
       .order_by("-id")
   )
   seasons = list(seasons_qs)
   ```
   The filter includes `"draft"` because a LG-01e Start-Next-Season
   click creates a `draft` Season that should appear as the in-progress
   row. (`state__in=["active", "draft", "completed"]` covers every
   `Season.state` literal that exists at LG-01f merge time per LG-01.)
4. **Build the global team-id set + `teams_by_id` lookup:**
   ```python
   team_ids: set[int] = set()
   for s in seasons:
       team_ids.update(s.starting_team_ids_json or [])
       if s.state in {"active", "draft"}:
           team_ids.update(t.id for t in s.teams.all())
   teams_by_id = Team.objects.in_bulk(team_ids)
   ```
   Single `IN`-query against the `Team` table after the prefetch. The
   `for t in s.teams.all()` loop hits the prefetch cache; no extra
   query per Season.
5. **Identify the in-progress Season:**
   ```python
   in_progress_season = next(
       (s for s in seasons if s.state in {"active", "draft"}),
       None,
   )
   completed_seasons = [s for s in seasons if s.state == "completed"]
   ```
   LG-01 invariant: at most one non-completed Season per League. The
   generator-expression-with-default returns `None` cleanly when no
   in-progress Season exists.
6. **Paginate `completed_seasons`:**
   ```python
   per_page = _coerce_per_page(request.GET.get("per_page"), default=10)
   paginator = Paginator(completed_seasons, per_page)
   page_obj = paginator.get_page(_coerce_page(request.GET.get("page"), default=1))
   ```
   The in-progress row is **NOT** in `completed_seasons`, so the
   pagination budget covers ONLY completed Seasons. With `per_page=10`,
   page 1 renders 1 in-progress row + 10 completed rows. **The
   in-progress row appears on every page** (pinned — it is a header
   row, not a body row, and is repeated above the body table on every
   page for consistency).
7. **Build the in-progress row + completed rows via the helper:**
   ```python
   in_progress_row = (
       _build_history_row(in_progress_season, teams_by_id, is_in_progress=True)
       if in_progress_season is not None
       else None
   )
   completed_rows = [
       _build_history_row(s, teams_by_id, is_in_progress=False)
       for s in page_obj.object_list
   ]
   ```
8. **Session write:** `request.session["last_league_id"] = league.id`.
   Writes the int (not str) so the `league_nav` context processor can
   `reverse(...)` cleanly. Must fire AFTER the 405 / 404 guards (so a
   stale 404 doesn't pin a deleted League id into the session) and
   BEFORE the template render.
9. **Render:** `return render(request, "leagues/history.html", context)`
   with the 9 context keys (§0 table — `league`, `in_progress_row`,
   `completed_rows`, `page_obj`, `paginator`, `per_page`,
   `per_page_options`, `sidebar_links`, `sidebar_active`).
   `sidebar_links` is built via
   `_build_league_sidebar_links(league, displayed_season, "history")`
   where `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()`
   (LG-01c precedent — same Season-pick chain so the sidebar's
   Standings / Schedule links resolve to the same Season the dashboards
   would).

---

## 2. Per-row cells + in-progress row variant

### 2a. Column order (locked, left to right — 10 columns)

| # | Header | Source for completed Season row | Source for in-progress row |
|---|---|---|---|
| 1 | Season name | `<a href="{% url 'season_dashboard' season.id %}">{{ season.name }}</a>` | Same (live link to the in-progress Season's LG-01c dashboard) |
| 2 | Start date | `season.start_date` rendered with Django `{{ season.start_date|date:"Y-m-d" }}` | Same |
| 3 | # teams enrolled | `len(season.starting_team_ids_json or [])` (the LG-01 snapshot is the canonical list once the Season has been activated) | `season.teams.count()` via the prefetch cache when `starting_team_ids_json is None` (i.e. the Season is still a `draft` and hasn't been activated); else same as completed |
| 4 | Total Matches played | `len([m for m in season.matches.all() if m.is_completed])` (filter in Python over the prefetch — NOT a `season.matches.filter(is_completed=True).count()` query) | Same |
| 5 | Champion | `season.champion_team` (the FK from LG-01); fallback `teams_by_id.get(standings[0]["team_id"])` when `champion_team is None` on a completed Season (defensive — the LG-01 invariant fills `champion_team` at completion, but pre-LG-01 data drift is possible) | Literal `<span class="in-progress badge ...">In progress</span>` — NOT a team name; the `"in-progress"` substring is locked in the CSS class |
| 6 | Runner-Up | `teams_by_id.get(standings[1]["team_id"])` if `len(standings) >= 2`, else `"—"` | Same (live standings's rank-2 team if at least 2 teams have played; else `"—"`) |
| 7 | Tournament Champion | Literal `"—"` (em-dash U+2014) — placeholder, LG-02 fills later | Same |
| 8 | 1st place | `teams_by_id.get(standings[0]["team_id"])` if exists, else `"—"` | Same |
| 9 | 2nd place | `teams_by_id.get(standings[1]["team_id"])` if exists, else `"—"` | Same |
| 10 | 3rd place | `teams_by_id.get(standings[2]["team_id"])` if exists, else `"—"` | Same |

- `standings` is the list returned by `compute_standings(matches_list, enrolled_teams)`
  (LG-01 pure module `matches/standings.py`). The dict-key access in
  the table above (e.g. `standings[0]["team_id"]`) reflects the LG-01
  `StandingsRow` shape — the row dataclass exposes a `team_id` field;
  the helper materialises it as a dict for template-friendly access.
  See §4 for the exact row-dict shape.
- `teams_by_id.get(...)` is used (NOT `teams_by_id[...]`) so a stale
  team-id (admin deletion since Season activation) does not crash the
  template — `.get()` returns `None` and the template renders `"—"` via
  a `{% firstof team_obj.name "—" %}` (or equivalent).
- Cells 6/8/9/10 are populated for the in-progress row as well — the
  in-progress row's standings come from `compute_standings(...)` on the
  Season's completed Matches so far (may be `[]`, in which case all
  four cells render `"—"`). This is informative even though no champion
  has been crowned yet.

### 2b. In-progress row variant

- **Visual distinction:** the `<tr>` element carries a CSS class
  containing the substring `"in-progress-row"` (e.g. `class="in-progress-row table-warning"`
  — Code agent picks the Bootstrap utility class for the background
  tint; the `"in-progress-row"` substring is the locked hook).
- **Row DOM id:** `league-history-in-progress-row`.
- **Champion cell:** literal `"In progress"` text inside an element
  whose CSS class contains the substring `"in-progress"` (the badge
  class). NOT a team name; NOT a fallback to `standings[0]`.
- **Cells 6 / 8 / 9 / 10** (Runner-Up + top-3) DO render live
  standings — the row is informative, not a placeholder.
- **Pagination:** the in-progress row IS NOT counted toward `per_page`.
  Pagination budget covers ONLY completed Seasons. With `per_page=10`,
  page 1 = 1 in-progress row + 10 completed rows = 11 rows total. Pages
  2+ = 1 in-progress row + up to 10 completed rows = up to 11 rows
  total. **The in-progress row appears on every page.**

---

## 3. Pagination + sort + empty state

### 3a. Sort

- **Completed Seasons:** newest first by `-id`. Equivalent to newest
  by `start_date` in practice (Seasons are sequential per LG-01e), but
  `-id` is the durable sort key (start_date is a user-editable scalar
  at admin time; id is monotonic).
- **In-progress row:** always first, regardless of sort. Rendered
  ABOVE the `<tbody>` for the completed rows, NOT interleaved.

### 3b. Pagination

- **LG-00c-style helpers** — `_coerce_per_page` and `_coerce_page` are
  module-level flat helpers in `matches/views.py` (per LG-00c
  precedent; the same names + signatures are pinned in §0). Each is a
  3-to-5-line function; no new pure module.
- **`_coerce_per_page(raw, default=10) -> int`:**
  - Accepts `raw: str | None`.
  - Whitelist: `(10, 25, 50, 100)`. Any other value (including `None`,
    non-digit strings, negative, zero, > 100) ⇒ return `default`.
  - The locked whitelist is `(10, 25, 50, 100)`; pinning the value
    against future drift is the point of the helper.
- **`_coerce_page(raw, default=1) -> int`:**
  - Accepts `raw: str | None`.
  - Returns `int(raw)` if `raw` is a positive-int string; else
    `default`. Django `Paginator.get_page(...)` further clamps a
    too-large page to the last page silently.
  - The helper exists to keep the view body 1-line ("`page_obj = paginator.get_page(_coerce_page(...))`")
    rather than inlining the `try/except ValueError` block.
- **`Paginator`:** standard Django `django.core.paginator.Paginator`
  over `completed_seasons` with `per_page` from the helper.
- **`page_obj`:** from `paginator.get_page(_coerce_page(...))`.
  Standard Django page object.
- **Per-page selector:**
  - Rendered as a `<form id="league-history-per-page-form" method="get">`
    wrapping a `<select id="league-history-per-page-select" name="per_page">`
    with the four options `(10, 25, 50, 100)`. Form action defaults to
    the current URL (no `action` attribute needed). The current
    `per_page` option is `selected`.
  - The form may include a `<noscript>`-style submit button or rely
    on a JS-free `onchange` form submission — Code agent picks; the
    DOM ids are the locked hook.
- **Pagination links:**
  - Rendered inside a `<nav id="league-history-pagination">` ONLY when
    `paginator.num_pages > 1`. Empty / single-page ⇒ omit the `<nav>`.
  - Each link carries the current `per_page` in its query string so
    the user's choice persists across page navigation. Code agent
    picks the link-assembly style (Django `{% querystring %}` tag if
    available, manual string-concat, or a per-page querystring
    helper added to the context — the helper is the LG-00c precedent).
  - If a `querystring_helper` is added to the context for link
    assembly, pin it as a 10th context key: `pagination_querystring`
    (`str`). Code agent may instead inline the querystring logic in
    the template; the test boundary doesn't depend on which.

### 3c. Empty state

- **When:** `len(seasons) == 0` (no completed AND no in-progress
  Season). League has never had a Season.
- **Render:** a `<div id="league-history-empty-notice">` containing the
  substring `"No Seasons yet"` (locked exact substring; em-dash
  variants are NOT acceptable — just plain ASCII apostrophe-free text).
- **Omit:** the `<table>` body entirely. The empty-notice replaces the
  table. The sidebar partial + top-bar dropdown still render.
- **Pagination behaviour:** `paginator.num_pages == 0`; `page_obj.object_list == []`;
  the `<nav id="league-history-pagination">` is omitted (`num_pages > 1` is False).

---

## 4. Query strategy + `_build_history_row` dict shape

### 4a. Single-pass query strategy

The view issues exactly **3 SQL queries** for the data fetch (excluding
the session backend + the LG-00 middleware overhead):

1. `League.objects.get(pk=league_id)` — via `get_object_or_404`.
2. `seasons_qs` materialisation — the `select_related("champion_team").prefetch_related("matches", "teams").filter(state__in=[...])`
   query (one SELECT for Seasons + one prefetch for Matches + one
   prefetch for Teams + one prefetch for the M2M through table = 4
   queries via the Django prefetch machinery, but logically one
   "materialise" step).
3. `Team.objects.in_bulk(team_ids)` — single `IN`-query for the name
   lookup.

The `compute_standings(...)` call per Season operates purely in-Python
on the prefetched `season.matches.all()` list (filtered to
`is_completed=True` rows); zero additional queries.

### 4b. `_build_history_row` signature + dict shape

```python
def _build_history_row(
    season: Season,
    teams_by_id: dict[int, Team],
    *,
    is_in_progress: bool,
) -> dict:
    """LG-01f — Build one row of the league history table.

    Args:
        season: the Season to render (completed, active, or draft).
        teams_by_id: pre-fetched mapping from team_id to Team object;
            populated once per view call so this helper does zero
            queries.
        is_in_progress: True for the active/draft Season (renders the
            "In progress" badge in the Champion cell); False for
            completed Seasons.

    Returns:
        A dict with the 11 frozen keys below. ``None`` values are
        rendered as ``"—"`` in the template.
    """
```

- **Module-level** (NOT a method on `Season` / `League`). Lives in
  `matches/views.py`; private (`_`-prefix). RV-01 / LG-01c / LG-01d
  precedent.
- **Keyword-only `is_in_progress`** — prevents accidental positional
  inversion at call sites.
- **No DB hits** — the helper consumes the pre-fetched `season.matches.all()`
  via the prefetch cache and the pre-built `teams_by_id` lookup.

**Frozen 11-key dict shape:**

```python
{
    "season_id":              int,
    "season_name":            str,                  # season.name
    "season_url":             str,                  # reverse("season_dashboard", args=[season.id])
    "start_date":             datetime.date,        # season.start_date
    "teams_enrolled":         int,                  # see §2a column 3
    "matches_played":         int,                  # see §2a column 4
    "champion":               Team | None,          # for completed; None for in-progress (templates branch on is_in_progress)
    "runner_up":              Team | None,
    "tournament_champion":    None,                 # placeholder; LG-02 fills later (renders as "—")
    "top_three":              list[Team | None],    # length 3; rank-1, rank-2, rank-3; trailing slots may be None
    "is_in_progress":         bool,                 # carries through to the template branch
}
```

- `top_three` is exactly length 3. If fewer than 3 teams appear in
  `compute_standings(...)`'s output, the trailing slots are `None`
  (rendered as `"—"`).
- `champion` for the in-progress row is `None` (the template branches
  on `is_in_progress` to render the "In progress" badge instead of a
  `Team`).
- `runner_up` for the in-progress row is `teams_by_id.get(standings[1]["team_id"])`
  if `len(standings) >= 2`, else `None` — same as for completed Seasons.
- `tournament_champion` is **always** `None` at LG-01f merge time. LG-02
  will replace this with a real value; the contract reserves the key
  now so the template doesn't need to change at LG-02 land time.

### 4c. `compute_standings` call shape

```python
matches_list = [m for m in season.matches.all() if m.is_completed]
team_ids_for_season = season.starting_team_ids_json or sorted(t.id for t in season.teams.all())
enrolled_teams = [(tid, teams_by_id[tid].name if tid in teams_by_id else "") for tid in team_ids_for_season]
standings = compute_standings(matches_list, enrolled_teams)
```

- The `starting_team_ids_json or sorted(...)` fallback handles the
  defensive case of an unactivated `draft` Season (snapshot is `None`).
  Falls back to the live M2M sorted by id for deterministic order.
- `teams_by_id[tid].name` with `tid in teams_by_id` guard avoids a
  KeyError on a stale team-id.
- The returned `standings` is a list of `StandingsRow` instances (LG-01
  shape). `_build_history_row` reads `standings[i].team_id` (NOT
  `standings[i]["team_id"]` — `StandingsRow` is a dataclass at LG-01
  per `matches/standings.py`). The §2a / §4b shape commentary's
  dict-style indexing is shorthand; the actual code uses the dataclass
  attribute. (Pinned: if `compute_standings` returns dicts at LG-01
  merge time, the helper adapts via `getattr(row, "team_id", row["team_id"])`
  — the Code agent verifies the LG-01 return shape and consumes
  accordingly.)

---

## 5. Sidebar (partial + 14-entry list + helper signature + active-key mapping)

### 5a. NEW partial `templates/_partials/league_sidebar.html`

- **Path:** `laserforce_simulator/templates/_partials/league_sidebar.html`.
- **No `{% extends %}`** — it's a partial, included via
  `{% include "_partials/league_sidebar.html" %}` from the 5 pages
  listed below.
- **Outer wrapper:** `<nav id="league-sidebar">` (the locked DOM id).
- **Iterates `sidebar_links`** — the template reads the
  `sidebar_links` context key. It does NOT compute any of the URL /
  label / disabled logic itself; the helper does that view-side.
- **Section headers:** the LEAGUE / TEAM / PLAYERS section labels
  render as `<h6>` (or similar — Code agent picks the Bootstrap class
  for typography; only the `<h6>` tag and the literal section-header
  text `"LEAGUE"`, `"TEAM"`, `"PLAYERS"` are pinned). The `"top"`
  section has no header — the Dashboard entry sits directly above the
  LEAGUE header.
- **Per-entry rendering:**
  - If `entry["disabled"]`: render `<span id="sidebar-{section}-{key}" class="...disabled...">{{ entry.label }}</span>` — NO `<a href>`.
  - Else: render `<a id="sidebar-{section}-{key}" href="{{ entry.url }}" class="...">{{ entry.label }}</a>`.
  - If `entry["active"]`: the `class` attribute contains the substring `"active"` (Bootstrap convention; Code agent picks the exact class).
- **Section grouping:** the template renders entries grouped by
  `entry["section"]` in the pinned order `[top, league, team, players]`.
  The helper returns entries in this order already; the template just
  iterates and inserts section headers when the section changes.

### 5b. 14-entry sidebar list (locked order + locked keys)

The helper returns a list of exactly 14 dicts in this pinned order:

| Index | `section` | `key` | `label` | LIVE? (at LG-01f) | LG-01f `url` target |
|---|---|---|---|---|---|
| 0 | `"top"` | `"dashboard"` | `"Dashboard"` | LIVE | `reverse("league_dashboard", args=[league.id])` |
| 1 | `"league"` | `"standings"` | `"Standings"` | LIVE (conditional) | `reverse("season_standings", args=[displayed_season.id])` IF `displayed_season is not None` else `None` (disabled) |
| 2 | `"league"` | `"schedule"` | `"Schedule"` | LIVE (conditional) | `reverse("season_schedule", args=[displayed_season.id])` IF `displayed_season is not None` else `None` (disabled) — same fallback rule as Standings |
| 3 | `"league"` | `"playoffs"` | `"Playoffs"` | disabled | `None` |
| 4 | `"league"` | `"finances"` | `"Finances"` | disabled | `None` |
| 5 | `"league"` | `"history"` | `"History"` | LIVE | `reverse("league_history", args=[league.id])` |
| 6 | `"league"` | `"power_rankings"` | `"Power Rankings"` | disabled | `None` |
| 7 | `"team"` | `"roster"` | `"Roster"` | disabled | `None` |
| 8 | `"team"` | `"schedule_team"` | `"Schedule"` | disabled | `None` — **note: this is the TEAM section's Schedule entry, distinct from index 2's LEAGUE Schedule; the `_team` suffix on the `key` disambiguates** |
| 9 | `"team"` | `"finances_team"` | `"Finances"` | disabled | `None` |
| 10 | `"team"` | `"history_team"` | `"History"` | disabled | `None` — TEAM History, distinct from index 5's LEAGUE History |
| 11 | `"players"` | `"free_agents"` | `"Free Agents"` | disabled | `None` |
| 12 | `"players"` | `"trade"` | `"Trade"` | disabled | `None` |
| 13 | `"players"` | `"trading_block"` | `"Trading Block"` | disabled | `None` |

**Schedule resolution (2026-05-27):** Schedule is added to LEAGUE as a
6th entry (diverges from zengm's TEAM-section Schedule). Rationale: in
this project the schedule is league-level (full per-Season fixture
list) rather than per-team. The `sidebar_active="schedule"` literal
applied by the Season Schedule page now matches the LEAGUE > Schedule
entry (exactly one active entry). The TEAM section retains its own
disabled `"schedule_team"` entry as a placeholder for a future
per-team schedule view; the `_team` suffix on the `key` disambiguates
from the LEAGUE entry. This supersedes the prior judgment-call note
which dropped LEAGUE Schedule entirely.

### 5c. `_build_league_sidebar_links` signature + behaviour

```python
def _build_league_sidebar_links(
    league: League,
    displayed_season: Season | None,
    sidebar_active: str | None,
) -> list[dict]:
    """LG-01f — Build the 14-entry sidebar link list.

    Args:
        league: the League whose sidebar is being rendered.
        displayed_season: the Season to use for LIVE entry URLs (LEAGUE
            > Standings, LEAGUE > Schedule). Resolved by the calling
            view via ``league.active_season or league.seasons.filter(state="completed").order_by("-id").first()``.
            When ``None`` (no Season exists at all), both the Standings
            and Schedule entries fall back to disabled.
        sidebar_active: the literal key whose entry should carry
            ``active=True``. One of the 14 locked literals in §0; or
            ``None`` for no-active-entry (e.g. the season dashboard).

    Returns:
        Exactly 14 dicts in the pinned order from §5b. Each dict has
        exactly the 6 keys: ``key, label, section, url, disabled,
        active``. Disabled entries have ``url=None`` and ``disabled=True``.
        Exactly 0 or 1 entry has ``active=True`` (at most one — every
        ``sidebar_active`` literal matches at most one entry, and
        ``None`` matches zero).
    """
```

- **Module-level** in `matches/views.py`; private `_`-prefix.
- **Per-entry dict shape (locked 6 keys):**
  ```python
  {
      "key":      str,             # one of the 14 entry keys
      "label":    str,             # display text
      "section":  str,             # "top" | "league" | "team" | "players"
      "url":      str | None,      # None ⇒ disabled
      "disabled": bool,            # True iff url is None
      "active":   bool,            # True iff key == sidebar_active
  }
  ```
- **`disabled` derivation:** `disabled = (url is None)`. The helper
  doesn't accept an explicit `disabled` argument; it derives the value
  from whether `url` is `None`. (For the LEAGUE > Standings and LEAGUE
  > Schedule entries, if `displayed_season is None`, `url = None` and
  `disabled = True`.)
- **`active` derivation:** `active = (entry["key"] == sidebar_active)`
  (string equality). With `sidebar_active=None`, every entry's
  `active` is `False`.

### 5d. Active-key mapping (which page sets `sidebar_active` to what)

| Page | View | `sidebar_active` value | Notes |
|---|---|---|---|
| League dashboard | `matches.views.league_dashboard` | `"dashboard"` | LG-01c view; gets new `sidebar_active` context key |
| League history | `matches.views.league_history` | `"history"` | NEW LG-01f view |
| Season dashboard | `matches.views.season_dashboard` | `None` | LG-01c view; the LG-01c "Overview" sidebar entry is removed — no entry matches the season dashboard at LG-01f. Tests assert: sidebar renders with zero active entries |
| Season standings | `matches.views.season_standings` | `"standings"` | LG-01 view; matches LEAGUE > Standings entry |
| Season schedule | `matches.views.season_schedule` | `"schedule"` | LG-01 view; matches LEAGUE > Schedule entry (exactly one active entry on the schedule page) |

---

## 6. Top-bar dropdown (base.html edit)

### 6a. Existing markup (LG-01a, at LG-01f merge time)

```django
<a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>
```

(or similar — exact attribute order at LG-01a discretion). The locked
DOM id `leagues-nav-link` is set on the `<a>` per LG-01a.

### 6b. New markup (LG-01f)

A Bootstrap dropdown — `<li class="nav-item dropdown">` wrapping a
dropdown toggle `<a>` plus a dropdown menu `<ul>`:

```django
<li class="nav-item dropdown">
    <a class="nav-link dropdown-toggle"
       id="leagues-nav-link"
       href="{% url 'league_list' %}"
       role="button"
       data-bs-toggle="dropdown"
       aria-expanded="false">League ▾</a>
    <ul class="dropdown-menu" aria-labelledby="leagues-nav-link">
        <li><span class="dropdown-item disabled">Standings</span></li>
        <li><span class="dropdown-item disabled">Playoffs</span></li>
        <li><span class="dropdown-item disabled">Finances</span></li>
        <li><a class="dropdown-item"
               id="league-history-topbar-link"
               href="{{ top_bar_history_url }}">History</a></li>
        <li><span class="dropdown-item disabled">Power Rankings</span></li>
    </ul>
</li>
```

- **Toggle text:** `"League ▾"` (the literal word `League` plus a
  trailing space and the Unicode caret `▾` U+25BE). The trailing caret
  is the locked indicator; Code agent picks the exact caret rendering
  (CSS pseudo-element, Bootstrap built-in toggle indicator, or inline
  Unicode — only the visual presence of a downward caret is pinned).
- **Toggle id:** the LG-01a-locked `leagues-nav-link` is **preserved**
  — clicking the toggle still navigates to `/leagues/` (the LG-01a
  list page) via the `href` attribute, AND opens the dropdown (the
  `data-bs-toggle` attribute). Both behaviours coexist; Bootstrap's
  dropdown JS does not prevent the `href` navigation unless explicitly
  bound. (Code agent verifies via Bootstrap 5 docs that the
  combination works as expected; this is the LG-01a-compat
  requirement.)
- **5 dropdown items, locked order top to bottom:**
  1. Standings — disabled `<span class="dropdown-item disabled">`
  2. Playoffs — disabled `<span class="dropdown-item disabled">`
  3. Finances — disabled `<span class="dropdown-item disabled">`
  4. History — LIVE `<a class="dropdown-item" id="league-history-topbar-link" href="{{ top_bar_history_url }}">History</a>`
  5. Power Rankings — disabled `<span class="dropdown-item disabled">`
- **`top_bar_history_url`** is provided globally by the `league_nav`
  context processor (§7); the template doesn't compute it.
- **The History `<a>` is LIVE on every page** — every page that
  extends `base.html` renders the History link with `top_bar_history_url`
  resolved per the context-processor chain. The link is never disabled
  via the top-bar (even when there are zero Leagues, the URL falls
  through to `/leagues/`, which renders the empty-state LG-01a list).
- **NO inline JS** — Bootstrap 5's dropdown component is the only
  dependency. (Bootstrap is already a project dep per LG-01a / LG-01b;
  no `pip install`, no new asset.)

---

## 7. Session write + context processor (`league_nav`)

### 7a. Session-write sites (locked list)

Every League-context view writes
`request.session["last_league_id"] = <league_id>` near the top of the
view body, AFTER the 405 / 404 guards (and AFTER any active-Season
guard that REDIRECTS to a Season's dashboard — but BEFORE any final
template render or redirect on the happy path). The write must fire
even when the view 302-redirects on success (e.g. `next_season`), so a
follow-up GET to the same League's pages can use the session value.

| View | Source for the id | Position in body |
|---|---|---|
| `matches.views.league_dashboard` (LG-01c) | `league.id` | After 404, before `_build_dashboard_context` |
| `matches.views.league_history` (LG-01f, new) | `league.id` | After 404, before render |
| `matches.views.season_dashboard` (LG-01c) | `season.league_id` | After 404, before `_build_dashboard_context` |
| `matches.views.season_standings` (LG-01) | `season.league_id` | After 404, before render |
| `matches.views.season_schedule` (LG-01) | `season.league_id` | After 404, before render |
| `matches.views.next_season` (LG-01e) | `league.id` | **BEFORE the redirect** — pin: set the session key first, then `return redirect(...)`. The atomic-transaction wraps the session write too (Django's session middleware commits on response, not inside the atomic block; the locked rule is "session write fires before the redirect return statement so the cookie is set before the response is built"). |
| `matches.views.start_season` (LG-01d) | `season.league_id` | After 404, before any other logic |
| `matches.views.play_week` (LG-01d) | `season.league_id` | After 404, before any other logic |
| `matches.views.play_two_months` (LG-01d) | `season.league_id` | After 404 |
| `matches.views.play_until_end` (LG-01d) | `season.league_id` | After 404 |
| `matches.views.play_status` (LG-01d) | `season.league_id` | After 404; this is a polling endpoint that fires on every poll — pinning the session write here keeps `last_league_id` fresh as the user watches a job |

- **Write the int, not a string** (so `League.objects.filter(pk=lid).exists()`
  in the context processor works without coercion).
- **Defensive:** the context processor validates the session id
  against `League` existence (§7b) so a deleted League id in a stale
  session doesn't crash the reverse. The view doesn't need to validate
  on the write side.

### 7b. NEW context processor `core.context_processors.league_nav`

- **File:** `core/context_processors.py` (NEW).
- **Function:**

  ```python
  def league_nav(request: HttpRequest) -> dict[str, str]:
      """LG-01f — Resolve the top-bar History link's target.

      Resolution chain (locked):
          1. ``request.session["last_league_id"]``: if present AND the
             League still exists, return ``reverse("league_history",
             kwargs={"league_id": lid})``.
          2. Otherwise, if exactly one League exists in the DB, return
             ``reverse("league_history", kwargs={"league_id": <that
             league's id>})``.
          3. Otherwise (zero or 2+ Leagues, no session pin), return
             ``reverse("league_list")``.

      Returns:
          ``{"top_bar_history_url": <resolved URL string>}``.

      Notes:
          - The session-id existence check uses
            ``League.objects.filter(pk=lid).exists()`` — a defensive
            single-row query that returns False for both "lid is None"
            and "no such League". This prevents a 500 when an admin
            deletes a League whose id is still pinned in some user's
            session.
          - The single-League branch uses ``[:2]`` to LIMIT the count
            query — we only need to know "exactly 1" vs "more than 1",
            never the full count. ``len(qs)`` then yields 0, 1, or 2.
      """
  ```

- **Registration:** add `"core.context_processors.league_nav"` to
  `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` (after the
  Django built-ins; alphabetical or insertion order at Code agent's
  discretion).
- **Cost:** at most 2 lightweight queries per request — one `exists()`
  on the session id (when present) plus one `values_list("id", flat=True)[:2]`
  in the fallback branch. Neither hits any prefetch.
- **No caching at LG-01f** — the resolution is per-request; if the
  query cost is noticed in profiling later, cache via the session
  itself (set `request.session["resolved_history_url"]` alongside
  `last_league_id`). Deferred.

---

## 8. Templates (NEW + MODIFIED list + locked block-title strings)

### 8a. NEW templates

| Path | Extends / includes | `{% block title %}` | Notes |
|---|---|---|---|
| `templates/leagues/history.html` | `extends "base.html"` + `include "_partials/league_sidebar.html"` | `{{ league.name }} — History` (em-dash U+2014, locked exact format) | Renders the table + pagination + empty-notice + per-page selector |
| `templates/_partials/league_sidebar.html` | (no extends) | n/a | Iterates `sidebar_links`; section headers `<h6>` |

**`templates/leagues/history.html` structure (Code agent's discretion
on exact markup; only the DOM ids + the in-progress-row substring +
the per-row `id="league-history-row-{{ row.season_id }}"` rule are
locked):**

```django
{% extends "base.html" %}
{% block title %}{{ league.name }} — History{% endblock %}
{% block content %}
  <div class="d-flex">
    {% include "_partials/league_sidebar.html" %}
    <main>
      <h1>{{ league.name }} — History</h1>
      {% if not in_progress_row and not completed_rows %}
        <div id="league-history-empty-notice">No Seasons yet</div>
      {% else %}
        <table id="league-history-table">
          <thead>...</thead>
          <tbody>
            {% if in_progress_row %}
              <tr id="league-history-in-progress-row" class="in-progress-row ...">
                ... (10 cells, Champion = "In progress" badge) ...
              </tr>
            {% endif %}
            {% for row in completed_rows %}
              <tr id="league-history-row-{{ row.season_id }}">
                ... (10 cells) ...
              </tr>
            {% endfor %}
          </tbody>
        </table>
        <form id="league-history-per-page-form" method="get">
          <select id="league-history-per-page-select" name="per_page" onchange="this.form.submit()">
            {% for option in per_page_options %}
              <option value="{{ option }}" {% if option == per_page %}selected{% endif %}>{{ option }}</option>
            {% endfor %}
          </select>
        </form>
        {% if paginator.num_pages > 1 %}
          <nav id="league-history-pagination">... page links ...</nav>
        {% endif %}
      {% endif %}
    </main>
  </div>
{% endblock %}
```

The `onchange="this.form.submit()"` is the only inline JS allowed (the
LG-00c precedent for per-page selectors). If the Code agent prefers a
no-JS approach (visible submit button), that's acceptable too.

### 8b. MODIFIED templates

| Path | Edit | `sidebar_active` value to set |
|---|---|---|
| `templates/base.html` | Replace LG-01a `Leagues` `<a>` with the dropdown markup (§6b). Preserves DOM id `leagues-nav-link` on the toggle. | n/a (base layout) |
| `templates/leagues/dashboard.html` | Insert `{% include "_partials/league_sidebar.html" %}` adjacent to the existing `{% block content %}` body; structure becomes `<div class="d-flex">{% include ... %}<main>... existing dashboard markup ...</main></div>`. | `sidebar_active="dashboard"` (added to context by `league_dashboard`) |
| `templates/seasons/dashboard.html` | Same — insert sidebar partial. **DELETES** the LG-01c per-season 5-entry sidebar markup (the `season-dashboard-sidebar*` block in the LG-01c template). The LG-01c DOM ids `season-dashboard-sidebar`, `season-dashboard-sidebar-standings`, `season-dashboard-sidebar-schedule`, `season-dashboard-sidebar-teams`, `season-dashboard-sidebar-history` ARE REMOVED. LG-01c test `TestSeasonDashboardSidebar` is deleted in §9. | `sidebar_active=None` (added to context by `season_dashboard`) |
| `templates/seasons/standings.html` | Insert sidebar partial; restructure body to flex container around sidebar + main content. | `sidebar_active="standings"` (added to context by `season_standings`) |
| `templates/seasons/schedule.html` | Insert sidebar partial; restructure body. | `sidebar_active="schedule"` (added to context by `season_schedule`) |

**LG-01c context-key cleanup:** the LG-01c `season_dashboard` view's
context key `sidebar_active = "overview"` is **renamed** to
`sidebar_active = None`. The LG-01c `sidebar_links` 5-entry list is
**deleted** — replaced by the LG-01f 14-entry list from
`_build_league_sidebar_links`. The LG-01c `_season_sidebar_links`
helper is DELETED (per §0 locked-names recap).

**Context-key additions per page** (locked):
- `league_dashboard`: adds `sidebar_links`, `sidebar_active="dashboard"`
- `season_dashboard`: replaces LG-01c `sidebar_links` + `sidebar_active="overview"` with new `sidebar_links` (14 entries) + `sidebar_active=None`
- `season_standings`: adds `sidebar_links`, `sidebar_active="standings"`
- `season_schedule`: adds `sidebar_links`, `sidebar_active="schedule"`
- `league_history` (new): provides all 9 keys from §0

For the 4 modified views, the call is:

```python
displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()
sidebar_links = _build_league_sidebar_links(league, displayed_season, sidebar_active)
```

(For Season-context views, `league = season.league` — derive
`displayed_season` from the Season's parent League.)

---

## 9. Tests (file list + class list + delete-LG-01c-sidebar-class instruction)

Tests live in **3 NEW files + 5+ EXTENDED files**. Pinned by file name +
class name(s); test method names within each class are at the Tests
agent's discretion unless explicitly pinned below.

### 9a. NEW `matches/tests/test_league_history.py`

Django `TestCase`. Each test constructs minimal fixtures (League +
Seasons + Teams + Matches + PlayerRoundStates as needed; **never**
calls the simulator).

**Classes:**

- `TestLeagueHistoryRouting`
  - `test_reverse_resolves_to_expected_path` — `reverse("league_history", kwargs={"league_id": league.id})` == `/leagues/<id>/history/`.
  - `test_get_returns_200_for_existing_league`.
  - `test_get_returns_404_for_missing_league`.
  - `test_post_returns_405`.
  - `test_template_used_is_leagues_history_html`.

- `TestLeagueHistoryEmptyState`
  - `test_league_with_zero_seasons_renders_empty_notice` — substring `"No Seasons yet"` present; `id="league-history-empty-notice"` present; `id="league-history-table"` ABSENT.
  - `test_empty_state_still_renders_sidebar` — sidebar partial included; History entry present.

- `TestLeagueHistoryCompletedRows`
  - `test_season_name_cell_links_to_season_dashboard` — anchor `href` == `reverse("season_dashboard", args=[s.id])`.
  - `test_start_date_cell_uses_y_m_d_format` — `2025-03-15` substring for a Season with that start date.
  - `test_teams_enrolled_uses_starting_team_ids_json_length`.
  - `test_matches_played_counts_only_is_completed_matches` — fixture: 4 matches in the Season, 2 with `is_completed=True`; assertion: cell renders `2`.
  - `test_champion_cell_renders_champion_team_name`.
  - `test_runner_up_renders_standings_rank_2`.
  - `test_tournament_champion_cell_is_em_dash_placeholder`.
  - `test_top_three_cells_render_rank_1_2_3` (substring assertions on team names).
  - `test_top_three_cells_render_em_dash_when_fewer_than_three_teams` — Season with 2 teams ⇒ rank-3 cell is `—`.
  - `test_completed_rows_sorted_newest_first_by_id`.
  - `test_each_completed_row_has_id_league_history_row_seasonid` — e.g. `id="league-history-row-7"`.

- `TestLeagueHistoryInProgressRow`
  - `test_active_season_renders_in_progress_row_at_top` — `id="league-history-in-progress-row"` present; appears BEFORE the first `league-history-row-<id>` in the rendered HTML.
  - `test_in_progress_row_has_in_progress_row_class_substring` — row's `class` contains `"in-progress-row"`.
  - `test_in_progress_champion_cell_renders_in_progress_badge_not_team_name` — cell text contains `"In progress"`; cell's class contains `"in-progress"`.
  - `test_in_progress_top_three_cells_render_live_standings` — fixture: in-progress Season with 1 completed Match; assertion: rank-1 cell shows the winning team.
  - `test_in_progress_row_not_counted_in_per_page_budget` — 11 completed Seasons + 1 in-progress; per_page=10; page 1 has 1 in-progress row + 10 completed rows = 11 `<tr>` in `<tbody>`; page 2 has 1 in-progress row + 1 completed row = 2 `<tr>`.
  - `test_draft_season_also_renders_in_progress_row` — `state="draft"` Season (just created by LG-01e) ⇒ in-progress row present.
  - `test_no_active_or_draft_season_omits_in_progress_row` — `id="league-history-in-progress-row"` ABSENT when all Seasons are completed.

- `TestLeagueHistoryChampionFallback`
  - `test_champion_fk_null_falls_back_to_standings_rank_1` — fixture: a completed Season with `champion_team=None` and a standings rank-1 team; cell renders the rank-1 team's name (pre-LG-01 data drift defence).
  - `test_champion_fk_present_takes_precedence_over_standings_rank_1` — fixture: a completed Season with `champion_team=Team(A)` and standings rank-1 = `Team(B)`; cell renders `A`.

- `TestLeagueHistoryPagination`
  - `test_default_per_page_is_10`.
  - `test_per_page_25_50_100_accepted`.
  - `test_invalid_per_page_falls_back_to_10` — `?per_page=foo`, `?per_page=999`, `?per_page=-5`, `?per_page=0` all ⇒ 10.
  - `test_invalid_page_falls_back_to_1` — `?page=foo`, `?page=-1`, `?page=0` all ⇒ page 1.
  - `test_too_large_page_clamps_to_last_page` — Django `Paginator.get_page` semantics.
  - `test_page_2_carries_per_page_querystring` — link to page 2 contains both `?page=2` and `&per_page=10`.
  - `test_in_progress_row_appears_on_every_page` — 11 completed + 1 in-progress; per_page=10; assert `id="league-history-in-progress-row"` present on page 1 AND page 2.
  - `test_pagination_nav_omitted_when_single_page` — 5 completed Seasons, per_page=10 ⇒ `id="league-history-pagination"` ABSENT.

- `TestLeagueHistorySidebar`
  - `test_history_page_renders_sidebar_partial` — `id="league-sidebar"` present.
  - `test_sidebar_history_entry_has_active_class_on_history_page` — `id="sidebar-league-history"` element's `class` contains `"active"`.
  - `test_sidebar_dashboard_entry_is_not_active_on_history_page` — `id="sidebar-top-dashboard"` element's `class` does NOT contain `"active"`.
  - `test_sidebar_renders_all_14_entries` — every locked DOM id from §0 present in the rendered HTML.

- `TestLeagueHistorySessionWrite`
  - `test_get_history_writes_last_league_id_to_session` — `client.get(reverse("league_history", args=[league.id]))`; `self.assertEqual(client.session["last_league_id"], league.id)`.
  - `test_404_does_not_write_session` — GET on a missing League ⇒ 404; `"last_league_id" not in client.session` (or unchanged from a prior value).

### 9b. NEW `matches/tests/test_league_sidebar.py`

Django `TestCase` (the helper reads
`league.seasons.filter(state="completed").order_by("-id").first()` for
the displayed-Season fallback, so it touches the DB).

**Classes:**

- `TestBuildLeagueSidebarLinks`
  - `test_league_with_active_season_standings_url_targets_active_season` — set up: League + 1 active Season; assertion: entry with `key="standings"` has `url` == `reverse("season_standings", args=[active_season.id])`.
  - `test_league_with_active_season_schedule_url_targets_active_season` — set up: League + 1 active Season; assertion: entry with `key="schedule"` (LEAGUE section) has `url` == `reverse("season_schedule", args=[active_season.id])` and `disabled is False`. The 4-scenario coverage spans: (a) League + active Season, (b) League + only completed Seasons, (c) League + 0 Seasons, (d) League + draft Season — Schedule entry LIVE/disabled state mirrors Standings in each scenario.
  - `test_league_with_only_completed_seasons_standings_url_targets_most_recent_completed` — set up: League + 2 completed Seasons (ids 1 and 2); assertion: Standings url targets Season 2.
  - `test_league_with_only_completed_seasons_schedule_url_targets_most_recent_completed` — same fixture; assertion: Schedule url targets Season 2 via `reverse("season_schedule", args=[2])`, `disabled is False`.
  - `test_league_with_zero_seasons_standings_entry_is_disabled` — set up: League + 0 Seasons; assertion: Standings entry has `url=None`, `disabled=True`.
  - `test_league_with_zero_seasons_schedule_entry_is_disabled` — same fixture; assertion: LEAGUE Schedule entry has `url=None`, `disabled=True`.
  - `test_league_with_draft_season_schedule_url_targets_draft_season` — set up: League + 1 draft Season (active_season picks it up); assertion: Schedule entry LIVE, url targets the draft Season.
  - `test_dashboard_entry_url_always_targets_league_dashboard`.
  - `test_history_entry_url_always_targets_league_history`.
  - `test_sidebar_active_history_marks_only_history_entry_active` — exactly one entry has `active=True`, and it's the History entry.
  - `test_sidebar_active_none_marks_zero_entries_active`.
  - `test_sidebar_active_schedule_marks_only_league_schedule_entry_active` — exactly one entry has `active=True`, and it's the LEAGUE > Schedule entry (key `"schedule"`, section `"league"`); the TEAM > Schedule entry (key `"schedule_team"`) is NOT active.
  - `test_sidebar_active_dashboard_marks_only_dashboard_entry_active`.
  - `test_sidebar_active_standings_marks_only_standings_entry_active`.

- `TestSidebarLinkShape`
  - `test_returns_exactly_14_entries_in_pinned_order` — 14 entries in pinned order (1 top + 6 LEAGUE + 4 TEAM + 3 PLAYERS).
  - `test_entries_in_pinned_section_order_top_league_team_players` — section sequence in the returned list.
  - `test_each_entry_has_exactly_6_keys` — `key, label, section, url, disabled, active`.
  - `test_disabled_entries_have_url_none_and_disabled_true` — Playoffs, Finances, Power Rankings, Roster, Schedule (team), Finances (team), History (team), Free Agents, Trade, Trading Block ⇒ 10 disabled entries.
  - `test_live_entries_have_url_str_and_disabled_false` — Dashboard, History always; Standings and LEAGUE Schedule when `displayed_season is not None`.
  - `test_team_section_schedule_key_is_schedule_team_not_schedule` — disambiguation from LEAGUE Schedule (key `"schedule"`).
  - `test_team_section_history_key_is_history_team_not_history` — disambiguation from LEAGUE.

### 9c. NEW `matches/tests/test_league_nav_context_processor.py`

Django `TestCase`. The processor is registered in
`settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`, so the
`RequestContext` machinery invokes it on every render — the tests
exercise it directly by calling `league_nav(request)` with a
`RequestFactory()`-built request, then assert on the returned dict.

**Class:**

- `TestLeagueNavContextProcessor`
  - `test_session_pin_with_existing_league_returns_history_url` — set up: 2 Leagues exist (ids 1, 2); session has `last_league_id=2`; assertion: returns `reverse("league_history", kwargs={"league_id": 2})`.
  - `test_session_pin_with_stale_league_id_falls_through_to_single_league_branch` — set up: 1 League exists (id=5); session has `last_league_id=99` (deleted); assertion: returns `reverse("league_history", kwargs={"league_id": 5})`.
  - `test_session_pin_with_stale_league_id_falls_through_to_list_when_zero_leagues` — set up: 0 Leagues; session has `last_league_id=99`; assertion: returns `reverse("league_list")`.
  - `test_single_league_no_session_returns_that_leagues_history_url` — set up: 1 League (id=5); no session value; assertion: returns `reverse("league_history", kwargs={"league_id": 5})`.
  - `test_multiple_leagues_no_session_returns_list_url` — 2+ Leagues, no session ⇒ `reverse("league_list")`.
  - `test_zero_leagues_no_session_returns_list_url` — 0 Leagues, no session ⇒ `reverse("league_list")`.
  - `test_returned_key_is_top_bar_history_url` — every branch returns exactly `{"top_bar_history_url": str}` (single-key dict; value is a `str`).
  - `test_no_crash_when_request_has_no_session_attribute` — defensive: `RequestFactory()` may produce a request without `.session` (depending on middleware); the processor reads via `getattr(request, "session", {})` or `request.session.get("last_league_id")` — pin the no-crash behaviour.

### 9d. EXTENDED `matches/tests/test_league_dashboard.py`

Append two new test classes. Do NOT modify any existing LG-01c /
LG-01e class.

- `TestLg01fSidebarRendered`
  - `test_league_dashboard_renders_sidebar_partial` — `id="league-sidebar"` present.
  - `test_dashboard_entry_active_class` — `id="sidebar-top-dashboard"` has `active`-substring class.
  - `test_sidebar_links_has_14_entries`.
  - `test_history_entry_url_targets_this_leagues_history`.
- `TestLg01fSessionWrite`
  - `test_get_writes_last_league_id_to_session` — assert `client.session["last_league_id"] == league.id`.
  - `test_404_does_not_write_session`.

### 9e. EXTENDED `matches/tests/test_season_dashboard_view.py`

**DELETE** the LG-01c `TestSeasonDashboardSidebar` class wholesale —
its 5-entry assertions are obsolete under the 14-entry shape. (The
class header + all method bodies are removed.) Append two new test
classes:

- `TestLg01fSidebarRendered`
  - `test_season_dashboard_renders_sidebar_partial` — `id="league-sidebar"` present.
  - `test_sidebar_active_is_none_no_entry_active` — every entry has `active=False`.
  - `test_sidebar_links_has_14_entries`.
  - `test_lg01c_sidebar_dom_ids_are_absent` — `id="season-dashboard-sidebar"`, `id="season-dashboard-sidebar-standings"`, etc. ABSENT from the rendered HTML (the LG-01c sidebar markup is replaced).
- `TestLg01fSessionWrite`
  - `test_get_writes_last_league_id_to_session` — assert `client.session["last_league_id"] == season.league_id`.
  - `test_404_does_not_write_session`.

### 9f. EXTENDED `matches/tests/views_tests.py` (LG-01 standings + schedule tests)

For each existing LG-01 test class covering `season_standings` and
`season_schedule`, append minimal assertions (two per page):

- For `season_standings`:
  - `test_lg01f_sidebar_partial_rendered` — `id="league-sidebar"` present.
  - `test_lg01f_sidebar_active_is_standings` — `id="sidebar-league-standings"` has `active`-substring class.
  - `test_lg01f_session_write_last_league_id` — `client.session["last_league_id"] == season.league_id`.

- For `season_schedule`:
  - `test_lg01f_sidebar_partial_rendered`.
  - `test_lg01f_sidebar_active_is_league_schedule` — `id="sidebar-league-schedule"` element's `class` contains `"active"`; exactly one sidebar entry is active.
  - `test_lg01f_session_write_last_league_id` — `client.session["last_league_id"] == season.league_id`.

(The Tests agent picks the existing class to extend, or adds a new
`TestLg01fStandingsScheduleWiring` class — either is acceptable.)

### 9g. EXTENDED LG-01d + LG-01e view test files

For each LG-01d view (`start_season`, `play_week`, `play_two_months`,
`play_until_end`, `play_status`), append a single test:

- `test_lg01f_session_writes_last_league_id` — perform the locked
  POST (or GET on `play_status`) against a fixture League/Season; assert
  `client.session["last_league_id"] == season.league_id`.

For LG-01e `next_season`, append:

- `test_lg01f_session_writes_last_league_id_before_redirect` —
  POST a valid Start-Next-Season; assert the response is a 302 AND
  `client.session["last_league_id"] == league.id` (the session middleware
  commits the cookie before sending the redirect).

(The exact filename for each LG-01d view's test file is the
Tests agent's responsibility to locate — at LG-01f merge time these
live in `matches/tests/test_lg01d_*.py` per LG-01d's contract.)

### 9h. Test file ownership

| File | Status | Owner |
|---|---|---|
| `matches/tests/test_league_history.py` | NEW | Tests agent |
| `matches/tests/test_league_sidebar.py` | NEW | Tests agent |
| `matches/tests/test_league_nav_context_processor.py` | NEW | Tests agent |
| `matches/tests/test_league_dashboard.py` | EXTENDED — append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite` only | Tests agent |
| `matches/tests/test_season_dashboard_view.py` | EXTENDED — DELETE `TestSeasonDashboardSidebar`; append `TestLg01fSidebarRendered` + `TestLg01fSessionWrite` | Tests agent |
| `matches/tests/views_tests.py` | EXTENDED — append sidebar + session assertions to LG-01 standings + schedule tests | Tests agent |
| LG-01d view test files (5 endpoints) | EXTENDED — append session-write assertion per view | Tests agent |
| `matches/tests/test_lg01e_next_season.py` | EXTENDED — append session-write-before-redirect assertion | Tests agent |

**Tests must NOT touch `simulate_scheduled_round`, `simulate_match`,
`save_games`, or any simulator entry point.** LG-01f runs no
simulation. Tests that exercise an `"active"` or `"completed"` Season
do so by hand-constructing the persisted `Match` + `GameRound` +
`PlayerRoundState` rows (mirroring the LG-01c test fixture pattern).
A test that accidentally enters the simulator is a scope leak and is
locked out.

**Tests must NOT `mock.patch` the ORM** beyond the standard
`@override_settings` / `TestCase` machinery.

---

## 10. ADR + CONTEXT.md

- **ADR-0017** at `docs/adr/0017-league-context-nav-shape.md` is the
  design-decision record. The seam contract cites it; does **NOT**
  re-justify. The ADR covers: the 14-entry sidebar shape (Schedule
  added to LEAGUE as a 6th entry per the 2026-05-27 resolution), the
  session-driven top-bar History resolution chain, the rationale for
  replacing LG-01c's 5-entry sidebar wholesale, and the rationale for
  putting Schedule in LEAGUE rather than TEAM (league-level fixture
  list, not per-team).

- **No CONTEXT.md edit.** Sidebar / topnav / dropdown / session-pin
  terminology is implementation language, not domain language. The
  `League` / `Season` / `Standings` / `Matchday` glossary entries
  already exist; LG-01f introduces no new domain term.

---

## 11. Scope-out (locked)

The following are **explicitly out of scope** for LG-01f and must not
be touched by any of the three parallel agents:

- **No model change.** `matches/models.py`, `teams/models.py`, and
  `core/models.py` are read-only at LG-01f.
- **No migration.** LG-01e's `0029_*` (or whatever the latest LG-01x
  migration is) remains the final migration in the LG-01x stack
  through LG-01f.
- **No new pure module.** LG-01f's logic is thin view-glue plus one
  context processor; no aggregation worth factoring out. The
  `matches/season_dashboard.py` pure module gains zero new functions
  AND is NOT consumed (LG-01f doesn't render leaders / next-fixture).
  The `matches/standings.py` pure module is consumed verbatim (no
  edits); the `matches/schedule_generator.py` pure module is NOT
  consumed.
- **No simulator touch.** LG-01f runs no simulation, draws no random
  numbers, makes no `BatchSimulator` call. No SIM-07 / SIM-08 contract
  interaction. **No Score Calibration re-baseline obligation.**
- **No JS framework / htmx / Alpine / Stimulus.** Only Bootstrap 5's
  built-in dropdown JS (already a project dep) is used by the top-bar
  dropdown. The per-page selector uses `onchange="this.form.submit()"`
  inline JS (LG-00c precedent); no other inline JS. No `<script>`
  blocks anywhere new.
- **No API / DRF endpoint.** `/api/leagues/<id>/history/` is deferred —
  LG-01f is UI-only.
- **No `messages.success(...)` / `django.contrib.messages` flash.**
- **No new dependency** (no `pip install`, no `requirements.txt` edit,
  no JS framework, no npm install).
- **No admin change.** `LeagueAdmin` / `SeasonAdmin` / `TeamAdmin`
  registrations unchanged.
- **No CONTEXT.md edit** (per §10).
- **No edit to `matches/standings.py`** — LG-01 pure module consumed
  verbatim.
- **No edit to `matches/schedule_generator.py`** — not consumed by
  LG-01f.
- **No edit to `matches/season_dashboard.py`** — LG-01c / LG-01d pure
  module not touched.
- **No edit to `matches/tasks.py`** — no Celery touch.
- **No edit to `templates/leagues/list.html`** (LG-01a list page
  unchanged; the LG-01a per-row links to `/leagues/<id>/` already point
  at the LG-01c dashboard, which now adds the sidebar via §8b).
- **No edit to `templates/leagues/create.html`** (LG-01b create
  form unchanged).
- **No edit to LG-01d play-related templates beyond what §8b lists.**
  The LG-01d-rendered dropdown inside the dashboard's action-button
  area is untouched.
- **No "Archive League" toggle UI.** Deferred (admin-only).
- **No "Edit Draft Season" UI.** Deferred (admin-only).
- **No `Season.state="archived"` value.** Completed Seasons are
  already effectively read-only per LG-01.
- **No expansion of `sidebar_active` enum beyond the 14 locked
  literals + `None`.** LG-02+ may add new literals for new entries
  (e.g. `"playoffs"` becomes live when the Playoffs UI ships); those
  expansions are out of LG-01f scope.
- **No backfill** for existing Seasons without `champion_team` set
  beyond the defensive fallback in the Champion cell (§2a column 5).
  A management command to backfill `champion_team` for legacy
  completed Seasons is deferred.
- **No top-nav refactor beyond the `Leagues` → `League ▾` dropdown
  swap.** Other top-nav links (Teams, Players, Maps if present) are
  unchanged. The mode-based base.html restructure (different top-bar
  per LEAGUE / TEAM / PLAYERS mode) is **LG-01h's** scope; LG-01f
  partially skeletons it via the sidebar's 4-section grouping but
  does NOT implement mode-switching.
- **No new URL routes beyond `/leagues/<id>/history/`.** The disabled
  sidebar entries (Playoffs / Finances / Roster / etc.) and disabled
  top-bar dropdown items (Standings / Playoffs / Finances / Power
  Rankings) do NOT mount routes — they render as `<span class="disabled">`
  with no `<a href>`.
- **No re-baseline of LG-01c / LG-01d / LG-01e tests** beyond:
  - DELETE `TestSeasonDashboardSidebar` from `test_season_dashboard_view.py`
    (per §9e — its 5-entry assertions are obsolete).
  - Every other LG-01c / LG-01d / LG-01e test continues to pass without
    modification (the LG-01c dashboard's action-button DOM ids,
    `data-action-state` attributes, leaders / standings snippet DOM
    ids, etc. are all preserved by §8b's flex-container restructure).
- **No edit to PLAN.md by the Code agent or the Tests agent.** PLAN.md
  edits at land time are the Docs agent's job (§Constraints below
  references the post-merge PLAN.md note structure; the Docs agent
  fills it per LG-01x house style at land time).

---

## 12. Constraints (verbatim — paste into Step 7 agent prompts)

- Never prepend `cd` to a command, **git especially**. Run git from
  the repo root with no `cd`/`-C`.
- **Never run `git stash`, `git checkout`, `git reset`, `git restore`,
  or any destructive/worktree git** — agents share the repo `.git`;
  this reverts other agents' in-flight edits. Inspect with
  `git status` / `git diff` only.
- Do **not** run the full test suite (tests are authored in parallel).
  A scoped smoke/import check is fine.
- Stay in your lane (file ownership per §9h and §8 — Code agent owns
  `matches/views.py`, `matches/league_urls.py`, `core/context_processors.py`,
  `laserforce_simulator/settings.py` context-processor registration,
  `templates/leagues/history.html`, `templates/_partials/league_sidebar.html`,
  `templates/base.html`, `templates/leagues/dashboard.html`,
  `templates/seasons/dashboard.html`, `templates/seasons/standings.html`,
  `templates/seasons/schedule.html`. Tests agent owns every file
  under `matches/tests/` listed in §9. Docs agent owns the PLAN.md
  + ADR-0017 amendment + any LG-01h note extension; the Docs agent
  does NOT touch production code or tests.).

---

## Seam summary (one sentence)

LG-01f adds `GET /leagues/<int:league_id>/history/` →
`matches.views.league_history` (read-only paginated history table with
an in-progress row pinned at the top and 10 completed Seasons per page
by default), a NEW 14-entry sidebar partial
`templates/_partials/league_sidebar.html` (1 top + 6 LEAGUE + 4 TEAM +
3 PLAYERS entries) wired on 5 pages via
`matches.views._build_league_sidebar_links`, a NEW Bootstrap dropdown
in `templates/base.html` replacing the LG-01a `Leagues` link with a
`League ▾` toggle plus a 5-item menu whose History link is resolved by
a NEW context processor `core.context_processors.league_nav` (session
pin → single League → list-page fallback), and a session-write site
on every League-context view writing
`request.session["last_league_id"] = <league_id>` so the top-bar
History link resolves per-user — with one NEW URL, one NEW view, two
NEW helpers (`_build_history_row` + `_build_league_sidebar_links`),
two NEW pagination helpers (`_coerce_per_page` + `_coerce_page`), one
NEW context processor, one NEW partial, one NEW page template, five
MODIFIED templates, three NEW test files plus five+ EXTENDED test
files, the LG-01c `TestSeasonDashboardSidebar` class deleted as
obsolete, and **no model change, no migration, no new pure module, no
simulator touch, no RNG, no JS framework, no Celery, no `messages.*`,
no API endpoint, no new dependency, no admin change, no CONTEXT.md
edit**.
