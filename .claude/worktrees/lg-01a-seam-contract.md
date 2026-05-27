# LG-01a — Seam contract

**Task:** Mode-picker landing (`/`) + `/leagues/` index list.

Replace the existing `/` homepage redirect (today `path("", include("teams.urls"))`) with a card-based mode-picker landing (Sandbox / Single-player League / Multiplayer-greyed) that also lists in-progress Leagues. Add `/leagues/` as a flat list of every League (active + archived sections) with a `Create League` button.

**No model change. No migration. No ADR. No CONTEXT.md edit. No JS. No new dependency.** Pure read-only views + templates + a 2-line `urls.py` patch + a 2-line `base.html` navbar patch.

Pinned by the LG-01a grilling decisions and the locked patterns from LG-00 / LG-00b / LG-00c / LG-01 (read-only view, no aggregation module, no class — LG-00c precedent).

---

## Scope

- ONE new view `core.views.landing` (mode picker + in-progress Leagues card grid).
- ONE new view `matches.views.league_list` (active + archived tables).
- TWO new templates (`templates/core/landing.html`, `templates/leagues/list.html`).
- ONE new URL include file (`matches/league_urls.py`) — mirrors `matches/season_urls.py` (no `app_name`).
- TWO edits to `laserforce_simulator/urls.py` (replace one line; add one line).
- TWO edits to `laserforce_simulator/templates/base.html` (`navbar-brand` href + ONE new `nav-link`).
- TWO test files extended/added (`core/tests.py`, `matches/tests/test_league_list.py`).

The Sandbox card LINKS to the existing `/teams/` URL — `/teams/` itself is unchanged; only the duplicate mount at `/` is removed. `team_list` and the `{% url 'team_list' %}` reverse keep resolving to `/teams/`.

---

## Files modified

### `laserforce_simulator/laserforce_simulator/urls.py`

Pinned existing line set (do NOT touch beyond the diff below):

```python
from django.conf import settings
from django.conf.urls.static import static
from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/", include("laserforce_simulator.api_urls")),
    path("teams/", include("teams.urls")),
    path("matches/", include("matches.urls")),
    path("seasons/", include("matches.season_urls")),
    path("maps/", include("core.urls")),
    path(
        "players/", include("teams.player_urls")
    ),  # HX-01: must be above the "" include
    path("", include("teams.urls")),  # Homepage still goes to teams
] + static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

**Diff (locked, exact):**

1. **ADD** this import line below the existing `from django.urls import path, include` line:
   ```python
   from core import views as core_views
   ```
2. **REPLACE** the line
   ```python
   path("", include("teams.urls")),  # Homepage still goes to teams
   ```
   with
   ```python
   path("", core_views.landing, name="landing"),
   ```
3. **INSERT** the following line **immediately after** the existing `path("seasons/", include("matches.season_urls")),` line (and before `path("maps/", ...)`):
   ```python
   path("leagues/", include("matches.league_urls")),
   ```

The `path("teams/", include("teams.urls"))` line is **unchanged** — `{% url 'team_list' %}` still reverses to `/teams/`. The `path("players/", ...)` HX-01 ordering comment stays meaningful (`players/` is still above the homepage line) — no rewrite of that comment.

### `laserforce_simulator/laserforce_simulator/templates/base.html`

Pinned existing navbar block (do NOT touch lines outside the two diffs below):

```html
<a class="navbar-brand" href="{% url 'team_list' %}">⚡ Laserforce Manager</a>
...
<div class="navbar-nav ms-auto">
    <a class="nav-link" href="{% url 'team_list' %}">Teams</a>
    <a class="nav-link" id="player-list-nav-link" href="{% url 'player_list' %}">Players</a>
    <a class="nav-link" href="{% url 'match_list' %}">Matches</a>
    <a class="nav-link" href="{% url 'simulate_batch' %}">Batch Sim</a>
    <a class="nav-link" href="{% url 'team_create' %}">Create Team</a>
    <a class="nav-link" href="{% url 'map_list' %}">Maps</a>
