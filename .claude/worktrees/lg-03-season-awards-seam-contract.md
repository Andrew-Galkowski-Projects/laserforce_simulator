# LG-03 — Season-end awards — SEAM CONTRACT

Read-only / derived. **NO model change, NO migration, NO simulator change, NO Score
Calibration re-baseline.** All awards recomputed on render (transient) from frozen
`PlayerRoundState` rows. Mirrors the `matches/season_player_stats.py` /
`matches/league_leaders_logic.py` pure-module precedent and the LG-01z league-screen
view shell. Verified against the live repo (model fields, DOM ids, property-vs-method,
helper signatures) before writing.

---

## 0 · Locked names (quick index)

| Kind | Name |
|---|---|
| Pure module | `matches/season_awards.py` (allowlist: `dataclasses`, `typing`, `collections` ONLY) |
| Dataclass — winner | `AwardWinner(player_id, player_name, role, team_id, team_name, value)` (`frozen=True`, 6 fields, pinned order) |
| Dataclass — set | `AwardSet(most_points, best_accuracy, kd_by_role, best_medic, most_efficient_nuke, season_mvp, finals_mvp)` (`frozen=True`, 7 fields, pinned order) |
| Pure fn | `compute_season_awards(player_rounds: list[dict], *, min_games: int) -> AwardSet` |
| Pure fn | `pick_finals_mvp(final_round_dicts: list[dict]) -> AwardWinner | None` |
| Purity test | `matches/tests/test_season_awards.py::TestNoDjangoImportsLeaked` |
| View | `matches.league_views.season_awards(request, season_id)` |
| URL name / path | `season_awards` / `/seasons/<int:season_id>/awards/` (in `matches/season_urls.py`) |
| Template | `templates/seasons/awards.html` (extends `base.html`, `d-flex` + `_partials/league_sidebar.html` shell) |
| History helper (CHANGED) | `matches.league_views._build_history_row` — 11 → 13 keys |
| Player-page view (CHANGED) | `matches.league_screens.player_detail.player_detail` |
| Player-page template (CHANGED) | `templates/leagues/player_detail.html` (fill the `league-player-awards-stub`) |

---

## 1 · Pure module `matches/season_awards.py`

**Frozen import allowlist (LOCKED):** `dataclasses`, `typing`, `collections` ONLY.
**NO** Django / ORM / `random` / `datetime` / I/O / logging. Defended by a subprocess
fresh-import + `sys.modules` walk (`TestNoDjangoImportsLeaked`) — mirror
`matches/season_player_stats.py` + `matches/league_leaders_logic.py` (READ both for
style/shape; the latter reuses `LeaderRow` from `season_dashboard`, but LG-03 declares
its OWN dataclasses — do NOT reuse `LeaderRow`).

### 1.1 Dataclasses (pinned field order)

```python
@dataclass(frozen=True)
class AwardWinner:
    player_id: int
    player_name: str
    role: str
    team_id: int
    team_name: str
    value: float

@dataclass(frozen=True)
class AwardSet:
    most_points:        Optional[AwardWinner]          # any role; total — NOT gated
    best_accuracy:      Optional[AwardWinner]          # any role; rate — gated
    kd_by_role:         dict[str, Optional[AwardWinner]]  # 5 keys; per-role; NOT gated
    best_medic:         Optional[AwardWinner]          # medic only; total — NOT gated
    most_efficient_nuke: Optional[AwardWinner]         # commander only; rate — gated
    season_mvp:         Optional[AwardWinner]          # any role; rate — gated
    finals_mvp:         Optional[AwardWinner]          # set by view via pick_finals_mvp
```

- Every winner slot is `Optional[AwardWinner]` → `None` when no qualifying player.
- `kd_by_role` is a **mapping with exactly 5 keys**, one entry PER role —
  `{"commander": …, "heavy": …, "scout": …, "medic": …, "ammo": …}` — each value an
  `Optional[AwardWinner]` (`None` when no player played that role). These are the 5
  role strings as stored on `PlayerRoundState.role`.
