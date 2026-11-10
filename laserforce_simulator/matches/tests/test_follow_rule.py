"""MECH-10: unit tests for ``sim_helpers.follow_rule`` (the Follow rule).

Hand-built grids: ``.`` is floor, ``#`` is wall. A Downed player walks a
straight line; enemies walk alongside, across or against it. A Chase is
10 squares of progress along the path while inside the Trail (6 squares'
walk either side, never through a wall); the ban lasts until the Chased
player is Active again + 4 ticks.
"""

from __future__ import annotations

import unittest
from typing import Any

import pytest

from matches.sim_helpers.follow_rule import may_tag, record_step, start_trail
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.time_constants import RESPAWN_TICKS

GRACE = 4


def _grid(rows: list[str]) -> MapContext:
    """A map over the ``.`` cells of ``rows`` (4-connected) at 1 ft a square,
    so the rule's feet read as squares: a 10-square Chase, a 6-square band."""
    floor = {
        (r, c) for r, line in enumerate(rows) for c, ch in enumerate(line) if ch == "."
    }
    adjacency = {
        (r, c): [
            n for n in ((r - 1, c), (r + 1, c), (r, c - 1), (r, c + 1)) if n in floor
        ]
        for (r, c) in floor
    }
    return MapContext(
        adj=adjacency,
        spawn_cells={},
        zone_data=None,
        sight_data=None,
        base_sight_data={},
        cell_los_counts={},
        high_los_cells=[],
        strong_spots=[],
        wall_meta={},
        team_spawn_pools={},
        feet_per_square=1.0,
    )


OPEN = _grid(["." * 40] * 25)

# Two hallways, rows 0-6 and 8-12, split by a solid wall on row 7.
TWO_HALLS = _grid(["." * 40] * 7 + ["#" * 40] + ["." * 40] * 5)


def _ps(tag: str, team: str, cell: tuple[int, int]) -> PlayerState:
    p = PlayerState(
        tag_id=tag,
        name=tag,
        team_color=team,
        role="scout",
        accuracy=50,
        survival=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
    )
    p.cell_row, p.cell_col = cell
    return p


def _down(player: PlayerState, tick: int, adj: MapContext) -> None:
    """What ``record_down`` does for the rule: start the Trail, then stamp."""
    start_trail(player, tick, adj)
    player.last_downed_time = tick


def _walk(player, cells, tick, everyone, adj) -> None:
    """Advance ``player`` from its cell through ``cells`` on ``tick``."""
    start = (player.cell_row, player.cell_col)
    record_step(player, [start, *cells], tick, everyone, adj)
    player.cell_row, player.cell_col = cells[-1]


def _row(r: int, c_from: int, c_to: int) -> list[tuple[int, int]]:
    step = 1 if c_to >= c_from else -1
    return [(r, c) for c in range(c_from, c_to + step, step)]


class _Hallway(unittest.TestCase):
    """Blue scout Downed at (12, 0) on tick 100, then walks row 12 to col 30."""

    adj = OPEN
    row = 12

    def setUp(self) -> None:
        self.downed = _ps("blue_scout_1", "blue", (self.row, 0))
        self.chaser = _ps("red_scout_1", "red", (self.row, 0))
        self.everyone = [self.downed, self.chaser]
        _down(self.downed, 100, self.adj)
        _walk(self.downed, _row(self.row, 1, 30), 101, self.everyone, self.adj)


class TestStraightFollow(_Hallway):
    def test_ten_squares_along_the_trail_bars_the_tag(self) -> None:
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))

    def test_nine_squares_does_not(self) -> None:
        _walk(self.chaser, _row(12, 1, 9), 102, self.everyone, self.adj)
        self.assertTrue(may_tag(self.chaser, self.downed, 110))

    def test_progress_adds_up_across_ticks(self) -> None:
        _walk(self.chaser, _row(12, 1, 5), 102, self.everyone, self.adj)
        _walk(self.chaser, _row(12, 6, 10), 103, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))

    def test_ban_lifts_two_seconds_after_active(self) -> None:
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        lifts = 100 + RESPAWN_TICKS + GRACE
        self.assertFalse(may_tag(self.chaser, self.downed, lifts - 1))
        self.assertTrue(may_tag(self.chaser, self.downed, lifts))

    def test_steps_after_active_add_nothing(self) -> None:
        active = 100 + RESPAWN_TICKS
        _walk(self.chaser, _row(12, 1, 20), active, self.everyone, self.adj)
        self.assertTrue(may_tag(self.chaser, self.downed, active + 1))

    def test_another_enemy_in_the_trail_stays_taggable(self) -> None:
        bystander = _ps("blue_heavy", "blue", (12, 15))
        self.everyone.append(bystander)
        _walk(self.chaser, _row(12, 1, 14), 102, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))
        self.assertTrue(may_tag(self.chaser, bystander, 110))

    def test_every_enemy_can_chase_and_teammates_never_do(self) -> None:
        second = _ps("red_scout_2", "red", (11, 0))
        mate = _ps("blue_ammo", "blue", (13, 0))
        self.everyone += [second, mate]
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        _walk(second, _row(11, 1, 10), 102, self.everyone, self.adj)
        _walk(mate, _row(13, 1, 10), 102, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))
        self.assertFalse(may_tag(second, self.downed, 110))
        self.assertEqual(mate._chases, {})

    def test_a_downed_chaser_keeps_the_ban(self) -> None:
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        _down(self.chaser, 104, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))


