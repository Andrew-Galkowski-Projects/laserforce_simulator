# LG-01d — Play Season (Start Season + Play One Week + Play Two Months + Play Until End) · Seam Contract

Locked artifact for the three parallel agents (code / tests / docs). LG-01d
ships the **Play dropdown** that turns the LG-01c read-only dashboards
into a write surface: a Start Season POST (`draft → active`), a sync
Play One Week POST (one matchday's worth of Rounds inside a single
`@transaction.atomic`), two async Celery-backed POSTs (Play Two Months /
Play Until End of Season) sharing one task body parameterised by
`max_matchdays`, a shared polling endpoint, and symmetric dropdown UI on
the Season + League dashboards. **No model change, no migration, no new
dependency, no `django.contrib.messages`, no `master_seed` UI, no
mid-job cancel, no top-nav refactor, no URL nesting, no per-Season arena
map** — those are deferred to LG-01h / LG-01i / LG-01j and the
post-LG-02 tournaments scope.

This contract mirrors the structure of
[`.claude/worktrees/lg-01c-seam-contract.md`](lg-01c-seam-contract.md)
and extends the LG-01 foundation + the LG-01c dashboard surface +
the API-03 Celery executor pinned in
[`.claude/worktrees/api-03-seam-contract.md`](api-03-seam-contract.md).

> **Vocabulary note.** The originally-planned "Play Next Round" UI
> surface is **dropped**. The smallest user-facing play unit is **Play
> One Week** (a **Matchday**, which holds multiple `ScheduleFixture`
> rows — one per pairing). The scheduling unit at the engine level is
> still the **Round** (the `simulate_scheduled_round` call). The
> matchday-aware mapping is `Season.start_date + (matchday - 1) * 7
> days` from LG-01.

---

## 0. Overview

LG-01d adds **5 POST endpoints + 1 GET status endpoint** wired to the
LG-01c Season + League dashboards' previously-disabled action button:

- `POST /seasons/<int:season_id>/start-season/` — sync, calls
  `Season.start_season()` (existing `@transaction.atomic`); redirects
  on success / re-renders dashboard with `play_error` on failure.
  Idempotent on the "already active" double-submit race.
- `POST /seasons/<int:season_id>/play-week/` — sync, single
  `@transaction.atomic` wrapping every Round in the next-unplayed
  matchday; redirects on success.
- `POST /seasons/<int:season_id>/play-two-months/` — async, returns
  202 + `{"job_id", "season_id"}`; enqueues the shared
  `play_season_task` with `max_matchdays=8`.
- `POST /seasons/<int:season_id>/play-until-end/` — async, same shape,
  `max_matchdays=None`.
- `GET /seasons/<int:season_id>/play-status/<str:job_id>/?season_id=…`
  — single polling endpoint serving **both** async tasks.

The LG-01c dashboards' `action_button_state="play_next"` (now
"active-Season Play dropdown") slot is replaced by a Bootstrap dropdown
trigger that opens three submit forms (One Week / Two Months / Until End
of Season). The `action_button_state="start_season"` slot becomes a
single-button Start Season form. The other two states
(`"start_next_season"`, `"none"`) keep the LG-01c `<button disabled>`
placeholder. Inline polling JS lives on both dashboard templates; no
new JS file, no htmx, no framework.

One NEW ADR (`docs/adr/0016-play-season-job-execution-model.md`); two
CONTEXT.md edits (extend **Job**, add **Matchday**); three PLAN.md
entries appended (LG-01h / LG-01i / LG-01j); one PLAN.md task marked
`- completed`. The LG-01c `season_dashboard.py` pure module gains
**two** new functions (`find_next_matchday`, `select_play_fixtures`) on
the same frozen import allowlist. The `matches/tasks.py` API-03 module
gains **one** new Celery task (`play_season_task`). `matches/views.py`
gains **five** new view functions + **one** new flat `_`-prefixed helper.
The LG-01c `season_dashboard` / `league_dashboard` views gain new
`play_error` and `play_job_id` context keys.

---

## 1. URLs

Five new path entries in `matches/season_urls.py`. **No new URL include
file.** The LG-01 / LG-01c-mounted file is already routed by
`laserforce_simulator/urls.py`.

### 1a. `matches/season_urls.py` — final `urlpatterns` order

All 5 new patterns insert **BEFORE** the LG-01 `path("<int:season_id>/
standings/", …)` and `path("<int:season_id>/schedule/", …)` entries.
The new POST routes carry longer / more-specific path segments than
the LG-01 standings/schedule entries, but Django URL resolution is
first-match so the contract pins the ordering rather than relying on
"longer is more specific".

Final order (LG-01c top + LG-01d additions + LG-01 tail):

1. `path("<int:season_id>/", views.season_dashboard, name="season_dashboard")` *(LG-01c)*
2. `path("<int:season_id>/start-season/", views.start_season, name="start_season")` *(NEW — LG-01d)*
3. `path("<int:season_id>/play-week/", views.play_week, name="play_week")` *(NEW — LG-01d)*
4. `path("<int:season_id>/play-two-months/", views.play_two_months, name="play_two_months")` *(NEW — LG-01d)*
5. `path("<int:season_id>/play-until-end/", views.play_until_end, name="play_until_end")` *(NEW — LG-01d)*
6. `path("<int:season_id>/play-status/<str:job_id>/", views.play_status, name="play_status")` *(NEW — LG-01d)*
7. `path("<int:season_id>/standings/", views.season_standings, name="season_standings")` *(LG-01)*
8. `path("<int:season_id>/schedule/", views.season_schedule, name="season_schedule")` *(LG-01)*

### 1b. URL name + HTTP method matrix

| URL name | Path | HTTP | Failure modes |
|---|---|---|---|
| `start_season` | `/seasons/<id>/start-season/` | POST only | 405 on non-POST; 400 + `play_error` on most `ValidationError`s; idempotent 302 on the "already active" `ValidationError` substring match. |
| `play_week` | `/seasons/<id>/play-week/` | POST only | 405 on non-POST; 400 + `play_error` on Season state ≠ `"active"`; 302 (idempotent) when the to-play list is empty. |
| `play_two_months` | `/seasons/<id>/play-two-months/` | POST only | 405 on non-POST; 400 + `play_error` on Season state ≠ `"active"`; otherwise 202 + JSON. |
| `play_until_end` | `/seasons/<id>/play-until-end/` | POST only | Same as `play_two_months`. |
| `play_status` | `/seasons/<id>/play-status/<job_id>/` | GET only | 405 on non-GET; 200 + JSON otherwise (no 404 path — unknown `job_id` falls back to PENDING ⇒ `"running"`, the API-03 expiry-asymmetry precedent). |

All 5 views use the locked `HttpResponseNotAllowed([...])` guard pattern
(mirrors `movement_heatmap` / `export_round_report` / LG-01c
`season_dashboard`) as the **first** line of the body, BEFORE any ORM
hit. No `@require_POST` / `@require_GET` decorator.

---

## 2. View signatures

All five new views + one new helper are **appended** to
`matches/views.py`. None is decorated with `@transaction.atomic` at the
view-decorator level (the two sync views use an inline `with
transaction.atomic():` block; the two async views enqueue Celery and
each Round inside the task is atomic via
`simulate_scheduled_round`'s existing decorator; the GET status view
is read-only).

### 2a. `start_season`

```python
def start_season(request: HttpRequest, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for the ``draft → active`` transition.

    POST only. Idempotent on the "already active" double-submit race —
    a ``ValidationError`` whose message contains the substring
    ``"non-completed"`` (the LG-01 ``Season.clean()`` error wording)
    is swallowed and the user is redirected to the dashboard. Any
    other ``ValidationError`` (e.g. the ``"at least 2 enrolled
    teams"`` guard from ``Season.start_season()``) re-renders the
    Season dashboard with ``play_error`` populated and HTTP 400.
    """
```

- **405:** `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`.
- **404:** `season = get_object_or_404(Season, pk=season_id)`.
- **Body:** `try: season.start_season(); return redirect("season_dashboard", season_id=season.id)`.
- **Except `ValidationError as exc`:** if any of the exc messages
  contains the substring `"non-completed"` (the literal LG-01
  `Season.clean()` raises `"Only one non-completed Season is allowed
  per League."` — substring `"non-completed"` is the locked match
  token) OR if `season.state == "active"` post-refresh, swallow the
  error and `return redirect("season_dashboard", season_id=season.id)`
  (idempotent). Else re-render `templates/seasons/dashboard.html` with
  `play_error = str(exc)` and HTTP 400. The re-render reuses the LG-01c
  `season_dashboard` body assembly (call `_build_dashboard_context` +
  add the season-only keys) so the dashboard is fully populated when
  the error renders.

