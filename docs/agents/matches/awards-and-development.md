# matches: Awards and player development

LG-03 season awards, LG-04 player development + ratings history, LG-05 potential.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-03 season-end awards

A **read-only / derived** league screen: per-Season awards recomputed **on render
(transient)** from frozen `PlayerRoundState` rows — **NO model change, NO migration,
NO simulator change, NO Score Calibration re-baseline, NO persisted award rows, NO
caching**. Mirrors the `matches/season_player_stats.py` / `matches/league_leaders_logic.py`
pure-module precedent and the LG-01z league-screen view shell. A new Django-free pure
module `matches/season_awards.py` computes the award set; the view does ALL ORM work and
hands the pure fn a flat `list[dict]`. The **regular-season corpus** is
`PlayerRoundState.objects.filter(game_round__match__season=season)` — season-embedded
**playoff** Matches carry `season=NULL` (the Part2c-1 #3 decision) and are therefore
naturally **excluded** from the regular-season awards; **Finals MVP** is computed
separately over the championship bracket node's rounds (below). Extends no ADR (no ADR
for this slice) and adds **no new GLOSSARY.md term** (the **Season award** / **Season MVP**
/ **Finals MVP** glossary entries were finalised during grilling). Seam contract:
[`.claude/worktrees/lg-03-season-awards-seam-contract.md`](../../../.claude/worktrees/lg-03-season-awards-seam-contract.md).

**Pure module `matches/season_awards.py`.** Frozen import allowlist — `dataclasses`,
`typing`, `collections` ONLY; **NO** Django / ORM / `random` / `datetime` / I/O / logging,
defended by `matches/tests/test_season_awards.py::TestNoDjangoImportsLeaked` (subprocess
fresh-import + `sys.modules` walk, mirroring `season_player_stats.py` /
`league_leaders_logic.py`). It declares its OWN `frozen` dataclasses (does **not** reuse
`LeaderRow`): `AwardWinner(player_id, player_name, role, team_id, team_name, value)` (6
fields, pinned order) and `AwardSet(most_points, best_accuracy, kd_by_role, best_medic,
most_efficient_nuke, season_mvp, finals_mvp)` (7 fields, pinned order). Every winner slot
is `Optional[AwardWinner]` → `None` when no qualifying player; `kd_by_role` is a mapping
with **exactly 5 keys**, one per role (`commander`/`heavy`/`scout`/`medic`/`ammo` — the
strings stored on `PlayerRoundState.role`), each an `Optional[AwardWinner]`. The view
builds one seam dict per round-appearance with the required keys `player_id, player_name,
role, team_id, team_name, points_scored, tags_made, times_tagged, accuracy, mvp,
resupplies_given, specials_used, own_specials_cancelled` — `accuracy` pre-computed from
`PlayerRoundState.get_accuracy` (a `@property`, NO parens, int 0–100) and `mvp` from
`PlayerRoundState.get_mvp` (a `@property`, NO parens, `float`), both stored as `float(...)`
per the `season_player_stats` precedent.

**The 7 awards + metrics (pinned).** `compute_season_awards(player_rounds, *, min_games)`
groups rows by `player_id` (last-row-wins for displayed name/role/team, rows passed
id-ascending) and fills six slots; the view stamps the seventh (`finals_mvp`) separately
(rebuilding the set, e.g. `dataclasses.replace(...)`). Metrics: `most_points` =
`SUM(points_scored)` (any role); `best_accuracy` = `MEAN(accuracy)` (any role);
`kd_by_role[role]` = `SUM(tags_made) / max(SUM(times_tagged), 1)` (per role, one winner
each → the 5 entries); `best_medic` = `SUM(resupplies_given)` (`role == "medic"` only);
`most_efficient_nuke` = `(SUM(specials_used) − SUM(own_specials_cancelled)) /
max(SUM(specials_used), 1)` (`role == "commander"` only); `season_mvp` = `MEAN(mvp)` (any
role). **Rate-award qualifier (LOCKED):** the **mean / rate** awards — `season_mvp`,
`best_accuracy`, `most_efficient_nuke` ONLY — require `games(player) >= min_games`; the
**total / count** awards (`most_points`, `best_medic`, `kd_by_role`) are **NOT gated**. The
view passes `min_games = ceil(max_games_any_player / 2)` (`max_games_any_player` = max
per-player row count over the corpus; `0`/empty ⇒ `min_games = 0` and every slot `None`,
all 5 `kd_by_role` entries `None`). **Tiebreak ladder (deterministic):** primary metric
value → `games_played` desc → `player_id` asc (the `league_leaders_logic._rank` shape).

