# LG-07a — Member night (core slice) — SEAM CONTRACT

Single source of truth for the three parallel agents (code / tests / docs). Every new
method name, signature, dataclass field, dict key, flag literal, DOM id, URL name, view
name, and Celery task name is pinned here. **No production code, tests, or docs in this
file — it is the contract only.**

Source of truth read first: **ADR-0033** (`docs/adr/0033-member-night-season-attached-social-play.md`),
the CONTEXT.md **Member night** / **Site** / **Season phase** / **Standings** glossary
entries, **ADR-0023** (SeasonPhase + derived completion + the playoff `season=NULL`
precedent this diverges from), **ADR-0022** (LG-02x-1 drawn-team / `is_draw_team` /
random-role machinery this reuses), PLAN.md **LG-07**.

Makes the declared-but-inert `member_night` `SeasonPhase.phase_type` LIVE: a casual/social
play interlude run per **Site** (`Player.home_site`), embedded in a Season's phase flow.

**Locked spine (do NOT relitigate — ADR-0033):**
- A member-night game is a drawn-team **Match stamped `season=<this>` AND
  `season_phase=<member_night phase>`**; the two Teams are `is_draw_team=True` and
  **borrow** real Players (so `PlayerRoundState` references the real Player — career stats
  stay unified, the LG-02x-1 pattern). **Diverges on purpose** from the playoff
  `season=NULL` FK-chain precedent (ADR-0023) so member-night games are discoverable in
  `Match.objects.filter(season=...)` season history.
- These games are **excluded from Standings** via the single predicate
  `.exclude(season_phase__phase_type="member_night")` (or the `match__`-prefixed analogue
  on a `GameRound` queryset).
- **Completion is DERIVED — no `SeasonPhase.state` field, NO migration.**
- The run consumes a **fresh `random.Random()`** (non-deterministic by design, the
  LG-02x-1 unseeded precedent) — NOT the SIM-07 seed chain ⇒ **no Score Calibration
  re-baseline**.

---

## 0. NO migration / NO re-baseline / NON-deterministic (LOCKED)

- **NO migration.** Every column needed already exists: `Match.season` (LG-01, `0029`),
  `Match.season_phase` (Part2c-2, `0043`), `Match.leg` (Part2c-3a, `0044`),
  `Team.is_draw_team` (LG-02x-1, `teams 0011`), and `PlayerRoundState.player` (existing,
  the real-Player FK). Completion is derived (no `SeasonPhase.state`). ADR-0033 §Consequences
  confirms "No new model field is strictly required for the core." The latest matches
  migration stays `0056_season_play_job_cancel`; the latest teams migration stays
  `0014_player_team_health_injury`. **No `RunPython`, no backfill, no schema op.**
- **NO Score Calibration re-baseline.** LG-07a changes no simulation *mechanic*; the run
  draws a fresh `random.Random()` outside the SIM-07/08 seed chain, and the games run the
  ordinary per-Round simulator unchanged.
- **NON-deterministic by design.** Tests assert schema-level outcomes (Match counts,
  `season_phase` stamping, `is_draw_team` Teams, roster validity, completion derivation,
  the Standings exclusion, DOM ids, the pure split/draw under an INJECTED seeded
  `random.Random`) — **NEVER** raw simulated point totals.

---

## 1. Pure module — `matches/member_night.py` (NEW)

Balanced 2-team split + game-count / pool draws. **Pure Python, no Django / ORM / I/O /
logging.** Frozen import allowlist: **`dataclasses`, `typing`, `random`, `collections`**
(NO `django.*`, NO `datetime`, NO file I/O), PLUS a single in-module import
`from matches.draw import build_random_role_assignment` — `matches/draw.py` is itself a
frozen pure module (imports only `random` + `dataclasses`), so importing it leaks NO
Django. Defended by `matches/tests/test_member_night.py::TestNoDjangoImportsLeaked`
(subprocess fresh-import + `sys.modules` walk, mirroring `matches/draw.py` /
`matches/bracket.py` / `matches/standings.py`).

> `random` is allowlisted because the draws consume an **injected** `random.Random` (the
> run builds a FRESH `random.Random()` per run; seeded tests inject a seeded one to pin
> the consumption ORDER). The balanced split itself consumes **NO RNG** (deterministic
> greedy balance).

### 1a. Constants (LOCKED, tunable)

```python
MIN_POOL = 12   # a Site is VIABLE iff it has >= MIN_POOL available players
MAX_POOL = 18   # per-run cap: a Site pool larger than this is randomly down-sampled
                # to MAX_POOL players ("whoever shows up")
MIN_GAMES = 5   # inclusive lower bound on the per-Site game count draw
MAX_GAMES = 9   # inclusive upper bound on the per-Site game count draw
PLAYERS_PER_GAME = 12   # each game draws exactly 12 players, split 6 / 6
```

`ROLE_SLOTS` is NOT redefined here — the role maps are built by the reused
`matches.draw.build_random_role_assignment` (which owns `ROLE_SLOTS`).

### 1b. Dataclass — the pure-module ↔ view seam (LOCKED)

```python
@dataclass(frozen=True)
class MemberNightGame:
    site: str                 # the Site this game was drawn for
    game_index: int           # 0-based index within the whole run (all sites)
    team_a: dict[str, int]    # {slot_suffix: player_id} over the 6 ROLE_SLOTS
    team_b: dict[str, int]    # {slot_suffix: player_id} over the 6 ROLE_SLOTS
```

