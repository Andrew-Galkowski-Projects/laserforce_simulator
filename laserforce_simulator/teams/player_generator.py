"""LG-00 pure player-generation module.

Pure Python — no Django imports, no ORM, no I/O, no global RNG state.
The RNG is INJECTED by the caller; this module never seeds `random` and never
calls module-level `random.*` functions. The companion view in `teams/views.py`
owns all Django-facing concerns (form, transaction, ORM writes).

Public surface (frozen by the LG-00 seam contract):

- ``draw_stats(rng, mean, std_dev) -> dict[str, int]``
- ``draw_preferred_roles(rng) -> list[str]``
- ``assign_slots(preferred_roles_per_player) -> dict[str, int | None]``

Module-level tuples:

- ``_STAT_FIELDS`` — the 19 ``Player`` stat-field names, in canonical order.
- ``_ROLE_NAMES`` — the 5 role strings (lowercase) used by
  ``Player.preferred_roles`` and ``PlayerRoundState.role``.
- ``_SLOT_KEYS`` — the 6 ``Team.slot_*`` FK names (Scout has two).

The 5-tuple of role names and the 19-tuple of stat fields are hand-rolled
locally rather than imported so this module stays Django-free. The Tests agent
pins this with a defensive "no Django imports leaked" check.
"""

import random
from typing import Sequence

# 5 role names that match `Player.preferred_roles` JSON entries and
# `PlayerRoundState.role` strings. Order is the canonical role order shared
# with `matches.sim_helpers.role_constants.ROLE_STATS`.
_ROLE_NAMES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")

# 6 slot keys matching the `Team.slot_*` FK attribute names. Scout has TWO
# slots (`scout_1`, `scout_2`), both bound to the `"scout"` role.
_SLOT_KEYS: tuple[str, ...] = (
    "commander",
    "heavy",
    "scout_1",
    "scout_2",
    "medic",
    "ammo",
)

# 19 stat-field names on `Player`, in canonical order: 3 awareness, 1 decision,
# 5 physical, 2 team, 8 role. NOTE: `Offensive_synergy` is intentionally
# capital-O — it must match the field name on `Player` byte-for-byte (the view
# does `Player(**stats)`).
_STAT_FIELDS: tuple[str, ...] = (
    # 3 awareness
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    # 1 decision
    "decision_making",
    # 5 physical
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    # 2 team
    "communication",
    "teamwork",
    # 8 role
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)


def draw_stats(rng: random.Random, mean: float, std_dev: float) -> dict[str, int]:
    """Return one stat dict for a generated Player.

    PURE: receives the RNG as an argument; never reads global random state.

    Returns a dict keyed by every name in ``_STAT_FIELDS`` (exactly 19 keys),
    insertion-ordered to match ``_STAT_FIELDS``. Each value is
    ``max(0, min(100, round(rng.gauss(mean, std_dev))))`` — an integer clamped
    to ``[0, 100]``. Stats are drawn independently in ``_STAT_FIELDS`` order to
    keep RNG consumption deterministic given a seeded ``random.Random``.

    ``mean`` and ``std_dev`` are passed through verbatim — the form layer is
    responsible for range-checking them.
    """
    stats: dict[str, int] = {}
    for field in _STAT_FIELDS:
        raw = round(rng.gauss(mean, std_dev))
        stats[field] = max(0, min(100, raw))
    return stats


def draw_preferred_roles(rng: random.Random) -> list[str]:
    """Return a list of 1–3 unique role names drawn from ``_ROLE_NAMES``.

    Count distribution: 70% / 20% / 10% for length 1 / 2 / 3.
    Roles within a single draw are uniform without replacement.

    Implementation (pinned so RNG consumption is testable):
      1. ``n = rng.choices([1, 2, 3], weights=[70, 20, 10], k=1)[0]``
      2. ``rng.sample(_ROLE_NAMES, n)``
    """
    n = rng.choices([1, 2, 3], weights=[70, 20, 10], k=1)[0]
    return rng.sample(_ROLE_NAMES, n)


def assign_slots(
    preferred_roles_per_player: Sequence[Sequence[str]],
) -> dict[str, int | None]:
    """Greedy bipartite match of 6 players → 6 slot keys.

    Input: a length-6 sequence where each element is a player's
    ``preferred_roles`` list (a sequence of role names from ``_ROLE_NAMES``).
    The view trims to ``players[:6]`` BEFORE calling.

    Output: a dict keyed by ``_SLOT_KEYS`` (length 6), value is the player
    INDEX (0–5) assigned to that slot, or ``None`` if no preferring player was
    available when the slot was processed. The view back-fills ``None``
    entries with leftover players (by ascending player index) in a subsequent
    step.

    Algorithm — greedy bipartite, canonical-slot-first:
      1. Iterate ``_SLOT_KEYS`` in order: commander, heavy, scout_1, scout_2,
         medic, ammo.
      2. For each slot, the slot's *role* is the slot key with any trailing
         ``"_1"`` / ``"_2"`` stripped (so ``scout_1`` and ``scout_2`` both
         want ``"scout"``).
      3. Pick the lowest-index unassigned player whose ``preferred_roles``
         contains that role.
      4. If no such player exists, the slot's value is ``None`` and the loop
         continues to the next slot.

    Tie-break: when two unassigned players both prefer the current slot's
    role, the lower player-index wins. ``assign_slots`` is deterministic given
    its input — the RNG enters at the level above (the view shuffles player
    order before assembling ``preferred_roles_per_player``).
    """
    assigned: set[int] = set()
    result: dict[str, int | None] = {}
    n_players = len(preferred_roles_per_player)
    for slot_key in _SLOT_KEYS:
        # Derive the role this slot wants: strip a trailing "_1"/"_2" suffix
        # so scout_1 and scout_2 both map back to "scout".
        if slot_key.endswith("_1") or slot_key.endswith("_2"):
            role = slot_key[:-2]
        else:
            role = slot_key

        picked: int | None = None
        for idx in range(n_players):
            if idx in assigned:
                continue
            if role in preferred_roles_per_player[idx]:
                picked = idx
                break

        if picked is not None:
            assigned.add(picked)
        result[slot_key] = picked

    return result
