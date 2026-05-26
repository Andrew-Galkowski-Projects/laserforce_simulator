# LG-00b Seam Contract — Roster Import from CSV

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in
parallel. Names below are frozen — do not rename, do not add fields, do not
re-litigate the policy decisions in §0. If reality contradicts a name here,
STOP and flag; do not silently drift.

Branch: `lg-00b-roster-import` (already checked out).
All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

---

## 0. Resolved decisions (DO NOT re-open)

These are baked into the contract. Code / Tests / Docs agents must NOT
re-litigate them:

- **Multi-team CSV.** A `team` column routes rows to Teams: append to existing
  Teams (matched by exact name), auto-create missing Teams.
- **`role` column drives slot assignment ONLY** (`team.slot_<role>` FK).
  `preferred_roles` is a SEPARATE optional column (comma-separated within the
  cell) that populates the `Player.preferred_roles` JSON list. The two
  columns do NOT cross-influence each other.
- **All-or-nothing under `@transaction.atomic`.** Any error rejects the whole
  file; nothing is written.
- **Required columns (8):** `team, name, role, age, started_playing_age,
  total_games, home_site, height`.
- **Optional columns (20):** `preferred_roles` + the 19 stat columns. A
  missing stat cell or omitted stat column → defaults to `50`.
- **Strict exact-match headers (case-sensitive).** Including the capital-`O`
  `Offensive_synergy`. Unknown headers → hard error.
- **Format:** comma-delimited, UTF-8 (BOM tolerated), first-row header,
  **1000-row data cap**, parsed via stdlib `csv.DictReader`.
- **Entry point:** `GET/POST /teams/import/` (link added to
  `templates/teams/team_list.html`).
- **Template download:** `GET /teams/import/template.csv`.
- **CONTEXT.md entry "Roster import" was added in the grilling session — do
  NOT re-add it.** Docs agent only references it.
- **Slot collision policies (ALL HARD REJECT, no warnings):**
  1. Existing Team already has that `slot_<role>` filled (any non-Scout
     role), OR has BOTH Scout slots filled.
  2. Same CSV produces > 1 row of any non-Scout role on the same team.
  3. Same CSV produces > 2 Scouts on the same team.
  4. Same `(team, name)` pair appears twice in the file.

---

## 1. New public names (frozen)

| Kind | Name | Location |
|------|------|----------|
| Module | `teams/roster_importer.py` (new, pure) | `teams/roster_importer.py` |
| Dataclass | `RowError(row_num: int, field: str \| None, message: str)` | `teams/roster_importer.py` |
| Dataclass | `ParsedRoster(rows: list[ParsedRow], by_team: dict[str, list[ParsedRow]])` | `teams/roster_importer.py` |
| Dataclass | `ParsedRow(row_num: int, team: str, name: str, role: str, profile: dict[str, int \| str], stats: dict[str, int], preferred_roles: list[str])` | `teams/roster_importer.py` |
| Exception | `RosterImportError(Exception)` with `errors: list[RowError]` | `teams/roster_importer.py` |
| Pure function | `parse_roster_csv(text: str) -> ParsedRoster` | `teams/roster_importer.py` |
| Module tuple | `REQUIRED_COLUMNS: tuple[str, ...]` (8 entries — pinned in §2c) | `teams/roster_importer.py` |
| Module tuple | `OPTIONAL_COLUMNS: tuple[str, ...]` (20 entries — pinned in §2c) | `teams/roster_importer.py` |
| Module tuple | `STAT_COLUMNS: tuple[str, ...]` (19 entries — equals `_STAT_FIELDS` from `teams/player_generator.py`) | `teams/roster_importer.py` |
| Module tuple | `ROLE_NAMES: tuple[str, ...]` (5: commander, heavy, scout, medic, ammo) | `teams/roster_importer.py` |
| Module tuple | `ALL_COLUMNS: tuple[str, ...]` (28 — required 8 + optional 20, declared order) | `teams/roster_importer.py` |
| Module dict | `SLOT_LIMITS: dict[str, int]` = `{"commander": 1, "heavy": 1, "scout": 2, "medic": 1, "ammo": 1}` | `teams/roster_importer.py` |
| Module dict | `PROFILE_BOUNDS: dict[str, tuple[int, int]]` (re-pinned in §2d) | `teams/roster_importer.py` |
| Module constants | `STAT_DEFAULT = 50`, `MAX_DATA_ROWS = 1000`, `STAT_MIN = 0`, `STAT_MAX = 100`, `HOME_SITE_MAX_LEN = 100`, `HEIGHT_MAX_LEN = 20`, `NAME_MAX_LEN = 100`, `TEAM_NAME_MAX_LEN = 100` | `teams/roster_importer.py` |
| Form | `RosterImportForm(forms.Form)` with `csv_file: forms.FileField` | `teams/forms.py` |
| Form constant | `MAX_UPLOAD_BYTES = 2 * 1024 * 1024` (= 2 MiB) | `teams/forms.py` |
| View | `import_roster(request)` | `teams/views.py` |
| View | `import_roster_template(request)` | `teams/views.py` |
| URL name | `import_roster` | `teams/urls.py` |
| URL name | `import_roster_template` | `teams/urls.py` |
| Template — form | `templates/teams/roster_import.html` (new) | `templates/teams/roster_import.html` |
| Template — confirm | `templates/teams/roster_import_done.html` (new) | `templates/teams/roster_import_done.html` |
| Template link | "Import Roster" anchor → `{% url 'import_roster' %}` | `templates/teams/team_list.html` |

No model field change, no migration, no ADR, no new dependency.
The pure module is Django-free; the view owns all Django-facing concerns
(form, transaction, ORM writes).

---

## 2. The pure module seam (MOST IMPORTANT — view ↔ pure-Python boundary)

`teams/roster_importer.py` is **pure Python**. The Tests agent pins this with
a defensive "no Django imports leaked" subprocess check (see §11.1).

### 2a. Import allowlist (frozen)

The module may import **only** from:

- `csv` (stdlib — `DictReader` and `Sniffer` if needed).
- `io` (stdlib — `StringIO` to wrap the input string for `DictReader`).
- `dataclasses` (stdlib — for `RowError`, `ParsedRow`, `ParsedRoster`).
- `typing` (stdlib — annotations only).

The module must **NOT** import:

- `django.*` (no models, no ORM, no forms, no settings, no template engine,
  no exceptions module).
- `teams.models`, `teams.forms`, `teams.views`, `teams.constants`,
  `teams.player_generator` (the 19-stat tuple is re-declared locally so the
  contract is self-contained — see §2c).
- `matches.*` (no role-constants import — role names are hand-rolled).
- any I/O module (no file I/O, no network, no `os.path`).

The same "pure" discipline as RES-04 / RV-03 / HX-01 / HX-02 / LG-00's pure
modules.

### 2b. Canonical role-name and slot tuples (frozen, module-local)

Hand-rolled inside `roster_importer.py` (mirror LG-00's local declaration —
no import from `player_generator.py`):

