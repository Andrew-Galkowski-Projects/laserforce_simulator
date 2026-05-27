# LG-01c — League & Season Dashboards · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01c
ships **read-only** League / Season dashboard views on top of the LG-01
foundation: `GET /leagues/<int:league_id>/` and
`GET /seasons/<int:season_id>/`. **No model change, no migration, no ADR,
no CONTEXT.md edit, no POST endpoint, no simulator touch.** Action buttons
(Start Season / Play Next / Start Next Season) are `<button disabled>`
placeholders keyed off Season state; their POST counterparts are deferred
to LG-01d / LG-01e.

This contract mirrors the structure of
[`.claude/worktrees/lg-01b-seam-contract.md`](lg-01b-seam-contract.md) and
extends the LG-01 foundation pinned in
[`.claude/worktrees/lg-01-seam-contract.md`](lg-01-seam-contract.md).

---

## 0. Overview

LG-01c adds **two read-only dashboards** over LG-01's existing surface:

- **League dashboard** at `GET /leagues/<int:league_id>/` — picks one
  Season to display (the active Season, or the most-recent completed
  Season as fallback, or none) and renders a snapshot: state badge,
  placeholder action button, top-3 standings, next round, round count,
  three leaders snippets.
- **Season dashboard** at `GET /seasons/<int:season_id>/` — same body
  surface as the League dashboard plus a sidebar navigating to the
  existing LG-01 standings / schedule pages (live links) and to Teams /
  History (disabled placeholders, deferred to LG-01f / LG-01g).

One NEW **pure module** `matches/season_dashboard.py` carries the leader
aggregation + next-fixture lookup + round-count helpers (frozen import
allowlist; defensive `TestNoDjangoImportsLeaked` per HX-03 / RES-04 / LG-01
precedent). Two new view functions, two new templates, two
single-line inserts into the existing URL files. NEW test file
`matches/tests/test_season_dashboard.py` for the pure module; NEW test
file `matches/tests/test_league_dashboard.py` for the league view; NEW
test file `matches/tests/test_season_dashboard_view.py` for the season
view.

---

## 1. URLs

Two single-line inserts. **No new URL include file.** Both URL files are
LG-01 / LG-01a artefacts and already mounted by `laserforce_simulator/urls.py`.

### 1a. `matches/league_urls.py`

- **Insertion point:** the new `path(...)` line is inserted **AFTER**
  `path("create/", views.league_create, name="league_create")` (LG-01b)
  and **BEFORE** `path("", views.league_list, name="league_list")` (LG-01a).
  Django URL resolution is first-match — the typed `<int:league_id>/`
  pattern matches only digit-only paths so `/leagues/` still resolves to
  the LG-01a list view and `/leagues/create/` still resolves to the LG-01b
  create flow.
- **Final `urlpatterns` order:**
  1. `path("create/", views.league_create, name="league_create")` *(LG-01b)*
  2. `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` *(NEW — LG-01c)*
  3. `path("", views.league_list, name="league_list")` *(LG-01a)*
- **Full URL:** `/leagues/<int:league_id>/`
- **Reverse name:** `league_dashboard` (no `app_name`, bare URL namespace —
  mirrors `league_list`, `league_create`)
- **HTTP methods:** GET only. Non-GET ⇒ **405** via
  `HttpResponseNotAllowed(["GET"])` (mirrors the `movement_heatmap` /
  `export_round_report` guard).

### 1b. `matches/season_urls.py`

- **Insertion point:** the new `path(...)` line is inserted **BEFORE**
  the existing `path("<int:season_id>/standings/", ...)` and
  `path("<int:season_id>/schedule/", ...)` entries (both LG-01). The
  `<int:season_id>/` pattern (no trailing path segment) is more specific
  than `<int:season_id>/standings/` only in the sense of being shorter —
  Django URL resolution is first-match so the new dashboard route MUST
  come first or the existing standings/schedule patterns would shadow it.
  Insertion is at the top of the `urlpatterns` list.
- **Final `urlpatterns` order:**
  1. `path("<int:season_id>/", views.season_dashboard, name="season_dashboard")` *(NEW — LG-01c)*
  2. `path("<int:season_id>/standings/", views.season_standings, name="season_standings")` *(LG-01)*
  3. `path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule")` *(LG-01)*
- **Full URL:** `/seasons/<int:season_id>/`
- **Reverse name:** `season_dashboard` (no `app_name`, bare URL namespace —
  mirrors `season_standings`, `season_schedule`)
- **HTTP methods:** GET only. Non-GET ⇒ **405** via
  `HttpResponseNotAllowed(["GET"])`.

---

## 2. View signatures

Both views are appended to `matches/views.py` (the file LG-01 / LG-01a /
LG-01b's views live in). Both are undecorated (no `@transaction.atomic`
— read-only; no `@require_GET` — the explicit `HttpResponseNotAllowed`
guard is the locked pattern).

### 2a. `league_dashboard`

```python
def league_dashboard(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single League.

    Picks one Season to display (active > most-recent completed > none),
    renders state badge + placeholder action button + top-3 standings +
    next round + round count + three leaders snippets.
    """
```

- **404:** `get_object_or_404(League, pk=league_id)` — missing id ⇒ 404.
- **405:** `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`
  as the **first** line of the body (before the ORM hit, mirrors
  `movement_heatmap`).
- **Season pick logic** (locked, in order):
  1. `active = league.seasons.exclude(state="completed").order_by("-id").first()`
     — the LG-01 `League.active_season` property semantics. Implementation
     MUST call `league.active_season` (the `@property`, no parentheses),
     not re-implement the query.
  2. If `active is not None`:
     - `displayed_season = active`
     - `season_mode = "draft"` if `active.state == "draft"` else `"active"`
  3. Else (no non-completed Season exists):
     - `completed_recent = league.seasons.filter(state="completed").order_by("-id").first()`
     - If `completed_recent is not None`:
       - `displayed_season = completed_recent`
       - `season_mode = "completed"`
     - Else:
       - `displayed_season = None`
       - `season_mode = "none"`
- **Body assembly:** delegated to a shared private helper
  `_build_dashboard_context(displayed_season, season_mode)` (see §2c) so
  the league and season views build the same body keys identically. The
  league view does NOT prepend `season_dashboard`-only keys (`season`,
  `sidebar_active`, `sidebar_links`) — those are added by §2b alone.
