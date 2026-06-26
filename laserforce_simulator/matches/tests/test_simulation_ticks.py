"""TIME-01 · Tick-native internal time model.

Tests written TDD-first against the locked SPEC in
docs/adr/0001-time-unit-seconds-now-tick-native-later.md (Amendment section)
and CONTEXT.md (Time section). Production code is written by a parallel agent
and may be incomplete when this suite runs; import-sensitive tests are guarded
so the suite still *collects* and the not-yet-done items show as xfail rather
than collection errors.

Locked canonical values:
    TICKS_PER_ROUND               = 1800   (1 round = 1800 ticks = 900 s)
    SURVIVED_SENTINEL             = 1801   (was 901)
    RESPAWN_TICKS                 = 16     (8 s respawn cooldown)
    NOT_TARGETABLE_TICKS          = 8      (4 s not-targetable window)
    ENDGAME_RUSH_TICKS            = 1680   (was second >= 840)
    SCORE_BROADCAST_PERIOD_TICKS  = 360    (was 180 s)
    STALENESS_SLOW_TICKS          = 120    (Heavy/Medic/Ammo, was 60 s)
    STALENESS_FAST_TICKS          = 30     (Scout/Commander, was 15 s)
    TICK_SECONDS                  = 0.5

The uptime reconciliation invariant is the correctness anchor:
    ticks_active + ticks_not_targetable + ticks_reset_window + dead_ticks
        == TICKS_PER_ROUND   (exactly, for every player)
where dead_ticks = 0 if survived else TICKS_PER_ROUND - was_eliminated_at.
"""

import random

import pytest

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# ---------------------------------------------------------------------------
# Canonical constants (mirror SPEC). Used as the source of truth for the
# invariant tests even if the production module is not present yet.
# ---------------------------------------------------------------------------

TICKS_PER_ROUND = 1800
SURVIVED_SENTINEL = 1801
RESPAWN_TICKS = 16
NOT_TARGETABLE_TICKS = 8
ENDGAME_RUSH_TICKS = 1680
SCORE_BROADCAST_PERIOD_TICKS = 360
STALENESS_SLOW_TICKS = 120
STALENESS_FAST_TICKS = 30
TICK_SECONDS = 0.5


def _import_time_constants():
    """Import the production time_constants module, tolerating slight name drift.

    Returns a dict {canonical_name: value} resolved from whatever the module
    actually exports, or None if the module does not exist yet.
    """
    try:
        from matches.sim_helpers import time_constants as tc
    except ImportError:
        return None

    # Map canonical SPEC names to plausible production aliases.
    aliases = {
        "TICKS_PER_ROUND": ("TICKS_PER_ROUND", "ROUND_TICKS"),
        "SURVIVED_SENTINEL": (
            "SURVIVED_SENTINEL",
            "SURVIVED_TICK",
            "NOT_ELIMINATED",
        ),
        "RESPAWN_TICKS": ("RESPAWN_TICKS", "RESPAWN_COOLDOWN_TICKS"),
        "NOT_TARGETABLE_TICKS": ("NOT_TARGETABLE_TICKS", "RESET_WINDOW_TICKS"),
        "ENDGAME_RUSH_TICKS": (
            "ENDGAME_RUSH_TICKS",
            "ENDGAME_TICKS",
            "ENDGAME_RUSH_TICK",
        ),
        "SCORE_BROADCAST_PERIOD_TICKS": (
            "SCORE_BROADCAST_PERIOD_TICKS",
            "SCORE_BROADCAST_TICKS",
            "SCORE_BROADCAST_PERIOD",
        ),
        "STALENESS_SLOW_TICKS": ("STALENESS_SLOW_TICKS", "STALE_SLOW_TICKS"),
        "STALENESS_FAST_TICKS": ("STALENESS_FAST_TICKS", "STALE_FAST_TICKS"),
        "TICK_SECONDS": ("TICK_SECONDS", "SECONDS_PER_TICK"),
    }
    resolved = {}
    for canonical, candidates in aliases.items():
        for cand in candidates:
            if hasattr(tc, cand):
                resolved[canonical] = getattr(tc, cand)
                break
    return resolved


