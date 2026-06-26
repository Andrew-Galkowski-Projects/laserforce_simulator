# Play controls relocated to a topnav `Play ▾` dropdown (single advancement surface)

**Status:** Accepted (NAV-01, 2026-06-26)

## Context

Through LG-01d / LG-01e / LG-01i / LG-02-Part2c the league-advancement controls
(Start Season / One Week / Two Months / Until End / One Week Live / Start Next
Season / owner-evaluation entry / Play Single Round / Play Playoffs) accreted as a
**per-dashboard** Play dropdown, duplicated **symmetrically** across
`templates/seasons/dashboard.html` and `templates/leagues/dashboard.html` (the
`season-dashboard-play-*` / `league-dashboard-play-*` DOM-id pairs, plus two inline
poll `<script>` blocks per template). Play was therefore reachable **only from the
dashboard**: a manager browsing Standings, Schedule, or any other league-context page
had to navigate back to the dashboard to advance the season.

Meanwhile LG-01h / LG-01k had already moved the global navigation to a **mode-based
topnav** ([ADR-0017](0017-league-context-nav-shape.md)): in league mode
`base.html` renders `⌂ | League ▾ | Team ▾ | Players ▾ | Stats ▾ | Tools ▾ | Help ▾`,
fed by `core.context_processors.league_nav` resolving the displayed League + Season
from the session (`last_league_id`) rather than from any URL kwarg. The topnav is the
natural home for a "play from anywhere" advancement surface.

NAV-01 asks for a dedicated league-mode `Play ▾` topnav dropdown. The forcing
question grilled at the seam was whether to **add** a topnav Play dropdown while
keeping the dashboard controls (parity, two surfaces) or to **relocate** the controls
to the topnav so there is exactly one advancement surface. The play **endpoints**
themselves (`start_season`, `play_week`, `play_two_months`, `play_until_end`,
`play_week_live`, `play_single_round`, `play_playoffs`, `play_status`, `next_season`,
`owner_evaluation`) already redirect / return JSON in a way that does not depend on
the request origin, so either option reuses them verbatim — the decision is purely
about *where the controls render and whether they are duplicated*.

## Decision

1. **The topnav `Play ▾` is the SOLE league-advancement surface — RELOCATE, not
   duplicate.** All advancement controls move out of both dashboards into the league
   branch of `templates/base.html`. The dashboards keep only **read-only** panels
   (standings snippet / leaders / next-round / round-count / map-config label), the
   playoff **View bracket** link (→ `tournament_detail`), the CAR-02 **View past
   evaluations** link, and the `play_error` banner. A single global advancement
   surface is the explicit goal: there is exactly one place to click Play, reachable
   from every league-context page, with no risk of two surfaces drifting out of sync.

2. **League-mode only.** The dropdown renders only in the `app_mode == "league"`
   branch of `base.html` (the LG-01k path-prefix rule: `request.path` under
   `/leagues/` or `/seasons/`). Sandbox and start modes get no Play dropdown; the
   play context keys are simply absent off-league.

3. **Advance the league's RESOLVED active/displayed Season, NOT the URL's season.**
   The nav has no `season`/`league` template variable, so the controls operate on the
   Season resolved by the existing `league_nav` chain (session `last_league_id` →
   single-League → fallback for the League; `league.active_season` → most-recent
   completed → `None` for the displayed Season). Clicking Play from a page whose URL
   names a *different* season still advances the resolved displayed Season — the
   topnav is league-scoped, not page-scoped.

4. **Shared `_build_play_controls_context` seam + a gated `league_nav` extension.**
   The play-control state (the `action_button_state` machine + the playoff-cursor
   group + `live_preview_available` + `is_career_mode`) is factored OUT of
   `matches.league_views._build_dashboard_context` into a new module-level helper
   `matches.league_views._build_play_controls_context(league, displayed_season) ->
   dict` returning **9 play keys**. `core.context_processors.league_nav` is EXTENDED
   to call that helper (lazy local import, preserving the LG-01f `core ↔ matches`
   apps-loading-cycle guard) and merge its 9 keys plus two reverse-helper ids
   (`play_displayed_season_id`, `play_league_id`) into the context — **gated on the
   league path-prefix** so the work is skipped off-league and on the `_fallback()`
   path, where the keys are absent. After the factor-out, `_build_dashboard_context`
   STOPS emitting those 9 play keys; it retains the read-only body keys plus
   `playoff_tournament_id` (the dashboard still needs it for the read-only
   View-bracket link). `top_bar_links` / `top_bar_dashboard_url` are unchanged.

