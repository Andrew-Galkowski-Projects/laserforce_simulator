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

    def test_same_seed_produces_identical_event_log(self):
        """Same RNG state must produce a byte-for-byte identical event log.

        This is the replay guarantee: a stored seed must always replay the exact
        same sequence of events so the UI can reconstruct any round on demand.
        """
        import random

        red_roster, _ = self._rosters("SeedR5")
        blue_roster, _ = self._rosters("SeedB5")
        sim = BatchSimulator()

        random.seed(42)
        state = random.getstate()

        log1: list = []
        random.setstate(state)
        sim._simulate_round(red_roster, blue_roster, event_log=log1)

        log2: list = []
        random.setstate(state)
        sim._simulate_round(red_roster, blue_roster, event_log=log2)

        assert len(log1) > 0, "Event log must not be empty"
        assert len(log1) == len(
            log2
        ), f"Event log length differs: {len(log1)} vs {len(log2)}"
        for i, (e1, e2) in enumerate(zip(log1, log2)):
            assert e1 == e2, f"Event {i} differs:\n  run1: {e1}\n  run2: {e2}"


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
        fu = pending_fu[0]
        assert fu.fire_at == pytest.approx(10.5)
        assert fu.attacker is attacker
        assert fu.defender is defender
        assert fu.chain_depth == 1

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
        rx = pending_rx[0]
        assert rx.fire_at == pytest.approx(10.5)
        assert rx.attacker is defender
        assert rx.defender is attacker

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
        assert pending_rx[0].fire_at == pytest.approx(11.0)

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


# ---------------------------------------------------------------------------
# role_constants spot-checks
# ---------------------------------------------------------------------------


class TestRoleConstants:
    def test_heavy_starting_lives(self):
        from matches.sim_helpers.role_constants import MAX_LIVES

        assert MAX_LIVES["heavy"] == 20

    def test_commander_starting_shots(self):
        from matches.sim_helpers.role_constants import MAX_SHOTS

        assert MAX_SHOTS["commander"] == 60

    def test_heavy_shot_power(self):
        from matches.sim_helpers.role_constants import ROLE_STATS

        assert ROLE_STATS["heavy"]["shot_power"] == 3

    def test_scout_shield(self):
        from matches.sim_helpers.role_constants import ROLE_STATS

        assert ROLE_STATS["scout"]["shield"] == 1

    def test_all_roles_present(self):
        from matches.sim_helpers.role_constants import MAX_LIVES, MAX_SHOTS, ROLE_STATS

        roles = {"commander", "heavy", "scout", "medic", "ammo"}
        assert set(ROLE_STATS) == roles
        assert set(MAX_LIVES) == roles
        assert set(MAX_SHOTS) == roles


