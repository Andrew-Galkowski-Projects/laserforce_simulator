# League-context navigation shape (zengm-style sidebar + top-bar dropdown)

**Status:** Accepted (LG-01f, 2026-05-27). Supersedes the LG-01c sidebar shape.

## Context

LG-01f ships the read-only League history page at `/leagues/<int:league_id>/history/`. The grilling session
that locked the LG-01f seam pulled in a significant scope expansion: rather than just the history page +
one entry-point link, the user asked for a zengm-shaped left sidebar and a new top-bar `League ▾`
dropdown, both wired with placeholder entries for navigation surfaces that do not exist yet (Playoffs,
Finances, Power Rankings, per-Team Roster / Schedule / Finances / History, per-Players Free Agents /
Trade / Trading Block) and a follow-on PLAN task that fills the placeholders as those features land.

This collides head-on with two earlier locks:

- **LG-01c locked a 5-entry season-dashboard sidebar** (`Overview / Standings / Schedule / Teams /
  History`) with a `sidebar_links` context shape of `frozen 5 entries in pinned order`, and a
  `TestSeasonDashboardSidebar` Django `TestCase` that asserts the length, key set, and per-entry
  disabled / live state. The new shape is 13 entries across 3 sections plus a `Dashboard` top-level
  entry — a wholesale replacement, not an extension.
- **LG-01h (Global nav restructure)** is the PLAN task that was supposed to do exactly this work
  later, as a deferred surface. LG-01f effectively eats LG-01h's lunch by shipping the dropdown +
  sidebar skeleton now.

The forcing question is whether to keep the LG-01c sidebar surface stable and defer the zengm-shaped
restructure to LG-01h, or replace the sidebar wholesale in LG-01f and let LG-01h be a placeholder-filling
follow-on.

## Decision

1. **Replace the LG-01c sidebar wholesale.** The new sidebar is a 14-entry template partial
   (`templates/_partials/league_sidebar.html`) with one top-level `Dashboard` entry above three
   sections: **LEAGUE** (Standings, Schedule, Playoffs, Finances, History, Power Rankings —
   6 entries; Schedule is added relative to the zengm shape because in this project the
   schedule is league-level — it's the full per-Season fixture list, not a per-team view as in
   zengm), **TEAM** (Roster, Schedule, Finances, History — 4 entries), **PLAYERS** (Free Agents,
   Trade, Trading Block — 3 entries). Exactly **4 entries are LIVE** at LG-01f merge time —
   `Dashboard`, `Standings`, `Schedule` (LEAGUE), `History` — and the remaining 10 entries
   render as disabled `<span>` placeholders. Live targets resolve via the existing LG-01c
   `displayed_season` rule (`league.active_season` if exists, else most-recent completed
   Season; the sidebar's Standings / Schedule entries become disabled `<span>` when the
   League has zero Seasons).

2. **Wire the new sidebar on every League-context page**, replacing the LG-01c-locked
   `season-dashboard-sidebar` on the season dashboard AND adding the same partial to the league
   dashboard, the new league-history page, the LG-01 season-standings page, and the LG-01
   season-schedule page. Five pages total. The LG-01c sidebar tests are rewritten against the
   new 13-entry shape; the LG-01c `sidebar_links` context shape is replaced.

3. **Add a top-bar `League ▾` dropdown to `templates/base.html`.** Always rendered (no
   conditional, no DB hit per request), inserted in the existing `<div class="navbar-nav
   ms-auto">` block. The dropdown has 5 items mirroring the sidebar LEAGUE section:
   `Standings`, `Playoffs`, `Finances`, `History`, `Power Rankings`. **Only History is a live
   `<a href>`** — the other 4 are disabled placeholder entries. The History link's
   `<int:league_id>` is resolved context-aware: if `request.session["last_league_id"]` exists
   AND the League still exists, that id; else if there is exactly one League in DB, that id;
   else the link points to `/leagues/` (the LG-01a league list). Stateless after the session
   read, no per-request DB hit unless the session value needs validation.

4. **LG-01f is a stepping stone toward LG-01h; LG-01h's scope is broader than just
   placeholder-filling.** LG-01f ships the skeleton (one `League ▾` top-bar dropdown
   replacing the LG-01a `Leagues` link, plus the 13-entry sidebar on 5 pages with only 4
   entries live). LG-01h's scope when it lands covers (a) restructuring `templates/base.html`
   to a **mode-based navigation** — only a start-page link + global Help/Tools dropdowns at
   the top level, with league-related entries living inside the League dropdown / sidebar,
   sandbox-related entries inside a Sandbox dropdown / section, and multiplayer-related
   entries inside a Multiplayer dropdown / section once that mode exists; (b) flipping each
   of LG-01f's 9 disabled sidebar entries + 4 disabled top-bar items to live as their
   underlying features ship (LG-02 → Playoffs; future tasks → Finances / Power Rankings /
   Trade / Free Agents / Trading Block); and (c) additional nav entries beyond the 12 LG-01f
   shipped, captured in screenshots the user will add to the repo before LG-01h is picked
   up. LG-01f deliberately avoids any base.html restructure beyond inserting the one
   dropdown — the broader mode-based shape is LG-01h's territory.