- **Template:** `templates/leagues/dashboard.html`.

### 2b. `season_dashboard`

```python
def season_dashboard(request: HttpRequest, season_id: int) -> HttpResponse:
    """LG-01c — Dashboard for a single Season.

    Same body surface as the League dashboard plus a sidebar with live
    links to standings / schedule and disabled placeholders for Teams /
    History.
    """
```

- **404:** `get_object_or_404(Season, pk=season_id)` — missing id ⇒ 404.
- **405:** same `HttpResponseNotAllowed(["GET"])` guard as the first line.
- **Season pick logic:** trivial — `displayed_season = season`. The
  `season_mode` is the literal value of `season.state` (one of
  `"draft" | "active" | "completed"`; **never** `"none"` — the Season
  exists by virtue of resolving the URL).
- **Body assembly:** call the shared `_build_dashboard_context(season,
  season_mode)` helper, then add the season-only keys: `season`,
  `sidebar_active = "overview"`, and `sidebar_links` (see §3b for the
  4-entry list).
- **Template:** `templates/seasons/dashboard.html`.

### 2c. Shared body-context helper

`matches/views.py::_build_dashboard_context(displayed_season: Season | None, season_mode: str) -> dict` —
module-level flat helper (RV-01 / HX-03 `_`-prefixed precedent), private
to the file. Returns a dict with **exactly these keys** (the body-context
seam — pinned):

```python
{
    "displayed_season":         Season | None,
    "season_mode":              str,                  # "draft" | "active" | "completed" | "none"
    "standings_snippet":        list[tuple],          # top-3 (StandingsRow, Team) tuples, possibly []
    "next_fixture":             dict | None,          # frozen 7-key dict, see §4
    "round_count_completed":    int,                  # 0 in draft/none branches
    "round_count_total":        int,                  # 0 in draft/none branches
    "leaders_points":           list[LeaderRow],      # possibly []
    "leaders_tags":             list[LeaderRow],
    "leaders_ratio":            list[LeaderRow],
    "action_button_label":      str,
    "action_button_state":      str,                  # "start_season" | "play_next" | "start_next_season" | "none"
}
```

**Branch-specific population rules:**

- `season_mode == "none"` (League dashboard only — no Season exists):
  - `displayed_season = None`
  - `standings_snippet = []`, `next_fixture = None`, `round_count_* = 0`
  - `leaders_* = []`
  - `action_button_label = "No Season"`, `action_button_state = "none"`
- `season_mode == "draft"` (unpopulated preview):
  - **Standings snippet:** zero-filled top 3 — the displayed_season's
    `teams.all()` sorted by name (alphabetical asc), take first 3,
    rendered as `(StandingsRow_dict_with_team_id_and_zeros, team)`
    tuples. The dict shape mirrors the LG-01 standings draft-preview
    rows (same 9 keys `team_id, matches_played=0, wins=0, losses=0,
    ties=0, league_points=0, round_wins=0, total_score=0, rank=i+1`).
  - `next_fixture = None`, `round_count_completed = 0`, `round_count_total = 0`
  - `leaders_* = []` (no PlayerRoundStates to aggregate yet)
  - `action_button_label = "Start Season"`, `action_button_state = "start_season"`
- `season_mode == "active"` (fully populated):
  - **Standings snippet:** call the LG-01 `compute_standings(...)`
    pure module against the season's completed matches, take rows
    `[:3]`, pair each with its Team via a single
    `Team.objects.in_bulk(...)` query, build `[(row, team), ...]`.
  - **Next fixture:** materialise `fixtures = generate_schedule(
    displayed_season.starting_team_ids_json,
    displayed_season.schedule_format)`, materialise `played_keys =
    {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
    gr.round_number) for gr in GameRound.objects.filter(
    match__season=displayed_season).select_related("match")}`, then
    `fixture = find_next_fixture(fixtures, played_keys)`. If
    `fixture is None`: `next_fixture = None`. Else build the 7-key
    frozen `next_fixture` dict (§4).
  - **Round count:** `round_count_completed, round_count_total =
    round_progress(fixtures, played_keys)`.
  - `leaders_*`: build the `player_rounds` list (§5), then call
    `compute_leaders(player_rounds, stat, limit=3)` once per stat.
  - `action_button_label = "Play Next"`, `action_button_state = "play_next"`
- `season_mode == "completed"`:
  - **Standings snippet:** same as `"active"` (`compute_standings` over
    completed matches; top 3 rows).
  - **Next fixture:** `find_next_fixture` returns `None` on an all-played
    season (the completion invariant from LG-01 `complete_if_finished`);
    `next_fixture = None`.
  - **Round count:** `round_progress` returns
    `(len(fixtures), len(fixtures))`.
  - `leaders_*`: same as `"active"`.
  - `action_button_label = "Start Next Season"`,
    `action_button_state = "start_next_season"`

---

## 3. View context keys

### 3a. `league_dashboard` context

```python
{
    "league":                   League,
    "displayed_season":         Season | None,
    "season_mode":              str,                  # "draft" | "active" | "completed" | "none"
    "standings_snippet":        list[tuple[StandingsRow | dict, Team]],
    "next_fixture":             dict | None,
    "round_count_completed":    int,
    "round_count_total":        int,
    "leaders_points":           list[LeaderRow],
    "leaders_tags":             list[LeaderRow],
    "leaders_ratio":            list[LeaderRow],
    "action_button_label":      str,
    "action_button_state":      str,
}
```

Exactly 12 keys; `league` plus the 11 shared body keys from §2c. No
`teams_by_id`, no `rows_with_teams` — `standings_snippet` already pairs
rows with teams.

### 3b. `season_dashboard` context

```python
{
    "season":                   Season,
    "displayed_season":         Season,               # always == season here; redundant but kept for template parity with league dashboard
    "season_mode":              str,                  # "draft" | "active" | "completed"
    "standings_snippet":        list[tuple[StandingsRow | dict, Team]],
    "next_fixture":             dict | None,
    "round_count_completed":    int,
    "round_count_total":        int,
    "leaders_points":           list[LeaderRow],
    "leaders_tags":             list[LeaderRow],
    "leaders_ratio":            list[LeaderRow],
    "action_button_label":      str,
    "action_button_state":      str,
    "sidebar_active":           str,                  # always "overview" at LG-01c
    "sidebar_links":            list[dict],           # 4-entry list, see below
}
```

