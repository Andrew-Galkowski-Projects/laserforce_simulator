"""Tests for the repo-level ``scripts/lfstats_import/`` LFstats -> roster CSV tools.

The scripts live outside the Django project, so they are loaded by path. Formulas
are documented in docs/lfstats-import.md; these tests pin the anchors (a player
exactly at the field average scores 50) rather than every constant.
"""

import importlib.util
import json
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import TestCase

from teams.roster_importer import parse_roster_csv

_DIR = Path(__file__).resolve().parent.parent / "scripts" / "lfstats_import"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"lfstats_{name}", _DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


compute = _load("compute")
extract = _load("extract")

_POSITIONS = ("commander", "heavy", "scout", "ammo", "medic")


def _baseline() -> dict:
    """Field averages; every position uses the same numbers for simplicity."""
    return {
        "avg_accuracy": 0.75,
        "avg_hit_diff": 1.0,
        "avg_times_hit": 40.0,
        "elimination_rate": 0.5,
        "avg_nukes_canceled": 0.02,
        "avg_mvp": 15.0,
        "win_rate": 0.5,
        "avg_penalties": 0.0,
        "avg_shots_hit": 50.0,
        "avg_uptime_pct": 0.6,
        "avg_medic_hits": 2.0,
        "avg_eliminations": 0.5,
        "avg_assists": 5.0,
        "avg_resupplies_given": 30.0,
        "avg_nukes_detonated": 3.0,
        "avg_missiles_hit": 4.0,
        "avg_rapid_fire": 2.0,
    }


def _row(position: str, games: int = 50, **overrides: float) -> dict:
    return {"position": position, "games": games, **_baseline(), **overrides}


def _player(ipl: str, prefs: list[str], home: str = "Home", games: int = 100) -> dict:
    return {"ipl": ipl, "name": ipl, "prefs": prefs, "home": home, "games_2y": games}


class TestLogPart(TestCase):
    def test_average_is_50_and_double_is_75(self) -> None:
        self.assertEqual(compute.log_part(10, 10, 0.25), 50)
        self.assertEqual(compute.log_part(20, 10, 0.25), 75)
        self.assertEqual(compute.log_part(5, 10, 0.25), 25)

    def test_floor_and_missing_data(self) -> None:
        self.assertEqual(compute.log_part(0, 1, 0.25), 0)  # 50 + 25*log2(0.25) = 0
        self.assertIsNone(compute.log_part(None, 10, 0.25))
        self.assertIsNone(compute.log_part(5, 0, 0.25))


class TestStatsFor(TestCase):
    def setUp(self) -> None:
        self.baselines = {p: _baseline() for p in _POSITIONS}

    def test_player_at_field_average_scores_50(self) -> None:
        stats = compute.stats_for(
            [_row("scout")], [_row("scout")], self.baselines, self.baselines
        )
        self.assertEqual(set(stats), set(compute.STAT_COLUMNS))
        expected = {s: 50 for s in compute.STAT_COLUMNS}
        expected["accuracy"] = 75  # raw accuracy, not position-relative
        self.assertEqual(stats, expected)

    def test_double_mvp_raises_mvp_based_stats_to_75(self) -> None:
        stats = compute.stats_for(
            [_row("heavy", avg_mvp=30.0)], [], self.baselines, self.baselines
        )
        self.assertEqual(stats["game_awareness"], 75)
        self.assertEqual(stats["defensive_synergy"], 75)
        self.assertEqual(stats["midfield_synergy"], 75)
        self.assertEqual(stats["adaptability"], 50)  # no losses -> default

    def test_stats_are_games_weighted_across_positions(self) -> None:
        rows = [
            _row("scout", games=30, avg_mvp=30.0),
            _row("medic", games=10, avg_mvp=15.0),
        ]
        stats = compute.stats_for(rows, [], self.baselines, self.baselines)
        self.assertEqual(stats["game_awareness"], round((30 * 75 + 10 * 50) / 40))

    def test_values_clamp_to_0_100(self) -> None:
        stats = compute.stats_for(
            [_row("commander", avg_mvp=-5.0, avg_accuracy=1.2)],
            [],
            self.baselines,
            self.baselines,
        )
        self.assertEqual(stats["game_awareness"], 0)
        self.assertEqual(stats["accuracy"], 100)


class TestPreferredRoles(TestCase):
    def test_second_role_needs_20_percent_of_games(self) -> None:
        rows = [_row("scout", games=80), _row("medic", games=20)]
        self.assertEqual(compute.preferred_roles(rows), ["scout", "medic"])
        rows = [_row("scout", games=81), _row("medic", games=19)]
        self.assertEqual(compute.preferred_roles(rows), ["scout"])


