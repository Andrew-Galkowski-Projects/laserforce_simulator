"""LG-02x-1 — pure Random-Draw player-pool math.

Owns the tier-balanced draw computation + the two role-assignment-mode
bijection builders as plain data. No Django / ORM / I/O / logging.

Frozen import allowlist (the ONLY modules this file may import):
``dataclasses``, ``typing``, ``random``, ``collections``. NO ``django.*``, NO
``datetime``, NO file I/O. Enforced by a ``TestNoDjangoImportsLeaked``
subprocess fresh-import + ``sys.modules`` walk (mirrors ``matches/bracket.py`` /
``matches/standings.py`` / ``matches/schedule_generator.py``).

``random`` is allowed because the role-assignment builders consume an injected
``random.Random`` (the per-Round role draw). The DRAW COMPUTATION ITSELF
consumes NO RNG — it is a deterministic straight-tiers + greedy balance.
"""

import random
from dataclasses import dataclass

# The 6 ``Team.slot_*`` suffixes, fixed order. A role assignment is a mapping
# from these slot suffixes to player ids.
ROLE_SLOTS: tuple[str, ...] = (
    "commander",
    "heavy",
    "scout_1",
    "scout_2",
    "medic",
    "ammo",
)


@dataclass(frozen=True)
class DrawnTeamPlan:
    """One assembled team from the draw, as plain data (pre-ORM)."""

    team_index: int  # 0-based, draw order (drawn team N)
    player_ids: tuple[int, ...]  # the 6 player ids, tier 1..6 order
    tiers: tuple[int, ...]  # parallel to player_ids: the tier (1..6) of each


def compute_draw(pool: list[tuple[int, float]]) -> list[DrawnTeamPlan]:
    """STRAIGHT TIERS + GREEDY BALANCE. Deterministic — consumes NO RNG.

    ``pool`` is a list of ``(player_id, overall_rating)``. Precondition
    (caller-validated): ``len(pool) % 6 == 0`` and ``len(pool) >= 24``
    (>= 4 teams). Raises ``ValueError`` otherwise.

    Algorithm:
      1. Sort pool by overall_rating DESC, then player_id ASC (tiebreak).
      2. T = len(pool) // 6 teams. Form 6 contiguous tiers of T players each
         (tier 1 = the strongest band = first T, ..., tier 6 = weakest band).
      3. For each tier 1..6, in order: assign the strongest-remaining tier
         player to the currently-weakest team (lowest running total rating;
         team_index ASC tiebreak when totals are equal). One player per team
         per tier.
      4. Return one DrawnTeamPlan per team (team_index 0..T-1), player_ids /
         tiers ordered tier 1..6.

    Idempotent: same pool -> identical output.
    """
    n = len(pool)
    if n % 6 != 0:
        raise ValueError("pool size must be divisible by 6")
    if n < 24:
        raise ValueError("pool must have at least 24 players (>= 4 teams)")

    teams = n // 6
    # 1. Sort by rating DESC, then player_id ASC.
    ordered = sorted(pool, key=lambda pr: (-pr[1], pr[0]))

    # Per-team accumulators, indexed by team_index 0..teams-1.
    team_ids: list[list[int]] = [[] for _ in range(teams)]
    team_tiers: list[list[int]] = [[] for _ in range(teams)]
    team_totals: list[float] = [0.0 for _ in range(teams)]

    # 2 + 3. Process each tier in order; within a tier assign strongest-remaining
    # to the currently-weakest team.
    for tier in range(1, 7):
        band = ordered[(tier - 1) * teams : tier * teams]
        # band is already rating-DESC / player_id-ASC ordered (strongest first).
        # Assign each band member, strongest-remaining -> currently-weakest team.
        assigned_team_indices: set[int] = set()
        for player_id, rating in band:
            # Pick the lowest-running-total team not yet assigned this tier.
            best_idx = None
            best_total = None
            for idx in range(teams):
                if idx in assigned_team_indices:
                    continue
                total = team_totals[idx]
                if best_total is None or total < best_total:
                    best_total = total
                    best_idx = idx
            assigned_team_indices.add(best_idx)
            team_ids[best_idx].append(player_id)
            team_tiers[best_idx].append(tier)
            team_totals[best_idx] += rating

    return [
        DrawnTeamPlan(
            team_index=idx,
            player_ids=tuple(team_ids[idx]),
            tiers=tuple(team_tiers[idx]),
        )
        for idx in range(teams)
    ]


def build_random_role_assignment(
    tier_player_ids: list[int],
    rng: random.Random,
) -> dict[str, int]:
    """``random`` mode, per TEAM independently.

    ``tier_player_ids`` is the team's 6 player ids in tier order (index 0 =
    tier 1 .. index 5 = tier 6). Shuffle the 6 ids into the 6 ROLE_SLOTS.
    Returns ``{slot_suffix: player_id}`` over all 6 ROLE_SLOTS. Consumes the
    injected rng (one shuffle).
    """
    ids = list(tier_player_ids)
    rng.shuffle(ids)
    return {slot: ids[i] for i, slot in enumerate(ROLE_SLOTS)}


def build_per_tier_role_assignment(rng: random.Random) -> dict[int, str]:
    """``per_tier`` mode. Draw ONE tier->slot bijection for the Round, applied
    to BOTH teams (so equal-tier players play the same role).

    Returns ``{tier (1..6): slot_suffix}`` — a permutation of ROLE_SLOTS keyed
    by tier. Consumes the injected rng (one shuffle). The caller applies it to
    each team's tier->player map to produce that team's ``{slot_suffix:
    player_id}``.
    """
    slots = list(ROLE_SLOTS)
    rng.shuffle(slots)
    return {tier: slots[tier - 1] for tier in range(1, 7)}
