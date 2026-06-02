# Development Plan

Organized by phase. Phases 0–2 are prerequisites for later phases; don't skip ahead.
Story IDs from `sm5_user_stories_v2.html` are referenced where applicable.

---

## Phase 5 — Infrastructure & League System

### LG-06 · ZenGM league-screen parity polish

**Status: PARTIAL — a/b/c/d/e/f DONE, g/h NOT STARTED.** LG-06 is a multi-step group;
shipping a, b, c, d, e, and f does NOT complete LG-06. Next incomplete step is **LG-06g**.

Follow-ups to the shipped LG-01z read-only screens, from the per-page comparison
against the reference product (LOL GM) in
[`docs/zengm-comparison/`](docs/zengm-comparison/) (see that folder's `README.md`
for methodology + the C1–C10 cross-cutting table; each step links its per-page
doc). All UI-only, read-only — no model change, no simulator touch. Lower
priority than LG-02..LG-05; sequence after the screens have real multi-season
data to justify the controls. Each step should go through its own grilling
session before implementation.

- **LG-06a · [DONE] Page-size selector + Team History pagination.** Add the standard
  10/25/50/100 page-size `<select>` (LG-01f `league_history` precedent) to
  **Free Agents**, **Player Ratings**, **Player Stats**; add pagination to
  **Team History** (currently unbounded — one row per player ever, no paging).
  Cross-cutting **C4**. Docs:
  [`free-agents.md`](docs/zengm-comparison/free-agents.md),
  [`player-ratings.md`](docs/zengm-comparison/player-ratings.md),
  [`player-stats.md`](docs/zengm-comparison/player-stats.md),
  [`team-history.md`](docs/zengm-comparison/team-history.md).
  - completed: the three rating/stats screens (Free Agents, Player Ratings,
    Player Stats) already paginated view-side — each already imported
    `_coerce_per_page` / `_coerce_page` and set `per_page` / `page_obj` /
    `paginator` — so LG-06a added only the page-size `<select>` UI (the LG-01f
    `history.html` precedent) to their templates plus a `per_page_options`
    context key fed from the shared `matches.league_views._LG01F_PER_PAGE_OPTIONS
    = (10, 25, 50, 100)` tuple (the single source; not hardcoded in any
    template). Team History, which had **no** pagination before, gained
    `Paginator` wiring on the **Players section only** (view + template) —
    `page_obj` / `paginator` / `players_querystring_without_page` (the latter
    carries `team_id` and omits `page`); the Overall and Seasons sections were
    left untouched. On every screen the per-page `<form>` preserves the other
    params via hidden inputs (`sort` + `dir` on the rating/stats screens,
    `team_id` on Team History) and omits `page` so a page-size change resets to
    page 1; the Team History team-picker form additionally gained a hidden
    `per_page` so switching team preserves the chosen page size. New DOM ids
    `<screen>-per-page-form` / `<screen>-per-page-select` plus
    `team-history-players-pagination`; `_coerce_per_page` / `_coerce_page` were
    reused verbatim (no new helpers). UI-only — no model, migration,
    CONTEXT.md, ADR, simulator, or score re-baseline. Seam contract at
    `.claude/worktrees/lg-06a-seam-contract.md`.
- **LG-06b · [DONE] Team filter.** Add an "All Teams" + per-enrolled-team filter
  `<select>` to **Player Ratings**, **Player Stats**, **Statistical Feats** (the
  team list is already enrolled-season-scoped on those views). Cross-cutting
  **C5**. Docs:
  [`player-ratings.md`](docs/zengm-comparison/player-ratings.md),
  [`player-stats.md`](docs/zengm-comparison/player-stats.md),
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: all three screens gained an "All Teams" + per-enrolled-team
    `<select>` driven by `?team_id=<id>`, with a shared validator
    `matches.league_views._coerce_team_id(raw, enrolled_ids)` (mirrors
    `_coerce_per_page`; the single source imported by all three modules) that
    returns the int id iff it parses **and** is enrolled, else `None` (= All
    Teams, forgiving fallback). Each view sets `enrolled_teams`
    (`displayed_season.teams.order_by("name")`) + `selected_team_id`. Filter
    points differ per screen: Player Ratings filters the queryset
    (`qs.filter(team_id=selected)` after `_enrolled_player_queryset`); Player
    Stats filters the materialized rows post-`aggregate_player_stats` on
    `PlayerStatRow.team_id`; Statistical Feats filters the seam **inputs**
    before `stat_feats.scan_feats` (keep `player_rounds` where
    `team_id == selected`, keep `matches` where `selected in {red_team_id,
    blue_team_id}`) — `stat_feats.py` itself untouched. `team_id` is carried in
    both querystring helpers + a hidden per-page-form input on the two
    paginated screens (the picker form omits `page` so a team change resets to
    page 1); Statistical Feats has no pagination/sort. New DOM ids
    `{player-ratings,player-stats,statistical-feats}-team-filter-{form,select}`.
    UI-only, read-only — no model, migration, URL, simulator, CONTEXT.md, ADR,
    or score re-baseline. Cross-cutting **C5**. Seam contract at
    `.claude/worktrees/lg-06b-seam-contract.md`.
- **LG-06c · [DONE] Sortable columns on the remaining tables.** Bring the LG-00c
  `_coerce_sort` / `_coerce_dir` sort-header pattern (already used on Power
  Rankings / Free Agents / Player Ratings / Player Stats / Team Stats) to the
  five tables that lack it: **Team History**, **Game Log**, **League Leaders**,
  **Watch List**, **Statistical Feats**. Cross-cutting **C6**. Docs:
  [`team-history.md`](docs/zengm-comparison/team-history.md),
  [`game-log.md`](docs/zengm-comparison/game-log.md),
  [`league-leaders.md`](docs/zengm-comparison/league-leaders.md),
  [`watch-list.md`](docs/zengm-comparison/watch-list.md),
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: the five screens (Team History, Game Log, League Leaders, Watch
    List, Statistical Feats) gained the LG-00c sortable-column-header pattern,
    sorting **view-side** with in-memory `sorted(key=…, reverse=(dir=="desc"))`
    on the already-materialized rows — the pure modules `stat_feats.py`,
    `team_history_logic.py`, and `league_leaders_logic.py` (incl. `LeaderRow`,
    whose `rank` stays the frozen metric standing) are UNTOUCHED, sorted on
    their OUTPUT. Sort-key coercion is the single new shared helper
    `matches.league_views._coerce_sort_key(raw, allowed, default)` (returns
    `raw` iff in the `allowed` frozenset, else `default`; mirrors
    `_coerce_per_page` / `_coerce_team_id`), with `teams.views._coerce_dir`
    imported and reused verbatim (no duplicate). Multi-table screens use
    NAMESPACED params so sorting one table never resets a sibling: Team History
    (`players_sort`/`players_dir`, `seasons_sort`/`seasons_dir`) and League
    Leaders (per-board `<board>_sort`/`<board>_dir` across all four boards
    `avg_tags`/`avg_score`/`fewest_tagged`/`tag_ratio`); single-table screens
    (Game Log, Watch List, Statistical Feats) use a single `?sort=&dir=`. On the
    LG-06a-paginated Team History Players table the sort runs BEFORE
    `Paginator` (so the global, not per-page, top row leads), with the extended
    `players_querystring_without_page` carrying `players_sort`/`players_dir` on
    pagination links and a sibling `players_querystring_without_sort_page`
    backing the headers so a sort change resets to page 1. Sort coexists with
    the existing `?team_id=` filters on Game Log and Statistical Feats (header
    hrefs carry `team_id`; team-picker forms carry `sort`/`dir` via hidden
    inputs). Team History's Overall tab (a single W-L-T `dl`) stays unsorted.
    Key tuples are `None`-safe (`(value is None, value)` so `None` sorts last
    in asc) with a per-screen deterministic secondary tiebreak. New DOM ids
    `<screen>[-<table>]-th-<key>` with the active header appending ` ↑`/` ↓`
    glyphs. UI-only, read-only — no model, migration, URL, simulator, RNG,
    CONTEXT.md, ADR, or score re-baseline. Cross-cutting **C6**. Seam contract
    at `.claude/worktrees/lg-06c-seam-contract.md`.
