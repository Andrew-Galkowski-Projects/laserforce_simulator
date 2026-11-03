"""Tests for ``scripts/lfstats_import/calibration_data.py`` (LFS-01c).

The script builds the LFS-01 Training and Test files: Training is the two-year
window minus Internationals 2026, per (player, position), since LFstats can't
exclude one competition from a query. Loaded by path, like the other import
scripts (see test_lfstats_import.py).
"""

import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

_DIR = Path(__file__).resolve().parent.parent / "scripts" / "lfstats_import"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"lfstats_{name}", _DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


data = _load("calibration_data")

INT = "internationals_2026"


def _row(ipl: str, position: str, games: int, **metrics: float | None) -> dict:
    return {
        "ipl_id": ipl,
        "callsign": ipl.strip("#"),
        "position": position,
        "games": games,
        **metrics,
    }


class TestSubtract(TestCase):
    def test_average_is_window_total_minus_internationals_total(self) -> None:
        window = _row("#A", "scout", 40, avg_mvp=10.0, elimination_rate=0.5)
        held_out = _row("#A", "scout", 10, avg_mvp=16.0, elimination_rate=0.2)
        out = data.subtract(window, held_out, min_games=10)
        assert out is not None
        self.assertEqual(out["games"], 30)
        self.assertAlmostEqual(out["avg_mvp"], (10.0 * 40 - 16.0 * 10) / 30)
        self.assertAlmostEqual(out["elimination_rate"], (0.5 * 40 - 0.2 * 10) / 30)
        self.assertEqual(out["callsign"], "A")

    def test_row_below_min_games_after_subtraction_is_dropped(self) -> None:
        window = _row("#A", "scout", 15, avg_mvp=10.0)
        held_out = _row("#A", "scout", 6, avg_mvp=10.0)
        self.assertIsNone(data.subtract(window, held_out, min_games=10))
        self.assertIsNone(data.subtract(window, {**held_out, "games": 15}, 1))

    def test_no_internationals_row_leaves_the_window_row(self) -> None:
        window = _row("#A", "scout", 40, avg_mvp=10.0)
        self.assertEqual(data.subtract(window, None, min_games=10), window)

    def test_metric_missing_on_one_side_keeps_the_window_value(self) -> None:
        window = _row("#A", "heavy", 40, avg_missiles_hit=4.0)
        held_out = _row("#A", "heavy", 10, avg_missiles_hit=None)
        out = data.subtract(window, held_out, min_games=10)
        assert out is not None
        self.assertEqual(out["avg_missiles_hit"], 4.0)


class TestBuildTrain(TestCase):
    def _snapshot(self) -> dict:
        return {
            "rows": {
                "all": {"#A|scout": _row("#A", "scout", 40, avg_mvp=10.0)},
                "loss": {"#A|scout": _row("#A", "scout", 20, avg_mvp=8.0)},
            },
            "baselines": {"all": {"scout": {"avg_mvp": 9.0}}, "loss": {}},
            "alltime": {"#A": 900},
            "homes": {"#A": "Syracuse"},
        }

    def test_subtracts_all_and_loss_and_keeps_the_rest(self) -> None:
        held_out = {
            "all": {"#A|scout": _row("#A", "scout", 10, avg_mvp=16.0)},
            "loss": {"#A|scout": _row("#A", "scout", 5, avg_mvp=8.0)},
        }
        train = data.build_train(self._snapshot(), {}, held_out, min_games=10)
        self.assertEqual(train["rows"]["all"]["#A|scout"]["games"], 30)
        self.assertEqual(train["rows"]["loss"]["#A|scout"]["games"], 15)
        self.assertEqual(train["baselines"], self._snapshot()["baselines"])
        self.assertEqual(train["alltime"], {"#A": 900})
        self.assertEqual(train["homes"], {"#A": "Syracuse"})

    def test_extra_window_rows_join_but_never_replace_snapshot_rows(self) -> None:
        extra = {
            "#A|scout": _row("#A", "scout", 99, avg_mvp=1.0),
            "#B|medic": _row("#B", "medic", 30, avg_mvp=5.0),
        }
        train = data.build_train(
            self._snapshot(), extra, {"all": {}, "loss": {}}, min_games=10
        )
        self.assertEqual(train["rows"]["all"]["#A|scout"]["games"], 40)
        self.assertEqual(train["rows"]["all"]["#B|medic"]["games"], 30)
        self.assertNotIn("#B|medic", train["rows"]["loss"])


def _team(colour_enum: int, result: str, players: list[tuple[str, str]]) -> dict:
    return {
        "name": "Fire Marines" if colour_enum == 11 else "Ice Marines",
        "colour_enum": colour_enum,
        "score": 30000,
        "result": result,
        "players": [
            {
                "ipl_id": ipl,
                "callsign": ipl.strip("#"),
                "position": pos,
                "score": 5000,
                "mvp": 10.0,
                "is_mercenary": False,
            }
            for ipl, pos in players
        ],
    }


