"""FIN-04 — injury / availability roll math (pure, Django-free).

Django-free module owning the per-fixture **Injury** roll: a flat base
per-fixture probability that a healthy starter who fields is injured (scaled by
an age factor), plus a health-edge-scaled drawn duration in matchdays, and the
flat play-hurt stat penalty. Rolls happen OUTSIDE the simulator's tick loop and
the SIM-07/08 seed chain — production builds a FRESH ``random.Random()`` per
fixture (never the SIM seed), and the draws consume an INJECTED ``random.Random``.

Frozen import allowlist (the ONLY modules this file may import): ``dataclasses``,
``typing``, ``random``, ``collections``. NO ``django.*`` / ORM / ``datetime`` /
``math`` / I/O / logging / ``teams`` / ``matches`` imports — defended by
``matches/tests/test_injury.py::TestNoDjangoImportsLeaked`` (subprocess
fresh-import + ``sys.modules`` walk), the ``finance.py`` / ``development.py``
precedent. ``random`` is allowlisted because the draws consume an injected
``random.Random``.

The level→float ``health_effect`` map lives in ``finance.py`` (NOT here) — this
module CONSUMES the float. A positive edge shortens the drawn duration.

RNG-consumption order across one starter roll is PINNED so seeded tests are
deterministic: ``roll_injury`` (one ``rng.random()`` draw) THEN, only if injured,
``draw_duration`` (one ``rng.random()`` draw).
"""

import random

# --- Constants (LOCKED-but-tunable; calibration-deferred — invented-by-analogy,
# the LG-04 age-curve / FIN-01 magic-number precedent) -----------------------

# Flat per-fixture base probability a healthy starter who fields is injured
# (before the age factor).
BASE_INJURY_RATE: float = 0.04

# Age at which the age factor is 1.0.
AGE_FACTOR_PIVOT: int = 27
# Linear age-factor slope per year past the pivot (older ⇒ more injury-prone).
AGE_FACTOR_PER_YEAR: float = 0.04
# Age-factor clamp.
AGE_FACTOR_MIN: float = 0.5
AGE_FACTOR_MAX: float = 2.5

# Mean drawn duration (matchdays) before the health-edge scale applies.
DURATION_BASE_GAMES: float = 3.0
# Drawn-duration clamp.
DURATION_MIN_GAMES: int = 1
DURATION_MAX_GAMES: int = 12

# Flat magnitude (points) subtracted from each of the injured Player's 19 stat
# fields when fielded hurt (clamped to [0, 100] by the caller).
PLAY_HURT_STAT_PENALTY: int = 12


def _bound(value: float, lo: float, hi: float) -> float:
    """Clamp ``value`` to ``[lo, hi]``."""
    return max(lo, min(hi, value))


def age_factor(age: int) -> float:
    """Per-age injury-proneness factor.

    ``1.0`` at ``AGE_FACTOR_PIVOT``, linear ``AGE_FACTOR_PER_YEAR`` each side
    (older ⇒ higher), clamped ``[AGE_FACTOR_MIN, AGE_FACTOR_MAX]``. A ``None``
    age never reaches here — the caller coalesces ``None → 25`` (the LG-04
    convention). Consumes NO RNG.
    """
    raw = 1.0 + AGE_FACTOR_PER_YEAR * (age - AGE_FACTOR_PIVOT)
    return _bound(raw, AGE_FACTOR_MIN, AGE_FACTOR_MAX)


def injury_probability(age: int) -> float:
    """``BASE_INJURY_RATE * age_factor(age)``.

    No Stat input (flat base × age factor only). Consumes NO RNG.
    """
    return BASE_INJURY_RATE * age_factor(age)


def roll_injury(age: int, rng: random.Random) -> bool:
    """Roll a NEW injury for a healthy starter who fields.

    ``rng.random() < injury_probability(age)``. Exactly ONE ``rng.random()``
    draw.
    """
    return rng.random() < injury_probability(age)


def draw_duration(health_effect: float, age: int, rng: random.Random) -> int:
    """Draw an injury duration in matchdays, health-edge-scaled.

    Draws around ``DURATION_BASE_GAMES`` and scales the draw DOWN by
    ``health_effect`` (frequency is fixed — only the duration is health-scaled),
    clamps to ``[DURATION_MIN_GAMES, DURATION_MAX_GAMES]``, returns an int ≥ 1.
    Exactly ONE RNG draw. ``health_effect`` is the sign-flipped ZenGM analogue
    float from ``finance.health_effect``; a positive edge shortens the draw.
    """
    # One draw in [0.5, 1.5) scaling the base mean.
    raw = DURATION_BASE_GAMES * (0.5 + rng.random())
    # A positive health edge shortens the duration; a negative edge lengthens it.
    raw *= 1.0 - health_effect
    games = int(round(raw))
    return max(DURATION_MIN_GAMES, min(DURATION_MAX_GAMES, games))


def play_hurt_penalty() -> int:
    """Return the per-stat play-hurt magnitude (``PLAY_HURT_STAT_PENALTY``).

    Consumes NO RNG.
    """
    return PLAY_HURT_STAT_PENALTY
