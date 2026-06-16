"""CAR-02 — pure-unit tests for ``matches.owner_mood`` (seam contract §1 / §6.1).

Covers the four pure entry points of the owner-mood module — built from plain
ints / strings / frozen dataclasses, NO DB, NO mocks:

* ``compute_wins_delta(won, games) -> float`` — the .500-scaled regular-season
  wins delta; ``games == 0`` ⇒ ``0.0`` (no div-by-zero).
* ``compute_playoffs_delta(playoff_result, rounds_won, num_rounds) -> float`` —
  the champion / seeded / missed / none branches, the ``seeded`` scaling, the
  ``num_rounds == 0`` defensive ``0.0``, and the unknown-string forgiving ``0.0``.
* ``cap_cumulative(prev_cumulative, delta) -> float`` — upside-only cap at
  ``MOOD_FACTOR_CAP`` (no negative floor).
* ``decide_verdict(totals, deltas, *, seasons_in_tenure) -> Verdict`` — the
  grace gate, the fire threshold, the two hot-seat projection levels, and the
  level-1-wins-when-both-hold ordering.

Plus ``TestNoDjangoImportsLeaked`` — the subprocess fresh-import + ``sys.modules``
walk that defends the frozen ``dataclasses`` / ``typing`` / ``collections``-only
import allowlist (mirrors the ``matches.development`` /
``matches.season_awards`` precedent).

Assertion discipline (CAR-02 §6): assert on the returned floats / verdict
strings / hot-seat levels — NEVER on simulated point totals (every fixture here
is a hand-built scalar, so there are no sim totals at all).

Written test-first against the CAR-02 seam contract
(``.claude/worktrees/car-02-performance-based-firing-seam-contract.md``); these
FAIL until the Code agent lands ``matches/owner_mood.py``.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from matches import owner_mood
from matches.owner_mood import (
    FIRE_THRESHOLD,
    GRACE_PERIOD_SEASONS,
    MOOD_FACTOR_CAP,
    PLAYOFF_ADVANCE_SCALE,
    PLAYOFF_MISS,
    PLAYOFF_TITLE,
    WINS_BASELINE_SCALE,
    WINS_FACTOR,
    MoodDeltas,
    MoodTotals,
    Verdict,
    cap_cumulative,
    compute_playoffs_delta,
    compute_wins_delta,
    decide_verdict,
)

# ===========================================================================
# §1.1 — TestConstants (the locked module constants)
# ===========================================================================


class TestConstants(SimpleTestCase):
    """The locked §1.1 constant values."""

    def test_locked_values(self) -> None:
        self.assertEqual(WINS_FACTOR, 1.0)
        self.assertEqual(WINS_BASELINE_SCALE, 0.25)
        self.assertEqual(PLAYOFF_TITLE, 0.2)
        self.assertEqual(PLAYOFF_MISS, -0.2)
        self.assertEqual(PLAYOFF_ADVANCE_SCALE, 0.16)
        self.assertEqual(MOOD_FACTOR_CAP, 1.0)
        self.assertEqual(FIRE_THRESHOLD, -1.0)
        self.assertEqual(GRACE_PERIOD_SEASONS, 2)


# ===========================================================================
# §1.2 — TestDataclasses (frozen + pinned field order)
# ===========================================================================


class TestDataclasses(SimpleTestCase):
    """``MoodDeltas`` / ``MoodTotals`` / ``Verdict`` are frozen with the pinned
    field order."""

    def test_mood_deltas_fields_and_frozen(self) -> None:
        d = MoodDeltas(wins=0.1, playoffs=0.2, money=0.0)
        self.assertEqual(d.wins, 0.1)
        self.assertEqual(d.playoffs, 0.2)
        self.assertEqual(d.money, 0.0)
        with self.assertRaises(Exception):
            d.wins = 0.5  # type: ignore[misc]

    def test_mood_totals_fields_and_frozen(self) -> None:
        t = MoodTotals(wins=0.3, playoffs=-0.1, money=0.0)
        self.assertEqual(t.wins, 0.3)
        self.assertEqual(t.playoffs, -0.1)
        self.assertEqual(t.money, 0.0)
        with self.assertRaises(Exception):
            t.playoffs = 0.0  # type: ignore[misc]

    def test_verdict_fields_and_frozen(self) -> None:
        v = Verdict(outcome="retained", hot_seat_level=0)
        self.assertEqual(v.outcome, "retained")
        self.assertEqual(v.hot_seat_level, 0)
        with self.assertRaises(Exception):
            v.outcome = "fired"  # type: ignore[misc]

    def test_positional_field_order(self) -> None:
        # Positional construction pins the field order.
        d = MoodDeltas(0.1, 0.2, 0.0)
        self.assertEqual((d.wins, d.playoffs, d.money), (0.1, 0.2, 0.0))
        t = MoodTotals(0.3, 0.4, 0.0)
        self.assertEqual((t.wins, t.playoffs, t.money), (0.3, 0.4, 0.0))
        v = Verdict("hot_seat", 1)
        self.assertEqual((v.outcome, v.hot_seat_level), ("hot_seat", 1))


# ===========================================================================
# §1.3 — TestComputeWinsDelta
# ===========================================================================


class TestComputeWinsDelta(SimpleTestCase):
    """``WINS_FACTOR * WINS_BASELINE_SCALE * (won - games/2) / (games/2)``;
    ``games == 0`` ⇒ ``0.0``."""

    def test_exactly_500_is_zero(self) -> None:
        # 5 of 10 ⇒ exactly .500 ⇒ 0.
        self.assertEqual(compute_wins_delta(5, 10), 0.0)
        # 1 of 2.
        self.assertEqual(compute_wins_delta(1, 2), 0.0)
        # 6 of 12.
        self.assertEqual(compute_wins_delta(6, 12), 0.0)

    def test_above_500_is_positive(self) -> None:
        self.assertGreater(compute_wins_delta(8, 10), 0.0)
        self.assertGreater(compute_wins_delta(2, 2), 0.0)

    def test_below_500_is_negative(self) -> None:
        self.assertLess(compute_wins_delta(2, 10), 0.0)
        self.assertLess(compute_wins_delta(0, 2), 0.0)

    def test_perfect_record_value(self) -> None:
        # won == games: (won - games/2)/(games/2) == 1 ⇒ 1 * 0.25 * 1 == 0.25.
        self.assertAlmostEqual(compute_wins_delta(10, 10), 0.25, places=9)

    def test_winless_record_value(self) -> None:
        # won == 0: (0 - games/2)/(games/2) == -1 ⇒ 1 * 0.25 * -1 == -0.25.
        self.assertAlmostEqual(compute_wins_delta(0, 10), -0.25, places=9)

    def test_formula_matches_spec_for_arbitrary_record(self) -> None:
        # 8 of 10: (8-5)/5 == 0.6 ⇒ 0.25 * 0.6 == 0.15.
        self.assertAlmostEqual(compute_wins_delta(8, 10), 0.15, places=9)

    def test_zero_games_returns_zero(self) -> None:
        self.assertEqual(compute_wins_delta(0, 0), 0.0)
        # Even a non-zero ``won`` with games==0 is neutral (defensive).
        self.assertEqual(compute_wins_delta(3, 0), 0.0)

    def test_returns_float(self) -> None:
        self.assertIsInstance(compute_wins_delta(5, 10), float)


# ===========================================================================
# §1.4 — TestComputePlayoffsDelta
# ===========================================================================


class TestComputePlayoffsDelta(SimpleTestCase):
    """The 4-branch playoff mapping + defensive edges."""

    def test_champion_returns_playoff_title(self) -> None:
        self.assertEqual(
            compute_playoffs_delta("champion", rounds_won=3, num_rounds=3),
            PLAYOFF_TITLE,
        )
        # rounds_won / num_rounds are irrelevant on the champion branch.
        self.assertEqual(
            compute_playoffs_delta("champion", rounds_won=0, num_rounds=0),
            PLAYOFF_TITLE,
        )

    def test_missed_returns_playoff_miss(self) -> None:
        self.assertEqual(
            compute_playoffs_delta("missed", rounds_won=0, num_rounds=4),
            PLAYOFF_MISS,
        )

    def test_none_returns_zero(self) -> None:
        self.assertEqual(
            compute_playoffs_delta("none", rounds_won=0, num_rounds=0), 0.0
        )

    def test_seeded_scaling_formula(self) -> None:
        # (0.16 / num_rounds) * rounds_won.
        # 4-round bracket, won 2 rounds ⇒ (0.16/4) * 2 == 0.08.
        self.assertAlmostEqual(
            compute_playoffs_delta("seeded", rounds_won=2, num_rounds=4),
            0.08,
            places=9,
        )
        # 3-round bracket, won 1 round ⇒ (0.16/3) * 1.
        self.assertAlmostEqual(
            compute_playoffs_delta("seeded", rounds_won=1, num_rounds=3),
            0.16 / 3,
            places=9,
        )

    def test_seeded_zero_rounds_won_is_zero(self) -> None:
        self.assertEqual(
            compute_playoffs_delta("seeded", rounds_won=0, num_rounds=4), 0.0
        )

    def test_seeded_num_rounds_zero_returns_zero_no_div(self) -> None:
        # Defensive: never divides by zero.
        self.assertEqual(
            compute_playoffs_delta("seeded", rounds_won=2, num_rounds=0), 0.0
        )

    def test_unknown_string_returns_zero(self) -> None:
        self.assertEqual(
            compute_playoffs_delta("bogus", rounds_won=2, num_rounds=4), 0.0
        )
        self.assertEqual(compute_playoffs_delta("", rounds_won=0, num_rounds=0), 0.0)

    def test_returns_float(self) -> None:
        self.assertIsInstance(
            compute_playoffs_delta("seeded", rounds_won=1, num_rounds=4), float
        )


# ===========================================================================
# §1.5 — TestCapCumulative
# ===========================================================================


class TestCapCumulative(SimpleTestCase):
    """``min(prev_cumulative + delta, MOOD_FACTOR_CAP)`` — upside cap only."""

    def test_additive_below_cap(self) -> None:
        self.assertAlmostEqual(cap_cumulative(0.2, 0.1), 0.3, places=9)
        self.assertAlmostEqual(cap_cumulative(-0.5, 0.2), -0.3, places=9)

    def test_caps_at_plus_one(self) -> None:
        self.assertEqual(cap_cumulative(0.9, 0.5), MOOD_FACTOR_CAP)
        self.assertEqual(cap_cumulative(1.0, 0.1), MOOD_FACTOR_CAP)

    def test_exactly_at_cap_stays(self) -> None:
        self.assertEqual(cap_cumulative(0.8, 0.2), 1.0)

    def test_no_negative_floor(self) -> None:
        # You can sink arbitrarily low — no floor.
        self.assertAlmostEqual(cap_cumulative(-0.9, -0.5), -1.4, places=9)
        self.assertAlmostEqual(cap_cumulative(-2.0, -1.0), -3.0, places=9)

    def test_negative_delta_below_cap_is_additive(self) -> None:
        self.assertAlmostEqual(cap_cumulative(0.5, -0.3), 0.2, places=9)

    def test_returns_float(self) -> None:
        self.assertIsInstance(cap_cumulative(0.1, 0.1), float)


# ===========================================================================
# §1.6 — TestDecideVerdict
# ===========================================================================


def _totals(wins=0.0, playoffs=0.0, money=0.0) -> MoodTotals:
    return MoodTotals(wins=wins, playoffs=playoffs, money=money)


def _deltas(wins=0.0, playoffs=0.0, money=0.0) -> MoodDeltas:
    return MoodDeltas(wins=wins, playoffs=playoffs, money=money)


class TestDecideVerdict(SimpleTestCase):
    """The grace gate, the fire threshold, the two hot-seat projection levels,
    and the level-1-wins ordering."""

    def test_grace_suppresses_firing_at_or_below_grace(self) -> None:
        # total well below -1, but inside the grace period ⇒ always retained.
        totals = _totals(wins=-1.0, playoffs=-1.0)  # total -2.0
        deltas = _deltas(wins=-0.5, playoffs=-0.5)
        for tenure in (1, GRACE_PERIOD_SEASONS):  # 1 and 2
            v = decide_verdict(totals, deltas, seasons_in_tenure=tenure)
            self.assertEqual(v, Verdict("retained", 0), f"tenure={tenure}")

    def test_past_grace_total_at_or_below_minus_one_fires(self) -> None:
        # seasons_in_tenure strictly > 2 ⇒ past grace.
        totals = _totals(wins=-0.6, playoffs=-0.6)  # total -1.2 <= -1
        deltas = _deltas()
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("fired", 0))

    def test_fired_compare_is_less_than_or_equal(self) -> None:
        # total exactly -1.0 ⇒ fired (the firing compare is <=).
        totals = _totals(wins=-1.0)  # total -1.0
        deltas = _deltas()
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("fired", 0))

    def test_past_grace_above_minus_one_with_safe_delta_retained(self) -> None:
        totals = _totals(wins=0.5)  # total +0.5
        deltas = _deltas(wins=0.1)  # projections stay above -1
        v = decide_verdict(totals, deltas, seasons_in_tenure=5)
        self.assertEqual(v, Verdict("retained", 0))

    def test_hot_seat_level_1_when_total_plus_delta_below_minus_one(self) -> None:
        # total above -1 (not fired), but total + delta < -1 ⇒ level 1.
        totals = _totals(wins=-0.9)  # total -0.9 (> -1 so not fired)
        deltas = _deltas(wins=-0.3)  # -0.9 + -0.3 == -1.2 < -1
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("hot_seat", 1))

    def test_hot_seat_level_2_when_only_two_delta_below_minus_one(self) -> None:
        # total + delta >= -1 (no level 1), but total + 2*delta < -1 ⇒ level 2.
        totals = _totals(wins=-0.8)  # total -0.8
        deltas = _deltas(wins=-0.15)  # -0.8 + -0.15 == -0.95 (>= -1, no L1);
        # -0.8 + 2*-0.15 == -1.1 (< -1 ⇒ L2)
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("hot_seat", 2))

    def test_level_1_wins_when_both_projections_hold(self) -> None:
        # A delta that trips BOTH `total + delta < -1` AND
        # `total + 2*delta < -1` ⇒ the stricter level-1 projection wins.
        totals = _totals(wins=-0.9)  # total -0.9
        deltas = _deltas(wins=-0.4)  # -0.9 + -0.4 == -1.3 < -1 (L1);
        # -0.9 + -0.8 == -1.7 < -1 (L2 too) — L1 must win.
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("hot_seat", 1))

    def test_retained_past_grace_when_no_projection_trips(self) -> None:
        totals = _totals(wins=-0.5)  # total -0.5 (> -1, not fired)
        deltas = _deltas(wins=-0.1)  # -0.5 + -0.1 == -0.6 (>= -1);
        # -0.5 + -0.2 == -0.7 (>= -1) ⇒ retained
        v = decide_verdict(totals, deltas, seasons_in_tenure=4)
        self.assertEqual(v, Verdict("retained", 0))

    def test_grace_boundary_is_strictly_past(self) -> None:
        # past_grace == seasons_in_tenure > GRACE_PERIOD_SEASONS.
        # At exactly GRACE_PERIOD_SEASONS + 1 the gate opens.
        totals = _totals(wins=-1.5)
        deltas = _deltas()
        # tenure 2 (== grace) ⇒ retained.
        self.assertEqual(
            decide_verdict(totals, deltas, seasons_in_tenure=GRACE_PERIOD_SEASONS),
            Verdict("retained", 0),
        )
        # tenure 3 (> grace) ⇒ fired.
        self.assertEqual(
            decide_verdict(totals, deltas, seasons_in_tenure=GRACE_PERIOD_SEASONS + 1),
            Verdict("fired", 0),
        )

    def test_seasons_in_tenure_is_keyword_only(self) -> None:
        totals = _totals()
        deltas = _deltas()
        with self.assertRaises(TypeError):
            decide_verdict(totals, deltas, 3)  # type: ignore[misc]

    def test_total_and_delta_sum_all_three_factors(self) -> None:
        # The verdict math sums wins+playoffs+money for both total and delta.
        # money is dormant 0.0, but playoffs must contribute.
        totals = _totals(wins=-0.6, playoffs=-0.6)  # total -1.2 <= -1
        deltas = _deltas()
        v = decide_verdict(totals, deltas, seasons_in_tenure=3)
        self.assertEqual(v, Verdict("fired", 0))

    def test_retained_and_fired_have_hot_seat_level_zero(self) -> None:
        retained = decide_verdict(_totals(wins=0.5), _deltas(), seasons_in_tenure=5)
        self.assertEqual(retained.hot_seat_level, 0)
        fired = decide_verdict(_totals(wins=-1.5), _deltas(), seasons_in_tenure=5)
        self.assertEqual(fired.hot_seat_level, 0)

    def test_returns_verdict_instance(self) -> None:
        v = decide_verdict(_totals(), _deltas(), seasons_in_tenure=1)
        self.assertIsInstance(v, Verdict)


# ===========================================================================
# §6.1 — TestNoDjangoImportsLeaked
# ===========================================================================


class TestNoDjangoImportsLeaked(SimpleTestCase):
    """Importing ``matches.owner_mood`` (and exercising its 4 pure functions)
    in a fresh subprocess must not pull in ``django.*`` (nor ``matches.models``)
    — the frozen allowlist is ``dataclasses`` / ``typing`` / ``collections``.
    Mirrors the ``matches.development`` /
    ``matches.season_awards`` ``TestNoDjangoImportsLeaked`` precedent.
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
            from matches.owner_mood import (
                MoodDeltas,
                MoodTotals,
                Verdict,
                cap_cumulative,
                compute_playoffs_delta,
                compute_wins_delta,
                decide_verdict,
            )

            compute_wins_delta(5, 10)
            compute_playoffs_delta("seeded", 2, 4)
            cap_cumulative(0.2, 0.1)
            decide_verdict(
                MoodTotals(0.0, 0.0, 0.0),
                MoodDeltas(0.0, 0.0, 0.0),
                seasons_in_tenure=1,
            )

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