class TestNotAChase(_Hallway):
    def test_crossing_the_trail_gives_no_ban(self) -> None:
        crosser = _ps("red_heavy", "red", (0, 15))
        self.everyone.append(crosser)
        _walk(crosser, [(r, 15) for r in range(1, 25)], 102, self.everyone, self.adj)
        self.assertTrue(may_tag(crosser, self.downed, 110))

    def test_head_on_approach_gives_no_ban(self) -> None:
        facing = _ps("red_heavy", "red", (12, 39))
        self.everyone.append(facing)
        _walk(facing, _row(12, 38, 5), 102, self.everyone, self.adj)
        self.assertTrue(may_tag(facing, self.downed, 110))

    def test_distance_outside_the_trail_does_not_count(self) -> None:
        # 6 squares inside, out to row 20 (outside the 6-square band), 5 columns
        # along out there, back in, then 3 more inside: 9 counted, not 14.
        steps = (
            _row(12, 1, 6)
            + [(r, 6) for r in range(13, 21)]
            + _row(20, 7, 11)
            + [(r, 11) for r in range(19, 11, -1)]
            + _row(12, 12, 14)
        )
        _walk(self.chaser, steps, 102, self.everyone, self.adj)
        self.assertTrue(may_tag(self.chaser, self.downed, 110))
        _walk(self.chaser, [(12, 15)], 103, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))


class TestWalls(_Hallway):
    adj = TWO_HALLS
    row = 3

    def test_parallel_hallway_behind_a_wall_is_outside_the_trail(self) -> None:
        # Row 9 is 6 squares from row 3 as the crow flies, but the wall on
        # row 7 leaves no walk between them.
        parallel = _ps("red_heavy", "red", (9, 0))
        self.everyone.append(parallel)
        _walk(parallel, _row(9, 1, 25), 102, self.everyone, self.adj)
        self.assertTrue(may_tag(parallel, self.downed, 110))
        self.assertFalse(any(r > 7 for r, _ in self.downed._trail.band))


class TestReDown(_Hallway):
    def test_down_before_active_extends_the_trail_and_the_ban(self) -> None:
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        _down(self.downed, 110, self.adj)  # Reset window: not yet Active
        lifts = 110 + RESPAWN_TICKS + GRACE
        self.assertFalse(may_tag(self.chaser, self.downed, lifts - 1))
        self.assertTrue(may_tag(self.chaser, self.downed, lifts))

    def test_extended_trail_keeps_growing(self) -> None:
        _down(self.downed, 110, self.adj)
        _walk(self.downed, _row(12, 31, 39), 120, self.everyone, self.adj)
        # 10 squares, 6 of them on the path the extension added (cols 31-35).
        self.chaser.cell_col = 25
        _walk(self.chaser, _row(12, 26, 35), 121, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, 125))

    def test_down_after_active_starts_fresh_and_ends_the_ban(self) -> None:
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        _down(self.downed, 100 + RESPAWN_TICKS + 1, self.adj)  # in the grace
        self.assertTrue(may_tag(self.chaser, self.downed, 100 + RESPAWN_TICKS + 2))


class _Barred(_Hallway):
    """The red scout has Chased the blue scout; tick 110 is in the blue
    scout's Reset window (Taggable, not Active)."""

    tick = 110

    def setUp(self) -> None:
        super().setUp()
        _walk(self.chaser, _row(12, 1, 10), 102, self.everyone, self.adj)
        self.assertFalse(may_tag(self.chaser, self.downed, self.tick))


