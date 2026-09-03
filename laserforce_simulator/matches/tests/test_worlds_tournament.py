"""CONF-04 - the Worlds Tournament phase: the derived fifth-mode
``SeasonPhase``, the ``Season.build_pending_worlds_bracket()`` seam, the
``minimum`` bracket floor, and the Season champion it finally crowns.

The seam contract is locked at ``.claude/worktrees/conf-04-seam-contract.md``;
the design rationale is
[ADR-0037](../../docs/adr/0037-worlds-is-a-derived-season-phase.md) and the
CONTEXT.md **Worlds** / **Worlds phase** / **Worlds qualifier** terms. This
builds directly on CONF-02's regional playoffs (ADR-0035) and CONF-03's
qualification derivation (ADR-0036).

What is asserted here (contract §11.5 items 1-9, 12, 14 plus §9.5):

* **Phase derivation** (§5.1) - ``_ensure_worlds_phase`` writes exactly one row
  with every column explicit, at ``max(ordinal) + 1``, for a >= 2-Conference
  Season carrying a persisted non-``worlds`` tournament phase; NOTHING for a
  0/1-Conference Season, nothing for an RR-only composition, nothing on a
  second call, and it comes back identically through the
  ``activate_pending_tournament_phase`` recovery hook.
* **The build seam** (§5.2 / §5.3) - the four-step gate, the exact
  ``Tournament`` row (both CONF-02 linkage columns NULL and ``qualifier_stage``
  left at ``""``), ``seed=q.seed``, and idempotence.
* **Bracket shapes** - M = 2 (the single Worlds final), M = 3 (size 4, the top
  seed byes into the final) and M = 5 (size 8, three byes), all through
  ``lock_and_build(minimum=2)``.
* **The champion** (§5.4) - the Season PARKS on an unbuilt Worlds phase instead
  of completing championless, then stamps ``champion_team`` from the Worlds
  bracket with ``complete_if_finished`` / ``_stamp_champion_for_final_phase``
  UNEDITED.
* **The byte-identity pins** (§12) - a 0- or 1-Conference Season grows no
  Worlds phase, builds no Worlds bracket, still crowns its champion from its
  single bracket, and renders no ``-worlds`` DOM id.
* **``following_tournament_is_final``** (§9.5) - reproduced on both
  pre-CONF-04 phase shapes and FIXED on ``RR -> tournament -> worlds``.

**No simulation (contract §11.1, carried forward from CONF-02 §9.1 / CONF-03
§9.1).** Every fixture reaches its state one of two permitted ways: the
round-robin is the hand-built deterministic ``Match`` + ``GameRound`` set
shared with ``test_regional_playoffs.py``, and a bracket is drained either by
STAMPING ``Tournament.champion`` / ``state="completed"`` on the persisted rows
(when exercising a gate or a derivation) or by driving
``tournament_engine.play_next_bracket_round`` under a small ``ROUND_TICKS``
patch (when the drain itself is what is under test). Nothing patches the seam
under test. Assertions are schema-level - ids, seeds, ordinals, states,
booleans, return values, DOM ids - never a simulated point total.

**Internal detail is NOT asserted (contract §11.3).** ``Season._worlds_phase``
and ``Season._final_tournament_phase`` are never called here: the Worlds phase
is read off the PUBLIC ``SeasonPhase.tournament_mode`` discriminator, and the
narrowing of ``_final_tournament_phase`` is asserted only through
``worlds_qualifiers()``. ``_ensure_worlds_phase`` is private by name but
test-visible BY EXCEPTION (contract §11.2) - the ADR gives it no public caller.

**Naming hazards (contract §13).** ``tournament_mode == "worlds"`` is the PHASE
flavour; there is deliberately NO ``qualifier_stage == "worlds"``. The Worlds
``Tournament`` has ``season_phase_id is None`` (it is not a regional row) AND
``season_phases.first()`` IS the Worlds phase (it is the forward embed).
``id="league-playoffs-worlds"`` remains CONF-03's qualification TABLE; the
CONF-04 bracket section is ``league-playoffs-phase-<ord>-worlds``.

NOTE: this file requires the Code agent's ``SeasonPhase.tournament_mode``
``"worlds"`` choice + migration ``0060_alter_seasonphase_tournament_mode`` +
``_ensure_worlds_phase`` / ``_worlds_phase`` / ``build_pending_worlds_bracket``
+ the narrowed ``_final_tournament_phase`` + ``lock_and_build(minimum=...)`` +
the ``_playoff_cursor_keys`` generalisation to land. Until then these tests are
EXPECTED to fail (AttributeError / assertion) - the TDD red state, not a defect
in this file.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from matches.models import (
    BracketNode,
    Conference,
    League,
    Season,
    SeasonPhase,
    Tournament,
    TournamentParticipant,
)
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots
from matches.tests.test_regional_playoffs import (
    _built_regional_season,
    _conf_season,
    _flat_season,
    _hand_play_rr,
    _ids,
    _stamp_bracket_completed,
)

_FAST_TICKS = 30

_MINIMUM_MESSAGE = "A tournament requires at least 4 participants."

# Every column of the Worlds ``SeasonPhase`` row, contract §5.1. Written
# EXPLICITLY by ``_ensure_worlds_phase``, so the row cannot drift if a model
# default later changes - which is exactly what this table pins.
_WORLDS_PHASE_COLUMNS = {
    "phase_type": "tournament",
    "schedule_format": None,
    "tournament_mode": "worlds",
    "tournament_format": "single_elimination",
    "tournament_cut": 0,
    "final_series_length": 1,
    "semifinal_series_length": 1,
    "quarterfinal_series_length": 1,
    "earlier_series_length": 1,
    "wb_advancers": 0,
    "lb_advancers": 0,
    "swiss_rounds": 0,
}


# ---------------------------------------------------------------------------
# Helpers - hand-built, deterministic, ZERO simulation to reach the build
# ---------------------------------------------------------------------------


def _worlds_phase_row(season: Season) -> "SeasonPhase | None":
    """This Season's Worlds phase, read off the PUBLIC discriminator.

    Deliberately NOT ``season._worlds_phase()``: contract §11.3 forbids calling
    the private resolver, which is asserted only through its observable
    effects. ``SeasonPhase.tournament_mode`` is on the §11.2 public list.
    """
    return season.phases.filter(tournament_mode="worlds").first()


def _phase_columns(phase: SeasonPhase) -> dict:
    """The §5.1 column values of one phase, as a comparable dict."""
    return {name: getattr(phase, name) for name in _WORLDS_PHASE_COLUMNS}


def _regional_playoff(phase: SeasonPhase, conference: Conference):
    """CONF-03's locked read rule: a Regional playoff is any Conference-scoped
    row that is NOT ``last_chance`` - never a positive test on
    ``"regional_playoff"``, so an un-backfilled CONF-02 ``""`` row resolves."""
    return (
        phase.regional_tournaments.filter(conference=conference)
        .exclude(qualifier_stage="last_chance")
        .first()
    )


def _drain_regionals(phase: SeasonPhase, conferences: list, groups: list) -> None:
    """Crown every Regional playoff by STAMPING the persisted rows (§11.1
    technique 2). A Conference too small to field a bracket has none, and is
    skipped."""
    for conference, group in zip(conferences, groups):
        regional = _regional_playoff(phase, conference)
        if regional is not None:
            _stamp_bracket_completed(regional, group[0])


def _regionals_drained_season(prefix: str, sizes: list[int]):
    """A >= 2-Conference Season at the exact moment the Worlds bracket becomes
    buildable: every Regional playoff has crowned its Conference champion, and
    nothing has built Worlds yet.

    Returns ``(season, conferences, groups, rr_phase, phase)``.
    """
    season, conferences, groups, rr_phase, phase = _built_regional_season(prefix, sizes)
    _drain_regionals(phase, conferences, groups)
    return season, conferences, groups, rr_phase, phase


def _worlds_built_season(prefix: str, sizes: list[int]):
    """``_regionals_drained_season`` + the real ``build_pending_worlds_bracket``.

    Returns ``(season, conferences, groups, phase, worlds_phase, worlds)``.
    """
    season, conferences, groups, _rr, phase = _regionals_drained_season(prefix, sizes)
    built = season.build_pending_worlds_bracket()
    assert built is True, "fixture precondition: the Worlds bracket built"
    worlds_phase = _worlds_phase_row(season)
    assert worlds_phase is not None
    worlds_phase.refresh_from_db()
    return season, conferences, groups, phase, worlds_phase, worlds_phase.tournament


def _rr_only_conference_season(prefix: str, sizes: list[int]):
    """A >= 2-Conference Season composed of ROUND-ROBIN phases ONLY.

    Gate 2 of ``_ensure_worlds_phase`` (contract §3.3 - "at least one persisted
    non-``worlds`` ``tournament`` phase") fails, so no Worlds phase may exist.
    Returns ``(season, conferences, groups, rr_phase)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="2027", start_date=date(2027, 1, 1)
    )
    conferences: list[Conference] = []
    groups: list[list] = []
    for ci, size in enumerate(sizes, start=1):
        conference = Conference.objects.create(
            season=season, name=f"{prefix}Conf{ci}", ordinal=ci
        )
        teams = []
        for ti in range(size):
            team, _players = make_team_with_slots(f"{prefix}{ci}x{ti}")
            season.teams.add(team)
            conference.teams.add(team)
            teams.append(team)
        conferences.append(conference)
        groups.append(teams)
    rr_phase = SeasonPhase.objects.create(
        season=season, ordinal=1, phase_type="round_robin"
    )
    season.start_season()
    season.refresh_from_db()
    rr_phase.refresh_from_db()
    return season, conferences, groups, rr_phase


