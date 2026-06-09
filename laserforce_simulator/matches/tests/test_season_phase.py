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
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        # Mirror the production play loop: tag each Match with its owning RR
        # phase (LG-02-Part2c-2 per-phase completion scopes by season_phase;
        # the implicit/phase-less fallback has pk is None -> untagged).
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                team_a = by_id[fixture.team_a_id]
                team_b = by_id[fixture.team_b_id]
                sim.simulate_scheduled_round(
                    season,
                    team_a,
                    team_b,
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


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


# ===========================================================================
# LG-02-Part2b — the two NEW dormant ``SeasonPhase`` columns
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2b-seam-contract.md`` §1:
# ``SeasonPhase`` gains two fields appended after ``phase_type`` —
#   schedule_format = CharField(max_length=32, null=True, blank=True)
#   tournament = ForeignKey("matches.Tournament", null=True, blank=True,
#                           on_delete=SET_NULL, related_name="season_phases")
# Both are DORMANT this slice (nothing reads them); ``tournament`` is ALWAYS
# NULL in Part2b (the embed is Part2c). These tests pin the schema-level
# behaviour: ``schedule_format`` accepts a value and is None-able; the
# ``tournament`` FK is nullable, SET_NULL on delete, and reverse-accessible via
# ``tournament.season_phases``.
#
# Appended as NEW classes; no existing class above is modified.


from django.db.models import SET_NULL as _LG02_SET_NULL  # noqa: E402

from matches.models import Tournament as _Lg02Tournament  # noqa: E402


def _lg02_min_tournament(name: str = "T") -> _Lg02Tournament:
    """A minimal ``setup``-state Tournament (no participants needed for the
    FK-behaviour tests — the bracket is only built on lock)."""
    return _Lg02Tournament.objects.create(name=name)


class TestSeasonPhaseScheduleFormatField(TestCase):
    """LG-02-Part2b — ``SeasonPhase.schedule_format`` (nullable CharField)."""

    def test_schedule_format_accepts_a_value(self) -> None:
        season = _draft_season("Lg02SF")
        phase = SeasonPhase.objects.create(
            season=season,
            ordinal=1,
            phase_type="round_robin",
            schedule_format="single_round_robin",
        )
        phase.refresh_from_db()
        self.assertEqual(phase.schedule_format, "single_round_robin")

    def test_schedule_format_is_nullable(self) -> None:
        # A tournament-type phase persists schedule_format=None.
        season = _draft_season("Lg02SFNull")
        phase = SeasonPhase.objects.create(
            season=season,
            ordinal=1,
            phase_type="tournament",
            schedule_format=None,
        )
        phase.refresh_from_db()
        self.assertIsNone(phase.schedule_format)

    def test_schedule_format_field_declared_null_and_blank(self) -> None:
        field = SeasonPhase._meta.get_field("schedule_format")
        self.assertTrue(field.null)
        self.assertTrue(field.blank)
        self.assertEqual(field.max_length, 32)


class TestSeasonPhaseTournamentFK(TestCase):
    """LG-02-Part2b — ``SeasonPhase.tournament`` (nullable FK, SET_NULL,
    related_name ``season_phases``). ALWAYS NULL in production this slice;
    these tests exercise the schema only."""

    def test_tournament_fk_is_nullable(self) -> None:
        season = _draft_season("Lg02FKNull")
        phase = SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        phase.refresh_from_db()
        self.assertIsNone(phase.tournament_id)

    def test_tournament_fk_field_declared_null_and_blank(self) -> None:
        field = SeasonPhase._meta.get_field("tournament")
        self.assertIsInstance(field, ForeignKey)
        self.assertTrue(field.null)
        self.assertTrue(field.blank)

    def test_tournament_fk_on_delete_is_set_null(self) -> None:
        field = SeasonPhase._meta.get_field("tournament")
        self.assertEqual(field.remote_field.on_delete, _LG02_SET_NULL)

    def test_tournament_fk_related_name_is_season_phases(self) -> None:
        field = SeasonPhase._meta.get_field("tournament")
        self.assertEqual(field.remote_field.related_name, "season_phases")

    def test_phase_can_reference_a_tournament(self) -> None:
        season = _draft_season("Lg02FKRef")
        tournament = _lg02_min_tournament("Lg02RefT")
        phase = SeasonPhase.objects.create(
            season=season,
            ordinal=1,
            phase_type="tournament",
            tournament=tournament,
        )
        phase.refresh_from_db()
        self.assertEqual(phase.tournament_id, tournament.id)

    def test_reverse_accessor_season_phases(self) -> None:
        season = _draft_season("Lg02Reverse")
        tournament = _lg02_min_tournament("Lg02RevT")
        SeasonPhase.objects.create(
            season=season,
            ordinal=1,
            phase_type="tournament",
            tournament=tournament,
        )
        self.assertEqual(tournament.season_phases.count(), 1)
        self.assertEqual(tournament.season_phases.get().season_id, season.id)

    def test_deleting_tournament_nulls_fk_without_deleting_phase(self) -> None:
        season = _draft_season("Lg02DelSetNull")
        tournament = _lg02_min_tournament("Lg02DelT")
        phase = SeasonPhase.objects.create(
            season=season,
            ordinal=1,
            phase_type="tournament",
            tournament=tournament,
        )
        phase_pk = phase.pk
        tournament.delete()
        # The phase row survives; its FK is nulled (SET_NULL).
        phase = SeasonPhase.objects.get(pk=phase_pk)
        self.assertIsNone(phase.tournament_id)


# ---------------------------------------------------------------------------
# LG-02-Part2c-3b — SeasonPhase.tournament_mode (dormant CharField)
# ---------------------------------------------------------------------------
#
# Seam contract ``.claude/worktrees/lg-02-part2c-3b-seam-contract.md``:
#   tournament_mode = CharField(max_length=16, choices=TOURNAMENT_MODE_CHOICES,
#                               default="standings")
# All four values declared now (member_night precedent); DORMANT this slice
# (nothing branches on it — the composer only ever writes "standings").


class TestSeasonPhaseTournamentModeField(TestCase):
    """LG-02-Part2c-3b — locked field type / default / choices."""

    def test_tournament_mode_default_is_standings(self) -> None:
        season = _draft_season("ModeDefault")
        phase = SeasonPhase.objects.create(season=season, ordinal=1)
        self.assertEqual(phase.tournament_mode, "standings")

    def test_tournament_mode_choices_declare_all_four_values(self) -> None:
        values = {value for value, _label in SeasonPhase.TOURNAMENT_MODE_CHOICES}
        self.assertEqual(values, {"standings", "strength", "unseeded", "random_draw"})

    def test_tournament_mode_choices_pairs_exact(self) -> None:
        self.assertEqual(
            tuple(SeasonPhase.TOURNAMENT_MODE_CHOICES),
            (
                ("standings", "Season-ending: from Standings"),
                ("strength", "Mid-season: by team strength"),
                ("unseeded", "Mid-season: random seed"),
                ("random_draw", "Mid-season: drawn pool -> RR->DE"),
            ),
        )

    def test_tournament_mode_max_length_is_16(self) -> None:
        field = SeasonPhase._meta.get_field("tournament_mode")
        self.assertEqual(field.max_length, 16)

    def test_all_four_tournament_mode_values_persist(self) -> None:
        season = _draft_season("ModeAllFour")
        for ordinal, value in enumerate(
            ("standings", "strength", "unseeded", "random_draw"), start=1
        ):
            phase = SeasonPhase.objects.create(
                season=season,
                ordinal=ordinal,
                phase_type="tournament",
                tournament_mode=value,
            )
            phase.refresh_from_db()
            self.assertEqual(phase.tournament_mode, value)


# ===========================================================================
# LG-02-Part2c-1 — Season cursor + completion derivation + auto-build
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-1-seam-contract.md`` §1 + §8:
#   - Season.current_phase() -> first INCOMPLETE phase / None when all complete.
#   - Season._phase_complete(phase) -> RR via _is_finished(); tournament via
#     tournament_id is not None AND tournament.state == "completed".
#   - Season.activate_pending_tournament_phase() — no-op until the RR phase is
#     complete, then builds a Tournament with one TournamentParticipant per
#     season team seeded by standings rank (seed == rank, rank 1 -> seed 1),
#     wires phase.tournament, leaves it state="active" (locked+built), IDEMPOTENT.
#   - Season.complete_if_finished() stamps champion == phase.tournament.champion
#     when the final phase is a completed tournament; a single-RR-phase Season
#     stays byte-identical (champion == standings[0]) as a regression.
#
# Tests assert SCHEMA-LEVEL outcomes (phase advanced, participant count + seeds,
# champion identity, state) — NEVER exact simulated point totals. RR→tournament
# fixtures use N=4 small seeded sims.
#
# Appended as NEW classes; no existing class above is modified.

from matches.models import (  # noqa: E402
    BracketNode as _Lg02c1BracketNode,
)
from matches.models import (  # noqa: E402
    TournamentParticipant as _Lg02c1Participant,
)
from matches.standings import (
    compute_standings as _lg02c1_compute_standings,
)  # noqa: E402


def _lg02c1_rr_tournament_season(prefix: str, n: int = 4):
    """An active Season with an ordinal-1 ``round_robin`` + ordinal-2
    ``tournament`` SeasonPhase, ``n`` slotted teams enrolled, started.

    Returns ``(season, teams, rr_phase, tournament_phase)``.
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
    rr_phase = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    tournament_phase = SeasonPhase.objects.create(
        season=season, ordinal=2, phase_type="tournament"
    )
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr_phase, tournament_phase


def _lg02c1_play_rr(season: Season, teams: list) -> None:
    """Play every RR fixture of ``season`` via the real simulator under a small
    ROUND_TICKS patch.

    NOTE: ``scheduled_fixtures()`` is the Part2a RR-scoped chokepoint, so this
    plays only the round-robin phase's fixtures (the tournament phase is not in
    the fixture list).
    """
    by_id = {t.id: t for t in teams}
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        # Mirror the production play loop: tag each Match with its owning RR
        # phase (LG-02-Part2c-2 per-phase completion scopes by season_phase;
        # the implicit/phase-less fallback has pk is None -> untagged).
        for phase, fixtures in season.scheduled_fixtures_by_phase():
            for fixture in fixtures:
                team_a = by_id[fixture.team_a_id]
                team_b = by_id[fixture.team_b_id]
                sim.simulate_scheduled_round(
                    season,
                    team_a,
                    team_b,
                    fixture.round_number,
                    season_phase=phase if phase.pk is not None else None,
                )


def _lg02c1_drain_tournament(tournament) -> None:
    """Drain a built tournament bracket to a champion via the real engine."""
    from matches.tournament_engine import play_next_node

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        # Bound the loop generously; a 4-team single-elim drains in a few nodes.
        for _ in range(200):
            if play_next_node(tournament) is None:
                break
    tournament.refresh_from_db()


class TestCurrentPhase(TestCase):
    """``Season.current_phase()`` — first INCOMPLETE phase / None."""

    def test_returns_rr_phase_while_rr_unplayed(self) -> None:
        season, _teams, rr_phase, _t = _lg02c1_rr_tournament_season("CPUnplayed")
        current = season.current_phase()
        self.assertIsNotNone(current)
        self.assertEqual(current.pk, rr_phase.pk)
        self.assertEqual(current.phase_type, "round_robin")

    def test_returns_tournament_phase_once_rr_complete_and_built(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("CPBuilt")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        # The build hook fires inside simulate_scheduled_round when the last RR
        # fixture lands, so the tournament phase is now built + active.
        current = season.current_phase()
        self.assertIsNotNone(current)
        self.assertEqual(current.pk, tournament_phase.pk)
        self.assertEqual(current.phase_type, "tournament")

    def test_returns_none_once_tournament_completed(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("CPDone")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        tournament_phase.refresh_from_db()
        self.assertIsNotNone(tournament_phase.tournament_id)
        _lg02c1_drain_tournament(tournament_phase.tournament)
        season.refresh_from_db()
        # Every phase complete ⇒ cursor parks on None.
        self.assertIsNone(season.current_phase())


class TestPhaseCompleteRule(TestCase):
    """``Season._phase_complete(phase)`` per phase type."""

    def test_rr_phase_complete_tracks_is_finished(self) -> None:
        season, teams, rr_phase, _t = _lg02c1_rr_tournament_season("PCRR")
        # Before play: RR phase not complete (matches _is_finished()).
        self.assertFalse(season._phase_complete(rr_phase))
        self.assertEqual(season._phase_complete(rr_phase), season._is_finished())
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        rr_phase.refresh_from_db()
        self.assertTrue(season._phase_complete(rr_phase))
        self.assertEqual(season._phase_complete(rr_phase), season._is_finished())

    def test_tournament_phase_incomplete_when_unbuilt(self) -> None:
        season, _teams, _rr, tournament_phase = _lg02c1_rr_tournament_season(
            "PCUnbuilt"
        )
        # tournament_id is None ⇒ not complete.
        self.assertIsNone(tournament_phase.tournament_id)
        self.assertFalse(season._phase_complete(tournament_phase))

    def test_tournament_phase_incomplete_when_built_but_active(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("PCActive")
        _lg02c1_play_rr(season, teams)
        tournament_phase.refresh_from_db()
        self.assertIsNotNone(tournament_phase.tournament_id)
        self.assertEqual(tournament_phase.tournament.state, "active")
        # Built + active is NOT complete.
        self.assertFalse(season._phase_complete(tournament_phase))

    def test_tournament_phase_complete_when_built_and_completed(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season(
            "PCComplete"
        )
        _lg02c1_play_rr(season, teams)
        tournament_phase.refresh_from_db()
        _lg02c1_drain_tournament(tournament_phase.tournament)
        tournament_phase.refresh_from_db()
        self.assertEqual(tournament_phase.tournament.state, "completed")
        self.assertTrue(season._phase_complete(tournament_phase))


class TestActivatePendingTournamentPhase(TestCase):
    """``Season.activate_pending_tournament_phase()`` — the auto-build."""

    def test_noop_while_rr_incomplete(self) -> None:
        season, _teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("APNoop")
        season.activate_pending_tournament_phase()
        tournament_phase.refresh_from_db()
        # No tournament built while the RR phase is still unplayed.
        self.assertIsNone(tournament_phase.tournament_id)

    def test_builds_tournament_when_rr_complete(self) -> None:
        # Play the RR WITHOUT the auto-build hook to isolate the explicit call.
        # (The hook fires inside simulate_scheduled_round, so by the time RR is
        #  fully played the tournament is already built; we instead assert the
        #  resulting built tournament's shape, which is what the hook produces.)
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("APBuild")
        _lg02c1_play_rr(season, teams)
        # Explicit call must be idempotent on an already-built phase.
        season.activate_pending_tournament_phase()
        tournament_phase.refresh_from_db()
        self.assertIsNotNone(tournament_phase.tournament_id)
        tournament = tournament_phase.tournament
        self.assertEqual(tournament.format, "single_elimination")
        self.assertEqual(tournament.team_assembly, "preset")
        # Locked + built ⇒ active, and a bracket exists.
        self.assertEqual(tournament.state, "active")
        self.assertTrue(
            _Lg02c1BracketNode.objects.filter(tournament=tournament).exists()
        )

    def test_one_participant_per_team_seeded_by_standings_rank(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("APSeed")
        _lg02c1_play_rr(season, teams)
        tournament_phase.refresh_from_db()
        tournament = tournament_phase.tournament

        participants = list(
            _Lg02c1Participant.objects.filter(tournament=tournament).order_by("seed")
        )
        # One participant per season team.
        self.assertEqual(len(participants), len(teams))
        # Seeds are the dense 1..N standings ranks (rank 1 -> seed 1).
        self.assertEqual([p.seed for p in participants], list(range(1, len(teams) + 1)))

        # Seed order == standings rank order: rebuild standings the same way the
        # build hook does and assert seed-i team == standings-rank-i team.
        team_ids = season.starting_team_ids_json or []
        from matches.models import Match
        from teams.models import Team

        completed = []
        for m in Match.objects.filter(season=season, is_completed=True):
            completed.append(
                {
                    "match_id": m.id,
                    "team_red_id": m.team_red_id,
                    "team_blue_id": m.team_blue_id,
                    "winner_team_id": m.winner_id,
                    "red_rounds_won": m.red_rounds_won,
                    "blue_rounds_won": m.blue_rounds_won,
                    "red_total_points": m.red_total_points,
                    "blue_total_points": m.blue_total_points,
                }
            )
        enrolled = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))
        rows = _lg02c1_compute_standings(completed, enrolled)
        rank_to_team = {row.rank: row.team_id for row in rows}
        for p in participants:
            self.assertEqual(p.team_id, rank_to_team[p.seed])

    def test_idempotent_second_call_does_not_rebuild(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("APIdem")
        _lg02c1_play_rr(season, teams)
        tournament_phase.refresh_from_db()
        tournament_id = tournament_phase.tournament_id
        self.assertIsNotNone(tournament_id)
        node_count = _Lg02c1BracketNode.objects.filter(
            tournament_id=tournament_id
        ).count()

        # A second activate call is a no-op (hits the already-built guard).
        season.activate_pending_tournament_phase()
        tournament_phase.refresh_from_db()
        self.assertEqual(tournament_phase.tournament_id, tournament_id)
        self.assertEqual(
            _Lg02c1BracketNode.objects.filter(tournament_id=tournament_id).count(),
            node_count,
        )


class TestCompleteIfFinishedTournamentChampion(TestCase):
    """``complete_if_finished`` champion = ``phase.tournament.champion``."""

    def test_season_not_completed_while_tournament_active(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("CIFActive")
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        # RR done + tournament built but not drained ⇒ Season still active.
        self.assertEqual(season.state, "active")
        self.assertIsNone(season.champion_team_id)

    def test_season_champion_is_tournament_champion(self) -> None:
        season, teams, _rr, tournament_phase = _lg02c1_rr_tournament_season("CIFChamp")
        _lg02c1_play_rr(season, teams)
        tournament_phase.refresh_from_db()
        _lg02c1_drain_tournament(tournament_phase.tournament)
        tournament_phase.refresh_from_db()
        tournament = tournament_phase.tournament
        self.assertEqual(tournament.state, "completed")
        self.assertIsNotNone(tournament.champion_id)

        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, tournament.champion_id)


class TestSingleRrPhaseChampionRegression(TestCase):
    """A single-RR-phase Season stays byte-identical: champion == standings[0]."""

    def _started_single_rr_season(self, prefix: str, n: int = 2):
        league = League.objects.create(name=f"{prefix} League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        teams = [make_team_with_slots(f"{prefix}{i}")[0] for i in range(n)]
        for t in teams:
            season.teams.add(t)
        # Exactly one explicit round_robin phase — no tournament phase.
        SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
        season.start_season()
        season.refresh_from_db()
        return season, teams

    def test_single_rr_phase_completes_with_standings_leader(self) -> None:
        season, teams = self._started_single_rr_season("RegRR", 2)
        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)

        # The champion is the standings rank-1 team (the LG-01 rule, preserved).
        team_ids = season.starting_team_ids_json or []
        from matches.models import Match
        from teams.models import Team

        completed = []
        for m in Match.objects.filter(season=season, is_completed=True):
            completed.append(
                {
                    "match_id": m.id,
                    "team_red_id": m.team_red_id,
                    "team_blue_id": m.team_blue_id,
                    "winner_team_id": m.winner_id,
                    "red_rounds_won": m.red_rounds_won,
                    "blue_rounds_won": m.blue_rounds_won,
                    "red_total_points": m.red_total_points,
                    "blue_total_points": m.blue_total_points,
                }
            )
        enrolled = list(Team.objects.filter(id__in=team_ids).values_list("id", "name"))
        rows = _lg02c1_compute_standings(completed, enrolled)
        self.assertEqual(season.champion_team_id, rows[0].team_id)

    def test_phaseless_season_still_completes_with_standings_leader(self) -> None:
        # A genuinely phase-less Season (implicit fallback) is the other
        # byte-identical regression path.
        league = League.objects.create(name="RegPhaseless League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        teams = [make_team_with_slots(f"RegPL{i}")[0] for i in range(2)]
        for t in teams:
            season.teams.add(t)
        season.start_season()
        season.refresh_from_db()
        self.assertEqual(season.phases.count(), 0)

        _lg02c1_play_rr(season, teams)
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)


# ===========================================================================
# LG-02-Part2c-2 — multi-round-robin fixture seam + per-phase completion
# ===========================================================================
#
# Seam contract ``.claude/worktrees/lg-02-part2c-2-seam-contract.md`` §7
# (model-level fixture-seam + per-phase completion) + the single-RR-phase /
# phase-less byte-identical regression. Appended as NEW classes; no existing
# class above is modified.


def _lg02c2_two_rr_season(prefix: str, n: int = 2):
    """An active Season with ordinal-1 + ordinal-2 ``round_robin`` phases,
    ``n`` slotted teams enrolled, started. Returns ``(season, teams, rr1, rr2)``.
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
    rr1 = SeasonPhase.objects.create(season=season, ordinal=1, phase_type="round_robin")
    rr2 = SeasonPhase.objects.create(season=season, ordinal=2, phase_type="round_robin")
    season.start_season()
    season.refresh_from_db()
    return season, teams, rr1, rr2


