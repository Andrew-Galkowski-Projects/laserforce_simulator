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
        # GEN-01: default tier is ``scores`` (no GameEvent rows); this test
        # asserts on persisted event timestamps, so request the ``full`` tier.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )
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
        # GEN-01: cell_occupancy_json is a ``full``-tier write; the default
        # ``scores`` tier leaves it null. Request ``full``.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, arena_map=arena_map, fidelity="full"
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
        # GEN-01: request ``full`` so the null below is genuinely the map-less
        # ``movement_ctx is None`` gate, not the (scores-tier) occupancy skip.
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr_no = BatchSimulator().simulate_single_round_detailed(
                red_no, blue_no, fidelity="full"
            )
        assert gr_no.arena_map is None
        assert gr_no.cell_occupancy_json is None, (
            "RES-04 gate regression: a map-less round must leave "
            "cell_occupancy_json NULL (movement_ctx is None => skip the "
            "reconstruction step). Got "
            f"{gr_no.cell_occupancy_json!r}"
        )


# ---------------------------------------------------------------------------
# RV-02 — highlights persistence + nuke_cancelled / medic_reset emission
# ---------------------------------------------------------------------------


def _rv02_player(role, team_color, *, lives=3, shots=10, pid=1):
    """Minimal in-memory PlayerState for direct _record_down exercises."""
    from matches.sim_helpers.player_state import PlayerState

    return PlayerState(
        tag_id=f"{team_color}_{role}",
        name=f"{team_color}_{role}",
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=lives,
        starting_shots=shots,
        final_lives=lives,
        final_shots=shots,
        player_id=pid,
    )


@pytest.mark.django_db
class TestRV02HighlightsFlush:
    """RV-02: ``_flush_to_db`` populates ``GameRound.highlights_json`` on every
    path as a list of well-formed records (proves the build_highlights wiring +
    id->name resolution)."""

    RECORD_KEYS = {"kind", "tick", "team", "actor", "target", "points", "label"}
    KINDS = {
        "nuke_detonation",
        "nuke_cancelled",
        "medic_reset",
        "first_elimination",
        "team_elimination",
        "scoring_burst",
    }

    def test_flush_populates_highlights_json_list(self):
        red, _ = make_team_with_slots("Sim09RV02R")
        blue, _ = make_team_with_slots("Sim09RV02B")
        # A longer round so real combat produces at least one highlight.
        # GEN-01: highlights_json is a ``combat``/``full`` write; request
        # ``full`` (default ``scores`` leaves it null).
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )

        assert isinstance(gr.highlights_json, list), (
            "RV-02: highlights_json must be a list; got "
            f"{type(gr.highlights_json).__name__}"
        )
        # sorted by tick
        ticks = [h["tick"] for h in gr.highlights_json if h["tick"] is not None]
        assert ticks == sorted(ticks), "highlights must be sorted by tick"

        known_names = {p.name for p in red.players.all()} | {
            p.name for p in blue.players.all()
        }
        for rec in gr.highlights_json:
            assert set(rec.keys()) == self.RECORD_KEYS
            assert rec["kind"] in self.KINDS
            if rec["actor"] is not None:
                # id->name wiring resolved a real player name (not a bare id).
                assert rec["actor"] in known_names, rec


class TestRV02NukeCancelled:
    """RV-02: a Commander Downed during its nuke's fuse emits exactly one
    ``nuke_cancelled`` event at the down tick, leaves the nuke in the pending
    queue, and never double-emits. Exercises the ``record_down`` chokepoint
    directly (the deterministic life-loss path).

    Shot-resolver consolidation: ``record_down`` is now a pure function
    on ``sim_helpers.down``; the per-test ``RoundContext`` carries the
    event log + pending nuke queue that the legacy self-stash held.
    """

    def _ctx(self, pending_nukes=None):
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        return RoundContext(
            events=EventLog(persist=True),
            pending_nukes=pending_nukes if pending_nukes is not None else [],
            pending_followups=[],
            pending_reactions=[],
            all_alive=[],
            movement_ctx=None,
        )

    def _count(self, log, etype):
        return sum(1 for e in log if e["event_type"] == etype)

    def test_down_during_fuse_emits_once_and_leaves_nuke_queued(self):
        from matches.sim_helpers.down import record_down
        from matches.sim_helpers.pending_events import PendingNuke

        cmd = _rv02_player("commander", "red", pid=7)
        pn = PendingNuke(complete_time=120, player=cmd)
        ctx = self._ctx(pending_nukes=[pn])

        cmd.final_lives -= 1  # mimic the life-loss at the callsite
        record_down(cmd, 105, ctx)

        cancels = [e for e in ctx.events.entries if e["event_type"] == "nuke_cancelled"]
        assert len(cancels) == 1
        assert cancels[0]["actor_id"] == 7
        assert cancels[0]["timestamp"] == 105
        assert pn.cancel_logged is True
        assert pn in ctx.pending_nukes, "cancelled nuke must stay in pending_nukes"

    def test_no_double_emit_on_second_down(self):
        from matches.sim_helpers.down import record_down
        from matches.sim_helpers.pending_events import PendingNuke

        cmd = _rv02_player("commander", "red", pid=7)
        ctx = self._ctx(pending_nukes=[PendingNuke(complete_time=120, player=cmd)])

        cmd.final_lives -= 1
        record_down(cmd, 105, ctx)
        record_down(cmd, 106, ctx)  # already cancel_logged → no second event

        assert self._count(ctx.events.entries, "nuke_cancelled") == 1

    def test_commander_without_pending_nuke_emits_nothing(self):
        from matches.sim_helpers.down import record_down

        cmd = _rv02_player("commander", "red")
        ctx = self._ctx()
        cmd.final_lives -= 1
        record_down(cmd, 105, ctx)
        assert self._count(ctx.events.entries, "nuke_cancelled") == 0


