"""LG-02-Part2a — ``TestCase`` tests for the ``SeasonPhase`` model and the
``Season`` chokepoint methods (``ordered_phases`` / ``scheduled_fixtures``).

The seam contract is locked at
``.claude/worktrees/lg-02-part2a-seam-contract.md`` (§4 Test boundary). These
tests assert ONLY the locked public surface: model fields / defaults / choices,
``Meta.ordering``, the ``uniq_season_phase_ordinal`` UniqueConstraint, the
``season.phases`` reverse accessor + CASCADE, ``Season.ordered_phases()`` (incl.
the UNSAVED implicit-fallback phase with ``pk is None``),
``Season.scheduled_fixtures()`` (incl. the ``< 2``-team ``[]`` guard), and the
load-bearing **behaviour-equivalence** guarantee: a Season with exactly one
explicit ``round_robin`` phase behaves IDENTICALLY to an otherwise-identical
phase-less Season for ``scheduled_fixtures()`` AND
``_is_finished()``/``complete_if_finished()`` (state flip + ``champion_team``).

Tests assert schema-level outcomes (types, field values, ``pk is None``, state,
champion id) — NEVER exact simulated point totals. The behaviour-equivalence
tests use small N (2/3) with the real ``simulate_scheduled_round`` under a
patched ``ROUND_TICKS`` so the suite stays fast and deterministic-enough at the
schema level.

NOTE: collection / pass of this file requires the Code agent's ``SeasonPhase``
model + ``Season.ordered_phases`` / ``Season.scheduled_fixtures`` to land. Until
then these tests are EXPECTED to fail (import / attribute errors) — that is the
TDD red state, not a defect in this file.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.db import IntegrityError, transaction
from django.db.models import ForeignKey
from django.test import TestCase

from matches.models import League, Season, SeasonPhase
from matches.schedule_generator import ScheduleFixture
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

_FAST_TICKS = 20


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _draft_season(prefix: str, *, name: str = "S1") -> Season:
    """A bare draft Season (no teams, no phases) in a fresh League."""
    league = League.objects.create(name=f"{prefix} League")
    return Season.objects.create(league=league, name=name, start_date=date(2026, 1, 1))


def _active_season_n_teams(prefix: str, n: int) -> tuple[Season, list]:
    """A started (active) Season with ``n`` slotted teams enrolled.

    ``start_season()`` snapshots ``starting_team_ids_json`` so
    ``scheduled_fixtures()`` resolves team ids the same way the call sites do.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="S1", start_date=date(2026, 1, 1)
    )
    teams = []
    for i in range(n):
        t, _ = make_team_with_slots(f"{prefix}{i}")
        teams.append(t)
        season.teams.add(t)
    season.start_season()
    season.refresh_from_db()
    return season, teams


def _play_every_fixture(season: Season, teams: list) -> None:
    """Drive ``simulate_scheduled_round`` over every scheduled fixture.

    Resolves each fixture's two Teams by id from the enrolled set and plays
    them in the fixture's canonical ``(team_a_id, team_b_id)`` order so the
    Side-agnostic frozenset match in ``_is_finished`` lines up. Uses the
    real simulator under a small ``ROUND_TICKS`` patch.
    """
    by_id = {t.id: t for t in teams}
    fixtures = season.scheduled_fixtures()
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for fixture in fixtures:
            team_a = by_id[fixture.team_a_id]
            team_b = by_id[fixture.team_b_id]
            sim.simulate_scheduled_round(season, team_a, team_b, fixture.round_number)


# ---------------------------------------------------------------------------
# Model fields / defaults / choices
# ---------------------------------------------------------------------------


class TestSeasonPhaseFields(TestCase):
    """Locked field types / defaults / choices on ``SeasonPhase``."""

    def test_phase_type_default_is_round_robin(self) -> None:
        season = _draft_season("DefaultRR")
        phase = SeasonPhase.objects.create(season=season, ordinal=1)
        self.assertEqual(phase.phase_type, "round_robin")

    def test_ordinal_persists_as_given(self) -> None:
        season = _draft_season("Ordinal")
        phase = SeasonPhase.objects.create(season=season, ordinal=2)
        phase.refresh_from_db()
        self.assertEqual(phase.ordinal, 2)

    def test_phase_type_choices_declare_all_three_values(self) -> None:
        values = {value for value, _label in SeasonPhase.PHASE_TYPE_CHOICES}
        self.assertEqual(values, {"round_robin", "tournament", "member_night"})

    def test_phase_type_choices_pairs_exact(self) -> None:
        self.assertEqual(
            tuple(SeasonPhase.PHASE_TYPE_CHOICES),
            (
                ("round_robin", "Round-robin"),
                ("tournament", "Tournament"),
                ("member_night", "Member night"),
            ),
        )

    def test_all_three_phase_type_values_persist(self) -> None:
        season = _draft_season("AllThree")
        for ordinal, value in enumerate(
            ("round_robin", "tournament", "member_night"), start=1
        ):
            phase = SeasonPhase.objects.create(
                season=season, ordinal=ordinal, phase_type=value
            )
            phase.refresh_from_db()
            self.assertEqual(phase.phase_type, value)

    def test_season_fk_cascade_and_related_name(self) -> None:
        field = SeasonPhase._meta.get_field("season")
        self.assertIsInstance(field, ForeignKey)
        self.assertEqual(field.remote_field.related_name, "phases")
        # CASCADE on_delete.
        from django.db.models import CASCADE

        self.assertEqual(field.remote_field.on_delete, CASCADE)

    def test_str_shape(self) -> None:
        season = _draft_season("StrShape", name="Season 1")
        phase = SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        # f"{self.season} — phase {self.ordinal} ({self.phase_type})"
        self.assertEqual(str(phase), f"{season} — phase 1 (round_robin)")