### 2b. `play_week`

```python
def play_week(request: HttpRequest, season_id: int) -> HttpResponse:
    """LG-01d — POST entry point for Play One Week (one matchday).

    Sync, single ``@transaction.atomic`` wrapping every Round in the
    next unplayed matchday. On a Season already finished (no unplayed
    fixtures) ⇒ idempotent 302 redirect (no-op). On a non-``active``
    Season ⇒ 400 + ``play_error``.
    """
```

- **405:** `HttpResponseNotAllowed(["POST"])`.
- **404:** `get_object_or_404(Season, pk=season_id)`.
- **State guard:** if `season.state != "active"` ⇒ re-render dashboard
  with `play_error = f"Season must be active to play; got state={season.state!r}"`
  and HTTP 400.
- **Body (inside `with transaction.atomic():`):**
  1. `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)`
     (defensive `or []` — degenerate snapshot ⇒ `fixtures = []` ⇒
     to-play list is `[]` ⇒ idempotent redirect).
  2. `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=season).select_related("match")}`
     (mirrors LG-01c view-side materialisation byte-for-byte).
  3. `to_play = select_play_fixtures(fixtures, played_keys, max_matchdays=1)`
     (the new pure-module function — see §4).
  4. `if not to_play: return redirect("season_dashboard", season_id=season.id)`
     (idempotent no-op — Season already complete or this matchday
     is somehow empty).
  5. Loop: `for fixture in to_play: team_a = Team.objects.get(id=fixture.team_a_id); team_b = Team.objects.get(id=fixture.team_b_id); BatchSimulator().simulate_scheduled_round(season, team_a, team_b, fixture.round_number)`
     (no `arena_map` kwarg — deferred to LG-01j; `simulate_scheduled_round`
     defaults `arena_map=None`).
  6. `return redirect("season_dashboard", season_id=season.id)`.
- **Try-except wrapping the `with transaction.atomic():` block:** a
  `ValidationError` or `ValueError` raised inside the block rolls
  back the entire matchday and re-renders the dashboard with
  `play_error = str(exc)` + HTTP 400. **No partial-matchday commit.**

### 2c. `play_two_months`

```python
def play_two_months(request: HttpRequest, season_id: int) -> JsonResponse:
    """LG-01d — POST entry point for the Play Two Months async run.

    Validates the Season is ``active`` (else 400 + ``play_error``), then
    enqueues ``play_season_task.delay(season_id, max_matchdays=8)`` and
    returns ``JsonResponse({"job_id": result.id, "season_id":
    season_id}, status=202)``.
    """
```

- **405:** `HttpResponseNotAllowed(["POST"])`.
- **404:** `get_object_or_404(Season, pk=season_id)`.
- **State guard:** non-active ⇒ re-render dashboard with `play_error`
  + HTTP 400 (matches `play_week`).
- **Body:** `result = play_season_task.delay(season_id, max_matchdays=8); return JsonResponse({"job_id": result.id, "season_id": season_id}, status=202)`.

### 2d. `play_until_end`

Identical to `play_two_months` except `max_matchdays=None`. Docstring:

```python
def play_until_end(request: HttpRequest, season_id: int) -> JsonResponse:
    """LG-01d — POST entry point for the Play Until End of Season async run."""
```

### 2e. `play_status`

```python
def play_status(
    request: HttpRequest, season_id: int, job_id: str
) -> JsonResponse:
    """LG-01d — Shared polling endpoint for both async play tasks.

    GET only. Returns the locked 5-key polling JSON shape (see §3).
    Carries ``season_id`` from BOTH the URL kwarg and the
    ``?season_id=`` query param — the URL kwarg is authoritative; the
    query param is the API-03 carry pattern for stateless polling
    URLs.
    """
```

- **405:** `HttpResponseNotAllowed(["GET"])`.
- **No 404:** `season_id` is taken from the URL kwarg without a
  `get_object_or_404` (the polling JSON never reads any Season-row
  field — the URL kwarg is echoed straight back so the JS client can
  match poll responses to the in-page Season context; an unknown
  `job_id` resolves to Celery `PENDING` ⇒ `"running"`, the API-03
  expiry-asymmetry semantics, never 404).
- **Body:** `async_result = AsyncResult(job_id); return JsonResponse(_build_play_status_response(async_result, season_id=season_id))`.

### 2f. `_build_play_status_response`

Module-level flat `_`-prefixed helper (RV-01 / HX-03 / LG-01c
`_build_dashboard_context` precedent), private to `matches/views.py`.

```python
def _build_play_status_response(
    async_result: AsyncResult,
    *,
    season_id: int,
) -> dict:
    """Assemble the locked 5-key polling JSON for a Play Season job.

    Returns ``{"status", "completed", "total", "error", "season_id"}``
    — see §3 for the per-key sourcing rules.
    """
```

Reuses the existing API-03 `_celery_state_to_job_status` helper
**verbatim** (no rename, no fork; the LG-01d "Play Season" job uses
the same `running / complete / error` vocabulary as the batch /
save flows).

---

## 3. Polling JSON shape

`_build_play_status_response` returns a dict with **exactly these 5
keys**, in this order:

```python
{
    "status":    str,            # "running" | "complete" | "error"
    "completed": int,            # Round-level (NOT matchday-level)
    "total":     int,            # Round-level
    "error":     str | None,     # str(async_result.info) on FAILURE/REVOKED
    "season_id": int,            # echoed from the URL kwarg, authoritative
}
```

**Per-key sourcing** (locked):

- **`status`** — `_celery_state_to_job_status(async_result.state)` —
  the API-03 mapping verbatim:
  - `SUCCESS` ⇒ `"complete"`
  - `FAILURE` / `REVOKED` ⇒ `"error"`
  - `PENDING` / `STARTED` / `PROGRESS` / `RETRY` / unknown ⇒
    `"running"` (the defensive fallback that keeps the UI from
    breaking on unknown Celery states + handles expired `job_id`s).
