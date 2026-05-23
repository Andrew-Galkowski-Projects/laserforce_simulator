"""HX-02 — Pure-unit tests for ``teams/role_benchmarks.py``.

No DB, no Django imports in the assertion path. The seam contract is
locked at ``.claude/worktrees/hx-02-seam-contract.md``. Mirrors the
HX-01 ``test_career_stats.py`` precedent — `_round_dict` keyword helper
for readable cases.

Round-dict shape (the 18-key view ↔ pure-module seam) — the reducer
also reads ``player_id`` (added to the dict by the view layer):

    {
        # HX-01 10 keys
        "role", "points_scored", "tags_made", "times_tagged",
        "shots_missed", "final_special", "specials_used",
        "was_eliminated_at", "date_played", "game_round_id",
        # HX-02 6 raw counters
        "final_lives", "resupplies_given", "missiles_landed",
        "follow_up_shots", "reaction_shots", "combo_resupply_count",
        # HX-02 2 pre-computed view-side
        "mvp", "accuracy_pct",
        # carrier
        "player_id",
    }
"""

from __future__ import annotations

import unittest

from teams.role_benchmarks import (
    MVP_DERIVED_STATS,
    RATIO_STATS,
    ROLES,
    STAT_KEYS,
    apply_threshold,
    build_role_populations,
    compute_role_benchmarks,
    percentile_for,
    player_position,
    summarize_population,
)

# ---------------------------------------------------------------------------
# Pure-unit fixture helper
# ---------------------------------------------------------------------------


def _round_dict(
    *,
    player_id: int = 1,
    role: str = "scout",
    points_scored: int = 0,
    tags_made: int = 0,
    times_tagged: int = 0,
    shots_missed: int = 0,
    final_special: int = 0,
    specials_used: int = 0,
    was_eliminated_at: int = 1801,
    date_played: object = "2026-05-22T12:00:00",
    game_round_id: int = 1,
    final_lives: int = 0,
    resupplies_given: int = 0,
    missiles_landed: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    combo_resupply_count: int = 0,
    mvp: float = 0.0,
    accuracy_pct: float = 0.0,
) -> dict:
    """Build one 18-key + ``player_id`` round-dict with every key populated."""
    return {
        "player_id": player_id,
        "role": role,
        "points_scored": points_scored,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "shots_missed": shots_missed,
        "final_special": final_special,
        "specials_used": specials_used,
        "was_eliminated_at": was_eliminated_at,
        "date_played": date_played,
        "game_round_id": game_round_id,
        "final_lives": final_lives,
        "resupplies_given": resupplies_given,
        "missiles_landed": missiles_landed,
        "follow_up_shots": follow_up_shots,
        "reaction_shots": reaction_shots,
        "combo_resupply_count": combo_resupply_count,
        "mvp": mvp,
        "accuracy_pct": accuracy_pct,
    }


# ---------------------------------------------------------------------------
# §A — Frozen constants
# ---------------------------------------------------------------------------


class TestStatKeys(unittest.TestCase):
    """Pin the locked constants on the pure module."""

    def test_stat_keys_exact_tuple(self) -> None:
        """STAT_KEYS is the locked 12-tuple in the locked order."""
        self.assertEqual(
            STAT_KEYS,
            (
                "points_scored",
                "mvp",
                "tags_made",
                "times_tagged",
                "accuracy",
                "final_lives",
                "resupplies_given",
                "missiles_landed",
                "specials_used",
                "follow_up_shots",
                "reaction_shots",
                "combo_resupply_count",
            ),
        )
        self.assertEqual(len(STAT_KEYS), 12)

    def test_ratio_stats_is_just_accuracy(self) -> None:
        self.assertEqual(RATIO_STATS, frozenset({"accuracy"}))

    def test_mvp_derived_stats_is_just_mvp(self) -> None:
        self.assertEqual(MVP_DERIVED_STATS, frozenset({"mvp"}))

    def test_roles_locked_order(self) -> None:
        self.assertEqual(ROLES, ("commander", "heavy", "scout", "medic", "ammo"))


