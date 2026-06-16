# CAR-02 — Performance-based firing (ZenGM owner-mood) — SEAM CONTRACT

Branch: `car-02-performance-based-firing`. **Write-side league feature** — one new model
+ one migration, a Django-free pure module, orchestration in `matches/league_views.py`
(incl. a `next_season` rollover refactor), 2 new URL names, 1 new GET screen + 2 new
POST views, new templates + DOM ids, and dashboard reroute. Mirrors the LG-03/LG-04
pure-module purity discipline + the LG-06h league-screen view shell. Spec:
`Screenshots_and_video_examples/firing_rules/firing_rules.md`; decision:
`docs/adr/0026-manager-firing-owner-mood.md`; domain terms (already finalised, **do not
edit CONTEXT.md**): Manager / Owner mood / Mood factor / Manager tenure / Grace period /
Owner evaluation / Hot seat / Fired·Firing / Reassignment. The "Manager" is the implicit
local user = `League.current_team` (CAR-01); no Manager/User model.

Verified against the live repo before writing — pinned names: `next_season` is
`@transaction.atomic` (decorator at `league_views.py:2913`, body `2914`→`3013`);
`_write_baseline_ratings` (`574`), `_develop_league_for_new_season` (`608`),
`_build_dashboard_context` (`1008`, action-button keys at `1027`–`1056` / `1219`–`1220`),
`_build_league_sidebar_links` (`1637`), `_build_history_row` (`1976`), `season_awards`
(`205`); `Season._final_standings_for_phase` (`models.py:1136`) is the exact
`compute_standings`-input assembler reused for the *wins* + standings work;
`Season._stamp_champion_for_final_phase` (`1307`); `Tournament.champion_id` /
`SeasonPhase.phase_type=="tournament"` / `phase.tournament_id`; the latest matches
migration is `0048_playerseasonrating` ⇒ this slice is `0049_ownerevaluation`. The
league-screen view shell is `matches/league_screens/player_detail.py` (GET-guard →
`get_object_or_404` → `last_league_id` session write → `displayed_season` chain →
`_build_league_sidebar_links(..., sidebar_active=None)` → render). The current
`start_next_season` action button is a `<form … action="{% url 'next_season' %}">` in
both `templates/seasons/dashboard.html` (`:54`) and `templates/leagues/dashboard.html`.

---

## 0 · Locked names (quick index)

| Kind | Name |
|---|---|
| Pure module | `matches/owner_mood.py` (allowlist: `dataclasses`, `typing`, `collections` ONLY) |
| Constant | `WINS_FACTOR = 1` |
| Constant | `WINS_BASELINE_SCALE = 0.25` |
| Constant | `PLAYOFF_TITLE = 0.2` |
| Constant | `PLAYOFF_MISS = -0.2` |
| Constant | `PLAYOFF_ADVANCE_SCALE = 0.16` |
| Constant | `MOOD_FACTOR_CAP = 1.0` |
| Constant | `FIRE_THRESHOLD = -1.0` |
| Constant | `GRACE_PERIOD_SEASONS = 2` |
| Dataclass | `MoodDeltas(wins, playoffs, money)` (`frozen=True`, 3 floats) |
| Dataclass | `MoodTotals(wins, playoffs, money)` (`frozen=True`, 3 floats) |
| Dataclass | `Verdict(outcome, hot_seat_level)` (`frozen=True`) |
| Pure fn | `compute_wins_delta(won: int, games: int) -> float` |
| Pure fn | `compute_playoffs_delta(playoff_result: str, rounds_won: int, num_rounds: int) -> float` |
| Pure fn | `cap_cumulative(prev_cumulative: float, delta: float) -> float` |
| Pure fn | `decide_verdict(totals: MoodTotals, deltas: MoodDeltas, *, seasons_in_tenure: int) -> Verdict` |
| Purity test | `matches/tests/test_owner_mood.py::TestNoDjangoImportsLeaked` |
| Model | `matches.models.OwnerEvaluation` |
| Constraint | `uniq_league_season_owner_evaluation` |
| Migration | `matches/migrations/0049_ownerevaluation.py` (CreateModel-only) |
| Helper (writer) | `matches.league_views._ensure_owner_evaluations(league, up_to_season)` |
| Helper (rollover) | `matches.league_views._run_season_rollover(league, latest_completed) -> Season` |
| View (GET) | `matches.league_views.owner_evaluation(request, season_id)` |
| View (GET) | `matches.league_views.new_team_picker(request, league_id)` |
| View (POST) | `matches.league_views.reassign_team(request, league_id)` |
| URL/name | `owner_evaluation` / `/seasons/<int:season_id>/owner-evaluation/` |
| URL/name | `new_team_picker` / `/leagues/<int:league_id>/new-team/` |
| URL/name | `reassign_team` / `/leagues/<int:league_id>/reassign-team/` |
| Template | `templates/seasons/owner_evaluation.html` |
| Template | `templates/leagues/new_team.html` |
| Constant | `WORST_N_ELIGIBLE = 5` |