Exactly 15 keys. The `displayed_season` is kept (== `season`) so the
two templates can share leader / standings / next-fixture rendering
blocks via `{% include %}` against the same key.

**`sidebar_links` shape (4 entries in pinned order):**

```python
[
    {"key": "overview",  "label": "Overview",  "url": None,                                              "disabled": False, "active": True},
    {"key": "standings", "label": "Standings", "url": reverse("season_standings", args=[season.id]),     "disabled": False, "active": False},
    {"key": "schedule",  "label": "Schedule",  "url": reverse("season_schedule", args=[season.id]),      "disabled": False, "active": False},
    {"key": "teams",     "label": "Teams",     "url": None,                                              "disabled": True,  "active": False},
    {"key": "history",   "label": "History",   "url": None,                                              "disabled": True,  "active": False},
]
```

Five entries (overview, standings, schedule, teams, history) — the
in-prose "4-entry" was loose phrasing for the 4 non-overview tabs. The
template renders one `<li>` / `<a>` per entry; entries with
`disabled=True` render as `<span>` (no `<a href>`) so they cannot be
clicked. `key="overview"` carries `active=True` because LG-01c is the
overview view.

---

## 4. `next_fixture` dict shape

When non-`None`, this is a **frozen 7-key dict** built view-side from a
`ScheduleFixture` + the two Teams. Pinned shape:

```python
{
    "matchday":      int,            # 1-based, from ScheduleFixture.matchday
    "round_number":  int,            # 1 or 2, from ScheduleFixture.round_number
    "team_a_id":     int,
    "team_a_name":   str,            # resolved via Team.objects.in_bulk(...) once per view call
    "team_b_id":     int,
    "team_b_name":   str,
    "date":          datetime.date,  # season.start_date + (matchday - 1) * 7 days; mirrors LG-01 season_schedule
}
```

The `date` is computed the same way as LG-01 `season_schedule` (using
`from datetime import timedelta` + `season.start_date + timedelta(days=
(matchday - 1) * 7)`) so the dashboards and the schedule page agree on
matchday dates.

---

## 5. Pure module surface

**NEW file:** `matches/season_dashboard.py`

**Frozen import allowlist** — the module's `import` statements may
reference ONLY:

- `dataclasses` (for `@dataclass(frozen=True)`)
- `typing` (for `Optional`, `Sequence` if needed — PEP-585 syntax inline)
- `collections` (only if needed — `defaultdict` may be useful for the
  per-player aggregation in `compute_leaders`)

**Pinned: NO** Django, NO ORM imports, NO `random` / `secrets`, NO
`datetime`, NO file I/O, NO logging. Pinned by `TestNoDjangoImportsLeaked`
in `matches/tests/test_season_dashboard.py` (subprocess fresh-import +
walk `sys.modules`; HX-03 / HX-04 / RES-04 / LG-01 pattern).

The module ALSO does not import `matches.schedule_generator` — it
consumes `ScheduleFixture` instances passed in by the view (TYPE_CHECKING
or string annotations only). This keeps the pure import allowlist truly
frozen; the dataclass shape itself is the cross-module contract.

### 5a. `LeaderRow` dataclass

```python
@dataclass(frozen=True)
class LeaderRow:
    player_id:     int
    player_name:   str
    role:          str             # "commander" | "heavy" | "scout" | "medic" | "ammo"
    team_id:       int
    team_name:     str
    value:         float           # the leader stat's value
    games_played:  int             # count of PlayerRoundState rows for this player in the input
    rank:          int             # 1-based, dense, in iteration order
```

9 fields (8 listed above + the trailing `rank` per the user spec —
**recount:** `player_id, player_name, role, team_id, team_name, value,
games_played, rank` = 8 fields). 8 fields total.

### 5b. `compute_leaders`

```python
def compute_leaders(
    player_rounds: list[dict],
    stat: str,                       # "points_per_game" | "tags_per_game" | "tag_ratio"
    limit: int = 3,
) -> list[LeaderRow]:
    """Aggregate per-PlayerRoundState rows into a ranked leaders snippet.

    Args:
        player_rounds: list of dicts with the 7 frozen keys below. One
            entry per ``PlayerRoundState`` row across the Season's
            completed Rounds.
        stat: which leader stat to compute. Locked vocabulary:
            ``"points_per_game"`` — ``mean(points_scored)``;
            ``"tags_per_game"`` — ``mean(tags_made)``;
            ``"tag_ratio"``     — ``sum(tags_made) / max(sum(times_tagged), 1)``
            (canonical CONTEXT.md sum/sum form — NOT mean of per-row
            ratios; the ``max(..., 1)`` denominator avoids div-by-zero).
        limit: how many rows to return after sorting. Default 3.

    Returns:
        Top-``limit`` ``LeaderRow`` instances sorted by
        ``(value desc, games_played desc, player_id asc)``. ``rank`` is
        1-based dense in iteration order. Empty input ⇒ ``[]``.
    """
```

**Aggregation rules:**

- Group `player_rounds` by `player_id`. Each group's `player_name`,
  `role`, `team_id`, `team_name` come from any one row in the group
  (the view materialises these from the player's current state — see
  §6). If the group contains rows with inconsistent role / team
  (defensive — player moved teams or switched preferred role mid-Season),
  the module takes the **last** row's values (input-order-defined; the
  view passes rows in `id` ascending so "last" == most-recent
  PlayerRoundState).
- `games_played` = `len(group)`.
- Stat computation per group:
  - `"points_per_game"`: `value = sum(r["points_scored"] for r in group) / len(group)`
  - `"tags_per_game"`: `value = sum(r["tags_made"] for r in group) / len(group)`
  - `"tag_ratio"`: `value = sum(r["tags_made"] for r in group) / max(sum(r["times_tagged"] for r in group), 1)` — the
    `max(..., 1)` clamp is the locked denominator (matches the
    Player.career_stats existing rule); `value` is `float` even when
    both sums are 0 (`0 / 1 = 0.0`).
- Sort ladder (in order):
  1. `value` desc
  2. `games_played` desc
  3. `player_id` asc
