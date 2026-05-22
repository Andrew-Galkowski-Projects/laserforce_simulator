"""RV-02: pure auto-highlight builder.

Pure Python — **no Django imports, no I/O, no RNG**. Consumes the in-memory
event buffer (the ``event_log`` list ``_flush_to_db`` receives) plus the round
``result`` dict and returns a flat list of typed **Highlight** records sorted by
tick. The caller (``BatchSimulator._flush_to_db``) passes ``id -> name`` and
``id -> team`` maps so this function stays pure while still emitting player
NAME strings and per-event team in each record.

See CONTEXT.md (Highlight, Scoring burst, Medic reset chain, Nuke cancellation)
and docs/adr/0012-nuke-cancelled-event.md.
"""

from __future__ import annotations

from typing import Optional

# The "largest 30-second point swing" window: 30 s = 60 ticks (1 tick = 0.5 s).
SCORING_BURST_WINDOW_TICKS = 60

KINDS = (
    "nuke_detonation",
    "nuke_cancelled",
    "medic_reset",
    "first_elimination",
    "team_elimination",
    "scoring_burst",
)


def _record(
    kind: str,
    tick: Optional[int],
    *,
    team: Optional[str] = None,
    actor: Optional[str] = None,
    target: Optional[str] = None,
    points: Optional[int] = None,
    label: str = "",
) -> dict:
    """Build a highlight record with all seven keys always present."""
    return {
        "kind": kind,
        "tick": tick,
        "team": team,
        "actor": actor,
        "target": target,
        "points": points,
        "label": label,
    }


def build_highlights(
    events: list[dict],
    result: dict,
    *,
    round_ticks: int,
    name_by_id: dict[int, str],
    team_by_id: dict[int, str],
) -> list[dict]:
    """Return the round's flat list of Highlight records, sorted by tick.

    ``events`` is the in-memory event-buffer list (not ORM rows); each element
    is ``{"event_type", "actor_id", "target_id", "timestamp", "points_awarded",
    "description", "metadata"}``. ``result`` is the round result dict
    (``red_eliminated`` / ``blue_eliminated`` / ``eliminated_at`` are read).
    ``round_ticks`` bounds the scoring-burst arithmetic. ``name_by_id`` /
    ``team_by_id`` resolve actor/target ids to display strings; ids absent from
    a map resolve to ``None``.
    """

    def name(pid):
        return name_by_id.get(pid) if pid is not None else None

    def team(pid):
        return team_by_id.get(pid) if pid is not None else None

    highlights: list[dict] = []

    first_elim_tick = None  # smallest elimination tick seen so far
    # point-bearing events as (tick, team, points) — basis for the scoring burst.
    point_events: list[tuple[int, str, int]] = []

    for ev in events:
        etype = ev.get("event_type")
        meta = ev.get("metadata") or {}
        actor_id = ev.get("actor_id")
        target_id = ev.get("target_id")
        tick = ev.get("timestamp", 0)
        pts = ev.get("points_awarded", 0) or 0
        a_team = team(actor_id)

        if pts > 0 and a_team is not None:
            point_events.append((tick, a_team, pts))

        if etype == "special" and "targets" in meta and pts == 500:
            a = name(actor_id)
            highlights.append(
                _record(
                    "nuke_detonation",
                    tick,
                    team=a_team,
                    actor=a,
                    points=500,
                    label=f"{a} nuke detonates",
                )
            )
        elif etype == "nuke_cancelled":
            a = name(actor_id)
            highlights.append(
                _record(
                    "nuke_cancelled",
                    tick,
                    team=a_team,
                    actor=a,
                    points=0,
                    label=f"{a} nuke cancelled",
                )
            )
        elif etype == "medic_reset":
            a = name(actor_id)
            highlights.append(
                _record(
                    "medic_reset",
                    tick,
                    team=a_team,
                    actor=a,
                    label=f"{a} reset before recovering",
                )
            )
        elif etype == "elimination":
            # first_elimination: the earliest elimination by tick (ties keep the
            # earliest in buffer order). Record it the first time we hit a new
            # minimum; replace only on a strictly-earlier tick.
            if first_elim_tick is None or tick < first_elim_tick:
                first_elim_tick = tick
                a = name(actor_id)
                t = name(target_id)
                highlights[:] = [
                    h for h in highlights if h["kind"] != "first_elimination"
                ]
                highlights.append(
                    _record(
                        "first_elimination",
                        tick,
                        team=a_team,
                        actor=a,
                        target=t,
                        points=pts,
                        label=f"First elimination: {a} eliminates {t}",
                    )
                )

    # --- team_elimination (from result, not events) -------------------------
    if result.get("red_eliminated"):
        highlights.append(
            _record(
                "team_elimination",
                result.get("eliminated_at"),
                team="red",
                label="Red team eliminated",
            )
        )
    elif result.get("blue_eliminated"):
        highlights.append(
            _record(
                "team_elimination",
                result.get("eliminated_at"),
                team="blue",
                label="Blue team eliminated",
            )
        )

    # --- scoring_burst: largest single-team gross points in any 60-tick -----
    burst = _largest_scoring_burst(point_events)
    if burst is not None:
        burst_team, start_tick, gross = burst
        color = "Red" if burst_team == "red" else "Blue"
        highlights.append(
            _record(
                "scoring_burst",
                start_tick,
                team=burst_team,
                points=gross,
                label=f"{color} scoring burst: {gross} pts in 30s",
            )
        )

    highlights.sort(key=lambda h: (h["tick"] if h["tick"] is not None else 0))
    return highlights


def _largest_scoring_burst(
    point_events: list[tuple[int, str, int]],
) -> Optional[tuple[str, int, int]]:
    """Return ``(team, start_tick, gross_points)`` for the largest 60-tick
    forward window ``[t, t+60)`` of single-team gross points, or ``None`` when
    there are no point-bearing events.

    Deterministic tie-break: teams iterated red-then-blue, anchors in ascending
    tick order, best replaced only on a strictly-greater sum — so the earliest
    red window wins ties.
    """
    if not point_events:
        return None

    best = None  # (gross, start_tick, team)
    for color in ("red", "blue"):
        team_events = sorted(
            ((t, p) for t, c, p in point_events if c == color), key=lambda x: x[0]
        )
        for anchor_tick, _ in team_events:
            window_end = anchor_tick + SCORING_BURST_WINDOW_TICKS
            gross = sum(p for t, p in team_events if anchor_tick <= t < window_end)
            if best is None or gross > best[0]:
                best = (gross, anchor_tick, color)

    if best is None:
        return None
    gross, start_tick, color = best
    return (color, start_tick, gross)
