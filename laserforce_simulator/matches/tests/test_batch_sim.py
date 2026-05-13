"""
BatchSimulator tests: seed reproducibility, shot cooldown, follow-ups, reactions.
"""

import pytest
from unittest.mock import patch

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Shared PlayerState factory
# ---------------------------------------------------------------------------


def _make_ps(role, team_color="red", **kwargs):
    """Create a PlayerState with sensible defaults for unit tests."""
    from matches.sim_helpers.player_state import PlayerState

    tag_id = kwargs.pop("tag_id", f"{team_color}_{role}")
    defaults = dict(
        tag_id=tag_id,
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=0,
        player_awareness=50,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


# ---------------------------------------------------------------------------
# Seed reproducibility
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestBatchSimulatorSeedReproducibility:
    """Verify that capturing/restoring RNG state reproduces identical rounds."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    def test_same_state_produces_identical_round(self):
        """Restoring the same random.getstate() before _simulate_round gives the same scores."""
        import random

        red_roster, _ = self._rosters("SeedR1")
        blue_roster, _ = self._rosters("SeedB1")
        sim = BatchSimulator()

        random.seed(42)
        state = random.getstate()

        random.setstate(state)
        r1, _, _ = sim._simulate_round(red_roster, blue_roster)

        random.setstate(state)
        r2, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert r1["red_points"] == r2["red_points"]
        assert r1["blue_points"] == r2["blue_points"]
        assert r1["red_survivors"] == r2["red_survivors"]
        assert r1["blue_survivors"] == r2["blue_survivors"]

    def test_different_seeds_produce_different_outcomes(self):
        """Sanity check: across many seeds at least some rounds produce different scores."""
        import random

        red_roster, _ = self._rosters("SeedR2")
        blue_roster, _ = self._rosters("SeedB2")
        sim = BatchSimulator()

        outcomes = set()
        for seed_val in range(20):
            random.seed(seed_val)
            r, _, _ = sim._simulate_round(red_roster, blue_roster)
            outcomes.add((r["red_points"], r["blue_points"]))

        assert len(outcomes) > 1, "Expected varied results across different seeds"

    def test_serialized_seed_reproduces_round(self):
        """Seeds round-trip through the JSON-serializable format used by views.py."""
        import random

        def serialize(state):
            v, internal, gauss = state
            return [v, list(internal), gauss]

        def deserialize(data):
            v, internal, gauss = data
            return (v, tuple(internal), gauss)

        red_roster, _ = self._rosters("SeedR3")
        blue_roster, _ = self._rosters("SeedB3")
        sim = BatchSimulator()

        random.seed(7)
        state = random.getstate()

        random.setstate(state)
        r1, _, _ = sim._simulate_round(red_roster, blue_roster)

        random.setstate(deserialize(serialize(state)))
        r2, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert r1["red_points"] == r2["red_points"]
        assert r1["blue_points"] == r2["blue_points"]

    def test_mid_run_seed_replays_specific_round(self):
        """State captured after several rounds replays only that specific round."""
        import random

        red_roster, _ = self._rosters("SeedR4")
        blue_roster, _ = self._rosters("SeedB4")
        sim = BatchSimulator()

        random.seed(99)
        for _ in range(3):
            sim._simulate_round(red_roster, blue_roster)

        state = random.getstate()
        round4, _, _ = sim._simulate_round(red_roster, blue_roster)

        random.setstate(state)
        replay, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert round4["red_points"] == replay["red_points"]
        assert round4["blue_points"] == replay["blue_points"]
        assert round4["red_survivors"] == replay["red_survivors"]
        assert round4["blue_survivors"] == replay["blue_survivors"]


# ---------------------------------------------------------------------------
# Shot cooldown and tag-weight suppression
# ---------------------------------------------------------------------------


class TestBatchSimulatorShotCooldown:
    """Per-tick tag-weight suppression in _plan_action (cooldown values tested in test_mechanics.py)."""

    def _sim(self):
        return BatchSimulator()

    def test_plan_action_zeroes_tag_weight_when_fired_too_recently(self):
        sim = self._sim()
        p = _make_ps("commander", last_shot_time=5.0, final_shots=20)
        captured = []

        def capture(ch, wt):
            captured.append(list(wt))
            return ["change_zone"]

        with patch("random.choices", side_effect=capture):
            sim._plan_action(p, [p], 5.3)

        assert len(captured) == 1
        assert (
            captured[0][0] == 0
        ), "tag_player weight must be zeroed when cooldown has not elapsed"

    def test_plan_action_allows_tag_after_cooldown_elapsed(self):
        sim = self._sim()
        p = _make_ps("commander", last_shot_time=5.0, final_shots=20)
        captured = []

        def capture(ch, wt):
            captured.append(list(wt))
            return ["change_zone"]

        with patch("random.choices", side_effect=capture):
            sim._plan_action(p, [p], 5.6)

        assert len(captured) == 1
        assert (
            captured[0][0] > 0
        ), "tag_player weight must be non-zero after cooldown has elapsed"

    def test_last_shot_time_updated_on_hit(self):
        sim = self._sim()
        attacker = _make_ps("commander", team_color="red", final_shots=20)
        defender = _make_ps("scout", team_color="blue")
        defender.shields = 3  # commander shot_power=2; 3-2=1, not downed
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}], second=7.0
            )
        assert attacker.last_shot_time == 7.0

    def test_last_shot_time_updated_on_miss(self):
        sim = self._sim()
        attacker = _make_ps("commander", team_color="red", final_shots=20)
        defender = _make_ps("scout", team_color="blue")
        defender.survival = 100
        with patch("random.randint", return_value=99):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}], second=7.0
            )
        assert attacker.last_shot_time == 7.0


# ---------------------------------------------------------------------------
# Follow-up shots
# ---------------------------------------------------------------------------


class TestBatchSimulatorFollowUps:
    """Follow-up shot scheduling in _resolve_tag_attempts."""

    def test_non_downed_hit_schedules_follow_up(self):
        sim = BatchSimulator()
        attacker = _make_ps(
            "scout", team_color="red", final_shots=20, player_awareness=0
        )
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3

        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_followups=pending_fu,
            )

        assert len(pending_fu) == 1
        fire_at, fu_atk, fu_def, chain = pending_fu[0]
        assert fire_at == pytest.approx(10.5)
        assert fu_atk is attacker
        assert fu_def is defender
        assert chain == 1

    def test_downed_hit_no_follow_up(self):
        sim = BatchSimulator()
        attacker = _make_ps("heavy", team_color="red", final_shots=20)
        defender = _make_ps("medic", team_color="blue", player_awareness=0)
        defender.shields = 1

        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_followups=pending_fu,
            )

        assert len(pending_fu) == 0

    def test_rapid_fire_scout_follow_up_fires_immediately(self):
        sim = BatchSimulator()
        attacker = _make_ps(
            "scout",
            team_color="red",
            special_active_until=20,
            final_shots=20,
            player_awareness=0,
        )
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3

        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_followups=pending_fu,
            )

        assert len(pending_fu) == 0
        assert attacker.follow_up_shots == 2

    def test_high_defender_awareness_suppresses_follow_up(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps("commander", team_color="blue", player_awareness=100)
        defender.shields = 3

        pending_fu = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_followups=pending_fu,
            )

        assert len(pending_fu) == 0

    def test_initial_hit_uses_one_shot_follow_up_not_yet(self):
        sim = BatchSimulator()
        attacker = _make_ps(
            "scout", team_color="red", final_shots=10, player_awareness=0
        )
        defender = _make_ps("commander", team_color="blue", player_awareness=0)
        defender.shields = 3

        shots_before = attacker.final_shots
        pending_fu = []
        with patch("random.randint", return_value=1):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_followups=pending_fu,
            )

        assert attacker.final_shots == shots_before - 1
        assert len(pending_fu) == 1


# ---------------------------------------------------------------------------
# Reactions
# ---------------------------------------------------------------------------


class TestBatchSimulatorReactions:
    """Reaction shot scheduling in _resolve_tag_attempts."""

    def test_reaction_scheduled_on_hit_when_awareness_passes(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps(
            "commander", team_color="blue", player_awareness=100, final_shots=20
        )
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
        fire_at, r_atk, r_def = pending_rx[0]
        assert fire_at == pytest.approx(10.5)
        assert r_atk is defender
        assert r_def is attacker

    def test_no_reaction_when_awareness_roll_fails(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps(
            "commander", team_color="blue", player_awareness=0, final_shots=20
        )
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 0

    def test_heavy_defender_reaction_has_1s_delay(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps(
            "heavy", team_color="blue", player_awareness=100, final_shots=20
        )
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
        assert pending_rx[0][0] == pytest.approx(11.0)

    def test_inactive_defender_does_not_react(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps(
            "commander",
            team_color="blue",
            player_awareness=100,
            final_shots=20,
            last_downed_time=5,
        )
        defender.shields = 3

        pending_rx = []
        with patch("random.randint", return_value=50):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 0

    def test_reaction_scheduled_on_miss_when_awareness_passes(self):
        sim = BatchSimulator()
        attacker = _make_ps("scout", team_color="red", final_shots=20)
        defender = _make_ps(
            "commander", team_color="blue", player_awareness=100, final_shots=20
        )
        defender.shields = 3
        defender.survival = 100

        pending_rx = []
        with patch("random.randint", return_value=99):
            sim._resolve_tag_attempts(
                [{"attacker": attacker, "defender": defender}],
                second=10.0,
                pending_reactions=pending_rx,
            )

        assert len(pending_rx) == 1
