"""LG-01j — Pure-unit tests for ``matches.tasks._resolve_fixture_map``.

The seam contract is locked at ``.claude/worktrees/lg-01j-seam-contract.md``
(§10 ``_resolve_fixture_map`` helper body algorithm; §13 Locked Names
Index — helper signature).

The helper signature is:

    matches.tasks._resolve_fixture_map(
        season, fixture, pool_by_id
    ) -> ArenaMap | None

It is **pure** — NO Django ORM access — and consumes only duck-typed
attributes:

    season.id / season.map_mode / season.starting_map_pool_ids_json
    fixture.matchday / fixture.round_number / fixture.team_a_id /
        fixture.team_b_id
    pool_by_id : dict[int, <ArenaMap-like>]

These tests therefore use ``types.SimpleNamespace`` / hand-crafted
``@dataclass`` stubs and a plain ``dict`` for ``pool_by_id`` — no DB,
no Django ``TestCase``, no migrations.

Seed-string format (locked, byte-for-byte):

    f"{season.id}|{fixture.matchday}|{fixture.round_number}|"
    f"{fixture.team_a_id}|{fixture.team_b_id}"

Test class names mirror the seam contract verbatim.
"""

from __future__ import annotations

import random
import unittest
from dataclasses import dataclass

from matches.tasks import _resolve_fixture_map

# ---------------------------------------------------------------------------
# Duck-typed stubs (NO Django, NO DB)
# ---------------------------------------------------------------------------


@dataclass
class _SeasonStub:
    """Minimal Season duck-type — only the 3 attributes the helper reads."""

    id: int
    map_mode: str
    starting_map_pool_ids_json: list | None


@dataclass(frozen=True)
class _FixtureStub:
    """Minimal ScheduleFixture duck-type — 4 attributes the helper reads."""

    matchday: int
    round_number: int
    team_a_id: int
    team_b_id: int


@dataclass
class _MapStub:
    """Minimal ArenaMap duck-type — only an ``id`` and ``name``."""

    id: int
    name: str = "Stub"


def _season(
    *,
    id: int = 1,
    map_mode: str = "none",
    starting_map_pool_ids_json: list | None = None,
) -> _SeasonStub:
    return _SeasonStub(
        id=id,
        map_mode=map_mode,
        starting_map_pool_ids_json=starting_map_pool_ids_json,
    )


def _fixture(
    *,
    matchday: int = 1,
    round_number: int = 1,
    team_a_id: int = 1,
    team_b_id: int = 2,
) -> _FixtureStub:
    return _FixtureStub(
        matchday=matchday,
        round_number=round_number,
        team_a_id=team_a_id,
        team_b_id=team_b_id,
    )


# ---------------------------------------------------------------------------
# TestResolveFixtureMapNone
# ---------------------------------------------------------------------------


class TestResolveFixtureMapNone(unittest.TestCase):
    """``mode == "none"`` ⇒ returns ``None`` regardless of pool / fixture."""

    def test_none_mode_returns_none_with_empty_pool(self) -> None:
        season = _season(map_mode="none", starting_map_pool_ids_json=[])
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_none_mode_returns_none_even_with_populated_pool(self) -> None:
        """Defensive: a non-empty snapshot under mode 'none' (admin
        drift) still returns None — the mode is the final say."""
        season = _season(map_mode="none", starting_map_pool_ids_json=[5, 7])
        pool_by_id = {5: _MapStub(id=5), 7: _MapStub(id=7)}
        result = _resolve_fixture_map(season, _fixture(), pool_by_id)
        self.assertIsNone(result)

    def test_none_mode_returns_none_with_null_snapshot(self) -> None:
        season = _season(map_mode="none", starting_map_pool_ids_json=None)
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)


# ---------------------------------------------------------------------------
# TestResolveFixtureMapSingle
# ---------------------------------------------------------------------------


