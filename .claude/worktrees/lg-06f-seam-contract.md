# LG-06f Seam Contract — Watch List as a full stats view (+ per-League watch flag)

**Status:** locked. Code and Tests agents work in parallel from this file and must not disagree on a single name.

**Scope:** UI-only / session-only. NO model change, NO migration, NO simulator/RNG touch, NO Score Calibration re-baseline. CONTEXT.md (terms **Watch list**, **Watch flag**) and PLAN.md (LG-06h split-off) are ALREADY edited — do not touch them.

**One-line summary:** The Watch List screen is reshaped from a 3-column bookmark table into the **Player-Stats column set** filtered to watched players (zero-fill for watched players with no Rounds in scope); a ZenGM-style **watch flag** (in-row instant JS toggle, red when watched) lands on **8 league screens**; watch lists become **per-League** in the browser session.

---

## 1. Public signatures (every new/changed function + view + URL)

### Session helper — `matches/league_views.py`
```python
def _watched_player_ids(request: HttpRequest, league_id: int) -> set[int]:
    """LG-06f — the per-League watched-player id set for this browser session.

    Single source: reads request.session["watch_lists"].get(str(league_id), []),
    coerces each entry to int (silently dropping non-ints), returns a set[int].
    Consumed by BOTH core.context_processors.watch_list AND
    matches.league_screens.watch_list.watch_list. Never raises; missing key ⇒ set().
    """
```
- Owning module: `matches/league_views.py` (alongside `_coerce_per_page` / `_coerce_team_id` / `_coerce_season` etc.).
- `league_id` is the int already in the URL kwargs of every consumer.
- Coercion rule: an entry that is already `int` passes; a `str` that `int()`-parses passes; anything else (None, non-numeric str, float, dict) is dropped. De-dup is implicit via `set`.

### Context processor — `core/context_processors.py`
```python
def watch_list(request: HttpRequest) -> dict:
    """LG-06f — expose the watched-player id set to every league-screen template.

    Returns {"watched_player_ids": set[int]}.

    Resolves league_id from request.resolver_match.kwargs.get("league_id")
    (defensive: getattr(request, "resolver_match", None) — None when no match,
    e.g. a 404 before URL resolution or a RequestFactory()-built request).
    Off-League (no resolver_match, or no "league_id" kwarg) ⇒ {"watched_player_ids": set()}.
    Reuses matches.league_views._watched_player_ids for the read.
    """
```
- Owning module: `core/context_processors.py` (alongside `league_nav`, `app_mode`).
- Lazy import inside the function body: `from matches.league_views import _watched_player_ids` (mirrors the existing `league_nav` lazy-import-to-avoid-apps-cycle precedent).
- Registration: in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`, immediately AFTER `"core.context_processors.app_mode"` (so the final list is `... league_nav, app_mode, core.context_processors.watch_list`).
- Return-key is exactly `watched_player_ids`.

### Toggle endpoint — `matches/league_screens/watch_list.py`
```python
def watch_list_toggle(request: HttpRequest, league_id: int) -> JsonResponse:
    """LG-06f — POST-only flag toggle. Flips a player's membership in this
    League's session watch list. CSRF-protected (NOT exempt)."""
