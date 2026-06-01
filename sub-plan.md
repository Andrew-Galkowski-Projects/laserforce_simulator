# sub-plan.md — Sidebar placeholder backlog (LG-01z)

This document expands the **LG-01z** entry in `PLAN.md`. It lists every
"coming soon" placeholder rendered by the league sidebar partial
(`templates/_partials/league_sidebar.html` / `_build_league_sidebar_links`,
introduced by LG-01f and expanded to 23 entries by LG-01h) that does **not**
yet have a corresponding feature task in `PLAN.md`.

## Scope and exclusions

The 23-entry sidebar contains 19 placeholder entries. Of those:

- **Playoffs** — covered by `LG-02 · Tournament formats` in `PLAN.md`. Not
  listed here.

The remaining **17 placeholders** are each addressed by an entry below.
Help / Tools top-bar dropdowns (LG-01h) are out of scope for this sub-plan —
they belong to the global nav surface, not the league sidebar.

## Status (shipped 2026-05-30)

11 of the 17 shipped as real read-only screens in one parallel batch; the 6
blocked on unbuilt models now render an explainer page with a dependency note.

**✅ Live screens** (sidebar entry repointed from `coming_soon_*` to the real
URL; each has its own view in `matches/league_screens/`, an optional pure-logic
module, a `templates/leagues/<screen>.html` template, and a
`test_lg01z_<screen>.py` test file):

- LG-01z-b Power Rankings — `league_power_rankings` (sortable columns)
- LG-01z-c Team Roster — `team_roster` (current-team default + picker)
- LG-01z-e Team History — `team_history` (3 tabs: Overall / Seasons / Players)
- LG-01z-f Free Agents — `players_free_agents` (read-only sortable list)
- LG-01z-j Watch List — `players_watch_list` (session-scoped, GET add/remove)
- LG-01z-l Game Log — `stats_game_log`
- LG-01z-m League Leaders — `stats_league_leaders` (4 boards, top-10 each)
- LG-01z-n Player Ratings — `stats_player_ratings`
- LG-01z-o Player Stats — `stats_player_stats`
- LG-01z-p Team Stats — `stats_team_stats`
- LG-01z-q Statistical Feats — `stats_statistical_feats`

**🚧 Explainer stub** (still routes through `coming_soon`; `_FEATURE_REGISTRY`
entry gained a `blocker` note rendered on `_placeholder.html`):

- LG-01z-a League Finances — needs a salary / contract model (no PLAN entry yet)
- LG-01z-d Team Finances — needs the salary / contract model (LG-01z-a)
- LG-01z-g Trade — needs cap-space validation (salary model)
- LG-01z-h Trading Block — needs `Player.on_trading_block` + the Trade builder
- LG-01z-i Prospects — needs **LG-05** player potential
- LG-01z-k Hall of Fame — needs **LG-03** awards + **LG-04** stat updates

Seam contract: `.claude/worktrees/lg-01z-seam-contract.md`. All read-only — no
model change, no migration, no simulator touch.

## Conventions

Each entry below:

- Names the sidebar `key` it replaces (from `_FEATURE_REGISTRY` /
  `_build_league_sidebar_links`).
- Names the placeholder URL it supersedes (the existing `coming_soon_*`
  route from LG-01h).
- Sketches the minimum scope to turn the placeholder into a real page.
- Notes any dependency on other PLAN tasks where the feature cannot ship
  before that task lands.

These are **scope sketches**, not seam contracts. Each item is expected to
go through its own grilling session (CONTEXT.md update, ADR if applicable,
seam contract under `.claude/worktrees/`) before implementation.

---

## LEAGUE section

### LG-01z-a · League Finances (`league_finances`)

Replaces `coming_soon_finances` at `/leagues/<int:league_id>/finances/`.

Per-League salary cap / team budget surface. Read-only first cut: list
each enrolled Team with its current payroll, cap room, and recent
transactions. Write-side (sign / release / trade) is deferred to the
Player-economy entries below.

**Depends on:** a `Player.contract` / `Player.salary` field (new model
work, no PLAN entry yet) and a `Team.cap_space` derivation. Ships its own
migration, ADR, and CONTEXT.md terms (**Salary cap**, **Payroll**,
**Cap room**).

### LG-01z-b · Power Rankings (`league_power_rankings`)

Replaces `coming_soon_power_rankings` at
`/leagues/<int:league_id>/power-rankings/`.

Ordered list of enrolled Teams ranked by a derived strength index
(weighted combination of recent W-L record, points-for / against, average
MVP per Round, opponent-adjusted margin). Updated when the underlying
Matches change; no schedule cadence in v1.

**Depends on:** the LG-01 standings + the existing `Match` / `GameRound`
aggregates. Pure aggregation module — no model change, no migration.
Likely a new CONTEXT.md term (**Power ranking**).

---

## TEAM section