```python
ROLE_NAMES: tuple[str, ...] = ("commander", "heavy", "scout", "medic", "ammo")

SLOT_LIMITS: dict[str, int] = {
    "commander": 1,
    "heavy": 1,
    "scout": 2,
    "medic": 1,
    "ammo": 1,
}
```

### 2c. Column tuples (frozen, exact order — drives the template-CSV order)

```python
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

# STAT_COLUMNS must equal teams.player_generator._STAT_FIELDS verbatim
# (same 19 names, same order, including capital-O Offensive_synergy).
# Re-declared locally to keep this module Django-free and self-contained.
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
```

The test agent pins `STAT_COLUMNS == player_generator._STAT_FIELDS` with a
direct equality assertion (this is the ONE allowed `teams.player_generator`
import inside the pure-unit test file — see §11.1).

`ALL_COLUMNS` has exactly 28 entries (8 + 1 + 19). The template-CSV view in
§5 serialises columns in this exact order.

### 2d. Coercion + bounds (frozen)

Profile bounds — re-declared locally so the Code agent does NOT import
`_PROFILE_BOUNDS` from `teams/forms.py` (keeps the pure module Django-free):

```python
PROFILE_BOUNDS: dict[str, tuple[int, int]] = {
    "age": (5, 100),
    "started_playing_age": (3, 100),
    "total_games": (0, 100_000),
}
```

Per-cell coercion rules (every rule below is enforced inside
`parse_roster_csv`; failures append a `RowError` and do NOT raise immediately
— per-row errors accumulate):

| Column(s) | Rule |
|-----------|------|
| `team` | Stripped string, non-empty, max 100 chars. Empty → row error. |
| `name` | Stripped string, non-empty, max 100 chars. Empty → row error. |
| `role` | Lowercased + stripped; must be in `ROLE_NAMES`. Not-in → row error. |
| `age` | `int(cell)`; must satisfy `5 <= v <= 100`. Non-int / out-of-range → row error. |
| `started_playing_age` | `int(cell)`; must satisfy `3 <= v <= 100`. Non-int / out-of-range → row error. |
| `total_games` | `int(cell)`; must satisfy `0 <= v <= 100_000`. Non-int / out-of-range → row error. |
| `home_site` | Stripped string. Empty allowed (becomes `""`). Max 100 chars. |
| `height` | Stripped string. Empty allowed (becomes `""`). Max 20 chars. |
| `preferred_roles` (optional) | Cell split on `","`, each entry stripped + lowercased; empty cell or column absent → `[]`. Each non-empty entry must be in `ROLE_NAMES`; duplicates within the cell → row error. |
| Each of the 19 stat columns (optional) | Cell omitted / blank / column absent → `STAT_DEFAULT` (= `50`). Otherwise `int(cell)`; must satisfy `0 <= v <= 100`. Non-int / out-of-range → row error. |

**Header-level (file-level) errors** raise `RosterImportError` IMMEDIATELY
without attempting per-row parsing — there is no point reading rows under a
bad header:

- Missing any of the 8 `REQUIRED_COLUMNS` → single `RowError(row_num=0,
  field=<missing column>, message="Missing required column: <name>")`.
- Unknown header (any header not in `ALL_COLUMNS`) → single `RowError(row_num=0,
  field=<unknown column>, message="Unknown column: <name>")`. Multiple
  unknown headers MAY accumulate into the same raised `RosterImportError`.
- Duplicate header (same column appears twice) → `RowError(row_num=0,
  field=<duplicate column>, message="Duplicate column: <name>")`. Header-level
  raise.
- More than `MAX_DATA_ROWS` (1000) data rows → single `RowError(row_num=0,
  field=None, message="CSV exceeds 1000 data rows")`. Raised after the cap
  is exceeded — the parser does not need to count all rows; it may short-
  circuit on hitting row 1001.

**Per-row errors** are collected across the whole file and raised in a single
`RosterImportError(errors=...)` after the loop finishes (no per-row early
return). When per-row errors exist, no `ParsedRoster` is returned; the view
sees the raised exception only.

**In-file duplicate / collision detection** runs after per-row coercion as
an extra pass before returning `ParsedRoster`:

- Same `(team, name)` appears twice → `RowError(row_num=<second occurrence>,
  field="name", message="Duplicate (team, name) — first seen at row N")`.
- Same `(team, role)` produces more rows than `SLOT_LIMITS[role]` allows →
  `RowError(row_num=<the row that overflows>, field="role",
  message="Too many rows for role '<role>' on team '<team>' (limit N)")`.
  (Non-Scout overflow fires on the 2nd occurrence; Scout overflow fires on
  the 3rd occurrence.)

These in-file collisions accumulate alongside per-row coercion errors and
share the same single `RosterImportError` raise.

### 2e. `RowError` (frozen)

```python
@dataclasses.dataclass(frozen=True)
class RowError:
    row_num: int            # 1-based DATA row (excluding header). 0 = file-level.
    field: str | None       # Column name; None for whole-row errors.
    message: str            # Human-readable; the template renders this verbatim.
```

Pins:

- `frozen=True` so `RowError` is hashable; tests may put them in sets.
- `row_num` is **1-based DATA row index** — the first data row (line 2 of
  the file) is `row_num=1`. The header is line 1 of the file; file-level
  errors use `row_num=0` so the template can sort errors top-to-bottom
  with file-level errors first.
- `field` is `None` only for whole-row or whole-file errors.

### 2f. `ParsedRow` and `ParsedRoster` (frozen)

```python
@dataclasses.dataclass(frozen=True)
class ParsedRow:
    row_num: int                          # 1-based data row.
    team: str                             # Stripped team name.
    name: str                             # Stripped player name.
    role: str                             # Lowercased role from ROLE_NAMES.
    profile: dict[str, int | str]         # 5 keys: age, started_playing_age,
                                          # total_games, home_site, height.
    stats: dict[str, int]                 # 19 keys (every STAT_COLUMNS entry,
                                          # defaulted to 50 where blank).
    preferred_roles: list[str]            # 0–5 unique role names.


@dataclasses.dataclass(frozen=True)
class ParsedRoster:
    rows: list[ParsedRow]                 # All parsed rows in CSV order.
    by_team: dict[str, list[ParsedRow]]   # team_name → rows on that team,
                                          # in CSV encounter order. Insertion-
                                          # ordered dict — teams appear in
                                          # the order they FIRST appear in
                                          # the file.
```

Pins:

- Both dataclasses `frozen=True`.
- `ParsedRoster.rows` is the flat CSV-order list (used by the view for
  diagnostics + raw iteration).
- `ParsedRoster.by_team` is the view's primary consumption shape:
  team-by-team ORM writes. Insertion order matches first-appearance order
  in the CSV (Python 3.7+ dict insertion-order guarantee).
- `profile` keys are exactly `{"age", "started_playing_age",
  "total_games", "home_site", "height"}` — the view will pass `**profile`
  into `Player.objects.create(...)` and the keys must match `Player` field
  names byte-for-byte.