class TestAssignTeams(TestCase):
    def _six(self, home: str = "Home") -> list[dict]:
        roles = ["commander", "heavy", "scout", "scout", "medic", "ammo"]
        return [_player(f"{home}{i}", [r], home) for i, r in enumerate(roles)]

    def test_one_full_roster_and_extra_goes_to_free_agents(self) -> None:
        players = [*self._six(), _player("Extra", ["heavy"], games=1)]
        compute.assign_teams(players)
        teams = {p["ipl"]: p["team"] for p in players}
        self.assertEqual(teams["Extra"], compute.FREE_AGENTS_TEAM_NAME)
        self.assertEqual({t for k, t in teams.items() if k != "Extra"}, {"Home 1"})

    def test_second_preferred_role_fills_a_gap(self) -> None:
        players = self._six()
        players[0] = _player("Flex", ["scout", "commander"])  # no pure commander
        players.append(_player("Sc3", ["scout"], games=1))
        compute.assign_teams(players)
        flex = next(p for p in players if p["ipl"] == "Flex")
        self.assertEqual((flex["team"], flex["role"]), ("Home 1", "commander"))

    def test_single_role_scout_fills_any_open_slot(self) -> None:
        players = self._six()
        players[4] = _player("Sc3", ["scout"])  # replaces the medic
        compute.assign_teams(players)
        self.assertEqual({p["team"] for p in players}, {"Home 1"})
        self.assertEqual(
            sorted(p["role"] for p in players),
            ["ammo", "commander", "heavy", "medic", "scout", "scout"],
        )

    def test_repeated_name_on_a_team_gets_ipl_suffix(self) -> None:
        a, b = _player("#A", ["heavy"], games=9), _player("#B", ["heavy"], games=1)
        a["name"] = b["name"] = "Twin"
        compute.assign_teams([a, b])
        self.assertEqual(a["csv_name"], "Twin")
        self.assertEqual(b["csv_name"], "Twin (#B)")


class TestCsvRoundTrip(TestCase):
    def test_output_parses_with_the_roster_importer(self) -> None:
        baselines = {p: _baseline() for p in _POSITIONS}
        rows: dict = {}
        roles = ["commander", "heavy", "scout", "scout", "medic", "ammo", "scout"]
        for i, role in enumerate(roles):
            rows[f"#{i}|{role}"] = {
                **_row(role, games=60 - i),
                "ipl_id": f"#{i}",
                "callsign": f"P{i}",
            }
        data = {
            "rows": {"all": rows, "loss": {}},
            "baselines": {"all": baselines, "loss": baselines},
            "alltime": {"#0": 900},
            "homes": {f"#{i}": "Loveland" for i in range(7)},
        }
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.csv"
            compute.write_csv(compute.build_players(data), str(out))
            parsed = parse_roster_csv(out.read_text(encoding="utf-8"))
        self.assertEqual(len(parsed.by_team["Loveland 1"]), 6)
        self.assertEqual([r.name for r in parsed.by_team["Free Agents"]], ["P6"])
        commander = next(r for r in parsed.rows if r.name == "P0")
        self.assertEqual(commander.profile["total_games"], 900)
        self.assertEqual(commander.profile["home_site"], "Loveland")


class TestExtract(TestCase):
    def _write(self, path: Path, records: list[dict]) -> None:
        path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")

    def _call(self, tool_id: str, name: str, inp: dict, result: dict) -> list[dict]:
        use = {
            "type": "tool_use",
            "id": tool_id,
            "name": f"mcp__x__{name}",
            "input": inp,
        }
        res = {
            "type": "tool_result",
            "tool_use_id": tool_id,
            "content": [{"type": "text", "text": json.dumps(result)}],
        }
        return [{"message": {"content": [use]}}, {"message": {"content": [res]}}]

    def test_collects_rows_baselines_and_homes_once(self) -> None:
        board = {
            "group_by_position": True,
            "qualify": {"min_games": 26},
            "scope": {"team_result": "loss"},
        }
        row = {"ipl_id": "#A", "position": "scout", "games": 3, "avg_mvp": 9.0}
        resolve = {
            "data": {
                "players": [
                    {
                        "status": "unique",
                        "matches": [
                            {"ipl_id": "#A", "home_center": {"name": "Syracuse"}}
                        ],
                    }
                ]
            }
        }
        stats = {
            "data": {
                "baseline": {"breakdown": [{"position": "scout", "avg_mvp": 12.0}]}
            }
        }
        records = [
            *self._call("t1", "lfstats_leaderboard", board, {"data": [row]}),
            *self._call("t2", "lfstats_resolve", {"players": ["#A"]}, resolve),
            *self._call(
                "t3", "lfstats_player_stats", {"breakdown": ["position"]}, stats
            ),
            {"not json": True},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "session.jsonl"
            self._write(path, records)
            snapshot = extract.collect([str(path), str(path)])
        self.assertEqual(snapshot["rows"]["loss"], {"#A|scout": row})
        self.assertEqual(snapshot["rows"]["all"], {})
        self.assertEqual(snapshot["homes"], {"#A": "Syracuse"})
        self.assertEqual(snapshot["baselines"]["all"]["scout"]["avg_mvp"], 12.0)
