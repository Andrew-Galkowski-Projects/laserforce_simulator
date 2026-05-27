# LG-01b — Create-League Flow · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01b ships
the `GET/POST /leagues/create/` form that creates a `League(state=active)` +
initial `Season(state=draft)` + auto-generated `Team`s (via the existing
`teams.views._generate_teams` helper) enrolled into the Season's M2M, then
redirects to `/seasons/<season_id>/standings/`. The deferred broken
`league-create-link` `href="/leagues/create/"` shipped by LG-01a now resolves.

---

## 1. URL

- **File:** `matches/league_urls.py` (existing — LG-01a)
- **Insertion point:** the new `path(...)` line is inserted **BEFORE** the
  existing `path("", views.league_list, name="league_list")` line. Django URL
  resolution is first-match — the empty-string pattern would otherwise capture
  every request mounted at `/leagues/`. Final `urlpatterns` order:
  1. `path("create/", views.league_create, name="league_create")`
  2. `path("", views.league_list, name="league_list")`
- **Full URL:** `/leagues/create/`
- **Reverse name:** `league_create` (no `app_name`, bare URL namespace — mirrors
  `league_list`)
- **HTTP methods:** GET (render form) + POST (process form). No 405 guard on
  other methods (Django's default behaviour for `request.method` branching is
  sufficient; `HttpResponseNotAllowed` is not pinned).

---

## 2. Form

NEW file `matches/forms.py` is reused if it exists (it does — `MatchSetupForm`,
`SingleRoundSetupForm`, `BatchSimulateForm` live there); the new class is
appended.

- **Class:** `matches.forms.CreateLeagueForm(forms.Form)`
- **Fields (locked order, exact types, all validators / initials pinned):**

| Field name | Type | Constraints / Validators | Initial |
|---|---|---|---|
| `league_name` | `forms.CharField` | `max_length=100` | — (required) |
| `season_name` | `forms.CharField` | `max_length=100` | `"Season 1"` |
| `start_date` | `forms.DateField` | — | `django.utils.timezone.localdate` (callable, evaluated per-bind) |
| `num_teams` | `forms.TypedChoiceField` | `choices=[(4, "4"), (8, "8"), (12, "12"), (16, "16")]`, `coerce=int`, `empty_value=None` | `4` |
| `schedule_format` | `forms.ChoiceField` | `choices=[("single_round_robin", "Single round-robin")]`, `disabled=True` | `"single_round_robin"` |
| `mean` | `forms.IntegerField` | `min_value=0`, `max_value=100` | `50` |
| `std_dev` | `forms.IntegerField` | `min_value=1`, `max_value=40` | `15` |

- **`num_teams` coercion:** `TypedChoiceField` with `coerce=int` — `cleaned_data["num_teams"]`
  is an `int`, **not** a `str`. Choices tuples are `(int, str)` pairs (the value
  side is the int the choice posts as; the label is the display string). No
  alternate casting inside `clean()`.
- **`schedule_format` lockdown:** Single-option `ChoiceField` with
  `disabled=True` — Django serves the initial value to the form regardless of
  POST content, so a tampered POST cannot inject a different format. No
  additional `clean_schedule_format` guard required.
- **`league_name` uniqueness:** Duplicate League names are allowed at LG-01b —
  **no uniqueness validation**, no DB collision check. Two Leagues with the
  same `name` differ only by `id`.
- **`players_per_team` is NOT a form field** — it is fixed at `6` server-side
  in the view body (see §3 step 2).
- **No `Meta`, no `__init__` override, no custom widgets pinned.** Widget
  attributes (e.g. CSS classes for layout) are at the Code agent's discretion
  but must not change field types or validators.

---

## 3. View

NEW function appended to `matches/views.py` (the file LG-01a's `league_list`
lives in).

- **Signature:** `def league_create(request: HttpRequest) -> HttpResponse`
- **Decorator:** `@transaction.atomic` (single decorator, no other middleware
  decorators added)
- **Imports added to `matches/views.py` (top-of-file):**
  - `from django.db import transaction`
  - `from django.shortcuts import render, redirect`  *(redirect already
    imported by LG-01 surrounding views; transaction may already be imported by
    `simulate_scheduled_round` callers — defensive `from django.db import
    transaction` is safe)*
  - `from django.utils import timezone`
  - `import random`
  - `from teams.views import _generate_teams`  *(cross-app import — the only
    one in `matches/views.py` for this purpose; see §4)*
  - `from teams.constants import TEAM_NAMES, PLAYER_NAMES`
  - `from .forms import CreateLeagueForm`
  - `from .models import League, Season`  *(may already be imported)*