- `stats` always has all 19 `STAT_COLUMNS` keys (defaulted to 50 where
  blank); the view splats `**stats` into `Player.objects.create(...)`.

### 2g. `RosterImportError` (frozen)

```python
class RosterImportError(Exception):
    """Raised by parse_roster_csv when one or more errors are detected.

    All discovered errors are bundled into a single raise — the parser does
    not raise on the first error. (Exception: header-level errors raise
    immediately without attempting per-row parsing.)
    """

    def __init__(self, errors: list[RowError]):
        self.errors = errors
        # Build the .args/__str__ message lazily — tests inspect .errors.
        super().__init__(self._format(errors))

    @staticmethod
    def _format(errors: list[RowError]) -> str:
        return "; ".join(
            f"row {e.row_num}{':' + e.field if e.field else ''}: {e.message}"
            for e in errors
        )
```

Pins:

- The constructor signature is `__init__(self, errors: list[RowError])` —
  exactly one positional arg. Tests construct it directly to assert
  `.errors` is wired.
- `errors` is a `list[RowError]`. The view re-raises it and may EXTEND
  `errors` with DB-level collisions detected during the pre-flight check
  in §3.

### 2h. `parse_roster_csv` (frozen)

```python
def parse_roster_csv(text: str) -> ParsedRoster:
    """Parse a roster-import CSV string into a ParsedRoster.

    PURE: no Django imports, no I/O. The caller owns file reading and decoding
    (UTF-8 with BOM tolerance is the caller's job, BUT this function tolerates
    a leading BOM defensively by stripping a single leading "\\ufeff" before
    parsing).

    Behaviour:
      1. Wrap `text` in io.StringIO, feed to csv.DictReader.
      2. Validate the header row against ALL_COLUMNS:
         - Missing required column        -> immediate RosterImportError.
         - Unknown column                  -> immediate RosterImportError.
         - Duplicate column                -> immediate RosterImportError.
      3. Walk every data row, coerce per §2d, accumulate RowErrors into a list.
         Stop reading after MAX_DATA_ROWS+1 rows and append the "exceeds"
         RowError.
      4. After the walk, run the in-file duplicate / slot-overflow pass and
         append RowErrors as needed.
      5. If any RowErrors accumulated -> raise RosterImportError(errors=...).
         Otherwise return ParsedRoster(rows=..., by_team=...).

    Args:
        text: The full CSV file as a Python str. Caller decodes from bytes
              (UTF-8, BOM-tolerated).

    Returns:
        ParsedRoster — guaranteed coercion-valid + in-file-collision-free.

    Raises:
        RosterImportError: any header-level or per-row coercion or in-file
        collision error.
    """
```

Pins:

- Function signature: `parse_roster_csv(text: str) -> ParsedRoster`.
- Single string argument. No `**kwargs`, no optional flags.
- BOM tolerance: the function strips a single leading `"﻿"` if present
  before invoking `csv.DictReader` — the form ALSO decodes with
  `bytes.decode("utf-8-sig")`, so double-tolerance is intentional belt-and-
  suspenders.
- The parser does NOT make any ORM call, does NOT consult Django settings.

---

## 3. View contract (`teams/views.py`)

Two new view functions. Both are non-authenticated (no auth in this project).

### 3a. `import_roster(request)`

```python
@transaction.atomic
def import_roster(request):
    """LG-00b roster-import surface.

    GET  -> render the form (status 200).
    POST -> validate form; decode file; call parse_roster_csv; run the DB
            slot-collision pre-check; create Teams (auto-create missing)
            and Players inside a single transaction; render the confirmation
            page (status 200). On ANY error, render the form page with the
            error list (status 200) and write nothing.
    """
```

Behavioural pins:

- `request.method == "GET"` → render `templates/teams/roster_import.html`
  with `{"form": RosterImportForm(), "errors": [], "row_errors": []}`.
  Status **200**.
- `request.method == "POST"` → instantiate `RosterImportForm(request.POST,
  request.FILES)`. If `form.is_valid()` is False, re-render the form page
  with `{"form": form, "errors": [], "row_errors": []}`. Status **200**.
- On valid POST, in order:
  1. Read `form.cleaned_data["csv_file"]` (already decoded to `str` by
     the form — see §4). Call `parse_roster_csv(text)`.
  2. **DB slot-collision pre-check** (only reached if `parse_roster_csv`
     succeeds):
     - For each `(team_name, rows)` in `parsed.by_team.items()`:
       - If a `Team` with that name does NOT exist, skip — nothing to
         collide with.
       - Else, for each `row` in `rows` compute the target slot field
         (`slot_<role>` for non-Scout; first free `slot_scout_1` /
         `slot_scout_2` for Scout). If the slot is already filled
         (`team.slot_X is not None`), append a `RowError(row_num=row.row_num,
         field="role", message="Team '<team>' slot '<slot_key>' already
         filled by player '<existing_player.name>'")`.
       - For Scouts: if existing team has both `slot_scout_1` AND
         `slot_scout_2` filled (and any CSV row wants Scout for that team),
         emit ONE `RowError` per excess Scout row.
     - If any DB collisions detected, raise `RosterImportError(errors=...)`.
  3. **ORM writes**, per Team in CSV-encounter order (the iteration order
     of `parsed.by_team`):
     - `team, _ = Team.objects.get_or_create(name=team_name)` — auto-creates
       missing teams; existing teams are appended to.
     - For each `row` in `rows` (CSV order):
       - `player = Player.objects.create(team=team, name=row.name,
         preferred_roles=row.preferred_roles, **row.profile, **row.stats)`.
       - Determine the slot key:
         - Non-Scout: `slot_key = f"slot_{row.role}"`.
         - Scout: pick `slot_scout_1` if `team.slot_scout_1 is None`, else
           `slot_scout_2`.
       - `setattr(team, slot_key, player)` (in-memory only).
     - `team.save()` once after all rows for this team are processed.
  4. Render `templates/teams/roster_import_done.html` with context
     `{"created_teams": list[Team], "appended_teams": list[Team],
     "player_count": int, "row_count": int}`. Status **200**.

