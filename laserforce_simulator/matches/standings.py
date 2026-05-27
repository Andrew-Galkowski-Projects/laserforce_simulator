"""LG-01 Standings aggregator — pure deterministic standings table.

Public surface:

* ``StandingsRow`` — frozen dataclass with the 9 fields a Standings row
  carries (``team_id``, ``matches_played``, ``wins``, ``losses``,
  ``ties``, ``league_points``, ``round_wins``, ``total_score``,
  ``rank``).
* ``compute_standings(completed_matches, enrolled_teams)`` — aggregates
  the per-Match data into a ranked Standings list. Sort order:
  ``(league_points desc, round_wins desc, total_score desc,
  team_name asc)``; rank is populated 1-based and dense.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``, ``collections``. No Django, no ``random``,
no ``datetime``, no I/O, no logging. Enforced by the
``TestNoDjangoImportsLeaked`` subprocess check.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import NamedTuple


class _PreRow(NamedTuple):
    """Internal pre-rank tuple for the standings sort phase.

    Defined as a NamedTuple (not a plain tuple) so the sort key reads
    by attribute rather than positional indexing; never escapes this
    module.
    """

    team_id: int
    matches_played: int
    wins: int
    losses: int
    ties: int
    league_points: int
    round_wins: int
    total_score: int


@dataclass(frozen=True)
class StandingsRow:
    """One Standings row — a single team's aggregated record."""

    team_id: int
    matches_played: int
    wins: int
    losses: int
    ties: int
    league_points: int
    round_wins: int
    total_score: int
    rank: int


def compute_standings(
    completed_matches: list[dict],
    enrolled_teams: list[tuple[int, str]],
) -> list[StandingsRow]:
    """Aggregate per-Match outcomes into a ranked Standings table.

    Args:
        completed_matches: list of dicts, each describing one completed
            (``is_completed=True``) Match in the Season. The 8 keys are
            ``match_id``, ``team_red_id``, ``team_blue_id``,
            ``winner_team_id`` (``int | None`` — ``None`` = tie),
            ``red_rounds_won``, ``blue_rounds_won``,
            ``red_total_points``, ``blue_total_points``.
        enrolled_teams: list of ``(team_id, team_name)`` tuples — every
            team enrolled in the Season. Teams with no matches get a
            zero-filled row.

    Returns:
        list of ``StandingsRow`` sorted by ``(league_points desc,
        round_wins desc, total_score desc, team_name asc)``; ``rank``
        is populated 1-based and dense.
    """
    # Accumulator keyed by team_id. Each entry tracks the 7 raw counters
    # we need before the league_points / sort / rank pass.
    counters: dict[int, dict[str, int]] = defaultdict(
        lambda: {
            "matches_played": 0,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "round_wins": 0,
            "total_score": 0,
        }
    )

    # Seed the accumulator with every enrolled team so teams with no
    # matches still get a zero row in the output.
    for team_id, _team_name in enrolled_teams:
        _ = counters[team_id]  # touch to materialise via defaultdict

    # Walk each completed Match and attribute W/L/T + round_wins +
    # total_score to both teams.
    for match in completed_matches:
        red_id = match["team_red_id"]
        blue_id = match["team_blue_id"]
        winner = match["winner_team_id"]
        red_rounds_won = match["red_rounds_won"]
        blue_rounds_won = match["blue_rounds_won"]
        red_total_points = match["red_total_points"]
        blue_total_points = match["blue_total_points"]

        red = counters[red_id]
        blue = counters[blue_id]

        red["matches_played"] += 1
        blue["matches_played"] += 1
        red["round_wins"] += red_rounds_won
        blue["round_wins"] += blue_rounds_won
        red["total_score"] += red_total_points
        blue["total_score"] += blue_total_points

        if winner is None:
            # Explicit tie.
            red["ties"] += 1
            blue["ties"] += 1
        elif winner == red_id:
            red["wins"] += 1
            blue["losses"] += 1
        elif winner == blue_id:
            blue["wins"] += 1
            red["losses"] += 1
        else:
            # Defensive: unknown winner id (legacy / corrupt data) —
            # count as tie for both, mirroring the HX-03
            # ``compute_match_record`` defensive behaviour.
            red["ties"] += 1
            blue["ties"] += 1

    # Build a (team_id -> team_name) index so the alphabetical
    # tiebreaker is purely a function of the inputs.
    name_by_id: dict[int, str] = {tid: name for tid, name in enrolled_teams}

    # Materialise pre-rank rows (no ``rank`` yet) so we can sort.
    pre_rows: list[_PreRow] = []
    for team_id, c in counters.items():
        pre_rows.append(
            _PreRow(
                team_id=team_id,
                matches_played=c["matches_played"],
                wins=c["wins"],
                losses=c["losses"],
                ties=c["ties"],
                league_points=3 * c["wins"] + 1 * c["ties"],
                round_wins=c["round_wins"],
                total_score=c["total_score"],
            )
        )

    # Sort by (league_points desc, round_wins desc, total_score desc,
    # team_name asc). A team_id that isn't in ``enrolled_teams`` falls
    # through ``name_by_id.get(..., "")`` — empty string sorts before
    # every named team (a defensive ordering that matches the "still
    # aggregated" rule for unknown teams in the input matches).
    pre_rows.sort(
        key=lambda r: (
            -r.league_points,
            -r.round_wins,
            -r.total_score,
            name_by_id.get(r.team_id, ""),
        )
    )

    rows: list[StandingsRow] = []
    for index, r in enumerate(pre_rows):
        rows.append(
            StandingsRow(
                team_id=r.team_id,
                matches_played=r.matches_played,
                wins=r.wins,
                losses=r.losses,
                ties=r.ties,
                league_points=r.league_points,
                round_wins=r.round_wins,
                total_score=r.total_score,
                rank=index + 1,
            )
        )
    return rows