**Finals MVP (`pick_finals_mvp`) — bracket-format-only.** `finals_mvp` is set **only when**
the Season has a tournament/playoff phase **AND** that tournament's `format` is a **bracket**
format — `{single_elimination, double_elimination, round_robin_double_elim}`; for
`round_robin` / `swiss` phases and no-playoff Seasons it is `None`. Navigation (per
`matches/models.py`): `season.ordered_phases()` → the `SeasonPhase` with
`phase_type == "tournament"` and `tournament_id is not None` → `phase.tournament`, then check
`tournament.format`. **Deciding node = the terminal bracket node won by the champion:** for
`single_elimination` and `round_robin_double_elim`, the `BracketNode` with
`advances_to_id is None` whose `winner_id == tournament.champion_id`; for
`double_elimination`, the grand-final node **GF2** (`bracket_type == "grand_final"`,
`advances_to_id is None`), or **GF1** when GF2 is inert/bye (the bracket reset was skipped —
GF2 `is_bye` / no real Series). The Finals-MVP rounds are ALL `GameRound`s of ALL
`SeriesMatch` rows on the deciding node (`BracketNode.series_matches` → `SeriesMatch.match` →
`match.game_rounds` → `PlayerRoundState`); `pick_finals_mvp(final_round_dicts)` returns the
`AwardWinner` with the best `MEAN(get_mvp)` over those rounds (same tiebreak ladder; empty
input ⇒ `None`). The view stamps the result onto the `AwardSet`.

**View + URL + page shell.** `matches.league_views.season_awards(request, season_id)` at
URL name **`season_awards`** / path **`/seasons/<int:season_id>/awards/`** (added to
`matches/season_urls.py` alongside `season_standings` / `season_schedule`, bare name, no new
include). It `get_object_or_404(Season, …)` (404 on missing), LOCKS a GET-only 405 guard
(`HttpResponseNotAllowed(["GET"])`) as its first line, writes
`request.session["last_league_id"] = season.league_id`, and renders
`templates/seasons/awards.html` (extends `base.html`, the `d-flex` +
`_partials/league_sidebar.html` shell) with `sidebar_links =
_build_league_sidebar_links(league, displayed_season, sidebar_active=None)` (no sidebar
entry matches the awards page, the `player_detail` precedent). It resolves teams via
`team_color` against `game_round.team_red`/`team_blue`, mirroring
`matches.league_screens.player_stats._build_round_dicts` (now
`league_screen_services.build_round_dicts`). **LOCKED DOM ids:**
`season-awards-table` (outer table, present when ≥ 1 winner), `season-awards-most-points`,
`season-awards-best-accuracy`, `season-awards-kd-{role}` (the 5 — `kd-commander`/`kd-heavy`/
`kd-scout`/`kd-medic`/`kd-ammo`), `season-awards-best-medic`,
`season-awards-most-efficient-nuke`, `season-awards-season-mvp`, `season-awards-finals-mvp`,
and `season-awards-empty-notice` (when the Season has no completed regular-season rounds). A
`None` winner renders the em-dash `—` (the LG-01f / `player_detail` convention). Entry-point
links: `season-dashboard-awards-link` on `templates/seasons/dashboard.html` and
`league-history-awards-link-{season_id}` per row on `templates/leagues/history.html` (both
`{% url 'season_awards' … %}`).

**League History row — `_build_history_row` 11 → 13 keys (blast radius).**
`matches.league_views._build_history_row(season, teams_by_id, *, is_in_progress)` grows from
**11 keys** to **13** — appends `season_mvp` and `finals_mvp` (each `AwardWinner | None`).
**REUSE policy (LOCKED):** the History row **reuses `compute_season_awards`** (building the
same regular-season seam dicts) and takes only its `.season_mvp`, plus the `pick_finals_mvp`
path for `.finals_mvp` — NOT a lighter bespoke helper; the regular-season-dicts +
finals-corpus assembly is factored out of the `season_awards` view into a **shared private
helper** so the view and the History row call ONE path. (PERF-01: the History callers now
batch the page's award sets through `compute_season_award_sets`, so the page runs a constant
number of queries however many Seasons it shows; see `league-foundation.md` § PERF-01.) The template
(`templates/leagues/history.html`) gains **2 rightmost columns** — Season MVP and Finals MVP —
appended after the top-3 / Tournament-Champion cells, with cell DOM-id substrings
`league-history-season-mvp-{season_id}` / `league-history-finals-mvp-{season_id}` (`None` →
`—`). **Blast radius:** `matches/tests/test_league_history.py` — the existing 11-key shape and
per-row column-count assertions (`TestLeagueHistoryCompletedRows`) break and are updated to the
13-key / +2-column shape.

**League player page — `league-player-awards-stub` → live `league-player-awards`.**
`matches.league_screens.player_detail.player_detail` adds a new context key `player_awards:
list[dict]` — one entry per Season in which this player won ≥ 1 award in THIS league:
`{"season_id": int, "season_name": str, "award_labels": list[str]}` (`award_labels` = the
human labels of the awards the player won that Season, e.g. `["Season MVP", "Best Medic",
"K/D — Scout", "Finals MVP"]`). It iterates the league's Seasons, runs the same shared helper
(`compute_season_awards` + the finals-MVP path) per Season, and keeps the awards whose
winner's `player_id == player.id`. `templates/leagues/player_detail.html` **replaces** the
existing `league-player-awards-stub` block (the "Coming soon — awaiting an awards model."
placeholder) with a live element that keeps the DOM id **`league-player-awards`** (the
`-stub` suffix is removed), rendering one block per Season; when `player_awards` is empty it
renders an empty notice inside `league-player-awards`. The global HX-01 career page
(`player_career_stats` / `/players/<id>/stats/`) stays **UNCHANGED**.

