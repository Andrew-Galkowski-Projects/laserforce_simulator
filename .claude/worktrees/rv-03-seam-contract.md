# RV-03 Seam Contract — Round report PDF export

**Status:** LOCKED. Three agents (Code / Tests / Docs) work against this in parallel.
Names below are frozen — do not rename, do not add fields. If reality contradicts
a name here, STOP and flag; do not silently drift.

All paths are relative to the repo's nested Django project root:
`laserforce_simulator/laserforce_simulator/` (where `manage.py` lives).

---

## 1. New public names (frozen)

| Kind | Name | Location |
|------|------|----------|
| Model field | `GameRound.is_simulated` (`BooleanField(default=True)`) | `matches/models.py` |
| Migration | `matches/migrations/0028_gameround_is_simulated.py` | next after `0027_gameround_highlights_json` |
| Module | `matches/sim_helpers/pdf_report.py` | sibling of planned RV-05 `pdf_charts.py` |
| Pure builder | `build_round_report(report_data: dict, *, watermark: bool) -> bytes` | `pdf_report.py` |
| Watermark helper | `should_watermark(is_simulated: bool) -> bool` (testable seam, see §6) | `pdf_report.py` |
| View | `export_round_report(request, round_id)` | `matches/views.py` |
| URL name | `export_round_report` | `matches/urls.py` |
| Dependency | `reportlab` (unpinned line `reportlab>=4.0`) | `laserforce_simulator/requirements.txt` |

No backfill for `is_simulated` (ADR-0004 precedent — `rng_seed` / `cell_occupancy_json` / `highlights_json`).
Existing rows take the `default=True`.

---

## 2. The builder seam (MOST IMPORTANT — view ↔ builder boundary)

```python
def build_round_report(report_data: dict, *, watermark: bool) -> bytes:
    """Render a Round-report PDF entirely in memory.

    PURE: no Django/ORM imports, no settings access, no file I/O beyond an
    internal io.BytesIO buffer. Consumes the report_data dict (shape below)
    and returns the PDF as bytes. The diagonal "[Simulated]" watermark is
    drawn on EVERY page via a ReportLab canvas page callback (onFirstPage /
    onLaterPages, or SimpleDocTemplate(..., onPage=...) equivalent), gated by
    the `watermark` bool. When watermark is False, no watermark is drawn.
    """
```

- Returns non-empty `bytes` starting with the literal PDF magic `b"%PDF"`.
- `watermark` is a keyword-only bool. The view passes `watermark=game_round.is_simulated`.
- The builder NEVER touches the ORM. The view assembles `report_data` and hands
  it over. The dict below is the ONLY thing that crosses the seam.

### 2a. `report_data` dict schema (frozen — top-level keys)

```python
report_data = {
    # ----- round summary block -----
    "round_id": int,                 # GameRound.pk
    "round_label": str,              # "Round N of 2" if match else "Single Round"
    "date_played": str,              # pre-formatted display string (view formats it; builder prints verbatim)
    "map_name": str | None,          # arena_map.name, or None -> builder OMITS the map line
    "red_team_name": str,            # game_round.team_red.name
    "blue_team_name": str,           # game_round.team_blue.name
    "red_points": int,               # game_round.red_points
    "blue_points": int,              # game_round.blue_points
    "red_eliminated": bool,          # game_round.red_team_eliminated
    "blue_eliminated": bool,         # game_round.blue_team_eliminated
    "winner_name": str | None,       # game_round.winner.name, or None -> builder prints "Tie"

    # ----- per-player scoreboards (red first, blue second) -----
    "red_players": list[player_row],   # ordered -points_scored, role, player__name
    "blue_players": list[player_row],  # same ordering

    # ----- per-team resource summary -----
    "red_totals": team_totals,
    "blue_totals": team_totals,
}
```

### 2b. `player_row` dict schema (frozen — one per PlayerRoundState)

Columns are the RV-01 stat set, single-sourced from RV-01 — match exactly,
same fixed key order:

```python
player_row = {
    "name": str,                  # PlayerRoundState.player.name
    "role": str,                  # PlayerRoundState.role
    "points_scored": int,         # field
    "mvp": float,                 # PlayerRoundState.get_mvp property (NOT a field)
    "tags_made": int,             # field
    "times_tagged": int,          # field
    "accuracy": int,              # PlayerRoundState.get_accuracy property, 0-100 (NOT a field)
    "final_lives": int,           # field
    "resupplies_given": int,      # field
    "missiles_landed": int,       # field
    "specials_used": int,         # field
    "follow_up_shots": int,       # field
    "reaction_shots": int,        # field
    "combo_resupply_count": int,  # field
}
```

VERIFIED against `matches/models.py`:
- All 10 plain stats (`points_scored`, `tags_made`, `times_tagged`, `final_lives`,
  `resupplies_given`, `missiles_landed`, `specials_used`, `follow_up_shots`,
  `reaction_shots`, `combo_resupply_count`) are real `IntegerField`s.
