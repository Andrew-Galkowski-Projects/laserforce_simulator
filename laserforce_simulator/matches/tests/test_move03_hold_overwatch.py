"""MOVE-03 — Hold Action + pre-emptive Overwatch fire (pure-unit, deterministic).

Spec sources: docs/adr/0009-hold-overwatch.md and CONTEXT.md
(**Hold**, **Overwatch**, **Overwatch shot**, **Stationary**, **Reaction shot**).

These tests are written to the agreed MOVE-03 contract (TDD). The production
code is being written in parallel by another agent, so some of these tests are
expected to fail until that lands — each block documents which contract surface
it exercises so a failure points straight at the missing piece.

Contract being asserted (from ADR-0009 + CONTEXT.md + matches/CLAUDE.md):

* A 9th Action slot ``hold`` is added at index 8 of the action-weight array and
  the ``_CHOICES`` list; ``plan_action`` returns ``[{"type":"hold","actor":p}]``
  for a forced ``hold`` roll.
* ``is_holding`` is a transient bool (default-via-getattr False) that carries
  over across consecutive ``hold`` rolls and is cleared the first tick a
  non-``hold`` Action is rolled — mirroring ``is_hiding``.
* ``BatchSimulator._record_down`` force-clears ``is_holding`` (and still clears
  ``_path_cache``) — the structural life-loss clear from ADR-0009 decision 2.
* A holding player is **Stationary**: ``BatchSimulator._advance_player`` does
  not move it.
* An enemy that enters or *Advances through* a holder's LoS draws exactly one
  pre-emptive **Overwatch shot** that routes through the normal tag path; the
  resulting ``tag``/``miss`` event carries ``metadata["overwatch"] is True`` and
  a normal deliberate tag does not.
* Collection / LoS-cross / dedupe consume no RNG; only the resolved shot does.

All tests are pure-unit: hand-built ``PlayerState`` + ``MapContext.from_dict``,
no test DB, no real ArenaMap. Shot resolution is pinned with ``random.seed(42)``.
"""

import random

import pytest

from matches.sim_helpers.combat import _ACTION_IDX, _CHOICES, plan_action
from matches.sim_helpers.map_context import MapContext
from matches.sim_helpers.player_state import PlayerState
from matches.simulation import BatchSimulator

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _ps(role, team_color="red", **kwargs):
    """Lightweight in-memory PlayerState with unit-test defaults."""
    tag_id = kwargs.pop("tag_id", f"{team_color}_{role}")
    defaults = dict(
        tag_id=tag_id,
        name=f"{team_color} {role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=0,
        player_awareness=0,
        starting_lives=10,
        starting_shots=20,
        final_lives=10,
        final_shots=20,
    )
    defaults.update(kwargs)
    return PlayerState(**defaults)


def _ctx(sight_data=None, adj=None, zone_data=None, spawn_cells=None):
    """Hand-built MapContext via the documented from_dict bridge."""
    return MapContext.from_dict(
        {
            "sight_data": sight_data,
            "adj": adj if adj is not None else {},
            "zone_data": zone_data,
            "spawn_cells": spawn_cells if spawn_cells is not None else {},
        }
    )


def _force_choice(monkeypatch, action):
    """Make random.choices deterministically return *action* (no RNG spent)."""
    import matches.sim_helpers.combat as combat_mod

    monkeypatch.setattr(
        combat_mod.random, "choices", lambda choices, weights=None: [action]
    )


def _grid_ctx():
    """A small 1x4 floor corridor: cells (0,0)-(0,3) all walkable.

    sight_data is crafted so the holder at (0,3) sees ONLY (0,1) — the
    middle of the corridor — and neither endpoint (0,0) nor (0,3-mover-end).
    """
    adj = {
        (0, 0): [(0, 1)],
        (0, 1): [(0, 0), (0, 2)],
        (0, 2): [(0, 1), (0, 3)],
        (0, 3): [(0, 2)],
    }
    zone_data = [[1, 1, 1, 1]]
    return adj, zone_data


# ---------------------------------------------------------------------------
# Action slot
# ---------------------------------------------------------------------------


