# matches: League sidebar screens

LG-01z screens, LG-06f watch list + watch flag, LG-06g standings form, LG-06h league player page.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-01z league sidebar screens

Turns 17 of the LG-01h "coming soon" league-sidebar placeholders into real
pages: **11 live read-only screens** + **6 explainer stubs** for features
still blocked on unbuilt models. All read-only — **no model change, no
migration, no simulator touch, no RNG, no Score Calibration re-baseline.**
Per-screen status + blockers in [`sub-plan.md`](../../../sub-plan.md); seam
contract at [`.claude/worktrees/lg-01z-seam-contract.md`](../../../.claude/worktrees/lg-01z-seam-contract.md).

**Package `matches/league_screens/`** (NEW) — one module per screen, each
exposing a single GET-only view `<key>(request, league_id)` re-exported from
the package `__init__`. The 11 views: `power_rankings`, `team_roster`,
`team_history`, `free_agents`, `watch_list`, `game_log`, `league_leaders`,
`player_ratings`, `player_stats`, `team_stats`, `statistical_feats`. Each
follows the shared contract: GET-guard (`HttpResponseNotAllowed(["GET"])`) →
`get_object_or_404(League)` → `request.session["last_league_id"] = league.id`
→ `displayed_season = league.active_season or league.seasons.filter(
state="completed").order_by("-id").first()` → `sidebar_links =
_build_league_sidebar_links(league, displayed_season, sidebar_active="<key>")`
→ aggregation → render `templates/leagues/<key>.html` (the `d-flex` +
`_partials/league_sidebar.html` shell). Empty-state (no Season) renders a
`<key>-empty-notice` with the substring `"No Season"`. **Watch List** is the
one exception to GET-only: a `?action=add|remove&player_id=<id>` GET toggle
mutates `request.session["watch_list"]` (a list of int ids) then redirects to
the bare URL — a browser-session-local convenience list at the time. *(Superseded:
LG-06f replaced the GET toggle with a POST flag; ANG-01e moved the list to the
per-Account `WatchedPlayer` model — see **ANG-01e league screens API**.)*

**Pure logic modules** (NEW, top-level `matches/`, frozen `dataclasses` /
`typing` / `collections` import allowlist, each with a `TestNoDjangoImportsLeaked`
subprocess check): `power_rankings_logic.py` (composite power score = sum of
three per-League min-max-normalized components — team mean `overall_rating`,
win%, avg per-Round score diff; `compute_power_rankings` + `sort_power_rankings`
with `SORT_KEYS` for the sortable columns, rank preserved as the canonical
power-score position), `league_leaders_logic.py` (`compute_leaderboards` → 4
top-N boards: avg tags / avg score / fewest-tagged / tag ratio; reuses the
LG-01c `LeaderRow`), `season_player_stats.py` (per-player HX-01 STAT_KEYS
aggregation — counts summed, `mvp`/`accuracy` averaged), `team_stats_logic.py`
(`aggregate_team_stats` over per-round + per-event dicts; event→column mapping
`base_capture`/`missiled`(+`hit`)/`special`-nuke-detonation/`nuke_cancelled`),
`team_history_logic.py` (`compute_overall_record` / `compute_season_rows` /
`compute_player_rollups` — green = currently on team, blue = now elsewhere,
derived from `PlayerRoundState` history vs current `Player.team`), and
`stat_feats.py` (9 feat predicates → `FeatRecord`s, deep-linking to the Round).
Screens with trivial aggregation (Game Log, Player Ratings, Free Agents, Team
Roster, Watch List) keep their logic in the view and reuse the LG-00c sort
helpers (`teams.views._coerce_sort` / `_coerce_dir` / `_SORT_KEYS`). *(ANG-01e:
every screen's query + row building now lives in
`matches/league_screen_services.py`; the views only parse params and render.)*

**Central wiring** (`matches/league_urls.py` + `matches/league_views.py::
_build_league_sidebar_links` + `matches/views.py::_FEATURE_REGISTRY`): each
live screen gets a real `path("<int:league_id>/…/", league_screens.<view>,
name="<url_name>")` route (URL names `league_power_rankings`, `team_roster`,
`team_history`, `players_free_agents`, `players_watch_list`, `stats_game_log`,
`stats_league_leaders`, `stats_player_ratings`, `stats_player_stats`,
`stats_team_stats`, `stats_statistical_feats`); the sidebar builder's entry is
repointed from `_cs("coming_soon_<x>")` to `_cs("<url_name>")` — and because
`_build_league_sidebar_links` is the **single source of truth** for both the
LG-01f sidebar AND the LG-01k topbar, both surfaces flip live at once. The 11
now-live `coming_soon_*` routes + `_FEATURE_REGISTRY` entries are removed,
leaving the registry at **17 entries** (7 still-blocked LEAGUE/TEAM/PLAYERS
placeholders + 6 Help + 4 Tools). The 7 blocked placeholder entries gain a
`blocker` string, which `coming_soon` passes to `_placeholder.html`. **UXP-12:** the
template no longer renders it (it was developer-facing; the registry keeps it as
documentation), nor the `coming-soon-feature-key` debug line. Sidebar / top-bar entries carry a
`placeholder` flag (`_PLACEHOLDER_SIDEBAR_KEYS` in `league_views.py`: trade, trading_block,
prospects, hall_of_fame) and render greyed (`text-muted`) with a `placeholder-soon-badge` "Soon"
badge; every Tools / Help item gets the same treatment.

