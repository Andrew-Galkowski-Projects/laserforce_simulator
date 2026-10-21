"""Build a roster-import CSV from an LFstats snapshot (see docs/lfstats-import.md).

Usage:
    python scripts/lfstats_import/compute.py <snapshot.json> <out.csv>

The snapshot is the JSON written by ``extract.py``: per-(player, position) LFstats
rows for all games and for losses, per-position field baselines, all-time game
counts and home centers. Every stat formula here is recorded, with its reasoning,
in docs/lfstats-import.md; change both together.

Teams are built per home center: as many full 6-player rosters as the center can
field (main role first, then second preferred role, then single-role scouts in any
slot), and every remaining player goes to the Free Agents pool.
"""

from __future__ import annotations

import csv
import json
import math
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from typing import Any

STAT_COLUMNS: tuple[str, ...] = (
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    "decision_making",
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    "communication",
    "teamwork",
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
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
# The position's signature special, used by special_usage. Ammo/medic team boosts
# are only in per-game data, so resupplies stand in for them.
SPECIAL: dict[str, str] = {
    "commander": "avg_nukes_detonated",
    "heavy": "avg_missiles_hit",
    "scout": "avg_rapid_fire",
    "ammo": "avg_resupplies_given",
    "medic": "avg_resupplies_given",
}
SUPPORT: tuple[str, ...] = ("ammo", "medic")
# LFstats has no ages; fixed placeholders are easy to spot and correct by hand.
AGE, STARTED_AGE = 25, 18
# Floors keep log ratios finite for zero counts, zero/negative MVP and tiny ratios.
COUNT_FLOOR, MVP_FLOOR, RATIO_FLOOR = 0.25, 0.5, 0.05
SECOND_ROLE_SHARE = 0.2

Row = dict[str, Any]
Player = dict[str, Any]


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, x))


def log_part(
    x: float | None, base: float | None, floor: float, scale: float = 25.0
) -> float | None:
    """``50 + scale * log2(max(x, floor) / base)``, clamped; None without data."""
    if x is None or not base:
        return None
    return clamp(50 + scale * math.log2(max(x, floor) / base))


def pos_avg(
    rows: Iterable[Row],
    fn: Callable[[Row], float | None],
    positions: tuple[str, ...] | None = None,
) -> float | None:
    """Games-weighted mean of ``fn(row)`` over positions; None when nothing applies."""
    total = weight = 0.0
    for r in rows:
        if positions and r["position"] not in positions:
            continue
        v = fn(r)
        if v is None:
            continue
        total += r["games"] * v
        weight += r["games"]
    return total / weight if weight else None


def mean(*parts: float | None) -> float | None:
    vals = [p for p in parts if p is not None]
    return sum(vals) / len(vals) if vals else None


def stats_for(
    rows: list[Row],
    loss_rows: list[Row],
    baselines: dict[str, Row],
    loss_baselines: dict[str, Row],
) -> dict[str, int]:
    """The 19 stats for one player from their per-position LFstats rows."""

    def b(pos: str, metric: str) -> float | None:
        return baselines[pos].get(metric)

    def overall(metric: str) -> float:
        # Every per-position metric is a per-game mean, so the games-weighted mean
        # of the position rows is the player's overall value.
        return pos_avg(rows, lambda r: r.get(metric)) or 0.0

    def ratio(
        metric: str, floor: float, scale: float = 25.0
    ) -> Callable[[Row], float | None]:
        return lambda r: log_part(r.get(metric), b(r["position"], metric), floor, scale)

    mvp_part = ratio("avg_mvp", MVP_FLOOR)
    uptime_part = ratio("avg_uptime_pct", RATIO_FLOOR, scale=100.0)
    assists_part = ratio("avg_assists", COUNT_FLOOR)
    resup_part = ratio("avg_resupplies_given", COUNT_FLOOR)

    # Fewer tags taken than the position average scores higher, so the ratio is inverted.
    times_hit = pos_avg(
        rows,
        lambda r: (
            None
            if r.get("avg_times_hit") is None
            else clamp(
                50
                + 25
                * math.log2(
                    b(r["position"], "avg_times_hit")
                    / max(r["avg_times_hit"], COUNT_FLOOR)
                )
            )
        ),
    )
    nukes_bonus = (
        pos_avg(
            rows,
            lambda r: 10
            * clamp(
                (r["avg_nukes_canceled"] - b(r["position"], "avg_nukes_canceled"))
                / 0.06,
                0,
                1,
            ),
        )
        or 0.0
    )
    results = clamp(100 * overall("win_rate") - 20 * overall("avg_penalties"))

    values: dict[str, float | None] = {
        "player_awareness": clamp(
            50 + 25 * math.log2(max(overall("avg_hit_diff"), RATIO_FLOOR)) + nukes_bonus
        ),
        "game_awareness": pos_avg(rows, mvp_part),
        "resource_awareness": 50,
        "decision_making": mean(
            results, pos_avg(rows, ratio("avg_shots_hit", COUNT_FLOOR))
        ),
        "positioning": mean(
            pos_avg(rows, uptime_part),
            pos_avg(rows, ratio("avg_hit_diff", RATIO_FLOOR)),
        ),
        "stamina": 50,
        "speed": 50,
        "flexibility": 50,
        "adaptability": pos_avg(
            loss_rows,
            lambda r: log_part(
                r.get("avg_mvp"),
                loss_baselines[r["position"]].get("avg_mvp"),
                MVP_FLOOR,
            ),
        ),
        "communication": pos_avg(rows, assists_part),
        "teamwork": pos_avg(
            rows,
            lambda r: resup_part(r) if r["position"] in SUPPORT else assists_part(r),
        ),
        "Offensive_synergy": pos_avg(
            rows,
            lambda r: mean(
                ratio("avg_medic_hits", COUNT_FLOOR)(r),
                ratio("avg_eliminations", COUNT_FLOOR)(r),
            ),
        ),
        "defensive_synergy": pos_avg(
            rows, mvp_part, positions=("ammo", "medic", "heavy")
        ),
        "midfield_synergy": pos_avg(rows, mvp_part, positions=("heavy", "scout")),
        "resupply_synergy": pos_avg(
            rows, lambda r: resup_part(r) if r["position"] in SUPPORT else 50.0
        ),
        "resupply_efficiency": pos_avg(rows, uptime_part),
        "accuracy": overall("avg_accuracy") * 100,
        "survival": mean(times_hit, 100 * (1 - overall("elimination_rate"))),
        "special_usage": pos_avg(
            rows,
            lambda r: log_part(
                r.get(SPECIAL[r["position"]]),
                b(r["position"], SPECIAL[r["position"]]),
                COUNT_FLOOR,
            ),
        ),
    }
    return {k: int(round(clamp(50 if v is None else v))) for k, v in values.items()}


def preferred_roles(rows: list[Row]) -> list[str]:
    """Most-played position, plus the second if it has at least 20% of the games."""
    ordered = sorted(rows, key=lambda r: -r["games"])
    total = sum(r["games"] for r in rows)
    roles = [ordered[0]["position"]]
    if len(ordered) > 1 and ordered[1]["games"] >= SECOND_ROLE_SHARE * total:
        roles.append(ordered[1]["position"])
    return roles


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
