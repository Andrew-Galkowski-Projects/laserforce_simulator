# matches: Member night

LG-07a member night phases.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## LG-07a member night (core slice)

Makes the declared-but-inert **`member_night`** `SeasonPhase.phase_type` (parked in
`PHASE_TYPE_CHOICES` since LG-02-Part2a, rejected by the composer, returning `False` from
`_phase_complete`) **LIVE**: a casual/social play interlude run **per Site**
(`Player.home_site`), embedded in a Season's phase flow (e.g. RR → member night →
Tournament). A member-night game is a drawn-team **2-Round `Match` stamped `season=<this>`
AND `season_phase=<the member_night phase>`** — **season-attached on purpose**, diverging
from the playoff `season=NULL` FK-chain precedent (ADR-0023) so the games are discoverable
in raw `Match.objects.filter(season=...)` season history, while being kept out of the
competitive **Standings** table. The two Teams are `is_draw_team=True` and **borrow** real
Players (the LG-02x-1 / ADR-0022 posture — `PlayerRoundState` references the real Player,
so career stats stay unified and `Player.team` is never reassigned). **NO migration, NO
Score Calibration re-baseline, NON-deterministic by design.** See
[ADR-0033](../../adr/0033-member-night-season-attached-social-play.md), the GLOSSARY.md
**Member night** / **Site** glossary pair, and the seam contract
[`.claude/worktrees/lg-07-member-night-seam-contract.md`](../../../.claude/worktrees/lg-07-member-night-seam-contract.md).

**NO migration / NO re-baseline / NON-deterministic.** Every column already exists —
`Match.season` (LG-01), `Match.season_phase` (Part2c-2), `Match.leg` (Part2c-3a),
`Team.is_draw_team` (LG-02x-1), `PlayerRoundState.player` (the real-Player FK); completion
is **DERIVED** (no `SeasonPhase.state` field — ADR-0023 honoured), so there is **no
`RunPython`, no backfill, no schema op** (latest matches migration stays
`0056_season_play_job_cancel`, latest teams migration `0014_player_team_health_injury`).
The run draws a **fresh `random.Random()`** outside the SIM-07/08 seed chain and changes no
simulation *mechanic* (the ordinary per-Round simulator runs unchanged) ⇒ **no Score
Calibration re-baseline**. Tests assert schema-level outcomes (Match counts, FK stamping,
`is_draw_team` Teams, roster validity, completion derivation, the Standings exclusion, DOM
ids, the pure split/draw under an INJECTED seeded `random.Random`) — **NEVER raw simulated
point totals**.

**Pure module `matches/member_night.py` (NEW).** Django-free balanced 2-team split +
game-count / pool draws. Frozen import allowlist — `dataclasses` / `typing` / `random` /
`collections` PLUS the single in-module `from matches.draw import build_random_role_assignment`
(itself a frozen pure module, so it leaks no Django), defended by
`test_member_night.py::TestNoDjangoImportsLeaked`. Constants (tunable): `MIN_POOL = 12`
(a Site is viable iff `>= MIN_POOL` available players), `MAX_POOL = 18` (a larger pool is
randomly down-sampled — "whoever showed up"), `MIN_GAMES = 5` / `MAX_GAMES = 9` (inclusive
per-Site game-count draw), `PLAYERS_PER_GAME = 12` (split 6/6). The seam dataclass is
`@dataclass(frozen=True) MemberNightGame(site: str, game_index: int, team_a: dict[str, int],
team_b: dict[str, int])` — `team_a`/`team_b` are the FINAL `{slot_suffix: player_id}` role
maps over the 6 `ROLE_SLOTS` (owned by `matches.draw`, not redefined here), written straight
onto the two drawn Teams' `slot_*` FKs; the pure module consumes `(player_id, overall_rating)`
tuples and never touches a Django object. Public fns: `split_balanced(players) ->
(team_a_ids, team_b_ids)` (attempt-balanced 6/6 split of EXACTLY 12 — sort by rating DESC
then id ASC, greedy assign each next-strongest to the lower-total team, team-A tiebreak;
consumes **NO RNG**; `ValueError` on `len != 12` — does NOT stack the strong on one side);
`draw_member_night_games(pool_by_site, rng) -> list[MemberNightGame]` (the whole run,
iterating Sites in **sorted site-name order** so the RNG consumption order is deterministic
under a seeded rng, assigning the global `game_index` in append order); `draw_site_games(
site, pool, rng, start_index)` (one Site; returns `[]` when `len(pool) < MIN_POOL`). The
per-Site RNG-consumption order is **LOCKED**: `[sample? , randint] + n × [sample, shuffle,
shuffle]` — optional down-sample `rng.sample(pool, MAX_POOL)`, `rng.randint(MIN_GAMES,
MAX_GAMES)`, then per game `rng.sample(run_pool, 12)` → `split_balanced` (no RNG) →
`build_random_role_assignment(team_a_ids, rng)` / `(team_b_ids, rng)`. Roles are assigned
**ONCE** here (a member-night game's roster is fixed for both Rounds — no per-Round re-draw,
no `before_round_hook`, no `TournamentPlayerEntry`). The same Player MAY recur across games
of one run (each game re-samples), within ONE game the 12 are distinct.