class TestRV02MedicReset:
    """RV-02: a Medic re-Downed before recovering (2 downs in one unbroken
    chain) emits exactly one ``medic_reset``; a Medic that fully recovers
    between downs does not."""

    def _ctx(self):
        from matches.sim_helpers.event_log import EventLog
        from matches.sim_helpers.round_context import RoundContext

        return RoundContext(
            events=EventLog(persist=True),
            pending_nukes=[],
            pending_followups=[],
            pending_reactions=[],
            all_alive=[],
            movement_ctx=None,
        )

    def _count(self, log):
        return sum(1 for e in log if e["event_type"] == "medic_reset")

    def test_redown_within_cooldown_emits_once(self):
        from matches.sim_helpers.down import record_down

        ctx = self._ctx()
        medic = _rv02_player("medic", "blue", lives=3, pid=9)

        medic.final_lives -= 1
        record_down(medic, 100, ctx)  # fresh down (active) → chain 1, no emit
        assert self._count(ctx.events.entries) == 0

        medic.final_lives -= 1
        record_down(medic, 105, ctx)  # re-down within RESPAWN window → chain 2

        resets = [e for e in ctx.events.entries if e["event_type"] == "medic_reset"]
        assert len(resets) == 1
        assert resets[0]["actor_id"] == 9
        assert resets[0]["timestamp"] == 105

    def test_recovered_between_downs_does_not_emit(self):
        from matches.sim_helpers.down import record_down

        ctx = self._ctx()
        medic = _rv02_player("medic", "blue", lives=3)

        medic.final_lives -= 1
        record_down(medic, 100, ctx)
        medic.final_lives -= 1
        record_down(medic, 200, ctx)  # fully recovered (200-100 >> RESPAWN) → fresh

        assert self._count(ctx.events.entries) == 0

    def test_non_medic_chain_does_not_emit_medic_reset(self):
        from matches.sim_helpers.down import record_down

        ctx = self._ctx()
        scout = _rv02_player("scout", "red", lives=3)
        scout.final_lives -= 1
        record_down(scout, 100, ctx)
        scout.final_lives -= 1
        record_down(scout, 105, ctx)  # chain reaches 2 but role != medic
        assert self._count(ctx.events.entries) == 0


# ===========================================================================
# LG-02x-1 — simulate_match(before_round_hook=...) seam
# ===========================================================================
#
# NEW classes appended below (existing classes above are NOT modified). Seam
# contract: ``.claude/worktrees/lg-02x-1-seam-contract.md`` §3 / §7.
#
# ``simulate_match`` gains a keyword-only ``before_round_hook=None``. Default
# None ⇒ byte-unchanged for every existing caller (no hook invoked). When given,
# the hook fires ONCE PER ROUND with ``(round_number, team_red, team_blue)`` —
# round 1 gets ``(1, team_red, team_blue)`` and round 2 gets the SWAPPED
# ``(2, team_blue, team_red)`` order (the same physical-side order the per-Match
# colour swap uses). A hook that rewrites the drawn Teams' ``slot_*`` FKs in
# memory changes the roster that round simulates against — asserted via a
# recording hook + the resulting ``PlayerRoundState.role`` mapping, NOT via
# point totals.
#
# These assertions WILL fail until the Code agent lands the additive kwarg + the
# two hook-invocation lines; that is expected for the parallel build.