class TestResolveFixtureMapSingle(unittest.TestCase):
    """``mode == "single"`` ⇒ returns the lone entry in ``pool_by_id``."""

    def test_single_mode_returns_the_one_map(self) -> None:
        the_map = _MapStub(id=42, name="Alpha")
        season = _season(map_mode="single", starting_map_pool_ids_json=[42])
        result = _resolve_fixture_map(season, _fixture(), {42: the_map})
        self.assertIs(result, the_map)

    def test_single_mode_returns_first_id_when_pool_has_multiple(self) -> None:
        """Defensive: snapshot has multiple ids under 'single' (drift).
        Algorithm picks ``pool_ids[0]`` per the locked body."""
        m1 = _MapStub(id=10)
        m2 = _MapStub(id=20)
        season = _season(map_mode="single", starting_map_pool_ids_json=[10, 20])
        result = _resolve_fixture_map(season, _fixture(), {10: m1, 20: m2})
        self.assertIs(result, m1)

    def test_single_mode_returns_none_when_snapshot_empty(self) -> None:
        season = _season(map_mode="single", starting_map_pool_ids_json=[])
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_single_mode_returns_none_when_snapshot_is_null(self) -> None:
        season = _season(map_mode="single", starting_map_pool_ids_json=None)
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_single_mode_returns_none_when_chosen_id_missing_from_pool(
        self,
    ) -> None:
        """Admin-deleted-after-activation: snapshot has ``[42]`` but
        ``pool_by_id`` does not contain id 42 ⇒ returns ``None``
        (defensive ``.get()`` rather than raise / crash)."""
        season = _season(map_mode="single", starting_map_pool_ids_json=[42])
        # pool_by_id contains a different id.
        pool_by_id: dict[int, _MapStub] = {99: _MapStub(id=99)}
        result = _resolve_fixture_map(season, _fixture(), pool_by_id)
        self.assertIsNone(result)

    def test_single_mode_independent_of_fixture_identity(self) -> None:
        the_map = _MapStub(id=42)
        season = _season(map_mode="single", starting_map_pool_ids_json=[42])
        a = _resolve_fixture_map(
            season,
            _fixture(matchday=1, round_number=1, team_a_id=1, team_b_id=2),
            {42: the_map},
        )
        b = _resolve_fixture_map(
            season,
            _fixture(matchday=8, round_number=2, team_a_id=5, team_b_id=9),
            {42: the_map},
        )
        self.assertIs(a, the_map)
        self.assertIs(b, the_map)


# ---------------------------------------------------------------------------
# TestResolveFixtureMapRandomPerRound
# ---------------------------------------------------------------------------


class TestResolveFixtureMapRandomPerRound(unittest.TestCase):
    """``mode == "random_per_round"`` ⇒ deterministic by seed-string."""

    def _pool(self, ids: list[int]) -> dict[int, _MapStub]:
        return {i: _MapStub(id=i, name=f"M{i}") for i in ids}

    def test_seed_string_format_is_locked(self) -> None:
        """The seed string is the byte-for-byte concatenation of 5
        components, in this exact order, pipe-separated.

        Verified by recomputing the same ``random.Random(seed_str).choice``
        result independently and asserting equality.
        """
        pool_ids = [10, 20, 30]
        season = _season(
            id=1, map_mode="random_per_round", starting_map_pool_ids_json=pool_ids
        )
        fixture = _fixture(matchday=2, round_number=1, team_a_id=3, team_b_id=4)
        # Reconstruct the locked seed string exactly.
        expected_seed = "1|2|1|3|4"
        expected_id = random.Random(expected_seed).choice(pool_ids)
        result = _resolve_fixture_map(season, fixture, self._pool(pool_ids))
        self.assertIsNotNone(result)
        self.assertEqual(result.id, expected_id)

    def test_replay_equality_same_fixture_identity(self) -> None:
        """Same Season + same fixture identity + same pool ⇒ same map
        across many calls (replay-faithful)."""
        pool_ids = [10, 20, 30, 40, 50]
        season = _season(
            id=7, map_mode="random_per_round", starting_map_pool_ids_json=pool_ids
        )
        fixture = _fixture(matchday=3, round_number=2, team_a_id=11, team_b_id=22)
        pool_by_id = self._pool(pool_ids)
        results = [
            _resolve_fixture_map(season, fixture, pool_by_id) for _ in range(100)
        ]
        # All 100 results are the same map.
        self.assertEqual(len({r.id for r in results}), 1)

    def test_varied_distribution_across_fixtures(self) -> None:
        """Different fixtures with same Season + same pool ⇒ varied
        distribution (statistical sanity check — NOT all the same map
        across 50 distinct fixtures)."""
        pool_ids = [10, 20, 30, 40, 50]
        season = _season(
            id=1, map_mode="random_per_round", starting_map_pool_ids_json=pool_ids
        )
        pool_by_id = self._pool(pool_ids)
        results = []
        for matchday in range(1, 11):
            for round_number in (1, 2):
                for offset in (0, 100):
                    fixture = _fixture(
                        matchday=matchday,
                        round_number=round_number,
                        team_a_id=1 + offset,
                        team_b_id=2 + offset,
                    )
                    r = _resolve_fixture_map(season, fixture, pool_by_id)
                    results.append(r.id)
        # 40 fixtures × non-trivial distribution → more than 1 distinct
        # map drawn.
        self.assertGreater(len(set(results)), 1)

    def test_empty_pool_returns_none(self) -> None:
        season = _season(map_mode="random_per_round", starting_map_pool_ids_json=[])
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_null_snapshot_returns_none(self) -> None:
        season = _season(map_mode="random_per_round", starting_map_pool_ids_json=None)
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_returns_none_when_chosen_id_missing_from_pool_by_id(self) -> None:
        """All pool ids in the snapshot were admin-deleted ⇒ ``.get()``
        returns ``None``."""
        season = _season(
            id=1,
            map_mode="random_per_round",
            starting_map_pool_ids_json=[42],
        )
        # pool_by_id is empty — the chosen id 42 will not resolve.
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_uses_independent_random_does_not_perturb_global_seed(self) -> None:
        """The helper builds a fresh ``random.Random`` per call — calling
        it must NOT consume from the global ``random`` module's state."""
        pool_ids = [10, 20, 30]
        season = _season(
            id=1, map_mode="random_per_round", starting_map_pool_ids_json=pool_ids
        )
        random.seed(42)
        before = random.random()
        random.seed(42)
        # Now invoke the helper.
        _resolve_fixture_map(season, _fixture(), self._pool(pool_ids))
        # The next ``random.random()`` after re-seeding must equal
        # what we observed pre-call (i.e. the helper did NOT pull from
        # the global RNG).
        after = random.random()
        self.assertEqual(before, after)