class TestHoldActionSlot:
    """The 9th Action slot: index 8, in _CHOICES, baseline array length 9."""

    def test_hold_action_index_is_8(self):
        assert _ACTION_IDX["hold"] == 8

    def test_hold_in_choices(self):
        assert "hold" in _CHOICES

    def test_plan_action_baseline_array_length_is_9(self, monkeypatch):
        """plan_action builds a 9-element weight vector (slot 8 = hold)."""
        captured = {}

        import matches.sim_helpers.combat as combat_mod

        def fake_choices(choices, weights=None):
            captured["weights"] = list(weights)
            captured["choices"] = list(choices)
            return ["hide"]

        monkeypatch.setattr(combat_mod.random, "choices", fake_choices)

        p = _ps("scout", cell_row=None, cell_col=None)
        plan_action(p, [p], 0, None)

        assert len(captured["weights"]) == 9
        assert len(captured["choices"]) == 9

    def test_forced_hold_choice_yields_hold_plan(self, monkeypatch):
        _force_choice(monkeypatch, "hold")
        p = _ps("scout")
        plans = plan_action(p, [p], 0, None)
        assert {"type": "hold", "actor": p} in plans


# ---------------------------------------------------------------------------
# is_holding carry-over (mirrors is_hiding)
# ---------------------------------------------------------------------------


class TestIsHoldingCarryOver:
    """ADR-0009 decision 2: hold carries over until a non-hold roll or a Down."""

    def test_fresh_object_defaults_false_via_getattr(self):
        p = _ps("scout")
        assert getattr(p, "is_holding", False) is False

    def test_hold_roll_sets_is_holding(self, monkeypatch):
        _force_choice(monkeypatch, "hold")
        p = _ps("scout")
        plan_action(p, [p], 0, None)
        assert getattr(p, "is_holding", False) is True

    def test_is_holding_stays_true_across_consecutive_hold(self, monkeypatch):
        _force_choice(monkeypatch, "hold")
        p = _ps("scout")
        plan_action(p, [p], 0, None)
        plan_action(p, [p], 1, None)
        assert getattr(p, "is_holding", False) is True

    def test_is_holding_cleared_when_next_action_not_hold(self, monkeypatch):
        p = _ps("scout")
        _force_choice(monkeypatch, "hold")
        plan_action(p, [p], 0, None)
        assert getattr(p, "is_holding", False) is True
        # Next tick rolls a non-hold action → carry-over must end.
        _force_choice(monkeypatch, "only_move")
        plan_action(p, [p], 1, None)
        assert getattr(p, "is_holding", False) is False


# ---------------------------------------------------------------------------
# Down clears hold (and still clears _path_cache)
# ---------------------------------------------------------------------------


class TestRecordDownClearsHold:
    """ADR-0009 decision 2: the life-loss clear hangs off record_down.

    Shot-resolver consolidation lifted ``_record_down`` to
    ``sim_helpers.down.record_down`` as a pure function; these tests
    target the new entry point. The MOVE-03 ``is_holding`` clear is
    the load-bearing assertion here.
    """

    def test_record_down_clears_is_holding(self):
        from matches.sim_helpers.down import record_down

        p = _ps("scout")
        p.is_holding = True
        p._path_cache = (("g",), [(0, 1)], (0, 0))
        record_down(p, 12, ctx=None)
        assert getattr(p, "is_holding", False) is False

    def test_record_down_still_clears_path_cache(self):
        from matches.sim_helpers.down import record_down

        p = _ps("scout")
        p.is_holding = True
        p._path_cache = (("g",), [(0, 1)], (0, 0))
        record_down(p, 12, ctx=None)
        assert p._path_cache is None

    def test_record_down_still_stamps_last_downed_time(self):
        from matches.sim_helpers.down import record_down

        p = _ps("scout")
        record_down(p, 33, ctx=None)
        assert p.last_downed_time == 33


# ---------------------------------------------------------------------------
# Stationary: a holding player does NOT Advance
# ---------------------------------------------------------------------------


