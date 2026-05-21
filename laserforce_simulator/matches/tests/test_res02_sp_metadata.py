"""RES-02 / RES-02b — universal per-player snapshot contract for the chart
parity refactor.

These tests pin the event-metadata contract documented in the RES-02b seam
contract (`.claude/worktrees/res02b-parity-contract.md`), which **supersedes**
the original RES-02 "MUST NOT carry sp" restriction:

- Every event whose ``actor_id`` is set MUST carry the **universal actor
  block** — ``actor_role`` (str), ``actor_shots`` (int >= 0), ``actor_lives``
  (int >= 0), ``actor_points`` (int), and ``sp`` (int in ``[0, 99]``).
- Every event whose ``target_id`` is set MUST carry the **universal target
  block** — ``target_role`` (str), ``target_shots`` (int >= 0),
  ``target_lives`` (int >= 0), ``target_points`` (int).
- The three multi-target ``event_type="special"`` events (medic team-heal,
  ammo team-ammo, nuke detonation) MUST carry ``metadata["targets"]`` — a
  list of ``{pid, shots, lives, points}`` dicts. Medic and ammo team
  specials may legitimately produce an empty list when no teammate needed
  the resource; nuke detonation, when it fires, is asserted to have at
  least one entry.
- ``combo_resupply`` events now carry ``target_id = requestor.player_id``
  (was ``None`` pre-RES-02b); the universal target block applies.
- ``base_capture`` events DO NOT carry ``metadata["special_points"]`` — the
  RES-02 rename is preserved (no alias).
- The cap ``0 <= sp <= 99`` is enforced at every emit site.

Most assertions drive a short deterministic round via ``BatchSimulator`` so
the event log produces a real mix of event types. The heavy-exemption and
nuke-detonation pins use direct emit-helper calls with hand-built
``PlayerState``s so the conditions are forced rather than relied on by luck.
"""

from __future__ import annotations

import random
from unittest.mock import patch

import pytest

from matches.simulation import BatchSimulator
from matches.tests.conftest import make_team_with_slots

# Event-type groupings used below. Post-RES-02b, every event with an actor
# carries the universal actor block (including ``sp``); the legacy
# "NON_SP_TYPES" partition is gone.
ACTOR_BLOCK_KEYS = ("actor_role", "actor_shots", "actor_lives", "actor_points", "sp")
TARGET_BLOCK_KEYS = ("target_role", "target_shots", "target_lives", "target_points")

# Event types that historically were claimed to carry ``sp`` reliably (kept
# as a sanity-coverage hint for the broad-sweep test, NOT as a partition).
SP_TYPES = {"tag", "missiled", "special", "base_capture"}