</div>
```

**Diff (locked, exact):**

1. **REPLACE**
   ```html
   <a class="navbar-brand" href="{% url 'team_list' %}">⚡ Laserforce Manager</a>
   ```
   with
   ```html
   <a class="navbar-brand" href="{% url 'landing' %}">⚡ Laserforce Manager</a>
   ```
   The visible brand text (`⚡ Laserforce Manager`) and the unicode `⚡` are unchanged.

2. **INSERT** the following line as the **FIRST child** of the existing `<div class="navbar-nav ms-auto">` block, **above** the existing `Teams` `<a>` line:
   ```html
   <a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>
   ```

Do not touch the other `<a class="nav-link">` lines. Do not touch the `navbar-toggler`, `collapse navbar-collapse`, or container chrome.

### `laserforce_simulator/laserforce_simulator/matches/views.py`

Append ONE new view function `league_list(request)` (signature below). Place it next to the existing `season_standings` (matches/views.py:1760) / `season_schedule` (:1841) functions — at end of file is acceptable. No existing view is modified. No new imports beyond what is already imported by the surrounding views (`HttpResponse`, `render`, `League`).

Add (if not already imported at module top):

```python
from .models import League
```

### `laserforce_simulator/laserforce_simulator/core/views.py`

Append ONE new view function `landing(request)` (signature below). Place it at the bottom of the file (after `map_heatmap_data`). No existing view is modified.

Add imports at module top (if not already present):

```python
from django.http import HttpResponse  # already imported via HttpResponseBadRequest/HttpResponseNotAllowed siblings — re-use the existing line, no duplicate import
from matches.models import League
```

(The `matches.models` import is **lazy-safe** at module-top here — `core/views.py::map_heatmap_data` already does a lazy `from matches.models import GameRound, PlayerRoundState` inside its function body to avoid an apps-loading cycle. To mirror that precedent, **place the `from matches.models import League` import INSIDE the `landing` function body**, NOT at module top, to stay consistent with the established pattern.)

### `laserforce_simulator/laserforce_simulator/core/tests.py`

Extend with the landing-view test class (test method names pinned in `## Tests` below). No existing test is modified.

---

## Files added

### `laserforce_simulator/laserforce_simulator/matches/league_urls.py`

NEW file. Mirrors `matches/season_urls.py` (no `app_name` — bare URL name).

Exact contents:

```python
"""LG-01a — League URL patterns mounted at ``/leagues/`` by the project
URLconf. No ``app_name`` so reverse uses the bare name ``league_list``.
"""

from django.urls import path

from . import views

urlpatterns = [
    path("", views.league_list, name="league_list"),
]
```

### `laserforce_simulator/templates/core/landing.html`

NEW file. See `## Templates & DOM ids` below for the full DOM spec.

### `laserforce_simulator/templates/leagues/list.html`

NEW file. See `## Templates & DOM ids` below for the full DOM spec.

### `laserforce_simulator/laserforce_simulator/matches/tests/test_league_list.py`

NEW file. Django `TestCase`. See `## Tests` below for the pinned test method names.

---

## Public surface (locked names)

| Kind | Name | Path |
|---|---|---|
| View | `core.views.landing` | `core/views.py` (new, bottom of file) |
| View | `matches.views.league_list` | `matches/views.py` (new, end of file) |
| URL name | `landing` | `/` (project urls.py) |
| URL name | `league_list` | `/leagues/` (matches/league_urls.py) |
| URL include file | `matches/league_urls.py` | NEW (mirrors `matches/season_urls.py`) |
| Template | `templates/core/landing.html` | NEW |
| Template | `templates/leagues/list.html` | NEW |
| Test file | `core/tests.py` | EXTENDED |
| Test file | `matches/tests/test_league_list.py` | NEW |

No new model. No new migration. No new dataclass. No new module. No new dependency. No new `URL name` beyond `landing` + `league_list`.

---

## Views

### `core.views.landing(request) -> HttpResponse`

**Decorator:** none.

**Method:** any (Django default — view is GET-driven; no explicit allowlist needed since the only template renders are idempotent and the view writes nothing).

**Body (locked):**