# ---------------------------------------------------------------------------
# §B — build_role_populations
# ---------------------------------------------------------------------------


class TestBuildRolePopulations(unittest.TestCase):
    """Single-pass reducer correctness across roles, stats, and players."""

    def test_full_cartesian_always_present(self) -> None:
        """Output covers ROLES × STAT_KEYS (60 keys), empty populations are []."""
        out = build_role_populations([])
        self.assertEqual(len(out), len(ROLES) * len(STAT_KEYS))
        for role in ROLES:
            for stat in STAT_KEYS:
                self.assertIn((role, stat), out)
                self.assertEqual(out[(role, stat)], [])

    def test_two_players_same_role_two_rounds_each(self) -> None:
        """Two players × two rounds same role → exactly 2 entries per (role, stat).

        Player 1 commander rounds: points 100, 200 → mean 150.0
        Player 2 commander rounds: points 400, 600 → mean 500.0
        """
        rounds = [
            _round_dict(player_id=1, role="commander", points_scored=100),
            _round_dict(player_id=1, role="commander", points_scored=200),
            _round_dict(player_id=2, role="commander", points_scored=400),
            _round_dict(player_id=2, role="commander", points_scored=600),
        ]
        out = build_role_populations(rounds)
        points_samples = out[("commander", "points_scored")]
        self.assertEqual(len(points_samples), 2)
        as_dict = dict(points_samples)
        self.assertAlmostEqual(as_dict[1], 150.0)
        self.assertAlmostEqual(as_dict[2], 500.0)
        # All other roles' points_scored remain empty.
        for role in ("heavy", "scout", "medic", "ammo"):
            self.assertEqual(out[(role, "points_scored")], [])

    def test_cross_role_player_contributes_to_each_role(self) -> None:
        """Same player with rounds as heavy + scout contributes to both."""
        rounds = [
            _round_dict(player_id=7, role="heavy", points_scored=1000),
            _round_dict(player_id=7, role="scout", points_scored=300),
        ]
        out = build_role_populations(rounds)
        self.assertEqual(out[("heavy", "points_scored")], [(7, 1000.0)])
        self.assertEqual(out[("scout", "points_scored")], [(7, 300.0)])
        # Other roles unaffected.
        for role in ("commander", "medic", "ammo"):
            self.assertEqual(out[(role, "points_scored")], [])

    def test_accuracy_aggregates_sum_over_sum_not_mean_of_ratios(self) -> None:
        """accuracy = sum(tags) / (sum(tags) + sum(misses)) * 100 — NOT mean-of-ratios.

        Round A: tags=10, misses=0 → per-round 100.0
        Round B: tags=0,  misses=100 → per-round 0.0

        Correct sum/sum: 10 / 110 * 100 ≈ 9.0909…
        Wrong mean-of-ratios: (100 + 0) / 2 = 50.0
        """
        rounds = [
            _round_dict(player_id=1, role="ammo", tags_made=10, shots_missed=0),
            _round_dict(player_id=1, role="ammo", tags_made=0, shots_missed=100),
        ]
        out = build_role_populations(rounds)
        samples = out[("ammo", "accuracy")]
        self.assertEqual(len(samples), 1)
        _pid, value = samples[0]
        # 10 / 110 * 100 ≈ 9.09
        self.assertAlmostEqual(value, 10 / 110 * 100.0, places=4)
        # And explicitly NOT 50.0.
        self.assertNotAlmostEqual(value, 50.0, places=2)

    def test_counter_stats_use_per_round_mean(self) -> None:
        """Non-ratio counters aggregate as per-round mean (sum / count)."""
        rounds = [
            _round_dict(player_id=3, role="medic", resupplies_given=10),
            _round_dict(player_id=3, role="medic", resupplies_given=20),
            _round_dict(player_id=3, role="medic", resupplies_given=30),
        ]
        out = build_role_populations(rounds)
        samples = out[("medic", "resupplies_given")]
        self.assertEqual(samples, [(3, 20.0)])

    def test_mvp_aggregates_as_per_round_mean(self) -> None:
        """mvp is in MVP_DERIVED_STATS but is treated like any counter."""
        rounds = [
            _round_dict(player_id=4, role="scout", mvp=2.0),
            _round_dict(player_id=4, role="scout", mvp=4.0),
        ]
        out = build_role_populations(rounds)
        self.assertEqual(out[("scout", "mvp")], [(4, 3.0)])

    def test_empty_input_yields_sixty_empty_buckets(self) -> None:
        """Empty input → 60 keys each mapped to []."""
        out = build_role_populations([])
        self.assertEqual(sum(len(v) for v in out.values()), 0)
        self.assertEqual(set(out.keys()), {(r, s) for r in ROLES for s in STAT_KEYS})


