"""LG-01z-o -- pure-unit tests for ``matches.season_player_stats``.

Aggregation / sum-vs-average split / sorting / forgiving coercers, plus the
``TestNoDjangoImportsLeaked`` purity seam. The HTML Player Stats screen's
view tests went with the screen (ANG-08); the API serves the same rows
(``GET /api/leagues/<id>/player-stats/``).
"""

from __future__ import annotations

from django.test import TestCase

from matches.season_player_stats import (
    AVERAGED_KEYS,
    DERIVED_KEYS,
    STAT_KEYS,
    SUMMED_KEYS,
    aggregate_player_stats,
    coerce_dir,
    coerce_sort,
    sort_player_stats,
)
from laserforce_simulator.testing import run_project_python

# ===========================================================================
# Pure-unit -- aggregation
# ===========================================================================


def _round_dict(pid, name="P", tid=1, tname="T", role="scout", **stats) -> dict:
    """Build one round-dict with all 12 STAT_KEYS defaulted to 0."""
    base = {k: 0 for k in STAT_KEYS}
    base.update(stats)
    return {
        "player_id": pid,
        "player_name": name,
        "team_id": tid,
        "team_name": tname,
        "role": role,
        **base,
    }


class TestAggregateEmpty(TestCase):
    def test_empty_input_returns_empty_list(self) -> None:
        self.assertEqual(aggregate_player_stats([]), [])


class TestAggregateSumVsAverage(TestCase):
    def test_count_keys_are_summed(self) -> None:
        rounds = [
            _round_dict(1, points_scored=100, tags_made=5),
            _round_dict(1, points_scored=200, tags_made=7),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0].stats["points_scored"], 300)
        self.assertEqual(rows[0].stats["tags_made"], 12)
        self.assertEqual(rows[0].games, 2)

    def test_mvp_and_accuracy_are_averaged(self) -> None:
        rounds = [
            _round_dict(1, mvp=10.0, accuracy=80.0),
            _round_dict(1, mvp=30.0, accuracy=100.0),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["mvp"], 20.0)
        self.assertAlmostEqual(rows[0].stats["accuracy"], 90.0)

    def test_summed_and_averaged_key_sets_partition_stat_keys(self) -> None:
        # The two groups together cover exactly the 12 STAT_KEYS, disjoint.
        self.assertEqual(set(SUMMED_KEYS) | set(AVERAGED_KEYS), set(STAT_KEYS))
        self.assertEqual(set(SUMMED_KEYS) & set(AVERAGED_KEYS), set())

    def test_every_stat_key_present_on_row(self) -> None:
        rows = aggregate_player_stats([_round_dict(1)])
        for k in STAT_KEYS:
            self.assertIn(k, rows[0].stats)


