"""CONF-03 Worlds qualification — pure deterministic derivation helpers.

Public surface:

* ``WorldsQualifier`` — frozen dataclass for one Team's place in the Worlds
  field, carrying the ``tier`` that seeds it, the ``provenance`` that earned
  it, the four Conference-scoped regular-season integers the rate comparison
  reads, and the 1-based ``seed`` stamped by ``order_worlds_qualifiers``.
* ``qualifier_count_for_size(team_count)`` — how many Worlds qualifiers a
  Conference of ``team_count`` Teams sends (ADR-0036's size tiers: 2-4 ⇒ 1,
  5-8 ⇒ 2, 9+ ⇒ 3).
* ``first_unqualified(ranked_team_ids, qualified_team_ids)`` — the tier-2
  regular-season slot rule, and the 2-3-Team fallback's rank-1 rule.
* ``last_chance_field(ranked_team_ids, qualified_team_ids)`` — the Last-chance
  qualifier bracket's field, up to ``LAST_CHANCE_FIELD_SIZE`` ids.
* ``order_worlds_qualifiers(qualifiers)`` — order the unioned field
  tier-first, then by regular-season RATE within the tier, and stamp ``seed``.

Tier and provenance are SEPARATE axes on purpose (ADR-0036): a Conference too
small to field a Regional playoff sends its Standings rank-1 Team with
``tier = QUALIFIER_TIER_CHAMPION`` but
``provenance = PROVENANCE_REGULAR_SEASON``. Never derive one from the other.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``. No Django, no ORM, no ``random``, no
``datetime``, no I/O, no logging — the ``matches/standings.py`` /
``matches/bracket.py`` precedent. Enforced by the ``TestNoDjangoImportsLeaked``
subprocess check.
"""

from dataclasses import dataclass, replace

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Seeding tiers. Tier ordering beats every rate comparison (ADR-0036): every
# Conference champion seeds ahead of every regular-season qualifier, which in
# turn seeds ahead of every Last-chance winner.
QUALIFIER_TIER_CHAMPION = 1
QUALIFIER_TIER_REGULAR_SEASON = 2
QUALIFIER_TIER_LAST_CHANCE = 3

# How a slot was EARNED — display provenance, not a seeding input.
PROVENANCE_CHAMPION = "conference_champion"
PROVENANCE_REGULAR_SEASON = "regular_season"
PROVENANCE_LAST_CHANCE = "last_chance"

PROVENANCE_LABELS = {
    PROVENANCE_CHAMPION: "Conference champion",
    PROVENANCE_REGULAR_SEASON: "Regular season",
    PROVENANCE_LAST_CHANCE: "Last-chance qualifier",
}

# The Last-chance qualifier bracket's fixed field size (ADR-0036).
LAST_CHANCE_FIELD_SIZE = 4


@dataclass(frozen=True)
class WorldsQualifier:
    """One Team's place in the Worlds field, with the provenance that earned it."""

    team_id: int
    team_name: str
    conference_id: int
    conference_name: str
    tier: int  # QUALIFIER_TIER_* — the seeding tier
    provenance: str  # PROVENANCE_* — how the slot was earned
    matches_played: int  # regular-season, Conference-scoped
    league_points: int  # regular-season, Conference-scoped
    round_wins: int  # regular-season, Conference-scoped
    total_score: int  # regular-season, Conference-scoped
    seed: int = 0  # 1-based Worlds seed; 0 until order_worlds_qualifiers stamps it

    @property
    def provenance_label(self) -> str:
        """Human-readable provenance for display. Unknown value ⇒ ``""``."""
        return PROVENANCE_LABELS.get(self.provenance, "")


def qualifier_count_for_size(team_count: int) -> int:
    """How many Worlds qualifiers a Conference of ``team_count`` Teams sends.

    ADR-0036's size tiers, read off the Conference's ACTIVATION SNAPSHOT: fewer
    than 2 Teams ⇒ 0 (degenerate), 2-4 ⇒ 1, 5-8 ⇒ 2, 9 or more ⇒ 3. A negative
    input returns 0.
    """
    if team_count < 2:
        return 0
    if team_count <= 4:
        return 1
    if team_count <= 8:
        return 2
    return 3


def first_unqualified(ranked_team_ids: list, qualified_team_ids) -> "int | None":
    """The first id of ``ranked_team_ids`` not present in ``qualified_team_ids``.

    ``ranked_team_ids`` is a Conference's Standings order, best first.
    ``qualified_team_ids`` is any container supporting ``in``. ``None`` when
    every ranked id is already qualified (or the list is empty).
    """
    for team_id in ranked_team_ids:
        if team_id not in qualified_team_ids:
            return team_id
    return None


def last_chance_field(ranked_team_ids: list, qualified_team_ids) -> list:
    """The Last-chance qualifier's field: UP TO ``LAST_CHANCE_FIELD_SIZE`` ids.

    The highest-ranked ids of ``ranked_team_ids`` not in
    ``qualified_team_ids``, in rank order (index 0 becomes bracket seed 1).
    Returns FEWER than ``LAST_CHANCE_FIELD_SIZE`` when there are not enough
    unqualified Teams; the caller decides what to do about that. Never raises,
    never pads.
    """
    field: list = []
    for team_id in ranked_team_ids:
        if team_id in qualified_team_ids:
            continue
        field.append(team_id)
        if len(field) == LAST_CHANCE_FIELD_SIZE:
            break
    return field


def _rate(numerator: float, matches_played: int) -> float:
    """``numerator / matches_played``, or ``0.0`` when ``matches_played <= 0``."""
    if matches_played <= 0:
        return 0.0
    return numerator / matches_played


def order_worlds_qualifiers(qualifiers: list) -> list:
    """Order the unioned Worlds field into seeds 1..M and stamp ``seed``.

    Sort key (ADR-0036): tier ASC, then regular-season RATE within the tier —
    league_points/matches_played DESC, round_wins/matches_played DESC,
    total_score/matches_played DESC — then team_id ASC. ``matches_played <= 0``
    ⇒ every rate is 0.0 (no division attempted).

    Rate, not raw totals, because Conferences differ in size and therefore play
    different numbers of games.

    Returns a NEW list of ``WorldsQualifier`` with ``seed`` set 1..M
    (``dataclasses.replace``); the input list is not mutated. Empty input ⇒ [].
    """
    ordered = sorted(
        qualifiers,
        key=lambda q: (
            q.tier,
            -_rate(q.league_points, q.matches_played),
            -_rate(q.round_wins, q.matches_played),
            -_rate(q.total_score, q.matches_played),
            q.team_id,
        ),
    )
    return [replace(q, seed=position + 1) for position, q in enumerate(ordered)]
