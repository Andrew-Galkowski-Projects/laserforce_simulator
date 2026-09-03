"""LG-00 — Pure-unit tests for ``teams/player_generator.py``.

No DB, no Django imports in the assertion path. The seam contract is locked at
``.claude/worktrees/lg-00-seam-contract.md``. Mirrors the HX-01 / HX-02 /
RES-04 / RV-03 pure-module precedent — Django imports are forbidden here, and a
defensive subprocess check pins that ``teams.player_generator`` itself does not
transitively pull Django into ``sys.modules``.

The contract's `_STAT_FIELDS` 19-tuple is **hard-coded** in this file (the
copy below mirrors §2c of the seam contract verbatim) — do NOT import the
tuple from ``teams.player_generator`` to assert against itself, that would be
tautological.

The contract's `_ROLE_NAMES` 5-tuple is also hard-coded here for the same
reason. Both copies are the pinned "expected" values that the production
module must agree with byte-for-byte.
"""

from __future__ import annotations

import random
import unittest

from teams.player_generator import (
    LEAGUE_SPREAD_DELTAS,
    assign_slots,
    compute_tier_means,
    draw_preferred_roles,
    draw_stats,
)

# ---------------------------------------------------------------------------
# Hard-coded contract pins (§2b + §2c of lg-00-seam-contract.md)
# ---------------------------------------------------------------------------

