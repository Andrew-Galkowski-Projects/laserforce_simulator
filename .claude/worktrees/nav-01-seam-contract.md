# NAV-01 — `Play ▾` top-nav dropdown (single advancement surface) — SEAM CONTRACT

Add a league-mode-only `Play ▾` top-nav dropdown that becomes the **SINGLE** place to advance a
league's state. **RELOCATE** (not duplicate) the advancement controls out of both dashboards into
the global topnav; reuse every existing play endpoint verbatim.

Branch: `nav-01-play-dropdown`. No model change, no migration. ADR `docs/adr/0030-play-controls-relocated-to-topnav.md` (docs agent writes it; contract only NAMES it).

---

## 0. Locked decisions (verbatim)

1. **Full parity, RELOCATE not duplicate.** The topnav `Play ▾` is the sole advancement surface.
   Both dashboards (`templates/seasons/dashboard.html`, `templates/leagues/dashboard.html`) LOSE
   their advancement Play controls. The dashboards KEEP: read-only data panels (standings snippet /
   leaders / next-round / round-count / map-config label), the playoff **View bracket** link (→
   `tournament_detail`), the CAR-02 **View past evaluations** link, and the `play_error` banner.
2. **League-mode only**, gated on `app_mode == "league"` (LG-01k path-prefix rule: `/leagues/` or
   `/seasons/`). Rendered in the league branch of `templates/base.html`.
3. The nav advances the **league's resolved active/displayed Season** via the existing
   `core.context_processors.league_nav` resolution chain (session `last_league_id` → single-League →
   fallback; displayed_season = `league.active_season` → most-recent completed → None) — **NOT** the
   `season_id` in the URL.
4. **Processor wiring:** EXTEND `league_nav` to also compute the play-control state, GATED on the
   league path-prefix. It calls a NEW shared helper
   `matches.league_views._build_play_controls_context(league, displayed_season) -> dict` factored OUT
   of `_build_dashboard_context`. After the factor-out, `_build_dashboard_context` STOPS emitting the
   play keys (they move to the shared helper / processor). The processor returns the play keys
   alongside the existing `top_bar_links` / `top_bar_dashboard_url`.
5. **States the dropdown reproduces** (mirror the current `action_button_state` machine + playoff group):
   - `start_season` → Start-Season POST form (→ `start_season`).
   - `play_next` → 4 actions: One Week (`play_week`, sync), Two Months (`play_two_months`, async),
     Until End (`play_until_end`, async — terminal relabel "Play Until Playoffs" / "Play Until
     Tournament" / "Play Until End of Season" via `has_following_tournament_phase` /
     `following_tournament_is_final`), One Week Live (`play_week_live`, gated `live_preview_available`).
   - `start_next_season` → owner-eval link (career: → `owner_evaluation` w/ `displayed_season.id`) OR
     next-season POST form (non-career: → `next_season` w/ `league_id`), branched on `is_career_mode`.
   - playoff group when `playoff_phase_active`: Play Single Round (`play_single_round`) + Play
     Playoffs (`play_playoffs`). (View bracket stays on the dashboard, read-only.)
   - `none` → a disabled "Play" affordance.
6. **Async actions** (Two Months / Until End / Play Playoffs): progress-affordance ONLY — reuse the
   `play_status` poll endpoint + `_build_play_status_response` + `_celery_state_to_job_status`
   VERBATIM; relocate the inline poll JS into `base.html` (ONE copy, league branch, a navbar progress
   element); disable the dropdown while a run polls. Per-page poll only (no cross-navigation resume).
   Sync actions submit normally (server 302 redirect).
7. **Deferred to PLAY-01:** Play→Stop swap, cancel/revoke, live incremental standings/leaders,
   cross-page resumable progress. NAV-01 ships progress-display only.
8. **Sync errors** keep landing on the dashboard `play_error` banner (the endpoints' existing error
   path is unchanged — they `_render_season_dashboard_error(...)` → 400 dashboard re-render).
9. **No model change, no migration.** ADR-0030 named only.

---

## 1. Shared helper (factored OUT of `_build_dashboard_context`)

```python
# matches/league_views.py
def _build_play_controls_context(
    league: League, displayed_season: Optional[Season]
) -> dict:
    ...
```