- **Error handling.** Catch `RosterImportError` at the top of the POST
  branch (wrapping steps 1–3 above). On catch, re-render the form page
  with `{"form": form, "errors": ["<the exception summary string>"],
  "row_errors": exc.errors}`. Status **200**. Because the view is wrapped
  in `@transaction.atomic`, raising and catching `RosterImportError` would
  NOT by itself roll back writes — but no writes have happened yet at the
  point the `RosterImportError` is raised in steps 1 and 2 (parser pre-DB,
  DB pre-check pre-write). For step 3 mid-flow raises (e.g. an
  unanticipated `IntegrityError`), the `@transaction.atomic` decorator on
  the outer view handler ensures rollback when the exception propagates
  past the catch.

  Concrete pattern (the Code agent implements this shape):

  ```python
  @transaction.atomic
  def import_roster(request):
      if request.method == "POST":
          form = RosterImportForm(request.POST, request.FILES)
          if form.is_valid():
              try:
                  parsed = parse_roster_csv(form.cleaned_data["csv_file"])
                  _check_db_slot_collisions(parsed)  # raises RosterImportError
                  created, appended, player_count = _apply_roster(parsed)
              except RosterImportError as exc:
                  transaction.set_rollback(True)
                  return render(
                      request,
                      "teams/roster_import.html",
                      {"form": form, "errors": [str(exc)], "row_errors": exc.errors},
                  )
              return render(
                  request,
                  "teams/roster_import_done.html",
                  {
                      "created_teams": created,
                      "appended_teams": appended,
                      "player_count": player_count,
                      "row_count": len(parsed.rows),
                  },
              )
      else:
          form = RosterImportForm()
      return render(
          request,
          "teams/roster_import.html",
          {"form": form, "errors": [], "row_errors": []},
      )
  ```

  The two private helpers `_check_db_slot_collisions(parsed)` and
  `_apply_roster(parsed)` are module-private functions in `teams/views.py`.
  Their exact names are pinned here so test monkey-patching is stable.
  `_apply_roster` returns `(created_teams, appended_teams, player_count)`
  where `created_teams` is the list of Teams created during this call (in
  CSV encounter order) and `appended_teams` is the list of pre-existing
  Teams that received new players. A team can appear in only ONE of the
  two lists per call.

  `transaction.set_rollback(True)` inside the catch block is mandatory —
  it tells the atomic block to roll back at the end even though we are
  returning a 200 response. Tests will pin this with a "no Team / Player
  rows written when DB-collision raised" assertion.

### 3b. `import_roster_template(request)`

```python
def import_roster_template(request):
    """GET-only download of the canonical roster CSV template.

    Returns a HttpResponse with content-type text/csv and a
    Content-Disposition header so the browser saves it as
    "roster_template.csv".

    Body shape (frozen — pinned in §5):
      Line 1: header row of ALL_COLUMNS, comma-joined.
      Lines 2-3: two example data rows.
      Body uses "\r\n" line terminators (csv.writer default on Windows-
      compatible output).
    """
```

Pins:

- No decorator. Any HTTP method is accepted (Django default); tests
  exercise the GET path only.
- Returns `HttpResponse(body, content_type="text/csv")` with the header
  `response["Content-Disposition"] = 'attachment; filename="roster_template.csv"'`.
- Status **200**.
- The exact body is pinned byte-for-byte in §5 so the test can assert
  equality.

### 3c. Imports needed in `teams/views.py`

The Code agent adds to the existing import block:

```python
from django.http import HttpResponse
from .forms import RosterImportForm   # added alongside the existing form imports
from .roster_importer import (
    RosterImportError,
    SLOT_LIMITS,
    parse_roster_csv,
)
```

`@transaction.atomic` is already imported (used by `generate_players`).
`render` is already imported. No other new imports.

---

## 4. Form contract (`teams/forms.py`)

New form class appended to `teams/forms.py` (the module already exists; do
NOT create a new file).

```python
class RosterImportForm(forms.Form):
    """LG-00b roster-import form: single CSV file upload.

    `clean_csv_file` decodes the upload as UTF-8 (BOM tolerated via
    `utf-8-sig`) and stores the decoded text on `cleaned_data["csv_file"]`
    so the view consumes a str, not an UploadedFile.
    """

    MAX_UPLOAD_BYTES = 2 * 1024 * 1024  # 2 MiB — comfortably > 1000 rows.

    csv_file = forms.FileField(
        label="Roster CSV",
        widget=forms.ClearableFileInput(
            attrs={
                "id": "roster-import-file",
                "accept": ".csv,text/csv",
                "class": "form-control",
            }
        ),
    )

    def clean_csv_file(self):
        uploaded = self.cleaned_data["csv_file"]
        if uploaded.size > self.MAX_UPLOAD_BYTES:
            raise forms.ValidationError(
                f"CSV file is too large (max {self.MAX_UPLOAD_BYTES} bytes)"
            )
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise forms.ValidationError(
                "CSV file must be UTF-8 encoded"
            ) from exc
        return text
```

Pins:

- Field name: `csv_file` (singular). Widget id: `roster-import-file`.
- `MAX_UPLOAD_BYTES = 2 * 1024 * 1024` — bytes, not megabytes; 2 MiB
  exactly. Tests assert against this constant.
- Decodes with `"utf-8-sig"` so an Excel-generated BOM is tolerated.
- Returns a **str** from `clean_csv_file` — `cleaned_data["csv_file"]` is
  the decoded CSV text, not an `UploadedFile`. The view consumes the str
  directly.
- Locked error wording for file-too-large: substring `"too large"` (case
  preserved). Tests substring-match.
- Locked error wording for non-UTF-8: substring `"must be UTF-8"`. Tests
  substring-match.
- Row-cap enforcement is the PURE MODULE's job, NOT the form's. The form
  only enforces the byte cap (which is a defensive ceiling, not a row
  count).

---

## 5. Template-CSV companion view body (frozen, byte-for-byte)

The body of `GET /teams/import/template.csv` is exactly the following
(joined with `csv.writer`'s default `"\r\n"` terminator — note the trailing
`"\r\n"` after the last row, which `csv.writer` adds by default):

**Header row (line 1, 28 columns in `ALL_COLUMNS` order):**

```
team,name,role,age,started_playing_age,total_games,home_site,height,preferred_roles,player_awareness,game_awareness,resource_awareness,decision_making,positioning,stamina,speed,flexibility,adaptability,communication,teamwork,Offensive_synergy,defensive_synergy,midfield_synergy,resupply_synergy,resupply_efficiency,accuracy,survival,special_usage
```

**Example row 1 (line 2, Red Phoenix Commander):**

```
Red Phoenix,Alice,commander,28,16,120,Ultrazone Chicago,5'7",commander,75,70,65,80,72,68,74,66,71,78,80,82,55,60,50,60,65,72,55
```

**Example row 2 (line 3, Red Phoenix Scout with comma-split preferred_roles):**

```
Red Phoenix,Bob,scout,24,18,85,Ultrazone Chicago,5'10","scout,medic",60,62,58,70,65,80,85,78,72,65,68,55,62,60,58,55,82,75,60
```

Pins:

- The example rows belong to a single team `"Red Phoenix"` so the file
  demonstrates: (a) the team-grouping behaviour, (b) the comma-split
  `preferred_roles` quoting requirement, (c) a comma inside a quoted CSV
  cell. The Code agent uses `csv.writer` (NOT manual string formatting) so
  the `"scout,medic"` cell is auto-quoted correctly.
- The body terminates with a trailing `"\r\n"` after row 3 — this is
  `csv.writer`'s default behaviour. Tests assert body bytes equal a frozen
  expected string.
- The Tests agent constructs the expected body in code using `csv.writer`
  + `io.StringIO` (mirroring what the view does) rather than embedding a
  bytestring literal — this keeps the test resilient to platform-specific
  line endings while still being exact.

