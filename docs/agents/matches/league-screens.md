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

**The 11 screens:** Power Rankings, Team Roster, Team History, Free Agents, Watch List, Game
Log, League Leaders, Player Ratings, Player Stats, Team Stats, Statistical Feats. Every
screen's query + row building lives in `matches/league_screen_services.py`, served by
`matches/league_screen_api_views.py` — see [`api.md`](api.md) **ANG-01e league screens API**.
The watch list is the per-Account `WatchedPlayer` model (LG-06f below).

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
**Tests:** `matches/tests/test_league_<screen>.py` per screen (pure-unit + purity
classes for the logic modules).

**LG-06a polish.** Team History paginates its **Players section only**; Overall + Seasons are
not paginated. Seam contract:
[`.claude/worktrees/lg-06a-seam-contract.md`](../../../.claude/worktrees/lg-06a-seam-contract.md).

**LG-06b polish.** Player Ratings / Player Stats / Statistical Feats gained an
"All Teams" + per-enrolled-team `team_id` filter. Enrolment is
`league_screen_services.enrolled_teams` / `enrolled_team_ids` (the activation snapshot, live
M2M only for a draft); the API params Form rejects a non-enrolled id (400).
Filter points differ: Ratings filters the queryset (`qs.filter(team_id=…)`, now
inside `build_player_ratings_queryset`), Stats filters rows post-`aggregate_player_stats` on
`PlayerStatRow.team_id`, Feats filters the seam **inputs** before
`stat_feats.scan_feats` (`player_rounds` by `team_id`, `matches` by red/blue id)
— `stat_feats.py` untouched. Read-only, no model/simulator change. Seam contract:
[`.claude/worktrees/lg-06b-seam-contract.md`](../../../.claude/worktrees/lg-06b-seam-contract.md).

**LG-06c polish.** Sortable columns sort in memory on the already-materialized rows — the
pure modules `stat_feats.py` / `team_history_logic.py` / `league_leaders_logic.py` (incl.
`LeaderRow`, whose `rank` stays the frozen metric standing) are untouched, sorted on their
OUTPUT. On the paginated Team History Players table the sort runs BEFORE pagination (global top
row leads). Key tuples are `None`-safe with a per-screen secondary tiebreak. Read-only, no
model/simulator change. Seam contract:
[`.claude/worktrees/lg-06c-seam-contract.md`](../../../.claude/worktrees/lg-06c-seam-contract.md).