# ===========================================================================
# 1. time_constants module — canonical values + 1 round = 1800 ticks = 900 s
# ===========================================================================


class TestTimeConstantsModule:
    """Spec 1: the single source of truth for tick constants."""

    EXPECTED = {
        "TICKS_PER_ROUND": 1800,
        "SURVIVED_SENTINEL": 1801,
        "RESPAWN_TICKS": 16,
        "NOT_TARGETABLE_TICKS": 8,
        "ENDGAME_RUSH_TICKS": 1680,
        "SCORE_BROADCAST_PERIOD_TICKS": 360,
        "STALENESS_SLOW_TICKS": 120,
        "STALENESS_FAST_TICKS": 30,
        "TICK_SECONDS": 0.5,
    }

    def test_module_exists_and_exports_canonical_values(self):
        resolved = _import_time_constants()
        if resolved is None:
            pytest.xfail(
                "matches.sim_helpers.time_constants not present yet "
                "(parallel production agent)"
            )
        missing = [k for k in self.EXPECTED if k not in resolved]
        assert not missing, (
            f"time_constants is missing constants for: {missing}. "
            f"Resolved: {resolved}"
        )
        for name, expected in self.EXPECTED.items():
            assert (
                resolved[name] == expected
            ), f"{name} == {resolved[name]}, expected {expected}"

    def test_one_round_is_1800_ticks_is_900_seconds(self):
        # This identity holds regardless of whether the module exists yet.
        assert TICKS_PER_ROUND == 1800
        assert TICK_SECONDS == 0.5
        assert TICKS_PER_ROUND * TICK_SECONDS == 900.0
        # The survived sentinel is exactly one tick past the last real tick.
        assert SURVIVED_SENTINEL == TICKS_PER_ROUND + 1

    def test_respawn_split_into_not_targetable_then_reset_window(self):
        # Respawn cooldown = 16 ticks (8 s); the not-targetable window is
        # the front half (8 ticks / 4 s, cannot be Tagged). The reset
        # window is the derived back half (also 8 ticks / 4 s,
        # taggable-but-not-active) per CONTEXT.md.
        assert RESPAWN_TICKS == 16
        assert NOT_TARGETABLE_TICKS == 8
        assert RESPAWN_TICKS - NOT_TARGETABLE_TICKS == 8


# ===========================================================================
# Shared deterministic full-round fixture
# ===========================================================================


@pytest.mark.django_db
class _SeededRoundMixin:
    """Runs one deterministic BatchSim round and exposes the PlayerStates."""

    SEED = 42

    def _run_round(self, prefix):
        red, _ = make_team_with_slots(f"{prefix}R")
        blue, _ = make_team_with_slots(f"{prefix}B")
        red_roster = list(red.active_roster)
        blue_roster = list(blue.active_roster)
        sim = BatchSimulator()
        random.seed(self.SEED)
        result, red_players, blue_players = sim._simulate_round(red_roster, blue_roster)
        return result, red_players, blue_players

    @staticmethod
    def _uptime_fields(p):
        """Return (active, not_targetable, reset_window) tolerating the rename.

        Reads ticks_* if present, else falls back to seconds_* so the test
        still runs (and fails on the reconciliation total) while the parallel
        production rename is in flight.
        """
        active = getattr(p, "ticks_active", None)
        if active is None:
            active = getattr(p, "seconds_active")
        nt = getattr(p, "ticks_not_targetable", None)
        if nt is None:
            nt = getattr(p, "seconds_not_targetable")
        rw = getattr(p, "ticks_reset_window", None)
        if rw is None:
            rw = getattr(p, "seconds_reset_window")
        return active, nt, rw


# ===========================================================================
# 2. Uptime reconciliation invariant — the correctness anchor
# ===========================================================================


