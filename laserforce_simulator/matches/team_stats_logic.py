"""LG-01z-p — Team Stats aggregation (pure module).

Pure, Django-free aggregation of per-team statistics for the Team Stats
league screen. The view materialises plain per-round dicts (one per
``GameRound`` from the team's perspective) and plain per-event dicts (one
per relevant ``GameEvent``), then hands them to :func:`aggregate_team_stats`
which buckets them by ``team_id`` and produces a sortable list of
:class:`TeamStatRow` records.

Frozen import allowlist: ``dataclasses``, ``typing``, ``collections`` only —
**NO** Django / ORM / RNG / IO. Defended by the
``TestNoDjangoImportsLeaked`` subprocess check in
``matches/tests/test_lg01z_team_stats.py``.

Event-type → column mapping (verified against ``matches/CLAUDE.md``
"GameEvent" section and the seam contract §4-p):

* ``base_capture``  → ``base_captures`` (count). Emitted by the simulator
  (``get_event_icon`` documents the ``🚩`` icon) though it is not in the
  model ``EVENT_TYPES`` choices list — the view scans by the literal
  ``event_type`` string regardless of the choices enum.
* ``missiled``      → ``missiles_fired`` (count of all ``missiled`` rows)
  and ``missiles_hit`` (count where ``metadata["result"] == "hit"``).
  RES-03 split the legacy ``missile`` row into ``locking`` (fire tick) and
  ``missiled`` (resolution tick). We count ``missiled`` for *fired* per the
  spec; a ``locking`` with no matching ``missiled`` means the actor was
  Downed before resolution (the missile never landed), so ``missiled`` is
  the correct "actually fired a resolved shot" signal.
* ``special`` nuke detonation → ``nukes_fired`` AND ``nukes_landed`` (the
  same event — a detonation *is* a landed nuke; the spec says "nukes landed
  (same — detonation == landed; if you can distinguish, document)". We
  cannot distinguish at this layer, so both columns count the same
  detonation rows). A detonation is discriminated by ``points_awarded ==
  500`` AND a ``metadata["targets"]`` key (the nuke *activation* row carries
  ``points_awarded == 0`` + ``metadata["fires_at"]`` and is NOT counted).
* ``nuke_cancelled`` → ``cancelled_nukes`` (count).

The view is responsible for attributing each per-event dict to a team
(``team_id``) and for the detonation / hit discrimination above, passing in
already-classified booleans so this module stays a pure bucket-and-sum.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable, Mapping, Sequence

# ---------------------------------------------------------------------------
# Sortable column vocabulary
# ---------------------------------------------------------------------------

# Maps a ``?sort=`` key to the ``TeamStatRow`` attribute it sorts on. The
# template renders one sortable header per key; the view coerces the raw
# query param against this dict (unknown ⇒ default).
SORT_KEYS: dict[str, str] = {
    "team": "team_name",
    "avg_points_for": "avg_points_for",
    "avg_points_against": "avg_points_against",
    "avg_margin": "avg_margin",
    "avg_survivors": "avg_survivors",
    "total_tags_landed": "total_tags_landed",
    "total_times_tagged": "total_times_tagged",
    "base_captures": "base_captures",
    "missiles_fired": "missiles_fired",
    "missiles_hit": "missiles_hit",
    "nukes_fired": "nukes_fired",
    "nukes_landed": "nukes_landed",
    "cancelled_nukes": "cancelled_nukes",
}

# (key, human label) in display order — single source of truth for the
# template column loop.
SORT_KEYS_DISPLAY: tuple[tuple[str, str], ...] = (
    ("team", "Team"),
    ("avg_points_for", "Avg Pts For"),
    ("avg_points_against", "Avg Pts Against"),
    ("avg_margin", "Avg Margin"),
    ("avg_survivors", "Avg Survivors"),
    ("total_tags_landed", "Tags Landed"),
    ("total_times_tagged", "Times Tagged"),
    ("base_captures", "Base Captures"),
    ("missiles_fired", "Missiles Fired"),
    ("missiles_hit", "Missiles Hit"),
    ("nukes_fired", "Nukes Fired"),
    ("nukes_landed", "Nukes Landed"),
    ("cancelled_nukes", "Cancelled Nukes"),
)


def coerce_sort(raw: str | None, default: str = "team") -> str:
    """Coerce a raw ``?sort=`` value to a known sort key.

    Unknown / missing values fall back to ``default``.
    """
    if raw in SORT_KEYS:
        return raw
    return default


def coerce_dir(raw: str | None, default: str = "asc") -> str:
    """Coerce a raw ``?dir=`` value to ``"asc"`` or ``"desc"``.

    Anything other than the literal ``"desc"`` falls back to ``default``.
    """
    if raw == "desc":
        return "desc"
    if raw == "asc":
        return "asc"
    return default


# ---------------------------------------------------------------------------
# Output row
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TeamStatRow:
    """One aggregated per-team row of the Team Stats table.

    ``rounds_played`` is the count of per-round dicts attributed to the team
    (the denominator for every ``avg_*`` field). When zero, the averages are
    ``0.0`` (a team enrolled in the Season but with no completed Rounds).
    """

    team_id: int
    team_name: str
    rounds_played: int
    avg_points_for: float
    avg_points_against: float
    avg_margin: float
    avg_survivors: float
    total_tags_landed: int
    total_times_tagged: int
    base_captures: int
    missiles_fired: int
    missiles_hit: int
    nukes_fired: int
    nukes_landed: int
    cancelled_nukes: int


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _avg(total: int, n: int) -> float:
    """Mean of ``total`` over ``n`` rounds; ``0.0`` when ``n == 0``."""
    return (total / n) if n else 0.0


def aggregate_team_stats(
    team_rounds: Iterable[Mapping],
    team_events: Iterable[Mapping],
    enrolled_teams: Sequence[tuple[int, str]],
) -> list[TeamStatRow]:
    """Aggregate per-round + per-event dicts into per-team rows.

    ``team_rounds`` — one dict per (Team, GameRound) appearance, already
    normalised to the team's perspective by the view. Required keys:

        ``team_id`` (int), ``points_for`` (int), ``points_against`` (int),
        ``survivors`` (int — count of that team's players with
        ``final_lives > 0`` in this Round), ``tags_landed`` (int — sum of
        the team's ``tags_made``), ``times_tagged`` (int — sum of the team's
        ``times_tagged``).

    ``team_events`` — one dict per relevant ``GameEvent``, already
    attributed to a team and classified by the view. Required keys:

        ``team_id`` (int), ``kind`` (str — one of ``"base_capture"``,
        ``"missiled"``, ``"nuke_detonation"``, ``"nuke_cancelled"``),
        ``hit`` (bool — only meaningful when ``kind == "missiled"``).

    ``enrolled_teams`` — ``(team_id, team_name)`` pairs for every team
    enrolled in the displayed Season. A team with no rounds / events still
    gets a fully-zeroed row (so the table lists every enrolled team).

    Returns rows in ``team_name`` ascending order (the canonical pre-sort
    order); the view re-sorts via :func:`sort_team_stats`.
    """
    rounds_played: dict[int, int] = defaultdict(int)
    points_for_sum: dict[int, int] = defaultdict(int)
    points_against_sum: dict[int, int] = defaultdict(int)
    survivors_sum: dict[int, int] = defaultdict(int)
    tags_landed: dict[int, int] = defaultdict(int)
    times_tagged: dict[int, int] = defaultdict(int)

    for r in team_rounds:
        tid = r["team_id"]
        rounds_played[tid] += 1
        points_for_sum[tid] += r["points_for"]
        points_against_sum[tid] += r["points_against"]
        survivors_sum[tid] += r["survivors"]
        tags_landed[tid] += r["tags_landed"]
        times_tagged[tid] += r["times_tagged"]

    base_captures: dict[int, int] = defaultdict(int)
    missiles_fired: dict[int, int] = defaultdict(int)
    missiles_hit: dict[int, int] = defaultdict(int)
    nukes_fired: dict[int, int] = defaultdict(int)
    nukes_landed: dict[int, int] = defaultdict(int)
    cancelled_nukes: dict[int, int] = defaultdict(int)

    for e in team_events:
        tid = e["team_id"]
        kind = e["kind"]
        if kind == "base_capture":
            base_captures[tid] += 1
        elif kind == "missiled":
            missiles_fired[tid] += 1
            if e.get("hit"):
                missiles_hit[tid] += 1
        elif kind == "nuke_detonation":
            # Detonation == landed (cannot distinguish at this layer).
            nukes_fired[tid] += 1
            nukes_landed[tid] += 1
        elif kind == "nuke_cancelled":
            cancelled_nukes[tid] += 1

    rows: list[TeamStatRow] = []
    for team_id, team_name in enrolled_teams:
        n = rounds_played[team_id]
        rows.append(
            TeamStatRow(
                team_id=team_id,
                team_name=team_name,
                rounds_played=n,
                avg_points_for=_avg(points_for_sum[team_id], n),
                avg_points_against=_avg(points_against_sum[team_id], n),
                avg_margin=_avg(
                    points_for_sum[team_id] - points_against_sum[team_id], n
                ),
                avg_survivors=_avg(survivors_sum[team_id], n),
                total_tags_landed=tags_landed[team_id],
                total_times_tagged=times_tagged[team_id],
                base_captures=base_captures[team_id],
                missiles_fired=missiles_fired[team_id],
                missiles_hit=missiles_hit[team_id],
                nukes_fired=nukes_fired[team_id],
                nukes_landed=nukes_landed[team_id],
                cancelled_nukes=cancelled_nukes[team_id],
            )
        )

    rows.sort(key=lambda row: row.team_name)
    return rows


def sort_team_stats(
    rows: Sequence[TeamStatRow], sort: str, direction: str
) -> list[TeamStatRow]:
    """Return ``rows`` sorted by the ``sort`` column in ``direction``.

    ``sort`` is a key from :data:`SORT_KEYS` (unknown ⇒ ``"team"``);
    ``direction`` is ``"asc"`` or ``"desc"`` (anything else ⇒ ``"asc"``).
    Ties always break on ``team_name`` ascending for a stable, deterministic
    order regardless of the primary column.
    """
    sort = coerce_sort(sort)
    direction = coerce_dir(direction)
    attr = SORT_KEYS[sort]
    reverse = direction == "desc"

    if attr == "team_name":
        return sorted(rows, key=lambda r: r.team_name, reverse=reverse)

    # Primary key reversed as requested; secondary team_name tiebreak always
    # ascending — sort by the tiebreak first, then a stable sort by the
    # primary key preserves it within ties.
    pre = sorted(rows, key=lambda r: r.team_name)
    return sorted(pre, key=lambda r: getattr(r, attr), reverse=reverse)
