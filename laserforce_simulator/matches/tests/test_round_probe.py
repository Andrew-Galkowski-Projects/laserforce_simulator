"""``round_probe``: how players move, choose goals and use Actions over seeded rounds."""

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
from matches.management.commands.round_probe import calibration_lineups, format_result
from matches.round_probe import (
    ProbeResult,
    _default_roster,
    _goal_source,
    _squares_to_target,
    goal_sharing_share,
    probe_seeds,
    run_probe,
)
from matches.sim_helpers import shot
from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.map_loader import load_map_context
from matches.sim_helpers.modes import Mode
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.shot import ShotOutcome
from matches.sim_helpers.time_constants import SURVIVED_SENTINEL
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
                counters=SimpleNamespace(
                    points_scored=points,
                    tags_made=0,
                    times_tagged=0,
                    shots_missed=0,
                    reaction_shots=0,
                    follow_up_shots=0,
                    lives_lost_to_nukes=0,
                ),
                ticks_active=active,
                was_eliminated_at=SURVIVED_SENTINEL,
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


def _tick(
    action: str = "tag_player",
    *,
    plans: tuple[str, ...] = (),
    active: bool = True,
    hiding: bool = False,
    holding: bool = False,
) -> SimpleNamespace:
    """One scripted ``plan_action`` result: what the player rolled and now shows."""
    return SimpleNamespace(
        action=action, plans=plans, active=active, hiding=hiding, holding=holding
    )


def _scripted(
    *ticks: SimpleNamespace,
    role: str = "scout",
    team: str = "red",
    points: int = 0,
    tags_made: int = 0,
    eliminated_at: int = SURVIVED_SENTINEL,
    times_tagged: int = 0,
    shots_missed: int = 0,
    reaction_shots: int = 0,
    follow_up_shots: int = 0,
) -> SimpleNamespace:
    """A finished player whose ``plan_action`` calls follow ``ticks``."""
    return SimpleNamespace(
        role=role,
        team_color=team,
        script=ticks,
        is_active_at=lambda tick: ticks[tick].active,
        is_hiding=False,
        is_holding=False,
        last_chosen_action="",
        mode_ticks={},
        counters=SimpleNamespace(
            points_scored=points,
            tags_made=tags_made,
            times_tagged=times_tagged,
            shots_missed=shots_missed,
            reaction_shots=reaction_shots,
            follow_up_shots=follow_up_shots,
            lives_lost_to_nukes=0,
        ),
        ticks_active=sum(1 for step in ticks if step.active),
        was_eliminated_at=eliminated_at,
    )


def _probe_scripted(*rounds: list[SimpleNamespace]) -> ProbeResult:
    """Run the probe over scripted rounds, seeds 1 up, one list of players each."""
    remaining = iter(rounds)

    def scripted_plan(player, all_alive, tick, movement_ctx=None, **kwargs):
        step = player.script[tick]
        player.last_chosen_action = step.action
        player.is_hiding = step.hiding
        player.is_holding = step.holding
        return [{"type": plan, "actor": player} for plan in step.plans]

    def fake_round(self, red, blue, movement_ctx=None):
        players = next(remaining)
        for tick in range(max((len(p.script) for p in players), default=0)):
            for player in players:
                if tick < len(player.script):
                    entrypoints.plan_action(player, [], tick, None, time_domain="ticks")
        return {}, players, []

    with patch.object(entrypoints, "plan_action", scripted_plan):
        with patch.object(BatchSimulator, "_simulate_round", fake_round):
            return run_probe(None, range(1, len(rounds) + 1))


class TestActionShares(SimpleTestCase):
    def test_action_share_counts_active_rolls_only(self) -> None:
        player = _scripted(
            _tick("tag_player"),
            _tick("tag_player"),
            _tick("tag_player"),
            _tick("hide"),
            _tick("hide", active=False),
            _tick("only_move", active=False),
        )
        share = _probe_scripted([player]).roles["scout"].action_share
        self.assertEqual(share["tag_player"], 0.75)
        self.assertEqual(share["hide"], 0.25)
        self.assertEqual(share["only_move"], 0.0)

    def test_every_action_has_a_share_so_the_shape_is_fixed(self) -> None:
        share = _probe_scripted([_scripted(_tick("hold"))]).roles["scout"].action_share
        self.assertEqual(
            set(share),
            {
                "tag_player",
                "only_move",
                "hide",
                "capture_base",
                "use_special",
                "resupply_ally",
                "missile_player",
                "request_resupply",
                "hold",
            },
        )


class TestEmptyRollShare(SimpleTestCase):
    def test_it_is_a_share_of_that_actions_active_rolls(self) -> None:
        player = _scripted(
            _tick("tag_player", plans=("tag",)),
            _tick("tag_player"),
            _tick("tag_player"),
            _tick("tag_player"),
            _tick("hide", plans=("hide",)),
            _tick("capture_base"),
            # A Downed pack plans nothing; that is no empty Active roll.
            _tick("hide", active=False),
        )
        share = _probe_scripted([player]).roles["scout"].empty_roll_share
        self.assertEqual(share["tag_player"], 0.75)
        self.assertEqual(share["hide"], 0.0)
        self.assertEqual(share["capture_base"], 1.0)

    def test_an_action_never_rolled_is_zero(self) -> None:
        share = _probe_scripted([_scripted(_tick())]).roles["scout"].empty_roll_share
        self.assertEqual(share["hold"], 0.0)


class TestStillShare(SimpleTestCase):
    def test_hiding_holding_and_a_capture_roll_count_as_still(self) -> None:
        player = _scripted(
            _tick("tag_player", hiding=True),
            _tick("hold", holding=True),
            _tick("capture_base"),
            _tick("tag_player"),
            _tick("only_move"),
            _tick("only_move"),
            _tick("only_move"),
            _tick("only_move"),
        )
        self.assertEqual(_probe_scripted([player]).roles["scout"].still_share, 3 / 8)

    def test_the_tick_of_a_hide_roll_is_still(self) -> None:
        # The dispatch sets is_hiding after plan_action and before the Advance, so
        # the player does not move on this tick though the flag still reads False.
        player = _scripted(_tick("hide"), _tick("tag_player"))
        self.assertEqual(_probe_scripted([player]).roles["scout"].still_share, 0.5)

    def test_only_active_ticks_count_on_either_side_of_the_share(self) -> None:
        player = _scripted(
            _tick("hide", hiding=True),
            _tick("tag_player"),
            _tick("hide", active=False, hiding=True),
            _tick("hold", active=False, holding=True),
            _tick("capture_base", active=False),
        )
        self.assertEqual(_probe_scripted([player]).roles["scout"].still_share, 0.5)


class TestHoldMeasures(SimpleTestCase):
    def test_hold_share_is_the_active_ticks_holding(self) -> None:
        player = _scripted(
            _tick("hold", holding=True),
            _tick("tag_player"),
            _tick("tag_player"),
            _tick("tag_player"),
            # Time not Active is on neither side of the share.
            _tick("hold", active=False, holding=True),
        )
        self.assertEqual(_probe_scripted([player]).roles["scout"].hold_share, 0.25)

    def test_a_hold_run_ends_when_the_player_stops_holding(self) -> None:
        player = _scripted(
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("tag_player"),
            _tick("hold", holding=True),
        )
        # Runs of 3 and 1; the last is still open when the round ends.
        self.assertEqual(_probe_scripted([player]).roles["scout"].hold_run, 2.0)

    def test_a_tick_not_active_neither_extends_nor_ends_a_hold_run(self) -> None:
        player = _scripted(
            _tick("hold", holding=True),
            _tick("hold", active=False, holding=True),
            _tick("hold", active=False, holding=True),
            _tick("hold", holding=True),
        )
        self.assertEqual(_probe_scripted([player]).roles["scout"].hold_run, 2.0)

    def test_holding_only_while_not_active_is_no_run(self) -> None:
        player = _scripted(_tick("hold", active=False, holding=True), _tick())
        self.assertEqual(_probe_scripted([player]).roles["scout"].hold_run, 0.0)

    def test_each_players_run_is_their_own(self) -> None:
        holder = _scripted(_tick("hold", holding=True), _tick("hold", holding=True))
        other = _scripted(_tick(), _tick("hold", holding=True))
        # A shared run would read 3, or split into pieces of 1.
        self.assertEqual(_probe_scripted([holder, other]).roles["scout"].hold_run, 1.5)

    def test_a_run_does_not_carry_into_the_next_round(self) -> None:
        first = _scripted(_tick("hold", holding=True))
        second = _scripted(_tick("hold", holding=True))
        self.assertEqual(
            _probe_scripted([first], [second]).roles["scout"].hold_run, 1.0
        )


