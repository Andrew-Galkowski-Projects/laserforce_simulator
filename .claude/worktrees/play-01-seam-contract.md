# PLAY-01 seam contract — Live incremental stats + Stop/Cancel for multi-game runs

Layers onto the NAV-01 `Play ▾` topnav (`templates/_partials/topnav_play.html` +
`templates/_partials/topnav_play_script.html`, the single league-advancement surface).
Adds (1) a cooperative between-fixture **cancel** flag + a **Stop** control that swaps in
for the async Play forms while a run polls, and (2) **live incremental** standings/leaders
that recompute view-side from committed rows each poll and patch the dashboard panels by id.

**Scope: ASYNC runs only** — `play_two_months`, `play_until_end`, `play_playoffs`. The two
sync paths (`start_season`, `play_week`, `play_single_round`, `play_week_live`, `next_season`)
are untouched. No simulation-mechanic change, no new RNG in the tick loop → **NO Score
Calibration re-baseline**.

Locked design (already grilled — do not re-litigate): polling rail only (reuse NAV-01 poll
JS); cooperative DB-flag cancel, **NO `AsyncResult.revoke`**; task checks the flag at its top
AND between fixtures, stops cleanly, **RETURNS NORMALLY (Celery SUCCESS)** with partial
`{completed, total}` (maps to existing `"complete"`), optional `cancelled: true`; already-played
Rounds stay committed, run is resumable; no new status-vocabulary string.

---

## 1. Model + migration

**Module:** `matches/models.py` (the `Season` class, lines ~926–994 today). Two NEW fields,
appended after `starting_map_rotation_ids_json` (the last field), before `__str__`:

```python
active_play_job_id = models.CharField(max_length=255, null=True, blank=True, default=None)
play_cancel_requested = models.BooleanField(default=False)
```

- `active_play_job_id` — the Celery job id of the in-flight async run (NAV-01's poll
  carries the job id client-side; this column lets render/poll resume a run on page load).
  An async enqueue view SETS it at enqueue time; the task CLEARS it (`= None`) in its
  `finally`. Render/poll treats a **terminal** `AsyncResult` (`status` not `"running"`) as
  "no active run" regardless of the column (defends against a crashed worker that never
  cleared it).
- `play_cancel_requested` — the cooperative cancel flag. The async enqueue views CLEAR it
  (`= False`) at enqueue; `play_cancel` SETS it (`= True`); the task READS it (top + between
  fixtures) and never writes it.

Both confirmed **not present today** (grep clean). `next_season` is a VIEW
(`matches.league_views.next_season`), NOT a `Season` method — no collision.

**Migration:** `matches/migrations/0056_season_play_job_cancel.py` (next-sequential after the
current latest `0055_gameround_fidelity_roster_snapshot`), dependency
`("matches", "0055_gameround_fidelity_roster_snapshot")`. Exactly **2× `AddField`**
(`active_play_job_id` then `play_cancel_requested`). **NO `RunPython` / `RunSQL` / backfill**
(ADR-0004 disposable-data posture; existing Seasons take `null` / `False` defaults).

---

## 2. Views (`matches/league_views.py`)

### 2a. NEW view `play_cancel(request, season_id) -> JsonResponse`

POST-only. First line `if request.method != "POST": return HttpResponseNotAllowed(["POST"])`
(the locked LG-01d idiom). Then `season = get_object_or_404(Season, pk=season_id)`,
`request.session["last_league_id"] = season.league_id`, set `season.play_cancel_requested = True`
+ `season.save(update_fields=["play_cancel_requested"])`, return
`JsonResponse({"cancelled": True, "season_id": season.id})` (HTTP **200**). No active-run guard
needed — setting the flag on an idle Season is harmless (the next run clears it at enqueue;
nothing reads a stale `True`). 404 on a missing Season; the 405 guard precedes any ORM hit.

### 2b. Enqueue-time edits to the 3 async views (CHANGED)

`play_two_months` (~L2652), `play_until_end` (~L2676), `play_playoffs` (~L2725). Each keeps
its existing 405 guard, `last_league_id` write, and its active-state / built-tournament guard
**verbatim**. The ONLY change: between the guard and the existing `.delay(...)`, then on the
returned result, set the two new Season fields:

```python
result = play_season_task.delay(season.id, max_matchdays=8)   # (unchanged per view)
season.active_play_job_id = result.id
season.play_cancel_requested = False
season.save(update_fields=["active_play_job_id", "play_cancel_requested"])
return JsonResponse({"job_id": result.id, "season_id": season.id}, status=202)
```