---

## 6. URL contract (`teams/urls.py`)

Add two new entries to the existing `urlpatterns` list. Insert them BEFORE
the `<int:team_id>/` capture-group routes so the literal segment does not
risk shadowing (defensive — `import/` does not numerically match `<int>`
anyway, but ordering is part of the contract):

```python
path("import/", views.import_roster, name="import_roster"),
path("import/template.csv", views.import_roster_template, name="import_roster_template"),
```

Full URLs (mounted at `/teams/`):

- `/teams/import/` — name `import_roster`.
- `/teams/import/template.csv` — name `import_roster_template`.

Tests reverse via `reverse("import_roster")` and
`reverse("import_roster_template")` — no `app_name:` prefix (consistent
with existing `teams/urls.py`).

---

## 7. Template contracts

### 7a. `templates/teams/roster_import.html` (NEW — form page)

Extends `base.html`. Wireframe pinning DOM ids and copy substrings (Code
agent fills in styling):

```django
{% extends 'base.html' %}

{% block title %}Import Roster - Laserforce Manager{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Import Roster from CSV</h1>
    <p>
        Upload a roster CSV (max 1000 rows). Required columns:
        team, name, role, age, started_playing_age, total_games,
        home_site, height. Stat columns are optional (default 50).
        <a href="{% url 'import_roster_template' %}" id="roster-import-template-link">Download a template CSV</a>.
    </p>

    <form method="post" enctype="multipart/form-data" id="roster-import-form">
        {% csrf_token %}
        <div class="mb-3">
            <label for="roster-import-file" class="form-label">Roster CSV</label>
            {{ form.csv_file }}
            {% if form.csv_file.errors %}
                <div class="text-danger">{{ form.csv_file.errors }}</div>
            {% endif %}
        </div>

        {% if errors %}
            <div class="alert alert-danger" id="roster-import-errors-summary">
                {% for err in errors %}<p>{{ err }}</p>{% endfor %}
            </div>
        {% endif %}

        {% if row_errors %}
            <ul id="roster-import-errors" class="alert alert-danger">
                {% for err in row_errors %}
                    <li id="roster-import-error-{{ err.row_num }}-{% if err.field %}{{ err.field }}{% else %}row{% endif %}">
                        Row {{ err.row_num }}{% if err.field %} (field: {{ err.field }}){% endif %}: {{ err.message }}
                    </li>
                {% endfor %}
            </ul>
        {% endif %}

        <button type="submit" id="roster-import-submit" class="btn btn-primary">Import</button>
        <a href="{% url 'team_list' %}" class="btn btn-secondary">Cancel</a>
    </form>
</div>
{% endblock %}
```

Locked DOM ids:

| Element | Locked id |
|---------|-----------|
| `<form>` | `roster-import-form` |
| `<input type="file">` | `roster-import-file` |
| Submit `<button>` | `roster-import-submit` |
| Template-download `<a>` | `roster-import-template-link` |
| Errors `<ul>` | `roster-import-errors` |
| Errors summary `<div>` | `roster-import-errors-summary` |
| Per-error `<li>` | `roster-import-error-{row_num}-{field|"row"}` |

For the per-error `<li>` id, `{field}` is the literal field name when
`err.field is not None`, otherwise the literal string `"row"`. Example:
a `RowError(row_num=3, field="age", message=...)` gets
`id="roster-import-error-3-age"`; a `RowError(row_num=5, field=None,
message=...)` gets `id="roster-import-error-5-row"`.

### 7b. `templates/teams/roster_import_done.html` (NEW — confirmation page)

Extends `base.html`. Wireframe:

```django
{% extends 'base.html' %}

{% block title %}Roster Import Complete - Laserforce Manager{% endblock %}

{% block content %}
<div class="container mt-4">
    <h1>Import complete</h1>

    <div id="roster-import-confirm-summary">
        Imported <strong>{{ player_count }}</strong> players across
        <strong>{{ row_count }}</strong> rows.
    </div>

    {% if created_teams %}
        <h2>Created teams</h2>
        <ul id="roster-import-confirm-teams-list">
            {% for team in created_teams %}
                <li><a href="{% url 'team_detail' team.id %}">{{ team.name }}</a></li>
            {% endfor %}
        </ul>
    {% endif %}

    {% if appended_teams %}
        <h2>Appended to existing teams</h2>
        <ul id="roster-import-confirm-appended-list">
            {% for team in appended_teams %}
                <li><a href="{% url 'team_detail' team.id %}">{{ team.name }}</a></li>
            {% endfor %}
        </ul>
    {% endif %}

    <p>
        <a href="{% url 'team_list' %}" class="btn btn-secondary">Back to Teams</a>
        <a href="{% url 'import_roster' %}" class="btn btn-outline-primary">Import another</a>
    </p>
</div>
{% endblock %}
```

Locked DOM ids and copy substrings:

| Element / data | Locked value |
|----------------|--------------|
| Summary `<div>` id | `roster-import-confirm-summary` |
| Created-teams `<ul>` id | `roster-import-confirm-teams-list` |
| Appended-teams `<ul>` id | `roster-import-confirm-appended-list` |
| Summary copy (substring) | `"Imported"` … `"players across"` … `"rows"` |
| URL name used for team links | `team_detail` (existing) |
| URL name used for back link | `team_list` (existing) |
| URL name used for "Import another" | `import_roster` (this task) |

The created-teams block is rendered only when `created_teams` is non-empty;
ditto the appended-teams block.

### 7c. `templates/teams/team_list.html` (EXISTING — entry-point edit)

Add an anchor in the page-header `<div>` (sibling to the existing
`generate-players-link` and `Create New Team` link):

```django
<a href="{% url 'import_roster' %}" id="roster-import-link" class="btn btn-outline-primary">Import Roster</a>
```

Place it between `generate-players-link` and the `Create New Team` button.
Tests pin via substring `"Import Roster"` and DOM id `roster-import-link`.

---

## 8. File ownership (who edits what)

| File | Code | Tests | Docs |
|------|:----:|:-----:|:----:|
| `teams/roster_importer.py` (new pure module) | OWN | — | — |
| `teams/forms.py` (`RosterImportForm` append) | OWN | — | — |
| `teams/views.py` (`import_roster`, `import_roster_template`, `_check_db_slot_collisions`, `_apply_roster`) | OWN | — | — |
| `teams/urls.py` (two new `path(...)` entries) | OWN | — | — |
| `templates/teams/roster_import.html` (new) | OWN | — | — |
| `templates/teams/roster_import_done.html` (new) | OWN | — | — |
| `templates/teams/team_list.html` (entry-point link) | OWN | — | — |
| `teams/tests/test_roster_importer.py` (new pure-unit) | — | OWN | — |
| `teams/tests/test_roster_import_view.py` (new Django) | — | OWN | — |
| `CONTEXT.md` | — | — | (already done in grilling — no edit) |
| `PLAN.md` (mark LG-00b done) | — | — | OWN |
| `teams/CLAUDE.md` (LG-00b subsection) | — | — | OWN |

