"""CONF-01 — Django ``TestCase`` tests for the ``Conference`` partition model
and the ``Season`` Conference seams (SUB-01 piece 3, slice 1).

The seam contract is locked at ``.claude/worktrees/conf-01-seam-contract.md``;
the design rationale is [ADR-0034](../../docs/adr/0034-conference-partition.md)
and the CONTEXT.md **Conference** term.

These tests assert ONLY the locked public surface — model fields / defaults /
``__str__`` / the ``uniq_season_conference_ordinal`` constraint /
``Meta.ordering`` / CASCADE-on-Season-delete / the ``season.conferences`` +
``team.conferences`` reverse accessors; the ``Season`` Conference helpers
(``ordered_conferences`` / ``_scheduled_conference_partitions`` /
``conference_by_team_id``); the **parallel-overlay scheduling** (each
Conference's round-robin on the SAME matchday numbers, phase span == largest
Conference's span, flat ``scheduled_fixtures()`` == the union, zero-Conference
byte-identical to a flat Season); ``start_season`` snapshots each Conference;
per-Conference completion (the RR phase completes only when BOTH Conferences'
round-robins are played); and ``_stamp_champion_for_final_phase`` (NULL champion
for ``>= 2`` Conferences but the Season still flips ``state="completed"``;
``compute_standings[0]`` for ``0/1`` Conference).

Tests assert schema-level outcomes (types, field values, ids, fixture
lists / matchday numbers, completion booleans, champion null-vs-id) — NEVER
exact simulated point totals. Completion / champion tests drive the REAL
``simulate_scheduled_round`` under a patched ``ROUND_TICKS`` so the suite stays
fast and deterministic-enough at the schema level.

NOTE: collection / pass of this file requires the Code agent's ``Conference``
model + ``Match.conference`` FK + the ``Season`` Conference helpers + the
parallel-overlay ``scheduled_fixtures_by_phase`` change to land. Until then
these tests are EXPECTED to fail (import / attribute errors) — the TDD red
state, not a defect in this file.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.db.models import CASCADE, ForeignKey
from django.test import TestCase

from matches.models import (
    Conference,
    GameRound,
    League,
    Match,
    Season,
    SeasonPhase,
)
from matches.schedule_generator import generate_schedule
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draft_season(prefix: str, *, name: str = "S1") -> Season:
    """A bare draft Season (no teams, no conferences) in a fresh League."""
    league = League.objects.create(name=f"{prefix} League")
    return Season.objects.create(league=league, name=name, start_date=date(2026, 1, 1))


def _draft_conf_season(prefix: str, sizes: list[int]):
    """A draft Season with ``len(sizes)`` Conferences.

    Conference ``i+1`` (ordinal ``i+1``) enrolls ``sizes[i]`` fully-slotted
    Teams, each also added to ``season.teams`` (the enrolled set). Returns
    ``(season, [conference, ...], [[team, ...], ...])``.
    """
    season = _draft_season(prefix)
    confs: list[Conference] = []
    groups: list[list] = []
    for ci, size in enumerate(sizes, start=1):
        conf = Conference.objects.create(
            season=season, name=f"{prefix} Conf {ci}", ordinal=ci
        )
        teams = []
        for ti in range(size):
            team, _ = make_team_with_slots(f"{prefix}{ci}x{ti}")
            teams.append(team)
            season.teams.add(team)
            conf.teams.add(team)
        confs.append(conf)
        groups.append(teams)
    return season, confs, groups


def _active_conf_season(prefix: str, sizes: list[int]):
    """An ``_draft_conf_season`` that has been ``start_season()``-ed so each
    Conference's ``starting_team_ids_json`` snapshot is written."""
    season, confs, groups = _draft_conf_season(prefix, sizes)
    season.start_season()
    season.refresh_from_db()
    for conf in confs:
        conf.refresh_from_db()
    return season, confs, groups


def _active_plain_season(prefix: str, n: int):
    """A started (active) Season with ``n`` slotted teams and ZERO
    Conferences (the byte-identical-to-today flat shape)."""
    season = _draft_season(prefix)
    teams = []
    for i in range(n):
        team, _ = make_team_with_slots(f"{prefix}p{i}")
        teams.append(team)
        season.teams.add(team)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _conference_team_ids(conf: Conference) -> set[int]:
    """The team ids of a Conference — snapshot when activated, else live M2M."""
    snapshot = conf.starting_team_ids_json
    if snapshot:
        return set(snapshot)
    return {t.id for t in conf.teams.all()}


