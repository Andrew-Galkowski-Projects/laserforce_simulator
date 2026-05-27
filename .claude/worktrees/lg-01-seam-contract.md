# LG-01 Seam Contract — League / Season foundation

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in
parallel. Every name, signature, dataclass field, dict shape, URL, template,
DOM id, and test class name below is **frozen**. If reality contradicts a
name here, STOP and flag against this artifact — do not silently drift.

Branch: `lg-01-league-season-foundation`. Paths relative to the nested
Django project root `laserforce_simulator/laserforce_simulator/` (where
`manage.py` lives).

This contract is the single source of truth for LG-01 — the **foundation**
of single-player league mode. It ships the model + algorithm + simulator
surface layer only. The user-facing surfaces (mode picker, create flow,
dashboard, Play Next, history, team game log) are LG-01a..g — explicitly
**out of scope** for LG-01 itself (see §8). Two ADRs already exist and
are NOT re-written by LG-01: [ADR-0014](../../docs/adr/0014-league-season-foundation.md)
locks the model, [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md)
locks the algorithm surface. CONTEXT.md already carries the new
`### League and seasons` subsection (League / Season / Standings glossary
entries) — also **not** edited by LG-01.

---

## 0. Resolved decisions (DO NOT re-open)

These are baked into the contract — the implementation-grill outcomes:

- **One migration**, file name pinned: `matches/migrations/0029_league_season_match_fk.py`.
  Bundles `CreateModel(League)` + `CreateModel(Season)` + `AddField(Match,
  season)`. Dependency: prior matches migration `0028_gameround_is_simulated`.
  **No backfill** ([ADR-0004](../../docs/adr/0004-simulation-data-is-disposable.md)
  precedent).
- **Models live in `matches/`** (they own the `Match.season` FK and the
  simulator surface).
- **`Season.start_date` is required** (DateField, no default).
- **No new `Match` columns.** Partial completion rides on existing
  `is_completed=False` + `*_round1_*` populated + `*_round2_*` at default
  zero. `Match.calculate_winner` runs unchanged.
- **Active-Season invariant** enforced by `Season.clean()` (≤1 non-`completed`
  Season per League).
- **`starting_team_ids_json` is snapshotted ascending-sorted** at
  `draft → active`; the schedule algorithm reads this snapshot, not the live
  M2M.
- **Side-agnostic Match lookup** for round 2: `(season, {team_a_id,
  team_b_id})` resolved via two ORM queries `(red=a, blue=b)` OR
  `(red=b, blue=a)`, take `.first()`.
- **Args-reversed per-Match colour swap** for round 2 mirrors
  `simulate_match` byte-for-byte (`team_red=team_b, team_blue=team_a`).
- **Pure modules with frozen import allowlists**: `matches/schedule_generator.py`
  and `matches/standings.py`. No Django, no RNG, no I/O. Defensive
  `TestNoDjangoImportsLeaked` subprocess check (HX-01 / HX-02 / HX-03 / HX-04
  / RES-04 / RV-03 / LG-00 / LG-00b precedent).
- **Compute Standings sort tiebreak (a) — name in the tuple.** The pure
  module signature for `compute_standings` takes
  `enrolled_teams: list[tuple[int, str]]` (id, name) so the alphabetical
  tiebreak stays inside the pure module. Alternative (b) — drop the
  alphabetical tiebreak from the pure module and apply view-side — was
  evaluated and **rejected**: keeping the tiebreak inside the pure module
  means the full standings ordering is purely a function of `(completed_matches,
  enrolled_teams)` and is verifiable from a unit test with no DB.
- **Read-only view surfaces only.** Both views (`season_standings`,
  `season_schedule`) are `GET`-only. No POST routes in LG-01 (Play Next is
  LG-01d).
- **Admin** registers `League` and `Season`. No admin for `Match.season`
  beyond the existing `Match` admin (if any) — Code agent does NOT change
  `Match` admin registration in LG-01.
- **No CONTEXT.md edit, no ADR edits, no PLAN.md edit beyond Docs marking
  LG-01 done.**

---

## 1. Models

### 1a. `League` (NEW — `matches/models.py`)

```python
class League(models.Model):
    MODE_CHOICES = (
        ("sandbox", "Sandbox"),
        ("league", "League"),
        ("multiplayer", "Multiplayer"),
    )
    STATE_CHOICES = (
        ("active", "Active"),
        ("archived", "Archived"),
    )

    name = models.CharField(max_length=100)
    mode = models.CharField(max_length=16, choices=MODE_CHOICES, default="league")
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="active")
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    @property
    def active_season(self) -> "Season | None":
        """The single non-completed Season in this League, or None.

        Returns the most-recently-created non-completed Season (excludes
        Seasons in state ``completed``). The active-Season invariant (≤1
        non-completed Season per League, enforced by ``Season.clean``)
        guarantees this is well-defined.
        """
        return self.seasons.exclude(state="completed").order_by("-id").first()
```

**Pinned:**
- `name` `CharField(max_length=100)` — no `unique=True`, no validators
  beyond `max_length`.
- `mode` choices are tuple-of-2-tuples in the locked order above; default
  `"league"`. `max_length=16` covers the longest value `"multiplayer"`.
- `state` choices in locked order; default `"active"`. `max_length=16`.
- `created_at` is `auto_now_add=True` (no `auto_now`).
- `seasons` reverse-accessor comes from `Season.league.related_name="seasons"`
  below.
- `__str__` returns `self.name`.
- `active_season` is a `@property`, not a method. Tests call it as
  `league.active_season` (no parentheses).

### 1b. `Season` (NEW — `matches/models.py`)

```python
class Season(models.Model):
    STATE_CHOICES = (
        ("draft", "Draft"),
        ("active", "Active"),
        ("completed", "Completed"),
    )
    SCHEDULE_FORMAT_CHOICES = (
        ("single_round_robin", "Single round-robin"),
    )

    league = models.ForeignKey(
        League,
        on_delete=models.CASCADE,
        related_name="seasons",
    )
    name = models.CharField(max_length=100)
    start_date = models.DateField()  # required, no default
    teams = models.ManyToManyField(
        "teams.Team",
        related_name="enrolled_seasons",
    )
    state = models.CharField(max_length=16, choices=STATE_CHOICES, default="draft")
    schedule_format = models.CharField(
        max_length=32,
        choices=SCHEDULE_FORMAT_CHOICES,
        default="single_round_robin",
    )
    starting_team_ids_json = models.JSONField(null=True, blank=True, default=None)
    champion_team = models.ForeignKey(
        "teams.Team",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="seasons_won",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return f"{self.league.name} — {self.name}"

    def clean(self) -> None:
        """Active-Season invariant: ≤1 non-completed Season per League."""
        ...

    @transaction.atomic
    def start_season(self) -> None:
        """Flip ``draft → active``, snapshot M2M into starting_team_ids_json."""
        ...

    @transaction.atomic
    def complete_if_finished(self) -> None:
        """Auto-transition ``active → completed`` if every fixture is played."""
        ...
```

**Field semantics — pinned:**