**Scope-out (LOCKED).** NO `PlayerRoundState` / `Match` / any model field, NO migration, NO
simulator change, NO Score Calibration re-baseline, NO ADR, NO new GLOSSARY.md term, NO change
to the global HX-01 career page, NO persisted award rows (every award recomputed on render),
NO awards caching, NO API/DRF endpoint.

**Locked names (NEW / CHANGED).**
- `matches/season_awards.py` — **NEW** pure module (allowlist `dataclasses`, `typing`,
  `collections`): `AwardWinner` / `AwardSet` (frozen dataclasses),
  `compute_season_awards(player_rounds: list[dict], *, min_games: int) -> AwardSet`,
  `pick_finals_mvp(final_round_dicts: list[dict]) -> AwardWinner | None`.
- `matches.league_views.season_awards(request, season_id) -> HttpResponse` — **NEW** view.
- `season_awards` / `/seasons/<int:season_id>/awards/` — **NEW** URL name / path in
  `matches/season_urls.py`.
- `templates/seasons/awards.html` — **NEW** template.
- `matches.league_views._build_history_row` — **CHANGED** (11 → 13 keys: `+ season_mvp`,
  `+ finals_mvp`); `templates/leagues/history.html` gains 2 columns + the awards link.
- `matches.league_screens.player_detail.player_detail` — **CHANGED** (`+ player_awards`
  context); `templates/leagues/player_detail.html` — **CHANGED** (`league-player-awards-stub`
  → live `league-player-awards`).

### UXP-09 completed-Season summary snapshot

LG-03's "no awards caching" is superseded for **completed** Seasons. A completed Season's
corpus never changes (ADR-0029), so `matches/season_snapshot.py::completed_season_cached`
stores three whole-Season outputs in `Season.summary_snapshot_json` (migration 0065), written
lazily on the first read (which also backfills older Seasons): `"awards"`
(`compute_season_award_set` — Awards page, League History, League player page),
`"leaderboards"` (`build_leaderboards` for a Season scope; Career is never cached) and
`"dashboard_leaders"` (`build_dashboard_context`). An in-progress Season is never cached.
Player / Team names are re-read live on load, so a rename shows up; a stored row naming a
deleted Player / Team, or a snapshot whose `"v"` is not `SNAPSHOT_VERSION`, is recomputed.
Bump `SNAPSHOT_VERSION` when a stored shape changes. Tests:
`matches/tests/test_season_summary_snapshot.py`.

## LG-04 player development + ratings history

The **first persisted mutation of `Player` stats in the league flow** — every prior LG slice
was read-only/derived. At each per-League `next_season` rollover, every Player in the rolling
League's **developing set** is aged one year and run through a **ZenGM-faithful age curve**
that mutates their 19 live `Player` stat fields in place, and one immutable
`PlayerSeasonRating` snapshot row is written for the new Season. Development is driven
**purely by age** — `Player.total_games` is kept but **cosmetic** (ticked at rollover, never
an input to the develop math). Implements [ADR-0024](../../adr/0024-zengm-player-development-ratings-history.md)
(the **Player development** / **Ratings history** GLOSSARY.md terms are finalised there — this
slice adds **no new GLOSSARY.md term**). **NO Score Calibration re-baseline** (LG-04 mutates
Stat *inputs* but changes no simulation *mechanic*, so the calibration targets are untouched).
Seam contract:
[`.claude/worktrees/lg-04-player-development-seam-contract.md`](../../../.claude/worktrees/lg-04-player-development-seam-contract.md).

