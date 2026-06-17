"""FIN-04 — pure-unit tests for ``matches/injury.py`` (seam contract §2 / §8).

No DB, no Django imports in the assertion path. Mirrors the LG-04
``test_development.py`` / FIN-01 ``test_finance.py`` precedent — a pure
``SimpleTestCase`` with hand-crafted inputs + a seeded ``random.Random``, plus
the ``TestNoDjangoImportsLeaked`` subprocess fresh-import check.

The pure module owns the age-scaled injury roll + duration draw + the
play-hurt penalty magnitude. Frozen import allowlist: ``dataclasses`` /
``typing`` / ``random`` / ``collections`` — NO Django, NO ORM, NO ``datetime``,
NO ``math``, NO I/O, NO ``teams``/``matches`` imports. ``random`` is allowed
only because the draws consume an INJECTED ``random.Random``. The level→float
``health_effect`` map lives in ``finance.py`` (§3) — ``injury.py`` CONSUMES the
float; ``injury_probability`` / ``roll_injury`` take NO Stat input (flat base ×
age factor only).

RNG-consumption order across one starter roll is PINNED: ``roll_injury`` (1
``rng.random()`` draw) THEN, only if injured, ``draw_duration`` (1 draw) — so
seeded tests are deterministic. Each of ``roll_injury`` / ``draw_duration``
consumes EXACTLY ONE RNG draw; ``age_factor`` / ``injury_probability`` /
``play_hurt_penalty`` consume none.

These assertions WILL ImportError / fail until the Code agent lands
``matches/injury.py`` (the module may not yet exist); that is the expected TDD
red state for the parallel build, NOT a defect in this file.
"""

from __future__ import annotations

import random

from django.test import SimpleTestCase

from matches import injury
from matches.injury import (
    AGE_FACTOR_MAX,
    AGE_FACTOR_MIN,
    AGE_FACTOR_PER_YEAR,
    AGE_FACTOR_PIVOT,
    BASE_INJURY_RATE,
    DURATION_BASE_GAMES,
    DURATION_MAX_GAMES,
    DURATION_MIN_GAMES,
    PLAY_HURT_STAT_PENALTY,
    age_factor,
    draw_duration,
    injury_probability,
    play_hurt_penalty,
    roll_injury,
)


# ===========================================================================
# §2 — TestConstants (the locked-but-tunable module constants)
# ===========================================================================


class TestConstants(SimpleTestCase):
    """The locked constant values; concrete coefficients are tunable, but the
    structural identities are pinned (seam contract §2)."""

    def test_base_injury_rate(self) -> None:
        self.assertEqual(BASE_INJURY_RATE, 0.04)

    def test_age_factor_pivot(self) -> None:
        self.assertEqual(AGE_FACTOR_PIVOT, 27)

    def test_age_factor_per_year(self) -> None:
        self.assertEqual(AGE_FACTOR_PER_YEAR, 0.04)

    def test_age_factor_clamp_bounds(self) -> None:
        self.assertEqual(AGE_FACTOR_MIN, 0.5)
        self.assertEqual(AGE_FACTOR_MAX, 2.5)

    def test_duration_base_games(self) -> None:
        self.assertEqual(DURATION_BASE_GAMES, 3.0)

    def test_duration_clamp_bounds(self) -> None:
        self.assertEqual(DURATION_MIN_GAMES, 1)
        self.assertEqual(DURATION_MAX_GAMES, 12)

    def test_play_hurt_stat_penalty(self) -> None:
        self.assertEqual(PLAY_HURT_STAT_PENALTY, 12)


# ===========================================================================
# §2 — TestAgeFactor (1.0 at pivot; linear each side; clamp; no RNG)
# ===========================================================================