```
- POST-only. **First line of the body:** `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`.
- Step order (locked):
  1. `league = get_object_or_404(League, pk=league_id)`.
  2. Read `player_id` from `request.POST.get("player_id")`; coerce to int; invalid (None / non-numeric) ⇒ `return JsonResponse({"error": "invalid player_id"}, status=400)`.
  3. Validate `Player.objects.filter(pk=player_id).exists()`; **unknown id ⇒ 400** `JsonResponse({"error": "unknown player_id"}, status=400)` (locked decision: 400, not silent no-op).
  4. Read/normalise `lists = request.session.get("watch_lists", {})`; `key = str(league_id)`; `current = [int(x) for x in lists.get(key, []) if <int-coercible>]`.
  5. Flip: if `player_id in current` ⇒ remove (now `watched=False`); else append (now `watched=True`).
  6. Write back: `lists[key] = current`; `request.session["watch_lists"] = lists`; `request.session.modified = True`.
  7. `return JsonResponse({"watched": bool, "player_id": int})`.
- Owning module: `matches/league_screens/watch_list.py` (same module as the screen view).

### Watch List screen view (rewritten) — `matches/league_screens/watch_list.py`
```python
def watch_list(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-06f — Watch List as the Player-Stats column set filtered to watched
    players (zero-fill for watched players with no Rounds in scope)."""
```
- GET-only EXCEPT the retained `?action=clear` GET branch (see §7). First line: `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`.
- The legacy `?action=add|remove` GET branches are REMOVED. The add-form is DROPPED.

### Pure zero-fill helper — `matches/season_player_stats.py`
```python
def zero_fill_watched(
    rows: Iterable[PlayerStatRow],
    watched_ids: set[int],
    identity_by_id: Mapping[int, Mapping],
) -> list[PlayerStatRow]:
    """LG-06f — keep only watched rows, append a zero PlayerStatRow for each
    watched id with no Round in scope. Pure: dataclasses/typing only (the
    module's frozen no-Django import allowlist is preserved)."""
```
- Owning module: `matches/season_player_stats.py` (alongside `aggregate_player_stats`, `apply_rate`, `sort_player_stats`). NO new imports beyond what the module already has (`dataclasses`, `typing`).

### URL — `matches/league_urls.py`
```python
path(
    "<int:league_id>/players/watch-list/toggle/",
    league_screens.watch_list_toggle,
    name="watch_list_toggle",
),
```
- **Insertion point (locked):** immediately AFTER the existing `players/watch-list/` line (the `players_watch_list` route, currently lines ~56–60) and BEFORE any later catch-all. Because `players/watch-list/toggle/` is strictly longer than `players/watch-list/` and the final `path("", ...)` catch-all is the only catch-all, ordering only needs the toggle route to sit before that `""` entry — which the watch-list block already does. Placing it adjacent to `players_watch_list` keeps the pair together.
- URL name: `watch_list_toggle` (bare, no `app_name`). Full path: `/leagues/<int:league_id>/players/watch-list/toggle/`. Reachable because the whole include is mounted at `/leagues/<int:league_id>/...`.
- `league_screens.watch_list_toggle` must be re-exported from `matches/league_screens/__init__.py` (same place `watch_list` is already re-exported).

---

## 2. Session / data shapes

### `request.session["watch_lists"]` (the per-League store — LOCKED)
```python
request.session["watch_lists"]: dict[str, list[int]]
# keyed by str(league_id); value is the ordered list of watched Player ids
# e.g. {"3": [12, 47, 105], "8": [12]}
```
- The pre-LG-06f global `request.session["watch_list"]` (singular) key is **ABANDONED** — no migration, no read-compat, no fallback (session data is disposable, ADR-0004 precedent). Code must NOT read the old singular key anywhere.

### Toggle request body (POST form-encoded)
```
player_id=<int>   # the only field read; sent in the fetch body
```
- CSRF: the fetch sends the `X-CSRFToken` header from the `csrftoken` cookie (standard Django; the endpoint is NOT `@csrf_exempt`).

### Toggle response JSON
- Success (200): `{"watched": bool, "player_id": int}` — `watched` is the NEW state after the flip.
- Invalid player_id (400): `{"error": "invalid player_id"}`.
- Unknown player (400): `{"error": "unknown player_id"}`.
- Wrong method (405): `HttpResponseNotAllowed(["POST"])`.
- Missing League (404): `get_object_or_404(League)`.

### `identity_by_id` shape (input to `zero_fill_watched`, built by the view)
```python
identity_by_id: Mapping[int, Mapping]
# pid -> {"player_name": str, "team_id": int, "team_name": str, "role": str}
```
- The 4 value keys are REQUIRED on every entry. A watched id absent from `identity_by_id` (e.g. a deleted Player) is **silently skipped** by `zero_fill_watched` (no zero row emitted, no crash).
- Built in the view via:
```python
identity_by_id = {
    p.id: {
        "player_name": p.name,
        "team_id": p.team_id if p.team_id is not None else 0,
        "team_name": p.team.name if p.team is not None else "",
        "role": p.preferred_role,   # or the screen's existing role accessor; mirror player_stats _build_round_dicts' "role"
    }
    for p in Player.objects.filter(pk__in=watched_ids).select_related("team")
}
```
  (`role` source: use whatever single role string the Player exposes; this is identity-only display data, never aggregated. If no per-Player role exists, `""` is acceptable — pin to `""` only if the Code agent confirms no `Player`-level role field.)

### Zero `PlayerStatRow.stats` key set (LOCKED)
A zero row's `stats` mapping carries **every key in `STAT_KEYS` + `DERIVED_KEYS`**, each set to `0.0`:
```
points_scored, mvp, tags_made, times_tagged, accuracy, final_lives,
resupplies_given, missiles_landed, specials_used, follow_up_shots,
reaction_shots, combo_resupply_count,    # STAT_KEYS (12)
tag_ratio, survival                       # DERIVED_KEYS (2)
```
- The zero row's scalar fields: `player_id=pid`, `player_name`/`team_id`/`team_name`/`role` from `identity_by_id[pid]`, `games=0`, `stats={k: 0.0 for k in STAT_KEYS + DERIVED_KEYS}`.

### `zero_fill_watched` output order (LOCKED, deterministic)
1. **Aggregated rows first**, in their incoming order, filtered to `row.player_id in watched_ids`.
2. **Then zero rows**, one per watched id NOT present in the aggregated set AND present in `identity_by_id`, in **ascending player-id order** (`sorted(missing_ids)`).
- (Downstream `apply_rate` / `sort_player_stats` may re-order, but the helper's own output order is fixed as above so the unit test is deterministic.)

---

## 3. New flag / field / DOM-id names

### URL name & context-processor key
- URL name: `watch_list_toggle`.
- Context-processor return key: `watched_player_ids` (a `set[int]`).

### Partial paths
- `templates/_partials/watch_flag.html` — the per-cell flag button partial.
- `templates/_partials/watch_flag_script.html` — the once-per-page `<script>` partial.

### Flag button — DOM hooks (LOCKED)
- Element: `<button>` (type="button"), NOT a unique `id` attribute (Statistical Feats can render the same player on multiple rows — a unique id would collide). **Decision: use `class` + `data-player-id`, NO per-row `id`.**
- Base class: `watch-flag` (always present — the JS delegated handler selector).
- Watched-state class: `watch-flag-on` when `player_id in watched_player_ids`; absent otherwise. (Red styling keys off `.watch-flag-on`; the partial may also add a Bootstrap colour utility, but `.watch-flag-on` is the load-bearing toggle hook the test asserts on.)
- Attribute: `data-player-id="{{ player_id }}"` (the id the JS posts).
- The toggle URL is carried on the button via `data-toggle-url="{% url 'watch_list_toggle' league.id %}"` (rendered with the league id already baked in — `league.id` is in every league-screen context). The JS reads `button.dataset.toggleUrl`; it does NOT reconstruct the URL.
- Accessibility: `aria-pressed="true|false"` reflecting watched state (optional but recommended; not test-load-bearing).

### Flag partial include contract
```django
{% include "_partials/watch_flag.html" with player_id=<id> %}
```
- The partial reads `watched_player_ids` (from the context processor) + `league.id` (already in every league-screen context). It needs ONLY the `player_id` passed in.

### Script partial contract
- `templates/_partials/watch_flag_script.html` carries a single inline `<script>` with a **delegated** click handler bound once (e.g. `document.addEventListener("click", e => { const btn = e.target.closest(".watch-flag"); ... })`).
- On click: `fetch(btn.dataset.toggleUrl, {method:"POST", headers:{"X-CSRFToken": <csrftoken cookie>}, body: new URLSearchParams({player_id: btn.dataset.playerId})})` → on `{watched:true}` add `.watch-flag-on` to **all** buttons with that `data-player-id` (handles Statistical Feats' multi-row case), on `{watched:false}` remove it from all.
- The CSRF token is read from the `csrftoken` cookie (a small inline `getCookie("csrftoken")` helper inside the script partial).
- Included **exactly once per page** — each of the 8 screens includes it once near the end of its `{% block content %}` (NOT inside any loop).

### `watch-list-*` DOM ids (the Watch List screen surface)
- `watch-list-empty-notice` — PRESERVED. Two distinct messages share this id: "No Season" branch (substring `"No Season"` REQUIRED) and "watch list empty" branch.
- `watch-list-table` — PRESERVED (now renders the Player-Stats column set).
- `watch-list-remove-all` — PRESERVED (the Remove All control, now `?action=clear`).
- `watch-list-per-page-form` / `watch-list-per-page-select` — NEW (mirror `player-stats-per-page-*`).
- `watch-list-season-filter-form` / `watch-list-season-filter-select` — NEW (mirror `player-stats-season-filter-*`).
- `watch-list-rate-form` / `watch-list-rate-select` — NEW (mirror `player-stats-rate-*`).
- `watch-list-th-{key}` — NEW per sortable column (mirror `player-stats-th-{key}`).
- `watch-list-pagination` — NEW (mirror `player-stats-pagination`).
- **REMOVED ids:** `watch-list-add`, `watch-list-add-select`, `watch-list-row-{id}` (the add-form and the old 3-column rows are gone). Tests asserting these must be deleted/updated.
- `sidebar_active` for this screen stays `"watch_list"`.

---

## 4. Owning module per name

| Name | Owning module |
|---|---|
| `_watched_player_ids` | `matches/league_views.py` |
| `watch_list` (context processor) | `core/context_processors.py` |
| `watch_list_toggle` (view) | `matches/league_screens/watch_list.py` |
| `watch_list` (screen view, rewritten) | `matches/league_screens/watch_list.py` |
| `zero_fill_watched` | `matches/season_player_stats.py` |
| `watch_list_toggle` URL | `matches/league_urls.py` |
| `_build_round_dicts` (reused) | `matches/league_screens/player_stats.py` — **import it**: `from matches.league_screens.player_stats import _build_round_dicts` (recommended; no shared move needed — it already lives in `player_stats.py` and takes the same `prs_filter` shape) |
| `watch_flag.html` / `watch_flag_script.html` | `templates/_partials/` |
| context processor registration | `laserforce_simulator/laserforce_simulator/settings.py` |

---

## 5. Watch List pipeline (view body, LOCKED order)

Reuse the player_stats machinery. Inside `watch_list(request, league_id)` after the GET-guard, League 404, and the `?action=clear` branch (§7):

1. `request.session["last_league_id"] = league.id`.
2. `watched_ids = _watched_player_ids(request, league.id)`.
3. Resolve `displayed_season` (same chain as player_stats: `league.active_season or league.seasons.filter(state="completed").order_by("-id").first()`).
4. `sidebar_links = _build_league_sidebar_links(league, displayed_season, "watch_list")`.
5. Screen kit coercion: `sort = coerce_sort(...)`, `direction = coerce_dir(...)`, `per_page = _coerce_per_page(...)`, `rate = _coerce_rate(...)`; season scope via `seasons, selected_season, season_options, season_filter = _resolve_season_scope(request, league, displayed_season)`.
6. Empty-state: if `season_filter is None` → render with `page_obj=None`, `paginator=None`, empty querystring helpers (mirror player_stats' empty branch) and the `watch-list-empty-notice`.
7. Build `prs_filter = {f"game_round__{k}": v for k, v in season_filter.items()}` and `round_dicts = _build_round_dicts(prs_filter)`.
8. `rows = aggregate_player_stats(round_dicts)`.
9. **`identity_by_id`** = `{pid: {"player_name","team_id","team_name","role"}}` from `Player.objects.filter(pk__in=watched_ids).select_related("team")`.
10. **`rows = zero_fill_watched(rows, watched_ids, identity_by_id)`** — **NO team filter** anywhere in this screen.
11. `rows = apply_rate(rows, rate)`.
12. `rows = sort_player_stats(rows, sort, direction)`.
13. `paginator = Paginator(rows, per_page)`; `page_obj = paginator.get_page(_coerce_page(...))`.
14. Build `querystring_without_page` / `querystring_without_sort_dir_page` from COERCED values (mirror player_stats — carry `sort`/`dir`/`per_page`/`rate`/`season`, NO `team_id`).
15. Render `templates/leagues/watch_list.html`.

**Pipeline order (locked):** `_build_round_dicts → aggregate_player_stats → zero_fill_watched → apply_rate → sort_player_stats → Paginator`. (Note `zero_fill_watched` runs BEFORE `apply_rate` so zero rows get the same rate pass-through; zero games ⇒ `apply_rate` leaves zeros as zeros via its `games <= 0 ⇒ 0.0` guard.)

---

## 6. Watch List screen kit (controls)

- **Season selector** (+ Career) via `_resolve_season_scope` — `watch-list-season-filter-{form,select}`.
- **Rate toggle** via `_coerce_rate` + the 3 `_RATE_OPTIONS` (import the `_RATE_OPTIONS` tuple from `matches/league_screens/player_stats.py`, or redefine the identical `(("total","Totals"),("per_game","Per Game"),("per_10","Per 10 min"))` — recommend import) — `watch-list-rate-{form,select}`.
- **Per-page** via `_coerce_per_page` / `_coerce_page` + `_LG01F_PER_PAGE_OPTIONS` — `watch-list-per-page-{form,select}`.
- **Sortable columns** via `coerce_sort` / `coerce_dir` / `sort_player_stats` from `season_player_stats` — `watch-list-th-{key}`.
- **Column spec:** mirror `_PLAYER_STATS_COLUMNS` (the same `(sort_key, label, is_float)` tuple — import it from `player_stats.py` or redefine identically; recommend import). The per-row `<td>` set mirrors `player_stats.html` rows EXCEPT the player-name cell hosts the watch flag (see §9) and the legacy "Action"/"Remove" column is gone.
- **NO team filter** on this screen (the Watch List is a personal cross-team set).
- **Remove All:** the existing `?action=clear` GET is RETAINED — now it clears `request.session["watch_lists"][str(league_id)]` (set to `[]`, or `pop(str(league_id), None)`), sets `request.session.modified = True`, then `redirect` to the bare watch-list URL (`/leagues/<league_id>/players/watch-list/`). The `watch-list-remove-all` anchor `href="?action=clear"` is preserved.
- **Per-row flag replaces the old Remove control** — the flag toggle endpoint (§ toggle) is the only per-row mutation now.

---

## 7. Retained `?action=clear` branch (LOCKED)

Inside `watch_list`, after the GET-guard and `league = get_object_or_404(League, ...)`:
```python
if request.GET.get("action") == "clear":
    lists = request.session.get("watch_lists", {})
    lists.pop(str(league.id), None)          # or lists[str(league.id)] = []
    request.session["watch_lists"] = lists
    request.session.modified = True
    return redirect(f"/leagues/{league.id}/players/watch-list/")
```
- Only `action=clear` is honoured. `action=add|remove` are GONE (no branch — fall through to normal render, which is harmless).

---

## 8. 8-screen wiring (EXACT player-name cell per screen)

Each screen already has `league` + (post-context-processor) `watched_player_ids` in context. Insert `{% include "_partials/watch_flag.html" with player_id=<id> %}` into the player-name cell, and include `{% include "_partials/watch_flag_script.html" %}` exactly ONCE near the end of `{% block content %}`.

| # | Template | Current player-name line | `player_id` to pass |
|---|---|---|---|
| 1 | `templates/leagues/player_stats.html` | L110 `<td><a href="{% url 'player_career_stats' row.player_id %}">{{ row.player_name }}</a></td>` | `row.player_id` |
| 2 | `templates/leagues/player_ratings.html` | L86 `<td><a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` | `player.id` |
| 3 | `templates/leagues/free_agents.html` | L71 `<td><a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` | `player.id` |
| 4 | `templates/leagues/league_leaders.html` | 4 board rows, e.g. L56 `<a href="/players/{{ row.player_id }}/stats/">{{ row.player_name }}</a>,` (also L94, L132, L170) | `row.player_id` (insert flag in EACH of the 4 board rows) |
| 5 | `templates/leagues/statistical_feats.html` | L119 `<td>{{ row.player_name }}</td>` (plain text, no link; `row.player_id` IS available on `FeatRow`) | `row.player_id` |
| 6 | `templates/leagues/team_roster.html` | TWO sections — L57 and L95 both `<td><a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` (insert flag in BOTH) | `player.id` |
| 7 | `templates/leagues/team_history.html` | L163 `<td><a href="{% url 'player_career_stats' p.player_id %}">{{ p.name }}</a></td>` | `p.player_id` |
| 8 | `templates/leagues/watch_list.html` | rewritten — player-name cell mirrors player_stats: `<td>... {% include "_partials/watch_flag.html" with player_id=row.player_id %} <a href="{% url 'player_career_stats' row.player_id %}">{{ row.player_name }}</a></td>` | `row.player_id` |

Notes:
- League Leaders renders the same player across up to 4 boards — the multi-button-per-player case is exactly why the JS updates ALL buttons sharing a `data-player-id` rather than a single `id`.
- Statistical Feats can render the same player on multiple feed rows — same multi-button rule applies; confirms the `data-player-id`+class decision (NO unique `id`).
- The flag sits adjacent to the existing player link (don't remove the career-stats link); the include adds a `<button>` before/after the `<a>`.

---

## 9. Flag rendering detail (the partial body, locked surface)

`templates/_partials/watch_flag.html` renders:
```django
<button type="button"
        class="watch-flag{% if player_id in watched_player_ids %} watch-flag-on{% endif %}"
        data-player-id="{{ player_id }}"
        data-toggle-url="{% url 'watch_list_toggle' league.id %}"
        aria-pressed="{% if player_id in watched_player_ids %}true{% else %}false{% endif %}"
        title="Toggle watch">&#9733;</button>
```
- `&#9733;` (★) or any glyph — not test-load-bearing; the load-bearing surface is `class="watch-flag"`, the `.watch-flag-on` toggle, `data-player-id`, `data-toggle-url`.
- Red colour is driven by a CSS rule on `.watch-flag-on` (Code agent's discretion on exact colour; the class presence is what tests assert).

---

## 10. Test boundary (what Tests assert vs what's internal)

Three new test files + pure-unit tests.

### `matches/tests/test_watch_flag.py`
- **Partial render:** rendering a league screen (e.g. player_stats) with a watched player ⇒ that player's flag has class `watch-flag-on` (red); an unwatched player's flag has `watch-flag` but NOT `watch-flag-on` (grey).
- **Context processor resolution:** `watch_list(request)` returns `{"watched_player_ids": <set>}` matching the session store for the resolved `league_id`; **off-League** (request with no `resolver_match` / no `league_id` kwarg) ⇒ empty set. Test directly via `RequestFactory` + a stubbed `resolver_match`.
- Boundary: the JS toggle *behaviour* is NOT asserted here at unit level — only the rendered class + the context-processor output.

### `matches/tests/test_watch_toggle.py`
- Endpoint **add** (player not in list ⇒ `{"watched": true}`, session gains the id under `str(league_id)`).
- Endpoint **remove** (player in list ⇒ `{"watched": false}`, session drops the id).
- **Per-League isolation:** toggling in league A does not affect league B's list (`watch_lists` keyed by `str(league_id)`).
- **405** on GET. **400** on invalid `player_id` (`{"error": ...}`). **400** on unknown (non-existent) `player_id`. **404** on missing League.
- **CSRF:** a POST without the CSRF token is rejected (the endpoint is NOT exempt). (Use Django test client with CSRF checks enabled for this assertion.)

### `matches/tests/test_league_watch_list.py`
- Screen **200 / 405 / 404**.
- **Empty-state:** no Season ⇒ `watch-list-empty-notice` with substring `"No Season"`; Season present but no watched players ⇒ empty notice (watch-list-empty message).
- **Zero-fill:** a watched player with no Rounds in scope appears as a zero row (games=0, all stat cells 0) in `watch-list-table`.
- **Sort / per-page / season / rate:** the kit controls work (DOM ids present; sort reorders; per-page selector caps rows; season selector + Career; rate toggle).
- **Remove All:** `?action=clear` empties `watch_lists[str(league_id)]` and redirects to the bare URL.
- **Flag replaces Remove:** the old `watch-list-add` / `watch-list-row-{id}` / Remove-link surfaces are ABSENT; the flag (`watch-flag`) is present in the player-name cell.
- **Flag-present smoke asserts:** the `watch-flag` button renders on `player_stats`, `free_agents`, `team_roster` (and the script partial appears exactly once per page).

### Pure-unit `zero_fill_watched` tests
- Location: in `matches/tests/test_league_watch_list.py` (LOCKED choice — keeps the watch-list surface in one file; do NOT spin a separate `season_player_stats` test file for this).
- Assert: filters to watched ids; appends zero rows for missing-but-watched ids in ascending-id order; zero rows carry every `STAT_KEYS + DERIVED_KEYS` key at `0.0` and `games=0`; a watched id missing from `identity_by_id` is silently skipped; aggregated-rows-first / zero-rows-second deterministic order.

---

## 11. Non-goals / locked exclusions
- No model, no migration, no simulator/RNG touch, no Score Calibration re-baseline.
- No read-compat for the abandoned singular `session["watch_list"]` key.
- No team filter on the Watch List screen.
- No headless-browser test (JS behaviour is asserted via the endpoint contract + partial render only).
- No CSRF exemption on the toggle endpoint.
- No CONTEXT.md / PLAN.md edits (already done).
