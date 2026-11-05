"""MECH-07: Player Modes (spec #542).

A **Mode** (GLOSSARY.md) is how a player currently chooses where to go. Players
start in their role's default and switch on game-state triggers, using only what
they could know. The formulas are written out in docs/agents/matches/sim-helpers.md.

Pure, Django-free; map-less rounds never reach it. ``pathfinding`` imports this
module, so this module must not import ``pathfinding``.
"""

from __future__ import annotations

import random
from enum import StrEnum

from .follow_rule import Cell, open_trail
from .time_constants import _stale_table


class Mode(StrEnum):
    ATTACK = "attack"
    DEFENSE = "defense"
    CONTROL = "control"


# ── Switching (spec decisions 19, 20) ────────────────────────────────────
#: The least time in a Mode before the player may switch out of it.
MODE_DWELL_TICKS: int = 60
#: How often past the dwell the player re-reads the triggers.
MODE_CHECK_PERIOD_TICKS: int = 10
#: Row 1: lives or shots at or below this share of max.
LOW_RESOURCE_SHARE: float = 0.5
_LOW_RESOURCE_ROLES: tuple[str, ...] = ("commander", "scout")
#: Row 2: what each role wants when a fresh memory shows its own Heavy Downed.
_HEAVY_DOWNED_MODE: dict[str, Mode] = {
    "commander": Mode.CONTROL,
    "scout": Mode.ATTACK,
    "medic": Mode.CONTROL,
    "ammo": Mode.CONTROL,
}
#: The memory statuses the Heavy trigger reads as Downed (spec decision 27).
_DOWNED_STATUSES: tuple[str, ...] = ("downed", "reset_window")

# ── Trail avoidance (spec decisions 10, 22) ──────────────────────────────
#: What a step into a band cell of a remembered Downed enemy's Trail adds.
TRAIL_COST: float = 3.0

# ── Weights (spec decisions 8, 21) ───────────────────────────────────────
MODE_WEIGHT_MULTIPLIER: float = 1.25
# Like weights._HIDE_IDX, these mirror combat._ACTION_IDX (tag_player, resupply_ally,
# capture_base, hold): player_state imports this module, so it stays free of combat.
_MODE_WEIGHT_SLOTS: dict[Mode, tuple[int, ...]] = {
    Mode.ATTACK: (0,),
    Mode.DEFENSE: (5,),
    Mode.CONTROL: (3, 8),
}

_DEFAULT_MODE: dict[str, Mode] = {
    "commander": Mode.ATTACK,
    "scout": Mode.ATTACK,
    "heavy": Mode.CONTROL,
    "medic": Mode.DEFENSE,
    "ammo": Mode.DEFENSE,
}


def default_mode(role: str) -> Mode:
    """The Mode *role* starts in and returns to; Attack for an unknown role."""
    return _DEFAULT_MODE.get(role, Mode.ATTACK)


def _fresh_entry(
    player, tag_id: str, tick: float, time_domain: str = "ticks"
) -> dict | None:
    """*tag_id*'s memory entry while fresh, by the role table ``_cell_from_memory``
    uses (an entry with no role goes stale quickly), else None."""
    entry = player.player_memory.get(tag_id)
    if entry is None:
        return None
    table = _stale_table(time_domain)
    threshold = table.get(entry.get("role", "scout"), table["scout"])
    return None if tick - entry.get("timestamp", 0) > threshold else entry


def _shows_downed(entry: dict) -> bool:
    """True for ``downed`` and ``reset_window``. Nuke and medic-alert entries have
    no ``status``, which counts as ``active``."""
    return entry.get("status", "active") in _DOWNED_STATUSES


def _own_heavy_seen_downed(player, tick: float) -> bool:
    entry = _fresh_entry(player, f"{player.team_color}_heavy", tick)
    return entry is not None and _shows_downed(entry)


def _team_is_losing(player) -> bool:
    """True when a score broadcast named the other team. ``"tied"`` is not losing."""
    winning_team = player.score_broadcast_state.get("winning_team")
    return winning_team not in (None, "tied", player.team_color)


def _wanted_mode(player, tick: float) -> Mode:
    """The Mode the triggers ask for; the first matching row wins."""
    role = player.role
    if role in _LOW_RESOURCE_ROLES and (
        player.final_lives <= player.max_lives * LOW_RESOURCE_SHARE
        or player.final_shots <= player.max_shots * LOW_RESOURCE_SHARE
    ):
        return Mode.CONTROL
    if role in _HEAVY_DOWNED_MODE and _own_heavy_seen_downed(player, tick):
        return _HEAVY_DOWNED_MODE[role]
    if role == "heavy" and _team_is_losing(player):
        return Mode.ATTACK
    return default_mode(role)


def update_mode(player, tick: float) -> None:
    """One tick of Mode switching: count the tick, then, past the dwell and on the
    check cadence, switch toward the wanted Mode. See sim-helpers.md."""
    player.mode_ticks[player.mode] = player.mode_ticks.get(player.mode, 0) + 1
    if tick < player.mode_since + MODE_DWELL_TICKS or tick < player.mode_next_check:
        return
    player.mode_next_check = int(tick) + MODE_CHECK_PERIOD_TICKS
    wanted = _wanted_mode(player, tick)
    if wanted is player.mode or random.random() >= player.game_awareness / 100:
        return
    player.mode = wanted
    player.mode_since = int(tick)
    # An action goal (committed[1]) stays: it interrupts the Mode's goals anyway.
    committed = player._committed_goal
    if committed is not None and not committed[1]:
        player._committed_goal = None


def apply_mode_weights(player, weights: list) -> None:
    """Nudge the Mode's Actions in place by ``MODE_WEIGHT_MULTIPLIER``. No RNG.

    A zero stays zero. A negative raw slot is left for ``finalize_action_weights``
    to clamp rather than pushed further down.
    """
    for idx in _MODE_WEIGHT_SLOTS[player.mode]:
        if weights[idx] > 0:
            weights[idx] = round(weights[idx] * MODE_WEIGHT_MULTIPLIER)


def trail_costs(
    player, all_alive: list, tick: float, time_domain: str = "ticks"
) -> dict[Cell, float]:
    """The route cost of each cell in the open Trail of an enemy in *all_alive* the
    player freshly remembers as Downed. Trails that overlap cost the same as one,
    so the order of *all_alive* cannot change the result. No RNG."""
    costs: dict[Cell, float] = {}
    for other in all_alive:
        if other.team_color == player.team_color:
            continue
        trail = open_trail(other, tick)
        if trail is None:
            continue
        entry = _fresh_entry(player, other.tag_id_key, tick, time_domain)
        if entry is not None and _shows_downed(entry):
            costs.update(dict.fromkeys(trail.band, TRAIL_COST))
    return costs