**LG-06d polish.** Player Stats / Team Stats / League Leaders / Statistical Feats
/ Game Log / Power Rankings gained a `?season=` selector listing each of this
League's Seasons newest-first plus a **Career** entry (aggregate across all of
THIS League's Seasons); no `?season=` param keeps the current `displayed_season`
(backward-compatible). The API params Forms (`matches/api_forms.py`
`LeagueScreenParamsForm`) take `season` (blank ⇒ the displayed Season; a Season id of this
League; or the literal `"career"`; anything else is a 400) and `rate` (one of `"total"` /
`"per_game"` / `"per_10"`, blank ⇒ `"total"`); the season value feeds
`league_screen_services.resolve_season_scope`. Career is a **queryset switch** — each screen
swaps its round/match filter from
`...match__season=<season>` to `...match__season__league=league` and reuses its
existing pure aggregation module **verbatim** (`aggregate_player_stats`,
`team_stats_logic.aggregate_team_stats`, `league_leaders_logic.compute_leaderboards`,
`stat_feats.scan_feats`, the Game Log round-row build,
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
`apply_rate` → `team_id` filter → `sort_player_stats` → pagination, so sorting
runs on the **rate-adjusted** displayed value (ZenGM behaviour). **Team History is
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
(`TestNoDjangoImportsLeaked` retained). `league_screen_services` materialises the extended
per-(player,round) seam dicts — Opp / Result / Season computed outside the pure module, with
**per-Round** Result from `GameRound.red_points`/`blue_points` (NOT the Match
outcome), `mvp = float(prs.get_mvp)` (property) and
`accuracy = float(prs.get_accuracy())` (**method, call with `()`**), and
`nuke_detonations` from the existing `event_type="special"`/`points_awarded=500`
detonation pass (`is_nuke_detonation`) — then layers pagination (AFTER sort) and **expanded
LG-06c sort** over the full box-score column set (`league_screen_services.FEATS_SORT_KEYS`
frozenset of every descriptor + 13 box-score keys, the `feat_row_sort_value` extractor) with
**default sort = most recent first** (`-round`) and a `(round_id desc, player_id asc)`
secondary tiebreak; the Team-feats list is not paginated. Coexists with the LG-06b `team_id`
filter (applied to the seam INPUTS, `stat_feats.py` untouched by the filter) + the LG-06d
`season` selector (incl. Career). Read-only — no
model, migration, URL, simulator, RNG, Score Calibration re-baseline, GLOSSARY.md
edit (the **Statistical feat** term was already finalized), or ADR. Cross-cutting
Statistical-Feats reshape. Seam contract:
[`.claude/worktrees/lg-06e-seam-contract.md`](../../../.claude/worktrees/lg-06e-seam-contract.md).

## LG-06f watch list (+ watch flag)

The Watch List screen was reshaped from the 3-column bookmark table into the
**Player-Stats column set filtered to watched players** (zero-fill for watched
players with no Rounds in scope), a ZenGM-style **watch flag** landed on **8
league screens**, and watch lists became **per-League**. **No simulator, RNG, or Score
Calibration re-baseline** (GLOSSARY.md gained the **Watch list** + **Watch flag** terms; no
ADR). Seam contract:
[`.claude/worktrees/lg-06f-seam-contract.md`](../../../.claude/worktrees/lg-06f-seam-contract.md).

**Store (ANG-01e).** The watch list is the `WatchedPlayer` model, one row per (Account, League,
Player), read and written only through `league_screen_services` (`watched_player_ids` /
`add_watch` / `remove_watch` / `clear_watch_list`). Adding a Player on no enrolled Team of the
displayed Season and not in the free-agent pool is refused. The screens carry the flag as a
`watched` bool on each row; the API is `PUT` / `DELETE watch-list/<player_id>/` and
`DELETE watch-list/` (Remove All) — see [`api.md`](api.md) **ANG-01e league screens API**.

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

**Screen pipeline.** `league_screen_services.build_watch_list_rows` reuses the player_stats
machinery with the **locked pipeline**
`build_round_dicts → aggregate_player_stats → zero_fill_watched → apply_rate →
sort_player_stats` (`zero_fill_watched` runs BEFORE `apply_rate` so
zero rows take the same rate pass-through, where the `games <= 0 ⇒ 0.0` guard
leaves zeros as zeros). `identity_by_id` is built from
`Player.objects.filter(pk__in=watched_ids).select_related("team")` as `{pid:
{"player_name","team_id","team_name","role"}}`. The screen carries the full
Player-Stats kit **minus the team filter** (the Watch List is a personal
cross-team set): season selector (+ Career), rate toggle, sortable columns.

**The 8 flag screens:** Player Stats, Player Ratings, Free Agents, League Leaders (all 4
boards), Statistical Feats, Team Roster (both sections), Team History, and Watch List.
**Tests:** `matches/tests/test_league_watch_list.py` (screen + the pure `zero_fill_watched`
unit tests) and `matches/tests/test_api_watch_list.py`.

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
records/L5 as `(W,L,T)`); display strings and sort keys are derived outside it. `compute_standings(completed_matches, enrolled_teams,
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

**All 17 columns sortable (`matches/league_services.py::build_season_standings`).** Sort on
the materialized rows after `compute_standings` (`sort` a `STANDINGS_SORT_KEYS` member, the
17 keys; default `("rank","asc")`). Helpers `_standings_sort_value` / `_streak_sort_value` /
`_standings_row_attr` (attr-or-key adapter so the draft-preview dict rows sort
through the same path); record/L5 sort `(wins desc, losses asc)`, streaks by
signed run length, **`rank` stays frozen** (never renumbered — LG-06c
League-Leaders precedent). `season_rounds` is built from
`GameRound.objects.filter(match__season=season).values(…)`, with `date_played` added
to the Match dicts; the draft-preview branch emits the 8 new fields zeroed and still sorts.
Tests: `matches/tests/test_standings.py` (pure-unit), `matches/tests/test_league_services.py`
(`TestBuildSeasonStandings`) and `matches/tests/test_api_seasons.py` (`TestSeasonStandings`).

## LG-06h league player page

A read-only **League player page** — the in-League destination of every
player-name link on the 8 LG-06f league screens, mirroring the ZenGM player
profile pinned to one League. Read-only — **no model, migration, simulator, RNG,
or Score Calibration re-baseline; no ADR**; GLOSSARY.md already carries the
**League player page** term. Seam contract:
[`.claude/worktrees/lg-06h-seam-contract.md`](../../../.claude/worktrees/lg-06h-seam-contract.md).

**Service + API.** `league_screen_services.build_player_detail(league, player) ->
PlayerDetail` (`rs_rows`, `career_row`, `player_awards`, `ratings_history`), served by
`GET /api/leagues/<id>/players/<player_id>/`. **LENIENT:** any valid `(League, Player)` pair
succeeds; a Player with Rounds in zero of this League's Seasons gets `rs_rows == []` and
`career_row is None` (a current free agent with no League Rounds is not a 404).

**RS aggregation — no new pure module.** One League-wide `build_round_dicts` pass over
`{"game_round__match__season__league": league, "player_id": player.id}` (PERF-01), grouped per
Season; per-Season rows newest-first via `aggregate_player_stats` (only Seasons the Player has
Rounds in), plus the Career-in-League row from the same list (`year = "Career"`). **Per-Season
Team is derived from the player's actual Rounds that Season** — the aggregated row's last-seen
`team_name`/`team_id` (resolved per-Round inside `build_round_dicts` from `team_color` against
`game_round.team_red`/`team_blue`), NOT the current `Player.team` — so a dropped/transferred
player shows the team they played for. MVP/accuracy come from `get_mvp` / `get_accuracy` inside
`build_round_dicts`. The table is **not sortable and not paginated** (a single player across a
handful of Seasons). Row dict shape (per-Season and Career identical): `{"year": str,
"season_id": int | None, "team_name": str, "team_id": int, "games": int,
"stats": Mapping[str, float]}` — Career uses `year = "Career"`, `season_id =
None`, and the league-wide last-seen team. Tests:
`matches/tests/test_league_player_detail_queries.py`.