class TestHoldIsStationary:
    """ADR-0009 decision 3: is_holding joins the _advance_player stationary set.

    ResourceBasedSimulator._advance_player uses ORM PlayerRoundState + a DB
    ``player.save()`` inside _move_to_cell, so a faithful pure-unit RBS variant
    is not feasible without a test DB; ADR-0009 decision 4 also scopes Overwatch
    resolution to BatchSim only. We therefore assert the Stationary contract on
    BatchSimulator only (the task's explicit "else BatchSim only" fallback).
    """

    def setup_method(self):
        self.sim = BatchSimulator()
        self.adj, self.zone_data = _grid_ctx()
        self.ctx = _ctx(
            sight_data={},
            adj=self.adj,
            zone_data=self.zone_data,
            spawn_cells={"red": (0, 0), "blue": (0, 3)},
        )

    def test_non_holding_control_moves(self):
        """Control: a normal non-holding player does Advance toward its goal."""
        p = _ps("scout", cell_row=0, cell_col=0, speed=100)
        p.is_holding = False
        self.sim._advance_player(p, [p], 5, self.ctx, "")
        moved = (p.cell_row, p.cell_col) != (0, 0)
        assert moved, "non-holding control should have advanced from (0,0)"
        assert p.movement_trail, "control should have a movement_trail entry"

    def test_holding_player_does_not_move(self):
        p = _ps("scout", cell_row=0, cell_col=0, speed=100)
        p.is_holding = True
        self.sim._advance_player(p, [p], 5, self.ctx, "")
        assert (p.cell_row, p.cell_col) == (0, 0), "holder must stay anchored"
        assert not p.movement_trail, "holder must not append a movement_trail step"


# ---------------------------------------------------------------------------
# Overwatch trigger (basic): enemy Advances into holder LoS
# ---------------------------------------------------------------------------


class TestOverwatchTriggerBasic:
    """An enemy whose Advance ENDS in the holder's LoS draws an Overwatch shot.

    Contract surface: the BatchSim tick loop runs an Overwatch collection step
    (after Advances are applied) that, for each holding player, produces a
    tag_attempt against every enemy now in (or having crossed) its LoS. The
    attempt carries an ``"overwatch": True`` marker; ``_resolve_tag_attempts``
    emits the resulting ``tag``/``miss`` event with
    ``metadata["overwatch"] is True``.
    """

    def setup_method(self):
        self.sim = BatchSimulator()
        # Holder at (0,3) sees (0,2) and (0,3). Mover ends its Advance at (0,2).
        self.ctx = _ctx(
            sight_data={
                "0,3": frozenset(["0,2", "0,3"]),
                "0,2": frozenset(["0,3"]),
            }
        )

    def test_collection_produces_overwatch_attempt(self):
        """The Overwatch collection step builds one overwatch tag_attempt."""
        holder = _ps("scout", team_color="red", cell_row=0, cell_col=3)
        holder.is_holding = True
        enemy = _ps("scout", team_color="blue", cell_row=0, cell_col=2)

        attempts = self.sim._collect_overwatch_attempts([holder, enemy], 10, self.ctx)
        assert any(
            a.get("attacker") is holder
            and a.get("defender") is enemy
            and a.get("overwatch") is True
            for a in attempts
        ), f"expected an overwatch attempt holder→enemy, got {attempts!r}"

    def test_resolved_event_carries_overwatch_metadata(self):
        holder = _ps("scout", team_color="red", cell_row=0, cell_col=3)
        holder.is_holding = True
        enemy = _ps("scout", team_color="blue", cell_row=0, cell_col=2)

        random.seed(42)
        attempts = self.sim._collect_overwatch_attempts([holder, enemy], 10, self.ctx)
        log = []
        self.sim._resolve_tag_attempts(
            attempts, 10, log, movement_ctx=self.ctx, all_alive=[holder, enemy]
        )
        ow_events = [
            e
            for e in log
            if e["event_type"] in ("tag", "miss")
            and e.get("metadata", {}).get("overwatch") is True
        ]
        assert ow_events, f"expected a tag/miss with overwatch metadata, log={log!r}"

    def test_holder_did_not_advance(self):
        """A holder fires Overwatch in place — it never Advances on its turn."""
        adj = {(0, 2): [(0, 3)], (0, 3): [(0, 2)]}
        ctx = _ctx(
            sight_data={"0,3": frozenset(["0,2", "0,3"])},
            adj=adj,
            zone_data=[[1, 1, 1, 1]],
            spawn_cells={"red": (0, 0), "blue": (0, 3)},
        )
        holder = _ps("scout", team_color="red", cell_row=0, cell_col=3, speed=100)
        holder.is_holding = True
        self.sim._advance_player(holder, [holder], 10, ctx, "")
        assert (holder.cell_row, holder.cell_col) == (0, 3)