---

## 9. Constants pinned in contract (so all three agents agree)

| Name | Value |
|------|-------|
| `REQUIRED_COLUMNS` | `("team", "name", "role", "age", "started_playing_age", "total_games", "home_site", "height")` — 8 entries, order matters |
| `OPTIONAL_COLUMNS` | `("preferred_roles", *STAT_COLUMNS)` — 20 entries, `preferred_roles` first |
| `STAT_COLUMNS` | The 19-tuple matching `_STAT_FIELDS` from `teams/player_generator.py` verbatim (see §2c) |
| `ALL_COLUMNS` | `(*REQUIRED_COLUMNS, *OPTIONAL_COLUMNS)` — 28 entries |
| `ROLE_NAMES` | `("commander", "heavy", "scout", "medic", "ammo")` — 5 entries |
| `SLOT_LIMITS` | `{"commander": 1, "heavy": 1, "scout": 2, "medic": 1, "ammo": 1}` |
| `STAT_DEFAULT` | `50` |
| `STAT_MIN` | `0` |
| `STAT_MAX` | `100` |
| `MAX_DATA_ROWS` | `1000` |
| `MAX_UPLOAD_BYTES` (in `forms.py`) | `2 * 1024 * 1024` |
| `PROFILE_BOUNDS` | `{"age": (5, 100), "started_playing_age": (3, 100), "total_games": (0, 100_000)}` |
| `NAME_MAX_LEN` | `100` |
| `TEAM_NAME_MAX_LEN` | `100` |
| `HOME_SITE_MAX_LEN` | `100` |
| `HEIGHT_MAX_LEN` | `20` |

The Code agent re-declares `PROFILE_BOUNDS` locally in
`teams/roster_importer.py` rather than importing from `teams/forms.py` —
the pure module is Django-free (the existing `_PROFILE_BOUNDS` in
`teams/forms.py` is a module-level constant, but importing `teams.forms`
would pull Django).

---

## 10. Test boundary (frozen — Tests agent reads this section)

All LG-00b tests live in **two NEW files** under the existing
`teams/tests/` package (which already exists per LG-00 / HX-01 / HX-02):

| File | Kind |
|------|------|
| `teams/tests/test_roster_importer.py` | Pure-unit (no Django except one allowed equality import in §11.1.10) |
| `teams/tests/test_roster_import_view.py` | Django `TestCase` — form + view + DB writes |

### 10.1. `teams/tests/test_roster_importer.py` (pure-unit)

Class suggestions and required cases:

#### `TestHeaderValidation`

1. **`test_missing_required_column_raises_with_field_named`** — Build a CSV
   with the `role` column omitted. Call `parse_roster_csv`; assert
   `RosterImportError` raised; `exc.errors` has exactly one entry with
   `row_num=0`, `field="role"`, `message` containing `"Missing required column"`.
2. **`test_unknown_column_raises_with_column_named`** — CSV with all 8
   required columns plus an `unknown_col` header. Assert
   `RosterImportError`; `exc.errors[0].field == "unknown_col"`; message
   contains `"Unknown column"`.
3. **`test_duplicate_column_raises`** — CSV with the `name` column declared
   twice in the header. Assert `RosterImportError`; one error with
   `field="name"` and message containing `"Duplicate column"`.
4. **`test_bom_tolerated`** — CSV text prefixed with `"﻿"` parses
   without error (assuming all rows valid).
5. **`test_more_than_1000_rows_raises`** — Generate 1001 data rows. Assert
   `RosterImportError`; one error with `row_num=0`, `field is None`,
   message containing `"1000"`.

#### `TestCoercion`

1. **`test_empty_stat_cell_defaults_to_50`** — All 19 stat cells empty in
   a row. Parser succeeds; `parsed.rows[0].stats["player_awareness"] == 50`,
   and so for every other stat.
2. **`test_omitted_stat_column_defaults_to_50`** — CSV with only the 8
   required columns (no stat columns at all). Parser succeeds;
   `parsed.rows[0].stats[k] == 50` for every `k in STAT_COLUMNS`.
3. **`test_stat_out_of_range_raises_row_error`** — A row with
   `player_awareness=200`. `parsed` not returned; `exc.errors` contains a
   `RowError(row_num=1, field="player_awareness", ...)`.
4. **`test_stat_non_int_raises_row_error`** — `accuracy="hello"`. Single
   `RowError` with `field="accuracy"`.
5. **`test_role_out_of_range_raises_row_error`** — `role="captain"`.
   `RowError` with `field="role"`.
6. **`test_role_case_normalised`** — `role="COMMANDER"` lowercases to
   `"commander"` and succeeds.
7. **`test_age_out_of_bounds_raises_row_error`** — `age=4` (below the 5
   lower bound). `RowError` with `field="age"`.
8. **`test_started_playing_age_out_of_bounds`** — `started_playing_age=2`.
   `RowError`.
9. **`test_total_games_out_of_bounds`** — `total_games=-1`. `RowError`.
10. **`test_empty_team_cell_raises_row_error`** — `team=""`. `RowError` with
    `field="team"`.
11. **`test_empty_name_cell_raises_row_error`** — `name=""`. `RowError` with
    `field="name"`.
12. **`test_height_and_home_site_empty_allowed`** — `home_site=""`,
    `height=""` both succeed; `parsed.rows[0].profile["home_site"] == ""`,
    `parsed.rows[0].profile["height"] == ""`.

#### `TestPreferredRoles`

1. **`test_empty_cell_yields_empty_list`** — `preferred_roles=""` →
   `parsed.rows[0].preferred_roles == []`.
2. **`test_column_absent_yields_empty_list`** — CSV without the
   `preferred_roles` column at all → `parsed.rows[0].preferred_roles == []`.
3. **`test_comma_split_parsed`** — `preferred_roles="commander,heavy"` →
   `parsed.rows[0].preferred_roles == ["commander", "heavy"]`.
4. **`test_whitespace_trimmed_and_lowercased`** —
   `preferred_roles=" Commander , Heavy "` → `["commander", "heavy"]`.
5. **`test_invalid_role_in_cell_raises`** — `preferred_roles="captain"` →
   `RowError` with `field="preferred_roles"`.
6. **`test_duplicate_role_within_cell_raises`** —
   `preferred_roles="scout,scout"` → `RowError` with `field="preferred_roles"`.

#### `TestInFileCollisions`

1. **`test_duplicate_team_name_pair_raises`** — Two rows with
   `team="Red", name="Alice"`. `RowError(row_num=<second row>,
   field="name", ...)` with message containing `"Duplicate"`.
2. **`test_two_non_scout_rows_for_same_team_role_raises`** — Two
   `team="Red", role="commander"` rows. `RowError(row_num=<second>,
   field="role", ...)` with message containing `"commander"` and `"Too many"`.