- `mvp` is NOT a field — it is the `get_mvp` **property** (returns `float`, delegates to
  `score_calculator.calculate_mvp`). The dict key is `"mvp"`; the source is `get_mvp`.
- `accuracy` is NOT a field — RV-01 uses the `get_accuracy` **property** (int 0-100).
  NOTE: there is also a plain `accuracy` property on the model that delegates to
  `player.stat_for_simulation` — DO NOT use that one. Source the `"accuracy"` key from
  `get_accuracy`, matching RV-01 exactly.

### 2c. `team_totals` dict schema (frozen — one per team)

Summed over that team's `player_row`s plus derived team values:

```python
team_totals = {
    "resupplies_given": int,    # sum of resupplies_given over the team's players
    "missiles_landed": int,     # sum of missiles_landed over the team's players
    "specials_used": int,       # sum of specials_used over the team's players
    "tags_made": int,           # sum of tags_made over the team's players
    "survivors": int,           # count of the team's players with final_lives > 0
    "team_points": int,         # red_points for red_totals / blue_points for blue_totals
}
```

VERIFIED summable fields on `PlayerRoundState`: `resupplies_given`, `missiles_landed`,
`specials_used`, `tags_made`, `final_lives` (for the `survivors` count) all exist as
`IntegerField`s. `team_points` comes from `GameRound.red_points` / `blue_points` (the
team-level fields), NOT summed from players. No invented fields.

---

## 3. View contract (`export_round_report` in `matches/views.py`)

```python
def export_round_report(request, round_id):
    # GET only:
    if request.method != "GET":
        return HttpResponseNotAllowed(["GET"])          # -> 405
    game_round = get_object_or_404(GameRound, pk=round_id)  # -> 404 on missing
    report_data = { ... }   # assembled from ORM per §2 schema
    pdf_bytes = build_round_report(report_data, watermark=game_round.is_simulated)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = (
        f'attachment; filename="round-{round_id}-{red_slug}-vs-{blue_slug}.pdf"'
    )
    return response
```

- Mirrors the existing `movement_heatmap` GET-guard pattern (`views.py` ~L944:
  `if request.method != "GET": return HttpResponseNotAllowed(["GET"])`).
- `HttpResponse` and `HttpResponseNotAllowed` are already imported at `views.py:8`.
- Scoreboard ordering MUST mirror `game_round_detail` (`views.py:418`) EXACTLY:
  ```python
  game_round.player_states.filter(player__team=game_round.team_red)
      .select_related("player").order_by("-points_scored", "role", "player__name")
  ```
  and the same for `team_blue`. Red list -> `report_data["red_players"]`,
  blue -> `report_data["blue_players"]`.
- `round_label`: `f"Round {game_round.round_number} of 2"` if `game_round.match` else
  `"Single Round"`.
- `map_name`: `game_round.arena_map.name` if `game_round.arena_map_id` else `None`.
- `winner_name`: `game_round.winner.name` if `game_round.winner_id` else `None`.
- `date_played`: view formats `game_round.date_played` to a display string (builder prints verbatim).
- Filename slug: derive `red_slug` / `blue_slug` from the team names; the view owns
  slugification (lowercase, spaces->hyphens, strip unsafe chars). The exact slug
  function is the view's choice — only the `attachment; filename="round-<id>-<red>-vs-<blue>.pdf"`
  shape is pinned.

---

## 4. URL contract (`matches/urls.py`)

Add, grouped with the other `game-round/<int:round_id>/...` routes:

```python
path(
    "game-round/<int:round_id>/export/",
    views.export_round_report,
    name="export_round_report",
),
```

Full path: `/matches/game-round/<int:round_id>/export/`.

---

## 5. Migration contract

- File: `matches/migrations/0028_gameround_is_simulated.py`
- `dependencies = [("matches", "0027_gameround_highlights_json")]`
- One `AddField` adding `GameRound.is_simulated = BooleanField(default=True)`.
- Generate via `python laserforce_simulator/manage.py makemigrations matches` — do
  NOT hand-author if the auto-generated name/number matches `0028_gameround_is_simulated`.
- No backfill, no data migration.

---

## 6. Watermark testable seam (frozen mechanism)

ReportLab compresses page-content streams, so the literal text `[Simulated]` is NOT
reliably greppable in the output bytes. The watermark decision is therefore factored
into a small pure helper the Tests agent calls DIRECTLY — no PDF byte-parsing required:

```python
def should_watermark(is_simulated: bool) -> bool:
    """Single decision point for whether the diagonal '[Simulated]' watermark
    is drawn. The page callback in build_round_report consults this; tests
    assert on it directly without parsing compressed PDF streams."""
    return bool(is_simulated)
```

- `build_round_report` MUST route its watermark gating through `should_watermark(watermark)`
  (or use the `watermark` bool such that `should_watermark` is the documented decision seam).
