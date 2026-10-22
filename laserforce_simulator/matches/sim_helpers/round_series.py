"""Per-player resource series for a round, ported from the events page (RV-05 T1).

``build_round_series`` is a one-for-one Python port of the RES-02b series
build in ``templates/matches/game_round_events.html`` (``playerState`` /
``seriesByChart`` / ``pushIfChanged``), so the round report PDF charts plot
exactly what the page charts. Inputs are the server rows the page already
receives: the ``_round_players_data`` player rows and the
``round_playback_payload`` event rows. Pure Python: no Django imports, no
ORM, no I/O.
"""

from __future__ import annotations

CHART_KEYS: tuple[str, ...] = ("shots", "lives", "points", "sp")

# Ticks per second: ``ts`` is in canonical ticks (1 tick = 0.5 s), so the
# page's ``e.sec = e.ts / 2``.
_TICKS_PER_SECOND: float = 2.0


def _coerce(value: object) -> int | None:
    """Port of the page's ``parseInt`` guard: ``None`` and non-numeric values
    are ignored (no point appended)."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _push_if_changed(
    state: dict[str, dict[str, int]],
    series: dict[str, dict[str, list[tuple[float, int]]]],
    pid: str,
    key: str,
    value: object,
    sec: float,
) -> None:
    n = _coerce(value)
    if n is None or state[pid][key] == n:
        return
    state[pid][key] = n
    series[key][pid].append((sec, n))


def build_round_series(players: list[dict], events: list[dict]) -> dict:
    """Return ``{players, shots, lives, points, sp}`` for a round.

    ``players`` echoes the input rows as ``{id, name, team}`` in input order.
    Each chart dict is keyed by ``str(player id)`` and holds a list of
    ``(seconds, value)`` tuples starting at second 0 with shots at ``ss``,
    lives at ``sl``, points 0 and SP 0.
    """
    series: dict[str, dict[str, list[tuple[float, int]]]] = {
        key: {} for key in CHART_KEYS
    }
    state: dict[str, dict[str, int]] = {}
    for p in players:
        pid = str(p["id"])
        state[pid] = {
            "shots": int(p.get("ss") or 0),
            "lives": int(p.get("sl") or 0),
            "points": 0,
            "sp": 0,
        }
        for key in CHART_KEYS:
            series[key][pid] = [(0.0, state[pid][key])]

    for ev in events:
        sec = ev["ts"] / _TICKS_PER_SECOND
        meta = ev.get("meta") or {}
        aid = str(ev["aid"])
        if aid in state:
            _push_if_changed(state, series, aid, "shots", meta.get("actor_shots"), sec)
            _push_if_changed(state, series, aid, "lives", meta.get("actor_lives"), sec)
            _push_if_changed(
                state, series, aid, "points", meta.get("actor_points"), sec
            )
            _push_if_changed(state, series, aid, "sp", meta.get("sp"), sec)
        tid = str(ev["tid"])
        if ev["tid"] != -1 and tid in state:
            _push_if_changed(state, series, tid, "shots", meta.get("target_shots"), sec)
            _push_if_changed(state, series, tid, "lives", meta.get("target_lives"), sec)
            _push_if_changed(
                state, series, tid, "points", meta.get("target_points"), sec
            )
        targets = meta.get("targets")
        if isinstance(targets, list):
            for t in targets:
                pid = str(t.get("pid"))
                if pid not in state:
                    continue
                _push_if_changed(state, series, pid, "shots", t.get("shots"), sec)
                _push_if_changed(state, series, pid, "lives", t.get("lives"), sec)
                _push_if_changed(state, series, pid, "points", t.get("points"), sec)

    return {
        "players": [
            {"id": p["id"], "name": p["name"], "team": p["team"]} for p in players
        ],
        **series,
    }