def _flat_multi_phase_season(prefix: str, phase_types: list[str], n: int = 4):
    """A ZERO-Conference Season whose composition is ``phase_types`` in order.

    Used only for the ``following_tournament_is_final`` pre-CONF-04 pins, which
    need an ``RR -> t -> RR -> t`` shape no other fixture produces.
    Returns ``(season, teams, phases)``.
    """
    league = League.objects.create(name=f"{prefix} League")
    season = Season.objects.create(
        league=league, name="2027", start_date=date(2027, 1, 1)
    )
    teams = []
    for i in range(n):
        team, _players = make_team_with_slots(f"{prefix}f{i}")
        season.teams.add(team)
        teams.append(team)
    phases = [
        SeasonPhase.objects.create(
            season=season, ordinal=ordinal, phase_type=phase_type
        )
        for ordinal, phase_type in enumerate(phase_types, start=1)
    ]
    season.start_season()
    season.refresh_from_db()
    return season, teams, phases


def _participant_seeds(tournament: Tournament) -> dict[int, int]:
    """``{team_id: seed}`` for one bracket. Insertion ORDER is internal
    (contract §11.3); the ``seed`` values are the assertion."""
    return {
        participant.team_id: participant.seed
        for participant in TournamentParticipant.objects.filter(tournament=tournament)
    }


def _qualifier_seeds(season: Season) -> dict[int, int]:
    """``{team_id: seed}`` for the derived Worlds field."""
    return {q.team_id: q.seed for q in season.worlds_qualifiers()}


def _drain_bracket(tournament: Tournament, *, max_stages: int = 8) -> None:
    """Drive the REAL engine stage by stage (contract §11.1 technique 1)."""
    from matches.tournament_engine import play_next_bracket_round

    with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
        for _ in range(max_stages):
            if play_next_bracket_round(tournament) == 0:
                break


# ===========================================================================
# 1. Phase derivation - the row _ensure_worlds_phase writes
# ===========================================================================