| Field | Type / kwargs | Notes |
|---|---|---|
| `league` | `FK(League, on_delete=CASCADE, related_name="seasons")` | CASCADE — deleting a League deletes its Seasons. |
| `name` | `CharField(max_length=100)` | No unique, no validators. |
| `start_date` | `DateField()` | **Required**, no default — Code agent must NOT add `null=True` or a default. |
| `teams` | `ManyToManyField("teams.Team", related_name="enrolled_seasons")` | M2M to existing Team app. No `through=`. |
| `state` | `CharField(max_length=16, choices=STATE_CHOICES, default="draft")` | Choices in locked order: draft → active → completed. |
| `schedule_format` | `CharField(max_length=32, choices=…, default="single_round_robin")` | Only one choice v1; extensible. |
| `starting_team_ids_json` | `JSONField(null=True, blank=True, default=None)` | NULL while `draft`; sorted list of team ids the moment `start_season()` fires. |
| `champion_team` | `FK("teams.Team", on_delete=SET_NULL, null=True, blank=True, related_name="seasons_won")` | NULL until `complete_if_finished()` stamps it. SET_NULL — deleting a Team must NOT delete its Seasons-won. |
| `created_at` | `DateTimeField(auto_now_add=True)` | No `auto_now`. |

**`__str__`** returns the f-string `f"{self.league.name} — {self.name}"`
(em-dash, U+2014). Pinned by `TestSeasonModel.test_str`.

### 1c. `Season` methods — pinned signatures

```python
def clean(self) -> None:
    """Validate Active-Season invariant.

    Raises ``django.core.exceptions.ValidationError`` if saving would yield
    more than one non-``completed`` Season in this League. Excludes ``self``
    when updating (so re-saving an existing active Season does not trip the
    check against itself).
    """
```

- Pinned by `TestSeasonCleanInvariant.test_second_non_completed_season_in_same_league_raises`
  and `TestSeasonCleanInvariant.test_ok_when_first_season_is_completed`.
- The check uses `Season.objects.filter(league=self.league).exclude(state="completed").exclude(pk=self.pk)`
  — `pk=None` for unsaved objects naturally excludes nothing extra.
- Raises `django.core.exceptions.ValidationError`. The Code agent imports
  it as `from django.core.exceptions import ValidationError`.

```python
@transaction.atomic
def start_season(self) -> None:
    """draft → active transition.

    Raises ``ValidationError`` when ``self.teams.count() < 2``. Snapshots
    ``starting_team_ids_json = sorted([t.id for t in self.teams.all()])``;
    sets ``state="active"``; ``self.save()``.
    """
```

- `@transaction.atomic` — pinned. The snapshot + state flip + save must
  not interleave with another writer.
- Snapshot is `sorted([t.id for t in self.teams.all()])` — sorted
  ascending. `LG-01-schedule-generator` reads this list directly without
  re-sorting (the schedule generator internally sorts too — defence in
  depth).
- The Code agent must NOT call `self.clean()` from inside `start_season`;
  the active-Season invariant is enforced by `clean()` on `save()` via
  `full_clean()` / admin / the `clean()` check that runs *before* this
  method is invoked.
- Pinned by `TestSeasonStartSeason.test_flips_state_to_active`,
  `test_snapshots_starting_team_ids_sorted`,
  `test_raises_when_fewer_than_two_teams`.

```python
@transaction.atomic
def complete_if_finished(self) -> None:
    """active → completed (idempotent).

    No-op if ``self.state != "active"``. Calls
    ``generate_schedule(self.starting_team_ids_json, self.schedule_format)``
    and compares against persisted ``GameRound``s. If every fixture has a
    matching played Round, flips ``state="completed"``, computes
    Standings via ``compute_standings(...)``, and stamps
    ``champion_team`` to ``Team.objects.get(pk=rows[0].team_id)``.
    """
```

- Idempotent — re-calling after completion is a no-op.
- The "fixture played" predicate is **Side-agnostic**: a `GameRound`
  matches a fixture if `frozenset({game_round.team_red_id,
  game_round.team_blue_id}) == frozenset({fixture.team_a_id,
  fixture.team_b_id})` AND `game_round.round_number == fixture.round_number`.
  Game rounds are gathered from `GameRound.objects.filter(match__season=self)`.
- Standings input gathering: query `Match.objects.filter(season=self,
  is_completed=True)` and map each row to the 8-key dict shape (§2b).
  `enrolled_teams` is built as `[(t.id, t.name) for t in Team.objects.filter(
  id__in=self.starting_team_ids_json)]`.
- `champion_team` stamp: `Team.objects.get(pk=rows[0].team_id)` —
  the row at index 0 of `compute_standings`' output (rank 1).
- Pinned by `TestSeasonCompleteIfFinished.test_no_op_when_fixtures_not_played`,
  `test_flips_to_completed_when_all_played`,
  `test_stamps_champion_to_row_0`,
  `test_idempotent_on_re_call`,
  `test_no_op_when_state_is_not_active`.

### 1d. `Match.season` (FIELD ADDITION on existing `Match` model)

```python
season = models.ForeignKey(
    "matches.Season",
    null=True,
    blank=True,
    on_delete=models.SET_NULL,
    related_name="matches",
)
```

- Position in `Match`: **after** the existing `winner` field (or at the
  bottom of the field block — Code agent picks; tests do not pin
  position).
- `on_delete=SET_NULL` — deleting a Season must NOT cascade-delete its
  Matches (ADR-0014; pinned by
  `TestMatchSeasonFK.test_deleting_season_sets_match_season_to_null`).
- `null=True, blank=True` — sandbox Matches stay `season=NULL`.
- `related_name="matches"` — `season.matches.all()` is the reverse
  accessor.

### 1e. Imports added to `matches/models.py`

The Code agent adds (if not already present):

```python
from django.core.exceptions import ValidationError
from django.db import transaction
```

The Code agent must NOT remove or reorder any existing import.

---

## 2. Pure modules

### 2a. `matches/schedule_generator.py` (NEW)

**Frozen import allowlist** — the module's `import` statements may
reference ONLY:

- `dataclasses` (for `@dataclass`)
- `typing` (for `Optional`, `Sequence` if needed — `list[int]` etc. use PEP-585 syntax inline)
- `collections` (only if needed — not required by the locked algorithm)

**Pinned: NO** Django, NO `random` / `secrets`, NO `datetime`, NO file
I/O, NO logging. Pinned by `TestNoDjangoImportsLeaked` in
`matches/tests/test_schedule_generator.py` (subprocess fresh-import
+ walk `sys.modules`; HX-03 pattern).

**Public surface — frozen literal:**

```python
SCHEDULE_FORMATS: tuple[str, ...] = ("single_round_robin",)
"""View-side validation surface. Adding a format = appending a tuple entry
+ a branch in ``generate_schedule``."""


@dataclass(frozen=True)
class ScheduleFixture:
    matchday: int        # 1-based
    round_number: int    # 1 or 2
    team_a_id: int       # the team in the team_red slot of the underlying Match
    team_b_id: int       # the team in the team_blue slot


def generate_schedule(
    team_ids: list[int],
    schedule_format: str = "single_round_robin",
) -> list[ScheduleFixture]:
    """Return the deterministic fixture list for these enrolled teams.

    Args:
        team_ids: list of team ids. Sorted ascending internally before the
            algorithm runs, so the output is a function of the *set*, not
            of input order.
        schedule_format: one of ``SCHEDULE_FORMATS``.

    Returns:
        list of ``ScheduleFixture`` sorted by ``(matchday, team_a_id)``.

    Raises:
        ValueError: if ``schedule_format`` not in ``SCHEDULE_FORMATS``.
        ValueError: if ``len(team_ids) < 2``.
    """
```

**Algorithm — single round-robin (pinned):**

1. Sort `team_ids` ascending. Reject `len < 2` with `ValueError`.
2. If `N := len(team_ids)` is odd, append a phantom "bye" sentinel —
   the value used is **`-1`** (sentinel literal, NOT `None`; documented
   here so tests can assert it does not appear in output). Pairs whose
   one side is `-1` are dropped from the output entirely.
