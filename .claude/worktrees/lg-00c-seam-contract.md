# LG-00c Seam Contract — Sortable Players tab

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in
parallel. Names below are frozen — do not rename, do not add fields, do not
re-litigate the policy decisions in §0. If reality contradicts a name here,
STOP and flag; do not silently drift.

Branch: `lg-00c-sortable-players-tab` (cut from `main` after LG-00b lands).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

---

## 0. Resolved decisions (DO NOT re-open)

These are baked into the contract. Code / Tests / Docs agents must NOT
re-litigate them:

- **Read-only view.** Server-side sort only. No filtering, no role/team
  picker, no search box, no client-side sort, no JS. `GET` only — POST is not
  a route.
- **Server-side sort via `?sort=&dir=asc|desc`** with HX-02-style
  forgiving-fallback (invalid / missing → defaults; no 400s).
- **23 accepted sort keys total** — 22 ORM keys in the frozen `_SORT_KEYS`
  whitelist plus the **`preferred_roles` Python-side sentinel** (handled in
  a separate branch of the view, NOT in `_SORT_KEYS`).
- **Default sort:** `?sort=team&dir=asc` (when params missing/invalid).
- **Secondary tiebreak:** always `name asc`, appended to every `.order_by()`
  ORM call AND used in the Python-sort tuple. Deterministic ordering.
- **`Offensive_synergy` casing quirk.** The URL key is the lowercase
  `offensive_synergy`; the ORM target keeps the model's capital-O
  `Offensive_synergy`. Pinned by
  `test_sort_by_offensive_synergy_url_alias_maps_to_capital_O_field`.
- **All 19 stat columns plus `name`, `team`, `preferred_roles`,
  `overall_rating`** are sortable (23 keys).
- **Pagination:** `Paginator(qs, 50)`, sort + dir carried in page links;
  clicking a column header drops `page=` (resets to page 1).
- **Free Agents Team players appear in the listing** — no special-case.
  Their team cell links to `/teams/<free_agents.id>/` like any other team.
- **Helpers live INLINE in `teams/views.py`** (mirroring HX-02's
  `_coerce_threshold` / `_coerce_display`). **No new pure-aggregation
  module.** Tests import `_coerce_sort`, `_coerce_dir`, `_SORT_KEYS`,
  `_SORT_KEYS_DISPLAY`, `_VALID_DIRS`, `_PAGE_SIZE` directly from
  `teams.views`.
- **No model change, no migration, no ADR, no new dependency.**
- **CONTEXT.md edit:** the existing `Free Agents Team` glossary entry's
  trailing `(deferred)` qualifier is dropped — that is the ONLY CONTEXT.md
  change LG-00c is allowed to make. No new domain term.
- **`base.html` nav link "Players"** added immediately after the existing
  "Teams" `<a>` (line ~20), DOM id `player-list-nav-link`.

---

## 1. New public names (frozen)

| Kind | Name | Location |
|------|------|----------|
| Module constant | `_SORT_KEYS: dict[str, str]` (22 entries — URL key → ORM target) | `teams/views.py` |
| Module constant | `_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...]` (23 entries — `(url_key, human_label)`) | `teams/views.py` |
| Module constant | `_VALID_DIRS: tuple[str, str]` = `("asc", "desc")` | `teams/views.py` |
| Module constant | `_PAGE_SIZE: int` = `50` | `teams/views.py` |
| Module helper | `_coerce_sort(raw: str \| None, default: str = "team") -> str` | `teams/views.py` |
| Module helper | `_coerce_dir(raw: str \| None, default: str = "asc") -> str` | `teams/views.py` |
| View | `player_list(request)` | `teams/views.py` |
| URL name | `player_list` | `teams/player_urls.py` |
| URL pattern (new, appended) | `path("", views.player_list, name="player_list")` | `teams/player_urls.py` |
| Template (new) | `templates/teams/player_list.html` | `templates/teams/player_list.html` |
| Template nav link | `<a id="player-list-nav-link" class="nav-link" href="{% url 'player_list' %}">Players</a>` | `templates/base.html` |

No model field change, no migration, no ADR, no new dependency. The view
owns all Django-facing concerns (ORM, paginator, query-param parsing);
helpers are stdlib-only.

---

## 2. URL contract

### 2a. Edit `teams/player_urls.py` (append one route)

