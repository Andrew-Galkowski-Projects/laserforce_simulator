"""LG-00b roster-import pure module.

Parses a roster CSV string into a structured :class:`ParsedRoster` while
collecting every coercion / collision error into a single
:class:`RosterImportError` raise. **Pure Python** — Django-free; the only
imports are :mod:`csv`, :mod:`io`, :mod:`dataclasses`, and :mod:`typing`.

Header validation short-circuits per-row parsing (no point reading rows
under a bad header). Per-row errors accumulate across the whole file so the
view can render the full error list back to the uploader in one pass.

See ``.claude/worktrees/lg-00b-seam-contract.md`` for the locked contract.
"""

from __future__ import annotations

import csv
import dataclasses
import io

# ---------------------------------------------------------------------------
# Frozen constants (see seam contract §9)
# ---------------------------------------------------------------------------

STAT_DEFAULT: int = 50
STAT_MIN: int = 0
STAT_MAX: int = 100
MAX_DATA_ROWS: int = 1000
NAME_MAX_LEN: int = 100
TEAM_NAME_MAX_LEN: int = 100
HOME_SITE_MAX_LEN: int = 100
HEIGHT_MAX_LEN: int = 20


ROLE_NAMES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")


SLOT_LIMITS: dict[str, int] = {
    "commander": 1,
    "heavy": 1,
    "scout": 2,
    "medic": 1,
    "ammo": 1,
}


PROFILE_BOUNDS: dict[str, tuple[int, int]] = {
    "age": (5, 100),
    "started_playing_age": (3, 100),
    "total_games": (0, 100_000),
}


REQUIRED_COLUMNS: tuple[str, ...] = (
    "team",
    "name",
    "role",
    "age",
    "started_playing_age",
    "total_games",
    "home_site",
    "height",
)


# Must equal ``teams.player_generator._STAT_FIELDS`` verbatim (same 19
# names, same order, including the capital-O ``Offensive_synergy``).
# Re-declared locally to keep this module Django-free and self-contained;
# the Tests agent pins equality with a direct ``==`` assertion.
STAT_COLUMNS: tuple[str, ...] = (
    # 3 awareness
    "player_awareness",
    "game_awareness",
    "resource_awareness",
    # 1 decision
    "decision_making",
    # 5 physical
    "positioning",
    "stamina",
    "speed",
    "flexibility",
    "adaptability",
    # 2 team
    "communication",
    "teamwork",
    # 8 role — NOTE: Offensive_synergy is intentionally capital-O
    "Offensive_synergy",
    "defensive_synergy",
    "midfield_synergy",
    "resupply_synergy",
    "resupply_efficiency",
    "accuracy",
    "survival",
    "special_usage",
)


OPTIONAL_COLUMNS: tuple[str, ...] = ("preferred_roles", *STAT_COLUMNS)