3. Run the **circle method**: fix `team_ids[0]`, rotate the remaining
   `N-1` (or `N` if a bye was appended) slots. For each rotation step
   `k in 0..N-2` (round-1 matchdays `1..N-1`):
   - Pair the fixed team with the head of the rotating slice.
   - Pair the remaining rotating slots symmetrically (`rotating[i]` with
     `rotating[-1-i]`).
   - Within each fixture, set `team_a_id = min(pair)` and
     `team_b_id = max(pair)` so the fixture is normalised before the
     output sort. (Pins `team_a_id < team_b_id` for non-bye fixtures —
     tested by `TestGenerateScheduleOrder`.)
4. Mirror: matchdays `N..2*(N-1)` replay every round-1 matchday's
   fixtures with `round_number=2`, in the same matchday-relative order.
   So matchday `k + (N-1)` (for `k in 1..N-1`) holds the same pairings
   as matchday `k`, with `round_number=2`.
5. Filter out any fixture involving the bye sentinel `-1`.
6. Sort output by `(matchday, team_a_id)` ascending. Return.

**Pinned consequences:**

- N=4 → 6 fixtures (3 matchdays × 2 fixtures, round 1: matchdays 1-3;
  round 2: matchdays 4-6 — 6 fixtures total).
- N=8 → 56 fixtures (7 matchdays × 4 fixtures × 2 rounds).
- N=5 → odd N: 5 round-1 matchdays + 5 round-2 matchdays = 10 matchdays;
  each matchday has 2 played fixtures (one bye dropped). Total fixtures:
  `5 * 2 * 2 = 20`.
- For even N: round-1 fixture count = `N * (N-1) / 2`; total = `N * (N-1)`.
- Round-1 matchdays span `1..N-1`; round-2 matchdays span `N..2*(N-1)`.
- The output of `generate_schedule([5, 1, 3, 7])` is identical to the
  output of `generate_schedule([1, 3, 5, 7])` (input-order
  independence) — pinned by `TestGenerateScheduleDeterminism`.

### 2b. `matches/standings.py` (NEW)

**Frozen import allowlist** — the module's `import` statements may
reference ONLY:

- `dataclasses`
- `typing`
- `collections` (likely needed for `defaultdict`)

**Pinned: NO** Django, NO `random`, NO `datetime`, NO I/O, NO logging.
Pinned by `TestNoDjangoImportsLeaked` in `matches/tests/test_standings.py`.

**Public surface — frozen literal:**

```python
@dataclass(frozen=True)
class StandingsRow:
    team_id: int
    matches_played: int
    wins: int
    losses: int
    ties: int
    league_points: int
    round_wins: int
    total_score: int
    rank: int


def compute_standings(
    completed_matches: list[dict],
    enrolled_teams: list[tuple[int, str]],
) -> list[StandingsRow]:
    """Aggregate Match outcomes into a ranked Standings table.

    Args:
        completed_matches: list of dicts with the 8 frozen keys below.
            Each represents one completed (``is_completed=True``) Match in
            the Season.
        enrolled_teams: list of ``(team_id, team_name)`` tuples — every
            team enrolled in the Season. Teams with no matches get a
            zero-filled row.

    Returns:
        list of ``StandingsRow`` sorted by
        ``(league_points desc, round_wins desc, total_score desc,
        team_name asc)``. ``rank`` is populated 1-based and dense.
    """
```

**Input dict shape — 8 frozen keys (every key required, every key
present on every entry, no extras consumed):**

```python
{
    "match_id":          int,
    "team_red_id":       int,
    "team_blue_id":      int,
    "winner_team_id":    int | None,   # None = tie (Match.winner_id IS NULL)
    "red_rounds_won":    int,           # Match.red_rounds_won
    "blue_rounds_won":   int,           # Match.blue_rounds_won
    "red_total_points":  int,           # Match.red_total_points (property — includes team-elim bonus)
    "blue_total_points": int,           # Match.blue_total_points (property — includes team-elim bonus)
}
```

The view-side builder (in `season_standings`) materialises this dict
from the existing `Match` model: `red_rounds_won` / `blue_rounds_won`
are read via the existing `Match.red_rounds_won` / `blue_rounds_won`
methods (or fields — Code agent picks the correct read path against
`matches/models.py`); `red_total_points` / `blue_total_points` are
read via the existing `@property` (no parentheses).

**Aggregation rules — pinned:**

- For each match entry, both `team_red_id` and `team_blue_id` get their
  per-team counters incremented (`matches_played += 1`).
- W/L/T attribution:
  - `winner_team_id == team_red_id` → red W, blue L
  - `winner_team_id == team_blue_id` → blue W, red L
  - `winner_team_id is None` → both T
  - **Defensive:** `winner_team_id` neither (legacy / corrupt data) →
    both T (mirrors HX-03 `compute_match_record` defensive behaviour).
- `league_points = 3 * wins + 1 * ties + 0 * losses`.
- `round_wins`: for the red side, add `red_rounds_won`; for the blue
  side, add `blue_rounds_won`.
- `total_score`: for the red side, add `red_total_points`; for the
  blue side, add `blue_total_points`.
- Teams in `enrolled_teams` with no rows in `completed_matches` get a
  fully-zeroed row.
- A match entry whose `team_red_id` or `team_blue_id` is NOT in
  `enrolled_teams` is **still aggregated** (its rows are added to the
  table). The Code agent does not filter — the view passes only the
  Season's matches.

**Sort order — pinned, in order:**

1. `league_points` desc
2. `round_wins` desc
3. `total_score` desc
4. `team_name` asc (the second element of the `enrolled_teams` tuple
   — the pure module receives the name so this tiebreak lives inside
   the pure module, NOT view-side; decision (a), §0)