class TestEngineRefusesBarredTags(_Barred):
    def test_choose_tag_target_skips_the_chased_player(self) -> None:
        from matches.sim_helpers.mechanics import choose_tag_target

        everyone_visible = lambda actor, candidates, ctx: candidates  # noqa: E731
        other = _ps("blue_heavy", "blue", (12, 12))
        picks = [
            choose_tag_target(
                self.chaser,
                [self.downed, other],
                self.tick,
                los_filter=everyone_visible,
            )
            for _ in range(20)
        ]
        self.assertTrue(all(pick is other for pick in picks))
        alone = choose_tag_target(
            self.chaser, [self.downed], self.tick, los_filter=everyone_visible
        )
        self.assertIsNone(alone)

    def test_resolve_shot_is_no_shot_at_a_chased_player(self) -> None:
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext
        from matches.sim_helpers.shot import (
            SHOT_KIND_FOLLOW_UP,
            SHOT_KIND_INITIAL,
            SHOT_KIND_OVERWATCH,
            SHOT_KIND_REACTION,
            resolve_shot,
        )

        for kind in (
            SHOT_KIND_INITIAL,
            SHOT_KIND_FOLLOW_UP,
            SHOT_KIND_REACTION,
            SHOT_KIND_OVERWATCH,
        ):
            with self.subTest(kind=kind):
                ctx = RoundContext(events=EventLog(persist=True))
                outcome = resolve_shot(
                    self.chaser, self.downed, self.tick, kind=kind, ctx=ctx
                )
                self.assertFalse(outcome.hit)
                self.assertEqual(self.chaser.final_shots, 20)
                self.assertEqual(self.downed.final_lives, 10)
                self.assertEqual(ctx.events.entries, [])

    def test_chased_tag_ids_names_only_the_chased_player(self) -> None:
        from matches.sim_helpers.follow_rule import chased_tag_ids

        other = _ps("blue_heavy", "blue", (12, 12))
        everyone = [self.downed, other, self.chaser]
        self.assertEqual(
            chased_tag_ids(self.chaser, everyone, self.tick), {"blue_scout_1"}
        )
        lifts = 100 + RESPAWN_TICKS + GRACE
        self.assertEqual(chased_tag_ids(self.chaser, everyone, lifts), set())

    def test_overwatch_holder_keeps_its_shot_for_a_legal_mover(self) -> None:
        """A barred mover crossing first must not use up the holder's one
        Overwatch shot this tick."""
        from types import SimpleNamespace

        from matches.simulation import BatchSimulator

        other = _ps("blue_heavy", "blue", (12, 12))
        self.chaser.is_holding = True
        sees_all: Any = SimpleNamespace(can_see=lambda a, b: True)
        attempts = BatchSimulator()._collect_overwatch_attempts(
            [self.downed, other, self.chaser], self.tick, sees_all
        )
        self.assertEqual([a["defender"] for a in attempts], [other])

    def test_missile_does_not_land_on_a_chased_player(self) -> None:
        from matches.simulation import BatchSimulator

        self.chaser.final_missiles = 1
        BatchSimulator()._complete_missile(self.chaser, self.downed, self.tick)
        self.assertEqual(self.downed.final_lives, 10)
        self.assertEqual(self.chaser.counters.missiles_landed, 0)

    def test_missile_pick_skips_the_chased_player(self) -> None:
        from unittest import mock

        from matches.sim_helpers import combat

        self.chaser.final_missiles = 1
        other = _ps("blue_heavy", "blue", (12, 12))
        for p in (self.chaser, self.downed, other):
            p.current_zone = 1  # map-less LOS: the same Zone is visible
        with mock.patch.object(
            combat.random, "choices", lambda population, weights: ["missile_player"]
        ):
            alone = combat.plan_action(self.chaser, [self.downed], self.tick)
            both = combat.plan_action(self.chaser, [self.downed, other], self.tick)
        self.assertEqual([p for p in alone if p["type"] == "missile"], [])
        missiles = [p for p in both if p["type"] == "missile"]
        self.assertEqual(len(missiles), 1)
        self.assertIs(missiles[0]["target"], other)

    def test_tag_goal_skips_the_chased_player(self) -> None:
        from matches.sim_helpers.pathfinding import _goal_from_action

        far = _ps("blue_heavy", "blue", (20, 30))
        self.chaser.cell_row, self.chaser.cell_col = (12, 11)
        # MECH-17: goals read memory only, so both are remembered, and the Chased
        # player is the nearer. OPEN has no sight data, so the goal is the target's
        # cell. The invariant is unchanged: a Chaser's Tag goal skips who they Chased.
        for player, cell in ((self.downed, (12, 12)), (far, (20, 30))):
            self.chaser.player_memory[player.tag_id_key] = {
                "cell": cell,
                "timestamp": self.tick,
                "role": "heavy",
            }
        goal = _goal_from_action(
            self.chaser,
            [self.downed, far, self.chaser],
            "blue",
            12,
            11,
            "tag_player",
            OPEN,
            self.tick,
            "ticks",
        )
        self.assertEqual(goal, (20, 30))

    def test_commander_skips_a_chased_medic(self) -> None:
        from dataclasses import replace

        from matches.sim_helpers.pathfinding import _goal_from_action, _goal_from_role

        medic = _ps("blue_medic", "blue", (12, 0))
        medic.role = "medic"
        cmdr = _ps("red_commander", "red", (12, 0))
        cmdr.role = "commander"
        everyone = [medic, cmdr]
        _down(medic, 100, self.adj)
        _walk(medic, _row(12, 1, 30), 101, everyone, self.adj)
        _walk(cmdr, _row(12, 1, 10), 102, everyone, self.adj)
        cmdr.player_memory["blue_medic"] = {
            "cell": (12, 30),
            "timestamp": self.tick,
            "role": "medic",
        }
        far = _ps("blue_heavy", "blue", (20, 30))
        everyone.append(far)
        # MECH-07: the Attack rule draws cells that can see the Medic, so it needs a
        # real map, and (12, 29) is the cell it would take if the Chase were ignored.
        ctx = replace(
            OPEN, sight_data={"12,30": frozenset({"12,29"})}, high_los_cells=[]
        )
        action_goal = _goal_from_action(
            cmdr, everyone, "blue", 12, 10, "tag_player", ctx, self.tick, "ticks"
        )
        role_goal = _goal_from_role(
            cmdr, everyone, "blue", ctx, self.tick, time_domain="ticks"
        )
        self.assertNotEqual(action_goal, (12, 30))
        self.assertIsNone(role_goal)