# ---------------------------------------------------------------------------
# §C — apply_threshold
# ---------------------------------------------------------------------------


class TestApplyThreshold(unittest.TestCase):
    """Threshold filter: keeps only entries with enough rounds in role."""

    def test_filters_below_threshold(self) -> None:
        samples = [(1, 100.0), (2, 200.0), (3, 300.0)]
        round_counts = {1: 1, 2: 5, 3: 10}
        out = apply_threshold(samples, round_counts, min_rounds=5)
        # Player 1 has only 1 round → drops; 2/3 qualify.
        self.assertEqual(sorted(out), [(2, 200.0), (3, 300.0)])

    def test_absent_player_id_treated_as_zero(self) -> None:
        """A player_id missing from the round-counts map is treated as 0."""
        samples = [(1, 100.0), (42, 500.0)]
        round_counts = {1: 10}
        out = apply_threshold(samples, round_counts, min_rounds=5)
        # 42 missing → treated as 0 → filtered.
        self.assertEqual(out, [(1, 100.0)])

    def test_min_rounds_zero_keeps_everyone(self) -> None:
        samples = [(1, 1.0), (2, 2.0), (3, 3.0)]
        out = apply_threshold(samples, {}, min_rounds=0)
        self.assertEqual(out, samples)


# ---------------------------------------------------------------------------
# §D — summarize_population
# ---------------------------------------------------------------------------


class TestSummarizePopulation(unittest.TestCase):
    """Mean / median / p25 / p75 / p90 / n with nearest-rank percentile."""

    def test_empty_population_all_none_and_n_zero(self) -> None:
        out = summarize_population([])
        self.assertEqual(
            out,
            {
                "mean": None,
                "median": None,
                "p25": None,
                "p75": None,
                "p90": None,
                "n": 0,
            },
        )

    def test_single_sample_all_metrics_equal_that_value(self) -> None:
        out = summarize_population([(1, 7.5)])
        self.assertEqual(out["n"], 1)
        self.assertAlmostEqual(out["mean"], 7.5)
        self.assertAlmostEqual(out["median"], 7.5)
        self.assertAlmostEqual(out["p25"], 7.5)
        self.assertAlmostEqual(out["p75"], 7.5)
        self.assertAlmostEqual(out["p90"], 7.5)

    def test_four_samples_mean_median_and_percentiles(self) -> None:
        """values=[1,2,3,4]; nearest-rank idx = ceil(p/100 * n) - 1, clamped.

        n=4: p25 idx=0→1, p50 idx=1→2 (median statistics.median → 2.5),
        p75 idx=2→3, p90 idx=3→4.
        """
        samples = [(1, 1.0), (2, 2.0), (3, 3.0), (4, 4.0)]
        out = summarize_population(samples)
        self.assertEqual(out["n"], 4)
        self.assertAlmostEqual(out["mean"], 2.5)
        self.assertAlmostEqual(out["median"], 2.5)
        self.assertAlmostEqual(out["p25"], 1.0)
        self.assertAlmostEqual(out["p75"], 3.0)
        self.assertAlmostEqual(out["p90"], 4.0)

    def test_odd_n_median_is_middle_value(self) -> None:
        """n=5 odd; median = middle of sorted values."""
        samples = [(i, float(v)) for i, v in enumerate([5, 1, 9, 3, 7])]
        out = summarize_population(samples)
        self.assertEqual(out["n"], 5)
        self.assertAlmostEqual(out["median"], 5.0)
        # mean = 25 / 5 = 5.0
        self.assertAlmostEqual(out["mean"], 5.0)


