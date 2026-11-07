"""Support-survival switches (2026-11-06): five switches, four on by default.

The LFS-01 Test set had the sim Medic eliminated in 99% of games (real 66%) and shot
twice as often as real, and the Ammo eliminated in 87% (real 45%). Each switch below
changes one piece of how supports are targeted, positioned, hidden or remembered.
Off, a switch leaves the engine exactly as it was; the calibration sweep ran all 31
combinations against the Test set. Switches 1-4 together (``THMA``) are the default:
switches 3 + 4 are the pair that moves the Medic (eliminated 1.00 -> 0.83, uptime 0.45
-> 0.63) and the Ammo (eliminated 0.89 -> 0.80); 1 adds a little on top and 2 costs
nothing. Switch 5 changed no cell once 3 + 4 were on, so it stays off.

Pure Python; imports only ``time_constants`` so it is safe anywhere.
"""

from __future__ import annotations

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


def hide_gate_open(role: str) -> bool:
    """True when switch 3 (Medic) or 4 (Ammo) lets *role* hide whatever its allies
    need, and never pulls it out of a hide."""
    if role == "medic":
        return MEDIC_HOLDS_POSITION
    if role == "ammo":
        return AMMO_HIDES_FREELY
    return False