The existing file currently holds two entries (`benchmarks/` and
`<int:player_id>/stats/`). LG-00c **appends** a third entry — the empty
string route `""`, which corresponds to `/players/` (the outer include in
`laserforce_simulator/urls.py` already mounts this file at `players/`, so
the empty path here resolves to `/players/`).

**Ordering matters.** The `<int:player_id>` capture group cannot match an
empty path segment, so a trailing position is safe; but the contract pins
ordering as `benchmarks/` → `<int:player_id>/stats/` → `""` so a future
regex change cannot silently shadow the new route.

Final shape of `teams/player_urls.py` (after LG-00c):

```python
urlpatterns = [
    path("benchmarks/", views.role_benchmarks, name="role_benchmarks"),
    path("<int:player_id>/stats/", views.player_career_stats, name="player_career_stats"),
    path("", views.player_list, name="player_list"),
]
```

### 2b. Full URL

`GET /players/` (URL name `player_list`, reverse via the bare
`reverse("player_list")` — no `app_name:` prefix, consistent with the
existing HX-01 / HX-02 routes in this file).

### 2c. Query parameters

| Param | Type | Default | Notes |
|-------|------|---------|-------|
| `sort` | `str` | `"team"` | One of 23 accepted keys (see §3); invalid / missing → `"team"`. |
| `dir`  | `str` | `"asc"`  | `"asc"` or `"desc"`; case-sensitive — `"ASC"` falls back to `"asc"`. |
| `page` | `int` | `1`     | Standard Django `Paginator` page; `EmptyPage` / `PageNotAnInteger` → page 1. |

No other query params are read. Any additional params in the URL are
preserved verbatim by neither `querystring_without_page` nor
`querystring_without_sort_dir_page` (the helpers strip ONLY the named
params they remove). Tests do NOT exercise extra params — LG-00c is the
single owner of these three.

---

## 3. Sort contract — 23 keys

### 3a. `_SORT_KEYS` (22 ORM-backed entries — frozen literal)

Defined at module scope in `teams/views.py`. The dict literal is the
canonical source of accepted ORM-backed sort keys:

```python
_SORT_KEYS: dict[str, str] = {
    "name":                "name",
    "team":                "team__name",
    "overall_rating":      "overall_rating_db",  # the annotated F-expression below
    "player_awareness":    "player_awareness",
    "game_awareness":      "game_awareness",
    "resource_awareness":  "resource_awareness",
    "decision_making":     "decision_making",
    "positioning":         "positioning",
    "stamina":             "stamina",
    "speed":               "speed",
    "flexibility":         "flexibility",
    "adaptability":        "adaptability",
    "communication":       "communication",
    "teamwork":            "teamwork",
    "offensive_synergy":   "Offensive_synergy",  # lowercase URL alias; ORM target keeps the capital-O quirk
    "defensive_synergy":   "defensive_synergy",
    "midfield_synergy":    "midfield_synergy",
    "resupply_synergy":    "resupply_synergy",
    "resupply_efficiency": "resupply_efficiency",
    "accuracy":            "accuracy",
    "survival":            "survival",
    "special_usage":       "special_usage",
}
```

22 entries. Insertion order is the **declared order** — the template iterates
`_SORT_KEYS_DISPLAY` (§5) for headers, NOT `_SORT_KEYS`, so the dict's
ordering is for clarity only; tests treat membership, not ordering.

### 3b. The 23rd key — `preferred_roles` sentinel

`"preferred_roles"` is **NOT** in `_SORT_KEYS` (it has no ORM target — the
field is a JSON list). The view handles it in a separate branch:

```python
if sort == "preferred_roles":
    # Materialise + Python-side sort
    rows = list(qs)
    rows.sort(
        key=lambda p: (",".join(p.preferred_roles or []), p.name),
        reverse=(direction == "desc"),
    )
    paginator = Paginator(rows, _PAGE_SIZE)
else:
    # ORM branch
    prefix = "" if direction == "asc" else "-"
    qs = qs.order_by(prefix + _SORT_KEYS[sort], "name")
    paginator = Paginator(qs, _PAGE_SIZE)
```

**Pinned semantics of the `preferred_roles` Python sort:**
- Key: `(",".join(player.preferred_roles or []), player.name)`.
- Direction: ascending = lexicographic by joined comma string; descending
  is the same comparator with `reverse=True`.
- **Empty `preferred_roles` sorts to the TOP of asc** (the joined string is
  `""` which sorts before any non-empty string).
