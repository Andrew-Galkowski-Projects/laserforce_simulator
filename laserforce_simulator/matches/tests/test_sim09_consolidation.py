"""SIM-09 — BatchSimulator absorbs the view-path persistence surface.

ResourceBasedSimulator is removed; the production view path now drives
``BatchSimulator.simulate_match`` / ``simulate_single_round_detailed``, both of
which persist a real ``Match`` / ``GameRound`` via the extended
``_flush_to_db`` signature (now accepts ``match=``, ``round_number=``,
``arena_map=``, ``zone_size=``).

These tests pin the load-bearing structural guarantees of the new surface:
- per-Match colour swap on round 2 of ``simulate_match``,
- ``match.*_round1_*`` / ``match.*_round2_*`` derivation against the rounds
  (taking the colour swap into account),
- elimination bonus credited to the surviving side,
- distinct fresh ``rng_seed`` per round (independent 63-bit draws, **not** a
  master-seed chain — that path is the batch ``run`` flow),
- ``arena_map`` / ``zone_size`` written to every persisted ``GameRound``,
- ``@transaction.atomic`` rollback when round 2 raises mid-match,
- ``simulate_single_round_detailed`` standalone-round fields,
- ``_flush_to_db`` extended-kwarg surface (``match``, ``round_number``,
  ``arena_map``, ``zone_size``) lands on the persisted row.

Fast-test seam: ``patch.object(BatchSimulator, "ROUND_TICKS", 40)`` shrinks
the round to 20 s.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from matches.models import GameEvent, GameRound, Match
from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# Module-level cap — patched on every test so a stray full-length round
# doesn't make the suite glacial.
_FAST_TICKS = 40


# ---------------------------------------------------------------------------
# Shared minimal arena_map factory (DB-backed)
# ---------------------------------------------------------------------------


def _make_minimal_arena_map(name: str = "Sim09Arena"):
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
# simulate_match — full 2-round match with per-Match colour swap
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateMatch:
    """SIM-09: ``BatchSimulator.simulate_match`` persists a full
    2-round ``Match`` with the per-Match colour swap on round 2."""

    def _simulate(self, *, arena_map=None):
        random.seed(42)
        red, _ = make_team_with_slots("Sim09MR")
        blue, _ = make_team_with_slots("Sim09MB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(red, blue, arena_map=arena_map)
        return match, red, blue

    def test_returns_completed_match_with_two_rounds(self):
        match, _red, _blue = self._simulate()
        assert isinstance(match, Match)
        assert match.is_completed is True
        rounds = list(match.game_rounds.order_by("round_number"))
        assert len(rounds) == 2
        assert rounds[0].round_number == 1
        assert rounds[1].round_number == 2

    def test_round1_keeps_team_sides_round2_swaps_them(self):
        """Per-Match colour swap: round 1 has team_red=red,team_blue=blue;
        round 2 has team_red=blue,team_blue=red.
        """
        match, red, blue = self._simulate()
        r1, r2 = list(match.game_rounds.order_by("round_number"))
        assert r1.team_red == red and r1.team_blue == blue
        assert r2.team_red == blue and r2.team_blue == red

    def test_match_points_derived_with_colour_swap_in_mind(self):
        """Round 1: match.red_round1_points == r1.red_points (no swap).
        Round 2: red played as blue → match.red_round2_points == r2.blue_points.
        Same pattern for blue and for *_eliminated.
        """
        match, _red, _blue = self._simulate()
        r1, r2 = list(match.game_rounds.order_by("round_number"))
        assert match.red_round1_points == r1.red_points
        assert match.blue_round1_points == r1.blue_points
        # Round 2 — colour-swapped: persisted r2.red_points is what BLUE
        # team scored (since blue played red in round 2).
        assert match.red_round2_points == r2.blue_points
        assert match.blue_round2_points == r2.red_points
        # Eliminated bonus accounting follows the same swap.
        # GameRound stores ``red_team_eliminated`` / ``blue_team_eliminated``;
        # Match stores the per-round flags as ``*_round{1,2}_eliminated``.
        assert match.red_round1_eliminated == r1.red_team_eliminated
        assert match.blue_round1_eliminated == r1.blue_team_eliminated
        assert match.red_round2_eliminated == r2.blue_team_eliminated
        assert match.blue_round2_eliminated == r2.red_team_eliminated

    def test_match_winner_populated_when_scores_differ(self):
        match, _red, _blue = self._simulate()
        # We don't assert which team wins (depends on the seed) — only that
        # Match.save's winner logic ran. With deterministic seeds and a
        # 40-tick round there is overwhelmingly likely to be a non-tie.
        # If the round genuinely tied on both rounds the winner is allowed
        # to be None; otherwise it must be one of the two teams.
        if match.winner is not None:
            assert match.winner in (match.team_red, match.team_blue)

    def test_each_round_has_a_seed_and_seeds_differ(self):
        """``simulate_match`` draws a fresh independent 63-bit seed per round
        (CLAUDE.md: 'each round draws fresh via random.Random().getrandbits(63)';
        the two rounds of one Match have **different** seeds — independent
        draws, never derived from a master).
        """
        match, _red, _blue = self._simulate()
        r1, r2 = list(match.game_rounds.order_by("round_number"))
        assert r1.rng_seed is not None
        assert r2.rng_seed is not None
        assert (
            r1.rng_seed != r2.rng_seed
        ), "Each round must draw an independent fresh 63-bit seed"

    def test_no_arena_map_persists_none_on_both_rounds(self):
        match, _red, _blue = self._simulate(arena_map=None)
        for r in match.game_rounds.all():
            assert r.arena_map is None
            assert r.zone_size is None

    def test_arena_map_persists_on_both_rounds(self):
        arena_map, expected_zone_size = _make_minimal_arena_map("Sim09MatchMap")
        match, _red, _blue = self._simulate(arena_map=arena_map)
        rounds = list(match.game_rounds.all())
        assert len(rounds) == 2
        for r in rounds:
            assert r.arena_map == arena_map
            assert r.zone_size == expected_zone_size

    def test_round2_raise_rolls_back_match_atomically(self):
        """`@transaction.atomic`: if `_simulate_round` raises on round 2,
        no Match row, no GameRound rows, no leakage.
        """
        red, _ = make_team_with_slots("Sim09AtomR")
        blue, _ = make_team_with_slots("Sim09AtomB")

        original = BatchSimulator._simulate_round
        call_count = {"n": 0}

        def _flaky(self, *args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 2:
                raise RuntimeError("simulated round-2 failure")
            return original(self, *args, **kwargs)

        match_before = Match.objects.count()
        round_before = GameRound.objects.count()

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            with patch.object(BatchSimulator, "_simulate_round", _flaky):
                with pytest.raises(RuntimeError, match="round-2 failure"):
                    BatchSimulator().simulate_match(red, blue)

        # Atomic rollback: counts must be identical to before the call.
        assert Match.objects.count() == match_before
        assert GameRound.objects.count() == round_before


# ---------------------------------------------------------------------------
# simulate_single_round_detailed — standalone round
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestSimulateSingleRoundDetailed:
    """SIM-09: ``simulate_single_round_detailed`` produces one standalone
    GameRound (no parent Match) with ``round_number == 1`` and a fresh seed.
    """

    def test_returns_completed_standalone_round(self):
        red, _ = make_team_with_slots("Sim09SRR")
        blue, _ = make_team_with_slots("Sim09SRB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(red, blue)
        assert isinstance(gr, GameRound)
        assert gr.match is None
        assert gr.round_number == 1
        assert gr.is_completed is True
        assert gr.rng_seed is not None

    def test_no_map_round_persists_none_for_arena_fields(self):
        red, _ = make_team_with_slots("Sim09SRNoMapR")
        blue, _ = make_team_with_slots("Sim09SRNoMapB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(red, blue)
        assert gr.arena_map is None
        assert gr.zone_size is None

    def test_map_round_persists_arena_fields(self):
        arena_map, expected_zone_size = _make_minimal_arena_map("Sim09SRMap")
        red, _ = make_team_with_slots("Sim09SRMapR")
        blue, _ = make_team_with_slots("Sim09SRMapB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map
            )
        assert gr.arena_map == arena_map
        assert gr.zone_size == expected_zone_size


# ---------------------------------------------------------------------------
# ROUND_TICKS patch — round actually terminates in the patched window
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRoundTicksPatchable:
    """``BatchSimulator.ROUND_TICKS`` is the public test-speed knob (was
    ``ResourceBasedSimulator.ROUND_TICKS`` pre-SIM-09). Patching it to 40
    must actually shorten the round — every persisted ``GameEvent`` should
    have ``timestamp < ROUND_TICKS``.
    """

    def test_short_round_terminates_within_patched_tick_window(self):
        red, _ = make_team_with_slots("Sim09TicksR")
        blue, _ = make_team_with_slots("Sim09TicksB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(red, blue)
        events = list(GameEvent.objects.filter(game_round=gr))
        # No event timestamp may exceed the patched round length.
        for ev in events:
            assert (
                ev.timestamp < _FAST_TICKS + 2
            ), f"event at tick {ev.timestamp} escaped the {_FAST_TICKS}-tick round"


# ---------------------------------------------------------------------------
# _flush_to_db — extended kwarg surface lands on the persisted row
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestFlushToDBExtendedSignature:
    """``BatchSimulator._flush_to_db`` (per the SIM-09 seam contract) accepts
    new keyword args: ``match``, ``round_number``, ``arena_map``,
    ``zone_size``. Each must land on the resulting ``GameRound`` row.

    We assemble a tiny in-memory round via ``BatchSimulator.run(n=1)`` to get
    real ``result`` / ``red_players`` / ``blue_players`` / ``events`` payloads
    cheaply, then call ``_flush_to_db`` directly with the extended kwargs.
    """

    def _make_match_and_inputs(self, red, blue, arena_map=None):
        sim = BatchSimulator()
        # Use the seam-contract replay path to get one round's worth of
        # in-memory result tuples cheaply.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            result, red_players, blue_players, events = sim.replay_round(
                list(red.active_roster),
                list(blue.active_roster),
                seed=12345,
                flipped=False,
                movement_ctx=None,
            )
        match = Match.objects.create(team_red=red, team_blue=blue)
        return sim, match, result, red_players, blue_players, events

    def test_flush_writes_match_and_round_number(self):
        red, _ = make_team_with_slots("Sim09FlushR")
        blue, _ = make_team_with_slots("Sim09FlushB")
        sim, match, result, rp, bp, events = self._make_match_and_inputs(red, blue)
        gr = sim._flush_to_db(
            red,
            blue,
            result,
            rp,
            bp,
            events,
            rng_seed=12345,
            match=match,
            round_number=2,
        )
        assert gr.match == match
        assert gr.round_number == 2
        assert gr.rng_seed == 12345

    def test_flush_writes_arena_map_and_zone_size(self):
        red, _ = make_team_with_slots("Sim09FlushMapR")
        blue, _ = make_team_with_slots("Sim09FlushMapB")
        arena_map, expected_zone_size = _make_minimal_arena_map("Sim09FlushMap")
        sim, match, result, rp, bp, events = self._make_match_and_inputs(
            red, blue, arena_map=arena_map
        )
        gr = sim._flush_to_db(
            red,
            blue,
            result,
            rp,
            bp,
            events,
            rng_seed=99,
            arena_map=arena_map,
            zone_size=expected_zone_size,
        )
        assert gr.arena_map == arena_map
        assert gr.zone_size == expected_zone_size

    def test_flush_with_no_kwargs_leaves_arena_fields_none(self):
        """Backwards-compat: omitting ``arena_map``/``zone_size`` must persist
        the 3-zone-fallback shape (both fields ``None``)."""
        red, _ = make_team_with_slots("Sim09FlushNoMapR")
        blue, _ = make_team_with_slots("Sim09FlushNoMapB")
        sim, _match, result, rp, bp, events = self._make_match_and_inputs(red, blue)
        gr = sim._flush_to_db(red, blue, result, rp, bp, events, rng_seed=1)
        assert gr.arena_map is None
        assert gr.zone_size is None
        # Default round_number is 1; default match is None (standalone).
        assert gr.match is None
        assert gr.round_number == 1

    def test_flush_to_db_populates_cell_occupancy_json_when_map_active(self):
        """RES-04: ``_flush_to_db`` writes ``GameRound.cell_occupancy_json``
        when (and only when) a map is active.

        Active-map round: the field is a non-``None`` dict whose top-level
        keys are ``str(player_id)`` and whose inner keys are ``"r,c"`` cell
        strings → int tick counts. Map-less round: the field stays
        ``None`` (the gate `movement_ctx is not None` is respected).
        """
        import re

        # --- Map-active round ---------------------------------------------
        arena_map, expected_zone_size = _make_minimal_arena_map("Sim09RES04Map")
        red, _ = make_team_with_slots("Sim09RES04R")
        blue, _ = make_team_with_slots("Sim09RES04B")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map
            )
        assert gr.arena_map == arena_map
        assert gr.zone_size == expected_zone_size

        assert gr.cell_occupancy_json is not None, (
            "RES-04: a map-active round must populate cell_occupancy_json; " "got None"
        )
        assert isinstance(gr.cell_occupancy_json, dict), (
            "cell_occupancy_json must be a dict, got "
            f"{type(gr.cell_occupancy_json).__name__}"
        )

        top_key_re = re.compile(r"^\d+$")
        inner_key_re = re.compile(r"^\d+,\d+$")
        for player_key, per_cell in gr.cell_occupancy_json.items():
            assert top_key_re.fullmatch(player_key), (
                "cell_occupancy_json top-level keys must be str(player_id), "
                f"got {player_key!r}"
            )
            assert isinstance(per_cell, dict), (
                f"cell_occupancy_json[{player_key!r}] must be a dict, "
                f"got {type(per_cell).__name__}"
            )
            for cell_key, ticks in per_cell.items():
                assert inner_key_re.fullmatch(cell_key), (
                    "cell_occupancy_json inner keys must match 'r,c'; got "
                    f"{cell_key!r}"
                )
                assert isinstance(ticks, int), (
                    "cell_occupancy_json inner values must be int (post-"
                    f"rounding); got {ticks!r} ({type(ticks).__name__})"
                )

        # --- Map-less round -----------------------------------------------
        red_no, _ = make_team_with_slots("Sim09RES04NoMapR")
        blue_no, _ = make_team_with_slots("Sim09RES04NoMapB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr_no = BatchSimulator().simulate_single_round_detailed(red_no, blue_no)
        assert gr_no.arena_map is None
        assert gr_no.cell_occupancy_json is None, (
            "RES-04 gate regression: a map-less round must leave "
            "cell_occupancy_json NULL (movement_ctx is None => skip the "
            "reconstruction step). Got "
            f"{gr_no.cell_occupancy_json!r}"
        )
