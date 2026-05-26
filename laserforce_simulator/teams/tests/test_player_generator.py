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
    assign_slots,
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


if __name__ == "__main__":
    unittest.main()