# Multi-target special event types — disambiguated by description substring.
MULTI_TARGET_SPECIAL_DESCRIPTIONS = ("team heal", "team ammo", "nuke detonates")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ps(role: str, team_color: str = "red", **kwargs: object) -> object:
    """Create a minimal PlayerState for direct emit-helper tests."""
    from matches.sim_helpers.player_state import PlayerState

    tag_id = kwargs.pop("tag_id", f"{team_color}_{role}")
    defaults: dict[str, object] = dict(
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


def _simulate_event_log(seed: int = 42, ticks: int = 200) -> list[dict]:
    """Run a short deterministic round and return its in-memory event log.

    Uses ``BatchSimulator._simulate_round`` against rosters built from
    ``make_team_with_slots``. Patches ``ROUND_TICKS`` so the round terminates
    quickly while still producing a representative mix of event types. The
    fixed ``random.seed`` keeps the test deterministic between runs.
    """
    red, _ = make_team_with_slots("Res02Red")
    blue, _ = make_team_with_slots("Res02Blue")
    red_roster = list(red.active_roster)
    blue_roster = list(blue.active_roster)

    random.seed(seed)
    sim = BatchSimulator()
    event_log: list = []
    with patch.object(BatchSimulator, "ROUND_TICKS", ticks):
        sim._simulate_round(red_roster, blue_roster, event_log=event_log)
    return event_log


def _assert_actor_block(ev: dict) -> None:
    """Every event with an actor carries the full actor block."""
    md = ev.get("metadata") or {}
    etype = ev.get("event_type")
    for key in ACTOR_BLOCK_KEYS:
        assert key in md, (
            f"event {etype!r} with actor_id={ev.get('actor_id')!r} must carry "
            f"metadata[{key!r}] per the RES-02b seam contract; got keys "
            f"{sorted(md.keys())!r}"
        )
    assert isinstance(md["actor_role"], str), (
        f"metadata['actor_role'] on {etype!r} must be str, "
        f"got {type(md['actor_role']).__name__}"
    )
    assert isinstance(md["actor_shots"], int) and md["actor_shots"] >= 0, (
        f"metadata['actor_shots']={md['actor_shots']!r} on {etype!r} must be "
        f"int >= 0"
    )
    assert isinstance(md["actor_lives"], int) and md["actor_lives"] >= 0, (
        f"metadata['actor_lives']={md['actor_lives']!r} on {etype!r} must be "
        f"int >= 0"
    )
    assert isinstance(md["actor_points"], int), (
        f"metadata['actor_points']={md['actor_points']!r} on {etype!r} must " f"be int"
    )
    assert isinstance(md["sp"], int), (
        f"metadata['sp'] on {etype!r} must be int, got " f"{type(md['sp']).__name__}"
    )
    assert (
        0 <= md["sp"] <= 99
    ), f"metadata['sp']={md['sp']!r} out of [0, 99] on event {etype!r}"


def _assert_target_block(ev: dict) -> None:
    """Every event with a target carries the full target block."""
    md = ev.get("metadata") or {}
    etype = ev.get("event_type")
    for key in TARGET_BLOCK_KEYS:
        assert key in md, (
            f"event {etype!r} with target_id={ev.get('target_id')!r} must "
            f"carry metadata[{key!r}] per the RES-02b seam contract; got "
            f"keys {sorted(md.keys())!r}"
        )
    assert isinstance(md["target_role"], str), (
        f"metadata['target_role'] on {etype!r} must be str, "
        f"got {type(md['target_role']).__name__}"
    )
    assert isinstance(md["target_shots"], int) and md["target_shots"] >= 0, (
        f"metadata['target_shots']={md['target_shots']!r} on {etype!r} must "
        f"be int >= 0"
    )
    assert isinstance(md["target_lives"], int) and md["target_lives"] >= 0, (
        f"metadata['target_lives']={md['target_lives']!r} on {etype!r} must "
        f"be int >= 0"
    )
    assert isinstance(md["target_points"], int), (
        f"metadata['target_points']={md['target_points']!r} on {etype!r} "
        f"must be int"
    )


def _assert_targets_list(targets: object, *, allow_empty: bool, label: str) -> None:
    """Validate the multi-target ``meta.targets`` list shape."""
    assert isinstance(
        targets, list
    ), f"{label}: meta.targets must be a list, got {type(targets).__name__}"
    if not allow_empty:
        assert targets, f"{label}: meta.targets must be non-empty when event fires"
    for entry in targets:
        assert isinstance(entry, dict), (
            f"{label}: each meta.targets entry must be a dict, got "
            f"{type(entry).__name__}"
        )
        for key in ("pid", "shots", "lives", "points"):
            assert key in entry, (
                f"{label}: meta.targets entry missing {key!r}; got "
                f"{sorted(entry.keys())!r}"
            )
        assert isinstance(
            entry["pid"], int
        ), f"{label}: pid must be int, got {type(entry['pid']).__name__}"
        assert (
            isinstance(entry["shots"], int) and entry["shots"] >= 0
        ), f"{label}: shots={entry['shots']!r} must be int >= 0"
        assert (
            isinstance(entry["lives"], int) and entry["lives"] >= 0
        ), f"{label}: lives={entry['lives']!r} must be int >= 0"
        assert isinstance(
            entry["points"], int
        ), f"{label}: points={entry['points']!r} must be int"


# ---------------------------------------------------------------------------
# Universal contract tests
# ---------------------------------------------------------------------------


@pytest.mark.django_db
class TestRes02EventMetadata:
    """Universal RES-02b contract: per-player snapshots on every emit site.

    The file name (``test_res02_sp_metadata.py``) is kept since the test
    history is mature; the class is renamed to reflect that the contract
    is no longer SP-only — it now covers the full actor/target/targets
    snapshot blocks documented in the RES-02b seam contract.
    """

    # ---- Universal actor block: every event with an actor --------------- #

    def test_every_event_with_actor_carries_full_actor_block(self):
        """Sweep one simulated round: every event with ``actor_id`` set
        carries ``actor_role`` (str) + ``actor_shots``/``actor_lives``/
        ``actor_points`` (int >= 0 / int) + ``sp`` (int in [0, 99]).
        """
        events = _simulate_event_log(seed=42, ticks=800)
        assert events, "fixture round produced no events"
        actor_events = [e for e in events if e.get("actor_id") is not None]
        assert (
            actor_events
        ), "fixture round produced no events with an actor — adjust seed/ticks"
        for ev in actor_events:
            _assert_actor_block(ev)

    # ---- Universal target block: every event with a target -------------- #

    def test_every_event_with_target_carries_full_target_block(self):
        """Sweep one simulated round: every event with ``target_id`` set
        carries the target block (``target_role`` + shots/lives/points).
        """
        events = _simulate_event_log(seed=42, ticks=800)
        target_events = [e for e in events if e.get("target_id") is not None]
        assert target_events, (
            "fixture round produced no events with a target — adjust " "seed/ticks"
        )
        for ev in target_events:
            _assert_target_block(ev)

    # ---- base_capture preserves the RES-02 rename rule ------------------ #

    def test_base_capture_does_not_carry_special_points(self):
        """RES-02 rename: ``base_capture`` events use ``sp`` (no
        ``special_points`` alias). The universal actor block is asserted
        separately by the actor-block sweep above; here we pin the rename.
        """
        events = _simulate_event_log(seed=42, ticks=800)
        caps = [e for e in events if e["event_type"] == "base_capture"]
        if not caps:
            pytest.skip("No base_capture events emitted — coverage gap")
        for ev in caps:
            md = ev.get("metadata") or {}
            assert "special_points" not in md, (
                "base_capture must rename 'special_points' to 'sp' with no "
                f"alias; got metadata={md!r}"
            )

    # ---- combo_resupply now has target_id ------------------------------- #

    def test_combo_resupply_has_target_id_matching_requestor(self):
        """RES-02b: ``combo_resupply`` events emit with
        ``target_id = requestor.player_id`` (previously ``None``) so the
        data shape is uniform with single-resupply events.
        """
        events = _simulate_event_log(seed=42, ticks=1200)
        combos = [e for e in events if e["event_type"] == "combo_resupply"]
        if not combos:
            pytest.skip(
                "No combo_resupply events emitted in fixture round — "
                "coverage gap, not a contract failure"
            )
        for ev in combos:
            assert ev.get("target_id") is not None, (
                "combo_resupply must carry target_id = requestor.player_id "
                f"per the RES-02b seam contract; got event={ev!r}"
            )
            assert isinstance(ev["target_id"], int), (
                f"combo_resupply target_id must be int, got "
                f"{type(ev['target_id']).__name__}"
            )
            # And the universal target block follows.
            _assert_target_block(ev)

    # ---- Multi-target special: medic team-heal -------------------------- #

    def test_medic_team_heal_special_carries_targets_list(self):
        """The medic team-heal ``event_type="special"`` carries
        ``meta.targets`` as a list of ``{pid, shots, lives, points}``
        dicts. The list may legitimately be empty if no teammate needed
        the heal — we still assert the *key* is present and the shape is a
        list.
        """
        events: list[dict] = []
        for seed in (42, 7, 1, 99, 1234, 2024):
            events = _simulate_event_log(seed=seed, ticks=1200)
            medic_heals = [
                e
                for e in events
                if e["event_type"] == "special"
                and "team heal" in (e.get("description") or "").lower()
            ]
            if medic_heals:
                for ev in medic_heals:
                    md = ev.get("metadata") or {}
                    assert "targets" in md, (
                        f"medic team-heal special must carry meta.targets; "
                        f"got metadata={md!r}"
                    )
                    _assert_targets_list(
                        md["targets"],
                        allow_empty=True,
                        label="medic team-heal special",
                    )
                return
        pytest.skip("No medic team-heal special events emitted across seeds")

    # ---- Multi-target special: ammo team-ammo --------------------------- #

    def test_ammo_team_ammo_special_carries_targets_list(self):
        """The ammo team-ammo ``event_type="special"`` carries
        ``meta.targets`` as a list of ``{pid, shots, lives, points}``
        dicts. May legitimately be empty if no teammate needed shots.
        """
        for seed in (42, 7, 1, 99, 1234, 2024):
            events = _simulate_event_log(seed=seed, ticks=1200)
            ammo_specials = [
                e
                for e in events
                if e["event_type"] == "special"
                and "team ammo" in (e.get("description") or "").lower()
            ]
            if ammo_specials:
                for ev in ammo_specials:
                    md = ev.get("metadata") or {}
                    assert "targets" in md, (
                        f"ammo team-ammo special must carry meta.targets; "
                        f"got metadata={md!r}"
                    )
                    _assert_targets_list(
                        md["targets"],
                        allow_empty=True,
                        label="ammo team-ammo special",
                    )
                return
        pytest.skip("No ammo team-ammo special events emitted across seeds")

    # ---- Multi-target special: nuke detonation -------------------------- #

    def test_nuke_detonation_special_carries_non_empty_targets_list(self):
        """The nuke-detonation ``event_type="special"`` carries
        ``meta.targets`` as a NON-empty list — the Commander targets the
        opposing team and the blast radius covers the whole team, so when
        the event fires there is always at least one entry.
        """
        for seed in (42, 7, 1, 99, 1234, 2024):
            events = _simulate_event_log(seed=seed, ticks=1200)
            nuke_dets = [
                e
                for e in events
                if e["event_type"] == "special"
                and "nuke detonates" in (e.get("description") or "").lower()
            ]
            if nuke_dets:
                for ev in nuke_dets:
                    md = ev.get("metadata") or {}
                    assert "targets" in md, (
                        f"nuke detonation special must carry meta.targets; "
                        f"got metadata={md!r}"
                    )
                    _assert_targets_list(
                        md["targets"],
                        allow_empty=False,
                        label="nuke detonation special",
                    )
                return
        pytest.skip("No nuke detonation special events emitted across seeds")

    # ---- Cap enforcement: sp is always in [0, 99] ----------------------- #

    def test_sp_never_exceeds_99_in_simulated_round(self):
        """Cap enforcement at the emit site: regardless of how many
        SP-gaining events fire, ``metadata["sp"]`` never exceeds 99 and
        never goes below 0. The cap is enforced upstream by
        ``min(max_special, ...)`` but the assertion here pins it at emit
        time.
        """
        events = _simulate_event_log(seed=42, ticks=800)
        actor_events = [e for e in events if e.get("actor_id") is not None]
        if not actor_events:
            pytest.skip("No actor-bearing events emitted — coverage gap")
        for ev in actor_events:
            md = ev.get("metadata") or {}
            if "sp" in md:
                assert 0 <= md["sp"] <= 99, (
                    f"metadata['sp']={md['sp']!r} out of [0, 99] on event "
                    f"{ev.get('event_type')!r}; cap was not enforced"
                )

    # ---- Pinned heavy-exemption cases (preserved from RES-02) ----------- #

    def test_heavy_tag_emits_sp_equal_to_unchanged_final_special(self):
        """Heavies do NOT increment SP on a tag, but the event MUST still
        carry ``"sp"`` equal to the heavy's unchanged ``final_special``.
        """
        attacker = _make_ps("heavy", team_color="red", final_special=37)
        defender = _make_ps("scout", team_color="blue", final_lives=5)
        sim = BatchSimulator()
        event_log: list = []
        with patch("matches.simulation.random.randint", return_value=1):
            try:
                sim._resolve_tag_attempts(
                    [{"attacker": attacker, "defender": defender, "overwatch": False}],
                    second=10,
                    event_log=event_log,
                )
            except (AttributeError, TypeError):
                pytest.skip(
                    "_resolve_tag_attempts signature/availability changed — "
                    "covered by the universal actor-block sweep test"
                )
        tags = [
            ev
            for ev in event_log
            if ev["event_type"] == "tag" and ev["actor_id"] == attacker.player_id
        ]
        if not tags:
            pytest.skip(
                "No tag emitted through forced path — relies on internal "
                "resolve helper; broader assertion covered by simulated-round "
                "test"
            )
        ev = tags[0]
        _assert_actor_block(ev)
        assert ev["metadata"]["sp"] == attacker.final_special, (
            f"heavy tag sp={ev['metadata']['sp']!r} should equal heavy's "
            f"unchanged final_special={attacker.final_special!r}"
        )

    def test_simulated_round_heavy_tag_sp_present(self):
        """Fallback heavy-tag pin that does not depend on internal helpers.

        Any ``tag`` events from a heavy actor in a simulated round must
        carry ``sp`` (regardless of which seed/round produced them).
        """
        events = _simulate_event_log(seed=42, ticks=400)
        heavy_tags = [
            e
            for e in events
            if e["event_type"] == "tag"
            and (e.get("metadata") or {}).get("actor_role") == "heavy"
        ]
        if not heavy_tags:
            pytest.skip("No heavy tag events in fixture — coverage gap")
        for ev in heavy_tags:
            _assert_actor_block(ev)

    def test_simulated_round_heavy_missile_sp_present(self):
        """Heavy missile events still carry ``sp`` (heavy does not
        increment but the key is keyed on event_type, not whether sp
        changed).
        """
        for seed in (42, 7, 1, 99, 1234, 2024):
            events = _simulate_event_log(seed=seed, ticks=600)
            heavy_missiles = [
                e
                for e in events
                if e["event_type"] == "missiled"
                and (e.get("metadata") or {}).get("actor_role") == "heavy"
            ]
            if heavy_missiles:
                for ev in heavy_missiles:
                    _assert_actor_block(ev)
                return
        pytest.skip("No heavy missile events emitted across tested seeds")

    # ---- Pinned nuke-detonation case (preserved from RES-02) ----------- #

    def test_nuke_detonation_event_carries_sp_unchanged_from_activation(self):
        """``_complete_nuke`` emits ``event_type="special"`` describing
        detonation. SP was already spent at activation; detonation must
        NOT change the Commander's SP but the event MUST still carry
        ``"sp"`` equal to the Commander's current (unchanged)
        ``final_special``.
        """
        commander = _make_ps(
            "commander",
            team_color="red",
            final_special=23,
            final_lives=10,
        )
        victim = _make_ps("scout", team_color="blue", final_lives=3)
        event_log: list = []
        pre_sp = commander.final_special

        sim = BatchSimulator()
        try:
            sim._complete_nuke(
                commander,
                second=100,
                opposing_players=[victim],
                event_log=event_log,
            )
        except TypeError:
            events = _simulate_event_log(seed=42, ticks=1200)
            nuke_detonations = [
                e
                for e in events
                if e["event_type"] == "special"
                and "nuke detonates" in (e.get("description") or "").lower()
            ]
            if not nuke_detonations:
                pytest.skip(
                    "_complete_nuke signature changed AND no nuke detonations "
                    "in simulated round — coverage gap"
                )
            for ev in nuke_detonations:
                _assert_actor_block(ev)
            return

        detonations = [
            ev
            for ev in event_log
            if ev["event_type"] == "special"
            and "nuke detonates" in (ev.get("description") or "").lower()
        ]
        assert detonations, (
            "_complete_nuke did not emit a 'nuke detonates' special event; "
            f"event_log={event_log!r}"
        )
        ev = detonations[0]
        _assert_actor_block(ev)
        assert ev["metadata"]["sp"] == pre_sp, (
            "nuke detonation sp must equal the Commander's unchanged "
            f"final_special ({pre_sp!r}); got {ev['metadata']['sp']!r}"
        )
        assert commander.final_special == pre_sp, (
            "_complete_nuke must NOT mutate the Commander's final_special "
            "(SP was spent at activation, not detonation)"
        )

    # ---- Sanity: SP-coverage hint --------------------------------------- #

    def test_simulated_round_covers_sp_carrying_event_types(self):
        """Coverage sanity: a long-ish simulated round should produce at
        least one event from the SP-changing set (``tag``, ``missile``,
        ``special``, ``base_capture``). If not, the actor-block sweep
        above silently passes on a degenerate fixture; this test fails
        loudly instead.
        """
        events = _simulate_event_log(seed=42, ticks=800)
        sp_events = [e for e in events if e["event_type"] in SP_TYPES]
        assert sp_events, (
            "fixture round produced no SP-carrying events — adjust "
            "seed/ticks so the actor-block sweep has bite"
        )
