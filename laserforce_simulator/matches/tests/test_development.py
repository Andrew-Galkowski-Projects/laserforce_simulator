"""LG-04 — Pure-unit tests for ``matches/development.py``.

No DB, no Django imports in the assertion path. Seam contract locked at
``.claude/worktrees/lg-04-player-development-seam-contract.md`` (§3 / §7.1).
Mirrors the LG-02x-1 ``test_draw.py`` / LG-02a ``test_bracket.py`` precedent —
pure ``SimpleTestCase`` with hand-crafted inputs + a seeded ``random.Random``,
plus the ``TestNoDjangoImportsLeaked`` subprocess fresh-import check.

The pure module owns the ZenGM-style age-curve develop math: ``base_change`` +
``base_change_noise`` (per-player), ``age_modifier`` + ``change_limits`` (the
5 archetype groups), ``develop_stat`` + ``develop_player_stats`` (the per-stat
and whole-player developers), and the cosmetic ``free_agent_games_tick``. The
frozen import allowlist is ``dataclasses`` / ``typing`` / ``random`` /
``collections`` — NO Django, NO ORM, NO ``datetime``, NO ``math``, NO file I/O.
``random`` is allowed only because the develop math consumes an INJECTED
``random.Random``.

These assertions WILL fail / ImportError until the Code agent lands
``matches/development.py`` (the module may not yet exist); that is expected for
the parallel build.
"""

from __future__ import annotations

import random

from django.test import SimpleTestCase

