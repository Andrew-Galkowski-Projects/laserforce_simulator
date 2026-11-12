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
module constants below (``DOUBLE_WINDOW_TICKS`` for rule 1, then rules 2-9).

Pure Python; imports only ``time_constants`` at module level so it is safe anywhere
(``hold_allows`` imports ``resupply_queue._is_in_los`` when called).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Literal, Optional

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

#: The two kinds of Resupply (CAL-04d).
ResupplyKind = Literal["lives", "shots"]
#: The kind of Resupply each support role gives.
RESUPPLY_KIND: dict[str, ResupplyKind] = {"medic": "lives", "ammo": "shots"}


@dataclass(frozen=True)
class LastSingle:
    """The last Resupply a player received outside a double: its kind (``lives``
    or ``shots``), its tick and the supporter's ``tag_id_key``. ``paired`` is True
    once the other kind landed inside the window (the pair is used)."""

    kind: ResupplyKind
    tick: int
    supporter: str
    paired: bool = False


def double_window_open(teammate: Any, supporter: Any, tick: float) -> bool:
    """True while *teammate*'s last single is at most ``DOUBLE_WINDOW_TICKS`` old,
    it was the other kind from another supporter, no Down came after it, and its
    pair is unused: then *supporter* may deliver the second kind. No RNG."""
    single: Optional[LastSingle] = teammate.last_single
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


#: CAL-03 rule 2, idle trade: the chance an idle support's roll (an empty
#: ``resupply_ally`` or a ``hide``) Tags an enemy in sight instead.
IDLE_TRADE_RATE: float = 0.4
#: CAL-03 rule 3: at this many lives or fewer the Ammo's Defense cell anchors on its
#: Medic (``modes._ammo_anchor_goal``) ...
AMMO_ANCHOR_LIVES: int = 15
#: ... and at this many or fewer it walks to its Medic (``choose_goal_cell`` step 1).
AMMO_SEEK_MEDIC_LIVES: int = 6
#: CAL-03 rule 5, Medic back: the Medic's Defense candidates reach this many steps
#: from a cell that sees the Heavy, and a cell is valued ``HIDDEN x (1 - los_rank) +
#: DEPTH x depth from the enemy Base``. The Ammo's anchor cells use the same rule.
MEDIC_BACK_REACH: int = 3
MEDIC_BACK_HIDDEN_WEIGHT: float = 0.5
MEDIC_BACK_DEPTH_WEIGHT: float = 0.5
#: CAL-03 rules 7 and 8: a player at this many lives or fewer is critical.
CALLOUT_LIVES: int = 3
#: CAL-03 rule 7: a support forgets a call-out this many ticks after it was made.
CALLOUT_TICKS: int = 40
#: CAL-03 rule 9, support link: the Medic is "with" the Ammo within this many
#: squares (Chebyshev) or in its sight ...
LINK_SQUARES: int = 6
#: ... the Ammo goes back this many ticks after leaving it ...
LINK_RETURN_TICKS: int = 60
#: ... and its memory of the Medic is lost once older than this (the HOLD singles
#: case).
LINK_STALE_TICKS: int = 60

#: CAL-03 rule 6, Resupply visits: a Commander, Heavy or Scout starts one when its
#: lives or shots fall to ``visit_share`` of the cap ...
VISIT_BASE_SHARE: float = 1 / 3
VISIT_SPREAD: float = 0.1
VISIT_SHARE_MIN: float = 0.15
VISIT_SHARE_MAX: float = 0.5
#: ... waits at the supports this many ticks for both before it takes a single ...
VISIT_WAIT_TICKS: int = 20
#: ... ends this many ticks after it arrived, or after it started ...
VISIT_TIMEOUT_TICKS: int = 60
VISIT_MAX_TICKS: int = 120
#: ... starts no new visit for this many ticks after one ends ...
VISIT_COOLDOWN_TICKS: int = 160
#: ... except at this share of the cap or less (step 1's old seek threshold): such a
#: Critical visit waits only this many ticks after one ends (CAL-04b, #600).
VISIT_SEEK_SHARE: float = 0.3
CRITICAL_COOLDOWN_TICKS: int = 80
#: CAL-04b (#600): a visit ends Satisfied once Lives and Shots are each at least
#: this share of the cap, or once the visitor has received this many doubles on it.
SATISFIED_SHARE: float = 2 / 3
SATISFIED_DOUBLES: int = 2
#: CAL-04 (#598): the Ammo leash's reach, Chebyshev squares from the Medic's
#: remembered cell (``combat._support_link``, ``pathfinding._leash_goal``, and
#: ``round_probe``'s measures) ...
LEASH_SQUARES: int = 10
#: ... and the leash fires once the Ammo has been beyond it for more than this
#: many ticks (CAL-04c, #601).
LEASH_TICKS: int = 10

#: The two support roles, Medic then Ammo (the order ``_own_supports`` returns).
SUPPORT_ROLES: tuple[str, ...] = ("medic", "ammo")


