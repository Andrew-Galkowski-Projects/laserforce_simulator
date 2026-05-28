"""Pure-unit tests for ``sim_helpers.shot.resolve_shot``.

The wide-Shot resolver — the single Shot → Hit → Tag → Down →
Elimination ladder consumed by all four call-site ``kind``s
(initial, follow_up, reaction, overwatch). Pinned by the seam
contract at ``.claude/worktrees/shot-resolver-seam-contract.md`` §1
(the 10-phase spec) and §4 (the required test classes).

Two behaviour changes are pinned here (deliberate; fold into the
already-pending post-MOVE-01 Score Calibration re-baseline):

  - **Uniform hide-50%-miss roll.** Pre-refactor only the initial-tag
    site checked ``defender.is_hiding`` and rolled the 50% miss; the
    four other sites skipped it. Post-refactor every ``kind`` rolls.
  - **Uniform Ammo non-decrement of ``final_shots``.** Pre-refactor
    the initial-tag hit/miss branches decremented even for an Ammo
    attacker; the four other sites + ``miss_hid`` skipped. Post-
    refactor Ammo never decrements ``final_shots`` regardless of kind.
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from matches.sim_helpers.event_log import EventLog
from matches.sim_helpers.pending_events import PendingFollowup, PendingReaction
from matches.sim_helpers.player_state import PlayerState
from matches.sim_helpers.round_context import RoundContext
from matches.sim_helpers.shot import (
    SHOT_KIND_FOLLOW_UP,
    SHOT_KIND_INITIAL,
    SHOT_KIND_OVERWATCH,
    SHOT_KIND_REACTION,
    ShotOutcome,
    resolve_shot,
)

# ---------------------------------------------------------------------------
# Factories
# ---------------------------------------------------------------------------


def _player(
    *,
    role: str = "scout",
    team_color: str = "red",
    final_lives: int = 10,
    final_shots: int = 20,
    accuracy: int = 50,
    survival: int = 50,
    player_awareness: int = 0,  # default 0 ⇒ never reacts / always follow-up triggers
    is_hiding: bool = False,
    is_holding: bool = False,
    last_downed_time: int | None = None,
    down_chain_count: int = 0,
    player_id: int = 1,
    name: str = "P",
    path_cache: tuple | None = None,
    committed_goal: tuple | None = None,
) -> PlayerState:
    """Minimal PlayerState for resolve_shot tests."""
    p = PlayerState(
        tag_id=f"{team_color}_{role}_{player_id}",
        name=name,
        team_color=team_color,
        role=role,
        accuracy=accuracy,
        survival=survival,
        starting_lives=final_lives,
        starting_shots=final_shots,
        final_lives=final_lives,
        final_shots=final_shots,
        player_awareness=player_awareness,
        is_hiding=is_hiding,
        is_holding=is_holding,
        last_downed_time=last_downed_time,
        down_chain_count=down_chain_count,
        player_id=player_id,
    )
    p._path_cache = path_cache
    p._committed_goal = committed_goal
    # Mirror production: BatchSimulator._make_players passes
    # shields=ROLE_STATS[role]["shield"] explicitly. The PlayerState
    # dataclass default is shields=1, which silently miscounts
    # commander/heavy hits in tests if left as-is.
    p.shields = p.max_shields
    return p


def _ctx(*, all_alive: list | None = None) -> RoundContext:
    return RoundContext(
        events=EventLog(persist=True),
        pending_nukes=[],
        pending_followups=[],
        pending_reactions=[],
        all_alive=all_alive if all_alive is not None else [],
        movement_ctx=None,
    )


def _events_by_type(ctx: RoundContext, etype: str) -> list:
    return [e for e in ctx.events.entries if e["event_type"] == etype]


# RNG patch helpers. ``hit_chance = (70 + acc - surv)``; with acc=99,
# surv=0 that's 95 (max). A patched random.randint return of 1 ⇒
# definite HIT under that clamp. With acc=0, surv=99 ⇒ hit_chance=10
# (min); a return of 99 ⇒ definite MISS.
def _patch_hit():
    return patch("matches.sim_helpers.shot.random.randint", return_value=1)


def _patch_miss():
    return patch("matches.sim_helpers.shot.random.randint", return_value=99)


def _patch_see_through_hide():
    # random.random() <= 0.5 ⇒ hide does NOT cause a miss_hid
    return patch("matches.sim_helpers.shot.random.random", return_value=0.0)


def _patch_hide_misses():
    # random.random() > 0.5 ⇒ hide CAUSES a miss_hid
    return patch("matches.sim_helpers.shot.random.random", return_value=0.99)


# ---------------------------------------------------------------------------
# TestResolveShotInitial — hit / miss / miss_hid / invalid + uniform-Ammo rule
# ---------------------------------------------------------------------------


class TestResolveShotInitial(unittest.TestCase):
    """Phase 1-8 happy paths for ``kind = SHOT_KIND_INITIAL``."""

    def test_hit_returns_shotoutcome_hit_true(self) -> None:
        attacker = _player(role="scout", accuracy=99, player_id=1)
        defender = _player(
            role="scout", team_color="blue", survival=0, player_id=2, final_lives=10
        )
        defender.shields = 1  # scout shield = 1 ⇒ one hit downs
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
            )
        self.assertIsInstance(outcome, ShotOutcome)
        self.assertTrue(outcome.hit)

    def test_hit_increments_tags_made_and_points(self) -> None:
        attacker = _player(role="scout", accuracy=99, player_id=1)
        defender = _player(role="scout", team_color="blue", survival=0, player_id=2)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.tags_made, 1)
        self.assertEqual(attacker.points_scored, 100)
        self.assertEqual(defender.points_scored, -20)
        self.assertEqual(defender.times_tagged, 1)

    def test_hit_consumes_attacker_shot(self) -> None:
        attacker = _player(role="scout", accuracy=99, final_shots=20)
        defender = _player(role="scout", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_shots, 19)

    def test_hit_stamps_last_shot_time(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=42, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.last_shot_time, 42)

    def test_hit_emits_tag_event(self) -> None:
        attacker = _player(role="scout", accuracy=99, player_id=1, name="A")
        defender = _player(
            role="scout", team_color="blue", survival=0, player_id=2, name="D"
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        tags = _events_by_type(ctx, "tag")
        self.assertEqual(len(tags), 1)
        evt = tags[0]
        self.assertEqual(evt["actor_id"], 1)
        self.assertEqual(evt["target_id"], 2)
        self.assertEqual(evt["timestamp"], 5)
        self.assertEqual(evt["points_awarded"], 100)
        md = evt["metadata"]
        self.assertEqual(md["actor_role"], "scout")
        self.assertEqual(md["target_role"], "scout")
        # INITIAL: no kind metadata flags
        self.assertNotIn("is_follow_up", md)
        self.assertNotIn("is_reaction", md)
        self.assertNotIn("overwatch", md)

    def test_hit_decrements_defender_shields(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        # commander has shields=3, scout shot_power=1; one hit ⇒ shields=2
        original_shields = defender.shields
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(defender.shields, original_shields - attacker.shot_power)

    def test_miss_returns_shotoutcome_hit_false(self) -> None:
        attacker = _player(role="scout", accuracy=0)
        defender = _player(role="scout", team_color="blue", survival=99)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
            )
        self.assertFalse(outcome.hit)
        self.assertFalse(outcome.downed)
        self.assertFalse(outcome.eliminated)

    def test_miss_increments_shots_missed(self) -> None:
        attacker = _player(role="scout", accuracy=0)
        defender = _player(role="scout", team_color="blue", survival=99)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.shots_missed, 1)
        self.assertEqual(attacker.tags_made, 0)

    def test_miss_emits_miss_event(self) -> None:
        attacker = _player(role="scout", accuracy=0, player_id=1, name="A")
        defender = _player(
            role="scout", team_color="blue", survival=99, player_id=2, name="D"
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        misses = _events_by_type(ctx, "miss")
        self.assertEqual(len(misses), 1)
        evt = misses[0]
        self.assertEqual(evt["actor_id"], 1)
        self.assertEqual(evt["target_id"], 2)
        self.assertEqual(evt["timestamp"], 5)
        self.assertEqual(evt["points_awarded"], 0)
        # plain miss has no "reason" metadata key
        self.assertNotIn("reason", evt["metadata"])

    def test_miss_hid_when_defender_hiding(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", is_hiding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
            )
        self.assertFalse(outcome.hit)
        self.assertFalse(outcome.downed)
        # miss_hid still costs the shot and increments shots_missed
        self.assertEqual(attacker.shots_missed, 1)
        self.assertEqual(attacker.final_shots, 19)

    def test_miss_hid_emits_miss_event_with_reason(self) -> None:
        attacker = _player(role="scout", accuracy=99, player_id=1, name="A")
        defender = _player(
            role="scout", team_color="blue", is_hiding=True, player_id=2, name="D"
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        misses = _events_by_type(ctx, "miss")
        self.assertEqual(len(misses), 1)
        self.assertEqual(misses[0]["metadata"].get("reason"), "hiding")

    def test_invalid_gate_zero_shots_no_event(self) -> None:
        attacker = _player(role="scout", final_shots=0)
        defender = _player(role="scout", team_color="blue")
        ctx = _ctx(all_alive=[attacker, defender])
        outcome = resolve_shot(
            attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
        )
        self.assertFalse(outcome.hit)
        self.assertEqual(ctx.events.entries, [])

    def test_invalid_gate_dead_defender_no_event(self) -> None:
        attacker = _player(role="scout", final_shots=20)
        defender = _player(role="scout", team_color="blue", final_lives=0)
        ctx = _ctx(all_alive=[attacker, defender])
        outcome = resolve_shot(
            attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
        )
        self.assertFalse(outcome.hit)
        self.assertEqual(ctx.events.entries, [])


# ---------------------------------------------------------------------------
# Behaviour change A — uniform Ammo non-decrement of final_shots
# ---------------------------------------------------------------------------


class TestResolveShotAmmoUniformity(unittest.TestCase):
    """Ammo attacker NEVER decrements ``final_shots`` regardless of kind
    (fixes the pre-refactor initial-tag asymmetry)."""

    def test_ammo_initial_hit_does_not_decrement_shots(self) -> None:
        attacker = _player(role="ammo", accuracy=99, final_shots=15)
        defender = _player(role="scout", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_shots, 15)

    def test_ammo_initial_miss_does_not_decrement_shots(self) -> None:
        attacker = _player(role="ammo", accuracy=0, final_shots=15)
        defender = _player(role="scout", team_color="blue", survival=99)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_shots, 15)

    def test_ammo_miss_hid_does_not_decrement_shots(self) -> None:
        attacker = _player(role="ammo", accuracy=99, final_shots=15)
        defender = _player(role="scout", team_color="blue", is_hiding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_shots, 15)

    def test_non_ammo_decrements_shots_on_miss(self) -> None:
        attacker = _player(role="scout", accuracy=0, final_shots=15)
        defender = _player(role="scout", team_color="blue", survival=99)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_shots, 14)


# ---------------------------------------------------------------------------
# Behaviour change B — uniform hide-50% miss roll across all kinds
# ---------------------------------------------------------------------------


class TestResolveShotHideUniformity(unittest.TestCase):
    """The ``defender.is_hiding`` 50%-miss roll fires on all four kinds
    (pre-refactor only initial-tag rolled it)."""

    def test_follow_up_against_hiding_can_miss_hid(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", is_hiding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            outcome = resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        self.assertFalse(outcome.hit)
        self.assertEqual(
            _events_by_type(ctx, "miss")[0]["metadata"].get("reason"), "hiding"
        )

    def test_reaction_against_hiding_can_miss_hid(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", is_hiding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx
            )
        self.assertFalse(outcome.hit)
        self.assertEqual(
            _events_by_type(ctx, "miss")[0]["metadata"].get("reason"), "hiding"
        )

    def test_overwatch_against_hiding_can_miss_hid(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", is_hiding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hide_misses():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx
            )
        self.assertFalse(outcome.hit)
        # overwatch flag should still be on the miss-event metadata even
        # though the cause was a hide-roll
        evt = _events_by_type(ctx, "miss")[0]
        self.assertEqual(evt["metadata"].get("reason"), "hiding")
        self.assertTrue(evt["metadata"].get("overwatch"))


# ---------------------------------------------------------------------------
# TestResolveShotFollowUp
# ---------------------------------------------------------------------------


class TestResolveShotFollowUp(unittest.TestCase):
    """``kind = SHOT_KIND_FOLLOW_UP`` — chain_depth, counter, scheduling."""

    def test_follow_up_increments_follow_up_shots(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        self.assertEqual(attacker.follow_up_shots, 1)

    def test_follow_up_metadata_carries_chain_and_flag(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        # commander has shields=3 so a single scout hit does NOT down ⇒ tag event emits
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        evt = _events_by_type(ctx, "tag")[0]
        self.assertTrue(evt["metadata"].get("is_follow_up"))
        self.assertEqual(evt["metadata"].get("chain"), 1)

    def test_follow_up_chain_depth_2_does_not_chain_further(self) -> None:
        """chain cap of 2: a follow-up at chain==2 doesn't schedule chain==3."""
        attacker = _player(role="scout", accuracy=99, player_id=1)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_id=2,
            player_awareness=99,  # high — would normally trigger follow-up
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=2,
            )
        # No new follow-up scheduled (deferred or immediate)
        self.assertEqual(ctx.pending_followups, [])

    def test_follow_up_downed_defender_stops_chain(self) -> None:
        """A follow-up whose hit downs the defender (shields → 0) does NOT
        schedule another follow-up — chain stops on a Down."""
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(
            role="scout",
            team_color="blue",
            survival=0,
            player_awareness=99,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        self.assertTrue(outcome.downed)
        self.assertEqual(ctx.pending_followups, [])

    def test_follow_up_does_not_schedule_reaction(self) -> None:
        """A follow-up never provokes a victim Reaction shot — only the
        initial Tag does."""
        attacker = _player(role="scout", accuracy=99)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_awareness=99,  # would react if eligible
            final_shots=10,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        self.assertEqual(ctx.pending_reactions, [])


# ---------------------------------------------------------------------------
# TestResolveShotReaction
# ---------------------------------------------------------------------------


class TestResolveShotReaction(unittest.TestCase):
    """``kind = SHOT_KIND_REACTION`` — counter + no re-react + no follow-up."""

    def test_reaction_increments_reaction_shots(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx)
        self.assertEqual(attacker.reaction_shots, 1)

    def test_reaction_metadata_carries_is_reaction_flag(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx)
        evt = _events_by_type(ctx, "tag")[0]
        self.assertTrue(evt["metadata"].get("is_reaction"))

    def test_reaction_does_not_re_react(self) -> None:
        """A reaction shot does NOT schedule another reaction from its
        target (no infinite ping-pong)."""
        attacker = _player(role="scout", accuracy=99)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_awareness=99,  # would react if eligible
            final_shots=10,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx)
        self.assertEqual(ctx.pending_reactions, [])

    def test_reaction_does_not_chain_followup(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_awareness=99,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx)
        self.assertEqual(ctx.pending_followups, [])