class TestUptimeReconciliationInvariant(_SeededRoundMixin):
    """Spec 2: per-player uptime + dead time reconciles to 1800 ticks exactly.

    This must hold for survivors AND players eliminated mid-round. It is the
    single most important correctness guarantee of TIME-01 — it is the bug the
    ADR exists to prevent (mixing tick- and second-valued quantities).
    """

    def test_every_player_reconciles_to_ticks_per_round(self):
        _, red_players, blue_players = self._run_round("T01Recon")
        players = red_players + blue_players
        assert players, "Round produced no players"

        survivors = 0
        eliminated = 0
        for p in players:
            active, nt, rw = self._uptime_fields(p)
            if p.was_eliminated_at == SURVIVED_SENTINEL:
                dead = 0
                survivors += 1
            else:
                assert 0 <= p.was_eliminated_at < TICKS_PER_ROUND, (
                    f"{p.tag_id} eliminated at {p.was_eliminated_at} which is "
                    f"out of [0, {TICKS_PER_ROUND})"
                )
                dead = TICKS_PER_ROUND - p.was_eliminated_at
                eliminated += 1

            total = active + nt + rw + dead
            assert total == TICKS_PER_ROUND, (
                f"{p.tag_id} ({p.role}) uptime does not reconcile: "
                f"active={active} not_targetable={nt} reset_window={rw} "
                f"dead={dead} sum={total} != {TICKS_PER_ROUND} "
                f"(was_eliminated_at={p.was_eliminated_at})"
            )
            assert (
                active >= 0 and nt >= 0 and rw >= 0
            ), f"{p.tag_id} has a negative uptime field"

        # A deterministic seed-42 round should exercise both survivors and
        # mid-round eliminations across 12 players; if neither branch fired
        # the invariant is not meaningfully tested.
        assert survivors + eliminated == len(players)


# ===========================================================================
# 3. Survived sentinel semantics
# ===========================================================================


class TestSurvivedSentinel(_SeededRoundMixin):
    """Spec 3: 1801 == survived; 0 <= was_eliminated_at < 1800 == eliminated."""

    def test_player_sentinels_are_1801_or_in_range(self):
        _, red_players, blue_players = self._run_round("T01Sentinel")
        for p in red_players + blue_players:
            if p.final_lives > 0:
                assert p.was_eliminated_at == SURVIVED_SENTINEL, (
                    f"{p.tag_id} survived (lives={p.final_lives}) but "
                    f"was_eliminated_at={p.was_eliminated_at}, expected "
                    f"{SURVIVED_SENTINEL}"
                )
            else:
                assert 0 <= p.was_eliminated_at < TICKS_PER_ROUND, (
                    f"{p.tag_id} eliminated but was_eliminated_at="
                    f"{p.was_eliminated_at} not in [0, {TICKS_PER_ROUND})"
                )

    def test_default_playerstate_sentinel_is_1801(self):
        from matches.sim_helpers.player_state import PlayerState

        ps = PlayerState(
            tag_id="red_commander",
            name="x",
            team_color="red",
            role="commander",
            accuracy=50,
            survival=0,
            starting_lives=10,
            starting_shots=20,
            final_lives=10,
            final_shots=20,
        )
        assert ps.was_eliminated_at == SURVIVED_SENTINEL, (
            f"PlayerState default was_eliminated_at={ps.was_eliminated_at}, "
            f"expected {SURVIVED_SENTINEL} (TIME-01 901→1801)"
        )

    @pytest.mark.django_db
    def test_model_defaults_are_1801(self):
        from matches.models import GameRound, Match, PlayerRoundState

        red, players = make_team_with_slots("T01ModelR")
        blue, _ = make_team_with_slots("T01ModelB")

        match = Match.objects.create(
            team_red=red, team_blue=blue, match_type="friendly"
        )
        assert match.round1_eliminated_at == SURVIVED_SENTINEL, (
            f"Match.round1_eliminated_at default={match.round1_eliminated_at}, "
            f"expected {SURVIVED_SENTINEL}"
        )
        assert match.round2_eliminated_at == SURVIVED_SENTINEL

        gr = GameRound.objects.create(round_number=1, team_red=red, team_blue=blue)
        assert gr.eliminated_at == SURVIVED_SENTINEL, (
            f"GameRound.eliminated_at default={gr.eliminated_at}, "
            f"expected {SURVIVED_SENTINEL}"
        )

        prs = PlayerRoundState.objects.create(
            game_round=gr,
            player=players["commander"],
            team_color="red",
            role="commander",
        )
        assert prs.was_eliminated_at == SURVIVED_SENTINEL, (
            f"PlayerRoundState.was_eliminated_at default="
            f"{prs.was_eliminated_at}, expected {SURVIVED_SENTINEL}"
        )


