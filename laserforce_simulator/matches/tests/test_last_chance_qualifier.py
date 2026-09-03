"""CONF-03 — the Last-chance qualifier bracket: eager unseeded build, the
seeding seam, the completion gate, and the four drain hooks.

The seam contract is locked at ``.claude/worktrees/conf-03-seam-contract.md``
(§3, §5.3, §5.4, §5.6, §6); the design rationale is
[ADR-0036](../../docs/adr/0036-worlds-qualification-size-tiered-with-last-chance-bracket.md)
and the CONTEXT.md **Last-chance qualifier** term.

What is asserted here (contract §9.4 items 16-26):

* **The eager, UNSEEDED row** (§5.3) — a Conference of 9+ Teams gets a second
  ``Tournament`` at phase activation with ``qualifier_stage == "last_chance"``,
  ``state == "setup"``, ZERO participants and ZERO nodes. That is load-bearing:
  ``"setup" != "completed"`` already blocks ``_tournament_phase_complete``, and
  a node-less bracket already makes both drain loops no-op, so no engine change
  is needed and the cached ``tournaments_for_phase`` list in each drain loop
  already contains the row.
* **The seeding seam** (§5.4) — ``seed_pending_last_chance_brackets(phase)``
  returns the number of brackets it seeded, is idempotent, and fills the field
  with the 4 highest Standings finishers who are NOT already qualified
  (excluding BOTH the Conference champion and the tier-2 regular-season
  qualifier).
* **The gate** (invariant 5) — the phase refuses to advance while a Last-chance
  bracket is unseeded OR seeded-but-undrained.
* **The hooks** (§6) — one ``play_playoffs_task`` invocation seeds and drains;
  ``play_single_round`` is never a DEAD CLICK at the moment the last Regional
  playoff node resolves.
* **The byte-identity pins** (invariants 1-2) — a 0/1-Conference Season and the
  un-backfilled ``qualifier_stage == ""`` read rule of §3.2.

**No simulation where a gate or a derivation is under test (contract §9.1).**
The round-robin is hand-built (shared with ``test_regional_playoffs.py``), and
brackets are drained either by stamping the persisted rows or by driving the
real engine under a small ``ROUND_TICKS`` patch — the latter only where the
DRAIN itself is what is being exercised. Assertions are schema-level: row
counts, ids, seeds, states, return ints, status codes, context values.

**Internal detail is NOT asserted (contract §9.3).** ``Tournament.name`` is
matched by containment only — the em-dash separator is never hard-coded into an
equality; participant INSERTION order is never asserted, only ``seed`` values.

NOTE: this file requires the Code agent's ``Tournament.qualifier_stage`` +
migration ``0059_tournament_qualifier_stage`` + ``matches/worlds.py`` + the
``Season`` seeding / build seam + the four drain hooks to land. Until then
these tests are EXPECTED to fail — the TDD red state, not a defect here.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from matches.models import BracketNode, SeasonPhase, Tournament, TournamentParticipant
from matches.simulation import BatchSimulator
from matches.tests.test_regional_playoffs import (
    _built_regional_season,
    _conf_season,
    _flat_season,
    _hand_play_rr,
    _ids,
    _stamp_bracket_completed,
)

_FAST_TICKS = 30


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _last_chance_rows(phase):
    """Every Last-chance bracket of ``phase`` — the ONE permitted positive test
    on ``qualifier_stage`` (contract §3.2)."""
    return phase.regional_tournaments.filter(qualifier_stage="last_chance")


def _regional_row(phase, conference):
    """The Regional playoff of ``conference``: Conference-scoped and NOT a
    Last-chance row (the §3.2 read rule, which classifies an un-backfilled
    ``""`` correctly)."""
    return (
        phase.regional_tournaments.filter(conference=conference)
        .exclude(qualifier_stage="last_chance")
        .first()
    )


def _last_chance_row(phase, conference):
    return _last_chance_rows(phase).filter(conference=conference).first()


def _seed_order(tournament) -> list[int]:
    """Team ids of one bracket ordered by ``seed`` ascending (insertion order
    is never asserted — contract §9.3)."""
    return list(
        TournamentParticipant.objects.filter(tournament=tournament)
        .order_by("seed")
        .values_list("team_id", flat=True)
    )


def _resolved_node_count(tournament) -> int:
    return BracketNode.objects.filter(
        tournament=tournament, winner__isnull=False
    ).count()


def _lc_season(prefix: str, sizes=(9, 4), **kwargs):
    """A built Season whose final tournament phase holds a Last-chance row.

    ``sizes[0] >= 9`` so Conference 1 reaches a tier-3 slot. Returns
    ``(season, conferences, groups, rr_phase, phase)``.
    """
    return _built_regional_season(prefix, list(sizes), **kwargs)


# ===========================================================================
# 16. The eager, UNSEEDED row
# ===========================================================================


class TestEagerUnseededLastChanceRow(TestCase):
    """Right after ``activate_pending_tournament_phase`` a 9+-Team Conference
    carries a SECOND bracket that is deliberately unseeded (§5.3)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcEager", (9, 4))
        self.big, self.small = self.conferences
        self.regional = _regional_row(self.phase, self.big)
        self.last_chance = _last_chance_row(self.phase, self.big)

    def test_exactly_one_last_chance_row_exists(self) -> None:
        self.assertEqual(_last_chance_rows(self.phase).count(), 1)
        self.assertIsNotNone(self.last_chance)

    def test_the_row_is_conference_scoped_and_phase_linked(self) -> None:
        self.assertEqual(self.last_chance.conference_id, self.big.id)
        self.assertEqual(self.last_chance.season_phase_id, self.phase.id)

    def test_the_row_is_in_setup_state(self) -> None:
        self.assertEqual(self.last_chance.state, "setup")

    def test_the_row_has_zero_participants(self) -> None:
        self.assertEqual(self.last_chance.participants.count(), 0)

    def test_the_row_has_zero_bracket_nodes(self) -> None:
        self.assertEqual(self.last_chance.nodes.count(), 0)

    def test_the_row_has_no_champion(self) -> None:
        self.assertIsNone(self.last_chance.champion_id)

    def test_the_name_carries_the_last_chance_label(self) -> None:
        # Containment only — the em-dash separator is never asserted (§9.3).
        self.assertIn("Last Chance Qualifier", self.last_chance.name)
        self.assertIn(self.big.name, self.last_chance.name)

    def test_the_regional_sibling_is_active_with_a_full_field(self) -> None:
        self.assertEqual(self.regional.state, "active")
        self.assertEqual(self.regional.participants.count(), 9)
        self.assertGreater(self.regional.nodes.count(), 0)

    def test_the_regional_sibling_is_stamped_regional_playoff(self) -> None:
        self.assertEqual(self.regional.qualifier_stage, "regional_playoff")

    def test_the_small_conference_gets_no_last_chance_row(self) -> None:
        self.assertIsNone(_last_chance_row(self.phase, self.small))
        self.assertEqual(
            self.phase.regional_tournaments.filter(conference=self.small).count(), 1
        )

    def test_the_phase_holds_three_tournaments_in_total(self) -> None:
        self.assertEqual(self.phase.regional_tournaments.count(), 3)
        self.assertIsNone(self.phase.tournament_id)

    def test_reactivation_does_not_create_a_second_row(self) -> None:
        self.season.activate_pending_tournament_phase()
        self.assertEqual(_last_chance_rows(self.phase).count(), 1)
        self.assertEqual(self.phase.regional_tournaments.count(), 3)


