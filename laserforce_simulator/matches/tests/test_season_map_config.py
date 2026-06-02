"""Tests for per-Season arena-map configuration (LG-01j): the pure
``_resolve_fixture_map`` helper and the Season map-mode / map-pool model and
create-form surface.
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


# ===== Season map-mode / map-pool model + create-form =====
import io
from datetime import date

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import models as django_models
from django.test import TestCase

from core.models import ArenaMap
from matches.models import League, Season
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_league(name: str = "LG01jL") -> League:
    return League.objects.create(name=name, mode="league", state="active")


def _png_bytes() -> bytes:
    """Tiny synthetic PNG payload — enough bytes to land on disk."""
    from PIL import Image as PILImage

    buf = io.BytesIO()
    PILImage.new("RGB", (10, 10), color=(255, 0, 0)).save(buf, format="PNG")
    return buf.getvalue()


def _make_arena_map(name: str = "Map") -> ArenaMap:
    """Build an ArenaMap row with a real image file (the model's image
    FieldFile requires content)."""
    return ArenaMap.objects.create(
        name=name,
        image=SimpleUploadedFile(f"{name}.png", _png_bytes(), content_type="image/png"),
        img_width=10,
        img_height=10,
    )


def _make_draft_season(
    league: League,
    *,
    name: str = "S1",
    n_teams: int = 2,
    start_date: date = date(2026, 1, 1),
) -> tuple[Season, list]:
    season = Season.objects.create(
        league=league,
        name=name,
        start_date=start_date,
    )
    teams = []
    for i in range(n_teams):
        t, _ = make_team_with_slots(f"{league.name[:3]}T{i}")
        teams.append(t)
        season.teams.add(t)
    return season, teams


# ---------------------------------------------------------------------------
# TestSeasonMapModeField
# ---------------------------------------------------------------------------


class TestSeasonMapModeField(TestCase):
    """``Season.map_mode`` — CharField, choices, default, max_length."""

    def test_field_exists_on_season_model(self) -> None:
        field = Season._meta.get_field("map_mode")
        self.assertIsNotNone(field)

    def test_field_is_charfield(self) -> None:
        field = Season._meta.get_field("map_mode")
        self.assertIsInstance(field, django_models.CharField)

    def test_field_max_length_is_32(self) -> None:
        field = Season._meta.get_field("map_mode")
        self.assertEqual(field.max_length, 32)

    def test_field_default_is_none_string(self) -> None:
        field = Season._meta.get_field("map_mode")
        self.assertEqual(field.default, "none")

    def test_field_choices_are_the_three_locked_tuples(self) -> None:
        field = Season._meta.get_field("map_mode")
        # Locked at seam contract §3 + §13 Locked Names Index.
        self.assertEqual(
            list(field.choices),
            [
                ("none", "3-zone fallback"),
                ("single", "Single map"),
                ("random_per_round", "Random per Round"),
            ],
        )

    def test_default_value_persists_on_create(self) -> None:
        league = _make_league("DefaultMode")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        self.assertEqual(season.map_mode, "none")

    def test_full_clean_rejects_unknown_mode(self) -> None:
        league = _make_league("BadMode")
        season = Season(
            league=league,
            name="S1",
            start_date=date(2026, 1, 1),
            map_mode="bogus",
        )
        with self.assertRaises(ValidationError) as cm:
            season.full_clean()
        # The ValidationError dict carries the ``map_mode`` key.
        self.assertIn("map_mode", cm.exception.message_dict)

    def test_full_clean_accepts_each_of_the_three_modes(self) -> None:
        league = _make_league("AcceptModes")
        for mode in ("none", "single", "random_per_round"):
            season = Season(
                league=league,
                name=f"S-{mode}",
                start_date=date(2026, 1, 1),
                map_mode=mode,
            )
            try:
                # ``map_pool`` rule is form/admin-side, not model-side,
                # so ``full_clean`` here ignores the M2M / mode-vs-pool
                # validity.
                season.full_clean()
            except ValidationError as exc:
                # ``map_pool`` may not appear in the error dict; allow
                # other top-level errors (like LG-01 active-Season
                # invariant) only if NOT keyed on ``map_mode``.
                if "map_mode" in exc.message_dict:
                    self.fail(
                        f"mode={mode!r} unexpectedly raised on map_mode: "
                        f"{exc.message_dict!r}"
                    )


# ---------------------------------------------------------------------------
# TestSeasonMapPoolField
# ---------------------------------------------------------------------------


class TestSeasonMapPoolField(TestCase):
    """``Season.map_pool`` — M2M, blank, related_name, persistence."""

    def test_field_exists_on_season_model(self) -> None:
        field = Season._meta.get_field("map_pool")
        self.assertIsNotNone(field)

    def test_field_is_many_to_many(self) -> None:
        field = Season._meta.get_field("map_pool")
        self.assertIsInstance(field, django_models.ManyToManyField)

    def test_field_blank_true(self) -> None:
        field = Season._meta.get_field("map_pool")
        self.assertTrue(field.blank)

    def test_field_target_is_arena_map(self) -> None:
        field = Season._meta.get_field("map_pool")
        self.assertIs(field.related_model, ArenaMap)

    def test_related_name_is_seasons_using_pool(self) -> None:
        """``arena_map.seasons_using_pool`` reverse accessor must work."""
        league = _make_league("RelName")
        season, _ = _make_draft_season(league)
        m = _make_arena_map("Rel1")
        season.map_pool.add(m)
        self.assertIn(season, m.seasons_using_pool.all())

    def test_draft_season_can_set_pool(self) -> None:
        league = _make_league("DraftSet")
        season, _ = _make_draft_season(league)
        m1 = _make_arena_map("DSet1")
        m2 = _make_arena_map("DSet2")
        season.map_pool.set([m1, m2])
        ids = sorted(season.map_pool.values_list("id", flat=True))
        self.assertEqual(ids, sorted([m1.id, m2.id]))

    def test_adding_and_removing_maps_persists(self) -> None:
        league = _make_league("AddRem")
        season, _ = _make_draft_season(league)
        m1 = _make_arena_map("AR1")
        m2 = _make_arena_map("AR2")
        season.map_pool.add(m1)
        self.assertEqual(season.map_pool.count(), 1)
        season.map_pool.add(m2)
        self.assertEqual(season.map_pool.count(), 2)
        season.map_pool.remove(m1)
        remaining = list(season.map_pool.values_list("id", flat=True))
        self.assertEqual(remaining, [m2.id])

    def test_empty_pool_is_valid_at_orm_level(self) -> None:
        """Field-level ``blank=True`` means an empty M2M is OK at the
        ORM layer — mode-vs-pool rules live form/admin-side."""
        league = _make_league("EmptyPool")
        season, _ = _make_draft_season(league)
        # No maps added.
        self.assertEqual(season.map_pool.count(), 0)


# ---------------------------------------------------------------------------
# TestSeasonStartingMapPoolSnapshot
# ---------------------------------------------------------------------------


class TestSeasonStartingMapPoolSnapshot(TestCase):
    """``Season.starting_map_pool_ids_json`` — JSONField, null, default."""

    def test_field_exists_on_season_model(self) -> None:
        field = Season._meta.get_field("starting_map_pool_ids_json")
        self.assertIsNotNone(field)

    def test_field_is_json_field(self) -> None:
        field = Season._meta.get_field("starting_map_pool_ids_json")
        self.assertIsInstance(field, django_models.JSONField)

    def test_field_null_true(self) -> None:
        field = Season._meta.get_field("starting_map_pool_ids_json")
        self.assertTrue(field.null)

    def test_field_blank_true(self) -> None:
        field = Season._meta.get_field("starting_map_pool_ids_json")
        self.assertTrue(field.blank)

    def test_field_default_is_none(self) -> None:
        field = Season._meta.get_field("starting_map_pool_ids_json")
        self.assertIsNone(field.default)

    def test_new_draft_season_has_none_snapshot(self) -> None:
        league = _make_league("PreActSnap")
        season, _ = _make_draft_season(league)
        self.assertIsNone(season.starting_map_pool_ids_json)


# ---------------------------------------------------------------------------
# TestSeasonStartSeasonSnapshotsMapPool
# ---------------------------------------------------------------------------


class TestSeasonStartSeasonSnapshotsMapPool(TestCase):
    """``Season.start_season()`` populates ``starting_map_pool_ids_json``
    from the live M2M, sorted ascending, inside the existing
    ``@transaction.atomic`` block.
    """

    def test_start_season_snapshots_empty_pool_as_empty_list(self) -> None:
        league = _make_league("SnapEmpty")
        season, _ = _make_draft_season(league)
        season.start_season()
        season.refresh_from_db()
        # Empty pool ⇒ [] (NOT None — None is reserved for pre-activation).
        self.assertEqual(season.starting_map_pool_ids_json, [])

    def test_start_season_snapshots_pool_sorted_ascending(self) -> None:
        league = _make_league("SnapSort")
        season, _ = _make_draft_season(league)
        # Build 3 maps with controlled-but-not-sorted ids by adding in
        # alphabetical name order then asserting the SNAPSHOT is sorted
        # ascending by id (the locked rule).
        m_a = _make_arena_map("MA")
        m_b = _make_arena_map("MB")
        m_c = _make_arena_map("MC")
        # Add in a non-ascending order.
        season.map_pool.add(m_c, m_a, m_b)
        season.start_season()
        season.refresh_from_db()
        expected = sorted([m_a.id, m_b.id, m_c.id])
        self.assertEqual(season.starting_map_pool_ids_json, expected)

    def test_start_season_snapshots_single_pool(self) -> None:
        league = _make_league("SnapSingle")
        season, _ = _make_draft_season(league)
        m = _make_arena_map("Solo")
        season.map_pool.add(m)
        season.start_season()
        season.refresh_from_db()
        self.assertEqual(season.starting_map_pool_ids_json, [m.id])

    def test_snapshot_persists_after_save(self) -> None:
        """The snapshot is committed by ``self.save()`` inside the
        atomic block — confirmed by a second ``refresh_from_db()``."""
        league = _make_league("SnapPersist")
        season, _ = _make_draft_season(league)
        m1 = _make_arena_map("P1")
        m2 = _make_arena_map("P2")
        season.map_pool.add(m1, m2)
        season.start_season()
        # Cycle.
        snap_before = season.starting_map_pool_ids_json
        season.refresh_from_db()
        snap_after = season.starting_map_pool_ids_json
        self.assertEqual(snap_before, snap_after)
        self.assertEqual(snap_after, sorted([m1.id, m2.id]))

    def test_snapshot_does_not_track_live_m2m_after_activation(self) -> None:
        """After activation, mutating the live M2M leaves
        ``starting_map_pool_ids_json`` unchanged (the snapshot is the
        source of truth post-activation)."""
        league = _make_league("SnapFrozen")
        season, _ = _make_draft_season(league)
        m1 = _make_arena_map("F1")
        season.map_pool.add(m1)
        season.start_season()
        snap_at_activation = list(season.starting_map_pool_ids_json or [])

        # Mutate the live M2M after activation.
        m2 = _make_arena_map("F2")
        season.map_pool.add(m2)
        season.refresh_from_db()

        self.assertEqual(season.starting_map_pool_ids_json, snap_at_activation)
        # Cross-check: live M2M now has 2; snapshot still has 1.
        self.assertEqual(season.map_pool.count(), 2)
        self.assertEqual(len(season.starting_map_pool_ids_json), 1)

    def test_re_activation_after_draft_re_snapshots(self) -> None:
        """A Season that is back to draft and re-activated after a pool
        edit re-snapshots correctly. (Defends the LG-01-locked
        ``start_season`` re-entry semantics.)"""
        league = _make_league("ReSnap")
        season, _ = _make_draft_season(league)
        m1 = _make_arena_map("RS1")
        season.map_pool.add(m1)
        season.start_season()
        season.refresh_from_db()
        self.assertEqual(season.starting_map_pool_ids_json, [m1.id])

        # Force back to draft (bypass clean), edit pool, re-activate.
        Season.objects.filter(pk=season.pk).update(state="draft")
        season.refresh_from_db()
        m2 = _make_arena_map("RS2")
        season.map_pool.add(m2)
        season.start_season()
        season.refresh_from_db()
        self.assertEqual(season.starting_map_pool_ids_json, sorted([m1.id, m2.id]))
