# Architecture Improvement Candidates

Deepening opportunities surfaced by `/improve-codebase-architecture` on 2026-05-28. Each entry names the **Module** in question, where its **Interface** is leaking or **shallow**, and what a deeper version would buy. Vocabulary from [the skill's LANGUAGE.md](../../.claude/skills/improve-codebase-architecture/LANGUAGE.md) — Module, Interface, Implementation, Depth, Seam, Adapter, Leverage, Locality.

Domain vocabulary follows [CONTEXT.md](../CONTEXT.md).

---

## 1. Round analytics helpers trapped in `matches/views.py` — **COMPLETED (#90)**

Seam contract: [`.claude/worktrees/round-analytics-seam-contract.md`](../.claude/worktrees/round-analytics-seam-contract.md). Scope widened from "extract `_player_row` + `_team_totals` + missile summary + comparison helpers" to **also flip `game_round_detail.html` to render from the same dict the PDF uses**, crystallising the new domain term **Round scoreboard** (CONTEXT.md, `### Analytics and review`). Three new pure modules (`matches/round_summary.py` — 28-key `PLAYER_ROW_KEYS`, `matches/round_comparison.py`, `matches/missile_log_stats.py`), three new pure-unit test files, two custom template filters (`count_survivors`, `is_eliminated`) deleted. Behaviour-neutral; no migration, no ADR.

- **Files** — `matches/views.py` lines ~500–950: `_cumulative_team_points`, `_player_row`, `_team_totals`, `_stat_values`, `_player_stat_deltas`, `_shared_team_ids`, plus the missile-log and movement-heatmap aggregators. ~240 lines of pure aggregation.
- **Problem** — Shallow adapter functions at the wrong seam. They live in the view because each is called from one URL, but they have no HTTP shape — they read `GameEvent` / `PlayerRoundState` rows and return dicts. `h2h_stats.py` and `standings.py` already prove the pattern: pure module receives query results, returns aggregated rows. Round-detail and compare-rounds skipped that step.
- **Solution** — Extract `matches/round_analytics.py` and `matches/round_comparison.py`. View becomes ORM-fetch → pure module → render.
- **Benefits** — **Locality**: a future second consumer (PDF, REST, dashboard widget) reuses the aggregator instead of copying. **Test surface**: pure-function tests with `list[dict]` fixtures, no `RequestFactory` ceremony. **Leverage**: the **Round report** PDF (`pdf_report.py`) and the HTML detail view could share per-player table logic.

## 2. `role_benchmarks_cache.py` mixes ORM materialisation with caching — **COMPLETED (#91)**

Original framing was inaccurate: the module is 309 lines and does **three** things — ORM scan, `_MvpAdapter` materialisation (so `calculate_mvp` can run without ORM access), and caching. The "push cache to view layer" idea doesn't apply because the scan is unavoidable (every consumer needs the full materialised result), so the cache structurally belongs behind a single function.

**Solution** — Split the materialisation out into a new `teams/role_benchmarks_orm.py` (Django but uncached). The cache module shrinks to ~140 lines and owns *only* the caching policy: version key, key shape, `cache.get_many`, `transaction.on_commit` invalidation. ORM coupling is intrinsic to the role-benchmark surface (the population must scan every `PlayerRoundState`); making it explicit in module structure is the depth win.

- **Files (before)** — `teams/role_benchmarks_cache.py` (309 lines), `teams/signals.py` (unchanged), `matches/simulation.py` lazy hook (unchanged).
- **Files (after)** — `teams/role_benchmarks_orm.py` (NEW, ~180 lines: `_MvpAdapter` / `_MvpGameRound` / `_PLAYER_STATE_FIELDS` / `compute_benchmarks_uncached`), `teams/role_benchmarks_cache.py` (~140 lines: version key, key helpers, `invalidate_role_benchmarks`, `get_all_benchmark_data`, `get_role_benchmark_samples`).
- **Benefits** — **Locality**: cache policy (key, version, on_commit) sits next to its callers; ORM scan + adapter live in their own module. **Leverage**: a future uncached caller (admin diagnostic, CLI script, fixture) imports `compute_benchmarks_uncached` directly with no cache-backend dependency. **Test surface**: ORM tests cover the materialisation in isolation; cache tests cover cache-policy invariants without touching the scan.

## 3. `tick_engine.py` is a locked shallow module — **COMPLETED**

Inlined the three `drain_*` functions into `BatchSimulator._simulate_round` (six lines added at the three call sites — each `drain_X(pending, second)` becomes a two-line list-comprehension partition); deleted `matches/sim_helpers/tick_engine.py` (83 lines); dropped the import block from `simulation.py`. The SIM-09 caveat resolved: no actual seam contract file exists — only doc references describing the module, which are now removed. `sim_helpers/CLAUDE.md` and `matches/CLAUDE.md` updated accordingly. Behaviour-neutral; no migration, no ADR.

- **Files (before)** — `matches/sim_helpers/tick_engine.py` (83 lines), `matches/simulation.py` (3 `drain_*` call sites + a 5-line import block).
- **Files (after)** — `matches/sim_helpers/tick_engine.py` deleted; `matches/simulation.py` carries the partitions inline (6 lines added, 5-line import block removed; net ≈ +1 line at the simulator, −83 from the package).
- **Benefits** — One fewer module to navigate; the tick loop is now one place to read. **Locality** of the partition logic next to the resolution logic it feeds.

## 4. LG-01 League/Season views form a sub-app that hasn't been split out — **COMPLETED**

Moved 21 endpoints (the LG-01..LG-01f stack — `league_list`, `league_create`, `league_dashboard`, `league_history`, `next_season`, `season_standings`, `season_schedule`, `season_dashboard`, `start_season`, `play_week`, `play_two_months`, `play_until_end`, `play_status`) plus 8 `_`-prefixed orchestration helpers (`_compute_team_overall`, `_build_dashboard_context`, `_pick_displayed_season`, `_coerce_per_page`, `_coerce_page`, `_build_league_sidebar_links`, `_build_history_row`, `_render_season_dashboard_error`, `_build_play_status_response`) into the new `matches/league_views.py` (~1144 lines). `matches/views.py` shrank from 2796 → 1679 lines (−1117). URL configs repointed to `league_views.*`; URL names unchanged. `_celery_state_to_job_status` stays in `views.py` (shared with batch/save status paths) and is imported by `league_views.py`. Behaviour-neutral; no migration, no ADR.

- **Files (before)** — `matches/views.py` (2796 lines).
- **Files (after)** — `matches/views.py` (1679 lines, "match CRUD + analytics + batch jobs"), `matches/league_views.py` (NEW, 1144 lines).
- **Benefits** — **Locality**: league-only changes touch one file instead of grepping 2800 lines. **AI-navigability**: the file's purpose is recognisable at a glance. **Test surface**: unchanged — test patches re-pointed at `matches.league_views.*`.

## 5. Collapse `PlayerRoundState` counters into a derived view

- **Files** — `matches/models.py` `PlayerRoundState` (~390 lines, 30+ counter fields: `tags_made`, `times_tagged`, `points_scored`, `times_tagged_in_reset_window`, …); writers in `sim_helpers/shot.py`, `down.py`, `combat.py`, `_flush_to_db` in `simulation.py`.
- **Problem** — Counters are denormalised duplicates of `GameEvent` aggregates. The **Interface** to "how many tags did this player make" is two: read the counter, or sum events. Two interfaces over one fact ⇒ invariant enforced by convention. The **Player head-to-head record** already runs into this — it sums `GameEvent.actor` rows because the counter has no per-opponent split.
- **Solution** — Introduce a `RoundStatsView` (pure function, possibly cached on `GameRound` JSON) that materialises all counters from events at round close. `PlayerRoundState` keeps only structural fields (role, team_color, final_lives sentinel).
- **Benefits** — One source of truth (event log), one **Interface** for analytics. **Locality**: new counters added by emitting an event, not updating model fields at every writer. **Risk:** largest refactor on the list; affects every analytics consumer.

## 6. Split `simulation.py` into round-loop / entrypoints / persistence

- **Files** — `matches/simulation.py` (2473 lines): `BatchSimulator` class + `_simulate_round`, `simulate_match`, `simulate_single_round_detailed`, `simulate_scheduled_round`, `run`, `run_incremental`, `save_games`, and the 400-line `_flush_to_db`.
- **Problem** — `BatchSimulator` is a container, not an object — owns no instance state, every call site spells `BatchSimulator().simulate_match(...)`. `_flush_to_db` is a serialiser, not part of simulation.
- **Solution** — Split into `simulation/round_loop.py` (the tick loop, pure), `simulation/entrypoints.py` (the `simulate_*` public functions), `simulation/persistence.py` (in-memory → ORM translator). Keep `BatchSimulator` only if the class earns its keep — otherwise functions.
- **Benefits** — **Depth**: round loop becomes a deep module with a small interface (input state → output state + events). **Locality** of persistence: serialisation lives next to model definitions. **Test surface**: round-loop tests no longer stub `_flush_to_db`.
