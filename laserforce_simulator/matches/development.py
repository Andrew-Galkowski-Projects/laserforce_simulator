"""LG-04 — ZenGM-style player development (pure age-curve math).

Django-free module owning the per-Season stat-development computation: per-stat
age-curve modifiers, change limits, gaussian noise bands, and the whole-player
developer. Faithful to ZenGM's basketball ``calcBaseChange`` / per-rating
``ageModifier`` + ``changeLimits``, with the coaching multiplier fixed at 0 (a
pure age curve). Games-played is NOT a development input.

Frozen import allowlist (the ONLY modules this file may import):
``dataclasses``, ``typing``, ``random``, ``collections``. NO ``django.*``, NO
ORM, NO ``teams`` import, NO ``datetime`` / ``math`` / I/O / logging. The 19
stat field names are hand-rolled locally (they MUST equal
``teams.player_generator._STAT_FIELDS`` byte-for-byte — pinned by a test in the
pure-unit file — but are not imported here so the module stays Django-free).
Defended by ``TestNoDjangoImportsLeaked`` (subprocess fresh-import +
``sys.modules`` walk), mirroring ``matches/draw.py`` / ``matches/bracket.py``.

``random`` is allowlisted because the develop math consumes an INJECTED
``random.Random``. Production builds a fresh ``random.Random()`` per rollover;
tests inject a seeded one. No seed is stored anywhere — the
``PlayerSeasonRating`` row IS the audit trail.

RNG consumption order is PINNED so seeded tests are deterministic: per player
per rollover the noise draw (one ``gauss``) happens FIRST, then the 19
``develop_stat`` calls draw their ``uniform(0.4, 1.4)`` in ``STAT_FIELDS`` order.
"""

import random
from typing import Mapping

# The 19 Player stat field names, canonical order, hand-rolled locally so this
# module stays Django-free. MUST equal teams.player_generator._STAT_FIELDS
# byte-for-byte — incl. the intentional capital-O Offensive_synergy.
STAT_FIELDS: tuple[str, ...] = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)

STAT_MIN: int = 0
STAT_MAX: int = 100

# Each of the 19 stats is assigned a ZenGM archetype group; the group fixes its
# ``age_modifier`` and ``change_limits``. Invented-by-analogy, locked-but-tunable.
_STAT_ARCHETYPE: dict[str, str] = {
    "player_awareness": "awareness",
    "game_awareness": "awareness",
    "resource_awareness": "awareness",
    "decision_making": "awareness",
    "positioning": "awareness",
    "adaptability": "awareness",
    "stamina": "athletic",
    "speed": "athletic",
    "flexibility": "athletic",
    "communication": "team",
    "teamwork": "team",
    "Offensive_synergy": "team",
    "defensive_synergy": "team",
    "midfield_synergy": "team",
    "resupply_synergy": "team",
    "resupply_efficiency": "skill",
    "accuracy": "skill",
    "special_usage": "skill",
    "survival": "durable",
}


def base_change(age: int) -> int:
    """ZenGM calcBaseChange age table (coaching fixed at 0). Pure, total.

    A ``None`` age must never reach this function — the view coalesces a
    ``None`` ``Player.age`` to ``25`` before calling the developer.
    """
    if age <= 21:
        return 2
    if age <= 25:
        return 1
    if age <= 27:
        return 0
    if age <= 29:
        return -1
    if age <= 31:
        return -2
    if age <= 34:
        return -3
    if age <= 40:
        return -4
    if age <= 43:
        return -5
    return -6


def _bound(x: float, lo: float, hi: float) -> float:
    """Clamp ``x`` to ``[lo, hi]``."""
    return max(lo, min(hi, x))


def base_change_noise(age: int, rng: random.Random) -> float:
    """Age-banded gaussian noise added to the base change.

    Young players are volatile, veterans predictable. Consumes exactly one
    ``rng.gauss`` draw.
    """
    if age <= 23:
        return _bound(rng.gauss(0, 5), -4.0, 20.0)
    if age <= 25:
        return _bound(rng.gauss(0, 5), -4.0, 10.0)
    return _bound(rng.gauss(0, 3), -2.0, 4.0)


def age_modifier(group: str, age: int) -> float:
    """Per-archetype-group age modifier added to the effective base change."""
    if group == "awareness":
        # Big positive when young (experience compounds), resists late decline.
        if age <= 21:
            return 4.0
        if age <= 23:
            return 3.0
        if age <= 27:
            return 1.0
        if age <= 31:
            return 0.0
        return 0.5  # mild offset against base-change decline (IQ persists)
    if group == "skill":
        # Flat young, positive past prime (touch/technique held into 30s).
        if age <= 27:
            return 0.0
        if age <= 31:
            return 0.5
        return 1.5
    if group == "athletic":
        # Increasingly negative with age (athleticism fades first).
        if age <= 23:
            return 0.0
        if age <= 27:
            return -0.5
        if age <= 31:
            return -2.0
        return -4.0
    if group == "team":
        # Mild positive young, gentle taper.
        if age <= 25:
            return 1.0
        if age <= 31:
            return 0.0
        return -0.5
    # "durable" — no age modifier; follows the base change + noise only.
    return 0.0


def change_limits(group: str, age: int) -> tuple[float, float]:
    """Per-group ``(lo, hi)`` clamping the per-season delta before it is added."""
    if group == "awareness":
        # Young players can gain a lot; ZenGM-style widening upper bound.
        if age <= 24:
            return (-3.0, 7.0 + 5.0 * (24 - age))  # up to large early gains
        return (-3.0, 7.0)
    if group == "skill":
        return (-3.0, 13.0)
    if group == "athletic":
        return (-12.0, 2.0)  # can crash, barely improves
    if group == "team":
        return (-2.0, 5.0)
    # "durable" — unbounded (follows base change + noise only).
    return (-100.0, 100.0)


