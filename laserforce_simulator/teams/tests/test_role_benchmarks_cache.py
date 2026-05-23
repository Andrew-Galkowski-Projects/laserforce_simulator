"""HX-02 — Cache-invalidation tests for ``teams/role_benchmarks_cache.py``.

Pins:
- ``invalidate_role_benchmarks`` lazily initialises ``role_benchmark_version``
  then increments monotonically.
- ``PlayerRoundState`` post_save / post_delete signals call
  ``invalidate_role_benchmarks``.
- The ``BatchSimulator._flush_to_db`` chokepoint calls it too (the
  ``bulk_create`` path skips post_save, so the explicit hook is
  necessary).
- A stale read after a mutation reflects the new data end-to-end.
- ``_populate_all_caches`` is called exactly once per version on
  consecutive ``get_all_benchmark_data()`` calls.

Each class is wrapped in ``@override_settings`` to pin a clean
LOCATION; ``cache.clear()`` runs in ``setUp``.
"""

from __future__ import annotations

from unittest import mock

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from matches.models import GameRound, PlayerRoundState
from teams.models import Player, Team
from teams.role_benchmarks_cache import (
    _VERSION_KEY,
    get_all_benchmark_data,
    invalidate_role_benchmarks,
)


def _make_player(team_name: str, player_name: str) -> tuple[Team, Player]:
    team = Team.objects.create(name=team_name)
    player = Player.objects.create(team=team, name=player_name)
    team.slot_commander = player
    team.save()
    return team, player


def _make_round_state(
    player: Player,
    team: Team,
    *,
    role: str = "commander",
    points_scored: int = 500,
) -> PlayerRoundState:
    game_round = GameRound.objects.create(
        round_number=1,
        team_red=team,
        team_blue=team,
    )
    return PlayerRoundState.objects.create(
        game_round=game_round,
        player=player,
        team_color="red",
        role=role,
        points_scored=points_scored,
        tags_made=5,
        times_tagged=3,
        shots_missed=4,
        final_special=2,
        specials_used=1,
        was_eliminated_at=1500,
        final_lives=5,
    )


# ---------------------------------------------------------------------------
# Version-key invariants
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-version",
        }
    }
)
class TestVersionKey(TestCase):
    """Lazy-init at 0 then monotonic increment."""

    def setUp(self) -> None:
        cache.clear()

    def test_version_key_constant_matches_seam(self) -> None:
        self.assertEqual(_VERSION_KEY, "role_benchmark_version")

    def test_first_invalidate_lazily_creates_version(self) -> None:
        # Pre-condition: no key.
        self.assertIsNone(cache.get(_VERSION_KEY))
        # invalidate_role_benchmarks() defers via transaction.on_commit so
        # the bump fires when the wrapping context exits inside TestCase's
        # atomic block (which would otherwise swallow the callback).
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_role_benchmarks()
        self.assertEqual(int(cache.get(_VERSION_KEY)), 1)

    def test_subsequent_invalidates_are_monotonic(self) -> None:
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_role_benchmarks()
            invalidate_role_benchmarks()
            invalidate_role_benchmarks()
        self.assertEqual(int(cache.get(_VERSION_KEY)), 3)


# ---------------------------------------------------------------------------
# Signal wiring
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-signals",
        }
    }
)
class TestPostSaveSignal(TestCase):
    """A real PlayerRoundState.objects.create() bumps the version."""

    def setUp(self) -> None:
        cache.clear()

    def test_post_save_bumps_version(self) -> None:
        team, player = _make_player("Sig Team", "Sig Player")
        # Prime the version key so we have a baseline (and the create()
        # call observes a defined prior value).
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_role_benchmarks()
        before = int(cache.get(_VERSION_KEY))
        with self.captureOnCommitCallbacks(execute=True):
            _make_round_state(player, team)
        after = int(cache.get(_VERSION_KEY))
        self.assertGreater(after, before)


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-delete",
        }
    }
)
class TestPostDeleteSignal(TestCase):
    """A PlayerRoundState.delete() bumps the version."""

    def setUp(self) -> None:
        cache.clear()

    def test_post_delete_bumps_version(self) -> None:
        team, player = _make_player("Del Team", "Del Player")
        with self.captureOnCommitCallbacks(execute=True):
            state = _make_round_state(player, team)
            invalidate_role_benchmarks()
        before = int(cache.get(_VERSION_KEY))
        with self.captureOnCommitCallbacks(execute=True):
            state.delete()
        after = int(cache.get(_VERSION_KEY))
        self.assertGreater(after, before)


