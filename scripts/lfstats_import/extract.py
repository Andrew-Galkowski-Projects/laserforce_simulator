"""Collect LFstats results from Claude Code session transcripts into a snapshot.

Usage:
    python scripts/lfstats_import/extract.py <out.json> <transcript.jsonl> [...]

LFstats has no public API yet (see docs/lfstats-import.md), so the data was
fetched through the LFstats MCP connector in a Claude Code session. The tool
results are stored verbatim in that session's ``.jsonl`` transcripts, and this
script reads them back exactly, so no numbers are transcribed by hand. It
recognises these calls:

- ``lfstats_leaderboard`` with ``group_by_position`` and ``qualify.min_games``
  26 -> per-(player, position) rows; ``scope.team_result = "loss"`` goes to
  the loss rows;
- ``lfstats_leaderboard`` over ``all_time`` -> all-time game counts;
- ``lfstats_player_stats`` with a position breakdown -> field baselines;
- ``lfstats_resolve`` for players -> home centers.

Results with the same tool-use id are read once, and rows for the same key merge.
A result too large for the transcript is read from the file it was saved to.
``calibration_data.py`` reads the LFS-01 calls through ``tool_results`` too.
"""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterator
from typing import Any

LEADERBOARD = "lfstats_leaderboard"
PLAYER_STATS = "lfstats_player_stats"
RESOLVE = "lfstats_resolve"
QUALIFY_MIN_GAMES = 26
SAVED = re.compile(r"Output has been saved to (.+?\.txt)\.(?:\s|$)")


def saved_result(body: Any) -> str | None:
    """The text of a result Claude Code saved to a file because it was too large.

    Such a result's body is only a pointer ("Output has been saved to <path>.");
    the file holds the tool's JSON verbatim.
    """
    if not isinstance(body, str):
        return None
    match = SAVED.search(body)
    if match is None:
        return None
    with open(match.group(1), encoding="utf-8") as f:
        return f.read()


def tool_results(path: str) -> Iterator[tuple[str, dict[str, Any], dict[str, Any]]]:
    """Yield ``(tool name, input, parsed JSON result)`` for each LFstats call."""
    uses: dict[str, tuple[str, dict[str, Any]]] = {}
    seen: set[str] = set()
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            content = (rec.get("message") or {}).get("content")
            if not isinstance(content, list):
                continue
            for c in content:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use" and "lfstats" in c.get("name", ""):
                    uses[c["id"]] = (c["name"].split("__")[-1], c["input"])
                elif c.get("type") == "tool_result" and c.get("tool_use_id") in uses:
                    if c["tool_use_id"] in seen:
                        continue
                    seen.add(c["tool_use_id"])
                    body = c.get("content")
                    if isinstance(body, list):
                        body = "".join(
                            x.get("text", "") for x in body if isinstance(x, dict)
                        )
                    try:
                        data = json.loads(saved_result(body) or body)
                    except (TypeError, ValueError):
                        continue
                    name, inp = uses[c["tool_use_id"]]
                    yield name, inp, data


def collect(paths: list[str]) -> dict[str, Any]:
    rows: dict[str, dict[str, dict[str, Any]]] = {"all": {}, "loss": {}}
    baselines: dict[str, dict[str, dict[str, Any]]] = {"all": {}, "loss": {}}
    alltime: dict[str, int] = {}
    homes: dict[str, str] = {}
    for path in paths:
        for name, inp, data in tool_results(path):
            if name == RESOLVE:
                for p in (data.get("data") or {}).get("players") or []:
                    for m in p.get("matches") or []:
                        if p["status"] == "unique" and m.get("home_center"):
                            homes[m["ipl_id"]] = m["home_center"]["name"]
                continue
            scope = inp.get("scope") or {}
            kind = "loss" if scope.get("team_result") == "loss" else "all"
            qualify = inp.get("qualify") or {}
            if (
                name == LEADERBOARD
                and inp.get("group_by_position")
                and (qualify.get("min_games") == QUALIFY_MIN_GAMES)
            ):
                for r in data["data"]:
                    rows[kind].setdefault(f'{r["ipl_id"]}|{r["position"]}', {}).update(
                        r
                    )
            elif (
                name == LEADERBOARD
                and (scope.get("date_range") or {}).get("preset") == "all_time"
            ):
                for r in data["data"]:
                    alltime[r["ipl_id"]] = r["games"]
            elif (
                name == PLAYER_STATS
                and inp.get("breakdown") == ["position"]
                and data["data"].get("baseline")
            ):
                for b in data["data"]["baseline"]["breakdown"]:
                    baselines[kind].setdefault(b["position"], {}).update(b)
    return {"rows": rows, "baselines": baselines, "alltime": alltime, "homes": homes}


def main(argv: list[str]) -> None:
    snapshot = collect(argv[2:])
    with open(argv[1], "w", encoding="utf-8") as f:
        json.dump(snapshot, f)
    players = {k.split("|")[0] for k in snapshot["rows"]["all"]}
    print(
        f"rows {len(snapshot['rows']['all'])}  loss rows {len(snapshot['rows']['loss'])}"
        f"  players {len(players)}  all-time {len(snapshot['alltime'])}"
        f"  home centers {len(snapshot['homes'])}"
    )


if __name__ == "__main__":
    main(sys.argv)