from matches import development
from matches.development import (
    STAT_FIELDS,
    STAT_MAX,
    STAT_MIN,
    _STAT_ARCHETYPE,
    age_modifier,
    base_change,
    base_change_noise,
    change_limits,
    develop_player_stats,
    develop_stat,
    free_agent_games_tick,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _flat_stats(value: int = 50) -> dict[str, int]:
    """A 19-key stat dict with every STAT_FIELDS entry set to ``value``."""
    return {name: value for name in STAT_FIELDS}


# ===========================================================================
# §7.1 — TestStatFields
# ===========================================================================


class TestStatFields(SimpleTestCase):
    """``development.STAT_FIELDS`` mirrors ``teams.player_generator._STAT_FIELDS``
    byte-for-byte (the ONE allowed ``teams`` import in this file), 19 entries,
    capital-O ``Offensive_synergy`` present."""

    def test_stat_fields_equals_player_generator(self) -> None:
        # The ONE allowed teams.player_generator import, scoped to this test.
        from teams.player_generator import _STAT_FIELDS

        self.assertEqual(development.STAT_FIELDS, _STAT_FIELDS)

    def test_stat_fields_has_19_entries(self) -> None:
        self.assertEqual(len(STAT_FIELDS), 19)

    def test_stat_fields_is_a_tuple(self) -> None:
        self.assertIsInstance(STAT_FIELDS, tuple)

    def test_capital_o_offensive_synergy_present(self) -> None:
        self.assertIn("Offensive_synergy", STAT_FIELDS)
        # The lowercase variant must NOT be present (the intentional quirk).
        self.assertNotIn("offensive_synergy", STAT_FIELDS)

    def test_stat_min_max_constants(self) -> None:
        self.assertEqual(STAT_MIN, 0)
        self.assertEqual(STAT_MAX, 100)

    def test_archetype_maps_all_19_fields(self) -> None:
        # Every STAT_FIELDS entry has a group; no extras.
        self.assertEqual(set(_STAT_ARCHETYPE), set(STAT_FIELDS))
        self.assertEqual(len(_STAT_ARCHETYPE), 19)


# ===========================================================================
# §3.2 — TestBaseChange (every age-band boundary; monotone non-increasing)
# ===========================================================================


class TestBaseChange(SimpleTestCase):
    """``base_change(age)`` returns the locked age-table value at every band
    boundary; the curve is monotone non-increasing across age."""

    # The locked §3.2 table at each boundary age.
    _CASES = (
        (15, 2),
        (21, 2),
        (22, 1),
        (25, 1),
        (26, 0),
        (27, 0),
        (28, -1),
        (29, -1),
        (30, -2),
        (31, -2),
        (32, -3),
        (34, -3),
        (35, -4),
        (40, -4),
        (41, -5),
        (43, -5),
        (44, -6),
        (60, -6),
    )

    def test_each_boundary_age_returns_locked_value(self) -> None:
        for age, expected in self._CASES:
            self.assertEqual(
                base_change(age), expected, f"base_change({age}) != {expected}"
            )

    def test_returns_int(self) -> None:
        self.assertIsInstance(base_change(20), int)

    def test_monotone_non_increasing(self) -> None:
        prev = base_change(10)
        for age in range(11, 70):
            cur = base_change(age)
            self.assertLessEqual(
                cur, prev, f"base_change rose from age {age - 1} to {age}"
            )
            prev = cur

    def test_consumes_no_rng(self) -> None:
        # base_change takes a plain int and must touch no global RNG.
        random.seed(99)
        before = random.getstate()
        base_change(30)
        self.assertEqual(random.getstate(), before)


# ===========================================================================
# §3.3 — TestBaseChangeNoise (band bounds under seed; determinism)
# ===========================================================================


class TestBaseChangeNoise(SimpleTestCase):
    """``base_change_noise(age, rng)`` stays inside each age band's locked
    ``bound(...)`` window; deterministic for a fixed seed."""

    def test_young_band_bounds_under_seed(self) -> None:
        # age <= 23 ⇒ bound(gauss(0, 5), -4, 20).
        rng = random.Random(42)
        for _ in range(500):
            v = base_change_noise(20, rng)
            self.assertGreaterEqual(v, -4.0)
            self.assertLessEqual(v, 20.0)

    def test_mid_band_bounds_under_seed(self) -> None:
        # 24..25 ⇒ bound(gauss(0, 5), -4, 10).
        rng = random.Random(42)
        for age in (24, 25):
            for _ in range(300):
                v = base_change_noise(age, rng)
                self.assertGreaterEqual(v, -4.0)
                self.assertLessEqual(v, 10.0)

    def test_veteran_band_bounds_under_seed(self) -> None:
        # age >= 26 ⇒ bound(gauss(0, 3), -2, 4).
        rng = random.Random(42)
        for age in (26, 35, 50):
            for _ in range(300):
                v = base_change_noise(age, rng)
                self.assertGreaterEqual(v, -2.0)
                self.assertLessEqual(v, 4.0)

    def test_returns_float(self) -> None:
        self.assertIsInstance(base_change_noise(20, random.Random(0)), float)

    def test_deterministic_for_fixed_seed(self) -> None:
        a = [base_change_noise(20, r) for r in (random.Random(7),) for _ in range(5)]
        # Two fresh same-seed RNGs produce the same noise sequence.
        r1, r2 = random.Random(7), random.Random(7)
        s1 = [base_change_noise(20, r1) for _ in range(5)]
        s2 = [base_change_noise(20, r2) for _ in range(5)]
        self.assertEqual(s1, s2)

    def test_different_seeds_can_differ(self) -> None:
        seen = {base_change_noise(20, random.Random(s)) for s in range(30)}
        self.assertGreater(len(seen), 1, "the gaussian draw must vary with the seed")


# ===========================================================================
# §3.4 — TestAgeModifier (5 groups × boundaries)
# ===========================================================================


class TestAgeModifier(SimpleTestCase):
    """``age_modifier(group, age)`` returns the locked per-group value at each
    age boundary, for all 5 archetype groups."""

    def test_awareness_boundaries(self) -> None:
        cases = {
            21: 4.0,
            22: 3.0,
            23: 3.0,
            24: 1.0,
            27: 1.0,
            28: 0.0,
            31: 0.0,
            32: 0.5,
            45: 0.5,
        }
        for age, expected in cases.items():
            self.assertEqual(age_modifier("awareness", age), expected, f"age={age}")

    def test_skill_boundaries(self) -> None:
        cases = {
            27: 0.0,
            28: 0.5,
            31: 0.5,
            32: 1.5,
            50: 1.5,
        }
        for age, expected in cases.items():
            self.assertEqual(age_modifier("skill", age), expected, f"age={age}")

    def test_athletic_boundaries(self) -> None:
        cases = {
            23: 0.0,
            24: -0.5,
            27: -0.5,
            28: -2.0,
            31: -2.0,
            32: -4.0,
            45: -4.0,
        }
        for age, expected in cases.items():
            self.assertEqual(age_modifier("athletic", age), expected, f"age={age}")

    def test_team_boundaries(self) -> None:
        cases = {
            25: 1.0,
            26: 0.0,
            31: 0.0,
            32: -0.5,
            50: -0.5,
        }
        for age, expected in cases.items():
            self.assertEqual(age_modifier("team", age), expected, f"age={age}")

    def test_durable_is_always_zero(self) -> None:
        for age in (15, 25, 35, 50):
            self.assertEqual(age_modifier("durable", age), 0.0, f"age={age}")

    def test_returns_float(self) -> None:
        self.assertIsInstance(age_modifier("awareness", 20), float)


# ===========================================================================
# §3.4 — TestChangeLimits (each group boundary + awareness widening)
# ===========================================================================


class TestChangeLimits(SimpleTestCase):
    """``change_limits(group, age)`` returns the locked ``(lo, hi)`` per group;
    the awareness widening is ``7 + 5*(24 - age)`` for ``age <= 24``."""

    def test_awareness_widening_formula(self) -> None:
        # age <= 24 ⇒ (-3.0, 7.0 + 5.0 * (24 - age)).
        for age in (19, 20, 22, 24):
            lo, hi = change_limits("awareness", age)
            self.assertEqual(lo, -3.0)
            self.assertEqual(hi, 7.0 + 5.0 * (24 - age))
        # A 19-year-old's cap is 7 + 25 = 32 (the contract's worked example).
        self.assertEqual(change_limits("awareness", 19), (-3.0, 32.0))
        # At the boundary age 24 the widening term is zero ⇒ (-3, 7).
        self.assertEqual(change_limits("awareness", 24), (-3.0, 7.0))

    def test_awareness_above_24_is_flat(self) -> None:
        for age in (25, 30, 40):
            self.assertEqual(change_limits("awareness", age), (-3.0, 7.0))

    def test_skill_limits(self) -> None:
        for age in (20, 30, 40):
            self.assertEqual(change_limits("skill", age), (-3.0, 13.0))

    def test_athletic_limits(self) -> None:
        for age in (20, 30, 40):
            self.assertEqual(change_limits("athletic", age), (-12.0, 2.0))

    def test_team_limits(self) -> None:
        for age in (20, 30, 40):
            self.assertEqual(change_limits("team", age), (-2.0, 5.0))

    def test_durable_limits_effectively_unbounded(self) -> None:
        for age in (20, 30, 40):
            self.assertEqual(change_limits("durable", age), (-100.0, 100.0))

    def test_returns_tuple_of_two(self) -> None:
        result = change_limits("skill", 25)
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)


