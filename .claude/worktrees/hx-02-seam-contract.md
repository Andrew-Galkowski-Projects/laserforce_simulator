# HX-02 · Role benchmarks — Seam contract

Locked design surface. Code / Tests / Docs agents work in parallel against this
file. Locked names are at the bottom in **§G Frozen names**.

Background: HX-02 adds per-(Role, Stat) **Role benchmarks** (mean / median /
p25 / p75 / p90 / n) and per-player **Percentile rank** (CONTEXT.md) over the
*population of players' career-averages-when-playing-that-Role*. Surfaced on a
new `/players/benchmarks/` page (one table per role) and as extra columns on
the existing HX-01 per-role table at `/players/<id>/stats/`. Threshold and
mean/median toggle ride on `?threshold=` and `?display=` query params. Cache is
Django cache framework, keyed by a global version int that the simulator and
two `PlayerRoundState` signals bump.

---

## A. Pure module — `teams/role_benchmarks.py`

### Import allowlist (frozen — no Django / ORM / RNG / I/O)

```python
import bisect
import statistics
from collections import defaultdict
from typing import Iterable, Mapping
```

Nothing else. No `django.*`, no `matches.*` ORM models, no `random`, no file
I/O. The "no Django imports leaked" defensive invariant (see test file map) is
enforced — mirrors the HX-01 / RES-04 / RV-03 precedent.

### Module-level constants

```python
# The 12 frozen stats, in the order they render in tables.
STAT_KEYS: tuple[str, ...] = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)

# Aggregated sum-of-numerator / sum-of-denominator across the player's
# role-rounds (the Tag-ratio precedent, CONTEXT.md). The round-dict's
# pre-computed "accuracy_pct" key is the per-round value; the reducer
# weights it by per-round (tags_made + shots_missed) when summing.
RATIO_STATS: frozenset[str] = frozenset({"accuracy"})

# Stats whose per-round value comes from the view's pre-computed key
# (the view called calculate_mvp(player_round_state) once per round
# and stored the float in round-dict["mvp"]). Listed here only so the
# reducer can assert presence and the test fixture matches reality;
# from the reducer's POV `mvp` behaves like any other counter stat
# (per-round mean aggregation, NOT sum/sum).
MVP_DERIVED_STATS: frozenset[str] = frozenset({"mvp"})

# Locked role render order (matches HX-01 _ROLE_ORDER).
ROLES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")
```

### Public function signatures

#### `build_role_populations`

```python
def build_role_populations(
    rows: Iterable[Mapping],
) -> dict[tuple[str, str], list[tuple[int, float]]]:
    """
    Reduce raw round-dicts (one per PlayerRoundState row) into the per-
    (role, stat) population of players' career-averages-when-playing-that-role.

    Each input row is the SHARED round-dict (see §B). The function groups by
    (player_id, role) -> list of role-rounds for that player+role, then per
    (role, stat) emits ONE (player_id, career_avg) tuple per qualifying
    player.

    Aggregation rule per the locked decisions:
      - stat in RATIO_STATS (currently {"accuracy"}): sum/sum reducer.
        accuracy = sum(tags_made) / max(sum(tags_made + shots_missed), 1) * 100
        (the round-dict's pre-computed "accuracy_pct" key is NOT averaged
        directly; the reducer rebuilds the sum from raw tags_made +
        shots_missed so the result is per-tag-weighted, matching the
        Tag-ratio precedent).
      - stat NOT in RATIO_STATS: per-round mean of round-dict[stat]
        (sum / len(role_rounds_for_player)).
      - "mvp" is in MVP_DERIVED_STATS and treated as a counter: the per-
        round mvp float (computed view-side via calculate_mvp once per
        round-dict) is averaged across the player's role-rounds.

    Output keys are (role, stat); every key in
    {(role, stat) for role in ROLES for stat in STAT_KEYS} is present, even
    when the value is []. The (player_id, career_avg) tuples are NOT pre-
    sorted (caller sorts when needed).

    Pure: consumes no RNG, performs no I/O. Time: single pass over rows,
    O(rows + roles * stats * players).
    """
```

#### `apply_threshold`