Computes the play-control state for the **resolved** league + displayed Season. Returns the play
subset of keys currently emitted by `_build_dashboard_context` (those keys MOVE here):

| key | type | source / meaning (unchanged) |
|---|---|---|
| `action_button_label` | `str` | "No Season" / "Start Season" / "Play Next" / "Start Next Season" |
| `action_button_state` | `str` | `"none"` / `"start_season"` / `"play_next"` / `"start_next_season"` |
| `playoff_phase_active` | `bool` | `_playoff_cursor_keys(displayed_season)[0]` |
| `playoff_tournament_id` | `int \| None` | `_playoff_cursor_keys[1]` |
| `playoff_completed` | `bool` | `_playoff_cursor_keys[2]` |
| `has_following_tournament_phase` | `bool` | `_playoff_cursor_keys[3]` |
| `following_tournament_is_final` | `bool` | `_playoff_cursor_keys[4]` |
| `live_preview_available` | `bool` | `_resolve_live_cursor(...).kind in ("rr","playoff")` when `season_mode == "active"` |
| `is_career_mode` | `bool` | `_is_career_league(league)` |

`season_mode` derivation inside the helper mirrors the current view logic
(`league.active_season` present → `"draft"`/`"active"`; else most-recent completed → `"completed"`;
else `"none"`). The helper takes the **resolved displayed_season** (already chosen by `league_nav`'s
chain), so it does NOT re-implement the resolution; it derives `season_mode` from
`displayed_season.state` / `None`.

**`_build_dashboard_context(displayed_season, season_mode)` after the factor-out:** STOPS emitting
`action_button_label`, `action_button_state`, `playoff_phase_active`, `playoff_tournament_id`,
`playoff_completed`, `has_following_tournament_phase`, `following_tournament_is_final`,
`live_preview_available`, `is_career_mode`. It RETAINS the read-only body keys: `displayed_season`,
`season_mode`, `standings_snippet`, `next_fixture`, `round_count_completed`, `round_count_total`,
`leaders_points`, `leaders_tags`, `leaders_ratio`, `map_config_label`, **plus** `playoff_tournament_id`
(KEEP — the read-only View-bracket dashboard link still needs it; see §4). The dashboards call the
shared helper too? — **No.** The dashboards do not render play controls anymore, so they need NO play
keys except `playoff_tournament_id` for the read-only bracket link (which `_build_dashboard_context`
keeps). `play_error` / `play_job_id` context keys (LG-01d) STAY on the dashboard view (the banner is
dashboard-rendered on a sync error re-render).

> Implementation note: the cleanest factoring keeps `playoff_tournament_id` computed once (it serves
> both the dashboard View-bracket link AND the nav playoff group). The helper recomputes it from
> `_playoff_cursor_keys`; `_build_dashboard_context` keeps its own existing computation. No shared
> state required.

---

## 2. Processor change — `core.context_processors.league_nav`

EXTEND `league_nav(request)` (the LG-01k 2-key processor). After the existing 3-step league
resolution + displayed-Season resolution, **GATED on the league path-prefix** (skip the work on
sandbox/start pages — reuse the same `app_mode == "league"` test, i.e. `request.path` startswith
`/leagues/` or `/seasons/`):

- On a resolved league + (any) displayed_season AND a league-prefix path: call
  `matches.league_views._build_play_controls_context(league, displayed_season)` (lazy import inside
  the function body — the existing `core ↔ matches` apps-loading-cycle guard) and **merge its 9 keys**
  into the return dict alongside `top_bar_links` / `top_bar_dashboard_url`.
- Off-league (sandbox/start path) OR on the `_fallback()` path (no resolvable league): the play keys
  are **ABSENT** from the return dict (the nav `Play ▾` is rendered ONLY in the league branch, which
  only renders when `app_mode == "league"`, so absent keys are never read).

**New keys league_nav adds (league-prefix path only):** the 9 keys from §1's table —
`action_button_label`, `action_button_state`, `playoff_phase_active`, `playoff_tournament_id`,
`playoff_completed`, `has_following_tournament_phase`, `following_tournament_is_final`,
`live_preview_available`, `is_career_mode`. PLUS two URL/id keys the nav forms need to build their
`{% url %}` reverses against the **resolved** league/season (since the topnav has no `season`/`league`
template var):