**Derived completion + the barrier (`matches/models.py` `Season`).** No new field — two NEW
methods + one rename. `_phase_complete` gains a `member_night` branch delegating to NEW
`_member_night_phase_complete(phase)`: complete **iff** (≥1 member-night Match for the phase
AND every such Match `is_completed`) **OR** no Site has `>= MIN_POOL` available players
(nobody to play — auto-completes so the cursor never parks forever on an empty pool). The
Site pool is gathered by NEW `_member_night_pool_by_site() -> dict[str, list[tuple[int,
float]]]` — the Season's enrolled-Team Players (active + bench) PLUS the League's
`free_agent_pool`, grouped by `Player.home_site` (blank `home_site` buckets under `""`),
mirroring the `_developing_players` enrolled+free-agent precedent. The play-loop barrier
`_tournament_barrier_ordinal` is **RENAMED → `_phase_barrier_ordinal`** and generalised so
an incomplete `member_night` phase ALSO halts the RR loop (`phase.phase_type in
("tournament", "member_night")`); `playable_fixtures_by_phase` repoints to the renamed
method (body otherwise byte-identical — the sole caller). **`_rr_phase_complete` and
`_is_finished` are UNCHANGED** — `_rr_phase_complete` scopes `match__season_phase=<a
specific RR phase>` so member-night Matches never match (safe by construction), and
`_is_finished` only asserts every RR fixture key is present (surplus member-night rounds are
harmless).

**Standings exclusion (the single `.exclude` predicate).** Member nights are social, never
ranked: `.exclude(season_phase__phase_type="member_night")` on a `Match` queryset (or the
`match__`-prefixed analogue on a `GameRound` queryset) is pinned at every
`compute_standings`-feeding query — **7 sites**: `Season._final_standings_for_phase`,
`league_views.season_standings` (the Match query AND the LG-06g side-split `GameRound`
query), `_build_dashboard_context`, `_build_history_row` (an **in-Python** skip to preserve
the `season.matches` prefetch, NOT a queryset `.exclude`), `league_screens/team_history.py::
_build_seasons_context` (since ANG-01e `league_screen_services._team_season_rows`), and `league_screens/power_rankings.py` (win% component — member
nights must not move power rankings either). **PLAY-01 live polling needs no separate edit**
— `_build_play_status_response` → `_render_live_play_panels` → `_build_dashboard_context`
recomputes through the SAME excluded query, so the live poll is covered transitively.
**Leaders are NOT excluded this slice** (see scope-out): member-night `PlayerRoundState`
rows flow into `compute_leaders` unfiltered; only **Standings** are excluded now.

**Game shape (build-then-drain, race-free).** A member-night game reuses
`BatchSimulator.simulate_scheduled_round(season, team_a, team_b, round_number, *,
season_phase=<mn phase>)` **VERBATIM** (no new simulator method) — it already stamps
`season` + `season_phase` via its Side-agnostic find-or-create key and already supports
per-Round build-then-drain. **Setup** pre-creates ALL the game shells up front
(`Match(season=<this>, season_phase=<mn>, team_red, team_blue, is_completed=False)`, `leg`
defaults to 1) — the load-bearing race-free step: with the shells committed,
`_member_night_phase_complete` sees `∃ Match` AND `is_completed=False` so the phase parks
through the whole drain (NOT the "no viable Site" auto-complete branch). **Drain** plays
each unplayed shell Round 1 then Round 2 via `simulate_scheduled_round`; Round 1 FINDS the
shell, Round 2 completes it. Each game creates two `is_draw_team=True` Teams (`MN <site>
G<n> A`/`B`) with `slot_*` FKs set once from `MemberNightGame.team_a`/`team_b`. **No
cleanup / no re-roll** — the run is one-shot (completion is derived; the phase never
re-runs), the drawn Teams persist as the durable record (filtered out of enrolled-Team
lists by `is_draw_team`, never in `season.teams`).