- Take the first `limit` rows (default 3), assign `rank = i + 1` in
  iteration order. Less than `limit` available ⇒ return what exists.
- **Empty input** (`player_rounds == []`): return `[]` immediately.
- **Unknown stat string:** raise `ValueError(f"Unknown stat {stat!r}; expected one of points_per_game, tags_per_game, tag_ratio")`.

### 5c. `find_next_fixture`

```python
def find_next_fixture(
    fixtures: "list[ScheduleFixture]",
    played_keys: "set[tuple[frozenset[int], int]]",
) -> "Optional[ScheduleFixture]":
    """Return the first unplayed fixture in iteration order.

    Args:
        fixtures: list of ``ScheduleFixture`` in the canonical
            iteration order from ``generate_schedule(...)`` (already
            sorted by ``(matchday, team_a_id)``).
        played_keys: set of ``(frozenset({team_red_id, team_blue_id}),
            round_number)`` tuples for every persisted ``GameRound`` in
            the Season. Side-agnostic ``frozenset`` match.

    Returns:
        The first ``ScheduleFixture`` whose ``(frozenset({team_a_id,
        team_b_id}), round_number)`` is NOT in ``played_keys``, or
        ``None`` if every fixture has been played.
    """
```

**Edges:**

- `fixtures == []` ⇒ `None`.
- All played ⇒ `None`.
- Single fixture, unplayed ⇒ that fixture.

### 5d. `round_progress`

```python
def round_progress(
    fixtures: "list[ScheduleFixture]",
    played_keys: "set[tuple[frozenset[int], int]]",
) -> tuple[int, int]:
    """Return ``(completed, total)`` Round counts.

    Args:
        fixtures: as in ``find_next_fixture``.
        played_keys: as in ``find_next_fixture``.

    Returns:
        ``(completed, total)`` where ``completed`` = count of fixtures
        whose ``(frozenset({team_a_id, team_b_id}), round_number)``
        appears in ``played_keys``, and ``total`` = ``len(fixtures)``.

    Note:
        ``completed`` is computed from fixtures matched against
        ``played_keys``, NOT from ``len(played_keys)`` — extra
        ``GameRound`` rows that don't correspond to a fixture (defensive
        — e.g. data drift) are not double-counted.
    """
```

**Edges:**

- `fixtures == []` ⇒ `(0, 0)`.
- No played keys ⇒ `(0, len(fixtures))`.
- Every fixture played ⇒ `(len(fixtures), len(fixtures))`.

---

## 6. Player-round seam dict shape

The view materialises one entry per `PlayerRoundState` row in the
Season's completed Rounds and hands the list to `compute_leaders`. The
dict is the only thing crossing the view ↔ pure-module seam for leader
aggregation. **Frozen 7 keys, every key required, every key present on
every entry:**

```python
{
    "player_id":     int,
    "player_name":   str,
    "role":          str,            # PlayerRoundState.role
    "team_id":       int,
    "team_name":     str,
    "tags_made":     int,            # PlayerRoundState.tags_made
    "times_tagged":  int,            # PlayerRoundState.times_tagged
    "points_scored": int,            # PlayerRoundState.points_scored
}
```

**View-side materialisation** (locked):

```python
player_rounds_qs = (
    PlayerRoundState.objects
    .filter(game_round__match__season=displayed_season)
    .select_related("player", "game_round", "game_round__match")
    .order_by("id")
)
```

The view iterates this queryset (single `select_related`-flattened
query) and emits one dict per row. `player_name` reads via
`prs.player.name`. `team_id` / `team_name` are resolved from
`prs.game_round.team_red` / `prs.game_round.team_blue` using
`prs.team_color` (`"red"` ⇒ `team_red`, `"blue"` ⇒ `team_blue`). The
order-by-`id` ordering is what makes "last row wins" deterministic in
the pure module's defensive role/team fallback.

`displayed_season` is `None` in the `"none"` League branch and the
queryset is **not** issued; `leaders_* = []` directly.

---

## 7. Templates + DOM ids

Two NEW templates, both extending `base.html`. Code agent picks
Bootstrap class names; **DOM ids below are pinned exactly** and tests
assert their presence per applicable branch.

### 7a. `templates/leagues/dashboard.html`

- **Path:** `laserforce_simulator/templates/leagues/dashboard.html`
- **`{% block title %}{{ league.name }} — League{% endblock %}`** (locked
  exact string; em-dash U+2014)
- **`{% block content %}`** contains the elements below; structure /
  order at Code agent's discretion EXCEPT the DOM ids must appear on
  the listed elements and must be present per the branch rules.

| DOM id | Branch presence | Element |
|---|---|---|
| `league-dashboard-header` | always | outer header container with `{{ league.name }}` |
| `league-dashboard-state-badge` | always | element rendering `season_mode` (e.g. `<span class="badge ...">{{ season_mode }}</span>`); when `season_mode == "none"` renders the literal `"No Season"` |
| `league-dashboard-action-button` | always | the placeholder `<button disabled>` with text == `action_button_label`; HTML `disabled` attribute MUST be present |
| `league-dashboard-standings-snippet` | when `season_mode in {"draft","active","completed"}` | container listing the top-3 standings tuples |
| `league-dashboard-next-round` | when `season_mode in {"active","completed"}` | container with the `next_fixture` body (or "All fixtures played" stub when `next_fixture is None` and `season_mode == "completed"`); omitted entirely in `"draft"` / `"none"` |
| `league-dashboard-round-count` | when `season_mode in {"active","completed"}` | container with `{{ round_count_completed }} / {{ round_count_total }}` |
| `league-dashboard-leaders-points` | when `season_mode in {"active","completed"}` | container iterating `leaders_points` |
| `league-dashboard-leaders-tags` | when `season_mode in {"active","completed"}` | container iterating `leaders_tags` |
| `league-dashboard-leaders-ratio` | when `season_mode in {"active","completed"}` | container iterating `leaders_ratio` |
| `league-dashboard-no-season-notice` | only when `season_mode == "none"` | container with text containing the substring `"No Season"` |