```python
def apply_threshold(
    samples: list[tuple[int, float]],
    population_min_rounds: dict[int, int],
    min_rounds: int,
) -> list[tuple[int, float]]:
    """
    Filter `samples` to only those (player_id, value) entries where the
    player has at least `min_rounds` rounds in the relevant role.

    `population_min_rounds` is keyed by player_id and maps to that player's
    round-count IN THE ROLE the population represents. The caller (the
    cache helper / orchestrator) builds one dict per role:

        population_min_rounds_by_role[role] = {player_id: round_count_in_role}

    and hands the slice for the population's role to this function. A
    player absent from the dict is treated as 0 rounds (filtered out for
    any min_rounds >= 1).

    `min_rounds` is the user-configurable threshold from ?threshold=
    (default 5).
    """
```

#### `summarize_population`

```python
def summarize_population(samples: list[tuple[int, float]]) -> dict:
    """
    Return distribution stats for a single (role, stat) population AFTER
    apply_threshold has filtered it.

    Returns a 6-key dict with float|None values:
        {
            "mean":   float | None,
            "median": float | None,
            "p25":    float | None,
            "p75":    float | None,
            "p90":    float | None,
            "n":      int,
        }

    Empty input (n == 0) returns every metric as None and n=0; the view
    renders these cells as "—" with explicit "n = 0".

    Percentile formula (consistent with `percentile_for`'s nearest-rank
    convention): for percentile p in {25, 75, 90} with n samples sorted
    ascending, return sorted[idx] where
        idx = min(n - 1, max(0, ceil(p / 100 * n) - 1))
    (nearest-rank; subject is part of own population). Mean uses
    statistics.fmean; median uses statistics.median (lower-of-two for
    even n is fine — locked).
    """
```

#### `percentile_for`

```python
def percentile_for(value: float, sorted_values: list[float]) -> int:
    """
    Pure nearest-rank percentile, integer 0-100. `sorted_values` MUST be
    ascending-sorted (caller responsibility); function does not re-sort.

    Algorithm (locked):
        n = len(sorted_values)
        if n == 0: return 0   # caller should not call this on empty
                              # populations; defensive only.
        # rank = number of samples strictly less than value, plus one
        # tie offset so the subject's own value contributes to its rank.
        rank = bisect.bisect_left(sorted_values, value) + 1
        rank = min(rank, n)   # clamp (value >= max -> n -> 100)
        pct = (rank * 100) // n
        return max(0, min(100, pct))

    Ties: when `value` equals one or more entries, `bisect_left` returns
    the leftmost insertion index — the SUBJECT'S OWN VALUE COUNTS because
    the caller passes sorted_values that INCLUDES the subject (the cache
    population is the universe; the subject is in it). At the population
    maximum the result is 100; at the strict minimum it is at most
    floor(100/n).
    """
```

#### `compute_role_benchmarks`

```python
def compute_role_benchmarks(
    samples_by_key: dict[tuple[str, str], list[tuple[int, float]]],
    population_min_rounds_by_role: dict[str, dict[int, int]],
    min_rounds: int,
) -> dict[tuple[str, str], dict]:
    """
    Orchestrator the role_benchmarks view calls after pulling cached
    populations. For every (role, stat) in
    {(r, s) for r in ROLES for s in STAT_KEYS}:

      1. samples = samples_by_key[(role, stat)]  (may be empty list)
      2. role_min_rounds = population_min_rounds_by_role.get(role, {})
      3. filtered = apply_threshold(samples, role_min_rounds, min_rounds)
      4. result[(role, stat)] = summarize_population(filtered)

    Output dict has exactly len(ROLES) * len(STAT_KEYS) == 60 entries; the
    view shapes them into the per-role table rows.

    `samples_by_key` keys MUST cover the full cartesian product (the cache
    fill-on-miss helper guarantees this with empty-list defaults); a
    missing key raises KeyError (defensive — pinned by a test).
    """
```

#### `player_position`

