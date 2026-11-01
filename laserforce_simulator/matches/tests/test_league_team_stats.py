"""LG-01z-p -- pure-unit tests for ``matches/team_stats_logic.py``.

``TestAggregateTeamStats*`` / ``TestSortTeamStats`` / ``TestCoerce`` /
``TestNoDjangoImportsLeaked`` -- no DB, no Django in the assertion path. The
HTML Team Stats screen's view tests went with the screen (ANG-08); the API
serves the same rows (``GET /api/leagues/<id>/team-stats/``).
"""

from __future__ import annotations

import unittest

from matches.team_stats_logic import (
    SORT_KEYS,
    TeamStatRow,
    aggregate_team_stats,
    coerce_dir,
    coerce_sort,
    sort_team_stats,
)
from laserforce_simulator.testing import run_project_python

# ===========================================================================
# Pure-unit: aggregate_team_stats
# ===========================================================================


def _round(team_id, pf, pa, surv, tags, tagged):
    return {
        "team_id": team_id,
        "points_for": pf,
        "points_against": pa,
        "survivors": surv,
        "tags_landed": tags,
        "times_tagged": tagged,
    }


class TestAggregateTeamStatsEmpty(unittest.TestCase):
    def test_no_rounds_no_events_zeroed_row_per_enrolled_team(self) -> None:
        rows = aggregate_team_stats([], [], [(1, "Alpha"), (2, "Bravo")])
        self.assertEqual([r.team_id for r in rows], [1, 2])
        for r in rows:
            self.assertEqual(r.rounds_played, 0)
            self.assertEqual(r.avg_points_for, 0.0)
            self.assertEqual(r.avg_margin, 0.0)
            self.assertEqual(r.avg_survivors, 0.0)
            self.assertEqual(r.total_tags_landed, 0)
            self.assertEqual(r.base_captures, 0)
            self.assertEqual(r.nukes_fired, 0)

    def test_no_div_by_zero_on_zero_rounds(self) -> None:
        # Must not raise even with events but no rounds (defensive).
        rows = aggregate_team_stats(
            [],
            [{"team_id": 1, "kind": "base_capture"}],
            [(1, "Alpha")],
        )
        self.assertEqual(rows[0].base_captures, 1)
        self.assertEqual(rows[0].avg_points_for, 0.0)


class TestAggregateTeamStatsAverages(unittest.TestCase):
    def test_averages_over_two_rounds(self) -> None:
        rounds = [
            _round(1, pf=100, pa=40, surv=3, tags=20, tagged=10),
            _round(1, pf=60, pa=80, surv=1, tags=10, tagged=14),
        ]
        rows = aggregate_team_stats(rounds, [], [(1, "Alpha")])
        row = rows[0]
        self.assertEqual(row.rounds_played, 2)
        self.assertAlmostEqual(row.avg_points_for, 80.0)
        self.assertAlmostEqual(row.avg_points_against, 60.0)
        self.assertAlmostEqual(row.avg_margin, 20.0)
        self.assertAlmostEqual(row.avg_survivors, 2.0)
        # Tags / times tagged are TOTALS, not averages.
        self.assertEqual(row.total_tags_landed, 30)
        self.assertEqual(row.total_times_tagged, 24)

    def test_margin_is_signed_and_can_be_negative(self) -> None:
        rows = aggregate_team_stats(
            [_round(1, pf=10, pa=90, surv=0, tags=0, tagged=0)],
            [],
            [(1, "Alpha")],
        )
        self.assertAlmostEqual(rows[0].avg_margin, -80.0)


class TestAggregateTeamStatsEvents(unittest.TestCase):
    def test_event_kind_to_column_mapping(self) -> None:
        events = [
            {"team_id": 1, "kind": "base_capture"},
            {"team_id": 1, "kind": "base_capture"},
            {"team_id": 1, "kind": "missiled", "hit": True},
            {"team_id": 1, "kind": "missiled", "hit": False},
            {"team_id": 1, "kind": "missiled", "hit": True},
            {"team_id": 1, "kind": "nuke_detonation"},
            {"team_id": 1, "kind": "nuke_cancelled"},
            {"team_id": 1, "kind": "nuke_cancelled"},
        ]
        rows = aggregate_team_stats([], events, [(1, "Alpha")])
        row = rows[0]
        self.assertEqual(row.base_captures, 2)
        self.assertEqual(row.missiles_fired, 3)
        self.assertEqual(row.missiles_hit, 2)
        # Detonation == landed (same event counts both columns).
        self.assertEqual(row.nukes_fired, 1)
        self.assertEqual(row.nukes_landed, 1)
        self.assertEqual(row.cancelled_nukes, 2)

    def test_events_bucketed_by_team(self) -> None:
        events = [
            {"team_id": 1, "kind": "base_capture"},
            {"team_id": 2, "kind": "base_capture"},
            {"team_id": 2, "kind": "base_capture"},
        ]
        rows = aggregate_team_stats([], events, [(1, "Alpha"), (2, "Bravo")])
        by_id = {r.team_id: r for r in rows}
        self.assertEqual(by_id[1].base_captures, 1)
        self.assertEqual(by_id[2].base_captures, 2)

    def test_hit_absent_treated_as_miss(self) -> None:
        rows = aggregate_team_stats(
            [], [{"team_id": 1, "kind": "missiled"}], [(1, "Alpha")]
        )
        self.assertEqual(rows[0].missiles_fired, 1)
        self.assertEqual(rows[0].missiles_hit, 0)


