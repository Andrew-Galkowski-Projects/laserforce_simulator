# LG-06h Seam Contract — League player page (per-Player, league-pinned detail)

**Status:** locked. Code / Tests / Docs agents work in parallel from this file and must not disagree on a single name.

**One-line summary:** A read-only **League player page** at `/leagues/<league_id>/players/<player_id>/` — the in-League destination of every player-name link on the 8 LG-06f league screens. Renders a header (with the LG-06f watch flag + an external link to the global HX-01 career page), a Regular-Season stats table (per-Season rows + a league-wide Career row, built VIEW-SIDE by reusing existing modules), a "Potential" placeholder, and 5 inline "coming soon" stub blocks for the model-less sections.

**Scope / non-goals:** read-only. NO model change, NO migration, NO simulator/RNG touch, NO Score Calibration re-baseline, NO new pure module, NO new ADR. The CONTEXT.md term **League player page** is ALREADY written — do NOT touch CONTEXT.md. PLAN.md / CLAUDE.md are the Docs agent's job — Code/Tests agents do not edit them. The watch toggle endpoint (`watch_list_toggle`, LG-06f) already exists — REUSE it, do NOT add a new one. The team-scoped `templates/teams/player_detail.html` already exists and is SEPARATE — do NOT edit or reuse it.

---

## 1. Public signatures (every new name)

### View — `matches/league_screens/player_detail.py` (NEW module)
```python
def player_detail(request: HttpRequest, league_id: int, player_id: int) -> HttpResponse:
    """LG-06h — read-only League player page pinned to one League."""
```
- GET-only. First line: `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`.
- LENIENT: any valid `(League, Player)` pair renders 200; league-scoped sections render an empty-state when the player has no Rounds in this League. NEVER 404 on "player not in league".