---

## 1 · Pure module `matches/owner_mood.py`

**Frozen import allowlist (LOCKED):** `dataclasses`, `typing`, `collections` ONLY.
**NO** Django / ORM / `random` / `datetime` / `math` / I/O / logging. Defended by a
subprocess fresh-import + `sys.modules` walk (`TestNoDjangoImportsLeaked`) — mirror
`matches/season_awards.py` / `matches/development.py` / `matches/standings.py` (READ one
for the docstring + purity-test shape; `development.py` already carries the exact
`TestNoDjangoImportsLeaked` pattern). The view assembles flat inputs (ints / strings) and
calls these; the module never sees a Django object, ORM, or RNG.

### 1.1 Constants (LOCKED values)

```python
WINS_FACTOR: float = 1.0
WINS_BASELINE_SCALE: float = 0.25
PLAYOFF_TITLE: float = 0.2
PLAYOFF_MISS: float = -0.2
PLAYOFF_ADVANCE_SCALE: float = 0.16
MOOD_FACTOR_CAP: float = 1.0     # per-factor cumulative ceiling (+1; NO negative floor)
FIRE_THRESHOLD: float = -1.0
GRACE_PERIOD_SEASONS: int = 2    # flat — drop ZenGM's +3-if-joined-at-playoffs nuance
```

### 1.2 Dataclasses (frozen, pinned field order)

```python
@dataclass(frozen=True)
class MoodDeltas:
    wins: float
    playoffs: float
    money: float          # DORMANT — always 0.0 this slice

@dataclass(frozen=True)
class MoodTotals:
    wins: float           # cumulative, per-factor capped at MOOD_FACTOR_CAP
    playoffs: float
    money: float          # DORMANT — always 0.0 this slice

@dataclass(frozen=True)
class Verdict:
    outcome: str          # "retained" | "hot_seat" | "fired"
    hot_seat_level: int   # 0 = none; 1 = "another season..."; 2 = "a couple more..."
```

- `Verdict.outcome ∈ {"retained", "hot_seat", "fired"}` (LOCKED strings — the model's
  `verdict` choices + the template branches key on these).
- `hot_seat_level` is `0` for `retained` / `fired`; `1` or `2` only when
  `outcome == "hot_seat"`.
- **ONE decider function** (`decide_verdict`) returns BOTH the outcome string AND the
  hot-seat warning level — pinned as one function, not two (the firing decision and the
  hot-seat projection share the cumulative total + delta, so splitting them would
  duplicate the threshold math).

### 1.3 `compute_wins_delta(won, games) -> float`

`WINS_FACTOR * WINS_BASELINE_SCALE * (won - games/2) / (games/2)`. `won`/`games` = the
`current_team`'s **regular-season** Match record in the just-completed Season (W counted
from completed Matches). **`games == 0` ⇒ returns `0.0`** (no div-by-zero — a Season with
no completed Matches is neutral). This is the per-Season delta (pre-cap).

### 1.4 `compute_playoffs_delta(playoff_result, rounds_won, num_rounds) -> float`

Read off the Season's embedded `tournament` **Season phase** bracket (Part2c-1). The
**view** classifies the result and passes the flat triple; the pure fn maps it:

| `playoff_result` | returns |
|---|---|
| `"champion"` | `PLAYOFF_TITLE` (`+0.2`) |
| `"seeded"` (in bracket, no title) | `(PLAYOFF_ADVANCE_SCALE / num_rounds) * rounds_won` |
| `"missed"` (cut / not in bracket) | `PLAYOFF_MISS` (`-0.2`) |
| `"none"` (no tournament phase in the Season) | `0.0` (neutral) |

- `playoff_result ∈ {"champion", "seeded", "missed", "none"}` (LOCKED strings, view-supplied).
- `num_rounds` = the bracket's max `bracket_round`; **`num_rounds == 0` ⇒ the `"seeded"`
  branch returns `0.0`** (defensive — never divides by zero).
- `rounds_won` = the count of bracket rounds the `current_team` won a node in.
- Unknown `playoff_result` string ⇒ `0.0` (forgiving, the LG-06d/HX-02 precedent).

### 1.5 `cap_cumulative(prev_cumulative, delta) -> float`

`min(prev_cumulative + delta, MOOD_FACTOR_CAP)`. **Cap on the upside ONLY** — no negative
floor (you cannot bank goodwill past `+1`, but you can sink arbitrarily low). Applied
per-factor, oldest→newest, by the writer when chaining cumulative totals across a tenure.