# ---------------------------------------------------------------------------
# TestResolveShotOverwatch
# ---------------------------------------------------------------------------


class TestResolveShotOverwatch(unittest.TestCase):
    """``kind = SHOT_KIND_OVERWATCH`` — overwatch metadata flag on tag/miss
    events; elimination_action stays 'tag' (not 'overwatch')."""

    def test_overwatch_tag_event_carries_overwatch_metadata(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx)
        evt = _events_by_type(ctx, "tag")[0]
        self.assertTrue(evt["metadata"].get("overwatch"))

    def test_overwatch_miss_event_carries_overwatch_metadata(self) -> None:
        attacker = _player(role="scout", accuracy=0)
        defender = _player(role="commander", team_color="blue", survival=99)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_miss(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx)
        evt = _events_by_type(ctx, "miss")[0]
        self.assertTrue(evt["metadata"].get("overwatch"))

    def test_overwatch_elimination_action_is_tag_not_overwatch(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=1)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx
            )
        self.assertTrue(outcome.eliminated)
        elim_evt = _events_by_type(ctx, "elimination")[0]
        self.assertEqual(elim_evt["metadata"]["elimination_action"], "tag")

    def test_overwatch_schedules_reaction_like_initial(self) -> None:
        """An Overwatch shot provokes a victim Reaction shot (just like the
        initial Tag it imitates)."""
        attacker = _player(role="scout", accuracy=99)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_awareness=99,
            final_shots=10,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        # Need to drive both the hit roll and the reaction roll. Patch
        # randint to a sequence: first call hit (return 1), second call
        # reaction-eligibility (defender_awareness=99 vs random.randint(0,
        # 100) → return 10 ⇒ 99 >= 10 ⇒ react).
        with patch(
            "matches.sim_helpers.shot.random.randint", side_effect=[1, 10, 0]
        ), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx)
        self.assertTrue(
            len(ctx.pending_reactions) > 0
            or any(
                e["event_type"] == "tag" and e["metadata"].get("is_reaction")
                for e in ctx.events.entries
            )
        )

    def test_overwatch_chains_followup_like_initial(self) -> None:
        """An Overwatch hit that doesn't Down can chain a follow-up (just
        like the initial Tag it imitates)."""
        attacker = _player(role="scout", accuracy=99)
        defender = _player(
            role="commander",
            team_color="blue",
            survival=0,
            player_awareness=0,  # low ⇒ follow-up triggers (player_awareness < random)
        )
        ctx = _ctx(all_alive=[attacker, defender])
        # First randint=hit(1), second=reaction-eligibility roll (defender
        # player_awareness=0 < 99 ⇒ no reaction), third=follow-up-trigger
        # roll (player_awareness=0 ≱ 99 ⇒ chain). cd_ticks for scout = 1,
        # so the follow-up defers into pending_followups.
        with patch(
            "matches.sim_helpers.shot.random.randint", side_effect=[1, 99, 99]
        ), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_OVERWATCH, ctx=ctx)
        # Either a deferred follow-up was scheduled OR an immediate chain
        # produced a second tag event.
        chained = (
            len(ctx.pending_followups) > 0
            or sum(1 for e in ctx.events.entries if e["event_type"] == "tag") >= 2
        )
        self.assertTrue(chained)


