# LG-01k — Top Nav Bar Behaviour Fix · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01k ships the **three-mode topnav restructure** that extends the LG-01h `app_mode` context processor from a 2-value enum (`"league"`, `"sandbox"`) to a 3-value enum (`"start"`, `"league"`, `"sandbox"`), rewrites `templates/base.html`'s `<div class="navbar-nav ms-auto">` block around a 3-way `{% if app_mode == "league" %}` / `{% elif app_mode == "sandbox" %}` / `{% else %}` branch, and replaces the LG-01h flat `League ▾` dropdown with **4 section dropdown toggles** (`League ▾` / `Team ▾` / `Players ▾` / `Stats ▾`) sourced from a single new context key `top_bar_links` (the full 23-entry `_build_league_sidebar_links` output, regrouped by section in the template) plus a new `top_bar_dashboard_url` key powering a leading home-icon link. **No model change, no migration, no simulator touch, no RNG, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline, no new ADR, no CONTEXT.md edit, no JS framework, no Celery, no `messages.*`, no API endpoint, no new dependency, no admin change, no edit to `_partials/league_sidebar.html`, no edit to `_build_league_sidebar_links` (read-only consumed), no edit to any view, no edit to URLs.** This contract mirrors the structure of [`.claude/worktrees/lg-01h-seam-contract.md`](lg-01h-seam-contract.md) (mode-driven base.html branching, context-processor extension, DOM-id discipline) — LG-01k is best understood as an **in-place body extension** of both `core.context_processors.app_mode` (2→3 branches) and `core.context_processors.league_nav` (drop 5 keys, add 2 keys) plus a **rewrite of the topnav block** in `templates/base.html` to consume the new shape.

## Mode-detecting context processor (extension)

`core/context_processors.py` is MODIFIED — the existing LG-01h `app_mode(request: HttpRequest) -> dict[str, str]` function body is **rewritten in place** (signature unchanged, return-key unchanged, return-value type unchanged) to return `"start"` | `"league"` | `"sandbox"` instead of just `"league"` | `"sandbox"`. The locked 3-way path-prefix rule, applied in this exact order so that `/` does NOT fall into sandbox:

1. **`path == "/"` (exact match)** ⇒ `"start"`.
2. **`path.startswith("/leagues/")` or `path.startswith("/seasons/")`** ⇒ `"league"`.
3. **Everything else** (including empty string, missing `.path` attribute, `/teams/`, `/players/`, `/matches/`, `/maps/`, `/help/*`, `/tools/*`, any unknown path) ⇒ `"sandbox"`.

The defensive `getattr(request, "path", "/") or "/"` read from LG-01h stays — a `RequestFactory()`-built request with no `.path` attribute (or an empty string `""`) yields `"/"` via the `or "/"` fallback, which by rule (1) then resolves to `"start"`. **Wait — that contradicts the test plan.** The test plan locks that missing/empty `.path` falls to `"sandbox"`, not `"start"`. To satisfy both rules, the implementation must distinguish "missing attribute" from "explicit `/`": read with `getattr(request, "path", None)`, treat `None` and `""` as the sandbox fallback, and only return `"start"` for an explicit `path == "/"` string. Locked body sketch (Code agent matches exactly):

```python
path = getattr(request, "path", None)
if path == "/":
    return {"app_mode": "start"}
if path and (path.startswith("/leagues/") or path.startswith("/seasons/")):
    return {"app_mode": "league"}
return {"app_mode": "sandbox"}
```