**Views + drain task + URLs.** NEW `member_night_setup(request, season_id) -> HttpResponse`
— **POST-only** (405 on GET), guards `current_phase()` is a `member_night` phase (else the
dashboard-error re-render), reads the **multi-valued `sites` POST field** (the checked
per-Site toggle boxes; ≥1 required, else the dashboard-error re-render), and
`@transaction.atomic` draws via `draw_member_night_games(pool_by_site, random.Random())`
(FRESH non-deterministic Random) over the chosen Sites, creating the drawn Teams + unplayed
Match shells, then **302 redirects** to `season_dashboard` (sync setup; the shells exist and
the phase parks). NEW `play_member_night(request, season_id) -> JsonResponse` (**Play All**)
— POST-only (405), guards an unplayed member-night Match exists (else **409** JSON), enqueues
`play_member_night_task.delay(season.id)` + sets `active_play_job_id` /
`play_cancel_requested=False` (the PLAY-01 enqueue pattern), returns **202** `{job_id,
season_id}`. **Polling REUSES `play_status` + `_build_play_status_response` +
`_celery_state_to_job_status` verbatim** (no new status view). NEW
`play_member_night_single(request, season_id) -> HttpResponse` (**Play Single Members Game**)
— POST-only sync, drains exactly ONE unplayed shell (both Rounds via
`simulate_scheduled_round(..., season_phase=mn)` — shared `_next_member_night_shell` helper),
`complete_if_finished()`, **302** to the dashboard (the `play_single_round` analogue,
default `fidelity`). NEW `play_member_night_live(request, season_id) -> HttpResponse`
(**Single Members Game (Live)**) — POST-only sync, drains ONE shell at **`fidelity="full"`**
(GEN-01 — so the persisted event log drives the SIM-05 replay), stashes the 2 Round ids in
the `live_watch` session (`kind="member_night"`, the LG-01i pattern) and **302** redirects to
`play_week_live_watch` (reusing the watch view/template verbatim — it renders a 2-Round game
from `round_ids`). NEW Celery task
`play_member_night_task` (`@shared_task(bind=True, name="matches.play_member_night")`,
`(self, season_id) -> dict`) mirrors `play_playoffs_task`: the PLAY-01 top + between-game
cancel checks (`_play_cancel_requested`), loops the unplayed shells playing both Rounds,
`update_state` PROGRESS in **GAME counts** (`{completed, total}` = completed games / total
games), `complete_if_finished()` after the loop, and a `finally` clearing
`active_play_job_id` + `close_old_connections()`. **NO outer `@transaction.atomic`** — each
Round is its own atomic commit (ADR-0016), so a mid-drain failure leaves completed games
committed and the run resumable. URLs (`matches/season_urls.py`, bare names):
`member_night_setup` → `<int:season_id>/member-night/setup/` (POST),
`play_member_night` → `<int:season_id>/member-night/play/` (POST),
`play_member_night_single` → `<int:season_id>/member-night/play-single/` (POST),
`play_member_night_live` → `<int:season_id>/member-night/play-single-live/` (POST);
`play_status` is reused for the drain job (no new status route).

**Composer + carry-forward.** `parse_phase_composition` stops rejecting `member_night`: a
**bare token** parses to `PhaseSpec(phase_type="member_night", schedule_format=None,
…tournament defaults inert)` — `member_night:<anything>` (a colon) ⇒ `"malformed phase
composition"` (member nights carry **no** sub-config), and a member night may sit
**anywhere, including first** (the Part2c-1 preceding-RR guard only fires for `tournament`
standings tokens). `PhaseSpec` shape is UNCHANGED, the module stays Django-free, and every
pre-existing `ValueError` string is preserved. `templates/leagues/create.html` makes the
"coming soon" `member_night` placeholder a **live** selectable option per phase-row type
`<select>` (hiding the RR schedule-format + tournament sub-config controls; `serialize()`
emits the bare `member_night` token, no `:sub-config`). `next_season` carry-forward is
**UNCHANGED** — a `member_night` phase carries forward as just another `phase_type` row, so
a fresh member night re-runs each Season via derived completion.