- **LG-06d · [DONE] Season selector + rate/career toggles.** Add a `?season=` selector
  (and, where it maps, ZenGM's Per Game / Per 36 / Totals + Career-Totals
  toggles) across the stats screens once leagues routinely span multiple
  Seasons — currently every screen renders only `displayed_season`. Cross-cutting
  **C1 / C2 / C7**. Lowest priority of the set. Doc:
  [`README.md`](docs/zengm-comparison/README.md) (cross-cutting table).
  - completed: a `?season=` selector landed on **6 screens** — Player Stats,
    Team Stats, League Leaders, Statistical Feats, Game Log, Power Rankings —
    listing each of this League's Seasons newest-first plus a **Career** entry
    (aggregate across all of THIS League's Seasons); no `?season=` param keeps
    the current `displayed_season` (backward-compatible). **Team History is
    excluded** — it is natively all-time and its own Seasons tab already is the
    per-season view, so a season selector would be redundant there. Two new
    shared coercers in `matches.league_views` mirror the `_coerce_per_page` /
    `_coerce_team_id` forgiving precedent: `_coerce_season(raw, valid_season_ids,
    default)` (returns the literal `"career"` sentinel iff `raw == "career"`,
    else the int id iff it parses **and** is in the valid set, else the caller's
    `default` = the `displayed_season` id or `None`) and `_coerce_rate(raw,
    default="total")` (one of the locked literals `"total"` / `"per_game"` /
    `"per_10"`, else default). Career is a **view-side queryset switch** — each
    screen swaps its round/match filter from `...match__season=<season>` to
    `...match__season__league=league` and reuses its existing pure aggregation
    module **verbatim** (`aggregate_player_stats`, `team_stats_logic`,
    `league_leaders_logic`, `stat_feats`, the Game Log in-view round-row build,
    `power_rankings_logic` are all indifferent to one-season vs. all-seasons).
    Player Stats additionally gained a `?rate=` toggle — Totals / Per Game /
    **Per 10 min** (the laser-tag analogue of ZenGM's Per-36) — via a new pure fn
    `matches.season_player_stats.apply_rate(rows, rate)` that transforms the
    summed count columns **only** (`SUMMED_KEYS`); MVP / Acc% / Tag Ratio /
    Survival pass through untouched. Per-10 denominator = the player's total
    uptime, `stats["survival"] * games` (survival is the per-Round mean
    survival-seconds, so ×games rebuilds the summed uptime), i.e.
    `count * 600 / (survival_mean * games)` with a `<= 0` → `0.0` guard; per-game
    = `value / games`. The Player Stats pipeline is `aggregate_player_stats` →
    `apply_rate` → `team_id` filter → `sort_player_stats` → `Paginator`, so the
    sort runs on the **rate-adjusted** displayed value. `season` (and `rate` on
    Player Stats) carries through every querystring helper, hidden per-page /
    team-filter form input, and sort-header href; changing `season` or `rate`
    omits `page` to reset to page 1 (LG-06a/b/c precedent). New DOM ids
    `<screen>-season-filter-{form,select}` (prefixes `player-stats`, `team-stats`,
    `league-leaders`, `statistical-feats`, `game-log`, `power-rankings`) plus
    `player-stats-rate-{form,select}`. UI-only, read-only — no model, migration,
    simulator, RNG, or Score Calibration re-baseline; CONTEXT.md was edited (the
    **Per-10-minute rate** + **Career view (league-scoped)** terms); no ADR.
    Cross-cutting **C1 / C2 / C7**. Seam contract at
    `.claude/worktrees/lg-06d-seam-contract.md`.
- **LG-06e · [DONE] Statistical Feats as a per-game feed.** Reshape the feats screen
  from the current ~9 fixed category-best entries into ZenGM's model: one
  sortable row per notable single-game performance with its box-score line +
  Opp / Result / Season, deep-linking to the Round. Larger than the other LG-06
  steps (changes `stat_feats.py` output shape + template). Doc:
  [`statistical-feats.md`](docs/zengm-comparison/statistical-feats.md).
  - completed: the pure module `matches/stat_feats.py` had its OUTPUT SHAPE
    rewritten from the 9-finder/single-`FeatRecord` design into a per-game feed —
    `scan_feats(player_rounds, matches) -> tuple[list[FeatRow], list[TeamFeatRecord]]`
    now emits **one `FeatRow` per (player, round)** that qualifies, each carrying
    that round's full box-score line (the new pinned `BOX_SCORE_KEYS` tuple of 13:
    the 12 `season_player_stats.STAT_KEYS` per-round PLUS `nuke_detonations`) as a
    `stats` mapping plus view-computed Opp / per-Round Result / Season descriptors,
    and a stacked non-empty `feats` tuple of `FeatBadge(kind, label, is_season_best)`
    badges. **Hybrid qualification** — a row is included iff it crosses ANY
    per-game threshold OR is a season-best leader: threshold constants ship at
    conservative starting values (`TRIPLE_NUKE_THRESHOLD=3`, `HIGH_TAGS_THRESHOLD=20`,
    `HIGH_POINTS_THRESHOLD=12000`, `HIGH_MVP_THRESHOLD=15`,
    `HIGH_RESUPPLIES_THRESHOLD=20`, `HIGH_MISSILES_THRESHOLD=8`, plus the boolean
    `medic_shutout` = medic & `times_tagged==0` and `perfect_heavy` = heavy &
    `shots_missed==0` & `tags_made>0`), calibration explicitly deferred; the 5
    `SEASON_BEST_STATS` (`mvp`/`points_scored`/`tags_made`/`resupplies_given`/
    `missiles_landed`) each yield exactly one guaranteed leader row (tiebreak:
    highest value -> highest `round_id` -> lowest `player_id`, all-zero-max stat
    skipped) tagged `is_season_best=True`. A row both crossing a threshold AND
    leading its kind collapses to ONE badge with `is_season_best=True` winning.
    Feat kinds are pinned in `FEAT_KINDS` (8 `(kind, label)` pairs). `comeback_win`
    moved OUT of the per-player feed into a separate **Team feats** section —
    `find_comeback_win(matches) -> list[TeamFeatRecord]` (return type changed from
    `Optional[FeatRecord]`; detection logic unchanged). `scan_feats` guarantees a
    deterministic default order (`round_id` DESC, then `player_id` ASC); the module
    stays Django-free (`TestNoDjangoImportsLeaked` retained). The view
    `matches.league_screens.statistical_feats.statistical_feats` materialises the
    extended per-(player,round) seam dicts (Opp / Result / Season computed
    **view-side** from `GameRound.red_points`/`blue_points` per-ROUND — NOT the
    Match outcome — and `Match.season`; `mvp = float(prs.get_mvp)` property,
    `accuracy = float(prs.get_accuracy())` **method**, `nuke_detonations` from the
    existing `event_type="special"`/`points_awarded=500` detonation pass), then
    adds **LG-06a pagination** (`_coerce_per_page`/`_coerce_page`,
    `_LG01F_PER_PAGE_OPTIONS`, `Paginator` AFTER sort) and **expanded LG-06c sort**
    over the full box-score column set (`_FEATS_SORT_KEYS` frozenset of every
    descriptor + 13 box-score keys, `_FEATS_SORT_KEYS_DISPLAY`, the
    `_feat_row_sort_value` extractor, `teams.views._coerce_dir` reused) with
    **default sort = most recent first** (`("round", "desc")`) and a deterministic
    `(round_id desc, player_id asc)` secondary tiebreak; the Team-feats list is not
    paginated. Coexists with the LG-06b `?team_id=` filter (applied to the seam
    inputs) + the LG-06d `?season=` selector (incl. Career); changing season/team/
    sort/per-page omits `page` to reset to page 1. The template
    `templates/leagues/statistical_feats.html` was rewritten from a `<ul>` of
    categories into the sortable `statistical-feats-table` (DOM ids
    `statistical-feats-th-<key>` per column with ` ↑`/` ↓` glyphs,
    `stat-feat-badge-<kind>` badges with a `(season best)`/`season-best` suffix,
    `statistical-feats-per-page-{form,select}` / `-pagination`) plus the separate
    `statistical-feats-team-feats` section (`stat-team-feat-<kind>`), preserving the
    LG-06b/d filter ids and the `stat-feats-empty-notice`. Read-only — **no model,
    migration, URL, simulator, RNG, or Score Calibration re-baseline; no CONTEXT.md
    edit** (the **Statistical feat** term was already finalized) and no ADR. Tests
    reshaped in `matches/tests/test_league_statistical_feats.py` (pure-unit +
    view). Seam contract at
    [`.claude/worktrees/lg-06e-seam-contract.md`](.claude/worktrees/lg-06e-seam-contract.md).
- **LG-06f · [DONE] Watch List as a full stats view (+ per-League watch flag).** Replace the 3-column bookmark
  table with the Player-Stats column set filtered to watched players (ZenGM
  parity). Per-user (vs. current browser-session) persistence is **deferred to
  UX-01** (the watch list moves from `request.session` to a per-user model when
  accounts land). Doc: [`watch-list.md`](docs/zengm-comparison/watch-list.md).
  - completed: watch lists became **per-League** in the browser session —
    `request.session["watch_lists"]: dict[str, list[int]]` keyed by
    `str(league_id)` (e.g. `{"3": [12, 47], "8": [12]}`); the pre-LG-06f global
    singular `request.session["watch_list"]` key is **ABANDONED** with no
    migration, no read-compat, and no fallback (session data is disposable,
    ADR-0004 precedent). A single source-of-truth reader
    `matches.league_views._watched_player_ids(request, league_id) -> set[int]`
    (alongside `_coerce_per_page` / `_coerce_team_id` / `_coerce_season`) coerces
    each stored entry to int (silently dropping non-ints), never raises, and is
    consumed by BOTH the new context processor AND the screen view. A new context
    processor `core.context_processors.watch_list(request) -> {"watched_player_ids":
    set[int]}` (alongside `league_nav` / `app_mode`, lazy-importing
    `_watched_player_ids` to dodge the apps cycle) resolves `league_id` from
    `request.resolver_match.kwargs` defensively (off-League / no match ⇒ empty
    set) and is **registered immediately AFTER `core.context_processors.app_mode`**
    in `settings.TEMPLATES[0]["OPTIONS"]["context_processors"]`. A POST-only
    CSRF-protected toggle endpoint
    `matches.league_screens.watch_list.watch_list_toggle(request, league_id) ->
    JsonResponse` (URL name `watch_list_toggle`, route
    `/leagues/<int:league_id>/players/watch-list/toggle/` inserted right after the
    `players_watch_list` route) flips a player's membership in **this League's**
    list and returns `{"watched": bool, "player_id": int}` (200), `{"error":
    "invalid player_id"}` / `{"error": "unknown player_id"}` (both 400),
    `HttpResponseNotAllowed(["POST"])` (405), or 404 on missing League — per-League
    isolation guaranteed by the `str(league_id)` key. The Watch List screen view
    was **rewritten** into the Player-Stats column set filtered to watched players:
    a new **pure** helper `season_player_stats.zero_fill_watched(rows, watched_ids,
    identity_by_id) -> list[PlayerStatRow]` (alongside `aggregate_player_stats` /
    `apply_rate` / `sort_player_stats`, **no new imports** — the module's frozen
    no-Django allowlist is preserved) keeps only watched aggregated rows then
    appends a zero row (`games=0`, every `STAT_KEYS + DERIVED_KEYS` key at `0.0`)
    for each watched id with no Round in scope, in **ascending-id order**
    (aggregated-rows-first / zero-rows-second deterministic output; a watched id
    absent from `identity_by_id` is silently skipped). The locked view pipeline is
    `_build_round_dicts` (imported from `player_stats.py`) → `aggregate_player_stats`
    → `zero_fill_watched` → `apply_rate` → `sort_player_stats` → `Paginator`. The
    reshaped screen carries the full Player-Stats kit **minus the team filter**
    (the Watch List is a personal cross-team set) — season selector (+ Career) via
    `_resolve_season_scope`, rate toggle via `_coerce_rate`, per-page via
    `_coerce_per_page` / `_coerce_page`, sortable columns via `coerce_sort` /
    `coerce_dir` / `sort_player_stats` — with new DOM ids
    `watch-list-{per-page,season-filter,rate}-{form,select}` /
    `watch-list-th-{key}` / `watch-list-pagination` mirroring `player-stats-*`,
    preserving `watch-list-table` / `watch-list-empty-notice` (the `"No Season"`
    substring branch retained) and `sidebar_active="watch_list"`. The **add-form is
    DROPPED** (`watch-list-add` / `-select` and the old `watch-list-row-{id}` rows
    removed); **Remove All / `?action=clear`** is retained (now clears
    `watch_lists[str(league_id)]` then redirects to the bare URL); a per-row
    **watch flag replaces the per-row Remove control**. Two new partials —
    `templates/_partials/watch_flag.html` (a `<button class="watch-flag">` with
    `.watch-flag-on` when watched, `data-player-id` + `data-toggle-url`, NO unique
    `id` so duplicate-player rows don't collide) and
    `templates/_partials/watch_flag_script.html` (one delegated-click `<script>`,
    included exactly once per page, fetch-POSTs with the `X-CSRFToken` cookie and
    toggles `.watch-flag-on` on **all** buttons sharing a `data-player-id`) — wire
    the ZenGM-style flag onto the player-name cell of **8 league screens**
    (`player_stats`, `player_ratings`, `free_agents`, `league_leaders` ×4 boards,
    `statistical_feats`, `team_roster` ×2 sections, `team_history`, and the
    rewritten `watch_list`). UI-only — **no model, no migration, no simulator, no
    RNG, no Score Calibration re-baseline**; CONTEXT.md gained the **Watch list** /
    **Watch flag** terms; no ADR. Tests in
    `matches/tests/test_watch_flag.py`, `matches/tests/test_watch_toggle.py`, and
    `matches/tests/test_league_watch_list.py` (the latter also hosts the pure
    `zero_fill_watched` unit tests). The league-pinned **career-page** flag — the
    one player surface this reshape could not cover (the global
    `/players/<id>/stats/` page is league-agnostic, so its flag has no League to
    toggle against) — was **split off to LG-06h** on 2026-06-02. Seam contract:
    [`.claude/worktrees/lg-06f-seam-contract.md`](.claude/worktrees/lg-06f-seam-contract.md).
- **LG-06g · [TODO — NOT STARTED] Standings form/side detail.** Surface Streak, Last-5 (L5), and a
  home-away (Red/Blue side) split on the Standings table — we already persist
  per-Round side data; this is presentation only. Doc:
  [`standings.md`](docs/zengm-comparison/standings.md).
- **LG-06h · [TODO — NOT STARTED] League-scoped player page (+ watch flag).** Introduce a
  **league-pinned** player detail route (`/leagues/<league_id>/players/<player_id>/…`)
  so a Player viewed from inside a League carries that League's context — and put the
  ZenGM **watch flag** on it. This is the one player surface LG-06f could **not** cover:
  the existing `player_career_stats` page at `/players/<id>/stats/` is league-agnostic, so
  its flag has no League to toggle the (per-League) watch list against. Carved out of
  **LG-06f** on 2026-06-02 because pinning the global HX-01 career page to a League is a
  new route + view + template, not a watch-list reshape. Repoint the 8 LG-06f league
  screens' player-name links at the new route. **Open questions for its own grill:** does
  the page show **league-scoped** stats (only this League's Seasons) or the same global
  HX-01 career aggregates; how a Player with games in two Leagues is handled (name overlap
  is intentional — separate Player rows, separate per-League watch lists); whether to
  reuse the HX-01 aggregation or a Season-scoped one; sidebar chrome + flag placement.
  **Depends on LG-06f** — reuses the per-League watch-list storage, toggle endpoint,
  context processor, and flag partial it ships, verbatim.

Structural divergences surfaced by the playthrough that map to **existing**
tasks rather than LG-06 (see
[`season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md)): the
playoffs stage + phase-aware Play menu → **LG-02**; season-MVP / Finals-MVP
awards (and surfacing them on League History) → **LG-03**; MMR / Rank / Potential
columns → **STAT-PROXY-01**.


### LG-02 · Tournament formats

Support the following tournament types:

- **Single elimination** — 4/8/16 teams; standard knockout bracket.
- **Double elimination** — losers get a second chance via the losers bracket.
- **Round robin** — all teams play each other; used for seeding.
- **Round robin → Double elimination** — round robin seeding phase feeds into a double elimination finals.
- **Swiss** — pairings based on current standings; rounds auto-calculated from participant count
  (typically ⌈log₂(N)⌉), overridable by tournament admin.
- **Random Draw** — a pool of individual players with no pre-set teams. When all participants
  are registered, the system randomizes team assignments. Tournament admin reviews and edits
  the assignments, then confirms to lock them in. Format runs as Round Robin → Double Elimination.
- **Duos** — players register as pairs. Pairs are placed on 6v6 teams alongside other pairs.
  Pair performance is tracked independently across games. Requires `TournamentSubGroup` model.
- **Trios** — same as Duos but groups of three.

**TournamentSubGroup model:** links players as partners within a specific tournament. Used by Duos and Trios
to track sub-group performance independently of the full team result.

Bracket rendered as a visual tree. Results auto-advance winners.
This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

Once tournaments land, relabel "Until end of season" → "Until playoffs" (LG-01d ships the former label) and extend the play loop through tournament completion.

### LG-03 · Season-end awards

Computed from `PlayerRoundState` aggregates: Most Points, Highest K/D by role, Best Medic, 
Most Efficient Nuke, Best Accuracy. Awards page at `/seasons/<id>/awards/`. Award badge on player profile.

Also surface the headline **season MVP** (and, once LG-02 playoffs land, a
**Finals MVP**) on the **League History** table (LG-01f) — the reference product
puts both in its history row next to Champion / Runner-up, and ours currently has
no awards column. See
[`docs/zengm-comparison/season-lifecycle.md`](docs/zengm-comparison/season-lifecycle.md).

### LG-04 · Season-end stat updates

At the end of each season, all players (on active teams or otherwise) receive a stat update.
The update factors in:
- **New experience** — games played this season
- **Player age** — older players improve more slowly
- **Prior experience** — players with more historical games have a smaller update magnitude

Default weights for these three factors are fixed in code but overridable per season by the league admin.

### LG-05 · Player potential

Each player carries a `potential` attribute: a dynamically computed estimate of their likely stat ceiling.

- Computed at each season-end stat update, not on demand.
- Derived from current player stats + the team's seasonal scouting budget allocation.
- **Scouting budget** is a per-season allocation on the team. Higher budget = more accurate `potential`
  estimate. Lower budget = noisier estimate with added randomness.
- `potential` has a floor of `overall_rating` — it can never predict a player will regress below
  their current average.
- `potential` is not exposed in the UI until this phase is complete.

---

---

## Phase 5.5 — Single-Player Career Mode

A single-user play mode where the user acts as a team manager navigating a league season. This phase
sits between the League system (Phase 5) and full multiplayer (Phase 6).

### CAR-01 · Manager role and team assignment

In single-player career mode, the user is a team manager (not a player in the simulation).
The user is assigned to a team at the start of a career league. Each season the user manages their
team through the league schedule.

### LG-01i · Season "One Week (Live)" replay UI

Per-Round live replay surface invoked from the Play dropdown — a
"One Week (Live)" entry that plays the next matchday tick-by-tick in
the browser rather than committing the Rounds straight to the DB.
User watches each tag/down/elimination as it happens with a
play/pause/scrub control, then commits or discards the run at the
end. Depends on **CAR-01** + the new Season-replay engine (the
tick-stream surface the manager-mode career UI also consumes).
Deferred from LG-01d. Re-sequenced from Phase 5 to Phase 5.5
(post-CAR-01) on 2026-05-28 because CAR-01 owns the Season-replay
tick-stream engine LG-01i consumes.

### CAR-02 · Performance-based firing

The system tracks manager performance metrics (win rate, standings position, point differential).
When a manager's performance falls below a configurable threshold, the system fires them automatically.
After being fired, the manager can apply for or be assigned to another team in the league.

### CAR-03 · Career isolation from multiplayer

The firing mechanic and team-switching only apply in single-user career mode. In multiplayer leagues,
each user is locked to their team for the full duration of the league — no transfers, no firing.

### SUB-01 · Sub-leagues + per-sub-league rotating map pools

Introduce **sub-leagues** as a first-class domain concept: an
optional partition of a `Season`'s enrolled Teams into named groups
(conferences / divisions / pools), modelled as a new `SubLeague`
container under `Season` with its own `teams` M2M and an ordered
list of `ArenaMap`s. Each Round's map is then resolved from the
sub-league's pool by matchday (`maps[matchday % len(maps)]`), giving
the deterministic-rotation third mode that LG-01j originally
listed but had no domain referent for. Carved out of LG-01j on
2026-05-28 because no `SubLeague` model existed at LG-01j time and
the user wanted to defer the introduction until the career-mode
slice was in place. Depends on **LG-01j** (the per-Season map
config UI + `play_season_task` `arena_map` thread it adds — SUB-01
extends both with a sub-league-aware map-resolver branch) and on
**CAR-03** (sub-league grouping is most useful once manager-mode
career play is driving the Season). Adds the **SubLeague** term
to CONTEXT.md and ships an ADR for the new model + the
schedule-generation interaction (a sub-league partition implies
intra-pool vs cross-pool fixtures, a sequencing decision LG-02
will also lean on).

---

---

## Phase 5.6 probability features

### PR-01 · Pre-match win probability forecast

`/matches/forecast/?red=<id>&blue=<id>` — triggers 100-sim batch (requires SIM-02 and STAT-02). 
Shows win% per team, projected score range (10th–90th percentile), projected avg survivors, per-player risk flags.

### PR-02 · Roster composition comparison

Two side-by-side roster selectors vs same opponent, each running 100 sims. Side-by-side win%, avg score, avg survivors.
Recommended scenario highlighted with rationale.

### PR-03 · What-if scenario editor

Fork a real `GameRound`, change one variable (swap role, adjust stat, change player), 
re-simulate, show diff vs original. Forked scenario is temporary, not a permanent Match record.

---

---

## Phase 6 — Users and Multiplayer

### UX-01 · User accounts and team ownership

Django auth system (email + password). Open self-registration — anyone can create an account.
Admins can remove user accounts via Django Admin.

Permissions: only team owners can edit their teams/players; read-only access to others.
Users can see the teams, players, leagues, seasons, and tournaments they have created.

League access is **closed by default** (invite-only). League creators can set a league to open
(anyone can join) or send invitations to specific users.

Google/OAuth social login is deferred — see Deferred Items section.

### UX-02 · User–player link

Each user account may be linked to exactly one `Player` record (one-to-one). This link represents
a self-insert — the user's personal profile of what they believe their own stats are or aspire to be.
The linked player is a vanity record and does not automatically appear on any simulated team.

This should look at the screenshots existing within the /Screenshots_and_video_examples/ directory.

---

---

## Phase 8 — Angular Frontend Migration

Replaces Django's server-rendered HTML templates with an Angular single-page application (SPA).
Django becomes a pure API backend; Angular handles all UI in the browser. This phase requires Phase 5's
API-02 (REST API) to be complete and deployed (Phase 7) before starting.

**Approach:** migrate one feature area at a time. Django templates remain live until the Angular
equivalent is complete and verified. Django Admin is a permanent exception and is never migrated.

### ANG-01 · Harden and complete the REST API (prerequisite)

Before building Angular against it, ensure:

- All endpoints needed by the UI exist: teams, players, matches, rounds, events, maps
- Consistent JSON envelope (data, pagination, errors)
- Filtering and pagination on list endpoints
- Proper HTTP error codes (400 for validation, 404 for missing records, etc.)

### ANG-02 · CORS configuration

During development Angular runs on `http://localhost:4200` and Django runs on `http://localhost:8000`.

- Add `django-cors-headers` to `requirements.txt`
- Add `CorsMiddleware` to `MIDDLEWARE` (before `CommonMiddleware`)
- Set `CORS_ALLOWED_ORIGINS = ["http://localhost:4200"]` for dev; production domain added when known

### ANG-03 · JWT authentication

- Add `djangorestframework-simplejwt` to `requirements.txt`
- Add `/api/token/` (login) and `/api/token/refresh/` endpoints
- **Access token** stored in memory (not localStorage — avoids XSS token theft)
- **Refresh token** stored in an httpOnly cookie (survives page refresh without re-login)
- Angular `HttpInterceptor` attaches `Authorization: Bearer <token>` to every API request automatically

### ANG-04 · Angular project scaffold

One-time setup in a `/frontend/` directory at the repo root.

```bash
npm install -g @angular/cli
ng new frontend --routing --style=scss --strict
cd frontend
ng add @angular/material
```

### ANG-05 · Angular API services

One Angular service per Django API resource. Components never call `HttpClient` directly.

```
TeamsService     → GET/POST/PATCH /api/teams/
PlayersService   → GET/POST/PATCH /api/players/
MatchesService   → GET/POST       /api/matches/
RoundsService    → GET            /api/rounds/<id>/
EventsService    → GET            /api/rounds/<id>/events/
MapsService      → GET/POST       /api/maps/
```

### ANG-06 · Migrate views by feature area

Migrate one area at a time in order of complexity. Each item: build the Angular route/component,
verify feature parity with the existing Django template, then remove the Django template + view.

1. **Teams list & detail** — simple CRUD table + form; good first Angular component to build
2. **Player add/edit** — stat form with live `overall_rating` preview
3. **Match list & create** — team picker, match creation, results list
4. **Round detail** — per-player stat table, MVP scores
5. **Event timeline** — filtered event log, color-coded by type (SIM-05 replay controls slot in here)
6. **Map editor** — most complex: canvas overlay, zone painting, sight-line drag-select (migrate last)

### ANG-07 · Serve Angular from Docker (nginx sidecar)

Once Angular is built (`ng build --configuration production`), serve it via an nginx sidecar service.
nginx serves the Angular static files on port 80 and proxies `/api/` requests to the Django container
on port 8000. Add `nginx.conf` and update `docker-compose.yml` with the `nginx` service.

### ANG-08 · Remove Django template views

Once each Angular view is verified, delete the corresponding Django template file and its
HTML-serving view function. Keep the API endpoint. Update URL routing to remove the old path.
The app should have zero `.html` template files by the end of this phase, except Django Admin
(which is a permanent exception and stays indefinitely).

---

---

## Sequencing Summary

```
Phase 0 (Fixes) ← complete
  → Phase 7 (Docker & Deployment) ← do this first; ship the Django template UI to prod early
  → Phase 1 (Map Integration)
    → Phase 2 (Stats Integration)
      → Phase 3 (Simulation Mechanics)
        → Phase 4 (Analytics — most items can run in parallel with Phase 3)
          → Phase 5 (Infrastructure & League)
            → Phase 5.5 (Single-Player Career Mode)
              → Phase 6 (Users and Multiplayer)
                → Phase 8 (Angular Frontend Migration)
                  (requires Phase 5 API-02 REST API)
```

Phase 4 items RES-01 (accuracy %), RES-02 (SP chart), RES-03 (missile log), and SIM-01 (document weights)
are quick wins that can be done any time after Phase 0.

Phase 7 (Deployment) can be done in parallel with any feature phase — re-deploy as features land.

---

## Deferred Items

The following were explicitly scoped out and should not be implemented until re-evaluated:

- **Mirrored/reflective walls** (MAP-07) — shot-bouncing mechanic; deferred from Phase 1
- **Per-stat-per-role weight tuning** (STAT-02 follow-up) — granular multipliers per stat per role;
  deferred until baseline simulation data exists to inform the values
- **Google/OAuth social login** (UX-01) — deferred from Phase 6; email/password only for now
- **Custom domain** — deferred until the project grows; fly.dev subdomain is sufficient for now
- **Goal-recompute throttling** (MOVE-04) — behavioural perf lever (staler goals every *N* ticks);
  out of MOVE-02 scope, opened only if the MOVE-02 path cache alone is insufficient for the
  map-mode perf target

---

## Phase 4 — Highlight Surfacing & Chart Overlays (added 2026-05-21, post-RV-02)

Frontend-only follow-ons that reuse data already persisted/logged by earlier work — no new
simulation, no migration. Both build on the existing `game_round_events.html` infrastructure
(M-1 JSON windowing, the SIM-05 playback engine, and the RES-02 `_overlay_plugin` Chart.js v4
vertical-overlay pattern).

### RV-04 · Highlight overlay on the playback timeline + chart toggle

Surface the RV-02 **Highlight** list (`GameRound.highlights_json`) in two more places beyond the
Highlights tab:

- **Playback timeline (SIM-05):** mark each Highlight at its tick on the playback scrubber / event
  timeline (a coloured pip per `kind`, reusing the `OVERLAY_KIND_STYLE` palette extended for the
  RV-02 kinds — `nuke_detonation`, `nuke_cancelled`, `medic_reset`, `first_elimination`,
  `team_elimination`, `scoring_burst`). Clicking a pip jumps playback to that tick;
  the currently-playing Highlight is indicated. No new backend — `highlights_json` is passed to the
  page via `json_script` alongside `events_data`.
- **Chart toggle:** an optional overlay on the four event-page charts (`chart-shots`, `chart-lives`,
  `chart-points`, `chart-sp`) drawing one vertical line per Highlight, coloured by `kind`, label =
  kind + player/team — using the **existing** RES-02 `_overlay_plugin` registration path (inline
  `plugins:` array, `drawOverlays` mutating the closure-captured overlay list). A "Highlights" toggle
  in the chart filter UI mirrors the existing elimination/special/nuke overlay toggles exactly.

**Scope:** read-only/derived; no model change, no migration, no simulator change. Depends on RV-02
(`highlights_json`). **Acceptance:** every Highlight in `highlights_json` appears as a timeline pip
and (when toggled) a chart overlay line at the correct tick; toggling Highlights off restores the
prior chart appearance; clicking a timeline pip scrubs playback to that Highlight.

### RES-05 · Medic-hits overlay on the event-page charts

Add **medic hits** as a toggleable overlay on the four event-page charts, reusing the RES-02
`_overlay_plugin` pattern. The exact definition of "medic hit" is to be pinned during the grill
(candidates: every `tag` row whose **target** is a **Medic**; the **medic-under-fire alert** moments
— a Medic tagged 2× within `MEDIC_ALERT_WINDOW_TICKS`; or hits *landed by* a Medic) — the data is
already in the event log (`tag` rows carry actor/target roles in `metadata`), so this is a
client-side scan + overlay with no backend change. A "Medic hits" toggle joins the existing chart
filter toggles.

**Scope:** frontend-only; no model change, no migration, no simulator change. Depends on RES-02
(chart + overlay-plugin infrastructure). **Acceptance:** toggling "Medic hits" marks each qualifying
event on the charts at the correct tick and toggling it off restores the prior appearance; the
definition chosen in the grill is documented in CONTEXT.md if it introduces new domain language.

---

## Phase 3 — Simulation Mechanics Backlog (added 2026-05-21)

Mechanics and decision-making items captured from working notes. These extend the MECH / MOVE
families and the role-aware goal selection work (MAP-05). None are scheduled yet — each carries an
open question or design dependency that must be resolved before implementation. Items are ordered
roughly by readiness; MECH-07 (goal-selection rework) is intentionally last because its shape is
still undecided.

### MECH-08 · Reset-timing miss penalty

Players currently have no notion of *when* a downed enemy will turn back on, so they cannot mistime a
shot. Add behaviour where a player attempting to tag a reset target can fire **too early** — before
the target reactivates — and waste the shot. The miss should fall out of imperfect timing rather than
the existing hit-chance roll.

**Open question:** which stats drive the timing estimate? Candidates already on the model —
`game_awareness` (already gates the MECH-02 reset filter), `nuke_awareness`/reaction-style stats, and
possibly a new dedicated stat. Resolve which stat(s) feed the early-fire probability before wiring.

### MECH-09 · Reset re-tag action/goal

For reset handling, lean on the existing LOS infrastructure (MAP-03) and the per-tick candidate
filters rather than the abstract zone check. Add an action/goal so a player actively **looks for a
reset opportunity to re-tag a downed enemy** once it reactivates, using `SightLineConfig` for
eligibility and the appropriate target filters. Pairs with MECH-08 (timing) and builds on the MECH-02
`last_tagged_id` reset-target machinery.

### MECH-10 · Follow rule — cap pursuit of downed players

Medics are dying within ~4 minutes because players follow a downed target indefinitely. Add a
**follow rule**: a player cannot follow a downed player more than **10 squares along the downed
player's path**. The path is modelled as a hallway (corridor spread) that starts at the square where
the player was downed and extends until the player turns back on. Pursuit beyond the 10-square limit
is disallowed, which should give Medics survivable breathing room.

**Open question:** corridor width / spread of the "hallway" and how it interacts with LOS and walls
(MAP-07) still needs pinning.

### MECH-11 · Crouch mechanic + stamina cost

Add a **crouching** mechanic that makes a player un-hittable over a **half wall** (the low-wall type
from MAP-07). To prevent continuous abuse, crouching **drains stamina** — either disallowing
sustained crouch outright, or applying a **movement penalty** when stamina is depleted. Touches the
hit-eligibility path (low walls currently block movement but not sight) and the stamina schedule.

**Open question:** which lever — hard stamina gate vs. movement-penalty-on-empty — and whether
stamina here reuses the existing proportional stamina schedule or needs a separate pool.

### MECH-12 · High-ground / half-wall sight-line falloff formula

Rework the high-ground LOS formula (MAP-09) so elevation does **not** grant a clean look at everything
directly below a half wall. Behaviour: a player on elevation should **not** see the cells directly
below a half wall unless **close to the wall**. The farther the elevated player stands from the half
wall, the more of the near sight lines below the wall are removed; farther still removes more. The
falloff should follow a **triangle-type formula** (sight removed grows with distance from the wall).

**Status:** this is a formula rework of the MAP-09 shoot-over / `SightLineConfig` computation, not a
new subsystem. Lands in `compute_sight_lines` / `_has_los` (the `can_shoot_over_wall` path).

### MECH-13 · Per-player information table (imperfect information)

Players currently act on **perfect information**, which is incorrect — each player should decide using
only what they personally know. Add (or fully wire) a **per-player information table** that informs
decision-making, so choices are made against believed/last-known state rather than ground truth.

**Status:** a per-player view already exists via the MECH-06 `player_memory` dict (transient, staleness
thresholds per role). Unclear how much of decision-making actually consults it today — audit current
usage in goal/target selection, then route remaining perfect-information reads through the table.

### MECH-14 · Memory/comms-driven adaptive role behaviour

Now that memory (MECH-06) and communication are implemented, players should **change what they do**
based on new information they receive, rather than following static role scripts. Concrete behaviours
to encode:

- **Scouts** push in past the Heavy when the Heavy is down.
- **Commander** takes space when the Heavy is down.
- **Ammo** can resupply the Heavy for free when the Commander is down.

These are conditional goal/action overrides keyed off teammate-status memory; they extend the MECH-06
broadcast/memory hooks and feed into the role goal selection (MAP-05 / MECH-07).

### MOVE-05 · Simulation engine de-duplication (refactor)

`simulation.py` is heavily bloated and contains duplicated logic. Continue the consolidation already
begun.

**Status:** partially done — `ResourceBasedSimulator` was removed (SIM-09). Several areas still
**duplicate the tagging-and-related-checks code** (a player tag plus all the associated checks appears
in more than one place). Extract the shared tag/check path into a single helper so there is one
implementation. No behavioural change intended; fold any incidental delta into the existing pending
Score Calibration re-baseline.

### MECH-07 · Role-aware goal-selection rework (MAP-05 follow-up)

Make changes to role-aware goal selection (MAP-05). Shape is **still being worked out** — scope and
acceptance criteria are deliberately deferred.

**Status:** TBD — intentionally sequenced **last** in this batch until the design is settled.

---

---

## Phase 4 — Individual Performance & PDF Graphs (added 2026-05-22)

Three analytics/export follow-ons. They reuse data already persisted by earlier work (per-player
`PlayerRoundState`, the `GameEvent` log, and the RES-02 SP / shots / lives / points series) and the
RV-03 ReportLab export. **Decision (locked at planning):** charts are rendered **server-side with
matplotlib** (pure-Python, no browser, deterministic) rather than capturing the client-side Chart.js
canvases or printing the page in headless Chrome — keeps the export self-contained and avoids a
browser dependency ahead of the Angular migration, consistent with RV-03's ReportLab rationale. Both
PDF items below share a single matplotlib-to-ReportLab rendering helper.

**Shared prerequisite:** add `matplotlib` to `requirements.txt`. A new helper module
(`matches/sim_helpers/pdf_charts.py`, pure: data series in → PNG bytes / ReportLab `Image` out, no
ORM, no I/O beyond an in-memory buffer) re-plots each chart series with matplotlib using the
`Agg` (non-interactive) backend so it runs headless on the server. The chart **data** is the same
series the events page builds (per-player SP / shots / lives / points over time, sourced from
`GameEvent` rows — RES-02 contract); the helper does not need Chart.js. Charts won't be pixel-identical
to the on-screen Chart.js versions, but carry the same data.

### RV-05 · Round report PDF: chart/graph section (extends RV-03)

Add a **charts section** to the RV-03 PDF (same `GET /matches/game-round/<id>/export/` endpoint — one
PDF = summary + scoreboards + per-player table + resource summary + **graphs**). Render the same four
event-page charts (SP, shots, lives, points over time) server-side via the shared
`pdf_charts.py` helper and embed them as ReportLab `Image` flowables after the existing tables. The
"[Simulated]" watermark on simulator-generated rounds (RV-03) applies to the chart pages too.

**Depends on:** RV-03 (the export endpoint + ReportLab scaffold must land first; RV-05 amends its
scope). **Scope:** read-only/derived — no model change, no migration, no simulator change. **Acceptance:**
the exported PDF contains one rendered graph per event-page chart with the same data as the
on-screen charts; an empty/early-eliminated round degrades gracefully (axis with no series, no crash);
the watermark appears on chart pages for simulated rounds.

### HX-02 · Individual performance per round page

A **single-round, single-player** drilldown — distinct from HX-01, which aggregates a player's career
across *all* rounds. New page `/matches/game-round/<id>/player/<pid>/` (URL name e.g.
`round_player_detail`), linked from each player row on the round detail scoreboard
(`game_round_detail.html`) and from the round events page. Surfaces that player's performance **within
this one round**: their `PlayerRoundState` stat line (points, MVP, tags made / times tagged, accuracy
%, final lives, resupplies given, missiles landed, specials used, follow-up / reaction shots, combo
resupplies), their personal `GameEvent` timeline filtered to events where they are actor or target,
and their SP / shots / lives curves over the round (the RES-02 series, filtered to this player). If the
round has a movement heatmap (RES-04 `cell_occupancy_json`), embed this player's per-cell occupancy as
a mini-heatmap.

**Depends on:** existing `PlayerRoundState` + `GameEvent` data (no new persistence); reuses RES-01
accuracy, RES-02 SP series, and optionally RES-04 occupancy. **Scope:** read-only/derived — no model
change, no migration, no simulator change. **Acceptance:** the page renders the correct stat line and
event timeline for the given (round, player); a player who has no `PlayerRoundState` on the round
404s; the per-player charts show only that player's series; the round-detail scoreboard links to it.

### HX-03 · Export individual performance as PDF (extends HX-02)

`GET /matches/game-round/<id>/player/<pid>/export/` — a per-player, single-round PDF stat sheet:
header (player name, role, team, round), the stat line, the personal event timeline, and the player's
SP / shots / lives / points graphs rendered server-side via the **same** `pdf_charts.py` helper used by
RV-05 (one rendering path, reused). "[Simulated]" watermark on simulator-generated rounds, matching
RV-03 / RV-05.

**Depends on:** HX-02 (the page + its data assembly) and the RV-05 shared chart helper. **Scope:**
read-only/derived — no model change, no migration, no simulator change. **Acceptance:** the exported
PDF contains the player's stat line, timeline, and graphs for the one round; an absent
(round, player) pairing 404s; the watermark appears for simulated rounds.

### IMPORT-01 · Real-game `.tdf` log parser + import tool

Parse real Laserforce SM5 game logs (the `.tdf` files in `Screenshots_and_video_examples/sample_games/`)
and import them as `GameRound`s, so the app can store and review *actual* games alongside simulated ones.
The `.tdf` format is a **UTF-16, tab-separated, sectioned** export: `;0/info`, `;1/mission` (type, desc,
start, duration), `;2/team` (index, desc, colour), `;3/entity-start` (player/target id, role/battlesuit,
team, member id), and `;4/event` (time, type code, free-form payload) records. Write a pure parser
(`.tdf` bytes → structured rounds + events, no Django/ORM, no I/O) and an import tool (management command
and/or upload view) that maps parsed entities to `Player`/`Team` rows and parsed `;4/event` rows to
`GameEvent` rows, persisting a `GameRound` linked to an **`actual_game_log`** record.

**Provenance contract (locked at RV-03 planning):** a `GameRound` not paired with an `actual_game_log`
is `is_simulated = True` (the RV-03 watermark default); an imported round links to its `actual_game_log`
and is stored with `is_simulated = False` (no watermark). RV-03 adds the `is_simulated` flag now;
IMPORT-01 adds the `actual_game_log` link and is the first writer of `is_simulated = False`.

**Open design questions (resolve in this task's own grill):** the `actual_game_log` model shape (store
raw `.tdf` bytes vs. parsed JSON vs. both); how `;4/event` type codes map onto the simulator's
`GameEvent.event_type` vocabulary (tag / down / resupply / nuke / base-capture — the mapping is the risky
part and likely lossy); how parsed entities reconcile to existing `Player`/`Team` rows (match by member
id? create-on-import?); whether real-game ticks/timestamps (the `;4` `time` field is in different units)
need conversion to the TIME-01 tick model. **Scope:** new persistence (the `actual_game_log` model +
`is_simulated = False` writes) and a migration. **Acceptance:** both sample `.tdf` files parse without
error into a reviewable `GameRound` whose scoreboards/event log render in the existing round views, and the
imported round shows **no** "[Simulated]" watermark on its RV-03 export.

### SIM-12 · Clamp negative action weights before `random.choices`

Discovered during the SIM-01 grill/review (May 2026). `combat.plan_action` builds the 9-slot weight
vector and feeds it straight to `random.choices` **without clamping per-element negatives to 0**. CPython's
`random.choices` only raises when the *total* weight is ≤ 0 — it does **not** reject an individual negative
weight; instead the negative bucket becomes unreachable in the cumulative-weight bisect **and silently skews
the neighbouring buckets' probabilities**. Several role branches legitimately emit one negative slot today:
Heavy/Commander `only_move` while missiles remain (`25/15 → 5` after the MOVE-03 hold draw, then `−15`
missile cost → `−10`/`−5`), Heavy `only_move` while capturing (`5 − 10 = −5`), and Scout `tag_player` when
shots-critical with no ammo ally (`_SCOUT["seek_no_ammo_tag"]=50` > post-baseline tag `40` → `−10`). So the
action distribution on those ticks is subtly wrong, not crashing. SIM-01 deliberately left this **unfixed**
(it is a behavioural change, not a documentation change) and pinned only the true non-raising invariant
(`test_plan_action_never_raises_*` / `test_plan_action_total_weight_is_positive` in `test_weights.py`).

**Scope:** add a single non-negative clamp on the final weight vector in `plan_action` (e.g.
`weights = [max(0, x) for x in weights]`) immediately before `random.choices`, *after*
`apply_decision_making_spread` and the cooldown/stamina post-processing. Decide in this task's grill whether
the clamp belongs in `plan_action` (one site, covers all roles) or pushed back into the role functions /
helper subtraction sites (more surgical but many sites). **Tests:** convert the role-function-layer
`test_scout_shots_critical_tag_goes_negative_xfail` from `xfail` to a real assertion once the clamp lands at
the right layer (or keep it documenting the raw role-fn output and add a new `plan_action`-layer test that
the vector handed to `random.choices` has **every element ≥ 0**, not just total > 0 — strengthening the
SIM-01 `total > 0` invariant). Also pin the three known negative-emitting branches (Heavy missile, Heavy
capture, Scout shots-critical) so the clamp is regression-guarded per branch.

**This re-baselines seeded outcomes** (the corrected probabilities shift which Action is rolled on the
affected ticks) — fold it into the single pending post-MOVE-01 Score Calibration re-baseline; do **not**
create a separate re-baseline obligation. No migration, no new domain term, no ADR (a one-line clamp is
reversible and unsurprising).

### LG-06 · Phased Season lifecycle (off-season / regular / tournament)

Replace the current flat `draft → active → completed` Season state machine with a phased
lifecycle that mirrors a sports-league cadence:

1. **Off-season / pre-season** — Free Agents pool open for recruitment; teams may carry a
   variable roster (any size). Roster is **clamped to 10** on the press of the "Start Regular
   Season" button before round play begins.
2. **Regular season** — round-robin (today's `active` behaviour). PLAN backlog: add **alternative
   regular-season formats** beyond single-round-robin (double-round-robin, split-conference,
   stage-based, etc.) — owner picks per Season.
3. **Tournament (playoffs)** — best-of, double-elimination bracket between seeded teams,
   ending with a single champion. Tournament feeds from regular-season Standings. Subsumes
   the LG-02 double-elim format as the canonical end-of-Season closer.

**Dashboard implications (consumes by LG-01c re-visit):** during off-season the dashboard
renders an *unpopulated* preview (teams + players sorted by name); during regular season the
dashboard is fully populated as today; during tournament the dashboard mixes fixed regular-season
stats with live tournament-stats panels; post-tournament shows end-of-tournament stats until the
next off-season starts.

**Out of LG-01c scope** (LG-01c is read-only dashboard against the current 3-state model).
Touches: `Season.state` enum + migration, free-agent ↔ Team move flows, roster-size cap toggle,
tournament bracket model (LG-02 overlap), simulator's `simulate_scheduled_round` phase guard,
dashboard branches per phase.

### SIM-04 · Simulation confidence display

when we import real data we want to have a confidence level and "elo" skill rating of actual players using all imported games
Per-player data source label ("40 games" vs "Role defaults — no history") on simulation summary. 
Team-level confidence badge: Low (<5 games), Medium (5–20), High (>20). Link to edit stats from confidence panel.

### STAT-03 career stat additions

add mvp and elo over time to career stats

### STAT-PROXY-01 · Rating proxies — MMR, Rank tier, Potential

The LG-01z league screens (Player Ratings, Free Agents, Team Roster, and — once
unblocked — Hall of Fame) reserve columns for three LoL-GM rating concepts we don't yet
model: **MMR**, **Rank tier**, and **Potential**. They currently render a literal `-`
placeholder (see `stats.md`). This task replaces the placeholders with real values:

1. **MMR** — a per-player skill rating. Likely an Elo-style number seeded from
   `overall_rating` and updated from game results (ties into SIM-04's "elo skill rating
   of actual players using all imported games" and STAT-03's "elo over time"). Decide:
   stored field vs. derived; per-Season vs. career.
2. **Rank tier** — a **letter tier** (e.g. S / A / B / C / D, or named bands) derived
   from MMR or `overall_rating` bands. Cosmetic label; thresholds are tunable.
3. **Potential** — a ceiling rating (0–100) per player, paired with `overall_rating`.
   Likely a stored field set at generation / import; drives prospect scouting later.

**Implementation surface:** add the field(s) / derivation, then replace the `-`
placeholder cells on the Player Ratings, Free Agents, and Team Roster templates with the
real values (and make them sortable where it makes sense). Unblocks the **Hall of Fame**
screen's Peak MMR / Peak Overall columns (`stats.md` §11). No simulator-mechanic change;
no Score Calibration re-baseline. Coordinate with SIM-04 (import-driven Elo) so MMR has a
single source of truth.