class TestBySeed(SimpleTestCase):
    def test_each_seed_measures_its_own_round_and_roles_pool_them(self) -> None:
        low = _scripted(_tick("hold", holding=True), _tick(), points=100)
        high = _scripted(
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("hide"),
            points=300,
        )
        result = _probe_scripted([low], [high])

        first, second = result.by_seed[1]["scout"], result.by_seed[2]["scout"]
        self.assertEqual((first.mean_points, second.mean_points), (100, 300))
        self.assertEqual((first.hold_run, second.hold_run), (1.0, 3.0))
        self.assertEqual((first.hold_share, second.hold_share), (0.5, 0.75))
        self.assertEqual(first.action_share["hide"], 0.0)
        self.assertEqual(second.action_share["hide"], 0.25)
        pooled = result.roles["scout"]
        self.assertEqual(pooled.mean_points, 200)
        self.assertEqual(pooled.hold_run, 2.0)
        self.assertEqual(pooled.hold_share, 4 / 6)

    def test_a_run_open_at_the_end_of_a_round_stays_in_that_round(self) -> None:
        result = _probe_scripted(
            [_scripted(_tick("hold", holding=True))],
            [_scripted(_tick())],
        )
        self.assertEqual(result.by_seed[1]["scout"].hold_run, 1.0)
        self.assertEqual(result.by_seed[2]["scout"].hold_run, 0.0)


class TestPerRoundCounts(SimpleTestCase):
    def test_tags_per_round_counts_planned_tag_shots_per_player_round(self) -> None:
        one = _scripted(
            _tick("tag_player", plans=("tag",)),
            # A Scout's special plans two Shots from one roll.
            _tick("tag_player", plans=("tag", "tag")),
            # An empty roll is no Shot.
            _tick("tag_player"),
        )
        other = _scripted(_tick("tag_player", plans=("tag",)))
        again = _scripted(_tick("hide", plans=("hide",)))
        measures = _probe_scripted([one, other], [again]).roles["scout"]
        self.assertEqual(measures.tags_per_round, 4 / 3)

    def test_resupplies_per_round_counts_both_resupply_plans(self) -> None:
        medic = _scripted(
            _tick("resupply_ally", plans=("resupply_lives",)),
            _tick("resupply_ally"),
            role="medic",
        )
        ammo = _scripted(
            _tick("resupply_ally", plans=("resupply_ammo",)),
            _tick("resupply_ally", plans=("resupply_ammo",)),
            role="ammo",
        )
        roles = _probe_scripted([medic, ammo]).roles
        self.assertEqual(roles["medic"].resupplies_per_round, 1.0)
        self.assertEqual(roles["ammo"].resupplies_per_round, 2.0)


def _capturer(role: str, team: str, shots: int = 30) -> PlayerState:
    return PlayerState(
        tag_id=f"{team}-{role}",
        name=f"{team} {role}",
        team_color=team,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=shots,
        final_lives=10,
        final_shots=shots,
    )


def _probe_captures(*rounds: list[tuple[PlayerState, int]]) -> ProbeResult:
    """Each round is (player, base_id) capture attempts, then the round-end award."""
    remaining = iter(rounds)

    def fake_round(self, red, blue, movement_ctx=None):
        attempts = next(remaining)
        for player, base_id in attempts:
            self._capture_base(player, base_id)
        players = list({id(player): player for player, _ in attempts}.values())
        for player in players:
            self._award_bases(player)
        return {}, players, []

    with patch.object(BatchSimulator, "_simulate_round", fake_round):
        return run_probe(None, range(1, len(rounds) + 1))


class TestLiveCaptures(SimpleTestCase):
    def test_captures_per_round_counts_each_live_capture_per_player_round(self) -> None:
        commander = _capturer("commander", "red")
        heavy = _capturer("heavy", "red")
        roles = _probe_captures(
            [(commander, 15), (commander, 14), (heavy, 15)],
        ).roles
        self.assertEqual(roles["commander"].captures_per_round, 2.0)
        self.assertEqual(roles["heavy"].captures_per_round, 1.0)

    def test_the_round_end_award_is_not_a_live_capture(self) -> None:
        survivor = _capturer("commander", "red")
        result = _probe_captures([(survivor, 15)])
        # The award scored the opposing Base, which the player never captured.
        self.assertTrue(survivor.opposing_base_destroyed)
        self.assertEqual(result.roles["commander"].captures_per_round, 1.0)

    def test_an_attempt_short_of_shots_is_no_capture(self) -> None:
        short = _capturer("commander", "red", shots=2)
        result = _probe_captures([(short, 15)])
        self.assertEqual(result.roles["commander"].captures_per_round, 0.0)

    def test_captures_are_kept_per_team_per_seed(self) -> None:
        result = _probe_captures(
            [
                (_capturer("commander", "red"), 15),
                (_capturer("heavy", "red"), 15),
                (_capturer("commander", "blue"), 13),
            ],
            [(_capturer("commander", "red"), 15)],
        )
        self.assertEqual(
            result.team_captures_by_seed,
            {1: {"red": 2, "blue": 1}, 2: {"red": 1, "blue": 0}},
        )

    def test_it_puts_the_capture_spy_back(self) -> None:
        before = entrypoints._capture_base_shared
        _probe_captures([(_capturer("commander", "red"), 15)])
        self.assertIs(entrypoints._capture_base_shared, before)


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

    def test_it_puts_the_spies_back(self) -> None:
        self.assertEqual(entrypoints.choose_goal_cell.__name__, "choose_goal_cell")
        self.assertEqual(entrypoints.plan_action.__name__, "plan_action")
        self.assertEqual(entrypoints._capture_base_shared.__name__, "capture_base")

    def test_observing_draws_no_rng(self) -> None:
        # The round played by hand, unobserved, leaves the RNG where the probe does
        # and scores the same points, so the probe saw the round the seed plays.
        random.seed(1)
        _, reds, blues = BatchSimulator()._simulate_round(
            _default_roster("red"),
            _default_roster("blue"),
            movement_ctx=self.movement_ctx,
        )
        unobserved = random.getstate()
        points = [p.counters.points_scored for p in reds + blues]

        observed = run_probe(self.movement_ctx, [1])

        self.assertEqual(random.getstate(), unobserved)
        for role, measures in observed.roles.items():
            with self.subTest(role=role):
                scores = [
                    score
                    for player, score in zip(reds + blues, points)
                    if player.role == role
                ]
                self.assertEqual(measures.mean_points, sum(scores) / len(scores))
        # CAL-03a: the missile and Resupply observers ran in that same round, and
        # the counts they read match the players' own.
        life_resupplies = sum(
            m.life_resupplies.mean * self._count(reds + blues, role)
            for role, m in observed.roles.items()
        )
        self.assertGreater(life_resupplies, 0)
        missiled = sum(p.counters.times_missiled for p in reds + blues)
        missile_lives = sum(
            m.lives_lost["missile"].mean * self._count(reds + blues, role)
            for role, m in observed.roles.items()
        )
        self.assertEqual(missile_lives > 0, missiled > 0)
        self.assertEqual(EventLog.resupply_lives.__name__, "resupply_lives")
        self.assertEqual(BatchSimulator._complete_missile.__name__, "_complete_missile")

    @staticmethod
    def _count(players: list, role: str) -> int:
        return sum(1 for p in players if p.role == role)

    def test_every_role_measures_its_actions(self) -> None:
        # The real round feeds the Action measures, not just the scripted ones.
        for role, measures in self.result.roles.items():
            with self.subTest(role=role):
                self.assertAlmostEqual(sum(measures.action_share.values()), 1.0)
                self.assertGreater(measures.still_share, 0.0)
                self.assertTrue(0.0 <= measures.hold_share <= 1.0)
        self.assertGreater(self.result.roles["commander"].tags_per_round, 0.0)
        self.assertGreater(self.result.roles["medic"].resupplies_per_round, 0.0)
        self.assertGreater(self.result.roles["commander"].hold_run, 0.0)

    def test_by_seed_holds_every_role_for_every_seed(self) -> None:
        self.assertEqual(set(self.result.by_seed), {1, 2})
        for seed, roles in self.result.by_seed.items():
            with self.subTest(seed=seed):
                self.assertEqual(set(roles), _ROLES)

    def test_a_non_scout_role_reacts_on_seed_1(self) -> None:
        # CAL-02a bug A: the round the probe plays on seed 1, taken from its
        # players' counters. Before the fix only a rapid-fire Scout, whose
        # cooldown rounds to 0 ticks, ever made a Reaction shot.
        random.seed(1)
        _, reds, blues = BatchSimulator()._simulate_round(
            _default_roster("red"),
            _default_roster("blue"),
            movement_ctx=self.movement_ctx,
        )
        reactions = sum(
            p.counters.reaction_shots for p in reds + blues if p.role != "scout"
        )
        self.assertGreater(reactions, 0)