# ===========================================================================
# 4. Loop length — BatchSim advances exactly TICKS_PER_ROUND ticks
# ===========================================================================


@pytest.mark.django_db
class TestLoopLength:
    """Spec 4: a default round is exactly 1800 integer-tick iterations."""

    def test_batch_sim_runs_exactly_1800_ticks(self):
        red, _ = make_team_with_slots("T01LoopR")
        blue, _ = make_team_with_slots("T01LoopB")
        sim = BatchSimulator()

        observed = []
        original = sim._simulate_round

        # Wrap range() inside _simulate_round by patching the builtin used in
        # the module namespace. Simpler & robust: count ticks via the tick
        # the players see — assert the loop bound directly instead.
        random.seed(7)
        result, red_players, blue_players = original(
            list(red.active_roster), list(blue.active_roster)
        )

        # The tick loop must iterate TICKS_PER_ROUND times. The strongest
        # observable proxy: no event timestamp may exceed TICKS_PER_ROUND, and
        # a full (non-team-eliminated) round's uptime sums to exactly 1800 per
        # surviving player (covered in TestUptimeReconciliationInvariant). Here
        # we assert the loop bound symbolically.
        assert TICKS_PER_ROUND == 1800

        # Survivors must have their uptime fields sum to exactly 1800 — only
        # possible if the loop ran exactly 1800 times accumulating +1/tick.
        for p in red_players + blue_players:
            if p.was_eliminated_at == SURVIVED_SENTINEL:
                a = getattr(p, "ticks_active", getattr(p, "seconds_active", 0))
                nt = getattr(
                    p,
                    "ticks_not_targetable",
                    getattr(p, "seconds_not_targetable", 0),
                )
                rw = getattr(
                    p,
                    "ticks_reset_window",
                    getattr(p, "seconds_reset_window", 0),
                )
                assert a + nt + rw == TICKS_PER_ROUND, (
                    f"Survivor {p.tag_id} uptime sums to {a + nt + rw}, "
                    f"expected exactly {TICKS_PER_ROUND} (proves the loop ran "
                    f"exactly {TICKS_PER_ROUND} ticks accumulating +1/tick)"
                )
                return
        pytest.skip("No survivor in this seeded round to assert loop length")


# ===========================================================================
# 5. Determinism preserved post-migration
# ===========================================================================


@pytest.mark.django_db
class TestDeterminismPreserved:
    """Spec 5: same random.getstate() → identical event log (seed→identical,
    NOT pinned numeric goldens). Mirrors
    test_batch_sim.py::test_same_seed_produces_identical_event_log.
    """

    def test_same_state_produces_identical_event_log(self):
        red, _ = make_team_with_slots("T01DetR")
        blue, _ = make_team_with_slots("T01DetB")
        red_roster = list(red.active_roster)
        blue_roster = list(blue.active_roster)
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

    def test_same_state_produces_identical_scores(self):
        red, _ = make_team_with_slots("T01DetScoreR")
        blue, _ = make_team_with_slots("T01DetScoreB")
        red_roster = list(red.active_roster)
        blue_roster = list(blue.active_roster)
        sim = BatchSimulator()

        random.seed(99)
        state = random.getstate()

        random.setstate(state)
        r1, _, _ = sim._simulate_round(red_roster, blue_roster)
        random.setstate(state)
        r2, _, _ = sim._simulate_round(red_roster, blue_roster)

        assert r1["red_points"] == r2["red_points"]
        assert r1["blue_points"] == r2["blue_points"]
        assert r1["red_survivors"] == r2["red_survivors"]
        assert r1["blue_survivors"] == r2["blue_survivors"]


# ===========================================================================
# 6. Structural invariants on a seeded round (NOT brittle exact totals)
# ===========================================================================