class TestDownAndResupplyStartTrails(unittest.TestCase):
    def _ctx(self):
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        return RoundContext(events=EventLog(persist=False), movement_ctx=OPEN)

    def test_record_down_starts_a_trail(self) -> None:
        from matches.sim_helpers.down import record_down

        p = _ps("blue_scout_1", "blue", (12, 3))
        record_down(p, 100, self._ctx())
        self.assertEqual(p._trail.start_tick, 100)
        self.assertEqual(p._trail.path, [(12, 3)])

    def test_resupply_starts_a_trail_on_the_receiver(self) -> None:
        from matches.sim_helpers.combat import attempt_resupply

        for role in ("medic", "ammo"):
            with self.subTest(role=role):
                support = _ps(f"blue_{role}", "blue", (12, 2))
                support.role = role
                mate = _ps("blue_scout_1", "blue", (12, 3))
                mate.final_lives = mate.final_shots = 5
                attempt_resupply(support, mate, 100, ctx=self._ctx())
                self.assertEqual(mate._trail.start_tick, 100)


@pytest.mark.django_db
class TestSeededRoundOnAMap:
    """Real seeded rounds: bans form, and no Tag or Missile hit lands while
    the attacker is barred.

    The check runs when the tag / missiled event is emitted, which is after
    the hit's own Down. A Down in the Reset window extends the Trail, so the
    ban is still visible there; a hit in the 2 s grace starts a fresh Trail
    and is covered by the unit tests of the ``resolve_shot`` gate instead.
    """

    def test_no_tag_lands_on_a_chased_player(self, monkeypatch) -> None:
        import random

        from matches.sim_helpers import follow_rule
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.map_loader import load_map_context
        from matches.simulation import BatchSimulator, entrypoints
        from matches.tests.conftest import make_team_with_slots
        from matches.tests.test_batch_sim import _move01_make_map

        bans: list = []
        violations: list = []

        def spy_step(player, step_cells, tick, all_alive, movement_ctx):
            follow_rule.record_step(player, step_cells, tick, all_alive, movement_ctx)
            bans.extend(c for c in player._chases.values() if c.banned_at == tick)

        real_tag, real_missiled = EventLog.tag, EventLog.missiled

        def spy_tag(self, attacker, defender, tick, **kw):
            if not may_tag(attacker, defender, tick):
                violations.append(("tag", attacker.tag_id, defender.tag_id, tick))
            real_tag(self, attacker, defender, tick, **kw)

        # MECH-21: ``missiled`` lost its ``result`` kwarg; every row is a
        # launch, and a launched missile always hits, so every row is checked.
        def spy_missiled(self, attacker, defender, tick, *, friendly_fire):
            if not may_tag(attacker, defender, tick):
                violations.append(("missile", attacker.tag_id, defender.tag_id, tick))
            real_missiled(self, attacker, defender, tick, friendly_fire=friendly_fire)

        monkeypatch.setattr(entrypoints, "record_step", spy_step)
        monkeypatch.setattr(EventLog, "tag", spy_tag)
        monkeypatch.setattr(EventLog, "missiled", spy_missiled)

        red, _ = make_team_with_slots("FRRed")
        blue, _ = make_team_with_slots("FRBlue")
        movement_ctx, _ = load_map_context(_move01_make_map("FollowRuleArena"))
        for seed in range(1, 4):
            random.seed(seed)
            BatchSimulator()._simulate_round(
                list(red.active_roster),
                list(blue.active_roster),
                event_log=[],
                movement_ctx=movement_ctx,
            )
        assert bans, "the rule never fired in these rounds"
        assert violations == []