def partner_role(role: str) -> str:
    """The other support role: ``ammo`` for a Medic, else ``medic``."""
    return "ammo" if role == "medic" else "medic"


def visit_share(player: Any) -> float:
    """CAL-03 rule 6: the share of its lives or shots cap at which *player* starts a
    Resupply visit, ``VISIT_BASE_SHARE + VISIT_SPREAD x (decision_making - 50) /
    50`` clamped to [``VISIT_SHARE_MIN``, ``VISIT_SHARE_MAX``]. No RNG."""
    share = VISIT_BASE_SHARE + VISIT_SPREAD * (player.decision_making - 50) / 50
    return min(VISIT_SHARE_MAX, max(VISIT_SHARE_MIN, share))


#: A Resupply visit's phase (CAL-04d): ``wait`` for the supports, ``go`` to them,
#: or ``at`` them.
VisitPhase = Literal["wait", "go", "at"]


@dataclass(frozen=True)
class Visit:
    """A Resupply visit (CAL-03 rule 6, ``resupply_visit``): its ``phase``
    (``wait``, ``go`` or ``at``), the tick it ``started``, the ``arrived`` tick (a
    support first saw the player) and its ``goal`` cell. ``backline_done`` is True
    once the walk reached the player's ``backline_cell``; ``doubles`` counts the
    doubles received on this visit (CAL-04b, ``combat.attempt_resupply``)."""

    phase: VisitPhase
    started: int
    arrived: Optional[int] = None
    goal: Optional[tuple[int, int]] = None
    backline_done: bool = False
    doubles: int = 0


@dataclass(frozen=True)
class Callout:
    """CAL-03 rule 7 (``resupply_visit.call_out``): a teammate at
    ``CALLOUT_LIVES`` or fewer (``caller``, a ``tag_id_key``) called a support to
    ``cell`` at ``tick``."""

    caller: str
    cell: tuple[int, int]
    tick: int


def _cell(player: Any) -> Optional[tuple[int, int]]:
    if player.cell_row is None:
        return None
    return (player.cell_row, player.cell_col)


def medic_with_ammo(ammo: Any, medic: Any, movement_ctx: Any) -> bool:
    """True when *medic* is in *ammo*'s sight, or within ``LINK_SQUARES`` of it
    (Chebyshev, true cells). No RNG."""
    a, m = _cell(ammo), _cell(medic)
    if a is None or m is None:
        return False
    if max(abs(a[0] - m[0]), abs(a[1] - m[1])) <= LINK_SQUARES:
        return True
    return movement_ctx is not None and movement_ctx.can_see(a, m)


def own_teammate(player: Any, role: str, all_alive: list) -> Any:
    """*player*'s living teammate in *role* (not *player*), or None."""
    return next(
        (
            p
            for p in all_alive
            if p.team_color == player.team_color
            and p.role == role
            and p is not player
            and p.final_lives > 0
        ),
        None,
    )


def _ammo_singles_case(
    ammo: Any, all_alive: list, second: float, movement_ctx: Any
) -> bool:
    """The Ammo gives singles as before HOLD: its Medic is Eliminated, or the Medic
    is not with it and its memory of the Medic is missing or older than
    ``LINK_STALE_TICKS``. No RNG."""
    medic = own_teammate(ammo, "medic", all_alive)
    if medic is None:
        return True
    if medic_with_ammo(ammo, medic, movement_ctx):
        return False
    entry = ammo.player_memory.get(medic.tag_id_key)
    return (
        entry is None
        or entry.get("cell") is None
        or second - entry.get("timestamp", 0) > LINK_STALE_TICKS
    )


def hold_allows(
    support: Any, teammate: Any, all_alive: list, second: float, movement_ctx: Any
) -> bool:
    """CAL-03 rule 8, HOLD: may *support* give *teammate* a Resupply? True for a
    support teammate, a teammate on a visit or at ``CALLOUT_LIVES`` or fewer, one
    the other living support sees while Active, and anyone under the Ammo's
    singles case (``_ammo_singles_case``). No RNG."""
    from .resupply_queue import _is_in_los  # noqa: PLC0415 (resupply_queue -> here)

    if teammate.role in SUPPORT_ROLES or teammate.visit is not None:
        return True
    if 0 < teammate.final_lives <= CALLOUT_LIVES:
        return True
    if support.role == "ammo" and _ammo_singles_case(
        support, all_alive, second, movement_ctx
    ):
        return True
    other = own_teammate(support, partner_role(support.role), all_alive)
    return (
        other is not None
        and other is not teammate
        and other.is_active_at(second)
        and _is_in_los(teammate, other, movement_ctx)
    )


def hide_gate_open(role: str) -> bool:
    """True when switch 3 (Medic) or 4 (Ammo) lets *role* hide whatever its allies
    need, and never pulls it out of a hide."""
    if role == "medic":
        return MEDIC_HOLDS_POSITION
    if role == "ammo":
        return AMMO_HIDES_FREELY
    return False