class TestOutcomes(SimpleTestCase):
    """What landed, beside what was planned: the numbers real play is compared with."""

    def test_tags_made_is_per_player_round(self) -> None:
        result = _probe_scripted(
            [_scripted(_tick(), tags_made=30), _scripted(_tick(), tags_made=10)]
        )
        self.assertEqual(result.roles["scout"].tags_made_per_round, 20)

    def test_eliminated_share_counts_the_player_rounds_that_ended_out(self) -> None:
        result = _probe_scripted(
            [
                _scripted(_tick(), eliminated_at=600),
                _scripted(_tick()),
                _scripted(_tick(), eliminated_at=1000),
                _scripted(_tick()),
            ]
        )
        scout = result.roles["scout"]
        self.assertEqual(scout.eliminated_share, 0.5)
        self.assertEqual(scout.elimination_tick, 800)

    def test_with_no_elimination_there_is_no_tick(self) -> None:
        result = _probe_scripted([_scripted(_tick())])
        self.assertEqual(result.roles["scout"].eliminated_share, 0.0)
        self.assertIsNone(result.roles["scout"].elimination_tick)

    def test_they_print_per_role(self) -> None:
        lines = format_result(
            _probe_scripted(
                [_scripted(_tick(), tags_made=30, eliminated_at=600)],
                [_scripted(_tick(), tags_made=10, eliminated_at=1000)],
            )
        )
        self.assertIn("  Tags made per round: 20.0 +/- 10.0 (10.0-30.0)", lines)
        self.assertIn("  Eliminated %: 100.0 +/- 0.0 (100.0-100.0)", lines)
        self.assertIn("  Elimination tick: 800 +/- 200 (600-1000)", lines)

    def test_a_seed_with_no_elimination_is_left_out_of_the_tick(self) -> None:
        lines = format_result(
            _probe_scripted(
                [_scripted(_tick(), eliminated_at=600)], [_scripted(_tick())]
            )
        )
        self.assertIn("  Elimination tick: 600 +/- 0 (600-600)", lines)

    def test_a_role_nobody_eliminated_prints_none(self) -> None:
        lines = format_result(_probe_scripted([_scripted(_tick())]))
        self.assertIn("  Elimination tick: none", lines)


class TestTagMeasures(SimpleTestCase):
    """CAL-02 (#567): the tag measures, each a mean over player-rounds with its
    standard error over those player-rounds."""

    def test_times_tagged_is_the_mean_over_player_rounds(self) -> None:
        result = _probe_scripted(
            [_scripted(_tick(), times_tagged=1), _scripted(_tick(), times_tagged=5)]
        )
        measure = result.roles["scout"].times_tagged
        self.assertEqual(measure.mean, 3.0)
        # The sample deviation of 1 and 5 is sqrt(8), over the root of two players.
        self.assertEqual(measure.se, 2.0)

    def test_shots_fired_are_the_tags_made_plus_the_misses(self) -> None:
        result = _probe_scripted(
            [
                _scripted(_tick(), tags_made=30, shots_missed=10),
                _scripted(_tick(), tags_made=10, shots_missed=10),
            ]
        )
        self.assertEqual(result.roles["scout"].shots_fired.mean, 30.0)

    def test_reaction_and_follow_up_shots_made_are_per_player_round(self) -> None:
        result = _probe_scripted(
            [
                _scripted(_tick(), reaction_shots=2, follow_up_shots=1),
                _scripted(_tick(), reaction_shots=0, follow_up_shots=0),
            ]
        )
        self.assertEqual(result.roles["scout"].reaction_shots.mean, 1.0)
        self.assertEqual(result.roles["scout"].follow_up_shots.mean, 0.5)

    def test_active_share_and_tags_per_active_minute(self) -> None:
        # 900 of 1800 ticks Active is 7.5 minutes on the floor for 30 Tags.
        player = _scripted(_tick(), tags_made=30)
        player.ticks_active = 900
        result = _probe_scripted([player])
        self.assertEqual(result.roles["scout"].active_share.mean, 0.5)
        self.assertEqual(result.roles["scout"].tags_per_active_minute.mean, 4.0)

    def test_a_player_never_active_has_no_tags_per_minute(self) -> None:
        player = _scripted(_tick(active=False), tags_made=3)
        self.assertEqual(
            _probe_scripted([player]).roles["scout"].tags_per_active_minute.mean, 0.0
        )

    def test_mode_shares_are_over_the_round_ticks(self) -> None:
        # Eliminated at mid-round: the Mode ticks stop, the round does not.
        player = _scripted(_tick(), eliminated_at=1350)
        player.mode_ticks = {Mode.ATTACK: 900, Mode.CONTROL: 450}
        result = _probe_scripted([player])
        self.assertEqual(
            result.roles["scout"].mode_round_share, {"attack": 0.5, "control": 0.25}
        )
        # The existing share over the Mode ticks is unchanged.
        self.assertEqual(
            result.roles["scout"].mode_share, {"attack": 2 / 3, "control": 1 / 3}
        )


def _enemy(team: str, lives: int) -> SimpleNamespace:
    return SimpleNamespace(role="scout", team_color=team, final_lives=lives)


