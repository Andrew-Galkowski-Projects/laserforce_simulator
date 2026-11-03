"""Build a roster-import CSV from an LFstats snapshot (see docs/lfstats-import.md).

Usage:
    python scripts/lfstats_import/compute.py <snapshot.json> <out.csv>

The snapshot is the JSON written by ``extract.py``: per-(player, position) LFstats
rows for all games and for losses, per-position field baselines, all-time game
counts and home centers. Every stat formula here is recorded, with its reasoning,
in docs/lfstats-import.md; change both together.

The stat formulas live in ``teams/lfstats_formulas.py`` (pure, no Django), shared
with the calibration harness; this script imports them from the nested Django
project dir.

Teams are built per home center: as many full 6-player rosters as the center can
field (main role first, then second preferred role, then single-role scouts in any
slot), and every remaining player goes to the Free Agents pool.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from collections.abc import Callable
from pathlib import Path
from typing import Any

_PROJECT_DIR = str(Path(__file__).resolve().parents[2] / "laserforce_simulator")
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)

# Needs the sys.path entry above; the unused names are re-exported for callers.
from teams.lfstats_formulas import (  # noqa: E402, F401
    COUNT_FLOOR,
    MVP_FLOOR,
    RATIO_FLOOR,
    SECOND_ROLE_SHARE,
    SPECIAL,
    STAT_COLUMNS,
    SUPPORT,
    clamp,
    log_part,
    mean,
    pos_avg,
    preferred_roles,
    stats_for,
)

REQUIRED_COLUMNS: tuple[str, ...] = (
    "team",
    "name",
    "role",
    "age",
    "started_playing_age",
    "total_games",
    "home_site",
    "height",
)
HEADER: tuple[str, ...] = (*REQUIRED_COLUMNS, "preferred_roles", *STAT_COLUMNS)
SLOTS: dict[str, int] = {"commander": 1, "heavy": 1, "scout": 2, "medic": 1, "ammo": 1}
FREE_AGENTS_TEAM_NAME = "Free Agents"
# LFstats has no ages; fixed placeholders are easy to spot and correct by hand.
AGE, STARTED_AGE = 25, 18
# A player needs this many games in total, over all positions, to be imported.
MIN_GAMES = 26

Row = dict[str, Any]
Player = dict[str, Any]


def prefs_only(p: Player) -> list[str]:
    return p["prefs"]


def flex_scouts(p: Player) -> list[str]:
    """Single-role scouts may take any slot (scout first); others keep their prefs."""
    if p["prefs"] == ["scout"]:
        return ["scout", *(r for r in SLOTS if r != "scout")]
    return p["prefs"]


def match_roles(
    group: list[Player],
    capacity: dict[str, int],
    allowed: Callable[[Player], list[str]],
) -> dict[str, str]:
    """Maximum assignment of players to role capacities; returns ``{ipl: role}``.

    Kuhn's augmenting paths over role "seats". Earlier players in ``group`` are
    tried first and each tries its roles in ``allowed(p)`` order.
    """
    seats = [role for role, cap in capacity.items() for _ in range(cap)]
    owner: list[Player | None] = [None] * len(seats)

    def augment(p: Player, seen: set[int]) -> bool:
        for s, role in enumerate(seats):
            if role in allowed(p) and s not in seen:
                seen.add(s)
                current = owner[s]
                if current is None or augment(current, seen):
                    owner[s] = p
                    return True
        return False

    for p in group:
        augment(p, set())
    return {p["ipl"]: seats[s] for s, p in enumerate(owner) if p is not None}


def full_team_assignment(
    group: list[Player], allowed: Callable[[Player], list[str]] = prefs_only
) -> tuple[int, dict[str, str]]:
    """Largest ``k`` with ``k`` full rosters fillable from ``allowed`` roles.

    Main roles are matched first; other allowed roles only fill what is left.
    Returns ``(k, {ipl: role})`` for the players placed.
    """
    for k in range(len(group) // 6, 0, -1):
        capacity = {role: n * k for role, n in SLOTS.items()}
        placed = match_roles(group, capacity, lambda p: p["prefs"][:1])
        if len(placed) < 6 * k:
            left = dict(capacity)
            for role in placed.values():
                left[role] -= 1
            rest = [p for p in group if p["ipl"] not in placed]
            placed.update(match_roles(rest, left, allowed))
        if len(placed) == 6 * k:
            return k, placed
        # An off-role player may need to displace a main-role one; retry jointly.
        joint = match_roles(group, capacity, allowed)
        if len(joint) == 6 * k:
            return k, joint
    return 0, {}


def assign_teams(players: list[Player]) -> None:
    """Set ``team``, ``role`` and ``csv_name`` on every player, in place."""
    by_home: dict[str, list[Player]] = defaultdict(list)
    for p in players:
        by_home[p["home"]].append(p)
    for home, group in by_home.items():
        group.sort(key=lambda p: (-p["games_2y"], p["name"]))
        n_teams = 0
        assigned: set[str] = set()
        for allowed in (prefs_only, flex_scouts):
            rest = [p for p in group if p["ipl"] not in assigned]
            full, roles = full_team_assignment(rest, allowed)
            # Deal each role out by games played, so the pass's first team gets the
            # busiest commander, the two busiest scouts, and so on.
            by_role: dict[str, list[Player]] = defaultdict(list)
            for p in rest:
                if p["ipl"] in roles:
                    by_role[roles[p["ipl"]]].append(p)
            for role, members in by_role.items():
                for j, p in enumerate(members):
                    p["role"], p["team"] = (
                        role,
                        f"{home} {n_teams + j // SLOTS[role] + 1}",
                    )
            n_teams += full
            assigned |= set(roles)
        for p in group:
            if p["ipl"] not in assigned:
                p["role"], p["team"] = p["prefs"][0], FREE_AGENTS_TEAM_NAME
    # The importer rejects a repeated (team, name); different players can share a
    # callsign, so later repeats get their IPL id appended.
    seen: set[tuple[str, str]] = set()
    for p in sorted(players, key=lambda p: (-p["games_2y"], p["ipl"])):
        key = (p["team"], p["name"])
        p["csv_name"] = p["name"] if key not in seen else f'{p["name"]} ({p["ipl"]})'
        seen.add(key)


def build_players(data: dict[str, Any]) -> list[Player]:
    by_player: dict[str, list[Row]] = defaultdict(list)
    for r in data["rows"]["all"].values():
        by_player[r["ipl_id"]].append(r)
    loss_by_player: dict[str, list[Row]] = defaultdict(list)
    for r in data["rows"]["loss"].values():
        loss_by_player[r["ipl_id"]].append(r)
    baselines, loss_baselines = data["baselines"]["all"], data["baselines"]["loss"]
    players = []
    for ipl, rows in by_player.items():
        games_2y = sum(r["games"] for r in rows)
        if games_2y < MIN_GAMES:
            continue
        players.append(
            {
                "ipl": ipl,
                "name": rows[0]["callsign"].strip(),
                "prefs": preferred_roles(rows),
                "games_2y": games_2y,
                "total_games": data["alltime"].get(ipl, games_2y),
                "home": data["homes"].get(ipl, "Unknown"),
                "stats": stats_for(
                    rows, loss_by_player.get(ipl, []), baselines, loss_baselines
                ),
            }
        )
    assign_teams(players)
    return players


def write_csv(players: list[Player], path: str) -> None:
    def order(p: Player) -> tuple[bool, str, int, str]:
        return (
            p["team"] == FREE_AGENTS_TEAM_NAME,
            p["team"],
            list(SLOTS).index(p["role"]),
            p["csv_name"],
        )

    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(HEADER)
        for p in sorted(players, key=order):
            w.writerow(
                [
                    p["team"],
                    p["csv_name"],
                    p["role"],
                    AGE,
                    STARTED_AGE,
                    p["total_games"],
                    p["home"],
                    "",
                    ",".join(p["prefs"]),
                    *(p["stats"][s] for s in STAT_COLUMNS),
                ]
            )


def main(argv: list[str]) -> None:
    with open(argv[1], encoding="utf-8") as f:
        data = json.load(f)
    players = build_players(data)
    write_csv(players, argv[2])
    teams = {p["team"] for p in players} - {FREE_AGENTS_TEAM_NAME}
    pool = sum(p["team"] == FREE_AGENTS_TEAM_NAME for p in players)
    print(f"players {len(players)}  full teams {len(teams)}  free agents {pool}")


if __name__ == "__main__":
    main(sys.argv)
