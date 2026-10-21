"""UXP-09 — persisted whole-Season builder outputs for COMPLETED Seasons.

A completed Season's ``PlayerRoundState`` corpus never changes (ADR-0029: the
scoreboard is never rewritten), so the builders that aggregate it on every
request — the award set, the League Leaders boards and the Season dashboard
leader snippets — are computed once and stored under a key in
``Season.summary_snapshot_json``. The snapshot is written lazily on the first
read after completion, which also backfills Seasons completed before UXP-09.

Rules:

- **Never for an in-progress Season.** Anything but ``state == "completed"``
  computes and returns without touching the snapshot.
- **Names are live.** Every stored row carrying ``player_id`` / ``player_name``
  (an ``AwardWinner`` or a ``LeaderRow``) gets its ``player_name`` /
  ``team_name`` re-read from ``Player`` / ``Team`` on load (two small id
  lookups), so a rename shows up exactly as a recompute would show it. A row
  whose Player or Team no longer exists makes the key stale: it is recomputed
  and rewritten.
- **Versioned.** A snapshot whose ``"v"`` is not :data:`SNAPSHOT_VERSION` is
  discarded wholesale; bump the version when a stored shape changes.
- Dicts whose order matters are stored as ``[key, value]`` pair lists, because
  PostgreSQL ``jsonb`` does not keep object key order.
"""

from __future__ import annotations

import copy
from dataclasses import asdict
from typing import Any, Callable, Iterator, Optional, TypeVar

from django.db import transaction

from teams.models import Player, Team

from .models import Season
from .season_awards import AwardSet, AwardWinner
from .season_dashboard import LeaderRow

SNAPSHOT_VERSION = 1

T = TypeVar("T")


def completed_season_cached(
    season: Season,
    key: str,
    compute: Callable[[], T],
    *,
    dump: Callable[[T], Any],
    load: Callable[[Any], T],
) -> T:
    """Return ``compute()``, stored under ``key`` for a completed ``season``.

    ``dump`` turns the value into JSON-safe data and ``load`` turns that data
    back into an equal value. An in-progress Season always recomputes.
    """
    if season.pk is None or season.state != "completed":
        return compute()

    snapshot = season.summary_snapshot_json
    if (
        isinstance(snapshot, dict)
        and snapshot.get("v") == SNAPSHOT_VERSION
        and key in snapshot
    ):
        payload = copy.deepcopy(snapshot[key])
        if _refresh_names(payload):
            return load(payload)

    value = compute()
    _store(season, key, dump(value))
    return value


def _store(season: Season, key: str, payload: Any) -> None:
    """Merge ``key`` into the stored snapshot under a row lock, so two
    concurrent backfills of different keys do not drop each other's."""
    with transaction.atomic():
        current = (
            Season.objects.select_for_update()
            .filter(pk=season.pk)
            .values_list("summary_snapshot_json", flat=True)
            .first()
        )
        if isinstance(current, dict) and current.get("v") == SNAPSHOT_VERSION:
            snapshot = dict(current)
        else:
            snapshot = {"v": SNAPSHOT_VERSION}
        snapshot[key] = payload
        Season.objects.filter(pk=season.pk, state="completed").update(
            summary_snapshot_json=snapshot
        )
    season.summary_snapshot_json = snapshot


def _named_rows(node: Any) -> Iterator[dict]:
    """Every dict in ``node`` that carries a Player identity."""
    if isinstance(node, dict):
        if "player_id" in node and "player_name" in node:
            yield node
            return
        for value in node.values():
            yield from _named_rows(value)
    elif isinstance(node, list):
        for value in node:
            yield from _named_rows(value)


def _refresh_names(payload: Any) -> bool:
    """Overwrite the stored names with the live ones, in place.

    Returns ``False`` when a referenced Player or Team no longer exists (the
    stored value is stale and must be recomputed). Team id ``0`` is the
    builders' "no Team" sentinel and keeps its empty name.
    """
    rows = list(_named_rows(payload))
    if not rows:
        return True
    player_names = dict(
        Player.objects.filter(id__in={row["player_id"] for row in rows}).values_list(
            "id", "name"
        )
    )
    team_ids = {row["team_id"] for row in rows if row["team_id"]}
    team_names = (
        dict(Team.objects.filter(id__in=team_ids).values_list("id", "name"))
        if team_ids
        else {}
    )
    for row in rows:
        if row["player_id"] not in player_names:
            return False
        row["player_name"] = player_names[row["player_id"]]
        if row["team_id"]:
            if row["team_id"] not in team_names:
                return False
            row["team_name"] = team_names[row["team_id"]]
    return True


# ---------------------------------------------------------------------------
# (De)serialisers for the stored builder outputs
# ---------------------------------------------------------------------------


def _winner_to_json(winner: Optional[AwardWinner]) -> Optional[dict]:
    return asdict(winner) if winner is not None else None


def _winner_from_json(data: Optional[dict]) -> Optional[AwardWinner]:
    return AwardWinner(**data) if data is not None else None


def award_set_to_json(awards: AwardSet) -> dict:
    """An :class:`AwardSet` as JSON-safe data (``kd_by_role`` as pairs)."""
    return {
        "most_points": _winner_to_json(awards.most_points),
        "best_accuracy": _winner_to_json(awards.best_accuracy),
        "kd_by_role": [
            [role, _winner_to_json(winner)]
            for role, winner in awards.kd_by_role.items()
        ],
        "best_medic": _winner_to_json(awards.best_medic),
        "most_efficient_nuke": _winner_to_json(awards.most_efficient_nuke),
        "season_mvp": _winner_to_json(awards.season_mvp),
        "finals_mvp": _winner_to_json(awards.finals_mvp),
    }


def award_set_from_json(data: dict) -> AwardSet:
    """Inverse of :func:`award_set_to_json`."""
    return AwardSet(
        most_points=_winner_from_json(data["most_points"]),
        best_accuracy=_winner_from_json(data["best_accuracy"]),
        kd_by_role={
            role: _winner_from_json(winner) for role, winner in data["kd_by_role"]
        },
        best_medic=_winner_from_json(data["best_medic"]),
        most_efficient_nuke=_winner_from_json(data["most_efficient_nuke"]),
        season_mvp=_winner_from_json(data["season_mvp"]),
        finals_mvp=_winner_from_json(data["finals_mvp"]),
    )


def leader_boards_to_json(boards: dict[str, list[LeaderRow]]) -> list:
    """Named lists of :class:`LeaderRow` as ordered ``[name, rows]`` pairs."""
    return [[name, [asdict(row) for row in rows]] for name, rows in boards.items()]


def leader_boards_from_json(data: list) -> dict[str, list[LeaderRow]]:
    """Inverse of :func:`leader_boards_to_json`."""
    return {name: [LeaderRow(**row) for row in rows] for name, rows in data}
