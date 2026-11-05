"""``round_probe``: how players move and choose goals over seeded rounds (MECH-07 retro)."""

from __future__ import annotations

import random
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase, TestCase

from core.models import ArenaMap
from matches.lfstats_calibration import load_map_fixture
from matches.round_probe import (
    ProbeResult,
    _goal_source,
    _squares_to_target,
    goal_sharing_share,
    run_probe,
)
from matches.sim_helpers.map_loader import load_map_context
from matches.sim_helpers.modes import Mode
from matches.simulation import BatchSimulator, entrypoints
from teams.models import Player, Team

_SYRACUSE = Path(__file__).resolve().parents[3] / "data/maps/syracuse.json"
_ROLES = {"ammo", "commander", "heavy", "medic", "scout"}


class TestGoalSharingShare(SimpleTestCase):
    def test_the_share_of_team_ticks_where_two_teammates_share_a_goal(self) -> None:
        goals = {
            (1, "red"): {"a": (1, 1), "b": (1, 1), "c": (2, 2)},
            (1, "blue"): {"a": (1, 1), "b": (2, 2)},
            (2, "red"): {"a": (3, 3)},
            (2, "blue"): {"a": (4, 4), "b": (4, 4)},
        }
        self.assertEqual(goal_sharing_share(goals), 0.5)

    def test_no_goals_is_zero(self) -> None:
        self.assertEqual(goal_sharing_share({}), 0.0)


def _placed(role: str, team: str, row: int | None, col: int | None) -> SimpleNamespace:
    return SimpleNamespace(role=role, team_color=team, cell_row=row, cell_col=col)


class TestSquaresToTarget(SimpleTestCase):
    def test_an_attacker_measures_to_the_nearest_enemy_medic_or_ammo(self) -> None:
        commander = _placed("commander", "red", 0, 0)
        others = [
            _placed("medic", "red", 0, 1),  # own team: not a target
            _placed("heavy", "blue", 0, 2),  # not a target Role
            _placed("ammo", "blue", 0, 9),
            _placed("medic", "blue", 3, 4),
        ]
        self.assertEqual(_squares_to_target(commander, others), 7)

    def test_a_support_measures_to_its_own_heavy(self) -> None:
        medic = _placed("medic", "red", 0, 0)
        others = [_placed("heavy", "blue", 0, 1), _placed("heavy", "red", 2, 3)]
        self.assertEqual(_squares_to_target(medic, others), 5)

    def test_an_unplaced_target_is_skipped(self) -> None:
        medic = _placed("medic", "red", 0, 0)
        self.assertIsNone(
            _squares_to_target(medic, [_placed("heavy", "red", None, None)])
        )


class TestGoalSource(SimpleTestCase):
    def test_no_commitment_is_an_override(self) -> None:
        player = SimpleNamespace(_committed_goal=None)
        self.assertEqual(_goal_source(player, (1, 1)), "override")

    def test_a_goal_other_than_the_commitment_is_an_override(self) -> None:
        player = SimpleNamespace(_committed_goal=((2, 2), False, 10))
        self.assertEqual(_goal_source(player, (1, 1)), "override")

    def test_a_committed_action_goal(self) -> None:
        player = SimpleNamespace(_committed_goal=((1, 1), True, 10))
        self.assertEqual(_goal_source(player, (1, 1)), "action")

    def test_a_committed_positioning_goal(self) -> None:
        player = SimpleNamespace(_committed_goal=((1, 1), False, 10))
        self.assertEqual(_goal_source(player, (1, 1)), "positioning")