- `compute_season_awards` returns an `AwardSet` with `finals_mvp=None`; the **view**
  re-builds the set (or `dataclasses.replace(...)`) to attach the `pick_finals_mvp`
  result. (Pin the attach mechanism at code time; the seam is "view computes
  finals_mvp separately and stamps it onto the set".)

### 1.2 Seam dict (input to `compute_season_awards`) — EXACT required keys

One dict per `PlayerRoundState` row, built VIEW-SIDE. **Required keys (LOCKED):**

```
player_id, player_name, role, team_id, team_name,
points_scored, tags_made, times_tagged, accuracy, mvp,
resupplies_given, specials_used, own_specials_cancelled
```

- `accuracy` is pre-computed view-side from **`PlayerRoundState.get_accuracy`** — a
  `@property` (NO parens), integer percent 0–100. Stored `float(...)` per the
  `season_player_stats` precedent.
- `mvp` is pre-computed view-side from **`PlayerRoundState.get_mvp`** — a `@property`
  (NO parens), `float`.
- `own_specials_cancelled` sources from the `PlayerRoundState` field of the same name
  (the Commander's nukes that were cancelled — confirm the exact field name at code
  time; the seam key is `own_specials_cancelled`).

### 1.3 `compute_season_awards(player_rounds, *, min_games) -> AwardSet`

- `player_rounds`: flat `list[dict]` of the seam dicts above (one per round-appearance).
- `min_games`: keyword-only `int`. The **view passes**
  `min_games = ceil(max_games_any_player / 2)` (the qualifier — the view computes
  `max_games_any_player` over the grouped rows; the pure fn receives the resolved int).
  Empty input ⇒ every slot `None`, all 5 `kd_by_role` entries `None`.

Group rows by `player_id`; `games(player)` = count of rows for that player. Per the
`league_leaders_logic` precedent, "last row wins" for displayed name/role/team (the
view passes rows id-ascending).

**Award metric definitions (pinned):**

| Award | Metric | Scope | Gated? |
|---|---|---|---|
| `most_points` | `SUM(points_scored)` | any role | NO |
| `best_accuracy` | `MEAN(accuracy)` | any role | YES (rate) |
| `kd_by_role[role]` | `SUM(tags_made) / max(SUM(times_tagged), 1)` | per role, 1 winner each (5) | NO |
| `best_medic` | `SUM(resupplies_given)` | `role == "medic"` only | NO |
| `most_efficient_nuke` | `(SUM(specials_used) − SUM(own_specials_cancelled)) / max(SUM(specials_used), 1)` | `role == "commander"` only | YES (rate) |
| `season_mvp` | `MEAN(mvp)` | any role | YES (rate) |

**Qualifier (LOCKED):** the **rate / mean** awards — `season_mvp`, `best_accuracy`,
`most_efficient_nuke` ONLY — require `games(player) >= min_games`. The **total / count**
awards (`most_points`, `best_medic`, `kd_by_role`) are **NOT gated**.

**Tiebreak ladder (deterministic, LOCKED):** primary metric value → `games_played`
desc → `player_id` asc. (Same shape as `league_leaders_logic._rank`'s
`(value_sign*value, -games_played, player_id)`.)

### 1.4 `pick_finals_mvp(final_round_dicts) -> AwardWinner | None`

- `final_round_dicts`: `list[dict]`, one per `PlayerRoundState` over the championship
  Match's rounds — **same per-round dict shape** as §1.2; the **`mvp` key is required**
  (the other keys may be present for identity).
- Returns the `AwardWinner` for the player with the best **MEAN(`get_mvp`)** over those
  rounds (value = that mean), tiebroken by the same ladder. Empty input ⇒ `None`.

---

## 2 · View `matches.league_views.season_awards(request, season_id)`

URL `/seasons/<int:season_id>/awards/`, URL name **`season_awards`**, added to
`matches/season_urls.py` (bare name, no `app_name`) **alongside** `season_standings` /
`season_schedule` (READ `matches/season_urls.py` — same file, no new include).

**View shell (mirror `season_standings` + the LG-01z screen contract):**
- `season = get_object_or_404(Season, pk=season_id)` (404 on missing).
- **GET-only:** `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`
  as the first line (the LG-01z / `league_history` idiom — note `season_standings`
  itself omits it, but LG-03 LOCKS the 405 guard).
- `request.session["last_league_id"] = season.league_id` (int; mirrors
  `season_standings`).
- `league = season.league`; `displayed_season = season`;
  `sidebar_links = _build_league_sidebar_links(league, displayed_season,
  sidebar_active=None)` (no sidebar entry matches the awards page — every entry
  inactive, the `player_detail` precedent).

**Regular-season corpus (LOCKED ORM):**
`PlayerRoundState.objects.filter(game_round__match__season=season)` — playoff Matches
carry `season=NULL` (Part2c-1 #3) so they are naturally excluded. (Confirmed:
`Match.season` is the FK; the Part2c-3f `team_history` corpus precedent reaches playoff
rounds via `match__series_match__node__tournament__season_phases` — LG-03 does NOT
want those, so it uses the plain `match__season` join.) Build the flat seam dicts by
reading `get_accuracy` / `get_mvp` (properties), `role`, and team via `team_color`
against `game_round.team_red` / `game_round.team_blue` — **mirror
`matches.league_screens.player_stats._build_round_dicts`** (READ it; reuse its
team-resolution pattern; LG-03's dict carries the §1.2 key set, a different subset).
Order rows `id` ascending (last-row-wins determinism).

Compute `min_games = ceil(max_games_any_player / 2)` where `max_games_any_player` is the
max per-player row count over the corpus (`0`/empty ⇒ `min_games = 0`, all slots `None`).
Call `compute_season_awards(round_dicts, min_games=min_games)`.

### 2.1 Finals MVP corpus (identification rule + ORM path — LOCKED)

`finals_mvp` is set **only when** the Season's tournament/playoff phase exists **AND**
its tournament's `format` is a **BRACKET** format:
`{single_elimination, double_elimination, round_robin_double_elim}`. For
`round_robin` / `swiss` phases, and no-playoff Seasons ⇒ `finals_mvp = None`.

**Navigation (READ `matches/models.py` — confirmed names):**
`season.ordered_phases()` → the `SeasonPhase` with `phase_type == "tournament"` and
`phase.tournament_id is not None` → `phase.tournament` (a `Tournament`). Check
`tournament.format` against the bracket set above.

**Deciding node = the terminal bracket node WON BY THE CHAMPION:**
- `single_elimination` & `round_robin_double_elim` ⇒ the `BracketNode` with
  `advances_to_id is None` whose `winner_id == tournament.champion_id`.
- `double_elimination` ⇒ the grand-final node **GF2** (`bracket_type == "grand_final"`,
  `advances_to_id is None`), or **GF1** when GF2 is inert/bye (the Bracket reset was
  skipped — GF2 has `is_bye` / no real Series). (Confirmed fields: `BracketNode.winner`
  / `winner_id`, `advances_to` / `advances_to_id`, `bracket_type`, `is_bye`;
  `Tournament.champion` / `champion_id`, `Tournament.format`,
  `SeasonPhase.tournament`.)

**Finals-MVP rounds = ALL `GameRound`s of ALL `SeriesMatch` rows on the deciding node**
(a node holds a best-of-N Series — confirmed `BracketNode.series_matches` →
`SeriesMatch.match` → `match.game_rounds` → `PlayerRoundState`). Build the per-round
dicts (the `mvp` key required) and call `pick_finals_mvp(...)`; stamp the result onto
the `AwardSet`.

### 2.2 Context + DOM

Context keys (frozen): `season`, `league`, `displayed_season`, `sidebar_links`,
`sidebar_active` (= `None`), `awards` (the `AwardSet`), plus whatever the template
needs to iterate `kd_by_role` (the 5 role strings).

**LOCKED DOM ids (awards page):**

| DOM id | Renders |
|---|---|
| `season-awards-table` | the outer awards `<table>` (present when ≥ 1 award winner) |
| `season-awards-most-points` | `awards.most_points` row |
| `season-awards-best-accuracy` | `awards.best_accuracy` row |
| `season-awards-kd-{role}` | one per role — `kd-commander`, `kd-heavy`, `kd-scout`, `kd-medic`, `kd-ammo` |
| `season-awards-best-medic` | `awards.best_medic` row |
| `season-awards-most-efficient-nuke` | `awards.most_efficient_nuke` row |
| `season-awards-season-mvp` | `awards.season_mvp` row |
| `season-awards-finals-mvp` | `awards.finals_mvp` row |
| `season-awards-empty-notice` | rendered when the Season has **no completed regular-season rounds** |

A `None` winner renders the em-dash `—` (the LG-01f / `player_detail` convention).

### 2.3 Entry points

- **Season dashboard** (`templates/seasons/dashboard.html`): a link to the awards page,
  DOM id **`season-dashboard-awards-link`** (`{% url 'season_awards' season.id %}`).
- **League History** (`templates/leagues/history.html`): a per-row link to that
  Season's awards page, DOM id **`league-history-awards-link-{season_id}`**
  (`{% url 'season_awards' row.season_id %}`).

---

## 3 · League History row — `_build_history_row` 11 → 13 keys (BLAST RADIUS)

`matches.league_views._build_history_row(season, teams_by_id, *, is_in_progress)`
currently returns **exactly 11 keys** (CONFIRMED at `league_views.py:1399`):
`season_id, season_name, season_url, start_date, teams_enrolled, matches_played,
champion, runner_up, tournament_champion, top_three, is_in_progress`.

**LG-03 grows it to 13 — append `season_mvp` and `finals_mvp`**, each an
`AwardWinner | None`:

```python
return {
    "season_id": ..., "season_name": ..., "season_url": ...,
    "start_date": ..., "teams_enrolled": ..., "matches_played": ...,
    "champion": ..., "runner_up": ..., "tournament_champion": ...,
    "top_three": ..., "is_in_progress": ...,
    "season_mvp": <AwardWinner | None>,   # NEW
    "finals_mvp": <AwardWinner | None>,   # NEW
}
```

**Query cost (LOCKED, acceptable):** `_build_history_row` currently issues **zero
queries** (consumes the `season.matches` prefetch + `teams_by_id`). Computing
`season_mvp` / `finals_mvp` needs a per-season `PlayerRoundState` query. This is
**acceptable on the paginated (10-row) League History page** — at most 10 extra queries
per page. **REUSE policy (LOCKED):** the History row **reuses `compute_season_awards`**
(building the same regular-season seam dicts) and takes only its `.season_mvp`, plus the
`pick_finals_mvp` path for `.finals_mvp` — NOT a lighter bespoke helper. (Factor the
regular-season-dicts + finals-corpus assembly out of `season_awards` view into a shared
private helper at code time so the view and the History row call one path.)

**Template change (`templates/leagues/history.html`):** the 10-column per-row body
gains **2 columns** — **Season MVP** and **Finals MVP** — appended **after the existing
top-3 / Tournament-Champion cells** (rightmost). New DOM-id substrings on the cells:
`league-history-season-mvp-{season_id}` and `league-history-finals-mvp-{season_id}`. A
`None` value renders the em-dash `—`.

**BLAST RADIUS — `matches/tests/test_league_history.py`:** the existing 11-key shape
assertions (`TestLeagueHistoryCompletedRows`) and the per-row **column-count**
assertions break and **must be updated to the 13-key / +2-column shape**. Call this out
explicitly in the test plan.

---

## 4 · Player badge — fill the `league-player-awards-stub` (LG-06h)

**View `matches.league_screens.player_detail.player_detail`** (READ it — current
context keys: `league, player, displayed_season, sidebar_links, sidebar_active,
rs_rows, career_row, stat_columns`). LG-03 adds the per-player award list for THIS
league:

- Iterate this league's Seasons (`league.seasons...`); per Season run
  `compute_season_awards(...)` (over that Season's regular-season corpus, the same
  shared helper as §2/§3) **plus** the finals-MVP path; keep the awards whose winner's
  `player_id == player.id`.
- New context key (LOCKED shape):
  `player_awards: list[dict]` — one entry per Season in which the player won ≥ 1 award:
  `{"season_id": int, "season_name": str, "award_labels": list[str]}`
  (`award_labels` = the human labels of the awards this player won that Season, e.g.
  `["Season MVP", "Best Medic", "K/D — Scout", "Finals MVP"]`).

**Template `templates/leagues/player_detail.html`:** REPLACE the existing
`league-player-awards-stub` block (CONFIRMED at lines 213–218, currently
`<h3>Awards</h3> … Coming soon — awaiting an awards model.`). The live element that
**replaces** the stub keeps the DOM id **`league-player-awards`** (the stub id
`league-player-awards-stub` is removed; the live id is `league-player-awards`). It
renders `player_awards` (one block per Season, the award labels). When `player_awards`
is empty, render an empty notice inside `league-player-awards`.

**LOCKED:** the global career page (HX-01, `player_career_stats` /
`/players/<id>/stats/`) stays **UNCHANGED**.

---

## 5 · Test boundary

### 5.1 Pure-unit — `matches/tests/test_season_awards.py`
Over `compute_season_awards` / `pick_finals_mvp` only (NO DB):
- each of the 7 award metrics on hand-built seam-dict lists;
- the per-role K/D — one winner per role, 5 `kd_by_role` entries, `max(SUM(times_tagged),1)`
  clamp;
- the qualifier — `min_games` gates ONLY `season_mvp` / `best_accuracy` /
  `most_efficient_nuke`; `most_points` / `best_medic` / `kd_by_role` ungated;
- the tiebreak ladder (metric → games desc → player_id asc);
- empty input ⇒ all `None` (incl. all 5 `kd_by_role` entries);
- `pick_finals_mvp` best-mean / empty-input;
- `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk).

### 5.2 DB view tests — `matches/tests/test_season_awards_view.py` (NEW)
- awards view 200 / 404 (missing season) / 405 (non-GET);
- empty-state (`season-awards-empty-notice`) for a Season with no completed
  regular-season rounds;
- all LOCKED DOM ids present (`season-awards-table`, `-most-points`, `-best-accuracy`,
  the 5 `-kd-{role}`, `-best-medic`, `-most-efficient-nuke`, `-season-mvp`,
  `-finals-mvp`);
- `last_league_id` session write;
- finals-MVP set on a bracket-format playoff phase, `None` on `round_robin`/`swiss`/no
  playoff.

### 5.3 DB tests — extend existing files
- `matches/tests/test_league_history.py` — **update** the 11-key shape + column-count
  assertions to the 13-key / +2-column shape; assert `season_mvp` / `finals_mvp` cells
  + the per-row awards link.
- `matches/tests/test_league_player_detail.py` — the player-page awards block
  (`league-player-awards` live id replacing `league-player-awards-stub`, `player_awards`
  context, label rendering, empty notice).

### 5.4 Assertion discipline (LOCKED)
Assert on award **WINNER identity / values / DOM ids / row shape** — **NEVER** on exact
simulated point totals. Use **hand-built `PlayerRoundState` rows** (or deterministic
seeded fixtures) for DB tests. (Tournament/playoff sims are non-deterministic — finals
corpus tests hand-construct the bracket → `SeriesMatch` → `Match` → `GameRound` →
`PlayerRoundState` chain.)

---

## 6 · Scope-out (LOCKED — do NOT build)
No model field, no migration, no simulator change, no Score Calibration re-baseline, no
ADR, no new CONTEXT.md term, no change to the global HX-01 career page, no persisted
award rows (every award recomputed on render), no awards caching, no API/DRF endpoint.

---

### Verified-against-repo names (do not drift)
- `PlayerRoundState.get_accuracy` — **`@property`, NO parens**, int 0–100 (`models.py:693`).
- `PlayerRoundState.get_mvp` — **`@property`, NO parens**, `float` (`models.py:701`).
- `Tournament.format` choices include `single_elimination`, `double_elimination`,
  `round_robin`, `round_robin_double_elim`, `swiss`; `Tournament.champion` (`champion_id`),
  `Tournament.state` (`models.py:1655–1672, 1676`).
- `BracketNode.winner` (`winner_id`), `.advances_to` (`advances_to_id`), `.bracket_type`
  (`winners`/`losers`/`grand_final`/`round_robin`/`swiss`), `.is_bye`, `.series_matches`
  (`models.py:2381–2473, 2494`).
- `SeriesMatch.match` → `Match.game_rounds`; `SeriesMatch.node` (`models.py:2488–2502`).
- `SeasonPhase.phase_type` (`round_robin`/`tournament`/`member_night`), `.tournament`
  (`tournament_id`), `.ordinal` (`models.py:1527–1590`).
- `Season.ordered_phases()` (`models.py:1391`), `Season.champion_team` (`models.py:924`),
  `Season.league` (`league_id`).
- `_build_history_row` returns 11 keys at `league_views.py:1399`.
- `_build_round_dicts` team-resolution precedent: `league_screens/player_stats.py:93`.
- `_build_league_sidebar_links(league, displayed_season, sidebar_active=…)` signature:
  used by every `league_screens/*` view (e.g. `player_detail.py:78`).
- `season_standings` / `matches/season_urls.py` shell precedent (bare URL names,
  `last_league_id` session write).
- Stub block `league-player-awards-stub` at `player_detail.html:213`.