### URL — `matches/league_urls.py`
```python
path(
    "<int:league_id>/players/<int:player_id>/",
    league_screens.player_detail,
    name="league_player_detail",
),
```
- **Insertion point (locked):** among the existing `players/*` routes (after `players_free_agents` / `players_watch_list` / `watch_list_toggle`) and BEFORE the final `path("", league_views.league_list, name="league_list")` catch-all. The `<int:player_id>` converter is digit-only, so it does NOT shadow the literal `players/free-agents/`, `players/watch-list/`, `players/watch-list/toggle/` routes (those resolve first because Django is first-match AND the literals don't match a digits-only segment anyway). Bare URL name (no `app_name`). Full path: `/leagues/<int:league_id>/players/<int:player_id>/`.

### `__init__` re-export — `matches/league_screens/__init__.py`
- Add `from .player_detail import player_detail` and append `"player_detail"` to `__all__` (mirror how the other `league_screens` views are re-exported — see `watch_list` / `free_agents` etc.).

---

## 2. View body — exact step order (LOCKED)

Mirrors the shared LG-01z view contract (see `free_agents.py` / `player_stats.py`), with the LG-06h lenient extension.

1. **GET guard:** `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`.
2. `league = get_object_or_404(League, pk=league_id)`.
3. `player = get_object_or_404(Player, pk=player_id)`.  *(404 only on a missing League OR a missing Player — never on "player not in this league".)*
4. `request.session["last_league_id"] = league.id` (int — LG-01f session-write contract; after the 404 guards, before render).
5. `displayed_season = league.active_season or league.seasons.filter(state="completed").order_by("-id").first()`.
6. `sidebar_links = _build_league_sidebar_links(league, displayed_season, sidebar_active=None)` — NO sidebar entry matches this page, so `sidebar_active=None` (every entry renders inactive; tests assert zero active entries).
7. **Build the RS rows (per §3 loop):** one per-Season row per this-League Season the player has Rounds in, plus one league-wide Career row.
8. `return render(request, "leagues/player_detail.html", context)` with the §4 context keys.

**Lenient empty-state:** when the player has Rounds in zero of this League's Seasons, `rs_rows == []` and `career_row is None`; the template renders the `league-player-rs-stats-empty` notice in place of the `league-player-rs-stats-table`. (A current free agent with no League Rounds still renders 200 with the header, Potential placeholder, and all 5 stubs.)

---

## 3. RS stats aggregation loop (VIEW-SIDE, reusing existing modules)

NO new pure module. Reuse, by import:
- `_build_round_dicts` from `matches.league_screens.player_stats` — builds one plain dict per `PlayerRoundState` row from a `prs_filter` (the `game_round__…`-joined lookup dict).
- `aggregate_player_stats` from `matches.season_player_stats` — sums the count keys, averages mvp/accuracy, returns `list[PlayerStatRow]`.

### Per-Season rows (one aggregation pass per this-League Season the player has Rounds in)
For each `season` in `league.seasons.all()` (newest-first recommended — `order_by("-id")`):
1. `prs_filter = {"game_round__match__season": season, "player_id": player.id}` — the player + that Season scope on the `PlayerRoundState` join (mirror the `game_round__…` re-point in `player_stats.player_stats`).
2. `round_dicts = _build_round_dicts(prs_filter)`.
3. If `round_dicts` is empty ⇒ SKIP this Season (the player has no Rounds that Season — no row emitted).
4. `agg = aggregate_player_stats(round_dicts)` ⇒ a 1-element list (all rows are this one player) — take `agg[0]` as the `PlayerStatRow`.
5. **Per-Season "Team" derived from the player's actual Rounds that Season** (NOT current `Player.team`): take it from the aggregated row's `team_name` / `team_id` (which `_build_round_dicts` resolves per-Round from `game_round.team_red`/`team_blue` keyed on `team_color`, last-seen wins). This makes a dropped/left player show the team they played for that Season.
6. Emit one per-Season row dict (shape §4).

### Career-in-league row (one league-wide aggregation pass)
1. `prs_filter = {"game_round__match__season__league": league, "player_id": player.id}` — the LG-06d `…match__season__league=league` Career scope, filtered to this player.
2. `round_dicts = _build_round_dicts(prs_filter)`.
3. If empty ⇒ `career_row = None` (and `rs_rows == []` too, since no Season had Rounds) ⇒ empty-state.
4. Else `career_row = aggregate_player_stats(round_dicts)[0]` ⇒ build the Career row dict (shape §4), `year` label = `"Career"`, team derived from the league-wide last-seen (acceptable — it is the player's most-recent League team).

**Aggregation runs once per Season-with-rounds plus one league-wide pass.** Both passes consume `_build_round_dicts` → `aggregate_player_stats` verbatim (no recomputation of MVP/accuracy in the view — those come from `get_mvp` / `get_accuracy` inside `_build_round_dicts`).

---

## 4. Context keys (FROZEN) + row shapes

The view passes exactly these keys to `templates/leagues/player_detail.html`:

| Key | Type | Notes |
|---|---|---|
| `league` | `League` | the pinned League; needed for `{% url 'watch_list_toggle' league.id %}` in the flag partial + sidebar |
| `player` | `Player` | the subject; header reads `player.name`, `player.id` |
| `displayed_season` | `Season \| None` | the LG-01z displayed-Season pick (sidebar links target it) |
| `sidebar_links` | `list[dict]` | the 23-entry `_build_league_sidebar_links(...)` output |
| `sidebar_active` | `None` | LOCKED `None` — no sidebar entry matches this page |
| `rs_rows` | `list[dict]` | one per-Season row (newest-first); `[]` ⇒ empty-state |
| `career_row` | `dict \| None` | the league-wide Career row; `None` ⇒ empty-state |
| `stat_columns` | `tuple[tuple[str,str,bool], ...]` | the RS table's stat-column spec (see below) |

### Stat-column spec (`stat_columns`) — reuse the STAT portion of `_PLAYER_STATS_COLUMNS`
`_PLAYER_STATS_COLUMNS` lives in `matches.league_screens.player_stats` and is the `(sort_key, label, is_float)` tuple. For LG-06h the table is a SINGLE player, so:
- **DROP the `("name", "Name", False)` column** (column 0) — there is only one player.
- The view prefixes the table with **Year** + **Team** columns (rendered directly by the template), then iterates `stat_columns` for the stat cells.
- `stat_columns` = `_PLAYER_STATS_COLUMNS[2:]` (drop `name` at index 0 AND `team` at index 1 — Team is rendered as a dedicated prefix column derived per-Season; do NOT double-render it). i.e. start from `("games", "GP", False)` onward — the 15 entries `games, points_scored, mvp, tags_made, times_tagged, tag_ratio, accuracy, survival, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count`.
- The table is NOT sortable and NOT paginated (a single player across a handful of Seasons) — no `?sort=`/`?dir=`/`?per_page=` handling, no querystring helpers. (This is a deliberate simplification vs. the multi-player Player Stats screen.)

### Per-Season row dict shape (each entry of `rs_rows`) — LOCKED keys
```python
{
    "year": str,            # the Season label — Season.name (e.g. "Season 1")
    "season_id": int,       # for an optional link to the Season standings (template's discretion)
    "team_name": str,       # derived from the player's actual Rounds THAT Season (row.team_name)
    "team_id": int,         # derived likewise (row.team_id; 0 when unresolved)
    "games": int,           # PlayerStatRow.games
    "stats": Mapping[str, float],  # PlayerStatRow.stats — every STAT_KEYS + DERIVED_KEYS entry
}
```

### Career row dict shape (`career_row`) — same shape, `year = "Career"`
```python
{
    "year": "Career",       # literal label
    "season_id": None,      # no single Season
    "team_name": str,       # league-wide last-seen team (acceptable)
    "team_id": int,
    "games": int,
    "stats": Mapping[str, float],
}
```

**Empty-state signal:** `rs_rows == []` AND `career_row is None` ⇒ template renders `league-player-rs-stats-empty` instead of `league-player-rs-stats-table`.

---

## 5. Reused names (import-from) — confirmed by reading source

| Name | Import from | Confirmed |
|---|---|---|
| `_build_round_dicts` | `matches.league_screens.player_stats` | yes — `def _build_round_dicts(season_filter: dict) -> list[dict]` (player_stats.py:93) |
| `aggregate_player_stats` | `matches.season_player_stats` | yes — `def aggregate_player_stats(player_rounds) -> list[PlayerStatRow]` (season_player_stats.py:99) |
| `_PLAYER_STATS_COLUMNS` | `matches.league_screens.player_stats` | yes — module-level tuple (player_stats.py:72); column 0 is `("name", …)`, column 1 is `("team", …)` |
| `PlayerStatRow` | `matches.season_player_stats` | yes — frozen dataclass `(player_id, player_name, team_id, team_name, role, games, stats)` (season_player_stats.py:81) |
| `_build_league_sidebar_links` | `matches.league_views` | yes — `def _build_league_sidebar_links(league, displayed_season, sidebar_active) -> list[dict]` (league_views.py:1072), returns 23 entries |
| `League` | `matches.models` | yes |
| `Player` | `teams.models` | yes — `Player.name`, `Player.id` |
| `PlayerRoundState` | `matches.models` | yes (only needed transitively via `_build_round_dicts`; the view may not import it directly) |
| `player_career_stats` (URL name) | reverse only | yes — `/players/<id>/stats/` (teams/player_urls.py, bare name) — header external-link target |
| `watch_list_toggle` (URL name) | reverse only (inside the partial) | yes — LG-06f, `/leagues/<league_id>/players/watch-list/toggle/` |
| watch flag partials | template include | `_partials/watch_flag.html` + `_partials/watch_flag_script.html` |

**Watch flag context dependency:** the page context already provides `league` (for `{% url 'watch_list_toggle' league.id %}`) and `watched_player_ids` (via the `core.context_processors.watch_list` context processor — global, already registered). The view does NOT need to add `watched_player_ids` to its context. The partial needs ONLY `player_id` passed in.

**No correction needed** — every name in the task brief matches the real source.

---

## 6. Locked DOM ids + literals

Root + sections (template `templates/leagues/player_detail.html`):

| DOM id | Where | Presence |
|---|---|---|
| `league-player-detail` | root wrapper element | always |
| `league-player-header` | header block (player name + flag + external link) | always |
| `league-player-overall` | overall-rating / bio summary block | always |
| `league-player-potential` | Potential block | always; renders the literal `—` placeholder |
| `league-player-ratings` | current rating-attributes block (from `Player` fields) | always |
| `league-player-rs-stats-table` | the Regular-Season stats `<table>` | only when `rs_rows` non-empty OR `career_row` present |
| `league-player-rs-stats-empty` | empty-state notice | only when `rs_rows == []` AND `career_row is None` |
| `league-player-playoffs-stub` | Playoffs "coming soon" stub | always |
| `league-player-ratings-history-stub` | Ratings-history stub | always |
| `league-player-awards-stub` | Awards stub | always |
| `league-player-salaries-stub` | Salaries stub | always |
| `league-player-transactions-stub` | Transactions stub | always |

**Locked literals:**
- **Potential placeholder:** the literal string `—` (em-dash U+2014) rendered inside `league-player-potential`. (LG-05 owns the real field; none exists yet.)
- **Stub "coming soon" substring:** each of the 5 stub blocks (`*-stub`) contains the case-insensitive substring `Coming soon` (a heading + a short note). A stub MAY cite its blocking task (e.g. `arrives with LG-02`) — not test-load-bearing; the load-bearing assertion is the `Coming soon` substring + the stub's DOM id.
- **External career link:** the header contains an `<a href="{% url 'player_career_stats' player.id %}">` (the GLOBAL HX-01 career page). Load-bearing: the resolved `/players/<id>/stats/` href is present.
- **Watch flag:** `{% include "_partials/watch_flag.html" with player_id=player.id %}` in the header; `{% include "_partials/watch_flag_script.html" %}` exactly ONCE near the end of `{% block content %}` (NOT inside any loop).

**Template shell:** `templates/leagues/player_detail.html` (NEW) extends `base.html`, uses the `d-flex` + `{% include "_partials/league_sidebar.html" %}` shell like the other `templates/leagues/*.html` screens. `{% block title %}` recommended `{{ player.name }} — {{ league.name }}{% endblock %}` (em-dash U+2014; not test-load-bearing).

---

## 7. Owning module per name

| Name | Owning module / file |
|---|---|
| `player_detail` (view) | `matches/league_screens/player_detail.py` (NEW) |
| `league_player_detail` (URL) | `matches/league_urls.py` (one new `path(...)`) |
| `player_detail` re-export | `matches/league_screens/__init__.py` (1 import + `__all__` append) |
| `templates/leagues/player_detail.html` | NEW template |
| `_build_round_dicts` | `matches/league_screens/player_stats.py` (imported, unchanged) |
| `aggregate_player_stats` / `PlayerStatRow` | `matches/season_player_stats.py` (imported, unchanged) |
| `_PLAYER_STATS_COLUMNS` | `matches/league_screens/player_stats.py` (imported, unchanged) |
| `_build_league_sidebar_links` | `matches/league_views.py` (imported, unchanged) |
| watch flag partials | `templates/_partials/watch_flag.html` + `watch_flag_script.html` (included, unchanged) |
| `watch_list_toggle` | `matches/league_screens/watch_list.py` (REUSED — no new endpoint) |

---

## 8. 8-screen repoint table (player-name links → `league_player_detail`)

Repoint each player-name link from `player_career_stats` (global) to `league_player_detail` (in-League). Each template already has `league` in context. **Keep the watch-flag include verbatim** — only the `<a>` `href` changes. Statistical Feats currently has NO link (plain text) — WRAP its `{{ row.player_name }}` in a new `<a>`. Confirmed by reading each file:

| # | Template | Current player-name line (verbatim) | New `{% url 'league_player_detail' %}` call |
|---|---|---|---|
| 1 | `templates/leagues/player_stats.html` L110 | `<td>{% include "_partials/watch_flag.html" with player_id=row.player_id %} <a href="{% url 'player_career_stats' row.player_id %}">{{ row.player_name }}</a></td>` | `... <a href="{% url 'league_player_detail' league.id row.player_id %}">{{ row.player_name }}</a> ...` |
| 2 | `templates/leagues/player_ratings.html` L86 | `<td>{% include "_partials/watch_flag.html" with player_id=player.id %} <a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` | `... <a href="{% url 'league_player_detail' league.id player.id %}">{{ player.name }}</a> ...` |
| 3 | `templates/leagues/free_agents.html` L71 | `<td>{% include "_partials/watch_flag.html" with player_id=player.id %} <a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` | `... <a href="{% url 'league_player_detail' league.id player.id %}">{{ player.name }}</a> ...` |
| 4 | `templates/leagues/league_leaders.html` L57, L96, L135, L174 (4 board rows) | `<a href="/players/{{ row.player_id }}/stats/">{{ row.player_name }}</a>,` | `<a href="{% url 'league_player_detail' league.id row.player_id %}">{{ row.player_name }}</a>,` (repoint EACH of the 4 boards) |
| 5 | `templates/leagues/statistical_feats.html` L119 | `<td>{% include "_partials/watch_flag.html" with player_id=row.player_id %} {{ row.player_name }}</td>` (plain text, NO link) | `<td>{% include "_partials/watch_flag.html" with player_id=row.player_id %} <a href="{% url 'league_player_detail' league.id row.player_id %}">{{ row.player_name }}</a></td>` |
| 6 | `templates/leagues/team_roster.html` L57 AND L95 (BOTH sections) | `<td>{% include "_partials/watch_flag.html" with player_id=player.id %} <a href="{% url 'player_career_stats' player.id %}">{{ player.name }}</a></td>` | `... <a href="{% url 'league_player_detail' league.id player.id %}">{{ player.name }}</a> ...` (BOTH lines) |
| 7 | `templates/leagues/team_history.html` L163 | `<td>{% include "_partials/watch_flag.html" with player_id=p.player_id %} <a href="{% url 'player_career_stats' p.player_id %}">{{ p.name }}</a></td>` | `... <a href="{% url 'league_player_detail' league.id p.player_id %}">{{ p.name }}</a> ...` |
| 8 | `templates/leagues/watch_list.html` L107 | `<a href="{% url 'player_career_stats' row.player_id %}">{{ row.player_name }}</a>` | `<a href="{% url 'league_player_detail' league.id row.player_id %}">{{ row.player_name }}</a>` |

**Player-id expr per template (locked):** `player_stats` ⇒ `row.player_id`; `player_ratings` ⇒ `player.id`; `free_agents` ⇒ `player.id`; `league_leaders` ⇒ `row.player_id` (×4); `statistical_feats` ⇒ `row.player_id`; `team_roster` ⇒ `player.id` (×2); `team_history` ⇒ `p.player_id`; `watch_list` ⇒ `row.player_id`.

**DO NOT repoint** the sandbox/global surfaces — `templates/teams/player_list.html` and `templates/teams/player_detail.html` keep pointing at `player_career_stats` (they are league-agnostic).

**Inside the NEW `templates/leagues/player_detail.html`**, the header's EXTERNAL link points at `player_career_stats` (the global career page) — this is the one place in the league templates that intentionally still reverses `player_career_stats`.

---

## 9. Test boundary

Pin the test file: **`matches/tests/test_league_player_detail.py`** (NEW, Django `TestCase`).

**Tests assert (the seam — public behaviour):**
- **Routing:** `reverse("league_player_detail", args=[league.id, player.id])` resolves to `/leagues/<league_id>/players/<player_id>/`; GET ⇒ 200 + `assertTemplateUsed("leagues/player_detail.html")`.
- **405** on POST (and any non-GET).
- **404** on a bad `league_id` (missing League) AND on a bad `player_id` (missing Player).
- **Session write:** a successful GET sets `client.session["last_league_id"] == league.id`.
- **Lenient empty-state:** a Player with NO Rounds in this League (e.g. a current free agent, or a Player whose only Rounds are in another League) ⇒ 200, the `league-player-rs-stats-empty` notice present, `league-player-rs-stats-table` ABSENT, and the header + Potential + all 5 stubs still render.
- **RS rows present when rounds exist:** with persisted `Match` + `GameRound` + `PlayerRoundState` rows for the player across ≥1 Season ⇒ `league-player-rs-stats-table` present, one per-Season row per Season the player has Rounds in, plus the Career row (`year == "Career"`).
- **Per-Season Team derived from Rounds (not current `Player.team`):** construct a player who played for Team A in Season 1 but whose current `Player.team` is Team B (or the free-agent pool); assert the Season-1 row's team cell shows Team A.
- **Watch flag rendered + script once:** the header contains a `.watch-flag` button with `data-player-id="{{ player.id }}"`; the `watch_flag_script.html` `<script>` appears exactly once (assert a single occurrence of the once-bound marker, e.g. `__lfWatchFlagBound`).
- **External career link present:** the rendered HTML contains the resolved `/players/<player_id>/stats/` href (the global HX-01 page).
- **Potential placeholder:** `league-player-potential` contains the literal `—`.
- **All 5 stubs present:** each of `league-player-playoffs-stub` / `league-player-ratings-history-stub` / `league-player-awards-stub` / `league-player-salaries-stub` / `league-player-transactions-stub` is present and contains the `Coming soon` substring.
- **Sidebar rendered, no active entry:** `sidebar_links` has 23 entries; zero entries have `active=True` (since `sidebar_active=None`).

**Internal (NOT asserted at the seam):** the per-Season aggregation loop mechanics (how `_build_round_dicts` builds dicts, how `aggregate_player_stats` sums/averages) — those are already covered by the existing `season_player_stats` / `player_stats` tests; LG-06h tests assert the RENDERED rows, not the loop internals.

**Blast radius — existing tests on the 8 screens.** Any existing test that asserts a player-name link target on the 8 LG-06f screens (i.e. asserts the link reverses `player_career_stats` or contains `/players/<id>/stats/`) will need updating to the new `league_player_detail` route (`/leagues/<league_id>/players/<player_id>/`). The Tests agent must grep the existing `matches/tests/test_league_*.py` files for `player_career_stats` / `/players/` / `/stats/` link assertions on these 8 screens and repoint them. (Assertions on the NEW `player_detail.html` header's EXTERNAL link, and on the sandbox `teams/` templates, stay pointing at `player_career_stats`.)
