# ZenGM (LOL GM) vs. Laserforce — League Screen Comparison

Per-page comparison of our league-side screens against the reference product,
**LOL GM** (`lol.zengm.com`, a ZenGM-engine game). One file per screen/feature.

## How this was captured

- **Reference:** a LOL GM league at `http://lol.zengm.com/l/1` (app **v3.355**,
  build `2022.07.15.835`). The column inventory was first taken pre-season (zeros,
  but full column structure + controls render regardless); a **second pass played
  the league 2020→2027** so every stats table, the playoffs bracket, League
  History, and the Hall of Fame are populated, and a full year-cycle was walked
  with the **Play** button. Each screen's `<h1>`, `<select>` controls, tabs, and
  table `<thead>`/row structure were extracted from the DOM.
- **Ours:** read from `templates/leagues/*.html`,
  `templates/seasons/standings.html`, and `matches/league_screens/*.py`.
- **Domain gap:** ZenGM here is a League-of-Legends esports GM (kills / deaths /
  assists / creep score / towers / MMR / contracts); ours is laser tag. The team
  already wrote [`../../stats.md`](../../stats.md) mapping each ZenGM screen onto
  the Laserforce columns we chose and which screens are deferred. Divergences
  `stats.md` records as deliberate are flagged **Intentional**; everything else
  is a candidate gap.

## Legend

- **= Intentional** — deliberate divergence already documented in `stats.md`
  (LoL-only columns dropped; finances/draft/retirement screens deferred; MMR /
  Rank / Potential rendered as `-` proxies pending **STAT-PROXY-01**).
- **⚠ Gap** — a ZenGM capability we plausibly want and do not have yet.
- **▲ Layout/UX** — same information, materially different presentation.

## Per-page / per-feature docs

| Doc | Screen | Our route |
|---|---|---|
| [standings.md](standings.md) | Standings | `season_standings` |
| [power-rankings.md](power-rankings.md) | Power Rankings | `league_power_rankings` |
| [roster.md](roster.md) | Roster | `team_roster` |
| [team-history.md](team-history.md) | Team History | `team_history` |
| [team-stats.md](team-stats.md) | Team Stats | `stats_team_stats` |
| [free-agents.md](free-agents.md) | Free Agents | `players_free_agents` |
| [watch-list.md](watch-list.md) | Watch List | `players_watch_list` |
| [game-log.md](game-log.md) | Game Log | `stats_game_log` |
| [league-leaders.md](league-leaders.md) | League Leaders | `stats_league_leaders` |
| [player-ratings.md](player-ratings.md) | Player Ratings | `stats_player_ratings` |
| [player-stats.md](player-stats.md) | Player Stats | `stats_player_stats` |
| [statistical-feats.md](statistical-feats.md) | Statistical Feats | `stats_statistical_feats` |
| [season-lifecycle.md](season-lifecycle.md) | **Feature** — season phases + the Play button | LG-01c/d/e |

## Cross-cutting discrepancies

These recur across most screens; per-page docs reference them by id.

| # | ZenGM has | We have | Type | PLAN |
|---|-----------|---------|------|------|
| C1 | **Season selector** on nearly every stats screen | `?season=` selector (each Season + Career) on Player/Team Stats, League Leaders, Statistical Feats, Game Log, Power Rankings; Team History intentionally excluded (natively all-time) | ✓ Delivered (LG-06d) | LG-06d |
| C2 | **Rate toggle** Per Game / Per 36 / Totals | `?rate=` Totals / Per Game / **Per 10 min** on Player Stats (Per-10 replaces Per-36 — intentional laser-tag divergence) | ✓ Delivered (LG-06d) | LG-06d |
| C3 | **Regular Season / Playoffs** toggle | No playoffs yet | = Intentional | LG-02 |
| C4 | **Page-size selector** 10/25/50/100 on every paginated table | Prev/Next + "Page X of Y" only; Team History has none | ⚠ Gap | LG-06a |
| C5 | **Team filter** ("All Teams") on Ratings/Stats/Feats | None | ⚠ Gap | LG-06b |
| C6 | **All stat tables sortable** | Team History, Game Log, League Leaders, Watch List, Feats not sortable | ⚠ Gap | LG-06c |
| C7 | **Career Totals vs. season** toggle | **Career** entry in the `?season=` selector = league-scoped aggregate across all of this League's Seasons (view-side queryset switch) | ✓ Delivered (LG-06d) | LG-06d |
| C8 | **Grouped header rows** ("superCols") | Flat header | ▲ Layout (mostly LoL objective groups) | — |
| C9 | **Inline roster/FA actions** (Release, Negotiate, pick/ban, drag-reorder) | Read-only | = Intentional | finances deferred |
| C10 | **Top-nav dropdowns** League/Team/Players/Stats/Tools/Help | Same shape (LG-01k) | ✓ Matches | — |

## Top actionable gaps → PLAN.md

Filtering out intentional/deferred items, the new follow-up steps (all under
**`### LG-06`** in PLAN.md) are:

1. **LG-06a** — page-size selector on Free Agents / Player Ratings / Player Stats;
   add pagination to Team History (C4).
2. **LG-06b** — team filter on Player Ratings / Player Stats / Statistical Feats (C5).
3. **LG-06c** — sortable columns on Team History / Game Log / League Leaders /
   Watch List / Statistical Feats (C6).
4. **LG-06d** — season selector + rate/career toggles across stats screens
   (C1/C2/C7) — low value while leagues are single-season.
5. **LG-06e** — Statistical Feats as a per-game feed (one row per notable game)
   rather than fixed category bests ([statistical-feats.md](statistical-feats.md)).
6. **LG-06f** — Watch List as a full stats table; per-user persistence folds into
   **UX-01** ([watch-list.md](watch-list.md)).
7. **LG-06g** — Standings form/side detail: Streak / Last-5 / home-away split
   ([standings.md](standings.md)).

Cross-referenced to existing tasks: **MVP / Finals MVP awards on League History**
→ **LG-03**; **Playoffs / phase-aware Play menu** → **LG-02**; **Potential / MMR /
Rank columns** → **STAT-PROXY-01**. See [season-lifecycle.md](season-lifecycle.md)
for the structural (lifecycle) divergences.