1. Lazy-import `from matches.models import League` inside the function (mirrors the `map_heatmap_data` lazy-import precedent in `core/views.py`).
2. Query: `in_progress_leagues = list(League.objects.filter(state="active").order_by("-id"))` — one ORM call. Sort key `-id` is locked (the LG-01 grilling decision: "in-progress" = `state == "active"`, sorted `-id`).
3. **Do NOT** `select_related` or `prefetch_related` `active_season` — `League.active_season` is a `@property` (not an FK; defined on `models.py:833`), so per-card it issues one extra `Season.objects.exclude(state="completed").order_by("-id").first()` query. This is acceptable: the landing page is bounded by the user-created League count and writing a custom prefetch is over-engineering for LG-01a. (If perf ever bites, the optimisation is non-breaking — add a `Prefetch` later.)
4. Render `templates/core/landing.html` with context **exactly** `{"in_progress_leagues": in_progress_leagues}`.
5. Return the rendered `HttpResponse`.

**Context contract (frozen — template reads only these):**

| Key | Type | Notes |
|---|---|---|
| `in_progress_leagues` | `list[League]` | Each entry is the `League` model instance; template accesses `league.id`, `league.name`, and `league.active_season` (which itself exposes `.name`). |

### `matches.views.league_list(request) -> HttpResponse`

**Decorator:** none.

**Method:** any (same reasoning as `landing`).

**Body (locked):**

1. `active_leagues = list(League.objects.filter(state="active").order_by("-id"))`
2. `archived_leagues = list(League.objects.filter(state="archived").order_by("-id"))`
3. Render `templates/leagues/list.html` with context **exactly** `{"active_leagues": active_leagues, "archived_leagues": archived_leagues}`.

**Context contract (frozen):**

| Key | Type | Notes |
|---|---|---|
| `active_leagues` | `list[League]` | Sorted by `-id`. |
| `archived_leagues` | `list[League]` | Sorted by `-id`. |

The view does **NOT** read any other model fields beyond `id`, `name`, `state` (the template renders these). It does NOT touch `Season` or `active_season` — `/leagues/` is the flat list; per-League dashboards are deferred to LG-01c.

---

## Templates & DOM ids

### `templates/core/landing.html`

`{% extends "base.html" %}`

`{% block title %}Laserforce Manager{% endblock %}`

`{% block content %}` body structure (locked DOM ids in **bold**):

```
<h1>... headline text ...</h1>

<section id="mode-picker">
    <div class="row row-cols-1 row-cols-md-3 g-3">
        <a id="mode-card-sandbox" class="..." href="{% url 'team_list' %}">
            ... card title: "Sandbox" + short description ...
        </a>
        <a id="mode-card-league" class="..." href="{% url 'league_list' %}">
            ... card title: "Single-player League" + short description ...
        </a>
        <div id="mode-card-multiplayer" class="... opacity-50 ..." aria-disabled="true">
            ... card title: "Multiplayer" + <span class="badge bg-secondary">Coming soon</span> ...
        </div>
    </div>
</section>

{% if in_progress_leagues %}
<section id="in-progress-leagues">
    <h2>In Progress</h2>
    <div class="row row-cols-1 row-cols-md-3 g-3">
        {% for league in in_progress_leagues %}
        <a id="in-progress-league-card-{{ league.id }}" class="card ..." href="/leagues/{{ league.id }}/">
            <div class="card-body">
                <h5 class="card-title">{{ league.name }}</h5>
                <p class="card-text">
                    {% if league.active_season %}
                        Season: {{ league.active_season.name }}
                    {% else %}
                        No active season
                    {% endif %}
                </p>
                <span class="state-badge ...">{{ league.state }}</span>
            </div>
        </a>
        {% endfor %}
    </div>
</section>
{% endif %}
```

**Locked DOM ids (pinned by tests — do not rename, do not omit, do not nest under different parents):**

