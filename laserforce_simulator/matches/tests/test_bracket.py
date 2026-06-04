"""LG-02a — Pure-unit tests for ``matches/bracket.py``.

No DB, no Django imports in the assertion path. The seam contract is locked
at ``.claude/worktrees/lg-02a-seam-contract.md`` (§2). Mirrors the LG-01
``test_standings.py`` / ``test_schedule_generator.py`` precedent — pure
``SimpleTestCase`` with hand-crafted inputs, plus the
``TestNoDjangoImportsLeaked`` subprocess fresh-import check.

The pure module owns bracket STRUCTURE + Seeding + bye placement + tie-break
math. Frozen import allowlist: ``dataclasses`` / ``typing`` / ``math`` /
``collections`` — NO Django, NO ORM, NO ``random``, NO ``datetime``.

These assertions WILL fail until the Code agent lands ``matches/bracket.py``
(the module does not yet exist); that is expected for the parallel build.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches.bracket import (
    BracketNodeSpec,
    ParticipantSpec,
    advance_winner,
    break_tie,
    build_bracket,
    default_seed_order,
    find_next_node,
    resolve_bye_chain,
    stage_progress,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _participants(n: int) -> list[ParticipantSpec]:
    """``n`` participants, Bracket seed 1..n, team_id = 100 + seed."""
    return [ParticipantSpec(team_id=100 + s, seed=s) for s in range(1, n + 1)]


def _node_dict(
    *,
    bracket_round: int,
    position: int,
    team_a_id: int | None = None,
    team_b_id: int | None = None,
    seed_a: int | None = None,
    seed_b: int | None = None,
    is_bye: bool = False,
    winner_id: int | None = None,
    wins_a: int = 0,
    wins_b: int = 0,
    series_length: int = 1,
    advances_to: tuple[int, int] | None = None,
    advances_to_slot: str | None = None,
) -> dict:
    """Build one flattened node dict matching the post-LG-02b ``_node_to_dict``
    seam keys (carries ``wins_a`` / ``wins_b`` / ``series_length``; the old
    ``match_id`` key was dropped when ``BracketNode.match`` was removed)."""
    return {
        "bracket_round": bracket_round,
        "position": position,
        "team_a_id": team_a_id,
        "team_b_id": team_b_id,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "is_bye": is_bye,
        "winner_id": winner_id,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "series_length": series_length,
        "advances_to": advances_to,
        "advances_to_slot": advances_to_slot,
    }


# ---------------------------------------------------------------------------
# §2b — default_seed_order
# ---------------------------------------------------------------------------


class TestDefaultSeedOrder(SimpleTestCase):
    """Rating-desc Seeding with team_id ASC tiebreak."""

    def test_orders_by_rating_descending(self) -> None:
        order = default_seed_order([(1, 40.0), (2, 90.0), (3, 70.0)])
        # Best rating first (index 0 == Bracket seed 1).
        self.assertEqual(order, [2, 3, 1])

    def test_team_id_ascending_tiebreak_on_equal_rating(self) -> None:
        order = default_seed_order([(5, 50.0), (2, 50.0), (8, 50.0)])
        self.assertEqual(order, [2, 5, 8])

    def test_empty_input_returns_empty(self) -> None:
        self.assertEqual(default_seed_order([]), [])

    def test_single_team(self) -> None:
        self.assertEqual(default_seed_order([(7, 12.5)]), [7])

    def test_returns_only_team_ids(self) -> None:
        order = default_seed_order([(1, 10.0), (2, 20.0)])
        self.assertEqual(set(order), {1, 2})


# ---------------------------------------------------------------------------
# §2b — build_bracket (power of two)
# ---------------------------------------------------------------------------


class TestBuildBracketPowerOfTwo(SimpleTestCase):
    """N = 4 / 8 / 16 — full bracket, no byes."""

    def test_n4_node_count_is_3(self) -> None:
        # 4 participants ⇒ 2 round-1 nodes + 1 final = 3 nodes.
        nodes = build_bracket(_participants(4))
        self.assertEqual(len(nodes), 3)

    def test_n8_node_count_is_7(self) -> None:
        nodes = build_bracket(_participants(8))
        self.assertEqual(len(nodes), 7)

    def test_n16_node_count_is_15(self) -> None:
        nodes = build_bracket(_participants(16))
        self.assertEqual(len(nodes), 15)

    def test_returns_bracketnodespec_instances(self) -> None:
        nodes = build_bracket(_participants(4))
        for n in nodes:
            self.assertIsInstance(n, BracketNodeSpec)

    def test_no_byes_for_power_of_two(self) -> None:
        nodes = build_bracket(_participants(8))
        self.assertFalse(any(n.is_bye for n in nodes))

    def test_ordered_by_round_then_position(self) -> None:
        nodes = build_bracket(_participants(8))
        keys = [(n.bracket_round, n.position) for n in nodes]
        self.assertEqual(keys, sorted(keys))

    def test_n4_round1_pairing_1vN_2v_n_minus_1(self) -> None:
        # Standard seeding: 1v4, 2v3 in round 1.
        nodes = build_bracket(_participants(4))
        r1 = [n for n in nodes if n.bracket_round == 1]
        pairs = {frozenset({n.seed_a, n.seed_b}) for n in r1}
        self.assertEqual(pairs, {frozenset({1, 4}), frozenset({2, 3})})

    def test_n8_round1_pairing(self) -> None:
        nodes = build_bracket(_participants(8))
        r1 = [n for n in nodes if n.bracket_round == 1]
        pairs = {frozenset({n.seed_a, n.seed_b}) for n in r1}
        self.assertEqual(
            pairs,
            {
                frozenset({1, 8}),
                frozenset({2, 7}),
                frozenset({3, 6}),
                frozenset({4, 5}),
            },
        )

    def test_final_node_has_no_advances_to(self) -> None:
        nodes = build_bracket(_participants(4))
        final = max(nodes, key=lambda n: n.bracket_round)
        self.assertIsNone(final.advances_to)
        self.assertIsNone(final.advances_to_slot)

    def test_every_non_final_node_wires_to_a_parent(self) -> None:
        nodes = build_bracket(_participants(8))
        max_round = max(n.bracket_round for n in nodes)
        for n in nodes:
            if n.bracket_round < max_round:
                self.assertIsNotNone(n.advances_to, f"node {n} has no parent")
                self.assertIn(n.advances_to_slot, ("a", "b"))

    def test_advances_to_slot_by_position_parity(self) -> None:
        # Two feeders of one parent fill slots a (even position) and b (odd).
        nodes = build_bracket(_participants(4))
        r1 = sorted(
            (n for n in nodes if n.bracket_round == 1), key=lambda n: n.position
        )
        self.assertEqual(r1[0].advances_to_slot, "a")
        self.assertEqual(r1[1].advances_to_slot, "b")

    def test_round1_slots_fully_populated_for_power_of_two(self) -> None:
        nodes = build_bracket(_participants(4))
        for n in (x for x in nodes if x.bracket_round == 1):
            self.assertIsNotNone(n.team_a_id)
            self.assertIsNotNone(n.team_b_id)

    def test_team_ids_map_from_participant_seeds(self) -> None:
        nodes = build_bracket(_participants(4))
        # team_id is 100 + seed per the fixture; verify the wiring.
        for n in (x for x in nodes if x.bracket_round == 1):
            if n.seed_a is not None:
                self.assertEqual(n.team_a_id, 100 + n.seed_a)
            if n.seed_b is not None:
                self.assertEqual(n.team_b_id, 100 + n.seed_b)


# ---------------------------------------------------------------------------
# §2b — build_bracket (byes / non-power-of-2)
# ---------------------------------------------------------------------------


class TestBuildBracketWithByes(SimpleTestCase):
    """N = 5 / 6 / 12 — byes for the top Bracket seeds."""

    def test_n5_has_three_byes(self) -> None:
        # Next power of two >= 5 is 8; size - N = 3 top seeds get a bye.
        nodes = build_bracket(_participants(5))
        byes = [n for n in nodes if n.is_bye]
        self.assertEqual(len(byes), 3)

    def test_n6_has_two_byes(self) -> None:
        nodes = build_bracket(_participants(6))
        byes = [n for n in nodes if n.is_bye]
        self.assertEqual(len(byes), 2)

    def test_n12_has_four_byes(self) -> None:
        # Next power of two >= 12 is 16; 16 - 12 = 4 byes.
        nodes = build_bracket(_participants(12))
        byes = [n for n in nodes if n.is_bye]
        self.assertEqual(len(byes), 4)

    def test_byes_go_to_top_seeds(self) -> None:
        nodes = build_bracket(_participants(5))
        bye_seeds = {n.seed_a for n in nodes if n.is_bye and n.seed_a is not None}
        # Top 3 seeds (1, 2, 3) receive byes.
        self.assertEqual(bye_seeds, {1, 2, 3})

    def test_bye_node_has_winner_preresolved(self) -> None:
        nodes = build_bracket(_participants(5))
        for n in (x for x in nodes if x.is_bye):
            self.assertIsNotNone(n.winner_id, f"bye node {n} has no winner_id")
            # The bye team carries forward.
            self.assertEqual(n.winner_id, n.team_a_id)

    def test_bye_node_has_one_empty_slot(self) -> None:
        nodes = build_bracket(_participants(5))
        for n in (x for x in nodes if x.is_bye):
            self.assertIsNone(n.team_b_id)
            self.assertIsNone(n.seed_b)

    def test_n5_non_bye_round1_pairing(self) -> None:
        # Only seeds 4 and 5 actually play in round 1 (4v5).
        nodes = build_bracket(_participants(5))
        played = [n for n in nodes if n.bracket_round == 1 and not n.is_bye]
        self.assertEqual(len(played), 1)
        self.assertEqual(
            frozenset({played[0].seed_a, played[0].seed_b}), frozenset({4, 5})
        )

    def test_n5_ordered_by_round_then_position(self) -> None:
        nodes = build_bracket(_participants(5))
        keys = [(n.bracket_round, n.position) for n in nodes]
        self.assertEqual(keys, sorted(keys))


# ---------------------------------------------------------------------------
# §2b — build_bracket errors
# ---------------------------------------------------------------------------


class TestBuildBracketErrors(SimpleTestCase):
    """N < 4 and duplicate seeds / team_ids raise ValueError."""

    def test_n3_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_bracket(_participants(3))

    def test_n0_raises_value_error(self) -> None:
        with self.assertRaises(ValueError):
            build_bracket([])

    def test_duplicate_seed_raises_value_error(self) -> None:
        dup = [
            ParticipantSpec(team_id=101, seed=1),
            ParticipantSpec(team_id=102, seed=1),
            ParticipantSpec(team_id=103, seed=3),
            ParticipantSpec(team_id=104, seed=4),
        ]
        with self.assertRaises(ValueError):
            build_bracket(dup)

    def test_duplicate_team_id_raises_value_error(self) -> None:
        dup = [
            ParticipantSpec(team_id=101, seed=1),
            ParticipantSpec(team_id=101, seed=2),
            ParticipantSpec(team_id=103, seed=3),
            ParticipantSpec(team_id=104, seed=4),
        ]
        with self.assertRaises(ValueError):
            build_bracket(dup)


# ---------------------------------------------------------------------------
# §2b — find_next_node
# ---------------------------------------------------------------------------


class TestFindNextNode(SimpleTestCase):
    """Lowest (bracket_round, position) playable node; skips byes/played."""

    def test_returns_lowest_ready_node(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=5,
                seed_a=2,
                seed_b=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_skips_bye_nodes(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=1,
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=4,
                team_b_id=5,
                seed_a=4,
                seed_b=5,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 1))

    def test_skips_already_played_nodes(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=1,  # Bo1 Series already clinched by team_a
                winner_id=1,
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=5,
                seed_a=2,
                seed_b=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 1))

    def test_skips_nodes_with_an_empty_slot(self) -> None:
        # A later-round node whose feeder hasn't resolved (team_b_id None).
        nodes = [
            _node_dict(
                bracket_round=2, position=0, team_a_id=1, team_b_id=None, seed_a=1
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_returns_none_when_nothing_ready(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=1,  # Bo1 Series already clinched -> nothing playable
                winner_id=1,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_empty_list_returns_none(self) -> None:
        self.assertIsNone(find_next_node([]))


# ---------------------------------------------------------------------------
# §2b — advance_winner
# ---------------------------------------------------------------------------


class TestAdvanceWinner(SimpleTestCase):
    """Parent-slot mutation dicts; slot a/b by feeder position parity."""

    def _bracket_dicts(self) -> list[dict]:
        # Two round-1 feeders into a final at (2, 0).
        return [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=104,
                seed_a=1,
                seed_b=4,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=102,
                team_b_id=103,
                seed_a=2,
                seed_b=3,
                advances_to=(2, 0),
                advances_to_slot="b",
            ),
            _node_dict(bracket_round=2, position=0),
        ]

    def test_advances_into_slot_a(self) -> None:
        muts = advance_winner(
            self._bracket_dicts(), (1, 0), winner_id=101, winner_seed=1
        )
        self.assertEqual(len(muts), 1)
        m = muts[0]
        self.assertEqual(m["bracket_round"], 2)
        self.assertEqual(m["position"], 0)
        self.assertEqual(m["slot"], "a")
        self.assertEqual(m["team_id"], 101)
        self.assertEqual(m["seed"], 1)

    def test_advances_into_slot_b(self) -> None:
        muts = advance_winner(
            self._bracket_dicts(), (1, 1), winner_id=102, winner_seed=2
        )
        self.assertEqual(len(muts), 1)
        m = muts[0]
        self.assertEqual(m["slot"], "b")
        self.assertEqual(m["team_id"], 102)
        self.assertEqual(m["seed"], 2)

    def test_final_node_returns_empty_list(self) -> None:
        muts = advance_winner(
            self._bracket_dicts(), (2, 0), winner_id=101, winner_seed=1
        )
        self.assertEqual(muts, [])

    def test_mutation_dict_has_five_keys(self) -> None:
        muts = advance_winner(
            self._bracket_dicts(), (1, 0), winner_id=101, winner_seed=1
        )
        self.assertEqual(
            set(muts[0].keys()),
            {"bracket_round", "position", "slot", "team_id", "seed"},
        )


# ---------------------------------------------------------------------------
# §2b — resolve_bye_chain
# ---------------------------------------------------------------------------


class TestResolveByeChain(SimpleTestCase):
    """Cascade byes at build time into parent slots."""

    def test_bye_promotes_into_parent_slot(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=101,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=104,
                team_b_id=105,
                seed_a=4,
                seed_b=5,
                advances_to=(2, 0),
                advances_to_slot="b",
            ),
            _node_dict(bracket_round=2, position=0),
        ]
        muts = resolve_bye_chain(nodes)
        self.assertTrue(
            any(
                m["bracket_round"] == 2
                and m["position"] == 0
                and m["slot"] == "a"
                and m["team_id"] == 101
                for m in muts
            ),
            f"bye did not promote: {muts}",
        )

    def test_no_byes_returns_empty(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=104,
                seed_a=1,
                seed_b=4,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=102,
                team_b_id=103,
                seed_a=2,
                seed_b=3,
                advances_to=(2, 0),
                advances_to_slot="b",
            ),
            _node_dict(bracket_round=2, position=0),
        ]
        self.assertEqual(resolve_bye_chain(nodes), [])

    def test_mutation_shape_matches_advance_winner(self) -> None:
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=101,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _node_dict(bracket_round=2, position=0),
        ]
        muts = resolve_bye_chain(nodes)
        if muts:
            self.assertEqual(
                set(muts[0].keys()),
                {"bracket_round", "position", "slot", "team_id", "seed"},
            )


# ---------------------------------------------------------------------------
# §2b — break_tie
# ---------------------------------------------------------------------------


class TestBreakTie(SimpleTestCase):
    """Higher best-round-score wins; equal => lower Bracket seed wins."""

    def test_higher_best_round_score_advances(self) -> None:
        # seed_a=1 best 500, seed_b=4 best 800 ⇒ seed_b advances.
        winning_seed = break_tie(1, 500, 4, 800)
        self.assertEqual(winning_seed, 4)

    def test_higher_best_round_score_other_direction(self) -> None:
        winning_seed = break_tie(2, 900, 3, 400)
        self.assertEqual(winning_seed, 2)

    def test_equal_best_round_score_lower_seed_wins(self) -> None:
        # Equal best ⇒ higher Bracket seed (lower seed int) advances.
        winning_seed = break_tie(2, 600, 5, 600)
        self.assertEqual(winning_seed, 2)

    def test_equal_best_round_score_lower_seed_wins_reversed_args(self) -> None:
        winning_seed = break_tie(7, 600, 3, 600)
        self.assertEqual(winning_seed, 3)

    def test_returns_one_of_the_two_input_seeds(self) -> None:
        winning_seed = break_tie(1, 100, 8, 100)
        self.assertIn(winning_seed, (1, 8))


# ---------------------------------------------------------------------------
# LG-02a-2 §1 — stage_progress (STAGE-based Bracket progress)
# ---------------------------------------------------------------------------


class TestStageProgress(SimpleTestCase):
    """Pure-unit: ``stage_progress(nodes) -> (completed_stages, total_stages)``.

    total_stages = max ``bracket_round`` (0 when empty). completed_stages =
    count of Bracket rounds in which every non-bye node has ``winner_id`` set
    (a round of all byes is vacuously complete). LG-02a-2 seam contract §1.
    """

    def _bracket_n4(
        self,
        *,
        r1_winners: tuple[int | None, int | None] = (None, None),
        final_winner: int | None = None,
    ) -> list[dict]:
        """A 4-team bracket (2 round-1 nodes + 1 final) as flat dicts."""
        w0, w1 = r1_winners
        return [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=104,
                seed_a=1,
                seed_b=4,
                winner_id=w0,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=102,
                team_b_id=103,
                seed_a=2,
                seed_b=3,
                winner_id=w1,
                advances_to=(2, 0),
                advances_to_slot="b",
            ),
            _node_dict(
                bracket_round=2,
                position=0,
                team_a_id=101 if w0 else None,
                team_b_id=102 if w1 else None,
                seed_a=1 if w0 else None,
                seed_b=2 if w1 else None,
                winner_id=final_winner,
            ),
        ]

    def test_empty_returns_zero_zero(self) -> None:
        self.assertEqual(stage_progress([]), (0, 0))

    def test_returns_a_two_tuple_of_ints(self) -> None:
        completed, total = stage_progress(self._bracket_n4())
        self.assertIsInstance(completed, int)
        self.assertIsInstance(total, int)

    def test_total_is_max_bracket_round(self) -> None:
        # N=4 ⇒ 2 Bracket rounds (ceil(log2(4)) == 2).
        _completed, total = stage_progress(self._bracket_n4())
        self.assertEqual(total, 2)

    def test_round_1_no_winners_completes_zero_stages(self) -> None:
        completed, total = stage_progress(self._bracket_n4())
        self.assertEqual((completed, total), (0, 2))

    def test_all_round_1_winners_set_completes_stage_1(self) -> None:
        # Both round-1 nodes resolved, final not played ⇒ stage 1 complete.
        completed, total = stage_progress(self._bracket_n4(r1_winners=(101, 102)))
        self.assertEqual((completed, total), (1, 2))

    def test_full_bracket_completes_all_stages(self) -> None:
        completed, total = stage_progress(
            self._bracket_n4(r1_winners=(101, 102), final_winner=101)
        )
        self.assertEqual((completed, total), (2, 2))

    def test_all_bye_round_is_vacuously_complete(self) -> None:
        # A whole round of byes (no non-bye nodes) counts as complete even
        # without an explicit winner on every node.
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                seed_a=1,
                is_bye=True,
                winner_id=101,
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=102,
                seed_a=2,
                is_bye=True,
                winner_id=102,
            ),
            # Round 2 is the real game, unplayed.
            _node_dict(
                bracket_round=2,
                position=0,
                team_a_id=101,
                team_b_id=102,
                seed_a=1,
                seed_b=2,
                winner_id=None,
            ),
        ]
        completed, total = stage_progress(nodes)
        # Round 1 (all byes) complete, round 2 not ⇒ 1 of 2.
        self.assertEqual((completed, total), (1, 2))

    def test_partial_round_is_not_complete(self) -> None:
        # One of two round-1 non-bye nodes resolved ⇒ stage 1 NOT complete.
        completed, _total = stage_progress(self._bracket_n4(r1_winners=(101, None)))
        self.assertEqual(completed, 0)

    def test_bye_mixed_round_ignores_bye_node_winner_requirement(self) -> None:
        # A round with one bye node (winner pre-set) and one real node: the
        # round is complete iff the single NON-bye node has a winner.
        nodes = [
            _node_dict(
                bracket_round=1,
                position=0,
                team_a_id=101,
                seed_a=1,
                is_bye=True,
                winner_id=101,
            ),
            _node_dict(
                bracket_round=1,
                position=1,
                team_a_id=104,
                team_b_id=105,
                seed_a=4,
                seed_b=5,
                winner_id=104,  # the real game is decided
            ),
        ]
        completed, total = stage_progress(nodes)
        self.assertEqual((completed, total), (1, 1))


# ---------------------------------------------------------------------------
# §2 — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.bracket`` in a fresh subprocess must not pull in
    ``django.*`` — the allowlist is ``dataclasses`` + ``typing`` + ``math`` +
    ``collections``. Mirrors the LG-01 ``test_standings.py`` precedent.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.bracket  # noqa: F401

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )

    def test_stage_progress_pulls_in_no_django(self) -> None:
        """LG-02a-2 §1 purity assertion: importing ``matches.bracket`` AND
        calling ``stage_progress`` in a fresh subprocess must not pull in
        ``django.*`` / ``matches.models`` — the new function imports nothing
        new (stdlib only). Mirrors ``test_pure_module_does_not_pull_in_django``.
        """
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(project_root)!r})
            from matches.bracket import stage_progress  # noqa: F401

            # Exercise it so a lazy import inside the function body would fire.
            stage_progress([])
            stage_progress([
                {{"bracket_round": 1, "position": 0, "is_bye": False,
                  "winner_id": 7}},
            ])

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


# ===========================================================================
# LG-02b — Best-of-N series bracket nodes
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-seam-contract.md``.
#
# ``clinch_threshold`` / ``series_winner_slot`` are pure series math; the new
# ``find_next_node`` predicate consumes flat dicts carrying ``wins_a`` /
# ``wins_b`` / ``series_length`` and NO ``match_id``. These imports are done
# LAZILY inside each class so the new functions' absence (pre-Code-landing)
# isolates the failure to these new classes and does NOT break the existing
# (passing) classes above at module-collection time.


def _series_node_dict(
    *,
    bracket_round: int,
    position: int,
    team_a_id: int | None = None,
    team_b_id: int | None = None,
    seed_a: int | None = None,
    seed_b: int | None = None,
    is_bye: bool = False,
    winner_id: int | None = None,
    wins_a: int = 0,
    wins_b: int = 0,
    series_length: int = 3,
    advances_to: tuple[int, int] | None = None,
    advances_to_slot: str | None = None,
) -> dict:
    """A flat node dict in the NEW LG-02b series shape.

    Carries ``wins_a`` / ``wins_b`` / ``series_length`` and DROPS ``match_id``
    (the new ``find_next_node`` predicate keys playability on the series score,
    not on a single attached Match). Mirrors the LG-02a ``_node_dict`` style
    plus the three new keys, minus ``match_id``.
    """
    return {
        "bracket_round": bracket_round,
        "position": position,
        "team_a_id": team_a_id,
        "team_b_id": team_b_id,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "is_bye": is_bye,
        "winner_id": winner_id,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "series_length": series_length,
        "advances_to": advances_to,
        "advances_to_slot": advances_to_slot,
    }


class TestClinchThreshold(SimpleTestCase):
    """``clinch_threshold(series_length) -> int``: wins needed to clinch."""

    def test_best_of_1_needs_1(self) -> None:
        from matches.bracket import clinch_threshold

        self.assertEqual(clinch_threshold(1), 1)

    def test_best_of_3_needs_2(self) -> None:
        from matches.bracket import clinch_threshold

        self.assertEqual(clinch_threshold(3), 2)

    def test_best_of_5_needs_3(self) -> None:
        from matches.bracket import clinch_threshold

        self.assertEqual(clinch_threshold(5), 3)


class TestSeriesWinnerSlot(SimpleTestCase):
    """``series_winner_slot(wins_a, wins_b, series_length) -> 'a'|'b'|None``."""

    def test_below_threshold_returns_none(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertIsNone(series_winner_slot(0, 0, 3))
        self.assertIsNone(series_winner_slot(1, 0, 3))
        self.assertIsNone(series_winner_slot(0, 1, 3))

    def test_a_at_threshold_returns_a(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(1, 0, 1), "a")

    def test_b_at_threshold_returns_b(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(0, 1, 1), "b")

    def test_bo3_boundary_one_one_is_none(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertIsNone(series_winner_slot(1, 1, 3))

    def test_bo3_two_zero_is_a(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(2, 0, 3), "a")

    def test_bo3_zero_two_is_b(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(0, 2, 3), "b")

    def test_bo5_two_two_is_none(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertIsNone(series_winner_slot(2, 2, 5))

    def test_bo5_three_zero_is_a(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(3, 0, 5), "a")

    def test_both_at_threshold_guard_a_checked_first(self) -> None:
        # An impossible-in-practice double-clinch must resolve deterministically:
        # ``a`` is checked first, so (2, 2, 3) returns "a".
        from matches.bracket import series_winner_slot

        self.assertEqual(series_winner_slot(2, 2, 3), "a")

    def test_returns_a_b_or_none_only(self) -> None:
        from matches.bracket import series_winner_slot

        self.assertIn(series_winner_slot(2, 1, 3), ("a", "b", None))
        self.assertIn(series_winner_slot(1, 1, 3), ("a", "b", None))


class TestFindNextNodeSeries(SimpleTestCase):
    """The NEW ``find_next_node`` predicate over series flat dicts.

    A node is playable when both slots are filled, it is not a bye, and
    ``series_winner_slot(wins_a, wins_b, series_length) is None`` (the series is
    not yet clinched). A clinched node is skipped; a half-played Bo3 stays
    playable; a Bo1 with one recorded win is clinched (skipped). Lowest
    (bracket_round, position) returned first. No ``match_id`` key is read.
    """

    def test_returns_playable_unstarted_node(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                series_length=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_clinched_node_not_returned(self) -> None:
        from matches.bracket import find_next_node

        # Bo3 with 2-0 ⇒ clinched ⇒ NOT playable.
        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=2,
                wins_b=0,
                series_length=3,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_half_played_bo3_still_playable(self) -> None:
        from matches.bracket import find_next_node

        # Bo3 with 1-0 ⇒ not clinched ⇒ STILL playable (next game needed).
        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=1,
                wins_b=0,
                series_length=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_bo1_with_one_win_is_clinched_not_playable(self) -> None:
        from matches.bracket import find_next_node

        # Bo1 with 1 recorded win ⇒ clinched ⇒ NOT playable.
        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=1,
                wins_b=0,
                series_length=1,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_skips_bye_nodes(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=1,
                series_length=3,
            ),
            _series_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=4,
                team_b_id=5,
                seed_a=4,
                seed_b=5,
                series_length=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 1))

    def test_skips_nodes_with_an_empty_slot(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _series_node_dict(
                bracket_round=2,
                position=0,
                team_a_id=1,
                team_b_id=None,
                seed_a=1,
                series_length=3,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_returns_lowest_round_position_first(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=5,
                seed_a=2,
                seed_b=3,
                series_length=3,
            ),
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                series_length=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_returns_none_when_all_clinched(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _series_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=4,
                wins_a=2,
                wins_b=1,
                series_length=3,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_empty_list_returns_none(self) -> None:
        from matches.bracket import find_next_node

        self.assertIsNone(find_next_node([]))


# ===========================================================================
# LG-02b-2 — per-Bracket-round Series escalation: series_length_for_round
# ===========================================================================
#
# NEW class appended below (existing classes above are NOT modified).
# Seam contract: ``.claude/worktrees/lg-02b-2-seam-contract.md`` §2a / §6a.
#
# ``series_length_for_round(bracket_round, total_rounds, *, final, semifinal,
# quarterfinal, earlier) -> int`` resolves a node's Series length from its
# depth below the final (``depth = total_rounds - bracket_round``): depth 0 ->
# final, 1 -> semifinal, 2 -> quarterfinal, >= 3 -> earlier. The four slot args
# are KEYWORD-ONLY. Pure integer dispatch; no new import (the frozen allowlist
# is unchanged, so ``TestNoDjangoImportsLeaked`` above stays green). The
# function is imported LAZILY inside each method so its absence (pre-Code-
# landing) isolates the failure to THIS class only.


class TestSeriesLengthForRound(SimpleTestCase):
    """Depth-anchored Series-length resolver (LG-02b-2 §2a)."""

    # Four DISTINCT slot values so a misrouted depth resolves to the wrong int
    # and the test catches it. final=5, semifinal=3, quarterfinal=7, earlier=9
    # (the 7/9 are deliberately NOT in the {1,3,5} ship set — the pure function
    # does NOT validate the slot values, it only dispatches by depth).
    _SLOTS = {"final": 5, "semifinal": 3, "quarterfinal": 7, "earlier": 9}

    # -- depth boundaries (total_rounds picked so each depth is hit) ----------

    def test_depth_0_resolves_to_final(self) -> None:
        from matches.bracket import series_length_for_round

        # depth = total_rounds - bracket_round = 4 - 4 = 0 -> final.
        self.assertEqual(series_length_for_round(4, 4, **self._SLOTS), 5)

    def test_depth_1_resolves_to_semifinal(self) -> None:
        from matches.bracket import series_length_for_round

        # depth = 4 - 3 = 1 -> semifinal.
        self.assertEqual(series_length_for_round(3, 4, **self._SLOTS), 3)

    def test_depth_2_resolves_to_quarterfinal(self) -> None:
        from matches.bracket import series_length_for_round

        # depth = 4 - 2 = 2 -> quarterfinal.
        self.assertEqual(series_length_for_round(2, 4, **self._SLOTS), 7)

    def test_depth_3_resolves_to_earlier(self) -> None:
        from matches.bracket import series_length_for_round

        # depth = 4 - 1 = 3 -> earlier (the depth->=3 fall-through).
        self.assertEqual(series_length_for_round(1, 4, **self._SLOTS), 9)

    def test_depth_4_resolves_to_earlier(self) -> None:
        from matches.bracket import series_length_for_round

        # depth = 5 - 1 = 4 (>= 3) -> earlier (still the else branch).
        self.assertEqual(series_length_for_round(1, 5, **self._SLOTS), 9)

    # -- worked sweep: total_rounds = 2 (N=4) ---------------------------------

    def test_n4_sweep_round2_is_final(self) -> None:
        from matches.bracket import series_length_for_round

        # N=4: 2 Bracket rounds. round 2 = final (depth 0).
        self.assertEqual(series_length_for_round(2, 2, **self._SLOTS), 5)

    def test_n4_sweep_round1_is_semifinal(self) -> None:
        from matches.bracket import series_length_for_round

        # N=4: round 1 = semifinal (depth 1).
        self.assertEqual(series_length_for_round(1, 2, **self._SLOTS), 3)

    # -- worked sweep: total_rounds = 3 (N=8) ---------------------------------

    def test_n8_sweep_round3_is_final(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertEqual(series_length_for_round(3, 3, **self._SLOTS), 5)

    def test_n8_sweep_round2_is_semifinal(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertEqual(series_length_for_round(2, 3, **self._SLOTS), 3)

    def test_n8_sweep_round1_is_quarterfinal(self) -> None:
        from matches.bracket import series_length_for_round

        # N=8: round 1 = quarterfinal (depth 2).
        self.assertEqual(series_length_for_round(1, 3, **self._SLOTS), 7)

    # -- worked sweep: total_rounds = 4 (N=16) --------------------------------

    def test_n16_sweep_round4_is_final(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertEqual(series_length_for_round(4, 4, **self._SLOTS), 5)

    def test_n16_sweep_round3_is_semifinal(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertEqual(series_length_for_round(3, 4, **self._SLOTS), 3)

    def test_n16_sweep_round2_is_quarterfinal(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertEqual(series_length_for_round(2, 4, **self._SLOTS), 7)

    def test_n16_sweep_round1_is_earlier(self) -> None:
        from matches.bracket import series_length_for_round

        # N=16: round 1 = depth 3 -> earlier.
        self.assertEqual(series_length_for_round(1, 4, **self._SLOTS), 9)

    # -- Bo1-everywhere byte-equivalence (locked-decision-5) ------------------

    def test_all_slots_one_resolves_to_one_at_every_depth(self) -> None:
        from matches.bracket import series_length_for_round

        ones = {"final": 1, "semifinal": 1, "quarterfinal": 1, "earlier": 1}
        for total_rounds in (2, 3, 4):
            for bracket_round in range(1, total_rounds + 1):
                self.assertEqual(
                    series_length_for_round(bracket_round, total_rounds, **ones),
                    1,
                    f"round {bracket_round}/{total_rounds} should be Bo1",
                )

    # -- signature is keyword-only past total_rounds --------------------------

    def test_slot_args_are_keyword_only(self) -> None:
        from matches.bracket import series_length_for_round

        # Passing a slot positionally past total_rounds is a TypeError (the
        # signature pins the four slots after ``*``).
        with self.assertRaises(TypeError):
            series_length_for_round(1, 4, 5, 3, 7, 9)  # type: ignore[misc]

    def test_returns_int(self) -> None:
        from matches.bracket import series_length_for_round

        self.assertIsInstance(series_length_for_round(1, 2, **self._SLOTS), int)


# Reference to silence unused-import warnings if BracketNodeSpec is dropped.
_ = BracketNodeSpec


# ===========================================================================
# LG-02c — Double-elimination tournaments (pure-unit)
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified — they
# stay green as single-elim regression guards). Seam contract:
# ``.claude/worktrees/lg-02c-seam-contract.md`` §2 (pure module) / §6a (test
# boundary). Every new pure name is imported LAZILY inside each method so its
# absence (pre-Code-landing) isolates the failure to THESE classes only and
# does NOT break the existing (passing) classes above at collection time.
#
# The DE builder hosts TWO coupled trees in one BracketNodeSpec list, tagged by
# ``bracket_type`` ("winners"/"losers"/"grand_final"). A WB node's LOSER drops
# into an LB slot (``loser_advances_to`` = (bracket_type, round, position) triple
# + ``loser_advances_to_slot``); GF1's loser feeds GF2 (the Bracket reset).
# Tests assert on STRUCTURE (node counts per bracket_type, the cross-bracket
# wiring coords, ``depth`` values, byes / Drop-bye collapse, the
# ``find_next_node`` total order, ``stage_progress`` group counts) — NEVER on
# simulated point totals.


def _de_node_dict(
    *,
    bracket_type: str = "winners",
    bracket_round: int,
    position: int,
    team_a_id: int | None = None,
    team_b_id: int | None = None,
    seed_a: int | None = None,
    seed_b: int | None = None,
    is_bye: bool = False,
    winner_id: int | None = None,
    wins_a: int = 0,
    wins_b: int = 0,
    series_length: int = 1,
    advances_to: tuple[int, int] | None = None,
    advances_to_slot: str | None = None,
    loser_advances_to: tuple[str, int, int] | None = None,
    loser_advances_to_slot: str | None = None,
) -> dict:
    """A flattened node dict in the LG-02c double-elim ``_node_to_dict`` shape.

    Adds the three new keys (``bracket_type``, ``loser_advances_to`` triple,
    ``loser_advances_to_slot``) to the LG-02b series-node shape. The existing
    ``advances_to`` key stays a 2-tuple ``(bracket_round, position)``;
    ``loser_advances_to`` is a 3-tuple ``(bracket_type, round, position)`` (the
    Drop crosses brackets, so the coord carries the destination bracket).
    """
    return {
        "bracket_type": bracket_type,
        "bracket_round": bracket_round,
        "position": position,
        "team_a_id": team_a_id,
        "team_b_id": team_b_id,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "is_bye": is_bye,
        "winner_id": winner_id,
        "wins_a": wins_a,
        "wins_b": wins_b,
        "series_length": series_length,
        "advances_to": advances_to,
        "advances_to_slot": advances_to_slot,
        "loser_advances_to": loser_advances_to,
        "loser_advances_to_slot": loser_advances_to_slot,
    }


def _de_participants(n: int) -> list:
    """``n`` ParticipantSpec, Bracket seed 1..n, team_id = 100 + seed."""
    from matches.bracket import ParticipantSpec

    return [ParticipantSpec(team_id=100 + s, seed=s) for s in range(1, n + 1)]


class TestSeriesLengthForDepth(SimpleTestCase):
    """``series_length_for_depth(depth, *, final, semifinal, quarterfinal,
    earlier)`` — the extracted depth->slot dispatch that
    ``series_length_for_round`` now delegates to (LG-02c §2b)."""

    # Four DISTINCT slot values so a misrouted depth resolves to the wrong int.
    _SLOTS = {"final": 5, "semifinal": 3, "quarterfinal": 7, "earlier": 9}

    def test_depth_0_resolves_to_final(self) -> None:
        from matches.bracket import series_length_for_depth

        self.assertEqual(series_length_for_depth(0, **self._SLOTS), 5)

    def test_depth_1_resolves_to_semifinal(self) -> None:
        from matches.bracket import series_length_for_depth

        self.assertEqual(series_length_for_depth(1, **self._SLOTS), 3)

    def test_depth_2_resolves_to_quarterfinal(self) -> None:
        from matches.bracket import series_length_for_depth

        self.assertEqual(series_length_for_depth(2, **self._SLOTS), 7)

    def test_depth_3_resolves_to_earlier(self) -> None:
        from matches.bracket import series_length_for_depth

        self.assertEqual(series_length_for_depth(3, **self._SLOTS), 9)

    def test_depth_4_resolves_to_earlier(self) -> None:
        from matches.bracket import series_length_for_depth

        # depth >= 3 is the catch-all -> earlier.
        self.assertEqual(series_length_for_depth(4, **self._SLOTS), 9)

    def test_slot_args_are_keyword_only(self) -> None:
        from matches.bracket import series_length_for_depth

        # The four slot args sit after ``*`` — passing them positionally raises.
        with self.assertRaises(TypeError):
            series_length_for_depth(0, 5, 3, 7, 9)  # type: ignore[misc]

    def test_returns_int(self) -> None:
        from matches.bracket import series_length_for_depth

        self.assertIsInstance(series_length_for_depth(0, **self._SLOTS), int)

    def test_series_length_for_round_delegates_with_identical_results(self) -> None:
        # series_length_for_round(bracket_round, total_rounds, ...) ==
        # series_length_for_depth(total_rounds - bracket_round, ...). The
        # delegation must be transparent for representative (round, total) pairs.
        from matches.bracket import series_length_for_depth, series_length_for_round

        for total_rounds in (2, 3, 4):
            for bracket_round in range(1, total_rounds + 1):
                self.assertEqual(
                    series_length_for_round(bracket_round, total_rounds, **self._SLOTS),
                    series_length_for_depth(
                        total_rounds - bracket_round, **self._SLOTS
                    ),
                    f"delegation mismatch at round {bracket_round}/{total_rounds}",
                )


class TestBuildDoubleElimBracket(SimpleTestCase):
    """``build_double_elim_bracket(participants)`` — the two-tree builder.

    N=4 / N=8 (power-of-two, no WB byes) and N=5 / N=6 (with WB byes). Asserts
    node counts per ``bracket_type``, the loser-drop coords, GF1/GF2 wiring, the
    per-spec ``depth`` values, and the ValueError guards. The internal
    (bracket_round, position) numbering scheme is NOT asserted (only the
    cross-bracket wiring coords + depth are pinned per §2c).
    """

    def _build(self, n: int) -> list:
        from matches.bracket import build_double_elim_bracket

        return build_double_elim_bracket(_de_participants(n))

    def _by_type(self, specs: list) -> dict:
        out: dict[str, list] = {"winners": [], "losers": [], "grand_final": []}
        for s in specs:
            out[s.bracket_type].append(s)
        return out

    # -- node counts per bracket_type (power-of-two) --------------------------

    def test_n4_winners_has_three_nodes(self) -> None:
        # WB = the single-elim tree for 4 teams = 3 nodes (2 R1 + 1 WB final).
        parts = self._by_type(self._build(4))
        self.assertEqual(len(parts["winners"]), 3)

    def test_n4_losers_has_two_nodes(self) -> None:
        # LB for 4 teams: LB-R1 (one node consuming the 2 WB-R1 losers) +
        # LB-final (consuming the LB-R1 winner + the WB-final loser) = 2 nodes.
        parts = self._by_type(self._build(4))
        self.assertEqual(len(parts["losers"]), 2)

    def test_n4_grand_final_has_two_nodes(self) -> None:
        # GF1 + GF2 (the Bracket reset) always = 2 nodes.
        parts = self._by_type(self._build(4))
        self.assertEqual(len(parts["grand_final"]), 2)

    def test_n8_winners_has_seven_nodes(self) -> None:
        parts = self._by_type(self._build(8))
        self.assertEqual(len(parts["winners"]), 7)

    def test_n8_grand_final_has_two_nodes(self) -> None:
        parts = self._by_type(self._build(8))
        self.assertEqual(len(parts["grand_final"]), 2)

    def test_n8_losers_node_count(self) -> None:
        # Standard double-elim LB for 8 teams = 6 nodes (2+1 minor/major pairs:
        # LB-R1 2 nodes, LB-R2 2 nodes, LB-R3 1 node, LB-final 1 node).
        parts = self._by_type(self._build(8))
        self.assertEqual(len(parts["losers"]), 6)

    # -- every spec carries a bracket_type from the locked vocabulary ---------

    def test_every_spec_bracket_type_in_vocabulary(self) -> None:
        for spec in self._build(8):
            self.assertIn(spec.bracket_type, ("winners", "losers", "grand_final"))

    # -- WB non-bye node loser-drop coords ------------------------------------

    def test_wb_non_bye_nodes_drop_into_losers(self) -> None:
        parts = self._by_type(self._build(8))
        for wb in parts["winners"]:
            if wb.is_bye:
                continue
            # A WB non-bye node's loser drops into an LB slot.
            self.assertIsNotNone(
                wb.loser_advances_to,
                f"WB node {wb.bracket_round}/{wb.position} has no loser dest",
            )
            dest_bracket = wb.loser_advances_to[0]
            self.assertEqual(dest_bracket, "losers")
            self.assertIn(wb.loser_advances_to_slot, ("a", "b"))

    def test_loser_advances_to_is_a_triple_coord(self) -> None:
        parts = self._by_type(self._build(4))
        wb = next(w for w in parts["winners"] if not w.is_bye)
        # (bracket_type, bracket_round, position) triple.
        self.assertEqual(len(wb.loser_advances_to), 3)

    # -- Grand final wiring ---------------------------------------------------

    def test_gf1_loser_advances_to_gf2(self) -> None:
        # GF1's loser feeds GF2 (so the LB-champ path advances both into GF2).
        parts = self._by_type(self._build(4))
        # GF1 is the grand_final node with a non-None advances_to (it points at
        # GF2); GF2 is the one with advances_to None.
        gfs = parts["grand_final"]
        gf1 = next(g for g in gfs if g.advances_to is not None)
        self.assertIsNotNone(gf1.loser_advances_to)
        self.assertEqual(gf1.loser_advances_to[0], "grand_final")

    def test_gf2_advances_to_and_loser_advances_to_are_none(self) -> None:
        parts = self._by_type(self._build(4))
        gfs = parts["grand_final"]
        gf2 = next(g for g in gfs if g.advances_to is None)
        self.assertIsNone(gf2.advances_to)
        self.assertIsNone(gf2.loser_advances_to)

    # -- depth = distance-to-GF1 ----------------------------------------------

    def test_grand_final_nodes_are_depth_zero(self) -> None:
        parts = self._by_type(self._build(4))
        for gf in parts["grand_final"]:
            self.assertEqual(gf.depth, 0, "GF1/GF2 are depth 0")

    def test_wb_final_is_depth_one(self) -> None:
        # The WB final (the winners node with no winners-bracket advances_to that
        # feeds GF) sits one step below GF1 -> depth 1.
        parts = self._by_type(self._build(4))
        # WB final = the winners node at the highest bracket_round.
        wb_final = max(parts["winners"], key=lambda s: s.bracket_round)
        self.assertEqual(wb_final.depth, 1)

    def test_lb_final_is_depth_one(self) -> None:
        parts = self._by_type(self._build(4))
        lb_final = max(parts["losers"], key=lambda s: s.bracket_round)
        self.assertEqual(lb_final.depth, 1)

    def test_every_spec_carries_an_integer_depth(self) -> None:
        for spec in self._build(8):
            self.assertIsInstance(spec.depth, int, f"{spec} has non-int depth")

    # -- WB byes for N=5 / N=6 ------------------------------------------------

    def test_n5_winners_has_three_byes(self) -> None:
        parts = self._by_type(self._build(5))
        byes = [w for w in parts["winners"] if w.is_bye]
        self.assertEqual(len(byes), 3)

    def test_n6_winners_has_two_byes(self) -> None:
        parts = self._by_type(self._build(6))
        byes = [w for w in parts["winners"] if w.is_bye]
        self.assertEqual(len(byes), 2)

    def test_byes_only_in_winners_bracket(self) -> None:
        parts = self._by_type(self._build(5))
        for s in parts["losers"] + parts["grand_final"]:
            self.assertFalse(s.is_bye, "LB / GF specs must not be byes pre-resolve")

    # -- error guards (mirror build_bracket) ----------------------------------

    def test_n3_raises_value_error(self) -> None:
        from matches.bracket import build_double_elim_bracket

        with self.assertRaises(ValueError):
            build_double_elim_bracket(_de_participants(3))

    def test_empty_raises_value_error(self) -> None:
        from matches.bracket import build_double_elim_bracket

        with self.assertRaises(ValueError):
            build_double_elim_bracket([])

    def test_duplicate_seed_raises_value_error(self) -> None:
        from matches.bracket import ParticipantSpec, build_double_elim_bracket

        dup = [
            ParticipantSpec(team_id=101, seed=1),
            ParticipantSpec(team_id=102, seed=1),
            ParticipantSpec(team_id=103, seed=3),
            ParticipantSpec(team_id=104, seed=4),
        ]
        with self.assertRaises(ValueError):
            build_double_elim_bracket(dup)

    def test_duplicate_team_id_raises_value_error(self) -> None:
        from matches.bracket import ParticipantSpec, build_double_elim_bracket

        dup = [
            ParticipantSpec(team_id=101, seed=1),
            ParticipantSpec(team_id=101, seed=2),
            ParticipantSpec(team_id=103, seed=3),
            ParticipantSpec(team_id=104, seed=4),
        ]
        with self.assertRaises(ValueError):
            build_double_elim_bracket(dup)


class TestAdvanceLoser(SimpleTestCase):
    """``advance_loser(nodes, node_position, loser_id, loser_seed)`` — the Drop
    mutation (parallel to ``advance_winner``). Reads ``loser_advances_to`` /
    ``loser_advances_to_slot`` off the resolved node; the mutation dict carries
    a ``bracket_type`` key (the LB destination's)."""

    def _wb_drop_nodes(self) -> list[dict]:
        # A resolved WB node whose loser drops into an LB node at ("losers",1,0)
        # slot "b", plus that LB destination node.
        return [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=104,
                seed_a=1,
                seed_b=4,
                advances_to=(2, 0),
                advances_to_slot="a",
                loser_advances_to=("losers", 1, 0),
                loser_advances_to_slot="b",
            ),
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
            ),
        ]

    def test_drops_loser_into_lb_slot(self) -> None:
        from matches.bracket import advance_loser

        muts = advance_loser(
            self._wb_drop_nodes(),
            ("winners", 1, 0),
            loser_id=104,
            loser_seed=4,
        )
        self.assertEqual(len(muts), 1)
        m = muts[0]
        self.assertEqual(m["bracket_type"], "losers")
        self.assertEqual(m["bracket_round"], 1)
        self.assertEqual(m["position"], 0)
        self.assertEqual(m["slot"], "b")
        self.assertEqual(m["team_id"], 104)
        self.assertEqual(m["seed"], 4)

    def test_mutation_dict_has_six_keys_including_bracket_type(self) -> None:
        from matches.bracket import advance_loser

        muts = advance_loser(
            self._wb_drop_nodes(), ("winners", 1, 0), loser_id=104, loser_seed=4
        )
        self.assertEqual(
            set(muts[0].keys()),
            {"bracket_type", "bracket_round", "position", "slot", "team_id", "seed"},
        )

    def test_empty_list_when_loser_advances_to_is_none(self) -> None:
        from matches.bracket import advance_loser

        # An LB node (or GF2, or a single-elim WB node) has no loser dest.
        nodes = [
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
                team_a_id=104,
                team_b_id=105,
                seed_a=4,
                seed_b=5,
                loser_advances_to=None,
            ),
        ]
        muts = advance_loser(nodes, ("losers", 1, 0), loser_id=105, loser_seed=5)
        self.assertEqual(muts, [])

    def test_empty_list_when_node_not_found(self) -> None:
        from matches.bracket import advance_loser

        muts = advance_loser(
            self._wb_drop_nodes(),
            ("winners", 9, 9),  # no such node
            loser_id=104,
            loser_seed=4,
        )
        self.assertEqual(muts, [])