class TestEnemiesOnFloorPerActiveRoll(SimpleTestCase):
    def test_it_counts_the_enemies_with_lives_at_each_active_roll(self) -> None:
        player = _scripted(_tick(), _tick(), _tick(active=False))
        floors = iter(
            [
                [
                    _enemy("blue", 3),
                    _enemy("blue", 1),
                    _enemy("blue", 0),
                    _enemy("red", 5),
                ],
                [
                    _enemy("blue", 3),
                    _enemy("blue", 1),
                    _enemy("blue", 1),
                    _enemy("blue", 2),
                ],
                # Not Active: not a roll, whatever the floor holds.
                [_enemy("blue", 1)],
            ]
        )

        def scripted_plan(player, all_alive, tick, movement_ctx=None, **kwargs):
            player.last_chosen_action = "tag_player"
            return []

        def fake_round(self, red, blue, movement_ctx=None):
            for tick in range(3):
                entrypoints.plan_action(player, next(floors), tick, None)
            return {}, [player], []

        with patch.object(entrypoints, "plan_action", scripted_plan):
            with patch.object(BatchSimulator, "_simulate_round", fake_round):
                result = run_probe(None, [1])

        self.assertEqual(
            result.roles["scout"].enemies_on_floor_per_active_roll.mean, 3.0
        )


def _probe_shots(*rounds: list[tuple]) -> ProbeResult:
    """Run the probe over rounds of scripted Shots, seeds 1 up.

    Each Shot is ``(attacker, defender, tick, landed)`` with ``landed`` one of
    ``"hit"``, ``"down"`` (a Downing Hit) or ``"miss"``; the scripted resolve moves
    the counters the way the engine does and returns the ``ShotOutcome``.
    """
    remaining = iter(rounds)
    landing: list[str] = []

    def scripted_resolve(attacker, defender, tick, *, kind, ctx, chain_depth=0):
        landed = landing.pop()
        if landed == "miss":
            attacker.counters.shots_missed += 1
            return ShotOutcome(False, False, False)
        attacker.counters.tags_made += 1
        defender.counters.times_tagged += 1
        return ShotOutcome(True, landed == "down", False)

    def fake_round(self, red, blue, movement_ctx=None):
        shots = next(remaining)
        players: dict[int, SimpleNamespace] = {}
        for attacker, defender, tick, landed in shots:
            players.setdefault(id(attacker), attacker)
            players.setdefault(id(defender), defender)
            landing.append(landed)
            entrypoints.resolve_shot(attacker, defender, tick, kind="initial", ctx=None)
        return {}, list(players.values()), []

    with patch.object(entrypoints, "resolve_shot", scripted_resolve):
        with patch.object(BatchSimulator, "_simulate_round", fake_round):
            return run_probe(None, range(1, len(rounds) + 1))