## Rejected alternatives

- **Keep LG-01c sidebar; ship only the history page + a single new link.** This is the
  pure PLAN-literal interpretation: one new view, one new template, no sidebar / top-bar
  changes. The user explicitly asked for the zengm-shaped navigation; this option was
  rejected in the grilling.

- **Match LG-01c's 5-entry shape, append 4 fake entries underneath.** Keep the LG-01c
  Overview / Standings / Schedule / Teams / History entries unchanged and append disabled
  Playoffs / Finances / Power Rankings / League History below. Less disruptive than the
  full replacement but the LG-01c-locked `sidebar_links` shape (5 entries in pinned order)
  becomes 9 entries, breaking the LG-01c sidebar length assertion. And the zengm shape (3
  sections) is lost — the result reads as "LG-01c's sidebar plus an awkward tail," not as
  the zengm pattern the user requested.

- **Ship only the History entry LIVE; disable Standings + Schedule too.** All 12 (or 13)
  sidebar entries except History rendered as disabled placeholders. The user picked this
  initially, then reversed when warned that LG-01c's existing Standings + Schedule sidebar
  links would regress to placeholders post-LG-01f. The reversal is what option 1 above
  encodes — Dashboard / Standings / Schedule / History live, the other 9 disabled.

- **Top-bar dropdown rendered only when a League exists / only on League-context pages.**
  Both options were considered. The "only when League exists" branch adds a DB hit (or a
  session-cached flag that risks staleness) per page render. The "only on League-context
  pages" branch requires every relevant view to inject a flag, or a middleware that
  pattern-matches the URL — adds surface area. Always-rendered + context-aware History
  href is the cheapest path that handles the orphan-dropdown UX gracefully (the link
  resolves to `/leagues/` instead of a 404).

- **Write a full ADR for the LG-01c sidebar at LG-01c time.** LG-01c did not write an ADR
  for the 5-entry sidebar — it was locked in the seam contract only. That precedent was
  followed throughout LG-01a / LG-01b / LG-01c / LG-01d / LG-01e (none wrote ADRs for
  their view-shape decisions). LG-01f's wholesale-replacement-plus-cross-page-rollout
  crosses the ADR threshold the earlier tasks did not: the LG-01c sidebar is the
  load-bearing surface 5 pages now consume, and re-deciding the shape after merge would
  require editing every page + every test. The decision is therefore hard-to-reverse,
  surprising-without-context, and the result of a real trade-off (defer to LG-01h vs.
  pull forward), satisfying all three ADR criteria.

## Consequences

- **LG-01c sidebar tests get rewritten.** `TestSeasonDashboardSidebar` and its 5-entry
  assertions are replaced with assertions against the 13-entry shape. The
  `season-dashboard-sidebar-history` disabled-`<span>` test is updated to assert against
  the new live History link (top-level, no longer Season-scoped).

- **`sidebar_links` context shape is replaced wholesale.** The LG-01c `_season_sidebar_links`
  helper is deleted; a new module-level helper `_build_league_sidebar_links(league,
  displayed_season, sidebar_active) -> list[dict]` returns the 13-entry list. Every page
  that renders the sidebar adds the helper to its context.

- **`sidebar_active` vocabulary expands from 1 value (`"overview"`) to a 14-value enum.**
  Locked literals: `"dashboard"`, `"standings"`, `"schedule"` (matches the LEAGUE Schedule
  entry), `"playoffs"`, `"finances"`, `"history"`, `"power_rankings"`, `"roster"`,
  `"schedule_team"`, `"finances_team"`, `"history_team"`, `"free_agents"`, `"trade"`,
  `"trading_block"`, `None` (no entry active).

- **LG-01h's PLAN.md description expands.** The entry's title (`LG-01h · Global nav
  restructure`) stays; the description is extended to record that LG-01f shipped a
  partial skeleton (one dropdown + a 13-entry sidebar with 9 disabled placeholders) and
  that LG-01h must (a) restructure `templates/base.html` to mode-based navigation
  (League / Sandbox / Multiplayer dropdowns owning their own nav, with only start-page +
  Help/Tools globally), (b) flip the 9 disabled sidebar entries + 4 disabled top-bar
  items to live as features ship, and (c) absorb additional nav entries beyond the 12
  LG-01f shipped (screenshots pending from the user before LG-01h is picked up). The
  `LG-01h` id stays stable; cross-references remain valid.

