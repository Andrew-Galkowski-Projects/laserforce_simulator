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

## 3. `tick_engine.py` is a locked shallow module

- **Files** — `matches/sim_helpers/tick_engine.py` (~83 lines, three near-identical `drain_*` functions).
- **Problem** — Each `drain_*` is a 3-line list partition by tick comparison. Deletion test concentrates nothing — inlining costs ~25 lines and reads more naturally in context. The genuine depth lives one layer down in `pending_events.py`.
- **Solution** — Inline the three drains into `BatchSimulator._simulate_round`; delete `tick_engine.py`. Keep `pending_events.py`.
- **Benefits** — One fewer module to navigate; tick loop becomes one place to read. **Caveat:** the file is pinned by the SIM-09 seam contract — confirm the contract was about *names*, not the file.

## 4. LG-01 League/Season views form a sub-app that hasn't been split out

- **Files** — `matches/views.py` lines ~1800–2862: 13 endpoints (`league_list`, `league_create`, `league_dashboard`, `league_history`, `next_season`, `season_standings`, `season_schedule`, `season_dashboard_view`, `start_season`, `play_week`, `play_two_months`, `play_until_end`, `play_status`) + 20+ `_`-prefixed orchestration helpers. ~900 lines.
- **Problem** — A coherent feature stack (League/Season lifecycle) with its own URLs, pure modules, and session model — but the view layer sits in the same file as `match_list` and `head_to_head`. The interface is well-defined; the implementation is in the wrong file.
- **Solution** — Move the 13 endpoints + helpers to `matches/league_views.py`. URL configs already point at named callables — low-risk move.
- **Benefits** — `matches/views.py` drops to ~1900 lines, recognisably "match CRUD + analytics + batch jobs". **AI-navigability**: league-only changes touch one file instead of grepping 2862 lines.

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
