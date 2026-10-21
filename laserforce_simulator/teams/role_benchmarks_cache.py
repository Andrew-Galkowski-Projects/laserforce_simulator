"""Role benchmark cache layer.

Caches the result of ``teams.role_benchmarks_orm.compute_benchmarks_uncached``
under a global integer version key (``role_benchmark_version``). The
``PlayerRoundState`` post_save / post_delete signal handlers (see
``teams/signals.py``) and the ``BatchSimulator._flush_to_db`` chokepoint
(see ``matches/simulation.py``) both call ``invalidate_role_benchmarks``
to bump the version on writes — including the ``bulk_create`` path that
skips ``post_save``.

UXP-14: benchmarks are scoped to the requesting Account (its own plus
Unmanaged Rounds, ADR-0038), so every cell key also carries the visibility
scope (``u<pk>`` for an Account, ``unmanaged`` for no Account). One
Account's cached numbers are never served to another. The version stays
global: any write busts every scope at once.

This module owns **only** the caching policy: the version key, the
``(scope, role, stat)`` cache-key shape, the ``cache.get_many`` round-trip, and
the ``transaction.on_commit`` deferral that prevents pre-commit
repopulation of stale data. ORM materialisation lives one layer below in
``teams.role_benchmarks_orm``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from django.core.cache import cache
from django.db import transaction

from .role_benchmarks import ROLES, STAT_KEYS

if TYPE_CHECKING:
    from django.contrib.auth.base_user import AbstractBaseUser

_VERSION_KEY: str = "role_benchmark_version"


def _visible_to(user: "AbstractBaseUser | None") -> "AbstractBaseUser | None":
    """``user`` when it is an authenticated Account, else ``None``."""
    if user is not None and getattr(user, "is_authenticated", False):
        return user
    return None


def _scope_for(user: "AbstractBaseUser | None") -> str:
    """Cache-key segment naming whose Rounds a cached population covers."""
    return f"u{user.pk}" if user is not None else "unmanaged"


def _key_for(version: int, scope: str, role: str, stat: str) -> str:
    """Cache key for a populated (role, stat) sample list."""
    return f"role_benchmark:v{version}:{scope}:{role}:{stat}"


def _rounds_in_role_key(version: int, scope: str, role: str) -> str:
    """Cache key for the per-role rounds-played map (player_id → count)."""
    return f"role_benchmark:v{version}:{scope}:_rounds_in_role:{role}"


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
        # disappeared between ``add`` and ``incr``; reset to 1 in that case so
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


def _write_through(
    version: int,
    scope: str,
    samples_by_key: dict[tuple[str, str], list[tuple[int, float]]],
    rounds_in_role: dict[str, dict[int, int]],
) -> None:
    """Write every (role, stat) cell and every rounds-in-role map under the
    given version. One-pass write after the ORM-layer scan returns.
    """
    for role in ROLES:
        cache.set(_rounds_in_role_key(version, scope, role), rounds_in_role[role])
        for stat in STAT_KEYS:
            cache.set(
                _key_for(version, scope, role, stat), samples_by_key[(role, stat)]
            )


def get_all_benchmark_data(*, user: "AbstractBaseUser | None") -> tuple[
    dict[tuple[str, str], list[tuple[int, float]]],
    dict[str, dict[int, int]],
]:
    """Return ``(samples_by_key, rounds_in_role_by_role)`` for the Rounds
    ``user`` may see (own + Unmanaged), from cache, populating it on a miss.
    An anonymous / ``None`` user sees Unmanaged Rounds only.

    Both structures cover the full cartesian product so callers can iterate
    ``ROLES × STAT_KEYS`` without key-missing branches.
    """
    visible_to = _visible_to(user)
    scope = _scope_for(visible_to)
    version = _current_version()

    # One round-trip for all 60 (role, stat) keys + 5 rounds-in-role keys
    # via cache.get_many — beats 60+ individual cache.get calls on any
    # network-backed cache (Memcached/Redis); same cost as the legacy probe
    # loop on LocMemCache.
    sample_keys = {
        (role, stat): _key_for(version, scope, role, stat)
        for role in ROLES
        for stat in STAT_KEYS
    }
    rounds_keys = {role: _rounds_in_role_key(version, scope, role) for role in ROLES}
    all_keys = list(sample_keys.values()) + list(rounds_keys.values())

    cached = cache.get_many(all_keys)

    # Cache miss iff any expected key is absent — repopulate the whole set.
    if len(cached) != len(all_keys):
        # Lazy import to avoid a top-level dependency cycle through
        # ``apps.py`` → ``signals`` → here.
        from .role_benchmarks_orm import compute_benchmarks_uncached

        samples_by_key, rounds_in_role = compute_benchmarks_uncached(user=visible_to)
        _write_through(version, scope, samples_by_key, rounds_in_role)
        return samples_by_key, rounds_in_role

    samples_by_key: dict[tuple[str, str], list[tuple[int, float]]] = {
        rs: cached[k] for rs, k in sample_keys.items()
    }
    rounds_in_role: dict[str, dict[int, int]] = {
        role: cached[k] for role, k in rounds_keys.items()
    }
    return samples_by_key, rounds_in_role


def get_role_benchmark_samples(
    role: str, stat: str, *, user: "AbstractBaseUser | None"
) -> list[tuple[int, float]]:
    """Return the samples list for a single ``(role, stat)`` cell, scoped to
    the Rounds ``user`` may see.

    Populates the full cache on a miss (mirrors ``get_all_benchmark_data``'s
    single-scan invariant).
    """
    samples_by_key, _ = get_all_benchmark_data(user=user)
    return samples_by_key.get((role, stat), [])
