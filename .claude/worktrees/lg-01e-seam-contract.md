# LG-01e — Start Next Season · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01e
ships the **Start Next Season** POST endpoint that fills the remaining
LG-01c-locked `action_button_state="start_next_season"` placeholder slot
on both dashboards — `POST /leagues/<int:league_id>/next-season/` creates
a fresh draft `Season` inside the same `League` with copied teams +
auto-generated name + Jan-1-next-year `start_date`, then redirects to the
new Season's LG-01c dashboard. **No model change, no migration, no ADR, no
CONTEXT.md edit, no new pure module, no simulator touch, no JS, no
`django.contrib.messages` usage, no async / Celery, no admin change.**

This contract mirrors the structure of the LG-01b
[`.claude/worktrees/lg-01b-seam-contract.md`](lg-01b-seam-contract.md)
CRUD-flow precedent (the closest analogue — a single `@transaction.atomic`
POST view that creates a `Season` row) and the LG-01d
[`.claude/worktrees/lg-01d-seam-contract.md`](lg-01d-seam-contract.md)
POST-endpoint + dashboard-form-wiring precedent (the 405 guard pattern,
the active-Season guard pattern, and the symmetric Season / League
dashboard form-wiring pattern). It extends the LG-01c dashboard surface
pinned in [`.claude/worktrees/lg-01c-seam-contract.md`](lg-01c-seam-contract.md)
by replacing the LG-01c-locked `<button disabled data-action-state="start_next_season">`
placeholder with a real `<form>` on both dashboards.

---

## 0. Overview

LG-01e adds **one POST endpoint** wired to the previously-disabled
`action_button_state="start_next_season"` slot on both LG-01c dashboards:

- `POST /leagues/<int:league_id>/next-season/` — sync, calls a single
  `@transaction.atomic` block creating a new `Season(state="draft")` in
  the same `League` with `name = f"Season {league.seasons.count() + 1}"`,
  `start_date = date(latest_completed.start_date.year + 1, 1, 1)`,
  `schedule_format = latest_completed.schedule_format`, and the M2M
  populated from `latest_completed.starting_team_ids_json` (the
  **snapshot**, NOT the live `teams.all()` — defence-in-depth, mirrors
  the LG-01 schedule generator's frozen-snapshot precedent). Returns
  HTTP 302 to `season_dashboard(season_id=new_season.id)` on success.
- The LG-01c-locked `action_button_state="start_next_season"` branch on
  both `templates/leagues/dashboard.html` and `templates/seasons/dashboard.html`
  is modified — the `<button disabled>` placeholder is replaced by a
  real `<form method="post" action="{% url 'next_season' league_id=… %}">`
  containing `{% csrf_token %}` + a single submit button labelled
  "Start Next Season". The LG-01c-locked `{league,season}-dashboard-action-button`
  outer-wrapper `<span>` ids continue to wrap the new form (mirrors
  LG-01d's stacking pattern — backwards compatibility with LG-01c
  tests). Two NEW DOM ids are added — `league-dashboard-next-season-form`
  and `season-dashboard-next-season-form` (the `<form>` elements).
- **No new context key.** The LG-01d `play_error: str | None` already on
  both dashboards is **NOT** populated by LG-01e — the active-Season
  guard is a redirect, not a re-render. There is no LG-01e-specific
  error to display.

---

## 1. URL

A single new path entry in `matches/league_urls.py`. **No new URL
include file.** The LG-01a-mounted file is already routed by
`laserforce_simulator/urls.py`.

### 1a. `matches/league_urls.py` — final `urlpatterns` order

The new `path(...)` line is inserted **AFTER** the LG-01c
`path("<int:league_id>/", views.league_dashboard, name="league_dashboard")`
entry and **BEFORE** the LG-01a `path("", views.league_list, name="league_list")`
entry. Django URL resolution is first-match — the typed
`<int:league_id>/next-season/` pattern is more specific than the LG-01c
`<int:league_id>/` (no trailing segment), but the contract pins the
ordering rather than relying on "longer is more specific". The LG-01b
`create/` literal stays at the top (literal segment matches before the
`<int:>` converter).

Final `urlpatterns` order (LG-01b top + LG-01c dashboard + LG-01e
addition + LG-01a tail):

1. `path("create/", views.league_create, name="league_create")` *(LG-01b)*
2. `path("<int:league_id>/", views.league_dashboard, name="league_dashboard")` *(LG-01c)*
3. `path("<int:league_id>/next-season/", views.next_season, name="next_season")` *(NEW — LG-01e)*
4. `path("", views.league_list, name="league_list")` *(LG-01a)*

### 1b. URL name + HTTP method matrix

| URL name | Path | HTTP | Failure modes |
|---|---|---|---|
| `next_season` | `/leagues/<int:league_id>/next-season/` | POST only | 405 on non-POST (`HttpResponseNotAllowed(["POST"])`); 404 on missing `League`; 302 to the existing active Season's dashboard when `league.active_season is not None` (active-Season guard — idempotent on the double-submit race); 400 `HttpResponseBadRequest("No completed Season in this League.")` when no completed Season exists in the League. |

- **URL name** is `next_season` (bare, **no `app_name`**) — mirrors the
  LG-01a / LG-01b / LG-01c bare-name precedent (`league_list`,
  `league_create`, `league_dashboard`).
- **POST only** with `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`
  as the **first** line of the view body (LG-01d `start_season` / `play_week`
  precedent). No `@require_POST` decorator.

---

## 2. View

A single new function appended to `matches/views.py` (the file LG-01 /
LG-01a / LG-01b / LG-01c / LG-01d's views live in).

### 2a. `next_season`

```python
def next_season(request: HttpRequest, league_id: int) -> HttpResponse:
    """LG-01e — POST entry point for the Start Next Season action.

    Creates a fresh ``draft`` Season inside ``league_id`` with copied
    teams from the latest completed Season's snapshot, an auto-generated
    name, and a Jan-1-next-year start date. Redirects to the new
    Season's dashboard on success.

    Guards (in order):
        1. 405 on non-POST.
        2. 404 on missing League.
        3. 302 redirect to ``season_dashboard`` of ``league.active_season``
           when a non-completed Season already exists (active-Season
           guard — idempotent on double-submit; the UI hides the
           button when a Season is in progress, but a stray POST
           lands the user on the in-progress Season's dashboard).
        4. 400 ``HttpResponseBadRequest("No completed Season in this League.")``
           when no completed Season exists (defensive — should never
           fire because the LG-01c button only shows when displayed
           Season is completed).
    """
```

- **Decorator:** `@transaction.atomic` (single decorator, no other
  middleware decorators added — mirrors LG-01b `league_create`).
- **405 guard:** `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`
  as the **first** line of the body, BEFORE any ORM hit (LG-01d
  precedent — the locked pattern).
- **404 guard:** `league = get_object_or_404(League, pk=league_id)`.
- **Body (pinned step order, no steps reordered, no steps added or
  omitted):**

  1. **Active-Season guard:** `if league.active_season is not None: return redirect("season_dashboard", season_id=league.active_season.id)`.
     Uses the LG-01 `League.active_season` `@property` (NOT a
     re-implemented `.exclude(state="completed")` query — the same
     property LG-01c's `league_dashboard` consults). A draft or active
     Season is non-completed; both block creation of a second
     non-completed Season per the LG-01 Active-Season invariant
     enforced by `Season.clean()`. The redirect lands the user on the
     existing in-progress Season's LG-01c dashboard so they can act
     from there.
  2. **Locate latest completed Season:**
     `latest_completed = league.seasons.filter(state="completed").order_by("-id").first()`.
     If `latest_completed is None`: `return HttpResponseBadRequest("No completed Season in this League.")`.
     **Defensive 400** — should never fire because the LG-01c
     `action_button_state="start_next_season"` branch only renders
     when `displayed_season.state == "completed"`, and that only
     happens when the League has at least one completed Season (and
     no active one — the active-Season guard in step 1 has already
     fired in that case). Pins the clean-400 behaviour so a direct
     POST (e.g. curl, replay attack) cannot crash with a `NoneType`
     `AttributeError`.
  3. **Compute the new Season's locked fields:**
     - `name = f"Season {league.seasons.count() + 1}"` — count is
       evaluated **BEFORE** the create, so the new Season takes the
       next sequential index (Season 1 already exists ⇒ count == 1
       ⇒ new Season is "Season 2"). Uses `.count()` on the reverse
       accessor (single `SELECT COUNT(*)` query — no in-memory
       materialisation).
     - `start_date = date(latest_completed.start_date.year + 1, 1, 1)` —
       **calendar-year jump, Jan 1 of next year**. Imports `date`
       from `datetime` (already imported at the top of `views.py`
       per the LG-01 / LG-01b precedent — defensive check first,
       no duplicate import).
     - `schedule_format = latest_completed.schedule_format` — carry
       over the format choice from the previous Season verbatim.
       At LG-01e merge time the only valid value is
       `"single_round_robin"` (the LG-01 single-element
       `SCHEDULE_FORMATS` tuple), but the contract passes through
       whatever the previous Season had — future schedule formats
       inherit automatically.
     - `state = "draft"` — **explicit** even though it's the
       `Season.state` field-level default (mirrors LG-01b's
       explicit-for-clarity precedent on field-level defaults).
     - `league = league`.
     - **NOT set** on the new Season: `starting_team_ids_json`
       (snapshotted by `Season.start_season()` at activation time,
       NOT at create — LG-01 precedent), `champion_team` (`None`
       by default, only stamped by `complete_if_finished`).
  4. **Create the new Season:**
     `new_season = Season.objects.create(league=league, name=name, start_date=start_date, schedule_format=schedule_format, state="draft")`.
  5. **Copy teams from the snapshot (NOT the live M2M):**
     - `team_ids = latest_completed.starting_team_ids_json or []`
       (defensive `or []` — the LG-01 schedule generator's
       `season.starting_team_ids_json or []` precedent; an unset
       snapshot ⇒ empty team list ⇒ defensive empty Season created,
       no crash).
     - `teams_qs = Team.objects.filter(id__in=team_ids)`.
     - `new_season.teams.add(*teams_qs)` — M2M bulk-add (single
       `INSERT` per row, no per-team `.save()`; mirrors LG-01b
       `season.teams.add(*created_teams)`).
     - **Defensive degradation:** Teams that no longer exist
       (`Team.objects.filter(id__in=…)` silently drops missing ids)
       are skipped — `Team.delete` cascades to `Match` but NOT to
       the JSON snapshot list. No explicit error, no log line; the
       new Season simply has fewer teams. Acceptable because the
       only way to lose a Team between completed-Season and
       Start-Next-Season is admin deletion.
  6. **Redirect on success:**
     `return redirect("season_dashboard", season_id=new_season.id)`
     → HTTP 302 to the LG-01c new Season's dashboard (which renders
     in `season_mode == "draft"` because the new Season is `draft`).

- **Transaction semantics:** `@transaction.atomic` wraps the entire
  view body. A failure in any step (e.g. `Season.objects.create`
  raises an `IntegrityError`, or `season.teams.add(*teams_qs)`
  raises mid-flow) rolls back the new Season row + any M2M rows
  atomically — no half-created Season can exist on error. The
  transaction is implicit via the decorator; **no explicit
  `savepoint` / `transaction.atomic()` context manager** is added
  inside the body.

- **Error path:** the active-Season guard and the no-completed-Season
  guard are the only two non-success branches. The active-Season
  guard is a 302 (idempotent — the user lands on the in-progress
  Season). The no-completed-Season guard is a clean 400 (defensive
  — should never fire from the LG-01c UI). **No `messages.*`
  usage, no flash, no re-render-with-error-context** — the
  redirect / 400 IS the user feedback.

- **No `play_error` population:** LG-01e does NOT add a new context
  key. The LG-01d `play_error` already on both dashboards is
  untouched — LG-01e errors either redirect (active-Season guard)
  or return a 400 (no-completed guard); neither re-renders a
  dashboard with `play_error` populated. The dashboard render
  triggered by the success redirect naturally has `play_error = None`
  (the GET render in `season_dashboard`).

---

## 3. Cross-app imports

Single new top-of-file import in `matches/views.py` (defensive — check
existing imports first, do NOT duplicate):

```python
from teams.models import Team
```

- **Check first:** the LG-01c `_build_dashboard_context` view-side
  materialisation already imports `Team` for `Team.objects.in_bulk(...)`
  (the standings-snippet pairing query). The top-of-file already has
  `from teams.models import Team, Player` (per the LG-01 `views.py`
  imports — pinned at LG-01b `Cross-app Import` §4 verbatim). **No
  new import is needed** at LG-01e merge time. The contract pins the
  defensive check + no-duplicate rule rather than adding a redundant
  line.

- **`from datetime import date`** — should already be imported at the
  top of `views.py` (LG-01b uses `start_date` and LG-01 standings /
  schedule views use `timedelta`). Defensive check + no-duplicate
  rule applies.

- **`from django.db import transaction`** — already imported (LG-01b
  precedent). Defensive check + no-duplicate.

- **`from django.shortcuts import render, get_object_or_404, redirect`**
  — already imported (LG-01b precedent). Defensive check + no-duplicate.

- **`from django.http import HttpResponseNotAllowed, HttpResponseBadRequest`**
  — `HttpResponseNotAllowed` is already imported (LG-01c / LG-01d
  precedent). `HttpResponseBadRequest` may or may not be — add it
  to the existing `from django.http import …` line if not present.
  (The Code agent's responsibility — the contract pins the import
  by name, not the exact diff.)

- **`from .models import League, Season`** — already imported (LG-01
  precedent). Defensive check + no-duplicate.

LG-01e introduces **zero** truly new cross-app imports. Every name it
needs is already at the top of `matches/views.py`. The Code agent
must verify (not re-add) each import.

---

## 4. Template wiring

**Two MODIFIED templates** — `templates/leagues/dashboard.html` and
`templates/seasons/dashboard.html`. The LG-01c-locked
`action_button_state="start_next_season"` branch currently renders a
`<button disabled data-action-state="start_next_season">Start Next
Season</button>` placeholder inside the
`{league,season}-dashboard-action-button` outer wrapper `<span>` (which
also covers the `"none"` state via the `{% else %}` fall-through
branch in both templates as they stand at LG-01d merge time). LG-01e
splits that `{% else %}` into TWO branches — `start_next_season`
becomes a real `<form>`, `none` keeps the `<button disabled>`.

### 4a. Branch matrix (post-LG-01e)

| `action_button_state` | Rendered markup (League dashboard) | Rendered markup (Season dashboard) |
|---|---|---|
| `"start_season"` | LG-01d `<form>` (unchanged) | LG-01d `<form>` (unchanged) |
| `"play_next"` | LG-01d dropdown (unchanged) | LG-01d dropdown (unchanged) |
| `"start_next_season"` | **NEW — `<form id="league-dashboard-next-season-form" method="post" action="{% url 'next_season' league_id=league.id %}">{% csrf_token %}<button type="submit" data-action-state="{{ action_button_state }}">Start Next Season</button></form>`** | **NEW — `<form id="season-dashboard-next-season-form" method="post" action="{% url 'next_season' league_id=season.league_id %}">{% csrf_token %}<button type="submit" data-action-state="{{ action_button_state }}">Start Next Season</button></form>`** |
| `"none"` | LG-01c `<button disabled data-action-state="none">No Season</button>` (unchanged) | (not reachable — Season dashboard never renders `"none"`; LG-01c invariant — but the `{% else %}` fall-through MUST keep the disabled-button shape for parity) |

- **`league_id` derivation** — on the **league dashboard**, the
  template has `league` in context, use `league.id` directly. On the
  **season dashboard**, the template has `season` in context, use
  `season.league_id` (NOT `season.league.id` — the `_id` accessor
  avoids the JOIN; the value is the same).
- **`{% csrf_token %}`** is mandatory inside the `<form>` (Django
  CSRF middleware). LG-01d precedent.
- **Submit button:** a single `<button type="submit">Start Next Season</button>`
  with text `"Start Next Season"` (mirrors the LG-01c-locked
  `action_button_label` value, which the
  `_build_dashboard_context` helper already sets to `"Start Next
  Season"` in the `season_mode == "completed"` branch). The
  `data-action-state="{{ action_button_state }}"` attribute is
  carried on the submit button (mirrors the LG-01c
  `data-action-state` attribute on the placeholder `<button disabled>`
  — tests assert this attribute is still present post-LG-01e for
  the LG-01c-test backwards compatibility).
- **NO inline JS, NO `<script>` block, NO `fetch()` interception** —
  the form submits synchronously (server-side 302 redirect on
  success; LG-01e is sync, unlike LG-01d's async Play Two Months /
  Until End forms).
- **NO extra `<div>` wrapper** — the form sits directly inside the
  LG-01c-locked `<span id="{league,season}-dashboard-action-button">`
  outer wrapper. The wrapper continues to be present in ALL four
  `action_button_state` branches for LG-01c-test backwards
  compatibility (mirrors LG-01d's stacking pattern verbatim).

### 4b. Template diff sketch (Season dashboard — leagues dashboard is symmetric)

The Season dashboard's existing `{% else %}` block (lines ~43–45 in
`templates/seasons/dashboard.html` at LG-01d merge time):

```django
{% else %}
    <button class="btn btn-outline-primary btn-sm" disabled data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>
{% endif %}
```

splits into two branches:

```django
{% elif action_button_state == "start_next_season" %}
    <form id="season-dashboard-next-season-form" method="post" action="{% url 'next_season' league_id=season.league_id %}" class="d-inline">
        {% csrf_token %}
        <button type="submit" class="btn btn-outline-primary btn-sm" data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>
    </form>
{% else %}
    <button class="btn btn-outline-primary btn-sm" disabled data-action-state="{{ action_button_state }}">{{ action_button_label }}</button>
{% endif %}
```

The League dashboard's equivalent split uses
`{% url 'next_season' league_id=league.id %}` and the form id
`league-dashboard-next-season-form`. The exact CSS classes
(`btn btn-outline-primary btn-sm`, `d-inline`) are at Code agent's
discretion — only the form id, the `action` URL, the submit text,
the `{% csrf_token %}` presence, and the `data-action-state`
attribute are pinned.

### 4c. NEW locked DOM ids (2)

| DOM id | Template | Branch presence | Element |
|---|---|---|---|
| `league-dashboard-next-season-form` | `templates/leagues/dashboard.html` | only when `action_button_state == "start_next_season"` | the `<form method="post">` element (its `id` attribute) |
| `season-dashboard-next-season-form` | `templates/seasons/dashboard.html` | only when `action_button_state == "start_next_season"` | the `<form method="post">` element (its `id` attribute) |

The LG-01c-locked `league-dashboard-action-button` /
`season-dashboard-action-button` outer-wrapper `<span>` ids continue to
wrap the form in ALL four `action_button_state` branches (LG-01c-test
backwards compatibility — mirrors LG-01d's stacking pattern). The
LG-01e form ids stack **underneath** that wrapper, not replacing it.

The LG-01c-locked `data-action-state="{{ action_button_state }}"`
attribute is carried on the submit `<button type="submit">` inside the
form (not on the outer wrapper `<span>`) so existing LG-01c tests
that scan for `data-action-state="start_next_season"` continue to
pass post-LG-01e.

---

## 5. Context keys

**No new context keys.** The LG-01c + LG-01d context keys are sufficient:

- LG-01c provides `action_button_label = "Start Next Season"` and
  `action_button_state = "start_next_season"` in the
  `season_mode == "completed"` branch — both consumed verbatim by the
  LG-01e template branch (the label is rendered as the submit button
  text; the state is rendered as the `data-action-state` attribute).
- LG-01c provides `league` (on the league dashboard) and `season` (on
  the season dashboard) — LG-01e reads `league.id` (league dashboard)
  / `season.league_id` (season dashboard) to build the
  `{% url 'next_season' league_id=… %}` reverse.
- LG-01d provides `play_error: str | None` on both dashboards — LG-01e
  does **NOT** populate this key (the LG-01e error paths redirect or
  return 400, neither re-renders a dashboard).
- LG-01d provides `play_job_id: str | None` on both dashboards —
  LG-01e does NOT use this key (LG-01e is sync, no Celery, no
  polling).

**The LG-01c `_build_dashboard_context` pure module is NOT edited.**
Its 11-key body context is consumed verbatim. The
`season_dashboard.py` pure module gains zero new functions.

---

## 6. Out of scope (locked)

The following are **explicitly out of scope** for LG-01e and must not
be touched by any of the three parallel agents:

- **No model change.** `matches/models.py` is read-only at LG-01e.
- **No migration.** LG-01's `0029_league_season_match_fk.py` remains
  the final migration in the LG-01x stack.
- **No ADR write.** [ADR-0014](../../docs/adr/0014-league-season-foundation.md)
  + [ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md)
  + [ADR-0016](../../docs/adr/0016-play-season-job-execution-model.md)
  cover the foundation, the schedule algorithm surface, and the play
  job-execution model. LG-01e is a thin CRUD POST endpoint and
  needs no design record — nothing surprising-without-context,
  nothing hard-to-reverse, no real trade-off requiring a record.
- **No CONTEXT.md edit.** `League` / `Season` / `Standings` /
  `Matchday` / `Job` / etc. glossary entries already exist. LG-01e
  introduces no new domain term — "Start Next Season" is a UI label,
  not a domain concept.
- **No new pure module.** LG-01e is pure CRUD; no aggregation logic
  worth factoring out. The `matches/season_dashboard.py` pure
  module gains zero new functions.
- **No "Archive League" toggle UI.** Deferred to admin-only access
  (`LeagueAdmin` already supports the `state = "archived"` flip);
  no public-facing button at LG-01e. **Narrows PLAN.md** — the
  Archive League UI is dropped from the LG-01x scope; if needed
  later, it earns its own task.
- **No edit-draft UI.** Editing a `draft` Season's roster / start
  date / name is admin-only at LG-01e merge time. `SeasonAdmin`
  already supports inline edits via `filter_horizontal=("teams",)`
  for the M2M and the default ModelAdmin form fields for the
  scalar fields. Deferred.
- **No `Season.state="archived"` value.** Completed Seasons are
  already effectively read-only per the LG-01 invariants
  (`complete_if_finished` is idempotent; the M2M is frozen by
  `starting_team_ids_json` snapshot at activation; `complete_if_finished`
  stamps `champion_team` and no further writes are expected). No
  state-machine extension at LG-01e.
- **No edit to `matches/models.py`** — model layer untouched.
- **No edit to `matches/simulation.py`** — no simulator touch.
- **No edit to `matches/standings.py`** — LG-01 pure module
  consumed verbatim.
- **No edit to `matches/schedule_generator.py`** — LG-01 pure
  module consumed verbatim.
- **No edit to `matches/season_dashboard.py`** — LG-01c / LG-01d
  pure module consumed verbatim (no new function, no edit to
  the existing 5 functions).
- **No edit to `matches/tasks.py`** — no Celery touch.
- **No edit to `LeagueAdmin` / `SeasonAdmin`** — LG-01 admin
  registrations unchanged.
- **No edit to `templates/leagues/list.html`** (LG-01a list page
  unchanged; the list page does not surface a per-League "Start
  Next Season" action — that lives on the per-League dashboard).
- **No edit to `templates/leagues/create.html`** (LG-01b create
  form unchanged).
- **No edit to `templates/seasons/standings.html`** (LG-01
  standings page unchanged).
- **No edit to `templates/seasons/schedule.html`** (LG-01
  schedule page unchanged).
- **No edit to `matches/forms.py`** — LG-01e takes no form input
  (the POST carries only the `csrfmiddlewaretoken`, no user data
  fields). The new Season's name / start_date / schedule_format /
  teams are all derived server-side from `latest_completed`.
- **No simulator touch, no RNG consumption.** LG-01e runs no
  simulation, draws no random numbers, makes no `BatchSimulator`
  call. **No SIM-07 / SIM-08 contract interaction.** **No Score
  Calibration re-baseline obligation.**
- **No JS, no inline `<script>` block, no htmx, no Alpine, no
  Bootstrap-JS interaction.** The form submits synchronously via
  the browser's native form submission; the server returns a 302
  redirect; the browser follows the redirect to the new Season's
  dashboard. End of story.
- **No new dependency** (no `pip install`, no `requirements.txt`
  edit, no JS framework, no npm install).
- **No API / DRF endpoint.** `/api/leagues/<id>/next-season/`
  is deferred — LG-01e is UI-only.
- **No `messages.success(...)` / `django.contrib.messages` flash.**
  The 302 redirect to the new Season's dashboard is the user
  feedback — they land on the new draft Season's preview page.
- **No backfill.** Existing completed Seasons that pre-date LG-01e
  are not touched. The first time a user clicks Start Next Season
  on any post-LG-01e completed Season creates the new Season
  in-place; no migration needed.
- **No top-nav refactor / sidebar restructure / URL nesting** —
  deferred to LG-01h (the same task LG-01d deferred). LG-01e does
  not touch global navigation.
- **No re-baseline of LG-01c / LG-01d tests** — the LG-01c
  test suite asserts the `action_button_state="start_next_season"`
  branch renders the `data-action-state` attribute and the
  `action_button_label="Start Next Season"` text; both assertions
  continue to pass post-LG-01e because the new `<form>` carries
  the same attribute on its submit button and the same text. The
  LG-01c `TestSeasonDashboardStateMatrix::test_action_button_state_data_attribute_per_state`
  test is the precedent — it scans for `data-action-state="start_next_season"`
  on any element inside the dashboard, not specifically a `<button disabled>`.

---

## 7. Test boundary

Tests live in **3 files** (1 NEW + 2 EXTENDED). Pinned by file name +
class name(s); test method names within each class are at the Tests
agent's discretion unless explicitly pinned below.

### 7a. `matches/tests/test_lg01e_next_season.py` (NEW)

Django `TestCase`. Constructs minimal fixtures per test (League +
Seasons + Teams as needed). Tests must **NOT** touch
`simulate_scheduled_round`, `simulate_match`, `save_games`, or any
simulator entry point — LG-01e runs no simulation.

**Classes:**

- `TestNextSeasonRouting`
  - `test_reverse_resolves_to_expected_path` —
    `reverse("next_season", kwargs={"league_id": league.id})` ==
    `/leagues/<id>/next-season/`.
  - `test_get_returns_405` — GET ⇒ 405.
  - `test_put_returns_405` — PUT ⇒ 405.
  - `test_delete_returns_405` — DELETE ⇒ 405.
  - `test_post_returns_404_for_missing_league` — POST to
    `/leagues/99999/next-season/` (no League with that id) ⇒ 404.

- `TestNextSeasonHappyPath`
  - `test_post_creates_new_draft_season_with_locked_fields` — given
    a League with one completed Season, POST creates exactly 1 new
    Season with `state="draft"`, `league_id == league.id`,
    `champion_team is None`, `starting_team_ids_json is None`. The
    response is a 302 to `reverse("season_dashboard", args=[new_season.id])`.
  - `test_post_redirects_to_new_season_dashboard` — 302 target
    matches `/seasons/<new_season.id>/` exactly.
  - `test_post_increments_season_count_by_exactly_one` — pre-count
    + 1 == post-count.

- `TestNextSeasonNameFormat`
  - `test_name_when_one_completed_exists` — 1 Season exists ⇒ new
    Season is `"Season 2"`.
  - `test_name_when_two_seasons_exist` — 2 Seasons exist (one
    completed, the other still completed historically — though
    only one non-completed allowed at a time per LG-01 invariant)
    ⇒ new Season is `"Season 3"`.
  - `test_name_when_five_seasons_exist` — `f"Season {5 + 1}"` ==
    `"Season 6"`. Set up by creating 4 completed Seasons plus the
    `latest_completed` Season.

- `TestNextSeasonStartDate`
  - `test_start_date_calendar_year_jump` — previous Season's
    `start_date = date(2025, 3, 15)` ⇒ new Season's
    `start_date == date(2026, 1, 1)`.
  - `test_start_date_jan_1_when_prev_was_jan_1` — previous
    `start_date = date(2025, 1, 1)` ⇒ new
    `start_date == date(2026, 1, 1)`.
  - `test_start_date_jan_1_when_prev_was_dec_31` — previous
    `start_date = date(2025, 12, 31)` ⇒ new
    `start_date == date(2026, 1, 1)` (Jan 1 of `prev.year + 1`,
    NOT a `+ 365 days` calculation).
  - `test_start_date_across_multiple_year_boundary` — three
    sequential creates from a 2024-started Season produce 2025,
    2026, 2027 starts (each test seeds a fresh completed Season
    of the previous year before its create).

- `TestNextSeasonScheduleFormatCarry`
  - `test_schedule_format_inherited_from_previous` — previous
    Season has `schedule_format="single_round_robin"` ⇒ new
    Season has `schedule_format="single_round_robin"`.

- `TestNextSeasonTeamsCopiedFromSnapshot`
  - `test_teams_m2m_populated_from_snapshot_json` — previous
    Season's `starting_team_ids_json = [1, 2, 3, 4]` and new
    Season's `teams.all()` resolves to those 4 Team rows by id.
  - `test_teams_copy_uses_snapshot_not_live_m2m` — set up: previous
    Season has snapshot `[1, 2, 3, 4]` AND live `teams.all()`
    artificially mutated to include `Team(id=5)` (defensive test —
    direct M2M `.add(team5)` on a completed Season is admin-only
    in practice but possible). POST creates new Season whose
    `teams.all()` is exactly `{1, 2, 3, 4}` (NOT `{1, 2, 3, 4, 5}`)
    — pins the snapshot-as-source-of-truth rule against future
    refactors.
  - `test_missing_team_in_snapshot_skipped_silently` — snapshot
    `[1, 2, 999]` where Team 999 does not exist ⇒ new Season's
    `teams.all()` is exactly `{1, 2}` (Team 999 silently dropped
    by the `Team.objects.filter(id__in=…)` `IN` clause). No
    error, no log, no 400.

- `TestNextSeasonActiveSeasonGuard`
  - `test_post_redirects_when_active_season_exists` — League has
    1 completed Season + 1 `active` Season. POST returns 302
    to `reverse("season_dashboard", args=[active_season.id])`
    (NOT a new Season; total Season count unchanged at 2).
  - `test_post_redirects_when_draft_season_exists` — League has
    1 completed Season + 1 `draft` Season (e.g. a previous
    Start-Next-Season click already created the draft but the
    user clicked the button again from a stale browser tab).
    POST returns 302 to the draft Season's dashboard (count
    unchanged at 2).
  - `test_active_season_guard_does_not_create_new_season` — pin
    that `Season.objects.count()` is unchanged across the
    blocked POST.

- `TestNextSeasonNoCompletedGuard`
  - `test_post_returns_400_when_no_completed_season_exists` — League
    has zero Seasons (or only non-completed Seasons that did not
    trigger the active-Season guard for some defensive reason —
    practically: zero Seasons total). POST returns 400 with
    body containing `"No completed Season in this League."`.
  - `test_post_returns_400_does_not_create_season` — pin that
    `Season.objects.count()` is 0 (or unchanged) after the
    blocked POST.

- `TestNextSeasonAtomicity`
  - `test_mid_flow_create_failure_rolls_back_m2m` — patch
    `Season.objects.create` to raise `IntegrityError` mid-flow
    AFTER the active-Season + no-completed guards have passed.
    Assert post-raise: no new Season row, no new M2M rows
    (the LG-01b transaction-rollback precedent — pins the
    `@transaction.atomic` boundary against future refactors).

**Locked: NO `mock.patch` on `League.objects.get` / `League.seasons.filter`
/ `Season.objects.create` / `Team.objects.filter` / `season.teams.add`
EXCEPT in `TestNextSeasonAtomicity`** — every other test exercises the
real ORM path end-to-end so signature drift between LG-01e's call sites
and the ORM surfaces as a test failure rather than a silent mock pass.
The atomicity test patches a single boundary (`Season.objects.create`)
to force the rollback path; that single mock is the LG-01b precedent.

### 7b. `matches/tests/test_league_dashboard.py` (EXTENDED)

Append a single new test class to the LG-01c test file. Do NOT modify
any existing class. The new class extends the LG-01c
`TestLeagueDashboardCompletedBranch` surface with LG-01e wiring
assertions.

**Class:**

- `TestLg01eDashboardWiring`
  - `test_completed_renders_next_season_form_with_correct_action_url`
    — set up a League with one completed Season (no active /
    draft). GET `/leagues/<id>/`. Response HTML contains the
    locked `id="league-dashboard-next-season-form"`, the form's
    `action` attribute equals
    `reverse("next_season", kwargs={"league_id": league.id})`,
    the form's `method` attribute is `"post"`, the form contains
    a `{% csrf_token %}` rendered hidden input, the submit
    button text is `"Start Next Season"`, and the submit button
    carries `data-action-state="start_next_season"`.
  - `test_draft_does_not_render_next_season_form` — active/draft
    branches DO NOT render `league-dashboard-next-season-form`
    (the id is absent from the HTML).
  - `test_active_does_not_render_next_season_form` — active
    branch DOES NOT render the form id.
  - `test_none_does_not_render_next_season_form` — none branch
    DOES NOT render the form id.

### 7c. `matches/tests/test_season_dashboard_view.py` (EXTENDED)

Append a single new test class to the LG-01c test file. Do NOT modify
any existing class.

**Class:**

- `TestLg01eDashboardWiring`
  - `test_completed_renders_next_season_form_with_correct_action_url`
    — set up a completed Season inside a League. GET
    `/seasons/<id>/`. Response HTML contains the locked
    `id="season-dashboard-next-season-form"`, the form's
    `action` attribute equals
    `reverse("next_season", kwargs={"league_id": season.league_id})`
    (NOT `season.league.id` — the test asserts the value, which
    is the same; the contract pins `season.league_id` for
    JOIN-free derivation), the form's `method` is `"post"`,
    contains a `{% csrf_token %}` hidden input, submit button
    text is `"Start Next Season"`, submit button carries
    `data-action-state="start_next_season"`.
  - `test_draft_does_not_render_next_season_form` — draft
    Season DOES NOT render the form id.
  - `test_active_does_not_render_next_season_form` — active
    Season DOES NOT render the form id.

### 7d. Test file ownership

| File | Status | Owner |
|---|---|---|
| `matches/tests/test_lg01e_next_season.py` | NEW | Tests agent |
| `matches/tests/test_league_dashboard.py` | EXTENDED — append `TestLg01eDashboardWiring` only | Tests agent |
| `matches/tests/test_season_dashboard_view.py` | EXTENDED — append `TestLg01eDashboardWiring` only | Tests agent |

Tests must NOT touch any simulator entry point. Tests must NOT
`mock.patch` the ORM beyond the single `Season.objects.create` patch
in `TestNextSeasonAtomicity`. Tests must NOT exercise an actual
Celery task path — LG-01e is sync, no Celery.

---

## 8. Locked Names (Recap)

Every public name LG-01e introduces or pins, in one place.

| Kind | Name | Notes |
|---|---|---|
| URL path | `/leagues/<int:league_id>/next-season/` | Inserted AFTER LG-01c `<int:league_id>/` and BEFORE LG-01a `""` in `matches/league_urls.py` |
| URL name | `next_season` | Bare name, no `app_name`. Reverse via `reverse("next_season", kwargs={"league_id": league.id})` |
| URL file edit | `matches/league_urls.py` | Single-line insert; final order `[create/, <int:league_id>/, <int:league_id>/next-season/, ""]` |
| View | `matches.views.next_season` | `(request: HttpRequest, league_id: int) -> HttpResponse`, decorated `@transaction.atomic`, POST-only via `HttpResponseNotAllowed(["POST"])` as the first line of the body |
| Atomic decorator | `@transaction.atomic` | Wraps the entire view body |
| Redirect target (success) | URL name `season_dashboard` | LG-01c — `reverse("season_dashboard", args=[new_season.id])` → HTTP 302 |
| Redirect target (active-Season guard) | URL name `season_dashboard` | LG-01c — `reverse("season_dashboard", args=[league.active_season.id])` → HTTP 302 |
| 400 response | `HttpResponseBadRequest("No completed Season in this League.")` | Fires when no completed Season exists in the League (defensive — should never fire from the LG-01c UI) |
| 405 response | `HttpResponseNotAllowed(["POST"])` | First line of view body, before any ORM hit |
| 404 response | `get_object_or_404(League, pk=league_id)` | After the 405 guard |
| Active-Season check | `league.active_season` (LG-01 `@property`) | NOT a re-implemented `.exclude(state="completed")` query |
| Latest-completed query | `league.seasons.filter(state="completed").order_by("-id").first()` | The reverse accessor `seasons` (LG-01 `related_name`) |
| Templates MODIFIED | `templates/leagues/dashboard.html` + `templates/seasons/dashboard.html` | Replace the LG-01c `<button disabled>` placeholder in the `action_button_state == "start_next_season"` branch with a `<form>` |
| Template branch split | LG-01c `{% else %}` ⇒ `{% elif action_button_state == "start_next_season" %}` + `{% else %}` | The `"none"` state keeps the `<button disabled>`; the `"start_next_season"` state gets the `<form>` |
| NEW DOM id (league dashboard) | `league-dashboard-next-season-form` | The `<form method="post">` element's `id` attribute; only when `action_button_state == "start_next_season"` |
| NEW DOM id (season dashboard) | `season-dashboard-next-season-form` | The `<form method="post">` element's `id` attribute; only when `action_button_state == "start_next_season"` |
| Preserved LG-01c DOM id (league) | `league-dashboard-action-button` | Outer wrapper `<span>` continues to wrap the new form in all 4 branches (LG-01c-test backwards compatibility) |
| Preserved LG-01c DOM id (season) | `season-dashboard-action-button` | Same — outer wrapper `<span>` preserved |
| Preserved LG-01c attribute | `data-action-state="{{ action_button_state }}"` | Carried on the submit `<button type="submit">` inside the form (NOT on the outer wrapper); tests scan for `data-action-state="start_next_season"` and continue to pass |
| Locked literal | `name = f"Season {league.seasons.count() + 1}"` | `.count()` evaluated BEFORE the create |
| Locked literal | `state = "draft"` | Explicit on `Season.objects.create(...)` |
| Locked literal | `start_date = date(latest_completed.start_date.year + 1, 1, 1)` | Calendar-year jump, Jan 1 of next year |
| Locked literal | `schedule_format = latest_completed.schedule_format` | Carry over from previous Season |
| Locked literal (label) | `"Start Next Season"` | Submit button text, matches LG-01c `action_button_label` |
| Locked literal (400 body) | `"No completed Season in this League."` | The exact `HttpResponseBadRequest` body |
| Snapshot read | `latest_completed.starting_team_ids_json or []` | Defensive `or []` (LG-01 schedule generator precedent) |
| Team resolution | `Team.objects.filter(id__in=team_ids)` | Single `IN` query; missing ids silently dropped |
| M2M populate | `new_season.teams.add(*teams_qs)` | Bulk-add, no per-team `.save()` (LG-01b precedent) |
| Cross-app import | `from teams.models import Team` | Already imported at top of `matches/views.py` per LG-01b — defensive check + no-duplicate |
| Cross-app import | `from datetime import date` | Already imported per LG-01 — defensive check + no-duplicate |
| Cross-app import | `from django.db import transaction` | Already imported per LG-01b — defensive check + no-duplicate |
| Cross-app import | `from django.shortcuts import redirect, get_object_or_404` | Already imported per LG-01 / LG-01b — defensive check + no-duplicate |
| Cross-app import | `from django.http import HttpResponseNotAllowed, HttpResponseBadRequest` | `HttpResponseNotAllowed` already imported per LG-01c / LG-01d; `HttpResponseBadRequest` may need adding to the existing `from django.http import …` line |
| Cross-app import | `from .models import League, Season` | Already imported per LG-01 — defensive check + no-duplicate |
| Context key (read) | `league` | LG-01c league dashboard context — `league.id` for `{% url 'next_season' league_id=league.id %}` |
| Context key (read) | `season` | LG-01c season dashboard context — `season.league_id` for `{% url 'next_season' league_id=season.league_id %}` |
| Context key (read) | `action_button_state` | LG-01c — branches the template on `"start_next_season"` |
| Context key (read) | `action_button_label` | LG-01c — `"Start Next Season"` rendered as the submit button text |
| Context key (NOT used) | `play_error` | LG-01d-added; LG-01e does NOT populate it (errors redirect or 400, never re-render) |
| Context key (NOT used) | `play_job_id` | LG-01d-added; LG-01e is sync, no Celery |
| Pure module touched | (none) | `matches/season_dashboard.py` is NOT edited |
| New pure function | (none) | LG-01e adds zero pure functions |
| Test file | `matches/tests/test_lg01e_next_season.py` | NEW |
| Test file | `matches/tests/test_league_dashboard.py` | EXTENDED — append `TestLg01eDashboardWiring` only |
| Test file | `matches/tests/test_season_dashboard_view.py` | EXTENDED — append `TestLg01eDashboardWiring` only |
| Test class | `TestNextSeasonRouting` | URL reverse, 405 on GET/PUT/DELETE, 404 on missing League |
| Test class | `TestNextSeasonHappyPath` | POST creates new draft Season with all locked fields + 302 redirect |
| Test class | `TestNextSeasonNameFormat` | `f"Season {n+1}"` derivation across n=1, 2, 5 |
| Test class | `TestNextSeasonStartDate` | `date(prev.start_date.year + 1, 1, 1)` across multiple years incl. year boundary |
| Test class | `TestNextSeasonScheduleFormatCarry` | New Season inherits `schedule_format` from previous |
| Test class | `TestNextSeasonTeamsCopiedFromSnapshot` | M2M populated from snapshot, not live `teams.all()`; missing-team id silently dropped |
| Test class | `TestNextSeasonActiveSeasonGuard` | Draft/active Season ⇒ 302 to that Season's dashboard; total count unchanged |
| Test class | `TestNextSeasonNoCompletedGuard` | Zero completed Seasons ⇒ 400 + body substring; count unchanged |
| Test class | `TestNextSeasonAtomicity` | Patch `Season.objects.create` mid-flow; assert rollback (no Season, no M2M) |
| Test class | `TestLg01eDashboardWiring` (league) | Asserts `<form>` rendered with `action`, csrf, submit text, `data-action-state` when `action_button_state == "start_next_season"` |
| Test class | `TestLg01eDashboardWiring` (season) | Same — asserts on the season dashboard; uses `season.league_id` for the action URL |

---

## 9. Scope summary (one sentence)

LG-01e adds `POST /leagues/<int:league_id>/next-season/` →
`matches.views.next_season` (decorated `@transaction.atomic`, 405 guard,
404 on missing League, 302-to-active-Season guard, 400 on no-completed
Season, creates `Season.objects.create(league=league, name=f"Season
{league.seasons.count() + 1}", start_date=date(latest_completed.start_date.year
+ 1, 1, 1), schedule_format=latest_completed.schedule_format,
state="draft")` + `new_season.teams.add(*Team.objects.filter(id__in=latest_completed.starting_team_ids_json
or []))` + `redirect("season_dashboard", season_id=new_season.id)`),
wired into the LG-01c-locked `action_button_state="start_next_season"`
slot via new `<form id="{league,season}-dashboard-next-season-form"
method="post" action="{% url 'next_season' league_id=… %}">` markup on
both `templates/leagues/dashboard.html` and
`templates/seasons/dashboard.html`, with three new test classes in
`matches/tests/test_lg01e_next_season.py` plus two `TestLg01eDashboardWiring`
extensions on the LG-01c dashboard test files — no model change, no
migration, no ADR, no CONTEXT.md edit, no new pure module, no simulator
touch, no RNG, no JS, no Celery, no `messages.*`, no API endpoint, no
new dependency, no admin change, no edit to any LG-01 / LG-01a / LG-01b
/ LG-01c / LG-01d pure module or template beyond the two dashboard
files.