- **Body (6-step skeleton — pinned order, no steps reordered, no steps added
  or omitted):**

  1. **GET branch:** if `request.method != "POST"`, instantiate
     `form = CreateLeagueForm()` and render
     `templates/leagues/create.html` with context `{"form": form}` — return.
  2. **POST branch — form bind & validate:** `form = CreateLeagueForm(request.POST)`,
     `if not form.is_valid()` → render the same template + form (errors
     auto-attached); return.
  3. **Build RNG + name pools:** Construct a fresh `rng = random.Random()`
     (default-seeded — LG-01b does not pin a deterministic seed; team /
     player generation is intentionally random per create). Pass
     `team_names_pool=list(TEAM_NAMES)` and
     `player_names_pool=list(PLAYER_NAMES)` (defensive `list(...)` copies so
     `_generate_teams` may mutate the pools internally without leaking back
     into the constants — mirrors the LG-00b roster-import precedent).
  4. **Call `_generate_teams`:** `created_teams = _generate_teams(`
     `cleaned["num_teams"], 6, rng=rng, mean=cleaned["mean"],`
     `std_dev=cleaned["std_dev"], team_names_pool=team_names_pool,`
     `player_names_pool=player_names_pool)`. `players_per_team` is the
     **literal `6`** (locked, not a form field, not configurable per
     create). The return value is `list[Team]` of length `num_teams`.
  5. **Create `League` + `Season`:**
     - `league = League.objects.create(name=cleaned["league_name"], mode="league", state="active")`
       *(`mode="league"` is the default per LG-01; explicit for clarity.
       `state="active"` is the default; explicit for clarity. Either may be
       omitted from the `.create(...)` kwargs — the field-level defaults
       still produce the locked values.)*
     - `season = Season.objects.create(league=league, name=cleaned["season_name"],`
       `start_date=cleaned["start_date"], state="draft",`
       `schedule_format=cleaned["schedule_format"])`
       *(`state="draft"` and `schedule_format="single_round_robin"` are the
       field-level defaults; explicit for clarity.)*
  6. **Enroll teams + redirect:** `season.teams.add(*created_teams)` (M2M
     bulk-add — single SQL `INSERT` per row, no per-team `.save()`), then
     `return redirect("season_standings", season_id=season.id)`.

- **Redirect URL name (locked):** `"season_standings"` (the LG-01 GET URL at
  `/seasons/<int:season_id>/standings/`). Reverse kwarg name is `season_id`
  (pinned by `matches/season_urls.py`).
- **Transaction semantics:** `@transaction.atomic` wraps the entire view
  body. A `_generate_teams` raise (or any subsequent step) rolls back the
  League + Season + Teams + Players + slot FKs + M2M rows atomically — no
  half-created League can exist on error. The transaction is implicit via the
  decorator; **no explicit `savepoint` / `transaction.atomic()` context
  manager** is added inside the body.
- **Error path:** form-invalid POST re-renders the same template with the
  bound form (Django's default unbound-vs-bound rendering surfaces the
  per-field errors). No flash / `messages` framework usage.
- **No `messages.success(...)` call** on the redirect path (out of scope —
  the redirect itself is the user feedback).

---

## 4. Cross-app Import

Single line, top of `matches/views.py`, alongside the other top-of-file
imports:

```
from teams.views import _generate_teams
```

- **Scope:** this is the **only** cross-app import LG-01b introduces. Name
  pools (`TEAM_NAMES`, `PLAYER_NAMES`) come from `teams.constants` (no view
  layer involved). The leading underscore on `_generate_teams` reflects its
  intra-`teams/` private status; LG-01b promotes it to a cross-app seam by
  reading-only (no rename, no signature change, no relocation).
- **The `_generate_teams` signature is the seam itself** (locked, not
  modified): `_generate_teams(num_teams: int, players_per_team: int, *, rng: random.Random, mean: int, std_dev: int, team_names_pool: list[str], player_names_pool: list[str]) -> list[Team]`. LG-01b
  **must not edit** `teams/views.py::_generate_teams`, `teams/forms.py`,
  `teams/constants.py`, or any other `teams/` file.