**Model `PlayerSeasonRating` (`matches/models.py`).** Declared **immediately after the
`Season` class and before `SeasonPhase`** (it FKs both `teams.Player` and `matches.Season`;
`Season` is defined above it, `Player` already imported at the top of the file). An immutable
per-Season snapshot of a Player's 19 stat ratings, age, and overall at the start of a Season —
a **baseline** row at `league_create` (as-generated stats, no development) and a **developed**
row at each `next_season`. A **read-only audit trail**: the live `teams.Player` stat fields
remain the Simulator's source of truth; these rows are **never read back by the engine**.
Fields: the **19 stat ints**, names **byte-for-byte identical to `Player`** — including the
intentional **capital-O `Offensive_synergy`** (every other stat lowercase; source of truth
`teams.player_generator._STAT_FIELDS`), **no validators** on the snapshot ints (the writer
always supplies clamped `[0,100]` values); `age` = `IntegerField(null=True, blank=True)`
(mirrors `Player.age`, which is nullable); `overall_rating` = `FloatField` (matches
`Player.overall_rating`'s `sum/len` float); `potential` = `FloatField(null=True, blank=True)`,
**reserved for LG-05 — always written as `None` in LG-04**. FKs `player` / `season` are both
`on_delete=CASCADE` (deleted Player or Season drops its snapshots — disposable derived data,
the ADR-0004 posture), related_names `Player.season_ratings` / `Season.player_ratings`.
Constraint `unique(player, season)` named **`uniq_player_season_rating`** (one row per player
per season — re-running a rollover must not duplicate). `Meta.ordering = ["player_id",
"season_id"]`.

**Migration `matches/migrations/0048_playerseasonrating.py`.** Exactly **one
`CreateModel(PlayerSeasonRating)`** carrying all 21 fields + the `UniqueConstraint` +
`Meta.ordering`. **NO `RunPython`, NO `RunSQL`, NO backfill** — existing Leagues/Seasons get no
historical rows (the ADR-0004 disposable-data posture, the `0029`/`0041`/`0042`/`0047`
precedent). Dependency `("matches", "0047_seasonphase_tournament_subconfig")` (the current
latest matches migration); the `teams.Player` FK dependency is resolved automatically by
`makemigrations`.

**Pure module `matches/development.py` (Django-free).** Frozen import allowlist — `dataclasses`,
`typing`, `random`, `collections` ONLY; **NO** Django / ORM / `datetime` / `math` / I/O /
logging. The 19 stat field NAMES are **hand-rolled locally** (NOT imported from `teams`/Django,
mirroring `matches/draw.py` / `matches/bracket.py` / `teams/roster_importer.py`), pinned equal
to `teams.player_generator._STAT_FIELDS` by a single allowed test-only import. Defended by
`matches/tests/test_development.py::TestNoDjangoImportsLeaked` (subprocess fresh-import +
`sys.modules` walk — no module name starts with `django`). `random` is allowlisted because the
develop math consumes an **injected** `random.Random`; production builds a **fresh
`random.Random()` per rollover** and **stores NO seed** — the `PlayerSeasonRating` row IS the
audit trail. The develop math, per ZenGM's `developSeason` with coaching fixed at **0**:
- **`base_change(age) -> int`** — the ZenGM age base-change table (LOCKED, tunable): `≤21 → +2`,
  `22–25 → +1`, `26–27 → 0`, `28–29 → −1`, `30–31 → −2`, `32–34 → −3`, `35–40 → −4`,
  `41–43 → −5`, `≥44 → −6`. A `None` age never reaches it — the view coalesces `None` → `25`
  before calling.
- **`base_change_noise(age, rng) -> float`** — age-banded gaussian (young players volatile,
  veterans predictable): `≤23 → bound(gauss(0,5), −4, 20)`, `24–25 → bound(gauss(0,5), −4, 10)`,
  `≥26 → bound(gauss(0,3), −2, 4)`. **One draw per player per rollover**, shared across all 19
  stats. The **effective base change** is `base_change(age) + base_change_noise(age, rng)`.
- **19-stat archetype map** `_STAT_ARCHETYPE` — each stat assigned a ZenGM archetype group:
  **`awareness`** (6: `player_awareness`, `game_awareness`, `resource_awareness`,
  `decision_making`, `positioning`, `adaptability` — cognitive/court-sense, climb young, fade
  last), **`athletic`** (3: `stamina`, `speed`, `flexibility` — physical tools peak early,
  decline fast), **`team`** (6: `communication`, `teamwork`, `Offensive_synergy`,
  `defensive_synergy`, `midfield_synergy`, `resupply_synergy` — coordination, slow steady
  curve), **`skill`** (3: `resupply_efficiency`, `accuracy`, `special_usage` — trained mechanics
  hold late), **`durable`** (1: `survival` — neutral, follows base change only). 6+3+6+3+1 = 19.
  The mapping is **invented-by-analogy, locked-but-tunable** (no laser-tag regression data).
  Per group, `age_modifier(group, age) -> float` and `change_limits(group, age) -> (lo, hi)`
  fix that group's age behaviour (awareness has a widening early upper cap `7 + 5*(24-age)` for
  `age ≤ 24`; athletic crashes `(-12, 2)`; durable is effectively unbounded `(-100, 100)`).
- **`develop_stat(current, stat_name, age, effective_base_change, rng) -> int`** — one stat's
  developed value: `delta = (effective_base_change + age_modifier(group, age)) *
  rng.uniform(0.4, 1.4)`, bound to `change_limits(group, age)`, then `_clamp_int(current +
  delta)` (round-then-floor to `[STAT_MIN=0, STAT_MAX=100]`, banker's rounding per the
  `teams/career_stats.py` precedent).
- **`develop_player_stats(stats, age, rng) -> dict[str, int]`** — develops all 19 stats for one
  (already-aged) age; returns a fresh 19-key dict. **Pinned RNG order:** the noise draw (1
  `gauss`) happens **first**, then the 19 `develop_stat` calls draw their `uniform(0.4, 1.4)` in
  `STAT_FIELDS` order — exactly 1 gauss + 19 uniform draws per player per rollover, so a seeded
  RNG is reproducible.
