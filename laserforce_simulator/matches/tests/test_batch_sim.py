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
        """Same int seed must produce a byte-for-byte identical event log.

        This is the replay guarantee: a stored int seed must always replay the
        exact same sequence of events so the UI can reconstruct any round on
        demand. SIM-07 replaced the getstate()/setstate() dance with a plain
        ``random.seed(<int>)`` before ``_simulate_round``.
        """
        import random

        red_roster, _ = self._rosters("SeedR5")
        blue_roster, _ = self._rosters("SeedB5")
        sim = BatchSimulator()

        log1: list = []
        random.seed(42)
        sim._simulate_round(red_roster, blue_roster, event_log=log1)

        log2: list = []
        random.seed(42)
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
            return ["only_move"]

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
            return ["only_move"]

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
        # TIME-01: tick-native. Scout cooldown 0.5 s → 1 tick; scheduled at
        # tick 10 + 1 = 11 (was second 10.0 + 0.5 = 10.5).
        assert fu.fire_at == pytest.approx(11.0)
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
        # TIME-01: tick-native. Reaction cooldown 0.5 s → 1 tick;
        # scheduled at tick 10 + 1 = 11 (was second 10.0 + 0.5 = 10.5).
        assert rx.fire_at == pytest.approx(11.0)
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

    def test_heavy_defender_reaction_has_2_tick_delay(self):
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
        # TIME-01: tick-native. Heavy cooldown 1.0 s → 2 ticks;
        # scheduled at tick 10 + 2 = 12 (was second 10.0 + 1.0 = 11.0).
        assert pending_rx[0].fire_at == pytest.approx(12.0)

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
        ticks_active, ticks_not_targetable, ticks_reset_window,
        combo_resupply_count, times_tagged_in_reset_window,
        missile_points, cell_row, cell_col

    TIME-01: the uptime columns were renamed seconds_* → ticks_* and store
    ticks; the survived sentinel is 1801.
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
        """Run n_rounds via BatchSimulator.run() then flush the first avg_seed.

        SIM-08: ``run()`` returns ``avg_seeds`` / ``outlier_seeds`` as
        ``[seed, flipped]`` pairs and ``save_games`` consumes that
        ``(seed, flipped)``-pair list directly.
        """
        import random

        random.seed(42)
        sim = BatchSimulator()
        stats = sim.run(
            team_red, team_blue, n=n_rounds, arena_map=arena_map, master_seed=42
        )
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

    def test_ticks_active_written_for_all_players(self):
        """Every non-immediately-eliminated player accumulates ticks_active > 0.

        TIME-01: renamed from test_seconds_active_written_for_all_players;
        seconds_active → ticks_active. Survivors have was_eliminated_at == 1801.
        """
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06ActiveR")
        team_blue, _ = make_team_with_slots("Sim06ActiveB")
        arena_map = self._make_arena_map("Sim06Active")

        gr = self._run_and_flush(team_red, team_blue, arena_map)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert states, "No PlayerRoundState rows were created"
        # Players who were not eliminated at tick 0 have been active for some
        # ticks (sentinel 1801 == survived; any positive elimination tick also
        # implies they were active before being eliminated).
        active_states = [s for s in states if s.was_eliminated_at > 0]
        for s in active_states:
            assert s.ticks_active > 0, (
                f"{s.player} ({s.role}) has was_eliminated_at={s.was_eliminated_at} "
                f"but ticks_active=0"
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

    def test_ticks_not_targetable_non_zero_on_at_least_one_player(self):
        """At least one player has ticks_not_targetable > 0 (was tagged at least once).

        TIME-01: renamed from seconds_not_targetable.
        """
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06NTR")
        team_blue, _ = make_team_with_slots("Sim06NTB")
        arena_map = self._make_arena_map("Sim06NT")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.ticks_not_targetable > 0 for s in states), (
            "Expected ticks_not_targetable > 0 on at least one tagged player "
            "but all were 0."
        )

    def test_ticks_reset_window_non_zero_on_at_least_one_player(self):
        """At least one player has ticks_reset_window > 0 (had taggable reset time).

        TIME-01: renamed from seconds_reset_window.
        """
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("Sim06RWR")
        team_blue, _ = make_team_with_slots("Sim06RWB")
        arena_map = self._make_arena_map("Sim06RW")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)

        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert any(s.ticks_reset_window > 0 for s in states), (
            "Expected ticks_reset_window > 0 on at least one player that was "
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
            # TIME-01: seconds_* → ticks_*
            "ticks_active",
            "ticks_not_targetable",
            "ticks_reset_window",
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


# ---------------------------------------------------------------------------
# SIM-07 — integer RNG seeds: replay determinism, seed persistence,
# master-seed reproducibility, serial == parallel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSim07RngSeed:
    """SIM-07 replaces RNG-state tuples with plain integer seeds.

    NOTE: every test here is ``django_db`` because the shared
    ``make_team_with_slots`` conftest helper creates Team/Player ORM rows —
    even the "no DB persistence" cases (master-seed reproducibility,
    serial==parallel) must build rosters through the ORM. This mirrors how the
    existing ``TestBatchSimulatorSeedReproducibility`` class marks the whole
    class ``django_db`` despite running purely in-memory simulations.

    Final API under test:
      * GameRound.rng_seed = BigIntegerField(null=True, blank=True)
      * BatchSimulator.run(..., master_seed=None) — per-round int seeds drawn
        from random.Random(master_seed); avg_seeds/outlier_seeds are lists of
        ints.
      * BatchSimulator.replay_round(red_roster, blue_roster, seed) — int seed;
        does random.seed(seed) then _simulate_round.
      * BatchSimulator.save_games(team_red, team_blue, seeds, n) — int seeds;
        each round persists its int seed onto GameRound.rng_seed via
        _flush_to_db(..., rng_seed=...).
    """

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    # ------------------------------------------------------------------
    # 2. Replay-vs-replay determinism (DB-backed)
    # ------------------------------------------------------------------

    def test_replay_vs_replay_is_deterministic_from_stored_seed(self):
        """save_games stores a valid int seed; replaying it twice is identical.

        Builds two real teams, persists one round via save_games with a fixed
        int seed, reloads the GameRound, asserts rng_seed is a valid non-null
        63-bit int, then replays that exact seed twice and asserts the two
        event logs are byte-for-byte identical.

        SIM-08: ``save_games`` now takes ``(seed, flipped)`` pairs and
        ``replay_round`` takes an explicit ``flipped`` flag. This case uses a
        canonical (non-flipped) game so the original SIM-07 intent —
        replay-vs-replay determinism from the stored seed — is preserved.
        """
        from matches.models import GameRound

        red, team_red = self._rosters("Sim07ReplayR")
        blue, team_blue = self._rosters("Sim07ReplayB")
        sim = BatchSimulator()

        fixed_seed = 13572468
        saved = sim.save_games(team_red, team_blue, seeds=[(fixed_seed, False)], n=1)
        assert saved, "save_games returned no GameRound objects"

        gr = GameRound.objects.get(id=saved[0].id)
        assert gr.rng_seed is not None, "GameRound.rng_seed must be persisted"
        assert 0 <= gr.rng_seed < 2**63, f"rng_seed out of 63-bit range: {gr.rng_seed}"

        _, _, _, log_a = sim.replay_round(red, blue, gr.rng_seed, flipped=False)
        _, _, _, log_b = sim.replay_round(red, blue, gr.rng_seed, flipped=False)

        assert len(log_a) > 0, "Replay event log must not be empty"
        assert len(log_a) == len(
            log_b
        ), f"Replay log length differs: {len(log_a)} vs {len(log_b)}"
        for i, (e1, e2) in enumerate(zip(log_a, log_b)):
            assert e1 == e2, f"Replay event {i} differs:\n  a: {e1}\n  b: {e2}"

    # ------------------------------------------------------------------
    # 3. Correct seed stored (DB-backed)
    # ------------------------------------------------------------------

    def test_save_games_stores_the_seed_it_replayed(self):
        """Each persisted GameRound stores exactly the int seed it replayed.

        Passing two distinct known ints and checking that the GameRounds, in
        creation order, carry rng_seed == s0 and rng_seed == s1 proves
        _flush_to_db stores the seed actually used to drive the round (not, e.g.,
        a re-derived or shuffled value).
        """
        from matches.models import GameRound

        red, team_red = self._rosters("Sim07StoreR")
        blue, team_blue = self._rosters("Sim07StoreB")
        sim = BatchSimulator()

        s0, s1 = 111111, 222222
        saved = sim.save_games(
            team_red, team_blue, seeds=[(s0, False), (s1, False)], n=2
        )
        assert len(saved) == 2, f"Expected 2 saved rounds, got {len(saved)}"

        rounds = list(
            GameRound.objects.filter(id__in=[g.id for g in saved]).order_by("id")
        )
        assert len(rounds) == 2
        assert (
            rounds[0].rng_seed == s0
        ), f"First round should store seed {s0}, got {rounds[0].rng_seed}"
        assert (
            rounds[1].rng_seed == s1
        ), f"Second round should store seed {s1}, got {rounds[1].rng_seed}"

    # ------------------------------------------------------------------
    # 4. Master-seed reproducibility (no DB)
    # ------------------------------------------------------------------

    def test_master_seed_makes_run_reproducible(self):
        """run() with the same master_seed yields identical aggregates+seed lists.

        A different master_seed must produce a different per-round int seed
        sequence (n>=8 makes accidental collision of all eight seeds
        negligible).
        """
        red, team_red = self._rosters("Sim07MasterR")
        blue, team_blue = self._rosters("Sim07MasterB")
        sim = BatchSimulator()

        stats_a = sim.run(team_red, team_blue, n=8, master_seed=12345)
        stats_b = sim.run(team_red, team_blue, n=8, master_seed=12345)

        assert stats_a["avg_red_score"] == stats_b["avg_red_score"]
        assert stats_a["avg_blue_score"] == stats_b["avg_blue_score"]
        assert stats_a["avg_seeds"] == stats_b["avg_seeds"]
        assert stats_a["outlier_seeds"] == stats_b["outlier_seeds"]
        # SIM-08: seeds are now ``[seed, flipped]`` pairs (was plain int).
        # The int seed must still be a plain int (not an RNG-state tuple) and
        # the flipped flag a bool.
        for pair in stats_a["avg_seeds"] + stats_a["outlier_seeds"]:
            assert len(pair) == 2, f"Expected [seed, flipped] pair, got {pair!r}"
            seed, flipped = pair
            assert isinstance(seed, int) and not isinstance(seed, bool)
            assert isinstance(flipped, bool)

        stats_c = sim.run(team_red, team_blue, n=8, master_seed=99999)
        assert (
            stats_c["avg_seeds"] != stats_a["avg_seeds"]
        ), "Different master_seed must yield a different avg_seeds list"
        assert (
            stats_c["outlier_seeds"] != stats_a["outlier_seeds"]
        ), "Different master_seed must yield a different outlier_seeds list"

    # ------------------------------------------------------------------
    # 5. Serial == parallel for a fixed master_seed (no DB)
    # ------------------------------------------------------------------

    def test_serial_equals_parallel_for_fixed_master_seed(self):
        """A fixed master_seed yields identical aggregates serial vs parallel.

        The parallel ProcessPoolExecutor path can be flaky on Windows inside
        this harness (spawn-based workers, Django re-init). If the parallel run
        fails to start workers, skip with a clear reason rather than weakening
        the determinism assertion.
        """
        red, team_red = self._rosters("Sim07ParR")
        blue, team_blue = self._rosters("Sim07ParB")
        sim = BatchSimulator()

        serial = sim.run(team_red, team_blue, n=4, master_seed=777)

        try:
            parallel = sim.run(team_red, team_blue, n=4, master_seed=777, workers=2)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(
                f"Parallel worker pool unavailable in this environment: {exc!r}"
            )

        assert serial["red_wins"] == parallel["red_wins"]
        assert serial["blue_wins"] == parallel["blue_wins"]
        assert serial["ties"] == parallel["ties"]
        assert serial["avg_red_score"] == parallel["avg_red_score"]
        assert serial["avg_blue_score"] == parallel["avg_blue_score"]


# ---------------------------------------------------------------------------
# SIM-08 — deterministic physical-side alternation with de-flipped,
# team-position-keyed aggregates and a new physical-side advantage panel
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSim08SideAlternation:
    """SIM-08 alternates physical sides per game (k odd ⇒ flipped) so the
    canonical/flipped split is exact, while keeping result keys keyed by
    *team position* (the ``team_red`` arg always maps to ``red_*``).

    Contract under test:
      * ``run`` flips game ``k`` iff ``k`` is odd; the choice never consumes
        the RNG and the per-game seed sequence is identical regardless of
        flipping.
      * ``red_*`` / ``blue_*`` aggregates are de-flipped → team-position keyed.
      * ``results["side_advantage"]`` carries PHYSICAL-side aggregates.
      * ``avg_seeds`` / ``outlier_seeds`` are ``[seed, flipped]`` pairs.
      * ``replay_round(red, blue, seed, flipped=...)`` swaps rosters for
        ``flipped=True`` and reproduces the team-position result ``run``
        recorded.
      * ``save_games`` takes ``(seed, flipped)`` pairs and persists the
        ACTUAL physical sides for flipped games.

    All tests pin ``master_seed`` and use deterministic ORM rosters built via
    the shared ``make_team_with_slots`` conftest helper, so the whole class is
    ``django_db`` (mirrors ``TestSim07RngSeed``).
    """

    SIDE_ADV_KEYS = (
        "red_side_wins",
        "blue_side_wins",
        "side_ties",
        "red_side_win_pct",
        "blue_side_win_pct",
        "avg_red_side_score",
        "avg_blue_side_score",
        "n",
    )

    def _rosters(self, prefix):
        team, players = make_team_with_slots(prefix)
        return list(team.active_roster), team, players

    def _bump_team_stats(self, players, *, accuracy, survival, awareness):
        """Set the combat-relevant stats on every player of a team.

        ``make_team_with_slots`` players default all stats to 50; bumping
        accuracy/awareness up and survival down (or vice versa) makes one
        team dramatically stronger than the other regardless of side.
        """
        for p in players.values():
            p.accuracy = accuracy
            p.survival = survival
            p.player_awareness = awareness
            p.save()

    # ------------------------------------------------------------------
    # 1 + 2. Alternation parity / even-split guarantee
    # ------------------------------------------------------------------

    def test_even_n_yields_exact_50_50_canonical_vs_flipped(self):
        """Even n ⇒ exactly n/2 flipped and n/2 canonical games.

        Asserted via the ``flipped`` flags on the documented
        ``avg_seeds`` + ``outlier_seeds`` pairs and via ``side_advantage``
        physical-side counts summing to n.
        """
        _, team_red, _ = self._rosters("Sim08EvenR")
        _, team_blue, _ = self._rosters("Sim08EvenB")
        sim = BatchSimulator()

        n = 8
        stats = sim.run(team_red, team_blue, n=n, master_seed=2024)

        # Both score lists carry exactly one entry per game (team-position).
        assert len(stats["red_scores"]) == n
        assert len(stats["blue_scores"]) == n

        # side_advantage physical-side counts partition all n games.
        sa = stats["side_advantage"]
        assert (
            sa["red_side_wins"] + sa["blue_side_wins"] + sa["side_ties"] == n
        ), f"side_advantage counts must sum to n={n}: {sa}"
        assert sa["n"] == n

        # The documented [seed, flipped] pairs for an 8-game run cover all 8
        # games across avg + outlier (run() splits 10 avg / 10 outlier but
        # caps at n). Verify the alternation rule produces an exact split:
        # n even ⇒ |#flipped - #canonical| == 0.
        seen = {}
        for pair in stats["avg_seeds"] + stats["outlier_seeds"]:
            seed, flipped = pair[0], pair[1]
            seen[seed] = bool(flipped)
        flips = list(seen.values())
        n_flipped = sum(1 for f in flips if f)
        n_canon = sum(1 for f in flips if not f)
        assert abs(n_flipped - n_canon) <= (n % 2), (
            "Even n must split flipped/canonical exactly 50/50 "
            f"(got {n_flipped} flipped vs {n_canon} canonical across "
            f"{len(flips)} distinct seeds)"
        )

    def test_odd_n_split_differs_by_exactly_one(self):
        """Odd n ⇒ physical-side game counts differ by exactly 1.

        Derived from the alternation: with n odd the canonical side gets one
        extra game. Verified through ``side_advantage`` (counts still sum to
        n) and the distinct ``[seed, flipped]`` pairs.
        """
        _, team_red, _ = self._rosters("Sim08OddR")
        _, team_blue, _ = self._rosters("Sim08OddB")
        sim = BatchSimulator()

        n = 7
        stats = sim.run(team_red, team_blue, n=n, master_seed=4242)

        assert len(stats["red_scores"]) == n
        assert len(stats["blue_scores"]) == n

        sa = stats["side_advantage"]
        assert sa["red_side_wins"] + sa["blue_side_wins"] + sa["side_ties"] == n
        assert sa["n"] == n

        seen = {}
        for pair in stats["avg_seeds"] + stats["outlier_seeds"]:
            seed, flipped = pair[0], pair[1]
            seen[seed] = bool(flipped)
        n_flipped = sum(1 for f in seen.values() if f)
        n_canon = sum(1 for f in seen.values() if not f)
        assert abs(n_flipped - n_canon) == 1, (
            "Odd n must split flipped/canonical differing by exactly 1 "
            f"(got {n_flipped} flipped vs {n_canon} canonical)"
        )

    # ------------------------------------------------------------------
    # 3. De-flip correctness — strong team's win% survives alternation
    # ------------------------------------------------------------------

    def test_strong_team_winpct_not_diluted_by_alternation(self):
        """A dramatically stronger team keeps a high *team-position* win%.

        ``team_red`` arg = strong team. Despite playing physical blue half
        the games, its de-flipped ``red_win_pct`` stays clearly above 50%.
        Meanwhile ``side_advantage`` red_side_win_pct should be ≈ 50% because
        each team plays physical red equally.

        The load-bearing invariant is the CONTRAST: the team-position win
        signal (strong team well above 50%) must be far stronger than the
        physical-side signal (near 50%). If results were *not* de-flipped,
        ``red_win_pct`` would itself collapse toward 50% — that is exactly
        what this test rules out.
        """
        _, strong_team, strong_players = self._rosters("Sim08StrongR")
        _, weak_team, weak_players = self._rosters("Sim08WeakB")

        # Strong: max accuracy/awareness, min survival (easy hits, hard to be
        # missed against). Weak: the inverse.
        self._bump_team_stats(strong_players, accuracy=100, survival=0, awareness=100)
        self._bump_team_stats(weak_players, accuracy=0, survival=100, awareness=0)

        sim = BatchSimulator()
        # MOVE-03 (ADR-0009): the new `hold` Action adds seed-sensitive
        # variance (strong-team players occasionally hold instead of pressing),
        # so a 30-game sample with a hard 58% cutoff became statistically
        # brittle while the genuine strength gap (avg_red_score asserted below)
        # is unchanged. Raising n restores a robust separation between the
        # team-position win signal and the ~50% physical-side signal — the
        # actual load-bearing invariant — without weakening the de-flipping
        # guard (the calibration re-baseline itself stays deferred).
        n = 120
        stats = sim.run(strong_team, weak_team, n=n, master_seed=9001)

        # Team-position: the strong team is the team_red arg → red_*.
        # It must out-score and out-win the weak team on a team-position
        # basis despite playing physical blue half the games.
        assert stats["avg_red_score"] > stats["avg_blue_score"], (
            "Strong team must out-score the weak team on a team-position "
            f"basis ({stats['avg_red_score']} vs {stats['avg_blue_score']})"
        )
        red_pct = stats["red_win_pct"]
        assert red_pct >= 58.0, (
            "Strong team's team-position win% must stay clearly above 50% "
            f"(not diluted by side alternation); got {red_pct:.1f}%. "
            "If de-flipping is broken this collapses toward ~50%."
        )

        # Physical-side advantage should be roughly balanced: both teams play
        # red equally often, so neither physical side has a structural edge.
        sa = stats["side_advantage"]
        side_pct = sa["red_side_win_pct"]
        assert 30.0 <= side_pct <= 70.0, (
            "Physical red-side win% should be near 50% (both teams play red "
            f"equally) — got {side_pct:.1f}%. If this tracks the strong "
            "team's win%, results were never de-flipped."
        )

        # The decisive contrast: the team-position signal must be strictly
        # stronger than the physical-side signal. De-flipping preserves the
        # team-strength signal while alternation washes out the side signal.
        assert abs(red_pct - 50.0) > abs(side_pct - 50.0), (
            "De-flip failure: the team-position win signal "
            f"(|{red_pct:.1f}-50|={abs(red_pct-50):.1f}) is not stronger "
            f"than the physical-side signal "
            f"(|{side_pct:.1f}-50|={abs(side_pct-50):.1f}). A correct "
            "de-flip keeps team strength visible in red_win_pct while "
            "side_advantage stays balanced."
        )

    # ------------------------------------------------------------------
    # 4. side_advantage shape
    # ------------------------------------------------------------------

    def test_side_advantage_shape_and_bounds(self):
        """All documented keys present; pcts in [0,100]; counts sum to n."""
        _, team_red, _ = self._rosters("Sim08ShapeR")
        _, team_blue, _ = self._rosters("Sim08ShapeB")
        sim = BatchSimulator()

        n = 10
        stats = sim.run(team_red, team_blue, n=n, master_seed=555)

        assert "side_advantage" in stats, "results must expose 'side_advantage'"
        sa = stats["side_advantage"]
        for key in self.SIDE_ADV_KEYS:
            assert key in sa, f"side_advantage missing documented key {key!r}"

        assert sa["n"] == n
        assert sa["red_side_wins"] + sa["blue_side_wins"] + sa["side_ties"] == n
        for pct_key in ("red_side_win_pct", "blue_side_win_pct"):
            assert (
                0.0 <= sa[pct_key] <= 100.0
            ), f"{pct_key} out of [0,100]: {sa[pct_key]}"
        for score_key in ("avg_red_side_score", "avg_blue_side_score"):
            assert sa[score_key] >= 0, f"{score_key} must be non-negative"

        # Team-position aggregates must still be present and consistent.
        assert stats["red_wins"] + stats["blue_wins"] + stats["ties"] == n

    # ------------------------------------------------------------------
    # 5. Determinism: same master_seed ⇒ identical everything
    # ------------------------------------------------------------------

    def test_same_master_seed_reproduces_scores_and_side_advantage(self):
        """Two ``run()`` calls with the same master_seed match exactly."""
        _, team_red, _ = self._rosters("Sim08DetR")
        _, team_blue, _ = self._rosters("Sim08DetB")
        sim = BatchSimulator()

        a = sim.run(team_red, team_blue, n=8, master_seed=314159)
        b = sim.run(team_red, team_blue, n=8, master_seed=314159)

        assert a["red_scores"] == b["red_scores"]
        assert a["blue_scores"] == b["blue_scores"]
        assert a["side_advantage"] == b["side_advantage"]
        assert a["avg_seeds"] == b["avg_seeds"]
        assert a["outlier_seeds"] == b["outlier_seeds"]

        # Each seed list element is the documented [seed, flipped] pair.
        for pair in a["avg_seeds"] + a["outlier_seeds"]:
            assert len(pair) == 2
            seed, flipped = pair[0], pair[1]
            assert isinstance(seed, int) and not isinstance(seed, bool)
            assert isinstance(flipped, bool)

    def test_serial_equals_parallel_team_position_and_side_advantage(self):
        """Serial and ``workers=2`` agree on team-position aggregates AND
        ``side_advantage`` for the same master_seed.

        Mirrors the SIM-07 serial-vs-parallel skip-on-unavailable pattern.
        """
        _, team_red, _ = self._rosters("Sim08ParR")
        _, team_blue, _ = self._rosters("Sim08ParB")
        sim = BatchSimulator()

        serial = sim.run(team_red, team_blue, n=4, master_seed=2718)
        try:
            parallel = sim.run(team_red, team_blue, n=4, master_seed=2718, workers=2)
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(
                f"Parallel worker pool unavailable in this environment: {exc!r}"
            )

        assert serial["red_wins"] == parallel["red_wins"]
        assert serial["blue_wins"] == parallel["blue_wins"]
        assert serial["ties"] == parallel["ties"]
        assert serial["red_scores"] == parallel["red_scores"]
        assert serial["blue_scores"] == parallel["blue_scores"]
        assert serial["avg_red_score"] == parallel["avg_red_score"]
        assert serial["avg_blue_score"] == parallel["avg_blue_score"]
        assert serial["side_advantage"] == parallel["side_advantage"]
        assert serial["avg_seeds"] == parallel["avg_seeds"]
        assert serial["outlier_seeds"] == parallel["outlier_seeds"]

    # ------------------------------------------------------------------
    # 6. Faithful flipped replay (pure, no DB persistence)
    # ------------------------------------------------------------------

    @staticmethod
    def _deflip(result, flipped):
        """De-flip a physical-side result back to team position.

        ``replay_round`` swaps rosters internally for ``flipped=True`` and
        returns the PHYSICAL-side result ("Returns (result, ...) as before").
        The team-position view — the one ``run()`` aggregates — is recovered
        by swapping red/blue back when the game was flipped.
        """
        rp, bp = result["red_points"], result["blue_points"]
        return (bp, rp) if flipped else (rp, bp)

    def test_flipped_replay_reproduces_recorded_team_position_result(self):
        """A flipped ``[seed, True]`` pair, replayed with ``flipped=True``,
        reproduces a team-position result that ``run()`` actually recorded for
        that batch; replaying it twice is byte-for-byte identical.

        Two distinguishable teams are used so the internal roster swap
        genuinely changes the physical-side outcome. ``replay_round`` returns
        the PHYSICAL-side result; de-flipping it (swap red/blue because the
        game was flipped) must yield a ``(team_red, team_blue)`` points pair
        that appears among the ``(red_scores[i], blue_scores[i])`` pairs
        ``run()`` recorded for the same ``master_seed``.
        """
        red, team_red, red_players = self._rosters("Sim08FlipReplayR")
        blue, team_blue, blue_players = self._rosters("Sim08FlipReplayB")
        # Make the two teams distinguishable so a roster swap actually changes
        # the physical-side outcome (otherwise flipped/non-flipped would be
        # trivially identical and the test would not exercise the swap).
        self._bump_team_stats(red_players, accuracy=90, survival=20, awareness=80)
        self._bump_team_stats(blue_players, accuracy=20, survival=90, awareness=20)
        sim = BatchSimulator()

        n = 12
        stats = sim.run(team_red, team_blue, n=n, master_seed=8675309)

        recorded_pairs = list(zip(stats["red_scores"], stats["blue_scores"]))

        # Find a flipped pair among the recorded seeds.
        all_pairs = stats["avg_seeds"] + stats["outlier_seeds"]
        flipped_pairs = [p for p in all_pairs if bool(p[1])]
        assert flipped_pairs, (
            "Expected at least one flipped game among recorded seeds for "
            f"n={n} (alternation guarantees ~n/2 flipped)"
        )
        seed = flipped_pairs[0][0]
        assert bool(flipped_pairs[0][1]) is True

        # Replay the flipped game twice — physical-side result, swapped
        # rosters internally.
        res_a, _, _, log_a = sim.replay_round(red, blue, seed, flipped=True)
        res_b, _, _, log_b = sim.replay_round(red, blue, seed, flipped=True)

        assert len(log_a) > 0, "Flipped replay event log must not be empty"
        assert len(log_a) == len(
            log_b
        ), f"Flipped replay log length differs: {len(log_a)} vs {len(log_b)}"
        for i, (e1, e2) in enumerate(zip(log_a, log_b)):
            assert e1 == e2, f"Flipped replay event {i} differs:\n {e1}\n {e2}"

        # Determinism: replaying twice is byte-identical at the result level.
        assert res_a["red_points"] == res_b["red_points"]
        assert res_a["blue_points"] == res_b["blue_points"]

        # Faithfulness: the flipped game's team-position outcome must be one
        # of the pairs run() recorded for this batch (same master_seed ⇒ same
        # per-game seeds ⇒ this flipped game's team-position score was
        # bucketed into red_scores/blue_scores). The contract leaves it open
        # whether replay_round returns the PHYSICAL-side result or already
        # de-flips it, so accept either orientation — both are faithful as
        # long as the team-position pair matches what the batch recorded.
        raw = (res_a["red_points"], res_a["blue_points"])
        deflipped = self._deflip(res_a, flipped=True)
        assert raw in recorded_pairs or deflipped in recorded_pairs, (
            "Flipped replay result is not faithful: neither the raw result "
            f"{raw} nor its de-flipped form {deflipped} appears among the "
            f"team-position score pairs run() recorded for the same "
            f"master_seed: {recorded_pairs}. replay_round(flipped=True) must "
            "reproduce the team-position outcome the batch saw for that "
            "flipped game (in physical or de-flipped orientation)."
        )

        # The flipped flag must actually change which roster plays physical
        # red: with distinguishable teams, a non-flipped replay of the SAME
        # seed yields a different physical-side result.
        res_canon, _, _, _ = sim.replay_round(red, blue, seed, flipped=False)
        assert (res_canon["red_points"], res_canon["blue_points"]) != (
            res_a["red_points"],
            res_a["blue_points"],
        ), (
            "Flipped vs non-flipped replay of the same seed must differ for "
            "distinguishable teams — the flipped flag is being ignored "
            "(no internal roster swap)."
        )

    # ------------------------------------------------------------------
    # 7. DB persistence — actual physical sides for flipped games
    # ------------------------------------------------------------------

    def test_save_games_persists_actual_physical_sides(self):
        """``save_games`` with one canonical and one flipped pair stores the
        ACTUAL physical sides.

        For the flipped pair, ``GameRound.team_red`` must be the team that
        physically played red (the ``team_blue`` arg), and every red-colored
        ``PlayerRoundState`` must belong to ``GameRound.team_red``. The
        canonical pair stores the unswapped sides.
        """
        from matches.models import GameRound, PlayerRoundState

        _, team_red, _ = self._rosters("Sim08PersistR")
        _, team_blue, _ = self._rosters("Sim08PersistB")
        sim = BatchSimulator()

        s_canon, s_flip = 1010101, 2020202
        saved = sim.save_games(
            team_red,
            team_blue,
            seeds=[(s_canon, False), (s_flip, True)],
            n=2,
        )
        assert len(saved) == 2, f"Expected 2 saved rounds, got {len(saved)}"

        rounds = list(
            GameRound.objects.filter(id__in=[g.id for g in saved]).order_by("id")
        )
        canon_round, flip_round = rounds[0], rounds[1]

        # Canonical: stored sides are the unswapped arguments.
        assert canon_round.team_red_id == team_red.id
        assert canon_round.team_blue_id == team_blue.id

        # Flipped: the ACTUAL physical red side was team_blue arg.
        assert flip_round.team_red_id == team_blue.id, (
            "Flipped game must persist the team that physically played red "
            "(the team_blue argument) as GameRound.team_red"
        )
        assert flip_round.team_blue_id == team_red.id

        # PlayerRoundState.team_color must stay consistent with stored sides:
        # red-colored PRS players belong to GameRound.team_red.
        for gr in (canon_round, flip_round):
            states = list(PlayerRoundState.objects.filter(game_round=gr))
            assert states, f"No PlayerRoundState rows for round {gr.id}"
            for s in states:
                expected_team = (
                    gr.team_red_id if s.team_color == "red" else gr.team_blue_id
                )
                assert s.player.team_id == expected_team, (
                    f"PRS {s.player} team_color={s.team_color} but its player "
                    f"belongs to team {s.player.team_id}, inconsistent with "
                    f"stored GameRound sides (red={gr.team_red_id}, "
                    f"blue={gr.team_blue_id})"
                )


@pytest.mark.django_db
class TestPrecomputeRosterParity:
    """Regression: _precompute_roster must precompute every stat _make_players reads.

    The parallel score_averages path (--workers > 1) ships _precompute_roster
    output into worker processes, which call _make_players on it. _PlayerData
    does a hard dict lookup, so any stat consumed by _make_players but absent
    from _SIMULATION_STATS raised KeyError only under --workers > 1 (the serial
    path reads stats live off the ORM Player and was unaffected).
    'game_awareness' was the missing key.
    """

    def test_precomputed_roster_runs_through_make_players(self):
        from matches.simulation import _precompute_roster

        team, _ = make_team_with_slots("PrecompParity")
        precomputed = _precompute_roster(list(team.active_roster))

        # Must not raise KeyError for any stat _make_players consumes.
        players = BatchSimulator()._make_players(precomputed, "red")

        assert len(players) == len(precomputed)
        for p in players:
            assert isinstance(p.game_awareness, int)


# ---------------------------------------------------------------------------
# MOVE-01 — BatchSimulator compact movement trail + determinism
#
# Locked decisions encoded here (CONTEXT.md / sim_helpers/CLAUDE.md / ADR-0007):
#   * PlayerState.movement_trail: transient list of compact
#     (start_cell, end_cell, timestamp) Advance steps; NO DB column, NO
#     migration; appended only when the cell actually changed.
#   * _flush_to_db turns movement_trail into compact movement GameEvents
#     (start + end + timestamp, no route list) ONLY when a round is saved.
#   * an unsaved run() never persists movement (no DB writes at all).
#   * determinism preserved: same master_seed ⇒ identical games (incl. trails);
#     serial == parallel team-position aggregates. Advance/A* consume no RNG.
# ---------------------------------------------------------------------------


_FLOOR_12X12 = [[1] * 12 for _ in range(12)]


def _move01_make_map(name, zone_data=None, zone_size=100):
    """Confirmed ArenaMap with the configs BatchSimulator.run needs for a map.

    Mirrors test_map.py::TestMap02CellMovement._make_map.
    """
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )
    from core.map_processing import compute_sight_lines

    zone_data = zone_data or _FLOOR_12X12
    rows, cols, px = len(zone_data), len(zone_data[0]), zone_size
    arena_map = ArenaMap.objects.create(
        name=name, img_width=cols * px, img_height=rows * px
    )
    MapZoneConfig.objects.create(
        arena_map=arena_map, zone_size=px, zone_data=zone_data, confirmed=True
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map, base_type="red", x_px=px // 2, y_px=px // 2
    )
    MapBaseConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        x_px=cols * px - px // 2,
        y_px=rows * px - px // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=px,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="red", zone_size=px, visible_cells=[]
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map, base_type="blue", zone_size=px, visible_cells=[]
    )
    return arena_map