# ---------------------------------------------------------------------------
# Simulator-hook coverage
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-sim",
        }
    }
)
class TestSimulatorHook(TestCase):
    """``BatchSimulator._flush_to_db`` calls ``invalidate_role_benchmarks``.

    Uses ``simulate_single_round_detailed`` end-to-end with two minimal
    rosters; we patch ``BatchSimulator.ROUND_TICKS`` to keep the test
    fast.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_flush_to_db_bumps_version(self) -> None:
        from matches.simulation import BatchSimulator
        from matches.tests.conftest import make_team_with_slots

        team_red, _ = make_team_with_slots("Red")
        team_blue, _ = make_team_with_slots("Blue")
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_role_benchmarks()
        before = int(cache.get(_VERSION_KEY))

        sim = BatchSimulator()
        with self.captureOnCommitCallbacks(execute=True):
            with mock.patch.object(BatchSimulator, "ROUND_TICKS", 5):
                sim.simulate_single_round_detailed(team_red, team_blue)

        after = int(cache.get(_VERSION_KEY))
        # Each PlayerRoundState bulk_create skips post_save, so the
        # explicit hook from _flush_to_db is what must fire here. Any
        # bump (signal + explicit hook combined) proves the chokepoint
        # closed the gap.
        self.assertGreater(after, before)


# ---------------------------------------------------------------------------
# End-to-end stale-read invalidation
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-stale",
        }
    }
)
class TestStaleReadInvalidated(TestCase):
    """View call → cache primed → mutate row → next view call reflects it."""

    def setUp(self) -> None:
        cache.clear()

    def test_mutation_reflected_in_next_view_call(self) -> None:
        team, player = _make_player("Stale Team", "Stale Player")
        with self.captureOnCommitCallbacks(execute=True):
            _make_round_state(player, team, role="commander", points_scored=1000)

        url = reverse("role_benchmarks")
        first = self.client.get(url + "?threshold=0")
        self.assertEqual(first.status_code, 200)
        first_benchmarks = first.context["benchmarks"]
        first_mean = next(
            row["mean"]
            for row in first_benchmarks["commander"]
            if row["stat"] == "points_scored"
        )
        # Single 1000-point round → mean of one-player population = 1000.
        self.assertAlmostEqual(first_mean, 1000.0)

        # Mutate: add a second round with 3000 points. Wrap the write in
        # captureOnCommitCallbacks(execute=True) so the deferred
        # invalidate_role_benchmarks() fires inside the TestCase atomic
        # block (otherwise the on_commit callback never runs).
        with self.captureOnCommitCallbacks(execute=True):
            _make_round_state(player, team, role="commander", points_scored=3000)

        second = self.client.get(url + "?threshold=0")
        self.assertEqual(second.status_code, 200)
        second_mean = next(
            row["mean"]
            for row in second.context["benchmarks"]["commander"]
            if row["stat"] == "points_scored"
        )
        # Player's career-mean is (1000+3000)/2 = 2000 — population
        # contains exactly one player with career-mean 2000.
        self.assertAlmostEqual(second_mean, 2000.0)
        # And explicitly NOT the stale 1000 (would indicate caching skipped
        # the invalidation).
        self.assertNotAlmostEqual(second_mean, 1000.0)


# ---------------------------------------------------------------------------
# Fill-on-miss-once
# ---------------------------------------------------------------------------


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "hx02-test-cache-fill",
        }
    }
)
class TestFillOnMissOnce(TestCase):
    """Two consecutive ``get_all_benchmark_data()`` calls at the same
    version → ``_populate_all_caches`` runs exactly once.
    """

    def setUp(self) -> None:
        cache.clear()

    def test_populate_called_exactly_once(self) -> None:
        # Prime the version so the first lookup probes a defined key.
        with self.captureOnCommitCallbacks(execute=True):
            invalidate_role_benchmarks()
        from teams import role_benchmarks_cache as cache_mod

        real_populate = cache_mod._populate_all_caches
        call_count = {"n": 0}

        def _spy(version: int):
            call_count["n"] += 1
            return real_populate(version)

        with mock.patch.object(cache_mod, "_populate_all_caches", _spy):
            get_all_benchmark_data()
            get_all_benchmark_data()

        self.assertEqual(call_count["n"], 1)