| id | Element | Notes |
|---|---|---|
| `mode-picker` | outer `<section>` (or `<div>`) containing the 3 mode cards | Wraps the row of cards. |
| `mode-card-sandbox` | anchor `<a>` reversing `{% url 'team_list' %}` | Card title text contains substring `Sandbox`. |
| `mode-card-league` | anchor `<a>` reversing `{% url 'league_list' %}` | Card title text contains substring `Single-player League`. |
| `mode-card-multiplayer` | **non-anchor** `<div>` with `aria-disabled="true"` | Card title text contains substring `Multiplayer`. Contains `<span class="badge bg-secondary">Coming soon</span>`. Visually greyed via Bootstrap `opacity-50` (or equivalent muted styling). Must NOT be wrapped in `<a>`. |
| `in-progress-leagues` | wrapping `<section>` | Rendered **only when** `in_progress_leagues` is non-empty (i.e. the empty `{% if in_progress_leagues %}` branch renders **no** notice — substring `id="in-progress-leagues"` must NOT appear in the HTML when zero active Leagues exist). |
| `in-progress-league-card-{league.id}` | one anchor `<a>` per active League | `href="/leagues/{{ league.id }}/"` — **raw URL string**, NOT `{% url ... %}` (the `league_detail` URL name does not yet exist; this is the deferred known-broken link to LG-01c). Contains `league.name`. Contains either the substring `Season: {{ league.active_season.name }}` (when `league.active_season` is truthy) or the literal substring `No active season` (otherwise). Contains a state badge whose `class` attribute contains the substring `state-badge`. |

**Mode-card copy (suggested, not test-pinned):**

- Sandbox: subtitle e.g. "Quick one-off matches with custom teams."
- Single-player League: subtitle e.g. "Run a multi-Season campaign against the CPU."
- Multiplayer: subtitle e.g. "Play against other humans online."

The exact subtitle wording is not pinned; the title text + the locked DOM ids + the locked substrings (`Coming soon`, `aria-disabled="true"`, `No active season`) are pinned.

### `templates/leagues/list.html`

`{% extends "base.html" %}`

`{% block title %}Leagues{% endblock %}`

`{% block content %}` body structure (locked DOM ids in **bold**):

```
<div class="d-flex justify-content-between align-items-center mb-3">
    <h1>Leagues</h1>
    <a id="league-create-link" class="btn btn-primary" href="/leagues/create/">Create League</a>
</div>

{% if active_leagues %}
<h2>Active</h2>
<table id="league-list-active-table" class="table table-striped">
    <thead><tr><th>Name</th><th>State</th></tr></thead>
    <tbody>
        {% for league in active_leagues %}
        <tr>
            <td><a href="/leagues/{{ league.id }}/">{{ league.name }}</a></td>
            <td><span class="state-badge ...">{{ league.state }}</span></td>
        </tr>
        {% endfor %}
    </tbody>
</table>
{% endif %}

{% if archived_leagues %}
<h2>Archived</h2>
<table id="league-list-archived-table" class="table table-striped">
    ... same row shape ...
</table>
{% endif %}

{% if not active_leagues and not archived_leagues %}
<div id="league-list-empty-notice" class="alert alert-info">
    No Leagues yet.
</div>
{% endif %}
```

**Locked DOM ids:**

| id | Element | Notes |
|---|---|---|
| `league-create-link` | anchor `<a>` | `href="/leagues/create/"` — **raw URL string** (the `league_create` URL name does not yet exist; this is the deferred known-broken link to LG-01b). Visible text contains substring `Create League`. Rendered on every load (whether or not the lists are empty). |
| `league-list-active-table` | `<table>` | Rendered **only when** `active_leagues` is non-empty. Omitted otherwise. |
| `league-list-archived-table` | `<table>` | Rendered **only when** `archived_leagues` is non-empty. Omitted otherwise. |
| `league-list-empty-notice` | `<div>` | Rendered **only when both lists are empty**. Contains substring `No Leagues yet`. |

**Per-row link shape (locked):** each row's League-name `<td>` contains an `<a>` whose `href` is the raw string `/leagues/{{ league.id }}/` (NOT `{% url 'league_detail' ... %}`). Each row also shows `league.state` rendered inside an element whose `class` attribute contains the substring `state-badge`.

---

## Navbar patch (base.html — repeated for emphasis)

Exactly two edits to `templates/base.html`:

1. `navbar-brand` href: `{% url 'team_list' %}` → `{% url 'landing' %}`. Visible text (`⚡ Laserforce Manager`) and the unicode `⚡` are unchanged.
2. Insert as the FIRST child of `<div class="navbar-nav ms-auto">`, ABOVE the existing `Teams` link:
   ```html
   <a class="nav-link" id="leagues-nav-link" href="{% url 'league_list' %}">Leagues</a>
   ```

Do not touch any other `<a class="nav-link">` line. Do not reorder, restyle, or rename the existing nav links. Do not change the navbar container, toggler, or collapse div.

---

## Tests

### `core/tests.py` — APPEND test class

Django `TestCase`. Test method names ARE part of the contract (the Tests agent MUST use these exact names; the Code agent's template MUST satisfy them).

| Test method | What it asserts |
|---|---|
| `test_landing_get_returns_200_with_default_context` | `client.get(reverse("landing"))` → 200; response.context contains `in_progress_leagues` (an iterable). |
| `test_landing_renders_three_mode_card_dom_ids` | Substrings `id="mode-card-sandbox"` AND `id="mode-card-league"` AND `id="mode-card-multiplayer"` all present in response body. |
| `test_landing_sandbox_card_links_to_team_list` | The `mode-card-sandbox` anchor's `href` resolves to the same URL as `reverse("team_list")` (i.e. `/teams/`). |
| `test_landing_league_card_links_to_league_list` | The `mode-card-league` anchor's `href` resolves to the same URL as `reverse("league_list")` (i.e. `/leagues/`). |
| `test_landing_multiplayer_card_is_non_anchor_with_coming_soon_badge` | Assert (a) substring `id="mode-card-multiplayer"` present, (b) the element with that id is NOT wrapped in `<a` (i.e. no `<a ... id="mode-card-multiplayer"` substring), (c) substring `Coming soon` present, (d) substring `aria-disabled="true"` present on the multiplayer card. |
| `test_landing_omits_in_progress_section_when_no_active_leagues` | With zero active Leagues in the DB, substring `id="in-progress-leagues"` is NOT present in response body. |
| `test_landing_lists_active_leagues_as_cards_sorted_by_id_desc` | Create two active Leagues (`L1`, then `L2`); GET `/`; assert both substrings `id="in-progress-league-card-{L1.id}"` and `id="in-progress-league-card-{L2.id}"` present AND the position of `L2`'s card id appears BEFORE `L1`'s in the body (sorted `-id`). |
| `test_landing_in_progress_card_links_to_deferred_league_detail_url` | For each active League card, assert substring `href="/leagues/{league.id}/"` present in the body (deferred broken link; LG-01c). |
| `test_landing_in_progress_card_shows_active_season_name_when_present` | Create a League with an active Season named `"Season 1"`; GET `/`; assert substring `Season: Season 1` present in the body. |
| `test_landing_in_progress_card_shows_no_active_season_subtitle_when_absent` | Create a League with NO active Season (no Season rows, or all completed); GET `/`; assert substring `No active season` present in the body. |
| `test_landing_excludes_archived_leagues_from_in_progress_section` | Create one `state="active"` League (`LA`) and one `state="archived"` League (`LZ`); GET `/`; assert substring `id="in-progress-league-card-{LA.id}"` IS present AND substring `id="in-progress-league-card-{LZ.id}"` is NOT present. |
| `test_root_url_reverses_to_landing_view` | `reverse("landing") == "/"`. |

### `matches/tests/test_league_list.py` — NEW file

Django `TestCase`. Test method names pinned (Tests agent MUST use these exact names).

| Test method | What it asserts |
|---|---|
| `test_league_list_get_returns_200_with_default_context` | `client.get(reverse("league_list"))` → 200; response.context contains `active_leagues` AND `archived_leagues`. |
| `test_league_list_url_reverses` | `reverse("league_list") == "/leagues/"`. |
| `test_league_list_empty_shows_empty_notice_and_create_button` | With zero Leagues, GET `/leagues/`; assert substring `No Leagues yet` present AND substring `id="league-create-link"` present. |
| `test_league_list_active_table_lists_active_leagues_sorted_by_id_desc` | Create two `state="active"` Leagues (`L1` then `L2`); GET `/leagues/`; assert substring `id="league-list-active-table"` present AND both names present AND `L2.name` appears before `L1.name` in the body. |
| `test_league_list_archived_table_lists_archived_leagues_sorted_by_id_desc` | Symmetric to the active case but with `state="archived"`. |
| `test_league_list_omits_active_table_when_no_active_leagues` | Create only archived Leagues; assert substring `id="league-list-active-table"` is NOT present. |
| `test_league_list_omits_archived_table_when_no_archived_leagues` | Create only active Leagues; assert substring `id="league-list-archived-table"` is NOT present. |
| `test_league_list_row_links_to_deferred_league_detail_url` | For each row, assert substring `href="/leagues/{league.id}/"` present in the body. |
| `test_create_league_link_points_to_deferred_lg01b_route` | Assert substring `href="/leagues/create/"` present in the body (deferred broken link; LG-01b). |

### Navbar regression test — choose ONE file (either `core/tests.py` OR `matches/tests/test_league_list.py`)

The Tests agent picks the placement; the assertion is the same in either home.

| Test method | What it asserts |
|---|---|
| `test_base_html_navbar_brand_links_to_landing_and_leagues_nav_link_present` | GET any view that extends `base.html` (e.g. `client.get(reverse("landing"))`); assert substring `id="leagues-nav-link"` present in the body AND the navbar-brand's `href` attribute value is `/` (i.e. substring `class="navbar-brand" href="/"` or equivalent — the exact attribute order is fine to match by either substring `navbar-brand` + `href="/"` near each other, or by parsing with `re` — your call). |

### Test data conventions

- The `League` model lives at `matches.models.League` (LG-01); `League(name=..., state="active"|"archived")` is sufficient — `mode` defaults to `"league"`, `created_at` is `auto_now_add`. No Team / Season required to construct a League. For the `active_season`-present test, create a `Season(league=<L>, name="Season 1", start_date=<any date>)` — `state` defaults to `"draft"` (which is non-`completed`, so `active_season` will return it).
- For the navbar regression test, do NOT instantiate any Leagues — the page must render with an empty in-progress section.

---

## Out of scope (deliberate, pinned)

- **No model change, no migration, no ADR, no CONTEXT.md edit.**
- **No new pure-aggregation module** (LG-00c precedent — read-only view with inline `.filter().order_by()` is sufficient; here the views are even simpler than LG-00c).
- **No JS.**
- **No new dependency.**
- **No simulator touch, no `_flush_to_db` touch, no SIM-07/SIM-08 contract interaction, no Score Calibration re-baseline obligation.**
- **No change to the existing `/teams/` route, the `team_list` view, the `team_list` URL name, or any of the existing `{% url 'team_list' %}` references** in other templates. Removing the duplicate `path("", include("teams.urls"))` mount does NOT affect `path("teams/", include("teams.urls"))` — `team_list` keeps reversing to `/teams/`.
- **No League dashboard, no League create flow, no Play Next, no Start Next Season** — those are LG-01b / LG-01c / LG-01d. The deferred `/leagues/<id>/` link AND the `/leagues/create/` button are KNOWN-BROKEN at LG-01a merge time; the web-smoke triage acknowledges these 404s.
- **No top-level `home` URL name** — the new URL name is `landing` (not `home`, not `index`).
- **No HX-01 ordering change** — `path("players/", include("teams.player_urls"))` stays above the homepage line. The HX-01 comment can stay verbatim, since "must be above the `''` include" is still accurate: there is still a `path("", ...)` line, it just now points to `core_views.landing` instead of `include("teams.urls")`.
- **No `app_name` in `matches/league_urls.py`** — bare URL name `league_list`, mirroring `matches/season_urls.py`.
- **No restyling, reordering, or relabelling of any existing navbar `<a class="nav-link">` line** — only the brand `href` changes, and exactly ONE new line is inserted.
- **No `select_related` / `prefetch_related` on `League.active_season`** in `landing` — it is a `@property`, not an FK. Iteration-with-per-card-query is the locked LG-01a approach (perf is acceptable for a user-bounded landing list; revisit only if it bites).