class TestWhoTagsWhom(SimpleTestCase):
    def test_three_hits_fill_the_attacker_by_defender_matrix_per_round(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        medic = _scripted(_tick(), role="medic", team="red")
        result = _probe_shots(
            [
                (commander, scout, 10, "hit"),
                (commander, scout, 12, "hit"),
                (scout, medic, 20, "down"),
                (commander, medic, 30, "miss"),
            ],
            [
                (
                    _scripted(_tick(), role="heavy"),
                    _scripted(_tick(), team="blue"),
                    5,
                    "miss",
                )
            ],
        )
        # Two rounds, so the three Hits read per round.
        self.assertEqual(result.who_tags_whom["commander"]["scout"], 1.0)
        self.assertEqual(result.who_tags_whom["scout"]["medic"], 0.5)
        self.assertEqual(result.who_tags_whom["commander"]["medic"], 0.0)
        self.assertEqual(result.who_tags_whom["heavy"]["scout"], 0.0)

    def test_a_downing_hit_is_a_down_given_a_down_taken_and_a_matrix_cell(self) -> None:
        heavy = _scripted(_tick(), role="heavy", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        ammo = _scripted(_tick(), role="ammo", team="blue")
        result = _probe_shots(
            [
                (heavy, scout, 10, "down"),
                # A Hit that does not Down is a Tag, not a Down.
                (heavy, ammo, 11, "hit"),
                (heavy, ammo, 12, "miss"),
            ]
        )
        self.assertEqual(result.roles["heavy"].downs_given.mean, 1.0)
        self.assertEqual(result.roles["scout"].downs_taken.mean, 1.0)
        self.assertEqual(result.roles["ammo"].downs_taken.mean, 0.0)
        self.assertEqual(result.who_downs_whom["heavy"]["scout"], 1.0)
        self.assertEqual(result.who_downs_whom["heavy"]["ammo"], 0.0)
        self.assertEqual(result.who_tags_whom["heavy"]["ammo"], 1.0)

    def test_a_miss_at_a_hiding_defender_is_counted_against_the_attacker(self) -> None:
        scout = _scripted(_tick(), role="scout", team="red")
        hidden = _scripted(_tick(), role="medic", team="blue")
        hidden.is_hiding = True
        seen = _scripted(_tick(), role="ammo", team="blue")
        result = _probe_shots(
            [
                (scout, hidden, 10, "miss"),
                (scout, hidden, 11, "miss"),
                (scout, seen, 12, "miss"),
                (scout, hidden, 13, "hit"),
            ]
        )
        self.assertEqual(result.roles["scout"].hiding_misses.mean, 2.0)
        self.assertEqual(result.roles["scout"].shots_fired.mean, 4.0)


class TestShotCadence(SimpleTestCase):
    def test_the_gaps_between_a_players_shots_fill_the_bins_and_the_median(
        self,
    ) -> None:
        scout = _scripted(_tick(), role="scout", team="red")
        target = _scripted(_tick(), role="heavy", team="blue")
        # Gaps of 1, 2, 4, 10, 20, 60, 120 and 121 ticks: one per bin, the last two
        # in the top one; a miss is a shot too.
        ticks = [0, 1, 3, 7, 17, 37, 97, 217, 338]
        result = _probe_shots(
            [(scout, target, tick, "miss" if tick == 3 else "hit") for tick in ticks]
        )
        cadence = result.roles["scout"].cadence
        self.assertEqual(cadence.median, 15.0)
        self.assertEqual(
            cadence.bin_share,
            {
                "<=1t": 0.125,
                "2t": 0.125,
                "3-4t": 0.125,
                "5-10t": 0.125,
                "11-20t": 0.125,
                "21-60t": 0.125,
                "61-120t": 0.125,
                "120t+": 0.125,
            },
        )

    def test_each_players_shots_are_their_own_and_a_round_starts_afresh(self) -> None:
        one = _scripted(_tick(), role="scout", team="red")
        other = _scripted(_tick(), role="scout", team="red")
        target = _scripted(_tick(), role="heavy", team="blue")
        result = _probe_shots(
            [
                (one, target, 0, "hit"),
                (other, target, 1, "hit"),
                (one, target, 3, "hit"),
            ],
            [(one, target, 100, "hit")],
        )
        # One gap of 3 ticks; the other player's shot and the next round add none.
        self.assertEqual(result.roles["scout"].cadence.median, 3.0)
        self.assertEqual(result.roles["scout"].cadence.bin_share["3-4t"], 1.0)

    def test_a_player_with_one_shot_has_no_median(self) -> None:
        result = _probe_shots(
            [(_scripted(_tick()), _scripted(_tick(), team="blue"), 0, "hit")]
        )
        self.assertIsNone(result.roles["scout"].cadence.median)

    def test_the_shot_observer_is_put_back(self) -> None:
        _probe_shots([(_scripted(_tick()), _scripted(_tick(), team="blue"), 0, "hit")])
        self.assertEqual(entrypoints.resolve_shot.__name__, "resolve_shot")
        self.assertEqual(shot.resolve_shot.__name__, "resolve_shot")


def _probe_round_of(players: list, wipe: int = SURVIVED_SENTINEL, play=None):
    """One probed round whose players are ``players``; ``play(sim)`` runs inside
    the round (the observers installed) and ``wipe`` is the round's team-wipe
    tick."""

    def fake_round(self, red, blue, movement_ctx=None):
        if play is not None:
            play(self)
        return {"eliminated_at": wipe}, players, []

    with patch.object(BatchSimulator, "_simulate_round", fake_round):
        return run_probe(None, [1])


class TestSurvivalAndMinutesInPlay(SimpleTestCase):
    """CAL-03a (#589): survival by minute, minutes in play, Tag Downs per minute."""

    def test_survival_is_the_share_still_in_play_at_each_minute(self) -> None:
        result = _probe_scripted(
            [
                _scripted(_tick(), eliminated_at=500),
                _scripted(_tick(), eliminated_at=900),
                _scripted(_tick()),
            ]
        )
        survival = result.roles["scout"].survival
        self.assertEqual(sorted(survival), [5, 7, 9, 11])
        self.assertAlmostEqual(survival[5].mean, 200 / 3)
        self.assertAlmostEqual(survival[9].mean, 100 / 3)
        # Eliminated on the minute's own tick: still in play at that minute.
        at_the_tick = _probe_scripted([_scripted(_tick(), eliminated_at=600)])
        self.assertEqual(at_the_tick.roles["scout"].survival[5].mean, 100.0)
        self.assertEqual(at_the_tick.roles["scout"].survival[7].mean, 0.0)

    def test_minutes_in_play_leave_out_the_time_after_a_wipe(self) -> None:
        survivor = _scripted(_tick())
        out = _scripted(_tick(), eliminated_at=600)
        result = _probe_round_of([survivor, out], wipe=1200)
        # The survivor plays to the wipe at 10 minutes, the other to minute 5.
        self.assertEqual(result.roles["scout"].minutes_in_play.mean, 7.5)

    def test_without_a_wipe_a_survivor_plays_the_whole_round(self) -> None:
        result = _probe_round_of([_scripted(_tick())])
        self.assertEqual(result.roles["scout"].minutes_in_play.mean, 15.0)

    def test_tag_downs_per_minute_in_play(self) -> None:
        heavy = _scripted(_tick(), role="heavy", team="red", eliminated_at=240)
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots([(heavy, scout, 10, "down"), (heavy, scout, 20, "down")])
        # Two Downs in two minutes in play.
        self.assertEqual(result.roles["heavy"].tag_downs_per_minute_in_play.mean, 1.0)


def _resupplied(role: str = "scout", team: str = "red") -> PlayerState:
    player = _capturer(role, team)
    player.final_lives = 3
    return player


class TestLivesLostByCause(SimpleTestCase):
    """CAL-03a (#589): the lives each player lost to Tags, missiles and nukes."""

    def test_each_cause_counts_the_lives_it_took(self) -> None:
        commander = _capturer("commander", "red")
        scout = _resupplied("scout", "blue")  # 3 lives

        def play(sim) -> None:
            sim._complete_missile(commander, scout, 100)  # 3 -> 1
            sim._complete_nuke(commander, 200, [scout])  # 1 -> 0

        result = _probe_round_of([commander, scout], play=play)
        lost = result.roles["scout"].lives_lost
        self.assertEqual(sorted(lost), ["missile", "nuke", "tag"])
        self.assertEqual((lost["missile"].mean, lost["nuke"].mean), (2.0, 1.0))
        self.assertEqual(lost["tag"].mean, 0.0)

    def test_a_tag_down_is_one_life(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots(
            [(commander, scout, 1, "down"), (commander, scout, 2, "hit")]
        )
        self.assertEqual(result.roles["scout"].lives_lost["tag"].mean, 1.0)

    def test_the_missile_observer_is_put_back(self) -> None:
        _probe_round_of([_scripted(_tick())])
        self.assertEqual(BatchSimulator._complete_missile.__name__, "_complete_missile")


class TestResupplyMeasures(SimpleTestCase):
    """CAL-03a (#589): life Resupplies received, doubles and Resupply visits."""

    def _result(self, resupplies: list[tuple[int, str]]) -> ProbeResult:
        from matches.sim_helpers.event_log import EventLog

        medic, ammo = _capturer("medic", "red"), _capturer("ammo", "red")
        scout = _resupplied()

        def play(sim) -> None:
            log = EventLog()
            for tick, kind in resupplies:
                if kind == "lives":
                    log.resupply_lives(medic, scout, tick, amount=3)
                else:
                    log.resupply_ammo(ammo, scout, tick, amount=10)

        return _probe_round_of([medic, ammo, scout], play=play)

    def test_life_resupplies_count_the_lives_kind_only(self) -> None:
        result = self._result([(10, "lives"), (10, "shots"), (90, "lives")])
        self.assertEqual(result.roles["scout"].life_resupplies.mean, 2.0)
        self.assertEqual(result.roles["medic"].life_resupplies.mean, 0.0)

    def test_doubles_pair_the_two_kinds_within_one_second(self) -> None:
        # 10 + 12 pair (2 ticks); 50 and 53 are 3 ticks apart; 90 + 90 are one kind.
        result = self._result(
            [
                (10, "lives"),
                (12, "shots"),
                (50, "lives"),
                (53, "shots"),
                (90, "lives"),
                (90, "lives"),
            ]
        )
        # One double among five Resupply episodes.
        self.assertEqual(result.roles["scout"].doubles_share.mean, 20.0)
        self.assertEqual(result.doubles_share.mean, 20.0)

    def test_resupplies_within_15_seconds_are_one_visit(self) -> None:
        result = self._result(
            [
                (100, "lives"),
                (100, "shots"),
                (130, "lives"),  # 30 ticks: the same visit
                (200, "shots"),  # 70 ticks after: a new visit
                (400, "lives"),
            ]
        )
        visits = result.roles["scout"].visits
        self.assertEqual(visits.per_round.mean, 3.0)
        self.assertEqual(visits.resupplies_per_visit.mean, 5 / 3)
        self.assertEqual(visits.doubles_per_visit.mean, 1 / 3)
        # Gaps of 70 and 200 ticks: 35 and 100 seconds.
        self.assertEqual(visits.gap_seconds.mean, 67.5)
        self.assertEqual(visits.median_gap_seconds, 67.5)

    def test_a_player_never_resupplied_has_no_visit(self) -> None:
        visits = self._result([]).roles["scout"].visits
        self.assertEqual(visits.per_round.mean, 0.0)
        self.assertEqual(visits.resupplies_per_visit.mean, 0.0)
        self.assertIsNone(visits.median_gap_seconds)

    def test_the_resupply_observers_are_put_back(self) -> None:
        from matches.sim_helpers.event_log import EventLog

        self._result([(10, "lives")])
        self.assertEqual(EventLog.resupply_lives.__name__, "resupply_lives")
        self.assertEqual(EventLog.resupply_ammo.__name__, "resupply_ammo")


class TestHitRatioAndShotFirst(SimpleTestCase):
    """CAL-03a (#589): Tags made over times tagged, and the share of Tag Downs
    whose defender Tagged its attacker in the 2 s before."""

    def test_the_hit_ratio_counts_enemy_tags_only(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        heavy = _scripted(_tick(), role="heavy", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots(
            [
                (commander, scout, 1, "hit"),
                (commander, scout, 2, "hit"),
                (scout, commander, 3, "hit"),
                (heavy, commander, 4, "hit"),  # a teammate: not counted
                (commander, scout, 5, "miss"),
            ]
        )
        self.assertEqual(result.roles["commander"].hit_ratio.mean, 2.0)
        self.assertEqual(result.roles["scout"].hit_ratio.mean, 0.5)

    def test_shot_first_is_a_tag_back_within_four_ticks(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots(
            [
                (commander, scout, 10, "hit"),
                (scout, commander, 14, "down"),  # 4 ticks after: shot first
                (commander, scout, 20, "hit"),
                (scout, commander, 25, "down"),  # 5 ticks after: not
                (scout, commander, 40, "down"),  # never tagged back: not
            ]
        )
        self.assertAlmostEqual(result.roles["commander"].shot_first_share.mean, 100 / 3)
        self.assertEqual(result.roles["scout"].shot_first_share.mean, 0.0)

    def test_a_tag_in_the_same_tick_before_the_down_counts(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots(
            [(commander, scout, 10, "hit"), (scout, commander, 10, "down")]
        )
        self.assertEqual(result.roles["commander"].shot_first_share.mean, 100.0)


class TestRoundTags(SimpleTestCase):
    def test_tags_per_round_sum_both_teams(self) -> None:
        result = _probe_scripted(
            [_scripted(_tick(), tags_made=30), _scripted(_tick(), tags_made=10)],
            [_scripted(_tick(), tags_made=20)],
        )
        self.assertEqual(result.round_tags.mean, 30.0)
        self.assertEqual(result.round_tags.se, 10.0)


class TestFormatResult(SimpleTestCase):
    def _lines(self) -> list[str]:
        low = _scripted(_tick("hold", holding=True), _tick(), points=100)
        low.mode_ticks = {Mode.ATTACK: 10}
        high = _scripted(
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("hold", holding=True),
            _tick("hide", plans=("hide",)),
            points=300,
        )
        high.mode_ticks = {Mode.ATTACK: 10, Mode.CONTROL: 30}
        return format_result(_probe_scripted([low], [high]))

    def test_measures_print_the_pooled_mean_its_standard_error_and_the_range(
        self,
    ) -> None:
        # The standard error is the seeds' sample deviation over the root of their count.
        lines = self._lines()
        self.assertIn("  Mean points: 200 +/- 100 (100-300)", lines)
        self.assertIn("  Hold %: 66.7 +/- 12.5 (50.0-75.0)", lines)
        self.assertIn("  Hold run, ticks: 2.0 +/- 1.0 (1.0-3.0)", lines)
        self.assertIn("  Still %: 83.3 +/- 25.0 (50.0-100.0)", lines)

    def test_one_seed_has_a_standard_error_of_nothing(self) -> None:
        lines = format_result(_probe_scripted([_scripted(_tick(), points=100)]))
        self.assertIn("  Mean points: 100 +/- 0 (100-100)", lines)

    def test_goal_sharing_prints_its_standard_error(self) -> None:
        result = ProbeResult(
            roles={},
            sharing_by_seed={1: 0.1, 2: 0.3},
            by_seed={},
            team_captures_by_seed={1: {"red": 0, "blue": 0}},
        )
        self.assertIn(
            "Goal sharing %: mean 20.0 +/- 10.0, min 10.0, max 30.0, "
            "1 of 2 rounds at 15 or more",
            format_result(result),
        )

    def test_existing_measures_print_the_range_too(self) -> None:
        lines = self._lines()
        self.assertIn("  Not Active %: 99.8 +/- 0.1 (99.8-99.9)", lines)
        self.assertIn("  Squares to enemy Medic/Ammo: 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn(
            "  Goals %: action 0.0 (0.0-0.0), positioning 0.0 (0.0-0.0), "
            "override 0.0 (0.0-0.0)",
            lines,
        )

    def test_a_mode_missing_from_a_seed_reads_zero_for_that_seed(self) -> None:
        self.assertIn(
            "  Mode %: attack 40.0 (25.0-100.0), control 60.0 (0.0-75.0)",
            self._lines(),
        )

    def test_only_rolled_actions_print(self) -> None:
        lines = self._lines()
        self.assertIn(
            "  Action %: tag_player 16.7 (0.0-50.0), hide 16.7 (0.0-25.0), "
            "hold 66.7 (50.0-75.0)",
            lines,
        )
        self.assertIn(
            # A seed that never rolled the Action has no empty share to range over.
            "  Empty rolls %: tag_player 100.0 (100.0-100.0), hide 0.0 (0.0-0.0), "
            "hold 100.0 (100.0-100.0)",
            lines,
        )

    def test_per_round_counts_name_what_they_count(self) -> None:
        lines = self._lines()
        self.assertIn("  Tags per round (planned Shots): 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn("  Resupplies per round (planned): 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn("  Live captures per round: 0.0 +/- 0.0 (0.0-0.0)", lines)

    def test_live_captures_print_per_team(self) -> None:
        result = _probe_captures(
            [
                (_capturer("commander", "red"), 15),
                (_capturer("heavy", "red"), 15),
                (_capturer("commander", "blue"), 13),
            ],
            [(_capturer("commander", "red"), 15)],
        )
        self.assertIn(
            "Live captures per round: red 1.5 (1-2), blue 0.5 (0-1)",
            format_result(result),
        )

    def test_a_role_missing_from_a_seed_is_left_out_of_its_range(self) -> None:
        result = _probe_scripted(
            [_scripted(_tick(), points=100)],
            [
                _scripted(_tick(), points=300),
                _scripted(_tick(), role="medic", points=50),
            ],
        )
        self.assertIn("  Mean points: 50 +/- 0 (50-50)", format_result(result))


_TARGETS = {
    "per_role": {
        "scout": {
            "tags": {"mean": 36.42, "se": 0.9, "n": 656},
            "times_tagged": {"mean": 57.44, "se": 1.0, "n": 656},
            "active_share": {"mean": 0.55, "se": 0.01, "n": 656},
            "eliminated": {"mean": 0.34, "se": 0.02, "n": 656},
            "enemies_on_floor": {"mean": 5.47, "se": 0.04, "n": 328},
            # The file keeps both: the sim's shots fired are Tags plus misses, so
            # the like-for-like real figure is the one without Base zaps.
            "shots_fired": {"mean": 58.6, "se": 1.0, "n": 656},
            "shots_at_players": {"mean": 54.3, "se": 1.0, "n": 656},
        },
        "heavy": {"downs_given": {"mean": 43.48, "se": 1.0, "n": 328}},
    },
    "cadence": {
        "scout": {"median": 3.6, "bins": {"<=0.5": 0.27}, "n": 1000},
    },
    "who_tags_whom": {
        "commander": {
            "commander": 28.16,
            "heavy": 22.54,
            "scout": 34.77,
            "ammo": 15.91,
            "medic": 7.65,
        },
    },
    "who_downs_whom": {
        "heavy": {
            "commander": 22.95,
            "heavy": 16.63,
            "scout": 33.09,
            "ammo": 9.92,
            "medic": 4.37,
        },
    },
}


class TestFormatTagMeasures(SimpleTestCase):
    """CAL-02 (#567): the tag measures print as ``mean +/- se (min-max)``, the
    standard error over the player-rounds and the range across the seeds, and the
    real value beside them when targets are given."""

    def _result(self) -> ProbeResult:
        first = _scripted(_tick(), times_tagged=1, tags_made=30, eliminated_at=600)
        first.ticks_active = 900
        first.mode_ticks = {Mode.ATTACK: 900, Mode.CONTROL: 450}
        second = _scripted(_tick(), times_tagged=5, tags_made=10)
        second.ticks_active = 900
        second.mode_ticks = {Mode.ATTACK: 900, Mode.CONTROL: 450}
        third = _scripted(_tick(), times_tagged=9, tags_made=20, eliminated_at=1000)
        third.ticks_active = 900
        third.mode_ticks = {Mode.ATTACK: 1800}
        return _probe_scripted([first, second], [third])

    def test_a_measure_prints_its_player_round_error_and_its_seed_range(self) -> None:
        lines = format_result(self._result())
        # 1, 5 and 9 over three player-rounds: deviation 4 over root 3; the seeds
        # read 3.0 and 9.0.
        self.assertIn("  Times tagged per round: 5.0 +/- 2.3 (3.0-9.0)", lines)
        self.assertIn("  Shots fired per round: 20.0 +/- 5.8 (20.0-20.0)", lines)
        self.assertIn(
            "  Misses at a hiding target per round: 0.0 +/- 0.0 (0.0-0.0)", lines
        )
        self.assertIn("  Downs given per round: 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn("  Downs taken per round: 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn("  Active %: 50.0 +/- 0.0 (50.0-50.0)", lines)
        self.assertIn("  Tags per Active minute: 2.7 +/- 0.8 (2.7-2.7)", lines)
        self.assertIn("  Reaction shots per round: 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn("  Follow-up shots per round: 0.0 +/- 0.0 (0.0-0.0)", lines)
        self.assertIn(
            "  Enemies on the floor per Active roll: 0.0 +/- 0.0 (0.0-0.0)", lines
        )

    def test_mode_shares_over_the_round_print_beside_the_mode_shares(self) -> None:
        lines = format_result(self._result())
        self.assertIn(
            "  Mode % of round: attack 66.7 (50.0-100.0), control 16.7 (0.0-25.0)",
            lines,
        )

    def test_the_cadence_prints_its_median_and_bins(self) -> None:
        one = _scripted(_tick(), role="scout", team="red")
        other = _scripted(_tick(), role="scout", team="red")
        target = _scripted(_tick(), role="heavy", team="blue")
        lines = format_result(
            _probe_shots(
                [
                    (one, target, 0, "hit"),
                    (other, target, 1, "hit"),
                    (one, target, 3, "hit"),
                ]
            )
        )
        self.assertIn(
            "  Shot cadence, ticks: median 3.0 over 1 gaps; <=1t 0%, 2t 0%, 3-4t 100%, "
            "5-10t 0%, 11-20t 0%, 21-60t 0%, 61-120t 0%, 120t+ 0%",
            lines,
        )
        self.assertIn("  Shot cadence, ticks: none", lines)  # the Heavy never shot

    def test_without_targets_no_line_names_a_real_value(self) -> None:
        self.assertFalse(
            [line for line in format_result(self._result()) if "real" in line]
        )

    def test_with_targets_each_role_line_with_a_counterpart_prints_the_real_mean(
        self,
    ) -> None:
        lines = format_result(self._result(), targets=_TARGETS)
        # The existing lines keep their across-seed error and gain the real value.
        self.assertIn(
            "  Tags made per round: 20.0 +/- 0.0 (20.0-20.0) | real 36.4", lines
        )
        self.assertIn("  Eliminated %: 66.7 +/- 25.0 (50.0-100.0) | real 34.0", lines)
        self.assertIn(
            "  Times tagged per round: 5.0 +/- 2.3 (3.0-9.0) | real 57.4", lines
        )
        self.assertIn("  Active %: 50.0 +/- 0.0 (50.0-50.0) | real 55.0", lines)
        self.assertIn(
            "  Enemies on the floor per Active roll: 0.0 +/- 0.0 (0.0-0.0) | real 5.5",
            lines,
        )
        # Like for like: the sim's shots fired are Tags plus misses, so the real
        # figure is ``shots_at_players`` (no Base zaps), not ``shots_fired``.
        self.assertIn(
            "  Shots fired per round: 20.0 +/- 5.8 (20.0-20.0) | real 54.3", lines
        )
        # No real figure in the file for this metric: the line is as without targets.
        self.assertIn("  Downs given per round: 0.0 +/- 0.0 (0.0-0.0)", lines)

    def test_the_cadence_line_prints_the_real_median_in_ticks(self) -> None:
        one = _scripted(_tick(), role="scout", team="red")
        target = _scripted(_tick(), role="heavy", team="blue")
        result = _probe_shots([(one, target, 0, "hit"), (one, target, 3, "hit")])
        lines = format_result(result, targets=_TARGETS)
        # The file's median is in seconds; a tick is 0.5 s, so 3.6 s is 7.2 ticks.
        self.assertIn(
            "  Shot cadence, ticks: median 3.0 over 1 gaps; <=1t 0%, 2t 0%, 3-4t 100%, "
            "5-10t 0%, 11-20t 0%, 21-60t 0%, 61-120t 0%, 120t+ 0% "
            "| real median 7.2 ticks",
            lines,
        )
        # No real cadence for the Heavy in the file: its line is as without targets.
        self.assertIn("  Shot cadence, ticks: none", lines)
        self.assertFalse(
            [line for line in format_result(result) if "real median" in line]
        )

    def test_the_matrices_print_per_round_with_a_total_and_the_real_rows(self) -> None:
        commander = _scripted(_tick(), role="commander", team="red")
        scout = _scripted(_tick(), role="scout", team="blue")
        result = _probe_shots(
            [(commander, scout, 10, "hit"), (commander, scout, 12, "down")],
            [(commander, scout, 5, "hit")],
        )
        lines = format_result(result)
        header = lines.index(
            "Who tags whom per round, both teams (attacker row, defender column):"
        )
        self.assertEqual(
            lines[header + 1],
            "             commander     heavy     scout      ammo     medic     total",
        )
        self.assertEqual(
            lines[header + 2],
            "commander          0.0       0.0       1.5       0.0       0.0       1.5",
        )
        downs = lines.index(
            "Who Downs whom per round, both teams (attacker row, defender column):"
        )
        self.assertEqual(
            lines[downs + 2],
            "commander          0.0       0.0       0.5       0.0       0.0       0.5",
        )
        self.assertFalse([line for line in lines if line.startswith("  real")])

        with_real = format_result(result, targets=_TARGETS)
        header = with_real.index(
            "Who tags whom per round, both teams (attacker row, defender column):"
        )
        self.assertEqual(
            with_real[header + 3],
            "  real            28.2      22.5      34.8      15.9       7.7     109.0",
        )
        # A row with no real figures has no real line under it.
        self.assertEqual(with_real[header + 4][:5], "heavy")


class TestFormatCal03Measures(SimpleTestCase):
    """CAL-03a (#589): each new measure prints ``mean +/- se (min-max)`` per role,
    with the real figure beside it in a targets run."""

    def _result(self) -> ProbeResult:
        return _probe_scripted(
            [_scripted(_tick(), role="ammo", eliminated_at=500), _scripted(_tick())],
            [_scripted(_tick(), role="ammo"), _scripted(_tick())],
        )

    def test_every_role_prints_the_new_measures(self) -> None:
        lines = format_result(self._result())
        ammo = lines[lines.index("ammo") :]
        for text in (
            "  Survival % at minute 5: 50.0 +/- 50.0 (0.0-100.0)",
            "  Survival % at minute 11: 50.0 +/- 50.0 (0.0-100.0)",
            "  Minutes in play: 9.6 +/- 5.4 (4.2-15.0)",
            "  Tag Downs per minute in play: 0.00 +/- 0.00 (0.00-0.00)",
            "  Lives lost to Tags: 0.0 +/- 0.0 (0.0-0.0)",
            "  Lives lost to missiles: 0.0 +/- 0.0 (0.0-0.0)",
            "  Lives lost to nukes: 0.0 +/- 0.0 (0.0-0.0)",
            "  Life Resupplies received: 0.0 +/- 0.0 (0.0-0.0)",
            "  Doubles % of Resupplies received: 0.0 +/- 0.0 (0.0-0.0)",
            "  Hit ratio (Tags made / times tagged): 0.00 +/- 0.00 (0.00-0.00)",
            "  Shot-first % of Tag Downs: 0.0 +/- 0.0 (0.0-0.0)",
            "  Resupply visits per round: 0.0 +/- 0.0 (0.0-0.0)",
            "  Resupplies per visit: 0.00 +/- 0.00 (0.00-0.00)",
            "  Doubles per visit: 0.00 +/- 0.00 (0.00-0.00)",
            "  Gap to the next visit, s: none",
        ):
            with self.subTest(line=text):
                self.assertIn(text, ammo)
        self.assertIn("Tags per round, both teams: 0.0 +/- 0.0", lines)
        self.assertIn("Doubles % of all Resupplies received: 0.0 +/- 0.0", lines)
        self.assertFalse([line for line in lines if "real" in line])

    def test_a_targets_run_prints_the_real_figure_beside_each(self) -> None:
        lines = format_result(self._result(), targets=_TARGETS)
        ammo = lines[lines.index("ammo") : lines.index("scout")]
        for text in (
            "  Survival % at minute 5: 50.0 +/- 50.0 (0.0-100.0) | real 97.0",
            "  Survival % at minute 9: 50.0 +/- 50.0 (0.0-100.0) | real 80.0",
            "  Tag Downs per minute in play: 0.00 +/- 0.00 (0.00-0.00) | real 2.11",
            "  Life Resupplies received: 0.0 +/- 0.0 (0.0-0.0) | real 10.2",
            "  Hit ratio (Tags made / times tagged): 0.00 +/- 0.00 (0.00-0.00) "
            "| real 0.99",
            "  Shot-first % of Tag Downs: 0.0 +/- 0.0 (0.0-0.0) | real 19.0",
        ):
            with self.subTest(line=text):
                self.assertIn(text, ammo)
        # No real figure at minute 11, or for an Ammo's visits.
        self.assertIn("  Survival % at minute 11: 50.0 +/- 50.0 (0.0-100.0)", ammo)
        self.assertIn("  Resupply visits per round: 0.0 +/- 0.0 (0.0-0.0)", ammo)
        scout = lines[lines.index("scout") :]
        for text in (
            "  Doubles % of Resupplies received: 0.0 +/- 0.0 (0.0-0.0) | real 46.0",
            "  Resupply visits per round: 0.0 +/- 0.0 (0.0-0.0) | real 4.6",
            "  Resupplies per visit: 0.00 +/- 0.00 (0.00-0.00) | real 2.77",
            "  Doubles per visit: 0.00 +/- 0.00 (0.00-0.00) | real 0.87",
            "  Gap to the next visit, s: none | real 111",
        ):
            with self.subTest(line=text):
                self.assertIn(text, scout)
        self.assertIn("Tags per round, both teams: 0.0 +/- 0.0 | real 403.0", lines)
        self.assertIn(
            "Doubles % of all Resupplies received: 0.0 +/- 0.0 | real 32.1", lines
        )

    def test_the_elimination_tick_prints_the_real_minute_in_ticks(self) -> None:
        targets = {"per_role": {"ammo": {"elim_minute": {"mean": 9.7, "se": 0.3}}}}
        lines = format_result(self._result(), targets=targets)
        self.assertIn("  Elimination tick: 500 +/- 0 (500-500) | real 1164.0", lines)

    def test_a_visit_gap_prints_its_median_and_mean(self) -> None:
        from matches.sim_helpers.event_log import EventLog

        medic, scout = _capturer("medic", "red"), _resupplied()

        def play(sim) -> None:
            for tick in (100, 200, 400):
                EventLog().resupply_lives(medic, scout, tick)

        lines = format_result(_probe_round_of([medic, scout], play=play))
        self.assertIn(
            "  Gap to the next visit, s: median 75.0 (mean 75.0 +/- 25.0)", lines
        )


class TestCalibrationLineups(SimpleTestCase):
    """CAL-02 (#567): the Test games' rosters, built as the Calibration harness does."""

    def test_each_used_test_game_gives_a_red_and_a_blue_roster_in_position_order(
        self,
    ) -> None:
        lineups = calibration_lineups(limit=2)
        self.assertEqual(len(lineups), 2)
        for red, blue in lineups:
            for roster in (red, blue):
                self.assertEqual(
                    [role for role, _ in roster],
                    ["commander", "heavy", "scout", "scout", "ammo", "medic"],
                )
            self.assertNotEqual([p.id for _, p in red], [p.id for _, p in blue])

    def test_every_used_test_game_by_default(self) -> None:
        self.assertEqual(len(calibration_lineups()), 128)


class TestProbeSeeds(SimpleTestCase):
    def test_one_63_bit_seed_per_game_from_the_master_seed(self) -> None:
        seeds = probe_seeds(42, 3)
        self.assertEqual(len(set(seeds)), 3)
        self.assertEqual(seeds, probe_seeds(42, 3))
        self.assertNotEqual(seeds, probe_seeds(43, 3))
        self.assertTrue(all(0 <= seed < 2**63 for seed in seeds))


class TestLineupsProbe(TestCase):
    """CAL-02 (#567): two Test games, in this process and across two workers."""

    @classmethod
    def setUpTestData(cls) -> None:
        cls.movement_ctx, _ = load_map_context(load_map_fixture(_SYRACUSE))

    def test_serial_and_two_workers_give_the_same_result(self) -> None:
        lineups = calibration_lineups(limit=2)
        seeds = probe_seeds(42, 2)
        serial = run_probe(self.movement_ctx, seeds, lineups=lineups, workers=1)
        parallel = run_probe(self.movement_ctx, seeds, lineups=lineups, workers=2)
        self.assertEqual(serial, parallel)
        self.assertEqual(list(serial.by_seed), seeds)
        self.assertEqual(set(serial.roles), _ROLES)
        self.assertGreater(serial.roles["commander"].times_tagged.mean, 0.0)
        self.assertGreater(serial.roles["commander"].shots_fired.mean, 0.0)
        self.assertGreater(serial.who_tags_whom["commander"]["scout"], 0.0)
        self.assertIsNotNone(serial.roles["commander"].cadence.median)
        # CAL-03a: the new measures come through the workers too.
        self.assertGreater(serial.round_tags.mean, 0.0)
        self.assertGreater(serial.roles["ammo"].life_resupplies.mean, 0.0)
        self.assertGreater(serial.roles["commander"].visits.per_round.mean, 0.0)


class TestRoundProbeCommand(TestCase):
    def test_the_test_lineups_print_with_the_real_values(self) -> None:
        out = StringIO()
        call_command("round_probe", "--lineups", "test", "--games", "1", stdout=out)
        output = out.getvalue()
        for text in (
            "Times tagged per round",
            "| real",
            "Who tags whom per round",
            "Who Downs whom per round",
            "  real",
            "Shot cadence, ticks",
            # CAL-03a: the new measures, with the real figures beside them.
            "Survival % at minute 7",
            "Hit ratio (Tags made / times tagged)",
            "Resupply visits per round",
            "Tags per round, both teams",
            "| real 403.0",
            "| real 32.1",
        ):
            self.assertIn(text, output)
        self.assertTrue(output.isascii())

    def test_an_unknown_lineup_set_is_refused(self) -> None:
        with self.assertRaisesMessage(CommandError, "bogus"):
            call_command("round_probe", "--lineups", "bogus", stdout=StringIO())

    def test_a_worker_count_below_one_is_refused(self) -> None:
        with self.assertRaisesMessage(CommandError, "--workers"):
            call_command("round_probe", "--workers", "0", stdout=StringIO())

    def test_a_missing_targets_file_is_named(self) -> None:
        with self.assertRaisesMessage(CommandError, "no_such_targets.json"):
            call_command(
                "round_probe",
                "--seeds",
                "1",
                "--targets",
                "no_such_targets.json",
                stdout=StringIO(),
            )

    def test_it_prints_the_measures_and_writes_nothing(self) -> None:
        maps, teams, players = (
            ArenaMap.objects.count(),
            Team.objects.count(),
            Player.objects.count(),
        )
        out = StringIO()

        call_command("round_probe", "--seeds", "1", stdout=out)

        output = out.getvalue()
        for text in (
            "commander",
            "enemy Medic/Ammo",
            "own Heavy",
            "Goal sharing",
            "Action %",
            "Empty rolls %",
            "Still %",
            "Hold run, ticks",
            "Tags per round",
            "Tags made per round",
            "Eliminated %",
            "Resupplies per round",
            "Live captures per round: red",
        ):
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
