"""LG-01c Season dashboard pure aggregator.

Public surface:

* ``LeaderRow`` — frozen dataclass describing one leader row in a
  per-Season top-N leader snippet.
* ``compute_leaders(player_rounds, stat, limit=3)`` — aggregate a list
  of per-``PlayerRoundState`` dicts into a ranked top-N leader snippet.
  Locked stat vocabulary: ``"points_per_game"``, ``"tags_per_game"``,
  ``"tag_ratio"``.
* ``find_next_fixture(fixtures, played_keys)`` — return the first
  unplayed ``ScheduleFixture`` in iteration order, or ``None`` if every
  fixture has been played.
* ``round_progress(fixtures, played_keys)`` — return
  ``(completed, total)`` Round counts where ``completed`` is the number
  of fixtures whose Side-agnostic key appears in ``played_keys``.
* ``find_next_matchday(fixtures, played_keys)`` — return the global
  matchday of the first unplayed ``(phase_id, fixture)`` pair (LG-01d;
  phase-aware via plain-int phase-ids since LG-02-Part2c-2).
* ``select_play_fixtures(fixtures, played_keys, max_matchdays)`` —
  return the unplayed ``(phase_id, fixture)`` pairs spanning the next
  ``max_matchdays`` distinct unplayed global matchdays; ``None`` returns
  ALL unplayed (LG-01d; phase-aware since LG-02-Part2c-2).

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``, ``collections``. No Django, no ORM, no
``random``, no ``datetime``, no I/O, no logging. The contract is
enforced by the ``TestNoDjangoImportsLeaked`` subprocess check.

This module deliberately does NOT import ``matches.schedule_generator``
— ``ScheduleFixture`` instances are passed in by the view, and the
dataclass shape itself is the cross-module contract. The
``ScheduleFixture`` references below are forward-string annotations.
"""

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class LeaderRow:
    """One row in a per-Season top-N leader snippet."""

    player_id: int
    player_name: str
    role: str
    team_id: int
    team_name: str
    value: float
    games_played: int
    rank: int


def compute_leaders(
    player_rounds: list[dict],
    stat: str,
    limit: int = 3,
) -> list[LeaderRow]:
    """Aggregate per-``PlayerRoundState`` rows into a ranked leaders snippet.

    Args:
        player_rounds: list of dicts with the 7 frozen keys. One entry
            per ``PlayerRoundState`` row across the Season's completed
            Rounds. Each dict carries ``player_id``, ``player_name``,
            ``role``, ``team_id``, ``team_name``, ``tags_made``,
            ``times_tagged``, ``points_scored``.
        stat: which leader stat to compute. Locked vocabulary:
            ``"points_per_game"`` — ``mean(points_scored)``;
            ``"tags_per_game"`` — ``mean(tags_made)``;
            ``"tag_ratio"``     — ``sum(tags_made) / max(sum(times_tagged), 1)``
            (canonical CONTEXT.md sum/sum form — NOT mean of per-row
            ratios; the ``max(..., 1)`` denominator avoids div-by-zero).
        limit: how many rows to return after sorting. Default 3.

    Returns:
        Top-``limit`` ``LeaderRow`` instances sorted by
        ``(value desc, games_played desc, player_id asc)``. ``rank`` is
        1-based dense in iteration order. Empty input ⇒ ``[]``.

    Raises:
        ValueError: if ``stat`` is not in the locked vocabulary.
    """
    if stat not in ("points_per_game", "tags_per_game", "tag_ratio"):
        raise ValueError(
            f"Unknown stat {stat!r}; expected one of "
            f"points_per_game, tags_per_game, tag_ratio"
        )

    if not player_rounds:
        return []

    # Group rows by player_id, preserving input order so "last row wins"
    # for the defensive role / team fallback.
    grouped: dict[int, list[dict]] = defaultdict(list)
    for row in player_rounds:
        grouped[row["player_id"]].append(row)

    # Build pre-rank tuples (player_id, value, games_played, last_row).
    pre: list[tuple[int, float, int, dict]] = []
    for player_id, rows in grouped.items():
        games_played = len(rows)
        last_row = rows[-1]
        if stat == "points_per_game":
            value = sum(r["points_scored"] for r in rows) / games_played
        elif stat == "tags_per_game":
            value = sum(r["tags_made"] for r in rows) / games_played
        else:  # tag_ratio
            total_tags = sum(r["tags_made"] for r in rows)
            total_tagged = sum(r["times_tagged"] for r in rows)
            value = total_tags / max(total_tagged, 1)
        pre.append((player_id, float(value), games_played, last_row))

    # Sort by (value desc, games_played desc, player_id asc).
    pre.sort(key=lambda t: (-t[1], -t[2], t[0]))

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


