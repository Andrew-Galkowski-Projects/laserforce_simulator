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
from typing import Any, Callable, Iterable, Iterator, Optional, Sequence, TypeVar

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

    payload = _stored_payload(season, key)
    if payload is not None and _refresh_names(payload):
        return load(payload)

    value = compute()
    _store(season, key, dump(value))
    return value


def completed_seasons_cached(
    seasons: Sequence[Season],
    key: str,
    compute_one: Callable[[Season], T],
    *,
    load: Callable[[Any], T],
) -> dict[int, T]:
    """PERF-01 -- the stored ``key`` value of many Seasons, keyed by Season id.

    Every fresh stored payload is loaded with one Player and one Team name
    query across all the Seasons. A Season that is in progress, has no stored
    ``key``, or whose snapshot is stale falls back to ``compute_one(season)``,
    which must be the single-Season :func:`completed_season_cached` path (so
    the write-on-first-read and recompute rules stay in one place).
    """
    payloads: dict[int, Any] = {}
    for season in seasons:
        if season.pk is not None and season.state == "completed":
            payload = _stored_payload(season, key)
            if payload is not None:
                payloads[season.pk] = payload
    player_names, team_names = _fetch_names(payloads.values())

    result: dict[int, T] = {}
    for season in seasons:
        payload = payloads.get(season.pk)
        if payload is not None and _refresh_names(
            payload, player_names=player_names, team_names=team_names
        ):
            result[season.id] = load(payload)
        else:
            result[season.id] = compute_one(season)
    return result


def _stored_payload(season: Season, key: str) -> Any:
    """A deep copy of ``key``'s stored payload, or ``None`` when the snapshot
    is missing, has another version, or lacks ``key``."""
    snapshot = season.summary_snapshot_json
    if (
        isinstance(snapshot, dict)
        and snapshot.get("v") == SNAPSHOT_VERSION
        and key in snapshot
    ):
        return copy.deepcopy(snapshot[key])
    return None


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


def _fetch_names(payloads: Iterable[Any]) -> tuple[dict[int, str], dict[int, str]]:
    """The live ``(player_names, team_names)`` of every row in ``payloads``:
    at most one Player and one Team id lookup, none when nothing is named."""
    rows = [row for payload in payloads for row in _named_rows(payload)]
    if not rows:
        return {}, {}
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
    return player_names, team_names


def _refresh_names(
    payload: Any,
    *,
    player_names: Optional[dict[int, str]] = None,
    team_names: Optional[dict[int, str]] = None,
) -> bool:
    """Overwrite the stored names with the live ones, in place.

    Returns ``False`` when a referenced Player or Team no longer exists (the
    stored value is stale and must be recomputed). Team id ``0`` is the
    builders' "no Team" sentinel and keeps its empty name. PERF-01: pass both
    pre-fetched name maps (from :func:`_fetch_names`) to skip the queries.
    """
    rows = list(_named_rows(payload))
    if not rows:
        return True
    if player_names is None or team_names is None:
        player_names, team_names = _fetch_names([payload])
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