class TestAggregateGrouping(TestCase):
    def test_distinct_players_each_get_a_row(self) -> None:
        rows = aggregate_player_stats(
            [_round_dict(1, name="Amy"), _round_dict(2, name="Bob")]
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual({r.player_id for r in rows}, {1, 2})

    def test_last_seen_identity_wins(self) -> None:
        rows = aggregate_player_stats(
            [
                _round_dict(1, name="Old", tname="OldTeam", role="scout"),
                _round_dict(1, name="New", tname="NewTeam", role="medic"),
            ]
        )
        self.assertEqual(rows[0].player_name, "New")
        self.assertEqual(rows[0].team_name, "NewTeam")
        self.assertEqual(rows[0].role, "medic")


class TestDerivedStats(TestCase):
    def test_derived_keys_are_not_in_stat_keys(self) -> None:
        # Derived keys are computed separately; they must not pollute the
        # 12-key SUMMED + AVERAGED partition.
        self.assertEqual(set(DERIVED_KEYS) & set(STAT_KEYS), set())

    def test_tag_ratio_is_sum_over_sum(self) -> None:
        # 10 tags / 0 tagged then 0 tags / 4 tagged => 10 / max(4,1) = 2.5,
        # NOT the mean of per-round ratios (which would be (inf + 0)/2).
        rounds = [
            _round_dict(1, tags_made=10, times_tagged=0),
            _round_dict(1, tags_made=0, times_tagged=4),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["tag_ratio"], 2.5)

    def test_tag_ratio_zero_tagged_clamps_denominator(self) -> None:
        rows = aggregate_player_stats([_round_dict(1, tags_made=7, times_tagged=0)])
        self.assertAlmostEqual(rows[0].stats["tag_ratio"], 7.0)

    def test_survival_is_mean_of_per_round_seconds(self) -> None:
        rounds = [
            _round_dict(1, survival_seconds=900.0),
            _round_dict(1, survival_seconds=300.0),
        ]
        rows = aggregate_player_stats(rounds)
        self.assertAlmostEqual(rows[0].stats["survival"], 600.0)

    def test_survival_defaults_to_zero_when_absent(self) -> None:
        # Round dicts without survival_seconds (defensive) contribute 0.
        rows = aggregate_player_stats([_round_dict(1)])
        self.assertAlmostEqual(rows[0].stats["survival"], 0.0)


# ===========================================================================
# Pure-unit -- coercers + sorting
# ===========================================================================


class TestCoercers(TestCase):
    def test_coerce_sort_accepts_every_stat_key(self) -> None:
        for k in STAT_KEYS:
            self.assertEqual(coerce_sort(k), k)

    def test_coerce_sort_accepts_name_team_games(self) -> None:
        self.assertEqual(coerce_sort("name"), "name")
        self.assertEqual(coerce_sort("team"), "team")
        self.assertEqual(coerce_sort("games"), "games")

    def test_coerce_sort_accepts_derived_keys(self) -> None:
        for k in DERIVED_KEYS:
            self.assertEqual(coerce_sort(k), k)

    def test_coerce_sort_falls_back(self) -> None:
        self.assertEqual(coerce_sort("BOGUS"), "points_scored")
        self.assertEqual(coerce_sort(None), "points_scored")
        self.assertEqual(coerce_sort(""), "points_scored")

    def test_coerce_dir_accepts_asc_desc(self) -> None:
        self.assertEqual(coerce_dir("asc"), "asc")
        self.assertEqual(coerce_dir("desc"), "desc")

    def test_coerce_dir_falls_back_default_desc(self) -> None:
        self.assertEqual(coerce_dir("SIDEWAYS"), "desc")
        self.assertEqual(coerce_dir(None), "desc")
        self.assertEqual(coerce_dir("ASC"), "desc")  # case-sensitive


class TestSorting(TestCase):
    def _rows(self):
        return aggregate_player_stats(
            [
                _round_dict(1, name="Zed", tname="Bravo", points_scored=50, mvp=10.0),
                _round_dict(2, name="Amy", tname="Alpha", points_scored=90, mvp=30.0),
                _round_dict(3, name="Bob", tname="Alpha", points_scored=70, mvp=20.0),
            ]
        )

    def test_sort_by_points_desc(self) -> None:
        rows = sort_player_stats(self._rows(), "points_scored", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_points_asc(self) -> None:
        rows = sort_player_stats(self._rows(), "points_scored", "asc")
        self.assertEqual([r.player_name for r in rows], ["Zed", "Bob", "Amy"])

    def test_sort_by_mvp_desc_averaged_key(self) -> None:
        rows = sort_player_stats(self._rows(), "mvp", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_name_asc(self) -> None:
        rows = sort_player_stats(self._rows(), "name", "asc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_sort_by_team_asc_name_tiebreak(self) -> None:
        rows = sort_player_stats(self._rows(), "team", "asc")
        # Alpha team players first (Amy then Bob), then Bravo (Zed).
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])

    def test_unknown_sort_falls_back_to_points(self) -> None:
        rows = sort_player_stats(self._rows(), "nonsense", "desc")
        self.assertEqual([r.player_name for r in rows], ["Amy", "Bob", "Zed"])


# ===========================================================================
# Pure-unit -- purity seam
# ===========================================================================


class TestNoDjangoImportsLeaked(TestCase):
    def test_no_django_imported_by_pure_module(self) -> None:
        code = (
            "import sys; import matches.season_player_stats; "
            "leaked = [m for m in sys.modules if m == 'django' "
            "or m.startswith('django.')]; "
            "print('LEAKED' if leaked else 'CLEAN')"
        )
        result = run_project_python(code)
        self.assertIn("CLEAN", result.stdout, msg=result.stderr)