The three locked literals `"start"` / `"league"` / `"sandbox"` and the context key `app_mode` are unchanged in name; the `"start"` literal is NEW. Settings registration order in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]` is unchanged from LG-01h — `core.context_processors.league_nav` first, then `core.context_processors.app_mode`.

## League-nav context processor (extension)

`core/context_processors.py` `league_nav(request: HttpRequest) -> dict[str, Any]` is REWRITTEN — the **5 LG-01h URL keys** `top_bar_history_url` / `top_bar_standings_url` / `top_bar_playoffs_url` / `top_bar_finances_url` / `top_bar_power_rankings_url` are **DELETED** from the return dict (zero callers remain after the `base.html` rewrite). The processor now returns exactly **2 keys**:

* `top_bar_links: list[dict]` — the **23-entry output of `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)`** for the resolved League + displayed Season, or `[]` when no League can be resolved. The `sidebar_active=None` argument is locked — no entry should render with the `active` styling in the topnav (the active-styling concept belongs to the sidebar partial, not the topnav).
* `top_bar_dashboard_url: str` — `reverse("league_dashboard", kwargs={"league_id": league.id})` for the resolved League, or `reverse("league_list")` when no League can be resolved.

**3-step League resolution chain** (locked, identical to LG-01f / LG-01h):

1. `request.session.get("last_league_id")` is present AND the League still exists ⇒ use that League.
2. Otherwise, exactly **one** League exists in the DB (`list(League.objects.values_list("id", flat=True)[:2])` is length 1) ⇒ use that League.
3. Otherwise (zero or 2+ Leagues, no session pin) ⇒ **fallback**: return `{"top_bar_links": [], "top_bar_dashboard_url": reverse("league_list")}`.

**Displayed-Season resolution** (locked, identical to LG-01h's standings chain): when a League is resolved, `displayed_season = league.active_season`; when that is `None`, `displayed_season = league.seasons.filter(state="completed").order_by("-id").first()`; when both are `None`, `displayed_season` stays `None`. The `displayed_season` value is then passed to `_build_league_sidebar_links(league, displayed_season, sidebar_active=None)` — the helper itself already handles `displayed_season is None` by emitting `url=None, disabled=True` for the Standings / Schedule entries (LG-01f rule, preserved verbatim).

**Defensive DB-error handling** (locked, identical to LG-01h): every ORM call inside the processor is wrapped in `try: ... except DatabaseError:` and logs at DEBUG; a broken-transaction render falls through to the fallback path (returns `{"top_bar_links": [], "top_bar_dashboard_url": reverse("league_list")}`). The lazy local-import `from matches.models import League` inside the function body stays (avoids the `core ↔ matches` apps-loading cycle).

**The 5 deleted keys are not replaced by 5 individual context keys** — `top_bar_links` carries the same URLs structurally (Standings / Playoffs / Finances / History / Power Rankings appear as entries at indexes 1–6 of the 23-entry list with section `"league"`), and the template iterates the regrouped list rather than referencing per-URL keys. **Every test / template / docstring reference to the 5 deleted keys must be updated** (see "Test plan" and "Retired DOM ids" sections).

**Cross-app import note** (locked): the processor imports `_build_league_sidebar_links` lazily inside the function body alongside the existing `from matches.models import League` lazy import. This is `from matches.views import _build_league_sidebar_links` — yes, importing a private (leading-underscore) helper from another app's views module is an unusual coupling, but the LG-01h seam contract already locked `_build_league_sidebar_links` as the single source of truth for both sidebar and topbar consumers; LG-01k formalises that consumption. The import lives inside the function body, not at module scope, to preserve LG-01f's apps-loading-cycle guard.

## Base.html mode branching (rewrite)

`templates/base.html` is MODIFIED — the existing LG-01h `<div class="navbar-nav ms-auto">` block is **fully rewritten** around a 3-way `{% if app_mode == "league" %}` / `{% elif app_mode == "sandbox" %}` / `{% else %}` branch. The brand link `<a class="navbar-brand" href="{% url 'landing' %}">⚡ Laserforce Manager</a>` is preserved verbatim across all 3 modes. The LG-01a-locked outer wrapper `<div class="container">` + the `<button class="navbar-toggler">` + the `<div class="collapse navbar-collapse" id="mainNav">` markup is preserved verbatim around the 3-branch block.

**The `{% else %}` branch is the start mode** (path == `/`). This is the simplest branch (Tools ▾ + Help ▾ only) and the natural default — placing it as the `{% else %}` minimises the visual complexity of the most-frequently-loaded path (the landing page).

### League mode block (`{% if app_mode == "league" %}`)

Pinned left-to-right order: `[Dashboard home icon] | League ▾ | Team ▾ | Players ▾ | Stats ▾ | Tools ▾ | Help ▾`. The 7 elements:

1. **Dashboard home-icon link**. `<a class="nav-link" id="dashboard-nav-link" href="{{ top_bar_dashboard_url }}" aria-label="League dashboard"><i class="bi bi-house"></i></a>`. The leading icon is a **Bootstrap Icons home glyph** rendered via `<i class="bi bi-house"></i>` (Bootstrap Icons are NOT currently bundled in `base.html` — to avoid adding a CDN `<link>` for one icon, the Code agent uses the **⌂ U+2302 ASCII character** as the text content instead: `<a class="nav-link" id="dashboard-nav-link" href="{{ top_bar_dashboard_url }}" aria-label="League dashboard">⌂</a>`). **Locked: the home-icon text content is the literal character `⌂` (U+2302, HOUSE).** No emoji (the 🏠 emoji renders inconsistently across Windows / cp1252 terminals and per project shell conventions ASCII is preferred). No `<i>` element. No SVG. Just the `⌂` character inside the anchor.
2. **`League ▾` dropdown toggle**. `<a class="nav-link dropdown-toggle" id="league-nav-link" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">League ▾</a>` followed by a `<ul class="dropdown-menu" aria-labelledby="league-nav-link">` containing the LEAGUE section of `top_bar_links` (6 entries: Standings / Schedule / Playoffs / Finances / History / Power Rankings).
3. **`Team ▾` dropdown toggle**. `<a class="nav-link dropdown-toggle" id="team-nav-link" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">Team ▾</a>` followed by a `<ul class="dropdown-menu" aria-labelledby="team-nav-link">` containing the TEAM section of `top_bar_links` (4 entries: Roster / Schedule / Finances / History).
4. **`Players ▾` dropdown toggle**. `<a class="nav-link dropdown-toggle" id="players-nav-link" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">Players ▾</a>` followed by a `<ul class="dropdown-menu" aria-labelledby="players-nav-link">` containing the PLAYERS section of `top_bar_links` (6 entries: Free Agents / Trade / Trading Block / Prospects / Watch List / Hall of Fame).
5. **`Stats ▾` dropdown toggle**. `<a class="nav-link dropdown-toggle" id="stats-nav-link" href="#" role="button" data-bs-toggle="dropdown" aria-expanded="false">Stats ▾</a>` followed by a `<ul class="dropdown-menu" aria-labelledby="stats-nav-link">` containing the STATS section of `top_bar_links` (6 entries: Game Log / League Leaders / Player Ratings / Player Stats / Team Stats / Statistical Feats).
6. **`Tools ▾` dropdown toggle**. Preserved verbatim from LG-01h (4 items, ids `tools-nav-link` + `tools-{achievements,screenshot,debug-mode,reset-db}-topbar-link`).
7. **`Help ▾` dropdown toggle**. Preserved verbatim from LG-01h (6 items, ids `help-nav-link` + `help-{overview,changes,custom-rosters,debugging,lol-gm-forums,zen-gm-forums}-topbar-link`).

**Order delta from LG-01h**: Tools is now BEFORE Help (LG-01h had Help-then-Tools; LG-01k swaps to Tools-then-Help). This applies in all 3 modes.

**Section-dropdown iteration pattern** (locked, applies to all 4 section dropdowns — League / Team / Players / Stats). The template uses a `{% regroup top_bar_links by section as sections %}` at the start of the league branch, then per-section renders the entries by filtering on `section.grouper`. Inside each section's `<ul>` the per-entry rendering branches on `entry.disabled`:

```django
{% for entry in section.list %}
    {% if entry.disabled %}
        <li><span class="dropdown-item disabled">{{ entry.label }}</span></li>
    {% else %}
        <li><a class="dropdown-item" id="topbar-{{ entry.section }}-{{ entry.key }}" href="{{ entry.url }}">{{ entry.label }}</a></li>
    {% endif %}
{% endfor %}
```