class TestRunProbeTotals(SimpleTestCase):
    """Exact totals over hand-built players, with the round itself replaced."""

    def test_it_totals_each_role_over_its_players(self) -> None:
        def finished(active: int, points: int, attack: int) -> SimpleNamespace:
            return SimpleNamespace(
                role="scout",
                mode_ticks={Mode.ATTACK: attack, Mode.CONTROL: 100 - attack},
                counters=SimpleNamespace(points_scored=points),
                ticks_active=active,
            )

        seeds_played = []

        def fake_round(self, red, blue, movement_ctx=None):
            seeds_played.append(random.random())
            return {}, [finished(450, 100, 80)], [finished(900, 300, 40)]

        with patch.object(BatchSimulator, "ROUND_TICKS", 1800):
            with patch.object(BatchSimulator, "_simulate_round", fake_round):
                result = run_probe(None, [7, 8])

        scout = result.roles["scout"]
        self.assertAlmostEqual(scout.not_active_share, 1 - 2700 / 7200)
        self.assertEqual(scout.mean_points, 200)
        self.assertEqual(scout.mode_share, {"attack": 0.6, "control": 0.4})
        self.assertEqual(set(result.sharing_by_seed), {7, 8})
        # Each round starts from its own seed.
        self.assertEqual(
            seeds_played, [random.Random(7).random(), random.Random(8).random()]
        )

    def test_each_seed_measures_goal_sharing_on_its_own_round(self) -> None:
        def teammate(tag_id: str) -> SimpleNamespace:
            return SimpleNamespace(
                tag_id=tag_id,
                role="scout",
                team_color="red",
                cell_row=None,
                _committed_goal=None,
            )

        # Different ticks, so a leftover goal from round 1 would still count in round 2.
        rounds = iter(
            [(1.0, {"a": (1, 1), "b": (1, 1)}), (2.0, {"a": (2, 2), "b": (3, 3)})]
        )
        goals: dict = {}

        def fake_round(self, red, blue, movement_ctx=None):
            tick, round_goals = next(rounds)
            goals.update(round_goals)
            for tag_id in round_goals:
                entrypoints.choose_goal_cell(teammate(tag_id), [], None, None, "", tick)
            return {}, [], []

        def fixed_goal(player, *args, **kwargs):
            return goals[player.tag_id]

        with patch.object(entrypoints, "choose_goal_cell", fixed_goal):
            with patch.object(BatchSimulator, "_simulate_round", fake_round):
                result = run_probe(None, [1, 2])

        self.assertEqual(result.sharing_by_seed, {1: 1.0, 2: 0.0})


class TestRunProbe(TestCase):
    result: ProbeResult

    @classmethod
    def setUpTestData(cls) -> None:
        cls.movement_ctx, _ = load_map_context(load_map_fixture(_SYRACUSE))
        cls.result = run_probe(cls.movement_ctx, [1, 2])

    def test_every_role_is_measured(self) -> None:
        self.assertEqual(set(self.result.roles), _ROLES)

    def test_the_shares_of_each_role_add_up(self) -> None:
        for role, measures in self.result.roles.items():
            with self.subTest(role=role):
                self.assertAlmostEqual(sum(measures.mode_share.values()), 1.0)
                self.assertAlmostEqual(sum(measures.goal_share.values()), 1.0)
                self.assertEqual(
                    set(measures.goal_share), {"action", "positioning", "override"}
                )
                self.assertTrue(0.0 <= measures.not_active_share <= 1.0)

    def test_each_role_measures_distance_to_its_own_target(self) -> None:
        for role, target in (
            ("commander", "enemy Medic/Ammo"),
            ("heavy", "enemy Medic/Ammo"),
            ("scout", "enemy Medic/Ammo"),
            ("medic", "own Heavy"),
            ("ammo", "own Heavy"),
        ):
            with self.subTest(role=role):
                self.assertEqual(self.result.roles[role].target, target)
                self.assertGreater(self.result.roles[role].squares_to_target, 0.0)

    def test_goal_sharing_is_reported_per_seed(self) -> None:
        self.assertEqual(set(self.result.sharing_by_seed), {1, 2})

    def test_the_same_seeds_give_the_same_result(self) -> None:
        self.assertEqual(run_probe(self.movement_ctx, [1, 2]), self.result)

    def test_it_puts_the_goal_spy_back(self) -> None:
        self.assertEqual(entrypoints.choose_goal_cell.__name__, "choose_goal_cell")


class TestRoundProbeCommand(TestCase):
    def test_it_prints_the_measures_and_writes_nothing(self) -> None:
        maps, teams, players = (
            ArenaMap.objects.count(),
            Team.objects.count(),
            Player.objects.count(),
        )
        out = StringIO()

        call_command("round_probe", "--seeds", "1", stdout=out)

        output = out.getvalue()
        for text in ("commander", "enemy Medic/Ammo", "own Heavy", "Goal sharing"):
            self.assertIn(text, output)
        self.assertTrue(output.isascii())
        self.assertEqual(ArenaMap.objects.count(), maps)
        self.assertEqual(Team.objects.count(), teams)
        self.assertEqual(Player.objects.count(), players)

    def test_a_seed_count_below_one_is_refused(self) -> None:
        with self.assertRaisesMessage(CommandError, "--seeds"):
            call_command("round_probe", "--seeds", "0", stdout=StringIO())

    def test_a_missing_fixture_is_named(self) -> None:
        with self.assertRaisesMessage(CommandError, "no_such_map.json"):
            call_command(
                "round_probe",
                "--seeds",
                "1",
                "--map-fixture",
                "no_such_map.json",
                stdout=StringIO(),
            )