# ===========================================================================
# §3.6 — TestDevelopStat (clamp to change limits; floor to [0,100])
# ===========================================================================


class TestDevelopStat(SimpleTestCase):
    """``develop_stat`` clamps the per-season delta to the change limits, then
    floors the result to ``[0, 100]``."""

    def test_huge_positive_cannot_exceed_floor_100(self) -> None:
        # A huge effective base change still can't push above 100.
        rng = random.Random(1)
        for _ in range(50):
            v = develop_stat(99, "accuracy", 20, 50.0, rng)
            self.assertLessEqual(v, 100)
            self.assertGreaterEqual(v, 0)

    def test_huge_negative_cannot_drop_below_0(self) -> None:
        rng = random.Random(1)
        for _ in range(50):
            v = develop_stat(1, "speed", 40, -50.0, rng)
            self.assertGreaterEqual(v, 0)
            self.assertLessEqual(v, 100)

    def test_delta_clamped_to_change_limits_high(self) -> None:
        # athletic hi limit is +2; even a giant effective base change can't add
        # more than 2 (before the [0,100] floor) — so from 50 the result is at
        # most 52.
        rng = random.Random(0)
        for _ in range(50):
            v = develop_stat(50, "speed", 20, 100.0, rng)
            self.assertLessEqual(v, 52)

    def test_delta_clamped_to_change_limits_low(self) -> None:
        # athletic lo limit is -12; from 90 the result is at least 78.
        rng = random.Random(0)
        for _ in range(50):
            v = develop_stat(90, "speed", 40, -100.0, rng)
            self.assertGreaterEqual(v, 78)

    def test_returns_int(self) -> None:
        self.assertIsInstance(
            develop_stat(50, "accuracy", 25, 1.0, random.Random(0)), int
        )

    def test_deterministic_for_fixed_seed(self) -> None:
        a = develop_stat(50, "accuracy", 22, 1.5, random.Random(123))
        b = develop_stat(50, "accuracy", 22, 1.5, random.Random(123))
        self.assertEqual(a, b)


# ===========================================================================
# §3.7 — TestDevelopPlayerStats (19 keys; DIRECTION; clamp; determinism)
# ===========================================================================