def find_next_fixture(
    fixtures: "list",
    played_keys: "set",
) -> "Optional[object]":
    """Return the first unplayed fixture in iteration order.

    Args:
        fixtures: list of ``ScheduleFixture`` in the canonical iteration
            order from ``generate_schedule(...)`` (already sorted by
            ``(matchday, team_a_id)``).
        played_keys: set of ``(frozenset({team_red_id, team_blue_id}),
            round_number)`` tuples for every persisted ``GameRound`` in
            the Season. Side-agnostic ``frozenset`` match.

    Returns:
        The first ``ScheduleFixture`` whose ``(frozenset({team_a_id,
        team_b_id}), round_number)`` is NOT in ``played_keys``, or
        ``None`` if every fixture has been played.
    """
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key not in played_keys:
            return fixture
    return None


def find_next_matchday(
    fixtures: "list",
    played_keys: "set",
) -> "Optional[int]":
    """Return the global matchday number of the first unplayed fixture.

    LG-02-Part2c-2 — phase-aware. ``fixtures`` is now a list of
    ``(phase_id, ScheduleFixture)`` pairs (``phase_id`` a plain
    ``int | None``) in canonical global-continuous iteration order;
    ``played_keys`` is a set of ``(phase_id, frozenset({team_red_id,
    team_blue_id}), round_number)`` 3-tuples (plain-int / frozenset only —
    the module stays Django-free).

    Returns the global ``matchday`` of the first ``(phase_id, fixture)`` pair
    whose 3-tuple key is NOT in ``played_keys``, or ``None`` if every fixture
    has been played (or the input is empty).
    """
    for phase_id, fixture in fixtures:
        key = (
            phase_id,
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key not in played_keys:
            return fixture.matchday
    return None


def select_play_fixtures(
    fixtures: "list",
    played_keys: "set",
    max_matchdays: "Optional[int]",
) -> "list":
    """Return the unplayed ``(phase_id, fixture)`` pairs spanning the next
    ``max_matchdays`` distinct unplayed global matchdays.

    LG-02-Part2c-2 — phase-aware. ``fixtures`` is a list of
    ``(phase_id, ScheduleFixture)`` pairs (``phase_id`` a plain
    ``int | None``) in canonical global-continuous iteration order;
    ``played_keys`` is a set of ``(phase_id, frozenset[int], int)`` 3-tuples.
    Because the play loop feeds OFFSET fixtures (their ``matchday`` is already
    global), the distinct-matchday sweep over ``fixture.matchday`` selects a
    contiguous global window that naturally spans the RR1->RR2 boundary.

    Args:
        fixtures: list of ``(phase_id, ScheduleFixture)`` pairs.
        played_keys: set of ``(phase_id, frozenset[int], int)`` 3-tuples.
        max_matchdays: if an ``int``, return only pairs whose ``matchday`` is
            among the next ``max_matchdays`` distinct unplayed global
            matchdays. If ``None``, return ALL unplayed pairs (Play Until
            End of Season).

    Returns:
        The unplayed ``(phase_id, fixture)`` pairs in iteration order. Empty
        list when ``fixtures`` is empty or every fixture has been played.
    """
    if not fixtures:
        return []

    selected: list = []
    distinct_matchdays: list[int] = []

    for entry in fixtures:
        phase_id, fixture = entry
        key = (
            phase_id,
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key in played_keys:
            continue

        if max_matchdays is None:
            selected.append(entry)
            continue

        # max_matchdays is an int. Track distinct unplayed (global) matchdays
        # in iteration order; accept pairs whose matchday is in the set, add
        # new matchdays only while we have headroom.
        if fixture.matchday in distinct_matchdays:
            selected.append(entry)
        elif len(distinct_matchdays) < max_matchdays:
            distinct_matchdays.append(fixture.matchday)
            selected.append(entry)
        # else: skip — past our matchday budget.

    return selected


def round_progress(
    fixtures: "list",
    played_keys: "set",
) -> tuple[int, int]:
    """Return ``(completed, total)`` Round counts.

    Args:
        fixtures: as in ``find_next_fixture``.
        played_keys: as in ``find_next_fixture``.

    Returns:
        ``(completed, total)`` where ``completed`` is the count of
        fixtures whose ``(frozenset({team_a_id, team_b_id}),
        round_number)`` appears in ``played_keys`` and ``total`` is
        ``len(fixtures)``.

    Note:
        ``completed`` is computed from fixtures matched against
        ``played_keys``, NOT from ``len(played_keys)`` — extra
        ``GameRound`` rows that don't correspond to a fixture
        (defensive — e.g. data drift) are not double-counted.
    """
    total = len(fixtures)
    completed = 0
    for fixture in fixtures:
        key = (
            frozenset({fixture.team_a_id, fixture.team_b_id}),
            fixture.round_number,
            fixture.leg,
        )
        if key in played_keys:
            completed += 1
    return completed, total