These three entries are the *League-context* per-Team surfaces (sidebar
key resolved via the LG-01g `_resolve_current_team_for_sidebar` chain).
They are distinct from the existing sandbox `/teams/<id>/` detail page.

### LG-01z-c · Team Roster (`team_roster`)

Replaces `coming_soon_team_roster` at
`/leagues/<int:league_id>/team/<int:team_id>/roster/`.

League-context roster view: starting six (the existing `slot_*` FKs)
plus the bench (`Team.bench_players`). Read-only first cut. Editing the
lineup (move slot ↔ bench, sign free agents) is the write-side counterpart
and belongs with the Free Agents / Trade entries below.

**Depends on:** existing `Team.active_players` / `Team.bench_players`
properties. No new model work for the read-only view.

### LG-01z-d · Team Finances (`team_finances`)

Replaces `coming_soon_team_finances` at
`/leagues/<int:league_id>/team/<int:team_id>/finances/`.

Per-Team payroll detail: salary cap room, per-Player contract amounts and
remaining years, projected cap room for the next Season.

**Depends on:** LG-01z-a (the League-level Finances surface introduces
the contract / salary model; LG-01z-d is the per-Team drilldown).

### LG-01z-e · Team History (`history_team`)

Replaces `coming_soon_team_history` at
`/leagues/<int:league_id>/team/<int:team_id>/history/`.

League-scoped Team history: one row per Season the Team enrolled in, with
its final standing, points-for / against, championship if any, top
MVP. Distinct from the existing `/matches/team/<int:team_id>/history/`
sandbox view, which lists every sandbox Match.

**Depends on:** LG-01 `Season.starting_team_ids_json` snapshot +
`Season.champion_team` FK. Pure read-only view.

---

## PLAYERS section

These six entries are the player-economy slice that `PLAN.md` line 663
flagged as "no PLAN entry yet — propose Phase 5.8 'Player economy' if
later wanted". Each ships separately because the model and UI surfaces
diverge, but they share underlying contract / signing infrastructure.

### LG-01z-f · Free Agents (`players_free_agents`)

Replaces `coming_soon_free_agents` at
`/leagues/<int:league_id>/players/free-agents/`.

Sortable list of every `Player` with no Team in this Season (the
LG-00 / LG-00c Free Agents pool, scoped to the current League). Write
action: sign a Free Agent onto the user's Team (career mode) or onto a
chosen Team (sandbox admin). Replaces the manual admin-only roster path
today.

**Depends on:** existing LG-00c sortable Players index +
`Season.teams` M2M filter. Write action depends on the contract /
salary model (LG-01z-a) so signings hit cap space.

### LG-01z-g · Trade (`players_trade`)

Replaces `coming_soon_trade` at `/leagues/<int:league_id>/players/trade/`.

Two-Team trade builder: pick player(s) on each side, validate cap-balance
constraints, commit the trade as an atomic swap of `slot_*` / bench
assignments. AI Team acceptance (will Team B accept this trade?) is a
deferred sub-task — v1 commits both sides unconditionally as the user is
managing both Teams.

**Depends on:** LG-01z-a (cap validation) + CONTEXT.md term **Trade**.

### LG-01z-h · Trading Block (`players_trading_block`)

Replaces `coming_soon_trading_block` at
`/leagues/<int:league_id>/players/trading-block/`.

List of Players each Team has marked as "available for trade". Filterable
by role, salary, overall rating. Each entry deep-links into the LG-01z-g
Trade builder pre-populated with that Player on one side.

**Depends on:** a `Player.on_trading_block: BoolField` (one new field,
one migration). LG-01z-g for the deep-link target.

### LG-01z-i · Prospects (`players_prospects`)

Replaces `coming_soon_prospects` at
`/leagues/<int:league_id>/players/prospects/`.

List of unsigned Players the user's Team has scouted. Surfaces the
`Player.potential` value (`LG-05 · Player potential`) and the scouting
budget's confidence interval. Sort by potential desc.

**Depends on:** **LG-05** (player potential model + scouting budget). No
new model work in this entry itself.

### LG-01z-j · Watch List (`players_watch_list`)

Replaces `coming_soon_watch_list` at
`/leagues/<int:league_id>/players/watch-list/`.

Per-user pinned list of Players to keep an eye on, scoped to the current
League. Add / remove via a single-action button on Player detail pages.

**Depends on:** **UX-01** (user accounts) — the watch list is per-user.
Until UX-01 lands, this entry could ship as a session-scoped list.

### LG-01z-k · Hall of Fame (`players_hall_of_fame`)

Replaces `coming_soon_hall_of_fame` at
`/leagues/<int:league_id>/players/hall-of-fame/`.

List of retired Players honoured for career achievements (championships,
career stat thresholds, awards). Inductees are derived from
`PlayerRoundState` career aggregates + the LG-03 awards corpus.

