"""HX-04 — Player head-to-head record (pure Python).

Aggregates the H2H history between two Players. The view assembles a
single flat dict list (``rounds_list``, side-agnostic and pre-attributed
to player_a / player_b) and hands it to the seven public functions below.
This module is **pure**: no Django, no ORM, no RNG, no I/O, no model
imports.

See ``.claude/worktrees/hx-04-seam-contract.md`` for the locked seam.
"""

from typing import Iterable, Mapping, Sequence

_NO_MAP_LABEL: str = "No map (3-zone)"


def compute_round_record(rounds_list: Iterable[Mapping]) -> dict:
    """W/L/T per Round across the unified basket (player_a perspective).

    Returns ``{"wins": int, "losses": int, "ties": int, "n": int}``.
    ``player_a_team_score > player_b_team_score`` → wins; ``<`` → losses;
    ``==`` → ties.
    """
    wins = 0
    losses = 0
    ties = 0
    for r in rounds_list:
        a = r["player_a_team_score"]
        b = r["player_b_team_score"]
        if a > b:
            wins += 1
        elif a < b:
            losses += 1
        else:
            ties += 1
    n = wins + losses + ties
    return {"wins": wins, "losses": losses, "ties": ties, "n": n}


def compute_score_margin(rounds_list: Iterable[Mapping]) -> dict:
    """Mean signed margin ``(player_a_team − player_b_team)`` per Round.

    Returns ``{"mean_margin": float, "n": int}``. Empty input ⇒
    ``{"mean_margin": 0.0, "n": 0}`` (no div-by-zero).
    """
    materialised: Sequence[Mapping] = list(rounds_list)
    n = len(materialised)
    if n == 0:
        return {"mean_margin": 0.0, "n": 0}
    total = 0.0
    for r in materialised:
        total += r["player_a_team_score"] - r["player_b_team_score"]
    return {"mean_margin": total / n, "n": n}


def compute_tag_stats(rounds_list: Iterable[Mapping]) -> dict:
    """Mean / total direct tags between the two Players.

    Returns ``{"avg_tags_a_to_b": float, "avg_tags_b_to_a": float,
    "total_tags_a_to_b": int, "total_tags_b_to_a": int, "n": int}``.
    Empty input ⇒ zeros across the board.
    """
    materialised: Sequence[Mapping] = list(rounds_list)
    n = len(materialised)
    if n == 0:
        return {
            "avg_tags_a_to_b": 0.0,
            "avg_tags_b_to_a": 0.0,
            "total_tags_a_to_b": 0,
            "total_tags_b_to_a": 0,
            "n": 0,
        }
    total_a_to_b = 0
    total_b_to_a = 0
    for r in materialised:
        total_a_to_b += r["tags_a_to_b"]
        total_b_to_a += r["tags_b_to_a"]
    return {
        "avg_tags_a_to_b": total_a_to_b / n,
        "avg_tags_b_to_a": total_b_to_a / n,
        "total_tags_a_to_b": total_a_to_b,
        "total_tags_b_to_a": total_b_to_a,
        "n": n,
    }


def compute_per_role_breakdown(rounds_list: Iterable[Mapping]) -> list[dict]:
    """Per-``role_a`` W/L/T + margin + tag breakdown table.

    One row per ``role_a`` value (player_a's per-Round role) observed.
    Shape ``{"role", "games", "wins", "losses", "ties", "mean_margin",
    "avg_tags_a_to_b", "avg_tags_b_to_a"}``. Sorted by ``games``
    descending; tiebreaker ``role`` ascending. Empty input ⇒ ``[]``.
    """
    buckets: dict[str, dict] = {}
    for r in rounds_list:
        role = r["role_a"]
        bucket = buckets.get(role)
        if bucket is None:
            bucket = {
                "role": role,
                "games": 0,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "_margin_sum": 0,
                "_tags_a_to_b_sum": 0,
                "_tags_b_to_a_sum": 0,
            }
            buckets[role] = bucket
        a = r["player_a_team_score"]
        b = r["player_b_team_score"]
        bucket["games"] += 1
        bucket["_margin_sum"] += a - b
        bucket["_tags_a_to_b_sum"] += r["tags_a_to_b"]
        bucket["_tags_b_to_a_sum"] += r["tags_b_to_a"]
        if a > b:
            bucket["wins"] += 1
        elif a < b:
            bucket["losses"] += 1
        else:
            bucket["ties"] += 1

    out: list[dict] = []
    for bucket in buckets.values():
        games = bucket["games"]
        mean_margin = bucket["_margin_sum"] / games if games else 0.0
        avg_a_to_b = bucket["_tags_a_to_b_sum"] / games if games else 0.0
        avg_b_to_a = bucket["_tags_b_to_a_sum"] / games if games else 0.0
        out.append(
            {
                "role": bucket["role"],
                "games": games,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "ties": bucket["ties"],
                "mean_margin": mean_margin,
                "avg_tags_a_to_b": avg_a_to_b,
                "avg_tags_b_to_a": avg_b_to_a,
            }
        )

    out.sort(key=lambda row: (-row["games"], row["role"]))
    return out