- Secondary tiebreak (within the same joined string): `player.name` asc
  always — the `reverse=True` on the desc branch flips both components,
  but the test
  `test_sort_by_preferred_roles_python_branch` only pins **asc** ordering.
- Pinned by `test_sort_by_preferred_roles_python_branch`.

### 3c. The accepted-key set

The total set of accepted sort keys is exactly:

```python
ACCEPTED_SORT_KEYS = set(_SORT_KEYS.keys()) | {"preferred_roles"}  # 23 entries
```

This is the set against which `_coerce_sort` whitelists. It is NOT exposed
as a module-level constant — `_coerce_sort` inlines the check via
`raw in _SORT_KEYS or raw == "preferred_roles"`.

---

## 4. Forgiving-fallback helpers (inline, mirror HX-02)

Defined at module scope in `teams/views.py`, placed beside the existing
`_coerce_threshold` (line ~320) and `_coerce_display` (line ~335). The two
new helpers mirror that pattern.

```python
def _coerce_sort(raw: str | None, default: str = "team") -> str:
    """LG-00c — invalid / missing → default.

    Accepted: every key in ``_SORT_KEYS`` plus the literal ``"preferred_roles"``
    sentinel (handled in a separate branch of the view).
    """
    if raw in _SORT_KEYS or raw == "preferred_roles":
        return raw
    return default


def _coerce_dir(raw: str | None, default: str = "asc") -> str:
    """LG-00c — only ``"asc"`` / ``"desc"`` accepted; everything else → default.

    Case-sensitive: ``"ASC"`` falls back to the default (mirrors HX-02's
    ``_coerce_display`` casing discipline).
    """
    if raw in _VALID_DIRS:
        return raw
    return default
```

Both helpers return `str` (never `None`); tests pin direct-call behaviour
(§7.1).

---

## 5. `_SORT_KEYS_DISPLAY` (23-entry column spec)

Module-level constant in `teams/views.py`. Single source of truth for
**both** the `<th>` headers and the per-row `<td>` cells in the template.

```python
_SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("name",                "Name"),
    ("team",                "Team"),
    ("preferred_roles",     "Preferred Roles"),
    ("overall_rating",      "Overall"),
    ("player_awareness",    "Player Aware"),
    ("game_awareness",      "Game Aware"),
    ("resource_awareness",  "Resource Aware"),
    ("decision_making",     "Decision"),
    ("positioning",         "Positioning"),
    ("stamina",             "Stamina"),
    ("speed",               "Speed"),
    ("flexibility",         "Flexibility"),
    ("adaptability",        "Adaptability"),
    ("communication",       "Communication"),
    ("teamwork",            "Teamwork"),
    ("offensive_synergy",   "Offensive Syn"),
    ("defensive_synergy",   "Defensive Syn"),
    ("midfield_synergy",    "Midfield Syn"),
    ("resupply_synergy",    "Resupply Syn"),
    ("resupply_efficiency", "Resupply Eff"),
    ("accuracy",            "Accuracy"),
    ("survival",            "Survival"),
    ("special_usage",       "Special Usage"),
)
```

23 entries — the 22 ORM-backed keys (every key in `_SORT_KEYS`) plus the
`preferred_roles` sentinel, in the locked display order above. Tests pin
`len(_SORT_KEYS_DISPLAY) == 23` and `{k for k, _ in _SORT_KEYS_DISPLAY} ==
set(_SORT_KEYS) | {"preferred_roles"}`.

---

## 6. View contract (`player_list` in `teams/views.py`)