# ---------------------------------------------------------------------------
# TestResolveFixtureMapUnknownMode
# ---------------------------------------------------------------------------


class TestResolveFixtureMapUnknownMode(unittest.TestCase):
    """``mode == "bogus"`` ⇒ ``ValueError`` with locked message."""

    def test_unknown_mode_raises_value_error(self) -> None:
        season = _season(map_mode="bogus", starting_map_pool_ids_json=[])
        with self.assertRaises(ValueError) as cm:
            _resolve_fixture_map(season, _fixture(), {})
        self.assertIn("Unknown map_mode:", str(cm.exception))

    def test_unknown_mode_message_uses_repr(self) -> None:
        """Locked: ``f"Unknown map_mode: {mode!r}"`` — the ``!r``
        produces a single-quoted string in the message."""
        season = _season(map_mode="weird", starting_map_pool_ids_json=[])
        with self.assertRaises(ValueError) as cm:
            _resolve_fixture_map(season, _fixture(), {})
        # ``repr("weird")`` is ``"'weird'"`` — assert the quoted form.
        self.assertIn("'weird'", str(cm.exception))

    def test_unknown_mode_empty_string(self) -> None:
        season = _season(map_mode="", starting_map_pool_ids_json=[])
        with self.assertRaises(ValueError):
            _resolve_fixture_map(season, _fixture(), {})


# ---------------------------------------------------------------------------
# TestResolveFixtureMapMissingMap
# ---------------------------------------------------------------------------


class TestResolveFixtureMapMissingMap(unittest.TestCase):
    """Defensive ``pool_by_id.get(chosen_id)`` returns ``None`` for both
    ``single`` and ``random_per_round`` when the map row was deleted
    between activation and simulation.
    """

    def test_single_missing_map_returns_none(self) -> None:
        # Snapshot says id 99 is in the pool; pool_by_id does not have it.
        season = _season(map_mode="single", starting_map_pool_ids_json=[99])
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_random_per_round_missing_map_returns_none(self) -> None:
        season = _season(
            id=1, map_mode="random_per_round", starting_map_pool_ids_json=[99]
        )
        # pool_by_id missing the single id => chosen id will be 99,
        # which is not in pool_by_id => returns None.
        result = _resolve_fixture_map(season, _fixture(), {})
        self.assertIsNone(result)

    def test_random_per_round_partial_pool_some_chosen_resolve_some_dont(
        self,
    ) -> None:
        """Snapshot has [10, 20, 30], pool_by_id only has 20. The helper
        may pick any of the three pool_ids deterministically per fixture;
        when it picks 10 or 30, returns None; when it picks 20, returns
        the live ArenaMap. The behaviour is defensive — not a crash."""
        pool_ids = [10, 20, 30]
        season = _season(
            id=1,
            map_mode="random_per_round",
            starting_map_pool_ids_json=pool_ids,
        )
        pool_by_id = {20: _MapStub(id=20)}
        # Try across many fixtures to land both resolved and unresolved
        # picks; assert the helper never raises and only ever returns
        # either the live map or None.
        seen_live = False
        seen_none = False
        for matchday in range(1, 25):
            for round_number in (1, 2):
                fixture = _fixture(
                    matchday=matchday,
                    round_number=round_number,
                    team_a_id=1,
                    team_b_id=2,
                )
                r = _resolve_fixture_map(season, fixture, pool_by_id)
                if r is None:
                    seen_none = True
                else:
                    self.assertEqual(r.id, 20)
                    seen_live = True
        # Both branches should be exercised by 48 fixtures + 3-id pool.
        self.assertTrue(seen_live)
        self.assertTrue(seen_none)
