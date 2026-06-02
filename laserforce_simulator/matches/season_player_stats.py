"""LG-01z-o — Player Stats (season performance) aggregation (pure Python).

Aggregates per-player PERFORMANCE across a Season's completed Rounds —
the HX-01 ``STAT_KEYS`` set. The view (``league_screens/player_stats.py``)
builds one plain dict per ``PlayerRoundState`` row (reading ``get_mvp`` /
``get_accuracy`` per round) and hands a flat list to ``aggregate_player_stats``;
this module never sees a Django object, the ORM, RNG, or I/O.

**Aggregation rule (documented decision).** The 12 STAT_KEYS split into two
groups by their statistical nature:

- **Summed (counts / totals)** — accumulate across the Season:
  ``points_scored``, ``tags_made``, ``times_tagged``, ``final_lives``,
  ``resupplies_given``, ``missiles_landed``, ``specials_used``,
  ``follow_up_shots``, ``reaction_shots``, ``combo_resupply_count``.
- **Averaged (rates / ratings)** — a per-Round mean is the meaningful
  figure: ``mvp`` (per-Round MVP rating, from ``get_mvp``) and
  ``accuracy`` (per-Round shot-accuracy percentage, from ``get_accuracy``).

Each averaged key is the arithmetic mean over the player's rounds
(``0.0`` for a player with zero rounds — defensive; the view never passes
a zero-round player).

See ``.claude/worktrees/lg-01z-seam-contract.md`` §4 entry "o · Player Stats".
"""

from dataclasses import dataclass, field
from typing import Iterable, Mapping, Sequence

# The 12 HX-01 STAT_KEYS in their canonical order.
STAT_KEYS: tuple[str, ...] = (
    "points_scored",
    "mvp",
    "tags_made",
    "times_tagged",
    "accuracy",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)

# Keys aggregated by SUM (counts / totals).
SUMMED_KEYS: tuple[str, ...] = (
    "points_scored",
    "tags_made",
    "times_tagged",
    "final_lives",
    "resupplies_given",
    "missiles_landed",
    "specials_used",
    "follow_up_shots",
    "reaction_shots",
    "combo_resupply_count",
)

# Keys aggregated by MEAN (rates / ratings) — pre-computed per round
# (mvp via get_mvp, accuracy via get_accuracy) by the view.
AVERAGED_KEYS: tuple[str, ...] = (
    "mvp",
    "accuracy",
)

# DERIVED keys — not in STAT_KEYS (they are neither a plain sum nor a plain
# per-round mean), computed from the accumulators after the round loop:
#   - "tag_ratio" = sum(tags_made) / max(sum(times_tagged), 1)  (our KDA)
#   - "survival"  = mean per-round survival seconds (the view pre-computes
#                   each round's ``survival_seconds`` = min(was_eliminated_at,
#                   1800) ÷ 2; missing ⇒ treated as 0.0, defensive).
# Surfaced in PlayerStatRow.stats alongside the STAT_KEYS so the template /
# sorter treat them uniformly.
DERIVED_KEYS: tuple[str, ...] = (
    "tag_ratio",
    "survival",
)


@dataclass(frozen=True)
class PlayerStatRow:
    """One aggregated player row.

    ``stats`` carries every STAT_KEYS entry: summed keys hold the Season
    total (numeric), averaged keys hold the per-Round mean (float).
    ``games`` is the number of Rounds the player appeared in.
    """

    player_id: int
    player_name: str
    team_id: int
    team_name: str
    role: str
    games: int
    stats: Mapping[str, float] = field(default_factory=dict)


