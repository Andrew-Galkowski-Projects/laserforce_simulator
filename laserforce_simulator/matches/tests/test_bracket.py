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
    match_id: int | None = None,
    winner_id: int | None = None,
    advances_to: tuple[int, int] | None = None,
    advances_to_slot: str | None = None,
) -> dict:
    """Build one flattened node dict matching the ``_node_to_dict`` seam keys."""
    return {
        "bracket_round": bracket_round,
        "position": position,
        "team_a_id": team_a_id,
        "team_b_id": team_b_id,
        "seed_a": seed_a,
        "seed_b": seed_b,
        "is_bye": is_bye,
        "match_id": match_id,
        "winner_id": winner_id,
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
                match_id=99,
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
                match_id=5,
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


# Reference to silence unused-import warnings if BracketNodeSpec is dropped.
_ = BracketNodeSpec
