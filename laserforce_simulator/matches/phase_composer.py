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

# LG-02-Part2c-3c — the tournament modes shipped this slice. ``random_draw``
# is DEFERRED (the picker offers it as a disabled "coming soon" option; the
# parser rejects it with the unknown-mode ValueError).
_VALID_TOURNAMENT_MODES = ("standings", "strength", "unseeded")

# LG-02-Part2c-3e — the tournament formats the per-phase build now accepts (the
# ``Tournament.FORMAT_CHOICES`` keys; kept inline so the parser stays Django-free).
_VALID_TOURNAMENT_FORMATS = (
    "single_elimination",
    "double_elimination",
    "round_robin",
    "round_robin_double_elim",
    "swiss",
)

# LG-02-Part2c-3e — valid (wb, lb) advancer combos for round_robin_double_elim.
_VALID_RRDE_COMBOS = {(4, 0), (4, 2), (8, 0), (8, 4), (16, 0), (16, 8)}

# LG-02-Part2c-3e — valid best-of-N series lengths for the four tier slots.
_VALID_SERIES_LENGTHS = (1, 3, 5)


@dataclass(frozen=True)
class PhaseSpec:
    """One ordered phase parsed from the composer wire format.

    ``ordinal`` is 1-based and contiguous (``1..N`` in composer order).
    ``phase_type`` is ``"round_robin"`` or ``"tournament"``.
    ``schedule_format`` is the season format for a round_robin phase and
    ``None`` for a tournament phase.

    LG-02-Part2c-3b/3c — ``tournament_mode`` carries the per-phase tournament
    flavour (season-ending ``standings`` vs mid-season ``strength`` /
    ``unseeded``; ``random_draw`` deferred). Parsed from the tournament token's
    second field since Part2c-3c. LG-02-Part2c-3e — ``tournament_format`` plus
    the 7 sub-config fields (series tiers / wb-lb advancers / swiss rounds)
    carry the now-live per-format build. All appended LAST with defaults so
    existing keyword constructions stay equality-identical (the Part2c-3a
    ``ScheduleFixture.leg`` precedent).
    """

    ordinal: int
    phase_type: str
    schedule_format: Optional[str]
    tournament_mode: str = "standings"
    tournament_cut: int = 0
    # LG-02-Part2c-3e — per-format sub-config carried through to the now-live
    # ``tournament_format`` build. Appended LAST with defaults so existing
    # keyword constructions stay equality-identical.
    tournament_format: str = "single_elimination"
    final_series_length: int = 1
    semifinal_series_length: int = 1
    quarterfinal_series_length: int = 1
    earlier_series_length: int = 1
    wb_advancers: int = 0
    lb_advancers: int = 0
    swiss_rounds: int = 0


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
        tournament_mode = "standings"
        tournament_cut = 0
        tournament_format = "single_elimination"
        final_series_length = 1
        semifinal_series_length = 1
        quarterfinal_series_length = 1
        earlier_series_length = 1
        wb_advancers = 0
        lb_advancers = 0
        swiss_rounds = 0
        if type_part == "round_robin":
            schedule_format: Optional[str] = format_part or season_schedule_format
            if schedule_format not in _VALID_SCHEDULE_FORMATS:
                raise ValueError(f"unknown schedule_format: {schedule_format!r}")
        elif type_part == "tournament":
            # LG-02-Part2c-3e — a ``tournament`` token's grammar is the positional
            # 11-field ``tournament:mode:cut:format:fsl:ssl:qsl:esl:wb:lb:swiss``
            # (versus the RR branch's 2-way ``partition``). Trailing-optional with
            # defaults; an empty field ⇒ malformed. ``parts[1]`` mode
            # (``"standings"``), ``[2]`` cut (``"0"``), ``[3]`` format
            # (``"single_elimination"``), ``[4..7]`` the four tier series lengths
            # (``"1"``), ``[8]`` wb (``"0"``), ``[9]`` lb (``"0"``), ``[10]``
            # swiss (``"0"``).
            schedule_format = None
            parts = token.split(":")
            # (1) length.
            if len(parts) > 11:
                raise ValueError("malformed phase composition")

            def _field(idx: int, default: str) -> str:
                if len(parts) <= idx:
                    return default
                value = parts[idx].strip()
                if not value:
                    raise ValueError("malformed phase composition")
                return value

            def _int_field(idx: int, default: str) -> int:
                try:
                    return int(_field(idx, default))
                except ValueError:
                    raise ValueError("malformed phase composition") from None

            # (2) mode.
            mode = _field(1, "standings")
            if mode not in _VALID_TOURNAMENT_MODES:
                raise ValueError(f"unknown tournament_mode: {mode!r}")
            tournament_mode = mode
            # (3) cut.
            cut = _int_field(2, "0")
            if cut != 0 and cut < 4:
                raise ValueError(f"tournament cut must be 0 or at least 4: {cut}")
            tournament_cut = cut
            # (4) format.
            fmt = _field(3, "single_elimination")
            if fmt not in _VALID_TOURNAMENT_FORMATS:
                raise ValueError(f"unknown tournament_format: {fmt!r}")
            tournament_format = fmt
            # (5) series-length tiers.
            final_series_length = _int_field(4, "1")
            semifinal_series_length = _int_field(5, "1")
            quarterfinal_series_length = _int_field(6, "1")
            earlier_series_length = _int_field(7, "1")
            for n in (
                final_series_length,
                semifinal_series_length,
                quarterfinal_series_length,
                earlier_series_length,
            ):
                if n not in _VALID_SERIES_LENGTHS:
                    raise ValueError(f"series length must be 1, 3, or 5: {n}")
            # (6) wb / lb (combo validated only for round_robin_double_elim).
            wb_advancers = _int_field(8, "0")
            lb_advancers = _int_field(9, "0")
            if fmt == "round_robin_double_elim":
                if (wb_advancers, lb_advancers) not in _VALID_RRDE_COMBOS:
                    raise ValueError(
                        "invalid wb/lb combo for round_robin_double_elim: "
                        f"{wb_advancers}/{lb_advancers}"
                    )
            # (7) swiss rounds.
            swiss_rounds = _int_field(10, "0")
        elif type_part == "member_night":
            # LG-07a — member_night carries NO sub-config (no schedule_format /
            # mode / cut / format / series / wb / lb / swiss). A bare token
            # only; a colon (any sub-config) ⇒ malformed. A member night may
            # sit anywhere, including first (the preceding-RR guard below only
            # fires for a season-ending ``standings`` tournament).
            if format_part:
                raise ValueError("malformed phase composition")
            schedule_format = None
            # tournament_* / series / wb / lb / swiss keep their declared
            # defaults (inert for a member_night phase).
        else:
            raise ValueError(f"unknown phase type: {token!r}")
        specs.append(
            PhaseSpec(
                ordinal=index + 1,
                phase_type=type_part,
                schedule_format=schedule_format,
                tournament_mode=tournament_mode,
                tournament_cut=tournament_cut,
                tournament_format=tournament_format,
                final_series_length=final_series_length,
                semifinal_series_length=semifinal_series_length,
                quarterfinal_series_length=quarterfinal_series_length,
                earlier_series_length=earlier_series_length,
                wb_advancers=wb_advancers,
                lb_advancers=lb_advancers,
                swiss_rounds=swiss_rounds,
            )
        )

    if not any(spec.phase_type == "round_robin" for spec in specs):
        raise ValueError("composition must contain at least one round-robin phase")

    # LG-02-Part2c-1 — a tournament phase must follow a round-robin phase.
    # LG-02-Part2c-3c — RELAXED: the rule now fires ONLY for a season-ending
    # ``standings`` tournament (which seeds from the preceding phase's
    # standings). ``strength`` / ``unseeded`` mid-season tournaments may sit
    # anywhere, including first.
    seen_round_robin = False
    for spec in specs:
        if spec.phase_type == "round_robin":
            seen_round_robin = True
        elif (
            spec.phase_type == "tournament"
            and spec.tournament_mode == "standings"
            and not seen_round_robin
        ):
            raise ValueError(
                "a tournament phase requires a preceding round-robin phase"
            )

    return specs