def _lg02c2_play_phase(season, teams, phase) -> None:
    """Play every fixture of ONE phase, attributing each Round to that phase."""
    by_id = {t.id: t for t in teams}
    fixtures = None
    for candidate, phase_fixtures in season.scheduled_fixtures_by_phase():
        if candidate.pk == phase.pk:
            fixtures = phase_fixtures
            break
    assert fixtures is not None
    sim = BatchSimulator()
    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for fixture in fixtures:
            sim.simulate_scheduled_round(
                season,
                by_id[fixture.team_a_id],
                by_id[fixture.team_b_id],
                fixture.round_number,
                season_phase=phase,
            )


class TestLg02c2FixtureSeamByPhase(TestCase):
    """``scheduled_fixtures_by_phase()`` per-phase ordinal order + monotonic
    global matchday calendar; ``scheduled_fixtures()`` == concatenation."""

    def test_one_entry_per_rr_phase_ordinal_order(self) -> None:
        season, _teams, rr1, rr2 = _lg02c2_two_rr_season("C2Order", n=3)
        by_phase = season.scheduled_fixtures_by_phase()
        self.assertEqual([p.pk for p, _ in by_phase], [rr1.pk, rr2.pk])

    def test_phase2_offset_past_phase1(self) -> None:
        season, _teams, _rr1, _rr2 = _lg02c2_two_rr_season("C2Offset", n=3)
        (_p1, f1), (_p2, f2) = season.scheduled_fixtures_by_phase()
        self.assertGreater(min(f.matchday for f in f2), max(f.matchday for f in f1))

    def test_flat_equals_concatenation(self) -> None:
        season, _teams, _rr1, _rr2 = _lg02c2_two_rr_season("C2Concat", n=3)
        concat = []
        for _p, fixtures in season.scheduled_fixtures_by_phase():
            concat.extend(fixtures)
        self.assertEqual(season.scheduled_fixtures(), concat)

    def test_global_calendar_contiguous(self) -> None:
        season, _teams, _rr1, _rr2 = _lg02c2_two_rr_season("C2Calendar", n=3)
        matchdays = sorted({f.matchday for f in season.scheduled_fixtures()})
        self.assertEqual(matchdays, list(range(1, len(matchdays) + 1)))