| key | type | value |
|---|---|---|
| `play_displayed_season_id` | `int \| None` | `displayed_season.id` (None when no Season) |
| `play_league_id` | `int \| None` | `league.id` |

`top_bar_links` / `top_bar_dashboard_url` are unchanged. Fallback shape unchanged (`top_bar_links=[]`,
`top_bar_dashboard_url=reverse("league_list")`, no play keys).

---

## 3. NEW nav DOM ids (full list, locked)

Toggle: **`play-nav-link`** (the `<a class="nav-link dropdown-toggle">Play ▾</a>`, U+25BE caret).

Dropdown wrapper: **`topbar-play-dropdown`** (the `<div class="nav-item dropdown">`).

Per-state items (the `<form>` / `<a>` carrying the action, mirroring the dashboard ids but
`topbar-play-`-prefixed):

| state | DOM id | element | endpoint (reverse) | mode |
|---|---|---|---|---|
| `start_season` | `topbar-play-start-season` | `<form method=post>` | `start_season` (`season_id=play_displayed_season_id`) | sync 302 |
| `play_next` | `topbar-play-one-week` | `<form>` | `play_week` (`season_id=…`) | sync 302 |
| `play_next` | `topbar-play-two-months` | `<form>` | `play_two_months` (`season_id=…`) | async 202 |
| `play_next` | `topbar-play-until-end` | `<form>` | `play_until_end` (`season_id=…`) | async 202 |
| `play_next` | `topbar-play-one-week-live` | `<form>` | `play_week_live` (`season_id=…`) | sync 302 (gated `live_preview_available`) |
| `start_next_season` (career) | `topbar-play-owner-evaluation` | `<a>` | `owner_evaluation` (`season_id=play_displayed_season_id`) | GET link |
| `start_next_season` (non-career) | `topbar-play-next-season` | `<form>` | `next_season` (`league_id=play_league_id`) | sync 302 |
| playoff group | `topbar-play-play-single-round` | `<form>` | `play_single_round` (`season_id=…`) | sync 302 |
| playoff group | `topbar-play-play-playoffs` | `<form>` | `play_playoffs` (`season_id=…`) | async 202 |

Progress affordance (one navbar element, always present in the league branch, hidden until a poll
starts): **`topbar-play-progress`** with the inner spinner class `play-progress-spinner`, the label
class `play-progress-label`, and the bar class `play-progress-bar` (reuse the LG-01d progress-block
DOM contract so the relocated JS reads the same hooks). Errors render into **`topbar-play-error`**
(navbar element, created/shown by the JS on an async failure — mirrors the LG-01d `ensureErrorEl`
contract).

The terminal-relabel of `topbar-play-until-end`'s button TEXT is unchanged behaviour:
`{% if has_following_tournament_phase %}{% if following_tournament_is_final %}Play Until Playoffs{% else %}Play Until Tournament{% endif %}{% else %}Play Until End of Season{% endif %}`.

`{% csrf_token %}` mandatory in every nav `<form>`. Each carries `data-action-state="{{ action_button_state }}"` on its submit control (test hook parity with the dashboards).

---

## 4. RETIRED dashboard DOM ids (DELETED) vs KEPT (read-only)

**DELETED from `templates/seasons/dashboard.html`** (advancement Play markup + its inline JS):
- `season-dashboard-play-dropdown`
- `season-dashboard-play-start-season`
- `season-dashboard-play-one-week`
- `season-dashboard-play-two-months`
- `season-dashboard-play-until-end`
- `season-dashboard-play-one-week-live`
- `season-dashboard-owner-evaluation-link`
- `season-dashboard-next-season-form`
- `season-dashboard-play-progress`
- `season-dashboard-play-single-round-form` / `season-dashboard-play-single-round-submit`
- `season-dashboard-play-playoffs-form` / `season-dashboard-play-playoffs-submit`
- `season-dashboard-play-playoffs-progress`
- both inline `<script>` poll blocks (the `action_button_state == "play_next"` block + the
  `playoff_phase_active` block)