(Clear cancel + record the job id at enqueue; the 202 JSON shape `{job_id, season_id}` is
UNCHANGED.) `play_playoffs` uses `play_playoffs_task.delay(season.id)` / `max_matchdays` per
its own current body — only the two-field assignment + save is inserted.

### 2c. Extended `play_status` JSON — `_build_play_status_response` (CHANGED)

`matches.league_views._build_play_status_response(async_result, *, season_id) -> dict`
(~L2747) and its caller `play_status` (~L2788) stay GET-only / same URL / same status-mapping
helper (`_celery_state_to_job_status`, reused verbatim). The **existing 5 keys are unchanged**:
`status` / `completed` / `total` / `error` / `season_id`. PLAY-01 ADDS these keys:

- `standings` — the live partial standings, **server-rendered HTML fragment** (see §2d).
- `leaders` — the live partial leaders, a **dict of 3 server-rendered HTML fragments**
  keyed `{"points": <html>, "tags": <html>, "ratio": <html>}` (one per leader board).
- `cancelled` — `bool`, optional UX toast flag. `True` when the task RETURNED a result dict
  carrying `cancelled: true` (read off `async_result.result["cancelled"]` on `SUCCESS`);
  `False` otherwise. (The status stays `"complete"` — there is NO new status string.)