def _play_conference_fixtures(sim, season, fixtures, conf, teams_by_id, phase) -> None:
    """Drive the REAL ``simulate_scheduled_round`` over exactly the fixtures
    of ``conf`` (intra-Conference: both teams in the Conference's id set).

    Iterates in fixture order so a pairing's round 1 (lower matchday) is
    played before its round 2 (higher matchday). Each Round is tagged with
    ``conference=conf``; the phase-less / implicit phase is coerced to None.
    """
    conf_ids = _conference_team_ids(conf)
    season_phase = phase if phase.pk is not None else None
    for fixture in fixtures:
        if {fixture.team_a_id, fixture.team_b_id} <= conf_ids:
            sim.simulate_scheduled_round(
                season,
                teams_by_id[fixture.team_a_id],
                teams_by_id[fixture.team_b_id],
                fixture.round_number,
                season_phase=season_phase,
                conference=conf,
            )


# ===========================================================================
# Model — fields / defaults / __str__ / constraint / ordering / cascade
# ===========================================================================


class TestConferenceFields(TestCase):
    """Locked field types / defaults / reverse accessors on ``Conference``."""

    def test_season_fk_cascade_and_related_name(self) -> None:
        field = Conference._meta.get_field("season")
        self.assertIsInstance(field, ForeignKey)
        self.assertEqual(field.remote_field.related_name, "conferences")
        self.assertEqual(field.remote_field.on_delete, CASCADE)

    def test_teams_m2m_related_name_is_conferences(self) -> None:
        field = Conference._meta.get_field("teams")
        self.assertEqual(field.remote_field.related_name, "conferences")

    def test_starting_team_ids_json_defaults_none(self) -> None:
        season = _draft_season("DefSnap")
        conf = Conference.objects.create(season=season, name="California", ordinal=1)
        conf.refresh_from_db()
        self.assertIsNone(conf.starting_team_ids_json)

    def test_starting_team_ids_json_field_nullable(self) -> None:
        field = Conference._meta.get_field("starting_team_ids_json")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_ordinal_persists_as_given(self) -> None:
        season = _draft_season("Ord")
        conf = Conference.objects.create(season=season, name="Nevada", ordinal=2)
        conf.refresh_from_db()
        self.assertEqual(conf.ordinal, 2)

    def test_name_persists_as_given(self) -> None:
        season = _draft_season("Name")
        conf = Conference.objects.create(season=season, name="California", ordinal=1)
        conf.refresh_from_db()
        self.assertEqual(conf.name, "California")


class TestConferenceStr(TestCase):
    """``__str__`` is ``f"{season} — {name}"`` (em-dash U+2014)."""

    def test_str_shape_exact(self) -> None:
        season = _draft_season("Str", name="Season 1")
        conf = Conference.objects.create(season=season, name="California", ordinal=1)
        self.assertEqual(str(conf), f"{season} — California")

    def test_str_contains_em_dash(self) -> None:
        season = _draft_season("Dash")
        conf = Conference.objects.create(season=season, name="Nevada", ordinal=1)
        self.assertIn("—", str(conf))


