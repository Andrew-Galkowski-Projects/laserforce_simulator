"""LG-01z-b — Pure power-ranking aggregator for the Power Rankings screen.

Computes a composite **power score** per Team from three raw per-Team
inputs the view hands in (team mean ``overall_rating``, win%, and average
per-Round score differential). Each raw component is **min-max-normalized
per-League to [0, 1]**; the power score is the sum of the three normalized
components. Highest sum ranks #1; ties break on team name ascending.

This module is the algorithmic seam — **pure Python, no Django imports,
no ORM, no RNG, no I/O** (frozen import allowlist: ``dataclasses``,
``typing``, ``collections`` only). Defended by a ``TestNoDjangoImportsLeaked``
subprocess check mirroring the HX-01 / RES-04 / LG-01 precedent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

# ---------------------------------------------------------------------------
# Sortable column vocabulary
# ---------------------------------------------------------------------------

# Maps a ``?sort=`` key to the ``PowerRankingRow`` attribute it sorts on. The
# template renders one sortable header per key; the view coerces the raw query
# param against this dict (unknown ⇒ default ``"rank"``).
SORT_KEYS: dict[str, str] = {
    "rank": "rank",
    "team": "team_name",
    "mean_rating": "mean_rating",
    "win_pct": "win_pct",
    "avg_score_diff": "avg_score_diff",
    "power_score": "power_score",
}

# (key, human label) in display / column order — single source of truth for the
# template header loop and the row-cell order.
SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("rank", "Rank"),
    ("team", "Team"),
    ("mean_rating", "Mean rating"),
    ("win_pct", "Win %"),
    ("avg_score_diff", "Avg score diff"),
    ("power_score", "Power score"),
)


def coerce_sort(raw: str | None, default: str = "rank") -> str:
    """Coerce a raw ``?sort=`` value to a known sort key (unknown ⇒ default)."""
    if raw in SORT_KEYS:
        return raw
    return default


def coerce_dir(raw: str | None, default: str = "asc") -> str:
    """Coerce a raw ``?dir=`` value to ``"asc"`` / ``"desc"`` (else default).

    The default direction is ``"asc"`` so the unsorted page shows ``rank``
    ascending — i.e. the #1 power score first.
    """
    if raw == "desc":
        return "desc"
    if raw == "asc":
        return "asc"
    return default


@dataclass(frozen=True)
class PowerRankingInput:
    """Raw per-Team inputs to the power-score computation.

    All three components are raw (un-normalized) values supplied by the
    view:

    * ``mean_rating`` — the team's mean ``overall_rating`` over its
      ``active_players`` (``0.0`` when no active players).
    * ``win_pct`` — wins / (wins + losses + ties) from completed Matches
      (``0.0`` when the team has played none).
    * ``avg_score_diff`` — mean of ``red_points - blue_points`` per Round
      from the team's perspective (``0.0`` when the team has no Rounds).
    """

    team_id: int
    team_name: str
    mean_rating: float
    win_pct: float
    avg_score_diff: float


@dataclass(frozen=True)
class PowerRankingRow:
    """One ranked Power Rankings row — a single team's scored record.

    Carries the rank, the three normalized [0, 1] components, and the
    composite power score (their sum). The raw inputs are preserved so the
    template can display either the raw or normalized value if desired.
    """

    rank: int
    team_id: int
    team_name: str
    mean_rating: float
    win_pct: float
    avg_score_diff: float
    norm_rating: float
    norm_win_pct: float
    norm_score_diff: float
    power_score: float


def _min_max_normalize(values: list[float]) -> list[float]:
    """Min-max-normalize a list of floats to [0, 1].

    Returns ``(v - lo) / (hi - lo)`` per element. When every value is
    equal (``hi == lo``, including the single-element and empty-spread
    cases), there is no spread to normalize against, so every element
    maps to ``0.0`` — this keeps the degenerate case from a div-by-zero
    and means a League where every team is identical on a component
    contributes ``0.0`` from that component for everyone (no team gains a
    relative edge).
    """
    if not values:
        return []
    lo = min(values)
    hi = max(values)
    spread = hi - lo
    if spread == 0:
        return [0.0 for _ in values]
    return [(v - lo) / spread for v in values]


def compute_power_rankings(
    inputs: list[PowerRankingInput],
) -> list[PowerRankingRow]:
    """Rank teams by composite power score over the supplied inputs.

    Each of the three raw components (``mean_rating``, ``win_pct``,
    ``avg_score_diff``) is min-max-normalized to [0, 1] across the whole
    ``inputs`` list (i.e. per-League). The power score is the sum of the
    three normalized components. Rows are sorted by ``power_score``
    descending; ties break on ``team_name`` ascending. ``rank`` is 1-based
    and dense in iteration order.

    Empty input ⇒ ``[]``.
    """
    if not inputs:
        return []

    norm_rating = _min_max_normalize([i.mean_rating for i in inputs])
    norm_win_pct = _min_max_normalize([i.win_pct for i in inputs])
    norm_score_diff = _min_max_normalize([i.avg_score_diff for i in inputs])

    scored: list[tuple[float, str, PowerRankingInput, float, float, float]] = []
    for inp, nr, nw, nsd in zip(inputs, norm_rating, norm_win_pct, norm_score_diff):
        power = nr + nw + nsd
        scored.append((power, inp.team_name, inp, nr, nw, nsd))

    # Sort by power desc, then team name asc. Negating power gives a stable
    # single-key tuple sort (name ascending is the natural string order).
    scored.sort(key=lambda t: (-t[0], t[1]))

    rows: list[PowerRankingRow] = []
    for index, (power, _name, inp, nr, nw, nsd) in enumerate(scored):
        rows.append(
            PowerRankingRow(
                rank=index + 1,
                team_id=inp.team_id,
                team_name=inp.team_name,
                mean_rating=inp.mean_rating,
                win_pct=inp.win_pct,
                avg_score_diff=inp.avg_score_diff,
                norm_rating=nr,
                norm_win_pct=nw,
                norm_score_diff=nsd,
                power_score=power,
            )
        )
    return rows


def sort_power_rankings(
    rows: Sequence[PowerRankingRow], sort: str, direction: str
) -> list[PowerRankingRow]:
    """Return ``rows`` sorted by the ``sort`` column in ``direction``.

    ``sort`` is a key from :data:`SORT_KEYS` (unknown ⇒ ``"rank"``);
    ``direction`` is ``"asc"`` or ``"desc"`` (anything else ⇒ ``"asc"``).
    The ``rank`` attribute is the canonical power-score position (assigned by
    :func:`compute_power_rankings`) and is preserved regardless of display
    order, so sorting by another column reorders the visible rows but each
    row keeps its true power rank. Ties always break on ``rank`` ascending
    for a stable, deterministic order.
    """
    sort = coerce_sort(sort)
    direction = coerce_dir(direction)
    attr = SORT_KEYS[sort]
    reverse = direction == "desc"

    if attr == "team_name":
        return sorted(rows, key=lambda r: r.team_name, reverse=reverse)

    # Numeric / rank columns: stable secondary tiebreak on rank ascending.
    pre = sorted(rows, key=lambda r: r.rank)
    return sorted(pre, key=lambda r: getattr(r, attr), reverse=reverse)