class TestEnsureWorldsPhaseCreatesTheRow(TestCase):
    """CONF-04 §5.1 - a >= 2-Conference Season grows exactly ONE derived Worlds
    phase at ``start_season``, with every column explicit."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("WtEnsure", [4, 4])
        self.worlds_phase = _worlds_phase_row(self.season)

    def test_start_season_appends_exactly_one_worlds_phase(self) -> None:
        self.assertIsNotNone(
            self.worlds_phase, "a >= 2-Conference Season grows a Worlds phase"
        )
        self.assertEqual(self.season.phases.filter(tournament_mode="worlds").count(), 1)

    def test_the_worlds_phase_takes_the_highest_ordinal(self) -> None:
        ordinals = [phase.ordinal for phase in self.season.phases.all()]
        self.assertEqual(self.worlds_phase.ordinal, max(ordinals))
        # RR(1) -> tournament(2) -> worlds(3): max + 1, never a renumber.
        self.assertEqual(self.worlds_phase.ordinal, 3)

    def test_ordered_phases_ends_with_the_worlds_phase(self) -> None:
        # ``complete_if_finished`` reads ``ordered_phases()[-1]`` - this IS why
        # the Season stops completing championless (§5.4 step 1).
        self.assertEqual(self.season.ordered_phases()[-1].pk, self.worlds_phase.pk)

    def test_every_column_of_the_row_is_the_contracted_value(self) -> None:
        self.assertEqual(_phase_columns(self.worlds_phase), _WORLDS_PHASE_COLUMNS)

    def test_the_phase_type_is_tournament_not_a_new_phase_type(self) -> None:
        # NOT a fourth ``phase_type``: every ``phase_type == "tournament"`` site
        # carries the Worlds phase for free (ADR-0037).
        self.assertEqual(self.worlds_phase.phase_type, "tournament")
        self.assertIn(
            "worlds",
            {value for value, _label in SeasonPhase.TOURNAMENT_MODE_CHOICES},
        )
        self.assertNotIn(
            "worlds", {value for value, _label in SeasonPhase.PHASE_TYPE_CHOICES}
        )

    def test_the_tournament_embed_starts_null(self) -> None:
        self.assertIsNone(self.worlds_phase.tournament_id)

    def test_the_regional_phase_is_untouched_by_the_derivation(self) -> None:
        self.phase.refresh_from_db()
        self.assertEqual(self.phase.tournament_mode, "standings")
        self.assertEqual(self.phase.ordinal, 2)

    def test_ensure_is_idempotent_and_returns_the_existing_row(self) -> None:
        first = self.season._ensure_worlds_phase()
        second = self.season._ensure_worlds_phase()
        self.assertIsNotNone(first)
        self.assertEqual(first.pk, self.worlds_phase.pk)
        self.assertEqual(second.pk, first.pk)
        self.assertEqual(self.season.phases.filter(tournament_mode="worlds").count(), 1)

    def test_repeated_ensures_add_no_phase_rows_at_all(self) -> None:
        before = self.season.phases.count()
        for _ in range(3):
            self.season._ensure_worlds_phase()
        self.assertEqual(self.season.phases.count(), before)

    def test_the_activation_hook_creates_no_second_worlds_phase(self) -> None:
        # ``activate_pending_tournament_phase`` runs after EVERY scheduled
        # Round (§6.E); it must never accumulate rows.
        for _ in range(3):
            self.season.activate_pending_tournament_phase()
        self.assertEqual(self.season.phases.filter(tournament_mode="worlds").count(), 1)


class TestWorldsTournamentModeChoice(TestCase):
    """CONF-04 §4 - exactly one new choice, appended fifth, with the four
    existing pairs byte-unchanged (ASCII arrow included)."""

    def test_the_choices_tuple_is_the_contracted_five(self) -> None:
        self.assertEqual(
            SeasonPhase.TOURNAMENT_MODE_CHOICES,
            (
                ("standings", "Season-ending: from Standings"),
                ("strength", "Mid-season: by team strength"),
                ("unseeded", "Mid-season: random seed"),
                ("random_draw", "Mid-season: drawn pool -> RR->DE"),
                ("worlds", "Worlds"),
            ),
        )

    def test_the_stored_value_fits_the_column(self) -> None:
        field = SeasonPhase._meta.get_field("tournament_mode")
        self.assertLessEqual(len("worlds"), field.max_length)
        self.assertEqual(field.default, "standings")

    def test_no_worlds_qualifier_stage_value_was_added(self) -> None:
        # Contract §12 item 5 / §13 item 2 - the Worlds bracket is identified
        # from the PHASE flavour, never from anything on the Tournament row.
        self.assertNotIn(
            "worlds",
            {value for value, _label in Tournament.QUALIFIER_STAGE_CHOICES},
        )


class TestEnsureWorldsPhaseGates(TestCase):
    """CONF-04 §3.3 - the three-part gate. Nothing is created unless ALL of
    ``>= 2`` Conferences, a persisted non-``worlds`` tournament phase, and no
    existing Worlds phase hold."""

    def test_a_zero_conference_season_grows_no_worlds_phase(self) -> None:
        season, _teams, _rr_phase, _phase = _flat_season("WtGate0")
        self.assertIsNone(_worlds_phase_row(season))
        self.assertIsNone(season._ensure_worlds_phase())
        self.assertEqual(season.phases.count(), 2)

    def test_a_one_conference_season_grows_no_worlds_phase(self) -> None:
        season, _conferences, _groups, _rr, _phase = _conf_season("WtGate1", [5])
        self.assertIsNone(_worlds_phase_row(season))
        self.assertIsNone(season._ensure_worlds_phase())
        self.assertEqual(season.phases.count(), 2)

    def test_a_round_robin_only_season_grows_no_worlds_phase(self) -> None:
        # Gate 2: there is no non-``worlds`` tournament phase to follow.
        season, _conferences, _groups, _rr = _rr_only_conference_season(
            "WtGateRr", [4, 4]
        )
        self.assertIsNone(_worlds_phase_row(season))
        self.assertIsNone(season._ensure_worlds_phase())
        self.assertEqual(season.phases.count(), 1)

    def test_a_round_robin_only_season_stays_gated_on_repeated_calls(self) -> None:
        season, _conferences, _groups, _rr = _rr_only_conference_season(
            "WtGateRr2", [4, 4]
        )
        for _ in range(3):
            self.assertIsNone(season._ensure_worlds_phase())
        self.assertEqual(season.phases.count(), 1)

    def test_a_three_conference_season_still_grows_exactly_one(self) -> None:
        season, _conferences, _groups, _rr, _phase = _conf_season("WtGate3", [4, 4, 4])
        self.assertEqual(season.phases.filter(tournament_mode="worlds").count(), 1)


class TestWorldsPhaseRecoveryHook(TestCase):
    """CONF-04 §6.E / §11.5 item 2 - a Season that was already ACTIVE when this
    slice shipped gains its Worlds phase through
    ``activate_pending_tournament_phase``, not through a data migration
    (ADR-0004). Simulated by deleting the row."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("WtRecover", [4, 4])
        original = _worlds_phase_row(self.season)
        self.original_columns = _phase_columns(original)
        self.original_ordinal = original.ordinal
        original.delete()
        self.season.refresh_from_db()

    def test_fixture_precondition_the_row_is_gone(self) -> None:
        self.assertIsNone(_worlds_phase_row(self.season))

    def test_the_activation_hook_recreates_it(self) -> None:
        self.season.activate_pending_tournament_phase()
        self.assertIsNotNone(_worlds_phase_row(self.season))

    def test_the_recreated_row_is_column_identical(self) -> None:
        self.season.activate_pending_tournament_phase()
        recovered = _worlds_phase_row(self.season)
        self.assertEqual(_phase_columns(recovered), self.original_columns)

    def test_the_recreated_row_takes_the_same_ordinal(self) -> None:
        self.season.activate_pending_tournament_phase()
        self.assertEqual(_worlds_phase_row(self.season).ordinal, self.original_ordinal)

    def test_recovery_after_the_regular_season_produces_the_identical_row(self) -> None:
        # Every input ``_ensure_worlds_phase`` reads is frozen at activation, so
        # a LATE call must produce the same row (§6.E).
        _hand_play_rr(
            self.season,
            self.rr_phase,
            _ids(self.groups[0]) + _ids(self.groups[1]),
        )
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        recovered = _worlds_phase_row(self.season)
        self.assertIsNotNone(recovered)
        self.assertEqual(_phase_columns(recovered), self.original_columns)
        self.assertEqual(recovered.ordinal, self.original_ordinal)