**Per-leader rendering inside each `leaders_*` container:** one `<a>` per
LeaderRow whose `href` is the **raw string `/players/{{ row.player_id }}/career-stats/`**
(NOT `{% url 'player_career_stats' ... %}`) — LG-01a precedent for
deferred URL resolution against an external surface; broken link
tolerated. Anchor text is `{{ row.player_name }}`, the value
`{{ row.value|floatformat:2 }}` is rendered adjacent. A **"View all
leaders"** anchor below the three snippets has `href` as the **raw
string `/leagues/{{ league.id }}/leaders/`** (placeholder — link
4'04s at LG-01c merge time; resolved by a future LG-01h or never; tests
assert the literal href substring is rendered).

**Standings-snippet rendering:** for each `(row, team)` tuple, render
`{{ row.rank }}. {{ team.name }} — {{ row.league_points }} pts` (or
similar — only the iteration over `standings_snippet` is pinned, not
the exact cell layout).

**Action button:** the `<button disabled>` has text == `action_button_label`
and a `data-action-state="{{ action_button_state }}"` attribute (locked
— tests assert both the label and the data attribute).

### 7b. `templates/seasons/dashboard.html`

- **Path:** `laserforce_simulator/templates/seasons/dashboard.html`
- **`{% block title %}{{ season.league.name }} — {{ season.name }}{% endblock %}`** (locked exact string)
- **`{% block content %}`** contains the elements below.

| DOM id | Branch presence | Element |
|---|---|---|
| `season-dashboard-header` | always | outer header container with `{{ season.league.name }} — {{ season.name }}` |
| `season-dashboard-state-badge` | always | element rendering `season_mode` |
| `season-dashboard-action-button` | always | the placeholder `<button disabled>` with text == `action_button_label`; HTML `disabled` MUST be present; `data-action-state="{{ action_button_state }}"` |
| `season-dashboard-sidebar` | always | outer `<nav>` / `<ul>` wrapping the 5 sidebar entries |
| `season-dashboard-sidebar-standings` | always | the standings `<a href="{{ sidebar_links.1.url }}">` (live link); `class` substring `sidebar-link` |
| `season-dashboard-sidebar-schedule` | always | the schedule `<a href="{{ sidebar_links.2.url }}">` (live link) |
| `season-dashboard-sidebar-teams` | always | the teams entry as `<span class="...disabled...">` (NO `<a href>`) — disabled placeholder |
| `season-dashboard-sidebar-history` | always | the history entry as `<span class="...disabled...">` — disabled placeholder |
| `season-dashboard-standings-snippet` | always | top-3 standings container (zero rows in draft branch, but the container is present so tests can assert the DOM id) |
| `season-dashboard-next-round` | when `season_mode in {"active","completed"}` | next-fixture container; in `"completed"` with `next_fixture is None` renders "All fixtures played" |
| `season-dashboard-round-count` | when `season_mode in {"active","completed"}` | `{{ round_count_completed }} / {{ round_count_total }}` |
| `season-dashboard-leaders-points` | when `season_mode in {"active","completed"}` | container iterating `leaders_points` |
| `season-dashboard-leaders-tags` | when `season_mode in {"active","completed"}` | container iterating `leaders_tags` |
| `season-dashboard-leaders-ratio` | when `season_mode in {"active","completed"}` | container iterating `leaders_ratio` |

**Sidebar rendering rule:** the template iterates `sidebar_links`. For
each entry, if `disabled` is True render `<span class="sidebar-link disabled">{{ entry.label }}</span>`;
else render `<a class="sidebar-link" href="{{ entry.url }}">{{ entry.label }}</a>`.
The overview entry (`key="overview"`) carries `url=None` AND `disabled=False`
AND `active=True`; it renders as `<span class="sidebar-link active">Overview</span>`
(active is the current page so no link is needed).