def aggregate_player_stats(player_rounds: Iterable[Mapping]) -> list[PlayerStatRow]:
    """Aggregate per-Round dicts into one ``PlayerStatRow`` per player.

    Each input dict carries (all required): ``player_id``, ``player_name``,
    ``team_id``, ``team_name``, ``role`` plus the 12 STAT_KEYS. ``mvp`` and
    ``accuracy`` are pre-computed per-Round values (from ``get_mvp`` /
    ``get_accuracy``).

    Summed keys accumulate across the player's Rounds; averaged keys
    become the per-Round mean. Empty input ⇒ ``[]``.

    Output is unsorted in player-id-ascending order (the view applies
    the user-requested sort via :func:`sort_player_stats`). Rows are
    grouped by ``player_id``; the most-recently-seen name / team / role
    wins on the (defensive) chance they differ across rounds.
    """
    materialised: Sequence[Mapping] = list(player_rounds)

    # player_id → accumulator dict.
    acc: dict[int, dict] = {}
    order: list[int] = []
    for pr in materialised:
        pid = pr["player_id"]
        bucket = acc.get(pid)
        if bucket is None:
            bucket = {
                "player_id": pid,
                "player_name": pr["player_name"],
                "team_id": pr["team_id"],
                "team_name": pr["team_name"],
                "role": pr["role"],
                "games": 0,
                "_sums": {k: 0.0 for k in SUMMED_KEYS},
                "_avg_sums": {k: 0.0 for k in AVERAGED_KEYS},
                "_survival_sum": 0.0,
            }
            acc[pid] = bucket
            order.append(pid)
        # Last-seen identity wins (the view passes rows id-ascending so the
        # most-recent PlayerRoundState's name/team/role is authoritative).
        bucket["player_name"] = pr["player_name"]
        bucket["team_id"] = pr["team_id"]
        bucket["team_name"] = pr["team_name"]
        bucket["role"] = pr["role"]
        bucket["games"] += 1
        for k in SUMMED_KEYS:
            bucket["_sums"][k] += pr[k]
        for k in AVERAGED_KEYS:
            bucket["_avg_sums"][k] += pr[k]
        # Derived: per-round survival seconds (view pre-computes; missing ⇒ 0).
        bucket["_survival_sum"] += pr.get("survival_seconds", 0.0)

    rows: list[PlayerStatRow] = []
    for pid in order:
        bucket = acc[pid]
        games = bucket["games"]
        stats: dict[str, float] = {}
        for k in SUMMED_KEYS:
            stats[k] = bucket["_sums"][k]
        for k in AVERAGED_KEYS:
            stats[k] = bucket["_avg_sums"][k] / games if games else 0.0
        # Derived keys (not in STAT_KEYS).
        stats["tag_ratio"] = bucket["_sums"]["tags_made"] / max(
            bucket["_sums"]["times_tagged"], 1
        )
        stats["survival"] = bucket["_survival_sum"] / games if games else 0.0
        rows.append(
            PlayerStatRow(
                player_id=bucket["player_id"],
                player_name=bucket["player_name"],
                team_id=bucket["team_id"],
                team_name=bucket["team_name"],
                role=bucket["role"],
                games=games,
                stats=stats,
            )
        )
    return rows