- the `season-dashboard-action-button` wrapper `<span>` (it only wrapped advancement controls) and
  the `season-dashboard-state-badge` STAYS (read-only mode badge).

**DELETED from `templates/leagues/dashboard.html`** (the symmetric `league-dashboard-play-*` set):
`league-dashboard-play-dropdown`, `league-dashboard-play-start-season`, `league-dashboard-play-one-week`,
`league-dashboard-play-two-months`, `league-dashboard-play-until-end`, `league-dashboard-play-one-week-live`,
`league-dashboard-owner-evaluation-link`, `league-dashboard-next-season-form`,
`league-dashboard-play-progress`, `league-dashboard-play-single-round-form` /
`-play-single-round-submit`, `league-dashboard-play-playoffs-form` / `-play-playoffs-submit`,
`league-dashboard-play-playoffs-progress`, the `league-dashboard-action-button` wrapper `<span>`, and
both inline poll `<script>` blocks.

**KEPT (read-only) on BOTH dashboards:**
- `{season,league}-dashboard-state-badge` (the mode badge — keep).
- `{season,league}-dashboard-view-bracket-link` (→ `tournament_detail` via `playoff_tournament_id`,
  rendered whenever `playoff_tournament_id is not None`).
- `{season,league}-dashboard-past-evaluations-link` (CAR-02 "View past evaluations" → league history).
- `{season,league}-dashboard-play-error` (the `play_error` banner — sync errors still land here).
- all standings / next-round / round-count / leaders / map-config DOM ids (LG-01c/j read-only panels).

> The `playoff_completed`-only / `playoff_tournament_id is not None` "View bracket" block survives on
> the dashboard; only the `playoff_phase_active` Play forms move to the nav.

---

## 5. `base.html` — added league-branch markup + relocated poll JS

**Added markup:** inside the `{% if app_mode == "league" %}` branch of the `<div class="navbar-nav
ms-auto">`, add a new `Play ▾` dropdown (`topbar-play-dropdown` + toggle `play-nav-link`). **Position:**
immediately AFTER the `dashboard-nav-link` ⌂ icon and BEFORE the `League ▾` section dropdown — i.e.
`⌂ | Play ▾ | League ▾ | Team ▾ | Players ▾ | Stats ▾ | Tools ▾ | Help ▾`. The dropdown body branches
on `action_button_state` (the §3 item table) using the `play_displayed_season_id` / `play_league_id`
context keys for its `{% url %}` reverses; renders the disabled "Play" affordance for `"none"`. The
navbar `topbar-play-progress` / `topbar-play-error` elements live alongside the dropdown (always
present in the league branch, hidden by default).

**Relocated poll `<script>` block** (ONE copy, in the league branch of `base.html`, rendered only when
the play keys are present, i.e. league mode): the inline IIFE relocated from the two dashboards,
collapsed to one copy. Its DOM contract:
- function names preserved from the LG-01d block: `interceptAsync(form)`, `startPolling(jobId)`,
  `showProgress(completed, total)`, `clearPolling()`, `setDropdownDisabled(disabled)`,
  `ensureErrorEl()`.
- progress element id read: **`topbar-play-progress`** (with inner `.play-progress-spinner` /
  `.play-progress-label` / `.play-progress-bar`); error element id: **`topbar-play-error`**.
- dropdown disabled target: **`topbar-play-dropdown`** (disables all `button, [type=submit]` while
  polling).
- async forms intercepted: `topbar-play-two-months`, `topbar-play-until-end`,
  `topbar-play-play-playoffs` (these POST → read the 202 JSON `{job_id, season_id}` → `startPolling`).
- status URL built: `{% url 'play_status' season_id=play_displayed_season_id job_id='JOB' %}` with
  `'JOB'` substituted client-side, polled at the LG-01d 500 ms interval, `?season_id=…` query carry,
  reload on `status === "complete"`, error into `topbar-play-error` on `status === "error"`.
- sync forms (`topbar-play-one-week`, `topbar-play-one-week-live`, `topbar-play-start-season`,
  `topbar-play-next-season`, `topbar-play-play-single-round`) submit normally (server 302).

The Tools/Help/section partials are untouched.

---

## 6. Templates modified