# ===========================================================================
# 17. No row for a Conference of 8 or fewer
# ===========================================================================


class TestNoLastChanceRowForSmallConferences(TestCase):
    """Invariant 2 — a Conference of 8 or fewer Teams is byte-identical to
    CONF-02 in rows."""

    def test_four_and_eight_team_conferences_build_one_bracket_each(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "LcSmall", [4, 8]
        )
        self.assertEqual(_last_chance_rows(phase).count(), 0)
        self.assertEqual(phase.regional_tournaments.count(), 2)
        for conference in conferences:
            self.assertEqual(
                phase.regional_tournaments.filter(conference=conference).count(), 1
            )

    def test_the_eight_team_boundary_is_exclusive(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "LcEight", [8, 8]
        )
        self.assertEqual(_last_chance_rows(phase).count(), 0)

    def test_the_nine_team_boundary_is_inclusive(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "LcNine", [9, 9]
        )
        self.assertEqual(_last_chance_rows(phase).count(), 2)

    def test_seeding_is_a_no_op_when_no_row_exists(self) -> None:
        season, conferences, groups, _rr, phase = _built_regional_season(
            "LcSmallSeed", [4, 8]
        )
        for conference, group in zip(conferences, groups):
            _stamp_bracket_completed(_regional_row(phase, conference), group[0])
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 0)


