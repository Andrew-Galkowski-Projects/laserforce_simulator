"""Pure-unit tests for ``matches/round_summary.py``.

No DB, no Django imports in the assertion path. Seam contract:
``.claude/worktrees/round-analytics-seam-contract.md``. Mirrors the
``test_h2h_stats.py`` precedent — hand-crafted dict literals via
``_player_row_dict``.
"""

from __future__ import annotations

import unittest

from matches.round_summary import (
    PLAYER_ROW_KEYS,
    survivor_count,
    team_eliminated,
    team_totals,
)


def _player_row_dict(
    *,
    name: str = "Alice",
    role: str = "scout",
    team_color: str = "red",
    was_eliminated_at: int = 1801,
    eliminated_timestamp: str = "",
    is_eliminated: bool = False,
    final_lives: int = 5,
    points_scored: int = 0,
    mvp: float = 0.0,
    tags_made: int = 0,
    times_tagged: int = 0,
    accuracy: int = 0,
    final_shots: int = 0,
    final_special: int = 0,
    shots_used: int = 0,
    missiles_used: int = 0,
    starting_missiles: int = 0,
    missiles_landed: int = 0,
    times_missiled: int = 0,
    final_medic_hits: int = 0,
    medic_lives_removed_from_nuke: int = 0,
    follow_up_shots: int = 0,
    reaction_shots: int = 0,
    resupplies_given: int = 0,
    specials_used: int = 0,
    combo_resupply_count: int = 0,
    specific_tags_count: int = 0,
    special_cost: int = 10,
) -> dict:
    return {
        "name": name,
        "role": role,
        "team_color": team_color,
        "was_eliminated_at": was_eliminated_at,
        "eliminated_timestamp": eliminated_timestamp,
        "is_eliminated": is_eliminated,
        "final_lives": final_lives,
        "points_scored": points_scored,
        "mvp": mvp,
        "tags_made": tags_made,
        "times_tagged": times_tagged,
        "accuracy": accuracy,
        "final_shots": final_shots,
        "final_special": final_special,
        "shots_used": shots_used,
        "missiles_used": missiles_used,
        "starting_missiles": starting_missiles,
        "missiles_landed": missiles_landed,
        "times_missiled": times_missiled,
        "final_medic_hits": final_medic_hits,
        "medic_lives_removed_from_nuke": medic_lives_removed_from_nuke,
        "follow_up_shots": follow_up_shots,
        "reaction_shots": reaction_shots,
        "resupplies_given": resupplies_given,
        "specials_used": specials_used,
        "combo_resupply_count": combo_resupply_count,
        "specific_tags_count": specific_tags_count,
        "special_cost": special_cost,
    }


class TestPlayerRowKeys(unittest.TestCase):
    def test_28_keys_in_pinned_order(self) -> None:
        self.assertEqual(len(PLAYER_ROW_KEYS), 28)
        self.assertEqual(PLAYER_ROW_KEYS[0], "name")
        self.assertEqual(PLAYER_ROW_KEYS[-1], "special_cost")

    def test_fixture_dict_covers_every_key(self) -> None:
        row = _player_row_dict()
        for key in PLAYER_ROW_KEYS:
            self.assertIn(key, row, msg=f"fixture missing pinned key {key!r}")

    def test_no_extra_keys_in_fixture(self) -> None:
        row = _player_row_dict()
        extras = set(row) - set(PLAYER_ROW_KEYS)
        self.assertEqual(extras, set(), msg=f"fixture has extras: {extras}")


class TestTeamTotals(unittest.TestCase):
    def test_empty_zeros(self) -> None:
        out = team_totals([], team_points=0)
        self.assertEqual(
            out,
            {
                "resupplies_given": 0,
                "missiles_landed": 0,
                "specials_used": 0,
                "tags_made": 0,
                "survivors": 0,
                "team_points": 0,
            },
        )

    def test_sums_across_rows(self) -> None:
        rows = [
            _player_row_dict(
                resupplies_given=3, missiles_landed=1, specials_used=2, tags_made=10
            ),
            _player_row_dict(
                resupplies_given=2, missiles_landed=4, specials_used=1, tags_made=5
            ),
        ]
        out = team_totals(rows, team_points=12_345)
        self.assertEqual(out["resupplies_given"], 5)
        self.assertEqual(out["missiles_landed"], 5)
        self.assertEqual(out["specials_used"], 3)
        self.assertEqual(out["tags_made"], 15)
        self.assertEqual(out["team_points"], 12_345)

    def test_survivors_from_final_lives(self) -> None:
        rows = [
            _player_row_dict(final_lives=3),
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        out = team_totals(rows, team_points=0)
        self.assertEqual(out["survivors"], 2)

    def test_accepts_iterable_not_just_list(self) -> None:
        gen = (_player_row_dict(tags_made=n) for n in (1, 2, 3))
        out = team_totals(gen, team_points=0)
        self.assertEqual(out["tags_made"], 6)


class TestSurvivorCount(unittest.TestCase):
    def test_empty_zero(self) -> None:
        self.assertEqual(survivor_count([]), 0)

    def test_all_survive(self) -> None:
        rows = [_player_row_dict(final_lives=n) for n in (1, 2, 3)]
        self.assertEqual(survivor_count(rows), 3)

    def test_all_eliminated(self) -> None:
        rows = [_player_row_dict(final_lives=0) for _ in range(4)]
        self.assertEqual(survivor_count(rows), 0)

    def test_mixed(self) -> None:
        rows = [
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=2),
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        self.assertEqual(survivor_count(rows), 2)


class TestTeamEliminated(unittest.TestCase):
    def test_empty_team_counts_as_eliminated(self) -> None:
        self.assertTrue(team_eliminated([]))

    def test_one_survivor_not_eliminated(self) -> None:
        rows = [
            _player_row_dict(final_lives=0),
            _player_row_dict(final_lives=1),
        ]
        self.assertFalse(team_eliminated(rows))

    def test_all_zero_eliminated(self) -> None:
        rows = [_player_row_dict(final_lives=0) for _ in range(6)]
        self.assertTrue(team_eliminated(rows))


class TestNoDjangoImportsLeaked(unittest.TestCase):
    """Importing ``matches.round_summary`` in a fresh subprocess must not
    pull in ``django.*`` or ``matches.models``.
    """

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
            import matches.round_summary  # noqa: F401

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
