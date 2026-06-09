"""LG-02-Part2b / Part2c-3a — pure phase-composition parser.

Parses the create-League composer's serialized wire format into an ordered
list of :class:`PhaseSpec`. The form's ``clean()`` calls
``parse_phase_composition`` and stashes the result; both SeasonPhase-creation
sites (``league_create`` / ``next_season``) loop over the specs.

LG-02-Part2c-3a — the wire format is now per-token ``type[:format]`` tokens,
e.g. ``"round_robin:double_round_robin,tournament"``. A bare ``round_robin``
token (no colon) defaults to ``single_round_robin`` (Part2b backward-compat);
a ``tournament`` token carries no format.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``. NO Django, NO ORM, NO ``random``, NO
``datetime``, NO ``json``, NO I/O, NO logging. Errors are raised as plain
:class:`ValueError` (never ``django.core.exceptions.ValidationError``) so the
module stays Django-free; the form layer catches and re-wraps. Enforced by
the ``TestNoDjangoImportsLeaked`` subprocess check.
"""

from dataclasses import dataclass
from typing import Optional

# LG-02-Part2c-3a — the valid per-phase regular-season schedule formats.
_VALID_SCHEDULE_FORMATS = ("single_round_robin", "double_round_robin")


@dataclass(frozen=True)
class PhaseSpec:
    """One ordered phase parsed from the composer wire format.

    ``ordinal`` is 1-based and contiguous (``1..N`` in composer order).
    ``phase_type`` is ``"round_robin"`` or ``"tournament"``.
    ``schedule_format`` is the season format for a round_robin phase and
    ``None`` for a tournament phase.

    LG-02-Part2c-3b — ``tournament_mode`` carries the per-phase tournament
    flavour (season-ending ``standings`` vs mid-season ``strength`` /
    ``unseeded`` / ``random_draw``). DORMANT this slice: it is **not** parsed
    from the wire format (the ``:`` syntax stays reserved for the Part2c-3c
    picker), so every spec defaults to ``"standings"``. Appended LAST with a
    default so existing keyword constructions stay equality-identical (the
    Part2c-3a ``ScheduleFixture.leg`` precedent).
    """

    ordinal: int
    phase_type: str
    schedule_format: Optional[str]
    tournament_mode: str = "standings"


def parse_phase_composition(
    raw: str, *, season_schedule_format: str
) -> list[PhaseSpec]:
    """Parse the composer wire format into ordered :class:`PhaseSpec` rows.

    Wire format (LG-02-Part2c-3a): comma-separated ``type[:format]`` tokens
    parsed with ``str.split(",")``, ``str.strip()`` per token, then split on
    the FIRST ``:`` only into ``(type_part, format_part)``.

    Behaviour:
        * EMPTY / blank ``raw`` ⇒ exactly one
          ``PhaseSpec(ordinal=1, phase_type="round_robin",
          schedule_format=season_schedule_format)`` (the Part2a default).
        * Otherwise: split on ``,``, strip each token, split on the first
          ``:`` into ``(type_part, format_part)``, assign contiguous ordinals
          ``1..N`` in composer order. For a ``round_robin`` token,
          ``schedule_format = format_part or season_schedule_format`` (so a
          bare ``round_robin`` ⇒ ``single_round_robin`` — the locked
          ``season_schedule_format`` fallback); a ``tournament`` token carries
          no format (``schedule_format=None``) and a present ``format_part`` is
          malformed.

    Valid phase types are ``"round_robin"`` and ``"tournament"`` only
    (``"member_night"`` is NOT selectable — it raises unknown-type). Valid
    per-phase schedule formats are ``single_round_robin`` and
    ``double_round_robin``.

    LG-02-Part2c-1 — after the zero-RR check, a ``tournament`` phase that
    precedes the first ``round_robin`` phase raises
    ``"a tournament phase requires a preceding round-robin phase"``.

    Raises plain :class:`ValueError` with the locked message strings.
    """
    if not raw.strip():
        return [
            PhaseSpec(
                ordinal=1,
                phase_type="round_robin",
                schedule_format=season_schedule_format,
            )
        ]

    tokens = [token.strip() for token in raw.split(",")]
    specs: list[PhaseSpec] = []
    for index, token in enumerate(tokens):
        if not token:
            raise ValueError("malformed phase composition")
        # Split on the FIRST ":" only into (type_part, format_part).
        type_part, _sep, format_part = token.partition(":")
        type_part = type_part.strip()
        format_part = format_part.strip()
        if not type_part:
            raise ValueError("malformed phase composition")
        if type_part == "round_robin":
            schedule_format: Optional[str] = format_part or season_schedule_format
            if schedule_format not in _VALID_SCHEDULE_FORMATS:
                raise ValueError(f"unknown schedule_format: {schedule_format!r}")
        elif type_part == "tournament":
            # A tournament token takes no format; a present one is malformed.
            if _sep or format_part:
                raise ValueError("malformed phase composition")
            schedule_format = None
        else:
            raise ValueError(f"unknown phase type: {token!r}")
        specs.append(
            PhaseSpec(
                ordinal=index + 1,
                phase_type=type_part,
                schedule_format=schedule_format,
            )
        )

    if not any(spec.phase_type == "round_robin" for spec in specs):
        raise ValueError("composition must contain at least one round-robin phase")

    # LG-02-Part2c-1 — a tournament phase must follow a round-robin phase.
    seen_round_robin = False
    for spec in specs:
        if spec.phase_type == "round_robin":
            seen_round_robin = True
        elif spec.phase_type == "tournament" and not seen_round_robin:
            raise ValueError(
                "a tournament phase requires a preceding round-robin phase"
            )

    return specs
