"""GEN-01 — three persistence-fidelity tiers off one seed.

These tests pin the GEN-01 seam contract (§7) for the
``scores`` ⊂ ``combat`` ⊂ ``full`` persistence-fidelity tiers and the
``BatchSimulator.ensure_fidelity`` lazy-upgrade primitive.

The locked design (contract §0):

- ``scores`` — ``GameRound`` + ``PlayerRoundState`` only (final scoreboard);
  **0** combat events, **0** movement events, ``cell_occupancy_json is None``,
  ``highlights_json is None``; every persisted round (every tier) also stores a
  non-null ``roster_snapshot_json`` carrying the 13 sim-stat inputs per side.
- ``combat`` — ``+`` combat ``GameEvent`` rows ``+`` ``highlights_json`` (but
  **0** movement events, ``cell_occupancy_json`` still null).
- ``full`` — ``+`` movement ``GameEvent`` rows ``+`` per-Advance route ``+``
  ``cell_occupancy_json``.

``ensure_fidelity(game_round, target)`` re-simulates from
``(rng_seed + roster_snapshot_json + arena_map)`` — reading the snapshot, NOT
live ``Team.active_roster`` — and backfills the missing higher-tier rows onto
the EXISTING row, bumping ``fidelity``. Idempotent.

**TDD note:** these tests are authored against the approved seam contract in
parallel with the Code agent, so the whole module is EXPECTED to fail (import
errors on ``FIDELITY_RANK`` / ``ensure_fidelity`` / the ``fidelity`` kwarg,
etc.) until the Code agent's work lands. That is fine — the tests pin the
schema-level contract, never exact simulated point totals (except where a row
is compared to ITSELF off the same seed, which is valid).
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from matches.models import GameEvent, GameRound
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# Module-level fast cap (mirrors test_simulation_view_paths.py). Fidelity is a
# persistence concern — the tick loop always runs in full — so a tiny round is
# semantically identical for these schema-level assertions and keeps the suite
# fast.
_FAST_TICKS = 40

# The 13 sim-stat keys the roster snapshot must carry per player (contract §1c;
# == entrypoints._SIMULATION_STATS).
_SNAPSHOT_STATS = {
    "accuracy",
    "survival",
    "player_awareness",
    "game_awareness",
    "decision_making",
    "stamina",
    "special_usage",
    "resupply_efficiency",
    "resupply_synergy",
    "teamwork",
    "communication",
    "resource_awareness",
    "speed",
}


# ---------------------------------------------------------------------------
# Shared minimal arena_map factory (DB-backed) — mirrors the helper in
# test_simulation_view_paths.py so the gating tests can drive a map-active
# round (where the ``full`` occupancy block actually fires).
# ---------------------------------------------------------------------------


def _make_minimal_arena_map(name: str = "Gen01Arena"):
    from core.models import (
        ArenaMap,
        BaseSightLineConfig,
        MapBaseConfig,
        MapZoneConfig,
        SightLineConfig,
    )
    from core.map_processing import compute_sight_lines

    zone_size = 50
    zone_data = [[1] * 4 for _ in range(4)]
    arena_map = ArenaMap.objects.create(
        name=name, img_width=4 * zone_size, img_height=4 * zone_size
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
        x_px=4 * zone_size - zone_size // 2,
        y_px=4 * zone_size - zone_size // 2,
    )
    SightLineConfig.objects.create(
        arena_map=arena_map,
        zone_size=zone_size,
        sight_data=compute_sight_lines(zone_data),
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map,
        base_type="red",
        zone_size=zone_size,
        visible_cells=[],
    )
    BaseSightLineConfig.objects.create(
        arena_map=arena_map,
        base_type="blue",
        zone_size=zone_size,
        visible_cells=[],
    )
    return arena_map, zone_size


# ---------------------------------------------------------------------------
# Per-fidelity GameEvent classification helpers
# ---------------------------------------------------------------------------

# The combat event types written at ``combat`` tier (contract §0: tag / missile
# / resupply / down / elimination / locking / missiled / etc.) — everything that
# is NOT a movement row.
_MOVEMENT_TYPE = "movement"


def _combat_events(game_round):
    return list(
        GameEvent.objects.filter(game_round=game_round).exclude(
            event_type=_MOVEMENT_TYPE
        )
    )


def _movement_events(game_round):
    return list(
        GameEvent.objects.filter(game_round=game_round, event_type=_MOVEMENT_TYPE)
    )


def _event_tuples(game_round):
    """Order-stable tuple view of a round's GameEvents for equivalence checks."""
    return [
        (e.event_type, e.timestamp, e.metadata)
        for e in GameEvent.objects.filter(game_round=game_round).order_by(
            "timestamp", "id"
        )
    ]