class TestAgeFactor(SimpleTestCase):
    """``age_factor(age)`` is ``1.0`` at ``AGE_FACTOR_PIVOT`` (27), slopes
    linearly ``AGE_FACTOR_PER_YEAR`` each side, and clamps to
    ``[AGE_FACTOR_MIN, AGE_FACTOR_MAX]``. Consumes NO RNG."""

    def test_pivot_is_one(self) -> None:
        self.assertAlmostEqual(age_factor(AGE_FACTOR_PIVOT), 1.0, places=9)

    def test_returns_float(self) -> None:
        self.assertIsInstance(age_factor(27), float)

    def test_linear_one_year_past_pivot(self) -> None:
        # One year older than the pivot adds exactly AGE_FACTOR_PER_YEAR.
        self.assertAlmostEqual(
            age_factor(AGE_FACTOR_PIVOT + 1),
            1.0 + AGE_FACTOR_PER_YEAR,
            places=9,
        )

    def test_linear_one_year_before_pivot(self) -> None:
        # One year younger than the pivot subtracts AGE_FACTOR_PER_YEAR.
        self.assertAlmostEqual(
            age_factor(AGE_FACTOR_PIVOT - 1),
            1.0 - AGE_FACTOR_PER_YEAR,
            places=9,
        )

    def test_older_is_more_injury_prone(self) -> None:
        # Monotone non-decreasing in age across the unclamped band.
        prev = age_factor(20)
        for age in range(21, 50):
            cur = age_factor(age)
            self.assertGreaterEqual(cur, prev - 1e-12, f"age={age}")
            prev = cur

    def test_clamped_above_at_max(self) -> None:
        # A very old player saturates at AGE_FACTOR_MAX (never exceeds it).
        for age in (60, 80, 120):
            self.assertLessEqual(age_factor(age), AGE_FACTOR_MAX + 1e-12, f"age={age}")
        # And actually reaches the ceiling for an extreme age.
        self.assertAlmostEqual(age_factor(120), AGE_FACTOR_MAX, places=9)

    def test_clamped_below_at_min(self) -> None:
        # A very young player never drops below AGE_FACTOR_MIN.
        for age in (1, 5, 10):
            self.assertGreaterEqual(age_factor(age), AGE_FACTOR_MIN - 1e-12, f"age={age}")

    def test_consumes_no_rng(self) -> None:
        random.seed(99)
        before = random.getstate()
        age_factor(30)
        self.assertEqual(random.getstate(), before)


# ===========================================================================
# §2 — TestInjuryProbability (base × age; no Stat input; no RNG)
# ===========================================================================


class TestInjuryProbability(SimpleTestCase):
    """``injury_probability(age) == BASE_INJURY_RATE * age_factor(age)`` — a
    flat base × age factor, NO Stat input. Consumes NO RNG."""

    def test_at_pivot_equals_base_rate(self) -> None:
        # age_factor(pivot) == 1.0 ⇒ probability == BASE_INJURY_RATE exactly.
        self.assertAlmostEqual(
            injury_probability(AGE_FACTOR_PIVOT), BASE_INJURY_RATE, places=9
        )

    def test_equals_base_times_age_factor(self) -> None:
        for age in (20, 27, 35, 45):
            self.assertAlmostEqual(
                injury_probability(age),
                BASE_INJURY_RATE * age_factor(age),
                places=12,
                msg=f"age={age}",
            )

    def test_returns_float(self) -> None:
        self.assertIsInstance(injury_probability(30), float)

    def test_older_player_higher_probability(self) -> None:
        self.assertGreater(injury_probability(45), injury_probability(20))

    def test_consumes_no_rng(self) -> None:
        random.seed(7)
        before = random.getstate()
        injury_probability(33)
        self.assertEqual(random.getstate(), before)


# ===========================================================================
# §2 — TestRollInjury (deterministic; exactly one rng.random() draw)
# ===========================================================================