The `topbar-{section}-{key}` DOM id pattern is locked (mirrors the LG-01f `sidebar-{section}-{key}` pattern). Concrete ids that result for the 22 league-mode dropdown entries (excluding the top Dashboard entry which is rendered as the leading icon link, NOT in any dropdown): `topbar-league-standings`, `topbar-league-schedule`, `topbar-league-playoffs`, `topbar-league-finances`, `topbar-league-history`, `topbar-league-power_rankings`; `topbar-team-roster`, `topbar-team-schedule_team`, `topbar-team-finances_team`, `topbar-team-history_team`; `topbar-players-free_agents`, `topbar-players-trade`, `topbar-players-trading_block`, `topbar-players-prospects`, `topbar-players-watch_list`, `topbar-players-hall_of_fame`; `topbar-stats-game_log`, `topbar-stats-league_leaders`, `topbar-stats-player_ratings`, `topbar-stats-player_stats`, `topbar-stats-team_stats`, `topbar-stats-statistical_feats`. **The top Dashboard entry (`section="top", key="dashboard"`) of `top_bar_links` is filtered OUT of the regrouped iteration** — it's surfaced only via the leading `dashboard-nav-link` icon, not in any dropdown. The template achieves this by skipping the `"top"` section inside the `{% for section in sections %}` loop (e.g. `{% if section.grouper != "top" %}...{% endif %}`).

**Disabled-entry semantics** (locked): when `displayed_season is None` the helper emits Standings / Schedule entries with `url=None, disabled=True`; the template renders these as `<span class="dropdown-item disabled">` per the branch above. The disabled `<span>` does NOT receive an `id` (the `topbar-{section}-{key}` id is only emitted on LIVE `<a>` elements — disabled entries have no DOM id and tests must not assert on them).

### Sandbox mode block (`{% elif app_mode == "sandbox" %}`)

Pinned left-to-right order: `Teams | Players | Matches | Batch Sim | Create Team | Maps | Tools ▾ | Help ▾`. The 8 elements (6 flat links + 2 dropdown toggles):

1. `<a class="nav-link" href="{% url 'team_list' %}">Teams</a>` (LG-01a, preserved verbatim — no DOM id).
2. `<a class="nav-link" id="player-list-nav-link" href="{% url 'player_list' %}">Players</a>` (LG-01a, preserved verbatim — id `player-list-nav-link` stays).
3. `<a class="nav-link" href="{% url 'match_list' %}">Matches</a>` (LG-01a, preserved verbatim).
4. `<a class="nav-link" href="{% url 'simulate_batch' %}">Batch Sim</a>` (LG-01a, preserved verbatim).
5. `<a class="nav-link" href="{% url 'team_create' %}">Create Team</a>` (LG-01a, preserved verbatim).
6. `<a class="nav-link" href="{% url 'map_list' %}">Maps</a>` (LG-01a, preserved verbatim).
7. **`Tools ▾` dropdown toggle** (LG-01h, preserved verbatim — see Tools dropdown items below).
8. **`Help ▾` dropdown toggle** (LG-01h, preserved verbatim — see Help dropdown items below).

**Delta from LG-01h**: the LG-01h sandbox branch included a `League ▾` dropdown after the 6 flat links — in LG-01k this `League ▾` dropdown is **REMOVED from sandbox mode** entirely. The intent: a user in sandbox mode is not browsing a League, so the League menu surface is irrelevant. The LG-01a-locked DOM id `player-list-nav-link` on the Players link is preserved. No new DOM ids are introduced on the 6 flat sandbox links (LG-01a left 5 of them id-less; that stays).

### Start mode block (`{% else %}`)

Pinned left-to-right order: `Tools ▾ | Help ▾`. The 2 elements:

1. **`Tools ▾` dropdown toggle** (LG-01h, preserved verbatim).
2. **`Help ▾` dropdown toggle** (LG-01h, preserved verbatim).

That's it. No League ▾, no Dashboard icon, no flat sandbox links. The start page (`/`) presents the minimum-viable topnav — only the universal Tools / Help surfaces. The user lands at `/`, picks a mode card (per LG-01a `mode-card-sandbox` / `mode-card-league` / `mode-card-multiplayer`), and only then does the topnav populate with the mode-specific surfaces.

## Tools and Help dropdowns (universal — all 3 modes)

The `Tools ▾` dropdown markup is identical across all 3 modes (`league` / `sandbox` / `start`). 4 items in pinned top-to-bottom order (LG-01h, preserved verbatim): **Achievements** → `coming_soon_tools_achievements`, **Screenshot** → `coming_soon_tools_screenshot`, **Enable Debug Mode** → `coming_soon_tools_debug_mode`, **Reset DB** → `coming_soon_tools_reset_db`. Toggle text `"Tools ▾"` (U+25BE), toggle DOM id `tools-nav-link`, per-item DOM ids `tools-achievements-topbar-link` / `tools-screenshot-topbar-link` / `tools-debug-mode-topbar-link` / `tools-reset-db-topbar-link`.

The `Help ▾` dropdown markup is identical across all 3 modes. 6 items in pinned top-to-bottom order (LG-01h, preserved verbatim): **Overview** → `coming_soon_help_overview`, **Changes** → `coming_soon_help_changes`, **Custom Rosters** → `coming_soon_help_custom_rosters`, **Debugging** → `coming_soon_help_debugging`, **LOL GM Forums** → `coming_soon_help_lol_gm_forums`, **Zen GM Forums** → `coming_soon_help_zen_gm_forums`. Toggle text `"Help ▾"` (U+25BE), toggle DOM id `help-nav-link`, per-item DOM ids `help-overview-topbar-link` / `help-changes-topbar-link` / `help-custom-rosters-topbar-link` / `help-debugging-topbar-link` / `help-lol-gm-forums-topbar-link` / `help-zen-gm-forums-topbar-link`.