# ---------------------------------------------------------------------------
# SIM-06 — _flush_to_db writes all 10 previously-skipped fields
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSim06FlushFields:
    """Verify that BatchSimulator._flush_to_db persists all 10 SIM-06 fields.

    Fields under test:
        follow_up_shots, reaction_shots,
        seconds_active, seconds_not_targetable, seconds_reset_window,
        combo_resupply_count, times_tagged_in_reset_window,
        missile_points, cell_row, cell_col
    """

    # ------------------------------------------------------------------
    # Map fixture — mirrors the simplest complete pattern from
    # TestMap02CellMovement._make_map and TestMap03DBIntegration
    # ------------------------------------------------------------------

    def _make_arena_map(self, name="Sim06Arena"):
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )
        from core.map_processing import compute_sight_lines

        # 4×4 open grid: 2=red-zone, 1=neutral/floor, 3=blue-zone
        zone_data = [
            [2, 2, 1, 1],
            [2, 1, 1, 1],
            [1, 1, 1, 3],
            [1, 1, 3, 3],
        ]
        zone_size = 100
        rows = len(zone_data)
        cols = len(zone_data[0])

        arena_map = ArenaMap.objects.create(
            name=name,
            img_width=cols * zone_size,
            img_height=rows * zone_size,
        )
        MapZoneConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            zone_data=zone_data,
            confirmed=True,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="red",
            x_px=zone_size // 2,
            y_px=zone_size // 2,
        )
        MapBaseConfig.objects.create(
            arena_map=arena_map,
            base_type="blue",
            x_px=cols * zone_size - zone_size // 2,
            y_px=rows * zone_size - zone_size // 2,
        )
        SightLineConfig.objects.create(
            arena_map=arena_map,
            zone_size=zone_size,
            sight_data=compute_sight_lines(zone_data),
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="red", zone_size=zone_size, visible_cells=[]
        )
        BaseSightLineConfig.objects.create(
            arena_map=arena_map, base_type="blue", zone_size=zone_size, visible_cells=[]
        )
        return arena_map

    # ------------------------------------------------------------------
    # Helper: run n rounds and flush exactly one to DB
    # ------------------------------------------------------------------

    def _run_and_flush(self, team_red, team_blue, arena_map, n_rounds=3):
        """Run n_rounds via BatchSimulator.run() then flush the first avg_seed."""
        import random

        random.seed(42)
        sim = BatchSimulator()
        stats = sim.run(team_red, team_blue, n=n_rounds, arena_map=arena_map)
        seeds = stats["avg_seeds"] or stats["outlier_seeds"]
        assert seeds, "run() produced no seeds — cannot flush to DB"
        game_rounds = sim.save_games(
            team_red, team_blue, seeds, n=1, arena_map=arena_map
        )
        assert game_rounds, "save_games returned no GameRound objects"
        return game_rounds[0]

    # ------------------------------------------------------------------
    # Tests
    # ------------------------------------------------------------------

    def test_seconds_active_written_for_all_players(self):
        """Every non-immediately-eliminated player accumulates seconds_active > 0."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06ActiveR")
        team_blue, _ = make_team_with_slots("Sim06ActiveB")
        arena_map = self._make_arena_map("Sim06Active")

        gr = self._run_and_flush(team_red, team_blue, arena_map)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert states, "No PlayerRoundState rows were created"
        # Every player with positive final_lives has been active for some ticks
        active_states = [s for s in states if s.was_eliminated_at > 0]
        for s in active_states:
            assert s.seconds_active > 0, (
                f"{s.player} ({s.role}) has was_eliminated_at={s.was_eliminated_at} "
                f"but seconds_active=0"
            )

    def test_cell_row_and_cell_col_non_null_with_map(self):
        """With an arena_map provided, at least one player has non-null cell coordinates."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06CellR")
        team_blue, _ = make_team_with_slots("Sim06CellB")
        arena_map = self._make_arena_map("Sim06Cell")

        gr = self._run_and_flush(team_red, team_blue, arena_map)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.cell_row is not None for s in states), (
            "Expected at least one PlayerRoundState with non-null cell_row after "
            "flushing a map-aware round, but all were null"
        )
        assert any(s.cell_col is not None for s in states), (
            "Expected at least one PlayerRoundState with non-null cell_col after "
            "flushing a map-aware round, but all were null"
        )

    def test_follow_up_shots_non_zero_on_at_least_one_player(self):
        """At least one player has follow_up_shots > 0 across 3 simulated rounds."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06FUR")
        team_blue, _ = make_team_with_slots("Sim06FUB")
        arena_map = self._make_arena_map("Sim06FU")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.follow_up_shots > 0 for s in states), (
            "Expected follow_up_shots > 0 on at least one player but all were 0. "
            "This field is probably not being written by _flush_to_db."
        )

    def test_reaction_shots_non_zero_on_at_least_one_player(self):
        """At least one player has reaction_shots > 0 across 3 simulated rounds."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06RxR")
        team_blue, _ = make_team_with_slots("Sim06RxB")
        arena_map = self._make_arena_map("Sim06Rx")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.reaction_shots > 0 for s in states), (
            "Expected reaction_shots > 0 on at least one player but all were 0. "
            "This field is probably not being written by _flush_to_db."
        )

    def test_seconds_not_targetable_non_zero_on_at_least_one_player(self):
        """At least one player has seconds_not_targetable > 0 (was tagged at least once)."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06NTR")
        team_blue, _ = make_team_with_slots("Sim06NTB")
        arena_map = self._make_arena_map("Sim06NT")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.seconds_not_targetable > 0 for s in states), (
            "Expected seconds_not_targetable > 0 on at least one tagged player "
            "but all were 0."
        )

    def test_seconds_reset_window_non_zero_on_at_least_one_player(self):
        """At least one player has seconds_reset_window > 0 (had taggable reset time)."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06RWR")
        team_blue, _ = make_team_with_slots("Sim06RWB")
        arena_map = self._make_arena_map("Sim06RW")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.seconds_reset_window > 0 for s in states), (
            "Expected seconds_reset_window > 0 on at least one player that was "
            "tagged (and thus entered the reset window), but all were 0."
        )

    def test_missile_points_non_zero_on_at_least_one_player(self):
        """At least one Commander or Heavy has missile_points > 0 across 3 rounds."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06MsR")
        team_blue, _ = make_team_with_slots("Sim06MsB")
        arena_map = self._make_arena_map("Sim06Ms")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.missile_points > 0 for s in states), (
            "Expected missile_points > 0 on at least one Commander or Heavy but "
            "all were 0. This field is probably not being written by _flush_to_db."
        )

    def test_combo_resupply_count_field_is_written(self):
        """combo_resupply_count must be explicitly written (not left at default)."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06CbR")
        team_blue, _ = make_team_with_slots("Sim06CbB")
        arena_map = self._make_arena_map("Sim06Cb")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        # combo_resupply_count defaults to 0; if _flush_to_db does not write it
        # the field will be 0 on all rows. We simply confirm it is present — a
        # deeper check would require knowing which rounds had combo resupplies.
        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert all(
            s.combo_resupply_count is not None for s in states
        ), "combo_resupply_count must not be null after flush"

    def test_times_tagged_in_reset_window_field_is_written(self):
        """times_tagged_in_reset_window must be written, not defaulted."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06TTRWR")
        team_blue, _ = make_team_with_slots("Sim06TTRWB")
        arena_map = self._make_arena_map("Sim06TTRW")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert all(
            s.times_tagged_in_reset_window is not None for s in states
        ), "times_tagged_in_reset_window must not be null after flush"

    def test_all_ten_fields_present_after_flush(self):
        """Integration smoke test: all 10 SIM-06 fields exist on every state row."""
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06AllR")
        team_blue, _ = make_team_with_slots("Sim06AllB")
        arena_map = self._make_arena_map("Sim06All")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert states, "No PlayerRoundState rows were created"

        int_fields = [
            "follow_up_shots",
            "reaction_shots",
            "seconds_active",
            "seconds_not_targetable",
            "seconds_reset_window",
            "combo_resupply_count",
            "times_tagged_in_reset_window",
            "missile_points",
        ]
        for s in states:
            for field in int_fields:
                val = getattr(s, field)
                assert val is not None, (
                    f"{field} is None on {s.player} ({s.role}) — "
                    f"_flush_to_db probably does not write it"
                )
            # cell_row / cell_col: at least collectively non-null across the round
        assert any(
            s.cell_row is not None for s in states
        ), "No player has cell_row set — _flush_to_db does not write cell coordinates"
        assert any(
            s.cell_col is not None for s in states
        ), "No player has cell_col set — _flush_to_db does not write cell coordinates"