ALL_COLUMNS: tuple[str, ...] = (*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class RowError:
    """A single coercion / collision error against one CSV row.

    ``row_num`` is the 1-based DATA-row index (the first data row, i.e.
    line 2 of the file, is ``row_num=1``). ``row_num=0`` is reserved for
    file-level errors (missing header column, duplicate header, etc.).
    ``field`` is the column name when the error pinpoints a specific cell,
    or ``None`` for whole-row / whole-file errors.
    """

    row_num: int
    field: str | None
    message: str


@dataclasses.dataclass(frozen=True)
class ParsedRow:
    """A single successfully-coerced data row.

    ``profile`` keys exactly match the 5 ``Player`` profile field names
    (``age``, ``started_playing_age``, ``total_games``, ``home_site``,
    ``height``) so the view can splat ``**profile`` into
    ``Player.objects.create(...)``. ``stats`` always carries all 19
    :data:`STAT_COLUMNS` keys, defaulted to :data:`STAT_DEFAULT` where the
    cell was blank or the column omitted.
    """

    row_num: int
    team: str
    name: str
    role: str
    profile: dict[str, int | str]
    stats: dict[str, int]
    preferred_roles: list[str]


@dataclasses.dataclass(frozen=True)
class ParsedRoster:
    """Result of a successful :func:`parse_roster_csv` call.

    ``rows`` is the flat CSV-order list. ``by_team`` groups the rows by
    team name in CSV-encounter order (Python 3.7+ dict insertion-order
    guarantee) — the view's primary consumption shape for team-by-team
    ORM writes.
    """

    rows: list[ParsedRow]
    by_team: dict[str, list[ParsedRow]]


# ---------------------------------------------------------------------------
# Exception
# ---------------------------------------------------------------------------


class RosterImportError(Exception):
    """Raised by :func:`parse_roster_csv` when one or more errors are detected.

    All discovered errors are bundled into a single raise — the parser does
    not raise on the first error. (Exception: header-level errors raise
    immediately without attempting per-row parsing.)
    """

    def __init__(self, errors: list[RowError]):
        self.errors = errors
        super().__init__(self._format(errors))

    @staticmethod
    def _format(errors: list[RowError]) -> str:
        return "; ".join(
            f"row {e.row_num}{':' + e.field if e.field else ''}: {e.message}"
            for e in errors
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coerce_bounded_int(
    raw: str | None,
    *,
    lo: int,
    hi: int,
) -> int | None:
    """Return ``int(raw)`` if it parses and falls in ``[lo, hi]``, else ``None``.

    A return of ``None`` signals "invalid"; callers wrap that into a
    :class:`RowError` with the appropriate field name. Blank / missing
    cells return ``None`` as well.
    """

    if raw is None:
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    try:
        val = int(stripped)
    except (TypeError, ValueError):
        return None
    if val < lo or val > hi:
        return None
    return val


def _parse_preferred_roles(
    raw: str | None,
    row_num: int,
    errors: list[RowError],
) -> list[str]:
    """Parse the optional ``preferred_roles`` cell.

    Empty / missing → ``[]``. Otherwise split on ``","``, lowercase + trim
    each entry, validate against :data:`ROLE_NAMES`, and reject
    duplicates. Errors are appended to ``errors`` in place; a malformed
    cell returns ``[]``.
    """

    if raw is None:
        return []
    stripped = raw.strip()
    if not stripped:
        return []
    parts = [p.strip().lower() for p in stripped.split(",")]
    # Drop empty fragments from things like "scout,,medic".
    parts = [p for p in parts if p]

    seen: set[str] = set()
    out: list[str] = []
    bad = False
    for entry in parts:
        if entry not in ROLE_NAMES:
            errors.append(
                RowError(
                    row_num=row_num,
                    field="preferred_roles",
                    message=f"Unknown preferred role: '{entry}'",
                )
            )
            bad = True
            continue
        if entry in seen:
            errors.append(
                RowError(
                    row_num=row_num,
                    field="preferred_roles",
                    message=f"Duplicate preferred role: '{entry}'",
                )
            )
            bad = True
            continue
        seen.add(entry)
        out.append(entry)

    if bad:
        return []
    return out


def _coerce_row(
    raw_row: dict[str, str | None],
    row_num: int,
    errors: list[RowError],
    *,
    has_preferred_roles_col: bool,
    stat_cols_present: tuple[str, ...],
) -> ParsedRow | None:
    """Coerce a single :class:`csv.DictReader` row.

    Returns a :class:`ParsedRow` on success, or ``None`` if any field on
    this row failed coercion (errors are appended to ``errors`` in place).
    """

    row_ok = True

    # team
    team_raw = (raw_row.get("team") or "").strip()
    if not team_raw:
        errors.append(
            RowError(row_num=row_num, field="team", message="team is required")
        )
        row_ok = False
    elif len(team_raw) > TEAM_NAME_MAX_LEN:
        errors.append(
            RowError(
                row_num=row_num,
                field="team",
                message=f"team is longer than {TEAM_NAME_MAX_LEN} characters",
            )
        )
        row_ok = False

    # name
    name_raw = (raw_row.get("name") or "").strip()
    if not name_raw:
        errors.append(
            RowError(row_num=row_num, field="name", message="name is required")
        )
        row_ok = False
    elif len(name_raw) > NAME_MAX_LEN:
        errors.append(
            RowError(
                row_num=row_num,
                field="name",
                message=f"name is longer than {NAME_MAX_LEN} characters",
            )
        )
        row_ok = False

    # role
    role_raw = (raw_row.get("role") or "").strip().lower()
    if role_raw not in ROLE_NAMES:
        errors.append(
            RowError(
                row_num=row_num,
                field="role",
                message=(
                    f"role '{raw_row.get('role', '')}' is not one of "
                    f"{sorted(ROLE_NAMES)}"
                ),
            )
        )
        row_ok = False

    # profile ints
    profile: dict[str, int | str] = {}
    for field, (lo, hi) in PROFILE_BOUNDS.items():
        coerced = _coerce_bounded_int(raw_row.get(field), lo=lo, hi=hi)
        if coerced is None:
            errors.append(
                RowError(
                    row_num=row_num,
                    field=field,
                    message=(
                        f"{field} must be an integer in [{lo}, {hi}]; "
                        f"got '{raw_row.get(field, '')}'"
                    ),
                )
            )
            row_ok = False
        else:
            profile[field] = coerced

    # home_site (optional value, max 100)
    home_site_raw = (raw_row.get("home_site") or "").strip()
    if len(home_site_raw) > HOME_SITE_MAX_LEN:
        errors.append(
            RowError(
                row_num=row_num,
                field="home_site",
                message=f"home_site is longer than {HOME_SITE_MAX_LEN} characters",
            )
        )
        row_ok = False
    else:
        profile["home_site"] = home_site_raw

    # height (optional value, max 20)
    height_raw = (raw_row.get("height") or "").strip()
    if len(height_raw) > HEIGHT_MAX_LEN:
        errors.append(
            RowError(
                row_num=row_num,
                field="height",
                message=f"height is longer than {HEIGHT_MAX_LEN} characters",
            )
        )
        row_ok = False
    else:
        profile["height"] = height_raw

    # 19 stat columns — default 50 when absent / blank.
    stats: dict[str, int] = {}
    for stat in STAT_COLUMNS:
        if stat not in stat_cols_present:
            stats[stat] = STAT_DEFAULT
            continue
        raw = raw_row.get(stat)
        if raw is None or raw.strip() == "":
            stats[stat] = STAT_DEFAULT
            continue
        coerced = _coerce_bounded_int(raw, lo=STAT_MIN, hi=STAT_MAX)
        if coerced is None:
            errors.append(
                RowError(
                    row_num=row_num,
                    field=stat,
                    message=(
                        f"{stat} must be an integer in [{STAT_MIN}, {STAT_MAX}]; "
                        f"got '{raw}'"
                    ),
                )
            )
            row_ok = False
        else:
            stats[stat] = coerced

    # preferred_roles
    if has_preferred_roles_col:
        preferred = _parse_preferred_roles(
            raw_row.get("preferred_roles"), row_num, errors
        )
    else:
        preferred = []

    if not row_ok:
        return None

    return ParsedRow(
        row_num=row_num,
        team=team_raw,
        name=name_raw,
        role=role_raw,
        profile=profile,
        stats=stats,
        preferred_roles=preferred,
    )


def _check_in_file_collisions(
    rows: list[ParsedRow],
    errors: list[RowError],
) -> None:
    """Append in-file duplicate / slot-overflow errors to ``errors``.

    Two passes:
      1. Same ``(team, name)`` twice → flag the second occurrence.
      2. Same ``(team, role)`` overflows :data:`SLOT_LIMITS` → flag the
         offending row.
    """

    seen_pairs: dict[tuple[str, str], int] = {}
    role_counts: dict[tuple[str, str], int] = {}

    for row in rows:
        pair = (row.team, row.name)
        if pair in seen_pairs:
            errors.append(
                RowError(
                    row_num=row.row_num,
                    field="name",
                    message=(
                        f"Duplicate (team, name) - first seen at row "
                        f"{seen_pairs[pair]}"
                    ),
                )
            )
        else:
            seen_pairs[pair] = row.row_num

        role_key = (row.team, row.role)
        role_counts[role_key] = role_counts.get(role_key, 0) + 1
        limit = SLOT_LIMITS.get(row.role)
        if limit is not None and role_counts[role_key] > limit:
            errors.append(
                RowError(
                    row_num=row.row_num,
                    field="role",
                    message=(
                        f"Too many rows for role '{row.role}' on team "
                        f"'{row.team}' (limit {limit})"
                    ),
                )
            )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_roster_csv(text: str) -> ParsedRoster:
    """Parse a roster-import CSV string into a :class:`ParsedRoster`.

    PURE: no Django imports, no I/O. The caller owns file reading and
    decoding (UTF-8 with BOM tolerance is the caller's job, BUT this
    function tolerates a leading BOM defensively by stripping a single
    leading ``"\\ufeff"`` before parsing).

    Behaviour:
      1. Wrap ``text`` in :class:`io.StringIO`, feed to
         :class:`csv.DictReader`.
      2. Validate the header row against :data:`ALL_COLUMNS`:
         missing required / unknown / duplicate column →
         :class:`RosterImportError` raised immediately.
      3. Walk every data row, coerce per the seam contract §2d,
         accumulate :class:`RowError` instances. Stop reading after
         :data:`MAX_DATA_ROWS` + 1 rows and append the "exceeds" error.
      4. After the walk, run the in-file duplicate / slot-overflow pass.
      5. If any errors accumulated → raise :class:`RosterImportError`.
         Otherwise return a :class:`ParsedRoster`.

    Args:
        text: The full CSV file as a Python ``str``. Caller decodes from
              bytes (UTF-8, BOM-tolerated).

    Returns:
        :class:`ParsedRoster` — guaranteed coercion-valid and free of
        in-file collisions.

    Raises:
        :class:`RosterImportError`: any header-level or per-row coercion
        or in-file collision error.
    """

    # Belt-and-suspenders: strip a single leading BOM if the caller's
    # decode left one through. The view's form decodes with ``utf-8-sig``
    # so this is normally a no-op.
    if text.startswith("﻿"):
        text = text[1:]

    reader = csv.DictReader(io.StringIO(text))

    # ---- header validation (file-level) -----------------------------------
    fieldnames = reader.fieldnames or []
    header_errors: list[RowError] = []

    seen_headers: set[str] = set()
    for header in fieldnames:
        if header in seen_headers:
            header_errors.append(
                RowError(
                    row_num=0,
                    field=header,
                    message=f"Duplicate column: {header}",
                )
            )
        seen_headers.add(header)

    allowed = set(ALL_COLUMNS)
    for header in fieldnames:
        if header not in allowed:
            header_errors.append(
                RowError(
                    row_num=0,
                    field=header,
                    message=f"Unknown column: {header}",
                )
            )

    for required in REQUIRED_COLUMNS:
        if required not in fieldnames:
            header_errors.append(
                RowError(
                    row_num=0,
                    field=required,
                    message=f"Missing required column: {required}",
                )
            )

    if header_errors:
        raise RosterImportError(header_errors)

    has_preferred_roles_col = "preferred_roles" in fieldnames
    stat_cols_present: tuple[str, ...] = tuple(
        s for s in STAT_COLUMNS if s in fieldnames
    )

    # ---- per-row walk -----------------------------------------------------
    row_errors: list[RowError] = []
    parsed_rows: list[ParsedRow] = []
    cap_exceeded = False

    for idx, raw_row in enumerate(reader, start=1):
        if idx > MAX_DATA_ROWS:
            row_errors.append(
                RowError(
                    row_num=0,
                    field=None,
                    message=f"CSV exceeds {MAX_DATA_ROWS} data rows",
                )
            )
            cap_exceeded = True
            break

        parsed = _coerce_row(
            raw_row,
            row_num=idx,
            errors=row_errors,
            has_preferred_roles_col=has_preferred_roles_col,
            stat_cols_present=stat_cols_present,
        )
        if parsed is not None:
            parsed_rows.append(parsed)

    # ---- in-file collisions (only when not over the cap) -----------------
    if not cap_exceeded:
        _check_in_file_collisions(parsed_rows, row_errors)

    if row_errors:
        raise RosterImportError(row_errors)

    # ---- group by team in CSV-encounter order ----------------------------
    by_team: dict[str, list[ParsedRow]] = {}
    for row in parsed_rows:
        by_team.setdefault(row.team, []).append(row)

    return ParsedRoster(rows=parsed_rows, by_team=by_team)


__all__ = [
    "ALL_COLUMNS",
    "HEIGHT_MAX_LEN",
    "HOME_SITE_MAX_LEN",
    "MAX_DATA_ROWS",
    "NAME_MAX_LEN",
    "OPTIONAL_COLUMNS",
    "PROFILE_BOUNDS",
    "ParsedRoster",
    "ParsedRow",
    "REQUIRED_COLUMNS",
    "ROLE_NAMES",
    "RosterImportError",
    "RowError",
    "SLOT_LIMITS",
    "STAT_COLUMNS",
    "STAT_DEFAULT",
    "STAT_MAX",
    "STAT_MIN",
    "TEAM_NAME_MAX_LEN",
    "parse_roster_csv",
]