# ===========================================================================
# 3. The build gate - build_pending_worlds_bracket
# ===========================================================================


class TestBuildPendingWorldsBracketGate(TestCase):
    """CONF-04 §5.3 / §11.5 item 3 - ``True`` IFF it built on THIS call."""

    def test_returns_false_while_the_regional_playoffs_are_undrained(self) -> None:
        season, _conferences, _groups, _rr, _phase = _built_regional_season(
            "WtGateUndrained", [4, 4]
        )
        self.assertFalse(season.build_pending_worlds_bracket())

    def test_builds_nothing_while_the_regional_playoffs_are_undrained(self) -> None:
        season, _conferences, _groups, _rr, _phase = _built_regional_season(
            "WtGateUndrainedRows", [4, 4]
        )
        before = Tournament.objects.count()
        season.build_pending_worlds_bracket()
        self.assertEqual(Tournament.objects.count(), before)
        self.assertIsNone(_worlds_phase_row(season).tournament_id)

    def test_returns_false_when_only_one_regional_playoff_has_drained(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WtGateHalf", [4, 4]
        )
        _stamp_bracket_completed(_regional_playoff(phase, conferences[0]), groups[0][0])
        self.assertFalse(season.build_pending_worlds_bracket())

    def test_returns_true_exactly_once_when_ready(self) -> None:
        season, _conferences, _groups, _rr, _phase = _regionals_drained_season(
            "WtGateReady", [4, 4]
        )
        self.assertTrue(season.build_pending_worlds_bracket())
        self.assertFalse(season.build_pending_worlds_bracket())
        self.assertFalse(season.build_pending_worlds_bracket())

    def test_the_second_call_creates_no_second_tournament(self) -> None:
        season, _conferences, _groups, _rr, _phase = _regionals_drained_season(
            "WtGateOnce", [4, 4]
        )
        season.build_pending_worlds_bracket()
        after_first = Tournament.objects.count()
        season.build_pending_worlds_bracket()
        self.assertEqual(Tournament.objects.count(), after_first)

    def test_the_second_call_creates_no_extra_participants_or_nodes(self) -> None:
        season, _conferences, _groups, _rr, _phase = _regionals_drained_season(
            "WtGateOnceRows", [4, 4]
        )
        season.build_pending_worlds_bracket()
        before = (
            TournamentParticipant.objects.count(),
            BracketNode.objects.count(),
        )
        season.build_pending_worlds_bracket()
        self.assertEqual(
            (TournamentParticipant.objects.count(), BracketNode.objects.count()),
            before,
        )

    def test_returns_false_for_a_zero_conference_season(self) -> None:
        season, teams, rr_phase, phase = _flat_season("WtGateFlat")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, teams[0])
        self.assertFalse(season.build_pending_worlds_bracket())
        self.assertEqual(Tournament.objects.count(), 1)

    def test_returns_false_for_a_one_conference_season(self) -> None:
        season, _conferences, groups, rr_phase, phase = _conf_season("WtGate1c", [5])
        _hand_play_rr(season, rr_phase, _ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, groups[0][0])
        self.assertFalse(season.build_pending_worlds_bracket())

    def test_returns_false_when_the_worlds_phase_row_is_missing(self) -> None:
        season, _conferences, _groups, _rr, _phase = _regionals_drained_season(
            "WtGateNoPhase", [4, 4]
        )
        _worlds_phase_row(season).delete()
        season.refresh_from_db()
        before = Tournament.objects.count()
        self.assertFalse(season.build_pending_worlds_bracket())
        self.assertEqual(Tournament.objects.count(), before)

    def test_returns_false_when_qualification_is_not_ready(self) -> None:
        """Gate step 4 - the prior phase IS complete but ``worlds_qualifiers()``
        is ``[]``. A 9-Team Conference's Last-chance bracket flipped to
        ``completed`` with a NULL champion is exactly that admin-mangled shape:
        ``_tournament_phase_complete`` passes, readiness does not."""
        season, conferences, groups, _rr, phase = _built_regional_season(
            "WtGateUnready", [9, 4], cut=4
        )
        _drain_regionals(phase, conferences, groups)
        last_chance = phase.regional_tournaments.filter(
            qualifier_stage="last_chance"
        ).first()
        self.assertIsNotNone(last_chance, "fixture precondition: a 9-Team Conference")
        last_chance.state = "completed"
        last_chance.save(update_fields=["state"])

        self.assertEqual(season.worlds_qualifiers(), [])
        self.assertFalse(season.build_pending_worlds_bracket())
        self.assertIsNone(_worlds_phase_row(season).tournament_id)

    def test_the_build_wires_the_phase_embed_pointer(self) -> None:
        season, _conferences, _groups, _rr, _phase = _regionals_drained_season(
            "WtGateWire", [4, 4]
        )
        self.assertTrue(season.build_pending_worlds_bracket())
        worlds_phase = _worlds_phase_row(season)
        worlds_phase.refresh_from_db()
        self.assertIsNotNone(worlds_phase.tournament_id)
        self.assertEqual(worlds_phase.tournament.state, "active")


# ===========================================================================
# 4 + 9. The Worlds Tournament row's exact shape
# ===========================================================================