```python
def player_position(
    samples: list[tuple[int, float]],
    subject_player_id: int,
    subject_value: float,
    min_rounds_qualified: bool,
) -> dict:
    """
    Return the per-player overlay for one (role, stat) cell on the HX-01
    player career stats page.

    Returns:
        {
            "benchmark_mean":   float | None,
            "benchmark_median": float | None,
            "delta_mean":       float | None,
            "delta_median":     float | None,
            "percentile":       int | None,
            "qualified":        bool,
            "n":                int,   # ALWAYS the population size
                                       # (not None even when unqualified)
        }

    Subject inclusion policy (locked — was the real ambiguity):
      - PERCENTILE uses the full `samples` list INCLUDING the subject's
        own data point (the bisect / nearest-rank convention; CONTEXT.md
        Percentile rank explicitly notes "the subject's own value counts",
        so the player at the maximum is at 100).
      - MEAN AND MEDIAN are computed OVER THE FULL POPULATION ALSO
        INCLUDING the subject — this matches the standalone benchmarks
        page exactly, so the same cell never reports two different
        numbers depending on which page renders it. "Compared against
        themselves" is the same n that drives the page.

    Behaviour:
      - n == 0:
          every metric field = None, qualified = False, n = 0.
      - n > 0 and min_rounds_qualified is False:
          benchmark_mean / benchmark_median are filled from the
          population (so the unqualified player still sees the
          population's mean/median for context, matching the
          /players/benchmarks/ page); delta_mean / delta_median /
          percentile are None; qualified = False. The view renders
          delta / percentile cells as "— (need N+ rounds)".
      - n > 0 and min_rounds_qualified is True:
          all fields populated. delta_mean = subject_value -
          benchmark_mean; delta_median = subject_value -
          benchmark_median; percentile = percentile_for(subject_value,
          sorted([v for (_pid, v) in samples])).

    Pure: no RNG, no I/O. Sorting the samples is done here (the cache
    layer hands raw samples to the per-player overlay; the standalone
    benchmarks page never calls this function — it goes through
    compute_role_benchmarks instead).
    """
```

### Defensive test invariant (pinned by tests)

`teams/tests/test_role_benchmarks.py` includes a "no Django imports leaked"
check that imports `teams.role_benchmarks`, walks `sys.modules` for any
submodule loaded as a transitive dependency, and asserts none start with
`django.` or `matches.models`. Mirrors the HX-01 / RES-04 / RV-03 precedent.

---

## B. Round-dict shape (view ↔ pure-module seam)

A strict superset of the HX-01 10-key dict. The HX-01 module ignores the new
keys (HX-01 tests stay green by construction — no signature change in
`teams/career_stats.py`). The HX-02 reducer reads them.

**Locked 18-key shape** (HX-01's 10 + 6 new counters + 2 pre-computed,
grouped for readability — the canonical order is the source-code listing
below; alphabetisation is not contractual):

```python
{
    # ---- HX-01's 10 existing keys (UNCHANGED) ----
    "role":              str,                # one of ROLES
    "points_scored":     int,
    "tags_made":         int,
    "times_tagged":      int,
    "shots_missed":      int,
    "final_special":     int,
    "specials_used":     int,
    "was_eliminated_at": int,                # ticks (1801 = survived)
    "date_played":       "datetime.date",
    "game_round_id":     int,
    # ---- HX-02 additive: 6 raw counter keys ----
    "final_lives":          int,
    "resupplies_given":     int,
    "missiles_landed":      int,
    "follow_up_shots":      int,
    "reaction_shots":       int,
    "combo_resupply_count": int,
    # ---- HX-02 additive: 2 pre-computed view-side floats ----
    "mvp":          float,   # view: calculate_mvp(player_round_state)
                             #   from matches.sim_helpers.score_calculator
    "accuracy_pct": float,   # view: float(player_round_state.get_accuracy)
                             #   (0..100). NOTE: accuracy population reducer
                             #   uses sum-of-tags_made / sum-of-(tags_made +
                             #   shots_missed) directly off the raw columns;
                             #   this key is present for symmetry with the
                             #   HX-01 page and for any future per-round
                             #   chart, but the role_benchmarks reducer
                             #   does NOT average it.
}
```

**HX-01 surface impact:** none. `summarize`, `summarize_by_role`, and
`points_trend` signatures and bodies are unchanged. They read only the 10
existing keys. The HX-01 view (`teams/views.py::player_career_stats`) grows
its round-dict literal from 10 keys to 18 (additive; existing keys
unchanged) — pinned by an HX-01 view test that asserts the existing 6 keys
still round-trip identically.

---

## C. View layer changes

### New view `teams/views.py::role_benchmarks(request)`

```python
def role_benchmarks(request):
    """
    /players/benchmarks/  (URL name: role_benchmarks).

    Read-only. Query params:
      - ?threshold=<int>  default 5; passed to apply_threshold.
      - ?display=mean|median  default "mean"; flips which metric the
        per-stat row foregrounds (the table always shows both columns;
        display only changes the highlighted column / default sort).

    Behaviour:
      1. Read min_rounds = int(request.GET.get("threshold", 5)); fall
         back to 5 on ValueError. Clamp to >= 0.
      2. Read display = request.GET.get("display", "mean"); fall back
         to "mean" if not in {"mean", "median"}.
      3. samples_by_key, min_rounds_by_role = get_all_benchmark_data()
         (the cache helper, see below — single cache lookup, fills on
         miss).
      4. summary = compute_role_benchmarks(samples_by_key,
         min_rounds_by_role, min_rounds).
      5. Shape into the 5-role render structure (see context below).
    """
```