**Player career-stats / leaders-page hrefs:** same raw-string pattern
as the league dashboard (`/players/{{ row.player_id }}/career-stats/`
for player names, `/seasons/{{ season.id }}/leaders/` for the "View
all leaders" anchor). LG-01c does NOT mount either of these URL routes
— the broken-link tolerance is locked.

---

## 8. Test boundary

Three NEW test files. Each pinned by file name + class name(s); test
method names within each class are at the Tests agent's discretion
unless explicitly pinned below.

### 8a. `matches/tests/test_season_dashboard.py` (pure unit)

`SimpleTestCase` (no DB) for the pure module. **Frozen import allowlist**
(test file itself): `subprocess`, `sys`, `unittest` /
`django.test.SimpleTestCase`, `dataclasses`, `matches.season_dashboard`.
The test file MUST NOT import `matches.schedule_generator`; if a
`ScheduleFixture` instance is needed it is constructed locally via a
3-line minimal `@dataclass(frozen=True)` stub (or `matches.season_dashboard`
re-exports a `Protocol` — Tests agent picks).

**Classes:**

- `TestComputeLeadersEmpty`
  - `test_empty_player_rounds_returns_empty_list` (any stat ⇒ `[]`)
- `TestComputeLeadersSinglePlayer`
  - `test_single_row_points_per_game` (one row ⇒ one LeaderRow, `value == row["points_scored"]`, `games_played == 1`, `rank == 1`)
  - `test_single_row_tags_per_game`
  - `test_single_row_tag_ratio_uses_max_one_denominator` (row with `tags_made=5, times_tagged=0` ⇒ `value == 5.0`, no div-by-zero)
- `TestComputeLeadersTiebreak`
  - `test_tied_value_resolved_by_games_played_desc`
  - `test_tied_value_and_games_played_resolved_by_player_id_asc`
- `TestComputeLeadersDeterministic`
  - `test_repeated_calls_return_identical_rank_order` (run `compute_leaders` twice on the same input, assert equality)
- `TestComputeLeadersRoleMix`
  - `test_role_mix_does_not_affect_value_ranking` (leaders rank purely by stat, role just rides along on the LeaderRow)
- `TestComputeLeadersStatVocabulary`
  - `test_points_per_game_uses_mean`
  - `test_tags_per_game_uses_mean`
  - `test_tag_ratio_uses_sum_over_sum_clamped` (multi-row aggregation: `value == sum(tags_made) / max(sum(times_tagged), 1)`, NOT `mean(per-row ratios)`)
  - `test_unknown_stat_raises_value_error`
- `TestComputeLeadersLimit`
  - `test_limit_caps_output_length` (10 players, limit=3 ⇒ 3 rows)
  - `test_limit_higher_than_input_returns_all` (2 players, limit=3 ⇒ 2 rows)
  - `test_default_limit_is_three`
- `TestComputeLeadersDefensiveLastWins`
  - `test_inconsistent_role_takes_last_row_in_id_order` (defensive — group has 2 rows with different role; result uses 2nd row's role)
- `TestFindNextFixture`
  - `test_empty_fixtures_returns_none`
  - `test_all_played_returns_none`
  - `test_first_unplayed_in_iteration_order`
  - `test_side_agnostic_frozenset_match` (a played_key of `(frozenset({1,2}), 1)` matches a fixture with `team_a_id=1, team_b_id=2, round_number=1`)
- `TestRoundProgress`
  - `test_empty_fixtures_returns_zero_zero`
  - `test_no_played_returns_zero_total`
  - `test_all_played_returns_total_total`
  - `test_partial_played_counts_correctly`
  - `test_extra_played_keys_not_counted` (defensive — played_keys contains a key not in fixtures; `completed` doesn't double-count)
- `TestNoDjangoImportsLeaked` — single test:
  - `test_pure_module_does_not_pull_in_django` — spawns
    `python -c "import sys, matches.season_dashboard; leaked = [m for m in sys.modules if m == 'django' or m.startswith('django.')]; assert not leaked, leaked"` via
    `subprocess.run(...)`. Mirror HX-03 / LG-01 exactly.

### 8b. `matches/tests/test_league_dashboard.py` (Django `TestCase`)

DB-driven tests for `matches.views.league_dashboard`. Each test
constructs the minimal fixture (League + 0 / 1 / 2+ Seasons + Teams +
PlayerRoundStates as needed).

**Classes:**

- `TestLeagueDashboardRouting`
  - `test_get_returns_200_for_existing_league`
  - `test_get_returns_404_for_missing_league`
  - `test_post_returns_405`
  - `test_reverse_resolves_to_expected_path` (`reverse("league_dashboard", args=[league.id])` == `/leagues/<id>/`)
  - `test_template_used_is_leagues_dashboard_html`
- `TestLeagueDashboardSeasonPick`
  - `test_no_seasons_renders_none_branch` (League with no Season ⇒ `season_mode == "none"`, `league-dashboard-no-season-notice` rendered)
  - `test_draft_season_picked_as_active` (single draft Season ⇒ `season_mode == "draft"`, displayed_season is the draft Season)
  - `test_active_season_picked` (single active Season ⇒ `season_mode == "active"`)
  - `test_completed_only_falls_back_to_most_recent` (League with two completed Seasons ⇒ `displayed_season` is the higher-`id`, `season_mode == "completed"`)
  - `test_active_takes_precedence_over_completed` (League with one completed + one active ⇒ active is picked)
- `TestLeagueDashboardDraftBranch`
  - `test_draft_renders_action_button_with_start_season_state` (`action_button_label == "Start Season"`, `action_button_state == "start_season"`, `disabled` attribute present)
  - `test_draft_standings_snippet_sorted_by_team_name_asc_top_3`
  - `test_draft_omits_next_round_and_round_count_and_leaders` (the 3 leaders DOM ids + `league-dashboard-next-round` + `league-dashboard-round-count` ABSENT from rendered HTML)
- `TestLeagueDashboardActiveBranch`
  - `test_active_renders_action_button_with_play_next_state`
  - `test_active_standings_snippet_calls_compute_standings`
  - `test_active_next_round_rendered_with_team_names_and_date`
  - `test_active_round_count_format` (`{completed} / {total}` substring present)
  - `test_active_leaders_points_rendered_with_top_3`
  - `test_active_leaders_tags_rendered_with_top_3`
  - `test_active_leaders_ratio_rendered_with_top_3`
  - `test_active_player_leader_anchor_uses_raw_career_stats_href`
  - `test_active_view_all_leaders_anchor_uses_raw_leaders_href`
- `TestLeagueDashboardCompletedBranch`
  - `test_completed_renders_action_button_with_start_next_season_state`
  - `test_completed_next_round_rendered_as_all_fixtures_played`
  - `test_completed_round_count_equals_total_total`
- `TestLeagueDashboardNoneBranch`
  - `test_none_renders_no_season_notice_with_substring_no_season`
  - `test_none_action_button_label_is_no_season_and_state_is_none`
  - `test_none_all_body_dom_ids_absent` (the 7 active-branch body DOM ids ABSENT from rendered HTML)

### 8c. `matches/tests/test_season_dashboard_view.py` (Django `TestCase`)

DB-driven tests for `matches.views.season_dashboard`.

**Classes:**

- `TestSeasonDashboardRouting`
  - `test_get_returns_200_for_existing_season`
  - `test_get_returns_404_for_missing_season`
  - `test_post_returns_405`
  - `test_reverse_resolves_to_expected_path`
  - `test_template_used_is_seasons_dashboard_html`
- `TestSeasonDashboardStateMatrix`
  - `test_draft_renders_all_locked_dom_ids` (the always-present DOM ids + the standings snippet container; the active-only DOM ids ABSENT)
  - `test_active_renders_all_locked_dom_ids` (every locked DOM id present)
  - `test_completed_renders_all_locked_dom_ids` (every locked DOM id present)
  - `test_action_button_label_per_state` (draft ⇒ "Start Season", active ⇒ "Play Next", completed ⇒ "Start Next Season")
  - `test_action_button_state_data_attribute_per_state`
- `TestSeasonDashboardSidebar`
  - `test_sidebar_links_has_five_entries_in_pinned_order` (keys: `overview, standings, schedule, teams, history`)
  - `test_sidebar_active_is_overview`
  - `test_sidebar_standings_link_reverses_to_season_standings_url`
  - `test_sidebar_schedule_link_reverses_to_season_schedule_url`
  - `test_sidebar_teams_renders_as_disabled_span_no_href` (assert HTML contains `season-dashboard-sidebar-teams` on a `<span>` not an `<a>`)
  - `test_sidebar_history_renders_as_disabled_span_no_href`
- `TestSeasonDashboardBody`
  - `test_leaders_use_compute_leaders_pure_module` (single integration assertion: an active Season with a known PlayerRoundState row produces the expected top-1 LeaderRow)
  - `test_next_fixture_omitted_when_completed_and_all_played`
  - `test_player_leader_anchor_uses_raw_career_stats_href`
  - `test_view_all_leaders_anchor_uses_raw_per_season_leaders_href` (the `/seasons/{{ season.id }}/leaders/` placeholder)

### 8d. Test file ownership

| File | Status |
|---|---|
| `matches/tests/test_season_dashboard.py` | NEW (Tests agent) |
| `matches/tests/test_league_dashboard.py` | NEW (Tests agent) |
| `matches/tests/test_season_dashboard_view.py` | NEW (Tests agent) |

The Tests agent does NOT extend any existing test file in LG-01c. The
Code agent does NOT modify any test file. The Docs agent does NOT touch
production code or tests.

**Tests must NOT touch `simulate_scheduled_round`, `simulate_match`,
`save_games`, or any simulator entry point** — LG-01c runs no simulation.
Tests that exercise an `"active"` or `"completed"` Season do so by
hand-constructing the persisted `Match` + `GameRound` + `PlayerRoundState`
rows (using `Match.objects.create(...)` + `is_completed=True` + the
required `*_round1_*` / `*_round2_*` fields, mirroring the LG-01
simulator-test setup pattern). A test that accidentally enters the
simulator would be a scope leak and is locked out.

---

## 9. Out of scope (locked)

The following are **explicitly out of scope** for LG-01c and must not
be touched by any of the three parallel agents:

- **No new ORM model, no migration, no ADR write, no CONTEXT.md edit**
  (the `League` / `Season` / `Standings` glossary entries exist from
  LG-01; leader-stat terminology is documented inline in the pure
  module's docstring, not in CONTEXT.md).
- **No POST endpoint.** Both views are GET-only with explicit
  `HttpResponseNotAllowed(["GET"])` on non-GET. The placeholder
  `<button disabled>`s are HTML attribute disabling only — no `<form>`
  wrapper, no `csrf_token`, no view branch for `request.method == "POST"`.
- **No `Season.start_season()` UI wire-up** — that POST is LG-01d's.
- **No `simulate_scheduled_round` touch** — that's the LG-01d "Play
  Next" path. LG-01c does NOT import `BatchSimulator` anywhere.
- **No LG-01d / LG-01e / LG-01f / LG-01g logic** — Play Next, Start
  Next Season chaining, Teams tab, History tab are deferred.
- **No Teams view, no History view** — the sidebar entries are disabled
  placeholders.
- **No `/leagues/<id>/leaders/` or `/seasons/<id>/leaders/` URL mount**
  — the "View all leaders" anchors render raw `href`s that 404; this
  is the LG-01a deferred-broken-link precedent, locked.
- **No `/players/<id>/career-stats/` URL mount** — same raw-href
  precedent. (The route may exist as a separate `teams/` URL; LG-01c
  does NOT depend on whether it does — the test assertions check the
  literal href string, not URL resolution.)
- **No JS, no Chart.js, no htmx, no inline `<script>` blocks.** The
  template is server-rendered HTML only.
- **No API / DRF endpoint** — `/api/leagues/<id>/`, `/api/seasons/<id>/`
  and any REST surface for dashboards are deferred.
- **No new dependency** (no `pip install`, no `requirements.txt` edit).
- **No edit to `matches/models.py`** — read-only at LG-01c.
- **No edit to `matches/simulation.py`** — read-only at LG-01c.
- **No edit to `matches/standings.py`** — the LG-01 pure module is
  consumed verbatim.
- **No edit to `matches/schedule_generator.py`** — the LG-01 pure
  module is consumed verbatim.
- **No edit to `templates/seasons/standings.html`** — the LG-01
  standings page is unchanged; the new dashboards link to it via the
  sidebar but don't modify it.
- **No edit to `templates/seasons/schedule.html`** — same.
- **No edit to `templates/leagues/list.html`** — the LG-01a list page
  is unchanged; the new dashboards are reached from the existing
  `league-list-active-table` / `league-list-archived-table` per-row
  anchors (which already point at `/leagues/<id>/` as raw hrefs — LG-01a
  triage), so no template-side edit is required.
- **No edit to `templates/leagues/create.html`** — LG-01b unchanged.
- **No edit to `LeagueAdmin` / `SeasonAdmin`** — LG-01 admin registrations
  unchanged.
- **No `messages.success(...)` / `django.contrib.messages` usage.**
- **No simulation mechanics change → no Score Calibration re-baseline
  obligation.** LG-01c runs no simulator, consumes no RNG.

---

## 10. Locked Names (Recap)

Every public name LG-01c introduces or pins, in one place:

| Kind | Name | Notes |
|---|---|---|
| URL path | `/leagues/<int:league_id>/` | Inserted AFTER `create/` and BEFORE `""` in `matches/league_urls.py` |
| URL name | `league_dashboard` | Bare name, no `app_name`. `reverse("league_dashboard", args=[league.id])` |
| URL path | `/seasons/<int:season_id>/` | Inserted BEFORE `<int:season_id>/standings/` in `matches/season_urls.py` |
| URL name | `season_dashboard` | Bare name. `reverse("season_dashboard", args=[season.id])` |
| View | `matches.views.league_dashboard` | `(request: HttpRequest, league_id: int) -> HttpResponse`, no decorator, GET-only via `HttpResponseNotAllowed(["GET"])` |
| View | `matches.views.season_dashboard` | `(request: HttpRequest, season_id: int) -> HttpResponse`, no decorator, GET-only |
| Helper | `matches.views._build_dashboard_context` | Module-level flat helper, `(displayed_season: Season \| None, season_mode: str) -> dict`, returns the 11-key body context |
| Pure module | `matches/season_dashboard.py` | NEW; frozen import allowlist (`dataclasses, typing, collections` only) |
| Dataclass | `season_dashboard.LeaderRow` | `@dataclass(frozen=True)`, 8 fields: `player_id, player_name, role, team_id, team_name, value, games_played, rank` |
| Function | `season_dashboard.compute_leaders` | `(player_rounds: list[dict], stat: str, limit: int = 3) -> list[LeaderRow]` |
| Function | `season_dashboard.find_next_fixture` | `(fixtures, played_keys) -> ScheduleFixture \| None` |
| Function | `season_dashboard.round_progress` | `(fixtures, played_keys) -> tuple[int, int]` |
| Stat vocabulary | `"points_per_game"`, `"tags_per_game"`, `"tag_ratio"` | The only 3 strings `compute_leaders`'s `stat` parameter accepts |
| Seam dict | player-round 7 keys | `player_id, player_name, role, team_id, team_name, tags_made, times_tagged, points_scored` |
| Seam dict | `next_fixture` 7 keys | `matchday, round_number, team_a_id, team_a_name, team_b_id, team_b_name, date` |
| Season mode literal | `"draft"`, `"active"`, `"completed"`, `"none"` | The 4 strings `season_mode` takes; `"none"` is league-dashboard-only |
| Action button state literal | `"start_season"`, `"play_next"`, `"start_next_season"`, `"none"` | The 4 strings `action_button_state` takes |
| Sidebar key literal | `"overview"`, `"standings"`, `"schedule"`, `"teams"`, `"history"` | The 5 entries of `sidebar_links`; `"overview"` is the always-active LG-01c key |
| Template | `templates/leagues/dashboard.html` | NEW. Block title `{{ league.name }} — League` |
| Template | `templates/seasons/dashboard.html` | NEW. Block title `{{ season.league.name }} — {{ season.name }}` |
| DOM id (league) | `league-dashboard-header` | always |
| DOM id (league) | `league-dashboard-state-badge` | always |
| DOM id (league) | `league-dashboard-action-button` | always; `disabled` attr; `data-action-state` attr |
| DOM id (league) | `league-dashboard-standings-snippet` | draft/active/completed |
| DOM id (league) | `league-dashboard-next-round` | active/completed |
| DOM id (league) | `league-dashboard-round-count` | active/completed |
| DOM id (league) | `league-dashboard-leaders-points` | active/completed |
| DOM id (league) | `league-dashboard-leaders-tags` | active/completed |
| DOM id (league) | `league-dashboard-leaders-ratio` | active/completed |
| DOM id (league) | `league-dashboard-no-season-notice` | only `"none"`; substring `"No Season"` |
| DOM id (season) | `season-dashboard-header` | always |
| DOM id (season) | `season-dashboard-state-badge` | always |
| DOM id (season) | `season-dashboard-action-button` | always; `disabled` attr; `data-action-state` attr |
| DOM id (season) | `season-dashboard-sidebar` | always |
| DOM id (season) | `season-dashboard-sidebar-standings` | always; live `<a href>` to `season_standings` |
| DOM id (season) | `season-dashboard-sidebar-schedule` | always; live `<a href>` to `season_schedule` |
| DOM id (season) | `season-dashboard-sidebar-teams` | always; disabled `<span>` (no `<a href>`) |
| DOM id (season) | `season-dashboard-sidebar-history` | always; disabled `<span>` (no `<a href>`) |
| DOM id (season) | `season-dashboard-standings-snippet` | always |
| DOM id (season) | `season-dashboard-next-round` | active/completed |
| DOM id (season) | `season-dashboard-round-count` | active/completed |
| DOM id (season) | `season-dashboard-leaders-points` | active/completed |
| DOM id (season) | `season-dashboard-leaders-tags` | active/completed |
| DOM id (season) | `season-dashboard-leaders-ratio` | active/completed |
| Raw href (player) | `/players/{{ row.player_id }}/career-stats/` | Per-leader anchor — LG-01a precedent, broken-link tolerated |
| Raw href (leaders, league) | `/leagues/{{ league.id }}/leaders/` | "View all leaders" placeholder anchor |
| Raw href (leaders, season) | `/seasons/{{ season.id }}/leaders/` | "View all leaders" placeholder anchor |
| Test file | `matches/tests/test_season_dashboard.py` | NEW; pure-unit |
| Test file | `matches/tests/test_league_dashboard.py` | NEW; Django `TestCase` |
| Test file | `matches/tests/test_season_dashboard_view.py` | NEW; Django `TestCase` |
| Test class | `TestComputeLeadersEmpty` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersSinglePlayer` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersTiebreak` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersDeterministic` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersRoleMix` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersStatVocabulary` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersLimit` | in `test_season_dashboard.py` |
| Test class | `TestComputeLeadersDefensiveLastWins` | in `test_season_dashboard.py` |
| Test class | `TestFindNextFixture` | in `test_season_dashboard.py` |
| Test class | `TestRoundProgress` | in `test_season_dashboard.py` |
| Test class | `TestNoDjangoImportsLeaked` | in `test_season_dashboard.py` |
| Test class | `TestLeagueDashboardRouting` | in `test_league_dashboard.py` |
| Test class | `TestLeagueDashboardSeasonPick` | in `test_league_dashboard.py` |
| Test class | `TestLeagueDashboardDraftBranch` | in `test_league_dashboard.py` |
| Test class | `TestLeagueDashboardActiveBranch` | in `test_league_dashboard.py` |
| Test class | `TestLeagueDashboardCompletedBranch` | in `test_league_dashboard.py` |
| Test class | `TestLeagueDashboardNoneBranch` | in `test_league_dashboard.py` |
| Test class | `TestSeasonDashboardRouting` | in `test_season_dashboard_view.py` |
| Test class | `TestSeasonDashboardStateMatrix` | in `test_season_dashboard_view.py` |
| Test class | `TestSeasonDashboardSidebar` | in `test_season_dashboard_view.py` |
| Test class | `TestSeasonDashboardBody` | in `test_season_dashboard_view.py` |

---

**Seam summary in one sentence:** LG-01c adds `GET /leagues/<id>/` →
`matches.views.league_dashboard` and `GET /seasons/<id>/` →
`matches.views.season_dashboard`, both reading from the LG-01 foundation
(`Match.season`, `Season.starting_team_ids_json`, `compute_standings`,
`generate_schedule`, `League.active_season`) and from the NEW pure
module `matches/season_dashboard.py` (`LeaderRow`, `compute_leaders`,
`find_next_fixture`, `round_progress`); rendering `templates/leagues/dashboard.html`
and `templates/seasons/dashboard.html` with disabled placeholder action
buttons (no POST endpoints, deferred to LG-01d / LG-01e), raw-href
player career-stats and "View all leaders" anchors (LG-01a deferred
broken-link precedent), a 5-entry sidebar on the Season dashboard
(2 live links to LG-01 standings/schedule + 2 disabled placeholders +
the active Overview), and four-branch behaviour (`draft / active /
completed / none`) keyed off the displayed Season's state.
