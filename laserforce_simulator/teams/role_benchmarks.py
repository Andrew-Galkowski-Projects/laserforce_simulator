"""HX-02 — Role benchmarks (pure Python).

Computes per-(Role, Stat) global benchmarks (mean / median / p25 / p75 / p90
/ n) over the population of players' **career-averages-when-playing-that-Role**.
Also exposes a per-player position helper that, given a subject's value and
the populated samples, returns mean / median / deltas / percentile rank.

This module is **pure**: no Django imports, no ORM, no RNG, no I/O. The
defensive invariant ("no `django.*` or `matches.models` loaded transitively")
is enforced by the tests; respect the import allowlist below.
"""

import bisect
import statistics
from collections import defaultdict
from typing import Iterable, Mapping

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

# RATIO_STATS are aggregated sum/sum across a player's rounds in a role
# (the HX-01 Tag-ratio precedent): each (role, stat) sample for a player is
# `sum(tags_made) / max(sum(tags_made + shots_missed), 1) * 100`.
RATIO_STATS: frozenset[str] = frozenset({"accuracy"})

# MVP_DERIVED_STATS is informational — these stats are pre-computed view-side
# (via `calculate_mvp`) on each round-dict before the reducer sees them. The
# reducer treats them like any per-round mean counter; the frozenset exists so
# tests can pin "mvp is special" without spreading the constant out.
MVP_DERIVED_STATS: frozenset[str] = frozenset({"mvp"})

ROLES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")


def compute_career_stat_for_role(rounds: list[Mapping], stat: str) -> float:
    """Single source of truth for the (Role, Stat) aggregation rule.

    - Ratio stats (just ``accuracy``) aggregate sum/sum:
      ``sum(tags_made) / max(sum(tags_made + shots_missed), 1) * 100``.
    - Every other stat (including ``mvp``) aggregates as per-round mean.

    Used by both ``build_role_populations`` (to emit population samples) and
    the view layer (to compute a subject player's value for the same role).
    Keeping the two callers on one helper means a future formula change has
    a single edit site.
    """
    if not rounds:
        return 0.0
    if stat in RATIO_STATS:
        tags = sum(rd["tags_made"] for rd in rounds)
        misses = sum(rd["shots_missed"] for rd in rounds)
        denom = max(tags + misses, 1)
        return tags / denom * 100.0
    return sum(float(rd[stat]) for rd in rounds) / len(rounds)


def build_role_populations(
    rows: Iterable[Mapping],
) -> dict[tuple[str, str], list[tuple[int, float]]]:
    """Reduce round-dicts into per-(role, stat) population samples.

    Single-pass: groups by ``(player_id, role)``, then per ``(role, stat)``
    emits **one** ``(player_id, career_avg)`` tuple per player. Aggregation
    rules per stat:

    - Stats in ``RATIO_STATS`` (just ``accuracy``) → sum(tags_made) /
      max(sum(tags_made + shots_missed), 1) * 100 over the player's rounds
      in the role.
    - Everything else (including ``mvp``) → per-round mean (sum / count).

    Output covers the full cartesian product ``ROLES × STAT_KEYS`` (60
    keys). Roles a player never played contribute no sample. Empty
    populations are ``[]``.

    Round-dict shape (the 18-key view ↔ pure-module seam): ``role``,
    ``points_scored``, ``tags_made``, ``times_tagged``, ``shots_missed``,
    ``final_special``, ``specials_used``, ``was_eliminated_at``,
    ``date_played``, ``game_round_id``, ``final_lives``,
    ``resupplies_given``, ``missiles_landed``, ``follow_up_shots``,
    ``reaction_shots``, ``combo_resupply_count``, ``mvp``,
    ``accuracy_pct``. The ``accuracy_pct`` carry-over is symmetric only —
    not consumed here; ``accuracy`` is rebuilt from raw counters.
    """
    # buckets[(player_id, role)] = list[round_dict]
    buckets: dict[tuple[int, str], list[Mapping]] = defaultdict(list)
    for r in rows:
        pid = r.get("player_id")
        if pid is None:
            continue
        buckets[(pid, r["role"])].append(r)

    # Pre-seed full cartesian so every (role, stat) key exists.
    out: dict[tuple[str, str], list[tuple[int, float]]] = {
        (role, stat): [] for role in ROLES for stat in STAT_KEYS
    }

    for (player_id, role), rounds in buckets.items():
        if role not in ROLES:
            continue
        n = len(rounds)
        if n == 0:
            continue
        for stat in STAT_KEYS:
            value = compute_career_stat_for_role(rounds, stat)
            out[(role, stat)].append((player_id, value))

    return out


def apply_threshold(
    samples: list[tuple[int, float]],
    population_min_rounds: Mapping[int, int],
    min_rounds: int,
) -> list[tuple[int, float]]:
    """Filter ``samples`` to entries whose player qualifies for the role.

    ``population_min_rounds`` maps ``player_id`` → that player's round-count
    IN THE ROLE the population represents. The caller builds one dict per
    role. An absent ``player_id`` is treated as 0 (excluded for any
    ``min_rounds > 0``).
    """
    if min_rounds <= 0:
        return list(samples)
    return [
        (pid, val)
        for pid, val in samples
        if population_min_rounds.get(pid, 0) >= min_rounds
    ]