3. **`test_three_scout_rows_for_same_team_raises`** — Three
   `team="Red", role="scout"` rows. `RowError(row_num=<third>,
   field="role", ...)` with message containing `"scout"` and `"Too many"`.
4. **`test_two_scout_rows_for_same_team_allowed`** — Two
   `team="Red", role="scout"` rows. Parses cleanly.

#### `TestMultiErrorAccumulation`

1. **`test_multiple_row_errors_accumulate_in_single_raise`** — A 5-row CSV
   with errors on rows 2, 3, 5. Single `RosterImportError`; `len(exc.errors)
   >= 3`; row numbers in `exc.errors` cover at least `{2, 3, 5}`.
2. **`test_header_error_short_circuits_per_row_parsing`** — CSV with a
   missing required column AND a broken row 1 (out-of-range stat). The
   raised `RosterImportError` contains the header error only (the parser
   short-circuited per-row parsing). Tests assert no `RowError` with
   `row_num >= 1` is present.

#### `TestRowErrorShape`

1. **`test_row_error_is_frozen_dataclass`** — `dataclasses.is_dataclass(RowError)`
   is True; attempting `err.row_num = 99` raises `dataclasses.FrozenInstanceError`.
2. **`test_row_error_is_hashable`** — `{RowError(1, "age", "x")}` works.

#### `TestParsedRosterShape`

1. **`test_by_team_grouping_in_csv_encounter_order`** — CSV with rows
   `team=A, team=B, team=A`. `list(parsed.by_team.keys()) == ["A", "B"]`
   (insertion order — A appears first). `parsed.by_team["A"]` has 2 rows;
   `parsed.by_team["B"]` has 1 row.
2. **`test_rows_list_matches_csv_order`** — `[r.row_num for r in
   parsed.rows] == [1, 2, 3]` for a 3-row CSV.
3. **`test_stat_columns_equals_player_generator_stat_fields`** — Imports
   `teams.player_generator._STAT_FIELDS` and asserts
   `STAT_COLUMNS == _STAT_FIELDS`. This is the ONE allowed
   `teams.player_generator` import inside the pure-unit test file (the
   pure module under test imports nothing from Django either way).

#### `TestNoDjangoImportsLeaked`

1. **`test_no_django_imports_leaked`** — Subprocess (or `sys.modules.pop`
   + `importlib.import_module`) fresh import of `teams.roster_importer`.
   Assert no module starting with `"django"` appears in `sys.modules`
   that wasn't there before — OR assert the module itself has no
   attribute named `django` / `models` / `forms`. Mirror
   `test_player_generator.py::TestNoDjangoImportsLeaked` exactly.

### 10.2. `teams/tests/test_roster_import_view.py` (Django `TestCase`)

#### `TestImportRosterGet`

1. **`test_get_200`** — `GET reverse("import_roster")` → 200.
2. **`test_form_field_present`** — Response body contains DOM ids
   `roster-import-form`, `roster-import-file`, `roster-import-submit`,
   `roster-import-template-link`.

#### `TestImportRosterPostHappyPath`

1. **`test_post_2_teams_12_players_creates_2_teams_12_players`** — Build
   a valid 12-row CSV (6 rows for team `"Red"`, 6 for team `"Blue"`,
   role mix that fills all 6 slots per team). POST as a multipart upload.
   Assert 200; `Team.objects.regular().count() == 2`;
   `Player.objects.count() == 12`. Each created Team is in
   `created_teams` context. Response body contains DOM id
   `roster-import-confirm-teams-list` and `roster-import-confirm-summary`.
2. **`test_post_assigns_slot_fks_correctly`** — Same setup. For one of
   the created Teams, assert every `slot_<role>` FK points at a Player
   with the matching `role` from the CSV (Scout slots both filled).
3. **`test_post_appends_to_existing_team`** — Pre-create a Team `"Red"`
   with `slot_commander` already filled (manually). POST a CSV with a
   Heavy + Scout for team `"Red"`. Assert: no new Team created, both
   players added to `"Red"`, `slot_heavy` now filled, `slot_scout_1`
   filled, `slot_commander` unchanged. Response context `appended_teams`
   includes the Red team.
4. **`test_post_auto_creates_missing_team`** — POST a CSV for a team
   name `"Brand New"` that does not exist. Assert: Team created;
   `created_teams` includes it; `appended_teams` does not.

#### `TestImportRosterPostDbSlotCollision`

1. **`test_existing_team_with_slot_commander_filled_rejects_new_commander`**
   — Pre-create Team `"Red"` with `slot_commander` filled. POST a CSV
   with one `team=Red, role=commander` row. Assert 200; response body
   contains a `roster-import-error-1-role` DOM id (the row error on row
   1); no new Players or Teams written.
2. **`test_existing_team_with_both_scout_slots_filled_rejects_new_scout`** —
   Pre-create Team `"Red"` with both `slot_scout_1` and `slot_scout_2`
   filled. POST a CSV with a Scout for `"Red"`. Assert 200; row-1 error
   rendered; no DB writes.

#### `TestImportRosterPostFormErrors`

1. **`test_post_file_too_large_renders_form_error`** — POST a CSV body
   larger than `MAX_UPLOAD_BYTES`. Assert 200; response body contains
   the substring `"too large"`; no DB writes.
2. **`test_post_non_utf8_file_renders_form_error`** — POST a body that
   is invalid UTF-8 (e.g. `b"\xff\xfe\xfd"`). Assert 200; response body
   contains the substring `"must be UTF-8"`; no DB writes.

#### `TestImportRosterPostParseErrors`

1. **`test_post_unknown_column_renders_row_errors_list`** — POST a CSV
   with an unknown header. Assert 200; response body contains DOM id
   `roster-import-errors`; the rendered text contains `"Unknown column"`.
2. **`test_post_invalid_row_renders_per_row_error_with_dom_id`** — POST a
   CSV with `accuracy=999` on row 2 (data row 1). Assert 200; response
   body contains DOM id `roster-import-error-1-accuracy`.
3. **`test_post_multiple_errors_all_rendered`** — POST a CSV with errors
   on data rows 1, 2, 3. Body contains DOM ids
   `roster-import-error-1-*`, `roster-import-error-2-*`,
   `roster-import-error-3-*` (any field suffix).

#### `TestImportRosterPostAtomic`

1. **`test_db_slot_collision_rolls_back_all_writes`** — Pre-create Team
   `"Red"` with `slot_commander` filled. POST a CSV with: 5 valid rows
   for team `"Blue"` (a brand-new team) AND 1 invalid `team=Red,
   role=commander` row. Assert 200 (error page); `Team.objects.filter
   (name="Blue").count() == 0`; `Player.objects.count()` equals the
   pre-test count (the only existing Player is the one filling
   `Red.slot_commander`). This pins `transaction.set_rollback(True)`.
2. **`test_parser_raise_writes_nothing`** — POST a CSV with one row that
   has `role="captain"`. Assert no Teams or Players created.

#### `TestImportRosterTemplate`