class TestBuildTest(TestCase):
    def test_games_keep_lineups_by_colour_and_averages_pass_through(self) -> None:
        game = {
            "game_slug": "4-23-2",
            "start_time": "2026-07-18T15:15:14",
            "competition": {"round_type": "finals"},
            "teams": [
                _team(12, "loss", [("#B", "medic")]),
                _team(11, "win", [("#A", "scout")]),
            ],
        }
        older = {**game, "game_slug": "4-23-1", "start_time": "2026-07-13T09:00:00"}
        averages = {"#A|scout": _row("#A", "scout", 3, avg_mvp=9.0)}
        test = data.build_test([game, older], averages)
        self.assertEqual([g["slug"] for g in test["games"]], ["4-23-1", "4-23-2"])
        first = test["games"][1]
        self.assertEqual(first["round_type"], "finals")
        self.assertEqual([t["colour"] for t in first["teams"]], ["fire", "ice"])
        self.assertEqual(first["teams"][0]["result"], "win")
        self.assertEqual(
            first["teams"][0]["roster"],
            [
                {
                    "ipl_id": "#A",
                    "callsign": "A",
                    "position": "scout",
                    "score": 5000,
                    "mvp": 10.0,
                    "is_mercenary": False,
                }
            ],
        )
        self.assertEqual(test["averages"], averages)


class TestCollect(TestCase):
    def _call(self, tool_id: str, name: str, inp: dict, result: dict) -> list[dict]:
        use = {"type": "tool_use", "id": tool_id, "name": f"mcp__x__{name}"}
        use["input"] = inp
        res = {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": [{"type": "text", "text": json.dumps(result)}],
        }
        return [{"message": {"content": [use]}}, {"message": {"content": [res]}}]

    def test_sorts_each_call_into_its_part(self) -> None:
        board = {"group_by_position": True, "min_games": 1}
        int_scope = {"competitions": [INT], "include_mercenary_games": True}
        r1 = _row("#A", "scout", 3, avg_mvp=9.0)
        r2 = _row("#A", "scout", 3, avg_accuracy=0.8)
        loss = _row("#A", "scout", 1, avg_mvp=4.0)
        window = _row("#A", "scout", 50, avg_mvp=7.0)
        qualify = {"min_games": 1, "scope": {"competitions": [INT]}}
        game = {"game_slug": "4-23-1", "teams": []}
        records = [
            *self._call(
                "t1",
                "lfstats_leaderboard",
                {**board, "scope": int_scope},
                {"data": [r1]},
            ),
            *self._call(
                "t2",
                "lfstats_leaderboard",
                {**board, "scope": int_scope},
                {"data": [r2]},
            ),
            *self._call(
                "t3",
                "lfstats_leaderboard",
                {**board, "scope": {**int_scope, "team_result": "loss"}},
                {"data": [loss]},
            ),
            *self._call(
                "t4",
                "lfstats_leaderboard",
                {**board, "scope": {"date_range": {}}, "qualify": qualify},
                {"data": [window]},
            ),
            *self._call(
                "t5",
                "lfstats_search_games",
                {"scope": {"competitions": [INT]}, "include_rosters": True},
                {"data": [game]},
            ),
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            path.write_text("\n".join(json.dumps(r) for r in records), "utf-8")
            got = data.collect_calibration([str(path), str(path)])
        self.assertEqual(got["held_out"]["all"]["#A|scout"], {**r1, **r2})
        self.assertEqual(got["held_out"]["loss"]["#A|scout"], loss)
        self.assertEqual(got["window"], {"#A|scout": window})
        self.assertEqual(got["games"], [game])

    def test_reads_a_result_the_harness_saved_to_a_file(self) -> None:
        game = {"game_slug": "4-23-9", "teams": []}
        with tempfile.TemporaryDirectory() as tmp:
            saved = Path(tmp) / "mcp-lfstats_search_games-1.txt"
            saved.write_text(json.dumps({"data": [game]}), "utf-8")
            pointer = (
                "Error: result (196,330 characters across 1 line) exceeds maximum"
                f" allowed tokens. Output has been saved to {saved}.\n"
                "Format: Plain text\nSearch within the file for specific content"
            )
            use = {
                "type": "tool_use",
                "id": "t1",
                "name": "mcp__x__lfstats_search_games",
                "input": {"scope": {"competitions": [INT]}},
            }
            res = {"type": "tool_result", "tool_use_id": "t1", "content": pointer}
            path = Path(tmp) / "session.jsonl"
            path.write_text(
                "\n".join(
                    json.dumps({"message": {"content": [c]}}) for c in (use, res)
                ),
                "utf-8",
            )
            got = data.collect_calibration([str(path)])
        self.assertEqual(got["games"], [game])