class TestConferenceUniqueConstraint(TestCase):
    """``uniq_season_conference_ordinal`` on ``(season, ordinal)``."""

    def test_duplicate_season_ordinal_rejected(self) -> None:
        season = _draft_season("DupReject")
        Conference.objects.create(season=season, name="A", ordinal=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                Conference.objects.create(season=season, name="B", ordinal=1)

    def test_same_ordinal_allowed_across_seasons(self) -> None:
        season_a = _draft_season("UniqA")
        season_b = _draft_season("UniqB")
        Conference.objects.create(season=season_a, name="A", ordinal=1)
        conf_b = Conference.objects.create(season=season_b, name="B", ordinal=1)
        self.assertEqual(conf_b.ordinal, 1)
        self.assertEqual(Conference.objects.filter(ordinal=1).count(), 2)

    def test_constraint_name_present(self) -> None:
        names = {c.name for c in Conference._meta.constraints}
        self.assertIn("uniq_season_conference_ordinal", names)


class TestConferenceOrdering(TestCase):
    """``Meta.ordering == ["ordinal"]`` — rows iterate ordinal-ascending."""

    def test_meta_ordering_is_ordinal(self) -> None:
        self.assertEqual(list(Conference._meta.ordering), ["ordinal"])

    def test_rows_iterate_in_ordinal_order_despite_insert_order(self) -> None:
        season = _draft_season("OrderIter")
        Conference.objects.create(season=season, name="B", ordinal=2)
        Conference.objects.create(season=season, name="A", ordinal=1)
        Conference.objects.create(season=season, name="C", ordinal=3)
        self.assertEqual([c.ordinal for c in season.conferences.all()], [1, 2, 3])


class TestConferenceReverseAccessorsAndCascade(TestCase):
    """``season.conferences`` / ``team.conferences`` accessors + CASCADE."""

    def test_season_conferences_reverse_accessor(self) -> None:
        season = _draft_season("RevS")
        Conference.objects.create(season=season, name="A", ordinal=1)
        Conference.objects.create(season=season, name="B", ordinal=2)
        self.assertEqual(season.conferences.count(), 2)

    def test_team_conferences_reverse_accessor(self) -> None:
        season = _draft_season("RevT")
        conf = Conference.objects.create(season=season, name="A", ordinal=1)
        team, _ = make_team_with_slots("RevTteam")
        season.teams.add(team)
        conf.teams.add(team)
        self.assertEqual(team.conferences.count(), 1)
        self.assertEqual(team.conferences.get().id, conf.id)

    def test_deleting_season_cascades_conferences(self) -> None:
        season = _draft_season("Cascade")
        Conference.objects.create(season=season, name="A", ordinal=1)
        Conference.objects.create(season=season, name="B", ordinal=2)
        season_pk = season.pk
        season.delete()
        self.assertEqual(Conference.objects.filter(season_id=season_pk).count(), 0)


# ===========================================================================
# Season.ordered_conferences()
# ===========================================================================


class TestOrderedConferences(TestCase):
    """``Season.ordered_conferences()`` — ordinal order; ``[]`` for none."""

    def test_empty_for_zero_conferences(self) -> None:
        season = _draft_season("OCEmpty")
        self.assertEqual(season.ordered_conferences(), [])

    def test_returns_list(self) -> None:
        season = _draft_season("OCList")
        Conference.objects.create(season=season, name="A", ordinal=1)
        self.assertIsInstance(season.ordered_conferences(), list)

    def test_ordinal_order(self) -> None:
        season = _draft_season("OCOrder")
        Conference.objects.create(season=season, name="C", ordinal=3)
        Conference.objects.create(season=season, name="A", ordinal=1)
        Conference.objects.create(season=season, name="B", ordinal=2)
        confs = season.ordered_conferences()
        self.assertEqual([c.ordinal for c in confs], [1, 2, 3])


# ===========================================================================
# Season._scheduled_conference_partitions()
# ===========================================================================


class TestScheduledConferencePartitions(TestCase):
    """``_scheduled_conference_partitions`` — ``(Conference | None, ids)``."""

    def test_zero_conferences_one_implicit_all_teams_partition(self) -> None:
        season, _teams = _active_plain_season("PartZero", 3)
        parts = season._scheduled_conference_partitions()
        self.assertEqual(len(parts), 1)
        self.assertIsNone(parts[0][0])
        self.assertEqual(parts[0][1], season._scheduled_team_ids())

    def test_draft_partitions_use_live_m2m_intersection(self) -> None:
        season, confs, groups = _draft_conf_season("PartDraft", [2, 2])
        parts = season._scheduled_conference_partitions()
        self.assertEqual([p[0].id for p in parts], [confs[0].id, confs[1].id])
        self.assertEqual(parts[0][1], sorted(t.id for t in groups[0]))
        self.assertEqual(parts[1][1], sorted(t.id for t in groups[1]))

    def test_active_partitions_use_snapshot(self) -> None:
        season, confs, groups = _active_conf_season("PartActive", [2, 2])
        parts = season._scheduled_conference_partitions()
        self.assertEqual(parts[0][1], list(confs[0].starting_team_ids_json or []))
        self.assertEqual(parts[1][1], list(confs[1].starting_team_ids_json or []))

    def test_active_partitions_ignore_post_snapshot_m2m_mutation(self) -> None:
        """The activation snapshot is frozen — adding a team to a
        Conference's live M2M after ``start_season`` must NOT change its
        scheduled partition (mirrors the ``starting_team_ids_json``
        snapshot determinism)."""
        season, confs, groups = _active_conf_season("PartFrozen", [2, 2])
        snapshot_before = list(confs[0].starting_team_ids_json or [])
        stray, _ = make_team_with_slots("PartStray")
        confs[0].teams.add(stray)
        parts = season._scheduled_conference_partitions()
        self.assertEqual(parts[0][1], snapshot_before)
        self.assertNotIn(stray.id, parts[0][1])


# ===========================================================================
# Season.conference_by_team_id()
# ===========================================================================


class TestConferenceByTeamId(TestCase):
    """``conference_by_team_id`` — ``{team_id: Conference}``; ``{}`` for none."""

    def test_empty_for_zero_conferences(self) -> None:
        season, _teams = _active_plain_season("CbtZero", 2)
        self.assertEqual(season.conference_by_team_id(), {})

    def test_draft_maps_every_team_to_its_conference(self) -> None:
        season, confs, groups = _draft_conf_season("CbtDraft", [2, 3])
        mapping = season.conference_by_team_id()
        self.assertEqual(len(mapping), 5)
        for team in groups[0]:
            self.assertEqual(mapping[team.id].id, confs[0].id)
        for team in groups[1]:
            self.assertEqual(mapping[team.id].id, confs[1].id)

    def test_active_maps_from_snapshot(self) -> None:
        season, confs, groups = _active_conf_season("CbtActive", [2, 2])
        mapping = season.conference_by_team_id()
        for team in groups[0]:
            self.assertEqual(mapping[team.id].id, confs[0].id)
        for team in groups[1]:
            self.assertEqual(mapping[team.id].id, confs[1].id)


# ===========================================================================
# Parallel-overlay scheduling
# ===========================================================================


class TestParallelOverlayScheduling(TestCase):
    """Each Conference's round-robin is overlaid on the SAME matchday
    calendar; the phase span == the largest Conference's span; flat
    ``scheduled_fixtures()`` is the union; zero-Conference is byte-identical
    to a flat Season."""

    def _partition_fixtures(self, fixtures, group):
        ids = {t.id for t in group}
        return [f for f in fixtures if {f.team_a_id, f.team_b_id} <= ids]

    def test_two_equal_conferences_share_matchday_numbers(self) -> None:
        season, _confs, groups = _draft_conf_season("Overlay", [2, 2])
        by_phase = season.scheduled_fixtures_by_phase()
        # One implicit round_robin phase carries both Conferences' overlaid RRs.
        self.assertEqual(len(by_phase), 1)
        _phase, fixtures = by_phase[0]

        c1 = self._partition_fixtures(fixtures, groups[0])
        c2 = self._partition_fixtures(fixtures, groups[1])
        # Intra-Conference only: every fixture belongs to exactly one group.
        self.assertEqual(len(c1) + len(c2), len(fixtures))
        # A 2-team round-robin = 2 fixtures (round 1 + round 2 mirror).
        self.assertEqual(len(c1), 2)
        self.assertEqual(len(c2), 2)
        # The SAME matchday numbers coexist — Conf 1 MD{1,2} and Conf 2 MD{1,2}.
        self.assertEqual({f.matchday for f in c1}, {1, 2})
        self.assertEqual({f.matchday for f in c2}, {1, 2})

    def test_phase_span_equals_largest_conference_span(self) -> None:
        # Conf 1: 4 teams (RR span 6 matchdays); Conf 2: 2 teams (span 2).
        season, _confs, groups = _draft_conf_season("Span", [4, 2])
        fixtures = season.scheduled_fixtures()
        big = self._partition_fixtures(fixtures, groups[0])
        small = self._partition_fixtures(fixtures, groups[1])
        self.assertEqual(max(f.matchday for f in big), 6)
        self.assertEqual(max(f.matchday for f in small), 2)
        # The phase span == the largest Conference's span (overlay starts both
        # Conferences at matchday 1, so the flat max IS the largest span).
        self.assertEqual(max(f.matchday for f in fixtures), 6)

    def test_phase_span_offsets_the_next_rr_phase(self) -> None:
        """With two explicit round_robin phases, RR2 is offset by RR1's
        LARGEST-Conference span (the parallel-overlay rule applied across
        phases)."""
        season, _confs, _groups = _draft_conf_season("XPhase", [4, 2])
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
        by_phase = season.scheduled_fixtures_by_phase()
        self.assertEqual(len(by_phase), 2)
        (_p1, f1), (_p2, f2) = by_phase
        # RR1 spans matchdays 1..6 (Conf 1's span).
        self.assertEqual(max(f.matchday for f in f1), 6)
        # RR2 is offset by 6 ⇒ its first matchday is 7.
        self.assertEqual(min(f.matchday for f in f2), 7)

    def test_flat_scheduled_fixtures_is_the_union(self) -> None:
        season, _confs, _groups = _draft_conf_season("Union", [2, 2])
        concat = []
        for _phase, fixtures in season.scheduled_fixtures_by_phase():
            concat.extend(fixtures)
        self.assertEqual(season.scheduled_fixtures(), concat)

    def test_zero_conference_byte_identical_to_flat_season(self) -> None:
        season, _teams = _active_plain_season("FlatEq", 4)
        team_ids = list(season.starting_team_ids_json or [])
        expected = generate_schedule(team_ids, "single_round_robin")
        self.assertEqual(season.scheduled_fixtures(), expected)


# ===========================================================================
# start_season() snapshots each Conference
# ===========================================================================


class TestStartSeasonConferenceSnapshot(TestCase):
    """``start_season`` writes each Conference's ``starting_team_ids_json``
    (sorted ascending)."""

    def test_snapshots_each_conference_sorted_ascending(self) -> None:
        season, confs, groups = _active_conf_season("Snap", [2, 3])
        for conf, group in zip(confs, groups):
            conf.refresh_from_db()
            self.assertEqual(
                conf.starting_team_ids_json,
                sorted(t.id for t in group),
            )

    def test_snapshot_is_sorted_even_when_added_out_of_order(self) -> None:
        season = _draft_season("SnapSort")
        conf = Conference.objects.create(season=season, name="A", ordinal=1)
        teams = [make_team_with_slots(f"SnapSort{i}")[0] for i in range(3)]
        # Add to the Conference in reverse-id order; the snapshot must sort.
        for team in reversed(teams):
            season.teams.add(team)
            conf.teams.add(team)
        season.start_season()
        conf.refresh_from_db()
        self.assertEqual(conf.starting_team_ids_json, sorted(t.id for t in teams))


# ===========================================================================
# Per-Conference completion
# ===========================================================================


class TestPerConferenceCompletion(TestCase):
    """A 2-Conference RR Season finishes only after BOTH Conferences'
    round-robins are played — playing one Conference's fixtures leaves the
    Season unfinished."""

    def test_one_conference_played_not_finished(self) -> None:
        season, confs, groups = _active_conf_season("CompOne", [2, 2])
        teams_by_id = {t.id: t for grp in groups for t in grp}
        phase, fixtures = season.scheduled_fixtures_by_phase()[0]
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            _play_conference_fixtures(
                sim, season, fixtures, confs[0], teams_by_id, phase
            )
        season.refresh_from_db()
        self.assertFalse(season._is_finished())
        self.assertEqual(season.state, "active")

    def test_both_conferences_played_finished(self) -> None:
        season, confs, groups = _active_conf_season("CompBoth", [2, 2])
        teams_by_id = {t.id: t for grp in groups for t in grp}
        phase, fixtures = season.scheduled_fixtures_by_phase()[0]
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            _play_conference_fixtures(
                sim, season, fixtures, confs[0], teams_by_id, phase
            )
            _play_conference_fixtures(
                sim, season, fixtures, confs[1], teams_by_id, phase
            )
        season.refresh_from_db()
        self.assertTrue(season._is_finished())
        self.assertEqual(season.state, "completed")


# ===========================================================================
# _stamp_champion_for_final_phase — champion null-vs-id by Conference count
# ===========================================================================


class TestStampChampionByConferenceCount(TestCase):
    """A ``>= 2``-Conference RR-final Season flips ``state="completed"`` with
    ``champion_team`` NULL; a ``0/1``-Conference Season crowns
    ``compute_standings(...)[0]`` exactly as today."""

    def _play_all(self, season, confs, groups):
        teams_by_id = {t.id: t for grp in groups for t in grp}
        phase, fixtures = season.scheduled_fixtures_by_phase()[0]
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for conf in confs:
                _play_conference_fixtures(
                    sim, season, fixtures, conf, teams_by_id, phase
                )
        season.refresh_from_db()

    def test_two_conferences_complete_with_null_champion(self) -> None:
        season, confs, groups = _active_conf_season("ChampTwo", [2, 2])
        self._play_all(season, confs, groups)
        self.assertEqual(season.state, "completed")
        self.assertIsNone(season.champion_team_id)

    def test_one_conference_crowns_standings_leader(self) -> None:
        season, confs, groups = _active_conf_season("ChampOne", [2])
        self._play_all(season, confs, groups)
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)
        rows = season._final_standings_for_phase(season.ordered_phases()[-1])
        self.assertEqual(season.champion_team_id, rows[0].team_id)

    def test_zero_conference_crowns_standings_leader(self) -> None:
        season, teams = _active_plain_season("ChampZero", 2)
        teams_by_id = {t.id: t for t in teams}
        phase, fixtures = season.scheduled_fixtures_by_phase()[0]
        sim = BatchSimulator()
        season_phase = phase if phase.pk is not None else None
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for fixture in fixtures:
                sim.simulate_scheduled_round(
                    season,
                    teams_by_id[fixture.team_a_id],
                    teams_by_id[fixture.team_b_id],
                    fixture.round_number,
                    season_phase=season_phase,
                )
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)
        rows = season._final_standings_for_phase(season.ordered_phases()[-1])
        self.assertEqual(season.champion_team_id, rows[0].team_id)