# 19-tuple of Player stat field names, in canonical order — 3 awareness, 1
# decision, 5 physical, 2 team, 8 role. ``Offensive_synergy`` is intentionally
# capital-O — matches the existing field name in teams/models.py line 203.
_EXPECTED_STAT_FIELDS: tuple[str, ...] = (
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

# 5-tuple of lowercase role names used by ``Player.preferred_roles`` and
# ``PlayerRoundState.role``.
_EXPECTED_ROLE_NAMES: tuple[str, ...] = (
    "commander",
    "heavy",
    "scout",
    "medic",
    "ammo",
)

# 6-tuple of slot keys; note Scout has TWO slots, both bound to ``"scout"``.
_EXPECTED_SLOT_KEYS: list[str] = [
    "commander",
    "heavy",
    "scout_1",
    "scout_2",
    "medic",
    "ammo",
]


# ---------------------------------------------------------------------------
# §2d — TestDrawStats
# ---------------------------------------------------------------------------


class TestDrawStats(unittest.TestCase):
    """Pure-unit coverage of ``draw_stats(rng, mean, std_dev)``."""

    def test_output_has_19_keys_in_canonical_order(self) -> None:
        """``list(result.keys())`` equals the hard-coded 19-tuple as a list."""
        result = draw_stats(random.Random(0), 50.0, 15.0)
        self.assertEqual(list(result.keys()), list(_EXPECTED_STAT_FIELDS))

    def test_all_values_int_in_0_100(self) -> None:
        """Every returned value is an ``int`` and ``0 <= v <= 100``."""
        result = draw_stats(random.Random(0), 50.0, 15.0)
        self.assertEqual(len(result), 19)
        for key, value in result.items():
            self.assertIsInstance(value, int, f"{key} value {value!r} is not int")
            # Booleans are a subclass of int; exclude them explicitly.
            self.assertNotIsInstance(value, bool, f"{key} value {value!r} is bool")
            self.assertGreaterEqual(value, 0, f"{key} value {value} < 0")
            self.assertLessEqual(value, 100, f"{key} value {value} > 100")

    def test_keys_are_real_player_fields(self) -> None:
        """Every returned key is in the hard-coded 19-tuple (no extras)."""
        result = draw_stats(random.Random(0), 50.0, 15.0)
        self.assertEqual(set(result.keys()), set(_EXPECTED_STAT_FIELDS))

    def test_clamp_at_0_and_100_triggers_with_extreme_std_dev(self) -> None:
        """Over 5000 draws with ``std_dev=40``, both ``0`` and ``100`` appear.

        Each draw is a 19-element dict; with mean=50, std_dev=40, the
        truncated-Gaussian tails must hit both endpoints somewhere in the
        ~95k value population. The seam contract pins
        ``max(0, min(100, round(rng.gauss(mean, std_dev))))``.
        """
        rng = random.Random(42)
        observed: set[int] = set()
        for _ in range(5000):
            observed.update(draw_stats(rng, 50.0, 40.0).values())
        self.assertIn(0, observed, "no clamp-to-0 observed in 5000 draws")
        self.assertIn(100, observed, "no clamp-to-100 observed in 5000 draws")

    def test_same_seed_produces_identical_output(self) -> None:
        """Two independent ``random.Random(123)`` produce equal dicts."""
        rng_a = random.Random(123)
        rng_b = random.Random(123)
        self.assertEqual(
            draw_stats(rng_a, 50.0, 15.0),
            draw_stats(rng_b, 50.0, 15.0),
        )


# ---------------------------------------------------------------------------
# §2e — TestDrawPreferredRoles
# ---------------------------------------------------------------------------


class TestDrawPreferredRoles(unittest.TestCase):
    """Pure-unit coverage of ``draw_preferred_roles(rng)``."""

    def test_output_length_is_1_2_or_3(self) -> None:
        """Over 1000 seeded draws, every output has length in {1, 2, 3}."""
        rng = random.Random(0)
        for _ in range(1000):
            self.assertIn(len(draw_preferred_roles(rng)), {1, 2, 3})

    def test_all_values_are_valid_roles(self) -> None:
        """Every output is a subset of the 5-tuple ``_ROLE_NAMES``."""
        rng = random.Random(1)
        valid = set(_EXPECTED_ROLE_NAMES)
        for _ in range(1000):
            out = draw_preferred_roles(rng)
            self.assertTrue(
                set(out).issubset(valid),
                f"unexpected role(s) in {out!r}",
            )

    def test_no_duplicates_within_a_single_draw(self) -> None:
        """``len(set(out)) == len(out)`` for every draw."""
        rng = random.Random(2)
        for _ in range(1000):
            out = draw_preferred_roles(rng)
            self.assertEqual(len(set(out)), len(out), f"duplicates in {out!r}")

    def test_count_distribution_approximates_70_20_10(self) -> None:
        """Over N=10_000 seeded draws, the length distribution matches 70/20/10
        within the §11.1 tolerances (±0.03, ±0.03, ±0.02)."""
        rng = random.Random(7)
        counts = {1: 0, 2: 0, 3: 0}
        n = 10_000
        for _ in range(n):
            counts[len(draw_preferred_roles(rng))] += 1
        self.assertAlmostEqual(counts[1] / n, 0.70, delta=0.03)
        self.assertAlmostEqual(counts[2] / n, 0.20, delta=0.03)
        self.assertAlmostEqual(counts[3] / n, 0.10, delta=0.02)

    def test_same_seed_produces_identical_output(self) -> None:
        """Two independent ``random.Random(123)`` produce equal lists across
        a 20-call sequence."""
        rng_a = random.Random(123)
        rng_b = random.Random(123)
        out_a = [draw_preferred_roles(rng_a) for _ in range(20)]
        out_b = [draw_preferred_roles(rng_b) for _ in range(20)]
        self.assertEqual(out_a, out_b)


# ---------------------------------------------------------------------------
# §2f — TestAssignSlots
# ---------------------------------------------------------------------------


class TestAssignSlots(unittest.TestCase):
    """Pure-unit coverage of ``assign_slots(preferred_roles_per_player)``."""

    def test_full_match_each_player_prefers_their_slot_role(self) -> None:
        """All 6 players align with their canonical slot role."""
        preferred = [
            ["commander"],
            ["heavy"],
            ["scout"],
            ["scout"],
            ["medic"],
            ["ammo"],
        ]
        result = assign_slots(preferred)
        self.assertEqual(
            result,
            {
                "commander": 0,
                "heavy": 1,
                "scout_1": 2,
                "scout_2": 3,
                "medic": 4,
                "ammo": 5,
            },
        )

    def test_partial_match_unmatched_slots_are_None(self) -> None:
        """Only 4 of 6 players prefer slot-aligning roles; the rest are None.

        Players 0/1/2/3 prefer commander/heavy/medic/ammo; players 4/5 prefer
        unrelated single roles (we use ``["commander"]`` would conflict since
        the commander slot is already filled by player 0 — but the
        contract's greedy algorithm walks slot order and skips already-used
        players, so any "no match available" slot ends up None). For a clean
        partial-match scenario, players 4/5 are given an empty preference
        list (no roles preferred at all).
        """
        preferred = [
            ["commander"],
            ["heavy"],
            ["medic"],
            ["ammo"],
            [],  # prefers nothing
            [],  # prefers nothing
        ]
        result = assign_slots(preferred)
        self.assertEqual(result["commander"], 0)
        self.assertEqual(result["heavy"], 1)
        self.assertIsNone(result["scout_1"])
        self.assertIsNone(result["scout_2"])
        self.assertEqual(result["medic"], 2)
        self.assertEqual(result["ammo"], 3)

    def test_over_prefer_scout_third_scout_preferer_displaced(self) -> None:
        """Three players prefer Scout; the two lowest-index Scout-preferers
        fill ``scout_1`` and ``scout_2``. The third Scout-preferer is NOT
        assigned to any Scout slot (the third player's index appears in NO
        Scout slot in the output).
        """
        preferred = [
            ["scout"],  # 0 — fills scout_1
            ["scout"],  # 1 — fills scout_2
            ["scout"],  # 2 — displaced (no Scout slot available)
            ["commander"],  # 3
            ["medic"],  # 4
            ["ammo"],  # 5
        ]
        result = assign_slots(preferred)
        # The two lowest-index Scout-preferers fill the Scout slots.
        self.assertEqual(result["scout_1"], 0)
        self.assertEqual(result["scout_2"], 1)
        # Player index 2 does NOT appear in any Scout slot.
        self.assertNotIn(2, (result["scout_1"], result["scout_2"]))
        # And player 2 was never matched to commander/heavy/medic/ammo either
        # (since their only preference is ``"scout"``), so the algorithm
        # never assigns them. Player 2's index appears in no slot value.
        self.assertNotIn(2, result.values())

    def test_no_player_prefers_commander_slot_is_None(self) -> None:
        """Every player's ``preferred_roles`` excludes ``"commander"``."""
        preferred = [
            ["heavy"],
            ["scout"],
            ["scout"],
            ["medic"],
            ["ammo"],
            ["heavy"],
        ]
        result = assign_slots(preferred)
        self.assertIsNone(result["commander"])

    def test_assign_slots_deterministic_tiebreak(self) -> None:
        """Player 0 and player 1 both prefer ``"heavy"`` — lower index wins.

        Run twice in the same test to also pin determinism.
        """
        preferred = [
            ["heavy"],
            ["heavy"],
            ["scout"],
            ["scout"],
            ["medic"],
            ["ammo"],
        ]
        result_first = assign_slots(preferred)
        result_second = assign_slots(preferred)
        self.assertEqual(result_first["heavy"], 0)
        self.assertEqual(result_first, result_second)

    def test_assign_slots_output_keys_are_slot_key_tuple_in_order(self) -> None:
        """The output dict's keys are exactly ``_SLOT_KEYS`` in order."""
        preferred = [
            ["commander"],
            ["heavy"],
            ["scout"],
            ["scout"],
            ["medic"],
            ["ammo"],
        ]
        result = assign_slots(preferred)
        self.assertEqual(list(result.keys()), _EXPECTED_SLOT_KEYS)


# ---------------------------------------------------------------------------
# §7.1 defensive check — no Django imports leaked
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """The pure module must import cleanly without pulling in Django.

    Mirrors the HX-01 / HX-02 / RES-04 / RV-03 precedent: a fresh subprocess
    imports ``teams.player_generator`` and prints any ``django.*`` modules in
    ``sys.modules``. We assert the printed output is empty.
    """

    def test_no_django_imports_leaked(self) -> None:
        import subprocess
        import sys

        # First — the in-process surface check (mirrors test_career_stats.py
        # case #15). Catches the trivial "module-level ``from django import
        # models``" mistake without spinning up a subprocess.
        import teams.player_generator as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))

        # Second — the subprocess check. A pristine Python interpreter
        # imports ``teams.player_generator`` and reports any django.* modules
        # that ended up in ``sys.modules``. If the pure module is genuinely
        # Django-free this must be empty.
        script = (
            "import teams.player_generator; "
            "import sys; "
            "print(','.join(m for m in sys.modules if m.startswith('django')))"
        )
        proc = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            check=False,
        )
        # stderr is informational on PYTHONPATH issues but the contract pin
        # is on stdout: a non-empty stdout means Django modules leaked.
        leaked = proc.stdout.strip()
        self.assertEqual(
            leaked,
            "",
            f"django modules leaked into teams.player_generator: {leaked!r}\n"
            f"stderr: {proc.stderr!r}",
        )