**Depends on:** **LG-03** (season-end awards) + **LG-04** (season-end
stat updates) so career aggregates and awards exist to derive from. Adds
a `Player.retired: BoolField` (one migration) and CONTEXT.md term **Hall
of Fame**.

---

## STATS section

The STATS section is **entirely new** at LG-01h merge time. All six
entries are placeholders. None of them have an LG-XX feature today.

### LG-01z-l · Game Log (`stats_game_log`)

Replaces `coming_soon_game_log` at
`/leagues/<int:league_id>/stats/game-log/`.

League-wide chronological game log: every `GameRound` in the current /
displayed Season, with score, winner, matchday, optional filter by Team.
Read-only.

**Depends on:** existing `Match` / `GameRound` ORM. No new model work.
**Distinct from** the LG-01g per-Team schedule view — that is one Team's
fixture list (played + upcoming); LG-01z-l is the *played* game log
across the whole Season.

### LG-01z-m · League Leaders (`stats_league_leaders`)

Replaces `coming_soon_league_leaders` at
`/leagues/<int:league_id>/stats/league-leaders/`.

Full per-stat leaderboard — extends the LG-01c dashboard's top-3
snippets (Points / Tags / Tag ratio) to the full enrolled-Player set,
sortable by every stat the simulator records on `PlayerRoundState`.
Currently the LG-01c dashboard renders a raw "View all leaders" `<a>` at
`/leagues/<id>/leaders/` that 404s — LG-01z-m's URL replaces that.

**Depends on:** existing LG-01c `matches/season_dashboard.py`
`compute_leaders` helper + a small extension to the stat vocabulary.

### LG-01z-n · Player Ratings (`stats_player_ratings`)

Replaces `coming_soon_player_ratings` at
`/leagues/<int:league_id>/stats/player-ratings/`.

Sortable per-Player view of the 19 stat ratings (`accuracy`, `survival`,
`speed`, …) plus `overall_rating`. League-scoped — only Players
participating in the current Season are listed. The sandbox-scope
equivalent is LG-00c at `/players/`.

**Depends on:** existing `Player` model + LG-00c forgiving-fallback
`?sort=&dir=` pattern.

### LG-01z-o · Player Stats (`stats_player_stats`)

Replaces `coming_soon_player_stats` at
`/leagues/<int:league_id>/stats/player-stats/`.

Sortable per-Player **performance** view (points scored, tags made,
times tagged, MVP, accuracy %, …) aggregated across every `GameRound`
in the current Season. League-scoped equivalent of HX-01 player career
stats.

**Depends on:** existing HX-01 / HX-02 aggregations + a Season-scoped
queryset filter.

### LG-01z-p · Team Stats (`stats_team_stats`)

Replaces `coming_soon_team_stats` at
`/leagues/<int:league_id>/stats/team-stats/`.

Sortable per-Team statistical breakdown: avg points-for, avg
points-against, avg margin, avg survivors, total tags landed, total
times tagged, base captures, missiles fired / hit, nukes fired /
landed, cancelled-nuke count. Season-scoped.

**Depends on:** existing `Match` / `GameRound` / `PlayerRoundState`
aggregations.

### LG-01z-q · Statistical Feats (`stats_statistical_feats`)

Replaces `coming_soon_statistical_feats` at
`/leagues/<int:league_id>/stats/statistical-feats/`.

Notable individual / team accomplishments: triple-nuke games, 0-tagged
shutouts by a Medic, perfect-accuracy Heavy rounds, highest single-game
MVP, longest tag streak, etc. Each feat is a derived predicate over
`PlayerRoundState` + `GameEvent` rows. Read-only list with deep-links to
the originating Round.

**Depends on:** existing `GameEvent` log + a pure helper module per feat
predicate. CONTEXT.md term **Statistical feat** likely worth adding.

---

## Cross-cutting notes

- **Sub-league rotation** (mode (b) per-sub-league rotating map pools)
  remains scoped to **SUB-01** in `PLAN.md` — not duplicated here.
- The 17 entries above are **independent of LG-01h's mode-based base.html
  restructure** — each one only flips its sidebar `key`'s entry from
  `coming_soon_*` to its real URL, with the rest of the sidebar (other
  disabled entries) unchanged.
- Each entry above is expected to:
  1. Carve its CONTEXT.md domain language additions in its own grill.
  2. Add an ADR if it introduces a hard-to-reverse choice (most do not).
  3. Land its own seam contract under `.claude/worktrees/`.
  4. Update `_FEATURE_REGISTRY` in `matches/views.py` to flip its entry
     from a placeholder to a live URL name.
- Sequencing is **not pinned** — Finances (LG-01z-a) + Player-economy
  (LG-01z-f..k) form one natural sub-cluster; STATS (LG-01z-l..q) is
  another. TEAM-section entries (LG-01z-c..e) need at least LG-01z-a
  for the per-Team Finances surface to be meaningful.