class TestLg02c2PerPhaseCompletionModel(TestCase):
    """Model-level per-phase RR completion: ``_phase_complete(rr1)`` True only
    once RR1 played (scoped ``match__season_phase=rr1``); ``current_phase()``
    returns RR2 only after RR1 is complete."""

    def test_phase_complete_rr1_only_after_rr1_played(self) -> None:
        season, teams, rr1, rr2 = _lg02c2_two_rr_season("C2CompRr1", n=2)
        self.assertFalse(season._phase_complete(rr1))
        _lg02c2_play_phase(season, teams, rr1)
        season.refresh_from_db()
        self.assertTrue(season._phase_complete(rr1))
        self.assertFalse(season._phase_complete(rr2))

    def test_current_phase_returns_rr2_only_after_rr1(self) -> None:
        season, teams, rr1, rr2 = _lg02c2_two_rr_season("C2Cursor", n=2)
        self.assertEqual(season.current_phase().pk, rr1.pk)
        _lg02c2_play_phase(season, teams, rr1)
        season.refresh_from_db()
        self.assertEqual(season.current_phase().pk, rr2.pk)

    def test_rr_phase_complete_scoped_to_owning_phase(self) -> None:
        """Playing under rr1's season_phase does NOT make rr2 complete — the
        per-phase scope is ``match__season_phase=phase``."""
        season, teams, rr1, rr2 = _lg02c2_two_rr_season("C2Scope", n=2)
        _lg02c2_play_phase(season, teams, rr1)
        season.refresh_from_db()
        self.assertFalse(season._phase_complete(rr2))