1. **`test_template_csv_200_with_content_disposition`** — `GET
   reverse("import_roster_template")` → 200; response
   `["Content-Disposition"] == 'attachment; filename="roster_template.csv"'`;
   `response["Content-Type"]` starts with `"text/csv"`.
2. **`test_template_csv_body_byte_for_byte`** — Reconstruct the expected
   body in the test using `csv.writer` + the 3 rows pinned in §5;
   assert `response.content == expected.encode("utf-8")`.
3. **`test_template_header_lists_all_28_columns_in_ALL_COLUMNS_order`** —
   First line of the response body, comma-split, equals
   `list(ALL_COLUMNS)`.

#### `TestEntryPointLink`

1. **`test_team_list_contains_roster_import_link`** — `GET
   reverse("team_list")`; response body contains DOM id
   `roster-import-link` and the substring `"Import Roster"`.

### 10.3. Files the Tests agent edits

| File | Action |
|------|--------|
| `teams/tests/test_roster_importer.py` | NEW |
| `teams/tests/test_roster_import_view.py` | NEW |

No existing test files are touched. The Tests agent runs the new tests
(scoped pytest run) and does NOT run the full suite.

---

## 11. Determinism / scope notes

- **No simulation behaviour change.** LG-00b is read-only with respect to
  simulator code; it creates Players and Teams; no Score Calibration
  re-baseline obligation.
- **No global RNG seeding.** The CSV import is fully deterministic given
  the input file — no `random.*` calls in either the pure module or the
  view.
- **`@transaction.atomic` covers the entire POST handler** so partial
  writes never persist. Pinned by
  `test_db_slot_collision_rolls_back_all_writes` AND by the explicit
  `transaction.set_rollback(True)` call in the view's catch block.
- **No model change, no migration, no ADR.**

---

## 12. Out of scope (do NOT add)

- No model change, no migration, no ADR.
- No simulator change. No `_flush_to_db` touch. No SIM-07 / SIM-08
  contract interaction.
- No Score Calibration re-baseline.
- No test-double for the CSV parser (it's pure stdlib).
- No async / celery / progress bar (1000-row cap makes the import
  sub-second; foreground under `@transaction.atomic`).
- No preview-before-commit UI (POST writes immediately on success).
- No per-team `/teams/<id>/import/` entry point (single global entry only).
- No editing the existing per-player Add Player flow (`player_add` view
  unchanged).
- No editing LG-00's `generate_players` view or `player_generator.py`
  pure module (the only contract-level cross-link is that
  `STAT_COLUMNS` MUST equal `_STAT_FIELDS` — pinned by a test, not by
  an import).
- No changes to Django admin.
- No changes to the REST API (`teams/api_views.py`,
  `teams/serializers.py` untouched).
- No JS validation / live preview.
- No per-row commit (it's all-or-nothing).
- No CSV dialect detection (comma-delimited only; `csv.DictReader`
  default dialect).
- No multi-file upload.
- No clobber / overwrite mode for existing players (the import only
  CREATES new Players; updating an existing Player is not in scope —
  `unique_together = ["team", "name"]` on `Player` enforces this at the
  DB layer as a hard backstop).
- No CONTEXT.md edit (the "Roster import" term was added in the grilling
  session). No ADR.

---

## 13. Locked names — quick-reference block

| Slot | Name |
|------|------|
| URL pattern (form) | `path("import/", views.import_roster, name="import_roster")` |
| URL pattern (template CSV) | `path("import/template.csv", views.import_roster_template, name="import_roster_template")` |
| Full URL (form) | `/teams/import/` |
| Full URL (template) | `/teams/import/template.csv` |
| View (form) | `teams.views.import_roster` |
| View (template CSV) | `teams.views.import_roster_template` |
| Private view helper | `teams.views._check_db_slot_collisions(parsed)` |
| Private view helper | `teams.views._apply_roster(parsed) -> (created, appended, player_count)` |
| Form | `teams.forms.RosterImportForm` |
| Form field name | `csv_file` |
| Form constant | `RosterImportForm.MAX_UPLOAD_BYTES = 2 * 1024 * 1024` |
| Pure module | `teams/roster_importer.py` |
| Pure function | `parse_roster_csv(text: str) -> ParsedRoster` |
| Dataclass — row error | `RowError(row_num: int, field: str \| None, message: str)` (frozen) |
| Dataclass — parsed row | `ParsedRow(row_num, team, name, role, profile, stats, preferred_roles)` (frozen) |
| Dataclass — parsed roster | `ParsedRoster(rows, by_team)` (frozen) |
| Exception | `RosterImportError(Exception)` with `errors: list[RowError]` |
| Module tuple — required cols | `REQUIRED_COLUMNS` (8) |
| Module tuple — optional cols | `OPTIONAL_COLUMNS` (20) |
| Module tuple — stat cols | `STAT_COLUMNS` (19, equals `_STAT_FIELDS`) |
| Module tuple — all cols | `ALL_COLUMNS` (28) |
| Module tuple — role names | `ROLE_NAMES` (5) |
| Module dict — slot limits | `SLOT_LIMITS = {"commander":1, "heavy":1, "scout":2, "medic":1, "ammo":1}` |
| Module dict — profile bounds | `PROFILE_BOUNDS = {"age":(5,100), "started_playing_age":(3,100), "total_games":(0,100_000)}` |
| Module constants | `STAT_DEFAULT=50, STAT_MIN=0, STAT_MAX=100, MAX_DATA_ROWS=1000, NAME_MAX_LEN=100, TEAM_NAME_MAX_LEN=100, HOME_SITE_MAX_LEN=100, HEIGHT_MAX_LEN=20` |
| Templates | `templates/teams/roster_import.html` (form) + `templates/teams/roster_import_done.html` (confirm) |
| DOM id — form | `roster-import-form` |
| DOM id — file input | `roster-import-file` |
| DOM id — submit button | `roster-import-submit` |
| DOM id — template-download link | `roster-import-template-link` |
| DOM id — errors list `<ul>` | `roster-import-errors` |
| DOM id — errors summary `<div>` | `roster-import-errors-summary` |
| DOM id — per-error `<li>` | `roster-import-error-{row_num}-{field|"row"}` |
| DOM id — confirm summary | `roster-import-confirm-summary` |
| DOM id — confirm teams list | `roster-import-confirm-teams-list` |
| DOM id — confirm appended list | `roster-import-confirm-appended-list` |
| DOM id — entry-point link (team_list) | `roster-import-link` |
| Test files (new) | `teams/tests/test_roster_importer.py`, `teams/tests/test_roster_import_view.py` |
| CONTEXT.md terms | Already added in grilling — Docs agent does NOT re-add |
| PLAN.md task | Mark **LG-00b** completed in house style |
| Scope-out reminders | no model change; no migration; no ADR; no simulator change; no Score Calibration re-baseline; no /teams/<id>/import/; no admin / REST API edits; no preview UI; no async / celery; no progress bar; no overwrite mode; no multi-file; no JS validation |