class TestComboResupply(unittest.TestCase):
    """A combo Resupply calls ``attempt_resupply`` twice on one tick, with the
    receiver's old ``last_downed_time`` restored between the calls, so the
    second call starts a fresh Trail rather than extending: same start tick,
    same one-cell path, so a Chase keyed on it is unaffected."""

    def test_combo_resupply_leaves_one_trail_from_that_tick(self) -> None:
        from unittest import mock

        from matches.sim_helpers import resupply_queue
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        medic = _ps("blue_medic", "blue", (12, 2))
        medic.role = "medic"
        ammo = _ps("blue_ammo", "blue", (12, 4))
        ammo.role = "ammo"
        mate = _ps("blue_scout_1", "blue", (12, 3))
        mate.final_lives = mate.final_shots = 5
        ctx = RoundContext(events=EventLog(persist=False), movement_ctx=OPEN)
        with mock.patch.object(resupply_queue.random, "random", lambda: 0.0):
            resupply_queue.resolve_resupply_requests(
                [mate], [medic, ammo, mate], 100, None, ctx=ctx
            )
        self.assertEqual(mate.counters.combo_resupply_count, 1)
        self.assertEqual(mate._trail.start_tick, 100)
        self.assertEqual(mate._trail.path, [(12, 3)])
        self.assertEqual(mate.last_downed_time, 100)


class TestFeetScale(unittest.TestCase):
    """MOVE-08: the 10 ft limit and 6 ft band are squares by the map's scale.
    At 5 ft a square (Syracuse) a Chase is 2 squares and the band 1 wide."""

    def setUp(self) -> None:
        self.mc = _grid(["." * 20] * 9)
        self.mc.feet_per_square = 5.0
        self.downed = _ps("blue_scout_1", "blue", (4, 0))
        self.chaser = _ps("red_scout_1", "red", (4, 0))
        self.everyone = [self.downed, self.chaser]
        _down(self.downed, 100, self.mc)
        _walk(self.downed, _row(4, 1, 15), 101, self.everyone, self.mc)

    def test_two_squares_is_a_chase(self) -> None:
        _walk(self.chaser, _row(4, 1, 2), 102, self.everyone, self.mc)
        self.assertFalse(may_tag(self.chaser, self.downed, 110))

    def test_one_square_is_not(self) -> None:
        _walk(self.chaser, _row(4, 1, 1), 102, self.everyone, self.mc)
        self.assertTrue(may_tag(self.chaser, self.downed, 110))

    def test_band_is_one_square_wide(self) -> None:
        rows = {r for r, _ in self.downed._trail.band}
        self.assertEqual(rows, {3, 4, 5})


class TestRoundMap(unittest.TestCase):
    def test_the_map_comes_from_the_round_context(self) -> None:
        from matches.sim_helpers.follow_rule import round_map
        from matches.sim_helpers.round_context import RoundContext

        self.assertIs(round_map(RoundContext(movement_ctx=OPEN)), OPEN)

    def test_no_context_or_no_map_gives_none(self) -> None:
        from matches.sim_helpers.follow_rule import round_map
        from matches.sim_helpers.round_context import RoundContext

        self.assertIsNone(round_map(None))
        self.assertIsNone(round_map(RoundContext(movement_ctx=None)))


class TestInertWithoutMap(unittest.TestCase):
    def test_no_adjacency_starts_no_trail(self) -> None:
        p = _ps("blue_scout_1", "blue", (0, 0))
        start_trail(p, 100, None)
        self.assertIsNone(p._trail)

    def test_no_cell_starts_no_trail(self) -> None:
        p = _ps("blue_scout_1", "blue", (0, 0))
        p.cell_row = p.cell_col = None
        start_trail(p, 100, OPEN)
        self.assertIsNone(p._trail)


if __name__ == "__main__":
    unittest.main()