**Play-surface controls (NAV-01 topnav).** The Play dropdown is **phase-contextual**: on a
`member_night` OR `tournament` cursor the RR **One Week / Two Months / Until End** trio is
**suppressed** (`{% if not playoff_phase_active and not member_night_phase_active %}`), and
that phase's own control set renders instead. The **playoff** set (when `playoff_phase_active`)
is **Play Single Round** (`play_single_round`, sync) / **Play Two Months**
(`topbar-play-play-two-months` → `play_two_months`, async = up to 8 Bracket rounds per
Part2c-3f) / **Through Playoffs** (`topbar-play-play-playoffs` → `play_playoffs`, async, drain
to champion) / **Play Single Round (Live)** (`topbar-play-playoff-live` → `play_week_live`,
sync → replay, gated on `live_preview_available`). The **member-night** controls are fed by
NEW `_build_play_controls_context` keys (merged into the topnav by `league_nav` with no
`league_nav` edit): `member_night_phase_active` (`bool`), `member_night_sites`
(`list[(site, count)]`, viable sites only, sorted — drives the toggle boxes),
`member_night_has_unplayed` (`bool`, gates the three play controls). **LOCKED DOM ids**:
`topbar-play-member-night-setup` (a SYNC POST `<form>` to `member_night_setup` with an
`topbar-play-member-night-all` "All Sites" master toggle + per-Site
`topbar-play-member-night-site-cb` toggle boxes posting the multi-valued `sites` field + a
Confirm button), and — only when `member_night_has_unplayed` — the three play controls
`topbar-play-member-night-single` (sync, `play_member_night_single`),
`topbar-play-member-night-live` (sync → replay, `play_member_night_live`), and
`topbar-play-member-night-play` (**Play All**, async, `play_member_night`, polled by the
existing `topnav_play_script.html` IIFE against `play_status` — same progress + Stop wiring as
Play Playoffs). The IIFE also intercepts the async playoff `topbar-play-play-two-months` and
wires the `topbar-play-member-night-all` master toggle to check/uncheck every per-Site box.

**Schedule surfaces (read-only / derived).** Member-night games (Standings-excluded but
real `season`-attached drawn-team Matches) are surfaced on two schedule pages via shared
helpers in `matches/league_views.py`: `_member_night_game_dict(match)` (one display row —
drawn-Team names encode the Site, plus result + per-Round `game_round_detail` links),
`_member_night_games_for_season(season) -> list[dict]` (grouped by `member_night` phase
ordinal), and `_member_night_appearances_for_team(season, team) -> list[dict]` (games where a
Player of `team` appeared, found via `PlayerRoundState.player__team` — each game lists which
of the Team's Players played). `season_schedule` gains `member_night_games` (a "Member nights"
section in `seasons/schedule.html`, DOM ids `season-schedule-member-nights` /
`season-schedule-member-night-{ordinal}` / `member-night-game-{match_id}`); `team_schedule`
gains `member_night_appearances` (a "Member Night Appearances" section in
`leagues/team_schedule.html`, DOM ids `team-schedule-member-nights` /
`team-schedule-member-night-list` / `team-schedule-member-night-row-{match_id}`). No model
change, no migration.

**Scope-out (DEFERRED).** The **per-Season player-stat include/exclude/only filter** (Player
Stats / League Leaders / Statistical Feats / Team-History Players — the LG-06d `?provenance=`
selector precedent) is **DEFERRED to LG-07b**: this slice does NOT touch those screens and
member-night `PlayerRoundState` rows flow into them **unfiltered**; only **Standings** are
excluded now. Also out of scope: NO migration / no `SeasonPhase.state`; NO Score Calibration
re-baseline; NO simulator-mechanics change (`simulate_scheduled_round` verbatim, `arena_map`
3-zone fallback, no per-Round role re-draw); NO `TournamentPlayerEntry` and NO `compute_draw`
reuse (it requires `len(pool) % 6 == 0` and `>= 24` — the member-night split is its own
balanced 6/6 over a 12–18 pool); NO re-roll / cleanup of drawn Teams.

**Locked names (quick index).**
- **Pure module** `matches/member_night.py` — constants `MIN_POOL=12` / `MAX_POOL=18` /
  `MIN_GAMES=5` / `MAX_GAMES=9` / `PLAYERS_PER_GAME=12`; dataclass
  `MemberNightGame(site, game_index, team_a, team_b)`; fns `split_balanced(players)` /
  `draw_member_night_games(pool_by_site, rng)` / `draw_site_games(site, pool, rng,
  start_index)`; reuses `matches.draw.build_random_role_assignment`; RNG order `[sample? ,
  randint] + n × [sample, shuffle, shuffle]` per sorted Site, fresh `random.Random()` per
  run.
