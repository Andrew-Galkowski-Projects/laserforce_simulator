"""Pure-unit tests for ``sim_helpers.event_log.EventLog``.

13 per-event-type verbs × null-log + iteration + construction
behaviours. Pinned by the seam contract at
``.claude/worktrees/event-log-seam-contract.md`` §4.

The dict shape produced by every verb is byte-identical to the
pre-refactor inline ``event_log.append({...})`` literals, so
``BatchSimulator._flush_to_db``, ``build_highlights``, the missile
log view, and every existing analytics reader keep working
unchanged. These tests pin the 7-key dict shape and the metadata
schema per verb.

No Django ORM or test DB required — uses hand-built ``PlayerState``
dataclass instances.
"""

from __future__ import annotations

import subprocess
import sys
import unittest

from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.player_state import PlayerState

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _player(
    *,
    role: str = "scout",
    team_color: str = "red",
    player_id: int = 1,
    name: str = "P",
    final_lives: int = 10,
    final_shots: int = 20,
    final_special: int = 0,
    points_scored: int = 0,
) -> PlayerState:
    """Minimal PlayerState for EventLog tests."""
    p = PlayerState(
        tag_id=f"{team_color}_{role}_{player_id}",
        name=name,
        team_color=team_color,
        role=role,
        accuracy=50,
        survival=50,
        starting_lives=final_lives,
        starting_shots=final_shots,
        final_lives=final_lives,
        final_shots=final_shots,
        final_special=final_special,
        player_id=player_id,
    )
    p.points_scored = points_scored
    return p


def _attacker():
    return _player(role="scout", team_color="red", player_id=1, name="Alice")


def _defender():
    return _player(role="commander", team_color="blue", player_id=2, name="Bob")


# ---------------------------------------------------------------------------
# Construction + Read API
# ---------------------------------------------------------------------------


class TestEventLogConstruction(unittest.TestCase):
    """``EventLog(persist=True)`` records; ``persist=False`` is a no-op."""

    def test_default_is_persist_true(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5)
        self.assertEqual(len(log), 1)

    def test_persist_false_drops_all_emits(self) -> None:
        log = EventLog(persist=False)
        log.tag(_attacker(), _defender(), 5)
        log.miss(_attacker(), _defender(), 6)
        log.medic_reset(_attacker(), 7)
        self.assertEqual(len(log), 0)

    def test_persist_true_explicit(self) -> None:
        log = EventLog(persist=True)
        log.tag(_attacker(), _defender(), 5)
        self.assertEqual(len(log), 1)


class TestEventLogReadAPI(unittest.TestCase):
    """``entries`` / ``__iter__`` / ``__len__`` / ``__repr__``."""

    def test_entries_returns_live_list(self) -> None:
        log = EventLog(persist=True)
        log.tag(_attacker(), _defender(), 5)
        # entries is the live internal list (not a copy) — mutations
        # propagate. This is a documented contract.
        entries = log.entries
        log.miss(_attacker(), _defender(), 6)
        self.assertEqual(len(entries), 2)

    def test_iter_yields_entries_in_order(self) -> None:
        log = EventLog(persist=True)
        log.tag(_attacker(), _defender(), 5)
        log.miss(_attacker(), _defender(), 6)
        got = list(log)
        self.assertEqual(len(got), 2)
        self.assertEqual(got[0]["event_type"], "tag")
        self.assertEqual(got[1]["event_type"], "miss")

    def test_len_matches_entry_count(self) -> None:
        log = EventLog(persist=True)
        self.assertEqual(len(log), 0)
        log.tag(_attacker(), _defender(), 5)
        self.assertEqual(len(log), 1)
        log.miss(_attacker(), _defender(), 6)
        self.assertEqual(len(log), 2)

    def test_repr_distinguishes_persist_modes(self) -> None:
        self.assertIn("persist", repr(EventLog(persist=True)))
        self.assertIn("null", repr(EventLog(persist=False)))


# ---------------------------------------------------------------------------
# Verb 1: tag
# ---------------------------------------------------------------------------