**Locked context keys** (exactly these five):

```python
{
    "min_rounds": int,        # the effective threshold (post-clamp)
    "display":    str,        # "mean" | "median"
    "roles":      tuple,      # ROLES (passed through for template loop)
    "benchmarks": dict,       # {role: [{"stat": str,
                              #          "mean": float|None,
                              #          "median": float|None,
                              #          "p25": float|None,
                              #          "p75": float|None,
                              #          "p90": float|None,
                              #          "n": int}, ...]}
                              # Inner list ordered by STAT_KEYS.
    "stat_keys":  tuple,      # STAT_KEYS (drives the row order in the
                              #   template loop)
}
```

**Template-only convenience extras** (NOT part of the locked context
contract — documented here so future readers don't flag them as
accidental). The view also ships:

- `benchmarks_by_role: list[tuple[str, list[dict]]]` — the same
  `benchmarks` dict flattened as a list of `(role, rows)` 2-tuples.
  Required because Django templates can't iterate a dict with the role
  as a variable key.
- `any_data: bool` — `True` iff at least one `(role, stat)` summary
  has `n > 0`. Drives the empty-state notice
  (`benchmark-no-data-notice`).

Tests assert the locked five keys via `assertIn`, not strict-set
equality, so the extras are tolerated. The dict + tuple + list shapes
above are the public contract; the two convenience keys may be
renamed/dropped if a future refactor moves the template to JS-driven
rendering.

### Extended view `teams/views.py::player_career_stats`

Same `get_object_or_404(Player, pk=player_id)` and same single
`PlayerRoundState.objects.filter(...).select_related("game_round").order_by(...)`
query. Builds the 18-key round-dict (was 10-key — see §B). Adds these
context keys ON TOP of the existing six (`player`, `total_rounds`, `career`,
`per_role`, `trend`, `has_rounds`):

```python
{
    "min_rounds": int,    # ?threshold=  default 5 (same parsing as
                          #   role_benchmarks view)
    "display":    str,    # ?display=    default "mean"
    "stat_keys":  tuple,  # The 5 stats the HX-01 per-role row already
                          #   shows: ("avg_points", "tag_ratio",
                          #   "avg_survival_ticks", "avg_accuracy_pct",
                          #   "avg_sp_earned"). Locked here so the
                          #   template loop has a single source of truth
                          #   for the extra benchmark columns.
    "per_role_with_benchmarks": list[dict],
        # Same len/order as the existing `per_role` (Commander, Heavy,
        # Scout, Medic, Ammo, omitting unplayed roles). Each entry is
        # the existing per_role dict UNION:
        #   {
        #     "benchmarks_by_stat": {
        #       <stat_key>: {
        #         "benchmark_mean":   float|None,
        #         "benchmark_median": float|None,
        #         "delta_mean":       float|None,
        #         "delta_median":     float|None,
        #         "percentile":       int|None,
        #         "qualified":        bool,
        #         "n":                int,
        #       },
        #       ...
        #     }
        #   }
        # The 5 stat_keys are HX-01's display stats but they MAP to the
        # underlying 12 STAT_KEYS as follows for the benchmark lookup
        # (locked):
        #     "avg_points"          -> "points_scored"
        #     "tag_ratio"           -> derived from "tags_made" and
        #                              "times_tagged" populations:
        #                              uses the "tags_made" population
        #                              key for the benchmark lookup
        #                              (NOTE: tag_ratio's benchmark is
        #                              the population's mean tags_made/
        #                              times_tagged sum-of-sum; this
        #                              is intentionally a CARRIED-OVER
        #                              HX-01 number, not a separate
        #                              STAT_KEY entry — handled by
        #                              the view assembling a synthetic
        #                              {"tags_made", "times_tagged"} pair
        #                              and computing the ratio at view
        #                              time). For HX-02 v1 we DROP the
        #                              tag_ratio benchmark column from
        #                              the HX-01 page extension and
        #                              instead show benchmarks only for
        #                              avg_points / avg_accuracy_pct /
        #                              avg_sp_earned plus the two new
        #                              counter-based ones (avg_survival
        #                              and the existing tag_ratio cell
        #                              keeps its HX-01 display, no
        #                              benchmark column). The remaining
        #                              counter stats from STAT_KEYS
        #                              surface only on the standalone
        #                              /players/benchmarks/ page.
        #     "avg_accuracy_pct"    -> "accuracy"
        #     "avg_sp_earned"       -> no STAT_KEY (HX-01-specific
        #                              formula); falls back to None;
        #                              benchmark cell renders "—".
        #     "avg_survival_ticks"  -> no STAT_KEY; same as above.
        # In short: HX-01 page benchmark columns are populated for
        # "avg_points" (-> points_scored) and "avg_accuracy_pct" (->
        # accuracy) only; the other three HX-01 stats render the
        # benchmark cells as "—" with class `benchmark-na`. This keeps
        # HX-02 a strict overlay of HX-01 — the 5 HX-01 cells are
        # unchanged; only 2 of the 5 sprout benchmark columns. The
        # standalone /players/benchmarks/ page is where the FULL 12
        # STAT_KEYS surface.
}
```