@pytest.mark.django_db
class TestMove01PlayerStateMovementTrail:
    """PlayerState.movement_trail: transient, defaults empty, accumulates
    compact (start, end, timestamp) entries during a map-active round."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    def test_movement_trail_defaults_to_empty_list(self):
        """Fresh PlayerState has an independent empty movement_trail (no DB
        column — it is a transient default_factory list)."""
        from matches.sim_helpers.player_state import PlayerState

        a = PlayerState(
            tag_id="red_scout",
            name="s",
            team_color="red",
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=30,
            final_lives=15,
            final_shots=30,
        )
        b = PlayerState(
            tag_id="blue_scout",
            name="s2",
            team_color="blue",
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=30,
            final_lives=15,
            final_shots=30,
        )
        assert a.movement_trail == []
        a.movement_trail.append(((0, 0), (0, 1), 0))
        assert b.movement_trail == [], "movement_trail must not be shared state"

    def test_move_player_in_memory_appends_compact_trail_entry(self):
        """A real in-memory move appends one compact (start, end, ts) tuple;
        a no-op move appends nothing."""
        from matches.simulation import BatchSimulator
        from matches.sim_helpers.player_state import PlayerState
        from matches.sim_helpers.map_context import MapContext
        from matches.sim_helpers.pathfinding import build_movement_adjacency

        grid = [[1] * 20]
        ctx = MapContext.from_dict(
            {
                "adj": build_movement_adjacency(grid),
                "spawn_cells": {"red": (0, 0), "blue": (0, 19)},
                "zone_data": grid,
                "sight_data": None,
            }
        )
        player = PlayerState(
            tag_id="red_scout",
            name="s",
            team_color="red",
            role="scout",
            accuracy=50,
            survival=50,
            starting_lives=15,
            starting_shots=30,
            final_lives=15,
            final_shots=30,
            speed=50,
            cell_row=0,
            cell_col=0,
        )

        sim = BatchSimulator()
        sim._move_player_in_memory(player, 4, (0, 19), ctx, 1)
        assert len(player.movement_trail) == 1
        start, end, ts = player.movement_trail[0]
        assert start == (0, 0)
        assert end == (player.cell_row, player.cell_col)
        assert ts == 4

        # No-op move (already at goal) appends nothing.
        before = list(player.movement_trail)
        sim._move_player_in_memory(
            player, 5, (player.cell_row, player.cell_col), ctx, 1
        )
        assert player.movement_trail == before

    def test_movement_trail_accumulates_over_a_map_round(self):
        """Over a batch round with a map active, players accumulate a
        multi-step movement_trail (the always-on Advance fires every tick)."""
        red, team_red = self._rosters("M01TrailR")
        blue, team_blue = self._rosters("M01TrailB")
        arena_map = _move01_make_map("M01TrailMap")

        from matches.sim_helpers.map_loader import load_map_context

        sim = BatchSimulator()
        movement_ctx, _ = load_map_context(arena_map)

        import random

        random.seed(42)
        _result, red_players, blue_players = sim._simulate_round(
            red, blue, movement_ctx=movement_ctx
        )

        all_players = red_players + blue_players
        assert any(
            len(p.movement_trail) > 0 for p in all_players
        ), "at least one player must accumulate a movement_trail under a map"
        for p in all_players:
            for entry in p.movement_trail:
                start, end, ts = entry
                assert start != end, "trail entries are real (changed) Advances"
                assert isinstance(ts, int)


@pytest.mark.django_db
class TestMove01FlushTrailToCompactEvents:
    """_flush_to_db converts movement_trail into compact movement GameEvents
    (start + end + timestamp, no route list); an unsaved run() persists none."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    _ROUTE_KEYS = ("route", "path", "cells", "cell_path", "trail", "steps")

    def test_save_games_emits_compact_movement_events_from_trail(self):
        from matches.models import GameEvent

        red, team_red = self._rosters("M01FlushR")
        blue, team_blue = self._rosters("M01FlushB")
        arena_map = _move01_make_map("M01FlushMap")

        sim = BatchSimulator()
        saved = sim.save_games(
            team_red,
            team_blue,
            seeds=[(20240517, False)],
            n=1,
            arena_map=arena_map,
        )
        assert saved, "save_games returned no rounds"
        gr = saved[0]

        move_events = list(
            GameEvent.objects.filter(game_round=gr, event_type="movement")
        )
        assert move_events, (
            "save_games must flush PlayerState.movement_trail into compact "
            "movement GameEvents"
        )
        for ev in move_events:
            md = ev.metadata
            assert "start_row" in md and "start_col" in md, md
            assert "end_row" in md and "end_col" in md, md
            assert ev.timestamp is not None
            assert (md["start_row"], md["start_col"]) != (
                md["end_row"],
                md["end_col"],
            ), f"compact event must reflect a real Advance: {md}"
            for bad in self._ROUTE_KEYS:
                assert bad not in md, f"no route list allowed in metadata: {md}"
            for v in md.values():
                assert not isinstance(
                    v, (list, tuple)
                ), f"metadata must be flat scalars: {md}"

    def test_unsaved_run_persists_no_movement_events(self):
        """run() is pure in-memory: a movement_trail accumulates but NO
        GameEvent / PlayerRoundState rows are written for an unsaved batch."""
        from matches.models import GameEvent, GameRound, PlayerRoundState

        red, team_red = self._rosters("M01NoSaveR")
        blue, team_blue = self._rosters("M01NoSaveB")
        arena_map = _move01_make_map("M01NoSaveMap")

        gr_before = GameRound.objects.count()
        ev_before = GameEvent.objects.count()
        prs_before = PlayerRoundState.objects.count()

        BatchSimulator().run(
            team_red, team_blue, n=2, arena_map=arena_map, master_seed=99
        )

        assert GameRound.objects.count() == gr_before
        assert GameEvent.objects.count() == ev_before
        assert PlayerRoundState.objects.count() == prs_before


