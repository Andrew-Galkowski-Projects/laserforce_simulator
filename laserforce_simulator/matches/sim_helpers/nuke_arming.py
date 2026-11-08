"""How a Commander arms a nuke (CAL #558, 2026-11-06).

On the LFS-01 Test set the sim detonated 0.55 nukes a game against a real 2.79.
Two things held it down: the awareness-tier gate (``weights._commander_nuke_gate``)
kept Commanders with 48 to 64 awareness waiting for 40 or 60 SP, so they ended a
round holding 45 SP unspent; and once the gate opened at 20 SP, half the nukes fired
were cancelled by a tag during the 8 to 14 tick fuse, because the Commander kept
shooting (and drawing reactions) while arming.

Pure Python; imports only ``support_survival`` (for the memory staleness table) and
``time_constants`` (the respawn window).

The rules added on 2026-11-07 come from the TDF event logs of 165 real games
(``docs/lfstats-import.md``, "When Commanders fire"): no support resupplies an
arming Commander, a per-nuke SP bank drawn from the real SP-at-activation table, a
``use_special`` boost just after the Commander comes back from a Down, and the
Down chokepoint ending the nuke (``down.record_down``).
"""

from __future__ import annotations

import math
import random

from . import support_survival
from .time_constants import RESPAWN_TICKS

#: While the fuse runs, the Commander rolls only ``hide``: Stationary, missed 50% of
#: the time, passed over as a target while a visible enemy exists, and no missile
#: lock can start on it. Measured on 12 Test rounds with the 20 SP gate: nukes
#: cancelled 1.29 -> 0.75 a Commander-round, detonated 1.29 -> 1.58.
HIDE_WHILE_ARMING: bool = True
ARMING_HIDE_WEIGHT: int = 100


def arming(player, second: float) -> bool:
    """True while *player* is a Commander whose nuke fuse has not run out.

    A Down zeroes ``special_active_until`` (the tag-cancel), so a Downed Commander
    is never arming.
    """
    return (
        getattr(player, "role", None) == "commander"
        and getattr(player, "special_active_until", 0) > second
    )


#: Two more arming gates (2026-11-07), on top of the 20 SP gate, read through
#: ``may_arm`` in ``weights._get_commander_weights``. 0 / False leave the gate open.
#:
#: The Commander arms only when every enemy it remembers (a non-stale memory entry:
#: a sighting or a teammate's call-out) is at least this many cells away, straight
#: line. Syracuse cells are 1 ft.
MIN_CELLS_FROM_KNOWN_ENEMY: int = 0
#: The Commander arms only while an enemy Commander or Heavy is Downed (inside its
#: respawn window) or eliminated: the one real-arena habit the owner sees, "only
#: nuke if one or both of heavy / commander are down".
REQUIRE_ENEMY_3HIT_DOWN: bool = False

_THREE_HIT = ("commander", "heavy")


def far_from_known_enemies(player, second: float, time_domain: str = "ticks") -> bool:
    """True when no non-stale enemy in *player*'s memory is within
    ``MIN_CELLS_FROM_KNOWN_ENEMY`` cells; always True with the gate at 0 or off a map.
    """
    minimum = MIN_CELLS_FROM_KNOWN_ENEMY
    row = getattr(player, "cell_row", None)
    col = getattr(player, "cell_col", None)
    memory = getattr(player, "player_memory", None)
    if minimum <= 0 or row is None or col is None or not memory:
        return True
    enemy_color = "blue" if player.team_color == "red" else "red"
    table = support_survival.stale_table(time_domain)
    for tag_id, entry in memory.items():
        if not tag_id.startswith(enemy_color):
            continue
        role = entry.get("role", "scout")
        if second - entry.get("timestamp", 0) > table.get(role, table["scout"]):
            continue
        cell = entry.get("cell")
        if cell is None:
            continue
        if math.hypot(int(cell[0]) - row, int(cell[1]) - col) < minimum:
            return False
    return True


def an_enemy_3hit_is_down(player, all_alive: list, second: float) -> bool:
    """True when an enemy Commander or Heavy is not Active at *second*: Downed and
    still inside the respawn window, or eliminated."""
    return any(
        p.team_color != player.team_color
        and getattr(p, "role", None) in _THREE_HIT
        and not p.is_active_at(second)
        for p in all_alive
    )


#: A Resupply resets the receiver's pack and ends a pending nuke, in the arena as
#: here (``combat._reset_resupplied``). Real teams simply do not resupply an arming
#: Commander (4 of 978 real activations, 0.4%, all 4 cancelled); the sim's own Medic
#: and Ammo did it 0.62 times a round, 45% of all sim cancels. On, an arming Commander
#: is not resupplyable (``PlayerState.is_resupplyable_at``), so ``choose_resupply_target``
#: passes it over and ``attempt_resupply`` gives it nothing, except for the nukes whose
#: slip roll came up: when the fuse starts (``fuse_lit``) each nuke rolls once, with
#: ``RESUPPLY_WHILE_ARMING_CHANCE``, whether its supports may still resupply it (the
#: owner: "some chance to resup during nuke arming, like 1-2%, based off real games").
#: The roll is per nuke, not per attempt (the sim's supports try about five times a
#: nuke, 21 refusals a round). A flagged nuke is reached by a Resupply about one time
#: in ten (a support has to be beside the Commander inside the 8 to 14 tick fuse), so
#: 0.10 realizes 1.2% of nukes ended by the Commander's own supports (counter, 128
#: Test rounds: 59 of 503 nukes flagged, 6 resupplied); 0.015 realized none in 500.
NO_RESUPPLY_WHILE_ARMING: bool = True
RESUPPLY_WHILE_ARMING_CHANCE: float = 0.10