---

## 5. Template

NEW file `templates/leagues/create.html` extends `base.html`.

- **Path:** `laserforce_simulator/templates/leagues/create.html`
- **`{% block title %}Create League{% endblock %}`** (locked exact string)
- **`{% block content %}`:** a single `<form method="post">` containing
  `{% csrf_token %}`, the 7 form fields (rendered field-by-field — NOT
  `{{ form.as_p }}` / `{{ form.as_table }}` so DOM ids are deterministic),
  and a submit button.
- **Locked DOM ids (every id is the form field's `<input>` / `<select>` id
  attribute, NOT the wrapping `<label>` / `<div>` — tests will assert
  presence via `assertContains` / DOM parse):**

| DOM id | Element |
|---|---|
| `league-create-form` | outer `<form>` element |
| `league-create-league-name` | `<input type="text">` for `league_name` |
| `league-create-season-name` | `<input type="text">` for `season_name` |
| `league-create-start-date` | `<input type="date">` for `start_date` |
| `league-create-num-teams` | `<select>` for `num_teams` |
| `league-create-schedule-format` | `<select disabled>` for `schedule_format` |
| `league-create-mean` | `<input type="number">` for `mean` |
| `league-create-std-dev` | `<input type="number">` for `std_dev` |
| `league-create-submit` | the submit `<button>` / `<input type="submit">` |

- **Field-error display:** each field renders its `{{ form.<field>.errors }}`
  block adjacent to the input (standard Django pattern). No global error
  banner pinned (form-level non-field errors are out of scope — LG-01b has no
  `clean()`-level cross-field validation).
- **No client-side JS in the template** — `disabled` on `schedule_format` is
  the only client-side affordance and it is a pure HTML attribute, not JS.

---

## 6. Test Boundary

NEW file `matches/tests/test_league_create.py` (mirrors the `test_league_list.py`
LG-01a precedent). Four `TestCase` subclasses, pinned names and pinned
assertion surface:

### `TestLeagueCreateGet(TestCase)`
- GET `/leagues/create/` → **200**.
- Template `leagues/create.html` used (`assertTemplateUsed`).
- Response contains all 9 locked DOM ids (substring or DOM-parse — Code
  agent's discretion which assertion form): `league-create-form`,
  `league-create-league-name`, `league-create-season-name`,
  `league-create-start-date`, `league-create-num-teams`,
  `league-create-schedule-format`, `league-create-mean`,
  `league-create-std-dev`, `league-create-submit`.
- The `<select>` for `schedule_format` carries the `disabled` attribute.
- Reverse via `reverse("league_create")` resolves to `/leagues/create/`.

### `TestLeagueCreatePost(TestCase)`
- POST valid payload (e.g. `{league_name: "Spring 2026", season_name: "Season 1", start_date: "2026-06-01", num_teams: 4, schedule_format: "single_round_robin", mean: 50, std_dev: 15}`) →
  **302** redirect.
- Redirect URL equals `reverse("season_standings", args=[season.id])` where
  `season` is the newly-created Season (look up via
  `Season.objects.get(name="Season 1")`).
- After POST: exactly 1 new `League` row with `name="Spring 2026"`,
  `mode="league"`, `state="active"`.
- After POST: exactly 1 new `Season` row with `name="Season 1"`,
  `state="draft"`, `schedule_format="single_round_robin"`,
  `start_date == date(2026, 6, 1)`, `league_id == league.id`,
  `champion_team is None`, `starting_team_ids_json is None`.
- After POST: exactly `num_teams == 4` new `Team` rows created — the Season's
  M2M `season.teams.count() == 4`.
- Each created Team has 6 active-slot Players (the existing `_generate_teams`
  invariant — assert via `team.active_players` length).
- Sanity assertion: the redirect target page `/seasons/<id>/standings/`
  returns 200 (i.e. the new Season renders in `draft` preview mode — this
  smokes the LG-01 standings view's `is_draft_preview` branch with a real
  freshly-created Season).
- Boundary case: `num_teams=16` POST creates 16 Teams + 96 Players (16 × 6)
  without raising.

### `TestLeagueCreateFormValidation(TestCase)`
- Missing `league_name` → form re-renders with **200**, no new `League`
  created (`League.objects.count()` unchanged), form has a `league_name`
  error.
- `num_teams=5` (not in `{4, 8, 12, 16}`) → form re-renders with 200, no
  new League/Season/Team rows, form has a `num_teams` error.
- `mean=-1` → form re-renders, `mean` error, no new rows.
- `mean=101` → form re-renders, `mean` error, no new rows.
- `std_dev=0` → form re-renders, `std_dev` error, no new rows.
- `std_dev=41` → form re-renders, `std_dev` error, no new rows.
- Empty `start_date` → form re-renders, `start_date` error, no new rows.
- A POST that tampers with `schedule_format` (e.g. `schedule_format="double_round_robin"`)
  still creates the Season with `schedule_format="single_round_robin"`
  (because the field is `disabled=True` and Django serves the initial
  value) — assert the persisted Season has the locked value, the POST is
  not rejected.

### `TestSeamWithGenerateTeams(TestCase)`
**Locked: no `mock.patch` on `_generate_teams`.** Tests exercise the real
function end-to-end so a signature drift between LG-01b's call site and
`teams/views.py` surfaces as a test failure rather than a silent mock pass.
- Real-call test: POST `num_teams=4` and assert the 4 created Teams each
  have **6** Players (the locked `players_per_team=6` literal flows through
  to the helper). Players are distributed across the 6 slot FKs
  (`slot_commander`, `slot_heavy_red`, `slot_heavy_blue`, `slot_scout_red`,
  `slot_scout_blue`, `slot_medic` — whatever the existing `_generate_teams`
  slot-fill rule pins; LG-01b inherits it untouched).
- Real-call test: stat range — each Player's `accuracy` / `survival` /
  `decision_making` / etc. falls within `[0, 100]` (the `_generate_teams`
  clipping invariant; LG-01b does not alter mean / std_dev semantics).
- **Transaction rollback test:** simulate a mid-flow raise by patching
  `Season.objects.create` (NOT `_generate_teams`) to raise after the
  League and Teams have already been created. Assert post-raise:
  `League.objects.filter(name=...).count() == 0` AND
  `Team.objects.filter(name__in=...).count() == 0` (the locked
  `@transaction.atomic` rolls everything back, including the Teams /
  Players / slot FKs created upstream by `_generate_teams`). This pins the
  view's atomicity contract against future refactors that might move the
  `@transaction.atomic` boundary or call `_generate_teams` outside the
  atomic block.

**Tests must NOT touch `simulate_scheduled_round` or any simulator code
path.** LG-01b does not run a simulation; the Season is created in `draft`
state and `_generate_teams` is the only heavy operation. A test that
accidentally enters the simulator (e.g. by calling `season.start_season()`
+ a sim) would be a scope leak and is locked out.

---

## 7. Out of Scope (Locked)

The following are **explicitly out of scope** for LG-01b and must not be
touched by any of the three parallel agents:

- **No model change.** `matches/models.py` is read-only at LG-01b.
- **No migration.** No `0030_*` file; LG-01 shipped `0029_league_season_match_fk.py`
  and that is the final migration in the LG-01x stack until LG-01c.
- **No ADR write.** [ADR-0014](../../docs/adr/0014-league-season-foundation.md)
  and [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md)
  cover the foundation; LG-01b is a CRUD surface and needs no design record.
- **No CONTEXT.md edit.** `League`, `Season`, `Standings` glossary entries
  exist from LG-01.
- **No "Start Season" UI or POST endpoint.** The `draft → active` transition
  via `Season.start_season()` is deferred (to LG-01d or later). LG-01b
  leaves the Season in `draft` indefinitely — the standings page renders the
  draft preview, and the user has no in-product affordance yet to flip it.
- **No JS.** The template is server-rendered HTML only. No Chart.js, no
  htmx, no inline `<script>` blocks.
- **No API / DRF endpoint.** `/api/leagues/`, `/api/seasons/`, and any REST
  surface for create are deferred.
- **No new dependency** (no `pip install`, no `requirements.txt` edit).
- **No edit to `teams/views.py::_generate_teams`** — the function signature
  is the seam and changing it would break LG-01b's contract with `teams/`.
- **No edit to `teams/forms.py`** — LG-01b's form lives entirely in
  `matches/forms.py`. The `teams/forms.py::PlayerStatsForm` /
  `BulkRosterImportForm` (LG-00b precedent) is untouched.
- **No edit to `LeagueAdmin` / `SeasonAdmin`** (LG-01 shipped both; LG-01b
  does not extend the admin surface).
- **The Free Agents Team must not be touched.** `_generate_teams` creates
  fresh Teams + Players from the constants pools; LG-01b does not pull from
  or push to the Free Agents Team (LG-00 / LG-00b territory).
- **No edit to `matches/league_urls.py` beyond inserting the single
  `create/` line.** The LG-01a `league_list` entry is preserved verbatim.
- **No edit to `matches/views.py::league_list`** (LG-01a's view stays
  read-only).
- **No edit to `templates/leagues/list.html`** — the
  `league-create-link` `href="/leagues/create/"` from LG-01a continues to
  point at the (now-resolving) URL without template-side changes.
- **No `messages.success(...)` flash / `django.contrib.messages` usage.**
- **No deterministic seeding of the RNG** — `random.Random()` is
  default-seeded. LG-01b is not under the SIM-07 / SIM-08 contract (it runs
  no simulator).
- **No simulation mechanics change → no Score Calibration re-baseline
  obligation.**

---

## 8. Locked Names (Recap)

Every public name LG-01b introduces or pins, in one place:

| Kind | Name | Notes |
|---|---|---|
| URL path | `/leagues/create/` | Inserted before `path("", ...)` in `matches/league_urls.py` |
| URL name | `league_create` | Bare name, no `app_name`. Reverse via `reverse("league_create")` |
| View | `matches.views.league_create` | `(request: HttpRequest) -> HttpResponse`, `@transaction.atomic` |
| Form class | `matches.forms.CreateLeagueForm` | `forms.Form` subclass |
| Form fields | `league_name`, `season_name`, `start_date`, `num_teams`, `schedule_format`, `mean`, `std_dev` | 7 fields, pinned order; `players_per_team` is NOT a field |
| Template | `templates/leagues/create.html` | Extends `base.html`, block title `Create League` |
| Cross-app import | `from teams.views import _generate_teams` | The one new top-of-file import in `matches/views.py` |
| Redirect URL name | `season_standings` | Reverse kwarg `season_id` (LG-01 pinning) |
| DOM id (form) | `league-create-form` | Outer `<form>` |
| DOM id (input) | `league-create-league-name` | `league_name` `<input>` |
| DOM id (input) | `league-create-season-name` | `season_name` `<input>` |
| DOM id (input) | `league-create-start-date` | `start_date` `<input type="date">` |
| DOM id (input) | `league-create-num-teams` | `num_teams` `<select>` |
| DOM id (input) | `league-create-schedule-format` | `schedule_format` `<select disabled>` |
| DOM id (input) | `league-create-mean` | `mean` `<input type="number">` |
| DOM id (input) | `league-create-std-dev` | `std_dev` `<input type="number">` |
| DOM id (button) | `league-create-submit` | The submit button |
| Test file | `matches/tests/test_league_create.py` | NEW file |
| Test class | `TestLeagueCreateGet` | GET-side surface |
| Test class | `TestLeagueCreatePost` | POST happy path |
| Test class | `TestLeagueCreateFormValidation` | Per-field validators |
| Test class | `TestSeamWithGenerateTeams` | End-to-end real `_generate_teams` + transaction rollback |
| Locked literal | `players_per_team = 6` | Server-side, not a form field, inline in view step 4 |
| Locked literal | `mode = "league"` | On `League.objects.create` (defaults to this anyway) |
| Locked literal | `state = "active"` | On League (default) |
| Locked literal | `state = "draft"` | On Season (default) |
| Locked literal | `schedule_format = "single_round_robin"` | On Season + form (default + disabled choice) |

---

**Seam summary in one sentence:** LG-01b adds `GET/POST /leagues/create/` →
`matches.views.league_create` → `matches.forms.CreateLeagueForm` →
`teams.views._generate_teams(num_teams, 6, ...)` → `League.objects.create(...)` +
`Season.objects.create(...)` + `season.teams.add(*teams)` →
`redirect("season_standings", season_id=season.id)`, all under a single
`@transaction.atomic` wrapper, with `templates/leagues/create.html` and
`matches/tests/test_league_create.py` as the rendering / verification
surfaces.