class TestResolveByeChainDropBye(SimpleTestCase):
    """A WB bye produces NO loser, so the matching LB slot can never fill ->
    that LB node collapses (Drop bye): the surviving LB opponent auto-advances
    (``is_bye=True``, ``winner_id`` set). The returned collapse mutation carries
    a ``bracket_type`` key.

    Single-elim ``resolve_bye_chain`` behaviour stays byte-identical (covered by
    ``TestResolveByeChain`` above, which stays green)."""

    def _drop_bye_nodes(self) -> list[dict]:
        # WB bye at ("winners",1,0): team 101 auto-advances, NO loser. Its loser
        # would have dropped into LB node ("losers",1,0) slot "a". The other LB
        # feeder is a real WB node's loser landing in slot "b" (team 105). With
        # slot "a" feeder permanently empty (the bye produced no loser), the LB
        # node must collapse so 105 auto-advances.
        return [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=101,
                advances_to=(2, 0),
                advances_to_slot="a",
                loser_advances_to=("losers", 1, 0),
                loser_advances_to_slot="a",
            ),
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=1,
                team_a_id=104,
                team_b_id=105,
                seed_a=4,
                seed_b=5,
                advances_to=(2, 0),
                advances_to_slot="b",
                loser_advances_to=("losers", 1, 0),
                loser_advances_to_slot="b",
            ),
            # LB node fed only by the two WB-R1 losers. Slot "b" got team 105
            # (the real WB game's loser); slot "a"'s feeder was the bye -> empty.
            # It advances into an LB-final at ("losers",2,0) so the collapse
            # promotion emits an observable bracket_type-tagged mutation.
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
                team_a_id=None,
                team_b_id=105,
                seed_a=None,
                seed_b=5,
                advances_to=(2, 0),
                advances_to_slot="a",
            ),
            _de_node_dict(
                bracket_type="losers",
                bracket_round=2,
                position=0,
            ),
        ]

    def test_drop_bye_collapses_lb_node(self) -> None:
        from matches.bracket import resolve_bye_chain

        muts = resolve_bye_chain(self._drop_bye_nodes())
        # The LB node's surviving opponent (105) auto-advances out of the
        # collapsed node. A collapse mutation targets the LB node's parent OR
        # marks the LB node resolved — at minimum a loser-side mutation carrying
        # bracket_type must be emitted for the Drop-bye collapse.
        lb_muts = [m for m in muts if m.get("bracket_type") is not None]
        self.assertTrue(
            lb_muts,
            f"a Drop-bye collapse must emit a bracket_type-tagged mutation; got {muts}",
        )

    def test_no_byes_returns_empty(self) -> None:
        from matches.bracket import resolve_bye_chain

        nodes = [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=101,
                team_b_id=104,
                seed_a=1,
                seed_b=4,
                advances_to=(2, 0),
                advances_to_slot="a",
                loser_advances_to=("losers", 1, 0),
                loser_advances_to_slot="a",
            ),
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=1,
                team_a_id=102,
                team_b_id=103,
                seed_a=2,
                seed_b=3,
                advances_to=(2, 0),
                advances_to_slot="b",
                loser_advances_to=("losers", 1, 0),
                loser_advances_to_slot="b",
            ),
        ]
        self.assertEqual(resolve_bye_chain(nodes), [])


