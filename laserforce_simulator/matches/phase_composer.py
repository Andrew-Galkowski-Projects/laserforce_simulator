"""LG-02-Part2b — pure phase-composition parser.

Parses the create-League composer's serialized wire format (a
comma-separated list of phase-type tokens, e.g. ``"round_robin,tournament"``)
into an ordered list of :class:`PhaseSpec`. The form's ``clean()`` calls
``parse_phase_composition`` and stashes the result; both SeasonPhase-creation
sites (``league_create`` / ``next_season``) loop over the specs.

Frozen import allowlist (the only modules this file may import):
``dataclasses``, ``typing``. NO Django, NO ORM, NO ``random``, NO
``datetime``, NO ``json``, NO I/O, NO logging. Errors are raised as plain
:class:`ValueError` (never ``django.core.exceptions.ValidationError``) so the
module stays Django-free; the form layer catches and re-wraps. Enforced by
the ``TestNoDjangoImportsLeaked`` subprocess check.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class PhaseSpec:
    """One ordered phase parsed from the composer wire format.

    ``ordinal`` is 1-based and contiguous (``1..N`` in composer order).
    ``phase_type`` is ``"round_robin"`` or ``"tournament"``.
    ``schedule_format`` is the season format for a round_robin phase and
    ``None`` for a tournament phase.
    """

    ordinal: int
    phase_type: str
    schedule_format: Optional[str]


def parse_phase_composition(
    raw: str, *, season_schedule_format: str
) -> list[PhaseSpec]:
    """Parse the composer wire format into ordered :class:`PhaseSpec` rows.

    Wire format: comma-separated phase-type tokens parsed with
    ``str.split(",")`` and ``str.strip()`` per token.

    Behaviour:
        * EMPTY / blank ``raw`` ⇒ exactly one
          ``PhaseSpec(ordinal=1, phase_type="round_robin",
          schedule_format=season_schedule_format)`` (the Part2a default).
        * Otherwise: split on ``,``, strip each token, assign contiguous
          ordinals ``1..N`` in composer order; ``round_robin`` specs get
          ``season_schedule_format``, ``tournament`` specs get ``None``.

    Valid phase types are ``"round_robin"`` and ``"tournament"`` only
    (``"member_night"`` is NOT selectable in Part2b — it raises
    unknown-type).

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
        if token == "round_robin":
            schedule_format: Optional[str] = season_schedule_format
        elif token == "tournament":
            schedule_format = None
        else:
            raise ValueError(f"unknown phase type: {token!r}")
        specs.append(
            PhaseSpec(
                ordinal=index + 1,
                phase_type=token,
                schedule_format=schedule_format,
            )
        )

    if not any(spec.phase_type == "round_robin" for spec in specs):
        raise ValueError("composition must contain at least one round-robin phase")

    return specs
