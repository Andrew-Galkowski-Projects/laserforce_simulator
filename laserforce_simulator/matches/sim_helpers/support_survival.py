"""Support-survival switches (2026-11-06): five switches, four on by default.

The LFS-01 Test set had the sim Medic eliminated in 99% of games (real 66%) and shot
twice as often as real, and the Ammo eliminated in 87% (real 45%). Each switch below
changes one piece of how supports are targeted, positioned, hidden or remembered.
Off, a switch leaves the engine exactly as it was; the calibration sweep ran all 31
combinations against the Test set. Switches 1-4 together (``THMA``) are the default:
switches 3 + 4 are the pair that moves the Medic (eliminated 1.00 -> 0.83, uptime 0.45
-> 0.63) and the Ammo (eliminated 0.89 -> 0.80); 1 adds a little on top and 2 costs
nothing. Switch 5 changed no cell once 3 + 4 were on, so it stays off.

CAL-03 (#588) adds permanent rules with no switch; their tunable values are the
module constants below (``DOUBLE_WINDOW_TICKS``, the doubles window of rule 1).

Pure Python; imports only ``time_constants`` so it is safe anywhere.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from .time_constants import _stale_table

#: 1. Target priority (``mechanics.choose_tag_target``): a hiding enemy is passed over
#: while a visible one is not hiding, and an enemy Medic is a target only while no
#: other enemy in sight is Active. The Active bonus multiplies the role weight.
TARGET_PRIORITY: bool = True
_PRIORITY_ROLE_WEIGHTS: dict[str, int] = {
    "commander": 10,
    "heavy": 10,
    "scout": 6,
    "ammo": 3,
    "medic": 10,
}
PRIORITY_ACTIVE_MULTIPLIER: int = 3

#: 2. Hidden support cells (``modes._defense_goal``): a support's Defense candidates
#: are the cells within ``SUPPORT_CELL_REACH`` steps of a cell that sees the Heavy,
#: and both roles value a cell by how hidden it is.
HIDDEN_SUPPORT_CELLS: bool = True
SUPPORT_CELL_REACH: int = 2

#: 3. The Medic holds its position: it may hide whatever its allies need (a low ally
#: comes to it), and ``resupply_ally`` sets it no walking goal.
MEDIC_HOLDS_POSITION: bool = True

#: 4. The Ammo hides freely: a needy ally no longer forbids its hide or pulls it out,
#: and a support's resupply walk (either role) values a cell by how hidden it is.
AMMO_HIDES_FREELY: bool = True

#: 5. Teamwork call-outs: the chance a sighting is shared is ``communication x
#: teamwork``, and a remembered enemy Medic or Ammo goes stale after
#: ``SUPPORT_STALENESS_TICKS`` (the Scout's 30, not the slow roles' 120).
TEAMWORK_CALLOUTS: bool = False
SUPPORT_STALENESS_TICKS: int = 30


def stale_table(time_domain: str) -> dict[str, int]:
    """The memory staleness table for *time_domain*: ``time_constants._stale_table``,
    with the support rows shortened on a tick-domain round under switch 5."""
    table = _stale_table(time_domain)
    if not TEAMWORK_CALLOUTS or time_domain != "ticks":
        return table
    return {**table, "medic": SUPPORT_STALENESS_TICKS, "ammo": SUPPORT_STALENESS_TICKS}


def callout_chance(actor) -> float:
    """Percent chance *actor* shares a sighting this tick: ``communication``, times
    ``teamwork / 100`` under switch 5."""
    chance = float(getattr(actor, "communication", 0))
    if TEAMWORK_CALLOUTS:
        chance *= getattr(actor, "teamwork", 50) / 100
    return chance


#: CAL-03 rule 1 (#588), doubles: after one support's single, the other support
#: may land the other kind for this many ticks although the receiver's pack reset
#: has started (``attempt_resupply``, ``choose_resupply_target``).
DOUBLE_WINDOW_TICKS: int = 1

#: The kind of Resupply each support role gives.
RESUPPLY_KIND: dict[str, str] = {"medic": "lives", "ammo": "shots"}


@dataclass(frozen=True)
class LastSingle:
    """The last Resupply a player received outside a double: its kind (``lives``
    or ``shots``), its tick and the supporter's ``tag_id_key``. ``paired`` is True
    once the other kind landed inside the window (the pair is used)."""

    kind: str
    tick: int
    supporter: str
    paired: bool = False


def double_window_open(teammate: Any, supporter: Any, tick: float) -> bool:
    """True while *teammate*'s last single is at most ``DOUBLE_WINDOW_TICKS`` old,
    it was the other kind from another supporter, no Down came after it, and its
    pair is unused: then *supporter* may deliver the second kind. No RNG."""
    single: Optional[LastSingle] = getattr(teammate, "last_single", None)
    kind = RESUPPLY_KIND.get(supporter.role)
    if single is None or kind is None or single.paired:
        return False
    if kind == single.kind or supporter.tag_id_key == single.supporter:
        return False
    if teammate.last_downed_time != single.tick:
        return False
    return 0 <= tick - single.tick <= DOUBLE_WINDOW_TICKS


def resupply_landed(
    teammate: Any, supporter: Any, tick: float, in_window: bool
) -> LastSingle:
    """The receiver's ``last_single`` after *supporter*'s Resupply landed at *tick*:
    the old one marked paired when it completed a double (``in_window``), else a
    new single."""
    if in_window:
        return replace(teammate.last_single, paired=True)
    return LastSingle(
        kind=RESUPPLY_KIND[supporter.role],
        tick=int(tick),
        supporter=supporter.tag_id_key,
    )


def hide_gate_open(role: str) -> bool:
    """True when switch 3 (Medic) or 4 (Ammo) lets *role* hide whatever its allies
    need, and never pulls it out of a hide."""
    if role == "medic":
        return MEDIC_HOLDS_POSITION
    if role == "ammo":
        return AMMO_HIDES_FREELY
    return False
