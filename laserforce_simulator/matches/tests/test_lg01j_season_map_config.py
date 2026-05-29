"""LG-01j — Django ``TestCase`` tests for the per-Season map_mode +
map_pool model surface.

The seam contract is locked at ``.claude/worktrees/lg-01j-seam-contract.md``
(§3 Data model — 3 new ``Season`` fields; §5 ``Season.start_season()``
extension — snapshot to ``starting_map_pool_ids_json``).

Three new fields on ``Season``:

    map_mode: CharField(
        choices=[
            ("none", "3-zone fallback"),
            ("single", "Single map"),
            ("random_per_round", "Random per Round"),
        ],
        default="none",
        max_length=32,
    )

    map_pool: ManyToManyField(
        "core.ArenaMap",
        blank=True,
        related_name="seasons_using_pool",
    )

    starting_map_pool_ids_json: JSONField(
        null=True, blank=True, default=None
    )

``Season.start_season()`` extended to snapshot
``starting_map_pool_ids_json = sorted([m.id for m in self.map_pool.all()])``
inside the existing ``@transaction.atomic`` block, immediately after the
existing ``starting_team_ids_json`` snapshot.

Tests hand-construct ArenaMap fixtures directly via ``ArenaMap.objects.create``
(no image content needed — the tests assert on row identity / id ordering /
M2M membership, not on map_processing internals).
"""

from __future__ import annotations

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