### Cache helper module — `teams/role_benchmarks_cache.py`

**Decision: separate module** (not folded into `views.py`). Rationale:
keeps the signal handler and the simulator hook with a tiny shared import
surface, mirrors the precedent of `matches.sim_helpers.*` helpers, and
makes the cache surface testable in isolation.

```python
"""HX-02 role-benchmark cache layer.

Lives between teams/views.py and teams/role_benchmarks.py. Owns the cache
key format, the fill-on-miss scan, and the version-key invalidation. Pure
Django (no third-party libs); imports from teams.role_benchmarks for the
pure reducer.
"""

from django.core.cache import cache

# Public surface — frozen names:

_VERSION_KEY: str = "role_benchmark_version"   # cache.incr() target

def _key_for(version: int, role: str, stat: str) -> str:
    return f"role_benchmark:v{version}:{role}:{stat}"

def get_all_benchmark_data() -> tuple[
    dict[tuple[str, str], list[tuple[int, float]]],   # samples_by_key
    dict[str, dict[int, int]],                        # min_rounds_by_role
]:
    """Single entry the role_benchmarks view calls.

    Reads _VERSION_KEY (default 0; ensure key exists via cache.add).
    Probes the cache for EVERY (role, stat) key plus the two
    rounds-per-role companion keys:
        f"role_benchmark:v{version}:_rounds_in_role:{role}"
    On ANY miss, runs the single full-table scan _populate_all_caches
    (see below) which fills every (role, stat) key + every
    _rounds_in_role key under the SAME version. Returns the materialised
    dicts.
    """

def get_role_benchmark_samples(role: str, stat: str) -> list[tuple[int, float]]:
    """Convenience for the HX-01 per-player overlay — wraps
    get_all_benchmark_data and slices out one (role, stat).
    """

def invalidate_role_benchmarks() -> None:
    """The universal invalidation. Calls cache.incr(_VERSION_KEY).
    Safe to call multiple times; safe under concurrent simulator runs
    (cache.incr is atomic for LocMemCache and Memcached/Redis backends).
    Lazily initialises the key with cache.add(_VERSION_KEY, 0) on the
    first call if it does not exist.
    """

def _populate_all_caches(version: int) -> None:
    """Internal — runs ONE PlayerRoundState.objects.values(
        'player_id', 'role', 'points_scored', 'tags_made',
        'times_tagged', 'shots_missed', 'final_special',
        'specials_used', 'was_eliminated_at', 'final_lives',
        'resupplies_given', 'missiles_landed', 'follow_up_shots',
        'reaction_shots', 'combo_resupply_count',
        # mvp inputs (calculate_mvp needs the parent game_round +
        #   specific_tags etc); for the cache scan we re-pull these
        #   per-row via a second values() including the full set of
        #   columns calculate_mvp needs, OR we accept a `mvp=None`
        #   row when select_related cost is unacceptable. The
        #   implementation chooses the FULL pull and computes mvp in
        #   Python here so the pure module never sees the ORM:
        'game_round_id', 'team_color', 'final_medic_hits',
        'enemy_nuke_cancels', 'ally_nuke_cancels', 'times_missiled',
        'own_specials_cancelled', 'specific_tags',
        'game_round__blue_team_eliminated',
        'game_round__red_team_eliminated', 'game_round__eliminated_at',
    ) scan, builds round-dicts in-Python, calls
    role_benchmarks.build_role_populations, then cache.set's every
    (role, stat) key + every rounds-in-role key under `version`.

    NOTE: mvp computation in the scan path uses calculate_mvp ON A
    DUCK-TYPED IN-MEMORY ROW (not a PlayerRoundState ORM instance) to
    keep the scan one query. A tiny adapter dataclass at module scope
    wraps the values()-row + parent game-round fields so calculate_mvp
    can read the attributes it duck-types on.
    """
```