class TestWorldsTournamentRowShape(TestCase):
    """CONF-04 §5.2 - the flat 0/1-Conference shape, which is what makes
    champion stamping work with ``_stamp_champion_for_final_phase`` UNEDITED."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.worlds_phase,
            self.worlds,
        ) = _worlds_built_season("WtRow", [4, 4])

    def test_the_name_carries_the_worlds_marker(self) -> None:
        # Containment only - the exact name is internal (contract §11.3).
        self.assertIn("Worlds", self.worlds.name)

    def test_format_and_assembly_are_the_contracted_literals(self) -> None:
        self.assertEqual(self.worlds.format, "single_elimination")
        self.assertEqual(self.worlds.team_assembly, "preset")

    def test_the_row_is_active_by_the_time_the_method_returns(self) -> None:
        # ``lock_and_build(minimum=2)`` flips ``setup`` -> ``active`` inside the
        # same transaction that wires ``phase.tournament`` (§5.2).
        self.assertEqual(self.worlds.state, "active")

    def test_the_sub_config_columns_come_off_the_phase(self) -> None:
        self.assertEqual(self.worlds.final_series_length, 1)
        self.assertEqual(self.worlds.semifinal_series_length, 1)
        self.assertEqual(self.worlds.quarterfinal_series_length, 1)
        self.assertEqual(self.worlds.earlier_series_length, 1)
        self.assertEqual(self.worlds.wb_advancers, 0)
        self.assertEqual(self.worlds.lb_advancers, 0)
        self.assertEqual(self.worlds.swiss_rounds, 0)

    def test_the_conf02_linkage_columns_are_both_null(self) -> None:
        self.assertIsNone(
            self.worlds.season_phase_id,
            "the Worlds row is NOT a regional row (§5.2)",
        )
        self.assertIsNone(
            self.worlds.conference_id, "the Worlds row is Season-wide, not scoped"
        )

    def test_the_qualifier_stage_is_left_at_its_empty_default(self) -> None:
        # Contract §12 item 5 - there is deliberately NO
        # ``qualifier_stage == "worlds"``.
        self.assertEqual(self.worlds.qualifier_stage, "")

    def test_it_never_appears_in_any_regional_tournaments_queryset(self) -> None:
        for phase in self.season.phases.all():
            self.assertNotIn(
                self.worlds.id,
                list(phase.regional_tournaments.values_list("id", flat=True)),
            )

    def test_the_worlds_phase_holds_it_on_the_forward_embed(self) -> None:
        # Naming hazard §13.1: ``season_phase_id is None`` AND
        # ``season_phases.first()`` IS the Worlds phase. Both directions.
        self.assertEqual(self.worlds.season_phases.first().pk, self.worlds_phase.pk)
        self.assertEqual(self.worlds_phase.tournament_id, self.worlds.id)

    def test_tournaments_for_phase_returns_exactly_the_worlds_bracket(self) -> None:
        self.assertEqual(
            [t.id for t in self.season.tournaments_for_phase(self.worlds_phase)],
            [self.worlds.id],
        )

    def test_tournaments_for_phase_still_returns_the_regionals_for_the_prior(
        self,
    ) -> None:
        regional_ids = [t.id for t in self.season.tournaments_for_phase(self.phase)]
        self.assertEqual(len(regional_ids), 2)
        self.assertNotIn(self.worlds.id, regional_ids)

    def test_tournaments_for_phase_is_empty_on_an_unbuilt_worlds_phase(self) -> None:
        season, _conferences, _groups, _rr, _phase = _built_regional_season(
            "WtRowUnbuilt", [4, 4]
        )
        worlds_phase = _worlds_phase_row(season)
        self.assertEqual(season.tournaments_for_phase(worlds_phase), [])

    # -- item 4: seed=q.seed, NOT position + 1 -----------------------------

    def test_participants_are_seeded_from_the_qualifiers_stamped_seeds(self) -> None:
        # ``worlds_qualifiers()`` returns the field already ordered and already
        # stamped 1..M; the participants must carry THOSE integers (§5.2).
        self.assertEqual(_participant_seeds(self.worlds), _qualifier_seeds(self.season))

    def test_the_participant_count_equals_the_qualifier_count(self) -> None:
        self.assertEqual(
            self.worlds.participants.count(), len(self.season.worlds_qualifiers())
        )

    def test_the_seeds_are_contiguous_from_one(self) -> None:
        seeds = sorted(_participant_seeds(self.worlds).values())
        self.assertEqual(seeds, list(range(1, len(seeds) + 1)))

    def test_every_participant_is_a_qualifier_and_vice_versa(self) -> None:
        self.assertEqual(
            set(_participant_seeds(self.worlds)),
            {q.team_id for q in self.season.worlds_qualifiers()},
        )

    def test_the_field_spans_both_conferences(self) -> None:
        conference_by_team = self.season.conference_by_team_id()
        conference_ids = {
            conference_by_team[team_id].id
            for team_id in _participant_seeds(self.worlds)
        }
        self.assertEqual(
            conference_ids,
            {c.id for c in self.conferences},
            "Worlds is cross-Conference",
        )


# ===========================================================================
# 5 + 6 + 7. Bracket shapes at M = 2, 3 and 5
# ===========================================================================


class TestWorldsBracketShapeAtTwo(TestCase):
    """CONF-04 §11.5 item 5 - two Conferences of 4 Teams each send one
    qualifier apiece, so M = 2: a size-2 bracket whose SINGLE node is the
    Worlds final. This is the ``minimum=2`` path end to end, and the default
    shape CONF-05's create form produces."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.worlds_phase,
            self.worlds,
        ) = _worlds_built_season("WtM2", [4, 4])

    def test_the_field_is_exactly_two(self) -> None:
        self.assertEqual(len(self.season.worlds_qualifiers()), 2)
        self.assertEqual(self.worlds.participants.count(), 2)

    def test_the_bracket_is_a_single_node(self) -> None:
        self.assertEqual(self.worlds.nodes.count(), 1)

    def test_the_single_node_is_round_one_and_advances_nowhere(self) -> None:
        node = self.worlds.nodes.get()
        self.assertEqual(node.bracket_round, 1)
        self.assertEqual(node.position, 0)
        self.assertIsNone(node.advances_to_id, "that node IS the Worlds final")

    def test_the_single_node_has_no_bye_and_no_winner_yet(self) -> None:
        node = self.worlds.nodes.get()
        self.assertFalse(node.is_bye)
        self.assertIsNone(node.winner_id)

    def test_the_two_node_slots_are_the_two_qualifiers(self) -> None:
        node = self.worlds.nodes.get()
        self.assertEqual(
            {node.team_a_id, node.team_b_id},
            {q.team_id for q in self.season.worlds_qualifiers()},
        )

    def test_the_node_is_a_winners_bracket_node(self) -> None:
        self.assertEqual(self.worlds.nodes.get().bracket_type, "winners")