# ===========================================================================
# 18. Only the FINAL tournament phase gets Last-chance brackets
# ===========================================================================


class TestOnlyTheFinalTournamentPhaseQualifies(TestCase):
    """§2.6 — a mid-season ``tournament`` phase builds its regional brackets
    exactly as CONF-02 does: no Last-chance row, no new DOM id."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.mid_phase,
        ) = _conf_season("LcMid", [9, 4])
        self.final_phase = SeasonPhase.objects.create(
            season=self.season,
            ordinal=3,
            phase_type="tournament",
            tournament_mode="standings",
            tournament_format="single_elimination",
            tournament_cut=0,
        )
        _hand_play_rr(
            self.season,
            self.rr_phase,
            _ids(self.groups[0]) + _ids(self.groups[1]),
        )
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.mid_phase.refresh_from_db()

    def test_the_mid_season_phase_builds_its_regionals(self) -> None:
        self.assertEqual(self.mid_phase.regional_tournaments.count(), 2)

    def test_the_mid_season_phase_gets_no_last_chance_row(self) -> None:
        self.assertEqual(_last_chance_rows(self.mid_phase).count(), 0)

    def test_seeding_the_mid_season_phase_is_a_no_op(self) -> None:
        for conference, group in zip(self.conferences, self.groups):
            _stamp_bracket_completed(
                _regional_row(self.mid_phase, conference), group[0]
            )
        self.assertEqual(
            self.season.seed_pending_last_chance_brackets(self.mid_phase), 0
        )

    def test_the_final_phase_does_get_a_last_chance_row(self) -> None:
        for conference, group in zip(self.conferences, self.groups):
            _stamp_bracket_completed(
                _regional_row(self.mid_phase, conference), group[0]
            )
        self.season.refresh_from_db()
        self.season.activate_pending_tournament_phase()
        self.final_phase.refresh_from_db()

        self.assertEqual(self.final_phase.regional_tournaments.count(), 3)
        self.assertEqual(_last_chance_rows(self.final_phase).count(), 1)
        self.assertEqual(
            _last_chance_row(self.final_phase, self.conferences[0]).state, "setup"
        )

    def test_a_round_robin_phase_is_never_seeded(self) -> None:
        self.assertEqual(
            self.season.seed_pending_last_chance_brackets(self.rr_phase), 0
        )

    def test_seeding_a_none_phase_returns_zero(self) -> None:
        self.assertEqual(self.season.seed_pending_last_chance_brackets(None), 0)


# ===========================================================================
# 19. The field excludes BOTH already-qualified Teams
# ===========================================================================


class TestLastChanceFieldExcludesQualifiedTeams(TestCase):
    """§2.4 — the 4 highest Standings finishers who are NOT already qualified,
    i.e. excluding the Conference champion AND the tier-2 regular-season
    qualifier. This is why the bracket is strictly sequential."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcField", (9, 4), cut=4)
        self.big = self.conferences[0]
        self.ranked = _ids(self.groups[0])  # the Conference's Standings order
        # RANK 2 wins the Regional playoff (the upset), so rank 1 takes the
        # tier-2 slot and the Last-chance field starts at rank 3.
        _stamp_bracket_completed(_regional_row(self.phase, self.big), self.groups[0][1])
        self.seeded = self.season.seed_pending_last_chance_brackets(self.phase)
        self.last_chance = _last_chance_row(self.phase, self.big)

    def test_exactly_one_bracket_was_seeded(self) -> None:
        self.assertEqual(self.seeded, 1)

    def test_the_bracket_holds_exactly_four_participants(self) -> None:
        self.assertEqual(self.last_chance.participants.count(), 4)

    def test_seeds_are_one_through_four(self) -> None:
        seeds = sorted(
            TournamentParticipant.objects.filter(
                tournament=self.last_chance
            ).values_list("seed", flat=True)
        )
        self.assertEqual(seeds, [1, 2, 3, 4])

    def test_the_field_is_ranks_three_through_six_in_rank_order(self) -> None:
        self.assertEqual(_seed_order(self.last_chance), self.ranked[2:6])

    def test_the_conference_champion_is_excluded(self) -> None:
        self.assertNotIn(self.groups[0][1].id, _seed_order(self.last_chance))

    def test_the_regular_season_qualifier_is_excluded(self) -> None:
        self.assertNotIn(self.groups[0][0].id, _seed_order(self.last_chance))

    def test_the_bracket_is_now_active_and_playable(self) -> None:
        self.assertEqual(self.last_chance.state, "active")
        self.assertGreater(self.last_chance.nodes.count(), 0)
        self.assertIsNotNone(self.last_chance.find_next_playable_node())

    def test_no_team_from_another_conference_enters_the_field(self) -> None:
        for team_id in _seed_order(self.last_chance):
            self.assertIn(team_id, self.ranked)

    def test_the_field_is_disjoint_from_the_worlds_qualified_pair(self) -> None:
        qualified = {self.groups[0][0].id, self.groups[0][1].id}
        self.assertEqual(qualified & set(_seed_order(self.last_chance)), set())