- Tests assert `should_watermark(True) is True`, `should_watermark(False) is False`.
- Tests separately assert the builder returns `b"%PDF"`-prefixed non-empty bytes for
  both `watermark=True` and `watermark=False` (no crash either way), but do NOT assert
  on watermark text presence in the bytes.

---

## 7. Entry point (template)

- The round detail template is `laserforce_simulator/templates/matches/game_round_detail.html`
  (rendered by `game_round_detail`, context key `round`).
- Add an "Export PDF" link/button pointing to
  `{% url 'export_round_report' round.id %}`.
- This is a Docs/Code boundary marker: the Code agent adds the link; no behavioural
  logic lives in the template.

---

## 8. Edge cases (encode in builder + view + tests)

| Case | Expected behaviour |
|------|--------------------|
| Empty / early-eliminated round (all-zero stats) | renders with zeros, NO crash |
| Missing round id | view returns 404 (`get_object_or_404`) |
| Non-GET (POST etc.) | view returns 405 (`HttpResponseNotAllowed(["GET"])`) |
| Map-less round (`arena_map is None`) | `map_name=None` -> builder OMITS the map line |
| `winner is None` (tie) | `winner_name=None` -> builder prints "Tie" |
| `is_simulated=True` | watermark drawn on every page |
| `is_simulated=False` | no watermark |

---

## 9. File ownership (who edits what)

| File | Code | Tests | Docs |
|------|:----:|:-----:|:----:|
| `matches/models.py` (`is_simulated` field) | OWN | — | — |
| `matches/migrations/0028_gameround_is_simulated.py` | OWN | — | — |
| `matches/sim_helpers/pdf_report.py` (new) | OWN | — | — |
| `matches/views.py` (`export_round_report`) | OWN | — | — |
| `matches/urls.py` (route) | OWN | — | — |
| `laserforce_simulator/requirements.txt` (`reportlab`) | OWN | — | — |
| `templates/matches/game_round_detail.html` (link) | OWN | — | — |
| `matches/tests/test_rv03_pdf_report.py` (new, pure-unit) | — | OWN | — |
| `matches/tests/views_tests.py` (view/DB tests, append) | — | OWN | — |
| `matches/CLAUDE.md` (RV-03 subsection) | — | — | OWN |
| `PLAN.md` / `CONTEXT.md` (mark RV-03 done) | — | — | OWN |

Tests agent: do NOT import the view module from the pure-unit file — the pure-unit
file imports ONLY `matches.sim_helpers.pdf_report` and stdlib. The view/DB tests use
Django's `TestCase` + the test client.

---

## 10. Test boundary (frozen — Tests agent and Code agent agree here)

Tests package confirmed at `matches/tests/` (it is a package: `__init__.py` present,
e.g. `conftest.py`, `test_rv02_highlights.py`, `views_tests.py`). NOTE: the
`matches/tests.py` referenced in the root CLAUDE.md does NOT exist as a module — the
tests live in the `matches/tests/` PACKAGE; view-layer tests go in
`matches/tests/views_tests.py`.

### 10a. Pure-unit file — `matches/tests/test_rv03_pdf_report.py`
Asserts only against `build_round_report(report_data, watermark=...)` and `should_watermark`:
- returns non-empty `bytes` starting with `b"%PDF"` for `watermark=True`.
- returns non-empty `bytes` starting with `b"%PDF"` for `watermark=False`.
- `should_watermark(True) is True`; `should_watermark(False) is False`.
- empty/early-eliminated `report_data` (zeroed player rows, empty player lists,
  `map_name=None`, `winner_name=None`) renders without crashing and still starts with `b"%PDF"`.
- (defensive) no Django import leaks into `pdf_report.py` — assert the module has no
  `django` attribute / imports cleanly without Django setup, mirroring the RES-04
  "no Django imports leaked" check in `test_res04_cell_occupancy.py`.
- Use a hand-built `report_data` dict literal (NO ORM) so the file is DB-free.

### 10b. View/DB additions — `matches/tests/views_tests.py`
On a real saved `GameRound` (Django `TestCase`):
- GET `export_round_report` -> `200`, `Content-Type == "application/pdf"`,
  `Content-Disposition` starts with `attachment; filename="round-<id>-` and ends `.pdf"`,
  response body starts with `b"%PDF"`.
- GET on a missing round id -> `404`.
- `POST` to the route -> `405`.
- (recommended) one round saved with `is_simulated=True` and one with `is_simulated=False`
  both return `200` + `b"%PDF"` (exercises both watermark branches end-to-end).

---

## 11. Out of scope (do NOT add)

- No charts/graphs in the PDF (RV-05 territory — `pdf_charts.py` is a *future* sibling).
- No CSV/PNG export, no email, no async/job queue.
- No new model fields beyond `is_simulated`.
- No backfill migration.
- No Score Calibration re-baseline (RV-03 runs no simulation, consumes no RNG).