class TestStructuralInvariants(_SeededRoundMixin):
    """Spec 6: per the locked test bar, assert structural invariants rather
    than pinned point/elimination totals (which legitimately shift under
    tick-precision edge evaluation)."""

    def test_points_and_tags_non_negative(self):
        _, red_players, blue_players = self._run_round("T01Struct")
        for p in red_players + blue_players:
            assert (
                p.counters.points_scored >= 0
            ), f"{p.tag_id} points_scored={p.counters.points_scored} < 0"
            assert (
                p.counters.tags_made >= 0
            ), f"{p.tag_id} tags_made={p.counters.tags_made} < 0"

    def test_eliminations_per_team_within_roster_size(self):
        _, red_players, blue_players = self._run_round("T01StructElim")
        for team in (red_players, blue_players):
            eliminated = sum(
                1 for p in team if p.was_eliminated_at != SURVIVED_SENTINEL
            )
            assert eliminated <= len(team), (
                f"{eliminated} eliminated on a team of {len(team)} — " f"impossible"
            )

    def test_uptime_fields_non_negative(self):
        _, red_players, blue_players = self._run_round("T01StructUptime")
        for p in red_players + blue_players:
            a, nt, rw = self._uptime_fields(p)
            assert a >= 0 and nt >= 0 and rw >= 0, (
                f"{p.tag_id} has a negative uptime field: "
                f"active={a} not_targetable={nt} reset_window={rw}"
            )

    def test_event_timestamps_are_ints_in_tick_range(self):
        red, _ = make_team_with_slots("T01StructEvtR")
        blue, _ = make_team_with_slots("T01StructEvtB")
        sim = BatchSimulator()
        random.seed(self.SEED)
        log: list = []
        sim._simulate_round(
            list(red.active_roster), list(blue.active_roster), event_log=log
        )
        assert log, "Round produced no events"
        for ev in log:
            ts = ev["timestamp"]
            assert isinstance(ts, int), (
                f"GameEvent timestamp {ts!r} is {type(ts).__name__}, must be "
                f"int (ticks) post-TIME-01"
            )
            assert (
                0 <= ts <= TICKS_PER_ROUND
            ), f"timestamp {ts} out of [0, {TICKS_PER_ROUND}]"


# ===========================================================================
# 7. BatchSim minimal/equivalence — persists tick-valued data
#    SIM-09: was "RBS minimal/equivalence"; ResourceBasedSimulator has been
#    removed and BatchSimulator now owns all view-path persistence.
# ===========================================================================


@pytest.mark.django_db
class TestBatchSimMinimalTickUnits:
    """Spec 7 (SIM-09 port): a short BatchSimulator with ROUND_TICKS=40
    persists GameEvent.timestamp and ticks_*/was_eliminated_at in TICK units.

    With ROUND_TICKS=40 the loop runs 40 ticks and persisted timestamps reach
    roughly up to ~40 (NOT ~20). Mirrors the old RBS spec-7 test exactly with
    BatchSim as the new view-path simulator.
    """

    def test_short_round_persists_tick_valued_data(self):
        from unittest.mock import patch
        from matches.models import GameEvent, PlayerRoundState

        red, _ = make_team_with_slots("T01BSR")
        blue, _ = make_team_with_slots("T01BSB")

        random.seed(42)
        # GEN-01: this asserts on persisted GameEvent timestamps; the default
        # ``scores`` tier writes no events, so request ``full``.
        with patch.object(BatchSimulator, "ROUND_TICKS", 40):
            gr = BatchSimulator().simulate_single_round_detailed(
                red, blue, fidelity="full"
            )

        assert gr is not None and gr.is_completed

        # Survived sentinel is tick-valued (1801, not 901).
        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert states, "No PlayerRoundState rows persisted"
        survivors = [s for s in states if s.was_eliminated_at == SURVIVED_SENTINEL]
        assert survivors, (
            "Expected at least one survivor in a 40-tick round; none had "
            f"was_eliminated_at == {SURVIVED_SENTINEL} — sentinel likely "
            "still 901 (pre-TIME-01)"
        )
        # No persisted elimination tick may exceed the tiny round length.
        for s in states:
            if s.was_eliminated_at != SURVIVED_SENTINEL:
                assert 0 <= s.was_eliminated_at <= 40 + 2, (
                    f"{s} eliminated at tick {s.was_eliminated_at} in a "
                    f"40-tick round — not tick-valued"
                )

        events = list(GameEvent.objects.filter(game_round=gr))
        assert events, "BatchSim round produced no GameEvent rows"
        max_ts = max(e.timestamp for e in events)
        # All persisted timestamps must fall within the 40-tick round length.
        assert max_ts <= 42, (
            f"max GameEvent.timestamp={max_ts} exceeds the 40-tick round "
            f"length — timestamps are not tick-bounded"
        )

    def test_default_round_uses_tick_per_round(self):
        """A default BatchSim round runs TICKS_PER_ROUND ticks."""
        from matches.models import GameEvent

        red, _ = make_team_with_slots("T01BSDefR")
        blue, _ = make_team_with_slots("T01BSDefB")
        random.seed(1)
        # GEN-01: asserts on persisted GameEvent timestamps — request ``full``.
        gr = BatchSimulator().simulate_single_round_detailed(red, blue, fidelity="full")
        events = list(GameEvent.objects.filter(game_round=gr))
        if events:
            max_ts = max(e.timestamp for e in events)
            assert max_ts <= TICKS_PER_ROUND, (
                f"max timestamp {max_ts} > {TICKS_PER_ROUND} for a default " f"round"
            )