def summarize_population(samples: list[tuple[int, float]]) -> dict:
    """Compute mean / median / p25 / p75 / p90 / n over the samples' values.

    Empty input → all metric fields ``None`` and ``n=0``. Percentile formula
    is nearest-rank with
    ``idx = min(n-1, max(0, ceil(p/100 * n) - 1))``.
    """
    n = len(samples)
    if n == 0:
        return {
            "mean": None,
            "median": None,
            "p25": None,
            "p75": None,
            "p90": None,
            "n": 0,
        }

    values = sorted(val for _pid, val in samples)
    mean = statistics.fmean(values)
    median = statistics.median(values)

    def _pct(p: int) -> float:
        # Nearest-rank: idx = ceil(p/100 * n) - 1, clamped to [0, n-1].
        # ceil(a/b) = -(-a // b) for positive ints.
        raw = -(-(p * n) // 100) - 1
        idx = min(n - 1, max(0, raw))
        return values[idx]

    return {
        "mean": mean,
        "median": median,
        "p25": _pct(25),
        "p75": _pct(75),
        "p90": _pct(90),
        "n": n,
    }


def percentile_for(value: float, sorted_values: list[float]) -> int:
    """Nearest-rank percentile in ``[0, 100]`` for ``value`` over
    ``sorted_values``.

    The subject's own value is expected to be present in ``sorted_values``
    (caller responsibility) — so the population maximum maps to 100.
    Returns 0 for an empty population.
    """
    n = len(sorted_values)
    if n == 0:
        return 0
    rank = bisect.bisect_left(sorted_values, value) + 1
    rank = min(rank, n)
    pct = (rank * 100) // n
    if pct < 0:
        return 0
    if pct > 100:
        return 100
    return pct


def compute_role_benchmarks(
    samples_by_key: Mapping[tuple[str, str], list[tuple[int, float]]],
    population_min_rounds_by_role: Mapping[str, Mapping[int, int]],
    min_rounds: int,
) -> dict[tuple[str, str], dict]:
    """Orchestrator: for every (role, stat) in ROLES × STAT_KEYS, apply the
    threshold and summarise.

    Output has exactly 60 entries. A missing key in ``samples_by_key`` is a
    contract violation and raises ``KeyError``.
    """
    out: dict[tuple[str, str], dict] = {}
    for role in ROLES:
        role_thresholds = population_min_rounds_by_role.get(role, {})
        # Hoist the qualified-player-id set once per role so the 12 stat
        # populations don't each re-derive the same predicate.
        if min_rounds <= 0:
            qualified_pids: frozenset[int] | None = None  # keep-all sentinel
        else:
            qualified_pids = frozenset(
                pid for pid, n in role_thresholds.items() if n >= min_rounds
            )
        for stat in STAT_KEYS:
            samples = samples_by_key[(role, stat)]
            if qualified_pids is None:
                filtered = list(samples)
            else:
                filtered = [(pid, val) for pid, val in samples if pid in qualified_pids]
            out[(role, stat)] = summarize_population(filtered)
    return out


def player_position(
    samples: list[tuple[int, float]],
    subject_player_id: int,
    subject_value: float,
    min_rounds_qualified: bool,
) -> dict:
    """Per-player position dict against the population.

    Subject inclusion policy: percentile AND mean/median are computed over
    the FULL population INCLUDING the subject (so the standalone benchmarks
    page and the HX-01 overlay always show the same number for the same
    cell). The caller passes the full ``samples`` list (post-threshold,
    same population the benchmarks summary used); whether the subject's
    ``(pid, value)`` is in the list when this function is called depends on
    the threshold the caller already applied — this function does NOT
    inject or remove the subject.

    Returns
    -------
    dict with keys ``{"benchmark_mean", "benchmark_median", "delta_mean",
    "delta_median", "percentile", "qualified", "n"}``.

    Semantics:
      - ``n == 0`` → all metric fields ``None``, ``qualified=False``.
      - ``n > 0`` and ``not min_rounds_qualified`` → ``benchmark_mean`` /
        ``benchmark_median`` filled; deltas + percentile ``None``;
        ``qualified=False`` (view renders "— (need N+ rounds)").
      - qualified → all fields populated; ``delta = subject_value -
        benchmark_value``.
    """
    n = len(samples)
    if n == 0:
        return {
            "benchmark_mean": None,
            "benchmark_median": None,
            "delta_mean": None,
            "delta_median": None,
            "percentile": None,
            "qualified": False,
            "n": 0,
        }

    values = sorted(val for _pid, val in samples)
    mean = statistics.fmean(values)
    median = statistics.median(values)

    if not min_rounds_qualified:
        return {
            "benchmark_mean": mean,
            "benchmark_median": median,
            "delta_mean": None,
            "delta_median": None,
            "percentile": None,
            "qualified": False,
            "n": n,
        }

    return {
        "benchmark_mean": mean,
        "benchmark_median": median,
        "delta_mean": subject_value - mean,
        "delta_median": subject_value - median,
        "percentile": percentile_for(subject_value, values),
        "qualified": True,
        "n": n,
    }