# ===========================================================================
# 20. The phase refuses to advance
# ===========================================================================


class TestPhaseGateBlocksOnLastChanceBracket(TestCase):
    """Invariant 5 — an unseeded OR undrained Last-chance bracket blocks
    ``_tournament_phase_complete`` for free (``"setup" != "completed"``)."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcGate", (9, 4), cut=4)
        for conference, group in zip(self.conferences, self.groups):
            _stamp_bracket_completed(_regional_row(self.phase, conference), group[0])

    def test_every_regional_playoff_is_drained(self) -> None:
        for conference in self.conferences:
            self.assertEqual(_regional_row(self.phase, conference).state, "completed")

    def test_an_unseeded_bracket_keeps_the_cursor_on_the_phase(self) -> None:
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertEqual(self.season.current_phase().id, self.phase.id)

    def test_a_seeded_but_undrained_bracket_still_blocks(self) -> None:
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 1)
        self.assertEqual(
            _last_chance_row(self.phase, self.conferences[0]).state, "active"
        )
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "active")
        self.assertEqual(self.season.current_phase().id, self.phase.id)

    def test_crowning_the_last_chance_bracket_releases_the_gate(self) -> None:
        self.season.seed_pending_last_chance_brackets(self.phase)
        last_chance = _last_chance_row(self.phase, self.conferences[0])
        _stamp_bracket_completed(last_chance, self.groups[0][2])
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.current_phase())

    def test_the_season_champion_stays_null_when_the_gate_releases(self) -> None:
        # Invariant 4 — this slice crowns NOTHING.
        self.season.seed_pending_last_chance_brackets(self.phase)
        _stamp_bracket_completed(
            _last_chance_row(self.phase, self.conferences[0]), self.groups[0][2]
        )
        self.season.complete_if_finished()
        self.season.refresh_from_db()
        self.assertIsNone(self.season.champion_team_id)


# ===========================================================================
# 21. One play_playoffs_task invocation seeds AND drains
# ===========================================================================


class TestPlayPlayoffsTaskSeedsAndDrainsInOneCall(TestCase):
    """§6.1's seed-then-continue: the no-progress exit seeds, then RETRIES the
    loop rather than exiting, so a single invocation finishes the phase."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcTask", (9, 4), cut=4)

    def test_one_call_leaves_every_tournament_completed(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        tournaments = list(self.phase.regional_tournaments.all())
        self.assertEqual(len(tournaments), 3)
        for tournament in tournaments:
            self.assertEqual(tournament.state, "completed")
            self.assertIsNotNone(tournament.champion_id)

    def test_one_call_seeds_and_drains_the_last_chance_bracket(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        last_chance = _last_chance_row(self.phase, self.conferences[0])
        self.assertEqual(last_chance.participants.count(), 4)
        self.assertEqual(last_chance.state, "completed")
        self.assertIsNotNone(last_chance.champion_id)

    def test_the_last_chance_winner_comes_from_its_own_conference(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        last_chance = _last_chance_row(self.phase, self.conferences[0])
        self.assertIn(last_chance.champion_id, _ids(self.groups[0]))

    def test_one_call_completes_the_season_with_a_null_champion(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.champion_team_id)

    def test_the_worlds_field_is_ready_after_the_single_call(self) -> None:
        from matches.tasks import play_playoffs_task

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            play_playoffs_task.apply(args=(self.season.id,))

        # 3 from the 9-Team Conference + 1 from the 4-Team one.
        field = self.season.worlds_qualifiers()
        self.assertEqual(len(field), 4)
        self.assertEqual([q.seed for q in field], [1, 2, 3, 4])


# ===========================================================================
# 22. Idempotence of the seeding seam
# ===========================================================================


class TestSeedPendingLastChanceBracketsIdempotence(TestCase):
    """§5.4 — ``0`` before the Regional playoff has a champion, a positive
    count on the call that seeds, ``0`` on every subsequent call."""

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcIdem", (9, 4), cut=4)
        self.big = self.conferences[0]

    def test_returns_zero_before_the_regional_playoff_has_a_champion(self) -> None:
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 0)
        self.assertEqual(_last_chance_row(self.phase, self.big).state, "setup")
        self.assertEqual(_last_chance_row(self.phase, self.big).participants.count(), 0)

    def test_returns_one_on_the_call_that_seeds(self) -> None:
        _stamp_bracket_completed(_regional_row(self.phase, self.big), self.groups[0][0])
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 1)

    def test_returns_zero_on_every_subsequent_call(self) -> None:
        _stamp_bracket_completed(_regional_row(self.phase, self.big), self.groups[0][0])
        self.season.seed_pending_last_chance_brackets(self.phase)
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 0)
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 0)

    def test_repeat_calls_create_no_extra_participants_or_nodes(self) -> None:
        _stamp_bracket_completed(_regional_row(self.phase, self.big), self.groups[0][0])
        self.season.seed_pending_last_chance_brackets(self.phase)
        last_chance = _last_chance_row(self.phase, self.big)
        before = (last_chance.participants.count(), last_chance.nodes.count())

        self.season.seed_pending_last_chance_brackets(self.phase)
        self.season.seed_pending_last_chance_brackets(self.phase)

        last_chance.refresh_from_db()
        self.assertEqual(
            (last_chance.participants.count(), last_chance.nodes.count()), before
        )
        self.assertEqual(
            TournamentParticipant.objects.filter(tournament=last_chance).count(), 4
        )

    def test_two_big_conferences_seed_independently(self) -> None:
        season, conferences, groups, _rr, phase = _lc_season("LcTwoBig", (9, 9), cut=4)
        _stamp_bracket_completed(_regional_row(phase, conferences[0]), groups[0][0])
        # Only Conference 1 is ready.
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 1)
        self.assertEqual(_last_chance_row(phase, conferences[0]).state, "active")
        self.assertEqual(_last_chance_row(phase, conferences[1]).state, "setup")

        _stamp_bracket_completed(_regional_row(phase, conferences[1]), groups[1][0])
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 1)
        self.assertEqual(_last_chance_row(phase, conferences[1]).state, "active")
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 0)


