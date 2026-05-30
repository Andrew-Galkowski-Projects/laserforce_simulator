"""LG-01z-m — League Leaders pure aggregator.

Builds four top-N leaderboards from a flat list of per-``PlayerRoundState``
dicts, one entry per Round a player appeared in. Each player is aggregated
across all their Rounds, then the four boards are ranked independently.

Public surface:

* ``compute_leaderboards(player_rounds, *, limit=10) -> dict[str,
  list[LeaderRow]]`` — returns four ranked top-``limit`` lists keyed
  ``"avg_tags"`` / ``"avg_score"`` / ``"fewest_tagged"`` / ``"tag_ratio"``.

Verb definitions:

* ``avg_tags``      — ``mean(tags_made)`` per player, **descending**.
* ``avg_score``     — ``mean(points_scored)`` per player, **descending**.
* ``fewest_tagged`` — ``mean(times_tagged)`` per player, **ascending**
  (least-tagged leads).
* ``tag_ratio``     — ``sum(tags_made) / max(sum(times_tagged), 1)`` per
  player, **descending** (canonical CONTEXT.md sum/sum form, NOT a mean
  of per-row ratios; the ``max(..., 1)`` denominator avoids div-by-zero).

Reuses the LG-01c ``LeaderRow`` dataclass for each row (importing the
frozen dataclass does not load Django).

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``, ``collections`` — plus the
``LeaderRow`` dataclass from ``matches.season_dashboard`` (a pure
dataclass import, no Django). NO Django, no ORM, no ``random``, no
``datetime``, no I/O, no logging. Enforced by the
``TestNoDjangoImportsLeaked`` subprocess check.
"""

from collections import defaultdict
from typing import Callable

from matches.season_dashboard import LeaderRow

__all__ = ["compute_leaderboards"]


def _rank(
    grouped: dict[int, list[dict]],
    *,
    value_fn: Callable[[list[dict], int], float],
    ascending: bool,
    limit: int,
) -> list[LeaderRow]:
    """Rank players by ``value_fn`` and return the top-``limit`` rows.

    Tiebreak ladder (deterministic): primary value (``ascending`` controls
    direction), then ``games_played`` descending, then ``player_id``
    ascending. ``rank`` is 1-based dense in iteration order.
    """
    pre: list[tuple[int, float, int, dict]] = []
    for player_id, rows in grouped.items():
        games_played = len(rows)
        value = float(value_fn(rows, games_played))
        pre.append((player_id, value, games_played, rows[-1]))

    # value direction depends on `ascending`; games_played always desc;
    # player_id always asc.
    value_sign = 1 if ascending else -1
    pre.sort(key=lambda t: (value_sign * t[1], -t[2], t[0]))

    leaders: list[LeaderRow] = []
    for index, (player_id, value, games_played, last_row) in enumerate(pre[:limit]):
        leaders.append(
            LeaderRow(
                player_id=player_id,
                player_name=last_row["player_name"],
                role=last_row["role"],
                team_id=last_row["team_id"],
                team_name=last_row["team_name"],
                value=value,
                games_played=games_played,
                rank=index + 1,
            )
        )
    return leaders


def compute_leaderboards(
    player_rounds: list[dict],
    *,
    limit: int = 10,
) -> dict[str, list[LeaderRow]]:
    """Aggregate per-Round dicts into four ranked top-``limit`` boards.

    Args:
        player_rounds: list of dicts, one per ``PlayerRoundState`` row across
            the Season's completed Rounds. Each dict carries the 8 keys
            ``player_id``, ``player_name``, ``role``, ``team_id``,
            ``team_name``, ``tags_made``, ``times_tagged``,
            ``points_scored``. Players are grouped by ``player_id``;
            "last row wins" for the displayed name / role / team (the view
            passes rows in ``id`` ascending order, so "last" is the most
            recent Round).
        limit: how many rows each board returns after sorting. Default 10.

    Returns:
        A dict with exactly four keys — ``"avg_tags"``, ``"avg_score"``,
        ``"fewest_tagged"``, ``"tag_ratio"`` — each mapping to a list of
        up to ``limit`` ``LeaderRow`` instances. Empty input ⇒ four empty
        lists.
    """
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in player_rounds:
        grouped[row["player_id"]].append(row)

    def avg_tags(rows: list[dict], games: int) -> float:
        return sum(r["tags_made"] for r in rows) / games

    def avg_score(rows: list[dict], games: int) -> float:
        return sum(r["points_scored"] for r in rows) / games

    def avg_tagged(rows: list[dict], games: int) -> float:
        return sum(r["times_tagged"] for r in rows) / games

    def tag_ratio(rows: list[dict], games: int) -> float:
        total_tags = sum(r["tags_made"] for r in rows)
        total_tagged = sum(r["times_tagged"] for r in rows)
        return total_tags / max(total_tagged, 1)

    return {
        "avg_tags": _rank(grouped, value_fn=avg_tags, ascending=False, limit=limit),
        "avg_score": _rank(grouped, value_fn=avg_score, ascending=False, limit=limit),
        "fewest_tagged": _rank(
            grouped, value_fn=avg_tagged, ascending=True, limit=limit
        ),
        "tag_ratio": _rank(grouped, value_fn=tag_ratio, ascending=False, limit=limit),
    }