# ===========================================================================
# §7b — fidelity gating
# ===========================================================================


@pytest.mark.django_db
class TestFidelityGating:
    """Each tier persists exactly the rows the contract pins, and the
    ``roster_snapshot_json`` is always a non-null dict with the 13-key stats.
    """

    def _simulate(self, *, fidelity, arena_map=None, prefix="GatingMR"):
        random.seed(42)
        red, _ = make_team_with_slots(f"{prefix}R")
        blue, _ = make_team_with_slots(f"{prefix}B")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map, fidelity=fidelity
            )
        return gr

    def _assert_snapshot_well_formed(self, gr):
        snap = gr.roster_snapshot_json
        assert isinstance(snap, dict), (
            "roster_snapshot_json must be a non-null dict on every tier; got "
            f"{type(snap).__name__}"
        )
        assert set(snap) == {"red", "blue"}, snap.keys()
        for side in ("red", "blue"):
            assert (
                isinstance(snap[side], list) and snap[side]
            ), f"snapshot[{side!r}] must be a non-empty list"
            for entry in snap[side]:
                assert {"player_id", "name", "role", "stats"} <= set(entry), entry
                stats = entry["stats"]
                assert isinstance(stats, dict)
                # Exactly the 13 sim-stat keys, every value an int.
                assert set(stats) == _SNAPSHOT_STATS, set(stats) ^ _SNAPSHOT_STATS
                for v in stats.values():
                    assert isinstance(v, int), stats

    # -- scores -----------------------------------------------------------

    def test_scores_persists_scoreboard_only(self):
        gr = self._simulate(fidelity="scores", prefix="GateScores")
        assert gr.fidelity == "scores"
        assert len(_combat_events(gr)) == 0, "scores tier must write 0 combat events"
        assert (
            len(_movement_events(gr)) == 0
        ), "scores tier must write 0 movement events"
        assert gr.cell_occupancy_json is None
        assert gr.highlights_json is None
        self._assert_snapshot_well_formed(gr)

    def test_scores_on_map_round_still_scoreboard_only(self):
        """A map-active round flushed at ``scores`` still writes no events /
        no occupancy (the tier, not the map, gates the writes)."""
        arena_map, _zs = _make_minimal_arena_map("GateScoresMap")
        gr = self._simulate(
            fidelity="scores", arena_map=arena_map, prefix="GateScoresMap"
        )
        assert gr.fidelity == "scores"
        assert len(_combat_events(gr)) == 0
        assert len(_movement_events(gr)) == 0
        assert gr.cell_occupancy_json is None
        assert gr.highlights_json is None
        self._assert_snapshot_well_formed(gr)

    # -- combat -----------------------------------------------------------

    def test_combat_persists_events_and_highlights_but_no_movement(self):
        # A longer round so real combat reliably produces at least one event.
        random.seed(42)
        red, _ = make_team_with_slots("GateCombatR")
        blue, _ = make_team_with_slots("GateCombatB")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="combat"
            )
        assert gr.fidelity == "combat"
        assert len(_combat_events(gr)) > 0, "combat tier must persist combat events"
        assert (
            gr.highlights_json is not None
        ), "combat tier must persist highlights_json"
        assert isinstance(gr.highlights_json, list)
        # Movement + occupancy stay absent at combat.
        assert (
            len(_movement_events(gr)) == 0
        ), "combat tier must write 0 movement events"
        assert gr.cell_occupancy_json is None

    # -- full -------------------------------------------------------------

    def test_full_on_map_round_persists_everything(self):
        arena_map, _zs = _make_minimal_arena_map("GateFullMap")
        random.seed(42)
        red, _ = make_team_with_slots("GateFullR")
        blue, _ = make_team_with_slots("GateFullB")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map, fidelity="full"
            )
        assert gr.fidelity == "full"
        assert len(_combat_events(gr)) > 0
        assert gr.highlights_json is not None and isinstance(gr.highlights_json, list)
        assert (
            len(_movement_events(gr)) > 0
        ), "full tier on a map-active round must persist movement events"
        assert (
            gr.cell_occupancy_json is not None
        ), "full tier on a map-active round must populate cell_occupancy_json"
        assert isinstance(gr.cell_occupancy_json, dict)

    def test_full_without_map_leaves_occupancy_null(self):
        """A map-less ``full`` round still has ``cell_occupancy_json is None``
        (the ``movement_ctx is not None`` gate holds — contract §7b edge)."""
        gr = self._simulate(fidelity="full", arena_map=None, prefix="GateFullNoMap")
        assert gr.fidelity == "full"
        # cell_occupancy stays null with no map even at full fidelity.
        assert (
            gr.cell_occupancy_json is None
        ), "a map-less full round must leave cell_occupancy_json NULL"
        # Highlights still populate (combat tier is included by full).
        assert gr.highlights_json is not None