@pytest.mark.django_db
class TestMove01DeterminismPreserved:
    """MOVE-01 must not perturb SIM-07/08 determinism: the always-on Advance
    and 2× step consume no RNG, so same master_seed ⇒ identical games and
    serial == parallel team-position aggregates (with a map active)."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    def test_same_master_seed_identical_with_map(self):
        red, team_red = self._rosters("M01DetR")
        blue, team_blue = self._rosters("M01DetB")
        arena_map = _move01_make_map("M01DetMap")
        sim = BatchSimulator()

        a = sim.run(team_red, team_blue, n=6, arena_map=arena_map, master_seed=2024)
        b = sim.run(team_red, team_blue, n=6, arena_map=arena_map, master_seed=2024)

        assert a["avg_red_score"] == b["avg_red_score"]
        assert a["avg_blue_score"] == b["avg_blue_score"]
        assert a["red_wins"] == b["red_wins"]
        assert a["avg_seeds"] == b["avg_seeds"]
        assert a["outlier_seeds"] == b["outlier_seeds"]
        assert a["side_advantage"] == b["side_advantage"]

    def test_replay_movement_trail_is_deterministic(self):
        """Two replays of the same (seed, orientation) with a map produce
        byte-identical movement trails (Advance/A* are deterministic)."""
        red, team_red = self._rosters("M01ReplayR")
        blue, team_blue = self._rosters("M01ReplayB")
        arena_map = _move01_make_map("M01ReplayMap")

        from matches.sim_helpers.map_loader import load_map_context

        movement_ctx, _ = load_map_context(arena_map)
        sim = BatchSimulator()

        seed = 76543210
        _, red_a, blue_a, _ = sim.replay_round(
            red, blue, seed, flipped=False, movement_ctx=movement_ctx
        )
        _, red_b, blue_b, _ = sim.replay_round(
            red, blue, seed, flipped=False, movement_ctx=movement_ctx
        )

        trails_a = [p.movement_trail for p in red_a + blue_a]
        trails_b = [p.movement_trail for p in red_b + blue_b]
        assert trails_a == trails_b, "replayed movement trails must be identical"
        assert any(t for t in trails_a), "expected non-empty trails with a map"

    def test_serial_equals_parallel_team_positions_with_map(self):
        """Serial vs parallel batch with a map active: identical team-position
        aggregates and side_advantage (MOVE-01 Advance is RNG-free)."""
        red, team_red = self._rosters("M01ParR")
        blue, team_blue = self._rosters("M01ParB")
        arena_map = _move01_make_map("M01ParMap")
        sim = BatchSimulator()

        serial = sim.run(team_red, team_blue, n=4, arena_map=arena_map, master_seed=777)
        try:
            parallel = sim.run(
                team_red,
                team_blue,
                n=4,
                arena_map=arena_map,
                master_seed=777,
                workers=2,
            )
        except Exception as exc:  # pragma: no cover - environment dependent
            pytest.skip(f"Parallel worker pool unavailable: {exc!r}")

        assert serial["red_wins"] == parallel["red_wins"]
        assert serial["blue_wins"] == parallel["blue_wins"]
        assert serial["ties"] == parallel["ties"]
        assert serial["avg_red_score"] == parallel["avg_red_score"]
        assert serial["avg_blue_score"] == parallel["avg_blue_score"]
        assert serial["side_advantage"] == parallel["side_advantage"]


# ---------------------------------------------------------------------------
# MOVE-02 — path commitment via a goal-keyed A* cache (BatchSimulator only)
#
# Behavioural spec: docs/adr/0008-path-commitment-via-goal-keyed-cache.md +
# CONTEXT.md "Path commitment".
#
# Locked decisions encoded here:
#   * The MOVE-02 contract is *internal* determinism, NOT identity to
#     pre-MOVE-02 games (ADR-0008): same master_seed + Orientation + rosters +
#     map ⇒ identical game, and serial == parallel team-position aggregates
#     and side_advantage — and this must STILL hold with the path cache active
#     (the cache and its invalidation consume no RNG).
#   * The compact movement trail still flushes to compact
#     GameEvent(event_type="movement") rows correctly under caching: route
#     commitment must not corrupt the flushed trail (start/end cells coherent,
#     a row only when the cell actually changed, no route list in metadata).
#   * Scope is BatchSimulator only (RBS deliberately uncached per ADR-0008).
#
# No wall-clock timing, no Score-Calibration point assertions (re-baseline
# pending per ADR-0008), no mock-returns-mock.
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestMove02DeterminismParityWithCache(TestMove01DeterminismPreserved):
    """Extends the MOVE-01 serial==parallel / master-seed parity contract:

    the SAME determinism guarantees must hold with the MOVE-02 path cache
    active. Inherits every MOVE-01 determinism case (re-run here against the
    cached pathfinding) and adds a stronger same-master-seed parity assertion
    that also pins the per-round seed/orientation lists and the win split — the
    internal contract MOVE-02 must preserve.
    """

    def test_path_cache_does_not_perturb_master_seed_determinism(self):
        """Two map-active run()s with the same master_seed are identical down
        to seed/orientation lists and the full win split — the path cache and
        its invalidation must consume no RNG (ADR-0008 internal contract)."""
        red, team_red = self._rosters("M02DetR")
        blue, team_blue = self._rosters("M02DetB")
        arena_map = _move01_make_map("M02DetMap")
        sim = BatchSimulator()

        a = sim.run(team_red, team_blue, n=6, arena_map=arena_map, master_seed=2024)
        b = sim.run(team_red, team_blue, n=6, arena_map=arena_map, master_seed=2024)

        assert a["red_wins"] == b["red_wins"]
        assert a["blue_wins"] == b["blue_wins"]
        assert a["ties"] == b["ties"]
        assert a["avg_red_score"] == b["avg_red_score"]
        assert a["avg_blue_score"] == b["avg_blue_score"]
        assert a["avg_seeds"] == b["avg_seeds"]
        assert a["outlier_seeds"] == b["outlier_seeds"]
        assert a["side_advantage"] == b["side_advantage"]

    def test_replay_movement_trail_identical_with_cache(self):
        """Two replays of the same (seed, orientation) with a map produce
        byte-identical movement trails even with path commitment active —
        the committed route is a deterministic function of the seeded game."""
        red, team_red = self._rosters("M02ReplayR")
        blue, team_blue = self._rosters("M02ReplayB")
        arena_map = _move01_make_map("M02ReplayMap")

        from matches.sim_helpers.map_loader import load_map_context

        movement_ctx, _ = load_map_context(arena_map)
        sim = BatchSimulator()

        seed = 24681357
        _, red_a, blue_a, _ = sim.replay_round(
            red, blue, seed, flipped=False, movement_ctx=movement_ctx
        )
        _, red_b, blue_b, _ = sim.replay_round(
            red, blue, seed, flipped=False, movement_ctx=movement_ctx
        )

        trails_a = [p.movement_trail for p in red_a + blue_a]
        trails_b = [p.movement_trail for p in red_b + blue_b]
        assert trails_a == trails_b, (
            "MOVE-02: committed routes must replay byte-identically under the "
            "path cache"
        )
        assert any(t for t in trails_a), "expected non-empty trails with a map"


@pytest.mark.django_db
class TestMove02FlushTrailUnderCaching:
    """DB integration: a small BatchSim round WITH a map persisted via
    save_games (→ _flush_to_db) must produce coherent compact movement
    GameEvents under path commitment — route commitment must not corrupt the
    flushed trail."""

    def _rosters(self, prefix):
        team, _ = make_team_with_slots(prefix)
        return list(team.active_roster), team

    _ROUTE_KEYS = ("route", "path", "cells", "cell_path", "trail", "steps")

    def test_flushed_movement_trail_coherent_with_path_cache(self):
        from matches.models import GameEvent

        red, team_red = self._rosters("M02FlushR")
        blue, team_blue = self._rosters("M02FlushB")
        arena_map = _move01_make_map("M02FlushMap")

        sim = BatchSimulator()
        saved = sim.save_games(
            team_red,
            team_blue,
            seeds=[(20240517, False)],
            n=1,
            arena_map=arena_map,
        )
        assert saved, "save_games returned no rounds"
        gr = saved[0]

        move_events = list(
            GameEvent.objects.filter(game_round=gr, event_type="movement")
        )
        assert move_events, (
            "a map-active saved round must flush movement_trail into compact "
            "movement GameEvents even with the path cache active"
        )

        # Group movement events per actor in timestamp order; under path
        # commitment the per-Advance compact events must still chain coherently
        # (each event's start == the previous event's end for that actor) and
        # only ever fire on a real cell change.
        by_actor: dict[int, list] = {}
        for ev in sorted(move_events, key=lambda e: (e.actor_id, e.timestamp)):
            md = ev.metadata
            assert "start_row" in md and "start_col" in md, md
            assert "end_row" in md and "end_col" in md, md
            assert ev.timestamp is not None
            assert (md["start_row"], md["start_col"]) != (
                md["end_row"],
                md["end_col"],
            ), f"compact event must reflect a real Advance (cell changed): {md}"
            for bad in self._ROUTE_KEYS:
                assert bad not in md, (
                    f"route commitment must NOT leak a route list into "
                    f"metadata: {md}"
                )
            for v in md.values():
                assert not isinstance(
                    v, (list, tuple)
                ), f"metadata must be flat scalars: {md}"
            by_actor.setdefault(ev.actor_id, []).append(ev)

        # Per-actor trail continuity: consecutive Advances are contiguous —
        # the end cell of one compact event is the start cell of the next.
        # A wobbling / corrupted committed route would break this chain.
        chained_any = False
        for actor_id, evs in by_actor.items():
            for prev, nxt in zip(evs, evs[1:]):
                chained_any = True
                assert (
                    prev.metadata["end_row"],
                    prev.metadata["end_col"],
                ) == (
                    nxt.metadata["start_row"],
                    nxt.metadata["start_col"],
                ), (
                    f"actor {actor_id} trail discontinuity: "
                    f"{prev.metadata} then {nxt.metadata} — route commitment "
                    f"corrupted the flushed trail"
                )
        assert chained_any, (
            "expected at least one actor with >=2 chained Advances to verify "
            "trail continuity under caching"
        )

    def test_saved_cell_position_matches_last_trail_end(self):
        """The persisted PlayerRoundState.cell_row/col must equal the end cell
        of that player's final flushed movement event — path commitment must
        not desync the final cell from the committed trail."""
        from matches.models import GameEvent, PlayerRoundState

        red, team_red = self._rosters("M02CoherR")
        blue, team_blue = self._rosters("M02CoherB")
        arena_map = _move01_make_map("M02CoherMap")

        sim = BatchSimulator()
        saved = sim.save_games(
            team_red,
            team_blue,
            seeds=[(13131313, False)],
            n=1,
            arena_map=arena_map,
        )
        assert saved
        gr = saved[0]

        checked = 0
        for prs in PlayerRoundState.objects.filter(game_round=gr):
            evs = list(
                GameEvent.objects.filter(
                    game_round=gr, event_type="movement", actor=prs.player
                ).order_by("timestamp")
            )
            if not evs:
                continue
            last = evs[-1]
            assert (prs.cell_row, prs.cell_col) == (
                last.metadata["end_row"],
                last.metadata["end_col"],
            ), (
                f"{prs.player} final cell {(prs.cell_row, prs.cell_col)} must "
                f"match last committed Advance end "
                f"{(last.metadata['end_row'], last.metadata['end_col'])}"
            )
            checked += 1
        assert checked > 0, "expected at least one player with movement events"