**Tests:** `matches/tests/test_league_<screen>.py` per screen (view 200 / 405 /
404 / session-write / empty-state / DOM ids + sort/toggle behaviour, plus
pure-unit + purity classes for the logic modules). The LG-01h
`test_coming_soon.py` (registry now 17, value-dicts may carry `blocker`)
and `test_league_sidebar.py` (6 entries point at live URLs) were updated as
blast-radius. A pre-existing RNG-flaky `test_batch_sim.py::TestBugfixMedicHits
Tracked` (unseeded 95%-hit) was pinned deterministically with the file's own
`patch("random.randint", return_value=1)` idiom.

**LG-06a polish.** Free Agents / Player Ratings / Player Stats (already
paginated view-side) gained the 10/25/50/100 page-size `<select>` (LG-01f
`history.html` precedent) via a `per_page_options` context key fed from the
shared `_LG01F_PER_PAGE_OPTIONS`; Team History gained `Paginator` wiring on the
**Players section only** (`page_obj` / `paginator` /
`players_querystring_without_page`, carries `team_id` + omits `page`), Overall +
Seasons untouched. The per-page `<form>` preserves other params via hidden
inputs (`sort`+`dir`, or `team_id` on Team History) and omits `page` to reset to
page 1; new DOM ids `<screen>-per-page-form` / `-select` +
`team-history-players-pagination`. UI-only, no model/simulator change. Seam
contract: [`.claude/worktrees/lg-06a-seam-contract.md`](../../../.claude/worktrees/lg-06a-seam-contract.md).

**LG-06b polish.** Player Ratings / Player Stats / Statistical Feats gained an
"All Teams" + per-enrolled-team `?team_id=<id>` `<select>` via a shared
validator `league_views._coerce_team_id(raw, enrolled_ids)` (mirrors
`_coerce_per_page`; the single source for all three) returning the int id iff it
parses **and** is enrolled, else `None` (All Teams). Each view adds
`enrolled_teams` (`displayed_season.teams.order_by("name")`) + `selected_team_id`.
*(ANG-01e: enrolment is now `league_screen_services.enrolled_teams` /
`enrolled_team_ids` — the activation snapshot, live M2M only for a draft.)*
Filter points differ: Ratings filters the queryset (`qs.filter(team_id=…)`, now
inside `build_player_ratings_queryset`), Stats filters rows post-`aggregate_player_stats` on
`PlayerStatRow.team_id`, Feats filters the seam **inputs** before
`stat_feats.scan_feats` (`player_rounds` by `team_id`, `matches` by red/blue id)
— `stat_feats.py` untouched. `team_id` carries in both querystring helpers + a
hidden per-page-form input on the two paginated screens (picker form omits
`page`); Feats has no pagination/sort. DOM ids
`{player-ratings,player-stats,statistical-feats}-team-filter-{form,select}`.
UI-only, read-only, no model/simulator change. Seam contract:
[`.claude/worktrees/lg-06b-seam-contract.md`](../../../.claude/worktrees/lg-06b-seam-contract.md).

**LG-06c polish.** Team History / Game Log / League Leaders / Watch List /
Statistical Feats gained the LG-00c sortable-column-header pattern, sorting
**view-side** with in-memory `sorted(key=…, reverse=(dir=="desc"))` on the
already-materialized rows — the pure modules `stat_feats.py` /
`team_history_logic.py` / `league_leaders_logic.py` (incl. `LeaderRow`, whose
`rank` stays the frozen metric standing) are untouched, sorted on their OUTPUT.
The single new shared helper `league_views._coerce_sort_key(raw, allowed,
default)` (returns `raw` iff in the `allowed` frozenset, else `default`; mirrors
`_coerce_per_page` / `_coerce_team_id`) is the sole source of sort-key
coercion; `teams.views._coerce_dir` is imported and reused verbatim. Multi-table
screens namespace their params so one table never resets a sibling — Team
History (`players_sort`/`players_dir`, `seasons_sort`/`seasons_dir`) and League
Leaders (per-board `<board>_sort`/`<board>_dir` across the four
`avg_tags`/`avg_score`/`fewest_tagged`/`tag_ratio` boards) — while single-table
screens (Game Log, Watch List, Statistical Feats) use one `?sort=&dir=`. On the
LG-06a-paginated Team History Players table the sort runs BEFORE `Paginator`
(global top row leads), with the extended `players_querystring_without_page`
carrying `players_sort`/`players_dir` on pagination links and a sibling
`players_querystring_without_sort_page` backing the headers so a sort change
resets to page 1. Sort coexists with the existing `?team_id=` filters on Game
Log + Statistical Feats; Team History's Overall `dl` stays unsorted. Key tuples
are `None`-safe with a per-screen secondary tiebreak; new DOM ids
`<screen>[-<table>]-th-<key>` with ` ↑`/` ↓` glyphs on the active header.
UI-only, read-only, no model/simulator change. Seam contract:
[`.claude/worktrees/lg-06c-seam-contract.md`](../../../.claude/worktrees/lg-06c-seam-contract.md).