- **`completed`** / **`total`** — Round-level counts (NOT matchday-
  level). On `PROGRESS`: read `async_result.info["completed"]` /
  `["total"]` (the task `update_state` meta dict — see §5). On
  `SUCCESS`: read from `async_result.result["completed"]` /
  `["total"]` (the task's final return dict). On error / unknown:
  `0` / `0`.
- **`error`** — `str(async_result.info)` on `FAILURE` / `REVOKED`
  (API-03 precedent — Celery exposes the exception instance via
  `.info`; `str(exc)` matches the API-03 batch/save error contract).
  `None` otherwise.
- **`season_id`** — `season_id` parameter from the URL kwarg, echoed
  back; the `?season_id=` query param on the polling URL is the
  client-side carry pattern (the dashboard JS appends it to every
  status URL) but the URL kwarg is **authoritative** if the two
  disagree.

The polling JS reads `data.status === "complete"` (mirrors the
API-03 save-flow `"complete"` rename — never `"done"`) and reloads
the dashboard on transition to `"complete"`. `data.error` is
displayed inside the `season-dashboard-play-error` /
`league-dashboard-play-error` element on `"error"`.

### 3a. Status truth table (locked at the view boundary)

| Celery state | Mapped `status` | `completed` / `total` source | `error` |
|---|---|---|---|
| `PENDING` (incl. expired or unknown id) | `"running"` | `0` / `0` | `None` |
| `STARTED` | `"running"` | `0` / `0` | `None` |
| `PROGRESS` | `"running"` | `async_result.info["completed"]` / `["total"]` | `None` |
| `RETRY` | `"running"` | `0` / `0` | `None` |
| `SUCCESS` | `"complete"` | `async_result.result["completed"]` / `["total"]` | `None` |
| `FAILURE` | `"error"` | `0` / `0` | `str(async_result.info)` |
| `REVOKED` | `"error"` | `0` / `0` | `str(async_result.info)` |
| unknown | `"running"` | `0` / `0` | `None` |

### 3b. POST response shapes (async)

```python
# POST /seasons/<id>/play-two-months/  (status 202)
# POST /seasons/<id>/play-until-end/   (status 202)
{
    "job_id":    str,      # the Celery AsyncResult.id
    "season_id": int,      # echoed from the URL kwarg
}
```

The sync endpoints (`start_season`, `play_week`) return an HTTP 302
redirect to `season_dashboard` on success — they never return JSON.

---

## 4. Pure module extension

**MODIFIED file:** `matches/season_dashboard.py` (LG-01c file —
**frozen import allowlist unchanged**: `dataclasses`, `typing`,
`collections` only; **NO** Django, NO ORM, NO `random`, NO
`datetime`, NO I/O, NO logging; **NO** `matches.schedule_generator`
import — `ScheduleFixture` instances cross the seam as duck-typed
input). The defensive `TestNoDjangoImportsLeaked` subprocess check
in `matches/tests/test_season_dashboard.py` continues to pin the
allowlist verbatim and **must keep passing** after the two new
function additions.

### 4a. `find_next_matchday`

```python
def find_next_matchday(
    fixtures: "list",
    played_keys: "set",
) -> "Optional[int]":
    """Return the matchday number of the first unplayed fixture.

    Walks ``fixtures`` in canonical ``generate_schedule(...)`` iteration
    order (already sorted by ``(matchday, team_a_id)``) and returns
    the ``matchday`` of the first fixture whose Side-agnostic
    ``(frozenset({team_a_id, team_b_id}), round_number)`` key is NOT
    in ``played_keys``.

    Args:
        fixtures: list of ``ScheduleFixture`` in iteration order.
        played_keys: set of ``(frozenset({team_red_id, team_blue_id}),
            round_number)`` tuples for every persisted ``GameRound`` in
            the Season.

    Returns:
        The matchday number (1-based, ``int``) of the first unplayed
        fixture, or ``None`` if every fixture has been played (or the
        input is empty).
    """
```

**Edges:**

- `fixtures == []` ⇒ `None`.
- All played ⇒ `None`.
- Partial: returns the smallest `matchday` whose `(frozenset,
  round_number)` key is missing from `played_keys`.
- Side-agnostic match: a played key `(frozenset({1, 2}), 1)` matches
  a fixture with `team_a_id=1, team_b_id=2, round_number=1`.

### 4b. `select_play_fixtures`

```python
def select_play_fixtures(
    fixtures: "list",
    played_keys: "set",
    max_matchdays: "Optional[int]",
) -> "list":
    """Return the unplayed fixtures spanning the next ``max_matchdays``
    distinct unplayed matchdays starting at ``find_next_matchday``.

    Args:
        fixtures: list of ``ScheduleFixture`` in canonical iteration
            order.
        played_keys: as in ``find_next_matchday``.
        max_matchdays: if an ``int``, return only fixtures whose
            ``matchday`` is among the next ``max_matchdays`` distinct
            unplayed matchdays from the canonical sweep. If ``None``,
            return ALL unplayed fixtures (Play Until End of Season).

    Returns:
        The unplayed fixtures in canonical iteration order. Empty
        list when ``fixtures`` is empty or every fixture has been
        played.
    """
```

**Algorithm (locked):**

1. If `fixtures == []` ⇒ return `[]`.
2. Walk `fixtures` once in iteration order. For each fixture, compute
   its `key = (frozenset({fixture.team_a_id, fixture.team_b_id}),
   fixture.round_number)`. Skip when `key in played_keys`.
3. For each unplayed fixture, record its `matchday`. Maintain an
   ordered list of distinct unplayed matchdays seen so far (Python
   3.7+ dict-insertion-order or a manual `if md not in seen: seen.append(md)`).
4. If `max_matchdays is None`: collect every unplayed fixture
   regardless of matchday.
5. Else (`max_matchdays` is an `int`): collect every unplayed fixture
   whose `matchday` is among the **first `max_matchdays` distinct
   unplayed matchdays** seen during the sweep. A second sweep is
   not needed — once the distinct-matchday set reaches
   `max_matchdays`, stop accepting fixtures whose `matchday` is not
   already in the set.
6. Return the collected fixtures in iteration order (preserves
   `generate_schedule` order). Side-agnostic key matching, as in
   `find_next_matchday`.

**Edges:**

- `max_matchdays == 1` ⇒ exactly that one matchday's unplayed
  fixtures (Play One Week). Could be 0 fixtures (no unplayed
  matchdays).
- `max_matchdays == 8` ⇒ up to 8 distinct unplayed matchdays' worth
  of fixtures (Play Two Months). Stops short on a Season with
  fewer than 8 unplayed matchdays.
- `max_matchdays is None` ⇒ ALL unplayed fixtures (Play Until End).
- All-played input ⇒ `[]`.
- Boundary at last matchday: if only matchday `K` remains and
  `max_matchdays >= 1`, returns exactly matchday `K`'s unplayed
  fixtures.
- Empty `fixtures` ⇒ `[]`.

### 4c. `played_keys` shape (re-pinned for LG-01d)

Identical to the LG-01c `find_next_fixture` / `round_progress` shape
— set of `(frozenset({team_red_id, team_blue_id}), round_number)`
tuples — built **view-side** (or task-side) by:

```python
played_keys = {
    (
        frozenset({gr.match.team_red_id, gr.match.team_blue_id}),
        gr.round_number,
    )
    for gr in GameRound.objects.filter(match__season=season).select_related("match")
}
```

This is the Side-agnostic precedent from LG-01 `find_next_fixture` —
a team that played red in round 1 and blue in round 2 still matches
the corresponding fixture row.

---

## 5. Celery task

**MODIFIED file:** `matches/tasks.py` — one new `@shared_task` appended
after the existing API-03 `save_games_task`. The existing
`_resolve_arena_map` helper is **not** consumed (the LG-01d task does
not accept an `arena_map_id` kwarg — see §5b).

### 5a. `play_season_task`

```python
@shared_task(bind=True, name="matches.play_season")
def play_season_task(
    self,
    season_id: int,
    max_matchdays: int | None = None,
) -> dict:
    """LG-01d — Drive the Play Two Months / Play Until End async run.

    Loads the Season, materialises fixtures via ``generate_schedule(...)``,
    builds ``played_keys`` from persisted ``GameRound``s, calls the pure
    ``select_play_fixtures(fixtures, played_keys, max_matchdays)`` to
    decide which Rounds to play, then loops calling
    ``BatchSimulator().simulate_scheduled_round(...)`` per fixture with
    a ``self.update_state(state="PROGRESS", meta={"completed": k+1,
    "total": n})`` after each Round.

    Per-Round commits — the task body has NO outer ``@transaction.atomic``
    wrapper. ``simulate_scheduled_round`` is already
    ``@transaction.atomic`` so each Round is its own transactional
    unit. A mid-loop exception propagates (Celery records ``FAILURE``);
    every completed Round survives because it was its own atomic
    commit. **This is a load-bearing decision — see §10 ADR plan.**

    Returns:
        ``{"completed": n, "total": n}`` on success (where ``n`` is
        ``len(to_play)``). The shape matches what ``_build_play_status_
        response`` reads on ``SUCCESS``.
    """
```

### 5b. Task body steps (locked)

1. `import django.db` (the API-03 `close_old_connections` pattern).
2. `try:` block opens.
3. `from matches.models import Season; from matches.simulation import BatchSimulator; from matches.schedule_generator import generate_schedule; from matches.season_dashboard import select_play_fixtures; from teams.models import Team` (deferred imports keep `matches/tasks.py`'s module-level import surface lean — mirrors the existing `_resolve_arena_map` `from core.models import ArenaMap` deferred-import precedent).
4. `season = Season.objects.get(id=season_id)` (no `get_or_404` — a
   stale id raises `Season.DoesNotExist`, the task FAILs, the
   polling view reports `"error"` with the exception string).
5. `fixtures = generate_schedule(season.starting_team_ids_json or [], season.schedule_format)`
   (defensive `or []`).
6. `played_keys = {(frozenset({gr.match.team_red_id, gr.match.team_blue_id}), gr.round_number) for gr in GameRound.objects.filter(match__season=season).select_related("match")}`.
7. `to_play = select_play_fixtures(fixtures, played_keys, max_matchdays)`.
8. `n = len(to_play)`.
9. **Empty to-play list path:** if `n == 0`, the task returns
   `{"completed": 0, "total": 0}` immediately (no PROGRESS emission,
   the polling view sees `SUCCESS` ⇒ `"complete"` ⇒ dashboard
   reloads, idempotent).
10. Loop with index: `for k, fixture in enumerate(to_play):`
    - `team_a = Team.objects.get(id=fixture.team_a_id)`
    - `team_b = Team.objects.get(id=fixture.team_b_id)`
    - `BatchSimulator().simulate_scheduled_round(season, team_a, team_b, fixture.round_number)`
      (no `arena_map` kwarg — deferred to LG-01j).
    - `self.update_state(state="PROGRESS", meta={"completed": k + 1, "total": n})`
      (emit AFTER each Round so `completed` is the count of
      Rounds already committed).
11. `return {"completed": n, "total": n}`.
12. `finally: django.db.close_old_connections()` (the API-03 hygiene
    pattern — closes the long-lived per-worker DB connections that
    a multi-Round loop tends to accumulate).

**No outer `@transaction.atomic` on the task body.** Each Round's
atomic commit is the existing `simulate_scheduled_round`
`@transaction.atomic` decorator. A mid-loop exception (e.g.
roster validation failure on the 5th Round of 12) propagates;
Celery records `FAILURE`; the first 4 Rounds survive because each
was its own atomic commit; the Season stays `active` (the LG-01
`complete_if_finished` auto-transition only fires when the final
fixture lands); the user re-clicking Play resumes from where the
last successful Round left off.

### 5c. `arena_map_id` deliberately omitted

The task signature **does not** accept an `arena_map_id` kwarg.
`simulate_scheduled_round` is called without the `arena_map` kwarg
(its default is `None` ⇒ the 3-zone fallback path). Per-Season
arena map support is deferred to **LG-01j** (see §10).

### 5d. `team_a` / `team_b` resolution

The `select_play_fixtures` output preserves the canonical id order
from `generate_schedule` (`team_a_id = min(pair), team_b_id =
max(pair)`). The task resolves `Team.objects.get(id=fixture.team_a_id)`
/ `team_b_id` and passes them straight to
`simulate_scheduled_round`, which performs **Side-agnostic Match
find-or-create** (LG-01) — so the canonical id-order at the seam
is independent of which Team physically played red in any
previous round. The simulator's per-Match colour swap (round 2
args reversed) is unchanged.

---

## 6. View context keys

### 6a. LG-01c `season_dashboard` context — NEW keys

```python
{
    # ... all LG-01c keys verbatim ...
    "play_error":  str | None,      # NEW — populated on sync POST failure
    "play_job_id": str | None,      # NEW — always None at LG-01d (async POSTs
                                    #        return 202 JSON; the in-page JS
                                    #        carries the job_id, the dashboard
                                    #        never re-renders with a job_id in
                                    #        context). Reserved key for future
                                    #        extension; tests assert presence
                                    #        with value None.
}
```

The LG-01c `season_dashboard` view is **extended** with these two new
context keys. `play_error` is `None` on the normal GET render; it is
the `str(exc)` of the caught `ValidationError` / `ValueError` only
when one of the sync POST views (`start_season`, `play_week`)
re-renders the dashboard. `play_job_id` is always `None` at LG-01d
merge time (the async flow does not redirect — the POST returns 202
JSON and the JS handles polling in-page).

### 6b. LG-01c `league_dashboard` context — NEW keys

Same two new keys: `play_error`, `play_job_id` (both default `None`
on the GET render; `play_error` populated when a sync POST view
re-renders the league dashboard's symmetric Play surface).

> **Note on symmetric placement.** The grilling session locked the
> Play dropdown as symmetric across the Season + League dashboards
> (the League dashboard surfaces the active Season's Play actions in
> its own slot). The sync error re-render path on the League
> dashboard uses the LG-01c `league_dashboard` view's existing
> `displayed_season` resolution to find the correct active Season,
> then renders with `play_error` populated.

---

## 7. Templates + DOM ids

The LG-01c templates `templates/seasons/dashboard.html` and
`templates/leagues/dashboard.html` are **modified** — the existing
`<button disabled>` placeholder in the
`season-dashboard-action-button` / `league-dashboard-action-button`
slot is replaced by branched markup keyed off `action_button_state`.

### 7a. Action button branches (replaces the LG-01c `<button disabled>`)

| `action_button_state` | Rendered markup |
|---|---|
| `"start_season"` | A `<form method="post" action="{% url 'start_season' season_id=season.id %}">{% csrf_token %}<button type="submit">Start Season</button></form>` wrapped in a `<div id="season-dashboard-play-dropdown">` (and an identical `<div id="league-dashboard-play-dropdown">` on the League dashboard) for DOM-id parity with the active-state dropdown wrapper. |
| `"play_next"` | A Bootstrap-style dropdown: a trigger button labelled `Play` + a menu of three submit forms — One Week (`play_week`), Two Months (`play_two_months`), Until End of Season (`play_until_end`). Each form `method="post"`, contains `{% csrf_token %}`, posts to the respective URL via `{% url ... season_id=season.id %}`. Wrapped in a `<div id="season-dashboard-play-dropdown">` (League: `league-dashboard-play-dropdown`). |
| `"start_next_season"` | Unchanged from LG-01c — `<button disabled data-action-state="start_next_season">Start Next Season</button>` placeholder. |
| `"none"` | Unchanged from LG-01c — `<button disabled data-action-state="none">No Season</button>` placeholder. |

### 7b. Locked DOM ids (Season dashboard)

These are **NEW** ids on top of the LG-01c set; tests assert their
presence per the branch rules below.

| DOM id | Branch presence | Element |
|---|---|---|
| `season-dashboard-play-dropdown` | always when an action button renders (i.e. all 4 `action_button_state` values; encompasses both the Start Season single-button form and the active-state dropdown wrapper) | outer `<div>` wrapping the form(s) |
| `season-dashboard-play-start-season` | only when `action_button_state == "start_season"` | the Start Season `<form>` element (its `id` attribute) |
| `season-dashboard-play-one-week` | only when `action_button_state == "play_next"` | the One Week `<form>` (POSTs to `play_week`) |
| `season-dashboard-play-two-months` | only when `action_button_state == "play_next"` | the Two Months `<form>` (POSTs to `play_two_months`) |
| `season-dashboard-play-until-end` | only when `action_button_state == "play_next"` | the Until End of Season `<form>` (POSTs to `play_until_end`) |
| `season-dashboard-play-error` | only when `play_error` is truthy (post-sync-POST failure re-render) | an element rendering `{{ play_error }}`; tests assert the text content matches the rendered exception string |
| `season-dashboard-play-progress` | always (rendered but hidden by default — populated by JS during polling) | the progress-bar container (e.g. a `<div>` the JS writes a `<progress>` element into); the inline polling JS toggles its `hidden` attribute on submit |

### 7c. Locked DOM ids (League dashboard)

Identical structure under the `league-dashboard-*` prefix:
`league-dashboard-play-dropdown`, `league-dashboard-play-start-season`,
`league-dashboard-play-one-week`, `league-dashboard-play-two-months`,
`league-dashboard-play-until-end`, `league-dashboard-play-error`,
`league-dashboard-play-progress`. Same branch presence rules — the
League dashboard's symmetric Play surface mirrors the Season dashboard
exactly when `displayed_season` is active.

> **Note.** The LG-01c-locked `season-dashboard-action-button` /
> `league-dashboard-action-button` ids continue to be present on the
> outer wrapper container in all 4 states for backwards compatibility
> with the LG-01c tests. The LG-01d additions stack underneath that id
> rather than replacing it.

### 7d. Inline polling JS

Both dashboard templates carry an inline `<script>` block (no
external JS file, no framework, no htmx). The script:

1. Selects the One Week / Two Months / Until End submit forms by
   id.
2. **Submit interception** — the One Week form submits normally
   (sync — server-side redirect). The Two Months and Until End
   forms intercept submit via `addEventListener("submit", e =>
   { e.preventDefault(); fetch(form.action, {method: "POST",
   body: new FormData(form), headers: {"X-CSRFToken": …}}).then(r
   => r.json()).then(json => startPolling(json.job_id,
   json.season_id)); })`.
3. **Disable dropdown while polling** — `startPolling` adds a
   `disabled` attribute to the dropdown trigger and each submit
   button so the user can't double-fire.
4. **Poll every 1000 ms** — `setInterval(() => fetch("{% url
   'play_status' season_id=season.id job_id='JOB' %}?season_id={{
   season.id }}".replace("JOB", jobId)).then(r =>
   r.json()).then(data => …), 1000)`. The `{% url %}` template tag
   takes a placeholder `'JOB'` that the JS `.replace("JOB",
   jobId)` substitutes — this keeps the URL construction
   server-side (Django reverse) while staying static-template-
   friendly.
5. **Render progress bar** — on each poll, update the
   `season-dashboard-play-progress` / `league-dashboard-play-
   progress` element with a `<progress value="{{ data.completed
   }}" max="{{ data.total }}">…</progress>` element + text label.
6. **On `data.status === "complete"`** — `clearInterval`,
   `window.location.reload()` (the dashboard re-renders with the
   new Rounds in standings / leaders / next-fixture).
7. **On `data.status === "error"`** — `clearInterval`, render
   `data.error` into the `season-dashboard-play-error` /
   `league-dashboard-play-error` element, re-enable the dropdown.

The polling JS lives **inline** in each dashboard template (no
separate `static/` JS file). It is duplicated across the two
templates (no Django template `{% include %}` for JS); the
duplication is locked — keeping the JS inline and per-template is
the LG-01d simplicity choice over factoring out a shared partial.

---

## 8. ADR plan

**NEW file:** `docs/adr/0016-play-season-job-execution-model.md`. Mirror
the [ADR-0013](../../docs/adr/0013-async-batch-execution-via-celery-redis.md)
shape (Context / Decision / Consequences / Rejected Alternatives).

### 8a. Context section

- LG-01d ships 4 Play action variants (Start Season, Play One Week,
  Play Two Months, Play Until End of Season) plus their polling
  endpoint.
- The 2 async actions (Play Two Months, Play Until End) execute via
  Celery, mirroring the API-03 batch/save executor swap.
- The two async actions share a near-identical body — load Season,
  materialise fixtures, compute played keys, select to-play fixtures,
  loop calling `simulate_scheduled_round`. The only difference is
  how many matchdays they span.
- The sync actions (Start Season, Play One Week) are short enough
  to run inline in the request thread — Start Season is a single
  state flip; Play One Week is at most N/2 Rounds for a Season of
  N teams (the matchday width), bounded by the 200ms-per-Round
  BatchSimulator cost.

### 8b. Decision section

1. **Per-Round atomic commits inside the Celery task.** The task
   body has NO outer `@transaction.atomic` wrapper.
   `simulate_scheduled_round` is already `@transaction.atomic` so
   each Round is its own transactional unit. A mid-loop exception
   propagates (Celery records `FAILURE`); every completed Round
   survives because it was its own atomic commit.
2. **Resumable mid-job.** Because every completed Round is a
   permanent atomic commit, the user can re-click Play (One Week
   / Two Months / Until End) on a partially-completed Season and
   the next click resumes from where the previous run left off.
   The `select_play_fixtures` pure module re-reads `played_keys`
   on every invocation, so each click is fully idempotent at the
   Round level.
3. **One shared Celery task body parameterised by
   `max_matchdays: int | None`.** `max_matchdays=8` for Play Two
   Months; `max_matchdays=None` for Play Until End of Season.
   The two endpoints differ only by the kwarg value passed to
   `play_season_task.delay(season_id, max_matchdays=…)`.
4. **One shared polling endpoint** (`play_status`) for both async
   tasks. The polling JSON shape is the same for both; the URL
   names the `job_id` and `season_id` so the client can
   distinguish poll responses.
5. **Side-agnostic Match find-or-create at the simulator boundary.**
   The LG-01 `simulate_scheduled_round` handles all the per-Round
   concurrency / idempotency at the DB layer — the task does not
   need additional locking. A double-submit race (user clicks Play
   twice rapidly) wastes CPU on a redundant second call but does
   not corrupt state.

### 8c. Consequences section

- **Partial-completion is a feature, not a bug.** Mid-job failure
  leaves the Season `active` with N Rounds played and the
  remaining Rounds unplayed. The user re-clicks Play and the
  Season completes.
- **Polling clients tolerate `PENDING` indefinitely.** A `job_id`
  whose Celery result expired (1h TTL — `CELERY_RESULT_EXPIRES =
  3600` from API-03) resolves to `PENDING` ⇒ `"running"` — the
  client would poll forever. The dashboard JS sidesteps this by
  reloading the page on `"complete"`, so an expired `job_id`
  never appears in a normal flow (the task always finishes within
  a few seconds for realistic Season sizes).
- **One Celery task name** (`matches.play_season`) — visible in
  the Celery worker logs and `--queues` config.
- **No `simulate_match` change.** The existing 2-Round-atomic
  sandbox simulator entry point is untouched; LG-01d uses
  `simulate_scheduled_round` exclusively.

### 8d. Rejected alternatives section

- **Outer-atomic task body.** Rejected because a 56-Round Play
  Until End run would either (a) succeed all-or-nothing (a
  multi-minute transaction holding ORM locks the whole time, with
  rollback cost dominating the wall-clock on the very last
  Round's failure) or (b) succeed silently mid-rollback if the
  ORM session was reset — neither is operationally desirable.
- **Two separate task functions** (`play_two_months_task` +
  `play_until_end_task`). Rejected — the two bodies would be
  99% duplicate code; parameterising on `max_matchdays` is one
  line different.
- **ScheduleEntry-row-locking approach** (acquire a DB lock on
  the next unplayed fixture row, simulate, commit, repeat).
  Rejected — there is no `ScheduleEntry` table
  ([ADR-0015](../../docs/adr/0015-schedule-on-demand-no-fixture-rows.md)
  pinned the no-fixture-rows approach); fixtures are derived on
  demand from the pure schedule generator.
- **Mid-job cancel via `AsyncResult.revoke`.** Rejected for
  LG-01d — the UI complexity (a "Cancel run" button that
  gracefully terminates between Rounds) is deferred. Killing
  the worker mid-Round would leave a half-committed Round
  state; only between-Round cancellation is safe, and that needs
  cooperative-cancel polling inside the task body. Deferred to
  a future task.
- **Client-side dropdown disable as the only concurrency guard
  + server-side `Season.state` lock.** The contract goes with
  client-side disable only — overlapping submissions are
  accepted by the server and resolved at the DB layer
  (Side-agnostic Match find-or-create makes the second
  submission's first Round a duplicate of the first
  submission's last Round, which the find-or-create harmlessly
  picks up; subsequent Rounds proceed). Wasted CPU only on a
  double-submit race; no data corruption possible.

---

## 9. CONTEXT.md plan

**Two edits** (Docs agent, Step 7):

### 9a. Extend the **Job** entry

Current entry (post-API-03): "A unit of background work scheduled via
Celery on the Redis broker. Two kinds today: a **Batch run job** (run
N simulations) and a **Save-games job** (replay avg/outlier seeds and
persist their `GameRound`s)."

**LG-01d edit:** rewrite to "Three kinds today: a **Batch run job**, a
**Save-games job**, and a **Play Season job** (run all unplayed Rounds
in the next N matchdays of a Season — `N=8` for Two Months, `N=None`
for Until End of Season). All three share the same per-Job lifecycle
(`running` / `complete` / `error`) and the same expiry-asymmetry
(`PENDING` after the 1h TTL is indistinguishable from a never-
submitted Job id)."

### 9b. Add a new term: **Matchday**

Insert into the `### League and seasons` section (the LG-01 home for
`League` / `Season` / `Standings`):

"**Matchday** — One slot in the `generate_schedule(...)` mirror
pattern. The 1-based ``matchday`` field on `ScheduleFixture`. Multiple
fixtures share a matchday — one per pairing (round-1 matchdays
`1..N-1` carry the first leg; round-2 matchdays `N..2*(N-1)` carry
the mirrored leg). The scheduling unit is the **Round**, not the
matchday — a matchday holds multiple `ScheduleFixture` rows. The
**Play One Week** action plays every Round in a single Matchday;
**Play Two Months** plays the next 8 Matchdays; **Play Until End of
Season** plays every unplayed Round regardless of Matchday. The
calendar date of a Matchday is `Season.start_date + (matchday - 1) *
7 days`."

---

## 10. PLAN.md plan

**Docs agent (Step 7) edits:**

### 10a. Mark LG-01d completed

Find the existing `### LG-01d · Play Next Round (+ Play Week / Play
To End)` header (around line 713) and replace the entire prose body
(lines 714–727) with a **dense implementation note in the LG-01c
style** — one paragraph naming every artifact (5 view functions, 1
new pure function pair, 1 Celery task, 5 URL routes, 1 polling
endpoint, 2 template DOM-id sets, the ADR file, the CONTEXT.md
edits) and the locked decisions (per-Round atomic commits, single
shared task body, sync vs async split, client-side dropdown
disable, "non-completed" idempotent swallow). Keep the LG-01d task
header line. Add the `- completed` marker per the project
convention. Cross-reference this seam contract path.

### 10b. Append LG-01h / LG-01i / LG-01j

Under the `## LG — League mode` (or equivalent) section, append
three new task entries:

- **`### LG-01h · Global nav restructure`** — Move League / Season
  navigation into a sidebar inside the League. URL nesting
  (`/leagues/<id>/<app>/`). Hide sandbox features inside a
  League. Deferred from LG-01d's scope-narrow decision (no
  top-nav refactor at LG-01d).
- **`### LG-01i · Season "One Week (Live)" replay UI`** — A
  per-Round live replay surface invoked from the Play dropdown.
  Depends on **CAR-01** + the new Season-replay engine.
  Deferred from LG-01d.
- **`### LG-01j · Per-Season arena map options`** — Single map
  per Season; per-sub-league map sets; random map for tournaments.
  At LG-01d the Play actions pass `arena_map=None` to
  `simulate_scheduled_round` (3-zone fallback). LG-01j adds the
  per-Season map configuration UI + threads `arena_map` through
  the Celery task signature.

### 10c. Append a note under LG-02

Under the existing `### LG-02 · Tournaments` (or equivalent)
section, append: "Once tournaments land, relabel 'Until end of
season' → 'Until playoffs' (LG-01d ships the former label) and
extend the play loop through tournament completion."

---

## 11. Test boundary

Tests under `CELERY_TASK_ALWAYS_EAGER = True` — the API-03
`conftest.py` already sets `LF_CELERY_EAGER=1`; no new test
infrastructure required.

### 11a. `matches/tests/test_play_orchestrator.py` (NEW, pure-unit)

`SimpleTestCase` (no DB) for the two new pure functions in
`matches/season_dashboard.py`. **Frozen import allowlist** (test file
itself): mirrors LG-01c `test_season_dashboard.py` — `unittest` /
`django.test.SimpleTestCase`, `dataclasses`, `matches.season_dashboard`.
Locally-stubbed `@dataclass(frozen=True)` `_F(matchday, round_number,
team_a_id, team_b_id)` shape (no `matches.schedule_generator` import).

**Classes:**

- `TestFindNextMatchday`
  - `test_empty_fixtures_returns_none`
  - `test_no_played_returns_first_matchday`
  - `test_partial_played_returns_first_unplayed_matchday`
  - `test_all_played_returns_none`
  - `test_side_agnostic_frozenset_match` (a played key
    `(frozenset({1, 2}), 1)` matches a fixture with
    `team_a_id=1, team_b_id=2, round_number=1`)
  - `test_round_2_matchday_unplayed_while_round_1_played`
    (the next unplayed matchday may be a round-2 matchday)
- `TestSelectPlayFixtures`
  - `test_empty_fixtures_returns_empty_list`
  - `test_max_matchdays_1_returns_one_matchday_unplayed_only`
    (Play One Week happy path)
  - `test_max_matchdays_8_returns_up_to_8_distinct_matchdays`
    (Play Two Months happy path on a > 8-matchday Season)
  - `test_max_matchdays_8_caps_at_actual_remaining_when_fewer`
    (Season with only 3 unplayed matchdays + `max_matchdays=8`
    returns those 3)
  - `test_max_matchdays_none_returns_all_unplayed`
    (Play Until End happy path)
  - `test_boundary_at_last_matchday_returns_that_matchdays_unplayed`
  - `test_all_played_returns_empty_list`
  - `test_preserves_generate_schedule_iteration_order`
    (the output list's iteration order matches the input
    `fixtures` order)
  - `test_side_agnostic_key_matching` (a played key whose
    `frozenset` matches an unplayed fixture is treated as played)
  - `test_max_matchdays_1_with_zero_unplayed_matchdays_returns_empty`
    (defensive — all-played input + `max_matchdays=1` ⇒ `[]`)
  - `test_partial_matchday_played_still_returns_remaining_fixtures`
    (if 2 of 4 fixtures on matchday 3 are played and 2 are
    unplayed, `max_matchdays=1` starting from matchday 3 returns
    just those 2 remaining fixtures)

### 11b. `matches/tests/test_lg01d_tasks.py` (NEW, Celery EAGER)

Django `TestCase` exercising `play_season_task` under
`CELERY_TASK_ALWAYS_EAGER=True`. Tests construct minimal Seasons
with `start_season()` called (so the task can find `played_keys` /
fixtures); use small N (N=2 or N=3) so the full Season completes in
a few Rounds.

**Classes:**

- `TestPlaySeasonTaskHappyPath`
  - `test_play_until_end_loops_n_rounds_and_persists_game_round_rows`
  - `test_play_until_end_completes_season_via_complete_if_finished`
    (Season transitions to `completed`, `champion_team` stamped,
    `is_completed=True` on every Match)
  - `test_task_returns_completed_and_total_keys_matching_n`
  - `test_progress_update_state_emitted_per_round`
    (under EAGER, `update_state` is observable via the
    `AsyncResult.info` after each yield — pin the meta shape
    `{"completed": k+1, "total": n}`)
- `TestPlaySeasonTaskMaxMatchdays`
  - `test_max_matchdays_1_plays_exactly_one_matchday_worth_of_rounds`
  - `test_max_matchdays_8_caps_at_8_distinct_matchdays`
    (Season with > 8 matchdays + `max_matchdays=8` plays exactly
    8 matchdays' Rounds)
  - `test_max_matchdays_none_plays_every_unplayed_round`
    (full-Season Play Until End)
- `TestPlaySeasonTaskPerRoundCommit`
  - `test_mid_loop_exception_leaves_prior_rounds_committed`
    (patch `simulate_scheduled_round` to raise on the 3rd call;
    assert the first 2 Rounds persisted, the Season stays
    `active`, the task raises and the EAGER FAILURE is observable)
  - `test_re_clicking_play_resumes_from_where_failure_stopped`
    (after the patched failure, un-patch and re-invoke the task;
    assert the Season completes)
- `TestPlaySeasonTaskTeamLookup`
  - `test_canonical_id_order_from_select_play_fixtures_resolves_via_team_objects_get`
    (the task's `Team.objects.get(id=fixture.team_a_id)` /
    `team_b_id` call sequence is verified by patching
    `Team.objects.get` to record calls; assert the calls came
    in canonical id order from the pure module)

### 11c. `matches/tests/views_tests.py` — EXTENDED

Add 5 new test classes for the 5 new views.

- `TestLg01dStartSeason`
  - `test_post_flips_draft_to_active_and_redirects_to_dashboard`
  - `test_post_with_less_than_two_teams_returns_400_with_play_error`
  - `test_post_on_already_active_season_returns_idempotent_302_no_play_error`
    (the LG-01 `Season.clean()` would raise; the view catches
    and redirects)
  - `test_get_returns_405`
  - `test_post_on_missing_season_id_returns_404`
- `TestLg01dPlayWeek`
  - `test_post_plays_exactly_one_matchdays_worth_of_rounds`
    (N=3 Season; first POST plays matchday 1's 1 fixture, second
    POST plays matchday 2's 1 fixture, etc.)
  - `test_post_is_atomic_across_the_matchday`
    (patch `simulate_scheduled_round` to raise mid-loop; assert
    NO `GameRound` rows persisted)
  - `test_post_on_completed_season_returns_idempotent_302_no_play_error`
  - `test_post_on_non_active_season_returns_400_with_play_error`
  - `test_get_returns_405`
  - `test_post_on_missing_season_id_returns_404`
- `TestLg01dPlayTwoMonths`
  - `test_post_returns_202_with_job_id_and_season_id_keys`
  - `test_under_eager_task_runs_to_completion_and_persists_rounds`
    (the `delay()` call under EAGER runs synchronously; assert
    the Season completes)
  - `test_post_on_non_active_season_returns_400_with_play_error`
  - `test_get_returns_405`
- `TestLg01dPlayUntilEnd`
  - `test_post_returns_202_with_job_id_and_season_id_keys`
  - `test_under_eager_task_runs_full_season_to_completion`
    (a small Season completes; `state` flips to `"completed"`
    via `complete_if_finished`)
  - `test_post_on_non_active_season_returns_400_with_play_error`
  - `test_get_returns_405`
- `TestLg01dPlayStatus`
  - `test_progress_state_returns_running_with_completed_total`
    (mock an `AsyncResult` with `state="PROGRESS"` + `info={"completed": 5, "total": 12}`; assert response JSON
    has `{"status": "running", "completed": 5, "total": 12, "error": None, "season_id": <id>}`)
  - `test_success_state_returns_complete_with_completed_total`
    (mock `state="SUCCESS"` + `result={"completed": 12, "total": 12}`)
  - `test_failure_state_returns_error_with_str_info`
    (mock `state="FAILURE"` + `info=Exception("boom")`; assert
    `"status": "error"` + `"error": "boom"`)
  - `test_unknown_job_id_returns_running_with_zero_zero`
    (a never-submitted `job_id` resolves to `PENDING` ⇒
    `"running"` + 0/0)
  - `test_get_returns_200_on_any_job_id`
  - `test_post_returns_405`
  - `test_season_id_query_param_echoed_in_response_when_provided`
    (the URL kwarg is authoritative; the query param is the
    carry pattern but the URL kwarg wins on disagreement)

### 11d. Test file ownership

| File | Status |
|---|---|
| `matches/tests/test_play_orchestrator.py` | NEW (Tests agent) |
| `matches/tests/test_lg01d_tasks.py` | NEW (Tests agent) |
| `matches/tests/views_tests.py` | EXTENDED (Tests agent — 5 new classes appended; no existing class modified) |

The Tests agent does NOT extend any other test file at LG-01d. The
Code agent does NOT modify any test file. The Docs agent does NOT
touch production code or tests.

### 11e. Test rules

- Use small-N seeded simulations (N=2 / N=3) so the full Season
  completes in a few Rounds — the cost is genuine CPU.
- DO NOT assert on exact score totals from unseeded runs (the
  project rule from `CLAUDE.md`); assert on schema-level outcomes
  (row counts, state transitions, persisted FK shape).
- Use `random.seed(42)` or deterministic player stat injection
  where game-outcome assertions are needed.
- Tests must respect the existing `LF_CELERY_EAGER=1` conftest
  setting — no separate `pytest-celery` dependency.
- DO NOT `mock.patch("matches.tasks.play_season_task.delay")` in
  the happy-path EAGER tests — exercising the real task body
  under EAGER catches signature drift between view and task.

---

## 12. Out of scope (locked)

The following are **explicitly out of scope** for LG-01d and must
NOT be touched by any of the three parallel agents:

- **No model change, no migration** (`matches/models.py` /
  `matches/migrations/` read-only at LG-01d).
- **No `django.contrib.messages` / `messages.success(...)` /
  `messages.error(...)` usage.** Sync errors flow through the
  `play_error` context key.
- **No new dependency** (no `pip install`, no `requirements.txt`
  edit, no JS framework / htmx / Alpine).
- **No `master_seed` UI exposure.** The Celery task signature
  does not accept `master_seed`. (The future `play_season_task`
  may grow a `master_seed` kwarg for test pinning later, but
  LG-01d does not ship one.)
- **No mid-job cancel UI.** No `AsyncResult.revoke` call, no
  "Cancel run" button, no cooperative-cancel polling inside the
  task body.
- **No top-nav refactor / sidebar / URL nesting.** Deferred to
  LG-01h.
- **No per-Season arena map options.** The Celery task signature
  does not accept `arena_map_id`. `simulate_scheduled_round` is
  called with `arena_map=None` (3-zone fallback). Deferred to
  LG-01j.
- **No "One Week (Live)" replay surface.** Deferred to LG-01i
  (depends on CAR-01 + the new Season-replay engine).
- **No tournament-aware "Until playoffs" relabel.** The LG-01d
  label is "Until end of season"; the LG-02 task carries the
  relabel once tournaments land.
- **No `simulate_match` change.** The existing 2-Round-atomic
  sandbox simulator entry point is untouched.
- **No `simulate_scheduled_round` change.** The LG-01 per-Round
  entry point is consumed verbatim.
- **No edit to `matches/models.py` / `matches/standings.py` /
  `matches/schedule_generator.py` / `matches/simulation.py`** —
  all LG-01 / LG-01a / LG-01b / LG-01c modules read-only.
- **No edit to `LeagueAdmin` / `SeasonAdmin`** — LG-01 admin
  registrations unchanged.
- **No simulation mechanics change → no Score Calibration
  re-baseline obligation.** `simulate_scheduled_round` consumes
  no new mechanics; LG-01d is pure orchestration.
- **No SIM-07 / SIM-08 contract interaction.** Each Round draws
  its own fresh 63-bit seed via the existing
  `simulate_scheduled_round` (SIM-09); no master-seed chain at
  the Play Season level.
- **No API / DRF endpoint.** `/api/seasons/<id>/play-*/` REST
  surfaces are deferred.
- **No edit to `matches/api_views.py` / `matches/api_urls.py`.**
- **No edit to `templates/leagues/list.html` /
  `templates/leagues/create.html` /
  `templates/seasons/standings.html` /
  `templates/seasons/schedule.html`** — LG-01 / LG-01a / LG-01b
  templates unchanged. Only the LG-01c
  `templates/leagues/dashboard.html` +
  `templates/seasons/dashboard.html` are modified.
- **No JS file added to `static/`.** Polling JS is inline in the
  two dashboard templates; duplication across the two templates
  is locked.
- **No external CSS / Bootstrap upgrade.** The dropdown uses
  whatever Bootstrap version the existing templates already
  ship (the LG-01c contract is silent on the Bootstrap version;
  the Code agent picks compatible classes).
- **No edit to `CONTEXT.md` beyond the two pinned edits** (extend
  **Job**, add **Matchday**).
- **No new ADR beyond `0016-play-season-job-execution-model.md`.**
- **No `Season.matchday_cadence_days` field** (the 7-day cadence
  is hardcoded in the LG-01 `season_schedule` view via
  `timedelta(days=(matchday - 1) * 7)`; LG-01d preserves that
  verbatim, no new field).
- **No `Match.state` enum** (LG-01 has only `is_completed: bool`
  on `Match`; LG-01d preserves that).
- **No `League.owner_user`** (LG-01 / LG-01b ship the League
  without an owner FK; LG-01d preserves that).
- **No edit to the LG-01c pure module
  `matches/season_dashboard.py`'s 3 existing functions**
  (`compute_leaders`, `find_next_fixture`, `round_progress`).
  Only `find_next_matchday` and `select_play_fixtures` are
  appended.
- **No edit to the LG-01c view's body-context helper
  `_build_dashboard_context`** beyond the 2 new keys
  (`play_error`, `play_job_id`) wired into the context dict.
- **No edit to the LG-01c view function bodies** beyond the
  `play_error` / `play_job_id` context-key plumbing on the
  sync-error re-render path.

---

## 13. Locked Names (Recap)

Every public name LG-01d introduces or pins, in one place:

| Kind | Name | Notes |
|---|---|---|
| URL path | `/seasons/<int:season_id>/start-season/` | Inserted BEFORE LG-01 standings/schedule in `matches/season_urls.py` |
| URL name | `start_season` | Bare name. `reverse("start_season", season_id=season.id)` |
| URL path | `/seasons/<int:season_id>/play-week/` | Inserted with the other LG-01d entries |
| URL name | `play_week` | Bare name |
| URL path | `/seasons/<int:season_id>/play-two-months/` | Inserted with the other LG-01d entries |
| URL name | `play_two_months` | Bare name |
| URL path | `/seasons/<int:season_id>/play-until-end/` | Inserted with the other LG-01d entries |
| URL name | `play_until_end` | Bare name |
| URL path | `/seasons/<int:season_id>/play-status/<str:job_id>/` | Polling endpoint; shared between the two async tasks |
| URL name | `play_status` | Bare name. `reverse("play_status", season_id=season.id, job_id=job_id)` |
| View | `matches.views.start_season` | `(request, season_id) -> HttpResponse`; POST only via `HttpResponseNotAllowed(["POST"])` |
| View | `matches.views.play_week` | `(request, season_id) -> HttpResponse`; POST only; sync `@transaction.atomic` block inside body |
| View | `matches.views.play_two_months` | `(request, season_id) -> JsonResponse`; POST only; returns 202 |
| View | `matches.views.play_until_end` | `(request, season_id) -> JsonResponse`; POST only; returns 202 |
| View | `matches.views.play_status` | `(request, season_id, job_id) -> JsonResponse`; GET only |
| Helper | `matches.views._build_play_status_response` | Flat `_`-prefixed module-level helper, `(async_result, *, season_id) -> dict`; returns the 5-key polling JSON shape |
| Reused helper | `matches.views._celery_state_to_job_status` | API-03 mapping helper, consumed verbatim (no rename, no fork) |
| Celery task | `matches.tasks.play_season_task` | `@shared_task(bind=True, name="matches.play_season")`; `(self, season_id, max_matchdays=None) -> dict` |
| Pure function | `matches.season_dashboard.find_next_matchday` | `(fixtures, played_keys) -> Optional[int]` |
| Pure function | `matches.season_dashboard.select_play_fixtures` | `(fixtures, played_keys, max_matchdays) -> list`; canonical-iteration-order output |
| `max_matchdays` literals | `1`, `8`, `None` | One Week / Two Months / Until End values; pinned at the view layer where the task `.delay()` call is constructed |
| Polling JSON | 5 keys | `status, completed, total, error, season_id` |
| Polling status vocabulary | `"running"` / `"complete"` / `"error"` | Mirrors API-03 batch/save mapping |
| POST async response | 2 keys | `job_id, season_id` |
| `played_keys` shape | `set[tuple[frozenset[int], int]]` | Side-agnostic key — `(frozenset({team_red_id, team_blue_id}), round_number)` |
| Context key (Season dashboard) | `play_error` | `str \| None`; populated on sync-error re-render |
| Context key (Season dashboard) | `play_job_id` | `str \| None`; always `None` at LG-01d (reserved) |
| Context key (League dashboard) | `play_error` / `play_job_id` | Same shape as the Season dashboard |
| Template | `templates/seasons/dashboard.html` | MODIFIED — action-button slot now branched markup |
| Template | `templates/leagues/dashboard.html` | MODIFIED — symmetric action-button slot |
| DOM id (season) | `season-dashboard-play-dropdown` | Outer wrapper, always present when an action button renders |
| DOM id (season) | `season-dashboard-play-start-season` | Start Season `<form>`, only in `start_season` state |
| DOM id (season) | `season-dashboard-play-one-week` | One Week `<form>`, only in `play_next` state |
| DOM id (season) | `season-dashboard-play-two-months` | Two Months `<form>`, only in `play_next` state |
| DOM id (season) | `season-dashboard-play-until-end` | Until End `<form>`, only in `play_next` state |
| DOM id (season) | `season-dashboard-play-error` | Only when `play_error` truthy |
| DOM id (season) | `season-dashboard-play-progress` | Always (hidden by default; populated by JS) |
| DOM id (league) | `league-dashboard-play-dropdown` | League dashboard's symmetric outer wrapper |
| DOM id (league) | `league-dashboard-play-start-season` | League dashboard's Start Season `<form>` |
| DOM id (league) | `league-dashboard-play-one-week` | League dashboard's One Week `<form>` |
| DOM id (league) | `league-dashboard-play-two-months` | League dashboard's Two Months `<form>` |
| DOM id (league) | `league-dashboard-play-until-end` | League dashboard's Until End `<form>` |
| DOM id (league) | `league-dashboard-play-error` | League dashboard's error element |
| DOM id (league) | `league-dashboard-play-progress` | League dashboard's progress container |
| ADR file | `docs/adr/0016-play-season-job-execution-model.md` | NEW |
| CONTEXT.md edit | `Job` entry extended | "Three kinds today: Batch run job, Save-games job, Play Season job …" |
| CONTEXT.md addition | `Matchday` term | Added to `### League and seasons` section |
| PLAN.md edit | LG-01d marked `- completed` | Dense implementation note in LG-01c style |
| PLAN.md addition | LG-01h / LG-01i / LG-01j | Three new task entries |
| PLAN.md note | LG-02 relabel intent | "Until end of season" → "Until playoffs" once tournaments land |
| Test file | `matches/tests/test_play_orchestrator.py` | NEW; `SimpleTestCase`; pure-unit for `find_next_matchday` + `select_play_fixtures` |
| Test file | `matches/tests/test_lg01d_tasks.py` | NEW; `TestCase`; Celery EAGER tests on `play_season_task` |
| Test file | `matches/tests/views_tests.py` | EXTENDED; 5 new test classes (no existing class modified) |
| Test class | `TestFindNextMatchday` | in `test_play_orchestrator.py` |
| Test class | `TestSelectPlayFixtures` | in `test_play_orchestrator.py` |
| Test class | `TestPlaySeasonTaskHappyPath` | in `test_lg01d_tasks.py` |
| Test class | `TestPlaySeasonTaskMaxMatchdays` | in `test_lg01d_tasks.py` |
| Test class | `TestPlaySeasonTaskPerRoundCommit` | in `test_lg01d_tasks.py` |
| Test class | `TestPlaySeasonTaskTeamLookup` | in `test_lg01d_tasks.py` |
| Test class | `TestLg01dStartSeason` | in `views_tests.py` |
| Test class | `TestLg01dPlayWeek` | in `views_tests.py` |
| Test class | `TestLg01dPlayTwoMonths` | in `views_tests.py` |
| Test class | `TestLg01dPlayUntilEnd` | in `views_tests.py` |
| Test class | `TestLg01dPlayStatus` | in `views_tests.py` |
| Celery task name (broker) | `matches.play_season` | The `name=` kwarg on `@shared_task`; visible in worker logs |
| Idempotency token (Start Season) | substring `"non-completed"` | The substring matched in `ValidationError` messages to detect the "already active" double-submit race |
| ValidationError source | `Season.clean()` LG-01 | `"Only one non-completed Season is allowed per League."` |
| `arena_map` policy | omitted | `simulate_scheduled_round` called without `arena_map` kwarg; deferred to LG-01j |

---

**Seam summary in one sentence:** LG-01d adds 5 new POST + 1 new GET
URL routes on `/seasons/<id>/...` (`start_season`, `play_week` sync;
`play_two_months`, `play_until_end` async via the single shared Celery
task `matches.play_season` parameterised by `max_matchdays: int |
None`; `play_status` polling), 5 new view functions in
`matches/views.py` + 1 new flat `_build_play_status_response` helper
(reusing the API-03 `_celery_state_to_job_status` verbatim), 2 new
pure functions (`find_next_matchday`, `select_play_fixtures`) on the
LG-01c `matches/season_dashboard.py` module's frozen import allowlist,
1 new Celery task `play_season_task` in `matches/tasks.py` with
per-Round atomic commits (NO outer `@transaction.atomic` — load-
bearing decision recorded in ADR-0016), modified LG-01c
`templates/seasons/dashboard.html` + `templates/leagues/dashboard.html`
replacing the disabled action-button placeholder with branched markup
(single-button Start Season form OR Bootstrap dropdown trigger + 3
submit forms) and 14 new locked DOM ids (7 per dashboard), inline
polling JS per template (no external JS file), a locked 5-key polling
JSON shape `{status, completed, total, error, season_id}`, the
"non-completed" substring as the idempotent Start Season swallow
token, no model change / no migration / no `messages.*` / no
`master_seed` UI / no cancel UI / no `arena_map` UI / no top-nav
refactor (the latter four deferred to LG-01h / LG-01i / LG-01j and
LG-02), 1 new ADR (`docs/adr/0016-play-season-job-execution-model.md`),
2 CONTEXT.md edits (extend **Job**, add **Matchday**), 4 PLAN.md
edits (mark LG-01d completed + append LG-01h / LG-01i / LG-01j +
relabel note under LG-02), and 3 test files (2 NEW + 1 EXTENDED) with
11 new test classes — all running under the API-03
`LF_CELERY_EAGER=1` conftest with no new test infrastructure.