def _clamp_int(value: float) -> int:
    """Round to int and floor to ``[STAT_MIN, STAT_MAX]`` (banker's rounding)."""
    return max(STAT_MIN, min(STAT_MAX, round(value)))


def develop_stat(
    current: int,
    stat_name: str,
    age: int,
    effective_base_change: float,
    rng: random.Random,
) -> int:
    """One stat's developed value for one season.

    ``delta = (effective_base_change + age_modifier(group, age)) *
    uniform(0.4, 1.4)``, clamped to ``change_limits(group, age)``, added to
    ``current``, then floored to ``[0, 100]``. Consumes one
    ``rng.uniform(0.4, 1.4)`` draw.
    """
    group = _STAT_ARCHETYPE[stat_name]
    raw = (effective_base_change + age_modifier(group, age)) * rng.uniform(0.4, 1.4)
    lo, hi = change_limits(group, age)
    delta = _bound(raw, lo, hi)
    return _clamp_int(current + delta)


def develop_player_stats(
    stats: Mapping[str, int],
    age: int,
    rng: random.Random,
) -> dict[str, int]:
    """Develop all 19 stats one season for a player of the given (aged) ``age``.

    ``stats`` is a 19-key mapping (every ``STAT_FIELDS`` key). Returns a fresh
    19-key dict of clamped ``[0, 100]`` ints.

    Pure: receives the RNG; consumes exactly 1 gauss + 19 uniform draws, in
    (noise, then ``STAT_FIELDS`` order) sequence. The view passes the
    already-incremented age (the develop step runs on age ``+1``).
    """
    effective = base_change(age) + base_change_noise(age, rng)
    return {
        name: develop_stat(stats[name], name, age, effective, rng)
        for name in STAT_FIELDS
    }


# ====================================================================
# LG-05 — Player potential (projected peak overall_rating)
# ====================================================================

DEFAULT_SCOUTING_BUDGET: int = 50
POTENTIAL_MAX_SD: float = 8.0
_POTENTIAL_HORIZON_AGE: int = 40
_POTENTIAL_MIDPOINT_MULT: float = 0.9


def _project_stat_noise_free(current: int, stat_name: str, age: int) -> int:
    """One stat's NOISE-FREE projected value for one season.

    Mirrors ``develop_stat`` but deterministic: the ``rng.uniform(0.4, 1.4)``
    midpoint multiplier is replaced by the fixed ``_POTENTIAL_MIDPOINT_MULT``
    (0.9) and ``base_change_noise`` is dropped entirely. Consumes NO RNG.
    """
    group = _STAT_ARCHETYPE[stat_name]
    raw = (base_change(age) + age_modifier(group, age)) * _POTENTIAL_MIDPOINT_MULT
    lo, hi = change_limits(group, age)
    delta = _bound(raw, lo, hi)
    return _clamp_int(current + delta)


def _project_peak_overall(stats: Mapping[str, int], age: int) -> float:
    """Project the peak ``overall_rating`` reachable from ``stats`` at ``age``.

    Rolls the noise-free age curve forward from ``age + 1`` to
    ``_POTENTIAL_HORIZON_AGE`` (40) inclusive, developing every stat each
    year via ``_project_stat_noise_free`` and tracking the running-max
    overall (mean of the 19 stats). Seeds the running-max with the CURRENT
    overall, so the result is always ``>= mean(stats)``. Consumes NO RNG;
    when ``age >= 40`` the loop never runs and the current overall is
    returned exactly.
    """
    current = {name: stats[name] for name in STAT_FIELDS}
    best = sum(current[name] for name in STAT_FIELDS) / len(STAT_FIELDS)
    a = age
    while a < _POTENTIAL_HORIZON_AGE:
        a += 1
        current = {
            name: _project_stat_noise_free(current[name], name, a)
            for name in STAT_FIELDS
        }
        best = max(best, sum(current[name] for name in STAT_FIELDS) / len(STAT_FIELDS))
    return best


def compute_potential(
    stats: Mapping[str, int],
    age: int,
    rng: random.Random,
    *,
    scouting_budget: int = DEFAULT_SCOUTING_BUDGET,
) -> float:
    """Compute a Player's ``potential`` — a projected peak ``overall_rating``.

    The noise-free ``_project_peak_overall`` ceiling plus a scouting-noise
    band (``sd = POTENTIAL_MAX_SD * (1 - scouting_budget / 100)``: budget 0
    => max sd, 100 => 0), clamped to ``[current_overall, 100]``. Consumes
    EXACTLY ONE ``rng.gauss`` draw (always drawn, even when ``sd == 0``).
    """
    ceiling = _project_peak_overall(stats, age)
    sd = POTENTIAL_MAX_SD * (1 - scouting_budget / 100)
    floor = sum(stats[name] for name in STAT_FIELDS) / len(STAT_FIELDS)
    value = ceiling + rng.gauss(0, sd)
    return _bound(value, floor, 100.0)


def free_agent_games_tick(median_active: int, rng: random.Random) -> int:
    """Cosmetic ``total_games`` bump for a free-agent-pool player at rollover.

    Returns ``rng.randint(0, median_active // 2)``. The degenerate no-active
    case (``median_active == 0``) returns 0 deterministically
    (``randint(0, 0) == 0``).
    """
    return rng.randint(0, max(0, median_active) // 2)
