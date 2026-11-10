"""RES-03 missile log — pure aggregation.

Drives the per-round missile-log surface at
``/matches/game-round/<id>/missile-log/``. The view materialises one dict
per ``GameEvent(event_type="missiled")`` row (locking events are filtered
out upstream) and hands the list to ``summarize_missile_log``.

This module is **pure**: no Django, no ORM, no RNG, no I/O.

See ``.claude/worktrees/round-analytics-seam-contract.md`` for the locked
seam.
"""

from dataclasses import asdict, dataclass
from typing import Iterable, Mapping


@dataclass(frozen=True)
class MissileRow:
    """One rendered row in the missile-log table."""

    timestamp: int
    timestamp_mmss: str
    actor_role: str
    target_role: str
    result: str
    friendly_fire: bool
    description: str
    points: int
    row_class: str


def _format_mmss(timestamp_ticks: int) -> str:
    """Tick → MM:SS at the HTML display boundary (TIME-01 ``÷2``)."""
    seconds_total = int(timestamp_ticks) // 2
    minutes = seconds_total // 60
    seconds = seconds_total % 60
    return f"{minutes:02d}:{seconds:02d}"


def summarize_missile_log(events: Iterable[Mapping], *, locks: int) -> dict:
    """Build the locks/missiles/conversion summary plus per-event display rows.

    ``events`` is a list of missiled-event dicts with keys ``timestamp``
    (int ticks), ``metadata`` (dict, may be ``{}``), ``description`` (str),
    ``points_awarded`` (int or ``None``). Locking events are filtered out
    by the caller — only missiled rows cross the seam; the caller counts
    the round's ``locking`` rows into ``locks``.

    Returns ``{"locks": int, "missiles": int, "conversion": float,
    "rows": list[dict]}`` where each row is the ``MissileRow`` dict (9
    keys, fully flat). MECH-21 (ADR-0050): every launched missile hits, so
    ``conversion`` is ``missiles / locks * 100``, 0.0 when ``locks`` is 0.
    Friendly-fire missiles count — preserves the GLOSSARY.md
    **Friendly fire** contract (the missile landed; FF is qualitative).
    """
    materialised = list(events)
    missiles = len(materialised)
    conversion = (missiles / locks * 100.0) if locks else 0.0

    rows: list[dict] = []
    for ev in materialised:
        meta = ev.get("metadata") or {}
        ts = ev.get("timestamp") or 0
        friendly = bool(meta.get("friendly_fire"))
        row = MissileRow(
            timestamp=ts,
            timestamp_mmss=_format_mmss(ts),
            actor_role=meta.get("actor_role", ""),
            target_role=meta.get("target_role", ""),
            result=meta.get("result", ""),
            friendly_fire=friendly,
            description=ev.get("description", "") or "",
            points=ev.get("points_awarded") or 0,
            row_class="missile-row friendly-fire" if friendly else "missile-row",
        )
        rows.append(asdict(row))

    return {
        "locks": locks,
        "missiles": missiles,
        "conversion": conversion,
        "rows": rows,
    }