def compute_per_map_breakdown(rounds_list: Iterable[Mapping]) -> list[dict]:
    """Per-``arena_map`` W/L/T + margin table.

    One entry per ``arena_map_id`` observed in ``rounds_list`` (including
    a single entry for ``arena_map_id=None`` labelled ``"No map
    (3-zone)"``).

    Returns ``list[{"arena_map_id": int|None, "arena_map_name": str,
    "games": int, "wins": int, "losses": int, "ties": int,
    "mean_margin": float}]``, sorted by ``games`` descending; tiebreaker
    ``arena_map_id`` ascending with ``None`` last. Empty input ⇒ ``[]``.
    """
    buckets: dict = {}
    for r in rounds_list:
        mid = r["arena_map_id"]
        bucket = buckets.get(mid)
        if bucket is None:
            bucket = {
                "arena_map_id": mid,
                "arena_map_name": (
                    _NO_MAP_LABEL if mid is None else (r["arena_map_name"] or "")
                ),
                "games": 0,
                "wins": 0,
                "losses": 0,
                "ties": 0,
                "_margin_sum": 0,
            }
            buckets[mid] = bucket
        a = r["player_a_team_score"]
        b = r["player_b_team_score"]
        bucket["games"] += 1
        bucket["_margin_sum"] += a - b
        if a > b:
            bucket["wins"] += 1
        elif a < b:
            bucket["losses"] += 1
        else:
            bucket["ties"] += 1

    out: list[dict] = []
    for bucket in buckets.values():
        games = bucket["games"]
        mean_margin = bucket["_margin_sum"] / games if games else 0.0
        out.append(
            {
                "arena_map_id": bucket["arena_map_id"],
                "arena_map_name": bucket["arena_map_name"],
                "games": games,
                "wins": bucket["wins"],
                "losses": bucket["losses"],
                "ties": bucket["ties"],
                "mean_margin": mean_margin,
            }
        )

    def _sort_key(row: dict) -> tuple:
        mid = row["arena_map_id"]
        # games desc; arena_map_id asc with None last.
        return (
            -row["games"],
            0 if mid is not None else 1,
            mid if mid is not None else 0,
        )

    out.sort(key=_sort_key)
    return out


def margin_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — signed margin per Round chronologically.

    Returns ``[[round_idx_1based, signed_margin_int], ...]`` sorted by
    ``(date_played, round_id)`` ascending. ``list[list]`` (not
    ``list[tuple]``) for ``json_script`` serialisation. Empty input ⇒
    ``[]``.
    """
    ordered = sorted(rounds_list, key=lambda r: (r["date_played"], r["round_id"]))
    if not ordered:
        return []
    return [
        [idx + 1, r["player_a_team_score"] - r["player_b_team_score"]]
        for idx, r in enumerate(ordered)
    ]


def cumulative_wl_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — cumulative ``(player_a_wins − player_b_wins)`` Round-level.

    Returns ``[[round_idx_1based, cum_diff], ...]`` sorted by
    ``(date_played, round_id)`` ascending. Ties don't move the running
    diff. Empty input ⇒ ``[]``.
    """
    ordered = sorted(rounds_list, key=lambda r: (r["date_played"], r["round_id"]))
    if not ordered:
        return []
    out: list[list] = []
    running = 0
    for idx, r in enumerate(ordered):
        a = r["player_a_team_score"]
        b = r["player_b_team_score"]
        if a > b:
            running += 1
        elif a < b:
            running -= 1
        # ties: no change
        out.append([idx + 1, running])
    return out


__all__ = [
    "compute_round_record",
    "compute_score_margin",
    "compute_tag_stats",
    "compute_per_role_breakdown",
    "compute_per_map_breakdown",
    "margin_series",
    "cumulative_wl_series",
]
