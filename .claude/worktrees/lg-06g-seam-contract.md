# LG-06g Seam Contract — Standings form + side detail

Single source of truth. Adds 8 columns to Season Standings and makes **all 17 columns sortable** via the LG-06c pattern. Read-only — no model change, no migration, no simulator/RNG touch, no Score Calibration re-baseline, no ADR, no CONTEXT.md edit (the **Standings form** + **Side split** terms were added during grilling).

## Files
| File | Change |
|---|---|
| `matches/standings.py` | EXTEND `StandingsRow` (+8 fields) + `compute_standings` (+1 param). |
| `matches/league_views.py` | EXTEND `season_standings`: query `season_rounds`, `date_played` on match dicts, view-side sort, new context keys, new module helpers. |
| `templates/seasons/standings.html` | 17 LG-06c sort headers + 17 `<td>`. |
| `matches/tests/test_standings.py` | EXTEND pure-unit (signature change updates all callsites). |
| `matches/tests/test_season_views.py` | EXTEND view/DOM tests. |

## StandingsRow — 17 fields (pinned order)
Existing 9 (`team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank`) + 8 appended after `rank`:
```
match_streak: tuple[str,int]   # (kind,length); kind in {"W","L","T",""}, length>=0
match_l5: tuple[int,int,int]   # (W,L,T) over last 5 completed Matches
round_streak: tuple[str,int]
round_l5: tuple[int,int,int]
red_wlt: tuple[int,int,int]    # (W,L,T) of Rounds physically played RED
blue_wlt: tuple[int,int,int]
red_points_for: int            # total points scored while physically RED
blue_points_for: int
```
Dataclass holds STRUCTURED NUMERICS ONLY; template formats display (`("W",3)`→`"W3"`, `("",0)`→`"—"`, records→`"5-4-0"`); view derives sort keys.

## compute_standings(completed_matches, enrolled_teams, season_rounds)
- `completed_matches` dict — 9 keys: + `date_played` (orders Matches by `(date_played, match_id)` asc).
- `season_rounds` dict — 6 keys: `round_id, team_red_id, team_blue_id, red_points, blue_points, date_played` (PHYSICAL sides per SIM-08; orders by `(date_played, round_id)` asc). Carries EVERY persisted Season Round incl. in-progress Matches.

## Two corpora
- Match-grain (existing W/L/T/Pts/RW/TS + `match_streak` + `match_l5`) → completed Matches only.
- Round-grain (`round_streak`, `round_l5`, `red_wlt`, `blue_wlt`, `red_points_for`, `blue_points_for`) → every Season Round.

## Round result / side split
red wins iff `red_points>blue_points`; blue iff `blue_points>red_points`; tie iff equal. `red_wlt`/`red_points_for` from rounds the team physically played red (`team==team_red_id`); blue symmetric; a team aggregates into both. `round_streak`/`round_l5` = team's own W/L/T regardless of side. NEVER use Match-level `red_*`/`blue_*` (team-position-keyed).

## Sort (LG-06c), view-side in `season_standings`
- `_STANDINGS_SORT_KEYS` frozenset (17 keys) + `_STANDINGS_SORT_KEYS_DISPLAY` `(key,label)` tuple; default `("rank","asc")` ⇒ today's order unchanged.
- `_coerce_sort_key` (`league_views`) + `_coerce_dir` (`teams.views`, newly imported).
- `_standings_sort_value(row, team_name, key)` + `_streak_sort_value(streak)` + `_standings_row_attr(row, key)` (attr-or-key so draft dicts AND dataclasses work). Records/L5 sort `(wins, -losses)`; streak signed length; tiebreak `team_id`. `rank` stays FROZEN (never renumbered).
- Context adds `sort, dir, sort_keys, querystring_without_sort_dir`.

## Template
Thead = sort-header loop over `sort_keys` (game_log pattern, DOM id `season-standings-th-<key>`, ` ↑`/` ↓` glyphs). 17 `<td>` in column order. Streak cell `{% if row.X.0 %}{{ row.X.0 }}{{ row.X.1 }}{% else %}—{% endif %}`; record cells `{{ row.X.0 }}-{{ row.X.1 }}-{{ row.X.2 }}`. Preserved ids `season-standings-table`/`-empty`/`-draft-preview-banner`/`season-state-badge`.

## Draft-preview branch
Each zeroed dict gains the 8 keys: streaks `("",0)`, L5/records `(0,0,0)`, points `0`. Sorts via the same path.

## Test boundary
- `test_standings.py` (pure-unit, no DB): every `compute_standings(...)` callsite gains a 3rd arg (`[]` for Match-only cases); helper gains `date_played`; new classes for both grains + side split + ordering + tiebreak + in-progress-round inclusion; `TestNoDjangoImportsLeaked` unchanged.
- `test_season_views.py` (view/DOM): all 17 `<th>` ids; default rank order; `?sort=wins&dir=desc` reorders but rank frozen; cells render expected display strings; draft renders 17 cols zeroed + sortable; invalid `?sort` → rank.

## Scope-out
Read-only; no RNG/simulation/`_flush_to_db`/SIM-07-08; no model/migration/ADR; CONTEXT.md already done; pure module's frozen allowlist (`dataclasses`/`typing`/`collections`) unchanged.