class TestLg02c2SingleRrPhaselessRegression(TestCase):
    """A single-RR-phase Season (and the phase-less implicit fallback) is
    byte-identical: the implicit-fallback ``_phase_complete`` routes through
    ``_is_finished()`` and the same fixtures / completion result hold."""

    def _started_phaseless(self, prefix: str, n: int = 2):
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

    def test_phaseless_phase_complete_routes_through_is_finished(self) -> None:
        season, _teams = self._started_phaseless("C2RegFallback", 2)
        self.assertEqual(season.phases.count(), 0)
        implicit = season.ordered_phases()[0]
        self.assertIsNone(implicit.pk)
        # Before play: implicit fallback _phase_complete == _is_finished == False.
        self.assertEqual(season._phase_complete(implicit), season._is_finished())
        self.assertFalse(season._phase_complete(implicit))

    def test_phaseless_single_rr_completes_byte_identically(self) -> None:
        season, teams = self._started_phaseless("C2RegComplete", 2)
        by_id = {t.id: t for t in teams}
        sim = BatchSimulator()
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            for fixture in season.scheduled_fixtures():
                sim.simulate_scheduled_round(
                    season,
                    by_id[fixture.team_a_id],
                    by_id[fixture.team_b_id],
                    fixture.round_number,
                )
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertIsNotNone(season.champion_team_id)
        # season_phase stays NULL for the legacy/phase-less path.
        from matches.models import Match as _M

        for m in _M.objects.filter(season=season):
            self.assertIsNone(m.season_phase_id)

    def test_single_explicit_rr_phase_complete_matches_is_finished(self) -> None:
        league = League.objects.create(name="C2RegExplicit League")
        season = Season.objects.create(
            league=league, name="S1", start_date=date(2026, 1, 1)
        )
        teams = [make_team_with_slots(f"C2RegExp{i}")[0] for i in range(2)]
        for t in teams:
            season.teams.add(t)
        rr1 = SeasonPhase.objects.create(
            season=season, ordinal=1, phase_type="round_robin"
        )
        season.start_season()
        season.refresh_from_db()
        # Before play: a single explicit RR phase mirrors _is_finished.
        self.assertFalse(season._phase_complete(rr1))
        self.assertFalse(season._is_finished())
