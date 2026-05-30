"""LG-01z-e — Team History (league-context) pure aggregation module.

Pure deterministic helpers feeding the three Team History tabs (Overall /
Seasons / Players). The league-screen view materialises plain dicts from
the ORM and hands them here; this module never imports Django, touches the
ORM, draws RNG, or does any I/O.

Public surface
--------------
* ``OverallRecord`` — the team's all-time round-level W-L-T plus playoff
  appearances (placeholder ``0`` until LG-02) and championships.
* ``SeasonRow`` — one row of the Seasons tab: a Season the team enrolled
  in, with that Season's round-level record and final standing/rank.
* ``PlayerRollup`` — one row of the Players tab: a player who appeared for
  the team, with career-long aggregate stats, games played on the team,
  last season played, and an "on team / elsewhere" colour flag.
* ``compute_overall_record(round_outcomes, championships)`` — fold per-round
  W/L/T outcomes into an ``OverallRecord``.
* ``compute_season_rows(season_dicts)`` — one ``SeasonRow`` per enrolled
  Season.
* ``compute_player_rollups(player_round_dicts)`` — one ``PlayerRollup`` per
  distinct player who appeared for the team.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``, ``collections``. No Django, no ``random``,
no ``datetime``, no I/O, no logging. Enforced by the
``TestNoDjangoImportsLeaked`` subprocess check.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Iterable, Mapping

# Career aggregate stat keys rolled up per player on the Players tab. Each
# key is summed across every round the player played for the team; the view
# hands one dict per ``PlayerRoundState`` row carrying these keys.
PLAYER_STAT_KEYS: tuple[str, ...] = (
    "points_scored",
    "tags_made",
    "times_tagged",
    "missiles_landed",
    "resupplies_given",
    "specials_used",
)


@dataclass(frozen=True)
class OverallRecord:
    """The Overall tab — a team's all-time round-level record."""

    wins: int
    losses: int
    ties: int
    playoff_appearances: int
    championships: int


@dataclass(frozen=True)
class SeasonRow:
    """One row of the Seasons tab — a Season the team enrolled in."""

    season_id: int
    year: int | None
    wins: int
    losses: int
    ties: int
    rank: int | None


@dataclass(frozen=True)
class PlayerRollup:
    """One row of the Players tab — a player who appeared for the team."""

    player_id: int
    name: str
    on_team: bool
    games_played: int
    last_season_year: int | None
    stats: dict = field(default_factory=dict)

    @property
    def colour_class(self) -> str:
        """CSS colour class: green when still on the team, else blue."""
        return (
            "team-history-player-green" if self.on_team else "team-history-player-blue"
        )


def round_outcome(team_points: int, opponent_points: int) -> str:
    """Classify one round from the team's perspective.

    Mirrors the LG-01g per-Round W/L/T rule: compare the team's points for
    that round against the opponent's. Equal points is a tie.
    """
    if team_points > opponent_points:
        return "W"
    if team_points < opponent_points:
        return "L"
    return "T"


def compute_overall_record(
    round_outcomes: Iterable[str],
    *,
    championships: int,
    playoff_appearances: int = 0,
) -> OverallRecord:
    """Fold per-round ``"W"`` / ``"L"`` / ``"T"`` outcomes into a record.

    Args:
        round_outcomes: iterable of single-char outcome strings from the
            team's perspective (use :func:`round_outcome` to derive each).
            Any value other than ``"W"`` / ``"L"`` is counted as a tie
            (defensive — mirrors the standings module's unknown-winner
            rule).
        championships: count of Seasons the team won (``champion_team``).
        playoff_appearances: placeholder, defaults to ``0`` (LG-02).
    """
    wins = 0
    losses = 0
    ties = 0
    for outcome in round_outcomes:
        if outcome == "W":
            wins += 1
        elif outcome == "L":
            losses += 1
        else:
            ties += 1
    return OverallRecord(
        wins=wins,
        losses=losses,
        ties=ties,
        playoff_appearances=playoff_appearances,
        championships=championships,
    )


def compute_season_rows(season_dicts: Iterable[Mapping]) -> list[SeasonRow]:
    """Build the Seasons-tab rows.

    Each input dict carries the 6 keys ``season_id``, ``year``
    (``int | None``), ``wins``, ``losses``, ``ties``, ``rank``
    (``int | None``). Rows are returned in input order (the view sorts
    Seasons newest-first by id before calling).
    """
    rows: list[SeasonRow] = []
    for d in season_dicts:
        rows.append(
            SeasonRow(
                season_id=d["season_id"],
                year=d.get("year"),
                wins=d.get("wins", 0),
                losses=d.get("losses", 0),
                ties=d.get("ties", 0),
                rank=d.get("rank"),
            )
        )
    return rows


def compute_player_rollups(
    player_round_dicts: Iterable[Mapping],
) -> list[PlayerRollup]:
    """Build the Players-tab rows from per-round player appearances.

    Each input dict describes one ``PlayerRoundState`` row for a round the
    player played FOR the team, with keys:

        * ``player_id`` (int), ``player_name`` (str)
        * ``on_team`` (bool — ``Player.team == team``)
        * ``season_year`` (``int | None`` — the round's Season start year,
          ``None`` for non-Season / sandbox rounds)
        * every key in :data:`PLAYER_STAT_KEYS` (int, that round's value)

    Multiple dicts for the same ``player_id`` are folded into a single
    ``PlayerRollup``: ``games_played`` counts the dicts, each stat key is
    summed, ``last_season_year`` is the maximum non-``None`` ``season_year``
    seen, and ``on_team`` / ``name`` take the most recent value (defensive
    "last wins" — the view passes rows in id-ascending order so "last" is
    the most recent appearance).

    Rows are returned sorted by ``(name, player_id)`` ascending for a
    deterministic, stable display order.
    """
    games: dict[int, int] = defaultdict(int)
    names: dict[int, str] = {}
    on_team: dict[int, bool] = {}
    last_year: dict[int, int | None] = {}
    stat_sums: dict[int, dict[str, int]] = defaultdict(
        lambda: {key: 0 for key in PLAYER_STAT_KEYS}
    )

    for d in player_round_dicts:
        pid = d["player_id"]
        games[pid] += 1
        names[pid] = d.get("player_name", "")
        on_team[pid] = bool(d.get("on_team", False))

        year = d.get("season_year")
        if year is not None:
            prior = last_year.get(pid)
            if prior is None or year > prior:
                last_year[pid] = year
        else:
            last_year.setdefault(pid, None)

        for key in PLAYER_STAT_KEYS:
            stat_sums[pid][key] += int(d.get(key, 0) or 0)

    rollups: list[PlayerRollup] = []
    for pid in games:
        rollups.append(
            PlayerRollup(
                player_id=pid,
                name=names[pid],
                on_team=on_team[pid],
                games_played=games[pid],
                last_season_year=last_year.get(pid),
                stats=dict(stat_sums[pid]),
            )
        )

    rollups.sort(key=lambda r: (r.name, r.player_id))
    return rollups