### 1.6 `decide_verdict(totals, deltas, *, seasons_in_tenure) -> Verdict`

`seasons_in_tenure` is keyword-only — **how many Seasons (inclusive) the Manager has held
this team within the current tenure** (1 = first Season of the tenure). Algorithm
(LOCKED, ZenGM-faithful):

```
total = totals.wins + totals.playoffs + totals.money
delta = deltas.wins + deltas.playoffs + deltas.money
past_grace = seasons_in_tenure > GRACE_PERIOD_SEASONS   # strictly past the 2-season cushion

if past_grace and total <= FIRE_THRESHOLD:
    return Verdict("fired", 0)
# warning projection (only past grace, not fired)
if past_grace and total + delta < FIRE_THRESHOLD:
    return Verdict("hot_seat", 1)        # "another season like that..."
if past_grace and total + 2 * delta < FIRE_THRESHOLD:
    return Verdict("hot_seat", 2)        # "a couple more seasons..."
return Verdict("retained", 0)
```

- **Grace gate is on FIRING + the hot-seat warning** — inside the grace period (≤
  `GRACE_PERIOD_SEASONS`) the verdict is always `"retained"` regardless of mood.
- The two hot-seat branches are checked in order (`level 1` is the stricter projection and
  wins when both hold). `< FIRE_THRESHOLD` is a strict-less compare (matches the spec's
  `currentTotal + deltas < -1`); the firing compare is `<=` (`currentTotal <= -1`).

---

## 2 · Model `matches.models.OwnerEvaluation`

**Placement (LOCKED):** in `matches/models.py` AFTER `Season`, `SeasonPhase`, AND `Team`
references resolve — it FKs `League`, `Season`, and `teams.Team`. Place it after
`PlayerSeasonRating` (which already sits after `Season`) and before `SeasonPhase`/`Tournament`
is not required — simplest correct placement is **immediately after `PlayerSeasonRating`**
(both reference `Season`, `Team`/`Player` is already imported at the top of `models.py`).

One immutable snapshot per `(League, completed Season)`. **Fields (every name + type +
on_delete + null/blank + related_name LOCKED):**

```python
class OwnerEvaluation(models.Model):
    VERDICT_CHOICES = (
        ("retained", "Retained"),
        ("hot_seat", "Hot seat"),
        ("fired", "Fired"),
    )

    league = models.ForeignKey(
        "matches.League", on_delete=models.CASCADE, related_name="owner_evaluations"
    )
    season = models.ForeignKey(
        "matches.Season", on_delete=models.CASCADE, related_name="owner_evaluations"
    )
    team_managed = models.ForeignKey(
        "teams.Team", on_delete=models.SET_NULL, null=True, blank=True,
        related_name="+",
    )

    # per-Season factor deltas (pre-cap, this Season only)
    wins_delta = models.FloatField()
    playoffs_delta = models.FloatField()
    money_delta = models.FloatField(default=0.0)          # DORMANT — always 0.0

    # per-factor cumulative-capped totals (across the tenure, through this Season)
    wins_total = models.FloatField()
    playoffs_total = models.FloatField()
    money_total = models.FloatField(default=0.0)          # DORMANT — always 0.0

    verdict = models.CharField(max_length=16, choices=VERDICT_CHOICES)
    hot_seat_level = models.PositiveSmallIntegerField(default=0)   # 0 / 1 / 2

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["league_id", "season_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["league", "season"],
                name="uniq_league_season_owner_evaluation",
            ),
        ]
```

- `team_managed` = the Team the Manager managed during that Season (the `League.current_team`
  AT that Season). `on_delete=SET_NULL` + `related_name="+"` (no reverse accessor needed).
- **Tenure boundaries + grace derive from the snapshot chain (LOCKED):** a change in
  `team_managed` between two consecutive rows (by Season order) marks a NEW tenure — the
  cumulative resets and the grace counter restarts. There is **no `tenure_id` field**.
- `money_delta` / `money_total` are persisted but ALWAYS `0.0` this slice (the column exists
  so a future finance subsystem lights up without a migration).
- The snapshot is **immutable** — written once via `get_or_create`, never updated.

### Migration `matches/migrations/0049_ownerevaluation.py`

- **One `CreateModel(OwnerEvaluation)`** carrying all fields + the `UniqueConstraint` +
  `Meta.ordering`. **NO `RunPython`, NO `RunSQL`, NO backfill** (ADR-0026 §Consequences +
  the ADR-0004 disposable-data posture; the `0048` / `0029` precedent). Existing Leagues get
  no historical rows — the lazy writer (§3.1) fills them in Season order the first time the
  eval screen / rollover is reached.