- **Model** (`matches/models.py`, `Season`) — NEW `_member_night_phase_complete(phase)` +
  `_member_night_pool_by_site()`; CHANGED `_phase_complete` (member_night branch); RENAMED
  `_tournament_barrier_ordinal` → `_phase_barrier_ordinal` (extended to halt on incomplete
  member_night); `playable_fixtures_by_phase` repoints. UNCHANGED: `_rr_phase_complete`,
  `_is_finished`, `current_phase`; `_final_standings_for_phase` gains only the `.exclude`.
- **Standings exclusion** — `.exclude(season_phase__phase_type="member_night")` at
  `_final_standings_for_phase`, `season_standings` (Match + GameRound), `_build_dashboard_context`,
  `_build_history_row` (in-Python skip), `team_history._build_seasons_context` (now
  `league_screen_services._team_season_rows`),
  `power_rankings`. PLAY-01 live poll covered transitively.
- **Game shape** — 2-Round Match via `simulate_scheduled_round(season, team_a, team_b,
  round_number, *, season_phase=<mn phase>)` VERBATIM; setup pre-creates `Match(season,
  season_phase=mn, team_red, team_blue, is_completed=False)` shells; two `is_draw_team`
  Teams per game, roles set once at setup, no `before_round_hook`, no `TournamentPlayerEntry`.
- **Views** — `member_night_setup(request, season_id) -> HttpResponse` (POST sync, multi
  `sites`, 302 / dashboard-error / 405); `play_member_night(request, season_id) ->
  JsonResponse` (Play All, POST async, 202 / 409 / 405);
  `play_member_night_single(request, season_id) -> HttpResponse` (POST sync, drains ONE shell,
  302 / dashboard-error / 405); `play_member_night_live(request, season_id) -> HttpResponse`
  (POST sync, ONE shell at `fidelity="full"` → `live_watch` session → 302 to
  `play_week_live_watch`); shared helper `_next_member_night_shell(season)`. Reused:
  `play_status` + `_build_play_status_response` + `_celery_state_to_job_status` (Play All);
  `play_week_live_watch` (Live). The Play dropdown SUPPRESSES the RR trio on a
  member_night/playoff cursor; the playoff set relabels to Single Round / Two Months
  (`play_two_months`) / Through Playoffs (`play_playoffs`) / Live (`play_week_live`).
- **URL names** (`matches/season_urls.py`, bare) — `member_night_setup`
  (`<int:season_id>/member-night/setup/`), `play_member_night`
  (`<int:season_id>/member-night/play/`), `play_member_night_single`
  (`<int:season_id>/member-night/play-single/`), `play_member_night_live`
  (`<int:season_id>/member-night/play-single-live/`).
- **Celery task** — `play_member_night_task`,
  `@shared_task(bind=True, name="matches.play_member_night")`, `(self, season_id) -> dict`
  returning `{"completed": int, "total": int}` (GAME counts), PLAY-01 cancel checks +
  `finally` clear of `active_play_job_id`.
- **Composer** — `parse_phase_composition` accepts a bare `member_night` token
  (`schedule_format=None`, no sub-config; `member_night:x` ⇒ `"malformed phase composition"`;
  no preceding-RR guard); `templates/leagues/create.html` member_night option live
  (serialize emits bare `member_night`); `next_season` carry-forward UNCHANGED.
- **Context keys** (`_build_play_controls_context`, merged by `league_nav`) —
  `member_night_phase_active` (`bool`), `member_night_sites` (`list[(str, int)]`),
  `member_night_has_unplayed` (`bool`).
- **DOM ids** (`templates/_partials/topnav_play.html`) — `topbar-play-member-night-setup`
  (POST form), `topbar-play-member-night-all` (All-Sites master toggle) + per-Site
  `topbar-play-member-night-site-cb` toggle boxes (multi-valued `sites` field), and the three
  play controls (gated on `member_night_has_unplayed`) `topbar-play-member-night-single`,
  `topbar-play-member-night-live`, `topbar-play-member-night-play` (Play All). Playoff set:
  `topbar-play-play-single-round` / `topbar-play-play-two-months` / `topbar-play-play-playoffs`
  / `topbar-play-playoff-live`. The RR trio (`topbar-play-one-week` / `-two-months` /
  `-until-end`) is suppressed on a member_night/playoff cursor.
- **ADR / CONTEXT** — ADR-0033 + the GLOSSARY.md **Member night** / **Site** glossary pair
  (already written). **No new ADR, no new domain term, no migration, no re-baseline.**
- **DEFERRED to LG-07b** — the per-Season player-stat member-nights include/exclude/only
  filter.
