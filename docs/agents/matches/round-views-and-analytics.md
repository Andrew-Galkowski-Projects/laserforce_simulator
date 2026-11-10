# matches: Round views and analytics

Forms, RES-04 heatmap, RV-01 compare, RV-02 highlights, RV-03 PDF report, RV-05 PDF charts, HX-03 / HX-04 head-to-head.
Moved verbatim from `laserforce_simulator/matches/CLAUDE.md`; the app index there points here.

## Forms (`matches/forms.py`)

**`MatchSetupForm`** and **`SingleRoundSetupForm`** both include an optional `arena_map` `ModelChoiceField` (empty_label="No map (3-zone fallback)"). The queryset is populated in `__init__` via `_maps_with_confirmed_config()` which returns only simulation-ready `ArenaMap` objects: at least one confirmed `MapZoneConfig` plus both a red and a blue `MapBaseConfig` (UXP-10 — the same helper feeds every League map pool / rotation picker). Rounds without a map fall back to the existing 3-zone logic.

`matches.services.create_sandbox_match` / `create_sandbox_round` pass `arena_map` keyword-only to the simulator and re-raise its `ValueError` (missing config, missing base, or missing sight lines) as `MatchSetupError`, which the create API turns into a 400.

## Round-playback map

`matches/playback.py::build_playback_map(game_round) -> dict | None` (served in the `map` key of `GET /api/rounds/<id>/playback/`, see **ANG-06f Playback** below) returns `None` for a map-less round (3-zone fallback). Per player it ships `spawn` + `moves = [[tick, polyline], …]` where the polyline is the **exact cells the player walked that Advance**: it prefers the persisted `GameEvent.metadata["route"]` (MOVE-01, see the **`GameEvent`** paragraph above) and falls back to a fresh `astar_path(start, end)` reconstruction only for pre-route rounds. The route is captured during simulation on the transient `PlayerState.movement_routes` (in lockstep with `movement_trail`; see [`sim-helpers.md`](sim-helpers.md#player_statepy)) and written by `_flush_to_db`. Tested by `matches/tests/test_playback_map.py`. **No model change / no migration** — `movement_routes` is transient and `metadata["route"]` rides on the existing `GameEvent.metadata` JSON; the MOVE-01 "no route list / flat scalars" assertion in `test_map.py` / `test_batch_sim.py` is deliberately superseded (the canonical `route` key is now allowed, alternative encodings still forbidden).

## RES-04 movement heatmap

Per-round **Cell occupancy** snapshots ship via two surfaces driven from a single persisted form on `GameRound.cell_occupancy_json` (see the **`GameRound`** paragraph above for the field, migration, and null-on-map-less semantics).

**Pure reconstruction.** `matches/sim_helpers/cell_occupancy.py::reconstruct_cell_occupancy(movement_trail, spawn_cell, round_ticks, eliminated_at, adj, elevation_data=None) -> dict[tuple[int,int], int]` is the algorithmic seam — **pure Python, no Django imports, no I/O, consumes no RNG**. It walks the player's **Movement trail** (GLOSSARY.md) crediting the stationary slice `[cursor_tick, ts)` before each Advance to `cursor_cell`, the Advance itself splits 1 tick evenly across `1 + len(astar_path(start, end, adj, elevation_data))` cells (the `+1` is the start cell — `astar_path` returns the route excluding start, including end), then the trailing stationary slice `[cursor_tick, end_tick)` after the final Advance, with `end_tick = min(round_ticks, eliminated_at)`. The float accumulator is cast to `int` via banker's-rounded `round()` and cells whose final value rounds to `0` are **omitted** — the output is sparse, tuple-keyed, int-valued. The caller stringifies (`(r,c) → "r,c"`, `player_id → str(player_id)`) for JSON; the function stays in the pure tuple/int domain so it is testable without JSON round-trip.

**`_flush_to_db` integration.** Sits in `flush_to_db` (`matches/simulation/persistence.py`; called by `BatchSimulator._flush_to_db` in `matches/simulation/entrypoints.py`) **immediately after** the per-player `movement` `GameEvent` flush block and **before** the final `return game_round`, **gated on `movement_ctx is not None`** (no map ⇒ no A* adjacency ⇒ the column stays `null`). Spawn cell sources from `p.movement_trail[0][0]` when the trail is non-empty, else `(p.cell_row, p.cell_col)`; players with neither (defensive — e.g. `p.player_id is None`) are skipped. The snapshot is persisted by a **second** `game_round.save(update_fields=["cell_occupancy_json"])` — the earlier `_flush_to_db` save triggers winner calculation; the second save is intentional and cheap. `movement_ctx.get_adjacency()` and `movement_ctx.elevation_grid` are read off the **existing** `MapContext` accessors — **no new `MapContext` accessor** and **no new `_flush_to_db` kwarg** (`movement_ctx` is the already-existing SIM-09 kwarg). RES-04 does **not** add a per-tick cost — the reconstruction runs once per player at round end.

**Per-round payload.** `matches.services.build_heatmap(game_round)` (upgrades the round to `full` fidelity) backs `GET /api/rounds/<id>/heatmap/`: `cell_occupancy_json`, `player_roster` (list of `{id, name, role, team_color}` dicts ordered red-then-blue, role-then-name), `has_map`, `arena_map`, `zone_size`. Filtering by player / role / team is **client-side** (see **ANG-06-10** below); no server round-trip per filter change. The original PLAN.md "per-zone bar chart fallback" is **dropped** because MAP-01..09 are complete (the 3-zone fallback survives only as a compatibility shim for rounds the user explicitly creates without a map — RES-04 simply does not render a heatmap there).

**Multi-round aggregate** lives **inside the map editor** as a mode alongside Zones & Bases and Sight Lines (see [`core/CLAUDE.md`](../../../laserforce_simulator/core/CLAUDE.md)). `core.services.aggregate_map_heatmap` backs `GET /api/maps/<id>/heatmap/` (`HeatmapParamsForm`: required `zone_size` int, optional `team_color` ∈ {`"red"`, `"blue"`}); **only rounds visible to the requester** — `owned_game_round_q(request.user)`: own + Unmanaged + own / Unmanaged Match / League — are aggregated, with `round_count` the scoped count. Filtering is **server-side on `team_color` only** — the service joins `GameRound.cell_occupancy_json` against `PlayerRoundState.team_color` to drop non-matching players, then sums the remaining `"r,c"` → ticks entries. Response shape `{"cell_occupancy": {"r,c": int}, "zone_size": int, "rows": int, "cols": int, "round_count": int}` with cells whose final sum is `0` omitted (matches the per-round file format). The editor exposes **no** per-player or per-role filters — those are single-round only.

**Single-source contract.** Per-player JSON is the only persisted form; team-color / role / per-player aggregates are **always derived at read time** via `PlayerRoundState` (the map aggregate server-side, the round heatmap client-side via `player_roster`). Same seed + Orientation + rosters + map ⇒ identical `cell_occupancy_json` — reconstruction consumes no RNG and reads only the deterministic movement trail + A* route, so the SIM-07/08 contract extends to the new field. **No simulation behaviour change** → **no Score Calibration re-baseline obligation**. Locked names (model field, migration, pure function, test files) are pinned by the seam contract at [`.claude/worktrees/res-04-seam-contract.md`](../../../.claude/worktrees/res-04-seam-contract.md). **No ADR** — decisions are reversible (the column is a `JSONField` add; the cache is regenerable). **Scope-out:** no backfill, no time-window slicing, no PNG/PDF/CSV export, no JS unit tests, no new `MapContext` accessor.

## RV-01 round comparison

`GET /api/rounds/compare/?round_a=&round_b=` compares two owned `GameRound`s that share at least one team (`CompareRoundsParamsForm`: both ids required, `round_a == round_b` ⇒ 400; a missing / foreign id ⇒ 404; no shared team ⇒ 400). The payload is `{round_a, round_b, stat_keys, team_ids, points_series, deltas}`, built by `matches.services.build_round_comparison(round_a, round_b, user)` over the pure module `matches/round_comparison.py`. **No model change, no migration** — pure read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. Tests: `matches/tests/test_api_compare_rounds.py`.

**"Shares a team" is Side-agnostic Team-id overlap:** `services.shared_team_ids(round_a, round_b)` = `{a.team_red_id, a.team_blue_id} & {b.team_red_id, b.team_blue_id}`, sorted — a team that played red in round A and blue in round B still pairs (Orientation-independent). The delta rows pair `PlayerRoundState` **by `player_id`**, so the same human is compared to themselves across both rounds regardless of Side.

**Pure helpers** (`matches/round_comparison.py`):
- `player_stat_deltas(rows_a, rows_b) -> list[dict]` — one row per player on a shared team, shape `{player_id, name, role_a, role_b, side_a, side_b, stats: {<stat>: {a, b, delta}}, cells}` where **`delta = b - a`**; the whole `delta` (and the absent side's `a`/`b`) is **`None` when that player has no `PlayerRoundState` on one of the rounds** (joined the roster between rounds). Rows are ordered by name.
- `cumulative_team_points(events) -> list[list]` — `[[tick, cum_points]]` running totals from that team's `(timestamp, points_awarded)` pairs (fetched by `services._cumulative_team_points`), **coalescing the nullable `GameEvent.points_awarded` to 0** so non-scoring events don't break the cumulative sum.

The delta table is the **extended** stat set in a fixed key order, `COMPARE_STAT_KEYS` (served as `stat_keys`): `points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count`. **`mvp` reuses the existing `PlayerRoundState.get_mvp` property** and **`accuracy` reuses the existing `get_accuracy` property** (RES-01) — neither is recomputed.

The **Points-Over-Time** overlay is `points_series` = one entry **per shared team** `{team_id, team_name, a: [[tick, cum]], b: [[tick, cum]]}`. Timestamps are raw **ticks** through the JSON boundary; any mm:ss display divides by 2 client-side (TIME-01). The SPA screen is described in **ANG-06-9** below.

## RV-02 highlights

Per-round **Highlight** auto-flagging (GLOSSARY.md) persisted to `GameRound.highlights_json` (see the **`GameRound`** paragraph above for the field, migration `0027`, and null-no-backfill semantics) and served as `highlights` (`highlights_json or []`) by `GET /api/rounds/<id>/timeline/` (see **ANG-06e** below).

**Pure builder.** `matches/sim_helpers/highlights.py::build_highlights(events, result, *, round_ticks, name_by_id, team_by_id) -> list[dict]` is the detection seam — **pure Python, no Django imports, no I/O, consumes no RNG**. `events` is the **in-memory event buffer** (NOT ORM rows); `result` is the round result dict; `round_ticks` is `TICKS_PER_ROUND` (1800); `name_by_id` / `team_by_id` are id→name / id→team maps passed in so the builder emits NAME strings + a per-event team while staying pure (an absent id resolves to `None`). Returns a flat list sorted by tick ascending, each record the fixed 7-key shape `{kind, tick, team, actor, target, points, label}`. **Six kinds:** `nuke_detonation` (discriminated by `event_type=="special"` + `metadata["targets"]` + `points_awarded==500`; the activation row — `points==0` & `metadata["fires_at"]` — is **not** flagged), `nuke_cancelled`, `medic_reset`, `first_elimination` (first elimination by tick → one record), `team_elimination` (from `result["red_eliminated"]`/`["blue_eliminated"]` + `["eliminated_at"]`, **NOT** the `dead` event), `scoring_burst` (the **Scoring burst** — forward `[t, t+60)` 60-tick window with maximum single-team gross points → one record; none when the round had no point events). **Base captures are deliberately not a highlight kind** (routine point-grabs — left to the events-log timeline); their `points_awarded` still feed the `scoring_burst` sum.

**`record_down` chokepoint.** The shared life-loss chokepoint is `sim_helpers.down.record_down(player, tick, ctx)` — see the **Shot resolver consolidation** section below for the lift. It emits `nuke_cancelled` at the down/disarm tick for a Commander with a live pending nuke and sets `PendingNuke.cancel_logged=True` while **leaving the nuke in `pending_nukes`** (MECH-05 reaction/drain unchanged; drain-else emits only when `not cancel_logged`), and increments the transient `PlayerState.down_chain_count` **before** stamping `last_downed_time` when `not is_active_at(second)`, emitting `medic_reset` once when the chain reaches 2 for a `medic`. (Pre-consolidation this was `BatchSimulator._record_down`, a static→instance method reading `self._event_log` / `self._pending_nukes` stashed in `_simulate_round`; both the method and the self-stash are gone.)

**`_flush_to_db` integration.** `build_highlights` is invoked in `flush_to_db` (`matches/simulation/persistence.py`; called by `BatchSimulator._flush_to_db` in `matches/simulation/entrypoints.py`) **after** the RES-04 `cell_occupancy_json` block and **before** the final `return`: it builds `name_by_id` / `team_by_id` from the red+blue players, calls the builder, sets `game_round.highlights_json`, and persists via a **second** `game_round.save(update_fields=["highlights_json"])` (mirrors the RES-04 second-save pattern). Populated on **every** save path; no per-tick cost.

**No re-baseline.** The builder consumes no RNG and the cancelled nuke is left in the pending queue, so seeded games are byte-identical — **no Score Calibration re-baseline obligation**. Domain terms (Highlight, Scoring burst, Medic reset chain, Nuke cancellation) are in [GLOSSARY.md](../../../GLOSSARY.md); the nuke-cancelled-event decision is in [ADR-0012](../../adr/0012-nuke-cancelled-event.md).

## RV-03 round report PDF

A single-**Round** PDF export — the **Round report** (GLOSSARY.md) — at `GET /api/rounds/<id>/report/` (the DRF `report` action), generated server-side with **ReportLab** (programmatic PDF; chosen over WeasyPrint to avoid an HTML-template dependency ahead of the planned Angular migration — added as the unpinned `reportlab>=4.0` line to `laserforce_simulator/requirements.txt`). Provenance is driven by the new `GameRound.is_simulated` flag (see the **`GameRound`** paragraph above for the field, migration `0028`, and no-backfill semantics).

**Pure builder.** `matches/sim_helpers/pdf_report.py::build_round_report(report_data: dict, *, watermark: bool) -> bytes` is the render seam — **pure Python, no Django/ORM imports, no settings access, no file I/O beyond an internal `io.BytesIO` buffer, consumes no RNG**. It returns `b"%PDF"`-prefixed non-empty bytes and draws a diagonal "[Simulated]" watermark on **every** page via a ReportLab `onFirstPage`/`onLaterPages` page callback, gated by the keyword-only `watermark` bool (`watermark=False` ⇒ no watermark drawn). The builder **never** touches the ORM — `matches.services.build_round_report_data(game_round)` assembles `report_data` and hands it over; that dict is the only thing crossing the seam.

**Watermark testable seam.** ReportLab **compresses page-content streams**, so the literal `[Simulated]` text is **not reliably greppable** in the output bytes. The watermark decision is therefore factored into a tiny pure helper `should_watermark(is_simulated: bool) -> bool` that the page callback consults and tests assert on **directly** — `should_watermark(True) is True`, `should_watermark(False) is False` — **without parsing compressed PDF streams**. The byte-level tests assert only the `b"%PDF"` prefix on both branches; they never check for watermark-text presence.

**Endpoint.** The `report` action resolves the owned round (404 otherwise), calls `build_round_report(build_round_report_data(game_round), watermark=game_round.is_simulated)`, and returns a raw (never enveloped) `HttpResponse(pdf_bytes, content_type="application/pdf")` with `Content-Disposition: attachment; filename="round-<id>-<red_slug>-vs-<blue_slug>.pdf"` (`services.round_report_filename` owns slugification; only the filename shape is pinned). Scoreboard ordering is `-points_scored, role, player__name`, the `round_scoreboard` order.

**`report_data` content (frozen seam).** Round-summary block: `round_id`, `round_label` (`f"Round {n} of 2"` if the round has a `match` else `"Single Round"`), `date_played` (pre-formatted; the builder prints verbatim), `map_name` (`None` ⇒ builder **omits** the map line), team names / points / `*_eliminated` flags, and `winner_name` (`None` ⇒ builder prints `"Tie"`). The **per-player table is the RV-01 stat set, single-sourced** — same fixed key order `points_scored, mvp, tags_made, times_tagged, accuracy, final_lives, resupplies_given, missiles_landed, specials_used, follow_up_shots, reaction_shots, combo_resupply_count` — where **`mvp` reuses the `PlayerRoundState.get_mvp` property** and **`accuracy` reuses the `get_accuracy` property** (NOT the plain `accuracy` property that delegates to `stat_for_simulation`), neither recomputed. A **per-team resource summary** (`red_totals` / `blue_totals`) sums `resupplies_given` / `missiles_landed` / `specials_used` / `tags_made` over the team's players, derives `survivors` (count with `final_lives > 0`), and reads `team_points` from `GameRound.red_points` / `blue_points` (the team-level field, **not** summed from players).

**Edge cases.** An empty / early-eliminated round (all-zero stats) renders with zeros and **no crash**; a map-less round (`map_name=None`) omits the map line; a tie (`winner_name=None`) prints "Tie".

**Scope-out.** Charts/graphs were deferred to RV-05 (shipped; see **RV-05 round report charts** below). The `is_simulated=False` write — the **Actual game log** `.tdf` import that pairs a Round to a real game — is deferred to **IMPORT-01** (the first writer of `is_simulated=False`; the provenance contract was locked at RV-03 planning). **RV-03 runs no simulation and consumes no RNG** → no SIM-07/08 contract interaction and **no Score Calibration re-baseline**. Locked names are pinned by the seam contract at [`.claude/worktrees/rv-03-seam-contract.md`](../../../.claude/worktrees/rv-03-seam-contract.md). **No ADR** — decisions are reversible (a `BooleanField` add + a pure render module).

## RV-05 round report charts

Spec #283 (tickets #284 series port, #285 renderer, #286 integration). The RV-03 PDF ends with the
four events-page charts: shots, lives, points and SP over time, per player. Same endpoint
(`GET /api/rounds/<id>/report/`); no model change, no
migration.

- **Series port.** `matches/sim_helpers/round_series.py::build_round_series(players, events) -> dict`
  is a one-for-one Python port of the old Django events page's RES-02b series build. Inputs are
  the `_round_players_data` / `round_playback_payload` rows. Output: `players` (`{id, name, team}` in input order) plus `shots`,
  `lives`, `points`, `sp`, each keyed by `str(player id)` with `(seconds, value)` tuples. Every player
  starts at second 0 with shots `ss`, lives `sl`, points 0, SP 0; a point is appended only on change;
  actor, target and `targets[]` snapshots each feed their own player; seconds are ticks / 2. Pure, no
  Django. Tests pin the port to the RES-02b metadata cases.
- **Renderer.** `matches/sim_helpers/pdf_charts.py`: `CHARTS` (four `ChartDef`s in card order, titles
  as the page cards; SP fixed 0..99 with a dashed cap at `SP_CAP = 99`; the rest begin at zero),
  `render_chart_png(chart_key, series, *, dpi=150) -> bytes` (stepped `steps-post` lines, the page's
  hues via `player_colour(team, index, team_size)`: red `hsl(350 + 40*i/n, 70%, 45%)`, blue base 210,
  a legend, x in seconds; empty series gives titled empty axes) and `chart_flowable(png, width) ->
  Image` (aspect kept). matplotlib's object-oriented API only (`Figure` + Agg canvas), never `pyplot`,
  never `matplotlib.use`. Reusable by HX-03.
- **Report.** `build_round_report_data` gains `chart_series` (the series over the round's rows,
  movement rows excluded since they carry no snapshots); `build_round_report` keeps its signature
  and, only when `chart_series` is not `None`, adds a page break after the resource tables and the
  four charts two per page at the content width. `pdf_report` imports `pdf_charts` lazily inside the
  chart section, so importing `pdf_report` does not load matplotlib.
- **Fidelity.** The DRF `report` action calls
  `BatchSimulator().ensure_fidelity(game_round, "full")` before building, as `timeline` does
  (this replaces RV-03's "the report does not upgrade" rule). The builder itself still does not
  upgrade. A round with no events after that yields four empty-axes charts.
- **Row builders moved.** `_round_players_data` and `round_playback_payload` now live in
  `matches/services.py`. `_round_players_data` orders by `id` so legend order and hues are stable.
- **Tests.** `test_round_series.py`, `test_pdf_charts.py` (both with the subprocess no-Django check;
  the renderer also checks `pyplot` stays unloaded), `test_round_views.py::TestBuildRoundReport`
  (image count by the
  `/ColorSpace /DeviceRGB` byte marker and images per page, via `pdf_image_count` /
  `pdf_images_per_page` in `matches/tests/conftest.py`, no PDF parser), `test_services.py`,
  and the DRF `test_api_round_analytics.py::TestRoundReport`.
- **Dependency.** `matplotlib>=3.8` in `laserforce_simulator/requirements.txt`.

## HX-03 head-to-head

`GET /api/teams/head-to-head/?team_a=&team_b=&provenance=&from=&to=` aggregates the **Head-to-head record** (GLOSSARY.md, `### Analytics and review`) between two **Teams** — W/L Match record, W/L Round record, mean signed score margin from team_a's perspective, per-team mean survivors, the most impactful player per team, a per-map breakdown table, two chart series (margin over time, cumulative W/L), and a unified reverse-chronological detail list of Matches + standalone Rounds. `TeamH2HParamsForm` (`matches/api_forms.py`): `team_a` / `team_b` required, the same id twice ⇒ 400, `provenance` ∈ `all` / `real` / `sim` (default `all`), optional `from` / `to` `YYYY-MM-DD`; a missing / foreign Team ⇒ 404. Built by `matches.services.build_team_h2h(team_a, team_b, *, provenance, date_from, date_to)` over the pure aggregation module `matches/h2h_stats.py`. **No model change, no migration** — read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. An empty history returns all-zero aggregates. Tests: `matches/tests/test_api_h2h.py`.

**Corpus.** Every H2H **Match** (`{team_red, team_blue} == {team_a, team_b}`, `is_completed=True`) PLUS every standalone H2H **Round** (no `Match` parent), under **Side-agnostic Team-id pairing** (Orientation-independent — a Team that played red in one game and blue in another still pairs by Team id, matching the RV-01 "shares a team" Side-agnostic intersection precedent). The Match record applies only to the Matches subset; the Round record + score margin + avg survivors + most-impactful-player + per-map breakdown + both charts apply across the unified basket (the 2 Rounds of each H2H Match + every standalone H2H Round). Scores are normalised by the service to team_a's perspective before crossing the seam (flipping red/blue when `game_round.team_red_id != team_a_id`).

**Pure module** `matches/h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`). Exposes **eight** public functions: `compute_match_record` (W/L/T over H2H Matches; `winner_team_id` mapped to W/L/T; unknown winner id defensively counts as tie), `compute_round_record` (W/L/T per Round; team_a vs team_b score comparison, equal = tie), `compute_score_margin` (mean signed `team_a_score − team_b_score` per Round; empty ⇒ `0.0`, no div-by-zero), `compute_avg_survivors` (per-team mean `count(final_lives > 0)` per Round; two numbers — team_a and team_b), `top_impactful_per_team` (top cumulative-MVP player per team; tiebreaker lower `player_id`; team with no rows returns `None` for that side), `compute_per_map_breakdown` (one row per `arena_map_id` observed including the single `None` row labelled `"No map (3-zone)"`; sorted by games desc with `arena_map_id` asc tiebreaker and `None` last), `margin_series` (chart data — signed margin per Round chronologically sorted by `(date_played, round_id)`, `list[list]` not `list[tuple]` for JSON serialisation), `cumulative_wl_series` (chart data — cumulative `team_a_wins − team_b_wins` Round-level chronologically; ties don't move the running diff). Every signature returns zeros (or `[]` / `None`) on empty input, never raises. The "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` mirrors the HX-01 / HX-02 / RES-04 / RV-03 precedent (walks `sys.modules` from a fresh subprocess after `import matches.h2h_stats`).

**Seam.** Three flat dict lists cross service ↔ pure-module: `matches_list` (one entry per H2H Match — `match_id`, `winner_team_id` (`None` = tie), `date_played`, `is_simulated`), `rounds_list` (one entry per Round in the unified basket, **already normalised from team_a perspective by the service** — `round_id`, `date_played`, `team_a_score`, `team_b_score`, `team_a_survivors`, `team_b_survivors`, `match_id` (`None` = standalone), `arena_map_id`, `arena_map_name` (`None` when null), `is_simulated`), and `player_rounds_list` (one entry per `PlayerRoundState` in the rounds basket, **already attributed to team_a or team_b by the service** via per-Round `team_color` resolution — `player_id`, `player_name`, `team_id`, `mvp` (`PlayerRoundState.get_mvp` — **property, no parentheses**), `round_id`). The pure module never sees a Django object, only plain dicts.

**Service helpers** (`matches/services.py`, flat module-level, prefixed `_`): `_h2h_matches_qs(team_a, team_b, provenance, date_from, date_to)` (filters by Team-id pair on either red or blue, `is_completed=True`, date range, and when `provenance != "all"` requires **BOTH** `game_rounds` of the Match to match `is_simulated` — the locked conservative rule), `_h2h_rounds_qs(team_a, team_b, provenance, date_from, date_to)` (filters by Team-id pair on either red or blue, date range, provenance; includes Rounds with `match=None` and Rounds whose Match is in the H2H Match set), `_normalize_round(game_round, team_a_id)` (returns the `rounds_list` shape, flipping red/blue when `game_round.team_red_id != team_a_id` so team_a's perspective is canonical), `_team_a_or_b(round_, team_color, team_a_id)` (resolves `"red"`/`"blue"` against the actual Round's sides + `team_a_id` → `team_a_id` or `team_b_id`; this is the per-Round attribution that lets a player who switched teams appear in BOTH per-team pools), `_build_player_rounds(rounds_qs, team_a_id, team_b_id)` (single ORM query `PlayerRoundState.objects.filter(game_round__in=rounds_qs).select_related("player", "game_round")`, maps each row to the `player_rounds_list` shape using `_team_a_or_b`, reads `mvp = state.get_mvp` — **property, no parentheses**), and `_build_detail_list(matches_list, rounds_list, match_lookup, team_a, team_b)` (unified reverse-chronological list, one row per Match with 2-round totals + winner AND one row per standalone Round with that Round's score; each row carries `kind` ∈ `{"match", "round"}` + display fields). `date_played` is real wall-clock time (not the TIME-01 tick clock).

**Determinism / scope.** **Read-only** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. **No model change, no migration, no ADR** (decisions are reversible — a read-only service + pure aggregation module). **No GLOSSARY.md edit** (the **Head-to-head record** term was added inline during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section).

**Locked names.** Endpoint `GET /api/teams/head-to-head/` (`TeamH2HParamsForm`); service `matches.services.build_team_h2h`; pure module `matches/h2h_stats.py` (8 functions: `compute_match_record`, `compute_round_record`, `compute_score_margin`, `compute_avg_survivors`, `top_impactful_per_team`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series`); test files `matches/tests/test_h2h_stats.py` + `matches/tests/test_api_h2h.py`. Seam contract: [`.claude/worktrees/hx-03-seam-contract.md`](../../../.claude/worktrees/hx-03-seam-contract.md).

## HX-04 player head-to-head

`GET /api/players/head-to-head/?player_a=&player_b=&role=&provenance=&from=&to=` aggregates the **Player head-to-head record** (GLOSSARY.md, `### Analytics and review` — the Player analogue of the **Head-to-head record** term, sitting adjacent to it) between two **Players** — W/L Round record, mean signed score margin from player_a's perspective, per-direction tag means + raw totals (A→B and B→A, independent), a per-role breakdown table (bucketed on player_a's per-Round role), a per-map breakdown table, two chart series (margin over time, cumulative W/L), and a unified reverse-chronological detail list. Built by `matches.services.build_player_h2h(player_a, player_b, *, role, provenance, date_from, date_to)` over the pure aggregation module `matches/player_h2h_stats.py`. **No model change, no migration** — read-only/derived; **consumes no RNG**, runs no simulation, so it is outside the SIM-07/08 contract and triggers no Score Calibration re-baseline. An empty basket returns all-zero aggregates. Tests: `matches/tests/test_api_h2h.py`.

**Corpus.** Every `GameRound` where both Players appeared with **different `PlayerRoundState.team_color`** (i.e. on opposite teams). Same-team Rounds are **excluded entirely** — no fallback display, no "of which N on the same team" footnote. Side-agnostic per-Round attribution (a Player who switched teams between Rounds still pairs by per-Round `team_color`, never by `Team` id) — each Round is independently evaluated against the opposite-teams gate. Scores are normalised by the service to player_a's perspective before crossing the seam (reading each `PlayerRoundState.team_color` against the Round's `team_red_id` / `team_blue_id`).

**Pure module** `matches/player_h2h_stats.py` — **pure Python, no Django imports, no ORM, no RNG, no I/O** (frozen import allowlist: `typing.Iterable`, `typing.Mapping`, `typing.Sequence`, `collections.defaultdict`). Exposes **seven** public functions: `compute_round_record` (W/L/T per Round from player_a's perspective; equal scores tie), `compute_score_margin` (mean signed `player_a_team_score − player_b_team_score` per Round; empty ⇒ `{"mean_margin": 0.0, "n": 0}`, no div-by-zero), `compute_tag_stats` (per-Round means of A→B and B→A `GameEvent(event_type="tag")` counts plus raw totals + `n`; tag direction is independent — A→B and B→A are separate counters), `compute_per_role_breakdown` (one row per `role_a` observed — bucketed on player_a's per-Round role regardless of what player_b played, the *display* breakdown distinct from the both-semantics `?role=` filter; sorted by games desc with `role` asc tiebreaker), `compute_per_map_breakdown` (one row per `arena_map_id` observed including the single `None` row labelled `"No map (3-zone)"`; sorted by games desc with `arena_map_id` asc tiebreaker and `None` last), `margin_series` (chart data — signed margin per Round chronologically sorted by `(date_played, round_id)`, `list[list]` not `list[tuple]` for JSON serialisation), `cumulative_wl_series` (chart data — cumulative `player_a_wins − player_b_wins` Round-level chronologically; ties don't move the running diff). Every signature returns zeros (or `[]`) on empty input, never raises. The "no Django imports leaked" defensive check `TestNoDjangoImportsLeaked` mirrors the HX-01 / HX-02 / HX-03 / RES-04 / RV-03 precedent.

**Seam.** A single flat dict list crosses service ↔ pure-module: `rounds_list` (one entry per Round in the opposite-teams basket after all filters applied, **already normalised from player_a's perspective by the service** with **exactly 12 keys**: `round_id`, `date_played`, `player_a_team_score`, `player_b_team_score`, `tags_a_to_b` (`GameEvent(actor=A, target=B, event_type="tag")` count for this Round), `tags_b_to_a`, `role_a` (player_a's `PlayerRoundState.role` for this Round), `role_b`, `match_id` (`None` = standalone), `arena_map_id`, `arena_map_name` (`None` when null), `is_simulated`). The pure module never sees a Django object, only plain dicts.

**Service helpers** (`matches/services.py`, flat module-level, prefixed `_`): `_player_h2h_rounds_qs(player_a, player_b, provenance, date_from, date_to)` (filters to Rounds where **both** Players have a `PlayerRoundState` row + date + provenance; **does NOT apply the opposite-teams gate or role filter** — those happen after per-Round `team_color` / `role` resolution), `_normalize_player_round(game_round, prs_a, prs_b, tag_counts)` (returns the `rounds_list` 12-key shape keyed from player_a's perspective by reading the two `PlayerRoundState` rows; returns **`None`** when `prs_a.team_color == prs_b.team_color` — the same-team gate the caller filters out — and flips `player_a_team_score` / `player_b_team_score` based on each PRS's `team_color` against the Round's `team_red_id` / `team_blue_id`), `_build_player_h2h_tag_counts(rounds_qs, player_a_id, player_b_id)` (**single ORM iterate query**, locked — `GameEvent.objects.filter(game_round__in=rounds_qs, event_type="tag", actor_id__in={A,B}, target_id__in={A,B}).values_list("game_round_id", "actor_id", "target_id")` then Python-side group into `{round_id: (tags_a_to_b, tags_b_to_a)}`; **NOT** two `.annotate(Count())` calls), `_filter_by_role_both(rounds_list, role)` (applies the both-semantics `?role=` filter on already-normalised dicts: returns rows where `row["role_a"] == role AND row["role_b"] == role`; passthrough when `role` is `None` / empty / not a role), and `_build_player_h2h_detail_list(rounds_list)` (reverse-chronological list with display fields).

**Query params** (`PlayerH2HParamsForm`, `matches/api_forms.py`; strict, a bad value is a 400). `player_a` / `player_b` required, the same id twice ⇒ 400, a missing / foreign Player ⇒ 404; `role` (optional, default *any* — when set, basket is restricted to Rounds where **both** Players played that role per `PlayerRoundState.role`, **both-semantics**, locked); `provenance` ∈ `all` / `real` / `sim` (default `all`; `real` ⇒ `is_simulated=False`, `sim` ⇒ `is_simulated=True`); `from` / `to` `YYYY-MM-DD` (both optional, default unbounded; filters `GameRound.date_played`, real wall-clock time, not the TIME-01 tick clock).

**Determinism / scope.** **Read-only** — no RNG, no simulation, no `_flush_to_db` touch, no SIM-07 / SIM-08 contract interaction, no Score Calibration re-baseline obligation. **No model change, no migration, no ADR** (decisions are reversible — a read-only service + pure aggregation module). **No GLOSSARY.md edit** (the **Player head-to-head record** term was added inline during the grilling session that produced this contract and already lives at the bottom of the `### Analytics and review` section adjacent to **Head-to-head record**). **Scope-out (locked):** same-team Rounds excluded entirely; no MVP / most-impactful surface (HX-03 only — pairwise comparison doesn't transfer); no per-player-per-role asymmetric matchup view (deferred — `?role=` is symmetric 'both' only); no seasonal/tournament/month filter; no model change / migration / ADR / GLOSSARY.md edit; no simulation-mechanics change; no new ORM column; no backfill; no Score Calibration re-baseline.

**Locked names.** Endpoint `GET /api/players/head-to-head/` (`PlayerH2HParamsForm`); service `matches.services.build_player_h2h`; pure module `matches/player_h2h_stats.py` (7 functions: `compute_round_record`, `compute_score_margin`, `compute_tag_stats`, `compute_per_role_breakdown`, `compute_per_map_breakdown`, `margin_series`, `cumulative_wl_series`); test files `matches/tests/test_player_h2h_stats.py` + `matches/tests/test_api_h2h.py`. Seam contract: [`.claude/worktrees/hx-04-seam-contract.md`](../../../.claude/worktrees/hx-04-seam-contract.md).

## ANG-06d Round scoreboard builder

`matches/services.py::round_scoreboard(game_round) -> dict` builds the **Round scoreboard** per Side:
`{"red" | "blue": {"players", "survivors", "eliminated", "elim_bonus"}}`. Each row is the 28
`PLAYER_ROW_KEYS` from `_player_row` plus `player_id` (the UX-02 "You" badge). Rows are grouped by
`team_color` (LG-07: never `player__team`) and ordered `-points_scored, role, player__name`.
`survivors` / `eliminated` come from `round_summary`, but a Side with **no rows** (a legacy Round)
is never eliminated. `elim_bonus` is `BatchSimulator.elimination_bonus` when the other Side is
eliminated, else 0. It reuses a `player_states` prefetch when one is set (the API retrieve sets
one); otherwise it queries.

Its consumer is `GameRoundSerializer.scoreboard` (`GET /api/rounds/<id>/`, see `api.md`), which
the Angular `/rounds/:id` page renders. The empty-Side rule removes the false `+10000` a legacy
Round showed.

The PDF report (`build_round_report_data`) builds its rows from `_player_row` too.
Tests: `matches/tests/test_api_round_scoreboard.py` (shape, grouping, ordering, bonus, empty Side,
query counts, ownership).

## ANG-06e Event log timeline

`matches/services.py::round_timeline(game_round) -> dict` builds the whole **Event log** for
`GET /api/rounds/<id>/timeline/` (see `api.md`): `{"events", "players", "highlights", "summary"}`.

- `events` remaps the `round_playback_payload` rows one by one (`_timeline_event`), so RV-05's
  `round_series` and the API read the same rows. `round_playback_payload` keeps its 13-key row,
  so a timeline event has no `id`: the Angular screen tracks rows by index.
- Each event: `tick, event_type, points, description, actor_id, actor_name, actor_side, target_id,
  target_name, target_side, metadata`. Sides come from `PlayerRoundState.team_color` (the payload's
  `at` / `tt` are Team ids). With no target, the three `target_*` keys are `null`, never the
  payload's `-1` / `""`.
- `players` are `{id, name, side, role}`; `highlights` is `highlights_json or []`; `summary` is
  `GameRound.get_event_summary()`.
- The action upgrades the round to `full` fidelity first. Its query count does not grow with the
  number of events.

The Angular screen is `/rounds/:id/events` (`frontend/src/app/matches/round-events/`). The browser
filters the timeline by **Event group** (`matches/shared/event-groups.ts`, the nine groups and their
`--lf-ev-*` tokens) and by player (actor or target). The kill feed was
dropped. SIM-05 playback shipped as ANG-06f (below); the four charts shipped as ANG-06g (below).

Tests: `matches/tests/test_api_round_timeline.py` (shape, order, null targets, Sides, ownership,
405, the drift guard against `round_playback_payload`, query count).

## ANG-06f Playback

`matches/playback.py` (its own module; it imports no view module) serves
`GET /api/rounds/<id>/playback/` (see `api.md`). Glossary: **Playback**, **Playback snapshot**.
[ADR-0044](../../adr/0044-playback-state-from-logged-after-event-values.md) records why the state is
computed this way.

- `round_playback(game_round) -> dict` returns `{duration_ticks, not_targetable_ticks,
  respawn_ticks, players: [{id, snapshots}], map}`, with players in `PlayerRoundState.id` order.
  - A snapshot is `{tick, score, lives, shots, missiles, sp, tags, misses, downed_at, eliminated,
    rapid_fire, nuking}`. Each player has one tick-0 opening snapshot (from the `starting_*`
    fields), then one per tick at which any of their fields changed. Events on the same tick
    collapse into the state after the last one.
  - **Values are read, never re-derived.** Score, lives, shots and SP come from the after-event
    values every event logs: `actor_*`, `sp`, `target_*`, and `special.targets[]`.
  - **Derived values:**
    - `missiles`: −1 per `locking`. A hit costs nothing more: the engine spends the missile once,
      at the lock (**MECH-16**, fixed 2026-11-06, with the matching −1 dropped here).
    - `eliminated`: from the target's `elimination` event.
    - `tags` / `misses`: counts of `tag` / `miss` events.
    - `downed_at`: set on a lives decrease (`record_down` is the engine's life-loss chokepoint) or
      on a received `resupply_*`.
    - `rapid_fire`: on from "activates rapid fire" until the player's next received resupply.
    - `nuking`: on from "activates nuke" until a detonation, a `nuke_cancelled` or `fires_at`.
  - The description match for the specials lives only in `_special_kind`. A test checks it against
    the engine's `specials_used` over seeded rounds.
- `build_playback_map(game_round) -> dict | None` (see **Round-playback map** above) adds
  `arena_map: {id, name}`. Players come in state-id
  order and moves in (tick, event id) order.
  - RV-07a: each map player carries its role's limits right after `role`, so the SPA keeps no
    copy of the role table: `max_lives` (`MAX_LIVES`), `max_shots` (`MAX_SHOTS`), `special_cost`
    (`SPECIAL_COST.get(role)`: `null` for a Heavy), all from `sim_helpers/role_constants.py` and
    case-insensitive on `role`, and `max_missiles` (the state's `starting_missiles`). They come
    from rows already loaded, so the query count is unchanged.
- **Final-state invariant:** each player's last snapshot equals their `PlayerRoundState`. The fields
  compared are score, lives, shots, missiles, sp, eliminated, `tags_made` and `shots_missed`.
- The action upgrades the round to `full` fidelity. Its query count does not grow with the number of
  events. `.only()` must keep `game_round_id`, or every event costs a query.

The Angular screen is `/rounds/:id/playback` (`frontend/src/app/matches/playback/`). It is styled
after LFstats's Replay tab, within ADR-0043.

- **The shell** (`playback.ts`):
  - It loads round + timeline + playback with `forkJoin` and owns the `cursor` tick.
  - A 100 ms loop advances the cursor by `speed × 0.2` ticks.
  - Controls: 1x / 2x / 4x / 8x, start / end, and step to the previous / next event tick, skipping
    Movement. Previous floors a fractional cursor first.
  - A tick slider pauses playback while it is dragged, and `seek(tick)` moves the cursor.
  - A `.pb-markers` anchor above the slider is reserved for RV-04's pips. They must render inside
    the shell, because a routed component gets no projected content.
- **The live state** comes from the pure `snapshotAt` binary search.
- **`PlaybackScoreboard`** shows one Side each, using LFstats's columns and live rank. It adds:
  - a conic respawn dot;
  - an OUT badge;
  - "—" for a Heavy's SP;
  - Acc as `tags / (tags + misses)`;
  - the rapid-fire pulse, the nuke rainbow, and the 700 ms rank-up flash.

  Under reduced motion the flash is gone entirely, while the static rainbow and glow stay.
- **`PlaybackFeed`** lists events newest first; the first row is `is-current`. It has search, plus
  Misses and Movement toggles that start off.
- **`PlaybackMap`** is **beta**: movement was reworked by MOVE-08 (feet-based speed, the Downed option) and MOVE-06+07 (drawn goals, per-player routes), and MECH-07 (Modes) reworked it again.
  - It stays collapsed behind "Show map (beta)", and only its contract is tested.
  - On first open it fetches the processed image through `MapsService.processedImage`, and revokes
    the object URL on destroy.
  - It draws on a canvas, ported from the Django overlay's Catmull-Rom and 16 s trails.
  - It dims a player's marker and trail while they are deactivated (`deactivatedAt`).
  - RV-06a: each marker is the player's SM5 role icon (`public/roles/<role>.png`, restored from
    the Django templates), tinted in the Side colour by `matches/shared/role-icon.ts`
    (`tintPixels`: line art takes the colour, the white fill stays; `tintedRoleIcon` caches per
    role + colour), with no ring, at 1.5 times the old ring (`playback-markers.ts` `markerSize`).
    A deactivated player's icon takes `--lf-map-marker-off` (one grey in both themes: the map
    image is white in both) at 0.35 opacity (`markerLook`). A dot stands in until the PNG loads
    (`roleIconLoads` redraws). The Scout badge sits at the icon's bottom-right.
  - The Playback scoreboard's Pos cell and the feed's actor / target names show
    `<app-role-icon [role] [side]>` (`role-icon-img.ts`), whose `alt` / `title` is the role's
    name; it tints from `--lf-side-<side>` and re-tints when `side` changes (the feed reuses rows,
    and tracks them by event). A feed row's actor and target wrap onto two lines when they don't
    fit (RV-07 review: at 768px a long target name was clipped), each name kept on one line.
  - RV-06c: it takes the timeline's `events` too, and draws a **Tag line** for each tag, miss,
    missile, resupply and active base capture, from the pure `playback-overlays.ts`
    (`overlaysAt`, `baseMarkers`; its spec is the test seam). A line joins where both players
    stood at the event's tick, sits `LINE_OFFSET_PX` (4) to the actor's right, shows for
    `LINE_TICKS` (2) and fades, with a muzzle flare at the acting end. A miss is dashed and faint,
    missiles take `--lf-ev-missile` (a miss dashed), a resupply `--lf-ev-resupply` with a "+" at
    the receiver. A capture's base is `metadata.base_type`, else `base_id` 13 red / 14 blue / 15
    the nearest `neutral_*`; it adds 8 radial lasers for `BURST_TICKS` (4). Round-end awarded
    captures (no `shots_remaining`) draw nothing.
  - On first open it also fetches `MapsService.zoneConfig` and draws each base as an R / B / N
    diamond. A failed fetch, or no bases, draws none and shows no notice. Draw order: bases,
    trails, lines and bursts, markers.
  - RV-07a: each marker carries what the player holds, from the pure `playback-gauges.ts`
    (`playerGauges(player, snapshots, tick)` reads the snapshot at the tick through
    `snapshotAt`: `lives` / `shots` as 0..1 of `max_lives` / `max_shots`, clamped, and
    `missiles` or `null` when `max_missiles` is 0; `gaugeColour` blends `--lf-gauge-empty` ->
    `--lf-gauge-mid` below 0.5 and mid -> `--lf-gauge-full` from 0.5, each token read through
    `resolveColor` and `parseRgb` (any `#rgb` / `#rrggbb` / `rgb()` value; an unreadable one
    falls back to the token's default, never black); its spec is the test
    seam). `drawGauges` paints, after the icon: a lives arc on the left (`0.55 pi` to
    `1.45 pi`) and a shots arc on the right (`0.45 pi` back to `-0.45 pi`), each at radius
    `1.12 h` (`h` = half the marker size), width `max(3, 0.1 size)`, filling from the bottom
    over a `--lf-gauge-track` arc; then, for a Commander or Heavy, `max_missiles` pips of radius
    `0.055 size` under the arcs, lit in `--lf-ev-missile` per missile left, unlit in the track.
    A deactivated player's gauges are drawn at the icon's 0.35 alpha and keep their colours. The
    four gauge tokens are the same in light and dark (the map image is white in both).
  - RV-07b: every player with a special (not a Heavy) gets a **special bar** under the pips.
    `playerGauges` fills `special` (`SpecialBar`, `null` when `special_cost` is null): SP is
    capped at `MAX_SP` (99), `lap = floor(sp / cost)` the completed laps and
    `fill = (sp % cost) / cost` the current one; `running` is `'nuke'` on `nuking`,
    `'rapid-fire'` on `rapid_fire`, else `'none'`; `fuse` is `fuseProgress(snapshots, tick)`
    while nuking. `fuseProgress` lights the fuse at the first snapshot of the latest `nuking` run
    at or before the tick and ends it at the next snapshot that clears `nuking` (detonation or
    cancel), else lit + 14; the end tick reads 1, later ticks `null`. `drawSpecialBar` paints it
    `1.15 size` wide and `max(4, 0.13 size)` high, centred `1.12 h` + `0.3 size` (with missiles,
    else `0.18 size`) under the player: the previous lap's colour (`lapColour(lap - 1)`, or the
    track before the first lap) with the current lap's fill over it, and one tick per completed
    lap above the bar in that lap's colour. `lapColour(lap, laps)` takes the ten
    `--lf-special-lap-*` tokens (green, orange, red, then darker reds; the same in both themes)
    and clamps to the last. **Rapid fire** rolls stripes (`--lf-special-stripe`, white in both themes)
    across the whole bar over the Side colour; a **nuke** turns the bar into its fuse, the first
    `fuse` of the track rolling the same stripes, with no label. The bar dims with the icon.
  - **Frame loop:** while the map is open and `anySpecialRunning(players, snapshots, cursor)`,
    an effect runs a `requestAnimationFrame` loop that sets `performance.now()` for the stripe
    phase, so the stripes roll while paused. It is cancelled when no special runs, the map
    closes or the component is destroyed. Under `prefers-reduced-motion: reduce` it never starts
    and the stripes stand at phase 0. `playback-map.spec.ts` drives it with fake rAF
    (`POLL_TIMERS`).
- Shared helpers are `matches/shared/role-label.ts` (`roleLabel`) and `side-tag.ts` (`sideTag`).

Tests:
- `matches/tests/test_playback.py`: the invariant over three fresh rounds (a Single Round seeds
  itself from OS entropy, so `random.seed` cannot pin it; a failure names the `rng_seed` for a
  **Replay**), unit cases on hand-built
  events, and the special-kind check.
- `test_api_round_playback.py`: shape, map, ownership, fidelity and query count.
- `test_playback_map.py`: the map and its order, and each role's limits (RV-07a).
- The Vitest specs beside each component.

## ANG-06g Event charts

The Angular screen is `/rounds/:id/charts` (`frontend/src/app/matches/round-charts/`), declared
before `:id`. It needs no endpoint of its own: it `forkJoin`s the Round, `/timeline/` and
`/playback/`. Glossary: **Chart overlay**.
[ADR-0045](../../adr/0045-chart-js-for-angular-event-charts.md) records the library choice.

- **`event-charts.ts`** is pure (no Angular, no Chart.js):
  - `buildEventCharts(timeline, playback) -> EventCharts {players, series, overlays}`. Series are
    keyed by `ChartKey` (`shots`, `lives`, `points` from the snapshot's `score`, `sp`); a point is
    `{sec, value}` with `sec = tick / TICKS_PER_SECOND`, and an unchanged value adds no point. A
    timeline player missing from Playback gets an empty series. Roles are lower-cased to `Role`.
  - `OVERLAY_KINDS` is the Chart overlay registry: `elimination` (labelled with the eliminated
    player), `special` (the actor) and `nuke` (from each `nuke_detonation` Highlight, labelled with
    the Commander). A `special` at the same tick and actor as a detonation is not also a Special.
    **RV-04 and RES-05 each add one entry here**, plus the events it marks.
  - `defaultFilters`, `visiblePlayers` (Side AND role AND own box) and
    `sideTotals(players, series, visible, 'sum' | 'avg')`: over the visible players only, each
    counting from their first point.
- **`line-chart.ts`** (`app-line-chart`) wraps one Chart.js line chart. It registers only what a
  line chart draws; its own plugins (`lfOverlays`, and `lfCapLine` when `yMax` is set) go inline
  in each chart's `plugins:` array, never globally. Lines are `stepped: 'before'` and hold their
  last value to the end of the Round; the x-axis is mm:ss. Specs read its public `config`,
  because jsdom has no canvas.
- **`chart-colors.ts`**: `playerColors` (shades of the Side token mixed toward
  `--lf-text`) and `resolveColor` (a probe element turns any CSS colour into one canvas can use);
  tokens are read with `shared/read-token.ts` `readToken` (also used by the map editor palette).
- **`round-charts.ts`** reads every token in one `palette` computed (colours plus
  `--lf-font-body`), again when a `matchMedia` listener sees the colour scheme flip. Filters are
  a `linkedSignal`, reset on each load and when you leave the page.
- Specs: `matches/testing/fake-canvas.ts` (`stubCanvasContext`) gives jsdom a no-op 2D context
  and a silent `ResizeObserver`, so a real `Chart` can be built and destroyed.
- Out of scope (follow-ups): clicking a chart to seek Playback, a cursor synced with Playback,
  zoom / pan.

## ANG-06-9 Head-to-head & history in Angular

Spec #377. Four Angular screens port the HX-03 / HX-04 / Team match history / RV-01 templates.
Every pick lives in the URL query (deep-linkable); a bad
value is dropped client-side so the server default applies; submit navigates; a 400 shows its
message above the picker; a 404 shows the screen's not-found state.

| Route | Component | API |
|---|---|---|
| `/teams/head-to-head?team_a=&team_b=&provenance=&from=&to=` | `teams/team-head-to-head/` | `teams/head-to-head/` |
| `/players/head-to-head?player_a=&player_b=&role=&provenance=&from=&to=` | `players/player-head-to-head/` | `players/head-to-head/` |
| `/teams/:id/history?scope=…&applied=1` | `teams/team-history/` | `teams/<id>/history/` (new, see `api.md`) |
| `/rounds/compare?round_a=&round_b=` | `matches/compare-rounds/` | `rounds/compare/` |

- **Shared:** `frontend/src/app/shared/head-to-head/` (the two H2H screens: `h2h-query.ts` URL
  readers incl. `queryId`, record cards, per-map table, the two `app-line-chart` charts with
  `xAxis: 'count'` and a dashed "Zero" dataset); `shared/not-found-or-error.ts`
  (`notFoundOrError(notFound, error)`, the 404-or-message `catchError` tail of all four screens).
- **Pickers** load every owned row with `listAll` (`page_size=100`). Team history's scope filter
  uses an `applied` marker: `scope` without `applied` is a fresh load (every scope).
- **Team history** has no doughnut (ADR-0045 is line-only); its numbers are the Match /
  Round W-L-T tiles. An unfinished Match still counts and shows as a Tie.
- **Compare Rounds** colours one shared Team per new token `--lf-chart-1` / `--lf-chart-2`
  (solid = Round A, dashed = Round B); fractions show to 2 decimals; rows sort case-insensitively.
- **Team H2H rows** carry `match_id` / `round_id` (ANG-06-9), so "View" links by id.
- **Entry points:** Team detail "Match History"; Match / Round detail "{team} History"; Team
  history "vs. {opp} — H2H"; Player career "Head-to-head" (`?player_a=`); Match list
  "Head-to-Head" + "Compare Rounds". Tournament / League entry points come with ANG-06 13b / 15 / 16.
- **Not changed:** the HX-03 / HX-04 corpora are still not ownership-filtered (PLAN.md Deferred
  Items); a seeded Round with no events gives an empty points chart (the API series is empty).

## ANG-06-10 Round extras in Angular

Spec #384. Two Angular screens port the RES-03 / RES-04 templates, linked from Round detail's
action row after Charts. Both load the Round with
`loadRouteRecord('Round', …)` for the "<red> vs <blue>" heading (404 -> Round not-found, other ->
"Could not load the Round."), then their payload; a failed payload shows its `apiMessage`.

| Route | Component | API |
|---|---|---|
| `/rounds/:id/missile-log` | `matches/missile-log/` | `rounds/<id>/missile-log/` |
| `/rounds/:id/heatmap` | `matches/round-heatmap/` | `rounds/<id>/heatmap/` + `maps/<id>/processed-image/` |

- **Missile log:** Fired / Hit / Efficiency (`toFixed(1)` + `%`; the API sends `hit / fired *
  100` unrounded), then Time, Actor, Target (`roleLabel`), Result, Friendly fire yes/no,
  Description, Points. Friendly-fire rows get class `friendly-fire` from the `friendly_fire`
  boolean (not the API's `row_class`), tinted from `--lf-ev-missile`. Below 640px of table width a
  container query tightens the cells so all seven columns fit at a 768px viewport.
- **Movement heatmap:** the `playback-map` pattern: the processed image as a Blob object URL
  (revoked on destroy), a canvas at its natural size,
  both scaled by CSS. Pure `filteredOccupancy(heatmap, filter)` (`heatmap-filter.ts`) sums the
  occupancy of roster players passing Player / Role / Team (AND; a player id not on the roster is
  ignored); `heatCells` / `heatColour` from `maps/heatmap/heat-cells.ts`
  paint it with the `--lf-map-heat-*` tokens. A picker change redraws with no request. No map:
  "No map — heatmap unavailable." and no image or canvas; null `zone_size` gives the bare map.
- **Export file name:** Round detail's Export PDF saves under the server's `Content-Disposition`
  name (`round-<id>-<red>-vs-<blue>.pdf`), via `ApiClient.getFile` and the
  `CORS_EXPOSE_HEADERS` setting (see `api.md`, CORS), falling back to `round-<id>.pdf`.
- **Not ported:** player names in Missile log rows (the API sends roles only), a heatmap legend.
