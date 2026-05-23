"""HX-01 — Per-player career stats (pure Python).

Aggregates a player's career across all rounds (`PlayerRoundState`
records assembled into plain dicts by the view). This module is **pure**:
no Django imports, no ORM, no RNG, no I/O. The single external dependency
is `SPECIAL_COST` from `matches.sim_helpers.role_constants` — a frozen
constants dict with no Django imports of its own.

See `.claude/worktrees/hx-01-seam-contract.md` for the locked seam.
"""

from collections import defaultdict
from typing import Iterable, Mapping, Sequence

from matches.sim_helpers.role_constants import SPECIAL_COST

# Locked role order for `summarize_by_role` (Commander, Heavy, Scout, Medic,
# Ammo). Roles not played by the player are omitted from the output list;
# this list only fixes the order of those that are present.
_ROLE_ORDER: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")

# TIME-01 cap: `was_eliminated_at == 1801` is the SURVIVED_SENTINEL; we cap
# at TICKS_PER_ROUND = 1800 in the survival mean so survivors contribute
# 1800, not 1801. We hard-code the cap here (rather than importing from
# `matches.sim_helpers.time_constants`) because the seam's import allowlist
# limits us to `SPECIAL_COST` only.
_SURVIVAL_CAP_TICKS: int = 1800


def _empty_summary() -> dict:
    """The zeroed shape returned for empty input."""
    return {
        "games": 0,
        "avg_points": 0.0,
        "tag_ratio": 0.0,
        "avg_survival_ticks": 0.0,
        "avg_accuracy_pct": 0.0,
        "avg_sp_earned": 0.0,
    }


def _summarize_list(rounds: Sequence[Mapping]) -> dict:
    """Internal: aggregate over an already-materialised sequence of round-dicts.

    Accepts any ``Sequence[Mapping]`` (list, tuple, etc.) — only ``len()``
    and iteration are required.
    """
    games = len(rounds)
    if games == 0:
        return _empty_summary()

    sum_points = 0
    sum_tags = 0
    sum_tagged = 0
    sum_misses = 0
    sum_survival = 0
    sum_sp = 0

    for r in rounds:
        sum_points += r["points_scored"]
        sum_tags += r["tags_made"]
        sum_tagged += r["times_tagged"]
        sum_misses += r["shots_missed"]
        sum_survival += min(r["was_eliminated_at"], _SURVIVAL_CAP_TICKS)
        role = r["role"]
        sum_sp += r["final_special"] + SPECIAL_COST.get(role, 0) * r["specials_used"]

    tag_ratio = sum_tags / max(sum_tagged, 1)
    avg_accuracy_pct = sum_tags / max(sum_tags + sum_misses, 1) * 100

    return {
        "games": games,
        "avg_points": sum_points / games,
        "tag_ratio": tag_ratio,
        "avg_survival_ticks": sum_survival / games,
        "avg_accuracy_pct": avg_accuracy_pct,
        "avg_sp_earned": sum_sp / games,
    }


def summarize(rounds: Iterable[Mapping]) -> dict:
    """Aggregate a player's career across all rounds.

    Empty input ⇒ ``{"games": 0, "avg_points": 0.0, "tag_ratio": 0.0,
                     "avg_survival_ticks": 0.0, "avg_accuracy_pct": 0.0,
                     "avg_sp_earned": 0.0}``.

    Formulas (frozen by the seam):
      - ``tag_ratio`` and ``avg_accuracy_pct`` are sum/sum (not mean of
        per-round ratios) with ``max(..., 1)`` denominator floors.
      - ``avg_survival_ticks`` caps each round's ``was_eliminated_at``
        at 1800 (the ``SURVIVED_SENTINEL`` of 1801 contributes 1800).
      - ``avg_sp_earned`` = mean of
        ``final_special + SPECIAL_COST.get(role, 0) * specials_used``;
        the ``.get(role, 0)`` fallback ensures Heavy (no SP entry)
        contributes ``final_special`` only.
    """
    return _summarize_list(list(rounds))


def summarize_by_role(rounds: Iterable[Mapping]) -> list[dict]:
    """Per-role breakdown. One entry per role ACTUALLY PLAYED.

    Roles not played by the player are omitted. Entries are ordered
    Commander, Heavy, Scout, Medic, Ammo. Empty input ⇒ ``[]``.
    """
    by_role: dict[str, list[Mapping]] = defaultdict(list)
    for r in rounds:
        by_role[r["role"]].append(r)

    out: list[dict] = []
    for role in _ROLE_ORDER:
        bucket = by_role.get(role)
        if not bucket:
            continue
        s = _summarize_list(bucket)
        out.append(
            {
                "role": role,
                "games": s["games"],
                "avg_points": s["avg_points"],
                "tag_ratio": s["tag_ratio"],
                "avg_survival_ticks": s["avg_survival_ticks"],
                "avg_accuracy_pct": s["avg_accuracy_pct"],
                "avg_sp_earned": s["avg_sp_earned"],
            }
        )
    return out


def rolling_mean(values: list[float], window: int = 10) -> list[float]:
    """Trailing rolling mean with a partial window for the first ``window-1``
    entries.

    Empty input ⇒ ``[]``.
    For ``1 <= i < window``: mean of ``values[:i+1]`` (partial).
    For ``i >= window``: mean of ``values[i-window+1 : i+1]`` (full).
    """
    n = len(values)
    if n == 0:
        return []

    out: list[float] = []
    running = 0.0
    for i in range(n):
        running += values[i]
        if i < window:
            out.append(running / (i + 1))
        else:
            running -= values[i - window]
            out.append(running / window)
    return out


def points_trend(rounds: Iterable[Mapping], window: int = 10) -> list[list]:
    """Rolling-mean trend of ``points_scored`` over time.

    Returns ``[[round_idx, mean_points], ...]`` with ``round_idx`` 1-based.
    Rounds are sorted ascending by ``(date_played, game_round_id)``.
    Partial trailing window for rounds 1..window-1.
    Empty input ⇒ ``[]``.

    The output is a ``list[list]`` (not ``list[tuple]``) so it serialises
    trivially via Django's ``json_script`` filter.
    """
    ordered = sorted(rounds, key=lambda r: (r["date_played"], r["game_round_id"]))
    if not ordered:
        return []

    point_values: list[float] = [float(r["points_scored"]) for r in ordered]
    means = rolling_mean(point_values, window=window)
    return [[idx + 1, mean] for idx, mean in enumerate(means)]
