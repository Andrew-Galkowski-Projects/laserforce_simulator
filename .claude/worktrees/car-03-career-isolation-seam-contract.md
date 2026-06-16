# CAR-03 — Career isolation from multiplayer — seam contract

**Slice goal:** gate the CAR-02 owner-evaluation / firing / reassignment lifecycle
so it is **inert unless `League.mode == "league"`**. Defensive gate only — no
multiplayer creation flow, no new mode value, **no model change, no migration, no
simulator change, no Score Calibration re-baseline, no new ADR, no new CONTEXT.md
term** (the **Owner evaluation** glossary entry already carries the league-mode-only
clause, added this session).

**Gate predicate (LOCKED):** positive allowlist `mode == "league"` — both
`"sandbox"` and `"multiplayer"` (and any future mode) are inert.

## Shared helper (single source of truth)

`matches/league_views.py`:

```python
def _is_career_league(league: League) -> bool:
    """CAR-03 — the owner-mood firing lifecycle runs only in single-player
    career mode (League.mode == "league"). Sandbox / multiplayer Leagues never
    fire, evaluate, or reassign."""
    return league.mode == "league"
```

Consumed by the writer, the two reassign views, and the dashboard context.

## Code seam

| Site (`matches/league_views.py`) | Change |
|---|---|
| `_ensure_owner_evaluations(league, up_to_season) -> None` | **First line:** `if not _is_career_league(league): return`. The chokepoint — no `OwnerEvaluation` rows ever written for a non-career League. |
| `next_season(request, league_id)` | **UNCHANGED.** Writer no-op ⇒ `evaluation is None` ⇒ rolls the season normally (no firing, no New-Team redirect). |
| `owner_evaluation(request, season_id)` | **UNCHANGED.** Naturally raises `Http404("No owner evaluation for this Season.")` on the missing row for a non-career completed Season. |
| `new_team_picker(request, league_id)` | **ADD guard** after `get_object_or_404(League, ...)`: `if not _is_career_league(league): return HttpResponseBadRequest("...")` ⇒ **HTTP 400**. |
| `reassign_team(request, league_id)` | **ADD guard** after `get_object_or_404(League, ...)` (BEFORE any `current_team` write): `if not _is_career_league(league): return HttpResponseBadRequest("...")` ⇒ **HTTP 400**, writes nothing. |
| `_build_dashboard_context(displayed_season, season_mode) -> dict` | **+1 context key** `is_career_mode: bool` = `displayed_season is not None and _is_career_league(displayed_season.league)`. (Reachable only on a `start_next_season` branch, where `displayed_season` is the completed Season.) |

`HttpResponseBadRequest` is already imported in `league_views.py` (used by
`next_season` / `reassign_team`); the Code agent confirms, adds nothing duplicate.

## Template seam

Both `templates/seasons/dashboard.html` and `templates/leagues/dashboard.html`,
the `{% elif action_button_state == "start_next_season" %}` arm splits on
`is_career_mode`:

- **`{% if is_career_mode %}`** → the existing CAR-02 owner-evaluation GET link
  (DOM id `season-dashboard-owner-evaluation-link` / `league-dashboard-owner-evaluation-link`,
  `href="{% url 'owner_evaluation' displayed_season.id %}"`, `data-action-state="start_next_season"`).
- **`{% else %}`** → a direct `next_season` POST `<form>` (the pre-CAR-02 LG-01e
  shape): DOM id `season-dashboard-next-season-form` / `league-dashboard-next-season-form`,
  `<form method="post" action="{% url 'next_season' league_id=... %}">` + `{% csrf_token %}`
  + a submit `<button data-action-state="start_next_season">{{ action_button_label }}</button>`.
  `league_id` is `season.league_id` (season dashboard) / `league.id` (league dashboard).

The outer `season-dashboard-action-button` / `league-dashboard-action-button`
wrapper `<span>` and the `data-action-state="start_next_season"` attribute are
preserved in BOTH arms (LG-01c/e backward-compat).

## Test boundary (Tests agent — extend existing CAR-02 files)

All cases use a **`mode="multiplayer"`** League fixture (multiplayer is the
representative non-career mode).

| File | Assertion |
|---|---|
| `test_owner_evaluations_writer.py` | `_ensure_owner_evaluations` on a multiplayer League with a completed Season writes **0** `OwnerEvaluation` rows. |
| `test_league_next_season.py` | `next_season` on a multiplayer League's completed Season → **302** to the new Season's dashboard, a new draft Season exists, **no** `OwnerEvaluation` row, `current_team` unchanged (no firing/New-Team redirect). |
| `test_reassign_team.py` | `reassign_team` POST on a multiplayer League → **400**, `current_team` unchanged; `new_team_picker` GET on a multiplayer League → **400**. |
| `test_league_dashboard.py` + `test_season_dashboard_view.py` | Completed Season: **multiplayer** dashboard renders `…-next-season-form` and NOT `…-owner-evaluation-link`; **`league`** mode still renders `…-owner-evaluation-link` and NOT the form. |

Tests are Django `TestCase` (view/writer-level — no pure module touched). No
`mock.patch` on the ORM; exercise real views end-to-end. Assert on row counts /
status codes / `current_team` / rendered DOM ids — never on simulated point totals.

## Out of scope (LOCKED)

No multiplayer creation flow / form field / new mode value; no `Manager`/`User`
model; no model field / migration; no simulator or RNG change; no Score
Calibration re-baseline; no new ADR; no new CONTEXT.md term. `owner_evaluation`
gets **no** explicit guard (its natural 404 suffices).
