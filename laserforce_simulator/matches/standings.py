"""LG-01 Standings aggregator — pure deterministic standings table.

Public surface:

* ``StandingsRow`` — frozen dataclass with the 17 fields a Standings row
  carries. The original 9 (``team_id``, ``matches_played``, ``wins``,
  ``losses``, ``ties``, ``league_points``, ``round_wins``,
  ``total_score``, ``rank``) plus the 8 LG-06g side-detail / form fields
  (``match_streak``, ``match_l5``, ``round_streak``, ``round_l5``,
  ``red_wlt``, ``blue_wlt``, ``red_points_for``, ``blue_points_for``).
* ``compute_standings(completed_matches, enrolled_teams, season_rounds)``
  — aggregates per-Match data (completed Matches) and per-Round data
  (every persisted Season Round) into a ranked Standings list. Sort
  order: ``(league_points desc, round_wins desc, total_score desc,
  team_name asc)``; rank is populated 1-based and dense.
* ``match_score(...)`` — the 6-point **Match score** for one Match (``+2``
  per Round won, ``+2`` for winning the Match). Returns ``(red, blue)``.
* ``swiss_points_by_team(completed_matches)`` — per-team sum of
  ``match_score`` over a list of completed-Match dicts; the Swiss
  standings rank on this instead of ``3*wins``.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``, ``collections``. No Django, no ``random``,
no ``datetime``, no I/O, no logging. ``datetime`` ordering uses values
passed IN as dict values; this module compares them without importing
``datetime``. Enforced by the ``TestNoDjangoImportsLeaked`` subprocess
check.
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
    match_streak: tuple
    match_l5: tuple
    round_streak: tuple
    round_l5: tuple
    red_wlt: tuple
    blue_wlt: tuple
    red_points_for: int
    blue_points_for: int


@dataclass(frozen=True)
class StandingsRow:
    """One Standings row — a single team's aggregated record.

    Holds STRUCTURED NUMERICS ONLY (tuples + ints). The template formats
    display; the view computes sort keys. Streak ``(kind, length)``:
    empty scope ⇒ ``("", 0)`` displays ``"—"``; ``("W", 3)`` → ``"W3"``,
    ``("L", 2)`` → ``"L2"``, ``("T", 1)`` → ``"T1"``.
    """

    team_id: int
    matches_played: int
    wins: int
    losses: int
    ties: int
    league_points: int
    round_wins: int
    total_score: int
    rank: int
    match_streak: tuple[str, int]  # (kind, length); kind in {"W","L","T",""}
    match_l5: tuple[int, int, int]  # (W,L,T) over last 5 completed Matches
    round_streak: tuple[str, int]  # (kind, length) over Season Rounds
    round_l5: tuple[int, int, int]  # (W,L,T) over last 5 Season Rounds
    red_wlt: tuple[int, int, int]  # (W,L,T) of Rounds physically played RED
    blue_wlt: tuple[int, int, int]  # (W,L,T) of Rounds physically played BLUE
    red_points_for: int  # total points scored while physically RED
    blue_points_for: int  # total points scored while physically BLUE


def _match_outcome(match: dict, team_id: int) -> str:
    """Return ``"W"`` / ``"L"`` / ``"T"`` for ``team_id`` in a Match.

    ``winner_team_id`` neither team's id (or ``None``) counts as a tie,
    mirroring the HX-03 ``compute_match_record`` defensive precedent.
    """
    winner = match["winner_team_id"]
    if winner == team_id:
        return "W"
    if winner is None:
        return "T"
    # winner is the other team (or an unknown id — defensive tie only when
    # neither the red nor the blue team is named).
    red_id = match["team_red_id"]
    blue_id = match["team_blue_id"]
    if winner == red_id or winner == blue_id:
        return "L"
    return "T"


def _round_outcome(team_id: int, red_id: int, red_pts: int, blue_pts: int) -> str:
    """Own W/L/T for ``team_id`` in a Round, regardless of physical side.

    Team won iff (team red AND red>blue) OR (team blue AND blue>red);
    tie iff equal; else loss.
    """
    if red_pts == blue_pts:
        return "T"
    is_red = team_id == red_id
    red_won = red_pts > blue_pts
    if (is_red and red_won) or (not is_red and not red_won):
        return "W"
    return "L"


def _streak_from(outcomes: list) -> tuple[str, int]:
    """Run from the tail of ``outcomes`` ⇒ ``(kind, length)``.

    Empty ⇒ ``("", 0)``. ``outcomes`` is oldest→newest; the streak is the
    run of identical outcomes counting back from the most-recent (tail).
    """
    if not outcomes:
        return ("", 0)
    kind = outcomes[-1]
    length = 0
    for o in reversed(outcomes):
        if o == kind:
            length += 1
        else:
            break
    return (kind, length)


def _l5_from(outcomes: list) -> tuple[int, int, int]:
    """(W, L, T) over the last ≤5 of ``outcomes`` (oldest→newest)."""
    last5 = outcomes[-5:]
    return (last5.count("W"), last5.count("L"), last5.count("T"))


def compute_standings(
    completed_matches: list,
    enrolled_teams: list,
    season_rounds: list | None = None,
) -> list:
    """Aggregate per-Match outcomes + per-Round side detail into a ranked
    Standings table.

    Args:
        completed_matches: list of dicts, each describing one completed
            (``is_completed=True``) Match in the Season. The 9 keys are
            ``match_id``, ``team_red_id``, ``team_blue_id``,
            ``winner_team_id`` (``int | None`` — ``None`` = tie),
            ``red_rounds_won``, ``blue_rounds_won``, ``red_total_points``,
            ``blue_total_points``, ``date_played``. Ordered by
            ``(date_played, match_id)`` asc for streak / L5.
        enrolled_teams: list of ``(team_id, team_name)`` tuples — every
            team enrolled in the Season. Teams with no matches get a
            zero-filled row.
        season_rounds: list of dicts, one per persisted Season Round
            (incl. in-progress Matches). The 6 keys are ``round_id``,
            ``team_red_id``, ``team_blue_id``, ``red_points``,
            ``blue_points``, ``date_played``. Physical sides (SIM-08).
            Ordered by ``(date_played, round_id)`` asc. Optional —
            ``None`` (the default) is treated as ``[]`` so the Match-grain
            consumers (Power Rankings, Team History, the dashboard / history
            champion-stamping) that only read the original 9 columns can
            call without a round corpus; the Round-grain + side-split
            columns then come back zeroed.

    Returns:
        list of ``StandingsRow`` sorted by ``(league_points desc,
        round_wins desc, total_score desc, team_name asc)``; ``rank`` is
        populated 1-based and dense.
    """
    if season_rounds is None:
        season_rounds = []

    # --- Match-corpus accumulator (completed Matches only) -------------
    counters: dict = defaultdict(
        lambda: {
            "matches_played": 0,
            "wins": 0,
            "losses": 0,
            "ties": 0,
            "round_wins": 0,
            "total_score": 0,
        }
    )

    # Per-team ordered outcome lists for streaks / L5.
    match_outcomes: dict = defaultdict(list)
    round_outcomes: dict = defaultdict(list)

    # Side-split accumulators (every persisted Season Round).
    red_w: dict = defaultdict(int)
    red_l: dict = defaultdict(int)
    red_t: dict = defaultdict(int)
    blue_w: dict = defaultdict(int)
    blue_l: dict = defaultdict(int)
    blue_t: dict = defaultdict(int)
    red_pf: dict = defaultdict(int)
    blue_pf: dict = defaultdict(int)

    # Seed every enrolled team so teams with no matches still get a row.
    for team_id, _team_name in enrolled_teams:
        _ = counters[team_id]  # materialise via defaultdict

    # --- Walk completed Matches (ordered by (date_played, match_id)) ---
    # ``date_played`` is optional in the input dict: the full Standings view
    # supplies it (a ``datetime``) so streak / L5 order chronologically, but
    # the Match-grain-only callers (Power Rankings, Team History, dashboard /
    # history snippets, champion-stamping) pass the legacy 8-key dict and only
    # read the original 9 columns — for them order falls to ``match_id``.
    ordered_matches = sorted(
        completed_matches,
        key=lambda m: (m.get("date_played", 0), m["match_id"]),
    )
    for match in ordered_matches:
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
            red["ties"] += 1
            blue["ties"] += 1
        elif winner == red_id:
            red["wins"] += 1
            blue["losses"] += 1
        elif winner == blue_id:
            blue["wins"] += 1
            red["losses"] += 1
        else:
            # Defensive: unknown winner id (legacy / corrupt data) — tie.
            red["ties"] += 1
            blue["ties"] += 1

        match_outcomes[red_id].append(_match_outcome(match, red_id))
        match_outcomes[blue_id].append(_match_outcome(match, blue_id))

    # --- Walk Season Rounds (ordered by (date_played, round_id)) -------
    ordered_rounds = sorted(
        season_rounds,
        key=lambda r: (r["date_played"], r["round_id"]),
    )
    for rnd in ordered_rounds:
        red_id = rnd["team_red_id"]
        blue_id = rnd["team_blue_id"]
        red_pts = rnd["red_points"]
        blue_pts = rnd["blue_points"]

        # Side-split: red team (physical red) + blue team (physical blue).
        red_pf[red_id] += red_pts
        blue_pf[blue_id] += blue_pts

        if red_pts > blue_pts:
            red_w[red_id] += 1
            blue_l[blue_id] += 1
        elif blue_pts > red_pts:
            red_l[red_id] += 1
            blue_w[blue_id] += 1
        else:
            red_t[red_id] += 1
            blue_t[blue_id] += 1

        # Own W/L/T (side-agnostic) for round streak / L5.
        round_outcomes[red_id].append(_round_outcome(red_id, red_id, red_pts, blue_pts))
        round_outcomes[blue_id].append(
            _round_outcome(blue_id, red_id, red_pts, blue_pts)
        )

    # Build a (team_id -> team_name) index for the alphabetical tiebreak.
    name_by_id: dict = {tid: name for tid, name in enrolled_teams}

    # Materialise pre-rank rows (no ``rank`` yet) so we can sort.
    pre_rows: list = []
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
                match_streak=_streak_from(match_outcomes[team_id]),
                match_l5=_l5_from(match_outcomes[team_id]),
                round_streak=_streak_from(round_outcomes[team_id]),
                round_l5=_l5_from(round_outcomes[team_id]),
                red_wlt=(red_w[team_id], red_l[team_id], red_t[team_id]),
                blue_wlt=(blue_w[team_id], blue_l[team_id], blue_t[team_id]),
                red_points_for=red_pf[team_id],
                blue_points_for=blue_pf[team_id],
            )
        )

    # Sort by (league_points desc, round_wins desc, total_score desc,
    # team_name asc). An unknown team_id falls through ``name_by_id.get(
    # ..., "")`` — empty string sorts before every named team.
    pre_rows.sort(
        key=lambda r: (
            -r.league_points,
            -r.round_wins,
            -r.total_score,
            name_by_id.get(r.team_id, ""),
        )
    )

    rows: list = []
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
                match_streak=r.match_streak,
                match_l5=r.match_l5,
                round_streak=r.round_streak,
                round_l5=r.round_l5,
                red_wlt=r.red_wlt,
                blue_wlt=r.blue_wlt,
                red_points_for=r.red_points_for,
                blue_points_for=r.blue_points_for,
            )
        )
    return rows


def match_score(
    red_rounds_won: int,
    blue_rounds_won: int,
    winner_team_id,
    team_red_id: int,
    team_blue_id: int,
) -> tuple:
    """The 6-point **Match score** for one Match: ``+2`` per Round won plus
    ``+2`` for winning the Match overall.

    Returns ``(red_score, blue_score)``. A 2-Round Match distributes up to 6
    points (sweep both Rounds = 4, win the Match = 2). Example: Red wins Round 1,
    Blue wins Round 2, Blue wins the Match on total points ⇒ ``(2, 4)``. A tied
    Match (``winner_team_id is None``) awards no Match-win bonus to either side.

    Pure integer math over the same ``red_rounds_won`` / ``blue_rounds_won`` /
    ``winner_team_id`` fields ``compute_standings`` already consumes — no Django,
    no ORM.
    """
    red = 2 * red_rounds_won + (2 if winner_team_id == team_red_id else 0)
    blue = 2 * blue_rounds_won + (2 if winner_team_id == team_blue_id else 0)
    return red, blue


def swiss_points_by_team(completed_matches: list) -> dict:
    """Sum each team's :func:`match_score` over the given completed-Match dicts.

    ``completed_matches`` is the same dict list ``compute_standings`` consumes
    (the keys read here are ``red_rounds_won``, ``blue_rounds_won``,
    ``winner_team_id``, ``team_red_id``, ``team_blue_id``). Returns
    ``{team_id: total_match_points}``; a team absent from every Match is absent
    from the dict (callers default missing teams to 0). Pure — no Django, no ORM.
    """
    points: dict = defaultdict(int)
    for m in completed_matches:
        red, blue = match_score(
            m["red_rounds_won"],
            m["blue_rounds_won"],
            m["winner_team_id"],
            m["team_red_id"],
            m["team_blue_id"],
        )
        points[m["team_red_id"]] += red
        points[m["team_blue_id"]] += blue
    return dict(points)
