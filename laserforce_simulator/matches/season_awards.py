"""LG-03 — Season-end awards (pure Python).

Computes the per-Season awards (6 category awards + 2 headline awards) from
plain dict / int inputs. The view / ``Season`` method assemble the three
inputs from the ORM and hand them over; this module never sees a Django
object, the ORM, RNG, ``datetime``, or I/O.

**Frozen import allowlist:** ``dataclasses``, ``typing``, ``math``,
``collections`` ONLY — NO Django, NO ORM, NO ``random``, NO ``datetime``,
NO I/O, NO logging. Defended by ``TestNoDjangoImportsLeaked``.

The module owns its OWN summed-MVP accumulation — it does NOT reuse
``aggregate_player_stats`` (that AVERAGES mvp and omits nuke elims). It DOES
consume the ``_build_round_dicts`` dict shape verbatim for ``regular_rounds``
/ ``finals_rounds`` (every field except the nuke-elimination count, which is
supplied separately as ``nuke_elims_by_player``).

See ``.claude/worktrees/lg-03-seam-contract.md`` §1 for the locked seam.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from typing import Optional

# The 6 per-Season category awards — LOCKED (key, label) pairs. Rendered as a
# table; ``tag_ratio`` expands to its <=5 per-role winners.
AWARD_CATEGORIES: tuple[tuple[str, str], ...] = (
    ("most_points", "Most Points"),
    ("tag_ratio", "Highest Tag Ratio by Role"),
    ("most_resupplies", "Most Resupplies"),
    ("longest_survival", "Longest Survival"),
    ("most_efficient_nuke", "Most Efficient Nuke"),
    ("best_accuracy", "Best Accuracy"),
)

# Headline category keys — NOT in AWARD_CATEGORIES; rendered in their own slots.
HEADLINE_SEASON_MVP = "season_mvp"
HEADLINE_FINALS_MVP = "finals_mvp"

# The 5 Tag-Ratio per-role buckets, in render order. Absent roles are skipped.
_TAG_RATIO_ROLES: tuple[str, ...] = (
    "commander",
    "heavy",
    "scout",
    "medic",
    "ammo",
)


@dataclass(frozen=True)
class AwardWinner:
    """A single award winner. ``role`` is set ONLY for the 5 tag_ratio per-role
    winners; ``None`` for every other category and the two headlines."""

    category: str
    label: str
    player_id: int
    player_name: str
    team_id: int
    team_name: str
    value: float
    role: Optional[str] = None


# --- internal accumulation -------------------------------------------------


class _PlayerAccum:
    """Per-player running totals over a round corpus. Identity fields take the
    LAST round's values (the view feeds rounds id-ascending, so "last" is the
    most-recent appearance)."""

    __slots__ = (
        "player_id",
        "player_name",
        "team_id",
        "team_name",
        "role",
        "games",
        "points_scored",
        "tags_made",
        "times_tagged",
        "resupplies_given",
        "survival_seconds_sum",
        "accuracy_sum",
        "mvp_sum",
    )

    def __init__(self, player_id: int) -> None:
        self.player_id = player_id
        self.player_name = ""
        self.team_id = 0
        self.team_name = ""
        self.role = ""
        self.games = 0
        self.points_scored = 0.0
        self.tags_made = 0.0
        self.times_tagged = 0.0
        self.resupplies_given = 0.0
        self.survival_seconds_sum = 0.0
        self.accuracy_sum = 0.0
        self.mvp_sum = 0.0

    def add(self, rd: dict) -> None:
        self.player_name = rd["player_name"]
        self.team_id = rd["team_id"]
        self.team_name = rd["team_name"]
        self.role = rd["role"]
        self.games += 1
        self.points_scored += rd["points_scored"]
        self.tags_made += rd["tags_made"]
        self.times_tagged += rd["times_tagged"]
        self.resupplies_given += rd["resupplies_given"]
        self.survival_seconds_sum += rd["survival_seconds"]
        self.accuracy_sum += rd["accuracy"]
        self.mvp_sum += rd["mvp"]

    @property
    def survival_mean(self) -> float:
        return self.survival_seconds_sum / self.games if self.games else 0.0

    @property
    def accuracy_mean(self) -> float:
        return self.accuracy_sum / self.games if self.games else 0.0

    @property
    def tag_ratio(self) -> float:
        return self.tags_made / max(self.times_tagged, 1)


def _aggregate(rounds: list[dict]) -> dict[int, _PlayerAccum]:
    """Accumulate per-player totals over a round corpus, keyed by player_id."""
    by_player: dict[int, _PlayerAccum] = {}
    for rd in rounds:
        pid = rd["player_id"]
        accum = by_player.get(pid)
        if accum is None:
            accum = _PlayerAccum(pid)
            by_player[pid] = accum
        accum.add(rd)
    return by_player


def _games_floor(by_player: dict[int, _PlayerAccum]) -> int:
    """``ceil(games / 2)`` where ``games = max(games over all players)`` — the
    half-season eligibility bar for the 3 rate awards. Empty corpus ⇒ 0."""
    if not by_player:
        return 0
    games = max(a.games for a in by_player.values())
    return math.ceil(games / 2)


def _best(
    candidates: list[tuple[float, int, _PlayerAccum]],
) -> Optional[tuple[float, int, _PlayerAccum]]:
    """Tiebreak: value DESC -> player_id ASC. Empty ⇒ None."""
    if not candidates:
        return None
    # Sort ascending by (-value, player_id) so the first entry is the winner.
    return min(candidates, key=lambda c: (-c[0], c[1]))


def _make_winner(
    category: str,
    label: str,
    accum: _PlayerAccum,
    value: float,
    role: Optional[str] = None,
) -> AwardWinner:
    return AwardWinner(
        category=category,
        label=label,
        player_id=accum.player_id,
        player_name=accum.player_name,
        team_id=accum.team_id,
        team_name=accum.team_name,
        value=float(value),
        role=role,
    )


# --- per-category winners --------------------------------------------------


def _count_award(
    by_player: dict[int, _PlayerAccum],
    category: str,
    label: str,
    metric,
) -> Optional[AwardWinner]:
    """A count award — NO games floor. ``metric`` maps an accum to its value."""
    candidates = [(metric(a), a.player_id, a) for a in by_player.values()]
    best = _best(candidates)
    if best is None:
        return None
    value, _pid, accum = best
    return _make_winner(category, label, accum, value)


def _rate_award(
    by_player: dict[int, _PlayerAccum],
    floor: int,
    category: str,
    label: str,
    metric,
) -> Optional[AwardWinner]:
    """A rate / avg award — eligible iff ``games >= floor``."""
    candidates = [
        (metric(a), a.player_id, a) for a in by_player.values() if a.games >= floor
    ]
    best = _best(candidates)
    if best is None:
        return None
    value, _pid, accum = best
    return _make_winner(category, label, accum, value)


def _tag_ratio_winners(
    by_player: dict[int, _PlayerAccum], floor: int
) -> list[AwardWinner]:
    """The <=5 per-role Tag-Ratio winners (commander/heavy/scout/medic/ammo
    order, absent roles skipped). Floor applies within each role bucket."""
    buckets: dict[str, list[_PlayerAccum]] = defaultdict(list)
    for a in by_player.values():
        if a.role in _TAG_RATIO_ROLES and a.games >= floor:
            buckets[a.role].append(a)

    winners: list[AwardWinner] = []
    for role in _TAG_RATIO_ROLES:
        accums = buckets.get(role)
        if not accums:
            continue
        candidates = [(a.tag_ratio, a.player_id, a) for a in accums]
        best = _best(candidates)
        if best is None:
            continue
        value, _pid, accum = best
        winners.append(
            _make_winner("tag_ratio", "Highest Tag Ratio by Role", accum, value, role)
        )
    return winners


def _most_efficient_nuke(
    by_player: dict[int, _PlayerAccum],
    nuke_elims_by_player: dict[int, int],
) -> Optional[AwardWinner]:
    """Most Efficient Nuke — nuke-elimination count, count award (NO floor),
    keyed off ``nuke_elims_by_player``. Absent when the map is empty or no
    counted player appears in the round corpus."""
    candidates: list[tuple[float, int, _PlayerAccum]] = []
    for pid, count in nuke_elims_by_player.items():
        accum = by_player.get(pid)
        if accum is None:
            # A nuking Commander with no PlayerRoundState in this corpus has no
            # identity to render — skip defensively.
            continue
        candidates.append((float(count), pid, accum))
    best = _best(candidates)
    if best is None:
        return None
    value, _pid, accum = best
    return _make_winner("most_efficient_nuke", "Most Efficient Nuke", accum, value)


def _season_mvp(by_player: dict[int, _PlayerAccum]) -> Optional[AwardWinner]:
    """Season MVP — summed ``mvp``, self-flooring (NO games floor)."""
    candidates = [(a.mvp_sum, a.player_id, a) for a in by_player.values()]
    best = _best(candidates)
    if best is None:
        return None
    value, _pid, accum = best
    return _make_winner(HEADLINE_SEASON_MVP, "Season MVP", accum, value)


def _finals_mvp(
    finals_rounds: list[dict],
    champion_team_id: Optional[int],
) -> Optional[AwardWinner]:
    """Finals MVP — summed ``mvp`` over the deciding playoff node's Rounds,
    restricted to champion-team players. Absent when ``finals_rounds == []``
    or ``champion_team_id is None``."""
    if not finals_rounds or champion_team_id is None:
        return None
    champion_rounds = [rd for rd in finals_rounds if rd["team_id"] == champion_team_id]
    by_player = _aggregate(champion_rounds)
    candidates = [(a.mvp_sum, a.player_id, a) for a in by_player.values()]
    best = _best(candidates)
    if best is None:
        return None
    value, _pid, accum = best
    return _make_winner(HEADLINE_FINALS_MVP, "Finals MVP", accum, value)


def compute_season_awards(
    regular_rounds: list[dict],
    nuke_elims_by_player: dict[int, int],
    finals_rounds: list[dict],
    champion_team_id: Optional[int],
) -> dict[str, object]:
    """Compute every Season award from plain dict / int inputs.

    ``regular_rounds`` / ``finals_rounds`` are per-PlayerRoundState dicts in the
    ``_build_round_dicts`` shape. ``nuke_elims_by_player`` is
    ``{player_id: nuke-elimination count}`` over the regular-season Rounds.
    ``champion_team_id`` is the Season champion team id (``None`` if no playoff
    champion).

    Returns a dict keyed by category (the cached JSON shape, §3): each
    ``AWARD_CATEGORIES`` key maps to an ``AwardWinner`` (or ``None``), except
    ``"tag_ratio"`` which maps to a ``list[AwardWinner]`` (the <=5 per-role
    winners); plus ``"season_mvp"`` and ``"finals_mvp"`` ⇒ ``AwardWinner |
    None``. The pure module returns ``AwardWinner`` instances; serialisation to
    plain dicts happens at the ``Season`` method boundary.
    """
    by_player = _aggregate(regular_rounds)
    floor = _games_floor(by_player)

    return {
        "most_points": _count_award(
            by_player, "most_points", "Most Points", lambda a: a.points_scored
        ),
        "tag_ratio": _tag_ratio_winners(by_player, floor),
        "most_resupplies": _count_award(
            by_player,
            "most_resupplies",
            "Most Resupplies",
            lambda a: a.resupplies_given,
        ),
        "longest_survival": _rate_award(
            by_player,
            floor,
            "longest_survival",
            "Longest Survival",
            lambda a: a.survival_mean,
        ),
        "most_efficient_nuke": _most_efficient_nuke(by_player, nuke_elims_by_player),
        "best_accuracy": _rate_award(
            by_player,
            floor,
            "best_accuracy",
            "Best Accuracy",
            lambda a: a.accuracy_mean,
        ),
        HEADLINE_SEASON_MVP: _season_mvp(by_player),
        HEADLINE_FINALS_MVP: _finals_mvp(finals_rounds, champion_team_id),
    }