# ---------------------------------------------------------------------------
# Meta.ordering
# ---------------------------------------------------------------------------


class TestSeasonPhaseOrdering(TestCase):
    """``Meta.ordering == ["ordinal"]`` — rows iterate ordinal-ascending."""

    def test_meta_ordering_is_ordinal(self) -> None:
        self.assertEqual(list(SeasonPhase._meta.ordering), ["ordinal"])

    def test_rows_iterate_in_ordinal_order_despite_insert_order(self) -> None:
        season = _draft_season("OrderIter")
        # Insert out of order: 2, 1, 3.
        SeasonPhase.objects.create(season=season, ordinal=2)
        SeasonPhase.objects.create(season=season, ordinal=1)
        SeasonPhase.objects.create(season=season, ordinal=3)
        ordinals = [p.ordinal for p in season.phases.all()]
        self.assertEqual(ordinals, [1, 2, 3])


# ---------------------------------------------------------------------------
# UniqueConstraint
# ---------------------------------------------------------------------------


class TestSeasonPhaseUniqueConstraint(TestCase):
    """``uniq_season_phase_ordinal`` on ``(season, ordinal)``."""

    def test_duplicate_season_ordinal_rejected(self) -> None:
        season = _draft_season("DupReject")
        SeasonPhase.objects.create(season=season, ordinal=1)
        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                SeasonPhase.objects.create(season=season, ordinal=1)

    def test_same_ordinal_allowed_across_seasons(self) -> None:
        season_a = _draft_season("UniqA")
        season_b = _draft_season("UniqB")
        SeasonPhase.objects.create(season=season_a, ordinal=1)
        # Same ordinal in a DIFFERENT Season must NOT collide.
        phase_b = SeasonPhase.objects.create(season=season_b, ordinal=1)
        self.assertEqual(phase_b.ordinal, 1)
        self.assertEqual(SeasonPhase.objects.filter(ordinal=1).count(), 2)

    def test_constraint_name_present(self) -> None:
        names = {c.name for c in SeasonPhase._meta.constraints}
        self.assertIn("uniq_season_phase_ordinal", names)


# ---------------------------------------------------------------------------
# Reverse accessor + CASCADE delete
# ---------------------------------------------------------------------------


class TestSeasonPhaseReverseAccessorAndCascade(TestCase):
    """``season.phases`` reverse accessor + CASCADE on Season delete."""

    def test_phases_reverse_accessor(self) -> None:
        season = _draft_season("Reverse")
        SeasonPhase.objects.create(season=season, ordinal=1)
        SeasonPhase.objects.create(season=season, ordinal=2)
        self.assertEqual(season.phases.count(), 2)

    def test_deleting_season_cascades_phases(self) -> None:
        season = _draft_season("Cascade")
        SeasonPhase.objects.create(season=season, ordinal=1)
        SeasonPhase.objects.create(season=season, ordinal=2)
        season_pk = season.pk
        season.delete()
        self.assertEqual(SeasonPhase.objects.filter(season_id=season_pk).count(), 0)


# ---------------------------------------------------------------------------
# Season.ordered_phases()
# ---------------------------------------------------------------------------


class TestOrderedPhasesExplicit(TestCase):
    """With >= 1 persisted phase, ``ordered_phases`` returns them ordinal-ordered."""

    def test_returns_explicit_phases_in_ordinal_order(self) -> None:
        season = _draft_season("ExplicitOrder")
        SeasonPhase.objects.create(season=season, ordinal=3)
        SeasonPhase.objects.create(season=season, ordinal=1)
        SeasonPhase.objects.create(season=season, ordinal=2)
        phases = season.ordered_phases()
        self.assertEqual([p.ordinal for p in phases], [1, 2, 3])
        # All are persisted rows (pk set).
        for p in phases:
            self.assertIsNotNone(p.pk)

    def test_returns_a_list(self) -> None:
        season = _draft_season("ListType")
        SeasonPhase.objects.create(season=season, ordinal=1)
        self.assertIsInstance(season.ordered_phases(), list)

    def test_single_explicit_phase_round_trips(self) -> None:
        season = _draft_season("SingleExplicit")
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        phases = season.ordered_phases()
        self.assertEqual(len(phases), 1)
        self.assertEqual(phases[0].phase_type, "round_robin")