| file | change |
|---|---|
| `templates/base.html` | ADD `Play ▾` dropdown + progress/error navbar elements + ONE relocated poll JS block (league branch). |
| `templates/seasons/dashboard.html` | REMOVE advancement Play controls + both inline poll `<script>` blocks; KEEP read-only links + `play_error` banner (§4). |
| `templates/leagues/dashboard.html` | Same removal/keep as season dashboard (symmetric ids). |

(Optional, code-agent discretion: factor the `Play ▾` dropdown body into
`templates/_partials/topnav_play.html` included from the league branch — only the DOM ids are pinned,
not the inclusion structure. The poll JS may likewise live in a `_partials/topnav_play_script.html`,
ONE include in the league branch.)

---

## 7. Views / URLs — NO new routes, NO new view functions

Confirmed by inspection (`matches/season_urls.py`, `matches/league_urls.py`):
- All play endpoints are reused **verbatim**: `start_season`, `play_week`, `play_two_months`,
  `play_until_end`, `play_week_live`, `play_single_round`, `play_playoffs`, `play_status` (season
  URLs), `next_season`, `owner_evaluation` (league/season URLs).
- **They already 302 to `season_dashboard` (sync) or return 202 JSON (async) from anywhere** — the
  redirect/JSON targets do not depend on the request origin, so submitting from the topnav works with
  **no view tweak**. `start_season` / `play_week` / `play_single_round` / `next_season` all
  `redirect("season_dashboard", season_id=…)`; the async trio return `JsonResponse(..., status=202)`;
  `play_status` returns the 5-key JSON; `owner_evaluation` / `next_season` reverse cleanly.