class _RandomCounter(random.Random):
    """A ``random.Random`` subclass counting ``random()`` calls, so we can pin
    that ``roll_injury`` / ``draw_duration`` each consume EXACTLY ONE draw."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.random_calls = 0

    def random(self):  # type: ignore[override]
        self.random_calls += 1
        return super().random()


class TestRollInjury(SimpleTestCase):
    """``roll_injury(age, rng) == (rng.random() < injury_probability(age))`` —
    exactly ONE ``rng.random()`` draw, deterministic under a seeded RNG."""

    def test_returns_bool(self) -> None:
        self.assertIsInstance(roll_injury(30, random.Random(0)), bool)

    def test_matches_threshold_under_seed(self) -> None:
        # The injury fires iff the single draw is below injury_probability(age).
        # Reproduce the draw with a parallel same-seed RNG to pin the threshold.
        age = 35
        probe = random.Random(123).random()
        expected = probe < injury_probability(age)
        self.assertEqual(roll_injury(age, random.Random(123)), expected)

    def test_consumes_exactly_one_random_draw(self) -> None:
        rng = _RandomCounter(42)
        roll_injury(30, rng)
        self.assertEqual(rng.random_calls, 1)

    def test_deterministic_for_fixed_seed(self) -> None:
        a = roll_injury(33, random.Random(99))
        b = roll_injury(33, random.Random(99))
        self.assertEqual(a, b)

    def test_higher_age_injures_at_least_as_often_over_many_seeds(self) -> None:
        # DIRECTION, not magnitude: the older cohort's injury count is >= the
        # younger cohort's over the same fixed seed sequence.
        young = sum(roll_injury(20, random.Random(s)) for s in range(400))
        old = sum(roll_injury(45, random.Random(s)) for s in range(400))
        self.assertGreaterEqual(old, young)


# ===========================================================================
# §2 — TestDrawDuration (health-edge-scaled DOWN; clamp; one draw)
# ===========================================================================


class TestDrawDuration(SimpleTestCase):
    """``draw_duration(health_effect, age, rng)`` draws a duration in matchdays,
    scales it DOWN by ``health_effect`` (a positive edge shortens the draw),
    clamps to ``[DURATION_MIN_GAMES, DURATION_MAX_GAMES]``, returns an int ≥ 1.
    Exactly ONE RNG draw."""

    def test_returns_int(self) -> None:
        self.assertIsInstance(
            draw_duration(0.0, 30, random.Random(0)), int
        )

    def test_within_clamp_bounds_over_many_seeds(self) -> None:
        for he in (-0.5, 0.0, 0.5):
            for age in (20, 30, 45):
                for s in range(200):
                    n = draw_duration(he, age, random.Random(s))
                    self.assertGreaterEqual(n, DURATION_MIN_GAMES, f"he={he} age={age}")
                    self.assertLessEqual(n, DURATION_MAX_GAMES, f"he={he} age={age}")

    def test_at_least_one(self) -> None:
        # An int >= 1 always, even with a strong positive health edge.
        for s in range(200):
            self.assertGreaterEqual(draw_duration(0.5, 30, random.Random(s)), 1)

    def test_consumes_exactly_one_rng_draw(self) -> None:
        rng = _RandomCounter(42)
        draw_duration(0.0, 30, rng)
        self.assertEqual(rng.random_calls, 1)

    def test_deterministic_for_fixed_seed(self) -> None:
        a = draw_duration(0.2, 33, random.Random(99))
        b = draw_duration(0.2, 33, random.Random(99))
        self.assertEqual(a, b)

    def test_positive_health_effect_shortens_aggregate_duration(self) -> None:
        # DIRECTION: a positive health edge scales the draw DOWN, so the summed
        # duration over a fixed seed sequence is <= the no-edge baseline.
        baseline = sum(draw_duration(0.0, 30, random.Random(s)) for s in range(300))
        with_edge = sum(draw_duration(0.5, 30, random.Random(s)) for s in range(300))
        self.assertLessEqual(with_edge, baseline)

    def test_negative_health_effect_does_not_shorten(self) -> None:
        # A neglected health budget (negative effect) lengthens, not shortens:
        # the summed duration is >= the no-edge baseline.
        baseline = sum(draw_duration(0.0, 30, random.Random(s)) for s in range(300))
        neglected = sum(draw_duration(-0.5, 30, random.Random(s)) for s in range(300))
        self.assertGreaterEqual(neglected, baseline)


# ===========================================================================
# §2 — TestPlayHurtPenalty (returns the magnitude; no RNG)
# ===========================================================================


class TestPlayHurtPenalty(SimpleTestCase):
    """``play_hurt_penalty()`` returns ``PLAY_HURT_STAT_PENALTY`` — the flat
    per-stat magnitude. Consumes NO RNG."""

    def test_returns_the_penalty_constant(self) -> None:
        self.assertEqual(play_hurt_penalty(), PLAY_HURT_STAT_PENALTY)

    def test_returns_int(self) -> None:
        self.assertIsInstance(play_hurt_penalty(), int)

    def test_consumes_no_rng(self) -> None:
        random.seed(5)
        before = random.getstate()
        play_hurt_penalty()
        self.assertEqual(random.getstate(), before)


# ===========================================================================
# §2 — TestRollThenDrawOrder (the PINNED per-starter RNG-consumption order)
# ===========================================================================


class TestRollThenDrawOrder(SimpleTestCase):
    """The pinned per-starter RNG order: ``roll_injury`` (1 draw) THEN, only if
    injured, ``draw_duration`` (1 draw). Two RNGs seeded identically and driven
    through ``roll`` then (on a hit) ``draw`` must consume draws in lockstep, so
    the whole starter roll is reproducible."""

    def test_roll_then_draw_is_two_draws_on_a_hit(self) -> None:
        # Find a seed whose age-35 roll injures, then assert that running
        # roll_injury followed by draw_duration consumes exactly 2 draws total.
        age = 35
        chosen = None
        for s in range(500):
            if roll_injury(age, random.Random(s)):
                chosen = s
                break
        self.assertIsNotNone(chosen, "no injuring seed found in range")

        rng = _RandomCounter(chosen)
        injured = roll_injury(age, rng)
        self.assertTrue(injured)
        if injured:
            draw_duration(0.0, age, rng)
        self.assertEqual(rng.random_calls, 2)

    def test_no_hit_consumes_only_the_roll_draw(self) -> None:
        # A seed whose roll does NOT injure consumes exactly 1 draw (the roll);
        # draw_duration is not called.
        age = 20
        chosen = None
        for s in range(500):
            if not roll_injury(age, random.Random(s)):
                chosen = s
                break
        self.assertIsNotNone(chosen, "no non-injuring seed found in range")

        rng = _RandomCounter(chosen)
        injured = roll_injury(age, rng)
        self.assertFalse(injured)
        # The caller would NOT call draw_duration on a miss.
        self.assertEqual(rng.random_calls, 1)

    def test_seeded_roll_then_draw_is_reproducible(self) -> None:
        # The same seed driven through the pinned order yields the same
        # (injured, duration-or-None) pair.
        def one_starter(seed: int):
            rng = random.Random(seed)
            injured = roll_injury(40, rng)
            duration = draw_duration(0.0, 40, rng) if injured else None
            return injured, duration

        self.assertEqual(one_starter(2026), one_starter(2026))


# ===========================================================================
# §8 — TestNoDjangoImportsLeaked
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.injury`` (and exercising its pure functions) in a
    fresh subprocess must not pull in ``django.*`` (nor ``matches.models`` /
    ``finance``) — the frozen allowlist is ``dataclasses`` / ``typing`` /
    ``random`` / ``collections``. Mirrors the ``test_finance.py`` /
    ``test_development.py`` ``TestNoDjangoImportsLeaked`` precedent.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        here = pathlib.Path(__file__).resolve()
        project_root = None
        for parent in here.parents:
            if (parent / "manage.py").exists():
                project_root = parent
                break
        self.assertIsNotNone(project_root, "could not locate manage.py from test file")

        script = textwrap.dedent(f"""
            import random
            import sys
            sys.path.insert(0, {str(project_root)!r})
            from matches.injury import (
                age_factor,
                injury_probability,
                roll_injury,
                draw_duration,
                play_hurt_penalty,
            )

            rng = random.Random(0)
            age_factor(30)
            injury_probability(30)
            roll_injury(30, rng)
            draw_duration(0.0, 30, rng)
            play_hurt_penalty()

            offenders = sorted(
                name
                for name in sys.modules
                if name == "django"
                or name.startswith("django.")
                or name == "matches.models"
            )
            if offenders:
                print("LEAK:" + ",".join(offenders))
                sys.exit(1)
            sys.exit(0)
            """)
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=30,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