class TestOrderedPhasesImplicitFallback(TestCase):
    """A phase-less Season returns ONE unsaved implicit ``round_robin`` phase."""

    def test_phaseless_returns_one_element(self) -> None:
        season = _draft_season("ImplicitOne")
        phases = season.ordered_phases()
        self.assertEqual(len(phases), 1)

    def test_implicit_member_is_unsaved(self) -> None:
        season = _draft_season("ImplicitUnsaved")
        phase = season.ordered_phases()[0]
        self.assertIsInstance(phase, SeasonPhase)
        self.assertIsNone(phase.pk)

    def test_implicit_member_field_values(self) -> None:
        season = _draft_season("ImplicitFields")
        phase = season.ordered_phases()[0]
        self.assertEqual(phase.phase_type, "round_robin")
        self.assertEqual(phase.ordinal, 1)
        # The implicit phase points back at this Season.
        self.assertEqual(phase.season_id, season.id)

    def test_implicit_member_not_persisted(self) -> None:
        """Building the implicit phase must NOT write a DB row."""
        season = _draft_season("ImplicitNoWrite")
        before = SeasonPhase.objects.count()
        season.ordered_phases()
        self.assertEqual(SeasonPhase.objects.count(), before)


# ---------------------------------------------------------------------------
# Season.scheduled_fixtures()
# ---------------------------------------------------------------------------