- Dependency: `("matches", "0048_playerseasonrating")`. The `teams.Team` FK dependency is
  resolved automatically by `makemigrations`.

---

## 3 · Orchestration in `matches/league_views.py`

### 3.1 Lazy + idempotent writer — `_ensure_owner_evaluations(league, up_to_season)`

```python
def _ensure_owner_evaluations(league: League, up_to_season: Season) -> None:
```

Ensures an `OwnerEvaluation` row exists for **every completed Season of `league` up to and
including `up_to_season`**, written **oldest→newest in Season order** so the per-factor
caps + cumulatives + tenure derivation are correct. **`get_or_create`-keyed on
`(league, season)`** (idempotent — a row already present is left untouched; **NO backfill of
seasons before the first computable one**, ADR-0004). Behaviour (LOCKED):

1. Gather `league`'s **completed** Seasons up to `up_to_season.id`, ordered ascending by
   Season id (`league.seasons.filter(state="completed", id__lte=up_to_season.id).order_by("id")`).
2. Walk them oldest→newest, threading three per-factor running totals + a tenure marker
   (`prev_team_managed` + `seasons_in_tenure` counter). For each Season:
   - **`team_managed`** for the Season = the `OwnerEvaluation.team_managed` of the prior row
     if present, else `league.current_team` (the manager's team that Season — see §3.1a).
   - **Tenure reset:** if `team_managed != prev_team_managed`, reset the three running totals
     to `0.0` and `seasons_in_tenure = 1`; else `seasons_in_tenure += 1`.
   - Build the flat inputs (§3.1a) → `compute_wins_delta`, `compute_playoffs_delta`
     (`money_delta = 0.0`).
   - Cap-chain the cumulative totals: `wins_total = cap_cumulative(running.wins,
     wins_delta)` (and playoffs; `money_total = 0.0`); update the running totals.
   - `decide_verdict(MoodTotals(...), MoodDeltas(...), seasons_in_tenure=seasons_in_tenure)`.
   - `OwnerEvaluation.objects.get_or_create(league=league, season=season, defaults={...})`
     stamping the 3 deltas, the 3 totals, `team_managed`, `verdict`, `hot_seat_level`.
   - **Idempotency note:** if a row already existed, **re-read its persisted deltas/totals/
     `team_managed`** into the running state before continuing (so a partially-written chain
     stays consistent — the persisted row, not a recompute, is the source of truth for
     prior Seasons). This is the load-bearing "write oldest→newest, derive from prior rows"
     rule.

**`team_managed` derivation (LOCKED):** because firings change `current_team`, the team a
Manager ran in a PAST Season cannot be re-read from current state. The writer derives it
from the snapshot chain: the **first** ever row's `team_managed` is `league.current_team`
at write time (the founding team, CAR-01); each subsequent row inherits the prior row's
`team_managed` UNLESS a Reassignment happened (a Reassignment writes the new
`current_team`, and the next ensure pass reads `league.current_team` for the new tenure's
first row — see §3.4). In practice the writer reads `league.current_team` only when there
is no prior row OR the prior row's verdict was `"fired"` AND a Reassignment has since set a
new `current_team`. **Pin at code time:** the simplest correct rule is — the row's
`team_managed` is `league.current_team` when there is no prior in-tenure row, else the prior
row's `team_managed`; a `"fired"` prior row ends the tenure so the next row reads
`league.current_team` again (the post-Reassignment team).

#### 3.1a Flat-input assembly (view-side, per Season) — REUSE the standings path

The writer (or a private helper it calls) builds, for each Season + its `team_managed`:

- **wins / games:** REUSE `Season._final_standings_for_phase(season.ordered_phases()[-1])`
  (or the same `compute_standings` assembly it performs) to get the regular-season Standings;
  read the `StandingsRow` for `team_managed.id` → `won = row.wins`, `games = row.matches_played`.
  (The standings query already excludes playoff Matches — they carry `season=NULL`.)
- **playoffs:** locate the Season's `tournament` phase via `season.ordered_phases()` (the
  `SeasonPhase` with `phase_type == "tournament"` and `tournament_id is not None`):
  - no such built phase ⇒ `playoff_result = "none"`, `rounds_won = 0`, `num_rounds = 0`.
  - else read `tournament = phase.tournament`:
    - `num_rounds = max(bracket_round over the tournament's nodes)` (the bracket's depth).
    - `champion` ⇒ `playoff_result = "champion"` when `tournament.champion_id == team_managed.id`.
    - in the bracket but not champion ⇒ `playoff_result = "seeded"`, `rounds_won =` the count
      of distinct `bracket_round`s in which `team_managed` won a node (a `BracketNode` whose
      `winner_id == team_managed.id`).
    - seeded as a participant but cut / never advanced **and not seeded into the bracket at
      all** ⇒ `playoff_result = "missed"`. (Pin the champion/seeded/missed classification at
      code time off `Tournament.participants` + `BracketNode.winner_id` /
      `tournament.champion_id`; the seam to the pure fn is the resolved string + two ints.)
- **money:** always `("none"-irrelevant)` → `money_delta = 0.0` (dormant).

The pure module receives only `(won, games)` and `(playoff_result, rounds_won, num_rounds)`
— never a Django object.

### 3.2 `next_season` refactor — shared rollover body `_run_season_rollover`

Extract the **rollover body** of the current `next_season` (`league_views.py:2954`→`3011`:
create the new draft `Season`, carry teams, carry map pool, carry phases, then
`_develop_league_for_new_season`) into:

```python
def _run_season_rollover(league: League, latest_completed: Season) -> Season:
    """Create + return the next draft Season (teams/map/phases carried, players
    developed). The shared body consumed by BOTH the normal Start-Next-Season
    path and the reassign-then-roll path."""
```

- Returns the new `Season`. Contains the EXACT existing body (steps unchanged, byte-for-byte
  — name `f"Season {len(all_seasons)+1}"`, Jan-1-next-year start, schedule_format /
  map_mode carry, teams `add`, map_pool `set`, phase copy loop, then
  `_develop_league_for_new_season(league, new_season, latest_completed)`). Stays inside the
  caller's `@transaction.atomic` (the helper is NOT separately decorated — the caller owns
  the atomic boundary; **both** call sites are `@transaction.atomic`).