# ===========================================================================
# §7a — the load-bearing equivalence test
# ===========================================================================


@pytest.mark.django_db
class TestFidelityEquivalence:
    """A round flushed DIRECTLY at ``full`` vs the same seed+snapshot flushed at
    ``scores`` then ``ensure_fidelity("full")`` ⇒ IDENTICAL combat + movement
    ``GameEvent`` tuples, ``cell_occupancy_json``, and ``highlights_json``.

    Construction (contract §7a): run a ``full`` round, capture its persisted
    ``rng_seed`` + ``roster_snapshot_json``, hand-build a ``scores`` ``GameRound``
    carrying that SAME seed + snapshot (+ same arena_map), then upgrade it.
    Because ``ensure_fidelity`` re-seeds from the stored ``rng_seed`` and reads
    the stored snapshot + the round's ``arena_map``, the re-sim is byte-identical
    to the direct-``full`` run.
    """

    def _build_scores_twin(self, gr_full, *, arena_map):
        """A ``fidelity="scores"`` GameRound carrying gr_full's seed+snapshot."""
        twin = GameRound.objects.create(
            team_red=gr_full.team_red,
            team_blue=gr_full.team_blue,
            round_number=1,
            red_points=gr_full.red_points,
            blue_points=gr_full.blue_points,
            rng_seed=gr_full.rng_seed,
            arena_map=arena_map,
            zone_size=gr_full.zone_size,
            fidelity="scores",
            roster_snapshot_json=gr_full.roster_snapshot_json,
        )
        return twin

    def _run(self, *, arena_map, ticks):
        random.seed(7)
        red, _ = make_team_with_slots("EquivR")
        blue, _ = make_team_with_slots("EquivB")
        with patch.object(BatchSimulator, "ROUND_TICKS", ticks):
            gr_full = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map, fidelity="full"
            )
        twin = self._build_scores_twin(gr_full, arena_map=arena_map)
        with patch.object(BatchSimulator, "ROUND_TICKS", ticks):
            upgraded = BatchSimulator().ensure_fidelity(twin, "full")
        return gr_full, upgraded

    def test_map_round_full_equals_scores_then_upgrade(self):
        arena_map, _zs = _make_minimal_arena_map("EquivMap")
        gr_full, upgraded = self._run(arena_map=arena_map, ticks=400)

        assert upgraded.fidelity == "full"
        # Identical GameEvent tuples (event_type / timestamp / metadata),
        # including movement rows on the map path.
        assert _event_tuples(upgraded) == _event_tuples(
            gr_full
        ), "upgraded scores->full round must reproduce the direct-full event log"
        assert upgraded.cell_occupancy_json == gr_full.cell_occupancy_json
        assert upgraded.highlights_json == gr_full.highlights_json

    def test_mapless_round_full_equals_scores_then_upgrade(self):
        gr_full, upgraded = self._run(arena_map=None, ticks=400)

        assert upgraded.fidelity == "full"
        assert _event_tuples(upgraded) == _event_tuples(gr_full)
        # Both map-less rounds leave occupancy null.
        assert upgraded.cell_occupancy_json is None
        assert gr_full.cell_occupancy_json is None
        assert upgraded.highlights_json == gr_full.highlights_json


# ===========================================================================
# §7c — idempotency
# ===========================================================================