def apply_rate(rows: Iterable[PlayerStatRow], rate: str) -> list[PlayerStatRow]:
    """LG-06d — re-express the SUMMED_KEYS of each row as a rate, pure.

    ``rate`` is one of ``"total"`` / ``"per_game"`` / ``"per_10"`` (coerce
    upstream via :func:`matches.league_views._coerce_rate`). Only the 10
    :data:`SUMMED_KEYS` count totals are transformed; :data:`AVERAGED_KEYS`
    (``mvp`` / ``accuracy``) and :data:`DERIVED_KEYS` (``tag_ratio`` /
    ``survival``) pass through untouched, and ``games`` is unchanged.

    - ``"total"`` ⇒ identity (every summed key returned as-is).
    - ``"per_game"`` ⇒ ``value / games`` (``0.0`` when ``games <= 0``).
    - ``"per_10"`` ⇒ ``value * 600 / total_uptime_seconds`` where
      ``total_uptime_seconds = stats["survival"] * games`` (the laser-tag
      analogue of ZenGM Per-36 — denominator is the player's total
      survival/uptime seconds across the Season); guard
      ``total_uptime_seconds <= 0`` ⇒ ``0.0``.

    Returns a NEW list of NEW frozen :class:`PlayerStatRow` objects — the input
    rows are never mutated; each output row's ``stats`` is a fresh dict copy
    with only the summed keys replaced. Pure: no Django / ORM / RNG / I/O.
    """
    out: list[PlayerStatRow] = []
    for row in rows:
        if rate == "total":
            new_stats = dict(row.stats)
        else:
            games = row.games
            new_stats = dict(row.stats)
            if rate == "per_game":
                divisor = float(games)
                for key in SUMMED_KEYS:
                    new_stats[key] = (row.stats[key] / divisor) if divisor > 0 else 0.0
            else:  # rate == "per_10"
                total_uptime_seconds = row.stats.get("survival", 0.0) * games
                for key in SUMMED_KEYS:
                    if total_uptime_seconds > 0:
                        new_stats[key] = row.stats[key] * 600 / total_uptime_seconds
                    else:
                        new_stats[key] = 0.0
        out.append(
            PlayerStatRow(
                player_id=row.player_id,
                player_name=row.player_name,
                team_id=row.team_id,
                team_name=row.team_name,
                role=row.role,
                games=row.games,
                stats=new_stats,
            )
        )
    return out


def coerce_sort(raw: "str | None", default: str = "points_scored") -> str:
    """Forgiving ``?sort=`` validator over the STAT_KEYS + ``name`` / ``team``.

    Accepted: every key in :data:`STAT_KEYS` and :data:`DERIVED_KEYS` plus
    ``"name"`` / ``"team"`` / ``"games"``. Anything else (``None``, unknown,
    empty) ⇒ ``default``.
    """
    if raw in STAT_KEYS or raw in DERIVED_KEYS or raw in ("name", "team", "games"):
        return raw  # type: ignore[return-value]
    return default


def coerce_dir(raw: "str | None", default: str = "desc") -> str:
    """Forgiving ``?dir=`` validator; only ``"asc"`` / ``"desc"`` accepted.

    Case-sensitive (``"ASC"`` falls back to ``default``). Default is
    ``"desc"`` so the highest-scoring players surface first by default.
    """
    if raw in ("asc", "desc"):
        return raw  # type: ignore[return-value]
    return default


def sort_player_stats(
    rows: Iterable[PlayerStatRow], sort: str, direction: str
) -> list[PlayerStatRow]:
    """Return ``rows`` sorted by ``sort`` in ``direction`` (``asc`` / ``desc``).

    ``sort`` is one of the STAT_KEYS, ``"name"``, ``"team"``, or
    ``"games"`` (coerce upstream via :func:`coerce_sort`). An unrecognised
    key falls back to ``"points_scored"`` defensively. The secondary
    tiebreak is always ``player_name`` ascending so the order is
    deterministic. Name / team sorts are case-insensitive.
    """
    materialised = list(rows)
    reverse = direction == "desc"

    if sort == "name":
        materialised.sort(key=lambda r: (r.player_name.lower(), r.player_id))
        if reverse:
            materialised.reverse()
        return materialised
    if sort == "team":
        materialised.sort(
            key=lambda r: (r.team_name.lower(), r.player_name.lower(), r.player_id)
        )
        if reverse:
            materialised.reverse()
        return materialised
    if sort == "games":
        primary = lambda r: r.games  # noqa: E731
    elif sort in STAT_KEYS or sort in DERIVED_KEYS:
        primary = lambda r: r.stats.get(sort, 0.0)  # noqa: E731
    else:
        primary = lambda r: r.stats.get("points_scored", 0.0)  # noqa: E731

    # Sort by name asc first (stable secondary), then by the primary key.
    materialised.sort(key=lambda r: (r.player_name.lower(), r.player_id))
    materialised.sort(key=primary, reverse=reverse)
    return materialised