The recompute runs **VIEW-SIDE from committed rows each poll** (NOT from Celery task meta) —
the same helpers `_build_dashboard_context` already uses (`compute_standings` over
`Match.objects.filter(season=season, is_completed=True)`; `compute_leaders` over
`PlayerRoundState.objects.filter(game_round__match__season=season)`), so the panels reflect
exactly what the per-Round commits have landed so far. `_build_play_status_response` therefore
needs the `season` (resolve it once in `play_status` and pass it in alongside `season_id`,
or pass `season_id` and re-fetch inside the helper — Code-agent discretion; the JSON shape is
what's pinned). `standings` / `leaders` are computed on EVERY poll (status running OR complete)
so the final poll patches the finished tables too.

### 2d. Partial-stats recompute source (PROPOSED, pinned at the seam)

**Proposed approach (recommended, not strictly pinned beyond the JSON keys + patched DOM ids):**
extract the standings-snippet and the three leaders-snippet markup out of
`templates/seasons/dashboard.html` / `templates/leagues/dashboard.html` into shared partials
(suggested `templates/_partials/dashboard_standings_snippet.html` +
`templates/_partials/dashboard_leaders_snippet.html`), `{% include %}`d by both dashboards AND
rendered by `_build_play_status_response` via `django.template.loader.render_to_string(...)`
fed the same `standings_snippet` / `leaders_points` / `leaders_tags` / `leaders_ratio` context
`_build_dashboard_context` builds. This keeps markup single-sourced (the dashboard and the live
patch render byte-identical HTML). **Only the JSON keys (`standings`, `leaders`, optional
`cancelled`) and the patched DOM ids (§5) are LOCKED** — the partial filenames/extraction are
the recommended path, Code-agent may choose an equivalent single-source mechanism.

The recompute reuses (verbatim, no new behaviour):
- `matches.standings.compute_standings(completed_matches, enrolled_teams, season_rounds=None)`
  → `list[StandingsRow]` (17-field frozen dataclass: `team_id, matches_played, wins, losses,
  ties, league_points, round_wins, total_score, rank, match_streak, match_l5, round_streak,
  round_l5, red_wlt, blue_wlt, red_points_for, blue_points_for`).
- `matches.season_dashboard.compute_leaders(player_rounds, stat, limit=3)` → `list[LeaderRow]`
  for each of `"points_per_game"` / `"tags_per_game"` / `"tag_ratio"`.

---

## 3. Tasks (`matches/tasks.py`)

Both `play_season_task` (`name="matches.play_season"`) and `play_playoffs_task`
(`name="matches.play_playoffs"`) gain the cooperative cancel check + the `active_play_job_id`
clear. Each already wraps its body in `try: … finally: django.db.close_old_connections()`.

### 3a. Cancel-check helper (NEW, module-level in `matches/tasks.py`)

```python
def _play_cancel_requested(season_id: int) -> bool:
    """True iff the Season's cooperative cancel flag is set. Re-reads from the
    DB each call (the flag is set by the play_cancel view mid-run)."""
    from matches.models import Season
    return Season.objects.filter(id=season_id, play_cancel_requested=True).exists()
```

A single-column existence query (no Season instance materialised), re-read each call so a
mid-run `play_cancel` POST is observed. Deferred import (the file's deferred-import precedent).

### 3b. Where it's checked + the early-SUCCESS return

- **Top (queued-but-not-started case):** at the start of the task body, after loading the
  Season and BEFORE the fixture/stage loop begins. If `_play_cancel_requested(season_id)`,
  return early with `{"completed": 0, "total": <n or 0>, "cancelled": True}`.
- **Between fixtures (running case):** inside the per-fixture loop (`play_season_task`) /
  per-stage drain loop (`play_playoffs_task`), at the TOP of each iteration, BEFORE
  `simulate_scheduled_round(...)` / `play_next_bracket_round(...)`. If
  `_play_cancel_requested(season_id)`, **break** out cleanly and return
  `{"completed": <k so far>, "total": <n>, "cancelled": True}`.

The early return is a **normal return** ⇒ Celery records **SUCCESS** ⇒ `play_status` maps it
to `"complete"` (NO new status string). The partial `{completed, total}` is whatever committed;
`cancelled: True` rides along for the optional toast. Already-played Rounds stay committed
(each is its own per-Round atomic commit, ADR-0016); the Season stays `active` and is
resumable (re-clicking Play resumes via the existing played-keys / `find_next_playable_node`
skip).

### 3c. The `finally` clear

In BOTH tasks' existing `finally:` block, alongside `django.db.close_old_connections()`, clear
the active-run column (best-effort, swallow errors so the close still runs):

```python
finally:
    from matches.models import Season
    Season.objects.filter(id=season_id).update(active_play_job_id=None)
    django.db.close_old_connections()
```

`.update(...)` (not `.save()`) avoids materialising the Season and touches only the one column.
This fires on success, cancel-return, AND failure — so a crashed run never leaves a stale
`active_play_job_id`. The cancel flag is NOT cleared here (it's cleared at the next enqueue);
render/poll treats a terminal `AsyncResult` as "no active run" regardless.

---

## 4. Render seam (`_build_play_controls_context` / `league_nav`)

`matches.league_views._build_play_controls_context(league, displayed_season) -> dict`
(~L1416) gains ONE key so the topnav can render the Stop control + resume polling on load:

- `active_play_job_id` (`str | None`) — `displayed_season.active_play_job_id` when a displayed
  Season exists, else `None`.

`core.context_processors.league_nav` already merges every `_build_play_controls_context` key
into the topnav context (its `result.update(play_keys)` on the league-prefix path), so
`active_play_job_id` flows through with NO `league_nav` edit beyond the key already being in
the merged dict. (It stays ABSENT off-league / on the fallback path, exactly like the other
play keys — the `Play ▾` only renders in the league branch.) `play_displayed_season_id` /
`play_league_id` are already emitted by `league_nav` and reused as-is for the `play_cancel`
reverse.

---

## 5. Templates

### 5a. `templates/_partials/topnav_play.html` — Play→Stop swap

Add a **Stop control** (NEW DOM id `topbar-play-stop`) rendered when a run is active —
i.e. when `active_play_job_id` is truthy. A POST `<form>` to `play_cancel`:

```html
{% if active_play_job_id %}
<form id="topbar-play-stop" method="post" action="{% url 'play_cancel' season_id=play_displayed_season_id %}" class="px-2">
    {% csrf_token %}
    <button type="submit" class="dropdown-item text-danger" data-action-state="{{ action_button_state }}">Stop</button>
</form>
{% endif %}
```

Placement: inside `#topbar-play-dropdown`'s `<ul class="dropdown-menu">` (a Stop item in the
dropdown), OR as a standalone control swapped in for the dropdown when active — Code-agent
discretion on layout; the LOCKED part is the `topbar-play-stop` id, the `play_cancel` POST
action, the `{% csrf_token %}`, and that it renders iff `active_play_job_id`. The existing
`topbar-play-dropdown` / `topbar-play-two-months` / `topbar-play-until-end` /
`topbar-play-play-playoffs` / `topbar-play-progress` (+ inner `.play-progress-spinner` /
`.play-progress-label` / `.play-progress-bar`) / `topbar-play-error` ids are PRESERVED.

### 5b. `templates/_partials/topnav_play_script.html` — resume-on-load, patch panels, wire Stop

The relocated NAV-01 poll IIFE (its `interceptAsync` / `startPolling` / `showProgress` /
`clearPolling` / `setDropdownDisabled` / `ensureErrorEl` fns) is EXTENDED:

1. **Resume polling on load when a run is active.** After the existing
   `interceptAsync(...)` wiring at the bottom, add: if `active_play_job_id` is non-empty,
   call `startPolling("{{ active_play_job_id }}")` so a page reload mid-run re-attaches to the
   in-flight job and shows progress without a fresh submit. (Gate the inline `<script>` is
   already `{% if play_displayed_season_id %}`; add an `active_play_job_id` template var read.)
2. **Patch dashboard panels by id each poll, existence-guarded (no-op off-dashboard).** Inside
   the `startPolling` `.then(function(data){...})` success handler, after `showProgress(...)`,
   if `data.standings` is present and the element exists, replace its innerHTML; same for the
   three leaders fragments. Use the REAL dashboard ids (both season- and league- variants —
   patch whichever exist; off-dashboard pages have none, so every patch is a guarded no-op):

   - standings: `season-dashboard-standings-snippet`, `league-dashboard-standings-snippet`
   - leaders (points): `season-dashboard-leaders-points`, `league-dashboard-leaders-points`
   - leaders (tags): `season-dashboard-leaders-tags`, `league-dashboard-leaders-tags`
   - leaders (ratio): `season-dashboard-leaders-ratio`, `league-dashboard-leaders-ratio`

   (`data.standings` ⇒ both standings ids; `data.leaders.points` ⇒ both points ids; etc. —
   the JSON `leaders` is the 3-key dict from §2c. `round-count` / `next-round` are NOT patched
   this slice — only standings + the three leaders boards.)
3. **Wire Stop.** Bind the `#topbar-play-stop` form's submit to a fetch-POST to its `action`
   (the `play_cancel` URL) with the `X-CSRFToken` header (reuse the existing `CSRF_TOKEN` +
   the `interceptAsync` fetch shape), then on response leave polling running — the task
   observes the flag between fixtures and finishes with `status: "complete"` +
   `cancelled: true`, at which point the existing `clearPolling()` + `window.location.reload()`
   path fires (and Code-agent MAY surface the optional `data.cancelled` toast before reload).
   The Stop form must NOT navigate (preventDefault) — it's a cooperative request, the run
   keeps polling until it returns.

The poll URL / interval (`play_status`, 500 ms, `?season_id=` carry), the reload-on-complete,
and the `topbar-play-error` surfacing are UNCHANGED.

### 5c. Dashboard panels to patch (the live targets, both variants, verbatim ids)

These already exist on the dashboards (read-only panels, NAV-01 KEPT them) and are the patch
targets — no template change to the dashboards themselves beyond the §2d partial extraction:

| panel | season variant | league variant |
|---|---|---|
| standings snippet | `season-dashboard-standings-snippet` | `league-dashboard-standings-snippet` |
| leaders — points | `season-dashboard-leaders-points` | `league-dashboard-leaders-points` |
| leaders — tags | `season-dashboard-leaders-tags` | `league-dashboard-leaders-tags` |
| leaders — ratio | `season-dashboard-leaders-ratio` | `league-dashboard-leaders-ratio` |

(Existing-but-not-patched read-only ids: `*-dashboard-round-count`, `*-dashboard-next-round`,
`*-dashboard-state-badge`, `*-dashboard-map-config`, `*-dashboard-view-bracket-link`,
`*-dashboard-past-evaluations-link`, `*-dashboard-play-error`.)

---

## 6. Test boundary

The Tests agent asserts against (public surface):

1. **Cancel halts the task after one fixture under EAGER, no mocks.** Build a real multi-fixture
   active Season; enqueue `play_season_task` under `CELERY_TASK_ALWAYS_EAGER`; set
   `play_cancel_requested = True` after one fixture commits (e.g. via a small real seam — a
   2-fixture Season where the flag is set, then re-run; or assert the top-check early-return on
   a pre-set flag). Assert: the task RETURNS NORMALLY (no exception), the return dict carries
   `cancelled: True` and a partial `{completed < total}`, already-committed Rounds survive
   (`GameRound` rows for the played fixture exist), the Season stays `state == "active"`, and
   `active_play_job_id` is cleared (`None`) in the `finally`. Same shape for `play_playoffs_task`
   (one bracket stage then cancel). **No `mock.patch` on the task / `simulate_scheduled_round` /
   `play_next_bracket_round`** — real per-fixture commits so the cancel-halt is exercised end to
   end.
2. **`play_cancel` view — flag + status.** POST → 200 JSON `{"cancelled": true, "season_id"}`
   AND `season.play_cancel_requested == True` persisted; GET → 405; missing Season → 404.
3. **Extended `play_status` JSON keys.** The response carries the existing 5 keys PLUS
   `standings` (HTML string), `leaders` (3-key dict `points`/`tags`/`ratio`), and `cancelled`
   (bool, `True` only when the task returned `cancelled: true`).
4. **Partial-stats recompute is VIEW-SIDE from committed rows.** With N of M fixtures committed,
   `play_status`'s `standings` / `leaders` reflect exactly the committed rows (assert a known
   team's W/L appears / a known scorer leads) — recomputed via `compute_standings` /
   `compute_leaders`, NOT read from Celery task meta. Off-dashboard the JSON still carries the
   fragments (the JS patch is the no-op, not the view).
5. **Resumable-render context.** `_build_play_controls_context` / `league_nav` emit
   `active_play_job_id` = the Season's column on the league-prefix path (and it's ABSENT
   off-league / on fallback). The enqueue views SET `active_play_job_id` + clear
   `play_cancel_requested`; the task's `finally` clears `active_play_job_id`.
6. **Migration shape.** `0056_season_play_job_cancel` is 2× `AddField`, no `RunPython`;
   `makemigrations --check` is clean.

What is **internal** (NOT a test target): the partial-filename extraction / `render_to_string`
mechanism (only the JSON keys + patched DOM ids are pinned); the exact JS patch implementation
(only the resume-on-load + existence-guarded patch behaviour is pinned); the `_play_cancel_requested`
query form; the Stop control's dropdown-vs-standalone layout.

---

## Locked names (quick index)

- **Model fields:** `Season.active_play_job_id` (`CharField(max_length=255, null=True,
  blank=True, default=None)`), `Season.play_cancel_requested` (`BooleanField(default=False)`).
- **Migration:** `matches/migrations/0056_season_play_job_cancel.py` (dep
  `0055_gameround_fidelity_roster_snapshot`, 2× `AddField`, no `RunPython`).
- **View:** `matches.league_views.play_cancel(request, season_id) -> JsonResponse` (POST →
  200 `{"cancelled": True, "season_id"}`; 405 / 404).
- **URL:** name `play_cancel`, path `/seasons/<int:season_id>/play-cancel/`, added to
  `matches/season_urls.py` among the play routes — after `play_status`, before
  `season_standings` / `season_schedule` (first-match precedent).
- **Enqueue edits:** `play_two_months` / `play_until_end` / `play_playoffs` set
  `active_play_job_id = result.id` + `play_cancel_requested = False` +
  `save(update_fields=[...])` before the 202 (shape `{job_id, season_id}` unchanged).
- **Extended `_build_play_status_response`:** existing 5 keys (`status, completed, total,
  error, season_id`) PLUS `standings` (str HTML), `leaders` (`{"points","tags","ratio"}`
  HTML dict), `cancelled` (bool). Recomputed view-side via `compute_standings` /
  `compute_leaders` each poll. `_celery_state_to_job_status` reused verbatim; no new status
  string.
- **Task helper:** `matches.tasks._play_cancel_requested(season_id) -> bool` (single-column
  exists query). Checked at task top AND between fixtures; cancel ⇒ normal return
  `{"completed", "total", "cancelled": True}` (Celery SUCCESS ⇒ `"complete"`). `finally`
  clears `active_play_job_id` via `.update(active_play_job_id=None)` alongside
  `django.db.close_old_connections()` in BOTH `play_season_task` + `play_playoffs_task`.
- **Render key:** `_build_play_controls_context` adds `active_play_job_id`; `league_nav`
  merges it on the league-prefix path (ABSENT off-league / fallback).
- **Templates:** `topnav_play.html` adds `#topbar-play-stop` (POST `play_cancel`, renders iff
  `active_play_job_id`); `topnav_play_script.html` resumes polling on load when
  `active_play_job_id`, patches `{season,league}-dashboard-standings-snippet` /
  `-leaders-points` / `-leaders-tags` / `-leaders-ratio` existence-guarded, wires Stop.
  Proposed shared partials `templates/_partials/dashboard_standings_snippet.html` +
  `dashboard_leaders_snippet.html` (single-source markup; only the JSON keys + DOM ids locked).
- **ADRs:** NEW ADR recording cooperative-cancel + polling-stats (reverses ADR-0013's
  "no cancel-in-flight UX" / `AsyncResult.revoke`-exists-but-no-UI and ADR-0016's
  `AsyncResult.revoke` rejection at lines 147–155 — "Mid-job cancel via `AsyncResult.revoke`.
  Rejected for LG-01d … that needs cooperative-cancel polling inside the task body"); short
  addenda on ADR-0013 + ADR-0016.
- **Locked: NO `AsyncResult.revoke`, NO new status-vocabulary string, ASYNC runs only, NO
  Score Calibration re-baseline.**