class TestFindNextNodeBracketOrder(SimpleTestCase):
    """``find_next_node`` total order across brackets: the playable predicate is
    byte-identical (both slots filled, not bye, Series not clinched); the ONLY
    change is the tiebreak sort key
    ``(winners<losers<grand_final, bracket_round asc, position asc)``.

    A single-elim-only list (all ``bracket_type="winners"``) collapses to
    ``(bracket_round, position)`` order exactly as today (covered by
    ``TestFindNextNode`` / ``TestFindNextNodeSeries`` above, kept green)."""

    def test_winners_beats_losers_and_grand_final(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _de_node_dict(
                bracket_type="grand_final",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
            ),
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
            ),
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=5,
                team_b_id=6,
                seed_a=5,
                seed_b=6,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual(nxt["bracket_type"], "winners")

    def test_losers_beats_grand_final(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _de_node_dict(
                bracket_type="grand_final",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
            ),
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual(nxt["bracket_type"], "losers")

    def test_within_bracket_lowest_round_position_wins(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
            ),
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_grand_final_returned_when_only_it_is_ready(self) -> None:
        from matches.bracket import find_next_node

        # WB / LB nodes already clinched (Bo1, one win) -> only GF is playable.
        nodes = [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
                wins_a=1,
                winner_id=1,
                series_length=1,
            ),
            _de_node_dict(
                bracket_type="grand_final",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=3,
                seed_a=1,
                seed_b=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual(nxt["bracket_type"], "grand_final")


class TestStageProgressDoubleElim(SimpleTestCase):
    """``stage_progress`` generalized: ``total`` = count of distinct
    ``(bracket_type, bracket_round)`` groups; a group of all byes / all-inert
    counts complete; ``completed`` advances as groups finish. Single-elim
    behaviour is byte-unchanged (every group is ``("winners", r)``), covered by
    ``TestStageProgress`` above (kept green)."""

    def _two_bracket_nodes(
        self,
        *,
        wb_winner: int | None = None,
        lb_winner: int | None = None,
    ) -> list[dict]:
        # One WB group ("winners", 1) and one LB group ("losers", 1), each a
        # single non-bye node.
        return [
            _de_node_dict(
                bracket_type="winners",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
                winner_id=wb_winner,
            ),
            _de_node_dict(
                bracket_type="losers",
                bracket_round=1,
                position=0,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
                winner_id=lb_winner,
            ),
        ]

    def test_empty_returns_zero_zero(self) -> None:
        from matches.bracket import stage_progress

        self.assertEqual(stage_progress([]), (0, 0))

    def test_total_counts_distinct_bracket_type_round_groups(self) -> None:
        from matches.bracket import stage_progress

        _completed, total = stage_progress(self._two_bracket_nodes())
        # One ("winners",1) group + one ("losers",1) group = 2 groups.
        self.assertEqual(total, 2)

    def test_no_winners_completes_zero_groups(self) -> None:
        from matches.bracket import stage_progress

        completed, total = stage_progress(self._two_bracket_nodes())
        self.assertEqual((completed, total), (0, 2))

    def test_one_group_resolved_completes_one(self) -> None:
        from matches.bracket import stage_progress

        completed, total = stage_progress(self._two_bracket_nodes(wb_winner=1))
        self.assertEqual((completed, total), (1, 2))

    def test_both_groups_resolved_completes_all(self) -> None:
        from matches.bracket import stage_progress

        completed, total = stage_progress(
            self._two_bracket_nodes(wb_winner=1, lb_winner=3)
        )
        self.assertEqual((completed, total), (2, 2))

    def test_inert_grand_final_node_counts_complete(self) -> None:
        from matches.bracket import stage_progress

        # An inert auto-resolved GF2 node (winner_id set, never played) makes its
        # ("grand_final", r) group complete via the existing winner_id check.
        nodes = [
            _de_node_dict(
                bracket_type="grand_final",
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
                winner_id=1,
            ),
            _de_node_dict(
                bracket_type="grand_final",
                bracket_round=2,
                position=0,
                team_a_id=1,
                team_b_id=None,
                seed_a=1,
                is_bye=True,
                winner_id=1,
            ),
        ]
        completed, total = stage_progress(nodes)
        # Two grand_final groups (round 1 + round 2), both complete.
        self.assertEqual((completed, total), (2, 2))


# ===========================================================================
# LG-02c — Round robin tournament format (pure-unit)
# ===========================================================================
#
# NEW class appended below (existing classes above are NOT modified — every
# single-elim / double-elim case stays green as a regression guard). Seam
# contract: ``.claude/worktrees/lg-02c-round-robin-seam-contract.md`` §1 /
# §10. The round-robin format adds NO new pure builder and NO new import to
# ``bracket.py`` — the ONLY pure change is the ``_BRACKET_RANK`` dict gaining
# the ``"round_robin": 3`` entry (a deterministic find_next_node tiebreak).
# Every name is imported LAZILY inside each method so its absence (pre-Code-
# landing) isolates the failure to THIS class only and does NOT break the
# existing (passing) classes above at collection time.
#
# RR nodes are FLAT: ``bracket_type="round_robin"``, both slots filled at lock
# time, ``is_bye=False``, ``series_length=1`` (Bo1), ``advances_to`` /
# ``loser_advances_to`` both ``None`` (RR never advances). ``find_next_node`` is
# UNCHANGED — an unplayed RR node (``wins_a=0, wins_b=0``) is playable
# (``series_winner_slot(0, 0, 1) is None``) and a clinched one
# (``series_winner_slot(1, 0, 1) == "a"``) is skipped. The sort key
# ``(_BRACKET_RANK[bracket_type], bracket_round, position)`` orders RR nodes by
# ``(3, matchday, position)`` — deterministic.


def _rr_node_dict(
    *,
    bracket_round: int,
    position: int,
    team_a_id: int,
    team_b_id: int,
    seed_a: int | None = None,
    seed_b: int | None = None,
    winner_id: int | None = None,
    wins_a: int = 0,
    wins_b: int = 0,
) -> dict:
    """A flat round-robin node dict in the post-LG-02c ``_node_to_dict`` shape.

    RR nodes are always ``bracket_type="round_robin"``, both slots filled,
    ``is_bye=False``, ``series_length=1`` (Bo1), and both advance pointers
    ``None``. ``bracket_round`` doubles as the matchday; ``position`` is the
    0-based index within the matchday. Reuses the LG-02c ``_de_node_dict`` key
    set so ``find_next_node`` reads every key it expects.
    """
    return _de_node_dict(
        bracket_type="round_robin",
        bracket_round=bracket_round,
        position=position,
        team_a_id=team_a_id,
        team_b_id=team_b_id,
        seed_a=seed_a,
        seed_b=seed_b,
        is_bye=False,
        winner_id=winner_id,
        wins_a=wins_a,
        wins_b=wins_b,
        series_length=1,
        advances_to=None,
        advances_to_slot=None,
        loser_advances_to=None,
        loser_advances_to_slot=None,
    )


class TestBracketRankRoundRobin(SimpleTestCase):
    """``_BRACKET_RANK["round_robin"] == 3`` + ``find_next_node`` over a flat
    list of RR-only nodes returns the lowest unplayed ``(matchday, position)``
    and skips clinched (Bo1-played) nodes."""

    def test_bracket_rank_has_round_robin_entry_3(self) -> None:
        from matches.bracket import _BRACKET_RANK

        self.assertEqual(_BRACKET_RANK["round_robin"], 3)

    def test_bracket_rank_round_robin_get_does_not_fall_back_to_zero(self) -> None:
        # Defence in depth: the .get(...) lookup find_next_node uses must resolve
        # to 3 for an RR node, never the 0 default.
        from matches.bracket import _BRACKET_RANK

        self.assertEqual(_BRACKET_RANK.get("round_robin", 0), 3)

    def test_returns_lowest_unplayed_matchday_position(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _rr_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=2,
                team_b_id=3,
                seed_a=2,
                seed_b=3,
            ),
            _rr_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=4,
                seed_a=1,
                seed_b=4,
            ),
            _rr_node_dict(
                bracket_round=2,
                position=0,
                team_a_id=1,
                team_b_id=3,
                seed_a=1,
                seed_b=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_skips_clinched_bo1_node(self) -> None:
        from matches.bracket import find_next_node

        # The (1, 0) node already has a Bo1 result (wins_a=1) ⇒ clinched ⇒
        # skipped; the next playable is (1, 1).
        nodes = [
            _rr_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=4,
                seed_a=1,
                seed_b=4,
                wins_a=1,
                winner_id=1,
            ),
            _rr_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=2,
                team_b_id=3,
                seed_a=2,
                seed_b=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 1))

    def test_unplayed_rr_node_is_playable(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _rr_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertIsNotNone(nxt)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (1, 0))

    def test_returns_none_when_every_rr_node_clinched(self) -> None:
        from matches.bracket import find_next_node

        nodes = [
            _rr_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
                wins_a=1,
                winner_id=1,
            ),
            _rr_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
                wins_b=1,
                winner_id=4,
            ),
        ]
        self.assertIsNone(find_next_node(nodes))

    def test_orders_across_matchdays_by_matchday_then_position(self) -> None:
        from matches.bracket import find_next_node

        # Matchday-1 nodes both clinched ⇒ the first playable is the lowest
        # unplayed matchday-2 position.
        nodes = [
            _rr_node_dict(
                bracket_round=2,
                position=1,
                team_a_id=2,
                team_b_id=4,
                seed_a=2,
                seed_b=4,
            ),
            _rr_node_dict(
                bracket_round=2,
                position=0,
                team_a_id=1,
                team_b_id=3,
                seed_a=1,
                seed_b=3,
            ),
            _rr_node_dict(
                bracket_round=1,
                position=0,
                team_a_id=1,
                team_b_id=2,
                seed_a=1,
                seed_b=2,
                wins_a=1,
                winner_id=1,
            ),
            _rr_node_dict(
                bracket_round=1,
                position=1,
                team_a_id=3,
                team_b_id=4,
                seed_a=3,
                seed_b=4,
                wins_a=1,
                winner_id=3,
            ),
        ]
        nxt = find_next_node(nodes)
        self.assertEqual((nxt["bracket_round"], nxt["position"]), (2, 0))