# ---------------------------------------------------------------------------
# TestResolveShotDownChain — the Down / Elimination cascade + record_down
# ---------------------------------------------------------------------------


class TestResolveShotDownChain(unittest.TestCase):
    """The Shot → Down → Elimination ladder, including record_down
    side-effects (path cache, holding, committed goal, medic_reset,
    nuke_cancelled)."""

    def test_heavy_one_shots_downs_target(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=5)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
            )
        self.assertTrue(outcome.hit)
        self.assertTrue(outcome.downed)
        self.assertFalse(outcome.eliminated)
        self.assertEqual(defender.final_lives, 4)
        self.assertEqual(defender.shields, defender.max_shields)
        self.assertEqual(defender.last_downed_time, 5)

    def test_heavy_one_shots_eliminates_last_life(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=1)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            outcome = resolve_shot(
                attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx
            )
        self.assertTrue(outcome.eliminated)
        self.assertEqual(defender.final_lives, 0)
        self.assertEqual(defender.was_eliminated_at, 5)

    def test_elimination_event_emitted_with_action(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=1)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        elims = _events_by_type(ctx, "elimination")
        self.assertEqual(len(elims), 1)
        self.assertEqual(elims[0]["metadata"]["elimination_action"], "tag")

    def test_follow_up_elimination_action_is_follow_up_tag(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=1)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(
                attacker,
                defender,
                tick=5,
                kind=SHOT_KIND_FOLLOW_UP,
                ctx=ctx,
                chain_depth=1,
            )
        elim = _events_by_type(ctx, "elimination")[0]
        self.assertEqual(elim["metadata"]["elimination_action"], "follow_up_tag")

    def test_reaction_elimination_action_is_reaction(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, final_lives=1)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_REACTION, ctx=ctx)
        elim = _events_by_type(ctx, "elimination")[0]
        self.assertEqual(elim["metadata"]["elimination_action"], "reaction")

    def test_down_clears_path_cache(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(
            role="scout",
            team_color="blue",
            survival=0,
            path_cache=(((5, 5),), [(5, 6)], (5, 5)),
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertIsNone(defender._path_cache)

    def test_down_clears_is_holding(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(role="scout", team_color="blue", survival=0, is_holding=True)
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertFalse(defender.is_holding)

    def test_down_clears_action_driven_committed_goal(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(
            role="scout",
            team_color="blue",
            survival=0,
            committed_goal=((5, 5), True, 14),
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertIsNone(defender._committed_goal)

    def test_down_preserves_positioning_committed_goal(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        goal = ((5, 5), False, 14)
        defender = _player(
            role="scout",
            team_color="blue",
            survival=0,
            committed_goal=goal,
        )
        ctx = _ctx(all_alive=[attacker, defender])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(defender._committed_goal, goal)

    def test_medic_redown_within_cooldown_emits_medic_reset(self) -> None:
        attacker = _player(role="heavy", accuracy=99, player_id=1)
        # Medic is freshly Downed at tick=5, still in cooldown at tick=10
        medic = _player(
            role="medic",
            team_color="blue",
            survival=0,
            final_lives=5,
            last_downed_time=5,
            down_chain_count=1,
            player_id=2,
        )
        ctx = _ctx(all_alive=[attacker, medic])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, medic, tick=10, kind=SHOT_KIND_INITIAL, ctx=ctx)
        resets = _events_by_type(ctx, "medic_reset")
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0]["actor_id"], 2)

    def test_attacker_medic_hits_increments_on_hit_against_medic(self) -> None:
        attacker = _player(role="scout", accuracy=99, player_id=1)
        medic = _player(
            role="medic", team_color="blue", survival=0, final_lives=5, player_id=2
        )
        ctx = _ctx(all_alive=[attacker, medic])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, medic, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.medic_hits, 1)

    def test_commander_down_during_own_nuke_emits_cancelled(self) -> None:
        from matches.sim_helpers.pending_events import PendingNuke

        attacker = _player(role="heavy", accuracy=99, player_id=1)
        cmdr = _player(
            role="commander",
            team_color="blue",
            survival=0,
            final_lives=5,
            player_id=2,
        )
        cmdr.special_active_until = 200
        nuke = PendingNuke(complete_time=200, player=cmdr)
        ctx = _ctx(all_alive=[attacker, cmdr])
        ctx.pending_nukes.append(nuke)
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, cmdr, tick=100, kind=SHOT_KIND_INITIAL, ctx=ctx)
        cancels = _events_by_type(ctx, "nuke_cancelled")
        self.assertEqual(len(cancels), 1)
        self.assertTrue(nuke.cancel_logged)

    def test_commander_down_clears_special_active_until(self) -> None:
        """When a Commander is Downed during its own nuke fuse, the tag-cancel
        sets ``special_active_until = 0`` so the nuke-resolution guard
        (``special_active_until >= complete_time``) catches it."""
        attacker = _player(role="heavy", accuracy=99)
        cmdr = _player(role="commander", team_color="blue", survival=0, final_lives=5)
        cmdr.special_active_until = 200
        ctx = _ctx(all_alive=[attacker, cmdr])
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, cmdr, tick=100, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(cmdr.special_active_until, 0)


# ---------------------------------------------------------------------------
# TestResolveShotSpecialPoints — non-Heavy attackers gain SP on hits
# ---------------------------------------------------------------------------


class TestResolveShotSpecialPoints(unittest.TestCase):
    """Phase 7: non-Heavy attackers gain +1 SP on a hit (Heavy excluded)."""

    def test_scout_gains_sp_on_hit(self) -> None:
        attacker = _player(role="scout", accuracy=99)
        defender = _player(role="commander", team_color="blue", survival=0)
        ctx = _ctx(all_alive=[attacker, defender])
        attacker.final_special = 5
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_special, 6)

    def test_heavy_does_not_gain_sp_on_hit(self) -> None:
        attacker = _player(role="heavy", accuracy=99)
        defender = _player(
            role="commander", team_color="blue", survival=0, final_lives=10
        )
        ctx = _ctx(all_alive=[attacker, defender])
        attacker.final_special = 5
        with _patch_hit(), _patch_see_through_hide():
            resolve_shot(attacker, defender, tick=5, kind=SHOT_KIND_INITIAL, ctx=ctx)
        self.assertEqual(attacker.final_special, 5)


if __name__ == "__main__":
    unittest.main()
