"""RV-01 round comparison — pure aggregation.

The view materialises a per-PlayerRoundState comparison-row dict (16 keys:
``player_id`` / ``name`` / ``role`` / ``team_color`` plus the 12
``COMPARE_STAT_KEYS`` values) and hands two ``{player_id: row}`` maps
plus a list of ``(timestamp, points)`` tuples per team to the functions
below.

This module is **pure**: no Django, no ORM, no RNG, no I/O.

See ``.claude/worktrees/round-analytics-seam-contract.md`` for the locked
seam.
"""

from typing import Iterable, Mapping

# Ordered stat keys for the delta-table columns. ``mvp`` reads the
# ``PlayerRoundState.get_mvp`` property (float); ``accuracy`` reads
# ``get_accuracy`` (int percent, divide-by-zero guarded); every other
# key is a same-named IntegerField.
COMPARE_STAT_KEYS: tuple[str, ...] = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)

# Stat keys that map directly to same-named PlayerRoundState IntegerFields.
COMPARE_FIELD_STAT_KEYS: tuple[str, ...] = (
    "points_scored",
    "tags_made",
    "times_tagged",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)


def stat_values(comparison_row: Mapping) -> dict:
    """Extract the 12 ordered comparison stats from one comparison-row dict.

    ``comparison_row`` is the view-built per-PlayerRoundState dict (16 keys).
    Returns the 12-key subset in ``COMPARE_STAT_KEYS`` order.
    """
    return {key: comparison_row[key] for key in COMPARE_STAT_KEYS}


def player_stat_deltas(
    rows_a: Iterable[Mapping], rows_b: Iterable[Mapping]
) -> list[dict]:
    """Per-player stat-delta rows, paired by ``player_id``.

    ``rows_a`` / ``rows_b`` are the view-built per-PlayerRoundState
    comparison-row dicts (16 keys each) for round A and round B,
    already filtered to shared teams.

    A player present in only one round yields a row whose missing side's
    ``role_*`` / ``side_*`` and per-stat ``a``/``b``/``delta`` are
    ``None``. When both sides exist, ``delta = b - a``. Rows are ordered
    by name ascending.
    """
    by_id_a: dict[int, Mapping] = {row["player_id"]: row for row in rows_a}
    by_id_b: dict[int, Mapping] = {row["player_id"]: row for row in rows_b}

    out: list[dict] = []
    for player_id in set(by_id_a) | set(by_id_b):
        row_a = by_id_a.get(player_id)
        row_b = by_id_b.get(player_id)
        name = (row_a or row_b)["name"]

        values_a = stat_values(row_a) if row_a is not None else None
        values_b = stat_values(row_b) if row_b is not None else None

        stats: dict = {}
        for key in COMPARE_STAT_KEYS:
            a_val = values_a[key] if values_a is not None else None
            b_val = values_b[key] if values_b is not None else None
            delta = (
                (b_val - a_val) if (a_val is not None and b_val is not None) else None
            )
            stats[key] = {"a": a_val, "b": b_val, "delta": delta}

        out.append(
            {
                "player_id": player_id,
                "name": name,
                "role_a": row_a["role"] if row_a is not None else None,
                "role_b": row_b["role"] if row_b is not None else None,
                "side_a": row_a["team_color"] if row_a is not None else None,
                "side_b": row_b["team_color"] if row_b is not None else None,
                "stats": stats,
                # Template-friendly ordered view of ``stats`` (Django templates
                # cannot do dynamic dict lookup by a variable key).
                "cells": [stats[key] for key in COMPARE_STAT_KEYS],
            }
        )

    out.sort(key=lambda row: row["name"])
    return out


def cumulative_team_points(events: Iterable[tuple[int, int | None]]) -> list[list]:
    """Cumulative-points series from per-team timestamped point events.

    Input is an iterable of ``(timestamp, points_awarded)`` pairs already
    ordered by timestamp; ``points_awarded`` of ``None`` is treated as ``0``.
    Returns ``[[tick, cumulative_points], ...]``; an empty input yields ``[]``.
    """
    series: list[list] = []
    cumulative = 0
    for timestamp, points_awarded in events:
        cumulative += points_awarded or 0
        series.append([timestamp, cumulative])
    return series