- **`next_season` (rewritten, `@transaction.atomic` kept) becomes the VERDICT GATE +
  rollover caller:**
  1. 405 on non-POST; `get_object_or_404(League)`; `last_league_id` session write
     (unchanged).
  2. active-Season guard → 302 to its dashboard (unchanged).
  3. `latest_completed` resolve; no completed ⇒ 400 `HttpResponseBadRequest("No completed
     Season in this League.")` (unchanged).
  4. **NEW verdict gate:** `_ensure_owner_evaluations(league, latest_completed)`; read the
     `OwnerEvaluation` for `(league, latest_completed)`. **If `verdict == "fired"` AND the
     Manager has NOT yet reassigned** (i.e. `league.current_team` still == the row's
     `team_managed` — a fired-and-unreassigned manager) ⇒ **redirect to `new_team_picker`**
     (`reverse("new_team_picker", kwargs={"league_id": league.id})`) — a
     fired-and-unreassigned manager **cannot roll**. Else (`retained` / `hot_seat`, or
     `fired` but already reassigned) ⇒ `_run_season_rollover(league, latest_completed)` +
     redirect to the new Season's dashboard (unchanged terminal behaviour).

**Byte-equivalence guarantee (LOCKED for the test agent):** for a **non-fired** Manager the
`next_season` rollover output is byte-equivalent to today (same Season name / start / teams /
map / phases / developed players) — the only added work is the `_ensure_owner_evaluations`
write + the verdict read, which do not touch the rollover body.

### 3.3 `owner_evaluation` view (GET)

```python
def owner_evaluation(request, season_id: int) -> HttpResponse:
```

GET-only league-screen, mirroring the `player_detail` / `season_awards` shell:

1. `if request.method != "GET": return HttpResponseNotAllowed(["GET"])` (first line).
2. `season = get_object_or_404(Season, pk=season_id)` (404 on missing).
3. `request.session["last_league_id"] = season.league_id` (int, LG-01f contract).
4. `league = season.league`; `_ensure_owner_evaluations(league, season)` (lazy + idempotent
   — ensures THIS Season's row + all prior in-tenure rows exist before reading).
5. Read the `OwnerEvaluation` for `(league, season)` (it now exists). **404** when `season`
   is **not completed** (the eval screen is only meaningful for a completed Season — pin: if
   `season.state != "completed"` and no row was written, return 404; the writer skips
   non-completed Seasons).
6. `displayed_season = season`; `sidebar_links = _build_league_sidebar_links(league,
   displayed_season, sidebar_active=None)` (no sidebar entry matches — every entry inactive).
7. Context (frozen keys): `season`, `league`, `displayed_season`, `sidebar_links`,
   `sidebar_active` (= `None`), `evaluation` (the `OwnerEvaluation` row), and the CTA flags
   `is_fired` (= `evaluation.verdict == "fired"`), `reassigned` (= the fired-and-already-
   reassigned bool, so the screen shows "Choose New Team" vs "Start Next Season").
8. `render(request, "seasons/owner_evaluation.html", context)`.

### 3.4 New-Team picker view (GET) + reassign view (POST)

```python
def new_team_picker(request, league_id: int) -> HttpResponse:    # GET-only
def reassign_team(request, league_id: int) -> HttpResponse:      # POST-only
```

**`new_team_picker`** (GET): 405 guard; `get_object_or_404(League)`; `last_league_id` write;
resolve `latest_completed` (the just-completed Season). Builds the **eligible team list** =
the **worst-`WORST_N_ELIGIBLE` (5)** teams by the just-completed Season's **final Standings**
(`Season._final_standings_for_phase(latest_completed.ordered_phases()[-1])` → take the rows
with the **highest `rank`** = worst, slice 5), **EXCLUDING the just-left team**
(`league.current_team`, the team that fired the Manager). Context: `league`,
`latest_completed`, `eligible_teams` (`list[(StandingsRow, Team)]` or `list[Team]` — pin the
shape at code time; the template needs team id + name + rank), `sidebar_links`,
`sidebar_active` (= `None`). Renders `templates/leagues/new_team.html`.

**`reassign_team`** (POST, `@transaction.atomic`): 405 guard; `get_object_or_404(League)`;
read `team_id` from POST → validate it is one of the eligible worst-N teams (re-derive the
eligible set server-side; reject a tampered/out-of-set id with a 400). Then (LOCKED order):
1. `league.current_team = <picked team>`; `league.save(update_fields=["current_team"])` —
   this **sets `current_team` + starts a new tenure** (the next
   `_ensure_owner_evaluations` pass sees the changed `team_managed` and resets cumulative +
   grace from the snapshot chain).
2. `_run_season_rollover(league, latest_completed)` — the SAME shared body as `next_season`.
3. Redirect (302) to the new Season's dashboard.

A fired manager therefore: hits `next_season` → gets redirected to `new_team_picker` →
POSTs `reassign_team` → reassigned + rolled. The verdict gate in `next_season` (§3.2) blocks
a fired-and-unreassigned manager from rolling.

### 3.5 `_build_history_row` + a `_player_award_labels`-style verdict accessor

**No new field on `_build_history_row` is required by CAR-02** — but the dashboard reroute
(§4) needs the verdict for the just-completed Season; expose it via the eval row read
(§3.3), not via `_build_history_row`. (If a "View past evaluations" surface needs a per-row
verdict on the History page, add an `owner_verdict` key to `_build_history_row` reading the
`OwnerEvaluation` for that Season — **optional, pin at code time**; the locked requirement is
the per-Season eval screen browsability via the dashboard link, §4.)

---

## 4 · URLs, templates, DOM ids, dashboard reroute

### 4.1 URLs (bare names, no `app_name`)

- `matches/season_urls.py`: add `path("<int:season_id>/owner-evaluation/",
  league_views.owner_evaluation, name="owner_evaluation")` — insert **before** the
  `standings/` / `schedule/` entries and after `awards/` (first-match resolution: the typed
  `<int:season_id>/owner-evaluation/` prefix is unambiguous, but keep it grouped with the
  other typed-prefix routes).
- `matches/league_urls.py`: add
  `path("<int:league_id>/new-team/", league_views.new_team_picker, name="new_team_picker")`
  and `path("<int:league_id>/reassign-team/", league_views.reassign_team,
  name="reassign_team")` — insert **after** `<int:league_id>/next-season/` and **before** the
  `""` `league_list` catch-all (the digit-only `<int:league_id>` prefix cannot shadow the
  literal routes; the catch-all must stay last).

### 4.2 Templates + LOCKED DOM ids

**`templates/seasons/owner_evaluation.html`** — extends `base.html`, `d-flex` +
`{% include "_partials/league_sidebar.html" %}` shell (the LG-06h/LG-03 league-screen
convention). LOCKED DOM ids:

| DOM id | Renders |
|---|---|
| `owner-evaluation` | root container |
| `owner-evaluation-verdict` | the verdict badge — text == `evaluation.verdict` (`retained`/`hot_seat`/`fired`) |
| `owner-evaluation-hot-seat-warning` | the hot-seat message (only when `verdict == "hot_seat"`; `hot_seat_level` 1 ⇒ "another season…", 2 ⇒ "a couple more…") |
| `owner-evaluation-factor-wins` | wins delta + cumulative-capped total |
| `owner-evaluation-factor-playoffs` | playoffs delta + total |
| `owner-evaluation-factor-money` | money delta + total (renders `0.0` — dormant) |
| `owner-evaluation-total` | overall cumulative mood (`wins_total + playoffs_total + money_total`) |
| `owner-evaluation-cta-start-next` | the "Start Next Season" form/button — rendered when `not is_fired` (retained / hot_seat); POSTs `next_season` |
| `owner-evaluation-cta-choose-team` | the "Choose New Team" link — rendered when `is_fired and not reassigned`; links `{% url 'new_team_picker' league.id %}` |

**`templates/leagues/new_team.html`** — same shell. LOCKED DOM ids:

| DOM id | Renders |
|---|---|
| `new-team-picker` | root container |
| `new-team-form` | the `<form method="post" action="{% url 'reassign_team' league.id %}">` |
| `new-team-option-{team_id}` | one selectable option/row per eligible worst-N team |
| `new-team-submit` | the submit button |

`{% csrf_token %}` mandatory inside both POST forms.

### 4.3 Dashboard reroute (gates "Start Next Season" through the eval screen)

In **both** `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`, the
existing `action_button_state == "start_next_season"` branch (currently a POST `<form>` to
`next_season`, DOM ids `{season,league}-dashboard-next-season-form`) is **rerouted to a GET
link to the eval screen**:

- The "Start Next Season" control becomes an **`<a>` (GET) to
  `{% url 'owner_evaluation' displayed_season.id %}`** (the eval screen then exposes the
  right CTA — Start Next Season if retained/hot_seat, Choose New Team if fired). LOCKED DOM
  ids (KEEP the existing wrapper ids for LG-01c/e test back-compat): the outer
  `{season,league}-dashboard-action-button` wrapper stays; the inner control's NEW DOM id is
  `{season,league}-dashboard-owner-evaluation-link` (was the `-next-season-form` POST form).
  The `data-action-state="start_next_season"` attribute is **carried on the link** so the
  LG-01c/e tests that scan for `data-action-state="start_next_season"` keep passing.
- **"View past evaluations" link (LOCKED):** add a dashboard link with DOM id
  `{season,league}-dashboard-past-evaluations-link`. Simplest target: the **League History**
  page (`{% url 'league_history' league.id %}`), where each row exposes a per-Season eval
  link (DOM id `league-history-owner-evaluation-link-{season_id}` →
  `{% url 'owner_evaluation' row.season_id %}`). (Pin the exact "browse past evaluations"
  surface at code time — either a per-row History link or a standalone list; the locked
  requirement is the per-Season eval screen is reachable for PAST Seasons via a dashboard
  link.)

**BLAST RADIUS (LOCKED):** LG-01e's `TestLg01eDashboardWiring` (in `test_league_dashboard.py`
+ `test_season_dashboard_view.py`) asserts the completed branch renders a `<form
id="…-next-season-form">` POSTing `next_season`. CAR-02 replaces that POST form with the
GET eval link → **those assertions must be updated** to the rerouted link shape (the
`data-action-state="start_next_season"` attribute survives on the link; the `-next-season-form`
id is replaced by `-owner-evaluation-link`). Call this out explicitly in the test plan.