# ---------------------------------------------------------------------------
# §E — percentile_for
# ---------------------------------------------------------------------------


class TestPercentileFor(unittest.TestCase):
    """Nearest-rank percentile with the subject INCLUDED in sorted_values."""

    def test_value_in_middle_of_four(self) -> None:
        """pop=[5,10,15,20], subject=15 → rank=3 → 3*100//4 = 75."""
        self.assertEqual(percentile_for(15.0, [5.0, 10.0, 15.0, 20.0]), 75)

    def test_subject_at_maximum_is_one_hundred(self) -> None:
        self.assertEqual(percentile_for(20.0, [5.0, 10.0, 15.0, 20.0]), 100)

    def test_subject_at_minimum_returns_floor_clamp(self) -> None:
        """pop=[5,10,15,20], subject=5 → bisect_left=0, rank=1, 1*100//4 = 25."""
        self.assertEqual(percentile_for(5.0, [5.0, 10.0, 15.0, 20.0]), 25)

    def test_singleton_population_is_one_hundred(self) -> None:
        """Single-element pop: subject == that value → rank=1, 1*100//1 = 100."""
        self.assertEqual(percentile_for(42.0, [42.0]), 100)

    def test_subject_below_all_values(self) -> None:
        """subject below min: bisect_left=0, rank=1, 1*100//4 = 25 (clamp floor)."""
        self.assertEqual(percentile_for(0.0, [5.0, 10.0, 15.0, 20.0]), 25)

    def test_empty_population_returns_zero(self) -> None:
        self.assertEqual(percentile_for(10.0, []), 0)


# ---------------------------------------------------------------------------
# §F — compute_role_benchmarks
# ---------------------------------------------------------------------------


class TestComputeRoleBenchmarks(unittest.TestCase):
    """Orchestrator — wires apply_threshold + summarize for every cell."""

    def _empty_samples(self) -> dict:
        return {(role, stat): [] for role in ROLES for stat in STAT_KEYS}

    def test_output_has_exactly_sixty_entries(self) -> None:
        out = compute_role_benchmarks(self._empty_samples(), {}, min_rounds=0)
        self.assertEqual(len(out), 60)
        for role in ROLES:
            for stat in STAT_KEYS:
                self.assertIn((role, stat), out)
                self.assertEqual(out[(role, stat)]["n"], 0)

    def test_threshold_filters_per_role(self) -> None:
        """Player below per-role threshold is dropped from that cell."""
        samples = self._empty_samples()
        samples[("commander", "points_scored")] = [(1, 100.0), (2, 200.0)]
        # Player 1 has 1 round in commander; player 2 has 10.
        thresholds = {"commander": {1: 1, 2: 10}}
        out = compute_role_benchmarks(samples, thresholds, min_rounds=5)
        self.assertEqual(out[("commander", "points_scored")]["n"], 1)
        self.assertAlmostEqual(out[("commander", "points_scored")]["mean"], 200.0)

    def test_missing_key_in_samples_raises_keyerror(self) -> None:
        # Drop one key from the cartesian.
        partial = self._empty_samples()
        del partial[("commander", "points_scored")]
        with self.assertRaises(KeyError):
            compute_role_benchmarks(partial, {}, min_rounds=0)


# ---------------------------------------------------------------------------
# §G — player_position
# ---------------------------------------------------------------------------