### Signal wiring — `teams/signals.py` + `teams/apps.py`

**New module `teams/signals.py`:**

```python
from django.db.models.signals import post_save, post_delete
from django.dispatch import receiver

from matches.models import PlayerRoundState
from teams.role_benchmarks_cache import invalidate_role_benchmarks

@receiver(post_save, sender=PlayerRoundState)
@receiver(post_delete, sender=PlayerRoundState)
def _bump_role_benchmark_version(sender, instance, **kwargs) -> None:
    invalidate_role_benchmarks()
```

**`teams/apps.py::TeamsConfig.ready`** is extended with a single line:

```python
def ready(self) -> None:
    from teams import signals  # noqa: F401  -- register handlers
```

(If `apps.py` does not exist today, it is created; the
`default_app_config` migration for older Django is unnecessary on 5.2.)

### Simulator hook — `BatchSimulator._flush_to_db`

`matches/simulation.py`, INSIDE `_flush_to_db` (the same method that
already writes `cell_occupancy_json` then `highlights_json` and returns
`game_round`). Locked location:

> **Immediately before `return game_round`** at the bottom of the method
> (after the `game_round.save(update_fields=["highlights_json"])` line —
> line ~3061 today). One line:

```python
        # HX-02: bump role-benchmark cache version after persisting
        # PlayerRoundState rows. bulk_create skips post_save signals, so
        # the signal-driven invalidation in teams/signals.py would miss
        # these inserts without this explicit bump.
        from teams.role_benchmarks_cache import invalidate_role_benchmarks
        invalidate_role_benchmarks()

        return game_round
```

Placement rationale: must run on EVERY save path that `_flush_to_db`
serves (full match via `simulate_match`, single round via
`simulate_single_round_detailed`, batch save via `save_games`). Gated on
nothing — the bump is cheap (one cache op) and always-safe; if the round
ends up rolled back by the surrounding `@transaction.atomic`, the cache
version is now ahead of reality but the next view request will simply
re-scan and rebuild — invalidation is monotonic, never wrong.

---

## D. URL config