class TestWorldsBracketShapeAtThree(TestCase):
    """CONF-04 §11.5 item 6 - a 5-Team + 4-Team pairing sends 2 + 1 = 3, so the
    bracket rounds up to size 4 and the TOP SEED byes into the final."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.worlds_phase,
            self.worlds,
        ) = _worlds_built_season("WtM3", [5, 4])

    def test_the_field_is_exactly_three(self) -> None:
        self.assertEqual(len(self.season.worlds_qualifiers()), 3)
        self.assertEqual(self.worlds.participants.count(), 3)

    def test_the_bracket_rounds_up_to_size_four(self) -> None:
        self.assertEqual(self.worlds.nodes.count(), 3)
        self.assertEqual(max(node.bracket_round for node in self.worlds.nodes.all()), 2)

    def test_exactly_one_round_one_bye(self) -> None:
        byes = list(self.worlds.nodes.filter(is_bye=True))
        self.assertEqual(len(byes), 1, "size - M == 1 bye")
        self.assertEqual(byes[0].bracket_round, 1)

    def test_the_bye_belongs_to_seed_one(self) -> None:
        bye = self.worlds.nodes.get(is_bye=True)
        seed_one = self.worlds.participants.get(seed=1)
        self.assertEqual(bye.team_a_id, seed_one.team_id)
        self.assertEqual(bye.winner_id, seed_one.team_id, "a bye is pre-resolved")

    def test_the_bye_advances_into_the_final(self) -> None:
        bye = self.worlds.nodes.get(is_bye=True)
        self.assertIsNotNone(bye.advances_to_id)
        self.assertEqual(bye.advances_to.bracket_round, 2)

    def test_the_final_advances_nowhere(self) -> None:
        final = self.worlds.nodes.get(advances_to__isnull=True)
        self.assertEqual(final.bracket_round, 2)


class TestWorldsBracketShapeAtFive(TestCase):
    """CONF-04 §11.5 item 7 - a non-power-of-two field (5 = 2 + 2 + 1) builds a
    size-8 bracket with three byes, inherited from ``build_bracket``."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.worlds_phase,
            self.worlds,
        ) = _worlds_built_season("WtM5", [5, 5, 4])

    def test_the_field_is_exactly_five(self) -> None:
        self.assertEqual(len(self.season.worlds_qualifiers()), 5)
        self.assertEqual(self.worlds.participants.count(), 5)

    def test_the_bracket_rounds_up_to_size_eight(self) -> None:
        self.assertEqual(self.worlds.nodes.count(), 7)
        self.assertEqual(max(node.bracket_round for node in self.worlds.nodes.all()), 3)

    def test_three_round_one_byes(self) -> None:
        byes = list(self.worlds.nodes.filter(is_bye=True))
        self.assertEqual(len(byes), 3, "size - M == 3 byes")
        self.assertEqual({bye.bracket_round for bye in byes}, {1})

    def test_the_byes_go_to_the_top_three_seeds(self) -> None:
        bye_team_ids = set(
            self.worlds.nodes.filter(is_bye=True).values_list("team_a_id", flat=True)
        )
        top_three = set(
            self.worlds.participants.filter(seed__lte=3).values_list(
                "team_id", flat=True
            )
        )
        self.assertEqual(bye_team_ids, top_three)

    def test_the_field_spans_all_three_conferences(self) -> None:
        conference_by_team = self.season.conference_by_team_id()
        self.assertEqual(
            {conference_by_team[q.team_id].id for q in self.season.worlds_qualifiers()},
            {c.id for c in self.conferences},
        )


# ===========================================================================
# lock_and_build(minimum=...) - the DB half of contract §7.2
# ===========================================================================


class TestLockAndBuildMinimum(TestCase):
    """CONF-04 §7.2 - the keyword-only ``minimum`` floor on
    ``Tournament.lock_and_build``. (The pure-builder half lives in
    ``test_bracket.py``, which is a ``SimpleTestCase`` with no DB.)"""

    def _tournament_with(self, prefix: str, n: int, *, fmt: str = "single_elimination"):
        tournament = Tournament.objects.create(
            name=f"{prefix} Bracket",
            format=fmt,
            team_assembly="preset",
            state="setup",
        )
        for seed in range(1, n + 1):
            team, _players = make_team_with_slots(f"{prefix}{seed}")
            TournamentParticipant.objects.create(
                tournament=tournament, team=team, seed=seed
            )
        return tournament

    def test_the_default_still_rejects_two_participants(self) -> None:
        tournament = self._tournament_with("LbDefault2", 2)
        with self.assertRaises(ValidationError):
            tournament.lock_and_build()

    def test_the_default_rejection_message_is_unchanged(self) -> None:
        tournament = self._tournament_with("LbMsg", 3)
        with self.assertRaises(ValidationError) as ctx:
            tournament.lock_and_build()
        self.assertIn(_MINIMUM_MESSAGE, ctx.exception.messages)

    def test_a_rejected_lock_leaves_the_row_in_setup_with_no_nodes(self) -> None:
        tournament = self._tournament_with("LbRollback", 2)
        with self.assertRaises(ValidationError):
            tournament.lock_and_build()
        tournament.refresh_from_db()
        self.assertEqual(tournament.state, "setup")
        self.assertEqual(tournament.nodes.count(), 0)

    def test_minimum_two_accepts_two_participants(self) -> None:
        tournament = self._tournament_with("LbMin2", 2)
        tournament.lock_and_build(minimum=2)
        tournament.refresh_from_db()
        self.assertEqual(tournament.state, "active")
        self.assertEqual(tournament.nodes.count(), 1)

    def test_minimum_two_accepts_three_participants_as_a_size_four_tree(self) -> None:
        tournament = self._tournament_with("LbMin3", 3)
        tournament.lock_and_build(minimum=2)
        self.assertEqual(tournament.nodes.count(), 3)
        self.assertEqual(tournament.nodes.filter(is_bye=True).count(), 1)

    def test_minimum_is_keyword_only(self) -> None:
        tournament = self._tournament_with("LbKwOnly", 2)
        with self.assertRaises(TypeError):
            tournament.lock_and_build(2)  # type: ignore[misc]

    def test_minimum_two_leaves_a_four_team_build_unchanged(self) -> None:
        default_tournament = self._tournament_with("LbFourA", 4)
        default_tournament.lock_and_build()
        floored_tournament = self._tournament_with("LbFourB", 4)
        floored_tournament.lock_and_build(minimum=2)
        self.assertEqual(
            default_tournament.nodes.count(), floored_tournament.nodes.count()
        )
        self.assertEqual(
            sorted(default_tournament.nodes.values_list("bracket_round", "position")),
            sorted(floored_tournament.nodes.values_list("bracket_round", "position")),
        )

    def test_the_state_guard_is_unchanged_under_a_lowered_minimum(self) -> None:
        tournament = self._tournament_with("LbState", 2)
        tournament.lock_and_build(minimum=2)
        with self.assertRaises(ValidationError):
            tournament.lock_and_build(minimum=2)

    def test_minimum_two_forwards_into_the_double_elimination_builder(self) -> None:
        tournament = self._tournament_with("LbDe", 3, fmt="double_elimination")
        tournament.lock_and_build(minimum=2)
        tournament.refresh_from_db()
        self.assertEqual(tournament.state, "active")
        self.assertEqual(
            set(tournament.nodes.values_list("bracket_type", flat=True)),
            {"winners", "losers", "grand_final"},
        )


# ===========================================================================
# 8. The champion - complete_if_finished, UNEDITED
# ===========================================================================