---

## 5 · Docs deltas (for the Docs agent — DO NOT edit here)

- **PLAN.md:** mark **CAR-02 done**; add a **finance-subsystem follow-up** item (player
  salary + team budget + season profit → lights up the dormant *money* mood factor).
- `matches/CLAUDE.md`: a **CAR-02 manager firing** subsection (model + pure module + views +
  URLs + DOM ids, mirroring the CAR-01 entry).
- ADR-0026 is already written (no edit). CONTEXT.md terms are already finalised (no edit).

---

## 6 · Test boundary

Assertion discipline (LOCKED): assert **schema-level outcomes / verdicts / row shapes / DOM
ids** — **NEVER exact simulated point totals** (tournament sims are non-deterministic; build
standings/brackets from hand-constructed `Match`/`GameRound`/`BracketNode` rows).

### 6.1 Pure-unit — `matches/tests/test_owner_mood.py`
Over `compute_wins_delta` / `compute_playoffs_delta` / `cap_cumulative` / `decide_verdict`
ONLY (NO DB):
- `compute_wins_delta`: .500 ⇒ 0; above/below .500 signed; `games == 0` ⇒ `0.0`.
- `compute_playoffs_delta`: `champion`/`missed`/`none` constants; `seeded` scaling
  `(0.16/num_rounds)*rounds_won`; `num_rounds == 0` ⇒ `0.0`; unknown string ⇒ `0.0`.
- `cap_cumulative`: caps at `+1`, no negative floor, additive below cap.
- `decide_verdict`: grace suppresses firing/hot-seat at `seasons_in_tenure <=
  GRACE_PERIOD_SEASONS`; past grace `total <= -1` ⇒ `fired`; `total + delta < -1` ⇒
  `hot_seat` level 1; `total + 2*delta < -1` ⇒ level 2; else `retained`; the level-1-wins-
  when-both-hold ordering.