# ---------------------------------------------------------------------------
# CRE-02 — compute_tier_means + LEAGUE_SPREAD_DELTAS (seam contract §3)
# ---------------------------------------------------------------------------
#
# Contract: `.claude/worktrees/cre-02-seam-contract.md`.
#
# The expected vectors below are HARD-CODED contract pins copied from §3.3 of
# that document — deliberately NOT recomputed from the production formula,
# which would be tautological (same precedent as `_EXPECTED_STAT_FIELDS`
# above). Compared with `assertAlmostEqual(places=6)`; the contract forbids
# pinning the exact float repr.
#
# MEAN-PRESERVATION CAVEAT (contract §3.2): the clamp to [0, 100] is applied
# AFTER the ramp, so it breaks mean preservation at the extremes. Mean
# preservation is therefore asserted ONLY on un-clamped parameter sets
# (mean=50). The mean=95 / mean=5 cases assert bounds + monotonicity only.

_EXPECTED_LEAGUE_SPREAD_DELTAS: dict[str, float] = {
    "even": 0.0,
    "tiered": 8.0,
    "steep": 16.0,
}

# compute_tier_means(8, 50, 8) — "Tiered". List mean 50.0, head-tail gap 16.0.
_EXPECTED_TIERED_8_50: tuple[float, ...] = (
    58.000000,
    55.714286,
    53.428571,
    51.142857,
    48.857143,
    46.571429,
    44.285714,
    42.000000,
)