@pytest.mark.django_db
class TestEnsureFidelityIdempotency:
    """``ensure_fidelity`` is a no-op at or above the target, and upgrading
    twice writes no duplicate rows."""

    def _scores_round(self, *, prefix="Idem"):
        random.seed(42)
        red, _ = make_team_with_slots(f"{prefix}R")
        blue, _ = make_team_with_slots(f"{prefix}B")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="scores"
            )
        return gr

    def _full_round(self, *, prefix="IdemFull"):
        random.seed(42)
        red, _ = make_team_with_slots(f"{prefix}R")
        blue, _ = make_team_with_slots(f"{prefix}B")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )
        return gr

    def test_noop_at_target(self):
        gr = self._full_round(prefix="IdemAt")
        before = GameEvent.objects.filter(game_round=gr).count()
        out = BatchSimulator().ensure_fidelity(gr, "full")
        out.refresh_from_db()
        assert out.fidelity == "full"
        assert GameEvent.objects.filter(game_round=gr).count() == before

    def test_noop_above_target(self):
        gr = self._full_round(prefix="IdemAbove")
        before = GameEvent.objects.filter(game_round=gr).count()
        out = BatchSimulator().ensure_fidelity(gr, "combat")
        out.refresh_from_db()
        # Downgrade is never performed — the row stays full.
        assert out.fidelity == "full"
        assert GameEvent.objects.filter(game_round=gr).count() == before

    def test_double_upgrade_writes_no_duplicate_rows(self):
        gr = self._scores_round(prefix="IdemDouble")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            BatchSimulator().ensure_fidelity(gr, "full")
        gr.refresh_from_db()
        assert gr.fidelity == "full"
        first_events = GameEvent.objects.filter(game_round=gr).count()
        first_occ = gr.cell_occupancy_json
        first_hl = gr.highlights_json

        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            BatchSimulator().ensure_fidelity(gr, "full")
        gr.refresh_from_db()
        # Second call is a pure no-op: no duplicate event/occupancy/highlight.
        assert GameEvent.objects.filter(game_round=gr).count() == first_events
        assert gr.cell_occupancy_json == first_occ
        assert gr.highlights_json == first_hl


# ===========================================================================
# §7c — snapshot-None defensive no-op
# ===========================================================================


@pytest.mark.django_db
class TestEnsureFidelitySnapshotGuard:
    """A hand-built ``fidelity="scores"`` row with ``roster_snapshot_json=None``
    is unupgradeable: ``ensure_fidelity("full")`` returns it unchanged, writes
    nothing, and does not crash (contract §5 defensive guard)."""

    def test_snapshot_none_is_returned_unchanged(self):
        red, _ = make_team_with_slots("SnapGuardR")
        blue, _ = make_team_with_slots("SnapGuardB")
        gr = GameRound.objects.create(
            team_red=red,
            team_blue=blue,
            round_number=1,
            red_points=10,
            blue_points=5,
            rng_seed=12345,
            fidelity="scores",
            roster_snapshot_json=None,
        )
        events_before = GameEvent.objects.filter(game_round=gr).count()

        out = BatchSimulator().ensure_fidelity(gr, "full")

        assert out.pk == gr.pk
        out.refresh_from_db()
        # No crash, no rows written, fidelity untouched.
        assert out.fidelity == "scores"
        assert out.cell_occupancy_json is None
        assert out.highlights_json is None
        assert GameEvent.objects.filter(game_round=gr).count() == events_before


# ===========================================================================
# §7d — roster-snapshot faithfulness
# ===========================================================================


@pytest.mark.django_db
class TestRosterSnapshotFaithfulness:
    """The upgrade re-sims from ``roster_snapshot_json``, NOT live
    ``Team.active_roster`` — so mutating the live ``Player`` stats AFTER a
    ``scores`` flush does not change what the upgrade reproduces. The stored
    scoreboard (``GameRound`` points / ``PlayerRoundState`` rows) is unchanged
    by the upgrade, proving the snapshot (not live stats) drives the re-sim."""

    def test_upgrade_reads_snapshot_not_mutated_live_stats(self):
        random.seed(42)
        red, players = make_team_with_slots("FaithR")
        blue, _ = make_team_with_slots("FaithB")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="scores"
            )

        # Capture the frozen scoreboard before the upgrade.
        red_points = gr.red_points
        blue_points = gr.blue_points
        states_before = {
            s.player_id: (s.points_scored, s.tags_made, s.was_eliminated_at)
            for s in gr.player_states.all()
        }

        # MUTATE live Player stats to wildly different values.
        from teams.models import Player

        for p in Player.objects.filter(team=red):
            p.accuracy = 1
            p.survival = 99
            p.speed = 1
            p.save()

        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            upgraded = BatchSimulator().ensure_fidelity(gr, "full")
        upgraded.refresh_from_db()

        # Scoreboard byte-identical — the upgrade re-sim read the snapshot, not
        # the now-mutated live stats, and MUST NOT have rewritten the scoreboard.
        assert upgraded.red_points == red_points
        assert upgraded.blue_points == blue_points
        states_after = {
            s.player_id: (s.points_scored, s.tags_made, s.was_eliminated_at)
            for s in upgraded.player_states.all()
        }
        assert states_after == states_before, (
            "ensure_fidelity must not rewrite PlayerRoundState; the re-sim off "
            "the stored snapshot must reproduce the original scoreboard despite "
            "the mutated live Player stats"
        )
        assert upgraded.fidelity == "full"