# ---------------------------------------------------------------------------
# MANDATED EDGE CASE: multi-cell Advance, only an intermediate cell in LoS
# ---------------------------------------------------------------------------


class TestOverwatchThroughIntermediateCell:
    """The key guarantee (ADR-0009 decision 4).

    Enemy performs ONE multi-cell Advance where:
      * START cell (0,0) is NOT in the holder's LoS,
      * END cell (0,3) is NOT in the holder's LoS,
      * an INTERMEDIATE traversed cell (0,1) or (0,2) IS in the holder's LoS.

    The traversed cells must be read from the cells astar_advance_cached pops
    off the committed route this tick (the path-commitment cache), so we
    hand-seed ``enemy._path_cache`` to make the route deterministic and assert
    ≥1 Overwatch shot results for that holder/mover pair.
    """

    def setup_method(self):
        self.sim = BatchSimulator()
        self.adj, self.zone_data = _grid_ctx()
        # Holder at (0,3) sees ONLY the intermediate cell (0,1).
        # It does NOT see (0,0) [start] nor (0,3) end region beyond (0,1).
        # spawn_cells: the blue mover's Goal cell defaults to its enemy
        # (red) base; choose_goal_cell is consulted every tick (ADR-0008), so
        # red base must be (0,3) for the mover to traverse (0,0)->(0,3) and
        # consume its seeded path cache.
        self.ctx = _ctx(
            sight_data={
                "0,3": frozenset(["0,1"]),
                "0,1": frozenset(["0,3"]),
            },
            adj=self.adj,
            zone_data=self.zone_data,
            spawn_cells={"red": (0, 3), "blue": (0, 0)},
        )

    def _holder_and_mover(self):
        holder = _ps("heavy", team_color="red", cell_row=0, cell_col=3)
        holder.is_holding = True
        # Mover starts at (0,0), goal (0,3); speed high enough to traverse the
        # whole corridor in one tick so neither endpoint is in LoS.
        mover = _ps("scout", team_color="blue", cell_row=0, cell_col=0, speed=100)
        # Hand-seed the path-commitment cache: 3-tuple (goal, remaining, anchor).
        # remaining = the route still to walk, head = next cell, ends at goal.
        mover._path_cache = ((0, 3), [(0, 1), (0, 2), (0, 3)], (0, 0))
        return holder, mover

    def test_neither_endpoint_is_in_holder_los(self):
        """Sanity: confirm the fixture really hides both endpoints."""
        visible = self.ctx.sight_data["0,3"]
        assert "0,0" not in visible, "start must be out of LoS for this edge case"
        assert "0,3" not in visible, "end must be out of LoS for this edge case"
        assert "0,1" in visible, "intermediate cell must be in LoS"

    def test_multi_cell_advance_through_los_draws_overwatch_shot(self):
        holder, mover = self._holder_and_mover()
        # Apply the mover's multi-cell Advance (consumes the seeded cache).
        self.sim._advance_player(mover, [holder, mover], 20, self.ctx, "")
        # Endpoint sanity: it really moved past the visible cell to a hidden end.
        assert (mover.cell_row, mover.cell_col) == (0, 3), (
            f"mover should have traversed the full corridor, "
            f"at {(mover.cell_row, mover.cell_col)}"
        )
        attempts = self.sim._collect_overwatch_attempts([holder, mover], 20, self.ctx)
        ow = [
            a
            for a in attempts
            if a.get("attacker") is holder
            and a.get("defender") is mover
            and a.get("overwatch") is True
        ]
        assert len(ow) >= 1, (
            "an enemy that Advanced THROUGH the holder's LoS (only an "
            f"intermediate cell visible) must draw >=1 Overwatch shot; got {attempts!r}"
        )