`team_a` / `team_b` are the FINAL role assignments (slot suffix → real player id) the view
writes straight onto the two drawn `Team` rows' `slot_*` FKs. The pure module never sees a
Django object — it consumes `(player_id, overall_rating)` tuples and returns
`MemberNightGame`s. Roles are assigned **ONCE here** (no per-Round re-draw hook, unlike
LG-02x-1 tournaments — a member-night game's roster is fixed for both Rounds, so NO
`TournamentPlayerEntry` and NO `before_round_hook` are involved).

### 1c. Public functions (LOCKED signatures)

```python
def split_balanced(players: list[tuple[int, float]]) -> tuple[list[int], list[int]]:
    """Attempt-balanced 6/6 split of EXACTLY 12 players. Deterministic — consumes NO RNG.
    `players` is 12 (player_id, overall_rating) tuples. Sort by overall_rating DESC then
    player_id ASC; greedily assign each next-strongest player to the team with the lower
    running total rating (team A index tiebreak on equal totals). Returns
    (team_a_ids, team_b_ids), 6 ids each. Raises ValueError if len(players) != 12.
    Does NOT stack the strong players on one side (the compute_draw greedy-balance shape,
    2 teams of 6)."""

def draw_member_night_games(
    pool_by_site: dict[str, list[tuple[int, float]]],
    rng: random.Random,
) -> list[MemberNightGame]:
    """The whole run. Iterates Sites in SORTED (site name ASC) order so the RNG
    consumption order is deterministic for a seeded rng. For each Site, calls the per-Site
    draw and appends its games (assigning the global `game_index` in append order).
    Returns the flat list of MemberNightGame across all Sites."""

def draw_site_games(
    site: str,
    pool: list[tuple[int, float]],
    rng: random.Random,
    start_index: int,
) -> list[MemberNightGame]:
    """One Site's games. `pool` is its (player_id, overall_rating) list; `start_index` is
    the running global game_index. Returns [] when len(pool) < MIN_POOL (NOT viable)."""
```

### 1d. Per-Site algorithm + RNG-consumption order (LOCKED — deterministic under a seeded rng)

For each viable Site (`len(pool) >= MIN_POOL`), in SORTED site-name order, in this exact
RNG-draw sequence:

1. **Down-sample** — `run_pool = pool` when `len(pool) <= MAX_POOL`, else
   `rng.sample(pool, MAX_POOL)` (ONE `rng.sample`). Models "whoever showed up."
2. **Game count** — `n_games = rng.randint(MIN_GAMES, MAX_GAMES)` (ONE `rng.randint`).
3. **Per game** (game 0 … n_games-1), in order:
   a. `twelve = rng.sample(run_pool, PLAYERS_PER_GAME)` (ONE `rng.sample`).
   b. `team_a_ids, team_b_ids = split_balanced(twelve)` (**NO RNG**).
   c. `team_a = build_random_role_assignment(team_a_ids, rng)` (ONE `rng.shuffle`).
   d. `team_b = build_random_role_assignment(team_b_ids, rng)` (ONE `rng.shuffle`).

So one Site of `n` games consumes: `[sample? , randint] + n × [sample, shuffle, shuffle]`.
A non-viable Site consumes NO RNG. A Site under MAX_POOL skips step 1's `sample`. The run
uses a FRESH `random.Random()` in production (non-deterministic); seeded tests inject a
seeded `random.Random` and assert this exact consumption order reproduces.

> The same Player MAY appear in two different games of one run (each game re-samples
> `run_pool`) — accepted (matches "whoever shows up", multiple casual games an evening).
> Within ONE game the 12 are distinct (`rng.sample` is without replacement).

---

## 2. Model changes — `matches/models.py` (`Season`)

NO new field. Two NEW methods + a small generalization. The `Season` class already owns
`current_phase` / `_phase_complete` / `ordered_phases` / `_tournament_barrier_ordinal` /
`playable_fixtures_by_phase` (read in this contract).

### 2a. `_phase_complete` — NEW `member_night` branch (CHANGED, `matches/models.py` ~L1109)

Current body (L1124-1135): `round_robin` ⇒ `_is_finished()` / `_rr_phase_complete(phase)`;
`tournament` ⇒ built AND `tournament.state == "completed"`; `return False` otherwise. ADD
a `member_night` branch BEFORE the trailing `return False`:

```python
        if phase.phase_type == "member_night":
            return self._member_night_phase_complete(phase)
        return False
```

### 2b. `_member_night_phase_complete(self, phase) -> bool` (NEW, `matches/models.py`)

The DERIVED completion rule (ADR-0033, no stored state):

```python
    def _member_night_phase_complete(self, phase) -> bool:
        """A member_night phase is complete IFF:
          - at least one member-night Match exists for it AND every such Match
            is is_completed; OR
          - no Site in the pool has >= MIN_POOL available players (nobody to play —
            the phase auto-completes so the cursor never parks forever on an empty Site
            pool).
        """
        from .member_night import MIN_POOL  # deferred import; pure module
        games = Match.objects.filter(season_phase=phase)
        if games.exists():
            return not games.filter(is_completed=False).exists()
        pool_by_site = self._member_night_pool_by_site()
        return not any(len(pool) >= MIN_POOL for pool in pool_by_site.values())
```

