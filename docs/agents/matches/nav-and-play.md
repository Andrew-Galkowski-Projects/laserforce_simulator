# matches: Navigation and play controls

NAV-01 `Play` dropdown, PLAY-01 cancel, LG-01i One Week (Live), ANG-06-19 / ANG-06-20 Angular play loop and menus.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## NAV-01 `Play ▾` top-nav dropdown (single advancement surface)

The league-mode `Play ▾` menu is the **SOLE** league-advancement surface (Start Season / One Week / Two Months / Until End of Season / One Week Live / Start Next Season / owner-evaluation entry / Play Single Round / Play Playoffs); the dashboards keep only their read-only panels. It advances the League's **displayed Season**, not a Season taken from the URL. Decision: [ADR-0030](../../adr/0030-play-controls-relocated-to-topnav.md). The Angular menu is ANG-06-19 below.

**Play-controls helper.** `matches.league_play_services.build_play_controls(league, displayed_season) -> dict` computes the play-control state for the **already-resolved** displayed Season (13 keys, listed in its docstring): `action_button_label` / `action_button_state` (`"none"` / `"start_season"` / `"play_next"` / `"start_next_season"`), the five `_playoff_cursor_keys(displayed_season)` values, `live_preview_available` (from `resolve_live_cursor`, LG-01i, when the Season is `active`), `is_career_mode` (`is_career_league(league)`), the three member-night keys and `active_play_job_id`. `GET /api/leagues/<id>/play-controls/` serves it.

## PLAY-01 Stop/Cancel