class TestAggregateTeamStatsOrder(unittest.TestCase):
    def test_default_order_is_team_name_ascending(self) -> None:
        rows = aggregate_team_stats([], [], [(2, "Zulu"), (1, "Alpha"), (3, "Mike")])
        self.assertEqual([r.team_name for r in rows], ["Alpha", "Mike", "Zulu"])


# ===========================================================================
# Pure-unit: sort_team_stats / coerce_*
# ===========================================================================


def _row(team_id, name, **kw):
    base = dict(
        team_id=team_id,
        team_name=name,
        rounds_played=1,
        avg_points_for=0.0,
        avg_points_against=0.0,
        avg_margin=0.0,
        avg_survivors=0.0,
        total_tags_landed=0,
        total_times_tagged=0,
        base_captures=0,
        missiles_fired=0,
        missiles_hit=0,
        nukes_fired=0,
        nukes_landed=0,
        cancelled_nukes=0,
    )
    base.update(kw)
    return TeamStatRow(**base)


class TestCoerce(unittest.TestCase):
    def test_coerce_sort_unknown_falls_back(self) -> None:
        self.assertEqual(coerce_sort("bogus"), "team")
        self.assertEqual(coerce_sort(None), "team")
        self.assertEqual(coerce_sort("avg_margin"), "avg_margin")

    def test_coerce_dir(self) -> None:
        self.assertEqual(coerce_dir("desc"), "desc")
        self.assertEqual(coerce_dir("asc"), "asc")
        self.assertEqual(coerce_dir("bogus"), "asc")
        self.assertEqual(coerce_dir(None), "asc")


class TestSortTeamStats(unittest.TestCase):
    def setUp(self) -> None:
        self.rows = [
            _row(1, "Alpha", avg_margin=10.0, base_captures=5),
            _row(2, "Bravo", avg_margin=30.0, base_captures=1),
            _row(3, "Charlie", avg_margin=20.0, base_captures=5),
        ]

    def test_sort_numeric_desc(self) -> None:
        out = sort_team_stats(self.rows, "avg_margin", "desc")
        self.assertEqual([r.team_id for r in out], [2, 3, 1])

    def test_sort_numeric_asc(self) -> None:
        out = sort_team_stats(self.rows, "avg_margin", "asc")
        self.assertEqual([r.team_id for r in out], [1, 3, 2])

    def test_ties_break_on_team_name_ascending(self) -> None:
        # Alpha + Charlie both have base_captures=5; Alpha < Charlie by name.
        out = sort_team_stats(self.rows, "base_captures", "desc")
        self.assertEqual([r.team_name for r in out], ["Alpha", "Charlie", "Bravo"])

    def test_sort_by_team_name(self) -> None:
        out = sort_team_stats(self.rows, "team", "desc")
        self.assertEqual([r.team_name for r in out], ["Charlie", "Bravo", "Alpha"])

    def test_unknown_sort_defaults_to_team(self) -> None:
        out = sort_team_stats(self.rows, "bogus", "asc")
        self.assertEqual([r.team_name for r in out], ["Alpha", "Bravo", "Charlie"])

    def test_sort_keys_cover_every_display_column(self) -> None:
        # Every column we render must be a known sort key.
        for key in (
            "team",
            "avg_points_for",
            "avg_points_against",
            "avg_margin",
            "avg_survivors",
            "total_tags_landed",
            "total_times_tagged",
            "base_captures",
            "missiles_fired",
            "missiles_hit",
            "nukes_fired",
            "nukes_landed",
            "cancelled_nukes",
        ):
            self.assertIn(key, SORT_KEYS)


# ===========================================================================
# Pure-module purity
# ===========================================================================


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """``matches.team_stats_logic`` must not transitively import Django.

    Spawn a fresh subprocess, ``import matches.team_stats_logic``, then walk
    ``sys.modules`` and assert no entry matches the ``django`` prefix.
    """

    def test_pure_module_does_not_pull_in_django(self) -> None:
        import pathlib
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
            import matches.team_stats_logic  # noqa: F401
            leaked = sorted(
                m for m in sys.modules
                if m == "django" or m.startswith("django.")
            )
            if leaked:
                print("LEAK:" + ",".join(leaked))
                sys.exit(1)
            sys.exit(0)
            """)
        result = run_project_python(script, timeout=30)
        self.assertEqual(
            result.returncode,
            0,
            f"Django import leaked into matches.team_stats_logic.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}",
        )
