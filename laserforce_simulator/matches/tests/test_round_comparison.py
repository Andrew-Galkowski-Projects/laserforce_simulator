"""Pure-unit tests for ``matches/round_comparison.py``.

No DB, no Django imports in the assertion path. Seam contract:
``.claude/worktrees/round-analytics-seam-contract.md``.
"""

from __future__ import annotations

import unittest

from matches.round_comparison import (
    COMPARE_FIELD_STAT_KEYS,
    COMPARE_STAT_KEYS,
    cumulative_team_points,
    player_stat_deltas,
    stat_values,
)


def _row(
    *,
    player_id: int = 1,
    name: str = "Alice",
    role: str = "scout",
    team_color: str = "red",
    points_scored: int = 0,
    mvp: float = 0.0,
    tags_made: int = 0,
    times_tagged: int = 0,
    accuracy: int = 0,
    final_lives: int = 0,
    resupplies_given: int = 0,
    missiles_landed: int = 0,
    specials_used: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    combo_resupply_count: int = 0,
) -> dict:
    return {
        "player_id": player_id,
        "name": name,
        "role": role,
        "team_color": team_color,
        "points_scored": points_scored,
        "mvp": mvp,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "final_lives": final_lives,
        "resupplies_given": resupplies_given,
        "missiles_landed": missiles_landed,
        "specials_used": specials_used,
        "follow_up_shots": follow_up_shots,
        "reaction_shots": reaction_shots,
        "combo_resupply_count": combo_resupply_count,
    }


class TestStatValues(unittest.TestCase):
    def test_returns_12_keys_in_order(self) -> None:
        out = stat_values(_row())
        self.assertEqual(list(out.keys()), list(COMPARE_STAT_KEYS))

    def test_mvp_and_accuracy_carry_through(self) -> None:
        out = stat_values(_row(mvp=42.5, accuracy=80))
        self.assertEqual(out["mvp"], 42.5)
        self.assertEqual(out["accuracy"], 80)

    def test_field_keys_subset_of_stat_keys(self) -> None:
        # COMPARE_FIELD_STAT_KEYS is the IntegerField subset (no mvp/accuracy)
        self.assertTrue(set(COMPARE_FIELD_STAT_KEYS).issubset(set(COMPARE_STAT_KEYS)))
        self.assertNotIn("mvp", COMPARE_FIELD_STAT_KEYS)
        self.assertNotIn("accuracy", COMPARE_FIELD_STAT_KEYS)


class TestPlayerStatDeltas(unittest.TestCase):
    def test_empty_inputs_yield_empty(self) -> None:
        self.assertEqual(player_stat_deltas([], []), [])

    def test_both_sides_present_delta_math(self) -> None:
        a = [_row(player_id=1, name="Alice", points_scored=100, tags_made=10)]
        b = [_row(player_id=1, name="Alice", points_scored=150, tags_made=12)]
        rows = player_stat_deltas(a, b)
        self.assertEqual(len(rows), 1)
        cell_pts = rows[0]["stats"]["points_scored"]
        self.assertEqual(cell_pts, {"a": 100, "b": 150, "delta": 50})
        cell_tags = rows[0]["stats"]["tags_made"]
        self.assertEqual(cell_tags, {"a": 10, "b": 12, "delta": 2})

    def test_player_only_in_a_yields_none_b(self) -> None:
        a = [_row(player_id=1, name="Alice", role="scout", team_color="red")]
        rows = player_stat_deltas(a, [])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["role_a"], "scout")
        self.assertIsNone(row["role_b"])
        self.assertEqual(row["side_a"], "red")
        self.assertIsNone(row["side_b"])
        self.assertIsNone(row["stats"]["points_scored"]["delta"])
        self.assertIsNone(row["stats"]["points_scored"]["b"])

    def test_player_only_in_b_yields_none_a(self) -> None:
        b = [_row(player_id=2, name="Bob", role="medic", team_color="blue")]
        rows = player_stat_deltas([], b)
        row = rows[0]
        self.assertIsNone(row["role_a"])
        self.assertEqual(row["role_b"], "medic")
        self.assertIsNone(row["stats"]["tags_made"]["a"])
        self.assertIsNone(row["stats"]["tags_made"]["delta"])

    def test_sorted_by_name_asc(self) -> None:
        a = [
            _row(player_id=1, name="Charlie"),
            _row(player_id=2, name="Alice"),
            _row(player_id=3, name="Bob"),
        ]
        b = [
            _row(player_id=1, name="Charlie"),
            _row(player_id=2, name="Alice"),
            _row(player_id=3, name="Bob"),
        ]
        rows = player_stat_deltas(a, b)
        self.assertEqual([r["name"] for r in rows], ["Alice", "Bob", "Charlie"])

    def test_cells_array_is_template_friendly_ordered_view(self) -> None:
        a = [_row(player_id=1, points_scored=10, mvp=1.0)]
        b = [_row(player_id=1, points_scored=20, mvp=2.0)]
        rows = player_stat_deltas(a, b)
        cells = rows[0]["cells"]
        self.assertEqual(len(cells), len(COMPARE_STAT_KEYS))
        # First entry corresponds to points_scored (first in COMPARE_STAT_KEYS).
        self.assertEqual(cells[0], rows[0]["stats"]["points_scored"])


class TestCumulativeTeamPoints(unittest.TestCase):
    def test_empty_yields_empty(self) -> None:
        self.assertEqual(cumulative_team_points([]), [])

    def test_null_points_coalesce_to_zero(self) -> None:
        events = [(10, None), (20, 5), (30, None)]
        series = cumulative_team_points(events)
        self.assertEqual(series, [[10, 0], [20, 5], [30, 5]])

    def test_running_sum(self) -> None:
        events = [(10, 100), (20, 50), (30, 25)]
        series = cumulative_team_points(events)
        self.assertEqual(series, [[10, 100], [20, 150], [30, 175]])

    def test_accepts_iterable(self) -> None:
        gen = ((tick, 10) for tick in (1, 2, 3))
        series = cumulative_team_points(gen)
        self.assertEqual(series, [[1, 10], [2, 20], [3, 30]])


class TestNoDjangoImportsLeaked(unittest.TestCase):
    def test_clean_import_in_subprocess(self) -> None:
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
            import matches.round_comparison  # noqa: F401

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