In `teams/player_urls.py` (the file already exists from HX-01; we add ONE
entry — `app_name` stays absent per HX-01's documented decision):

```python
urlpatterns = [
    path(
        "<int:player_id>/stats/",
        views.player_career_stats,
        name="player_career_stats",
    ),
    # HX-02:
    path(
        "benchmarks/",
        views.role_benchmarks,
        name="role_benchmarks",
    ),
]
```

Order: the static `"benchmarks/"` path is placed BEFORE
`"<int:player_id>/stats/"` in source — `<int:player_id>` rejects
`"benchmarks"` natively so order is not strictly required, but listing
the static route first matches Django convention and removes ambiguity
for future maintainers.

**Resulting URL:** `GET /players/benchmarks/`
**URL name (no namespace):** `role_benchmarks`
Reverse: `{% url 'role_benchmarks' %}` (bare; no `app_name:` prefix —
identical convention to HX-01's `'player_career_stats'`).

---

## E. Template surface (names + DOM IDs only — no markup)

### New template `templates/teams/role_benchmarks.html`

Extends `base.html`. Renders one `<section>` per role (`{% for role in
roles %}`); inside each section, a `<table>` whose rows are
`{% for stat_row in benchmarks[role] %}` (the template adapts the dict
access via the standard team_extras filter or via Python-shaped context).
Locked DOM IDs (the Tests agent will assert against these by substring
match):

| DOM ID                                       | Element       | Notes |
|----------------------------------------------|---------------|-------|
| `benchmark-threshold-input`                  | `<input>`     | The `?threshold=` form input. |
| `benchmark-display-toggle`                   | `<select>` or `<fieldset>` | The `?display=mean\|median` control. |
| `benchmark-filter-form`                      | `<form>`      | The wrapper form that submits both query params. |
| `benchmark-table-{role}` (5 IDs)             | `<table>`     | One per role: `benchmark-table-commander`, `benchmark-table-heavy`, `benchmark-table-scout`, `benchmark-table-medic`, `benchmark-table-ammo`. |
| `benchmark-row-{role}-{stat}` (60 IDs)       | `<tr>`        | One per (role, stat) cell; substring assertions via `id^=` are also acceptable. |
| `benchmark-no-data-notice`                   | `<p>`         | Rendered ONLY when every population is empty (defensive — substring "no benchmark data yet"). |

**No `json_script` block** ships from this view (the page is pure HTML —
no Chart.js, no client-side compute; the threshold/display form GET-
submits and re-renders). If a future iteration adds an inline distribution
chart, the `json_script` id is reserved as `benchmark-distribution-data`
(noted for future use only — NOT shipped in v1).

### Extended template `templates/teams/player_career_stats.html`

The existing `career-per-role-table` (HX-01 DOM ID, unchanged) gains
additional `<th>` headers and `<td>` cells per row. Locked extra-cell DOM
ID convention:

| DOM ID pattern                                                | Cell                  |
|---------------------------------------------------------------|-----------------------|
| `benchmark-{role}-{stat_key}-mean`                            | benchmark mean value  |
| `benchmark-{role}-{stat_key}-median`                          | benchmark median value|
| `benchmark-{role}-{stat_key}-delta`                           | delta vs benchmark    |
| `benchmark-{role}-{stat_key}-percentile`                      | percentile rank       |
| `benchmark-{role}-{stat_key}-n`                               | population size cell  |

Where `{stat_key}` is the HX-01 display key (`avg_points`,
`avg_accuracy_pct`, etc.) — NOT the underlying STAT_KEYS entry. As noted
in §C, only `avg_points` and `avg_accuracy_pct` cells receive populated
benchmark columns in v1; the other three HX-01 display stats render
`<td class="benchmark-na">—</td>` with the same ID pattern so the DOM
shape is consistent (tests for the unpopulated cells assert on
`class="benchmark-na"`).

Unqualified-player cells (subject below threshold) render with substring
`need ` and a number (e.g. `— (need 5+ rounds)`) — pinned by substring
match per HX-01 precedent.

A new threshold/display form (DOM IDs `benchmark-threshold-input` and
`benchmark-display-toggle` — SAME names as the standalone page; the IDs
are scoped per-page so duplicate uses across the two pages do not
collide) is rendered above the per-role table.

### Entry-point link to `/players/benchmarks/`

**Decision: ONE entry point in `templates/teams/player_career_stats.html`
header** — a button-styled anchor with the locked DOM ID
`role-benchmarks-link` (HX-01-page header). Rationale: the page is a
deep-analytics surface that lives in the same `/players/...` tree as
`/players/<id>/stats/`; users land on the career page first and the
benchmarks page is the natural "compare to the league" follow-up. No
nav-bar entry in `base.html` (consistent with HX-01, which also did not
add a global nav entry — the homepage `/` is the team list and the
`/players/...` tree is reached via team → player drill-down).

Tests pin via substring `"Role benchmarks"` in the rendered career-stats
response body.

---

## F. Test file map (file names only — no test bodies)

| File                                          | Concern                                                  |
|-----------------------------------------------|----------------------------------------------------------|
| `teams/tests/test_role_benchmarks.py`         | Pure-unit. Covers all 6 functions + module constants + the "no Django imports leaked" defensive check. NO DB. |
| `teams/tests/test_role_benchmarks_view.py`    | Django `TestCase`. `/players/benchmarks/` GET 200 + locked context keys, `?threshold=` + `?display=` parsing (including malformed falling back to defaults), empty-population rendering (`benchmark-no-data-notice`); plus extended `/players/<id>/stats/` GET 200 with the new `min_rounds` / `display` / `stat_keys` / `per_role_with_benchmarks` context keys, the `"need 5+ rounds"` substring on an unqualified player, the `role-benchmarks-link` substring, and pinning that HX-01's existing six context keys remain present (regression for the round-dict superset change). |
| `teams/tests/test_role_benchmarks_cache.py`   | Django `TestCase`. Pins cache invalidation paths: `post_save` signal bumps the version, `post_delete` signal bumps it, `BatchSimulator._flush_to_db` bumps it (creating a round end-to-end), `cache.incr` of the version key invalidates a stale read, fill-on-miss happens exactly once per version, the version key is lazily initialised. |

**Test isolation pattern (documented once):** every test class in the
three files that exercises the cache wraps with
`@override_settings(CACHES={"default": {"BACKEND":
"django.core.cache.backends.locmem.LocMemCache", "LOCATION":
"hx02-test-<unique>"}})` (the `LOCATION` per class so different classes
don't share state). The HX-01 view test is updated to use the same
override so the post_save signal-bump in HX-01's test factory doesn't
leak between tests.

---

## G. Frozen names (quick reference)

**URL paths**
- `GET /players/benchmarks/` (resulting full path under the project
  `path("players/", include("teams.player_urls"))`).
- `GET /players/<int:player_id>/stats/` (unchanged from HX-01).

**URL names** (no namespace; `app_name` absent in `player_urls.py`)
- `role_benchmarks`
- `player_career_stats` (unchanged)

**View function names** (in `teams/views.py`)
- `role_benchmarks(request)` (new)
- `player_career_stats(request, player_id)` (extended)

**Pure module path + functions** — `teams/role_benchmarks.py`
- Constants: `STAT_KEYS`, `RATIO_STATS`, `MVP_DERIVED_STATS`, `ROLES`
- Functions: `build_role_populations`, `apply_threshold`,
  `summarize_population`, `percentile_for`, `compute_role_benchmarks`,
  `player_position`

**Cache helper module path + functions** — `teams/role_benchmarks_cache.py`
- Module-private: `_VERSION_KEY = "role_benchmark_version"`,
  `_key_for(version, role, stat)`, `_populate_all_caches(version)`
- Public: `get_all_benchmark_data()`, `get_role_benchmark_samples(role,
  stat)`, `invalidate_role_benchmarks()`

**Cache key formats**
- `f"role_benchmark:v{version}:{role}:{stat}"` (per (role, stat) sample
  list)
- `f"role_benchmark:v{version}:_rounds_in_role:{role}"` (per-role
  rounds-played-by-player map, drives `apply_threshold`)
- `"role_benchmark_version"` (global int version, target of `cache.incr`)

**Signal handler**
- `teams/signals.py::_bump_role_benchmark_version` (decorated with
  `@receiver(post_save, sender=PlayerRoundState)` AND `@receiver(
  post_delete, sender=PlayerRoundState)`)
- Registered via `teams/apps.py::TeamsConfig.ready` importing
  `teams.signals`

**Simulator hook function name**
- `teams.role_benchmarks_cache.invalidate_role_benchmarks` — called once
  inside `BatchSimulator._flush_to_db` (`matches/simulation.py`)
  immediately before the final `return game_round`.

**Template paths**
- `templates/teams/role_benchmarks.html` (new)
- `templates/teams/player_career_stats.html` (extended)

**DOM IDs**
- Shared form controls: `benchmark-threshold-input`,
  `benchmark-display-toggle`, `benchmark-filter-form`
- Standalone page tables: `benchmark-table-commander`,
  `benchmark-table-heavy`, `benchmark-table-scout`,
  `benchmark-table-medic`, `benchmark-table-ammo`
- Standalone page rows: `benchmark-row-{role}-{stat}`
- Standalone page empty-state: `benchmark-no-data-notice`
- HX-01 page extra cells: `benchmark-{role}-{stat_key}-mean`,
  `benchmark-{role}-{stat_key}-median`,
  `benchmark-{role}-{stat_key}-delta`,
  `benchmark-{role}-{stat_key}-percentile`,
  `benchmark-{role}-{stat_key}-n`
- HX-01 page entry point: `role-benchmarks-link`
- HX-01 page unpopulated benchmark cells: `class="benchmark-na"`

**Test file paths**
- `teams/tests/test_role_benchmarks.py` (pure-unit)
- `teams/tests/test_role_benchmarks_view.py` (view layer)
- `teams/tests/test_role_benchmarks_cache.py` (cache + signal +
  simulator hook)

**Locked context keys**
- `role_benchmarks` view: `min_rounds, display, roles, benchmarks,
  stat_keys`
- `player_career_stats` view (additive over HX-01's existing six):
  `min_rounds, display, stat_keys, per_role_with_benchmarks`

**Locked query params (both views)**
- `?threshold=<int>` (default `5`; clamp to `>= 0`; non-int falls back
  to default)
- `?display=mean|median` (default `"mean"`; other values fall back to
  default)