**LG-06d polish.** Player Stats / Team Stats / League Leaders / Statistical Feats
/ Game Log / Power Rankings gained a `?season=` selector listing each of this
League's Seasons newest-first plus a **Career** entry (aggregate across all of
THIS League's Seasons); no `?season=` param keeps the current `displayed_season`
(backward-compatible). Two new shared coercers in `league_views.py` mirror the
`_coerce_per_page` / `_coerce_team_id` forgiving precedent:
`_coerce_season(raw, valid_season_ids, default)` (returns the literal `"career"`
sentinel iff `raw == "career"`, else the int id iff it parses **and** is in the
valid set, else the caller's `default` = the `displayed_season` id or `None`) and
`_coerce_rate(raw, default="total")` (returns one of the locked literals
`"total"` / `"per_game"` / `"per_10"`, else default). Career is a **view-side
queryset switch** — each screen swaps its round/match filter from
`...match__season=<season>` to `...match__season__league=league` and reuses its
existing pure aggregation module **verbatim** (`aggregate_player_stats`,
`team_stats_logic.aggregate_team_stats`, `league_leaders_logic.compute_leaderboards`,
`stat_feats.scan_feats`, the Game Log in-view round-row build,
`power_rankings_logic.compute_power_rankings` are all indifferent to one-season
vs. all-seasons, consuming a flat list of per-Round/per-Match dicts). Player
Stats additionally gained a `?rate=` toggle (Totals / Per Game / **Per 10 min** —
the laser-tag analogue of ZenGM's Per-36) via a new pure fn
`season_player_stats.apply_rate(rows, rate)` that transforms the summed count
columns **only** (`SUMMED_KEYS`); MVP / Acc% / Tag Ratio / Survival
(`AVERAGED_KEYS` + `DERIVED_KEYS`) and `games` pass through untouched. Per-10
denominator = the player's total uptime, `stats["survival"] * games` (survival is
the per-Round mean survival-seconds, so ×games rebuilds summed uptime), i.e.
`value * 600 / total_uptime_seconds` with a `<= 0` → `0.0` guard; per-game =
`value / games`. The Player Stats pipeline is `aggregate_player_stats` →
`apply_rate` → `team_id` filter → `sort_player_stats` → `Paginator`, so sorting
runs on the **rate-adjusted** displayed value (ZenGM behaviour). `season` (and
`rate` on Player Stats) is carried through every querystring helper, hidden
per-page / team-filter form input, and sort-header href on the touched screens;
changing `season` or `rate` omits `page` to reset to page 1 (LG-06a/b/c
precedent). New DOM ids `<screen>-season-filter-{form,select}` (prefixes
`player-stats`, `team-stats`, `league-leaders`, `statistical-feats`, `game-log`,
`power-rankings`) plus `player-stats-rate-{form,select}`. **Team History is
excluded** — it is natively all-time and its Seasons tab already is the
per-season view, so a season selector would be redundant there. UI-only,
read-only — no model, migration, simulator, RNG, or Score Calibration
re-baseline (GLOSSARY.md gained the **Per-10-minute rate** + **Career view
(league-scoped)** terms; no ADR). Cross-cutting **C1 / C2 / C7**. Seam contract:
[`.claude/worktrees/lg-06d-seam-contract.md`](../../../.claude/worktrees/lg-06d-seam-contract.md).

**LG-06e polish.** Statistical Feats was reshaped from a `<ul>` of ~9
category-best entries into ZenGM's **per-game feed**: one sortable row per
(player, round) that achieved a feat, with that round's box-score line +
Opp / Result / Season, deep-linking to the Round. The pure module
`stat_feats.py` had its OUTPUT SHAPE rewritten — the 9-finder /
single-`FeatRecord` design is gone; `scan_feats(player_rounds, matches) ->
tuple[list[FeatRow], list[TeamFeatRecord]]` now emits one frozen `FeatRow` per
qualifying (player, round), each carrying a `stats` mapping over the new pinned
`BOX_SCORE_KEYS` (the 12 `season_player_stats.STAT_KEYS` per-round PLUS
`nuke_detonations`), view-computed Opp / per-Round Result / Season descriptors,
and a non-empty stacked `feats` tuple of `FeatBadge(kind, label, is_season_best)`.
**Hybrid qualification:** a row is included iff it crosses ANY per-game threshold
(`TRIPLE_NUKE_THRESHOLD=3`, `HIGH_TAGS_THRESHOLD=20`, `HIGH_POINTS_THRESHOLD=12000`,
`HIGH_MVP_THRESHOLD=15`, `HIGH_RESUPPLIES_THRESHOLD=20`, `HIGH_MISSILES_THRESHOLD=8`,
plus boolean `medic_shutout` / `perfect_heavy`) OR is a season-best leader for any
of the 5 `SEASON_BEST_STATS` (each yields exactly one guaranteed leader row,
tiebreak highest value -> highest `round_id` -> lowest `player_id`, all-zero-max
skipped) tagged `is_season_best=True`; a row both crossing a threshold AND leading
its kind collapses to ONE badge, season-best winning. Feat kinds are pinned in
`FEAT_KINDS` (8 `(kind, label)` pairs). `comeback_win` moved OUT of the feed into
a separate **Team feats** section — `find_comeback_win(matches) ->
list[TeamFeatRecord]` (return type changed from `Optional[FeatRecord]`; detection
logic unchanged). `scan_feats` guarantees a deterministic default order
(`round_id` DESC, then `player_id` ASC); module stays Django-free
(`TestNoDjangoImportsLeaked` retained). The view materialises the extended
per-(player,round) seam dicts — Opp / Result / Season computed **view-side**, with
**per-Round** Result from `GameRound.red_points`/`blue_points` (NOT the Match
outcome), `mvp = float(prs.get_mvp)` (property) and
`accuracy = float(prs.get_accuracy())` (**method, call with `()`**), and
`nuke_detonations` from the existing `event_type="special"`/`points_awarded=500`
detonation pass — then layers **LG-06a pagination** (`Paginator` AFTER sort,
`_LG01F_PER_PAGE_OPTIONS`) and **expanded LG-06c sort** over the full box-score
column set (`_FEATS_SORT_KEYS` frozenset of every descriptor + 13 box-score keys,
`_FEATS_SORT_KEYS_DISPLAY`, the `feat_row_sort_value` extractor — ANG-01e moved it,
`is_nuke_detonation` and `FEATS_SORT_KEYS` to `league_screen_services.py`,
`teams.views._coerce_dir` reused) with **default sort = most recent first**
(`("round", "desc")`) and a `(round_id desc, player_id asc)` secondary tiebreak;
the Team-feats list is not paginated. Coexists with the LG-06b `?team_id=` filter
(applied to the seam INPUTS, `stat_feats.py` untouched by the filter) + the LG-06d
`?season=` selector (incl. Career); changing season/team/sort/per-page omits
`page`. The template `statistical_feats.html` was rewritten into the sortable
`statistical-feats-table` (DOM ids `statistical-feats-th-<key>` per column with
` ↑`/` ↓` glyphs, `stat-feat-badge-<kind>` badges with a `(season best)` /
`season-best` suffix, `statistical-feats-per-page-{form,select}` / `-pagination`)
plus the separate `statistical-feats-team-feats` section (`stat-team-feat-<kind>`),
preserving the LG-06b/d filter ids + the `stat-feats-empty-notice`. Read-only — no
model, migration, URL, simulator, RNG, Score Calibration re-baseline, GLOSSARY.md
edit (the **Statistical feat** term was already finalized), or ADR. Cross-cutting
Statistical-Feats reshape. Seam contract:
[`.claude/worktrees/lg-06e-seam-contract.md`](../../../.claude/worktrees/lg-06e-seam-contract.md).

## LG-06f watch list (+ watch flag)

The Watch List screen was reshaped from the 3-column bookmark table into the
**Player-Stats column set filtered to watched players** (zero-fill for watched
players with no Rounds in scope), a ZenGM-style **watch flag** landed on **8
league screens**, and watch lists became **per-League** in the browser session.
UI-only — **no model, migration, simulator, RNG, or Score Calibration
re-baseline** (GLOSSARY.md gained the **Watch list** + **Watch flag** terms; no
ADR). Seam contract:
[`.claude/worktrees/lg-06f-seam-contract.md`](../../../.claude/worktrees/lg-06f-seam-contract.md).

> **Superseded in part by ANG-01e.** The watch list is no longer in the session: it is the
> `WatchedPlayer` model, one row per (Account, League, Player), read and written only through
> `league_screen_services` (`watched_player_ids` / `add_watch` / `remove_watch` /
> `clear_watch_list`). `league_views._watched_player_ids(request, league_id)` is now a thin wrapper
> (anonymous ⇒ `set()`); the context processor imports `league_screen_services.watched_player_ids`
> directly. The toggle also returns **400 `{"error": "player not in league"}`** for a Player on no
> enrolled Team of the displayed Season and not in the free-agent pool. **Remove All** is a POST to
> `watch_list_clear` (`/leagues/<id>/players/watch-list/clear/`); `GET ?action=clear` is gone. The
> screen runs `build_watch_list_rows` (the pipeline below, moved). The session-store details below are
> history.

**Per-League session store.** `request.session["watch_lists"]: dict[str,
list[int]]` keyed by `str(league_id)`, value an ordered list of watched Player
ids (e.g. `{"3": [12, 47], "8": [12]}`). The pre-LG-06f global singular
`request.session["watch_list"]` key is **ABANDONED** — no migration, no
read-compat, no fallback (session data is disposable, ADR-0004 precedent). The
single source-of-truth reader `league_views._watched_player_ids(request,
league_id) -> set[int]` (alongside `_coerce_per_page` / `_coerce_team_id` /
`_coerce_season`) reads `session["watch_lists"].get(str(league_id), [])`, coerces
each entry to int (silently dropping non-ints), never raises (missing key ⇒
`set()`), and is consumed by BOTH the context processor AND the screen view.

**Context processor.** `core.context_processors.watch_list(request) ->
{"watched_player_ids": set[int]}` (alongside `league_nav` / `app_mode`,
lazy-importing `league_screen_services.watched_player_ids` since ANG-01e, to dodge
the `core ↔ matches` apps cycle)
resolves `league_id` from `request.resolver_match.kwargs` defensively — no
`resolver_match` or no `league_id` kwarg (off-League, a pre-resolution 404, or a
`RequestFactory()` request) ⇒ `{"watched_player_ids": set()}`. **Registered
immediately AFTER `core.context_processors.app_mode`** in
`settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`, so every league-screen
template sees `watched_player_ids` with zero per-view wiring.

**Toggle endpoint.** `matches.league_screens.watch_list.watch_list_toggle(
request, league_id) -> JsonResponse` — **POST-only** (`HttpResponseNotAllowed(
["POST"])` first line), CSRF-protected (NOT `@csrf_exempt`), re-exported from
`league_screens/__init__`. URL name `watch_list_toggle`, route
`/leagues/<int:league_id>/players/watch-list/toggle/` (inserted right after the
`players_watch_list` route in `league_urls.py`). Step order: `get_object_or_404(
League)` → coerce `player_id` from POST (invalid ⇒ 400 `{"error": "invalid
player_id"}`) → `Player.objects.filter(pk=...).exists()` (unknown ⇒ 400 `{"error":
"unknown player_id"}`, a locked decision — not a silent no-op) → flip membership
(ANG-01e: `remove_watch` / `add_watch`, the latter 400 `player not in league`;
originally `watch_lists[str(league_id)]` + `session.modified = True`) → return `{"watched":
bool, "player_id": int}` (`watched` is the NEW post-flip state). Per-League
isolation falls out of the `str(league_id)` key — toggling in League A never
touches League B's list.

**Pure zero-fill helper.** `season_player_stats.zero_fill_watched(rows,
watched_ids, identity_by_id) -> list[PlayerStatRow]` (alongside
`aggregate_player_stats` / `apply_rate` / `sort_player_stats`, **no new imports** —
the module's frozen no-Django allowlist is preserved) keeps only aggregated rows
whose `player_id in watched_ids`, then appends a zero row for each watched id with
no Round in scope. A zero row carries `games=0` and `stats={k: 0.0 for k in
STAT_KEYS + DERIVED_KEYS}` with `player_name` / `team_id` / `team_name` / `role`
from `identity_by_id[pid]`; a watched id absent from `identity_by_id` (e.g. a
deleted Player) is **silently skipped**. Output order is locked: aggregated rows
first (incoming order), then zero rows in **ascending player-id order**
(`sorted(missing_ids)`) — deterministic for the unit test even though downstream
`apply_rate` / `sort_player_stats` may re-order.

**Rewritten screen view.** `watch_list(request, league_id)` was GET-only EXCEPT
the retained `?action=clear` branch (ANG-01e: removed — it is now GET-only and Remove
All POSTs to `watch_list_clear`). The legacy
`?action=add|remove` branches and the add-form are GONE. The view reuses the
player_stats machinery — since ANG-01e via `league_screen_services.build_watch_list_rows`
over `build_round_dicts` — with the **locked pipeline**
`build_round_dicts → aggregate_player_stats → zero_fill_watched → apply_rate →
sort_player_stats → Paginator` (`zero_fill_watched` runs BEFORE `apply_rate` so
zero rows take the same rate pass-through, where the `games <= 0 ⇒ 0.0` guard
leaves zeros as zeros). `identity_by_id` is built view-side from
`Player.objects.filter(pk__in=watched_ids).select_related("team")` as `{pid:
{"player_name","team_id","team_name","role"}}`. The screen carries the full
Player-Stats kit **minus the team filter** (the Watch List is a personal
cross-team set) — season selector (+ Career) via `_resolve_season_scope`, rate
toggle via `_coerce_rate`, per-page via `_coerce_per_page` / `_coerce_page`,
sortable columns via `coerce_sort` / `coerce_dir` / `sort_player_stats` — with
new DOM ids `watch-list-{per-page,season-filter,rate}-{form,select}` /
`watch-list-th-{key}` / `watch-list-pagination` mirroring `player-stats-*`,
preserving `watch-list-table`, `watch-list-empty-notice` (the `"No Season"`
substring branch retained) and `watch-list-remove-all` (Remove All; ANG-01e made
it the submit button of the POST form `watch-list-remove-all-form`), and
`sidebar_active="watch_list"`. **Removed ids:**
`watch-list-add` / `-select` / `watch-list-row-{id}` (add-form + old 3-column rows
gone); a per-row flag replaces the per-row Remove control.

**Flag partials + 8-screen surface.** `templates/_partials/watch_flag.html`
renders a `<button type="button" class="watch-flag">` (`.watch-flag-on` added when
`player_id in watched_player_ids`, red styling keys off that class) with
`data-player-id` + `data-toggle-url="{% url 'watch_list_toggle' league.id %}"` and
**NO unique `id`** (League Leaders and Statistical Feats can render the same
player on multiple rows — a per-row id would collide). Include contract:
`{% include "_partials/watch_flag.html" with player_id=<id> %}` (the partial reads
`watched_player_ids` + `league.id` from context). `watch_flag_script.html` carries
a single **delegated** click `<script>` (bound once via `closest(".watch-flag")`),
included **exactly once per page**, that fetch-POSTs to `btn.dataset.toggleUrl`
with the `X-CSRFToken` cookie header and a `player_id` body, then on the response
adds/removes `.watch-flag-on` on **all** buttons sharing that `data-player-id`
(handles the multi-row case). The flag wires into the player-name cell of **8
screens** — `player_stats`, `player_ratings`, `free_agents`, `league_leaders`
(all 4 boards), `statistical_feats`, `team_roster` (both sections),
`team_history`, and the rewritten `watch_list` — adjacent to the existing
career-stats link (the link is kept). **Tests:**
`matches/tests/test_watch_flag.py` (partial render + context-processor
resolution), `matches/tests/test_watch_toggle.py` (endpoint add/remove,
per-League isolation, 405/400/404, CSRF), and
`matches/tests/test_league_watch_list.py` (screen + the pure `zero_fill_watched`
unit tests). The league-pinned **career-page** flag was split off to **LG-06h**
(the global `/players/<id>/stats/` page is league-agnostic, so its flag has no
League to toggle against).

## LG-06g standings form + side detail

The LG-01 Season **Standings** table gained **8 form / side-detail columns** and
every column became sortable (LG-06c pattern). Read-only — **no model, migration,
URL, simulator, RNG, or Score Calibration re-baseline; no ADR**; GLOSSARY.md
carries the new **Standings form** + **Side split** terms. Seam contract:
[`.claude/worktrees/lg-06g-seam-contract.md`](../../../.claude/worktrees/lg-06g-seam-contract.md).

**Pure module (`matches/standings.py`, extended in place).** `StandingsRow` grew
from 9 to **17 fields** — the 8 new ones appended after `rank` (pinned order):
`match_streak: tuple[str,int]`, `match_l5: tuple[int,int,int]`,
`round_streak: tuple[str,int]`, `round_l5: tuple[int,int,int]`,
`red_wlt: tuple[int,int,int]`, `blue_wlt: tuple[int,int,int]`,
`red_points_for: int`, `blue_points_for: int`. The dataclass holds **structured
numerics only** (streak as `(kind, length)` where `kind ∈ {"W","L","T",""}`;
records/L5 as `(W,L,T)`); the template formats display strings and the view
derives sort keys. `compute_standings(completed_matches, enrolled_teams,
season_rounds)` gained the 3rd param — the Match dict is now **9 keys** (adds
`date_played`) and `season_rounds` is a **6-key** dict (`round_id, team_red_id,
team_blue_id, red_points, blue_points, date_played`). The frozen import allowlist
(`dataclasses`/`typing`/`collections`) is unchanged (`TestNoDjangoImportsLeaked`
still passes — `date_played` values cross the seam as already-comparable dict
values, never imported).

**Two corpora (by design).** Match-grain — existing `wins/losses/ties/
league_points/round_wins/total_score` + `match_streak` + `match_l5` — read the
**completed-Match** corpus. Round-grain — `round_streak`, `round_l5`, and all
four side-split columns — read **every persisted Season Round** including Rounds
of in-progress (`is_completed=False`) Matches. Streak runs from the most recent,
both grains ordered chronologically by `(date_played, id)` asc; L5 is `(W,L,T)`
over the last ≤5.

**Side split is per PHYSICAL side.** Read straight off `GameRound.team_red`/
`team_blue` (the actual physical sides, SIM-08) + `red_points`/`blue_points` —
**never** the Match-level `red_*`/`blue_*` fields (team-position-keyed:
`Match.red_round2_points` is team_red's points while it physically played BLUE in
R2). Round result: red wins iff `red_points > blue_points`, blue iff the reverse,
tie iff equal. `red_wlt`/`red_points_for` aggregate Rounds the team physically
held red, `blue_*` symmetric; a team aggregates into both across the Season.
`round_streak`/`round_l5` are the team's own side-agnostic W/L/T.

**All 17 columns sortable (`matches/league_views.py::season_standings`).**
View-side sort on the materialized rows after `compute_standings`, via
`_coerce_sort_key` (new `_STANDINGS_SORT_KEYS` frozenset of 17 keys; default
`("rank","asc")` ⇒ no `?sort` renders today's order) + `teams.views._coerce_dir`
(newly imported). Helpers `_standings_sort_value` / `_streak_sort_value` /
`_standings_row_attr` (attr-or-key adapter so the draft-preview dict rows sort
through the same path); record/L5 sort `(wins desc, losses asc)`, streaks by
signed run length, **`rank` stays frozen** (never renumbered — LG-06c
League-Leaders precedent). The view builds `season_rounds` from
`GameRound.objects.filter(match__season=season).values(…)`, adds `date_played`
to the Match dicts, and adds context keys `sort` / `dir` / `sort_keys`
(= `_STANDINGS_SORT_KEYS_DISPLAY`) / `querystring_without_sort_dir`; the
draft-preview branch emits the 8 new fields zeroed and still sorts. Template
`templates/seasons/standings.html` swapped its 9 hardcoded `<th>` for the
sort-header loop (DOM ids `season-standings-th-<key>`, ` ↑`/` ↓` glyphs) + 17
`<td>`, preserving `season-standings-table` / `-empty` / `-draft-preview-banner`
/ `season-state-badge`. Tests: `matches/tests/test_standings.py` (pure-unit) +
`matches/tests/test_season_views.py` (view/DOM, classes
`TestLg06gStandingsFormSideDetail` / `TestLg06gStandingsDraftPreview`).

## LG-06h league player page

A read-only **League player page** — the in-League destination of every
player-name link on the 8 LG-06f league screens, mirroring the ZenGM player
profile pinned to one League. Read-only — **no model, migration, simulator, RNG,
or Score Calibration re-baseline; no ADR**; GLOSSARY.md already carries the
**League player page** term. Seam contract:
[`.claude/worktrees/lg-06h-seam-contract.md`](../../../.claude/worktrees/lg-06h-seam-contract.md).

**Route + view.** URL name `league_player_detail`, path
`/leagues/<int:league_id>/players/<int:player_id>/`, added to
`matches/league_urls.py` among the `players/*` routes (after
`players_free_agents` / `players_watch_list` / `watch_list_toggle`, before the
`league_list` catch-all — the digit-only `<int:player_id>` converter cannot
shadow the literal `players/free-agents/` / `players/watch-list/` routes). The
view `matches/league_screens/player_detail.py::player_detail(request,
league_id, player_id) -> HttpResponse` is **GET-only** (`HttpResponseNotAllowed(
["GET"])` first line) and re-exported from `league_screens/__init__`
(`from .player_detail import player_detail` + `__all__` append, mirroring
`watch_list` / `free_agents`). Step order: GET guard → `get_object_or_404(
League, pk=league_id)` → `get_object_or_404(Player, pk=player_id)` (404 only on a
missing League OR missing Player — never on "player not in this League") →
`request.session["last_league_id"] = league.id` → `displayed_season =
league.active_season or league.seasons.filter(state="completed").order_by(
"-id").first()` → `_build_league_sidebar_links(league, displayed_season,
sidebar_active=None)` (no sidebar entry matches this page, so **every entry
renders inactive**) → build the RS rows → `render(request,
"leagues/player_detail.html", context)`.

**LENIENT rendering.** Any valid `(League, Player)` pair renders 200. When the
player has Rounds in zero of this League's Seasons, `rs_rows == []` and
`career_row is None` ⇒ the template shows `league-player-rs-stats-empty` in place
of `league-player-rs-stats-table`; the header, Potential placeholder, and all 5
stubs still render (a current free agent with no League Rounds is a 200, not a
404).

**RS aggregation is VIEW-SIDE — no new pure module.** *(ANG-01e moved it into
`league_screen_services.build_player_detail(league, player) -> PlayerDetail`, shared with
`GET /api/leagues/<id>/players/<player_id>/`; `_build_round_dicts` is now the public
`build_round_dicts`.)* Reuse, by import,
`_build_round_dicts` from `matches.league_screens.player_stats` and
`aggregate_player_stats` (+ `PlayerStatRow`) from
`matches.season_player_stats`. Per-Season rows: for each `season in
league.seasons.all()` (newest-first, `order_by("-id")`), build a `prs_filter` of
`{"game_round__match__season": season, "player_id": player.id}`, run
`_build_round_dicts → aggregate_player_stats`, **skip the Season when the round
dicts are empty**, else emit one row from `agg[0]`. Career-in-league row: one
league-wide pass with `prs_filter = {"game_round__match__season__league":
league, "player_id": player.id}` (the LG-06d Career scope filtered to this
player), `year = "Career"`; empty ⇒ `career_row = None`. **Per-Season Team is
derived from the player's actual Rounds that Season** — the aggregated row's
last-seen `team_name`/`team_id` (resolved per-Round inside `_build_round_dicts`
from `team_color` against `game_round.team_red`/`team_blue`), NOT the current
`Player.team` — so a dropped/transferred player shows the team they played for.
No MVP/accuracy recomputation in the view (those come from `get_mvp` /
`get_accuracy` inside `_build_round_dicts`). The table is **not sortable and not
paginated** (a single player across a handful of Seasons — a deliberate
simplification vs. the multi-player Player Stats screen, so no `?sort` / `?dir` /
`?per_page` handling).

**Context keys (frozen).** `league` (the pinned League — used by the watch-flag
partial's `{% url 'watch_list_toggle' league.id %}` + sidebar), `player`,
`displayed_season` (`Season | None`), `sidebar_links` (the 23-entry
`_build_league_sidebar_links` output), `sidebar_active` (locked `None`), `rs_rows`
(`list[dict]`, newest-first, `[]` ⇒ empty-state), `career_row` (`dict | None`),
`stat_columns`. The view does **not** add `watched_player_ids` — the global
`core.context_processors.watch_list` processor supplies it. `stat_columns` is the
STAT portion of `_PLAYER_STATS_COLUMNS` (imported from `player_stats`) sliced
`[2:]` — drop the `name` (col 0, single player) and `team` (col 1, rendered as a
dedicated per-Season prefix column) entries, leaving the 15 stat columns
`games … combo_resupply_count`; the template prefixes **Year** + **Team** columns
itself. Row dict shape (per-Season and Career identical): `{"year": str,
"season_id": int | None, "team_name": str, "team_id": int, "games": int,
"stats": Mapping[str, float]}` — Career uses `year = "Career"`, `season_id =
None`, and the league-wide last-seen team.

**Template + DOM ids.** `templates/leagues/player_detail.html` (NEW) extends
`base.html` and uses the `d-flex` + `{% include "_partials/league_sidebar.html"
%}` shell like the other `templates/leagues/*.html` screens. 11 locked DOM ids:
`league-player-detail` (root), `league-player-header`, `league-player-overall`,
`league-player-potential` (renders the literal `—` em-dash placeholder),
`league-player-ratings`, `league-player-rs-stats-table` (only when rows exist),
`league-player-rs-stats-empty` (only on the empty-state), and the 5 "coming soon"
stubs `league-player-{playoffs,ratings-history,awards,salaries,transactions}-stub`
(each contains the case-insensitive `Coming soon` substring; may cite its
blocking task). The header's EXTERNAL link reverses `player_career_stats` (the
one place in the league templates that still points at the global career page);
the watch flag is `{% include "_partials/watch_flag.html" with
player_id=player.id %}` with `{% include "_partials/watch_flag_script.html" %}`
exactly **once** near the end of `{% block content %}` (never inside a loop).

**8-screen repoint.** Every player-name link on the LG-06f league screens was
repointed from `player_career_stats` (global) to `league_player_detail`
(in-League), keeping the watch-flag include verbatim — only the `<a>` `href`
changes: `player_stats` (`row.player_id`), `player_ratings` (`player.id`),
`free_agents` (`player.id`), `league_leaders` (`row.player_id` ×4 boards),
`statistical_feats` (`row.player_id` — previously plain text, **now wrapped** in a
link), `team_roster` (`player.id` ×2 sections), `team_history` (`p.player_id`),
and `watch_list` (`row.player_id`). The sandbox/global `templates/teams/
player_list.html` and `templates/teams/player_detail.html` stay on
`player_career_stats` (they are league-agnostic). Tests:
`matches/tests/test_league_player_detail.py` (routing / 405 / 404-both-ids /
session write / lenient empty-state / RS rows + Career row / per-Season Team
derived from Rounds / watch flag + single script / external career link /
Potential placeholder / 5 stubs / 23-entry sidebar with zero active); existing
link-target assertions on the 8 screens were repointed to the new route.
