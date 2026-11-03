"""Build the LFS-01 Training and Test files (see docs/lfstats-import.md).

Usage:
    python scripts/lfstats_import/calibration_data.py <snapshot.json> \
        <train_out.json> <test_out.json> <transcript.jsonl> [...]

The Test set is Internationals 2026; the Training set is the snapshot's two-year
window minus it. LFstats can't exclude one competition from a query, so each
(player, position) row is rebuilt from totals: ``(avg_window * games_window -
avg_int * games_int) / (games_window - games_int)``. The transcripts hold these
LFstats MCP calls (read back exactly by ``extract.tool_results``):

- ``lfstats_leaderboard`` scoped to ``internationals_2026`` -> held-out rows
  (``scope.team_result = "loss"`` -> the loss rows);
- ``lfstats_leaderboard`` whose ``qualify.scope`` is ``internationals_2026`` ->
  window rows for Internationals players outside the snapshot;
- ``lfstats_search_games`` scoped to ``internationals_2026`` -> the games.

Baselines, all-time counts and home centers come from the snapshot unchanged.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

COMPETITION = "internationals_2026"
# Keep every row with Training games left: the Test's 5-game thin-data rule is applied by
# the Calibration harness, and compute.py weighs small positions by games.
MIN_GAMES = 1
# Row fields that are identity, not per-game means.
IDENTITY = ("rank", "ipl_id", "member_id", "callsign", "position", "games")

Row = dict[str, Any]


def _extract() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "lfstats_extract", Path(__file__).with_name("extract.py")
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _is_competition(scope: dict[str, Any] | None) -> bool:
    return (scope or {}).get("competitions") == [COMPETITION]


def collect_calibration(paths: list[str]) -> dict[str, Any]:
    """The held-out rows, extra window rows and games found in ``paths``."""
    tool_results = _extract().tool_results
    held_out: dict[str, dict[str, Row]] = {"all": {}, "loss": {}}
    window: dict[str, Row] = {}
    games: dict[str, Row] = {}
    for path in paths:
        for name, inp, result in tool_results(path):
            scope = inp.get("scope") or {}
            if name == "lfstats_search_games" and _is_competition(scope):
                for g in result["data"]:
                    games.setdefault(g["game_slug"], g)
            elif name != "lfstats_leaderboard" or not inp.get("group_by_position"):
                continue
            elif _is_competition(scope):
                kind = "loss" if scope.get("team_result") == "loss" else "all"
                for r in result["data"]:
                    key = f'{r["ipl_id"]}|{r["position"]}'
                    held_out[kind].setdefault(key, {}).update(r)
            elif _is_competition((inp.get("qualify") or {}).get("scope")):
                for r in result["data"]:
                    window.setdefault(f'{r["ipl_id"]}|{r["position"]}', {}).update(r)
    return {"held_out": held_out, "window": window, "games": list(games.values())}


def subtract(window: Row, held_out: Row | None, min_games: int) -> Row | None:
    """``window`` minus ``held_out``; None when fewer than ``min_games`` remain."""
    if held_out is None:
        return window
    games = window["games"] - held_out["games"]
    if games < min_games or games <= 0:
        return None
    out = {**window, "games": games}
    for metric, value in window.items():
        held = held_out.get(metric)
        if metric in IDENTITY or value is None or held is None:
            continue
        out[metric] = (value * window["games"] - held * held_out["games"]) / games
    return out


def build_train(
    snapshot: dict[str, Any],
    extra_window: dict[str, Row],
    held_out: dict[str, dict[str, Row]],
    min_games: int = MIN_GAMES,
) -> dict[str, Any]:
    """The snapshot with every row's held-out games subtracted.

    ``extra_window`` rows (Internationals players outside the snapshot) join the
    ``all`` rows; a snapshot row always wins over one for the same key.
    """
    rows: dict[str, dict[str, Row]] = {}
    for kind in ("all", "loss"):
        source = dict(snapshot["rows"][kind])
        if kind == "all":
            source = {**extra_window, **source}
        rows[kind] = {}
        for key, row in source.items():
            out = subtract(row, held_out[kind].get(key), min_games)
            if out is not None:
                rows[kind][key] = out
    return {
        "rows": rows,
        "baselines": snapshot["baselines"],
        "alltime": snapshot["alltime"],
        "homes": snapshot["homes"],
    }


COLOURS = {11: "fire", 12: "ice"}


def build_test(games: list[Row], averages: dict[str, Row]) -> dict[str, Any]:
    """The Test file: each game's lineups by colour (oldest game first), plus the
    per-(player, position) Internationals averages."""
    out = []
    for g in sorted(games, key=lambda g: (g["start_time"], g["game_slug"])):
        teams = sorted(g["teams"], key=lambda t: t["colour_enum"])
        out.append(
            {
                "slug": g["game_slug"],
                "start_time": g["start_time"],
                "round_type": (g.get("competition") or {}).get("round_type"),
                "teams": [
                    {
                        "colour": COLOURS[t["colour_enum"]],
                        "score": t["score"],
                        "result": t["result"],
                        "roster": [
                            {
                                k: p[k]
                                for k in (
                                    "ipl_id",
                                    "callsign",
                                    "position",
                                    "score",
                                    "mvp",
                                    "is_mercenary",
                                )
                            }
                            for p in t["players"]
                        ],
                    }
                    for t in teams
                ],
            }
        )
    return {"games": out, "averages": averages}


def main(argv: list[str]) -> None:
    with open(argv[1], encoding="utf-8") as f:
        snapshot = json.load(f)
    found = collect_calibration(argv[4:])
    train = build_train(snapshot, found["window"], found["held_out"])
    test = build_test(found["games"], found["held_out"]["all"])
    for path, payload in ((argv[2], train), (argv[3], test)):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=1, sort_keys=True)
    print(
        f"train rows {len(train['rows']['all'])}  loss rows {len(train['rows']['loss'])}"
        f"  test games {len(test['games'])}  test averages {len(test['averages'])}"
    )


if __name__ == "__main__":
    main(sys.argv)