- `∃ member-night Match` is keyed on `season_phase=phase` (member-night Matches are the
  only Matches stamped with a `member_night` phase). Before setup ⇒ 0 games ⇒ falls to the
  pool branch (cursor parks while a viable Site exists). During the drain ⇒ ≥1 game exists
  AND some `is_completed=False` ⇒ incomplete (cursor parks — the build-then-drain
  race-free property). After the drain ⇒ all complete ⇒ complete (cursor advances).

### 2c. `_member_night_pool_by_site(self) -> dict[str, list[tuple[int, float]]]` (NEW, `matches/models.py`)

The Site pool gatherer — used by BOTH `_member_night_phase_complete` (the "no viable Site"
fallback) and the setup view (the draw). Pool = the Season's enrolled-Team Players (active
slots AND bench, i.e. `team.players.all()`) PLUS the League's `free_agent_pool` Players,
grouped by `Player.home_site`. Mirrors the `_developing_players` enrolled+free-agent
precedent (`matches/league_views.py:554`):

```python
    def _member_night_pool_by_site(self) -> "dict[str, list[tuple[int, float]]]":
        team_ids = list(self.starting_team_ids_json or []) or [
            t.id for t in self.teams.all()
        ]
        seen: set[int] = set()
        by_site: dict[str, list[tuple[int, float]]] = {}
        def _add(player):
            if player.pk in seen:
                return
            seen.add(player.pk)
            by_site.setdefault(player.home_site, []).append(
                (player.id, player.overall_rating)
            )
        for team in Team.objects.filter(id__in=team_ids):
            for player in team.players.all():
                _add(player)
        pool = self.league.free_agent_pool
        if pool is not None:
            for player in pool.players.all():
                _add(player)
        return by_site
```

`Player.home_site` is `teams/models.py:257` (`CharField(max_length=100, blank=True,
default="")`); `Player.overall_rating` is the existing `@property`. A blank `home_site`
groups under `""` (a single bucket — acceptable; viability still needs ≥ MIN_POOL there).

### 2d. Barrier generalization — RENAME `_tournament_barrier_ordinal` → `_phase_barrier_ordinal` (CHANGED, `matches/models.py` L1547)

Current `_tournament_barrier_ordinal` (L1547-1558) returns the ordinal of the first
incomplete `tournament` phase. **RENAME to `_phase_barrier_ordinal`** and extend the
predicate so an incomplete `member_night` phase ALSO halts the RR loop:

```python
    def _phase_barrier_ordinal(self) -> "int | None":
        """The ordinal of the first INCOMPLETE non-RR play-loop barrier phase
        (tournament OR member_night), or None. Halts the RR loop so a mid-season
        bracket OR member night drains before later RR phases play."""
        for phase in self.ordered_phases():
            if phase.phase_type in ("tournament", "member_night") and not (
                self._phase_complete(phase)
            ):
                return phase.ordinal
        return None
```

`playable_fixtures_by_phase` (L1560) calls the renamed method
(`barrier = self._phase_barrier_ordinal()`); its body is otherwise byte-identical. Grep
for any other `_tournament_barrier_ordinal` caller and repoint (there is exactly one — the
`playable_fixtures_by_phase` self-call).

### 2e. NO change — `_rr_phase_complete` and `_is_finished` (STATE EXPLICITLY)

- **`_rr_phase_complete` (L1137) needs NO change.** It scopes its played-rounds query
  `GameRound.objects.filter(match__season_phase=phase)` to **a specific RR phase**, and
  member-night Matches carry the *member_night* phase as their `season_phase`, so they
  never match — **safe by construction** (ADR-0033 §48). Its played-keys / fixture-compare
  carry `leg` (Part2c-3a) — member-night Matches default `leg=1` and never enter this
  per-RR-phase set anyway.
- **`_is_finished` (L1396) needs NO change for correctness.** It builds `played_keys` from
  `GameRound.objects.filter(match__season=self)` and then asserts every **RR fixture** key
  is present — it iterates the RR fixture list, never "every persisted round must be a
  fixture", so the extra member-night rounds in the set are harmless surplus (ADR-0033 §54).
  (An `.exclude(match__season_phase__phase_type="member_night")` MAY be added for
  cleanliness but is not required and is NOT pinned.)

---

## 3. Standings exclusion sites (the `.exclude(...)` predicate)

The single predicate is `.exclude(season_phase__phase_type="member_night")` on a `Match`
queryset, or `.exclude(match__season_phase__phase_type="member_night")` on a `GameRound`
queryset. Pin it at every season-scoped `compute_standings`-feeding query (read confirmed):

| # | File · function | Exact line / queryset today | Edit |
|---|---|---|---|
| 1 | `matches/models.py` · `Season._final_standings_for_phase` | `matches_qs = Match.objects.filter(season=self, is_completed=True)` (L1217) | append `.exclude(season_phase__phase_type="member_night")` |
| 2 | `matches/league_views.py` · `season_standings` | `completed_qs = Match.objects.filter(season=season, is_completed=True)` (L311) | append the `Match` exclude |
| 3 | `matches/league_views.py` · `season_standings` (LG-06g Side-split / `season_rounds`) | `GameRound.objects.filter(match__season=season).values(...)` (L341) | append `.exclude(match__season_phase__phase_type="member_night")` |
| 4 | `matches/league_views.py` · `_build_dashboard_context` (standings snippet) | `completed_qs = Match.objects.filter(season=displayed_season, is_completed=True)` (L1258) | append the `Match` exclude |
| 5 | `matches/league_views.py` · `_build_history_row` (League History row) | `for match in season.matches.all(): if not match.is_completed: continue` (L2279-2281) | ALSO skip member-night: `or (match.season_phase_id is not None and match.season_phase.phase_type == "member_night")` — in-Python filter to preserve the `season.matches` prefetch (do NOT add a queryset `.exclude` here; it consumes the prefetched cache) |
| 6 | `matches/league_screens/team_history.py` · `_build_seasons_context` | `season.matches.filter(is_completed=True).prefetch_related("game_rounds")` (L227) | append the `Match` exclude |
| 7 | `matches/league_screens/power_rankings.py` · win% component | `Match.objects.filter(is_completed=True, **match_filter)` (L135) | append the `Match` exclude (member nights are social, not ranked — they must not move power rankings either) |