class TestTagVerb(unittest.TestCase):
    """``tag(attacker, defender, tick, kind=..., chain_depth=...)`` emits
    ``event_type='tag'``, points=100, kind-specific description + metadata."""

    def _emit_initial(self) -> dict:
        log = EventLog()
        a = _attacker()
        d = _defender()
        log.tag(a, d, 5, kind="initial")
        return log.entries[0]

    def test_event_type_is_tag(self) -> None:
        self.assertEqual(self._emit_initial()["event_type"], "tag")

    def test_actor_and_target_ids(self) -> None:
        e = self._emit_initial()
        self.assertEqual(e["actor_id"], 1)
        self.assertEqual(e["target_id"], 2)

    def test_timestamp_passed_through(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 42)
        self.assertEqual(log.entries[0]["timestamp"], 42)

    def test_points_awarded_is_100(self) -> None:
        self.assertEqual(self._emit_initial()["points_awarded"], 100)

    def test_initial_description(self) -> None:
        self.assertEqual(self._emit_initial()["description"], "Alice tags Bob")

    def test_follow_up_description(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="follow_up", chain_depth=1)
        self.assertEqual(log.entries[0]["description"], "Alice follow-up tags Bob")

    def test_reaction_description(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="reaction")
        self.assertEqual(log.entries[0]["description"], "Alice reacts to Bob")

    def test_overwatch_reuses_initial_wording(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="overwatch")
        # The overwatch flag goes into metadata, not the description.
        self.assertEqual(log.entries[0]["description"], "Alice tags Bob")

    def test_metadata_has_actor_block(self) -> None:
        md = self._emit_initial()["metadata"]
        self.assertEqual(md["actor_role"], "scout")
        self.assertEqual(md["actor_shots"], 20)
        self.assertEqual(md["actor_lives"], 10)
        self.assertEqual(md["actor_points"], 0)
        self.assertEqual(md["sp"], 0)

    def test_metadata_has_target_block(self) -> None:
        md = self._emit_initial()["metadata"]
        # Factory defaults are final_shots=20, final_lives=10 — the
        # dataclass defaults, not the role-derived maxima.
        self.assertEqual(md["target_role"], "commander")
        self.assertEqual(md["target_shots"], 20)
        self.assertEqual(md["target_lives"], 10)
        self.assertEqual(md["target_points"], 0)

    def test_initial_has_no_kind_flag(self) -> None:
        md = self._emit_initial()["metadata"]
        self.assertNotIn("is_follow_up", md)
        self.assertNotIn("is_reaction", md)
        self.assertNotIn("overwatch", md)
        self.assertNotIn("chain", md)

    def test_follow_up_metadata_carries_chain(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="follow_up", chain_depth=2)
        md = log.entries[0]["metadata"]
        self.assertTrue(md["is_follow_up"])
        self.assertEqual(md["chain"], 2)

    def test_reaction_metadata_flag(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="reaction")
        self.assertTrue(log.entries[0]["metadata"]["is_reaction"])

    def test_overwatch_metadata_flag(self) -> None:
        log = EventLog()
        log.tag(_attacker(), _defender(), 5, kind="overwatch")
        self.assertTrue(log.entries[0]["metadata"]["overwatch"])


# ---------------------------------------------------------------------------
# Verb 2: miss
# ---------------------------------------------------------------------------


class TestMissVerb(unittest.TestCase):
    """``miss(attacker, defender, tick, kind=..., reason=...)`` emits
    ``event_type='miss'``, points=0."""

    def test_initial_miss(self) -> None:
        log = EventLog()
        log.miss(_attacker(), _defender(), 5)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "miss")
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Alice misses Bob")
        self.assertNotIn("reason", e["metadata"])

    def test_hiding_reason(self) -> None:
        log = EventLog()
        log.miss(_attacker(), _defender(), 5, reason="hiding")
        e = log.entries[0]
        self.assertEqual(e["description"], "Alice misses Bob (hiding)")
        self.assertEqual(e["metadata"]["reason"], "hiding")

    def test_follow_up_miss(self) -> None:
        log = EventLog()
        log.miss(_attacker(), _defender(), 5, kind="follow_up", chain_depth=1)
        e = log.entries[0]
        self.assertEqual(e["description"], "Alice follow-up miss on Bob")
        self.assertTrue(e["metadata"]["is_follow_up"])

    def test_reaction_miss(self) -> None:
        log = EventLog()
        log.miss(_attacker(), _defender(), 5, kind="reaction")
        e = log.entries[0]
        self.assertEqual(e["description"], "Alice reaction miss on Bob")
        self.assertTrue(e["metadata"]["is_reaction"])

    def test_overwatch_hiding_miss_carries_both_flags(self) -> None:
        """Overwatch + hide: metadata should have both overwatch=True
        and reason='hiding'."""
        log = EventLog()
        log.miss(_attacker(), _defender(), 5, kind="overwatch", reason="hiding")
        md = log.entries[0]["metadata"]
        self.assertTrue(md["overwatch"])
        self.assertEqual(md["reason"], "hiding")