class TestDevelopPlayerStats(SimpleTestCase):
    """``develop_player_stats`` returns exactly 19 keys; the aggregate
    sum-of-deltas trends UP for a young player and DOWN for an old player under
    a seeded RNG (direction, NOT magnitude); 0/100 clamp at extremes;
    deterministic for a fixed seed."""

    def test_returns_exactly_19_keys(self) -> None:
        out = develop_player_stats(_flat_stats(50), 25, random.Random(42))
        self.assertEqual(set(out), set(STAT_FIELDS))
        self.assertEqual(len(out), 19)

    def test_all_values_in_range(self) -> None:
        out = develop_player_stats(_flat_stats(50), 25, random.Random(42))
        for name, val in out.items():
            self.assertGreaterEqual(val, 0, name)
            self.assertLessEqual(val, 100, name)

    def test_young_player_trends_up_in_aggregate(self) -> None:
        # Age 20: base_change +2 and most groups push positive ⇒ the sum of the
        # 19 deltas is positive under a seed. Assert DIRECTION, not magnitude.
        base = _flat_stats(50)
        out = develop_player_stats(base, 20, random.Random(42))
        delta = sum(out[name] - base[name] for name in STAT_FIELDS)
        self.assertGreater(delta, 0, "young player should net-trend up")

    def test_old_player_trends_down_in_aggregate(self) -> None:
        # Age 40: base_change -4 and athletic/team push negative ⇒ the sum of
        # the 19 deltas is negative under a seed.
        base = _flat_stats(50)
        out = develop_player_stats(base, 40, random.Random(42))
        delta = sum(out[name] - base[name] for name in STAT_FIELDS)
        self.assertLess(delta, 0, "old player should net-trend down")

    def test_clamp_at_floor_zero(self) -> None:
        out = develop_player_stats(_flat_stats(0), 40, random.Random(7))
        for name, val in out.items():
            self.assertGreaterEqual(val, 0, name)

    def test_clamp_at_ceiling_100(self) -> None:
        out = develop_player_stats(_flat_stats(100), 20, random.Random(7))
        for name, val in out.items():
            self.assertLessEqual(val, 100, name)

    def test_deterministic_for_fixed_seed(self) -> None:
        first = develop_player_stats(_flat_stats(50), 25, random.Random(42))
        second = develop_player_stats(_flat_stats(50), 25, random.Random(42))
        self.assertEqual(first, second)

    def test_returns_fresh_dict_does_not_mutate_input(self) -> None:
        base = _flat_stats(50)
        snapshot = dict(base)
        develop_player_stats(base, 25, random.Random(0))
        self.assertEqual(base, snapshot, "input mapping must not be mutated")


# ===========================================================================
# §3.8 — TestFreeAgentGamesTick (bounds + (0,rng)==0 + determinism)
# ===========================================================================


class TestFreeAgentGamesTick(SimpleTestCase):
    """``free_agent_games_tick(median_active, rng)`` returns
    ``rng.randint(0, median_active // 2)`` — bounded, ``0`` at the degenerate
    no-active case, deterministic for a fixed seed."""

    def test_bounds(self) -> None:
        rng = random.Random(42)
        for median in (4, 10, 25, 100):
            for _ in range(200):
                v = free_agent_games_tick(median, rng)
                self.assertGreaterEqual(v, 0)
                self.assertLessEqual(v, median // 2)

    def test_zero_median_returns_zero(self) -> None:
        rng = random.Random(42)
        for _ in range(20):
            self.assertEqual(free_agent_games_tick(0, rng), 0)

    def test_negative_median_returns_zero(self) -> None:
        # max(0, median_active) // 2 ⇒ a defensive negative still yields 0.
        rng = random.Random(42)
        self.assertEqual(free_agent_games_tick(-5, rng), 0)

    def test_returns_int(self) -> None:
        self.assertIsInstance(free_agent_games_tick(10, random.Random(0)), int)

    def test_deterministic_for_fixed_seed(self) -> None:
        r1, r2 = random.Random(99), random.Random(99)
        s1 = [free_agent_games_tick(10, r1) for _ in range(10)]
        s2 = [free_agent_games_tick(10, r2) for _ in range(10)]
        self.assertEqual(s1, s2)


# ===========================================================================
# §7.1 — TestNoDjangoImportsLeaked
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.development`` in a fresh subprocess must not pull in
    ``django.*`` (nor ``matches.models``) — the frozen allowlist is
    ``dataclasses`` / ``typing`` / ``random`` / ``collections``. Mirrors the
    LG-02x-1 ``test_draw.py::TestNoDjangoImportsLeaked`` precedent.
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
            import sys
            sys.path.insert(0, {str(project_root)!r})
            import matches.development  # noqa: F401

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

    def test_develop_functions_pull_in_no_django(self) -> None:
        """Importing + exercising the develop functions in a fresh subprocess
        must not pull in ``django.*`` — they import nothing new (``random``
        only)."""
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
            from matches.development import (
                STAT_FIELDS,
                base_change,
                base_change_noise,
                develop_player_stats,
                develop_stat,
                free_agent_games_tick,
            )

            rng = random.Random(0)
            base_change(25)
            base_change_noise(25, rng)
            develop_stat(50, "accuracy", 25, 1.0, rng)
            develop_player_stats({{name: 50 for name in STAT_FIELDS}}, 25, rng)
            free_agent_games_tick(10, rng)

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