5. **The dashboard Play DOM is retired.** The full `{season,league}-dashboard-play-*`
   advancement set is deleted from both dashboard templates — the play dropdown
   wrapper, the Start-Season / One-Week / Two-Months / Until-End / One-Week-Live forms,
   the owner-evaluation link, the next-season form, the Play-Single-Round /
   Play-Playoffs forms, both `-play-progress` elements, the `-action-button` wrapper
   `<span>`, and both inline poll `<script>` blocks. The new nav surface uses the
   locked `topbar-play-*` id family (toggle `play-nav-link`; wrapper
   `topbar-play-dropdown`; items `topbar-play-start-season` / `-one-week` /
   `-two-months` / `-until-end` / `-one-week-live` / `-owner-evaluation` /
   `-next-season` / `-play-single-round` / `-play-playoffs`; progress
   `topbar-play-progress`; error `topbar-play-error`). The dashboards KEEP the
   read-only `-state-badge` / `-view-bracket-link` / `-past-evaluations-link` /
   `-play-error` ids.

6. **No model change, no migration, no new routes, no new view functions.** All 10
   play endpoints are reused verbatim — they already 302 to `season_dashboard` (sync)
   or return 202 JSON (async) regardless of request origin, so a topnav submission
   needs no view tweak. Sync errors still land on the dashboard `play_error` banner
   (the endpoints' existing `_render_season_dashboard_error` → 400 dashboard re-render
   is unchanged). The only code edit beyond templates is the
   `_build_play_controls_context` factor-out and the `league_nav` extension.

7. **PLAY-01 boundary — async actions ship progress-display ONLY.** The three async
   actions (Two Months / Until End / Play Playoffs) reuse the `play_status` poll +
   `_build_play_status_response` + `_celery_state_to_job_status` verbatim; the inline
   poll JS is relocated to ONE copy in the league branch of `base.html`, reading the
   `topbar-play-progress` / `topbar-play-error` hooks and disabling the dropdown while
   a run polls. The **Play→Stop swap, cancel/revoke, live incremental
   standings/leaders, and cross-page resumable progress are DEFERRED to PLAY-01** —
   NAV-01 ships per-page progress display only.

### Rejected alternative — add parity, keep both surfaces

Keep the dashboard Play controls and ALSO add a topnav `Play ▾` dropdown (the literal
"add a dropdown" reading of the NAV-01 stub). Rejected at grilling: two advancement
surfaces would have to stay in lockstep across every future advancement change
(LG-02 playoff additions, PLAY-01 Stop swap, owner-eval entry), doubling the DOM
contract, the poll JS, and the test matrix; and a manager could start a run from one
surface while the other shows a stale Play affordance. The single relocated surface is
the cheaper, less error-prone shape — the dashboards become purely read-only, which is
also a cleaner mental model (the dashboard *shows* league state; the nav *advances*
it).

## Consequences

- **Play is reachable from every league-context page.** A manager on Standings /
  Schedule / Playoffs / any `/leagues/*` or `/seasons/*` page can advance the season
  without navigating back to the dashboard — the resolved displayed Season is the
  target regardless of the current URL.

- **Per-page processor cost.** `league_nav` runs on every request, so it now computes
  the play-control state (one `_build_play_controls_context` call + its
  `_playoff_cursor_keys` / `_resolve_live_cursor` derivations) on every league-prefix
  page render, not just on the two dashboards. The cost is bounded — the gate skips it
  entirely off-league and on the no-League fallback — and is the accepted price of a
  single global advancement surface.

- **The dashboards lose all advancement DOM.** Every test that asserted a
  `{season,league}-dashboard-play-*` advancement id is rewritten to assert that id is
  now ABSENT from the dashboard and PRESENT in the league-branch `base.html` nav; the
  kept read-only ids (`-view-bracket-link` / `-past-evaluations-link` / `-play-error` /
  `-state-badge`) stay asserted on the dashboard. The endpoint-behaviour tests
  (status codes / 302 / 202 / 409 / 5-key `play_status` JSON) are untouched — the
  views did not change.

- **The poll JS collapses to one copy.** The two duplicated inline `<script>` blocks
  (one per dashboard) become a single relocated block in the league branch of
  `base.html`, preserving the LG-01d DOM contract (`interceptAsync` / `startPolling` /
  `showProgress` / `clearPolling` / `setDropdownDisabled` / `ensureErrorEl`) but
  re-targeted at the `topbar-play-*` hooks.

- **No simulation / determinism interaction.** NAV-01 is a pure view-context +
  template relocation: the play endpoints and `BatchSimulator` are untouched, no RNG
  is consumed, and there is **no Score Calibration re-baseline obligation**.

- **PLAY-01 inherits a single surface.** Because there is now exactly one advancement
  surface, the deferred Play→Stop swap / cancel / live-incremental / cross-page-resume
  work lands in one place (`base.html`'s nav dropdown + the relocated poll JS) rather
  than having to be applied to two dashboard surfaces.