class TestSeasonParksOnAnUnbuiltWorldsPhase(TestCase):
    """CONF-04 §5.4 step 2 - ``tournaments_for_phase(worlds_phase)`` is ``[]``
    while the bracket is unbuilt, so ``_tournament_phase_complete`` is False and
    the Season does NOT complete championless the instant the regionals drain.
    That race is precisely why the phase is derived at ``start_season``."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _regionals_drained_season("WtPark", [4, 4])

    def test_the_regional_phase_is_complete(self) -> None:
        for conference in self.conferences:
            self.assertEqual(
                _regional_playoff(self.phase, conference).state, "completed"
            )

    def test_the_season_does_not_complete_while_worlds_is_unbuilt(self) -> None:
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")

    def test_the_champion_stays_null_while_worlds_is_unbuilt(self) -> None:
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIsNone(self.season.champion_team_id)

    def test_the_cursor_parks_on_the_worlds_phase(self) -> None:
        current = self.season.current_phase()
        self.assertIsNotNone(current)
        self.assertEqual(current.pk, _worlds_phase_row(self.season).pk)
        self.assertEqual(current.tournament_mode, "worlds")

    def test_repeated_completion_attempts_still_do_not_complete(self) -> None:
        for _ in range(3):
            self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")


class TestWorldsCrownsTheSeasonChampion(TestCase):
    """CONF-04 §5.4 / §11.5 item 8 - the rule CONF-01 opened and CONF-02 /
    CONF-03 each carried forward is finally closed. Neither
    ``complete_if_finished`` nor ``_stamp_champion_for_final_phase`` is edited:
    the flat Worlds row shape (§5.2) is what makes the existing path fire."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.phase,
            self.worlds_phase,
            self.worlds,
        ) = _worlds_built_season("WtCrown", [4, 4])

    def test_the_season_is_still_active_before_the_worlds_drain(self) -> None:
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertIsNone(self.season.champion_team_id)

    def test_draining_worlds_completes_the_season(self) -> None:
        _drain_bracket(self.worlds)
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")

    def test_the_season_champion_is_the_worlds_champion(self) -> None:
        _drain_bracket(self.worlds)
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.worlds.refresh_from_db()
        self.assertIsNotNone(self.worlds.champion_id)
        self.assertEqual(self.season.champion_team_id, self.worlds.champion_id)

    def test_the_champion_is_one_of_the_qualifiers(self) -> None:
        qualifier_ids = {q.team_id for q in self.season.worlds_qualifiers()}
        _drain_bracket(self.worlds)
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIn(self.season.champion_team_id, qualifier_ids)

    def test_the_worlds_bracket_is_completed_after_the_drain(self) -> None:
        _drain_bracket(self.worlds)
        self.worlds.refresh_from_db()
        self.assertEqual(self.worlds.state, "completed")

    def test_the_cursor_is_none_once_the_season_completes(self) -> None:
        _drain_bracket(self.worlds)
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIsNone(self.season.current_phase())

    def test_a_conference_champion_is_still_not_the_season_champion(self) -> None:
        # ADR-0035's rule survives: only the WORLDS bracket crowns the Season.
        _drain_bracket(self.worlds)
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        regional_champions = {
            _regional_playoff(self.phase, conference).champion_id
            for conference in self.conferences
        }
        self.assertEqual(len(regional_champions), 2)
        # The Season champion is ONE of them - the other is not crowned.
        self.assertEqual(len(regional_champions - {self.season.champion_team_id}), 1)


class TestWorldsChampionAtThree(TestCase):
    """The same crowning through a size-4 bracket with a bye (M = 3)."""

    def test_a_three_team_field_drains_to_a_season_champion(self) -> None:
        (
            season,
            _conferences,
            _groups,
            _phase,
            _worlds_phase,
            worlds,
        ) = _worlds_built_season("WtCrown3", [5, 4])
        _drain_bracket(worlds)
        season.complete_if_finished()
        season.refresh_from_db()
        worlds.refresh_from_db()
        self.assertEqual(worlds.state, "completed")
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, worlds.champion_id)


# ===========================================================================
# 14. The _final_tournament_phase narrowing, seen only through worlds_qualifiers
# ===========================================================================