# ===========================================================================
# 23. tournaments_for_phase order
# ===========================================================================


class TestTournamentsForPhaseOrder(TestCase):
    """§5.8 — within a Conference the Regional playoff always sorts BEFORE its
    Last-chance sibling, and Conferences appear in ordinal order."""

    def test_regional_precedes_its_last_chance_sibling(self) -> None:
        season, conferences, groups, _rr, phase = _lc_season("LcOrder", (9, 4))
        tournaments = season.tournaments_for_phase(phase)
        self.assertEqual(len(tournaments), 3)
        self.assertEqual(
            [(t.conference_id, t.qualifier_stage) for t in tournaments],
            [
                (conferences[0].id, "regional_playoff"),
                (conferences[0].id, "last_chance"),
                (conferences[1].id, "regional_playoff"),
            ],
        )

    def test_two_big_conferences_interleave_by_ordinal(self) -> None:
        season, conferences, groups, _rr, phase = _lc_season("LcOrder2", (9, 9))
        tournaments = season.tournaments_for_phase(phase)
        self.assertEqual(len(tournaments), 4)
        self.assertEqual(
            [t.conference_id for t in tournaments],
            [
                conferences[0].id,
                conferences[0].id,
                conferences[1].id,
                conferences[1].id,
            ],
        )
        self.assertEqual(
            [t.qualifier_stage for t in tournaments],
            ["regional_playoff", "last_chance", "regional_playoff", "last_chance"],
        )

    def test_a_small_conference_contributes_exactly_one_row(self) -> None:
        season, conferences, groups, _rr, phase = _lc_season("LcOrder3", (9, 5))
        tournaments = season.tournaments_for_phase(phase)
        by_conference: dict[int, int] = {}
        for tournament in tournaments:
            by_conference[tournament.conference_id] = (
                by_conference.get(tournament.conference_id, 0) + 1
            )
        self.assertEqual(by_conference[conferences[0].id], 2)
        self.assertEqual(by_conference[conferences[1].id], 1)

    def test_the_result_is_still_a_plain_list(self) -> None:
        season, _conferences, _groups, _rr, phase = _lc_season("LcOrder4", (9, 4))
        self.assertIsInstance(season.tournaments_for_phase(phase), list)


