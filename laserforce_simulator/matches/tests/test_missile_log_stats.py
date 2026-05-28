"""Pure-unit tests for ``matches/missile_log_stats.py``.

No DB, no Django imports in the assertion path. Seam contract:
``.claude/worktrees/round-analytics-seam-contract.md``.
"""

from __future__ import annotations

import unittest

from matches.missile_log_stats import MissileRow, summarize_missile_log


def _ev(
    *,
    timestamp: int = 100,
    result: str = "hit",
    actor_role: str = "commander",
    target_role: str = "heavy",
    friendly_fire: bool = False,
    description: str = "missile hits target",
    points_awarded: int | None = 500,
) -> dict:
    return {
        "timestamp": timestamp,
        "metadata": {
            "result": result,
            "actor_role": actor_role,
            "target_role": target_role,
            "friendly_fire": friendly_fire,
        },
        "description": description,
        "points_awarded": points_awarded,
    }


class TestSummarizeMissileLogEmpty(unittest.TestCase):
    def test_empty_input(self) -> None:
        out = summarize_missile_log([])
        self.assertEqual(out, {"fired": 0, "hit": 0, "efficiency": 0.0, "rows": []})

    def test_no_div_by_zero_on_empty(self) -> None:
        # The 0/0 efficiency case must short-circuit cleanly.
        out = summarize_missile_log([])
        self.assertEqual(out["efficiency"], 0.0)


class TestSummarizeMissileLogCounts(unittest.TestCase):
    def test_one_hit(self) -> None:
        out = summarize_missile_log([_ev(result="hit")])
        self.assertEqual(out["fired"], 1)
        self.assertEqual(out["hit"], 1)
        self.assertEqual(out["efficiency"], 100.0)

    def test_one_miss(self) -> None:
        out = summarize_missile_log([_ev(result="miss")])
        self.assertEqual(out["fired"], 1)
        self.assertEqual(out["hit"], 0)
        self.assertEqual(out["efficiency"], 0.0)

    def test_mixed_efficiency(self) -> None:
        evs = [_ev(result="hit"), _ev(result="miss"), _ev(result="hit")]
        out = summarize_missile_log(evs)
        self.assertEqual(out["fired"], 3)
        self.assertEqual(out["hit"], 2)
        self.assertAlmostEqual(out["efficiency"], 2 / 3 * 100.0)


class TestFriendlyFireCountedAsHit(unittest.TestCase):
    def test_friendly_fire_hit_counted(self) -> None:
        # CONTEXT.md: the missile landed; FF is qualitative, not discount.
        out = summarize_missile_log(
            [_ev(result="hit", friendly_fire=True), _ev(result="miss")]
        )
        self.assertEqual(out["hit"], 1)
        self.assertEqual(out["fired"], 2)

    def test_row_class_marks_friendly_fire(self) -> None:
        out = summarize_missile_log([_ev(result="hit", friendly_fire=True)])
        self.assertIn("friendly-fire", out["rows"][0]["row_class"])

    def test_row_class_default_when_not_friendly(self) -> None:
        out = summarize_missile_log([_ev(result="hit", friendly_fire=False)])
        self.assertEqual(out["rows"][0]["row_class"], "missile-row")


class TestMmssFormatting(unittest.TestCase):
    def test_zero_tick(self) -> None:
        out = summarize_missile_log([_ev(timestamp=0)])
        self.assertEqual(out["rows"][0]["timestamp_mmss"], "00:00")

    def test_tick_2_is_one_second(self) -> None:
        # TIME-01: 2 ticks = 1 second.
        out = summarize_missile_log([_ev(timestamp=2)])
        self.assertEqual(out["rows"][0]["timestamp_mmss"], "00:01")

    def test_round_end_tick_1800(self) -> None:
        # 1800 ticks = 900 seconds = 15:00.
        out = summarize_missile_log([_ev(timestamp=1800)])
        self.assertEqual(out["rows"][0]["timestamp_mmss"], "15:00")

    def test_mid_round_tick(self) -> None:
        # 245 ticks // 2 = 122 s = 2:02.
        out = summarize_missile_log([_ev(timestamp=245)])
        self.assertEqual(out["rows"][0]["timestamp_mmss"], "02:02")


class TestRowShape(unittest.TestCase):
    def test_no_event_orm_ref_in_row(self) -> None:
        out = summarize_missile_log([_ev()])
        self.assertNotIn("event", out["rows"][0])

    def test_row_carries_description_and_points_flat(self) -> None:
        out = summarize_missile_log(
            [_ev(description="missile detonates", points_awarded=500)]
        )
        self.assertEqual(out["rows"][0]["description"], "missile detonates")
        self.assertEqual(out["rows"][0]["points"], 500)

    def test_null_points_coalesce_to_zero(self) -> None:
        out = summarize_missile_log([_ev(points_awarded=None)])
        self.assertEqual(out["rows"][0]["points"], 0)

    def test_missing_metadata_keys_default_to_empty_str(self) -> None:
        ev = {"timestamp": 10, "metadata": {}, "description": "", "points_awarded": 0}
        out = summarize_missile_log([ev])
        self.assertEqual(out["rows"][0]["actor_role"], "")
        self.assertEqual(out["rows"][0]["target_role"], "")
        self.assertEqual(out["rows"][0]["result"], "")
        self.assertFalse(out["rows"][0]["friendly_fire"])

    def test_missile_row_dataclass_is_frozen(self) -> None:
        row = MissileRow(
            timestamp=0,
            timestamp_mmss="00:00",
            actor_role="",
            target_role="",
            result="",
            friendly_fire=False,
            description="",
            points=0,
            row_class="missile-row",
        )
        with self.assertRaises(Exception):
            row.timestamp = 5  # type: ignore[misc]


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
            import matches.missile_log_stats  # noqa: F401

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