class TestQualificationDoesNotRedirectAtWorlds(TestCase):
    """CONF-04 §3.1 / §11.5 item 14 - without the ``tournament_mode ==
    "worlds"`` skip in ``_final_tournament_phase``, ``worlds_qualifiers()``
    would read the WORLDS phase's own bracket: empty before the build and
    self-referential after it. Asserted ONLY through the public seam."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _regionals_drained_season("WtNarrow", [5, 4])

    def test_the_field_is_non_empty_with_the_worlds_phase_present(self) -> None:
        # The Worlds phase already exists (created at ``start_season``) and is
        # the highest-ordinal tournament phase - yet qualification still reads
        # the REGIONAL phase.
        self.assertIsNotNone(_worlds_phase_row(self.season))
        self.assertEqual(len(self.season.worlds_qualifiers()), 3)

    def test_the_field_is_identical_before_and_after_the_build(self) -> None:
        before = [
            (q.seed, q.team_id, q.tier, q.provenance)
            for q in self.season.worlds_qualifiers()
        ]
        self.assertTrue(self.season.build_pending_worlds_bracket())
        after = [
            (q.seed, q.team_id, q.tier, q.provenance)
            for q in self.season.worlds_qualifiers()
        ]
        self.assertEqual(before, after)

    def test_the_field_survives_the_worlds_bracket_being_drained(self) -> None:
        before = [(q.seed, q.team_id) for q in self.season.worlds_qualifiers()]
        self.season.build_pending_worlds_bracket()
        worlds_phase = _worlds_phase_row(self.season)
        worlds_phase.refresh_from_db()
        _drain_bracket(worlds_phase.tournament)
        after = [(q.seed, q.team_id) for q in self.season.worlds_qualifiers()]
        self.assertEqual(before, after)

    def test_the_worlds_bracket_participants_are_not_a_second_qualifier_source(
        self,
    ) -> None:
        # Naming hazard §13.6 - the derived ``WorldsQualifier`` list and the
        # ``TournamentParticipant`` queryset carry the same ids and seeds, and
        # remain different objects.
        self.season.build_pending_worlds_bracket()
        worlds_phase = _worlds_phase_row(self.season)
        worlds_phase.refresh_from_db()
        self.assertEqual(
            _participant_seeds(worlds_phase.tournament),
            _qualifier_seeds(self.season),
        )


# ===========================================================================
# 12. Byte-identity pins - 0 and 1 Conference
# ===========================================================================


class TestZeroConferenceSeasonIsUnchanged(TestCase):
    """CONF-04 §12 invariant 1 - a 0-Conference Season is byte-identical to
    ``conf-03-worlds-qualification`` in rows, reads, champion stamping and
    rendered DOM ids."""

    def setUp(self) -> None:
        self.season, self.teams, self.rr_phase, self.phase = _flat_season("WtFlat")
        _hand_play_rr(self.season, self.rr_phase, _ids(self.teams))
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.phase.refresh_from_db()

    def test_no_worlds_phase_is_created(self) -> None:
        self.assertIsNone(_worlds_phase_row(self.season))
        self.assertEqual(self.season.phases.count(), 2)

    def test_build_pending_worlds_bracket_returns_false(self) -> None:
        self.assertFalse(self.season.build_pending_worlds_bracket())

    def test_worlds_qualifiers_is_empty(self) -> None:
        self.assertEqual(self.season.worlds_qualifiers(), [])

    def test_only_the_single_season_wide_bracket_exists(self) -> None:
        self.assertEqual(Tournament.objects.count(), 1)
        self.assertIsNotNone(self.phase.tournament_id)

    def test_the_single_bracket_still_crowns_the_season_champion(self) -> None:
        _stamp_bracket_completed(self.phase.tournament, self.teams[0])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, self.teams[0].id)

    def test_the_playoffs_screen_carries_no_worlds_dom_id(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "-worlds")
        self.assertNotContains(response, 'id="league-playoffs-worlds"')

    def test_the_playoffs_screen_keys_are_the_bare_phase_ordinal(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        keys = [bracket["key"] for bracket in response.context["brackets"]]
        self.assertEqual(keys, [str(self.phase.ordinal)])
        self.assertEqual(
            [bracket["stage"] for bracket in response.context["brackets"]], [""]
        )
        self.assertEqual(
            [bracket["stage_label"] for bracket in response.context["brackets"]], [""]
        )


class TestOneConferenceSeasonIsUnchanged(TestCase):
    """CONF-04 §12 invariant 1 - the same pins on the 1-Conference shape, whose
    tournament phase carries a Season-wide bracket, not a regional one."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _conf_season("WtOne", [5])
        _hand_play_rr(self.season, self.rr_phase, _ids(self.groups[0]))
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.phase.refresh_from_db()

    def test_no_worlds_phase_is_created(self) -> None:
        self.assertIsNone(_worlds_phase_row(self.season))
        self.assertEqual(self.season.phases.count(), 2)

    def test_build_pending_worlds_bracket_returns_false(self) -> None:
        self.assertFalse(self.season.build_pending_worlds_bracket())

    def test_worlds_qualifiers_is_empty(self) -> None:
        self.assertEqual(self.season.worlds_qualifiers(), [])

    def test_the_single_bracket_still_crowns_the_season_champion(self) -> None:
        _stamp_bracket_completed(self.phase.tournament, self.groups[0][0])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertEqual(self.season.champion_team_id, self.groups[0][0].id)

    def test_the_playoffs_screen_carries_no_worlds_dom_id(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "-worlds")


class TestTwoConferenceSeasonWithNoTournamentPhase(TestCase):
    """CONF-04 §12 - a >= 2-Conference Season with NO tournament phase to
    qualify from grows no Worlds phase and still ends CHAMPIONLESS (the CONF-01
    rule the ADR explicitly keeps)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
        ) = _rr_only_conference_season("WtNoT", [4, 4])

    def test_no_worlds_phase_and_no_tournament_row(self) -> None:
        self.assertIsNone(_worlds_phase_row(self.season))
        self.assertEqual(Tournament.objects.count(), 0)

    def test_build_pending_worlds_bracket_returns_false(self) -> None:
        self.assertFalse(self.season.build_pending_worlds_bracket())

    def test_worlds_qualifiers_is_empty(self) -> None:
        self.assertEqual(self.season.worlds_qualifiers(), [])

    def test_the_season_completes_championless(self) -> None:
        _hand_play_rr(
            self.season,
            self.rr_phase,
            _ids(self.groups[0]) + _ids(self.groups[1]),
        )
        self.season.refresh_from_db()
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.champion_team_id)


# ===========================================================================
# 9.5. following_tournament_is_final - generalised, both old shapes reproduced
# ===========================================================================


class TestFollowingTournamentIsFinal(TestCase):
    """CONF-04 §9.5 - the expression generalises from "the next tournament
    phase IS the last phase" to "nothing but tournament phases follows it".

    Appending Worlds after the Regional-playoff phase would otherwise flip a
    season-ending playoff back to the mid-season "Until Tournament" label.
    ``_playoff_cursor_keys`` is read directly, exactly as
    ``test_regional_playoffs_drain.py`` already does.
    """

    def _final_flag(self, season: Season) -> bool:
        from matches.league_views import _playoff_cursor_keys

        (
            _active,
            _tournament_id,
            _completed,
            _following,
            following_tournament_is_final,
        ) = _playoff_cursor_keys(season)
        return following_tournament_is_final

    def _has_following(self, season: Season) -> bool:
        from matches.league_views import _playoff_cursor_keys

        return _playoff_cursor_keys(season)[3]

    def test_rr_then_tournament_is_final_unchanged(self) -> None:
        # Old: min([2]) == last 2 => True. New: no phase has ordinal > 2 =>
        # vacuously True.
        season, _teams, _phases = _flat_multi_phase_season(
            "WtFinal2", ["round_robin", "tournament"]
        )
        self.assertTrue(self._has_following(season))
        self.assertTrue(self._final_flag(season))

    def test_rr_tournament_rr_tournament_is_not_final_unchanged(self) -> None:
        # Old: min([2, 4]) != last 4 => False. New: phase 3 is round_robin =>
        # False.
        season, _teams, _phases = _flat_multi_phase_season(
            "WtFinal4",
            ["round_robin", "tournament", "round_robin", "tournament"],
        )
        self.assertTrue(self._has_following(season))
        self.assertFalse(self._final_flag(season))

    def test_rr_tournament_worlds_is_final(self) -> None:
        # The value this generalisation exists for. Old: min([2, 3]) != last 3
        # => False (WRONG - the label would read "Until Tournament"). New:
        # phase 3 is a tournament phase => True.
        season, _conferences, _groups, _rr, _phase = _conf_season("WtFinalW", [4, 4])
        phases = season.ordered_phases()
        self.assertEqual(
            [phase.phase_type for phase in phases],
            ["round_robin", "tournament", "tournament"],
        )
        self.assertEqual(phases[-1].tournament_mode, "worlds")
        self.assertTrue(self._has_following(season))
        self.assertTrue(self._final_flag(season))

    def test_a_single_round_robin_phase_has_no_following_tournament(self) -> None:
        season, _conferences, _groups, _rr = _rr_only_conference_season(
            "WtFinalRr", [4, 4]
        )
        self.assertFalse(self._has_following(season))
        self.assertFalse(self._final_flag(season))