# ---------------------------------------------------------------------------
# Firing cap
# ---------------------------------------------------------------------------


class TestOverwatchFiringCap:
    """One Overwatch shot per tick for normal roles; per-crossing for rapid Scout.

    Also: no overwatch attempt at all when the holder is out of shots, on
    shot-cooldown, or not active/targetable.
    """

    def setup_method(self):
        self.sim = BatchSimulator()
        # Holder at (0,0) sees both enemy cells (0,1) and (0,2).
        self.ctx = _ctx(
            sight_data={
                "0,0": frozenset(["0,1", "0,2"]),
                "0,1": frozenset(["0,0"]),
                "0,2": frozenset(["0,0"]),
            }
        )

    def _two_enemies(self):
        e1 = _ps(
            "scout", team_color="blue", tag_id="blue_scout_1", cell_row=0, cell_col=1
        )
        e2 = _ps(
            "scout", team_color="blue", tag_id="blue_scout_2", cell_row=0, cell_col=2
        )
        return e1, e2

    def test_normal_role_caps_at_one_attempt_per_tick(self):
        holder = _ps("heavy", team_color="red", cell_row=0, cell_col=0, final_shots=20)
        holder.is_holding = True
        e1, e2 = self._two_enemies()
        attempts = self.sim._collect_overwatch_attempts([holder, e1, e2], 30, self.ctx)
        ow = [a for a in attempts if a.get("overwatch") is True]
        assert len(ow) == 1, (
            f"a normal-role holder fires at most ONE Overwatch shot per tick "
            f"even with two enemies in LoS; got {ow!r}"
        )

    def test_rapid_fire_scout_fires_per_crossing_enemy(self):
        holder = _ps(
            "scout",
            team_color="red",
            cell_row=0,
            cell_col=0,
            final_shots=20,
            special_active_until=999,
        )
        holder.is_holding = True
        e1, e2 = self._two_enemies()
        attempts = self.sim._collect_overwatch_attempts([holder, e1, e2], 30, self.ctx)
        ow = [a for a in attempts if a.get("overwatch") is True]
        assert len(ow) == 2, (
            "a rapid-fire Scout holder (special active) may fire one Overwatch "
            f"shot per crossing enemy; got {ow!r}"
        )

    def test_holder_with_zero_shots_produces_no_attempt(self):
        holder = _ps("heavy", team_color="red", cell_row=0, cell_col=0, final_shots=0)
        holder.is_holding = True
        e1, _ = self._two_enemies()
        attempts = self.sim._collect_overwatch_attempts([holder, e1], 30, self.ctx)
        assert not [a for a in attempts if a.get("overwatch") is True]

    def test_holder_on_shot_cooldown_produces_no_attempt(self):
        # Heavy cooldown is 1.0 s → 2 ticks. last_shot_time just now → blocked.
        holder = _ps(
            "heavy",
            team_color="red",
            cell_row=0,
            cell_col=0,
            final_shots=20,
            last_shot_time=30,
        )
        holder.is_holding = True
        e1, _ = self._two_enemies()
        attempts = self.sim._collect_overwatch_attempts([holder, e1], 30, self.ctx)
        assert not [a for a in attempts if a.get("overwatch") is True]

    def test_holder_not_active_produces_no_attempt(self):
        holder = _ps(
            "heavy",
            team_color="red",
            cell_row=0,
            cell_col=0,
            final_shots=20,
            last_downed_time=29,
        )
        holder.is_holding = True
        # last_downed_time=29, tick=30 → within RESPAWN/NOT_TARGETABLE window.
        assert not holder.is_taggable_at(30)
        e1, _ = self._two_enemies()
        attempts = self.sim._collect_overwatch_attempts([holder, e1], 30, self.ctx)
        assert not [a for a in attempts if a.get("overwatch") is True]


# ---------------------------------------------------------------------------
# No-RNG collection / determinism
# ---------------------------------------------------------------------------