- **`free_agent_games_tick(median_active, rng) -> int`** — the ONLY pure piece of the cosmetic
  `total_games` tick: `rng.randint(0, max(0, median_active) // 2)` (degenerate no-active case
  `median_active == 0` returns `0` deterministically).

Everything in `development.py` is pure (RNG injected); the view owns all ORM work
(developing-set query, appearance-count + median, `Player` mutation/save, `PlayerSeasonRating`
writes).

**`league_create` baseline write — `_write_baseline_ratings(season, players)`
(`matches/league_views.py`).** A new private module-level helper, inside `league_create`'s
existing `@transaction.atomic`, AFTER the founding Teams + Players + `free_agent_pool` pool and
the founding draft `Season` exist. Writes an **as-generated** `PlayerSeasonRating` row for
**every founding Player** — every competitive-Team player (active slots **and** bench) PLUS
every `league.free_agent_pool` Player — tagged to the founding draft Season, carrying
`age=p.age` (verbatim, may be `None`), the 19 stat fields copied straight off `p` via
`getattr(p, name)`, `overall_rating=p.overall_rating`, `potential=None`. **No development, no
age tick, no `total_games` tick.** One `bulk_create` for the whole founding set.

**`next_season` develop loop — `_develop_league_for_new_season(league, new_season,
latest_completed)` (`matches/league_views.py`).** At the **END** of `next_season`'s body,
INSIDE its `@transaction.atomic`, AFTER the team carry-forward / map-pool rehydrate / phase
copy, and BEFORE the final redirect; rows tag to **`new_season`** (the NEW Season). A failure
rolls back the whole rollover (new Season + carry-forward + development) atomically. It:
1. builds a **fresh `random.Random()`** (no stored seed);
2. gathers the **developing set** via `_developing_players(league)` — the snapshot Teams'
   players (active slots **+ bench**, i.e. full `team.players.all()` — do NOT use
   `team.active_players`; bench players develop too) PLUS `league.free_agent_pool` players,
   de-duplicated by pk, **ordered by id** (Teams, then each Team's players, then free agents;
   CI-01: the shared develop RNG is drawn in this order, and Postgres row order is not stable).
   Snapshot Team ids come from `latest_completed.starting_team_ids_json`
   (the frozen snapshot `next_season` already uses for carry-forward). **League-isolated by
   construction; NO cross-League guard** — a Player in a different League is not developed and
   gets no new-Season row;