class TestPlayerPosition(unittest.TestCase):
    """Per-player position dict — including the subject in the population."""

    def test_empty_population_returns_all_none(self) -> None:
        out = player_position(
            [], subject_player_id=1, subject_value=5.0, min_rounds_qualified=False
        )
        self.assertEqual(
            out,
            {
                "benchmark_mean": None,
                "benchmark_median": None,
                "delta_mean": None,
                "delta_median": None,
                "percentile": None,
                "qualified": False,
                "n": 0,
            },
        )

    def test_unqualified_subject_has_mean_median_but_no_deltas(self) -> None:
        samples = [(1, 10.0), (2, 20.0), (3, 30.0)]
        out = player_position(
            samples, subject_player_id=1, subject_value=15.0, min_rounds_qualified=False
        )
        self.assertAlmostEqual(out["benchmark_mean"], 20.0)
        self.assertAlmostEqual(out["benchmark_median"], 20.0)
        self.assertIsNone(out["delta_mean"])
        self.assertIsNone(out["delta_median"])
        self.assertIsNone(out["percentile"])
        self.assertFalse(out["qualified"])
        self.assertEqual(out["n"], 3)

    def test_qualified_subject_all_fields_populated(self) -> None:
        """qualified → all fields; delta = subject_value - benchmark."""
        samples = [(1, 10.0), (2, 20.0), (3, 30.0)]
        out = player_position(
            samples, subject_player_id=2, subject_value=20.0, min_rounds_qualified=True
        )
        self.assertAlmostEqual(out["benchmark_mean"], 20.0)
        self.assertAlmostEqual(out["benchmark_median"], 20.0)
        self.assertAlmostEqual(out["delta_mean"], 0.0)
        self.assertAlmostEqual(out["delta_median"], 0.0)
        self.assertTrue(out["qualified"])
        self.assertEqual(out["n"], 3)
        # subject sits at middle → rank=2 → 2*100//3 = 66.
        self.assertEqual(out["percentile"], 66)

    def test_inclusion_policy_subject_counts_in_mean_and_percentile(self) -> None:
        """3-player pop [5,10,15], subject=15 → percentile=100, mean=10.0.

        Pins the seam contract: percentile AND mean/median computed over
        the FULL population INCLUDING the subject. The subject is the
        population maximum (15) so its percentile is 100, and the mean
        (5+10+15)/3 = 10.0 includes the subject's own 15.
        """
        samples = [(1, 5.0), (2, 10.0), (3, 15.0)]
        out = player_position(
            samples, subject_player_id=3, subject_value=15.0, min_rounds_qualified=True
        )
        self.assertAlmostEqual(out["benchmark_mean"], 10.0)
        self.assertEqual(out["percentile"], 100)
        self.assertAlmostEqual(out["delta_mean"], 5.0)  # 15 - 10
        self.assertEqual(out["n"], 3)


# ---------------------------------------------------------------------------
# §H — Defensive: no Django imports leaked into the pure module
# ---------------------------------------------------------------------------


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Mirrors the HX-01 / RES-04 / RV-03 precedent.

    Runs LAST (uppercase class name + module-name ordering does not
    guarantee it, but the assertions are robust against pre-loaded modules
    because we measure the DELTA introduced by importing the pure module
    in a fresh subprocess, mirroring HX-01's clean-import pattern).
    """

    def test_pure_module_does_not_expose_django(self) -> None:
        import teams.role_benchmarks as m

        self.assertNotIn("django", dir(m))
        self.assertNotIn("models", dir(m))

    def test_clean_import_in_subprocess(self) -> None:
        """Importing the pure module in a fresh interpreter must not pull
        in django.* or matches.models.

        Runs out-of-process so the result is robust against the
        already-Django-loaded test runner. We add the project's
        manage.py directory to ``sys.path`` inside the subprocess so
        ``teams.role_benchmarks`` resolves without Django settings.
        """
        import os
        import pathlib
        import subprocess
        import sys
        import textwrap

        # `teams/` lives next to `manage.py`. Walk up from this test file
        # to find the directory containing manage.py.
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
            import teams.role_benchmarks  # noqa: F401

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


if __name__ == "__main__":
    unittest.main()