Adds a **cooperative between-fixture cancel** for the Season's background play runs (Two Months / Until End / Play Playoffs); the sync play paths are untouched. **NO `AsyncResult.revoke`** (a non-terminating revoke can't stop a running task; a terminating revoke would leave the half-committed Round ADR-0016 forbids — cooperative DB-flag cancel only). **No simulator/RNG touch → no Score Calibration re-baseline.** Transport is Job polling; **WebSockets/Channels are deferred** (no cancel-latency win — the safe-stop granularity is the fixture boundary, not the network round-trip — and they'd need an ASGI/Channels/deploy migration). Decision: [ADR-0031](../../adr/0031-cooperative-cancel-and-live-polling-stats.md) (reverses the ADR-0013 / ADR-0016 cancel scope-out; the `revoke` rejection stands). Seam contract: [`.claude/worktrees/play-01-seam-contract.md`](../../../.claude/worktrees/play-01-seam-contract.md).

**Model fields (2 NEW on `Season`) + migration.** `Season.active_play_job_id` (`models.CharField(max_length=255, null=True, blank=True, default=None)`) — the Celery job id of the in-flight async run; `league_play_services.enqueue_play_run` SETS it, the task CLEARS it (`= None`) in its `finally`. `Season.play_cancel_requested` (`models.BooleanField(default=False)`) — the cooperative cancel flag; `enqueue_play_run` CLEARS it (`= False`), `request_play_cancel` SETS it (`= True`), the task READS it (top + between fixtures) and never writes it. Migration `matches/migrations/0056_season_play_job_cancel.py` (dep `0055_gameround_fidelity_roster_snapshot`), exactly **2× `AddField`** (`active_play_job_id` then `play_cancel_requested`), **NO `RunPython` / `RunSQL` / backfill** (ADR-0004 — existing Seasons take `null` / `False`).

**API.** `POST /api/seasons/<id>/play-cancel/` ⇒ `{"cancel_requested": bool}` via `league_play_services.request_play_cancel(season)` (`False` and no write when idle; when Celery reports the job not running it also releases the stale `active_play_job_id`, so a crashed worker never pins the Season). `GET /api/seasons/<id>/play-jobs/<job_id>/` ⇒ `build_play_job_status`: `status` / `completed` / `total` / `cancelled` / `error` (no HTML fragments).

**Task control-flow (`matches/tasks.py`).** NEW module-level helper `matches.tasks._play_cancel_requested(season_id: int) -> bool` — `Season.objects.filter(id=season_id, play_cancel_requested=True).exists()` (single-column exists query, no instance materialised, re-read each call so a mid-run `play_cancel` POST is observed; deferred `from matches.models import Season` per the file's deferred-import precedent). Checked at **two points** in BOTH `play_season_task` (`name="matches.play_season"`) and `play_playoffs_task` (`name="matches.play_playoffs"`): (1) **task top** (after loading the Season, BEFORE the fixture/stage loop) — early-return `{"completed": 0, "total": <n or 0>, "cancelled": True}`; (2) **between fixtures** (TOP of each per-fixture iteration in `play_season_task` / per-stage drain iteration in `play_playoffs_task`, BEFORE `simulate_scheduled_round(...)` / `play_next_bracket_round(...)`) — **break** cleanly and return `{"completed": <k so far>, "total": <n>, "cancelled": True}`. The early return is a **NORMAL return** ⇒ Celery records **SUCCESS** ⇒ `celery_state_to_job_status` maps it to `complete` (no new status string); already-played Rounds stay committed (per-Round atomic commit, ADR-0016), the Season stays `active` and is resumable (re-click Play resumes via the existing `played_keys` / `find_next_playable_node` skip). In BOTH tasks' `finally:` block, clear the active-run column: `Season.objects.filter(id=season_id, active_play_job_id=self.request.id).update(active_play_job_id=None)` (`.update(...)` not `.save()` — touches only the one column, and only while it still holds this task's own id (ADR-0040); fires on success, cancel-return, AND failure so a crashed run never leaves a stale `active_play_job_id`). The cancel flag is NOT cleared here (it's cleared at the next enqueue).

**Test boundary.** Cancel halts the task after one fixture under `CELERY_TASK_ALWAYS_EAGER`, **no mocks** on the task / `simulate_scheduled_round` / `play_next_bracket_round` — task RETURNS NORMALLY, return dict carries `cancelled: True` + partial `{completed < total}`, already-committed `GameRound`s survive, Season stays `state == "active"`, `active_play_job_id` cleared (`None`) in `finally`; same shape for `play_playoffs_task` (`matches/tests/test_play_cancel.py`). The API side is `TestPlayCancel` / `TestPlayJobReadsCancelled` in `matches/tests/test_api_play_runs.py` and `matches/tests/test_play_cancel_stale_run.py`.

## LG-01i Season "One Week (Live)" play-now-and-watch replay

The manager-mode live surface in the Play menu — a **"One Week (Live)"** entry that **plays `League.current_team`'s next game immediately** (committing it), kicks off the **rest** of the matchday / bracket stage as a **background** run, and opens a read-only **replay** of the just-played game (the SIM-05 client playback engine, fed from the *persisted* event log). The locked model is **play-now-and-watch, NOT preview-then-commit and NOT server-side tick streaming**: results are **final the moment the action runs** — there is **no preview, no commit step, no discard, no retry**. Because the manager's game is committed by the existing per-Round / per-Match sim path with no injected seed, this slice is a thin **service** layer: **no model change, no migration, no engine-mechanics change, no Score Calibration re-baseline**. The **One Week (Live)** glossary term (above **Current team** in GLOSSARY.md `### League and seasons`) carries the domain language. Seam contract (historical — describes the superseded preview-then-commit design): [`.claude/worktrees/lg-01i-one-week-live-seam-contract.md`](../../../.claude/worktrees/lg-01i-one-week-live-seam-contract.md).

> **History:** LG-01i originally shipped as a **preview-then-commit** flow (an in-memory preview held as a session-pinned RNG seed, watched, then committed or discarded — with an injected-seed seam `rng_seed=` / `rng_seeds=` on the sim methods + `preview_scheduled_round` / `preview_tournament_match` / `_serialize_events_for_preview`). A follow-up redesign **replaced** it with the play-now model below; the preview methods, the injected-seed kwargs, the session pin, and the commit/discard views were all **removed**. The seam contract + the original PLAN note predate that change.

**Two watchable cursor types.** (1) **RR cursor** — `current_team`'s **single next-matchday Round**. A **bye** that matchday ⇒ no game to watch (the action just runs the week in the background, then redirects to the dashboard). (2) **Playoff cursor** — `current_team`'s **next undecided 2-Round playoff Match** (the next undecided `BracketNode` it occupies, `series_matches.count() == 0`), offered ONLY while that team is **alive** (an incomplete, non-bye node it participates in). Eliminated / not-a-participant ⇒ no live entry.

**Service (`matches/league_play_services.py`).** **`play_live(season) -> LivePlayResult`** (`kind`, `round_ids`, `job_id`) raises `SeasonNotActiveError` / `PlayInProgressError` / `NoLiveCursorError` / `BrokerUnavailableError`, all checked BEFORE anything is committed. It resolves the live cursor; an `rr_bye` cursor ⇒ no game, only the background run. **RR** plays the manager's Round ⇒ `round_ids = [game_round.id]`; **playoff** plays the manager's node via `tournament_engine.play_specific_node(node)` then reads the just-played Match's 2 `game_rounds` (the node's latest `series_matches` row) ⇒ `round_ids = [r1, r2]`. Then it **enqueues `play_season_task` with `max_matchdays=1`** (via `enqueue_play_run`) to play the rest of the matchday / drain the rest of the bracket stage in the **background** (the task skips the manager's just-played game via its played-keys / `find_next_playable_node` checks). `POST /api/seasons/<id>/play-live/` ⇒ `{kind, round_ids, job}`; the SPA replay screen is ANG-06-19 below.

**Cursor dispatch.** `matches.league_play_services.resolve_live_cursor(season) -> dict | None` and `alive_playoff_node(tournament, team) -> "BracketNode | None"`: `resolve_live_cursor` returns a `{"kind": "rr"|"playoff"|"rr_bye", ...}` descriptor (RR carries `fixture` / `season_phase` / `red_team` / `blue_team`; playoff carries `node` / `tournament` / `red_team` / `blue_team`) or `None`; `_next_matchday_to_play(season) -> (to_play, phase_by_id)` is the shared next-matchday-fixtures helper. `alive_playoff_node` selects the lowest `(_BRACKET_RANK[bracket_type], bracket_round, position)` undecided non-bye node the team occupies with no Series started (`select_related("team_a","team_b")`).

**Engine — `play_specific_node` (no injected seed).** `matches/tournament_engine.py::play_specific_node(node) -> "BracketNode | None"` (`@transaction.atomic`) is the per-Match resolve/advance body extracted from `play_next_node` (which delegates to it). It takes the node **directly** so the live-watch playoff path can play the manager's **specific** node; `play_next_node` plays the next playable one. (No `rng_seeds` — the redesign removed the injected-seed seam.)

**Play-controls key.** **`live_preview_available: bool`** (a `build_play_controls` key, NAV-01) — `True` iff `resolve_live_cursor(season)` returns a `"rr"` / `"playoff"` descriptor (NOT `None` / `"rr_bye"`); it gates the One Week (Live) menu entry.

**Scope-out (LOCKED).** No model change, no migration, no ADR, no engine-mechanics change, no Score Calibration re-baseline. No preview / commit / discard / retry. No server-side tick streaming. No watching non-manager games (only `current_team`'s game has a replay surface; the rest run headless in the background). No playoff-live when eliminated.

**Locked names (quick index).** Service `matches.league_play_services.play_live(season) -> LivePlayResult`; cursor helpers `resolve_live_cursor` / `alive_playoff_node` / `_next_matchday_to_play`; engine `tournament_engine.play_specific_node(node)` (extracted from `play_next_node`); background task `play_season_task` with `max_matchdays=1`; play-controls key `live_preview_available`; tests `TestLg01iCursorDispatch` in `matches/tests/test_league_play.py`.

## ANG-06-19 Angular Season play loop (Play menu, background runs, member nights, One Week (Live))

Spec #454, tickets #455-#457. The Angular SPA ports the `Play ▾` dropdown, the running-run progress and Stop, member nights and the Live screen.

- **API.** `GET /api/leagues/<id>/play-controls/` adds `displayed_season`: a Season ref, or null. It is the newest Season not yet completed, else the latest completed one (GLOSSARY **Displayed Season**). Only the viewset adds it, not `build_play_controls`. Tests: `API_CONTROL_KEYS` in `matches/tests/test_api_play_live.py`.
- **`PlayRun`** (`frontend/src/app/leagues/play/play-run.ts`, a root service) is the only caller of the Season play endpoints.
  - It holds the watched League's controls and runs one action at a time (I1).
  - It polls a background run's Job every 1 s (`POLL_MS`); a failed poll retries.
  - A 409 `play_in_progress` polls the Job it names (`field: "job:<id>"`). A 409 `manager_fired` goes to `/leagues/<id>/new-team`.
  - Errors show via `apiMessage`, or "Play job failed." (I3).
  - Every action targets `displayed_season` (I4).
- **Reload on change (I2).** After every success and every finished Job, `PlayRun` re-fetches the controls and ticks `changed`, and `LeagueLayout` reloads the League nav.
  - The open screen is re-created only on a `screenStale` tick: a finished Job, or an action whose landing left the URL unchanged. The outlet is wrapped in `@for (g of [generation()]; track g)` for this.
  - A screen reached on a new URL is not re-created, because the router just created it.
  - A route with `data: KEEP_ON_PLAY_CHANGE` (the `live` route) is never re-created. This means a finished rest-of-week run doesn't reset the replay.
- **Play menu** (`play-menu.ts`).
  - It sits in the Shell toolbar, on `/leagues/<n>` routes only. Its branches and labels are verbatim from the Django `topnav_play.html` it replaced.
  - While a run polls, it shows Stop, "Running… c / t" (at 600px and wider: `ROOMY` in `shared/breakpoints.ts`) and a progress bar.
  - In career mode, Start Next Season links to Owner Evaluation. Otherwise a returned draft goes to Manage Conferences.
- **Member nights** (`member-night-dialog.ts`). "Set Up Member Night…" opens a Site picker: an "All Sites" box, one box per Site with its player count, and the empty state "No Site has enough players (need 12+).".
- **One Week (Live)** (`seasons/season-live/`).
  - Route `/leagues/:id/seasons/:seasonId/live?rounds=<id>[,<id>]`, titled `One Week (Live) - <app title>`.
  - It embeds `<app-playback [roundId]>`, with a Round 1 / Round 2 toggle for a two-Round game.
  - Empty state: "No live game to watch — start one from the Play menu.".
  - `LivePlayKind` is `'rr' | 'playoff' | 'member_night'`. A bye returns `rr` with no Rounds and lands on the Season dashboard.
- **Spec helpers.** `releaseLeagueChrome()` and `releasePlayControls()` live in `leagues/testing/leagues-loads.ts`. Call them right after the navigation.

## ANG-06-20 Angular landing page and Tools / Help menus

Spec #460, ticket #461. Ports the Django landing page and Tools / Help menus. Frontend only.

- **Landing** (`frontend/src/app/landing/`, `Landing`) is the Shell's `''` child, titled
  `APP_TITLE`. Its h1 is `Laserforce Simulator`, matching the toolbar and the tab rather than
  Django's "Laserforce Manager".
  - Mode cards: Sandbox (`#mode-card-sandbox`, links to `/teams`), Single-player League
    (`#mode-card-league`, links to `/leagues`), and Multiplayer (`#mode-card-multiplayer`, a
    `<div aria-disabled="true">` with a "Coming soon" badge, never a link).
  - In Progress (`#in-progress-leagues`) shows one card per League from `GET /api/leagues/?state=active`
    (`listAll`, newest first, scoped by `owned_queryset`). Each card has the name, `Season: <name>` or
    `No active season`, and the `league-state` badge, and links to `/leagues/:id`. The section is
    hidden when the list is empty.
  - A failed load shows `apiMessage(error, GENERIC_ERROR_MESSAGE)` and Retry. The mode cards still
    render.
- **`HOME_URL` is `/`** (`auth/auth.guard.ts`). Sign-in, register, a signed-in visit to
  `/login` or `/register`, and the `'**'` redirect all land on the landing page. An anonymous `/`
  goes to `/login?returnUrl=%2F`.
- **Shell.** The title is an `<a routerLink="/">`. Tools then Help sit after the Play menu, on
  every URL. Their items come from `shell/stub-menus.ts` (`TOOLS_MENU`, `HELP_MENU`, in Django's
  order). Each item is a disabled `mat-menu-item` holding `app-soon-badge`, never a link
  (spec #409 I1). `.shell__soon-item` drops Material's 0.38 disabled fade, so the items read like
  the League nav's Soon entries.
- **No shared "Coming soon" route** (owner, grill 2026-10-31). The four League Soon entries
  (Trade, Trading Block, Prospects, Hall of Fame) stay disabled spans.
- **`SoonBadge` moved to `shared/soon-badge/`.** Its badge style is the `badges.soon` mixin
  (`shared/_badges.scss`), which the Multiplayer badge shares.
- **Spec helpers.** A spec that lands on `/` while signed in releases the landing page's GET with
  `releaseLeaguesLoads()` (`leagues/testing/leagues-loads.ts`).
- **Out of scope:** any backend change, the
  registry's `blocker` text, Multiplayer, any working Tools item, and sidenav changes.