# ===========================================================================
# 24. play_single_round is never a DEAD CLICK
# ===========================================================================


class TestPlaySingleRoundIsNeverADeadClick(TestCase):
    """§6.3 — the click that resolves the LAST Regional-playoff node would
    otherwise leave every regional bracket ``completed`` and the Last-chance
    sibling ``setup``, so the dashboard would read neither "active" nor
    "completed" and would HIDE the playoff controls. Seeding before the
    redirect closes that window.

    Conference 2's Regional playoff is stamped complete up front so that the
    final Conference-1 node really is the last regional node of the phase —
    the exact transient the fix exists for.
    """

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcClick", (9, 4), cut=4)
        self.big, self.small = self.conferences
        _stamp_bracket_completed(
            _regional_row(self.phase, self.small), self.groups[1][0]
        )
        self.url = reverse("play_single_round", args=[self.season.id])

    def _click(self):
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            return self.client.post(self.url)

    def _dashboard(self):
        return self.client.get(reverse("season_dashboard", args=[self.season.id]))

    def test_the_dead_click_window_is_closed(self) -> None:
        transition_seen = False
        for _ in range(20):
            response = self._click()
            self.assertEqual(response.status_code, 302)
            regional = _regional_row(self.phase, self.big)
            if regional.state != "completed":
                continue

            # THE MOMENT UNDER TEST: every Regional playoff of the phase has
            # drained. Without the §6.3 hook the Last-chance sibling would
            # still be ``setup`` here and the controls would vanish.
            last_chance = _last_chance_row(self.phase, self.big)
            self.assertEqual(last_chance.state, "active")
            self.assertEqual(last_chance.participants.count(), 4)

            dashboard = self._dashboard()
            self.assertEqual(dashboard.status_code, 200)
            self.assertIs(dashboard.context["playoff_phase_active"], True)
            self.assertIs(dashboard.context["playoff_completed"], False)
            transition_seen = True
            break
        self.assertTrue(
            transition_seen,
            "the Regional playoff never drained — fixture or view regression",
        )

    def test_further_clicks_drain_the_last_chance_bracket_to_a_champion(self) -> None:
        for _ in range(40):
            self.season.refresh_from_db()
            if self.season.state == "completed":
                break
            self._click()

        last_chance = _last_chance_row(self.phase, self.big)
        self.assertEqual(last_chance.state, "completed")
        self.assertIsNotNone(last_chance.champion_id)
        self.assertIn(last_chance.champion_id, _ids(self.groups[0]))

        self.season.refresh_from_db()
        self.assertEqual(self.season.state, "completed")
        self.assertIsNone(self.season.champion_team_id)

    def test_no_click_is_wasted_once_seeding_has_happened(self) -> None:
        # Drive to the transition, then assert the NEXT click resolves a node
        # in the newly-seeded bracket rather than doing nothing.
        for _ in range(20):
            self._click()
            if _regional_row(self.phase, self.big).state == "completed":
                break
        last_chance = _last_chance_row(self.phase, self.big)
        before = _resolved_node_count(last_chance)
        self._click()
        self.assertGreater(_resolved_node_count(last_chance), before)


# ===========================================================================
# 25. Byte-identity pin — 0 and 1 Conference
# ===========================================================================