# ---------------------------------------------------------------------------
# Verb 3: elimination
# ---------------------------------------------------------------------------


class TestEliminationVerb(unittest.TestCase):
    """``elimination(attacker, defender, tick, action=...)`` emits
    ``event_type='elimination'``, points=0, action-specific description."""

    def test_tag_action(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5, action="tag")
        e = log.entries[0]
        self.assertEqual(e["event_type"], "elimination")
        self.assertEqual(e["description"], "Bob eliminated by Alice")
        self.assertEqual(e["metadata"]["elimination_action"], "tag")

    def test_follow_up_tag_action(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5, action="follow_up_tag")
        self.assertEqual(
            log.entries[0]["description"], "Alice eliminates Bob (follow-up)"
        )

    def test_reaction_action(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5, action="reaction")
        self.assertEqual(
            log.entries[0]["description"], "Alice eliminates Bob (reaction)"
        )

    def test_missile_action(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5, action="missile")
        self.assertEqual(
            log.entries[0]["description"], "Bob eliminated by missile from Alice"
        )

    def test_nuke_action(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5, action="nuke")
        self.assertEqual(log.entries[0]["description"], "Bob eliminated by nuke")

    def test_default_action_is_tag(self) -> None:
        log = EventLog()
        log.elimination(_attacker(), _defender(), 5)
        self.assertEqual(log.entries[0]["metadata"]["elimination_action"], "tag")


# ---------------------------------------------------------------------------
# Verb 4: nuke_cancelled  (RV-02)
# ---------------------------------------------------------------------------


class TestNukeCancelledVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        cmd = _player(role="commander", player_id=7, name="Cmdr")
        log.nuke_cancelled(cmd, 100)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "nuke_cancelled")
        self.assertEqual(e["actor_id"], 7)
        self.assertIsNone(e["target_id"])
        self.assertEqual(e["timestamp"], 100)
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Cmdr nuke cancelled")

    def test_metadata_is_actor_block_only(self) -> None:
        log = EventLog()
        cmd = _player(role="commander", player_id=7, final_special=99)
        log.nuke_cancelled(cmd, 100)
        md = log.entries[0]["metadata"]
        self.assertEqual(md["actor_role"], "commander")
        self.assertEqual(md["sp"], 99)
        # No target block.
        self.assertNotIn("target_role", md)


# ---------------------------------------------------------------------------
# Verb 5: medic_reset  (RV-02)
# ---------------------------------------------------------------------------


class TestMedicResetVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        med = _player(role="medic", player_id=9, name="Doc")
        log.medic_reset(med, 50)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "medic_reset")
        self.assertEqual(e["actor_id"], 9)
        self.assertIsNone(e["target_id"])
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Doc medic reset (down-chain)")

    def test_metadata_actor_only(self) -> None:
        log = EventLog()
        med = _player(role="medic", final_special=15)
        log.medic_reset(med, 50)
        md = log.entries[0]["metadata"]
        self.assertEqual(md["actor_role"], "medic")
        self.assertEqual(md["sp"], 15)
        self.assertNotIn("target_role", md)


# ---------------------------------------------------------------------------
# Verb 6: special
# ---------------------------------------------------------------------------


class TestSpecialVerb(unittest.TestCase):
    def test_nuke_activation(self) -> None:
        log = EventLog()
        cmd = _player(role="commander", player_id=7, name="Cmdr")
        log.special(
            cmd,
            100,
            description="Cmdr activates nuke",
            metadata_extras={"fires_at": 108},
        )
        e = log.entries[0]
        self.assertEqual(e["event_type"], "special")
        self.assertEqual(e["actor_id"], 7)
        self.assertIsNone(e["target_id"])
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Cmdr activates nuke")
        self.assertEqual(e["metadata"]["fires_at"], 108)

    def test_nuke_detonation_points_and_targets(self) -> None:
        log = EventLog()
        cmd = _player(role="commander", player_id=7, name="Cmdr")
        log.special(
            cmd,
            108,
            description="Cmdr nuke detonates",
            points=500,
            metadata_extras={"targets": [10, 11, 12]},
        )
        e = log.entries[0]
        self.assertEqual(e["points_awarded"], 500)
        self.assertEqual(e["metadata"]["targets"], [10, 11, 12])

    def test_rapid_fire_activation(self) -> None:
        log = EventLog()
        scout = _player(role="scout", player_id=3, name="Speedy")
        log.special(scout, 50, description="Speedy activates rapid fire")
        e = log.entries[0]
        self.assertEqual(e["description"], "Speedy activates rapid fire")
        self.assertEqual(e["points_awarded"], 0)

    def test_metadata_has_actor_block(self) -> None:
        log = EventLog()
        cmd = _player(role="commander", final_special=99)
        log.special(cmd, 100, description="x")
        md = log.entries[0]["metadata"]
        self.assertEqual(md["actor_role"], "commander")
        self.assertEqual(md["sp"], 99)