**Rank** is populated 1-based, dense (1, 2, 3, …) — equal-ranked rows
that happen to compare equal across all 4 sort keys still get distinct
ranks in iteration order (the alphabetical tiebreak is the final
disambiguator; the rank field reflects index+1, not "competition
ranking").

**Edge cases:**

- `completed_matches=[]` and `enrolled_teams=[(1,"A"),(2,"B")]` →
  two zeroed rows ranked by name asc; ranks 1 and 2.
- `enrolled_teams=[]` → empty list returned.

### 2c. Implementation note for both pure modules

Both modules carry a module-level docstring mirroring the HX-03 /
HX-04 / RES-04 style: one paragraph summarising the public surface,
one paragraph listing the **frozen import allowlist** verbatim (this
list is the test's reference).

---

## 3. Simulator surface — `BatchSimulator.simulate_scheduled_round`

### 3a. Signature (frozen)

In `matches/simulation.py`, added as a method on the existing
`BatchSimulator` class:

```python
@transaction.atomic
def simulate_scheduled_round(
    self,
    season: "Season",
    team_a: "Team",
    team_b: "Team",
    round_number: int,
    *,
    arena_map: Optional["ArenaMap"] = None,
) -> "GameRound":
    """Simulate one Round of a Season Match.

    Round 1: find-or-create the Match (Side-agnostic) and persist
    ``GameRound(round_number=1)``. Match stays ``is_completed=False``.

    Round 2: find the existing Match Side-agnostically; simulate with
    args reversed (per-Match colour swap — mirrors ``simulate_match``
    verbatim); persist ``GameRound(round_number=2)``; flip
    ``match.is_completed=True`` and save (triggers
    ``calculate_winner``).

    After persistence (either round), call
    ``season.complete_if_finished()`` for auto-transition.

    Raises:
        ValueError: if ``season.state != "active"``.
        ValueError: if ``round_number not in (1, 2)``.
        ValueError: if ``round_number == 2`` and no existing Match found.
    """
```

**Pinned:**
- `@transaction.atomic` on the whole method.
- `season`, `team_a`, `team_b`, `round_number` positional; `arena_map`
  keyword-only (after `*`); default `None`.
- Return type `GameRound` (the newly-persisted Round).

### 3b. Guard sequence (pinned, in order)

1. `if season.state != "active": raise ValueError("Season must be active
   to simulate; got state={state}")` — exact message format not pinned,
   but the substring `"active"` must be present so the test can match
   forgivingly.
2. `if round_number not in (1, 2): raise ValueError("round_number must
   be 1 or 2; got {round_number}")`.
3. (Round-2 specific, below) raise `ValueError` if no existing Match
   found.

Pinned by `TestSimulateScheduledRoundGuards`.

### 3c. Side-agnostic Match lookup helper

The Match lookup logic is **inlined** in `simulate_scheduled_round`
(no separate helper method). Two ORM queries:

```python
match = (
    Match.objects
    .filter(season=season)
    .filter(team_red=team_a, team_blue=team_b)
    .first()
) or (
    Match.objects
    .filter(season=season)
    .filter(team_red=team_b, team_blue=team_a)
    .first()
)
```

- Returns `Match | None`. Round 1: `None` → create. Round 2: `None` →
  raise.

### 3d. Round 1 path (pinned)

1. Lookup via the side-agnostic query above.
2. If `match is None`: create with `Match.objects.create(
   season=season, team_red=team_a, team_blue=team_b, is_completed=False)`.
3. Call the existing per-Round simulation entry point that
   `simulate_match` already uses for round 1. **No new RNG consumption
   beyond what `simulate_match` already does at round 1** — the per-Round
   sim consumes one `rng_seed` per Round, just as today. The Code agent
   must mirror `simulate_match`'s round-1 setup byte-for-byte (same
   `arena_map` resolution, same seed-handling, same `_flush_to_db`
   parameters).
4. Persist `GameRound(round_number=1, match=match, team_red=team_a,
   team_blue=team_b, …)` — same persistence pattern as `simulate_match`.
5. Write to the Match:
   - `match.red_round1_points = …`
   - `match.blue_round1_points = …`
   - `match.red_round1_eliminated = …`
   - `match.blue_round1_eliminated = …`
   - `match.round1_eliminated_at = …`
6. Leave `match.is_completed = False`.
7. `match.save()`.
8. Call `season.complete_if_finished()` (no-op for round 1 unless this
   was the literal final fixture of the Season — possible only when N=2
   and round 1 of the second-half is the very last fixture; idempotent
   in any case).
9. Return the persisted `GameRound`.

### 3e. Round 2 path (pinned)

1. Lookup via the side-agnostic query above.
2. If `match is None`: raise `ValueError("No round-1 Match found for
   season=… team_a=… team_b=…; play round 1 first")` — substring
   `"round 1"` must be present.
3. Call the existing per-Round simulation entry point that
   `simulate_match` uses for round 2, **with args reversed**:
   `team_red=team_b, team_blue=team_a` — mirrors `simulate_match`'s
   per-Match colour swap byte-for-byte. The same `arena_map` resolution
   and seed-handling apply.
4. Persist `GameRound(round_number=2, match=match, team_red=team_b,
   team_blue=team_a, …)`. Note: the GameRound's `team_red` /
   `team_blue` reflect the **physical** colour for round 2 (i.e. team_b
   is red in round 2). This mirrors how `simulate_match` already
   persists round 2.
5. Write to the Match:
   - `match.red_round2_points = …`
   - `match.blue_round2_points = …`
   - `match.red_round2_eliminated = …`
   - `match.blue_round2_eliminated = …`
   - `match.round2_eliminated_at = …`
6. Set `match.is_completed = True`.
7. `match.save()` — triggers `calculate_winner` via the existing
   `save()` override, populating `match.winner`.
8. Call `season.complete_if_finished()`.
9. Return the persisted `GameRound`.

### 3f. No simulation mechanics change

The new method is **purely an orchestration layer** over the existing
per-Round simulator. The Code agent must not modify any function in
`matches/sim_helpers/`, must not change `simulate_match` behaviour,
and must not introduce new RNG draws. Pinned by §9.

---

## 4. Views + URLs + templates

### 4a. `season_standings` view (NEW — `matches/views.py`)

```python
def season_standings(request, season_id: int) -> HttpResponse:
    """LG-01 — Standings page for a Season.

    Draft preview: when ``season.state == "draft"``, lists enrolled teams
    sorted by computed team_overall desc (then name asc), rendered as
    zeroed StandingsRow-shaped dicts. Banner indicates "Preview — Season
    not started".

    Active / completed: aggregates completed Season Matches via
    ``compute_standings``.
    """
```

**Pinned behaviour:**

- `season = get_object_or_404(Season, pk=season_id)`.
- Branch on `season.state`:
  - `"draft"`:
    - `teams = list(season.teams.all())`
    - For each team, compute `team_overall = mean(p.overall_rating for p
      in team.active_players) if team.active_players else 0.0` — uses the
      existing `Team.active_players` `@property` (returns the 6 starting-
      lineup players via `slot_*` FKs) and the existing
      `Player.overall_rating` `@property`. **There is NO `is_bench` field
      on `Player`** — bench is derived from "not in any slot_* FK" via
      the existing `Team.bench_players` `@property`. The Code agent MUST
      NOT add a `Player.is_bench` field and MUST NOT query
      `team.players.filter(is_bench=False)`. If `team.active_players` is
      empty (no slots filled — e.g. Free Agents Team), `team_overall =
      0.0`.
    - Sort `teams` by `(-team_overall, name)`.
    - Build `rows` as a list of dicts (NOT the dataclass — the template
      treats both shapes via attribute / key access; the contract pins
      dict). Each dict has the same 9 keys as the `StandingsRow`
      dataclass: `team_id, matches_played=0, wins=0, losses=0, ties=0,
      league_points=0, round_wins=0, total_score=0, rank=i+1`. Code
      agent must NOT add extra keys like `team_overall` to the dict —
      the template renders the team name and link from the matching
      `Team` row queried separately (see template §4d).
    - Set `is_draft_preview = True`.
  - `"active"` or `"completed"`:
    - `qs = Match.objects.filter(season=season, is_completed=True)`.
    - Build `completed_matches` as a list of 8-key dicts (§2b).
    - Determine `team_ids`: use `season.starting_team_ids_json` if not
      None, otherwise (defensive) `sorted([t.id for t in
      season.teams.all()])`. Build `enrolled_teams = list(Team.objects
      .filter(id__in=team_ids).values_list("id", "name"))`.
    - `rows = compute_standings(completed_matches, enrolled_teams)`
      (list of `StandingsRow` dataclass instances).
    - Set `is_draft_preview = False`.
- Render `templates/seasons/standings.html`.

**Context keys (frozen):**

```python
{
    "season":             Season,
    "rows":               list[StandingsRow] | list[dict],  # dataclass in active/completed, dicts in draft
    "is_draft_preview":   bool,
    "teams_by_id":        dict[int, Team],  # for template lookups of team name + detail URL
}
```

The `teams_by_id` map is built as `{t.id: t for t in
Team.objects.filter(id__in=[r.team_id if hasattr(r, "team_id") else
r["team_id"] for r in rows])}` — a single query, both branches use it
the same way.

### 4b. `season_schedule` view (NEW — `matches/views.py`)

```python
def season_schedule(request, season_id: int) -> HttpResponse:
    """LG-01 — Schedule page for a Season.

    Renders the deterministic fixture list (from
    ``generate_schedule``) with played GameRounds overlaid. Fixtures
    grouped by matchday; display date = ``season.start_date +
    (matchday - 1) * 7 days``.
    """
```

**Pinned behaviour:**

- `season = get_object_or_404(Season, pk=season_id)`.
- Determine `team_ids`:
  - If `season.state == "draft"`: `team_ids = sorted([t.id for t in
    season.teams.all()])`.
  - Else: `team_ids = season.starting_team_ids_json` (which is
    guaranteed non-None at `active` / `completed` by `start_season`).
- `fixtures = generate_schedule(team_ids, season.schedule_format)`.
  - If `team_ids` has length < 2, **skip** the call (would `ValueError`);
    `fixtures = []` and `matchdays = []`. The schedule page still
    renders 200 with the empty-state notice.
- Query persisted `GameRound`s for this Season:
  `rounds_qs = GameRound.objects.filter(match__season=season)
  .select_related("match")`.
- Index by `(frozenset({game_round.match.team_red_id,
  game_round.match.team_blue_id}), game_round.round_number) →
  game_round`. Single dict.
- For each fixture, attach the following per-fixture dict:

  ```python
  {
      "matchday":     int,
      "round_number": int,
      "team_a_id":    int,
      "team_b_id":    int,
      "team_a":       Team,       # resolved via teams_by_id
      "team_b":       Team,       # resolved via teams_by_id
      "played":       bool,
      "game_round_id": int | None,
      "red_score":     int | None,    # from the played GameRound's red_points
      "blue_score":    int | None,    # from the played GameRound's blue_points
      "date":          date,           # season.start_date + (matchday - 1) * 7 days
  }
  ```

- Group by matchday: `matchdays = list[{"matchday": int, "date": date,
  "fixtures": list[per-fixture-dict]}]` in matchday-asc order.
- Render `templates/seasons/schedule.html`.

**Context keys (frozen):**

```python
{
    "season":    Season,
    "matchdays": list[dict],  # see shape above
}
```

### 4c. URL routing — `matches/season_urls.py` (NEW)

NEW file at `matches/season_urls.py`. No `app_name` — bare URL
namespace, consistent with `teams/player_urls.py` precedent:

```python
from django.urls import path

from . import views

urlpatterns = [
    path("<int:season_id>/standings/", views.season_standings, name="season_standings"),
    path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule"),
]
```

**Mount** in `laserforce_simulator/urls.py` — single line, added
**after** the existing `path("matches/", include("matches.urls"))` line:

```python
path("seasons/", include("matches.season_urls")),
```

**Resulting URLs:**
- `GET /seasons/<int:season_id>/standings/` — URL name `season_standings`
- `GET /seasons/<int:season_id>/schedule/` — URL name `season_schedule`

Reverse via the bare names (`reverse("season_standings",
args=[season.id])`) — no `app_name:` prefix.

### 4d. Templates

#### `templates/seasons/standings.html` (NEW)

Extends `base.html`. Frozen DOM ids:

| Element | Locked id |
|---|---|
| Outer `<table>` for the standings rows | `season-standings-table` |
| Empty-state notice (only when `is_draft_preview` AND `len(rows) == 0`) | `season-standings-empty` |
| "Preview — Season not started" banner (only when `is_draft_preview` truthy) | `season-draft-preview-banner` |
| State badge (renders `season.state`: `draft` / `active` / `completed`) | `season-state-badge` |

**Frozen header row order** (left to right):

`Rank | Team | MP | W | L | T | Pts | RW | TS`

Where MP=matches_played, W=wins, L=losses, T=ties, Pts=league_points,
RW=round_wins, TS=total_score.

**Per-row rendering:**
- Rank cell: `{{ row.rank }}`.
- Team cell: `<a href="{% url 'team_detail' row.team_id %}">{{
  teams_by_id|get_item:row.team_id|attr:"name" }}</a>` — or the
  pragmatic equivalent. The Code agent picks: either a custom
  template-tag (no — out of scope), or assemble a list of
  `(row, team)` tuples in the view and iterate that, or rely on the
  dataclass attribute access pattern. **Recommended:** assemble
  `rows_with_teams = [(row, teams_by_id.get(row.team_id if hasattr(row,
  "team_id") else row["team_id"])) for row in rows]` in the view and add
  that as the context key `rows_with_teams` — overriding the
  context-keys list above. Pinned tweak: add `rows_with_teams` to the
  context-keys list and iterate it in the template. Updated **frozen
  context keys** for `season_standings`:

```python
{
    "season":           Season,
    "rows":             list[StandingsRow] | list[dict],
    "rows_with_teams":  list[tuple[StandingsRow | dict, Team]],
    "is_draft_preview": bool,
}
```

`teams_by_id` is dropped — its only role was the template lookup, now
preassembled.

#### `templates/seasons/schedule.html` (NEW)

Extends `base.html`. Frozen DOM ids:

| Element | Locked id |
|---|---|
| Outer `<table>` (or wrapping container) for the schedule | `season-schedule-table` |
| Empty-state notice (when `len(matchdays) == 0`) | `season-schedule-empty` |
| Each matchday section (one per matchday) | `season-schedule-matchday-{n}` where `{n}` = matchday number |

**Per-matchday section:** header `Matchday {n} — {date|date:"Y-m-d"}`;
a sub-table or list of fixtures showing each fixture's `team_a.name`
vs `team_b.name`, `round_number`, and either the played score
(`red_score`–`blue_score` with a link to the `GameRound` detail page —
URL name TBD by Code agent against existing `matches/urls.py`; if no
such named URL exists, render as plain text "{red_score}–{blue_score}"
without a link — locked fallback) or the literal text `Unplayed`.

The Code agent has discretion on Bootstrap class names, but the DOM
ids above MUST be present verbatim.

### 4e. Admin (`matches/admin.py`)

Register `League` and `Season`. Insert the registrations AFTER any
existing registrations in `matches/admin.py` (e.g. `Match` admin).
The Code agent does NOT modify any existing registration.

```python
@admin.register(League)
class LeagueAdmin(admin.ModelAdmin):
    list_display = ("name", "mode", "state", "created_at")


@admin.register(Season)
class SeasonAdmin(admin.ModelAdmin):
    list_display = ("name", "league", "state", "schedule_format", "start_date")
    filter_horizontal = ("teams",)
```

**Pinned:**
- `LeagueAdmin.list_display` exactly the 4-tuple above, in order.
- `SeasonAdmin.list_display` exactly the 5-tuple above, in order.
- `SeasonAdmin.filter_horizontal = ("teams",)` — the M2M dual-select
  widget.
- No `list_filter`, `search_fields`, `readonly_fields`, or
  `fieldsets` pinned (Code agent may add only if needed for
  manage.py-level smoke; tests do not assert).

---

## 5. Migration

### 5a. File path + name

`matches/migrations/0029_league_season_match_fk.py`.

### 5b. Dependencies

```python
dependencies = [
    ("matches", "0028_gameround_is_simulated"),
    ("teams", "<latest teams migration at time of branch cut>"),
]
```

The Code agent inspects the highest-numbered file in
`teams/migrations/` at the time of writing and uses it as the second
dependency. Tests do not pin the teams-migration name — `pytest` will
detect drift.

### 5c. Operations (in order)

```python
operations = [
    migrations.CreateModel(
        name="League",
        fields=[
            ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=100)),
            ("mode", models.CharField(choices=[("sandbox","Sandbox"),("league","League"),("multiplayer","Multiplayer")], default="league", max_length=16)),
            ("state", models.CharField(choices=[("active","Active"),("archived","Archived")], default="active", max_length=16)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
        ],
    ),
    migrations.CreateModel(
        name="Season",
        fields=[
            ("id", models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
            ("name", models.CharField(max_length=100)),
            ("start_date", models.DateField()),
            ("state", models.CharField(choices=[("draft","Draft"),("active","Active"),("completed","Completed")], default="draft", max_length=16)),
            ("schedule_format", models.CharField(choices=[("single_round_robin","Single round-robin")], default="single_round_robin", max_length=32)),
            ("starting_team_ids_json", models.JSONField(blank=True, default=None, null=True)),
            ("created_at", models.DateTimeField(auto_now_add=True)),
            ("champion_team", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="seasons_won", to="teams.team")),
            ("league", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="seasons", to="matches.league")),
            ("teams", models.ManyToManyField(related_name="enrolled_seasons", to="teams.team")),
        ],
    ),
    migrations.AddField(
        model_name="Match",
        name="season",
        field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="matches", to="matches.season"),
    ),
]
```

**Pinned:**
- Operations in this exact order: `CreateModel(League)` →
  `CreateModel(Season)` → `AddField(Match, season)`.
- No `RunPython`, no `RunSQL`, no data backfill.
- The Code agent MAY use `python manage.py makemigrations` to generate
  the file, then rename it to `0029_league_season_match_fk.py` if the
  autogenerator picks a different name. The operations list MUST match
  the order above.

---

## 6. Test plan

**One test file per concern, 4 total in `matches/tests/`.**

### 6a. `matches/tests/test_schedule_generator.py` — pure unit

Pure-unit `TestCase` (no DB, no Django imports beyond `unittest.TestCase`
/ `django.test.SimpleTestCase`; Code agent prefers `SimpleTestCase` for
the no-DB guarantee).

**Frozen import allowlist (test file itself):**
- `subprocess`, `sys`, `unittest` / `django.test.SimpleTestCase`
- `matches.schedule_generator` (the module under test — imported by
  name only inside the `TestNoDjangoImportsLeaked` subprocess call;
  imported at file top for the other tests)
- `dataclasses` (only if needed to introspect `ScheduleFixture`)

**Classes:**

- `TestGenerateScheduleHappyPath`
  - `test_n4_returns_6_fixtures`
  - `test_n8_returns_56_fixtures`
  - `test_every_pair_appears_exactly_once_in_round_1`
  - `test_every_pair_appears_exactly_once_in_round_2`
- `TestGenerateScheduleOrder`
  - `test_round_1_matchdays_are_1_through_n_minus_1`
  - `test_round_2_matchdays_are_n_through_2n_minus_2`
  - `test_output_sorted_by_matchday_then_team_a_id`
  - `test_team_a_id_less_than_team_b_id_per_fixture`
- `TestGenerateScheduleOddN`
  - `test_n5_drops_bye_fixtures_from_output`
  - `test_n5_no_team_appears_twice_per_matchday`
  - `test_n5_total_played_fixtures_is_20`
  - `test_bye_sentinel_minus_one_never_appears_in_output`
- `TestGenerateScheduleDeterminism`
  - `test_input_order_does_not_affect_output` (compare output for
    `[5,1,3,7]` vs `[1,3,5,7]` — must be `==`)
  - `test_repeated_calls_return_identical_lists`
- `TestGenerateScheduleErrors`
  - `test_unknown_schedule_format_raises_value_error`
  - `test_empty_team_list_raises_value_error`
  - `test_single_team_raises_value_error`
- `TestScheduleFormatsConstant`
  - `test_schedule_formats_contains_single_round_robin`
- `TestNoDjangoImportsLeaked` — single test:
  - `test_pure_module_does_not_pull_in_django` — spawns
    `python -c "import sys, matches.schedule_generator;
    leaked = [m for m in sys.modules if m == 'django' or
    m.startswith('django.')]; assert not leaked, leaked"` via
    `subprocess.run(...)`. Mirror HX-03's pattern exactly. Pinned
    failure mode: any `django.*` module in `sys.modules` after the
    pure-import fails the test.

### 6b. `matches/tests/test_standings.py` — pure unit

Pure-unit. Same import discipline as `test_schedule_generator.py`.

**Classes:**

- `TestComputeStandingsEmptyInput`
  - `test_no_matches_all_enrolled_rows_zeroed`
  - `test_no_enrolled_teams_returns_empty_list`
- `TestComputeStandingsBasicWinLoss`
  - `test_one_match_red_wins_red_w_blue_l`
  - `test_one_match_blue_wins_blue_w_red_l`
  - `test_league_points_3w_1t_0l`
- `TestComputeStandingsTie`
  - `test_winner_team_id_none_counts_as_tie_both`
  - `test_unknown_winner_id_counts_as_tie_defensive`
- `TestComputeStandingsTiebreakLadder`
  - `test_tied_league_points_resolved_by_round_wins`
  - `test_tied_league_points_and_round_wins_resolved_by_total_score`
  - `test_tied_on_all_three_resolved_by_team_name_alphabetical`
- `TestComputeStandingsRankPopulated`
  - `test_rank_is_one_based_and_dense`
- `TestComputeStandingsTeamElimBonusFlowsIn`
  - `test_red_total_points_carries_team_elim_bonus_into_total_score`
- `TestNoDjangoImportsLeaked` — single test:
  - `test_pure_module_does_not_pull_in_django` (same subprocess
    pattern as 6a)

### 6c. `matches/tests/test_lg01_models.py` — Django `TestCase`

Standard DB tests. Uses `Team.objects.create(...)`, `League.objects.create(...)`,
`Season.objects.create(...)` (with `start_date` always populated).

**Classes:**

- `TestLeagueModel`
  - `test_mode_defaults_to_league`
  - `test_state_defaults_to_active`
  - `test_str_returns_name`
  - `test_active_season_property_returns_none_when_no_seasons`
  - `test_active_season_property_returns_draft_season`
  - `test_active_season_property_returns_active_season`
  - `test_active_season_property_excludes_completed`
- `TestSeasonModel`
  - `test_state_defaults_to_draft`
  - `test_schedule_format_defaults_to_single_round_robin`
  - `test_starting_team_ids_json_defaults_to_none`
  - `test_str_returns_league_name_em_dash_season_name`
- `TestSeasonCleanInvariant`
  - `test_second_non_completed_season_in_same_league_raises`
  - `test_second_non_completed_season_in_DIFFERENT_league_does_not_raise`
  - `test_ok_when_first_season_is_completed`
  - `test_clean_excludes_self_so_re_saving_active_season_does_not_raise`
- `TestSeasonStartSeason`
  - `test_flips_state_to_active`
  - `test_snapshots_starting_team_ids_sorted`
  - `test_raises_when_fewer_than_two_teams`
  - `test_does_not_modify_teams_m2m`
- `TestSeasonCompleteIfFinished`
  - `test_no_op_when_state_is_not_active`
  - `test_no_op_when_fixtures_not_all_played`
  - `test_flips_to_completed_when_all_fixtures_played`
  - `test_stamps_champion_to_row_0_of_compute_standings`
  - `test_idempotent_on_re_call`
- `TestMatchSeasonFK`
  - `test_match_season_default_is_null`
  - `test_match_season_assignable`
  - `test_deleting_season_sets_match_season_to_null_does_not_cascade_delete`
  - `test_season_matches_reverse_accessor_returns_all_matches`

### 6d. `matches/tests/test_lg01_simulator.py` — Django `TestCase`

Tests the new `BatchSimulator.simulate_scheduled_round` method.

**Classes:**

- `TestSimulateScheduledRoundGuards`
  - `test_raises_when_season_state_is_draft`
  - `test_raises_when_season_state_is_completed`
  - `test_raises_when_round_number_is_zero`
  - `test_raises_when_round_number_is_three`
  - `test_raises_when_round_2_called_without_round_1`
- `TestSimulateScheduledRoundRound1`
  - `test_creates_new_match_with_team_red_team_a_team_blue_team_b`
  - `test_persists_one_game_round_with_round_number_1`
  - `test_populates_match_red_round1_fields`
  - `test_populates_match_blue_round1_fields`
  - `test_leaves_match_is_completed_false`
- `TestSimulateScheduledRoundRound2`
  - `test_finds_existing_match_side_agnostically`
  - `test_persists_second_game_round_with_round_number_2`
  - `test_args_reversed_team_red_is_team_b_in_round_2_game_round`
  - `test_sets_match_is_completed_true`
  - `test_triggers_calculate_winner_via_save`
  - `test_populates_match_red_round2_and_blue_round2_fields`
- `TestSimulateScheduledRoundSideAgnosticLookup`
  - `test_round1_with_a_then_round2_with_b_a_finds_same_match`
  - `test_round1_with_b_then_round2_with_a_b_finds_same_match`
- `TestSimulateScheduledRoundAutoCompletion`
  - `test_simulating_last_fixture_flips_season_to_completed`
  - `test_simulating_last_fixture_stamps_champion_team`
  - `test_simulating_non_last_fixture_leaves_season_active`

The simulator tests **may** use small-N seeded simulations (e.g. N=2
or N=3 leagues to keep test runtime down). Tests do not pin exact
score values — only the schema-level outcomes (which fields got
populated, which state flipped).

### 6e. Files the Tests agent creates

| File | Status |
|---|---|
| `matches/tests/test_schedule_generator.py` | NEW |
| `matches/tests/test_standings.py` | NEW |
| `matches/tests/test_lg01_models.py` | NEW |
| `matches/tests/test_lg01_simulator.py` | NEW |

The Tests agent does NOT extend any existing test file in LG-01.

---

## 7. File ownership (who edits what)

| File | Code | Tests | Docs |
|---|:---:|:---:|:---:|
| `matches/models.py` (add `League`, `Season`, `Match.season` FK + imports) | OWN | — | — |
| `matches/migrations/0029_league_season_match_fk.py` (NEW) | OWN | — | — |
| `matches/schedule_generator.py` (NEW pure module) | OWN | — | — |
| `matches/standings.py` (NEW pure module) | OWN | — | — |
| `matches/simulation.py` (add `BatchSimulator.simulate_scheduled_round`) | OWN | — | — |
| `matches/views.py` (add `season_standings`, `season_schedule`) | OWN | — | — |
| `matches/season_urls.py` (NEW) | OWN | — | — |
| `laserforce_simulator/urls.py` (add `path("seasons/", …)`) | OWN | — | — |
| `templates/seasons/standings.html` (NEW) | OWN | — | — |
| `templates/seasons/schedule.html` (NEW) | OWN | — | — |
| `matches/admin.py` (register `League`, `Season`) | OWN | — | — |
| `matches/tests/test_schedule_generator.py` (NEW) | — | OWN | — |
| `matches/tests/test_standings.py` (NEW) | — | OWN | — |
| `matches/tests/test_lg01_models.py` (NEW) | — | OWN | — |
| `matches/tests/test_lg01_simulator.py` (NEW) | — | OWN | — |
| `PLAN.md` (mark LG-01 done + add dense impl note) | — | — | OWN |
| `laserforce_simulator/matches/CLAUDE.md` (add `## LG-01 league / season foundation` subsection) | — | — | OWN |
| `CONTEXT.md` | — | — | (already done at grill time — Docs MUST NOT touch) |
| `docs/adr/0014-*` and `docs/adr/0015-*` | — | — | (already exist — Docs MUST NOT touch) |

The Code agent does NOT touch tests; the Tests agent does NOT touch
production code; the Docs agent does NOT touch code or tests.

---

## 8. Out of scope (locked)

LG-01 explicitly does NOT touch:

- ❌ **LG-01a..g surfaces.** No `/` mode picker landing, no
  `/leagues/` list, no `/leagues/create/`, no League dashboard, no
  Season dashboard, no `POST /seasons/<id>/play-next/`, no "Start Next
  Season" chain UI, no `/leagues/<id>/history/`, no
  `/seasons/<id>/teams/<tid>/games/`. Each is its own task (LG-01a..g)
  grilled separately.
- ❌ **No change to `simulate_match`.** The existing both-Rounds-atomic
  sandbox simulator stays byte-for-byte identical. No edits to its
  signature, body, helpers, or `_flush_to_db` plumbing beyond what's
  needed to share the per-Round simulation entry point (which is
  internal to `simulation.py` already).
- ❌ **No change to any sandbox URL or view.** `/matches/create/`,
  `/matches/`, `/teams/`, `/players/`, every existing match / round
  detail page, `/leagues/` (does not exist yet), etc. — all unchanged.
- ❌ **No Score Calibration re-baseline.** No behavioural change to
  simulation mechanics — `simulate_scheduled_round` reuses the existing
  per-Round simulation verbatim. Per-Round RNG consumption is byte-for-byte
  identical to what `simulate_match` already does. No SIM-07 / SIM-08
  contract touch.
- ❌ **No CONTEXT.md edit.** The `League`, `Season`, `Standings`
  glossary entries under the new `### League and seasons` subsection
  were added at grill time and are already present.
- ❌ **No ADR write.** ADR-0014 and ADR-0015 were written at grill
  time and are already present. The Docs agent does NOT modify either.
- ❌ **No mode-picker landing page.** That's LG-01a. The current
  homepage redirect (`path("", include("teams.urls"))`) stays.
- ❌ **No `/leagues/` URL space.** That's LG-01a / LG-01c. LG-01
  ships only `/seasons/<id>/standings/` and `/seasons/<id>/schedule/`.
- ❌ **No batch-sim / Celery touch.** No edits under `matches/tasks.py`
  or any Celery surface.
- ❌ **No new model fields beyond the three listed in §1.** No
  `Season.matchday_cadence_days` (deferred), no `League.owner_user`
  (deferred to UX-01), no `Match.state` enum (rejected in ADR-0014).
- ❌ **No API / DRF endpoint** for `League` / `Season`. LG-01 ships
  HTML views only.
- ❌ **No backfill.** Pre-LG-01 Matches remain `season=NULL` forever.
- ❌ **No "Start Next Season" action** wiring (LG-01e). LG-01 ships
  the data model and the `complete_if_finished` hook only.
- ❌ **No Player / Team app touch** beyond the M2M reverse-accessor
  `Team.enrolled_seasons` and the FK reverse-accessor `Team.seasons_won`
  (both auto-generated; no code change required in `teams/`).

---

## 9. Determinism / scope notes

- **Read-only views + admin path + new simulator entry point.** The
  two new views (`season_standings`, `season_schedule`) are pure
  read-derivations; no writes, no RNG, no simulation kicked off.
- **New simulator entry point consumes no NEW RNG.** The per-Round
  simulation it delegates to consumes one `rng_seed` per Round, exactly
  as `simulate_match` already does at round-1 and round-2 time
  separately. `simulate_scheduled_round` itself adds zero RNG draws —
  it is pure orchestration.
- **Per-Match colour swap is verbatim.** Round 2's args-reversed
  invocation mirrors `simulate_match`'s body-internal colour swap
  byte-for-byte (`team_red=team_b, team_blue=team_a`). Same RNG
  consumption, same persistence schema, same `calculate_winner` trigger.
- **No Score Calibration re-baseline.** Simulation mechanics are
  untouched.
- **No `_flush_to_db` change beyond what's needed to share the
  per-Round path** — internal refactor inside `simulation.py` is OK;
  the public seam is `simulate_match` + the new `simulate_scheduled_round`.
- **Active-Season invariant is the only data-integrity rule
  enforced** at the model layer. Schedule determinism is enforced by
  `starting_team_ids_json` snapshot (frozen at activation) plus the
  pure-module's input-sort.
- **Pure modules carry zero state** — every call is a pure function
  of its inputs.

---

## 10. Locked names quick-reference

Every name a downstream agent needs.

### Models

| Slot | Name |
|---|---|
| Model class (new) | `matches.models.League` |
| Model class (new) | `matches.models.Season` |
| Existing model field added | `matches.models.Match.season` |
| `League.mode` choices | `("sandbox","Sandbox"), ("league","League"), ("multiplayer","Multiplayer")` |
| `League.mode` default | `"league"` |
| `League.state` choices | `("active","Active"), ("archived","Archived")` |
| `League.state` default | `"active"` |
| `League.seasons` related_name | `seasons` (auto from `Season.league` FK) |
| `League.active_season` | `@property` |
| `Season.state` choices | `("draft","Draft"), ("active","Active"), ("completed","Completed")` |
| `Season.state` default | `"draft"` |
| `Season.schedule_format` choices | `("single_round_robin","Single round-robin")` |
| `Season.schedule_format` default | `"single_round_robin"` |
| `Season.teams` related_name | `enrolled_seasons` |
| `Season.champion_team` related_name | `seasons_won` |
| `Season.starting_team_ids_json` default | `None` (`null=True, blank=True`) |
| `Season.clean()` | `-> None` (raises `ValidationError` on invariant break) |
| `Season.start_season()` | `-> None` (`@transaction.atomic`) |
| `Season.complete_if_finished()` | `-> None` (`@transaction.atomic`, idempotent) |
| `Match.season` | `FK(Season, null=True, blank=True, on_delete=SET_NULL, related_name="matches")` |

### Pure modules

| Slot | Name |
|---|---|
| Pure module (new) | `matches/schedule_generator.py` |
| Pure module (new) | `matches/standings.py` |
| Dataclass | `matches.schedule_generator.ScheduleFixture` |
| Dataclass fields (order) | `matchday: int, round_number: int, team_a_id: int, team_b_id: int` |
| Function | `matches.schedule_generator.generate_schedule(team_ids: list[int], schedule_format: str = "single_round_robin") -> list[ScheduleFixture]` |
| Module constant | `matches.schedule_generator.SCHEDULE_FORMATS = ("single_round_robin",)` |
| Bye sentinel (internal, not exported) | `-1` |
| Dataclass | `matches.standings.StandingsRow` |
| Dataclass fields (order) | `team_id, matches_played, wins, losses, ties, league_points, round_wins, total_score, rank` |
| Function | `matches.standings.compute_standings(completed_matches: list[dict], enrolled_teams: list[tuple[int, str]]) -> list[StandingsRow]` |
| Match dict shape (8 keys) | `match_id, team_red_id, team_blue_id, winner_team_id, red_rounds_won, blue_rounds_won, red_total_points, blue_total_points` |

### Simulator

| Slot | Name |
|---|---|
| Method | `matches.simulation.BatchSimulator.simulate_scheduled_round(self, season, team_a, team_b, round_number, *, arena_map=None) -> GameRound` |
| Decorator | `@transaction.atomic` |
| Guard error | `ValueError` (state, round_number, missing-round-1) |

### URLs + Views

| Slot | Name |
|---|---|
| URL file (new) | `matches/season_urls.py` |
| URL path mount (project) | `path("seasons/", include("matches.season_urls"))` |
| URL pattern | `path("<int:season_id>/standings/", views.season_standings, name="season_standings")` |
| URL pattern | `path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule")` |
| URL name | `season_standings` |
| URL name | `season_schedule` |
| View | `matches.views.season_standings(request, season_id: int) -> HttpResponse` |
| View | `matches.views.season_schedule(request, season_id: int) -> HttpResponse` |
| Context keys — `season_standings` | `season, rows, rows_with_teams, is_draft_preview` |
| Context keys — `season_schedule` | `season, matchdays` |

### Templates + DOM ids

| Slot | Name |
|---|---|
| Template (new) | `templates/seasons/standings.html` |
| Template (new) | `templates/seasons/schedule.html` |
| DOM id (standings table) | `season-standings-table` |
| DOM id (standings empty notice) | `season-standings-empty` |
| DOM id (draft preview banner) | `season-draft-preview-banner` |
| DOM id (state badge) | `season-state-badge` |
| DOM id (schedule table) | `season-schedule-table` |
| DOM id (schedule empty notice) | `season-schedule-empty` |
| DOM id (per-matchday section) | `season-schedule-matchday-{n}` |

### Admin

| Slot | Name |
|---|---|
| Admin class | `matches.admin.LeagueAdmin` |
| Admin class | `matches.admin.SeasonAdmin` |
| `LeagueAdmin.list_display` | `("name", "mode", "state", "created_at")` |
| `SeasonAdmin.list_display` | `("name", "league", "state", "schedule_format", "start_date")` |
| `SeasonAdmin.filter_horizontal` | `("teams",)` |

### Migration

| Slot | Name |
|---|---|
| Migration file (new) | `matches/migrations/0029_league_season_match_fk.py` |
| Dependency (matches) | `("matches", "0028_gameround_is_simulated")` |
| Operations (in order) | `CreateModel(League)` → `CreateModel(Season)` → `AddField(Match, season)` |

### Tests

| Slot | Name |
|---|---|
| Test file (new) | `matches/tests/test_schedule_generator.py` |
| Test file (new) | `matches/tests/test_standings.py` |
| Test file (new) | `matches/tests/test_lg01_models.py` |
| Test file (new) | `matches/tests/test_lg01_simulator.py` |
| Pure-unit classes (schedule) | `TestGenerateScheduleHappyPath, TestGenerateScheduleOrder, TestGenerateScheduleOddN, TestGenerateScheduleDeterminism, TestGenerateScheduleErrors, TestScheduleFormatsConstant, TestNoDjangoImportsLeaked` |
| Pure-unit classes (standings) | `TestComputeStandingsEmptyInput, TestComputeStandingsBasicWinLoss, TestComputeStandingsTie, TestComputeStandingsTiebreakLadder, TestComputeStandingsRankPopulated, TestComputeStandingsTeamElimBonusFlowsIn, TestNoDjangoImportsLeaked` |
| Django-DB classes (models) | `TestLeagueModel, TestSeasonModel, TestSeasonCleanInvariant, TestSeasonStartSeason, TestSeasonCompleteIfFinished, TestMatchSeasonFK` |
| Django-DB classes (simulator) | `TestSimulateScheduledRoundGuards, TestSimulateScheduledRoundRound1, TestSimulateScheduledRoundRound2, TestSimulateScheduledRoundSideAgnosticLookup, TestSimulateScheduledRoundAutoCompletion` |

### Imports added to `matches/models.py`

| Slot | Name |
|---|---|
| `from django.core.exceptions import ValidationError` | for `Season.clean()` |
| `from django.db import transaction` | for `@transaction.atomic` on Season methods |

### Imports added to `matches/views.py`

| Slot | Name |
|---|---|
| `from django.shortcuts import get_object_or_404` | already present likely |
| `from .schedule_generator import generate_schedule` | NEW |
| `from .standings import compute_standings` | NEW |
| `from teams.models import Team` | for `teams_by_id` materialisation |
| `from datetime import timedelta` | for matchday date arithmetic |

---

## Closing note

This contract is the locked seam for three parallel agents. Every name
appearing in the agent prompts must trace back to this document. If
reality contradicts a name here (e.g. a missing migration, a renamed
field, a different existing import), the contract is the source of
truth — STOP and flag the deviation back to the orchestrator rather
than silently drift.
