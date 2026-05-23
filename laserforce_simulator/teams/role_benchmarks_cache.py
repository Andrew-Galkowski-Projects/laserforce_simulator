"""HX-02 — Role benchmark cache helper.

Hydrates per-(role, stat) population samples from a **single full-table
PlayerRoundState scan** and caches them via the Django cache framework,
keyed by a global integer version (`role_benchmark_version`). Two signal
handlers (post_save / post_delete on PlayerRoundState) and the
simulator's `_flush_to_db` chokepoint bump the version, invalidating all
prior entries lazily.

The pure benchmarks module (`teams.role_benchmarks`) never sees an ORM
object: this helper adapts each row into a tiny `_MvpAdapter` dataclass
so `matches.sim_helpers.score_calculator.calculate_mvp` (duck-typed) can
compute MVP without touching the DB during reduction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.core.cache import cache
from django.db import transaction

from matches.models import PlayerRoundState
from matches.sim_helpers.score_calculator import calculate_mvp

from .role_benchmarks import ROLES, STAT_KEYS

_VERSION_KEY: str = "role_benchmark_version"


# ---------------------------------------------------------------------------
# MVP adapter — keeps `calculate_mvp` happy without exposing ORM internals to
# the pure role-benchmarks reducer.
# ---------------------------------------------------------------------------


@dataclass
class _MvpGameRound:
    """The subset of `GameRound` attributes `calculate_mvp` reads on `gr`."""

    blue_team_eliminated: bool
    red_team_eliminated: bool
    eliminated_at: int


@dataclass
class _MvpAdapter:
    """Duck-typed adapter exposing exactly the attributes `calculate_mvp`
    reads on a `PlayerRoundState`.

    `get_accuracy` is a `@property` because the function reads it as an
    attribute (no call); we mirror the ORM model's identical formula.
    """

    role: str
    team_color: str
    final_lives: int
    final_medic_hits: int
    enemy_nuke_cancels: int
    ally_nuke_cancels: int
    times_missiled: int
    missiles_landed: int
    specials_used: int
    own_specials_cancelled: int
    points_scored: int
    tags_made: int
    shots_missed: int
    specific_tags: dict
    game_round: _MvpGameRound = field(repr=False)

    @property
    def get_accuracy(self) -> int:
        total = self.tags_made + self.shots_missed
        if total == 0:
            return 0
        return round(self.tags_made / total * 100)


# Field names pulled from a single `.values(...)` scan. Mirrors every
# attribute `_MvpAdapter` (and downstream the benchmarks reducer) needs.
_PLAYER_STATE_FIELDS: tuple[str, ...] = (
    "id",
    "player_id",
    "role",
    "team_color",
    "final_lives",
    "final_medic_hits",
    "enemy_nuke_cancels",
    "ally_nuke_cancels",
    "times_missiled",
    "missiles_landed",
    "specials_used",
    "own_specials_cancelled",
    "points_scored",
    "tags_made",
    "shots_missed",
    "times_tagged",
    "resupplies_given",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
    "final_special",
    "specific_tags",
    "game_round__id",
    "game_round__blue_team_eliminated",
    "game_round__red_team_eliminated",
    "game_round__eliminated_at",
)


def _key_for(version: int, role: str, stat: str) -> str:
    """Cache key for a populated (role, stat) sample list."""
    return f"role_benchmark:v{version}:{role}:{stat}"


def _rounds_in_role_key(version: int, role: str) -> str:
    """Cache key for the per-role rounds-played map (player_id → count)."""
    return f"role_benchmark:v{version}:_rounds_in_role:{role}"


def _current_version() -> int:
    """Read the version counter, lazily initialising it to 0 on first call."""
    cache.add(_VERSION_KEY, 0)
    val = cache.get(_VERSION_KEY)
    if val is None:
        # An LRU cache might have evicted the key between add+get under load;
        # fall back to 0 (treat as fresh).
        return 0
    return int(val)


def _do_invalidate() -> None:
    """The actual version-bump. Called via ``transaction.on_commit``.

    Defers until commit so a concurrent reader can't repopulate the cache
    from pre-commit DB state between bump and commit (the next view request
    would see stale numbers until something else mutated). Outside an active
    transaction Django runs the callback immediately, so non-transactional
    callers behave as before.
    """
    cache.add(_VERSION_KEY, 0)
    try:
        cache.incr(_VERSION_KEY)
    except ValueError:
        # The dummy / local-memory cache occasionally raises if the key
        # disappeared between `add` and `incr`; reset to 1 in that case so
        # we still observe a version change.
        cache.set(_VERSION_KEY, 1)


def invalidate_role_benchmarks() -> None:
    """Bump the global benchmark version on transaction commit.

    All prior cache entries become unreachable (the new lookups miss and
    repopulate from the next view request). Wrapped in
    ``transaction.on_commit`` so signal handlers + the ``_flush_to_db``
    hook can't pre-commit-repopulate the cache from data the in-flight
    write hasn't yet committed. Outside an active transaction the callback
    fires immediately — safe to call eagerly.
    """
    transaction.on_commit(_do_invalidate)


def _populate_all_caches(
    version: int,
) -> tuple[
    dict[tuple[str, str], list[tuple[int, float]]],
    dict[str, dict[int, int]],
]:
    """Run the full-table scan, compute per-(role, stat) samples and per-role
    round-counts, and write every key under the given version.

    Returns the two structures so the calling helper can return them
    directly without a second cache round-trip.
    """
    # Lazy import to avoid a top-level dependency cycle through `apps.py`
    # → `signals` → here. Pure-module by contract; only this helper reaches
    # into the ORM.
    from .role_benchmarks import build_role_populations

    rows = list(PlayerRoundState.objects.values(*_PLAYER_STATE_FIELDS))

    round_dicts: list[dict[str, Any]] = []
    for row in rows:
        adapter = _MvpAdapter(
            role=row["role"],
            team_color=row["team_color"],
            final_lives=row["final_lives"],
            final_medic_hits=row["final_medic_hits"],
            enemy_nuke_cancels=row["enemy_nuke_cancels"],
            ally_nuke_cancels=row["ally_nuke_cancels"],
            times_missiled=row["times_missiled"],
            missiles_landed=row["missiles_landed"],
            specials_used=row["specials_used"],
            own_specials_cancelled=row["own_specials_cancelled"],
            points_scored=row["points_scored"],
            tags_made=row["tags_made"],
            shots_missed=row["shots_missed"],
            specific_tags=row["specific_tags"] or {},
            game_round=_MvpGameRound(
                blue_team_eliminated=bool(row["game_round__blue_team_eliminated"]),
                red_team_eliminated=bool(row["game_round__red_team_eliminated"]),
                eliminated_at=int(row["game_round__eliminated_at"] or 0),
            ),
        )
        mvp = float(calculate_mvp(adapter))
        accuracy_pct = float(adapter.get_accuracy)

        round_dicts.append(
            {
                "player_id": row["player_id"],
                "role": row["role"],
                "points_scored": row["points_scored"],
                "tags_made": row["tags_made"],
                "times_tagged": row["times_tagged"],
                "shots_missed": row["shots_missed"],
                "final_lives": row["final_lives"],
                "resupplies_given": row["resupplies_given"],
                "missiles_landed": row["missiles_landed"],
                "specials_used": row["specials_used"],
                "follow_up_shots": row["follow_up_shots"],
                "reaction_shots": row["reaction_shots"],
                "combo_resupply_count": row["combo_resupply_count"],
                "mvp": mvp,
                "accuracy_pct": accuracy_pct,
            }
        )

    samples_by_key = build_role_populations(round_dicts)

    # Per-role rounds-played map: how many rounds player `pid` played in
    # each role. Used to threshold a population by min-rounds.
    rounds_in_role: dict[str, dict[int, int]] = {role: {} for role in ROLES}
    for rd in round_dicts:
        role = rd["role"]
        if role not in rounds_in_role:
            continue
        pid = rd["player_id"]
        if pid is None:
            continue
        rounds_in_role[role][pid] = rounds_in_role[role].get(pid, 0) + 1

    # Write every key under this version.
    for role in ROLES:
        cache.set(_rounds_in_role_key(version, role), rounds_in_role[role])
        for stat in STAT_KEYS:
            cache.set(
                _key_for(version, role, stat),
                samples_by_key[(role, stat)],
            )

    return samples_by_key, rounds_in_role


def get_all_benchmark_data() -> tuple[
    dict[tuple[str, str], list[tuple[int, float]]],
    dict[str, dict[int, int]],
]:
    """Return ``(samples_by_key, rounds_in_role_by_role)`` from cache,
    populating it on a miss.

    Both structures cover the full cartesian product so callers can iterate
    ``ROLES × STAT_KEYS`` without key-missing branches.
    """
    version = _current_version()

    # One round-trip for all 60 (role, stat) keys + 5 rounds-in-role keys
    # via cache.get_many — beats 60+ individual cache.get calls on any
    # network-backed cache (Memcached/Redis); same cost as the legacy probe
    # loop on LocMemCache.
    sample_keys = {
        (role, stat): _key_for(version, role, stat)
        for role in ROLES
        for stat in STAT_KEYS
    }
    rounds_keys = {role: _rounds_in_role_key(version, role) for role in ROLES}
    all_keys = list(sample_keys.values()) + list(rounds_keys.values())

    cached = cache.get_many(all_keys)

    # Cache miss iff any expected key is absent — repopulate the whole set.
    if len(cached) != len(all_keys):
        return _populate_all_caches(version)

    samples_by_key: dict[tuple[str, str], list[tuple[int, float]]] = {
        rs: cached[k] for rs, k in sample_keys.items()
    }
    rounds_in_role: dict[str, dict[int, int]] = {
        role: cached[k] for role, k in rounds_keys.items()
    }
    return samples_by_key, rounds_in_role


def get_role_benchmark_samples(role: str, stat: str) -> list[tuple[int, float]]:
    """Return the samples list for a single ``(role, stat)`` cell.

    Populates the full cache on a miss (mirrors ``get_all_benchmark_data``'s
    single-scan invariant).
    """
    samples_by_key, _ = get_all_benchmark_data()
    return samples_by_key.get((role, stat), [])


# Sentinel — distinguishes a cached ``None`` value from a cache miss.
_MISSING = object()