```python
def player_list(request):
    """LG-00c — sortable, paginated index of every Player.

    Server-side sort via ``?sort=&dir=asc|desc``. Forgiving-fallback
    validation (invalid / missing → defaults). 50 per page. Includes
    players on the Free Agents Team.
    """
    sort = _coerce_sort(request.GET.get("sort"))
    direction = _coerce_dir(request.GET.get("dir"))

    qs = (
        Player.objects
        .select_related("team")
        .annotate(
            overall_rating_db=(
                F("player_awareness") + F("game_awareness") + F("resource_awareness")
                + F("decision_making") + F("positioning") + F("stamina")
                + F("speed") + F("flexibility") + F("adaptability")
                + F("communication") + F("teamwork") + F("Offensive_synergy")
                + F("defensive_synergy") + F("midfield_synergy")
                + F("resupply_synergy") + F("resupply_efficiency")
                + F("accuracy") + F("survival") + F("special_usage")
            ) / 19.0
        )
    )

    if sort == "preferred_roles":
        rows = list(qs)
        rows.sort(
            key=lambda p: (",".join(p.preferred_roles or []), p.name),
            reverse=(direction == "desc"),
        )
        paginator = Paginator(rows, _PAGE_SIZE)
    else:
        prefix = "" if direction == "asc" else "-"
        qs = qs.order_by(prefix + _SORT_KEYS[sort], "name")
        paginator = Paginator(qs, _PAGE_SIZE)

    page_raw = request.GET.get("page", 1)
    try:
        page_obj = paginator.page(page_raw)
    except (EmptyPage, PageNotAnInteger):
        page_obj = paginator.page(1)

    # Build querystring helpers for the template.
    qs_no_page = request.GET.copy()
    qs_no_page.pop("page", None)
    querystring_without_page = qs_no_page.urlencode()

    qs_no_sort_dir_page = request.GET.copy()
    qs_no_sort_dir_page.pop("page", None)
    qs_no_sort_dir_page.pop("sort", None)
    qs_no_sort_dir_page.pop("dir", None)
    querystring_without_sort_dir_page = qs_no_sort_dir_page.urlencode()

    context = {
        "page_obj": page_obj,
        "paginator": paginator,
        "sort": sort,
        "dir": direction,
        "sort_keys": _SORT_KEYS_DISPLAY,
        "querystring_without_page": querystring_without_page,
        "querystring_without_sort_dir_page": querystring_without_sort_dir_page,
    }
    return render(request, "teams/player_list.html", context)
```

**Pinned guarantees:**
- `get_object_or_404` is NOT used (no path-captured object). The view never
  404s.
- The view never raises on bad query params — every invalid input is
  coerced to a default.