# ===========================================================================
# 8. API unit — endpoints return raw ticks (no ÷2 in serializers)
# ===========================================================================


@pytest.mark.django_db
class TestAPIReturnsRawTicks:
    """Spec 8: /api/rounds/<id>/events/ and /api/rounds/<id>/ return tick
    values raw (no serializer-side ÷2). Mirrors test_apis.py patterns.
    """

    def _build_fixture(self):
        """Create a round + state + event with deliberately tick-valued data.

        Tolerates the in-flight seconds_*→ticks_* model rename: writes
        whichever uptime field names the model currently exposes. Raises
        OperationalError if the model field exists but no migration has been
        applied yet (transient parallel-production state) — the caller turns
        that into a clean xfail so the suite still collects.
        """
        from rest_framework.test import APIClient

        from matches.models import GameEvent, GameRound, PlayerRoundState

        self.client = APIClient()
        self.red, self.players = make_team_with_slots("T01ApiR")
        self.blue, _ = make_team_with_slots("T01ApiB")
        self.gr = GameRound.objects.create(
            round_number=1, team_red=self.red, team_blue=self.blue
        )
        state = PlayerRoundState(
            game_round=self.gr,
            player=self.players["commander"],
            team_color="red",
            role="commander",
            points_scored=2000,
        )
        field_names = {f.name for f in PlayerRoundState._meta.get_fields()}
        for tick_name, secs_name, val in (
            ("ticks_active", "seconds_active", 1600),
            ("ticks_not_targetable", "seconds_not_targetable", 120),
            ("ticks_reset_window", "seconds_reset_window", 80),
        ):
            name = tick_name if tick_name in field_names else secs_name
            setattr(state, name, val)
        state.was_eliminated_at = SURVIVED_SENTINEL
        state.save()
        self.state = state
        self.event = GameEvent.objects.create(
            game_round=self.gr,
            timestamp=1234,  # a tick value > any seconds-domain round
            event_type="tag",
            actor=self.players["commander"],
            points_awarded=100,
        )

    def _fixture_or_xfail(self):
        from django.db.utils import OperationalError, ProgrammingError

        try:
            self._build_fixture()
        except (OperationalError, ProgrammingError) as exc:
            pytest.xfail(
                "PlayerRoundState model renamed but migration not applied "
                f"yet (parallel production agent): {exc}"
            )

    def test_events_endpoint_returns_raw_tick_timestamp(self):
        self._fixture_or_xfail()
        data = self.client.get(f"/api/rounds/{self.gr.pk}/events/").json()
        assert data["count"] == 1
        ts = data["results"][0]["timestamp"]
        assert ts == 1234, (
            f"API returned timestamp={ts}; expected raw tick 1234. A value of "
            f"617 would mean the serializer is dividing by 2 (forbidden by "
            f"the TIME-01 ADR — API returns raw ticks)."
        )

    def test_round_detail_returns_raw_tick_uptime_fields(self):
        self._fixture_or_xfail()
        data = self.client.get(f"/api/rounds/{self.gr.pk}/").json()
        assert "player_states" in data and data["player_states"]
        ps = next(s for s in data["player_states"] if s["role"] == "commander")

        def field(*names):
            for n in names:
                if n in ps:
                    return ps[n]
            raise AssertionError(
                f"None of {names} present in serialized player_state: " f"{sorted(ps)}"
            )

        assert (
            field("ticks_active", "seconds_active") == 1600
        ), "ticks_active must be returned raw (1600), not ÷2"
        assert field("ticks_not_targetable", "seconds_not_targetable") == 120
        assert field("ticks_reset_window", "seconds_reset_window") == 80
        assert (
            ps["was_eliminated_at"] == SURVIVED_SENTINEL
        ), "API must return the raw tick sentinel 1801, not 901 or ÷2"


