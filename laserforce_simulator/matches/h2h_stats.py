"""HX-03 — Head-to-head record (pure Python).

Aggregates the H2H history between two Teams. The view assembles three
flat dict lists (``matches_list`` / ``rounds_list`` / ``player_rounds_list``,
side-agnostic and pre-attributed to team_a / team_b) and hands them to
the eight public functions below. This module is **pure**: no Django,
no ORM, no RNG, no I/O, no model imports.

See ``.claude/worktrees/hx-03-seam-contract.md`` for the locked seam.
"""

from typing import Iterable, Mapping, Sequence

_NO_MAP_LABEL: str = "No map (3-zone)"


def compute_match_record(
    matches_list: Iterable[Mapping], team_a_id: int, team_b_id: int
) -> dict:
    """W/L/T over H2H Matches.

    Returns ``{"wins": int, "losses": int, "ties": int, "n": int}``.
    ``winner_team_id == team_a_id`` → wins; ``== team_b_id`` → losses;
    ``None`` → ties. A ``winner_team_id`` that matches neither
    (legacy/corrupt) is **defensively** counted as a tie.
    """
    wins = 0
    losses = 0
    ties = 0
    for m in matches_list:
        winner = m["winner_team_id"]
        if winner is None:
            ties += 1
        elif winner == team_a_id:
            wins += 1
        elif winner == team_b_id:
            losses += 1
        else:
            ties += 1
    n = wins + losses + ties
    return {"wins": wins, "losses": losses, "ties": ties, "n": n}


def compute_round_record(rounds_list: Iterable[Mapping]) -> dict:
    """W/L/T per Round across the unified basket.

    Returns ``{"wins": int, "losses": int, "ties": int, "n": int}``.
    ``team_a_score > team_b_score`` → wins; ``<`` → losses; ``==`` → ties.
    """
    wins = 0
    losses = 0
    ties = 0
    for r in rounds_list:
        a = r["team_a_score"]
        b = r["team_b_score"]
        if a > b:
            wins += 1
        elif a < b:
            losses += 1
        else:
            ties += 1
    n = wins + losses + ties
    return {"wins": wins, "losses": losses, "ties": ties, "n": n}


def compute_score_margin(rounds_list: Iterable[Mapping]) -> dict:
    """Mean signed margin ``(team_a − team_b)`` per Round.

    Returns ``{"mean_margin": float, "n": int}``. Empty input ⇒
    ``{"mean_margin": 0.0, "n": 0}`` (no div-by-zero).
    """
    materialised: Sequence[Mapping] = list(rounds_list)
    n = len(materialised)
    if n == 0:
        return {"mean_margin": 0.0, "n": 0}
    total = 0
    for r in materialised:
        total += r["team_a_score"] - r["team_b_score"]
    return {"mean_margin": total / n, "n": n}


def compute_avg_survivors(rounds_list: Iterable[Mapping]) -> dict:
    """Per-team mean survivors per Round.

    Returns ``{"team_a_avg": float, "team_b_avg": float, "n": int}``.
    Empty input ⇒ ``{"team_a_avg": 0.0, "team_b_avg": 0.0, "n": 0}``.
    """
    materialised: Sequence[Mapping] = list(rounds_list)
    n = len(materialised)
    if n == 0:
        return {"team_a_avg": 0.0, "team_b_avg": 0.0, "n": 0}
    sum_a = 0
    sum_b = 0
    for r in materialised:
        sum_a += r["team_a_survivors"]
        sum_b += r["team_b_survivors"]
    return {"team_a_avg": sum_a / n, "team_b_avg": sum_b / n, "n": n}


def top_impactful_per_team(
    player_rounds_list: Iterable[Mapping], team_a_id: int, team_b_id: int
) -> dict:
    """Top cumulative-MVP player per team.

    Returns ``{"team_a": <dict|None>, "team_b": <dict|None>}`` where the
    inner dict shape is ``{"player_id": int, "name": str,
    "mvp_total": float, "games": int}``. ``None`` when that team has no
    rows in ``player_rounds_list``.

    Tiebreaker: highest ``mvp_total`` wins; equal totals → lower
    ``player_id`` wins (deterministic).
    """
    # Aggregate per (team_id, player_id) → cumulative mvp + name + rounds set.
    per_team: dict[int, dict[int, dict]] = {team_a_id: {}, team_b_id: {}}
    for pr in player_rounds_list:
        tid = pr["team_id"]
        if tid not in per_team:
            # Defensive: only attribute rows that belong to one of the two teams.
            continue
        pid = pr["player_id"]
        bucket = per_team[tid].setdefault(
            pid,
            {
                "player_id": pid,
                "name": pr["player_name"],
                "mvp_total": 0.0,
                "_rounds": set(),
            },
        )
        bucket["mvp_total"] += pr["mvp"]
        bucket["_rounds"].add(pr["round_id"])
        # Keep the first-seen name; a player can't change name across rounds.

    def _pick(team_id: int) -> dict | None:
        candidates = per_team[team_id]
        if not candidates:
            return None
        # Highest mvp_total first; equal totals → lower player_id first.
        best = max(
            candidates.values(),
            key=lambda b: (b["mvp_total"], -b["player_id"]),
        )
        return {
            "player_id": best["player_id"],
            "name": best["name"],
            "mvp_total": best["mvp_total"],
            "games": len(best["_rounds"]),
        }

    return {"team_a": _pick(team_a_id), "team_b": _pick(team_b_id)}


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
    buckets: dict[int | None, dict] = {}
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
        a = r["team_a_score"]
        b = r["team_b_score"]
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
        [idx + 1, r["team_a_score"] - r["team_b_score"]]
        for idx, r in enumerate(ordered)
    ]


def cumulative_wl_series(rounds_list: Iterable[Mapping]) -> list[list]:
    """Chart data — cumulative ``(team_a_wins − team_b_wins)`` Round-level.

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
        a = r["team_a_score"]
        b = r["team_b_score"]
        if a > b:
            running += 1
        elif a < b:
            running -= 1
        # ties: no change
        out.append([idx + 1, running])
    return out