- **Exactly one** ORM query is materialised in the ORM branch (the
  `Paginator` `.count()` + `.page()` round trips count as part of the
  paginator's contract, not extra application queries). In the
  `preferred_roles` Python branch, the queryset is fully materialised once
  via `list(qs)` then sorted in memory.
- The `overall_rating_db` annotation uses `F("Offensive_synergy")` —
  capital O — to match the underlying field name. The view's annotation is
  a `float` (the `/ 19.0` ensures float semantics under the SQL backend).
- The `.order_by()` always appends `"name"` as a secondary tiebreak, even
  when sorting by name itself — harmless, and keeps the ORM branch
  uniform.
- Context keys are **frozen**: `page_obj`, `paginator`, `sort`, `dir`,
  `sort_keys`, `querystring_without_page`,
  `querystring_without_sort_dir_page`. **Seven** keys total.
- Imports added: `from django.db.models import F`,
  `from django.core.paginator import Paginator, EmptyPage,
  PageNotAnInteger` (any subset already present is reused).

---

## 7. Template contract (`templates/teams/player_list.html`)

NEW template. Extends `base.html`. The contract pins **DOM IDs, header
shape, row shape, pagination block, link targets, and arrow glyphs**.

### 7a. Wireframe

```django
{% extends "base.html" %}

{% block title %}Players{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Players</h1>
    <p class="text-muted">{{ paginator.count }} player{{ paginator.count|pluralize }} total</p>

    <div class="table-responsive">
        <table class="table table-striped" id="player-list-table">
            <thead>
                <tr>
                    {% for key, label in sort_keys %}
                        {% if key == sort %}
                            {% if dir == "asc" %}
                                <th id="player-list-th-{{ key }}">
                                    <a href="?{{ querystring_without_sort_dir_page }}{% if querystring_without_sort_dir_page %}&{% endif %}sort={{ key }}&dir=desc">{{ label }} ↑</a>
                                </th>
                            {% else %}
                                <th id="player-list-th-{{ key }}">
                                    <a href="?{{ querystring_without_sort_dir_page }}{% if querystring_without_sort_dir_page %}&{% endif %}sort={{ key }}&dir=asc">{{ label }} ↓</a>
                                </th>
                            {% endif %}
                        {% else %}
                            <th id="player-list-th-{{ key }}">
                                <a href="?{{ querystring_without_sort_dir_page }}{% if querystring_without_sort_dir_page %}&{% endif %}sort={{ key }}&dir=asc">{{ label }}</a>
                            </th>
                        {% endif %}
                    {% endfor %}
                </tr>
            </thead>
            <tbody>
                {% for player in page_obj %}
                <tr>
                    <td><a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>
                    <td><a href="{% url 'team_detail' player.team.id %}">{{ player.team.name }}</a></td>
                    <td>{{ player.preferred_roles|join:", " }}</td>
                    <td>{{ player.overall_rating|floatformat:1 }}</td>
                    <td>{{ player.player_awareness }}</td>
                    <td>{{ player.game_awareness }}</td>
                    <td>{{ player.resource_awareness }}</td>
                    <td>{{ player.decision_making }}</td>
                    <td>{{ player.positioning }}</td>
                    <td>{{ player.stamina }}</td>
                    <td>{{ player.speed }}</td>
                    <td>{{ player.flexibility }}</td>
                    <td>{{ player.adaptability }}</td>
                    <td>{{ player.communication }}</td>
                    <td>{{ player.teamwork }}</td>
                    <td>{{ player.Offensive_synergy }}</td>
                    <td>{{ player.defensive_synergy }}</td>
                    <td>{{ player.midfield_synergy }}</td>
                    <td>{{ player.resupply_synergy }}</td>
                    <td>{{ player.resupply_efficiency }}</td>
                    <td>{{ player.accuracy }}</td>
                    <td>{{ player.survival }}</td>
                    <td>{{ player.special_usage }}</td>
                </tr>
                {% endfor %}
            </tbody>
        </table>
    </div>

    {% if page_obj.has_other_pages %}
        <nav id="player-list-pagination">
            <ul class="pagination">
                {% if page_obj.has_previous %}
                    <li class="page-item">
                        <a class="page-link" href="?{{ querystring_without_page }}{% if querystring_without_page %}&{% endif %}page={{ page_obj.previous_page_number }}">Previous</a>
                    </li>
                {% endif %}
                <li class="page-item active">
                    <span class="page-link">Page {{ page_obj.number }} of {{ paginator.num_pages }}</span>
                </li>
                {% if page_obj.has_next %}
                    <li class="page-item">
                        <a class="page-link" href="?{{ querystring_without_page }}{% if querystring_without_page %}&{% endif %}page={{ page_obj.next_page_number }}">Next</a>
                    </li>
                {% endif %}
            </ul>
        </nav>
    {% endif %}
</div>
{% endblock %}
```

### 7b. Per-cell rendering rules (frozen)

| `<th>` key | `<td>` content | Notes |
|---|---|---|
| `name` | `<a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a>` | Always links to HX-01 career page |
| `team` | `<a href="{% url 'team_detail' player.team.id %}">{{ player.team.name }}</a>` | Free Agents Team links to its own detail page; no special-case |
| `preferred_roles` | `{{ player.preferred_roles\|join:", " }}` | Empty list renders as empty cell |
| `overall_rating` | `{{ player.overall_rating\|floatformat:1 }}` | Uses the **`@property`** (not the annotation) for consistency |
| `player_awareness` … `special_usage` (19 stats) | `{{ player.<field> }}` | Plain integer; `Offensive_synergy` uses the capital-O attribute |

### 7c. Active-column arrow + flip-direction (frozen)

Each `<th>` renders an `<a>` whose href and arrow glyph follow this rule:

| Condition | Arrow | Next `dir` |
|---|---|---|
| `key == current_sort and dir == "asc"` | `↑` | `"desc"` (flip) |
| `key == current_sort and dir == "desc"` | `↓` | `"asc"` (flip) |
| `key != current_sort` | (none) | `"asc"` |

Locked Unicode glyphs: U+2191 (`↑`) and U+2193 (`↓`). The arrows are
appended to the human label with a single space (`{{ label }} ↑` /
`{{ label }} ↓`). Pinned by `test_active_column_renders_arrow_glyph`.

### 7d. Pagination behaviour (frozen)

- Page size: `_PAGE_SIZE = 50` (module constant on `teams/views.py`).
- Page-link hrefs use `querystring_without_page` so `sort` + `dir` are
  preserved across page navigation; pinned by
  `test_pagination_carries_sort_and_dir_in_links`.
- Column-header hrefs use `querystring_without_sort_dir_page` so clicking
  a header **drops** `page=` (resets to page 1); pinned by
  `test_sort_change_resets_to_page_1`.
- The pagination block renders only when `page_obj.has_other_pages` is
  true. The test that exercises pagination creates 51 players so page 1
  has 50 and page 2 has 1.

### 7e. Locked DOM IDs

| Element | Locked id |
|---|---|
| Outer `<table>` | `player-list-table` |
| Each `<th>` | `player-list-th-{url_key}` (e.g. `player-list-th-offensive_synergy`, `player-list-th-preferred_roles`) |
| Pagination wrapper `<nav>` | `player-list-pagination` |
| Nav link in `base.html` | `player-list-nav-link` |

The `<table>` is wrapped in `<div class="table-responsive">` (Bootstrap
5.3 — already on `base.html`).

---

## 8. `base.html` nav link edit

Add a single anchor in the navbar (`<div class="navbar-nav ms-auto">`)
immediately AFTER the existing `Teams` `<a>` (currently at line 20) and
BEFORE the existing `Matches` `<a>` (currently at line 21):

```django
<a class="nav-link" id="player-list-nav-link" href="{% url 'player_list' %}">Players</a>
```

The Code agent edits `templates/base.html` and ONLY this one line is
added. No CSS class change, no reordering of other nav links.

Pinned by `test_nav_link_present_in_base_html` — the test GETs any view
that extends `base.html` (the contract picks `team_list`), then asserts
both substrings `"Players"` and `id="player-list-nav-link"` appear in the
response body.

---

## 9. Test boundary (frozen — Tests agent reads this section)

All LG-00c tests live in a **single new file**:
`teams/tests/test_player_list_view.py`.

This file contains both the pure-unit cases (no DB) for the two helpers
and the DB/view cases (Django `TestCase`). Two classes.

### 9.1. `TestCoerceSortAndDir` (pure-unit; helpers only)

Imports `_coerce_sort`, `_coerce_dir`, `_SORT_KEYS`, `_VALID_DIRS`
directly from `teams.views`.

1. **`test_coerce_sort_accepts_every_orm_key`** — loops every key in
   `_SORT_KEYS` and asserts `_coerce_sort(key) == key` (identity-return).
2. **`test_coerce_sort_accepts_preferred_roles_sentinel`** —
   `_coerce_sort("preferred_roles") == "preferred_roles"`.
3. **`test_coerce_sort_falls_back_on_unknown_value`** — `"foo"` → `"team"`.
4. **`test_coerce_sort_falls_back_on_none`** — `None` → `"team"`.
5. **`test_coerce_sort_falls_back_on_empty_string`** — `""` → `"team"`.
6. **`test_coerce_dir_accepts_asc`** — `_coerce_dir("asc") == "asc"`.
7. **`test_coerce_dir_accepts_desc`** — `_coerce_dir("desc") == "desc"`.
8. **`test_coerce_dir_falls_back_on_unknown`** — `"sideways"` → `"asc"`.
9. **`test_coerce_dir_falls_back_on_none`** — `None` → `"asc"`.
10. **`test_coerce_dir_falls_back_on_uppercase`** — `"ASC"` → `"asc"`
    (case-sensitive).

### 9.2. `TestPlayerListView` (Django `TestCase`)

Uses `reverse("player_list")` + the test client. Creates Teams and Players
via the real ORM (no mocks). All sort assertions are on the rendered HTML
row ordering (parse `response.content` with a substring or
`response.context["page_obj"]` row order — the test author picks the
shape).

1. **`test_get_returns_200_with_default_sort`** — bare `GET /players/` →
   200; context `sort == "team"` and `dir == "asc"`.
2. **`test_default_sort_is_team_asc_with_name_secondary`** — creates two
   Teams (`"Alpha"`, `"Bravo"`) with two Players each (`"Zed"`, `"Aaron"`
   on each); asserts the rendered row order is `Alpha/Aaron`,
   `Alpha/Zed`, `Bravo/Aaron`, `Bravo/Zed` (team asc, name asc).
3. **`test_sort_by_name_asc`** — GET `?sort=name&dir=asc`; asserts first
   row's name is lexicographically lowest.
4. **`test_sort_by_overall_rating_desc`** — creates 3 Players with
   distinct stat sums (manually pinned); GET
   `?sort=overall_rating&dir=desc`; asserts top row is the highest-sum
   player.
5. **`test_sort_by_offensive_synergy_url_alias_maps_to_capital_O_field`**
   — creates 3 Players with distinct `Offensive_synergy` values; GET
   `?sort=offensive_synergy&dir=desc` (lowercase URL key); asserts the
   top row is the highest `Offensive_synergy` player. Pins the casing
   quirk (URL key lowercase, ORM target capital-O).
6. **`test_sort_by_preferred_roles_python_branch`** — creates 3 Players
   with `preferred_roles` = `[]`, `["scout"]`, `["commander", "heavy"]`;
   GET `?sort=preferred_roles&dir=asc`; asserts the empty list sorts
   FIRST (its joined string is `""`), then `"commander,heavy"`, then
   `"scout"` (lexicographic).
7. **`test_sort_by_every_stat_key_returns_200`** — parametrised loop over
   every `(url_key, _)` in `_SORT_KEYS_DISPLAY`; GET
   `?sort=<key>&dir=asc` and `?sort=<key>&dir=desc` for each; asserts
   each returns 200 and `len(response.context["page_obj"]) > 0` (the
   test seeds at least one Player).
8. **`test_invalid_sort_falls_back_to_team`** — GET `?sort=bogus` →
   context `sort == "team"`; the rendered row order matches the default
   team-asc / name-asc ordering.
9. **`test_invalid_dir_falls_back_to_asc`** — GET `?sort=name&dir=BOGUS`
   → context `dir == "asc"`; rendered row order matches name-asc.
10. **`test_pagination_renders_50_per_page`** — creates 51 Players; GET
    `/players/?page=1` shows 50 rows; GET `/players/?page=2` shows 1
    row.
11. **`test_pagination_carries_sort_and_dir_in_links`** — GET
    `/players/?sort=name&dir=desc&page=1` (with > 50 Players); asserts
    the "Next" page link's href contains the substring `sort=name` and
    `dir=desc`.
12. **`test_sort_change_resets_to_page_1`** — GET
    `/players/?sort=name&dir=asc&page=2` (with > 50 Players); asserts
    every `<th>` `<a>` href does NOT contain `page=` (uses
    `querystring_without_sort_dir_page`).
13. **`test_free_agents_players_appear_in_listing`** — creates a Free
    Agents Team (via `get_free_agents_team()`) with one Player on it;
    GET `/players/`; asserts the Free Agents player's name appears in
    the rendered body (no special-case exclusion).
14. **`test_name_cell_links_to_career_stats`** — creates one Player; GET
    `/players/`; asserts the rendered body contains the substring
    `href="/players/<player.id>/stats/"` (or
    `reverse("player_career_stats", args=[player.id])` for robustness).
15. **`test_team_cell_links_to_team_detail`** — creates one Player on a
    real Team; GET `/players/`; asserts the rendered body contains
    `href="/teams/<team.id>/"` (or
    `reverse("team_detail", args=[team.id])`).
16. **`test_active_column_renders_arrow_glyph`** — GET
    `/players/?sort=name&dir=asc` → asserts the substring `Name ↑`
    appears in the body. GET `/players/?sort=name&dir=desc` → asserts
    `Name ↓`. Pin via direct `↑` / `↓` substring presence (U+2191 /
    U+2193).
17. **`test_nav_link_present_in_base_html`** — GET the existing
    `team_list` page (the URL that renders `base.html` indirectly).
    Assert response body contains both substrings `"Players"` and
    `id="player-list-nav-link"`.

### 9.3. Files the Tests agent edits

| File | Action |
|------|--------|
| `teams/tests/test_player_list_view.py` | NEW — both helper unit tests and view tests |

No other test files are touched. The Tests agent does NOT edit
`teams/tests/test_team_list_filters_free_agents.py` (precedent file —
read for shape only; do not extend).

---

## 10. File ownership (who edits what)

| File | Code | Tests | Docs |
|------|:----:|:-----:|:----:|
| `teams/views.py` (add `_SORT_KEYS`, `_SORT_KEYS_DISPLAY`, `_VALID_DIRS`, `_PAGE_SIZE`, `_coerce_sort`, `_coerce_dir`, `player_list`) | OWN | — | — |
| `teams/player_urls.py` (append one `path("", ...)`) | OWN | — | — |
| `templates/teams/player_list.html` (new) | OWN | — | — |
| `templates/base.html` (add ONE `<a id="player-list-nav-link">…</a>`) | OWN | — | — |
| `teams/tests/test_player_list_view.py` (new) | — | OWN | — |
| `laserforce_simulator/teams/CLAUDE.md` (`## LG-00c sortable players tab` subsection) | — | — | OWN |
| `CONTEXT.md` (drop the trailing `(deferred)` qualifier on the existing **Free Agents Team** entry) | — | — | OWN |
| `PLAN.md` (mark LG-00c done) | — | — | OWN |

The Code agent does NOT touch tests; the Tests agent does NOT touch
production code; the Docs agent does NOT touch code or tests.

---

## 11. Determinism / scope notes

- **Read-only view.** No RNG, no simulation, no `_flush_to_db` touch, no
  SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline
  obligation.
- **No model field change, no migration, no ADR.**
- **No new pure-aggregation module.** All helpers inline in
  `teams/views.py`. The Tests agent imports them directly from
  `teams.views`.
- **No new dependency** — Django's stdlib `Paginator` and `F` are already
  imported / available.
- **No JS** — sort headers are plain anchor tags with query-string flips.
- The view is **fully deterministic** given the (Player, Team) DB state
  and the query string — same DB + same URL → identical rendered HTML.

---

## 12. Out of scope (do NOT add)

- ❌ No filter UI (no role filter, no team filter, no search box).
- ❌ No client-side / JS sort. No DataTables. No AJAX.
- ❌ No CSV export from this page.
- ❌ No per-player `Edit` / `Delete` buttons in rows (the existing
  `/teams/<team_id>/player/<player_id>/edit/` flow is unchanged).
- ❌ No bulk-actions checkbox column.
- ❌ No alternative page sizes (`?per_page=` is not read).
- ❌ No `is_simulated` toggle / filter.
- ❌ No API / JSON endpoint at `/players/` (the existing read-only DRF
  endpoint at `/api/players/` already covers programmatic access; this
  is purely the HTML index).
- ❌ No model change, no migration, no ADR, no new CONTEXT.md domain term
  (Docs agent ONLY edits the existing **Free Agents Team** entry to
  drop the trailing `(deferred)` qualifier).
- ❌ No edit to `teams/api_views.py`, `teams/serializers.py`,
  `teams/forms.py`, `teams/models.py`, `teams/career_stats.py`,
  `teams/role_benchmarks.py`, `teams/role_benchmarks_cache.py`,
  `teams/signals.py`, `teams/player_generator.py`,
  `teams/roster_importer.py`, `teams/constants.py`,
  `teams/templatetags/team_extras.py`,
  `laserforce_simulator/urls.py`, or any file under `matches/` or
  `core/`.
- ❌ No CSV import / LG-00b coupling.
- ❌ No batch-sim / Celery touch.

---

## 13. Quick-reference name table

| Slot | Name |
|---|---|
| URL path | `/players/` |
| URL name | `player_list` |
| URL pattern (appended to `teams/player_urls.py`) | `path("", views.player_list, name="player_list")` |
| View | `teams.views.player_list(request)` |
| Helper — sort | `teams.views._coerce_sort(raw, default="team") -> str` |
| Helper — direction | `teams.views._coerce_dir(raw, default="asc") -> str` |
| Module constant — ORM whitelist (22) | `teams.views._SORT_KEYS: dict[str, str]` |
| Module constant — column display (23) | `teams.views._SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...]` |
| Module constant — valid directions | `teams.views._VALID_DIRS = ("asc", "desc")` |
| Module constant — page size | `teams.views._PAGE_SIZE = 50` |
| Default sort key | `"team"` |
| Default direction | `"asc"` |
| Secondary tiebreak (ORM) | `"name"` (asc) — always appended |
| Secondary tiebreak (Python branch) | `player.name` in key tuple |
| Sentinel sort key (Python branch) | `"preferred_roles"` |
| Capital-O casing quirk | URL key `"offensive_synergy"` → ORM target `"Offensive_synergy"` |
| Annotation | `overall_rating_db = (sum of 19 F-fields) / 19.0` |
| Template (new) | `templates/teams/player_list.html` |
| Nav link target | `templates/base.html` (after the `Teams` `<a>`) |
| Nav link DOM id | `player-list-nav-link` |
| Table DOM id | `player-list-table` |
| Each `<th>` DOM id | `player-list-th-{url_key}` |
| Pagination `<nav>` DOM id | `player-list-pagination` |
| Active-asc arrow | `↑` (U+2191) |
| Active-desc arrow | `↓` (U+2193) |
| Context keys (7) | `page_obj, paginator, sort, dir, sort_keys, querystring_without_page, querystring_without_sort_dir_page` |
| Test file (new) | `teams/tests/test_player_list_view.py` |
| Test classes | `TestCoerceSortAndDir`, `TestPlayerListView` |
| CONTEXT.md change | Drop trailing `(deferred)` on existing **Free Agents Team** entry |
| PLAN.md change | Mark LG-00c done |