- Sync error path unchanged: `play_week` / `start_season` `_render_season_dashboard_error(...)` → 400
  dashboard re-render with `play_error` (so the nav-submitted sync error STILL lands on the dashboard
  banner — exactly the locked decision #8). `play_two_months` / `play_until_end` / `play_playoffs`
  return 409/400 JSON on a non-active/no-bracket guard (the JS surfaces it via `topbar-play-error`).

**Net:** zero new URL routes, zero new view functions. The ONLY code edit beyond templates is the
`_build_play_controls_context` factor-out + the `league_nav` extension (§1–§2).

---

## 8. Test boundary

### Rewritten / extended (advancement ids ABSENT on dashboards, PRESENT in nav)
- `matches/tests/test_season_dashboard_view.py` — the dashboard-play assertions
  (LG-01d `TestLg01d*`, the playoff `season-dashboard-play-single-round-*` / `-play-playoffs-*` checks,
  the LG-01e `TestLg01eDashboardWiring` next-season-form / owner-eval-link checks, the LG-01i
  `season-dashboard-play-one-week-live` entry, the LG-01j map-config keeps): assert the RETIRED ids
  (§4) are **ABSENT** from the rendered dashboard, the KEPT read-only ids (`-view-bracket-link`,
  `-past-evaluations-link`, `-play-error`, `-state-badge`) **PRESENT**.
- `matches/tests/test_league_dashboard.py` — symmetric for the league dashboard
  (`TestLeagueDashboardDraftBranch` / `ActiveBranch` / `CompletedBranch` / `NoneBranch`,
  `TestLg01eDashboardWiring`, `TestLg01iDashboardEntry`): RETIRED `league-dashboard-play-*` ids ABSENT,
  read-only KEPT ids PRESENT.
- `matches/tests/test_season_playoffs.py` — the dashboard playoff-control assertions
  (`season-dashboard-play-single-round-*`, `-play-playoffs-*`) move to nav-id assertions; the
  `playoff_tournament_id` View-bracket dashboard link assertion STAYS.
- `matches/tests/views_tests.py` — `TestLg01dStartSeason` / `TestLg01dPlayWeek` /
  `TestLg01dPlayTwoMonths` / `TestLg01dPlayUntilEnd` / `TestLg01dPlayStatus` keep their
  endpoint-behaviour assertions (status codes / 302 / 202 / 409 / 5-key JSON) UNCHANGED — those test
  the views, which are unchanged. Any assertion that the dashboard renders the play forms moves to the
  nav.

### NEW nav test file(s)
- `matches/tests/test_nav_play_dropdown.py` (NEW): renders a league-mode page (any `/leagues/<id>/`
  or `/seasons/<id>/` URL) across the **state matrix** and asserts on the league-branch `base.html`
  DOM ids:
  - `none` → `topbar-play-dropdown` present, disabled affordance, no action forms.
  - `start_season` → `topbar-play-start-season` form → `start_season` reverse.
  - `play_next` → `topbar-play-one-week` / `-two-months` / `-until-end` present; `-one-week-live`
    present iff `live_preview_available`; terminal label text per `has_following_tournament_phase` /
    `following_tournament_is_final`.
  - `start_next_season` career → `topbar-play-owner-evaluation` link; non-career →
    `topbar-play-next-season` form.
  - `playoff_phase_active` → `topbar-play-play-single-round` + `topbar-play-play-playoffs`.
  - poll path: an async submit returns 202, the navbar `topbar-play-progress` element exists and the
    relocated JS poll targets `play_status`.
  - off-league pages (sandbox/start `app_mode`) → no `play-nav-link` / `topbar-play-*` ids.
- `matches/tests/test_league_nav_context_processor.py` (EXTEND): `league_nav` adds the 9 play keys +
  `play_displayed_season_id` / `play_league_id` on a league-prefix request with a resolvable league;
  the play keys are ABSENT off-league and on the `_fallback()` path.
- `matches/tests/test_*dashboard*` may add a pure-helper test for
  `_build_play_controls_context(league, displayed_season)` returning the 9-key dict across the
  state matrix.

### What tests assert against vs internal
- **Asserted (contract surface):** the rendered league-branch `base.html` DOM ids across the state
  matrix; the processor's play keys; the `_build_play_controls_context` return dict; the unchanged
  endpoint status codes; the dashboards' RETIRED-absent / KEPT-present ids.
- **Internal (not asserted):** the exact Bootstrap classes, the partial-vs-inline inclusion structure,
  the JS function bodies (only the DOM contract — function names + element ids — is pinned).

---

## 9. Determinism / scope-out

- **No model / migration / RNG / simulator change.** Pure view-context + template relocation; the play
  endpoints and `BatchSimulator` are untouched. No Score Calibration interaction.
- **PLAY-01 deferrals (do NOT build here):** Play→Stop swap, cancel/revoke (`AsyncResult.revoke`), live
  incremental standings/leaders during a run, cross-page resumable progress. NAV-01 ships per-page
  progress-display only.
- **ADR named (docs agent writes):** `docs/adr/0030-play-controls-relocated-to-topnav.md`.

---

## 10. Locked names (quick index)

- **Shared helper:** `matches.league_views._build_play_controls_context(league, displayed_season) -> dict`
  (9 play keys); `_build_dashboard_context` DROPS those 9 keys, KEEPS `playoff_tournament_id` + read-only body keys.
- **Processor:** `core.context_processors.league_nav` adds 9 play keys + `play_displayed_season_id` +
  `play_league_id` on the league-prefix path; ABSENT off-league / on fallback.
- **Nav DOM ids:** toggle `play-nav-link`; dropdown `topbar-play-dropdown`; items
  `topbar-play-start-season` / `-one-week` / `-two-months` / `-until-end` / `-one-week-live` /
  `-owner-evaluation` / `-next-season` / `-play-single-round` / `-play-playoffs`; progress
  `topbar-play-progress` (+ `.play-progress-spinner` / `.play-progress-label` / `.play-progress-bar`);
  error `topbar-play-error`.
- **Retired dashboard ids:** the full `{season,league}-dashboard-play-*` + `-owner-evaluation-link` +
  `-next-season-form` + `-action-button` set (§4) DELETED; `-state-badge` / `-view-bracket-link` /
  `-past-evaluations-link` / `-play-error` KEPT.
- **Reused-verbatim URL names:** `start_season`, `play_week`, `play_two_months`, `play_until_end`,
  `play_week_live`, `play_single_round`, `play_playoffs`, `play_status`, `next_season`,
  `owner_evaluation` — NO new routes, NO new views.
- **ADR:** `docs/adr/0030-play-controls-relocated-to-topnav.md` (docs agent).