- **Per-League discoverability is now strong.** Top-bar dropdown is always present,
  sidebar is on every League-context page, History page has its own dedicated entry
  point. No user has to know the URL pattern to reach LG-01f's content.

- **Pagination matches LG-00c.** The history table uses the LG-00c `?per_page=10|25|50|100`
  selector pattern with default 10, mirroring the player list page exactly. No new
  pagination helper module — reuse the LG-00c approach inline.

### LG-01h extension (2026-05-28)

LG-01h extends but does not supersede this ADR — §1 (replace LG-01c sidebar wholesale),
§2 (LEAGUE > Schedule divergence-from-zengm), §3 (top-bar `League ▾` dropdown), and §4
(LG-01f as stepping stone toward LG-01h) all continue to hold. LG-01h fulfils §4's
promise. The additional consequences:

- **Sidebar shape extends from 14 to 23 entries.** The LG-01f-locked 14-entry list
  (1 top + 6 LEAGUE + 4 TEAM + 3 PLAYERS) grows by 3 PLAYERS additions (Prospects,
  Watch List, Hall of Fame) and an entirely NEW 6-entry STATS section (Game Log,
  League Leaders, Player Ratings, Player Stats, Team Stats, Statistical Feats). The
  shape is `1 top + 6 LEAGUE + 4 TEAM + 6 PLAYERS + 6 STATS = 23`. The helper
  `matches.views._build_league_sidebar_links` is extended in-place (signature
  unchanged, body returns 23 entries) — NOT renamed to a `_v2` (LG-01g precedent).
  Every page that already renders the LG-01f sidebar partial picks up the 23-entry
  shape automatically; zero per-page template edits required.

- **`sidebar_active` enum extends from 14+`None` to 23+`None`.** Adds `"prospects"`,
  `"watch_list"`, `"hall_of_fame"`, `"game_log"`, `"league_leaders"`,
  `"player_ratings"`, `"player_stats"`, `"team_stats"`, `"statistical_feats"`. The
  LG-01g `_team` suffix collision-rule (LEAGUE > Schedule keeps `"schedule"`; TEAM >
  Schedule uses `"schedule_team"`) extends unchanged; the 9 new keys introduce zero
  new collisions.

- **Mode-based base.html branching via `core.context_processors.app_mode`.** A NEW
  context processor (appended to the LG-01f-created `core/context_processors.py`,
  not a new file) classifies every request via the path-prefix rule
  `request.path.startswith("/leagues/") or request.path.startswith("/seasons/")` ⇒
  `"league"`; everything else ⇒ `"sandbox"`. `templates/base.html` branches around
  `{% if app_mode == "league" %}` / `{% else %}`: league mode shows brand →
  `League ▾` → `Help ▾` → `Tools ▾`; sandbox mode shows brand → the 6 LG-01a flat
  sandbox links → `League ▾` → `Help ▾` → `Tools ▾`. Mode is path-driven only — no
  toggle UI, clicking a sandbox link from inside a League re-renders in sandbox
  mode automatically. Multiplayer mode is deferred (§1 still defers it).

- **19 placeholder pages + shared `coming_soon` view + `_FEATURE_REGISTRY`
  vocabulary.** Every disabled sidebar entry and disabled top-bar dropdown item
  from LG-01f flips to LIVE via 19 placeholder URLs (3 League-scoped + 3
  Team-scoped + 6 Players-scoped + 6 Stats-scoped + 6 Help + 4 Tools — minus the
  ones already LIVE) all routed to a single shared view
  `matches.views.coming_soon` rendering `templates/_placeholder.html` with an
  `<h1>{{ feature_label }}</h1>` and a `<p>` containing the locked substring
  `"Coming soon"`. The hard-coded module-level dict
  `matches.views._FEATURE_REGISTRY` (35 entries) maps each `feature_key` to a
  `{label, section, sidebar_active}` value-dict; `section ∈ {"league", "team",
  "players", "stats", "help", "tools"}`; Help / Tools entries set
  `sidebar_active=None` (their placeholders are sandbox-mode pages and render with
  an empty sidebar).

- **Page wiring property: zero per-page edits.** Because the sidebar partial
  signature + the helper signature + the `sidebar_links` context-key contract are
  all stable from LG-01f, the 6 existing dashboard / history / standings /
  schedule / team_schedule templates (`templates/leagues/dashboard.html`,
  `templates/leagues/history.html`, `templates/leagues/team_schedule.html`,
  `templates/seasons/dashboard.html`, `templates/seasons/standings.html`,
  `templates/seasons/schedule.html`) need no edit at LG-01h — they automatically
  render the 23-entry shape with the new STATS section header. The only modified
  templates are `templates/base.html` (mode branching + Help / Tools dropdowns)
  and `templates/_partials/league_sidebar.html` (STATS section header), plus the
  NEW `templates/_placeholder.html`.