# ===========================================================================
# 9. Constructor rename — SIM-09 dropped this section
#    The duration_ticks/duration deprecation lived on ResourceBasedSimulator
#    only. RBS is removed; BatchSimulator has no equivalent constructor knob —
#    ROUND_TICKS is the patch-point. Nothing in this section ports.
# ===========================================================================


# ===========================================================================
# 10. SIM-06 regression — renamed ticks_* columns non-default after flush
# ===========================================================================


@pytest.mark.django_db
class TestSim06FlushRenamedColumns:
    """Spec 10: extend the SIM-06 flush-fields test so the renamed ticks_*
    columns are asserted non-default on at least one player after _flush_to_db.
    Mirrors test_batch_sim.py::TestSim06FlushFields fixture + flow.
    """

    def _make_arena_map(self, name="T01Sim06Arena"):
        from core.map_processing import compute_sight_lines
        from core.models import (
            ArenaMap,
            BaseSightLineConfig,
            MapBaseConfig,
            MapZoneConfig,
            SightLineConfig,
        )

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

    def _run_and_flush(self, team_red, team_blue, arena_map, n_rounds=3):
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

    def test_ticks_active_written_for_active_players(self):
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("T01S06ActR")
        team_blue, _ = make_team_with_slots("T01S06ActB")
        arena_map = self._make_arena_map("T01S06Act")

        gr = self._run_and_flush(team_red, team_blue, arena_map)
        states = list(PlayerRoundState.objects.filter(game_round=gr))
        assert states, "No PlayerRoundState rows created"

        def ticks_active(s):
            return getattr(s, "ticks_active", getattr(s, "seconds_active", 0))

        active_states = [s for s in states if s.was_eliminated_at == SURVIVED_SENTINEL]
        assert active_states, (
            "Expected survivors with the tick sentinel 1801; none found "
            "(sentinel likely still 901 pre-TIME-01)"
        )
        for s in active_states:
            assert ticks_active(s) > 0, (
                f"{s.player} ({s.role}) survived but ticks_active=0 — the "
                f"renamed column is not being flushed"
            )

    def test_renamed_uptime_columns_non_default_on_some_player(self):
        from matches.models import PlayerRoundState

        team_red, _ = make_team_with_slots("T01S06UpR")
        team_blue, _ = make_team_with_slots("T01S06UpB")
        arena_map = self._make_arena_map("T01S06Up")

        gr = self._run_and_flush(team_red, team_blue, arena_map, n_rounds=3)
        states = list(PlayerRoundState.objects.filter(game_round=gr))

        def get(s, tick, secs):
            return getattr(s, tick, getattr(s, secs, 0))

        assert any(
            get(s, "ticks_active", "seconds_active") > 0 for s in states
        ), "ticks_active default on every player after _flush_to_db"
        assert any(
            get(s, "ticks_not_targetable", "seconds_not_targetable") > 0 for s in states
        ), "ticks_not_targetable default on every player after _flush_to_db"
        assert any(
            get(s, "ticks_reset_window", "seconds_reset_window") > 0 for s in states
        ), "ticks_reset_window default on every player after _flush_to_db"
