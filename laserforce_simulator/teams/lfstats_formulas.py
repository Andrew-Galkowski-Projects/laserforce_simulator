"""LFstats -> Player stat formulas (LFS-01, see docs/lfstats-import.md).

Pure module: no Django imports, so the file-loaded ``scripts/lfstats_import/compute.py``
and the calibration harness compute stats the same way. A
``TestNoDjangoImportsLeaked`` subprocess check in ``test_lfstats_import.py`` pins it.

The tunables live in one record, ``ImportConstants``. ``DEFAULT_CONSTANTS`` is what
the import uses, and its ``preferred_role_boost`` is what
``Player.stat_for_simulation`` multiplies a preferred-Role stat by.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable
from dataclasses import dataclass
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
# Floors keep log ratios finite for zero counts, zero/negative MVP and tiny ratios.
COUNT_FLOOR, MVP_FLOOR, RATIO_FLOOR = 0.25, 0.5, 0.05
SECOND_ROLE_SHARE = 0.2
# Uptime is a fraction near 1, so its log ratio uses a fixed, wider scale.
UPTIME_SCALE = 100.0

Row = dict[str, Any]


@dataclass(frozen=True)
class ImportConstants:
    """The import's tunables. LFS-01's Training search chose the defaults
    (docs/lfstats-import.md "Calibration results"): only ``preferred_role_boost``
    moved from the original formulas, 1.2 -> 1.0."""

    # The log_part scale of every ratio stat (uptime keeps UPTIME_SCALE).
    log_scale: float = 25.0
    # accuracy = clamp(accuracy_offset + accuracy_scale * avg_accuracy).
    accuracy_offset: float = 0.0
    accuracy_scale: float = 100.0
    # survival weight on the inverted times-hit ratio; the rest is on
    # 100 * (1 - elimination_rate).
    survival_times_hit_weight: float = 0.5
    # What Player.stat_for_simulation multiplies a preferred-Role stat by. 1.0
    # (no bonus) fit the Training set best; the original value was 1.2.
    preferred_role_boost: float = 1.0


DEFAULT_CONSTANTS = ImportConstants()


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
    constants: ImportConstants = DEFAULT_CONSTANTS,
) -> dict[str, int]:
    """The 19 stats for one player from their per-position LFstats rows.

    Called with one position's row, it gives that position's stats.
    """
    scale = constants.log_scale

    def b(pos: str, metric: str) -> float | None:
        return baselines[pos].get(metric)

    def overall(metric: str) -> float:
        # Every per-position metric is a per-game mean, so the games-weighted mean
        # of the position rows is the player's overall value.
        return pos_avg(rows, lambda r: r.get(metric)) or 0.0

    def ratio(
        metric: str, floor: float, scale: float = scale
    ) -> Callable[[Row], float | None]:
        return lambda r: log_part(r.get(metric), b(r["position"], metric), floor, scale)

    mvp_part = ratio("avg_mvp", MVP_FLOOR)
    uptime_part = ratio("avg_uptime_pct", RATIO_FLOOR, scale=UPTIME_SCALE)
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
                + scale
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
    alive = 100 * (1 - overall("elimination_rate"))
    w = constants.survival_times_hit_weight
    survival = alive if times_hit is None else w * times_hit + (1 - w) * alive

    values: dict[str, float | None] = {
        "player_awareness": clamp(
            50
            + scale * math.log2(max(overall("avg_hit_diff"), RATIO_FLOOR))
            + nukes_bonus
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
                scale,
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
        "accuracy": clamp(
            constants.accuracy_offset
            + constants.accuracy_scale * overall("avg_accuracy")
        ),
        "survival": survival,
        "special_usage": pos_avg(
            rows,
            lambda r: log_part(
                r.get(SPECIAL[r["position"]]),
                b(r["position"], SPECIAL[r["position"]]),
                COUNT_FLOOR,
                scale,
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
