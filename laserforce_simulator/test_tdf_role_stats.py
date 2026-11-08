"""Tests for ``scripts/tdf_role_stats.py`` (CAL-02c, spec #563 D1 / D8).

The script parses the Laserforce TDF event logs of the Internationals 2026 games and
writes the real per-role targets (``data/tdf_role_targets_2026.json``). Loaded by path,
like ``test_lfstats_calibration_data.py``. The synthetic log below is one game with
twelve players and a handful of known events; every expected number is worked by hand
from those events, never from the script.
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import TestCase, mock

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO / "scripts" / "tdf_role_stats.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("tdf_role_stats", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # dataclasses resolve the module through sys.modules at class creation.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


tdf = _load()

# Red team "0": one of each role, two Scouts; blue team "1" the same.
PLAYERS: dict[str, tuple[str, str]] = {
    "#RC": ("0", "1"),
    "#RH": ("0", "2"),
    "#RS1": ("0", "3"),
    "#RS2": ("0", "3"),
    "#RA": ("0", "4"),
    "#RM": ("0", "5"),
    "#BC": ("1", "1"),
    "#BH": ("1", "2"),
    "#BS1": ("1", "3"),
    "#BS2": ("1", "3"),
    "#BA": ("1", "4"),
    "#BM": ("1", "5"),
}


def _entity_rows(players: dict[str, tuple[str, str]]) -> list[str]:
    rows = [
        "0\t2.006\t8.704\t4-23",
        "1\t5\tSpace Marines 5 (SM5-15)\t20260713122126\t900000\t0",
        "2\t0\tFire Marines\t11\tFire\t#FF5000",
        "2\t1\tIce Marines\t12\tIce\t#00A0FF",
        "3\t0000009\t@32\tstandard-target\tTeam Base\t1\t0\t0\tTeam Base\t",
        "3\t0000003\t@77\treferee\tNebula\t2\t0\t0\tNebula\t",
    ]
    for ent, (team, role) in players.items():
        rows.append(
            f"3\t0000003\t{ent}\tplayer\t{ent[1:]}\t{team}\t0\t{role}\tSuit\t4-23-1"
        )
    return rows


# Known events (ms, code, actor, text, target). The red Commander fires five shots:
# a Down on the blue Scout, a shield hit on the blue Commander, a miss, a Base zap and a
# zap on its own Heavy (a team zap: a shot, not a Tag). The blue Scout missiles it (not a
# Tag, not a time tagged); the red Medic and Ammo resupply it; the red Medic then zaps
# the blue Ammo.
EVENTS: list[str] = [
    "4\t0000000\t0100\t* Mission Start *",
    "4\t0001000\t0206\t#RC\t zaps \t#BS1",
    "4\t0001500\t0205\t#RC\t zaps \t#BC",
    "4\t0002200\t0201\t#RC\t misses",
    "4\t0005200\t0203\t#RC\t zaps \t@32",
    "4\t0006000\t0205\t#RC\t zaps \t#RH",
    "4\t0007000\t0306\t#BS1\t missiles \t#RC",
    "4\t0008000\t0502\t#RM\t resupplies \t#RC",
    "4\t0008100\t0500\t#RA\t resupplies \t#RC",
    "4\t0009000\t0205\t#RM\t zaps \t#BA",
]

# Player-state rows: the blue Scout is Down at 1.0 s, coming back at 4.0 s and Active
# again at 5.0 s; the blue Medic goes Down at 5:00 and never returns (an Elimination).
STATES: list[str] = [
    "9\t0000033\t#RC\t0",
    "9\t0001000\t#BS1\t3",
    "9\t0004000\t#BS1\t2",
    "9\t0005000\t#BS1\t0",
    "9\t0300000\t#BM\t3",
]

# sm5-stats: the red Medic's LFstats shotsHit is 3 (two Resupplies and one zap).
STATS: list[str] = [
    "7\t#RM\t3\t3\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t27\t0\t0\t0\t1\t0\t0\t0",
    "7\t#RC\t4\t5\t0\t1\t0\t0\t0\t0\t0\t0\t0\t0\t0\t0\t27\t0\t0\t0\t3\t1\t0\t0",
]


def _write_log(
    directory: Path,
    name: str = "4-23-20260713122126.tdf",
    players: dict[str, tuple[str, str]] = PLAYERS,
    events: list[str] = EVENTS,
    states: list[str] = STATES,
    stats: list[str] = STATS,
) -> Path:
    path = directory / name
    rows = _entity_rows(players) + events + states + stats
    path.write_text("\n".join(rows) + "\n", encoding="utf-16")
    return path


def _payload(**overrides: Any) -> dict[str, Any]:
    with tempfile.TemporaryDirectory() as tmp:
        _write_log(Path(tmp), **overrides)
        return tdf.build_payload(Path(tmp))


class TestTagsFromTheSyntheticLog(TestCase):
    def test_tags_are_zaps_on_enemies_per_role(self) -> None:
        per_role = _payload()["per_role"]
        # Red Commander 2 Tags (the Down and the shield hit), blue Commander 0.
        self.assertEqual(per_role["commander"]["tags"]["mean"], 1.0)
        self.assertEqual(per_role["commander"]["tags"]["se"], 1.0)
        # Only the red Medic's zap on the blue Ammo; the Resupplies are not Tags.
        self.assertEqual(per_role["medic"]["tags"]["mean"], 0.5)
        self.assertEqual(per_role["heavy"]["tags"]["mean"], 0.0)

    def test_times_tagged_counts_enemy_zaps_on_the_player(self) -> None:
        per_role = _payload()["per_role"]
        self.assertEqual(per_role["commander"]["times_tagged"]["mean"], 0.5)
        self.assertEqual(per_role["scout"]["times_tagged"]["mean"], 0.25)
        self.assertEqual(per_role["ammo"]["times_tagged"]["mean"], 0.5)
        # The team zap on the red Heavy is nobody's time tagged.
        self.assertEqual(per_role["heavy"]["times_tagged"]["mean"], 0.0)

    def test_games_and_player_games_are_counted(self) -> None:
        payload = _payload()
        self.assertEqual(payload["games"], {"total": 1, "used": 1, "skipped": 0})
        self.assertEqual(
            payload["player_games"],
            {"commander": 2, "heavy": 2, "scout": 4, "ammo": 2, "medic": 2},
        )

    def test_shots_fired_count_misses_base_zaps_and_every_zap(self) -> None:
        commander = _payload()["per_role"]["commander"]
        # The red Commander's five shots: Down, shield hit, miss, Base zap, team zap.
        self.assertEqual(commander["shots_fired"]["mean"], 2.5)
        self.assertEqual(commander["misses"]["mean"], 0.5)
        self.assertEqual(commander["base_zaps"]["mean"], 0.5)
        self.assertEqual(commander["team_zaps"]["mean"], 0.5)
        # Accuracy on enemies is Tags over Tags plus misses, over players who shot.
        self.assertEqual(commander["accuracy"], {"mean": 0.6667, "se": 0.0, "n": 1})

    def test_downs_given_and_taken_come_from_the_0206_zaps(self) -> None:
        per_role = _payload()["per_role"]
        self.assertEqual(per_role["commander"]["downs_given"]["mean"], 0.5)
        self.assertEqual(per_role["scout"]["downs_taken"]["mean"], 0.25)
        self.assertEqual(per_role["commander"]["downs_taken"]["mean"], 0.0)


class TestWhatATagIsNot(TestCase):
    def test_lfstats_shots_hit_with_resupplies_is_not_a_tag_count(self) -> None:
        medic = _payload()["per_role"]["medic"]
        # The red Medic's sm5-stats shotsHit is 3 (two Resupplies and a zap).
        self.assertEqual(medic["shots_hit_lfstats"]["mean"], 1.5)
        self.assertEqual(medic["tags"]["mean"], 0.5)

    def test_a_missiled_player_is_neither_tagged_nor_shot(self) -> None:
        # Named without the legacy event-type literal that
        # test_missile_log.py's codebase guard forbids in any .py file.
        without = _payload(events=[e for e in EVENTS if "\t0306\t" not in e])
        with_missile = _payload()
        for role in ("commander", "scout"):
            for metric in ("times_tagged", "shots_fired", "tags"):
                self.assertEqual(
                    with_missile["per_role"][role][metric],
                    without["per_role"][role][metric],
                    (role, metric),
                )


class TestStatesAndFloor(TestCase):
    def test_active_share_is_state_0_time_over_fifteen_minutes(self) -> None:
        per_role = _payload()["per_role"]
        # Blue Scout: Down from 1.0 s to 5.0 s -> 896 of 900 s; the other three full.
        self.assertEqual(per_role["scout"]["active_share"]["mean"], 0.9989)
        # Blue Medic Down at 5:00 for good -> 1/3; red Medic full.
        self.assertEqual(per_role["medic"]["active_share"]["mean"], 0.6667)
        self.assertEqual(per_role["commander"]["active_share"]["mean"], 1.0)

    def test_elimination_is_a_down_never_returned_from(self) -> None:
        per_role = _payload()["per_role"]
        self.assertEqual(per_role["medic"]["eliminated"]["mean"], 0.5)
        self.assertEqual(
            per_role["medic"]["elim_minute"], {"mean": 5.0, "se": 0.0, "n": 1}
        )
        # The blue Scout came back, so it is not Eliminated.
        self.assertEqual(per_role["scout"]["eliminated"]["mean"], 0.0)
        self.assertNotIn("elim_minute", per_role["scout"])

    def test_tags_per_active_minute(self) -> None:
        commander = _payload()["per_role"]["commander"]
        # Red Commander 2 Tags over 15 Active minutes, blue 0.
        self.assertEqual(commander["tags_per_active_min"]["mean"], 0.0667)

    def test_enemies_on_the_floor_averaged_over_the_game(self) -> None:
        payload = _payload()
        # Red faces five blues all game and the blue Medic for 300 of 900 s: 5.3333;
        # blue faces six all game. Per team-game.
        self.assertEqual(
            payload["enemies_on_floor"], {"mean": 5.6667, "se": 0.3333, "n": 2}
        )
        self.assertEqual(
            payload["per_role"]["medic"]["enemies_on_floor"]["mean"], 5.6667
        )


class TestMatricesAndCadence(TestCase):
    def test_who_tags_whom_and_who_downs_whom_per_game(self) -> None:
        payload = _payload()
        self.assertEqual(
            payload["who_tags_whom"]["commander"],
            {"commander": 1.0, "heavy": 0.0, "scout": 1.0, "ammo": 0.0, "medic": 0.0},
        )
        self.assertEqual(payload["who_tags_whom"]["medic"]["ammo"], 1.0)
        self.assertEqual(payload["who_tags_whom"]["heavy"]["heavy"], 0.0)
        self.assertEqual(
            payload["who_downs_whom"]["commander"],
            {"commander": 0.0, "heavy": 0.0, "scout": 1.0, "ammo": 0.0, "medic": 0.0},
        )

    def test_cadence_bins_the_gaps_between_one_players_shots(self) -> None:
        cadence = _payload()["cadence"]
        # Red Commander's gaps: 0.5, 0.7, 3.0 and 0.8 s.
        self.assertEqual(
            cadence["commander"],
            {
                "median": 0.75,
                "bins": {
                    "<0.5": 0.25,
                    "0.5-1": 0.5,
                    "1-2": 0.0,
                    "2-5": 0.25,
                    "5-10": 0.0,
                    "10-30": 0.0,
                    "30-60": 0.0,
                    "60+": 0.0,
                },
                "n": 4,
            },
        )
        # One shot gives no gap.
        self.assertEqual(cadence["medic"]["n"], 0)


class TestMedicEliminationSplit(TestCase):
    def test_teams_are_bucketed_by_their_own_medics_elimination_minute(self) -> None:
        split = _payload()["by_medic_elimination"]
        self.assertEqual(
            list(split), ["by_5", "5_to_8", "8_to_12", "after_12", "survives"]
        )
        # Blue's Medic went out at 5:00 -> the blue players sit in 5:00-8:00.
        blue = split["5_to_8"]
        self.assertEqual(blue["team_games"], 1)
        self.assertEqual(blue["per_role"]["commander"]["tags"]["mean"], 0.0)
        self.assertEqual(blue["per_role"]["commander"]["times_tagged"]["mean"], 1.0)
        # Red's Medic survives -> the red Commander's 2 Tags.
        red = split["survives"]
        self.assertEqual(red["team_games"], 1)
        self.assertEqual(red["per_role"]["commander"]["tags"]["mean"], 2.0)
        self.assertEqual(split["by_5"]["team_games"], 0)


class TestSkippedGames(TestCase):
    def test_a_game_with_eleven_players_is_skipped_and_counted(self) -> None:
        eleven = {k: v for k, v in PLAYERS.items() if k != "#BS2"}
        payload = _payload(players=eleven)
        self.assertEqual(payload["games"], {"total": 1, "used": 0, "skipped": 1})
        self.assertEqual(payload["player_games"]["scout"], 0)


class TestFetchAndMain(TestCase):
    def test_fetch_downloads_only_the_missing_slugs(self) -> None:
        calls: list[tuple[str, Path]] = []

        def fake_fetch(slug: str, dest: Path) -> None:
            calls.append((slug, dest))
            _write_log(dest.parent, dest.name)

        with tempfile.TemporaryDirectory() as tmp:
            logs = Path(tmp) / "logs"
            logs.mkdir()
            _write_log(logs, "4-23-a.tdf")
            test_file = Path(tmp) / "test.json"
            test_file.write_text(
                json.dumps({"games": [{"slug": "4-23-a"}, {"slug": "4-23-b"}]}),
                encoding="utf-8",
            )
            out = Path(tmp) / "out.json"
            with mock.patch.object(tdf, "download_log", fake_fetch):
                code = tdf.main(
                    [
                        str(logs),
                        "--out",
                        str(out),
                        "--fetch",
                        "--test-file",
                        str(test_file),
                    ]
                )
            self.assertEqual(code, 0)
            self.assertEqual(calls, [("4-23-b", logs / "4-23-b.tdf")])
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["games"], {"total": 2, "used": 2, "skipped": 0})
        self.assertEqual(
            set(written),
            {
                "games",
                "player_games",
                "per_role",
                "who_tags_whom",
                "who_downs_whom",
                "cadence",
                "enemies_on_floor",
                "by_medic_elimination",
            },
        )


def _assert_close(got: Any, want: Any, path: str = "") -> None:
    if isinstance(want, dict):
        assert isinstance(got, dict) and set(got) == set(want), path
        for key in want:
            _assert_close(got[key], want[key], f"{path}/{key}")
    elif isinstance(want, float):
        assert got == pytest.approx(want, rel=1e-9), path
    else:
        assert got == want, path


def test_committed_targets_match_the_logs() -> None:
    """``data/tdf_role_targets_2026.json`` is what the script writes from the logs.
    The logs stay out of the repo, so the test runs only where ``TDF_LOGS_DIR`` names
    a folder holding them (``TestCommittedTargets``'s pattern, within float noise)."""
    logs_dir = os.environ.get("TDF_LOGS_DIR")
    if not logs_dir or not any(Path(logs_dir).glob("*.tdf")):
        pytest.skip("TDF_LOGS_DIR is not set to a folder of .tdf logs")
    committed_path = _REPO / "data" / "tdf_role_targets_2026.json"
    committed = json.loads(committed_path.read_text(encoding="utf-8"))
    payload = tdf.build_payload(Path(logs_dir))
    assert committed["games"] == payload["games"]
    _assert_close(committed, payload)