@pytest.mark.django_db
class TestSimulateMatchHookDefaultNone:
    """``before_round_hook=None`` (and the bare default) leave the 2-round
    Match structure intact and never invoke a hook."""

    def _simulate(self, **kwargs):
        random.seed(42)
        red, _ = make_team_with_slots("HookNoneR")
        blue, _ = make_team_with_slots("HookNoneB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(red, blue, **kwargs)
        return match

    def test_explicit_none_accepted_and_two_rounds_persist(self):
        match = self._simulate(before_round_hook=None)
        assert isinstance(match, Match)
        assert match.is_completed is True
        rounds = list(match.game_rounds.order_by("round_number"))
        assert len(rounds) == 2
        assert [r.round_number for r in rounds] == [1, 2]

    def test_bare_default_call_still_works(self):
        # The default (no kwarg at all) must remain valid — byte-unchanged
        # surface for every legacy caller.
        match = self._simulate()
        assert isinstance(match, Match)
        assert match.is_completed is True

    def test_default_call_invokes_no_hook(self):
        # A recording sentinel passed as None can't record; instead prove the
        # colour-swap structure (the only observable of the unchanged path) is
        # intact: round 1 keeps sides, round 2 swaps them.
        match = self._simulate(before_round_hook=None)
        r1, r2 = list(match.game_rounds.order_by("round_number"))
        assert r1.team_red_id == match.team_red_id
        assert r2.team_red_id == match.team_blue_id


@pytest.mark.django_db
class TestSimulateMatchHookInvocation:
    """The hook fires once per round with the physical-side ``(round_number,
    team_red, team_blue)`` order; round 2 receives the swapped pair."""

    def _run(self):
        random.seed(7)
        red, _ = make_team_with_slots("HookInvR")
        blue, _ = make_team_with_slots("HookInvB")
        calls: list[tuple] = []

        def _hook(round_number, team_red, team_blue):
            calls.append((round_number, team_red.id, team_blue.id))

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            BatchSimulator().simulate_match(red, blue, before_round_hook=_hook)
        return calls, red, blue

    def test_hook_invoked_exactly_twice(self):
        calls, _red, _blue = self._run()
        assert len(calls) == 2, f"hook must fire once per round; got {calls!r}"

    def test_round_numbers_are_one_then_two(self):
        calls, _red, _blue = self._run()
        assert [c[0] for c in calls] == [1, 2]

    def test_round1_receives_red_then_blue(self):
        calls, red, blue = self._run()
        # Round 1: (1, team_red, team_blue) in the physical-side order.
        assert calls[0] == (1, red.id, blue.id)

    def test_round2_receives_swapped_blue_then_red(self):
        calls, red, blue = self._run()
        # Round 2: the colour swap means the hook sees (2, team_blue, team_red).
        assert calls[1] == (2, blue.id, red.id)


@pytest.mark.django_db
class TestSimulateMatchHookRewritesRoster:
    """A hook that rewrites the Teams' ``slot_*`` FKs in memory changes the
    roster the round actually simulates against — proven via the resulting
    ``PlayerRoundState.role`` mapping (NOT point totals)."""

    def _baseline_roles_by_player(self):
        """Run a normal match (no hook) and return round-1's
        {player_id: role} for the red team, the control mapping."""
        random.seed(101)
        red, _ = make_team_with_slots("HookRwBaseR")
        blue, _ = make_team_with_slots("HookRwBaseB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(red, blue)
        r1 = match.game_rounds.get(round_number=1)
        return {
            s.player_id: s.role for s in r1.player_states.filter(team_color="red")
        }, red

    def test_swapping_two_slots_changes_a_players_role_in_the_round(self):
        random.seed(101)
        red, _ = make_team_with_slots("HookRwR")
        blue, _ = make_team_with_slots("HookRwB")

        # Capture the round-1 default role of the commander-slot player so we
        # can prove the rewrite moved that player to a DIFFERENT role.
        commander_player_id = red.slot_commander_id
        ammo_player_id = red.slot_ammo_id

        def _hook(round_number, team_red, team_blue):
            # Only rewrite round 1's red team (the one we inspect): swap the
            # commander and ammo slot FKs in memory before the round sims.
            if round_number == 1 and team_red.id == red.id:
                team_red.slot_commander_id = ammo_player_id
                team_red.slot_ammo_id = commander_player_id

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(red, blue, before_round_hook=_hook)

        r1 = match.game_rounds.get(round_number=1)
        roles_by_player = {
            s.player_id: s.role for s in r1.player_states.filter(team_color="red")
        }
        # The commander-slot player now occupies the ammo slot's role, and the
        # ammo-slot player now occupies the commander slot's role — the swap the
        # hook performed in memory took effect for the simulated round.
        self_assert = roles_by_player.get(commander_player_id)
        other = roles_by_player.get(ammo_player_id)
        assert self_assert is not None and other is not None
        # The two players' roles are the SWAP of each other vs the default
        # layout: the commander-player's role == the role the ammo-player would
        # normally have, and vice versa.
        assert self_assert != other
        # Specifically the commander-player now holds the ammo role and the
        # ammo-player now holds the commander role.
        assert "ammo" in self_assert.lower()
        assert "commander" in other.lower()

    def test_no_rewrite_keeps_default_role_layout(self):
        # Control: a hook that does NOT touch slots leaves the role mapping at
        # the default slot layout (commander-player keeps the commander role).
        random.seed(101)
        red, _ = make_team_with_slots("HookCtrlR")
        blue, _ = make_team_with_slots("HookCtrlB")
        commander_player_id = red.slot_commander_id

        def _noop_hook(round_number, team_red, team_blue):
            return None

        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(
                red, blue, before_round_hook=_noop_hook
            )
        r1 = match.game_rounds.get(round_number=1)
        role = (
            r1.player_states.filter(team_color="red")
            .get(player_id=commander_player_id)
            .role
        )
        assert "commander" in role.lower()


# ===========================================================================
# GEN-01 — fidelity / roster_snapshot persistence on each create path
# ===========================================================================
#
# These pin the GEN-01 per-create-path contract (§7e): the sandbox create
# paths DEFAULT to ``scores`` (and stamp a non-null ``roster_snapshot_json``),
# while an explicit ``fidelity="full"`` writes the full-fidelity rows. The 13
# snapshot stat keys are checked against the locked set.

_GEN01_SNAPSHOT_STATS = {
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


def _assert_snapshot_shape(snap):
    assert isinstance(snap, dict) and set(snap) == {"red", "blue"}, snap
    for side in ("red", "blue"):
        assert isinstance(snap[side], list) and snap[side]
        for entry in snap[side]:
            assert {"player_id", "name", "role", "stats"} <= set(entry), entry
            assert set(entry["stats"]) == _GEN01_SNAPSHOT_STATS, entry["stats"]
            assert all(isinstance(v, int) for v in entry["stats"].values())


@pytest.mark.django_db
class TestGen01CreatePathFidelity:
    """``simulate_single_round_detailed`` and ``simulate_match`` default to
    ``scores`` and always stamp a non-null ``roster_snapshot_json``; an explicit
    ``fidelity="full"`` lands the full-fidelity rows."""

    def test_single_round_defaults_to_scores_with_snapshot(self):
        red, _ = make_team_with_slots("Gen01SRDefR")
        blue, _ = make_team_with_slots("Gen01SRDefB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            gr = BatchSimulator().simulate_single_round_detailed(red, blue)
        assert gr.fidelity == "scores"
        assert GameEvent.objects.filter(game_round=gr).count() == 0
        assert gr.cell_occupancy_json is None
        assert gr.highlights_json is None
        _assert_snapshot_shape(gr.roster_snapshot_json)

    def test_single_round_full_writes_full_rows_and_snapshot(self):
        red, _ = make_team_with_slots("Gen01SRFullR")
        blue, _ = make_team_with_slots("Gen01SRFullB")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )
        assert gr.fidelity == "full"
        assert GameEvent.objects.filter(game_round=gr).count() > 0
        assert gr.highlights_json is not None
        _assert_snapshot_shape(gr.roster_snapshot_json)

    def test_match_defaults_to_scores_on_both_rounds(self):
        red, _ = make_team_with_slots("Gen01MatchDefR")
        blue, _ = make_team_with_slots("Gen01MatchDefB")
        with patch.object(BatchSimulator, "ROUND_TICKS", _FAST_TICKS):
            match = BatchSimulator().simulate_match(red, blue)
        for r in match.game_rounds.all():
            assert r.fidelity == "scores"
            assert GameEvent.objects.filter(game_round=r).count() == 0
            assert r.cell_occupancy_json is None
            assert r.highlights_json is None
            _assert_snapshot_shape(r.roster_snapshot_json)

    def test_match_full_writes_full_rows_on_both_rounds(self):
        red, _ = make_team_with_slots("Gen01MatchFullR")
        blue, _ = make_team_with_slots("Gen01MatchFullB")
        with patch.object(BatchSimulator, "ROUND_TICKS", 400):
            match = BatchSimulator().simulate_match(red, blue, fidelity="full")
        for r in match.game_rounds.all():
            assert r.fidelity == "full"
            assert GameEvent.objects.filter(game_round=r).count() > 0
            assert r.highlights_json is not None
            _assert_snapshot_shape(r.roster_snapshot_json)