def resupplyable(player, second: float) -> bool:
    """False while ``NO_RESUPPLY_WHILE_ARMING`` and the Commander's fuse runs, unless
    this nuke's slip roll came up (``player.nuke_resupplyable``)."""
    if not (NO_RESUPPLY_WHILE_ARMING and arming(player, second)):
        return True
    return bool(getattr(player, "nuke_resupplyable", False))


def fuse_lit(player) -> None:
    """The Commander fired: spend the SP bank and roll this nuke's resupply slip."""
    bank_spent(player)
    player.nuke_resupplyable = random.random() < RESUPPLY_WHILE_ARMING_CHANCE


#: Real Commanders do not fire the moment they reach 20 SP: of 978 real activations
#: 31% came at exactly 20 SP, 13% at 21, 26% at 22-24, 14% at 25-29, 9% at 30-39 and
#: 7% at 40 or more (median 22, mean 24.3). Each nuke draws its own threshold,
#: 20 + one entry of ``SP_BANK_EXTRA`` (102 entries, that shape), when the Commander
#: first holds 20 SP; the weight opens at that threshold and the draw is spent when
#: the nuke fires (``bank_spent``). Before this the gate was a flat ``sp > 20`` and
#: 74% of sim nukes fired at 21 SP.
SP_BANK_DRAW: bool = True
SP_BANK_EXTRA: tuple[int, ...] = (
    (0,) * 31
    + (1,) * 13
    + (2, 3, 4) * 9
    + (5, 6, 7, 8, 9) * 3
    + (10, 12, 13, 15, 16, 17, 18, 19, 19)
    + (20, 25, 30, 40, 45, 50, 60)
)
_SP_FLOOR = 20


def draw_sp_threshold() -> int:
    """One per-nuke SP threshold from the real table: 20 plus a bank."""
    return _SP_FLOOR + random.choice(SP_BANK_EXTRA)


def banked_enough(player) -> bool:
    """True when the Commander holds this nuke's drawn SP threshold; the draw is
    made on the first call and kept on ``player.nuke_sp_threshold`` until
    ``bank_spent``. Always True with ``SP_BANK_DRAW`` off."""
    if not SP_BANK_DRAW:
        return True
    threshold = getattr(player, "nuke_sp_threshold", None)
    if threshold is None:
        threshold = player.nuke_sp_threshold = draw_sp_threshold()
    return player.final_special >= threshold


def bank_spent(player) -> None:
    """The nuke fired: the next one draws its own threshold."""
    player.nuke_sp_threshold = None


#: Four fifths of real nukes start within about 12 s of the Commander coming back
#: from its own Down (41% 5-10 s after the Down began, 39% 10-20 s; the sim 22% and
#: 34%). While the SP gate is open and the Commander reactivated inside the last
#: ``AFTER_DOWN_BOOST_TICKS`` ticks (a Resupply reset counts, it stamps the same
#: field), ``use_special`` is multiplied by ``AFTER_DOWN_BOOST``. 1.0 is off. A hard
#: gate on the same window left SP unspent without lowering the cancel rate.
#: Measured (counter, 128 Test rounds): off, 24% of nukes start 5-10 s after the
#: Commander's Down began; at 2.0, 28% (fired 3.88 -> 3.97 a round, cancelled 20%
#: -> 21%, detonated 3.10 -> 3.14); 3.0 gave the same as 2.0, so 2.0 ships.
AFTER_DOWN_BOOST_TICKS: int = 20
AFTER_DOWN_BOOST: float = 2.0


def just_back_from_down(player, second: float) -> bool:
    """True inside the ``AFTER_DOWN_BOOST_TICKS`` window after *player* reactivated
    from its last Down (or Resupply reset)."""
    last = getattr(player, "last_downed_time", None)
    if last is None or AFTER_DOWN_BOOST_TICKS <= 0:
        return False
    back = second - last
    return RESPAWN_TICKS <= back < RESPAWN_TICKS + AFTER_DOWN_BOOST_TICKS


def may_arm(player, all_alive: list, second: float, time_domain: str = "ticks") -> bool:
    """The arming gates under their switches: distance from known enemies, an
    enemy 3-hit role down when ``REQUIRE_ENEMY_3HIT_DOWN`` is on, and the drawn
    SP bank."""
    if not far_from_known_enemies(player, second, time_domain):
        return False
    if REQUIRE_ENEMY_3HIT_DOWN and not an_enemy_3hit_is_down(player, all_alive, second):
        return False
    return banked_enough(player)