- **PLAY-01 live polling needs NO separate edit.** `_build_play_status_response`
  (L2786) → `_render_live_play_panels` (L2846) → `_build_dashboard_context` → site #4. The
  live standings/leaders recompute through the SAME `_build_dashboard_context` standings
  query, so excluding it there covers the live poll transitively.
- **Leaders are NOT excluded this slice** (scope-out below) — member-night `PlayerRoundState`
  rows flow into `compute_leaders` unfiltered; only **Standings** are excluded now.

---

## 4. Pre-created drawn-team Match simulated stamped `season`+`season_phase` (game shape DECISION)

**DECISION (my call): a member-night game is a 2-Round `Match`, simulated by reusing
`BatchSimulator.simulate_scheduled_round` VERBATIM (no new simulator method), with the
unplayed Match shell pre-created at setup time.** Justification (lowest-friction reuse):

- `simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None,
  season_phase=None, leg=1, fidelity="scores")` (`matches/simulation/entrypoints.py:844`)
  **already stamps `season` + `season_phase`** via its Side-agnostic find-or-create key
  `(season, season_phase, frozenset({team_a_id, team_b_id}), leg)`, and **already supports
  per-Round build-then-drain** (Round 1 find-or-creates the Match `is_completed=False`;
  Round 2 finds it, plays with args reversed, sets `is_completed=True`). It reads
  `team.active_roster` off the drawn Team's `slot_*` FKs, and the LG-02x-1 `roster_errors`
  relaxation already lets `is_draw_team` Teams field borrowed Players. **Nothing new in the
  simulator.**
- `simulate_match` (L637) is rejected: it CREATES its own Match (no pre-created shell) and
  does NOT stamp `season`/`season_phase`. `simulate_single_round_detailed` (L742) is a
  single round with no Match parent (a member-night game is a full 2-Round Match, parallel
  to RR / playoff games).

**Flow (race-free):**
1. **Setup** pre-creates ALL the game shells:
   `Match.objects.create(season=<this>, season_phase=<member_night phase>,
   team_red=<draw Team A>, team_blue=<draw Team B>, is_completed=False)` (leg defaults
   to 1). This is the load-bearing race-free step: with the shells committed,
   `_member_night_phase_complete` sees `∃ Match` AND `is_completed=False` ⇒ phase parks
   through the whole drain (NOT the "no viable Site" auto-complete branch).
2. **Drain** plays each unplayed shell, per Match, per Round:
   `simulate_scheduled_round(season, m.team_red, m.team_blue, 1, season_phase=mn_phase)`
   then `simulate_scheduled_round(season, m.team_red, m.team_blue, 2,
   season_phase=mn_phase)`. Round 1 FINDS the shell (key matches); Round 2 finds it,
   completes it. (`simulate_scheduled_round`'s post-Round hooks
   `activate_pending_tournament_phase()` + `complete_if_finished()` are harmless no-ops
   here — `complete_if_finished` only ends the SEASON when the FINAL phase completes.)

> The roles are FIXED for the game (assigned once at setup onto each draw Team's `slot_*`
> FKs from `MemberNightGame.team_a`/`team_b`) — NO `before_round_hook`, NO per-Round
> re-draw, NO `TournamentPlayerEntry`. Both Rounds field the same six roles per side.

---

## 5. Setup view + drain view + Celery task + URLs

### 5a. Drawn-Team naming + cleanup (my call)

- Each game creates two `Team` rows: `Team.objects.create(name=f"MN {site} G{game_index+1} A",
  is_draw_team=True)` and `... B"`. `slot_*` FKs set from `MemberNightGame.team_a` /
  `team_b` (a valid no-duplicate 6-slot assignment from `build_random_role_assignment`),
  then `team.save()`.
- **No cleanup / no re-roll.** Member nights are run once (completion is derived; the phase
  never re-runs). The drawn Teams persist as the durable record of who played (borrowed
  Players, `Player.team` never reassigned — the LG-02x-1 / ADR-0022 posture). They carry
  `is_draw_team=True`, so they are filtered out of the regular enrolled-Team lists and
  never enter `season.teams`.

### 5b. NEW view — `member_night_setup` (`matches/league_views.py`)

```python
def member_night_setup(request, season_id: int) -> HttpResponse:
```

- **POST-only** (`if request.method != "POST": return HttpResponseNotAllowed(["POST"])`,
  the LG-01d idiom), first line. `season = get_object_or_404(Season, pk=season_id)`,
  `request.session["last_league_id"] = season.league_id`.
- Guard: `current_phase()` must be a `member_night` phase, else
  `_render_season_dashboard_error(request, season, "No member night to set up.")` (the
  dashboard 400-equivalent re-render, mirroring `play_single_round`).