- `TestNoDjangoImportsLeaked` (subprocess fresh-import + `sys.modules` walk).

### 6.2 Model/constraint — `matches/tests/test_owner_evaluation_model.py` (NEW)
- field defaults (`money_delta`/`money_total`/`hot_seat_level` default), `verdict` choices,
  `Meta.ordering`, the `uniq_league_season_owner_evaluation` constraint rejects a duplicate
  `(league, season)`, CASCADE on League/Season delete, `team_managed` SET_NULL.

### 6.3 Writer — `matches/tests/test_owner_evaluations_writer.py` (NEW, `TestCase`)
- `_ensure_owner_evaluations` writes rows **oldest→newest in Season order** with correct
  per-factor cap-chaining + cumulatives; **idempotent** (a second call writes no new rows,
  leaves existing untouched); **tenure-reset derivation** — a `team_managed` change between
  consecutive rows resets cumulative + the grace counter (hand-construct a fired→reassigned
  chain via direct `current_team` mutation + a written `"fired"` row); **no backfill** of
  Seasons before the first computable one.

### 6.4 Eval screen view — `matches/tests/test_owner_evaluation_view.py` (NEW)
- 200 / 404 (missing season + non-completed season) / 405 (non-GET); `last_league_id`
  session write; the verdict-per-mood matrix (retained / hot_seat / fired) over
  hand-built standings + bracket fixtures; the LOCKED DOM ids present
  (`owner-evaluation-verdict`, `-hot-seat-warning` only on hot_seat, the 3 `-factor-*`,
  `-total`, the two CTAs gated on `is_fired`/`reassigned`); past-Season browsability (the
  view ensures + reads a prior Season's row).

### 6.5 New-Team picker + reassign — `matches/tests/test_reassign_team.py` (NEW)
- picker: 200 / 405; the eligible list is the **worst-5** by the just-completed Season's
  final Standings with the just-left team **excluded**; the LOCKED DOM ids.
- reassign: 302 / 405 / 400 (out-of-set `team_id`); sets `current_team` to the picked team;
  starts a new tenure (the next ensure pass resets cumulative + grace); the shared
  `_run_season_rollover` runs (a new draft Season exists after the POST).

### 6.6 `next_season` refactor — `matches/tests/test_league_next_season.py` (EXTEND)
- **non-fired path is byte-equivalent** to the pre-CAR-02 rollover (same new-Season
  name/start/teams/map/phases/developed-player shape); the `OwnerEvaluation` row for the
  just-completed Season is written.
- **fired-and-unreassigned is blocked:** a fired Manager hitting `next_season` is redirected
  to `new_team_picker` and **no new Season is created**.
- the existing rollback/atomicity test still holds (verdict gate + ensure-writer inside the
  atomic boundary).

### 6.7 Dashboard reroute — extend `test_league_dashboard.py` + `test_season_dashboard_view.py`
- the completed branch renders the GET eval link (DOM id
  `{season,league}-dashboard-owner-evaluation-link` → `owner_evaluation`) carrying
  `data-action-state="start_next_season"` (the LG-01e `-next-season-form` assertions are
  **updated** to this shape — explicit blast radius); the "View past evaluations" link
  (`{season,league}-dashboard-past-evaluations-link`) renders.

---

## 7 · Scope-out (LOCKED — do NOT build)
Challenge-mode firings (miss-playoffs / luxury-tax) — DEFERRED. Voluntary rival-offer
switching — DEFERRED. Live *money* factor / any finance subsystem — DEFERRED (money is the
dormant `0.0` column + factor). No Manager/User model (the Manager is `League.current_team`,
CAR-01). No simulator change ⇒ NO Score Calibration re-baseline. No backfill / `RunPython`.
No new CONTEXT.md term (all 10 finalised). No ADR write (ADR-0026 already written). CAR-03
(gate firing to single-player `league` mode) is later — this slice operates in `league` mode,
the only mode with `current_team`.

---

### Naming/placement choices the existing code forced (one line)
`next_season` is ALREADY `@transaction.atomic` with its rollover body inline at
`league_views.py:2954-3011`, so I pinned `_run_season_rollover(league, latest_completed) ->
Season` as a **plain (undecorated) helper** extracted verbatim from that body and called by
BOTH `next_season` and `reassign_team` inside their own atomic blocks (the existing decorator
owns the boundary), and I derive the per-Season `team_managed` / tenure boundaries **from the
`OwnerEvaluation` snapshot chain** (a `team_managed` change between consecutive rows = a new
tenure) because firings mutate `League.current_team` and a past Season's managed team is
otherwise unrecoverable.