class TestScheduledFixturesGuards(TestCase):
    """``scheduled_fixtures()`` returns ``[]`` for ``< 2`` teams; never raises."""

    def test_zero_team_draft_returns_empty(self) -> None:
        season = _draft_season("ZeroTeam")
        self.assertEqual(season.scheduled_fixtures(), [])

    def test_one_team_draft_returns_empty(self) -> None:
        league = League.objects.create(name="OneTeam League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        t, _ = make_team_with_slots("Solo")
        season.teams.add(t)
        # draft (not started) ⇒ team_ids from live M2M; only 1 team ⇒ [].
        self.assertEqual(season.scheduled_fixtures(), [])

    def test_returns_list_of_schedule_fixtures(self) -> None:
        season, _teams = _active_season_n_teams("FixtureType", 2)
        fixtures = season.scheduled_fixtures()
        self.assertIsInstance(fixtures, list)
        self.assertGreater(len(fixtures), 0)
        for f in fixtures:
            self.assertIsInstance(f, ScheduleFixture)


# ---------------------------------------------------------------------------
# Behaviour equivalence — phase-less vs ONE explicit round_robin phase
# ---------------------------------------------------------------------------


class TestScheduledFixturesEquivalence(TestCase):
    """``scheduled_fixtures()`` is identical phase-less vs explicit RR phase."""

    def _build_pair(self, prefix: str, n: int):
        """Two structurally-identical active Seasons.

        Both enroll teams with the SAME ids snapshotted into
        ``starting_team_ids_json`` so ``generate_schedule`` (a pure function
        of the team-id set) produces identical fixtures. The only difference:
        ``with_phase`` carries one explicit ``round_robin`` phase row.
        """
        league = League.objects.create(name=f"{prefix} League")
        teams = [make_team_with_slots(f"{prefix}{i}")[0] for i in range(n)]
        team_ids = sorted(t.id for t in teams)

        phaseless = Season.objects.create(
            league=league,
            name="Phaseless",
            start_date=date(2026, 1, 1),
            state="active",
            starting_team_ids_json=team_ids,
        )
        phaseless.teams.add(*teams)

        league2 = League.objects.create(name=f"{prefix} League2")
        with_phase = Season.objects.create(
            league=league2,
            name="WithPhase",
            start_date=date(2026, 1, 1),
            state="active",
            starting_team_ids_json=team_ids,
        )
        with_phase.teams.add(*teams)
        SeasonPhase.objects.create(
            season=with_phase, ordinal=1, phase_type="round_robin"
        )
        return phaseless, with_phase

    def test_identical_fixtures_n2(self) -> None:
        phaseless, with_phase = self._build_pair("EqN2", 2)
        self.assertEqual(
            phaseless.scheduled_fixtures(), with_phase.scheduled_fixtures()
        )

    def test_identical_fixtures_n3(self) -> None:
        phaseless, with_phase = self._build_pair("EqN3", 3)
        self.assertEqual(
            phaseless.scheduled_fixtures(), with_phase.scheduled_fixtures()
        )

    def test_explicit_phase_fixtures_nonempty(self) -> None:
        _phaseless, with_phase = self._build_pair("EqNonEmpty", 2)
        self.assertGreater(len(with_phase.scheduled_fixtures()), 0)


class TestCompletionEquivalence(TestCase):
    """``_is_finished`` / ``complete_if_finished`` identical across the two shapes.

    Plays every fixture of an N=2 Season for both a phase-less Season and an
    otherwise-identical Season carrying one explicit ``round_robin`` phase,
    then asserts BOTH auto-complete (state flip) and stamp a champion — and
    that the same Team wins both (the schedule + completion math is a pure
    function of the team-id set, which is identical across the two).
    """

    def _started_phaseless_season(self, prefix: str, n: int):
        """A single active, phase-less Season with ``n`` slotted teams.

        ``start_season()`` snapshots ``starting_team_ids_json`` and creates NO
        SeasonPhase rows — the legacy / phase-less shape.
        """
        league = League.objects.create(name=f"{prefix} League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        teams = [make_team_with_slots(f"{prefix}{i}")[0] for i in range(n)]
        for t in teams:
            season.teams.add(t)
        season.start_season()
        season.refresh_from_db()
        return season, teams

    def test_is_finished_false_before_play_both_shapes(self) -> None:
        """Before any fixture is played, ``_is_finished`` is False under BOTH
        the phase-less and the explicit-RR-phase shapes of the SAME Season —
        the chokepoint fallback never spuriously reports finished."""
        season, _teams = self._started_phaseless_season("FinPre", 2)
        # Phase-less.
        self.assertFalse(season._is_finished())
        # Add ONE explicit round_robin phase to the SAME Season.
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.refresh_from_db()
        self.assertFalse(season._is_finished())

    def test_phaseless_season_auto_completes_and_stamps_champion(self) -> None:
        """A phase-less Season (implicit-fallback chokepoint) plays every
        fixture, auto-flips to completed, and stamps a champion — the LG-01
        behaviour, preserved verbatim by the fallback."""
        season, teams = self._started_phaseless_season("FinPhaseless", 2)
        self.assertEqual(season.phases.count(), 0)  # genuinely phase-less

        _play_every_fixture(season, teams)
        season.refresh_from_db()

        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)

    def test_explicit_rr_phase_season_auto_completes_and_stamps_champion(
        self,
    ) -> None:
        """A Season carrying one explicit ``round_robin`` phase plays every
        fixture and auto-completes + stamps a champion exactly as the
        phase-less Season does — the explicit path routes through the same
        chokepoint."""
        season, teams = self._started_phaseless_season("FinExplicit", 2)
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.refresh_from_db()
        self.assertEqual(season.phases.count(), 1)

        _play_every_fixture(season, teams)
        season.refresh_from_db()

        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)

    def test_completion_outcome_identical_when_phase_added_to_played_season(
        self,
    ) -> None:
        """The load-bearing equivalence, pinned DETERMINISTICALLY against a
        SINGLE played state.

        Play a phase-less Season to completion (recording the champion the
        chokepoint's implicit fallback stamped), then add an explicit
        ``round_robin`` phase to that SAME Season and re-run
        ``complete_if_finished()``. The explicit-phase chokepoint path reads
        the identical played state, so the completed state and the
        ``champion_team`` must be UNCHANGED — proving the two shapes produce
        the identical ``_is_finished`` / ``complete_if_finished`` outcome over
        one fixed set of GameRounds (NOT two independent, separately-seeded
        simulations, which could legitimately crown different teams).
        """
        season, teams = self._started_phaseless_season("FinEquiv", 2)

        # Play the phase-less Season fully (auto-completes via the fallback).
        _play_every_fixture(season, teams)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        champ_before = season.champion_team_id
        self.assertIsNotNone(champ_before)

        # Now attach an explicit round_robin phase to the SAME (already-played)
        # Season and exercise the chokepoint again. _is_finished must still be
        # True and complete_if_finished must be a no-op leaving the SAME
        # champion — the explicit path sees the identical fixture list + played
        # rounds the fallback did.
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.refresh_from_db()
        self.assertTrue(season._is_finished())
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, champ_before)

    def test_complete_if_finished_idempotent_both_shapes(self) -> None:
        season, teams = self._started_phaseless_season("FinIdem", 2)
        _play_every_fixture(season, teams)
        season.refresh_from_db()
        champ = season.champion_team_id

        # Phase-less: re-calling complete_if_finished is a no-op.
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, champ)

        # Add an explicit phase: still idempotent, same champion.
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.refresh_from_db()
        season.complete_if_finished()
        season.refresh_from_db()

        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, champ)