- Reads a POST field **`site`**: a Site name, or the literal **`"__all__"`** ("All Sites
  present"). Builds `pool_by_site = season._member_night_pool_by_site()`; when `site !=
  "__all__"`, narrows to `{site: pool_by_site.get(site, [])}`.
- `@transaction.atomic`: draws via `draw_member_night_games(pool_by_site,
  random.Random())` (FRESH non-deterministic Random), then for each `MemberNightGame`
  creates the two `is_draw_team` Teams (roles assigned) + the unplayed `Match` shell
  (§4 / §5a). Idempotency: if member-night Matches already exist for the phase, re-running
  setup APPENDS more games (no dedup needed — derived completion just gains more unplayed
  shells); the Code agent MAY guard against double-setup of the same Site, not pinned.
- Returns a **302 redirect to `season_dashboard`** (`redirect("season_dashboard",
  season_id=season.id)`) — sync setup; the shells now exist and the phase parks. The user
  then drains via §5c.

### 5c. NEW view — `play_member_night` (`matches/league_views.py`)

```python
def play_member_night(request, season_id: int) -> JsonResponse:
```

- **POST-only** (405 on GET). `get_object_or_404` + `last_league_id` write.
- Guard: `current_phase()` must be a `member_night` phase with at least one unplayed
  member-night Match, else **409** JSON `{"error": "No member night to play."}` (async
  endpoint returns JSON, the `play_playoffs` precedent at L2757).
- Enqueues `play_member_night_task.delay(season.id)`, sets `season.active_play_job_id =
  result.id` + `season.play_cancel_requested = False` +
  `season.save(update_fields=["active_play_job_id", "play_cancel_requested"])` (the PLAY-01
  enqueue pattern, L2760-2762), returns `JsonResponse({"job_id": result.id, "season_id":
  season.id}, status=202)`.
- **Polling REUSES `play_status` + `_build_play_status_response` +
  `_celery_state_to_job_status` VERBATIM** (same URL, same 5-base-key JSON + the PLAY-01
  `standings` / `leaders` / `cancelled` keys). No new status view.

### 5d. NEW Celery task — `play_member_night_task` (`matches/tasks.py`)

```python
@shared_task(bind=True, name="matches.play_member_night")
def play_member_night_task(self, season_id: int) -> dict:
```

Mirrors `play_playoffs_task` (`matches/tasks.py:397`) body shape exactly:

- `import django.db`; `try:` deferred imports (`Season`, `BatchSimulator`); `season =
  Season.objects.get(id=season_id)`.
- **PLAY-01 top cancel check** (queued case): `if _play_cancel_requested(season_id):
  return {"completed": 0, "total": <n>, "cancelled": True}` (reuse the existing
  `matches.tasks._play_cancel_requested`, L mod-level helper).
- Resolve the member-night phase: `phase = season.current_phase()`; guard `phase is None
  or phase.phase_type != "member_night"` ⇒ `return {"completed": 0, "total": 0}`.
- `total = Match.objects.filter(season_phase=phase).count()` (game count). Loop over the
  **unplayed** shells `Match.objects.filter(season_phase=phase, is_completed=False)`; for
  each, at the TOP of the iteration the **between-game cancel check** (`if
  _play_cancel_requested(season_id): return {"completed": <k>, "total": total,
  "cancelled": True}`), then play both Rounds via `simulate_scheduled_round(..., 1,
  season_phase=phase)` / `(..., 2, season_phase=phase)`; after each completed game
  `self.update_state(state="PROGRESS", meta={"completed": <games done>, "total": total})`.
- After the loop `season.complete_if_finished()`; `return {"completed": <games done>,
  "total": total}`.
- `finally:` clear `active_play_job_id` (`Season.objects.filter(id=season_id).update(
  active_play_job_id=None)`) + `django.db.close_old_connections()` (the PLAY-01 `finally`
  shape, L465-470).
- **Counts are GAME counts** (completed games / total games) — matches the generic
  `{completed, total}` shape `_build_play_status_response` reads. **NO outer
  `@transaction.atomic`** — each `simulate_scheduled_round` Round is its own atomic commit
  (ADR-0016); a mid-drain failure leaves completed games committed and the run resumable.

### 5e. URLs (`matches/season_urls.py`, bare names, no `app_name`)

Insert AFTER the existing play routes and BEFORE the `standings/` / `schedule/` entries
(first-match resolution, mirroring the LG-01d / Part2c-1 routes):

| URL name | Path | Method | View |
|---|---|---|---|
| `member_night_setup` | `<int:season_id>/member-night/setup/` | POST | `member_night_setup` |
| `play_member_night` | `<int:season_id>/member-night/play/` | POST | `play_member_night` |

`play_status` (`<int:season_id>/play-status/<str:job_id>/`) is REUSED for the drain job —
no new status route.

---

## 6. Composer + creation/carry-forward

### 6a. `parse_phase_composition` — stop rejecting `member_night` (CHANGED, `matches/phase_composer.py` ~L221)

The token loop currently rejects every non-RR/non-tournament token with
`raise ValueError(f"unknown phase type: {token!r}")` (L221-222). Insert a `member_night`
branch BEFORE that `else`. A `member_night` token **carries NO sub-config** (no
schedule_format / mode / cut / format / series / wb / lb / swiss):

```python
        elif type_part == "member_night":
            # member_night carries no schedule_format / mode / cut — a bare token only.
            if format_part:
                raise ValueError("malformed phase composition")
            schedule_format = None
            # tournament_* / series / wb / lb / swiss keep their declared defaults
            # (unused for a member_night phase).
        else:
            raise ValueError(f"unknown phase type: {token!r}")
```

- A bare `member_night` token parses to `PhaseSpec(ordinal=index+1,
  phase_type="member_night", schedule_format=None, tournament_mode="standings",
  tournament_cut=0, tournament_format="single_elimination", ...defaults)`. The
  tournament-only fields are inert for a member_night phase (never read by the build).
- `member_night:<anything>` (a colon) ⇒ `format_part` non-empty ⇒
  `"malformed phase composition"` (member nights take no sub-config).
- **The preceding-RR guard (Part2c-1) does NOT fire for `member_night`** — that guard only
  triggers for `tournament` tokens with `tournament_mode == "standings"`. A member night
  may sit **anywhere, including first**. `PhaseSpec` shape is UNCHANGED. The module stays
  Django-free (frozen `dataclasses` / `typing` allowlist; `TestNoDjangoImportsLeaked`
  unaffected). Every pre-existing `ValueError` string is preserved verbatim.

### 6b. Composer template — `templates/leagues/create.html` (CHANGED)

The "coming soon" `member_night` placeholder goes LIVE: add a selectable `member_night`
option to each phase-row type `<select>` (`league-create-phase-type-{i}`). When a row's
type is `member_night`, hide the RR schedule-format select + all tournament sub-config
controls (mirror the existing `applyType()` show/hide rule); `serialize()` emits the
**bare token `member_night`** for that row (no `:sub-config`). All existing Part2b / c-3a..e
composer DOM ids are UNCHANGED. The `league-create-member-night-note` "coming soon" note
copy is updated / removed (Code-agent discretion; the live option is the pinned part).

### 6c. `next_season` carry-forward — CONFIRM NO EDIT

`next_season` (`matches/league_views.py`) already copies each source phase's `phase_type`
+ `schedule_format` + `tournament_mode` + `tournament_cut` + `tournament_format` + the
series/wb/lb/swiss fields **verbatim** into the new draft Season's phases (the Part2b
carry-forward loop, extended through c-3e). A `member_night` phase is just another
`phase_type` row ⇒ it carries forward with **NO edit** (a fresh member night re-runs each
Season via derived completion). **State this explicitly: no `next_season` change.**

---

## 7. Dashboard / Play-surface controls + LOCKED DOM ids + context keys

NAV-01 relocated all league-advancement controls to the league-mode `Play ▾` topnav
(`templates/_partials/topnav_play.html` + `topnav_play_script.html`), fed by
`matches.league_views._build_play_controls_context(league, displayed_season)` (L1418) which
`core.context_processors.league_nav` merges into the topnav context via `result.update(
play_keys)` on the league-prefix path (`core/context_processors.py:141`). The member-night
controls live there, gated when the cursor is on a `member_night` phase.

### 7a. `_build_play_controls_context` — NEW context keys (CHANGED, `matches/league_views.py:1418`)

Append to the returned dict (alongside the existing 9 play keys + `active_play_job_id`):

- **`member_night_phase_active`** (`bool`) — `True` iff `displayed_season` is not None and
  `displayed_season.current_phase()` is a `member_night` phase.
- **`member_night_sites`** (`list[tuple[str, int]]`) — `[(site, len(pool))]` over
  `displayed_season._member_night_pool_by_site()`, VIABLE sites only
  (`len(pool) >= member_night.MIN_POOL`), sorted by site name. `[]` when the cursor is not
  on a member_night phase. Drives the Site `<select>`.
- **`member_night_has_unplayed`** (`bool`) — `True` iff `∃` an unplayed member-night Match
  for the current phase (`Match.objects.filter(season_phase=<phase>,
  is_completed=False).exists()`). Gates the drain button.

`league_nav` already merges every `_build_play_controls_context` key (`result.update(
play_keys)`) on the league-prefix path, so these three keys flow to the topnav with **NO
`league_nav` edit** (they stay ABSENT off-league, like the other play keys).

### 7b. `templates/_partials/topnav_play.html` — member-night block (CHANGED)

Inside `#topbar-play-dropdown`'s `<ul class="dropdown-menu">`, add a member-night section
rendered only when `member_night_phase_active`. **LOCKED DOM ids:**

- `topbar-play-member-night-setup` — a POST `<form>` to `{% url 'member_night_setup'
  season_id=play_displayed_season_id %}` with `{% csrf_token %}`, containing the Site
  `<select id="topbar-play-member-night-site" name="site">` (one `<option value="{site}">`
  per `(site, count)` in `member_night_sites`, plus an `<option value="__all__">All Sites
  present</option>`) and a submit button. Rendered when `member_night_phase_active`.
- `topbar-play-member-night-play` — a POST `<form>` to `{% url 'play_member_night'
  season_id=play_displayed_season_id %}` with `{% csrf_token %}` + submit button. Rendered
  when `member_night_phase_active AND member_night_has_unplayed`.

Each submit carries `data-action-state="{{ action_button_state }}"` (test-hook parity).
The existing `topbar-play-dropdown` / `-progress` / `-error` ids and the
`topnav_play_script.html` async-poll IIFE (`interceptAsync` / `startPolling`) are REUSED
verbatim — the `play_member_night` 202 `{job_id, season_id}` response is polled by the same
JS against `play_status` (the drain shows the same progress + Stop wiring as Play
Playoffs). `member_night_setup` submits SYNCHRONOUSLY (server-side 302 redirect), NOT
intercepted.

---

## 8. Scope-out (LOCKED — DEFERRED, do NOT build here)

- **Per-Season player-stat include/exclude/only filter** (Player Stats / League Leaders /
  Statistical Feats / Team-History Players) — **DEFERRED to LG-07b**. This slice does NOT
  touch those screens; member-night `PlayerRoundState` rows flow into them **unfiltered**.
  Only **Standings** are excluded now (§3).
- **NO migration** (no `SeasonPhase.state`, no new field — §0).
- **NO Score Calibration re-baseline** (no mechanic change; fresh `random.Random()`
  outside SIM-07/08 — §0).
- **NO simulator-mechanics change** — `simulate_scheduled_round` consumed verbatim
  (`arena_map=None` 3-zone fallback per game; no `before_round_hook`; no per-Round role
  re-draw).
- **NO `TournamentPlayerEntry`** (member-night roles are fixed for the game, assigned once
  at setup) and **NO `compute_draw` reuse** (it requires `len(pool) % 6 == 0` and `>= 24`
  — the member-night split is its own balanced 6/6 over a 12–18 pool).
- **NO re-roll / cleanup** of drawn member-night Teams (one-shot; borrowed Players,
  `Player.team` never reassigned).

---

## 9. Test boundary

**Tests agent asserts against (public seam):**

- **Pure `matches/member_night.py`** (`matches/tests/test_member_night.py`, NO DB):
  `split_balanced` — 6/6 split of 12, attempt-balanced (the strong players are NOT stacked
  on one side; assert the two teams' total-rating gap is minimised by the greedy rule),
  deterministic / consumes NO RNG, `ValueError` on `len != 12`; `draw_member_night_games` /
  `draw_site_games` under an **INJECTED seeded `random.Random`** — viability floor (a Site
  with `< MIN_POOL` yields 0 games), game count in `[MIN_GAMES, MAX_GAMES]`, each game
  exactly 12 distinct players split into two 6-slot `{slot: player_id}` role maps
  (permutation of all 6 `ROLE_SLOTS`, no duplicate player/slot), the MAX_POOL down-sample,
  the pinned RNG-consumption ORDER (same seed ⇒ same plan), sorted-site determinism, the
  `MemberNightGame` shape; `TestNoDjangoImportsLeaked` (subprocess fresh-import — incl. the
  `matches.draw` import leaks no Django).
- **Derived completion** (`matches/tests/test_season_phase.py` or
  `test_member_night.py`, Django `TestCase`): `_member_night_phase_complete` — 0 games +
  a viable Site ⇒ incomplete (cursor parks); 0 games + NO viable Site ⇒ complete
  (auto-advance); ≥1 game with an unplayed shell ⇒ incomplete; all games complete ⇒
  complete; `current_phase()` advances past the member_night phase once complete.
- **The barrier** (`test_season_phase.py` extend): `_phase_barrier_ordinal` halts the RR
  loop on an incomplete `member_night` phase (`playable_fixtures_by_phase` excludes
  post-barrier RR fixtures), and re-admits them once the member night completes — assert
  on the SET of excluded fixtures, never point totals. Confirm `_rr_phase_complete` /
  `_is_finished` unchanged (a member-night Match does not affect RR-phase completion).
- **Standings exclusion** (`matches/tests/test_standings.py` is pure — the exclusion is in
  the VIEW/model corpora, so assert at the DB layer): a member-night Match stamped
  `season=<this>, season_phase=<mn>` does NOT appear in `season_standings` /
  `_build_dashboard_context` / `_final_standings_for_phase` / `team_history` Seasons-tab
  rank / `power_rankings` win% / the League-History row's `matches_played`+rank; a regular
  RR Match still does. (`test_season_views.py` / `test_league_dashboard.py` /
  `test_league_history.py` / `test_team_history.py` / `test_power_rankings.py` extend.)
- **Composer** (`matches/tests/test_phase_composer.py` extend): a bare `member_night`
  token parses to `PhaseSpec(phase_type="member_night", schedule_format=None)`;
  `member_night:x` ⇒ `"malformed phase composition"`; `member_night` may sit first / mid /
  anywhere (no preceding-RR guard); a composition mixing RR + member_night + tournament
  parses with contiguous ordinals. **UPDATE the two existing rejection tests**
  `test_member_night_token_rejected_as_unknown_type` (L253) and
  `test_member_night_still_unknown_phase_type` (L689) — they currently assert
  `"unknown phase type: 'member_night'"`; member_night is now a VALID type, so these tests
  flip to assert acceptance (rename / repurpose; the `test_member_night_format_token_rejected`
  at L1250 stays — `tournament:...:member_night` as a *tournament_format* is still
  `"unknown tournament_format"`). Purity (`TestNoDjangoImportsLeaked`) still passes.
- **Setup / drain views** (`matches/tests/test_member_night_views.py` NEW, Django
  `TestCase` under `CELERY_TASK_ALWAYS_EAGER`): `member_night_setup` POST creates the
  drawn `is_draw_team` Teams + the unplayed `Match` shells stamped `season=<this>,
  season_phase=<mn>` (one Site / `__all__`), 302 to the dashboard, 405 on GET,
  dashboard-error when the cursor is not a member_night phase; `play_member_night` POST →
  202 `{job_id, season_id}`, 409 when no unplayed game, 405 on GET; `play_member_night_task`
  drains every shell to `is_completed=True` (counts are GAME counts, `{completed, total}`),
  the phase then completes and the cursor advances, the PLAY-01 cancel check halts the
  drain mid-run leaving played games committed (`cancelled: True`) and `active_play_job_id`
  cleared in `finally`. Reuse `play_status` JSON shape (assert the existing 5 base keys).
- **Drawn-team game shape** (same file): a member-night Match has two `is_draw_team`
  Teams, each `roster_errors`-valid (borrowed Players, no "does not belong" error), and its
  `PlayerRoundState` rows reference the **real** Players (career stats unified) — never on
  exact point totals.

**Internal (NOT asserted):** the exact drawn-Team name string, the in-Python member-night
skip vs queryset-exclude choice at site #5, the per-Round RNG draw values, the precise
`applyType()` JS, the exact Bootstrap classes, whether re-running setup dedups Sites.

**Test files (NEW + EXTENDED), `matches/tests/test_*.py` convention:**
- NEW: `matches/tests/test_member_night.py` (pure module + completion + barrier),
  `matches/tests/test_member_night_views.py` (setup / drain views + task + drawn-team
  shape).
- EXTENDED: `test_phase_composer.py` (member_night token + the two flipped rejection
  tests), `test_season_phase.py` (`_phase_complete` member_night branch + barrier),
  `test_season_views.py` (`season_standings` exclusion), `test_league_dashboard.py`
  (dashboard standings exclusion + the new `member_night_*` context keys),
  `test_league_history.py` (`_build_history_row` exclusion),
  `test_team_history.py` / `team_history` test file (Seasons-tab rank exclusion),
  `test_power_rankings.py` / the power-rankings test file (win% exclusion),
  `test_nav_play_dropdown.py` (the topnav member-night DOM ids + gating).

---

## 10. Locked names (quick index)

- **Pure module** `matches/member_night.py` — constants `MIN_POOL=12` / `MAX_POOL=18` /
  `MIN_GAMES=5` / `MAX_GAMES=9` / `PLAYERS_PER_GAME=12`; dataclass
  `MemberNightGame(site, game_index, team_a, team_b)`; fns `split_balanced(players)` /
  `draw_member_night_games(pool_by_site, rng)` / `draw_site_games(site, pool, rng,
  start_index)`; reuses `matches.draw.build_random_role_assignment`; RNG order
  `[sample? , randint] + n × [sample, shuffle, shuffle]` per sorted Site, fresh
  `random.Random()` per run.
- **Model** (`matches/models.py`, `Season`) — NEW `_member_night_phase_complete(phase)` +
  `_member_night_pool_by_site()`; CHANGED `_phase_complete` (member_night branch); RENAMED
  `_tournament_barrier_ordinal` → `_phase_barrier_ordinal` (extended to halt on incomplete
  member_night); `playable_fixtures_by_phase` repoints to the renamed method. UNCHANGED:
  `_rr_phase_complete`, `_is_finished`, `_final_standings_for_phase` (gains only the
  `.exclude`), `current_phase`.
- **Standings exclusion** — `.exclude(season_phase__phase_type="member_night")` at
  `_final_standings_for_phase` (L1217), `season_standings` (L311 Match + L341 GameRound),
  `_build_dashboard_context` (L1258), `_build_history_row` (L2279, in-Python skip),
  `team_history._build_seasons_context` (L227), `power_rankings` (L135). PLAY-01 live poll
  covered transitively.
- **Game shape** — 2-Round Match via `simulate_scheduled_round(season, team_a, team_b,
  round_number, *, season_phase=<mn phase>)` VERBATIM; setup pre-creates `Match(season,
  season_phase=mn, team_red, team_blue, is_completed=False)` shells; two `is_draw_team`
  Teams per game, roles set once at setup, no `before_round_hook`, no `TournamentPlayerEntry`.
- **Views** — `member_night_setup(request, season_id) -> HttpResponse` (POST sync, 302 /
  dashboard-error / 405); `play_member_night(request, season_id) -> JsonResponse` (POST
  async, 202 / 409 / 405). Reused: `play_status` + `_build_play_status_response` +
  `_celery_state_to_job_status`.
- **URL names** (`matches/season_urls.py`, bare) — `member_night_setup`
  (`<int:season_id>/member-night/setup/`), `play_member_night`
  (`<int:season_id>/member-night/play/`).
- **Celery task** — `play_member_night_task`,
  `@shared_task(bind=True, name="matches.play_member_night")`, `(self, season_id) -> dict`
  returning `{"completed": int, "total": int}` (GAME counts), PLAY-01 cancel checks +
  `finally` clear of `active_play_job_id`.
- **Composer** — `parse_phase_composition` accepts a bare `member_night` token
  (`schedule_format=None`, no sub-config; `member_night:x` ⇒ `"malformed phase
  composition"`; no preceding-RR guard); `templates/leagues/create.html` member_night
  option live (serialize emits bare `member_night`); `next_season` carry-forward UNCHANGED.
- **Context keys** (`_build_play_controls_context`, merged by `league_nav`) —
  `member_night_phase_active` (`bool`), `member_night_sites` (`list[(str, int)]`),
  `member_night_has_unplayed` (`bool`).
- **DOM ids** (`templates/_partials/topnav_play.html`) — `topbar-play-member-night-setup`
  (POST form), `topbar-play-member-night-site` (Site `<select>`, includes `__all__`),
  `topbar-play-member-night-play` (POST form, gated on `member_night_has_unplayed`).
- **ADR / CONTEXT** — already written: ADR-0033 + the CONTEXT.md **Member night** / **Site**
  glossary pair. **No new ADR, no new domain term, no migration, no re-baseline.**
