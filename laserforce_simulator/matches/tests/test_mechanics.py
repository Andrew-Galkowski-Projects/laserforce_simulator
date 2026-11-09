"""
Tests for shared game-mechanic functions in matches/sim_helpers/mechanics.py.
These functions are called by both ResourceBasedSimulator and BatchSimulator,
so testing them here covers both simulators simultaneously.
"""

from unittest.mock import patch

from matches.sim_helpers.mechanics import (
    cooldown_ticks,
    shot_cooldown,
    choose_tag_target,
    choose_resupply_target,
    choose_zone_change,
)
from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# Shared player factory
# ---------------------------------------------------------------------------


def _ps(role, team_color="red", **kwargs):
    defaults = dict(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=0,
        player_awareness=50,
        starting_lives=15,
        starting_shots=30,
        final_lives=15,
        final_shots=30,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


# ---------------------------------------------------------------------------
# shot_cooldown
# ---------------------------------------------------------------------------


class TestShotCooldown:
    def test_regular_roles_return_half_second(self):
        for role in ("commander", "medic", "ammo"):
            assert shot_cooldown(_ps(role), 0.0) == 0.5

    def test_heavy_returns_one_second(self):
        assert shot_cooldown(_ps("heavy"), 0.0) == 1.0

    def test_scout_without_special_returns_half_second(self):
        assert shot_cooldown(_ps("scout", special_active_until=0), 1.0) == 0.5

    def test_rapid_fire_scout_returns_zero(self):
        assert shot_cooldown(_ps("scout", special_active_until=10), 2.0) == 0.0

    def test_scout_special_boundary_active(self):
        # special_active_until=5, second=4 → still active → 0.0
        assert shot_cooldown(_ps("scout", special_active_until=5), 4.0) == 0.0

    def test_scout_special_boundary_expired(self):
        # special_active_until=5, second=5 → not strictly > second → 0.5
        assert shot_cooldown(_ps("scout", special_active_until=5), 5.0) == 0.5


class TestCooldownTicks:
    """The one seconds-to-ticks conversion the tick loop, ``plan_action`` and the
    deferred shot queues share (CAL-02 review): a Heavy waits 2 ticks, every
    other role 1, a rapid-fire Scout none."""

    def test_heavy_waits_two_ticks(self):
        assert cooldown_ticks(_ps("heavy"), 30) == 2

    def test_every_other_role_waits_one_tick(self):
        for role in ("commander", "medic", "ammo", "scout"):
            assert cooldown_ticks(_ps(role), 30) == 1, role

    def test_rapid_fire_scout_waits_none(self):
        assert cooldown_ticks(_ps("scout", special_active_until=100), 30) == 0

    def test_it_returns_an_int(self):
        assert type(cooldown_ticks(_ps("heavy"), 0)) is int


# ---------------------------------------------------------------------------
# plan_action's cooldown gate (CAL-02a bug B)
# ---------------------------------------------------------------------------


def _tag_weight(player, now, time_domain):
    """The ``tag_player`` weight ``plan_action`` hands the Action draw at ``now``."""
    from matches.sim_helpers.combat import _ACTION_IDX, plan_action

    enemy = _ps("commander", team_color="blue", tag_id="blue_commander")
    seen = []

    def record(choices, weights):
        seen.append(list(weights))
        return ["hide"]

    with patch("random.choices", side_effect=record):
        plan_action(player, [player, enemy], now, time_domain=time_domain)
    assert len(seen) == 1
    return seen[0][_ACTION_IDX["tag_player"]]


class TestPlanActionCooldownGate:
    """The gate compares ``last_shot_time`` with the cooldown in the caller's unit.

    CAL-02a bug B: in the ticks domain the seconds cooldown is converted to
    whole ticks first (Heavy 2, every other role 1), so a Heavy that fired last
    tick may not plan a Shot, the way the deferred queues already space them.
    """

    def test_heavy_that_fired_last_tick_plans_no_shot(self):
        heavy = _ps("heavy", last_shot_time=29)
        assert _tag_weight(heavy, 30, "ticks") == 0

    def test_heavy_two_ticks_after_its_shot_has_its_full_weight(self):
        rested = _tag_weight(_ps("heavy", last_shot_time=-100), 30, "ticks")
        assert rested > 0
        assert _tag_weight(_ps("heavy", last_shot_time=28), 30, "ticks") == rested

    def test_commander_that_fired_last_tick_keeps_its_weight(self):
        rested = _tag_weight(_ps("commander", last_shot_time=-100), 30, "ticks")
        assert rested > 0
        assert _tag_weight(_ps("commander", last_shot_time=29), 30, "ticks") == rested

    def test_rapid_fire_scout_keeps_its_weight_on_the_same_tick(self):
        scout = _ps("scout", last_shot_time=30, special_active_until=100)
        assert _tag_weight(scout, 30, "ticks") > 0

    def test_seconds_domain_still_blocks_inside_the_cooldown(self):
        # Unchanged: a Heavy 0.6 s after its shot is inside its 1.0 s cooldown,
        # a Commander 0.6 s after its shot is past its 0.5 s one.
        assert _tag_weight(_ps("heavy", last_shot_time=5.0), 5.6, "seconds") == 0
        assert _tag_weight(_ps("commander", last_shot_time=5.0), 5.6, "seconds") > 0


# ---------------------------------------------------------------------------
# choose_tag_target
# ---------------------------------------------------------------------------


class TestChooseTagTarget:
    def _enemy(self, role="scout", **kwargs):
        return _ps(role, team_color="blue", **kwargs)

    def _player(self, role="commander", **kwargs):
        return _ps(role, team_color="red", **kwargs)

    def test_returns_none_when_no_enemies(self):
        player = self._player()
        assert choose_tag_target(player, [player], 0.0) is None

    def test_returns_none_when_no_shots(self):
        player = self._player(final_shots=0)
        enemy = self._enemy()
        assert choose_tag_target(player, [player, enemy], 0.0) is None

    def test_returns_none_when_enemy_has_no_lives(self):
        player = self._player()
        enemy = self._enemy(final_lives=0)
        assert choose_tag_target(player, [player, enemy], 0.0) is None

    def test_returns_enemy_in_same_zone(self):
        player = self._player(current_zone=0)
        enemy = self._enemy(current_zone=0)
        with patch("random.choices", return_value=[enemy]):
            result = choose_tag_target(player, [player, enemy], 0.0)
        assert result is enemy

    def test_excludes_enemy_in_different_zone(self):
        player = self._player(current_zone=0)
        enemy = self._enemy(current_zone=2)
        result = choose_tag_target(player, [player, enemy], 0.0)
        assert result is None

    def test_excludes_recently_tagged_inactive_enemy(self):
        player = self._player()
        # Enemy is in reset window (downed 2 seconds ago → taggable but not active)
        enemy = self._enemy(last_downed_time=0)
        player.last_tagged_id = enemy.tag_id_key
        # Enemy is taggable but last_tagged_id matches → excluded
        result = choose_tag_target(player, [player, enemy], 2.0)
        assert result is None

    def test_allows_recently_tagged_enemy_who_is_now_active(self):
        player = self._player()
        enemy = self._enemy()
        player.last_tagged_id = enemy.tag_id_key
        # Enemy is fully active → eligible despite last_tagged_id match
        with patch("random.choices", return_value=[enemy]):
            result = choose_tag_target(player, [player, enemy], 0.0)
        assert result is enemy

    def test_custom_los_filter_applied(self):
        player = self._player(current_zone=0)
        enemy1 = self._enemy(current_zone=0, role="commander")
        enemy2 = self._enemy(current_zone=0, role="scout")
        captured = []

        def recording_filter(actor, candidates, ctx):
            captured.extend(candidates)
            return candidates

        with patch("random.choices", return_value=[enemy1]):
            choose_tag_target(
                player, [player, enemy1, enemy2], 0.0, los_filter=recording_filter
            )

        assert enemy1 in captured
        assert enemy2 in captured


# ---------------------------------------------------------------------------
# choose_resupply_target
# ---------------------------------------------------------------------------


class TestChooseResupplyTarget:
    def _medic(self, **kwargs):
        return _ps("medic", **kwargs)

    def _ammo(self, **kwargs):
        return _ps(
            "ammo",
            starting_lives=10,
            starting_shots=15,
            final_lives=10,
            final_shots=15,
            **kwargs,
        )

    def _scout(self, team_color="red", **kwargs):
        return _ps("scout", team_color=team_color, **kwargs)

    def test_returns_none_when_no_teammates(self):
        medic = self._medic()
        assert choose_resupply_target(medic, [medic], 0.0) is None

    def test_returns_none_when_all_allies_full(self):
        medic = self._medic()
        scout = self._scout(final_lives=30, starting_lives=30)  # full lives
        assert choose_resupply_target(medic, [medic, scout], 0.0) is None

    def test_returns_teammate_with_deficit(self):
        medic = self._medic()
        scout = self._scout(final_lives=5, starting_lives=30)
        with patch("random.choices", return_value=[scout]):
            result = choose_resupply_target(medic, [medic, scout], 0.0)
        assert result is scout

    def test_excludes_self(self):
        medic = self._medic(final_lives=1, starting_lives=20)
        result = choose_resupply_target(medic, [medic], 0.0)
        assert result is None

    def test_excludes_different_team(self):
        medic = self._medic()
        enemy_scout = self._scout(team_color="blue", final_lives=1)
        result = choose_resupply_target(medic, [medic, enemy_scout], 0.0)
        assert result is None

    def test_excludes_different_zone(self):
        medic = self._medic(current_zone=0)
        scout = self._scout(current_zone=2, final_lives=1)
        result = choose_resupply_target(medic, [medic, scout], 0.0)
        assert result is None

    def test_ammo_resupply_uses_shots_deficit(self):
        ammo = self._ammo(current_zone=0)
        # Scout full on lives but low on shots
        scout = self._scout(
            final_lives=30, starting_lives=30, final_shots=1, current_zone=0
        )
        with patch("random.choices", return_value=[scout]):
            result = choose_resupply_target(ammo, [ammo, scout], 0.0)
        assert result is scout

    def test_ammo_returns_none_when_ally_full_on_shots(self):
        ammo = self._ammo(current_zone=0)
        scout = self._scout(final_shots=60, starting_shots=60, current_zone=0)
        result = choose_resupply_target(ammo, [ammo, scout], 0.0)
        assert result is None

    def test_a_los_filter_replaces_the_zone_rule(self):
        medic = self._medic(current_zone=0)
        far = self._scout(current_zone=2, final_lives=1)
        near = self._scout(current_zone=0, final_lives=1)
        seen_ctx = []

        def only_far(actor, candidates, ctx):
            seen_ctx.append((actor, ctx))
            return [c for c in candidates if c is far]

        ctx = object()
        for _ in range(10):
            result = choose_resupply_target(
                medic, [medic, far, near], 0.0, ctx, los_filter=only_far
            )
            assert result is far
        assert seen_ctx[0] == (medic, ctx)

    def test_a_los_filter_that_keeps_nobody_gives_none(self):
        medic = self._medic(current_zone=0)
        scout = self._scout(current_zone=0, final_lives=1)
        result = choose_resupply_target(
            medic, [medic, scout], 0.0, los_filter=lambda actor, candidates, ctx: []
        )
        assert result is None

    def test_a_los_filter_sees_only_living_resupplyable_teammates(self):
        medic = self._medic(current_zone=0)
        enemy = self._scout(team_color="blue", final_lives=1)
        down = self._scout(final_lives=0)
        offered = []

        def spy(actor, candidates, ctx):
            offered.extend(candidates)
            return candidates

        choose_resupply_target(medic, [medic, enemy, down], 0.0, los_filter=spy)
        assert offered == []


# ---------------------------------------------------------------------------
# choose_zone_change
# ---------------------------------------------------------------------------


class TestChooseZoneChange:
    def _p(self, role, zone=0, **kwargs):
        lives_map = {"commander": 15, "heavy": 10, "scout": 15, "medic": 20, "ammo": 10}
        shots_map = {"commander": 30, "heavy": 20, "scout": 30, "medic": 15, "ammo": 15}
        defaults = dict(
            starting_lives=lives_map[role],
            starting_shots=shots_map[role],
            final_lives=lives_map[role],
            final_shots=shots_map[role],
            current_zone=zone,
        )
        defaults.update(kwargs)
        return _ps(role, **defaults)

    def test_returns_none_when_healthy(self):
        scout = self._p("scout", zone=0)
        assert choose_zone_change(scout, [scout]) is None

    def test_seeks_medic_when_critically_low_lives(self):
        scout = self._p("scout", final_lives=1, zone=0)
        medic = self._p("medic", zone=1)
        result = choose_zone_change(scout, [scout, medic])
        assert result == medic.current_zone

    def test_returns_none_when_medic_in_same_zone(self):
        scout = self._p("scout", final_lives=1, zone=0)
        medic = self._p("medic", zone=0)
        result = choose_zone_change(scout, [scout, medic])
        assert result is None

    def test_medic_does_not_seek_medic_when_low(self):
        medic = self._p("medic", final_lives=1, zone=0)
        other_medic = self._p("medic", zone=1)
        # medic role is excluded from life-seeking
        result = choose_zone_change(medic, [medic, other_medic])
        assert result is None

    def test_seeks_ammo_when_critically_low_shots(self):
        scout = self._p("scout", final_shots=1, zone=0)
        ammo = self._p("ammo", zone=2)
        result = choose_zone_change(scout, [scout, ammo])
        assert result == ammo.current_zone

    def test_lives_critical_takes_priority_over_shots(self):
        # Both lives and shots critical — medic sought, not ammo
        scout = self._p("scout", final_lives=1, final_shots=1, zone=0)
        medic = self._p("medic", zone=1)
        ammo = self._p("ammo", zone=2)
        result = choose_zone_change(scout, [scout, medic, ammo])
        assert result == medic.current_zone

    def test_returns_none_when_no_support_alive(self):
        scout = self._p("scout", final_lives=1, zone=0)
        result = choose_zone_change(scout, [scout])
        assert result is None