3. computes the completed-Season **appearance counts** once (ORM,
   `PlayerRoundState.objects.filter(game_round__match__season=latest_completed).values(
   "player_id").annotate(n=Count("id"))` — playoff rounds carry `match.season=NULL` per
   Part2c-1 #3 and are **naturally excluded**, so only regular-season appearances count) and the
   **`median_active`** (median over active-Team players' appearance counts; degenerate no-active
   ⇒ `0`);
4. partitions the set into active-roster vs free-agent-pool players (cheap membership test);
5. **per player, in this LOCKED order:** coalesce `raw_age = player.age if not None else 25` →
   **age tick** `player.age = raw_age + 1` (written even if it was `None` → `26`; the develop
   math uses the incremented age) → `develop_player_stats({name: getattr(player, name)}, …)` →
   mutate the 19 live fields in place → **`total_games` tick** (active player `+=` their exact
   regular-season appearance count; free-agent `+= free_agent_games_tick(median_active, rng)`) →
   stage a `PlayerSeasonRating(player, season=new_season, age=player.age, **new_stats,
   overall_rating=sum(new_stats.values())/19, potential=None)` row;
6. persists in **two bulk queries**: `Player.objects.bulk_update(players, fields=[*STAT_FIELDS,
   "age", "total_games"])` and `PlayerSeasonRating.objects.bulk_create(rows)`.

**LG-06h player-page stub fill (`league-player-ratings-history`).**
`matches.league_screens.player_detail.player_detail` gains a context key **`ratings_history:
list[dict]`** — one entry per `PlayerSeasonRating` row for this Player whose Season belongs to
**this League**, **oldest-first** (ascending by `season_id`, so the trend reads left-to-right):
`{season_id, season_name, age, overall_rating, potential (always None → renders "—"), stats:
{name: value for STAT_FIELDS}}`. A Player with rows in another League does **not** see them.
The key is added to `player_detail`'s frozen-keys docstring; imports `PlayerSeasonRating` and
`from matches import development` (for `STAT_FIELDS`). `templates/leagues/player_detail.html`
**replaces** the `league-player-ratings-history-stub` block with a live block whose DOM id
drops the `-stub` suffix to **`league-player-ratings-history`** (the LG-03
`league-player-awards` precedent — tests assert the live id present, the `-stub` id absent).
Inside: a Chart.js overall-rating-over-time **trend** (`<canvas
id="league-player-ratings-history-chart">` + a `json_script` id
`league-player-ratings-history-data` of `[season_name, overall_rating]` pairs built in-template,
the HX-01 pattern — solid per-Season line, dataset label `"Overall rating"`, x-axis `"Season"`,
y-axis `"Overall rating"`, `pointRadius: 2`; the Chart.js CDN `<script>` is included on this
page, NOT assumed from `base.html`), a per-Season stat table
`<table id="league-player-ratings-history-table">` (`Season | Age | Ovr | Pot | <19 stat
labels>`, oldest-first, `Pot` always `—` this slice, the 19 cells rendered explicitly in
`STAT_FIELDS` order since Django can't dict-lookup by loop variable), and an empty-state
`league-player-ratings-history-empty` (substring `"No ratings history"`). The global HX-01
career page (`player_career_stats` / `/players/<id>/stats/`), the `league-player-awards` block,
the other `-stub` cards, and the `league-player-potential` card (still `—` until LG-05) are
**untouched**.

**Determinism / scope (LOCKED).** Fresh `random.Random()` per rollover, **no stored seed** (the
`PlayerSeasonRating` row is the audit trail) — development sits **outside the SIM-07/08 seed
chain**; tests assert schema-level outcomes (age `+1`, one row per player, tick bounds, league
isolation) and pin the develop math via seeded unit tests on the pure module, **never** exact
unseeded stat values. **NO Score Calibration re-baseline.** `total_games` is cosmetic (never a
develop input). **Deferred:** the per-team **coaching/scouting budget** knob (ZenGM scales
development by a per-team budget; this app has no per-(team, season) state — coaching effect is
fixed at **0**, the budget model deferred to a slice designed *with* LG-05's scouting budget),
**retirement / replacement intake** (players age and decline indefinitely — its own later
grill), and **`potential`** (reserved nullable column, computed in **LG-05**). Free agents
outside any rolling League never age (an accepted gap).

**Locked names (NEW).**
- `matches.models.PlayerSeasonRating` — **NEW** model (19 stat ints incl. `Offensive_synergy`,
  `age` nullable int, `overall_rating` float, `potential` nullable float always `None`, FKs
  `player`/`season` both CASCADE, related_names `season_ratings`/`player_ratings`, constraint
  `uniq_player_season_rating`, `Meta.ordering ["player_id", "season_id"]`; declared after
  `Season`, before `SeasonPhase`).
- `matches/migrations/0048_playerseasonrating.py` — **NEW** (one `CreateModel`, dep `0047`, no
  `RunPython`).
- `matches/development.py` — **NEW** pure module (allowlist `dataclasses`/`typing`/`random`/
  `collections`): `STAT_FIELDS` (19), `STAT_MIN`/`STAT_MAX`, `_STAT_ARCHETYPE` (19),
  `base_change`, `base_change_noise`, `age_modifier`, `change_limits`, `develop_stat`,
  `develop_player_stats`, `free_agent_games_tick`, `_bound`, `_clamp_int`.
- `matches.league_views._write_baseline_ratings(season, players)` — **NEW** (baseline
  bulk_create); `_developing_players(league)` — **NEW** (developing-set gatherer);
  `_develop_league_for_new_season(league, new_season, latest_completed)` — **NEW** (develop +
  persist orchestrator).
- `matches.league_screens.player_detail.player_detail` — **CHANGED** (`+ ratings_history`
  context); `templates/leagues/player_detail.html` — **CHANGED** (`league-player-ratings-history-stub`
  → live `league-player-ratings-history` + chart/table/empty DOM ids).

## LG-05 player potential

Fills the `potential` column LG-04 reserved. At the `league_create` baseline AND each per-League
`next_season` rollover, every Player in the developing set gets a **projected peak overall** — a
ZenGM-faithful **forward projection** of their stats (the LG-04 age curve rolled forward
**noise-free**) plus a **scouting-noise band**, clamped to `[current_overall, 100]`. The value is
written to a NEW `teams.Player.potential` `FloatField` and into the already-existing
`matches.PlayerSeasonRating.potential` column. Potential is **read-only to the simulator** (never a
sim input) ⇒ **NO Score Calibration re-baseline** and **NO new ADR** (reversible — recomputed each
rollover; only a nullable `FloatField` add). The GLOSSARY.md **Potential** term is already written.
Seam contract:
[`.claude/worktrees/lg-05-player-potential-seam-contract.md`](../../../.claude/worktrees/lg-05-player-potential-seam-contract.md).

**Pure module additions — `matches/development.py` (still Django-free).** No new import: the two
new functions + a private helper reuse the existing module surface (`base_change`, `age_modifier`,
`change_limits`, `_STAT_ARCHETYPE`, `_bound`, `_clamp_int`, `STAT_FIELDS`), so the frozen
`dataclasses`/`typing`/`random`/`collections` allowlist holds and
`matches/tests/test_development.py::TestNoDjangoImportsLeaked` stays green. LG-04's `develop_stat`
/ `develop_player_stats` are left **byte-unchanged** (a NEW noise-free per-stat helper is added
instead of branching the existing one). New constants: **`DEFAULT_SCOUTING_BUDGET: int = 50`** (the
FIXED LG-05 scouting budget — CAR-01 later promotes it to a per-team field),
**`POTENTIAL_MAX_SD: float = 8.0`** (invented-by-analogy, locked-but-calibration-deferred — the
same precedent as LG-04's age-curve magic numbers), **`_POTENTIAL_HORIZON_AGE: int = 40`**,
**`_POTENTIAL_MIDPOINT_MULT: float = 0.9`**.
- **`_project_stat_noise_free(current, stat_name, age) -> int`** (private) — one stat's
  deterministic developed value: mirrors `develop_stat` but with **zero noise** — `raw =
  (base_change(age) + age_modifier(group, age)) * _POTENTIAL_MIDPOINT_MULT` (the `0.9` midpoint in
  place of `rng.uniform(0.4, 1.4)`), bound to `change_limits(group, age)`, then `_clamp_int(current
  + delta)`. **Consumes NO RNG.**
- **`_project_peak_overall(stats, age) -> float`** — rolls the LG-04 age curve forward noise-free
  from the current `age` (caller coalesces a `None` age to `25`, the LG-04 convention) to
  `_POTENTIAL_HORIZON_AGE = 40`, developing at the **incremented** age each step (LG-04
  convention), and tracks the **running-max** overall (mean of the 19 stats, `/ 19`) across the
  path. Seeds the running max with the **current overall** before any projection, so the result is
  `>= mean(stats)` by construction (the floor). When `age >= 40` the loop body never runs and it
  returns the current overall exactly. **Deterministic, consumes NO RNG** (no rng argument).
- **`compute_potential(stats, age, rng, *, scouting_budget=DEFAULT_SCOUTING_BUDGET) -> float`** —
  `ceiling = _project_peak_overall(stats, age)`; `sd = POTENTIAL_MAX_SD * (1 - scouting_budget /
  100)` (budget 0 → max sd, 100 → 0); `floor = mean(stats)` (current overall); `value = ceiling +
  rng.gauss(0, sd)`; returns `_bound(value, floor, 100.0)`. **Exactly ONE `rng.gauss` draw**, and
  ONLY in `compute_potential` (`_project_peak_overall` draws nothing). The draw still fires when
  `sd == 0` (`budget == 100`) but contributes `0`. Floor is the current overall so `potential` can
  never read below a player's present overall; cap is `100.0`.

**Separate-RNG invariant.** The potential gauss is drawn from a **fresh `random.Random()` built per
rollover, SEPARATE from LG-04's develop RNG** — so LG-04's pinned 1-gauss-then-19-uniform sequence
is unperturbed and its seeded develop output is **byte-identical**. No stored seed (the
`PlayerSeasonRating.potential` row is the audit trail). In `_develop_league_for_new_season` the two
`random.Random()` instances are distinct objects (pinned by a regression test).

**Live field + migration.** `teams.models.Player.potential =
models.FloatField(null=True, blank=True, default=None)` — a per-Player projected peak overall, set
at each rollover (and the founding baseline), mutated in place like the stats; `None` for players
outside a league flow. Migration **`teams/migrations/0012_player_potential.py`** — a single
`AddField`, dep `0011_team_is_draw_team`, **NO `RunPython`, NO backfill** (ADR-0004 posture). **NO
new `matches` migration** — `PlayerSeasonRating.potential` already exists (LG-04); LG-05 just stops
writing it as `None`.

**Two write sites (`matches/league_views.py`), both inside the existing `@transaction.atomic`
blocks.**
- **`_write_baseline_ratings(season, players)` (CHANGED).** Builds a fresh `pot_rng =
  random.Random()`; for each founding Player computes `pot = compute_potential({name:
  getattr(p, name) for name in STAT_FIELDS}, p.age if p.age is not None else 25, pot_rng)`, sets
  `p.potential = pot`, writes `pot` into the baseline `PlayerSeasonRating(..., potential=pot)` row
  (was `None`), and persists the live field via a new
  `Player.objects.bulk_update(players, ["potential"])` over the founding set. Baseline **does apply
  noise** — the founding potential is a scouting estimate, so the gauss band applies exactly as at a
  rollover. `players` must be a concrete list (computed-over AND bulk_updated).
- **`_develop_league_for_new_season(league, new_season, latest_completed)` (CHANGED).** Builds a
  fresh `pot_rng = random.Random()` — a SECOND instance, INDEPENDENT of the develop `rng`. AFTER
  each player's stats are developed and `player.age` is already incremented, computes `pot =
  compute_potential({name: getattr(player, name) for name in STAT_FIELDS}, player.age, pot_rng)` on
  the POST-development stats + incremented age, sets `player.potential = pot`, fills the developed
  `PlayerSeasonRating(..., potential=pot)` row (was `None`), and APPENDS `"potential"` to the
  EXISTING `Player.objects.bulk_update(players, fields=[*STAT_FIELDS, "age", "total_games",
  "potential"])`.

**Sort wiring.** `teams.views._SORT_KEYS += {"potential": "potential"}` (URL key → ORM target;
`Player.potential` is a real field — `_coerce_sort` then accepts `?sort=potential` with no edit).
`matches.league_views.RATING_SORT_KEYS_DISPLAY += ("potential", "Pot", "Potential")` as the 24th
and LAST entry (after `("special_usage", "SpcUse", "Special Usage")`), driving the sortable `<th>`
loop on the two ratings screens. Because `Player.potential` is nullable, the `player_ratings` and
`free_agents` ORM-sort branches special-case the key with **`F("potential").desc(nulls_last=True)`
/ `.asc(nulls_last=True)`** (`F` already imported in both) so a player with no potential never
floats to the top, preserving the `"name"` secondary tiebreak. `teams.views._SORT_KEYS_DISPLAY` (the
SEPARATE 23-entry global `/players/` display constant) is **UNTOUCHED** — adding `"potential"` to
`_SORT_KEYS` surfaces no header there (confirmed blast-radius; the global view harmlessly accepts
`?sort=potential` with no clickable header).

**Four template surfaces.**
- **Sortable** (`templates/leagues/player_ratings.html`, `templates/leagues/free_agents.html`): the
  fixed placeholder `<th id="...-th-potential" title="Potential">Pot</th>` + its trailing
  `<td>-</td>` placeholder cell are removed; the `Pot` column now renders via the `sort_keys` loop
  (emitting the `<screen>-th-potential` id as a sortable `<a sort=potential>` link) plus a live data
  cell `<td>{{ player.potential|floatformat:1|default:"—" }}</td>` immediately after the
  `special_usage` cell, matching the loop order. The live cell renders the **em-dash `—`** (matching
  `player_detail`), where the removed placeholders rendered the ASCII hyphen.
- **Render-only** (`templates/leagues/team_roster.html`): in BOTH the starting-roster and bench
  tables the trailing `Pot` placeholder `<td>-</td>` (the cell under the existing `<th>Potential</th>`
  header) becomes `<td>{{ player.potential|floatformat:1|default:"—" }}</td>` — matching how
  `overall_rating` is rendered (`|floatformat:1`) plus the em-dash default. No sort infra added.
- **LG-06h card + history** (`templates/leagues/player_detail.html`): the `#league-player-potential`
  card's static `&#8212;` "Arrives with LG-05" stub becomes the live
  `{{ player.potential|floatformat:1|default:"—" }}` (the card id stays); the LG-04 ratings-history
  `Pot` column (the row dicts already carry a `potential` key built in `player_detail.py` — **no
  Python change needed**) now renders real values for rows written after LG-05 and `—` for pre-LG-05
  `potential=None` rows.

**Scope-out (LOCKED).** NO Score Calibration re-baseline (potential is never a sim input — no
simulation mechanic changes). NO new ADR (reversible; recomputed each rollover). NO new `matches`
migration (the `PlayerSeasonRating.potential` column already exists). NO new import in
`matches/development.py` (`TestNoDjangoImportsLeaked` stays green). The global **HX-01 career page**
(`templates/teams/player_career_stats.html` / `teams/views.py::player_career_stats`) is UNTOUCHED.
**MMR / Rank** stay non-sortable `—` placeholders (STAT-PROXY-01) everywhere. `team_roster` is
render-only (no sort infra). `teams.views._SORT_KEYS_DISPLAY` (the global `/players/` 23-entry
constant) UNTOUCHED. `scouting_budget` is a FIXED CONSTANT (`DEFAULT_SCOUTING_BUDGET = 50`) — CAR-01
promotes it to a per-team field.

**Locked names (NEW).**
- `matches/development.py` — **NEW** pure fns `_project_peak_overall(stats, age)`,
  `compute_potential(stats, age, rng, *, scouting_budget=DEFAULT_SCOUTING_BUDGET)`, private
  `_project_stat_noise_free(current, stat_name, age)`; constants `DEFAULT_SCOUTING_BUDGET = 50`,
  `POTENTIAL_MAX_SD = 8.0`, `_POTENTIAL_HORIZON_AGE = 40`, `_POTENTIAL_MIDPOINT_MULT = 0.9`.
- `teams.models.Player.potential` — **NEW** `FloatField(null=True, blank=True, default=None)`;
  migration `teams/migrations/0012_player_potential.py` (one `AddField`, dep `0011_team_is_draw_team`,
  no `RunPython`).
- `matches.league_views._write_baseline_ratings` / `_develop_league_for_new_season` — **CHANGED**
  (compute + persist `potential`); `matches.league_views.RATING_SORT_KEYS_DISPLAY` — **CHANGED**
  (`+ ("potential", "Pot", "Potential")`).
- `teams.views._SORT_KEYS` — **CHANGED** (`+ {"potential": "potential"}`).
- `templates/leagues/player_ratings.html` / `free_agents.html` — **CHANGED** (placeholder `Pot` →
  sortable column); `templates/leagues/team_roster.html` — **CHANGED** (render-only cell);
  `templates/leagues/player_detail.html` — **CHANGED** (live `#league-player-potential` card + lit
  history `Pot` column).