# ---------------------------------------------------------------------------
# Verb 7: locking
# ---------------------------------------------------------------------------


class TestLockingVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        log.locking(_attacker(), _defender(), 30)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "locking")
        self.assertEqual(e["actor_id"], 1)
        self.assertEqual(e["target_id"], 2)
        self.assertEqual(e["timestamp"], 30)
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Alice locks on Bob")

    def test_metadata_has_both_blocks(self) -> None:
        log = EventLog()
        log.locking(_attacker(), _defender(), 30)
        md = log.entries[0]["metadata"]
        self.assertEqual(md["actor_role"], "scout")
        self.assertEqual(md["target_role"], "commander")


# ---------------------------------------------------------------------------
# Verb 8: missiled
# ---------------------------------------------------------------------------


class TestMissiledVerb(unittest.TestCase):
    def test_hit_result_points_500(self) -> None:
        log = EventLog()
        log.missiled(_attacker(), _defender(), 35, result="hit", friendly_fire=False)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "missiled")
        self.assertEqual(e["points_awarded"], 500)
        self.assertEqual(e["description"], "Alice hits Bob with missile")
        self.assertEqual(e["metadata"]["result"], "hit")
        self.assertFalse(e["metadata"]["friendly_fire"])

    def test_miss_result_points_0(self) -> None:
        log = EventLog()
        log.missiled(_attacker(), _defender(), 35, result="miss", friendly_fire=False)
        e = log.entries[0]
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Alice misses Bob with missile")
        self.assertEqual(e["metadata"]["result"], "miss")

    def test_friendly_fire_flag(self) -> None:
        log = EventLog()
        log.missiled(_attacker(), _defender(), 35, result="hit", friendly_fire=True)
        self.assertTrue(log.entries[0]["metadata"]["friendly_fire"])

    def test_metadata_has_required_four_keys(self) -> None:
        """RES-03 contract: metadata MUST include result + friendly_fire
        + actor_role + target_role."""
        log = EventLog()
        log.missiled(_attacker(), _defender(), 35, result="hit", friendly_fire=False)
        md = log.entries[0]["metadata"]
        for key in ("result", "friendly_fire", "actor_role", "target_role"):
            self.assertIn(key, md, f"missile metadata missing {key!r}")


# ---------------------------------------------------------------------------
# Verb 9: missile_dodge
# ---------------------------------------------------------------------------


class TestMissileDodgeVerb(unittest.TestCase):
    def test_actor_target_swap(self) -> None:
        """Defender is the protagonist of a dodge — actor=defender,
        target=attacker."""
        log = EventLog()
        att = _attacker()
        defn = _defender()
        log.missile_dodge(defn, att, 40)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "missile_dodge")
        self.assertEqual(e["actor_id"], defn.player_id)  # defender as actor
        self.assertEqual(e["target_id"], att.player_id)  # attacker as target
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Bob dodges missile from Alice")


# ---------------------------------------------------------------------------
# Verb 10: resupply_lives
# ---------------------------------------------------------------------------


class TestResupplyLivesVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        med = _player(role="medic", player_id=5, name="Doc")
        req = _player(role="scout", team_color="red", player_id=3, name="Tag")
        log.resupply_lives(med, req, 25)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "resupply_lives")
        self.assertEqual(e["actor_id"], 5)
        self.assertEqual(e["target_id"], 3)
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Doc heals Tag")

    def test_amount_in_metadata(self) -> None:
        log = EventLog()
        med = _player(role="medic", player_id=5, name="Doc")
        req = _player(role="scout", player_id=3, name="Tag")
        log.resupply_lives(med, req, 25, amount=2)
        self.assertEqual(log.entries[0]["metadata"]["amount"], 2)

    def test_amount_absent_when_none(self) -> None:
        log = EventLog()
        med = _player(role="medic", player_id=5, name="Doc")
        req = _player(role="scout", player_id=3, name="Tag")
        log.resupply_lives(med, req, 25)
        self.assertNotIn("amount", log.entries[0]["metadata"])


# ---------------------------------------------------------------------------
# Verb 11: resupply_ammo
# ---------------------------------------------------------------------------


class TestResupplyAmmoVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        amm = _player(role="ammo", player_id=4, name="Refill")
        req = _player(role="heavy", team_color="red", player_id=2, name="Hvy")
        log.resupply_ammo(amm, req, 28)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "resupply_ammo")
        self.assertEqual(e["actor_id"], 4)
        self.assertEqual(e["target_id"], 2)
        self.assertEqual(e["description"], "Refill resupplies Hvy")


# ---------------------------------------------------------------------------
# Verb 12: combo_resupply
# ---------------------------------------------------------------------------


class TestComboResupplyVerb(unittest.TestCase):
    def test_basic_shape(self) -> None:
        log = EventLog()
        med = _player(role="medic", team_color="red", player_id=5, name="Doc")
        amm = _player(role="ammo", team_color="red", player_id=4, name="Refill")
        req = _player(role="scout", team_color="red", player_id=3, name="Tag")
        log.combo_resupply(req, med, amm, 30)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "combo_resupply")
        # Convention: actor is medic, target is requestor.
        self.assertEqual(e["actor_id"], 5)
        self.assertEqual(e["target_id"], 3)
        self.assertEqual(e["points_awarded"], 0)
        self.assertEqual(e["description"], "Doc combo-resupplies Tag")

    def test_medic_and_ammo_tags_in_metadata(self) -> None:
        log = EventLog()
        med = _player(role="medic", team_color="red", player_id=5, name="Doc")
        amm = _player(role="ammo", team_color="red", player_id=4, name="Refill")
        req = _player(role="scout", team_color="red", player_id=3, name="Tag")
        log.combo_resupply(req, med, amm, 30)
        md = log.entries[0]["metadata"]
        self.assertEqual(md["medic_tag"], med.tag_id_key)
        self.assertEqual(md["ammo_tag"], amm.tag_id_key)


# ---------------------------------------------------------------------------
# Verb 13: base_capture
# ---------------------------------------------------------------------------


class TestBaseCaptureVerb(unittest.TestCase):
    def test_default_description_neutral(self) -> None:
        log = EventLog()
        log.base_capture(_attacker(), 60, base_id=15)
        e = log.entries[0]
        self.assertEqual(e["event_type"], "base_capture")
        self.assertEqual(e["actor_id"], 1)
        self.assertIsNone(e["target_id"])
        self.assertEqual(e["points_awarded"], 1001)
        self.assertEqual(e["description"], "Alice captures base neutral")
        self.assertEqual(e["metadata"]["base_id"], 15)

    def test_default_description_opposing(self) -> None:
        log = EventLog()
        log.base_capture(_attacker(), 60, base_id=14)
        self.assertEqual(log.entries[0]["description"], "Alice captures base opposing")

    def test_custom_description_for_awarded_neutral(self) -> None:
        log = EventLog()
        log.base_capture(
            _attacker(),
            900,
            base_id=15,
            description="Alice awarded neutral base",
        )
        self.assertEqual(log.entries[0]["description"], "Alice awarded neutral base")

    def test_metadata_extras_passed_through(self) -> None:
        log = EventLog()
        log.base_capture(
            _attacker(),
            60,
            base_id=15,
            metadata_extras={
                "shots_remaining": 17,
                "target_base_type": "neutral",
                "role": "scout",
            },
        )
        md = log.entries[0]["metadata"]
        self.assertEqual(md["shots_remaining"], 17)
        self.assertEqual(md["target_base_type"], "neutral")
        self.assertEqual(md["role"], "scout")
        # base_id is always in metadata.
        self.assertEqual(md["base_id"], 15)

    def test_custom_points(self) -> None:
        log = EventLog()
        log.base_capture(_attacker(), 60, base_id=15, points=2000)
        self.assertEqual(log.entries[0]["points_awarded"], 2000)


# ---------------------------------------------------------------------------
# Defensive: no Django imports leaked
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Subprocess fresh-import ``matches.sim_helpers.event_log`` and assert
    ``sys.modules`` has no module name starting with ``django``. Mirrors
    the HX-01 / HX-02 / RES-04 / shot-resolver-consolidation precedent.
    """

    def test_event_log_imports_no_django(self) -> None:
        code = (
            "import sys\n"
            "import matches.sim_helpers.event_log\n"
            "leaked = [m for m in sys.modules if m == 'django' or m.startswith('django.')]\n"
            "assert not leaked, f'leaked django modules: {leaked}'\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=15,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )


if __name__ == "__main__":
    unittest.main()