# compute_tier_means(8, 50, 16) — "Steep". List mean 50.0, head-tail gap 32.0.
_EXPECTED_STEEP_8_50: tuple[float, ...] = (
    66.000000,
    61.428571,
    56.857143,
    52.285714,
    47.714286,
    43.142857,
    38.571429,
    34.000000,
)

# compute_tier_means(8, 95, 16) — head clamps at 100.0. NO mean assertion.
_EXPECTED_CLAMPED_HIGH_8_95: tuple[float, ...] = (
    100.000000,
    100.000000,
    100.000000,
    97.285714,
    92.714286,
    88.142857,
    83.571429,
    79.000000,
)

# compute_tier_means(8, 5, 16) — tail clamps at 0.0. NO mean assertion.
_EXPECTED_CLAMPED_LOW_8_5: tuple[float, ...] = (
    21.000000,
    16.428571,
    11.857143,
    7.285714,
    2.714286,
    0.000000,
    0.000000,
    0.000000,
)


class TestComputeTierMeans(unittest.TestCase):
    """CRE-02 — the linear, mean-preserving tier ramp (pure: no DB, no RNG)."""

    # -- LEAGUE_SPREAD_DELTAS ----------------------------------------------

    def test_league_spread_deltas_has_exactly_three_keys(self) -> None:
        self.assertEqual(set(LEAGUE_SPREAD_DELTAS), set(_EXPECTED_LEAGUE_SPREAD_DELTAS))
        self.assertEqual(len(LEAGUE_SPREAD_DELTAS), 3)

    def test_league_spread_deltas_values_are_0_8_16(self) -> None:
        for key, expected in _EXPECTED_LEAGUE_SPREAD_DELTAS.items():
            with self.subTest(spread=key):
                self.assertAlmostEqual(LEAGUE_SPREAD_DELTAS[key], expected, places=6)

    def test_league_spread_delta_values_are_floats(self) -> None:
        for key, value in LEAGUE_SPREAD_DELTAS.items():
            with self.subTest(spread=key):
                self.assertIsInstance(value, float)

    # -- shape --------------------------------------------------------------

    def test_returns_list_of_length_num_teams_for_every_spread(self) -> None:
        for num_teams in (2, 4, 8, 16):
            for spread, delta in _EXPECTED_LEAGUE_SPREAD_DELTAS.items():
                with self.subTest(num_teams=num_teams, spread=spread):
                    result = compute_tier_means(num_teams, 50, delta)
                    self.assertIsInstance(result, list)
                    self.assertEqual(len(result), num_teams)

    def test_every_entry_is_a_float(self) -> None:
        for delta in (0.0, 8.0, 16.0):
            with self.subTest(delta=delta):
                for value in compute_tier_means(8, 50, delta):
                    self.assertIsInstance(value, float)

    # -- worked examples (contract §3.3) ------------------------------------

    def test_tiered_worked_example_matches_index_by_index(self) -> None:
        result = compute_tier_means(8, 50, 8)
        self.assertEqual(len(result), len(_EXPECTED_TIERED_8_50))
        for i, expected in enumerate(_EXPECTED_TIERED_8_50):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], expected, places=6)

    def test_steep_worked_example_matches_index_by_index(self) -> None:
        result = compute_tier_means(8, 50, 16)
        self.assertEqual(len(result), len(_EXPECTED_STEEP_8_50))
        for i, expected in enumerate(_EXPECTED_STEEP_8_50):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], expected, places=6)

    def test_head_minus_tail_gap_is_two_delta(self) -> None:
        for delta, expected_gap in ((8, 16.0), (16, 32.0)):
            with self.subTest(delta=delta):
                result = compute_tier_means(8, 50, delta)
                self.assertAlmostEqual(result[0] - result[-1], expected_gap, places=6)

    # -- monotonicity -------------------------------------------------------

    def test_monotonically_non_increasing_for_tiered_and_steep(self) -> None:
        for delta in (8, 16):
            for num_teams in (2, 4, 8, 16):
                with self.subTest(delta=delta, num_teams=num_teams):
                    result = compute_tier_means(num_teams, 50, delta)
                    for i in range(len(result) - 1):
                        self.assertGreaterEqual(
                            result[i],
                            result[i + 1],
                            f"index {i} < index {i + 1}: {result}",
                        )

    def test_strictly_decreasing_when_unclamped(self) -> None:
        result = compute_tier_means(8, 50, 16)
        for i in range(len(result) - 1):
            with self.subTest(i=i):
                self.assertGreater(result[i], result[i + 1])

    # -- mean preservation (UN-CLAMPED parameter sets ONLY) -----------------

    def test_mean_preserved_at_mean_50_for_nonzero_deltas(self) -> None:
        for delta in (8, 16):
            for num_teams in (2, 4, 8, 16):
                with self.subTest(delta=delta, num_teams=num_teams):
                    result = compute_tier_means(num_teams, 50, delta)
                    self.assertAlmostEqual(sum(result) / len(result), 50.0, places=6)

    def test_mean_preserved_for_every_spread_delta_at_mean_50(self) -> None:
        for spread, delta in _EXPECTED_LEAGUE_SPREAD_DELTAS.items():
            with self.subTest(spread=spread):
                result = compute_tier_means(8, 50, delta)
                self.assertAlmostEqual(sum(result) / len(result), 50.0, places=6)

    # -- clamping (bounds + monotonicity ONLY, never mean preservation) -----

    def test_clamping_at_high_mean_never_exceeds_100(self) -> None:
        result = compute_tier_means(8, 95, 16)
        for i, value in enumerate(result):
            with self.subTest(i=i):
                self.assertLessEqual(value, 100.0)
                self.assertGreaterEqual(value, 0.0)

    def test_clamping_at_high_mean_head_entries_are_exactly_100(self) -> None:
        result = compute_tier_means(8, 95, 16)
        for i in (0, 1, 2):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], 100.0, places=6)

    def test_clamping_at_high_mean_matches_reference_vector(self) -> None:
        result = compute_tier_means(8, 95, 16)
        self.assertEqual(len(result), len(_EXPECTED_CLAMPED_HIGH_8_95))
        for i, expected in enumerate(_EXPECTED_CLAMPED_HIGH_8_95):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], expected, places=6)

    def test_clamping_at_high_mean_still_non_increasing(self) -> None:
        result = compute_tier_means(8, 95, 16)
        for i in range(len(result) - 1):
            with self.subTest(i=i):
                self.assertGreaterEqual(result[i], result[i + 1])

    def test_clamping_at_low_mean_never_drops_below_0(self) -> None:
        result = compute_tier_means(8, 5, 16)
        for i, value in enumerate(result):
            with self.subTest(i=i):
                self.assertGreaterEqual(value, 0.0)
                self.assertLessEqual(value, 100.0)

    def test_clamping_at_low_mean_tail_entries_are_exactly_0(self) -> None:
        result = compute_tier_means(8, 5, 16)
        for i in (5, 6, 7):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], 0.0, places=6)

    def test_clamping_at_low_mean_matches_reference_vector(self) -> None:
        result = compute_tier_means(8, 5, 16)
        self.assertEqual(len(result), len(_EXPECTED_CLAMPED_LOW_8_5))
        for i, expected in enumerate(_EXPECTED_CLAMPED_LOW_8_5):
            with self.subTest(i=i):
                self.assertAlmostEqual(result[i], expected, places=6)

    def test_clamping_at_low_mean_still_non_increasing(self) -> None:
        result = compute_tier_means(8, 5, 16)
        for i in range(len(result) - 1):
            with self.subTest(i=i):
                self.assertGreaterEqual(result[i], result[i + 1])

    def test_bounds_hold_across_a_parameter_grid(self) -> None:
        for mean in (0, 5, 50, 95, 100):
            for delta in (0, 8, 16):
                for num_teams in (1, 2, 4, 8, 16):
                    with self.subTest(mean=mean, delta=delta, num_teams=num_teams):
                        for value in compute_tier_means(num_teams, mean, delta):
                            self.assertGreaterEqual(value, 0.0)
                            self.assertLessEqual(value, 100.0)

    # -- degenerate branches ------------------------------------------------

    def test_num_teams_one_returns_single_entry_at_mean(self) -> None:
        for delta in (0, 8, 16):
            with self.subTest(delta=delta):
                result = compute_tier_means(1, 50, delta)
                self.assertEqual(len(result), 1)
                self.assertAlmostEqual(result[0], 50.0, places=6)

    def test_num_teams_zero_returns_empty_list(self) -> None:
        for delta in (0, 8, 16):
            with self.subTest(delta=delta):
                self.assertEqual(compute_tier_means(0, 50, delta), [])

    def test_delta_zero_returns_flat_vector(self) -> None:
        for num_teams in (1, 8):
            with self.subTest(num_teams=num_teams):
                result = compute_tier_means(num_teams, 50, 0)
                self.assertEqual(len(result), num_teams)
                for value in result:
                    self.assertAlmostEqual(value, 50.0, places=6)

    def test_delta_zero_is_flat_at_a_non_50_mean(self) -> None:
        for value in compute_tier_means(8, 73, 0):
            self.assertAlmostEqual(value, 73.0, places=6)

    def test_delta_zero_does_not_clamp_a_valid_mean(self) -> None:
        # Degenerate branch returns ``[float(mean)] * n`` — no ramp, so an
        # extreme-but-legal mean survives untouched at both ends.
        self.assertEqual(compute_tier_means(4, 100, 0), [100.0] * 4)
        self.assertEqual(compute_tier_means(4, 0, 0), [0.0] * 4)

    def test_two_teams_is_mean_plus_minus_delta(self) -> None:
        result = compute_tier_means(2, 50, 16)
        self.assertAlmostEqual(result[0], 66.0, places=6)
        self.assertAlmostEqual(result[1], 34.0, places=6)

    # -- purity -------------------------------------------------------------

    def test_repeated_calls_are_identical(self) -> None:
        self.assertEqual(compute_tier_means(8, 50, 16), compute_tier_means(8, 50, 16))

    def test_accepts_a_float_mean(self) -> None:
        result = compute_tier_means(4, 50.5, 8)
        self.assertEqual(len(result), 4)
        self.assertAlmostEqual(sum(result) / len(result), 50.5, places=6)


if __name__ == "__main__":
    unittest.main()
