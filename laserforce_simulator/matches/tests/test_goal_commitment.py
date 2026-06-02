"""MOVE-04 — Goal commitment via tick-cadence throttling (pure-unit, deterministic).

Steady-state goal recompute is throttled: ``choose_goal_cell`` steps 2-4
(action-driven, role-positioning, enemy-base default) only run every
``GOAL_RECOMPUTE_PERIOD_TICKS`` (= 4) ticks. Reactive overrides (step 0
nuke-reaction, step 1 critical-resource, step 1b score-broadcast seek-medic)
still fire every tick and CLEAR any prior commitment.

These tests are written to the agreed MOVE-04 contract (TDD). The production
code is being written in parallel by another agent, so several of these tests
will fail until that lands. Each test block documents the surface it pins so a
failure points straight at the missing piece.

Seam contract (see prompt + matches/CLAUDE.md):
* ``GOAL_RECOMPUTE_PERIOD_TICKS = 4`` in ``sim_helpers/time_constants.py``.
* ``PlayerState._committed_goal: Optional[tuple[(row,col), from_action: bool,
  expires_at_tick: int]] = None``.
* ``choose_goal_cell`` returns the committed goal when one is valid (cache hit);
  otherwise runs steps 2-4 and writes a fresh commitment.
* Recompute triggers: ``_committed_goal is None``, ``current == cached goal``,
  or ``second >= expires_at_tick``.
* On recompute: ``_committed_goal = (goal, from_action, second + 4)`` where
  ``from_action`` is ``True`` iff step 2 (``_goal_from_action``) produced it.
* ``time_domain != "ticks"`` (RBS / seconds-domain): throttle is skipped, no
  commitment is ever stored.
* ``BatchSimulator._record_down`` clears commitment iff ``from_action=True``;
  positioning goals survive a Down.
* ``combat.plan_action`` clears commitment when the Stationary flags
  ``is_hiding`` / ``is_holding`` transition True -> False.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# Lightweight fixtures (mirror the style of test_goal_selection.py / test_move03)
# ---------------------------------------------------------------------------


_LIVES = {"commander": 30, "heavy": 20, "scout": 30, "medic": 20, "ammo": 20}
_SHOTS = {"commander": 60, "heavy": 40, "scout": 60, "medic": 30, "ammo": 15}


def _make_player(
    tag_id,
    team_color,
    role,
    *,
    lives=None,
    shots=None,
    cell_row=None,
    cell_col=None,
    **extra,
):
    max_lives = _LIVES[role]
    max_shots = _SHOTS[role]
    return PlayerState(
        tag_id=tag_id,
        name=tag_id,
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=max_lives,
        starting_shots=max_shots,
        final_lives=max_lives if lives is None else lives,
        final_shots=max_shots if shots is None else shots,
        cell_row=cell_row,
        cell_col=cell_col,
        **extra,
    )


def _ctx(
    *,
    adj=None,
    sight_data=None,
    cell_los_counts=None,
    high_los_cells=None,
    strong_spots=None,
    spawn_cells=None,
):
    return MapContext.from_dict(
        {
            "adj": adj or {},
            "spawn_cells": spawn_cells or {"red": (0, 0), "blue": (9, 9)},
            "zone_data": None,
            "sight_data": sight_data or {},
            "base_sight_data": {},
            "cell_los_counts": cell_los_counts or {},
            "high_los_cells": high_los_cells or [],
            "strong_spots": strong_spots or [],
        }
    )


# ---------------------------------------------------------------------------
# Suite
# ---------------------------------------------------------------------------


class TestMove04GoalCommitment:
    """All MOVE-04 goal-commitment behaviours under one roof."""

    # ── Constant + field default ────────────────────────────────────────────

    def test_goal_recompute_period_constant_is_4(self):
        """Locked behaviour: GOAL_RECOMPUTE_PERIOD_TICKS = 4."""
        from matches.sim_helpers.time_constants import GOAL_RECOMPUTE_PERIOD_TICKS

        assert GOAL_RECOMPUTE_PERIOD_TICKS == 4

    def test_committed_goal_defaults_none(self):
        """Fresh PlayerState starts with _committed_goal = None."""
        p = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        assert getattr(p, "_committed_goal", "MISSING") is None

    # ── (a) First call computes fresh + stores tuple ────────────────────────

    def test_first_call_computes_and_stores_commitment(self):
        """First tick: no commitment → run steps 2-4, store (goal, from_action, expires)."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        goal = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        # scout → nearest high-LOS cell (step 3, role positioning)
        assert goal == (2, 2)

        commitment = getattr(scout, "_committed_goal", None)
        assert commitment is not None, "commitment must be stored on first call"
        assert isinstance(commitment, tuple) and len(commitment) == 3
        cached_goal, from_action, expires_at = commitment
        assert cached_goal == (2, 2)
        assert from_action is False  # came from role-positioning (step 3), not action
        assert expires_at == 0 + 4  # second + GOAL_RECOMPUTE_PERIOD_TICKS

    # ── (b) Within window: cache hit, steps 2-4 skipped ─────────────────────

    def test_within_window_returns_cached_without_running_steps_2_to_4(
        self, monkeypatch
    ):
        """A second call within (second < expires_at) returns commitment;
        _goal_from_role / _goal_from_action MUST NOT be called."""
        from matches.sim_helpers import pathfinding as pf
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # Prime the cache.
        goal0 = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert goal0 == (2, 2)

        # Now patch the step-2/3 helpers and expect a hit (no calls).
        action_calls = []
        role_calls = []
        monkeypatch.setattr(
            pf,
            "_goal_from_action",
            lambda *a, **k: action_calls.append(a) or (99, 99),
        )
        monkeypatch.setattr(
            pf,
            "_goal_from_role",
            lambda *a, **k: role_calls.append(a) or (88, 88),
        )

        # Tick 1: well inside the 4-tick window (expires at 4).
        goal1 = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=1, time_domain="ticks"
        )
        assert goal1 == (2, 2), "must return committed goal, not patched value"
        assert action_calls == [], "_goal_from_action must NOT be called inside window"
        assert role_calls == [], "_goal_from_role must NOT be called inside window"

        # Tick 3 (still inside window: 3 < 4).
        goal3 = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=3, time_domain="ticks"
        )
        assert goal3 == (2, 2)
        assert action_calls == [] and role_calls == []

    # ── (c) At expires_at_tick: recompute fires ─────────────────────────────

    def test_expiry_triggers_recompute(self, monkeypatch):
        """``second >= expires_at_tick`` forces a recompute (steps 2-4 run again)."""
        from matches.sim_helpers import pathfinding as pf
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # Prime: expires at tick 4.
        choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert scout._committed_goal[2] == 4

        # Patch step 3 to return a NEW goal so we can prove recompute ran.
        role_calls = []

        def fake_role(*a, **k):
            role_calls.append(1)
            return (7, 7)

        monkeypatch.setattr(pf, "_goal_from_role", fake_role)

        # Tick 4: at the expiry boundary → recompute.
        goal4 = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=4, time_domain="ticks"
        )
        assert role_calls == [1], "expiry must trigger a step-2/3/4 recompute"
        assert goal4 == (7, 7)
        # And the new commitment carries the next 4-tick window.
        cg, _from_action, expires = scout._committed_goal
        assert cg == (7, 7)
        assert expires == 4 + 4

    # ── (d) Reaching the goal forces recompute ──────────────────────────────

    def test_reaching_committed_goal_forces_recompute(self, monkeypatch):
        """When ``current == committed_goal[0]``, recompute fires even mid-window."""
        from matches.sim_helpers import pathfinding as pf
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert scout._committed_goal[0] == (2, 2)

        # Simulate the player walking onto the goal cell.
        scout.cell_row, scout.cell_col = 2, 2

        role_calls = []

        def fake_role(*a, **k):
            role_calls.append(1)
            # Return None so the default-enemy-base step 4 kicks in.
            return None

        monkeypatch.setattr(pf, "_goal_from_role", fake_role)

        # Tick 1: well inside the 4-tick window — but current == cached goal.
        goal = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=1, time_domain="ticks"
        )
        assert role_calls == [1], "reaching the goal must trigger recompute"
        # With high_los_cells removed effectively (fake role returns None),
        # the default enemy-base fallback should be (9, 9).
        assert goal == (9, 9)

    # ── (e) Reactive overrides clear commitment ─────────────────────────────

    def test_critical_lives_override_clears_committed_goal(self):
        """Step 1 (critical-lives override) fires every tick AND clears commitment."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        medic = _make_player("red_medic", "red", "medic", cell_row=4, cell_col=4)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # Prime a normal commitment first (healthy scout).
        choose_goal_cell(
            scout, [scout, medic], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert scout._committed_goal is not None
        assert scout._committed_goal[0] == (2, 2)

        # Now drop lives below the 30% threshold (30 * 0.3 = 9 → 5 qualifies).
        scout.final_lives = 5

        goal = choose_goal_cell(
            scout, [scout, medic], spawn_cells, ctx, second=1, time_domain="ticks"
        )
        # Override seeks the medic regardless of any cached goal.
        assert goal == (4, 4)
        # The commitment must be cleared so reactive overrides keep firing.
        assert (
            getattr(scout, "_committed_goal", "MISSING") is None
        ), "reactive override must clear _committed_goal"

    def test_nuke_reaction_override_clears_committed_goal(self):
        """Step 0 (nuke-reaction override) fires every tick AND clears commitment."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player(
            "red_scout", "red", "scout", lives=5, cell_row=0, cell_col=0
        )
        medic = _make_player("red_medic", "red", "medic", cell_row=4, cell_col=4)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # Prime a normal commitment (use a healthy scout for the prime call).
        scout.final_lives = 30
        choose_goal_cell(
            scout, [scout, medic], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert scout._committed_goal is not None

        # Now arm the nuke-reaction override (non-support, lives ≤ 30%).
        scout.final_lives = 5
        scout.reacting_to_nuke = True

        goal = choose_goal_cell(
            scout, [scout, medic], spawn_cells, ctx, second=1, time_domain="ticks"
        )
        assert goal == (4, 4)
        assert getattr(scout, "_committed_goal", "MISSING") is None

    # ── (f) Down-clear honours from_action flag ─────────────────────────────

    def test_record_down_clears_commitment_iff_from_action_true(self):
        """record_down clears commitment only when from_action=True.

        Shot-resolver consolidation lifted ``_record_down`` to
        ``sim_helpers.down.record_down``; this test now targets the
        new pure-function entry point.
        """
        from matches.sim_helpers.down import record_down

        # from_action=True (action-driven) — must be cleared.
        p1 = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        p1._committed_goal = ((5, 5), True, 4)
        record_down(p1, 7, ctx=None)
        assert (
            p1._committed_goal is None
        ), "action-driven commitment must be cleared on Down"

        # from_action=False (positioning) — must SURVIVE.
        p2 = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        p2._committed_goal = ((2, 2), False, 4)
        record_down(p2, 7, ctx=None)
        assert (
            p2._committed_goal is not None
            and p2._committed_goal[0] == (2, 2)
            and p2._committed_goal[1] is False
        ), "positioning commitment must survive a Down"

    # ── (g) Stationary-exit clears commitment ───────────────────────────────

    def test_plan_action_clears_commitment_on_is_hiding_transition(self, monkeypatch):
        """plan_action: when ``is_hiding`` transitions True → False, clear commitment."""
        from matches.sim_helpers import combat as combat_mod
        from matches.sim_helpers.combat import plan_action

        p = _make_player("red_scout", "red", "scout")
        p.is_hiding = True
        p._committed_goal = ((5, 5), False, 4)

        # Force the action choice to a non-stationary action so is_hiding clears.
        monkeypatch.setattr(
            combat_mod.random, "choices", lambda choices, weights=None: ["tag_player"]
        )
        plan_action(p, [p], 1, None)

        assert p.is_hiding is False
        assert (
            getattr(p, "_committed_goal", "MISSING") is None
        ), "exiting Stationary (is_hiding) must clear commitment"

    def test_plan_action_clears_commitment_on_is_holding_transition(self, monkeypatch):
        """plan_action: when ``is_holding`` transitions True → False, clear commitment."""
        from matches.sim_helpers import combat as combat_mod
        from matches.sim_helpers.combat import plan_action

        p = _make_player("red_scout", "red", "scout")
        p.is_holding = True
        p._committed_goal = ((5, 5), False, 4)

        monkeypatch.setattr(
            combat_mod.random, "choices", lambda choices, weights=None: ["only_move"]
        )
        plan_action(p, [p], 1, None)

        assert p.is_holding is False
        assert (
            getattr(p, "_committed_goal", "MISSING") is None
        ), "exiting Stationary (is_holding) must clear commitment"

    # ── (h) RBS (seconds-domain) is unaffected by the throttle ──────────────

    def test_seconds_domain_does_not_store_commitment(self):
        """time_domain != 'ticks' → throttle disabled; _committed_goal stays None."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # Default time_domain="seconds" (RBS).
        for s in (0.0, 0.5, 1.0, 2.0, 5.0):
            goal = choose_goal_cell(scout, [scout], spawn_cells, ctx, second=s)
            assert goal == (2, 2)
            assert (
                getattr(scout, "_committed_goal", None) is None
            ), f"RBS path must never write _committed_goal (tick={s})"

    # ── (i) from_action flag correctness ────────────────────────────────────

    def test_from_action_true_when_step2_action_driven_goal_chosen(self):
        """A step-2 ``_goal_from_action`` result → from_action=True."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        # Enemy at (3,3) so tag_player action drives the goal.
        enemy = _make_player("blue_scout", "blue", "scout", cell_row=3, cell_col=3)
        ctx = _ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(
            scout,
            [scout, enemy],
            spawn_cells,
            ctx,
            intended_action="tag_player",
            second=0,
            time_domain="ticks",
        )
        assert goal == (3, 3)

        commitment = scout._committed_goal
        assert commitment is not None
        assert (
            commitment[1] is True
        ), "step-2 action-driven goal must set from_action=True"

    def test_from_action_false_when_step3_role_positioning_goal_chosen(self):
        """A step-3 ``_goal_from_role`` result → from_action=False."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        scout = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        ctx = _ctx(spawn_cells=spawn_cells, high_los_cells=[(2, 2)])

        # No intended_action → falls through to role positioning (step 3).
        goal = choose_goal_cell(
            scout, [scout], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert goal == (2, 2)
        assert scout._committed_goal[1] is False

    def test_from_action_false_when_step4_default_enemy_base_chosen(self):
        """A step-4 default-enemy-base result → from_action=False."""
        from matches.sim_helpers.pathfinding import choose_goal_cell

        spawn_cells = {"red": (0, 0), "blue": (9, 9)}
        # Commander with no enemy medic in roster → step 3 returns None,
        # step 4 falls back to enemy base.
        commander = _make_player(
            "red_commander", "red", "commander", cell_row=0, cell_col=0
        )
        ctx = _ctx(spawn_cells=spawn_cells)

        goal = choose_goal_cell(
            commander, [commander], spawn_cells, ctx, second=0, time_domain="ticks"
        )
        assert goal == (9, 9)
        assert commander._committed_goal is not None
        assert commander._committed_goal[1] is False

    # ── (j) Determinism / no leakage across rounds ──────────────────────────

    @pytest.mark.django_db
    def test_same_seed_produces_identical_event_log(self):
        """Replay guarantee under MOVE-04 — same int seed → byte-identical log.

        Mirrors test_batch_sim.py::test_same_seed_produces_identical_event_log
        as a MOVE-04 regression: throttle must not introduce nondeterminism.
        """
        from matches.simulation import BatchSimulator
        from matches.tests.conftest import make_team_with_slots

        red, _ = make_team_with_slots("M04SeedR")
        blue, _ = make_team_with_slots("M04SeedB")
        red_roster = list(red.active_roster)
        blue_roster = list(blue.active_roster)
        sim = BatchSimulator()

        log1: list = []
        random.seed(42)
        sim._simulate_round(red_roster, blue_roster, event_log=log1)

        log2: list = []
        random.seed(42)
        sim._simulate_round(red_roster, blue_roster, event_log=log2)

        assert len(log1) > 0
        assert len(log1) == len(log2)
        for i, (e1, e2) in enumerate(zip(log1, log2)):
            assert e1 == e2, f"Event {i} differs:\n  run1: {e1}\n  run2: {e2}"

    def test_fresh_player_state_starts_with_no_commitment(self):
        """No leakage: every newly constructed PlayerState starts None.

        Hand-build several rounds' worth of PlayerStates; commitments from one
        "round" must never appear on the next (the field is per-instance,
        default None, never module-level/global).
        """
        # Round 1.
        p1 = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        p1._committed_goal = ((9, 9), True, 4)
        assert p1._committed_goal is not None

        # Round 2 — fresh objects.
        p2 = _make_player("red_scout", "red", "scout", cell_row=0, cell_col=0)
        p3 = _make_player("blue_heavy", "blue", "heavy", cell_row=9, cell_col=9)
        assert getattr(p2, "_committed_goal", "MISSING") is None
        assert getattr(p3, "_committed_goal", "MISSING") is None