class TestZeroAndOneConferenceByteIdentityPin(TestCase):
    """Invariant 1 — a Season with 0 or 1 Conference builds no ``last_chance``
    row, seeds nothing, and STILL stamps ``Season.champion_team``."""

    def test_zero_conference_season_creates_no_last_chance_row(self) -> None:
        season, teams, rr_phase, phase = _flat_season("LcZero")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()

        self.assertEqual(Tournament.objects.count(), 1)
        self.assertEqual(
            Tournament.objects.filter(qualifier_stage="last_chance").count(), 0
        )
        self.assertEqual(phase.regional_tournaments.count(), 0)

    def test_zero_conference_embedded_bracket_has_a_blank_stage(self) -> None:
        season, teams, rr_phase, phase = _flat_season("LcZeroStage")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        # §3.2 row 2: a Season-wide embed is not a qualifier bracket at all.
        self.assertEqual(phase.tournament.qualifier_stage, "")
        self.assertIsNone(phase.tournament.conference_id)

    def test_zero_conference_seeding_returns_zero(self) -> None:
        season, teams, rr_phase, phase = _flat_season("LcZeroSeed")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 0)

    def test_zero_conference_still_stamps_the_season_champion(self) -> None:
        season, teams, rr_phase, phase = _flat_season("LcZeroChamp")
        _hand_play_rr(season, rr_phase, _ids(teams))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, teams[0])
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, teams[0].id)

    def test_one_conference_of_nine_teams_creates_no_last_chance_row(self) -> None:
        # Size alone does NOT arm the Last-chance bracket — a 1-Conference
        # Season degenerates to the Season-wide path (contract §5.5 step 1).
        season, conferences, groups, rr_phase, phase = _conf_season("LcOneBig", [9])
        _hand_play_rr(season, rr_phase, _ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()

        self.assertEqual(Tournament.objects.count(), 1)
        self.assertEqual(
            Tournament.objects.filter(qualifier_stage="last_chance").count(), 0
        )
        self.assertEqual(season.seed_pending_last_chance_brackets(phase), 0)

    def test_one_conference_still_stamps_the_season_champion(self) -> None:
        season, conferences, groups, rr_phase, phase = _conf_season("LcOneChamp", [9])
        _hand_play_rr(season, rr_phase, _ids(groups[0]))
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        _stamp_bracket_completed(phase.tournament, groups[0][0])
        season.complete_if_finished()
        season.refresh_from_db()
        self.assertEqual(season.state, "completed")
        self.assertEqual(season.champion_team_id, groups[0][0].id)


# ===========================================================================
# 26. Byte-identity pin — the no-backfill READ RULE
# ===========================================================================


class TestUnbackfilledRegionalRowReadRule(TestCase):
    """§3.2 — a CONF-02 regional row created BEFORE migration 0059 keeps
    ``qualifier_stage == ""`` and must still classify as the Regional playoff:
    ``conference_id is not None and qualifier_stage != "last_chance"``.
    """

    def setUp(self) -> None:
        (
            self.season,
            self.conferences,
            self.groups,
            self.rr_phase,
            self.phase,
        ) = _lc_season("LcLegacy", (9, 4), cut=4)
        self.big, self.small = self.conferences
        # Simulate the un-backfilled state: blank out the regional rows only.
        self.phase.regional_tournaments.exclude(qualifier_stage="last_chance").update(
            qualifier_stage=""
        )

    def test_fixture_precondition_the_regional_rows_are_blank(self) -> None:
        blanks = self.phase.regional_tournaments.filter(qualifier_stage="")
        self.assertEqual(blanks.count(), 2)
        self.assertEqual(_last_chance_rows(self.phase).count(), 1)

    def test_seeding_reads_the_champion_off_the_blank_row(self) -> None:
        _stamp_bracket_completed(_regional_row(self.phase, self.big), self.groups[0][0])
        self.assertEqual(self.season.seed_pending_last_chance_brackets(self.phase), 1)
        last_chance = _last_chance_row(self.phase, self.big)
        self.assertEqual(last_chance.state, "active")
        self.assertEqual(_seed_order(last_chance), _ids(self.groups[0])[2:6])

    def test_the_blank_row_is_the_tier_one_source_in_worlds_qualifiers(self) -> None:
        from matches.worlds import PROVENANCE_CHAMPION, QUALIFIER_TIER_CHAMPION

        for conference, group in zip(self.conferences, self.groups):
            _stamp_bracket_completed(_regional_row(self.phase, conference), group[0])
        self.season.seed_pending_last_chance_brackets(self.phase)
        _stamp_bracket_completed(
            _last_chance_row(self.phase, self.big), self.groups[0][2]
        )

        field = self.season.worlds_qualifiers()
        tier1 = [
            q
            for q in field
            if q.tier == QUALIFIER_TIER_CHAMPION and q.conference_id == self.big.id
        ]
        self.assertEqual([q.team_id for q in tier1], [self.groups[0][0].id])
        self.assertEqual(tier1[0].provenance, PROVENANCE_CHAMPION)

    def test_the_blank_row_renders_with_the_unsuffixed_key(self) -> None:
        response = self.client.get(
            reverse("league_playoffs", kwargs={"league_id": self.season.league_id})
        )
        self.assertEqual(response.status_code, 200)
        keys = [entry["key"] for entry in response.context["brackets"]]
        self.assertIn(f"{self.phase.ordinal}-{self.big.ordinal}", keys)
        self.assertIn(f"{self.phase.ordinal}-{self.small.ordinal}", keys)
        # Only the genuine Last-chance row carries the ``-lc`` discriminator.
        self.assertEqual(
            len([k for k in keys if k.endswith("-lc")]),
            1,
        )


# ===========================================================================
# 34. REGRESSION — a phase format whose advancer counts exceed a 4-Team field
# ===========================================================================


class TestLastChanceFormatFallsBackWhenFieldTooSmall(TestCase):
    """The Last-chance field is ALWAYS exactly 4 Teams, so a phase configured
    ``round_robin_double_elim`` with any combo except 4/0 asks for more
    participants than the bracket can ever hold.

    Before the fix the builder copied ``tournament_format`` + the advancer
    counts verbatim, so ``lock_and_build`` raised
    ``ValidationError("wb_advancers exceeds participant count.")`` at SEEDING
    time — inside the atomic ``seed_pending_last_chance_brackets``, uncaught at
    all four hook sites, and re-raised on every retry, so the phase could never
    complete and the Season was bricked. The builder now falls back to a plain
    single-elimination knockout for exactly this case.
    """

    def _big_conference_season(self, prefix: str, *, wb: int, lb: int):
        """A 9+9-Team Season whose tournament phase is RR->DE ``wb/lb``.

        BOTH Conferences are 9 Teams so the REGIONAL brackets themselves satisfy
        ``lock_and_build``'s ``wb <= n`` / ``wb + lb <= n`` count check — a
        pre-existing CONF-02 limit that is NOT what this class is about. That
        isolates the 4-Team Last-chance field as the only place the combo can
        overflow, which is exactly the regression under test.

        The phase config is written BEFORE activation, so the Last-chance row is
        built from it.
        """
        season, conferences, groups, rr_phase, phase = _conf_season(
            prefix, [9, 9], fmt="round_robin_double_elim"
        )
        phase.wb_advancers = wb
        phase.lb_advancers = lb
        phase.save(update_fields=["wb_advancers", "lb_advancers"])
        order = [team.id for group in groups for team in group]
        _hand_play_rr(season, rr_phase, order)
        season.refresh_from_db()
        season.activate_pending_tournament_phase()
        phase.refresh_from_db()
        return season, conferences, groups, phase

    def test_oversized_combo_falls_back_to_single_elimination(self) -> None:
        season, conferences, _groups, phase = self._big_conference_season(
            "LcFmtBig", wb=8, lb=0
        )
        row = _last_chance_row(phase, conferences[0])
        self.assertIsNotNone(row)
        self.assertEqual(row.format, "single_elimination")
        self.assertEqual(row.wb_advancers, 0)
        self.assertEqual(row.lb_advancers, 0)

    def test_four_two_combo_also_falls_back(self) -> None:
        # 4 + 2 = 6 > 4, so even the smallest non-zero ``lb`` overflows.
        season, conferences, _groups, phase = self._big_conference_season(
            "LcFmtFourTwo", wb=4, lb=2
        )
        row = _last_chance_row(phase, conferences[0])
        self.assertEqual(row.format, "single_elimination")
        self.assertEqual(row.lb_advancers, 0)

    def test_fitting_combo_is_copied_through_unchanged(self) -> None:
        # 4/0 fits a 4-Team field exactly, so the phase format is honoured.
        season, conferences, _groups, phase = self._big_conference_season(
            "LcFmtFits", wb=4, lb=0
        )
        row = _last_chance_row(phase, conferences[0])
        self.assertEqual(row.format, "round_robin_double_elim")
        self.assertEqual(row.wb_advancers, 4)

    def test_seeding_an_oversized_phase_does_not_raise_and_activates(self) -> None:
        """The load-bearing assertion: seeding must not blow up the phase."""
        season, conferences, groups, phase = self._big_conference_season(
            "LcFmtSeed", wb=8, lb=0
        )
        regional = _regional_row(phase, conferences[0])
        _stamp_bracket_completed(regional, groups[0][0])

        # Would raise ValidationError before the fix.
        seeded = season.seed_pending_last_chance_brackets(phase)

        self.assertEqual(seeded, 1)
        row = _last_chance_row(phase, conferences[0])
        self.assertEqual(row.state, "active")
        self.assertEqual(
            TournamentParticipant.objects.filter(tournament=row).count(), 4
        )