class TestOverwatchCollectionConsumesNoRNG:
    """ADR-0009 consequences: the LoS-cross check + dedupe consume no RNG.

    Only the resolved Overwatch shot spends RNG (through the tag path).
    """

    def setup_method(self):
        self.sim = BatchSimulator()
        self.ctx = _ctx(
            sight_data={
                "0,0": frozenset(["0,1", "0,2"]),
                "0,1": frozenset(["0,0"]),
                "0,2": frozenset(["0,0"]),
            }
        )

    def test_collection_does_not_advance_rng_state(self):
        holder = _ps(
            "scout",
            team_color="red",
            cell_row=0,
            cell_col=0,
            special_active_until=999,
            final_shots=20,
        )
        holder.is_holding = True
        e1 = _ps(
            "scout", team_color="blue", tag_id="blue_scout_1", cell_row=0, cell_col=1
        )
        e2 = _ps(
            "scout", team_color="blue", tag_id="blue_scout_2", cell_row=0, cell_col=2
        )

        random.seed(42)
        before = random.getstate()
        self.sim._collect_overwatch_attempts([holder, e1, e2], 40, self.ctx)
        after = random.getstate()
        assert before == after, "Overwatch collection must not consume RNG"

    def test_collection_is_idempotent_across_two_runs(self):
        holder = _ps("heavy", team_color="red", cell_row=0, cell_col=0, final_shots=20)
        holder.is_holding = True
        e1 = _ps("scout", team_color="blue", cell_row=0, cell_col=1)

        random.seed(42)
        a1 = self.sim._collect_overwatch_attempts([holder, e1], 40, self.ctx)
        a2 = self.sim._collect_overwatch_attempts([holder, e1], 40, self.ctx)
        # Same attacker/defender/overwatch tuples both times (order-stable).
        key = lambda lst: [
            (a["attacker"].tag_id, a["defender"].tag_id, a.get("overwatch"))
            for a in lst
        ]
        assert key(a1) == key(a2)


# ---------------------------------------------------------------------------
# Event provenance
# ---------------------------------------------------------------------------


class TestOverwatchEventProvenance:
    """A deliberate tag is NOT flagged overwatch; scoring is identical."""

    def setup_method(self):
        self.sim = BatchSimulator()

    def test_normal_tag_has_no_overwatch_metadata(self):
        attacker = _ps("commander", team_color="red", final_shots=20)
        defender = _ps("scout", team_color="blue", survival=0)
        defender.shields = 3  # commander shot_power 2 → not downed
        random.seed(42)
        log = []
        self.sim._resolve_tag_attempts(
            [{"attacker": attacker, "defender": defender}], 7, log
        )
        tags = [e for e in log if e["event_type"] in ("tag", "miss")]
        assert tags, "expected a tag/miss event"
        for e in tags:
            assert e.get("metadata", {}).get("overwatch") is not True, (
                "a deliberate (non-overwatch) tag must NOT carry "
                "metadata['overwatch'] is True"
            )

    def test_overwatch_hit_scores_identically_to_normal_tag(self):
        """event_type stays 'tag' and points are the normal +100 / -20."""
        ctx = _ctx(sight_data={"0,0": frozenset(["0,1"]), "0,1": frozenset(["0,0"])})
        holder = _ps(
            "commander",
            team_color="red",
            cell_row=0,
            cell_col=0,
            final_shots=20,
            accuracy=100,
        )
        holder.is_holding = True
        enemy = _ps("scout", team_color="blue", cell_row=0, cell_col=1, survival=0)
        enemy.shields = 3  # not downed by one commander shot

        random.seed(42)
        attempts = self.sim._collect_overwatch_attempts([holder, enemy], 12, ctx)
        log = []
        self.sim._resolve_tag_attempts(
            attempts, 12, log, movement_ctx=ctx, all_alive=[holder, enemy]
        )
        hits = [e for e in log if e["event_type"] == "tag"]
        assert hits, f"expected an overwatch tag hit (seed 42); log={log!r}"
        ev = hits[0]
        assert ev["event_type"] == "tag", "Overwatch hit reuses event_type 'tag'"
        assert ev["points_awarded"] == 100, "Overwatch tag scores the normal +100"
        assert ev["metadata"].get("overwatch") is True
        assert holder.points_scored == 100
        assert enemy.points_scored == -20