**To avoid template duplication** of the ~14 lines of Tools + Help markup across all 3 branches, the Code agent MAY (locked optional) factor the two dropdowns into a small `{% include "_partials/topnav_tools_help.html" %}` partial included at the end of each branch. The partial is purely structural (no logic, no context-key dependencies beyond the URL names). If the agent factors the include, the partial path is locked as `templates/_partials/topnav_tools_help.html` and the include is added at the bottom of each of the 3 branches. If the agent inlines the markup 3× instead (simpler diff, more lines), that is also acceptable — the test plan asserts on DOM ids, not on the inclusion structure.

## Retired DOM ids and URL keys

The following LG-01h DOM ids are RETIRED in LG-01k (they do not appear anywhere in the rewritten `base.html`):

* **`leagues-nav-link`** — was on the LG-01h League ▾ dropdown toggle (sandbox and league branches). Replaced by `league-nav-link` (note: drops the trailing `s` — LG-01k uses the singular form to match the section-label vocabulary of the regrouped `top_bar_links`).
* **`league-standings-topbar-link`** — replaced by `topbar-league-standings`.
* **`league-playoffs-topbar-link`** — replaced by `topbar-league-playoffs`.
* **`league-finances-topbar-link`** — replaced by `topbar-league-finances`.
* **`league-history-topbar-link`** — replaced by `topbar-league-history`.
* **`league-power-rankings-topbar-link`** — replaced by `topbar-league-power_rankings` (note: underscore not hyphen — matches the helper's `key="power_rankings"`).

The following LG-01h **context keys** are RETIRED (they are not in the `league_nav` return dict any more):

* `top_bar_history_url`
* `top_bar_standings_url`
* `top_bar_playoffs_url`
* `top_bar_finances_url`
* `top_bar_power_rankings_url`

**Every test, template, docstring, and CLAUDE.md reference to these 5 keys + 6 DOM ids must be updated to the new shape.** The retired-id list is exhaustive — no other LG-01h DOM id is touched (Tools / Help ids all stay verbatim; the LG-01a `player-list-nav-link` stays).

## Files touched (locked list)

1. **`laserforce_simulator/core/context_processors.py`** — `app_mode` body rewritten (2→3 branches); `league_nav` body rewritten (drop 5 URL keys, add 2 new keys `top_bar_links` + `top_bar_dashboard_url`, add lazy import of `_build_league_sidebar_links`, replace the `_urls_for_league` helper with a `_resolve_league_and_season` helper that returns `(League | None, Season | None)` to feed both new keys). Existing module-level docstring is updated to mention `"start"` as a third `app_mode` value and the new shape of the `league_nav` return dict.
2. **`laserforce_simulator/templates/base.html`** — the entire `<div class="navbar-nav ms-auto">` block (current lines 19–89 of `base.html`) is replaced with the 3-branch structure above. The `<a class="navbar-brand">` line (current line 14) is unchanged. The `<button class="navbar-toggler">` and the outer `<div class="collapse navbar-collapse" id="mainNav">` are unchanged. The post-navbar `<div class="container mt-4">` block (current lines 94–106) is unchanged. The Bootstrap CSS / JS CDN `<link>` and `<script>` lines are unchanged.
3. **`laserforce_simulator/templates/_partials/topnav_tools_help.html`** — OPTIONAL NEW partial (Code agent's discretion). If created, contains the verbatim Tools ▾ + Help ▾ markup blocks (~14 lines). If not created, the markup is inlined 3× in `base.html`.
4. **`laserforce_simulator/matches/tests/test_lg01h_app_mode_processor.py`** — EXTENDED to add the start-mode test cases. Existing test class `TestAppModeContextProcessor` gains new methods covering `"/"` ⇒ `"start"`, `"/leagues/"` ⇒ `"league"` (unchanged), `"/leagues/1/"` ⇒ `"league"` (unchanged), `"/teams/"` ⇒ `"sandbox"` (unchanged), `""` (empty string path) ⇒ `"sandbox"` (NEW assertion that empty string does NOT match the exact `/` rule), `None` (missing `.path` attribute via raw `RequestFactory()`) ⇒ `"sandbox"` (NEW assertion).
5. **`laserforce_simulator/matches/tests/test_lg01k_base_html_branching.py`** — NEW test file dedicated to LG-01k's 3-mode topbar DOM shape. Test classes `TestLg01kStartModeTopbar` / `TestLg01kSandboxModeTopbar` / `TestLg01kLeagueModeTopbar` per the test plan below. The LG-01h `test_lg01h_base_html_branching.py` is NOT edited — leaving it alone preserves the LG-01h behavioural test history at that filename and avoids merge confusion; the LG-01k file is the new authority for topbar DOM assertions and the LG-01h tests on the retired ids will be updated MINIMALLY (only to remove references to the now-deleted `leagues-nav-link` etc.; see below).
6. **`laserforce_simulator/matches/tests/test_lg01h_base_html_branching.py`** — MINIMAL EDIT. Any assertions referencing the retired ids (`leagues-nav-link`, `league-standings-topbar-link`, etc.) are deleted or updated to the new id pattern. Any assertions on the 5 retired URL context keys are deleted. Assertions on Tools / Help DOM ids stay verbatim.
7. **`laserforce_simulator/matches/tests/test_league_nav_context_processor.py`** — EXTENDED. The LG-01h test cases on the 5 retired URL keys are DELETED. New test cases cover the 2 new keys: `top_bar_links` is a `list` of length 23 when a League exists, `[]` when fallback; `top_bar_dashboard_url` resolves to `reverse("league_dashboard", args=[league.id])` when a League exists, `reverse("league_list")` when fallback; the 5 old keys are **absent** from the return dict; the helper is called with `sidebar_active=None`; the displayed-Season chain works (active → most-recent-completed → None); when `displayed_season is None` the returned `top_bar_links` still has 23 entries but Standings / Schedule entries are `url=None, disabled=True`.
8. **`laserforce_simulator/matches/CLAUDE.md`** — Docs agent adds an LG-01k subsection extending the existing "LG-01h global nav restructure" subsection. The extension note explains the 3-mode `app_mode` enum, the `top_bar_links` single-source-of-truth shape (one helper output consumed by both sidebar and topbar), the 6 retired DOM ids, and the 5 retired URL context keys.
9. **`PLAN.md`** — Docs agent flips the LG-01k bullet from incomplete to `- completed` and appends a dense house-style implementation note recording: 3-mode `app_mode` enum, removal of the LG-01h `League ▾` dropdown from sandbox, the new section-grouped topbar dropdowns sourced from `_build_league_sidebar_links`, the home-icon Dashboard link, the Tools-before-Help order swap, and the start-mode minimum-viable topnav.

## Public function signatures (locked)

```python
# core/context_processors.py

def app_mode(request: HttpRequest) -> dict[str, str]:
    """LG-01k — 3-mode path-prefix detector.

    Returns ``{"app_mode": "start" | "league" | "sandbox"}``.
    """

def league_nav(request: HttpRequest) -> dict[str, Any]:
    """LG-01k — resolve top-bar links + dashboard URL.

    Returns ``{"top_bar_links": list[dict], "top_bar_dashboard_url": str}``.

    When the 3-step League resolution chain succeeds:
      * ``top_bar_links`` is the 23-entry output of
        ``_build_league_sidebar_links(league, displayed_season, sidebar_active=None)``.
      * ``top_bar_dashboard_url`` is ``reverse("league_dashboard", kwargs={"league_id": league.id})``.

    When the chain falls back (zero or 2+ Leagues, no session pin, or
    DatabaseError inside a broken transaction):
      * ``top_bar_links`` is ``[]``.
      * ``top_bar_dashboard_url`` is ``reverse("league_list")``.
    """
```

Signature constraints:

* `app_mode` keeps its `request: HttpRequest -> dict[str, str]` signature — return type is still `dict[str, str]` because all 3 enum values are strings.
* `league_nav` widens its return-type annotation from `dict[str, str]` (LG-01h) to `dict[str, Any]` because `top_bar_links` is a `list[dict]`, not a `str`. The Code agent updates the annotation.
* The `_build_league_sidebar_links(league, displayed_season, sidebar_active=None)` call is the ONLY consumer of `_build_league_sidebar_links` introduced by LG-01k — the existing sidebar callers (`coming_soon`, `league_dashboard`, `season_dashboard`, etc.) keep their `sidebar_active=<literal>` callsites unchanged.
* The `_build_league_sidebar_links` signature and body are NOT modified by LG-01k. This is read-only consumption — the helper still returns 23 entries from LG-01h, and LG-01k just regroups them by section in the template.

## Topnav DOM structure (locked, per mode)

### Start mode (path == "/")

Left-to-right, inside `<div class="navbar-nav ms-auto">`:

1. `tools-nav-link` — Tools ▾ dropdown toggle. Children: `tools-achievements-topbar-link`, `tools-screenshot-topbar-link`, `tools-debug-mode-topbar-link`, `tools-reset-db-topbar-link`.
2. `help-nav-link` — Help ▾ dropdown toggle. Children: `help-overview-topbar-link`, `help-changes-topbar-link`, `help-custom-rosters-topbar-link`, `help-debugging-topbar-link`, `help-lol-gm-forums-topbar-link`, `help-zen-gm-forums-topbar-link`.

Nothing else. **No `dashboard-nav-link`, no `league-nav-link`, no `team-nav-link`, no `players-nav-link`, no `stats-nav-link`, no flat `team_list` / `player_list` / `match_list` / `simulate_batch` / `team_create` / `map_list` anchors, no `player-list-nav-link`.**

### Sandbox mode (any path not `/` and not under `/leagues/` or `/seasons/`)

Left-to-right, inside `<div class="navbar-nav ms-auto">`:

1. `<a class="nav-link" href="{% url 'team_list' %}">Teams</a>` (no DOM id).
2. `<a class="nav-link" id="player-list-nav-link" href="{% url 'player_list' %}">Players</a>` (LG-01a id preserved).
3. `<a class="nav-link" href="{% url 'match_list' %}">Matches</a>` (no DOM id).
4. `<a class="nav-link" href="{% url 'simulate_batch' %}">Batch Sim</a>` (no DOM id).
5. `<a class="nav-link" href="{% url 'team_create' %}">Create Team</a>` (no DOM id).
6. `<a class="nav-link" href="{% url 'map_list' %}">Maps</a>` (no DOM id).
7. `tools-nav-link` — Tools ▾ dropdown toggle (4 child items as above).
8. `help-nav-link` — Help ▾ dropdown toggle (6 child items as above).

**No `dashboard-nav-link`, no `league-nav-link`, no `team-nav-link`, no `players-nav-link`, no `stats-nav-link`, no `leagues-nav-link` (the LG-01h League dropdown is REMOVED from sandbox mode).**

### League mode (path starts with `/leagues/` or `/seasons/`)

Left-to-right, inside `<div class="navbar-nav ms-auto">`:

1. `dashboard-nav-link` — `<a class="nav-link" id="dashboard-nav-link" href="{{ top_bar_dashboard_url }}" aria-label="League dashboard">⌂</a>`. Text content: the literal `⌂` (U+2302 HOUSE) character.
2. `league-nav-link` — League ▾ dropdown toggle. Children iterate the LEAGUE section of `top_bar_links`; LIVE entries get ids `topbar-league-standings`, `topbar-league-schedule`, `topbar-league-playoffs`, `topbar-league-finances`, `topbar-league-history`, `topbar-league-power_rankings`.
3. `team-nav-link` — Team ▾ dropdown toggle. Children iterate the TEAM section; LIVE entries get ids `topbar-team-roster`, `topbar-team-schedule_team`, `topbar-team-finances_team`, `topbar-team-history_team`.
4. `players-nav-link` — Players ▾ dropdown toggle. Children iterate the PLAYERS section; LIVE entries get ids `topbar-players-free_agents`, `topbar-players-trade`, `topbar-players-trading_block`, `topbar-players-prospects`, `topbar-players-watch_list`, `topbar-players-hall_of_fame`.
5. `stats-nav-link` — Stats ▾ dropdown toggle. Children iterate the STATS section; LIVE entries get ids `topbar-stats-game_log`, `topbar-stats-league_leaders`, `topbar-stats-player_ratings`, `topbar-stats-player_stats`, `topbar-stats-team_stats`, `topbar-stats-statistical_feats`.
6. `tools-nav-link` — Tools ▾ dropdown toggle (4 child items).
7. `help-nav-link` — Help ▾ dropdown toggle (6 child items).

**The top Dashboard entry (`section="top", key="dashboard"`) of `top_bar_links` is filtered OUT of the regrouped iteration** — it is surfaced only via the leading `dashboard-nav-link` icon. The template's `{% for section in sections %}` loop skips `section.grouper == "top"`. No `topbar-top-dashboard` DOM id is emitted.

## Toggle text literals (locked)

Per-toggle visible text, exactly as it appears in the rendered HTML (each toggle text contains a trailing U+25BE downwards small triangle character):

* `League ▾`
* `Team ▾`
* `Players ▾`
* `Stats ▾`
* `Tools ▾`
* `Help ▾`

The home-icon text content is `⌂` (U+2302). No trailing space, no triangle.

## Test plan

NEW test file `matches/tests/test_lg01k_base_html_branching.py`. Test classes and what each pins:

* **`TestLg01kStartModeTopbar`** (Django `TestCase`). `setUp` creates a `Client()`. `test_start_mode_renders_only_tools_and_help`: GET `/` (the landing view from LG-01a). Asserts presence of `id="tools-nav-link"` and `id="help-nav-link"` and presence of `id="mode-picker"` (the LG-01a landing wrapper — sanity check that we're on `/`). Asserts ABSENCE of `id="dashboard-nav-link"`, `id="league-nav-link"`, `id="team-nav-link"`, `id="players-nav-link"`, `id="stats-nav-link"`, `id="player-list-nav-link"`, `id="leagues-nav-link"` (retired), and the substring `href="{% url 'team_list' %}"` rendered URL `/teams/` as a `nav-link` href (the flat sandbox anchors must not appear). `test_start_mode_tools_dropdown_items_present`: asserts all 4 Tools child ids present. `test_start_mode_help_dropdown_items_present`: asserts all 6 Help child ids present.

* **`TestLg01kSandboxModeTopbar`** (Django `TestCase`). `test_sandbox_mode_renders_6_flat_links`: GET `/teams/`. Asserts presence of the 6 flat anchors by href (`/teams/`, `/players/`, `/matches/`, `/simulate-batch/` or whatever `simulate_batch` reverses to, `/team-create/` or whatever `team_create` reverses to, `/maps/`) and presence of `id="player-list-nav-link"`. Asserts presence of `id="tools-nav-link"` and `id="help-nav-link"`. Asserts ABSENCE of `id="dashboard-nav-link"`, `id="league-nav-link"`, `id="team-nav-link"`, `id="players-nav-link"`, `id="stats-nav-link"`, `id="leagues-nav-link"` (retired). `test_sandbox_mode_tools_before_help`: asserts the rendered HTML has `tools-nav-link` appearing BEFORE `help-nav-link` in source order (string-index check). `test_sandbox_mode_no_league_dropdown`: asserts the substring `League ▾` does not appear in the rendered HTML for `/teams/`.

* **`TestLg01kLeagueModeTopbar`** (Django `TestCase`). `setUp` creates a League with at least one Season (so `_build_league_sidebar_links` returns 23 entries with Standings/Schedule LIVE). `test_league_mode_renders_dashboard_icon`: GET `/leagues/<league.id>/`. Asserts presence of `id="dashboard-nav-link"` and the `⌂` U+2302 character inside its anchor body. Asserts the `href` of `dashboard-nav-link` resolves to `reverse("league_dashboard", kwargs={"league_id": league.id})`. `test_league_mode_renders_4_section_toggles`: asserts presence of `id="league-nav-link"`, `id="team-nav-link"`, `id="players-nav-link"`, `id="stats-nav-link"` (all 4 dropdown toggles). `test_league_mode_renders_tools_help`: asserts presence of `id="tools-nav-link"` and `id="help-nav-link"`. `test_league_mode_no_flat_sandbox_links`: asserts absence of `id="player-list-nav-link"` and the substring `>Teams</a>` and `>Matches</a>` and `>Batch Sim</a>` and `>Create Team</a>` and `>Maps</a>` from the rendered HTML's `<div class="navbar-nav ms-auto">` block. `test_league_mode_topbar_links_iteration`: asserts at least one `topbar-{section}-{key}` id per section is present, e.g. `id="topbar-league-history"`, `id="topbar-team-roster"`, `id="topbar-players-free_agents"`, `id="topbar-stats-game_log"`. `test_league_mode_dashboard_entry_not_in_dropdowns`: asserts the substring `topbar-top-dashboard` does NOT appear in the rendered HTML (the top Dashboard entry is filtered out of the regrouped iteration). `test_league_mode_retired_ids_absent`: asserts absence of `id="leagues-nav-link"`, `id="league-standings-topbar-link"`, `id="league-playoffs-topbar-link"`, `id="league-finances-topbar-link"`, `id="league-history-topbar-link"`, `id="league-power-rankings-topbar-link"` (the 6 retired LG-01h ids).

EXTENDED test file `matches/tests/test_lg01h_app_mode_processor.py` (existing). Add the following tests to `TestAppModeContextProcessor`:

* `test_start_mode_for_exact_root_path`: builds a `RequestFactory().get("/")` and asserts `app_mode(request) == {"app_mode": "start"}`.
* `test_sandbox_mode_for_empty_path`: builds a request whose `.path = ""` (via setattr after `RequestFactory().get("/")`) and asserts `app_mode(request) == {"app_mode": "sandbox"}` (empty string does NOT match the exact `/` rule).
* `test_sandbox_mode_for_missing_path_attribute`: builds a raw object with no `.path` attribute (e.g. `type("R", (), {})()`) and asserts `app_mode(request) == {"app_mode": "sandbox"}`.
* `test_league_mode_for_leagues_prefix`: existing LG-01h test — unchanged.
* `test_league_mode_for_seasons_prefix`: existing LG-01h test — unchanged.
* `test_sandbox_mode_for_teams_prefix`: existing LG-01h test — unchanged.

EXTENDED test file `matches/tests/test_league_nav_context_processor.py` (existing). The 5 LG-01h test methods on the retired URL keys are DELETED. NEW test methods:

* `test_top_bar_links_is_23_entries_with_league`: creates a League + active Season, asserts `result["top_bar_links"]` is a `list` of length 23.
* `test_top_bar_links_is_empty_on_fallback`: zero Leagues exist, asserts `result["top_bar_links"] == []`.
* `test_top_bar_links_is_empty_with_two_leagues_no_session_pin`: creates 2 Leagues, no session pin, asserts `result["top_bar_links"] == []`.
* `test_top_bar_dashboard_url_resolves_to_league_dashboard`: creates a League, asserts `result["top_bar_dashboard_url"] == reverse("league_dashboard", kwargs={"league_id": league.id})`.
* `test_top_bar_dashboard_url_falls_back_to_league_list`: zero Leagues, asserts `result["top_bar_dashboard_url"] == reverse("league_list")`.
* `test_session_pin_resolves_picked_league`: creates 2 Leagues, sets `session["last_league_id"] = league_2.id`, asserts both `top_bar_links` non-empty and `top_bar_dashboard_url` resolves to League 2's dashboard.
* `test_displayed_season_falls_back_to_most_recent_completed`: League with no active Season but one completed Season, asserts `top_bar_links` is 23 entries (Standings / Schedule LIVE).
* `test_displayed_season_is_none_disables_standings_and_schedule`: League with no Season at all, asserts `top_bar_links` length is still 23 but the Standings entry has `url is None, disabled is True` and the LEAGUE > Schedule entry has `url is None, disabled is True`.
* `test_retired_keys_absent`: asserts `top_bar_history_url`, `top_bar_standings_url`, `top_bar_playoffs_url`, `top_bar_finances_url`, `top_bar_power_rankings_url` are NOT in `result.keys()`.
* `test_build_helper_called_with_sidebar_active_none`: monkeypatches `_build_league_sidebar_links` to record its `sidebar_active` kwarg, asserts the kwarg is `None`.
* `test_top_bar_links_top_entry_present`: asserts `result["top_bar_links"][0]["section"] == "top"` and `["key"] == "dashboard"` (the top Dashboard entry is present in `top_bar_links`; it's the TEMPLATE that filters it out of the regrouped iteration, not the processor).

The Tests agent writes failing tests against the locked DOM-id list + 2-key processor return + 3-mode `app_mode` enum BEFORE the Code agent lands the rewrite. Tests must NOT touch `simulate_scheduled_round` / `simulate_match` / `save_games` or any simulator entry point.

## Scope-out (locked)

No model change. No migration. No simulator touch. No RNG consumption. No `_flush_to_db` touch. No SIM-07 / SIM-08 contract interaction. **No Score Calibration re-baseline obligation** — LG-01k is a UI restructure; no simulation mechanics change. No new ADR (ADR-0017 is unchanged; the LG-01k modification is at the implementation layer, the LG-01h architectural decision still stands). No CONTEXT.md edit — `start` / `sandbox` / `league` are implementation enum values for the topnav rendering, not domain language. No new dependency. No API / DRF endpoint. No `django.contrib.messages` flash. No admin change. No JS framework / htmx / Alpine / Stimulus / inline `<script>` blocks (Bootstrap 5 dropdown JS already in `base.html` is the only existing dep). No new template tag library, no new Django context processor beyond the existing 2. No edit to `templates/_partials/league_sidebar.html` — the sidebar partial markup is unchanged. No edit to `matches.views._build_league_sidebar_links` — it is read-only consumed by both the sidebar partial and the new topbar. No edit to any view function. No edit to any URL include file. No edit to `core/views.py`. No edit to `matches/views.py` (the `_build_league_sidebar_links` import lives in the context processor, not in `matches/views.py` itself). No edit to the LG-01h `coming_soon` view / `_FEATURE_REGISTRY` / `templates/_placeholder.html`. No edit to the LG-01a `landing` view / `templates/core/landing.html`. No backfill. No edit to `teams/`, `core/` view modules, or any other app's models/views. No edit to `settings.py` (the `TEMPLATES` context-processor registration list is unchanged — only the existing `core.context_processors.app_mode` and `core.context_processors.league_nav` entries are reused). No mode-toggle UI (mode is path-driven only, per LG-01h precedent — clicking a mode card from `/` re-renders in the destination mode automatically). No multiplayer mode (deferred per ADR-0017 §1). No new placeholder views or `coming_soon_*` URL names — LG-01k strictly reuses the 25 LG-01h URL names.

## Behaviour-neutrality / determinism note

LG-01k is a **UI restructure** with three logical effects: (a) the topnav renders different DOM ids and a different visible link set per the 3-mode enum; (b) the `app_mode` context value `"start"` is added to the global template context for paths exactly equal to `/`; (c) the `league_nav` context dict shape changes from 5 URL keys to 2 keys (`top_bar_links` + `top_bar_dashboard_url`). None of these effects touch simulation: no read or write to `PlayerRoundState`, `GameEvent`, `GameRound`, `Match`, `Season.matchdays`; no RNG consumption; no `_flush_to_db` touch; no Score Calibration baseline change. **No SIM-07 / SIM-08 contract interaction.** The context-processor extensions are read-only — the existing `last_league_id` session write sites (LG-01f / LG-01h `coming_soon`) are unchanged; the LG-01k `league_nav` reads `session["last_league_id"]` but does not write it. Existing pytest determinism harness (fixed-seed simulation tests in `matches/tests/simulation_tests.py`) is unaffected — the topbar context-processor extensions never participate in simulation paths.

The Code agent runs `python -m black laserforce_simulator` on the modified `core/context_processors.py` and any new test files after landing the changes (per project tooling convention). The Tests agent runs the full `pytest` suite and reports exact pass/fail counts before reporting completion (per CLAUDE.md `## Testing & Verification`). The Docs agent updates `matches/CLAUDE.md` and `PLAN.md` and does NOT write any new `.md` files.

## Locked Names Index

Context processor: `core.context_processors.app_mode` (signature unchanged, body rewritten 2→3 branches), `core.context_processors.league_nav` (signature unchanged, body rewritten — drops 5 keys, adds 2 keys, return-type annotation widens from `dict[str, str]` to `dict[str, Any]`). Context keys (NEW): `top_bar_links` (`list[dict]`, 23 entries or `[]`), `top_bar_dashboard_url` (`str`). Context keys (RETIRED): `top_bar_history_url`, `top_bar_standings_url`, `top_bar_playoffs_url`, `top_bar_finances_url`, `top_bar_power_rankings_url`. Context key `app_mode` values (LOCKED 3-value enum): `"start"`, `"league"`, `"sandbox"`. URL name reuse (LG-01h, unchanged): `landing`, `team_list`, `player_list`, `match_list`, `simulate_batch`, `team_create`, `map_list`, `league_list`, `league_dashboard`, `season_standings`, `season_schedule`, `league_history`, `team_schedule`, `coming_soon_playoffs`, `coming_soon_finances`, `coming_soon_power_rankings`, `coming_soon_team_roster`, `coming_soon_team_finances`, `coming_soon_team_history`, `coming_soon_free_agents`, `coming_soon_trade`, `coming_soon_trading_block`, `coming_soon_prospects`, `coming_soon_watch_list`, `coming_soon_hall_of_fame`, `coming_soon_game_log`, `coming_soon_league_leaders`, `coming_soon_player_ratings`, `coming_soon_player_stats`, `coming_soon_team_stats`, `coming_soon_statistical_feats`, `coming_soon_help_overview`, `coming_soon_help_changes`, `coming_soon_help_custom_rosters`, `coming_soon_help_debugging`, `coming_soon_help_lol_gm_forums`, `coming_soon_help_zen_gm_forums`, `coming_soon_tools_achievements`, `coming_soon_tools_screenshot`, `coming_soon_tools_debug_mode`, `coming_soon_tools_reset_db`. Helper consumed read-only: `matches.views._build_league_sidebar_links(league, displayed_season, sidebar_active=None)` (23-entry return). DOM ids NEW: `dashboard-nav-link` (league mode only), `league-nav-link` (league mode only — replaces retired `leagues-nav-link`), `team-nav-link` (league mode only), `players-nav-link` (league mode only), `stats-nav-link` (league mode only), `topbar-league-standings`, `topbar-league-schedule`, `topbar-league-playoffs`, `topbar-league-finances`, `topbar-league-history`, `topbar-league-power_rankings`, `topbar-team-roster`, `topbar-team-schedule_team`, `topbar-team-finances_team`, `topbar-team-history_team`, `topbar-players-free_agents`, `topbar-players-trade`, `topbar-players-trading_block`, `topbar-players-prospects`, `topbar-players-watch_list`, `topbar-players-hall_of_fame`, `topbar-stats-game_log`, `topbar-stats-league_leaders`, `topbar-stats-player_ratings`, `topbar-stats-player_stats`, `topbar-stats-team_stats`, `topbar-stats-statistical_feats`. DOM ids PRESERVED from LG-01h: `tools-nav-link`, `tools-achievements-topbar-link`, `tools-screenshot-topbar-link`, `tools-debug-mode-topbar-link`, `tools-reset-db-topbar-link`, `help-nav-link`, `help-overview-topbar-link`, `help-changes-topbar-link`, `help-custom-rosters-topbar-link`, `help-debugging-topbar-link`, `help-lol-gm-forums-topbar-link`, `help-zen-gm-forums-topbar-link`. DOM id PRESERVED from LG-01a: `player-list-nav-link` (sandbox mode only). DOM ids RETIRED: `leagues-nav-link`, `league-standings-topbar-link`, `league-playoffs-topbar-link`, `league-finances-topbar-link`, `league-history-topbar-link`, `league-power-rankings-topbar-link`. Toggle text literals: `League ▾`, `Team ▾`, `Players ▾`, `Stats ▾`, `Tools ▾`, `Help ▾` (all trailing U+25BE). Home-icon text content literal: `⌂` (U+2302). DOM-id pattern locked: `topbar-{section}-{key}` (mirrors LG-01f `sidebar-{section}-{key}`). Files modified: `laserforce_simulator/core/context_processors.py`, `laserforce_simulator/templates/base.html`, `laserforce_simulator/matches/CLAUDE.md`, `PLAN.md`. Files new (test): `laserforce_simulator/matches/tests/test_lg01k_base_html_branching.py`. Files extended (test): `laserforce_simulator/matches/tests/test_lg01h_app_mode_processor.py`, `laserforce_simulator/matches/tests/test_league_nav_context_processor.py`, `laserforce_simulator/matches/tests/test_lg01h_base_html_branching.py` (minimal-edit removal of retired-id references). File new (optional partial, Code agent's discretion): `laserforce_simulator/templates/_partials/topnav_tools_help.html`. Test class names: `TestLg01kStartModeTopbar`, `TestLg01kSandboxModeTopbar`, `TestLg01kLeagueModeTopbar` (new file); `TestAppModeContextProcessor` (extended); `TestLeagueNavContextProcessor` (extended). Seam contract precedent: [`.claude/worktrees/lg-01h-seam-contract.md`](lg-01h-seam-contract.md).
